import httpx
import pytest

from civpulse_updater.transport import Fetcher, public_address, validate_url


@pytest.mark.parametrize(
    "url",
    [
        "http://www.bibbdems.com/events",
        "https://evil.test/",
        "https://www.bibbdems.com@127.0.0.1/",
        "https://www.bibbdems.com:444/",
        "file:///etc/passwd",
        "https://www.bibbdems.com/#fragment",
    ],
)
def test_url_policy(config, url):
    with pytest.raises(ValueError):
        validate_url(url, config.allowed_hosts)


def test_private_dns_denied(monkeypatch):
    monkeypatch.setattr(
        "socket.getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", ("127.0.0.1", 443))]
    )
    with pytest.raises(ValueError):
        public_address("www.bibbdems.com")


def install_transport(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr(
        "civpulse_updater.transport.public_address", lambda host: "93.184.216.34"
    )
    monkeypatch.setattr(
        "civpulse_updater.transport.httpx.Client",
        lambda **kw: original(transport=httpx.MockTransport(handler), **kw),
    )
    monkeypatch.setattr("civpulse_updater.transport.time.sleep", lambda _: None)


def test_ip_pinning_and_redirect_policy(monkeypatch, config):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.host == "93.184.216.34"
        assert request.headers["host"] == "www.bibbdems.com"
        assert request.extensions["sni_hostname"] == "www.bibbdems.com"
        return httpx.Response(302, headers={"location": "https://127.0.0.1/private"})

    install_transport(monkeypatch, handler)
    with pytest.raises(ValueError):
        Fetcher(config).get(config.url)
    assert len(calls) == 1


class Stream(httpx.SyncByteStream):
    def __iter__(self):
        yield b"a" * 1025


def test_size_limit(monkeypatch, config):
    install_transport(monkeypatch, lambda _: httpx.Response(200, stream=Stream()))
    with pytest.raises(ValueError, match="byte limit"):
        Fetcher(config.model_copy(update={"max_bytes": 1024})).get(config.url)


def test_advertised_size_reports_safe_numeric_limit(monkeypatch, config):
    from civpulse_updater.transport import ResponseLimitError

    install_transport(
        monkeypatch,
        lambda _: httpx.Response(
            200, headers={"content-length": "100000001"}, stream=Stream()
        ),
    )
    with pytest.raises(ResponseLimitError) as result:
        Fetcher(config).get(config.url)
    assert result.value.expected == 100000001
    assert not result.value.aggregate
    assert "https://" not in str(result.value)


def test_advertised_aggregate_limit_is_distinguished(monkeypatch, config):
    from civpulse_updater.transport import ResponseLimitError

    install_transport(
        monkeypatch,
        lambda _: httpx.Response(
            200, headers={"content-length": "1500"}, stream=Stream()
        ),
    )
    fetcher = Fetcher(config.model_copy(update={"max_total_bytes": 2000}))
    fetcher.received_bytes = 1000
    with pytest.raises(ResponseLimitError) as result:
        fetcher.get(config.url)
    assert result.value.aggregate and result.value.limit == 1000


def test_valid_transfer(monkeypatch, config):
    install_transport(monkeypatch, lambda _: httpx.Response(200, stream=Stream()))
    body, evidence = Fetcher(config).get(config.url)
    assert len(body) == evidence.size == 1025


def test_long_response_requires_explicit_bounded_budget(monkeypatch, config):
    from civpulse_updater.transport import FetchError

    clock = [100.0]

    class SlowStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b"a"
            clock[0] += 31
            yield b"b"

    install_transport(monkeypatch, lambda _: httpx.Response(200, stream=SlowStream()))
    monkeypatch.setattr("civpulse_updater.transport.time.monotonic", lambda: clock[0])
    with pytest.raises(FetchError, match="duration limit"):
        Fetcher(config).get(config.url)
    clock[0] = 100
    body, evidence = Fetcher(config.model_copy(update={"response_seconds": 180})).get(
        config.url
    )
    assert body == b"ab" and evidence.size == 2
    clock[0] = 100
    with pytest.raises(FetchError, match="duration limit"):
        Fetcher(config.model_copy(update={"response_seconds": 180})).exchange(
            config.url, deadline=110
        )


def test_deadline_is_checked_without_waiting_for_a_full_buffer(monkeypatch, config):
    from civpulse_updater.transport import FetchError

    clock = [100.0]
    reads = []

    class Trickle(httpx.SyncByteStream):
        def __iter__(self):
            for index in range(100):
                clock[0] += 10
                reads.append(index)
                yield b"a"

    install_transport(monkeypatch, lambda _: httpx.Response(200, stream=Trickle()))
    monkeypatch.setattr("civpulse_updater.transport.time.monotonic", lambda: clock[0])
    with pytest.raises(FetchError, match="duration limit"):
        Fetcher(config).get(config.url)
    assert len(reads) == 4


def test_transient_get_retry(monkeypatch, config):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503, headers={"retry-after": "0"})
        return httpx.Response(200, stream=Stream())

    install_transport(monkeypatch, handler)
    assert len(Fetcher(config).get(config.url)[0]) == 1025
    assert len(calls) == 2


def test_real_tls_connection_preserves_sni_and_host(monkeypatch, config):
    import ssl
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    import trustme

    ca = trustme.CA()
    certificate = ca.issue_cert("www.bibbdems.com")
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    certificate.configure_cert(server_context)
    names, hosts = [], []
    server_context.set_servername_callback(lambda sock, name, ctx: names.append(name))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            hosts.append(self.headers["Host"])
            self.send_response(200)
            self.send_header("Content-Length", "5")
            self.end_headers()
            self.wfile.write(b"hello")

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    server.socket = server_context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client_context = ssl.create_default_context()
    ca.configure_trust(client_context)
    original = httpx.Client

    class LocalPortClient(original):
        # Test-only remapping; production remains restricted to public HTTPS:443.
        def stream(self, method, url, **kwargs):
            return super().stream(
                method, url.copy_with(port=server.server_port), **kwargs
            )

    monkeypatch.setattr(
        "civpulse_updater.transport.public_address", lambda _: "127.0.0.1"
    )
    monkeypatch.setattr(
        "civpulse_updater.transport.httpx.Client",
        lambda **kw: LocalPortClient(verify=client_context, **kw),
    )
    try:
        assert Fetcher(config).get(config.url)[0] == b"hello"
        assert names == ["www.bibbdems.com"]
        assert hosts == ["www.bibbdems.com"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_source_budget_accepts_observed_listing(monkeypatch):
    from pathlib import Path

    from civpulse_updater.config import load_source

    class ObservedSize(httpx.SyncByteStream):
        def __iter__(self):
            # Measured official listing on October 25, 2026; no full-page fixture needed.
            yield b"x" * 2_006_558

    source = load_source(Path("sources"), "bibb-democrats")
    install_transport(monkeypatch, lambda _: httpx.Response(200, stream=ObservedSize()))
    body, _ = Fetcher(source).get(source.url)
    assert len(body) == 2_006_558


def test_total_download_budget_spans_requests(monkeypatch, config):
    install_transport(monkeypatch, lambda _: httpx.Response(200, stream=Stream()))
    fetcher = Fetcher(config.model_copy(update={"max_total_bytes": 2000}))
    assert len(fetcher.get(config.url)[0]) == 1025
    with pytest.raises(ValueError, match="total byte limit"):
        fetcher.get(config.url)


def test_safe_timeout_diagnostics(monkeypatch, config):
    def handler(request):
        raise httpx.ReadTimeout("private-source-token")

    install_transport(monkeypatch, handler)
    with pytest.raises(ValueError, match="read_timeout") as caught:
        Fetcher(config).exchange(config.url)
    assert "private-source-token" not in str(caught.value)


@pytest.mark.parametrize(
    "method,url,content",
    [
        ("DELETE", "https://www.bibbdems.com/", None),
        ("POST", "https://www.bibbdems.com/", b"proof"),
        ("POST", "https://simbli.eboardsolutions.com/Services/SaveMeeting", b"proof"),
        ("POST", "https://simbli.eboardsolutions.com/challenge", b"x" * 65537),
    ],
)
def test_transport_rejects_nonverification_writes(
    monkeypatch, config, method, url, content
):
    config = config.model_copy(
        update={"allowed_hosts": ("www.bibbdems.com", "simbli.eboardsolutions.com")}
    )
    monkeypatch.setattr(
        "civpulse_updater.transport.public_address",
        lambda *_: pytest.fail("invalid write reached DNS"),
    )
    with pytest.raises(ValueError):
        Fetcher(config).exchange(url, method=method, content=content)
