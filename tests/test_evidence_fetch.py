"""fetch_page opens a link only at a global address, at every redirect hop, and connects to the address it checked."""
import httpx
import pytest
from justshipit.services import evidence, netguard

PUBLIC = "93.184.216.34"


def resolve_to(monkeypatch, table):
    lookups = []

    def getaddrinfo(host, *a, **kw):
        lookups.append(host)
        return [(2, 1, 6, "", (ip, 0)) for ip in table.get(host, [PUBLIC])]

    monkeypatch.setattr(netguard.socket, "getaddrinfo", getaddrinfo)
    return lookups


def serve(monkeypatch, handler):
    def streamed(request):
        """A body handed over whole is sent as a stream, the way a server sends it."""
        r = handler(request)
        if r.is_stream_consumed:
            r = httpx.Response(r.status_code, headers=r.headers, stream=httpx.ByteStream(r.content))
        return r

    monkeypatch.setattr(netguard, "transport", lambda scheme, hostname: httpx.MockTransport(streamed))


def never(request):
    pytest.fail(f"connected to {request.url}")


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.5", "192.168.1.9", "169.254.169.254", "100.64.3.4", "::1",
                                "fe80::1", "::ffff:127.0.0.1", "0.0.0.0"])
def test_a_name_that_resolves_to_a_private_address_is_never_connected_to(monkeypatch, ip):
    resolve_to(monkeypatch, {"sneaky.example.com": [PUBLIC, ip]})
    serve(monkeypatch, never)
    assert evidence.fetch_page("https://sneaky.example.com/x")[0] == "url_unreachable"


def test_a_literal_private_address_is_refused(monkeypatch):
    serve(monkeypatch, never)
    for url in ("http://127.0.0.1:8000/admin", "http://[::1]/", "http://169.254.169.254/latest/meta-data/"):
        assert evidence.fetch_page(url)[0] == "url_unreachable"


def test_a_redirect_to_a_private_address_is_refused_at_that_hop(monkeypatch):
    resolve_to(monkeypatch, {"inner.example.com": ["10.0.0.7"]})
    seen = []

    def handler(request):
        seen.append(request.headers["host"])
        return httpx.Response(302, headers={"location": "http://inner.example.com/secret"})

    serve(monkeypatch, handler)
    assert evidence.fetch_page("https://front.example.com/start")[0] == "url_unreachable"
    assert seen == ["front.example.com"]


def test_redirects_are_capped(monkeypatch):
    resolve_to(monkeypatch, {})
    hops = []

    def handler(request):
        hops.append(request.url.path)
        return httpx.Response(302, headers={"location": f"/{len(hops)}"})

    serve(monkeypatch, handler)
    assert evidence.fetch_page("https://loop.example.com/")[0] == "url_unreachable"
    assert len(hops) == evidence.MAX_HOPS + 1


def test_it_connects_to_the_checked_address_with_the_real_name_and_follows_a_public_redirect(monkeypatch):
    lookups = resolve_to(monkeypatch, {})
    seen = []

    def handler(request):
        seen.append((str(request.url), request.headers["host"], request.extensions.get("sni_hostname")))
        if request.headers["host"] == "a.example.com":
            return httpx.Response(301, headers={"location": "https://b.example.com/page"})
        return httpx.Response(200, text="<html><body>Hello <b>World</b></body></html>",
                              headers={"content-type": "text/html"})

    serve(monkeypatch, handler)
    assert evidence.fetch_page("https://a.example.com/old") == ("url_ok", "HTTP 200", " hello world ")
    assert lookups == ["a.example.com", "b.example.com"]
    assert seen == [(f"https://{PUBLIC}/old", "a.example.com", "a.example.com"),
                    (f"https://{PUBLIC}/page", "b.example.com", "b.example.com")]


def test_status_handling_is_unchanged(monkeypatch):
    resolve_to(monkeypatch, {})
    for code, want in ((403, "unverifiable"), (429, "unverifiable"), (404, "url_unreachable"), (500, "url_unreachable")):
        serve(monkeypatch, lambda request, code=code: httpx.Response(code))
        assert evidence.fetch_page("https://x.example.com/")[0] == want
    serve(monkeypatch, lambda request: httpx.Response(200, json={"a": 1}))
    assert evidence.fetch_page("https://x.example.com/")[2] is None


EMBEDDED = ["fec0::1", "::7f00:1", "::10.0.0.5", "2002:7f00:1::1", "2002:a00:5::1"]


@pytest.mark.parametrize("ip", EMBEDDED)
def test_an_ipv6_address_that_is_site_local_or_carries_an_ipv4_address_is_never_connected_to(monkeypatch, ip):
    resolve_to(monkeypatch, {"sneaky.example.com": [ip]})
    serve(monkeypatch, never)
    assert evidence.fetch_page("https://sneaky.example.com/x")[0] == "url_unreachable"


@pytest.mark.parametrize("ip", EMBEDDED)
def test_such_an_address_written_in_the_link_is_refused_too(monkeypatch, ip):
    serve(monkeypatch, never)
    assert evidence.fetch_page(f"http://[{ip}]/")[0] == "url_unreachable"


def html(content, **headers):
    body = {"stream": httpx.ByteStream(content)} if isinstance(content, bytes) else {"content": content}
    return httpx.Response(200, headers={"content-type": "text/html", **headers}, **body)


def test_a_page_that_never_ends_is_read_up_to_the_cap_and_no_further(monkeypatch):
    resolve_to(monkeypatch, {})
    monkeypatch.setattr(evidence, "MAX_BYTES", 10_000, raising=False)
    sent = []

    def forever():
        for _ in range(2_000):
            sent.append(1)
            yield b"word " * 200

    serve(monkeypatch, lambda request: html(forever()))
    status, _, page = evidence.fetch_page("https://big.example.com/")
    assert status == "url_ok" and page.startswith("word word") and len(page) <= 10_000
    assert len(sent) <= 12, "the stream is dropped at the cap"


def test_a_compressed_page_is_inflated_only_up_to_the_cap(monkeypatch):
    import gzip
    resolve_to(monkeypatch, {})
    monkeypatch.setattr(evidence, "MAX_BYTES", 10_000, raising=False)
    bomb = gzip.compress(b"a" * 20_000_000)
    assert len(bomb) < 30_000
    seen = []

    def handler(request):
        seen.append(request.headers["accept-encoding"])
        return html(bomb, **{"content-encoding": "gzip"})

    serve(monkeypatch, handler)
    status, _, page = evidence.fetch_page("https://bomb.example.com/")
    assert status == "url_ok" and page == "a" * 10_000
    assert seen == ["gzip"], "only an encoding the capped reader inflates is asked for"

    serve(monkeypatch, lambda request: html(gzip.compress(b"<p>Hello <b>World</b></p>"), **{"content-encoding": "gzip"}))
    assert evidence.fetch_page("https://small.example.com/")[2] == " hello world "
    serve(monkeypatch, lambda request: html(b"\x00" * 50, **{"content-encoding": "br"}))
    assert evidence.fetch_page("https://other.example.com/")[0] == "url_unreachable"


def test_a_page_sent_too_slowly_is_given_up_on_at_the_total_time_limit(monkeypatch):
    import time
    resolve_to(monkeypatch, {})
    clock = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])

    def drip():
        for _ in range(10):
            clock[0] += 14  # each read comes inside the per-read timeout
            yield b"<p>still here</p>"

    serve(monkeypatch, lambda request: html(drip()))
    assert evidence.fetch_page("https://slow.example.com/")[:2] == ("url_unreachable", "ReadTimeout")


@pytest.mark.parametrize("hostile", ["<script" * 20_000, "<" * 60_000, "<style>" * 20_000, "<a " * 60_000,
                                     "<script></scrip" * 20_000])
def test_hostile_markup_is_stripped_in_time_that_grows_with_its_length(hostile):
    import time
    start = time.perf_counter()
    evidence._text(hostile)
    assert time.perf_counter() - start < 2


def test_script_and_style_go_with_their_content_and_a_stray_bracket_stays():
    assert evidence._text("A<SCRIPT type=x>var a = '<b>';</Script >B<style>p{}</style>C") == "a b c"
    assert evidence._text("shown<script>never closed <p>hidden</p>") == "shown"
    assert evidence._text("1 &lt; 2 and a < b") == "1 < 2 and a < b"
    assert evidence._text("<scripted>kept</scripted>") == " kept "
