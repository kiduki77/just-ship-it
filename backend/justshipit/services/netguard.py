"""Opening a link the model or a web page gave us, without letting it reach a private network.

A name is resolved once, every address it has must be global, and the connection goes to the address that was
checked, so a second lookup cannot answer differently. Callers follow redirects by hand and check each hop. The
owner's HTTP proxy is used through a CONNECT tunnel to the checked address, with TLS still verifying the URL's name.
"""
from __future__ import annotations

import base64
import ipaddress
import socket
import time
import zlib
from urllib.request import getproxies, proxy_bypass

import httpcore
import httpx

# NAT64 prefixes embed an IPv4 address a translator connects to; the well-known one counts as global.
NAT64 = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48"))
# IPv4-compatible addresses (::a.b.c.d) carry an IPv4 address too, and `is_global` is True for them whatever it is.
V4_COMPATIBLE = ipaddress.ip_network("::/96")


def public_address(host: str, port: int) -> str:
    """Resolve once and return the address to connect to; every address the name has must be global."""
    try:
        ips = [ipaddress.ip_address(info[4][0]) for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]
    except (socket.gaierror, ValueError):
        raise ValueError(f"refusing non-public address: {host}") from None
    for ip in ips:
        ip = getattr(ip, "ipv4_mapped", None) or ip
        if (not ip.is_global or ip.is_multicast or getattr(ip, "is_site_local", False)
                or any(ip in net for net in (*NAT64, V4_COMPATIBLE))):
            raise ValueError(f"refusing non-public address: {host}")
    return str(ips[0])


ACCEPT_ENCODING = "gzip"  # what read_capped is sure to decode within its cap


def read_capped(r: httpx.Response, max_bytes: int, deadline: float) -> bytes:
    """The first `max_bytes` of a streamed response's decoded body. It reads the raw
    stream and inflates it here, so a compressed body never expands past the cap in memory. A stream still going at
    `deadline` (time.monotonic) raises httpx.ReadTimeout: the client's own timeout only bounds each read."""
    encoding = r.headers.get("content-encoding", "").strip().lower()
    if encoding in ("", "identity"):
        inflater = None
    elif encoding in ("gzip", "x-gzip", "deflate"):
        inflater = zlib.decompressobj(47)  # gzip or zlib framing, told apart by the header
    else:
        raise ValueError(f"unsupported content-encoding: {encoding[:40]}")
    body = bytearray()
    for chunk in r.iter_raw():
        if time.monotonic() > deadline:
            raise httpx.ReadTimeout("over the total time limit")
        while chunk and len(body) <= max_bytes:
            if inflater is None:
                body += chunk
                break
            try:
                body += inflater.decompress(chunk, max_bytes + 1 - len(body))
            except zlib.error as exc:
                raise ValueError(f"undecodable {encoding} body") from exc
            chunk = inflater.unconsumed_tail
        if len(body) > max_bytes:
            break
    return bytes(body[:max_bytes])


class ConnectProxyBackend(httpcore.SyncBackend):
    """Opens each connection as a CONNECT tunnel through the owner's HTTP proxy to the address httpx asks for
    (our checked IP). httpcore's own proxy path would verify TLS against that IP instead of the URL's name."""

    def __init__(self, proxy: httpx.URL):
        self.proxy = proxy

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        stream = super().connect_tcp(self.proxy.host, self.proxy.port or (443 if self.proxy.scheme == "https" else 80),
                                     timeout=timeout, local_address=local_address, socket_options=socket_options)
        if self.proxy.scheme == "https":
            stream = stream.start_tls(httpx.create_ssl_context(), server_hostname=self.proxy.host, timeout=timeout)
        target = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
        head = f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n"
        if self.proxy.username:
            cred = base64.b64encode(f"{self.proxy.username}:{self.proxy.password}".encode()).decode()
            head += f"Proxy-Authorization: Basic {cred}\r\n"
        stream.write(f"{head}\r\n".encode("ascii"), timeout)
        reply = b""
        while b"\r\n\r\n" not in reply:
            chunk = stream.read(65536, timeout) if len(reply) < 65536 else b""
            if not chunk:
                stream.close()
                raise httpcore.ProxyError("proxy closed the CONNECT request without a response")
            reply += chunk
        status = reply.split(b"\r\n", 1)[0].decode("ascii", "replace")
        if status.split(" ")[1:2] != ["200"]:
            stream.close()
            raise httpcore.ProxyError(f"the proxy refused CONNECT {target}: {status}. Most proxies (Squid's defaults, "
                                      "say) don't allow a tunnel to an http:// address, so use an https:// address.")
        return stream


class PinnedProxyTransport(httpx.HTTPTransport):
    def __init__(self, proxy: httpx.URL):
        super().__init__()
        # HTTPTransport takes no network backend, so the pool is swapped after construction.
        self._pool = httpcore.ConnectionPool(ssl_context=httpx.create_ssl_context(),
                                             network_backend=ConnectProxyBackend(proxy))


def transport(scheme: str, hostname: str) -> httpx.BaseTransport:
    proxies = getproxies()
    proxy = proxies.get(scheme) or proxies.get("all")
    if proxy and not proxy_bypass(hostname):
        url = httpx.URL(proxy if "://" in proxy else f"http://{proxy}")
        if url.scheme not in ("http", "https"):
            raise ValueError(f"{url.scheme}:// proxies aren't supported (only HTTP CONNECT proxies). Set an http:// or "
                             "https:// proxy in HTTP_PROXY/HTTPS_PROXY, or clear ALL_PROXY.")
        return PinnedProxyTransport(url)
    return httpx.HTTPTransport()
