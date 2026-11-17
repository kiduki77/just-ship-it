import json
import re
from pathlib import Path

import pytest

from justshipit.analysis import runner
from justshipit.sandbox import docker
from justshipit.services import netguard
from sandbox_kit import needs_image


def test_secrets_passed_by_name_only():
    spec = docker.ContainerSpec(image="img", command=["claude", "-p"], env={"CLAUDE_CODE_OAUTH_TOKEN": "sk-secret"},
                                mounts=[docker.Mount(Path("."), "/work")])
    args = docker.build_args(spec, "n")
    assert "sk-secret" not in " ".join(args)
    assert ["-e", "CLAUDE_CODE_OAUTH_TOKEN"] == args[args.index("-e"):args.index("-e") + 2]
    assert "--cap-drop" in args and "no-new-privileges" in args


def test_containers_are_labelled_with_the_deployment_not_just_the_process():
    from justshipit.settings import Settings, settings

    spec = docker.ContainerSpec(image="img", command=["true"])
    args = docker.build_args(spec, "n")
    assert f"{docker.DEPLOYMENT_LABEL}={settings().deployment_id}" in args
    other_database = settings().database_url.rsplit("/", 1)[0] + "/" + Settings().pg_db
    assert Settings(database_url=other_database).deployment_id != settings().deployment_id


def test_fetch_refuses_private_and_non_http(tmp_path):
    for url in ("http://127.0.0.1:8000/api/inbox", "http://localhost/", "file:///etc/passwd",
                "http://10.0.0.5/x.xlsx", "ftp://example.com/a.csv"):
        with pytest.raises(ValueError):
            runner.fetch(url, tmp_path / "x", 1000)


def test_safe_name_strips_paths():
    assert runner.safe_name("../../etc/passwd", "http://x/y") == "passwd"
    assert runner.safe_name("", "https://kosis.kr/files/장기요양 2024.xlsx") == "장기요양_2024.xlsx"


def test_local_inputs_cannot_escape_owner_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "owner_data_dir", lambda slug: tmp_path / "data" / slug)
    (tmp_path / "data" / "p").mkdir(parents=True)
    (tmp_path / "secret.txt").write_text("x")
    with pytest.raises(ValueError):
        runner._local("../../secret.txt", "p", tmp_path / "out")


def test_evidence_classification():
    rec = runner.AnalysisRecord(workdir=Path("."), manifest=[
        {"filename": "a.xlsx", "url": "https://data.example/a.xlsx", "sha256": "ab" * 32}],
        final_results=json.dumps({"agencies": {"jeonnam": 1234}, "share": 0.1834}), reproduced=True)
    assert runner.classify_evidence(rec, "https://data.example/a.xlsx", "1,234", lang="en")[0] == "reproduced"
    assert runner.classify_evidence(rec, "https://data.example/a.xlsx", "0.1834", lang="en")[0] == "reproduced"
    assert runner.classify_evidence(rec, "https://data.example/a.xlsx", "999", lang="en") is None
    assert runner.classify_evidence(rec, "https://other.example/", "1234", lang="en") is None
    rec.reproduced = False
    assert runner.classify_evidence(rec, "https://data.example/a.xlsx", "1234", lang="en")[0] == "computed"


@needs_image
def test_script_runs_offline_and_isolated(tmp_path):
    (tmp_path / "inputs").mkdir()
    (tmp_path / "inputs" / "d.csv").write_text("region,n\nA,3\nB,4\n", encoding="utf-8")
    script = """
import json, pandas as pd, urllib.request, os
df = pd.read_csv('inputs/d.csv')
try:
    urllib.request.urlopen('https://example.com', timeout=5); net = True
except Exception:
    net = False
try:
    open('inputs/d.csv', 'a').write('x'); inputs_writable = True
except OSError:
    inputs_writable = False
json.dump({'total': int(df.n.sum()), 'network': net, 'inputs_writable': inputs_writable,
           'host_visible': os.path.exists('/c') or os.path.exists('/mnt/c')}, open('out/results.json', 'w'))
"""
    code, timed_out, out, err, results, files = runner.execute(tmp_path, script, tmp_path / "run")
    assert code == 0, err
    assert json.loads(results) == {"total": 7, "network": False, "inputs_writable": False, "host_visible": False}
    assert files == ["results.json"]


def test_values_from_typed_in_numbers_are_labelled_transcribed():
    rec = runner.AnalysisRecord(workdir=Path("."), manifest=[], final_script="x = 1965896 / 9279861",
                                final_results=json.dumps({"seoul": 21.18}), reproduced=True)
    assert runner.classify(rec, "https://jumin.mois.go.kr/x.do", "21.18", lang="en")[0] == "transcribed"
    assert runner.classify(rec, "https://jumin.mois.go.kr/x.do", "99", lang="en") is None
    assert "입력 파일이 없어요" in runner.appendix(rec, "p", lang="ko")


def test_sandbox_build_uses_the_tags_from_company_yaml(monkeypatch):
    import subprocess

    from justshipit import cli
    from justshipit.settings import company_config
    from typer.testing import CliRunner

    built: list[str] = []
    monkeypatch.setattr(subprocess, "run", lambda args, **kw: built.append(args[args.index("-t") + 1])
                        or subprocess.CompletedProcess(args, 0))
    result = CliRunner().invoke(cli.app, ["sandbox", "build", "all"])

    cc = company_config()
    assert result.exit_code == 0
    assert built == [cc["engineering"]["sandbox"]["image"], cc["analysis"]["image"]]
    assert built[0] != "ship/engineer:1"


def _resolve_to(monkeypatch, *ips):
    lookups: list[str] = []

    def getaddrinfo(host, *a, **kw):
        lookups.append(host)
        return [(2, 1, 6, "", (ip, 0)) for ip in ips]

    monkeypatch.setattr(netguard.socket, "getaddrinfo", getaddrinfo)
    return lookups


def _serve(monkeypatch, handler):
    import httpx
    monkeypatch.setattr(runner, "_transport", lambda scheme, hostname: httpx.MockTransport(handler))


def test_fetch_refuses_every_address_that_is_not_global(tmp_path, monkeypatch):
    _serve(monkeypatch, lambda request: pytest.fail(f"connected to {request.url}"))
    for ip in ("100.64.3.4", "198.18.0.1", "224.0.0.1", "::ffff:127.0.0.1", "64:ff9b:1::a00:1", "64:ff9b::7f00:1",
               "64:ff9b::808:808"):
        _resolve_to(monkeypatch, "93.184.216.34", ip)
        with pytest.raises(ValueError, match="non-public"):
            runner.fetch("https://data.example.com/a.csv", tmp_path / "x", 1000)


def test_fetch_connects_to_the_checked_address_with_the_real_hostname_for_tls(tmp_path, monkeypatch):
    import httpx

    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        if request.headers["host"] == "data.example.com":
            return httpx.Response(302, headers={"location": "https://cdn.example.net:8443/a.csv"})
        return httpx.Response(200, content=b"a,b\n1,2\n", headers={"content-type": "text/csv"})

    _serve(monkeypatch, handler)
    lookups = _resolve_to(monkeypatch, "93.184.216.34")
    meta = runner.fetch("https://data.example.com/a.csv", tmp_path / "x", 1000)

    assert meta["bytes"] == 8 and meta["final_url"] == "https://cdn.example.net:8443/a.csv"
    assert lookups == ["data.example.com", "cdn.example.net"]
    assert [str(r.url) for r in seen] == ["https://93.184.216.34/a.csv", "https://93.184.216.34:8443/a.csv"]
    assert [r.headers["host"] for r in seen] == ["data.example.com", "cdn.example.net:8443"]
    assert [r.extensions.get("sni_hostname") for r in seen] == ["data.example.com", "cdn.example.net"]


def test_fetch_stops_a_download_that_outlasts_the_time_limit(tmp_path, monkeypatch):
    import httpx

    clock = iter(range(0, 10_000, 10))
    monkeypatch.setattr(runner.time, "monotonic", lambda: next(clock), raising=False)
    _serve(monkeypatch, lambda request: httpx.Response(200, content=iter([b"x"] * 100)))
    _resolve_to(monkeypatch, "93.184.216.34")
    with pytest.raises(ValueError, match="time limit"):
        runner.fetch("https://slow.example.com/a.csv", tmp_path / "x", 10_000, timeout_s=120)
    assert not (tmp_path / "x").exists()


FIXTURES = Path(__file__).parent / "fixtures"


def _tls_server(seen: dict):
    """An HTTPS server for data.test on a free loopback port; records the SNI each handshake carried."""
    import http.server
    import ssl
    import threading

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("content-length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(FIXTURES / "data.test.crt", FIXTURES / "data.test.key")
    ctx.sni_callback = lambda sock, name, c: seen.setdefault("sni", []).append(name)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv.server_address[1]


def _connect_proxy(seen: dict):
    """A minimal HTTP CONNECT proxy on a free loopback port; records each tunnel target."""
    import select
    import socket
    import socketserver
    import threading

    class P(socketserver.BaseRequestHandler):
        def handle(self):
            req = b""
            while b"\r\n\r\n" not in req:
                req += self.request.recv(4096)
            target = req.split()[1].decode()
            seen.setdefault("connect", []).append(target)
            host, port = target.rsplit(":", 1)
            up = socket.create_connection((host.strip("[]"), int(port)))
            self.request.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
            try:
                while True:
                    ready, _, _ = select.select([self.request, up], [], [], 5)
                    if not ready:
                        return
                    for s in ready:
                        data = s.recv(65536)
                        if not data:
                            return
                        (up if s is self.request else self.request).sendall(data)
            except OSError:
                pass

    socketserver.ThreadingTCPServer.allow_reuse_address = True
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), P)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv.server_address[1]


def test_fetch_through_a_connect_proxy_still_pins_the_address_and_verifies_the_name(tmp_path, monkeypatch):
    seen: dict = {}
    port = _tls_server(seen)
    proxy_port = _connect_proxy(seen)
    for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SSL_CERT_FILE", str(FIXTURES / "data.test.crt"))
    monkeypatch.setattr(runner, "_public_address", lambda host, p: "127.0.0.1")

    direct = runner.fetch(f"https://data.test:{port}/a.csv", tmp_path / "direct", 1000, 10)
    monkeypatch.setenv("HTTPS_PROXY", f"http://127.0.0.1:{proxy_port}")
    proxied = runner.fetch(f"https://data.test:{port}/a.csv", tmp_path / "proxied", 1000, 10)

    assert (direct["bytes"], proxied["bytes"]) == (2, 2)
    assert seen["connect"] == [f"127.0.0.1:{port}"], "the proxy tunnels to the address we checked, not a name"
    assert seen["sni"] == ["data.test", "data.test"]
    monkeypatch.setenv("NO_PROXY", "data.test")
    runner.fetch(f"https://data.test:{port}/a.csv", tmp_path / "bypassed", 1000, 10)
    assert len(seen["connect"]) == 1, "NO_PROXY still bypasses the proxy"


def test_fetch_pins_an_ipv6_address_in_brackets(tmp_path, monkeypatch):
    import socketserver
    import threading

    import httpx

    v6 = "2606:4700:4700::1111"
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, content=b"ok")

    _serve(monkeypatch, handler)
    _resolve_to(monkeypatch, v6)
    assert runner.fetch("https://data.example.com:8443/a.csv", tmp_path / "x", 1000)["bytes"] == 2
    assert str(seen[0].url) == f"https://[{v6}]:8443/a.csv"
    assert seen[0].headers["host"] == "data.example.com:8443"
    assert seen[0].extensions["sni_hostname"] == "data.example.com"

    targets: list[str] = []

    class Refuse(socketserver.BaseRequestHandler):
        def handle(self):
            req = b""
            while b"\r\n\r\n" not in req:
                req += self.request.recv(4096)
            targets.append(req.split()[1].decode())
            self.request.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")

    proxy = socketserver.TCPServer(("127.0.0.1", 0), Refuse)
    threading.Thread(target=proxy.serve_forever, daemon=True).start()
    monkeypatch.undo()
    for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HTTPS_PROXY", f"http://127.0.0.1:{proxy.server_address[1]}")
    monkeypatch.setattr(runner, "_public_address", lambda host, p: v6)
    with pytest.raises(Exception, match="502"):
        runner.fetch("https://data.example.com/a.csv", tmp_path / "y", 1000, 10)
    proxy.shutdown()
    assert targets == [f"[{v6}]:443"]


def _proxy_refusing_connect(targets: list):
    import socketserver
    import threading

    class Refuse(socketserver.BaseRequestHandler):
        def handle(self):
            req = b""
            while b"\r\n\r\n" not in req:
                req += self.request.recv(4096)
            targets.append(req.split()[1].decode())
            self.request.sendall(b"HTTP/1.1 403 Forbidden\r\n\r\n")

    proxy = socketserver.TCPServer(("127.0.0.1", 0), Refuse)
    threading.Thread(target=proxy.serve_forever, daemon=True).start()
    return proxy


def test_fetch_says_why_a_proxy_refused_a_plain_http_download(tmp_path, monkeypatch):
    """Squid's default `deny CONNECT !SSL_ports` refuses a tunnel to port 80; the owner reads why and the
    way out (https) instead of a bare status line."""
    import httpx

    targets: list[str] = []
    proxy = _proxy_refusing_connect(targets)
    for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HTTP_PROXY", f"http://127.0.0.1:{proxy.server_address[1]}")
    monkeypatch.setattr(runner, "_public_address", lambda host, p: "93.184.216.34")
    with pytest.raises(httpx.ProxyError, match="403") as info:
        runner.fetch("http://data.example.com/a.csv", tmp_path / "x", 1000, 10)
    proxy.shutdown()
    assert targets == ["93.184.216.34:80"]
    assert "https://" in str(info.value) and not re.search("[가-힣]", str(info.value)), str(info.value)


def test_fetch_refuses_a_socks_proxy_instead_of_speaking_http_to_it(tmp_path, monkeypatch):
    import socket

    for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ALL_PROXY", "socks5://127.0.0.1:1080")
    monkeypatch.setattr(runner, "_public_address", lambda host, p: "93.184.216.34")
    monkeypatch.setattr(socket, "create_connection", lambda *a, **kw: pytest.fail("connected to the SOCKS port"))
    with pytest.raises(ValueError, match="socks5") as info:
        runner.fetch("https://data.example.com/a.csv", tmp_path / "x", 1000, 10)
    assert "HTTP CONNECT" in str(info.value), str(info.value)


def test_deployment_id_comes_from_the_database_identity_not_the_url_text():
    from justshipit.settings import Settings, settings

    url = settings().database_url
    alias = url.replace("127.0.0.1", "localhost")
    assert alias != url
    assert Settings(database_url=alias).deployment_id == settings().deployment_id


def test_deployment_id_is_never_a_fallback_while_the_database_is_unreachable():
    import psycopg
    from justshipit import settings as st

    asked: list[dict] = []

    def refused(dsn, **kw):
        asked.append(kw)
        raise psycopg.OperationalError("connection refused")

    s = st.Settings(database_url=st.settings().database_url)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(st.psycopg, "connect", refused)
        with pytest.raises(psycopg.OperationalError):
            _ = s.deployment_id
    assert asked[0]["connect_timeout"] == 2  # psycopg treats anything below 2 s as 2 s
    assert s.deployment_id == st.settings().deployment_id  # reachable again: the real id, cached from here on
