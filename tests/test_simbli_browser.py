import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from civpulse_updater.models import SourceConfig, digest
from civpulse_updater.simbli_browser import (
    BrowserAttachment,
    HostPolicy,
    SimbliFetcher,
    attachment_url,
)
from civpulse_updater.transport import FetchError

URL = "https://simbli.eboardsolutions.com/Meetings/Attachment.aspx?S=4013&AID=123&MID=138067"
HTML = b'<!doctype html><html><title>Pardon Our Interruption</title><script src="/fixture-challenge"></script></html>'
PDF = b"%PDF-1.7\npublic test document"


@pytest.fixture
def source():
    return SourceConfig(
        id="bibb-school-board",
        adapter="simbli",
        url="https://simbli.eboardsolutions.com/SB_Meetings/SB_MeetingListing.aspx?S=4013",
        allowed_hosts=("simbli.eboardsolutions.com",),
        browser_fallback=True,
    )


class Stream(httpx.SyncByteStream):
    def __init__(self, body):
        self.body = body

    def __iter__(self):
        yield self.body


def mock_network(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr(
        "civpulse_updater.transport.public_address", lambda _: "93.184.216.34"
    )
    monkeypatch.setattr(
        "civpulse_updater.transport.httpx.Client",
        lambda **kw: original(transport=httpx.MockTransport(handler), **kw),
    )


def no_pacing(monkeypatch):
    # Browser tests keep real clocks; only the wait policy is replaced.
    monkeypatch.setattr(HostPolicy, "pace", lambda self, deadline=None: self.check())

    def reserve(self, deadline=None):
        self.requests += 1
        if self.requests > self.config.max_requests:
            raise FetchError("Request budget exhausted")

    monkeypatch.setattr("civpulse_updater.transport.Fetcher.before_request", reserve)


@pytest.mark.parametrize(
    "url",
    [
        URL.replace("4013", "9999"),
        URL + "&action=delete",
        URL.replace("https:", "http:"),
        URL.replace("simbli.eboardsolutions.com", "127.0.0.1"),
        "https://simbli.eboardsolutions.com/Services/api/SaveMeeting",
        URL.replace("AID=123", "AID=0"),
    ],
)
def test_scope_rejects_unapproved_targets(url):
    try:
        allowed = attachment_url(url)
    except ValueError:
        allowed = False
    assert not allowed


def test_policy_persists_pacing_cooldown_and_attempt_limit(tmp_path, monkeypatch):
    clock = [10000.0]
    sleeps = []
    monkeypatch.setattr("civpulse_updater.simbli_browser.time.time", lambda: clock[0])
    monkeypatch.setattr(
        "civpulse_updater.simbli_browser.time.sleep",
        lambda n: (sleeps.append(n), clock.__setitem__(0, clock[0] + n)),
    )
    policy = HostPolicy(tmp_path)
    policy.pace()
    HostPolicy(tmp_path).pace()
    assert sleeps == [0, 1]
    policy = HostPolicy(tmp_path)
    policy.reserve_browser()
    with pytest.raises(FetchError):
        HostPolicy(tmp_path).reserve_browser()
    clock[0] += 301
    HostPolicy(tmp_path).reserve_browser()
    clock[0] += 301
    HostPolicy(tmp_path).reserve_browser()
    clock[0] += 301
    # The hourly cap is independent of spacing (e.g. imported shared state).
    policy = HostPolicy(tmp_path)
    policy.state["attempts"] = [clock[0] - 600] * 45
    policy.save()
    with pytest.raises(FetchError):
        HostPolicy(tmp_path).reserve_browser()
    policy = HostPolicy(tmp_path)
    policy.defer("3600")
    assert HostPolicy(tmp_path).state["not_before"] == clock[0] + 3600
    with pytest.raises(FetchError):
        HostPolicy(tmp_path).check()


@pytest.mark.parametrize("status", [403, 429, 503])
def test_deferral_never_launches_browser_or_retries(
    tmp_path, source, monkeypatch, status
):
    no_pacing(monkeypatch)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status, headers={"Retry-After": "3600"}, stream=Stream(b"")
        )

    mock_network(monkeypatch, handler)
    f = SimbliFetcher(
        source,
        tmp_path,
        lambda *_: None,
        browser=lambda *_: pytest.fail("browser must not run"),
    )
    with pytest.raises(FetchError):
        f.get(URL)
    with pytest.raises(FetchError):
        SimbliFetcher(source, tmp_path, lambda *_: None).get(URL)
    assert len(calls) == 1
    assert HostPolicy(tmp_path).state["not_before"] >= time.time() + 3590


def test_unknown_html_does_not_launch_browser(tmp_path, source, monkeypatch):
    no_pacing(monkeypatch)
    mock_network(
        monkeypatch, lambda _: httpx.Response(200, stream=Stream(b"<html>login</html>"))
    )
    f = SimbliFetcher(
        source,
        tmp_path,
        lambda *_: None,
        browser=lambda *_: pytest.fail("unexpected browser"),
    )
    assert f.get(URL)[0] == b"<html>login</html>"


def test_failed_browser_stops_remaining_network_and_preserves_challenge(
    tmp_path, source, monkeypatch
):
    no_pacing(monkeypatch)
    captures = []
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, stream=Stream(HTML))

    mock_network(monkeypatch, handler)

    class Failed:
        def __init__(self, fetcher):
            pass

        def recover(self, *_):
            raise FetchError("test unresolved")

    f = SimbliFetcher(source, tmp_path, lambda b, e: captures.append(b), browser=Failed)
    with pytest.raises(FetchError):
        f.get(URL)
    with pytest.raises(FetchError):
        f.get(URL)
    assert calls and len(calls) == 1 and captures == [HTML]
    assert f.browser_attempted


def test_browser_request_policy(source, tmp_path):
    broker = BrowserAttachment(SimbliFetcher(source, tmp_path, lambda *_: None))
    broker.target = URL
    broker.scripts(HTML, URL)
    assert broker.permitted(
        SimpleNamespace(url=URL, method="GET", resource_type="document")
    )
    for url, method, kind in [
        (URL, "POST", "document"),
        ("https://evil.test/a", "GET", "script"),
        ("http://127.0.0.1/", "GET", "fetch"),
        ("https://simbli.eboardsolutions.com/Services/api/SaveMeeting", "GET", "fetch"),
        (URL, "GET", "image"),
    ]:
        assert not broker.permitted(
            SimpleNamespace(url=url, method=method, resource_type=kind)
        )


def test_total_bytes_includes_browser_resources(tmp_path, source, monkeypatch):
    no_pacing(monkeypatch)
    source = source.model_copy(update={"max_total_bytes": 1100})
    mock_network(monkeypatch, lambda _: httpx.Response(200, stream=Stream(b"x" * 600)))
    f = SimbliFetcher(source, tmp_path, lambda *_: None)
    f.exchange(URL)
    f.in_browser = True
    with pytest.raises(FetchError, match="total byte limit"):
        f.exchange(URL)
    assert f.received_bytes == 1200


def test_browser_request_budget_no_extra_get(tmp_path, source, monkeypatch):
    no_pacing(monkeypatch)
    f = SimbliFetcher(source, tmp_path, lambda *_: None)
    f.exchange = lambda *_a, **_k: pytest.fail("budget must be checked before network")
    broker = BrowserAttachment(f)
    broker.target = URL
    broker.requests = 12
    broker.deadline = time.monotonic() + 30
    aborted = []
    req = SimpleNamespace(
        url=URL, method="GET", resource_type="document", all_headers=lambda: {}
    )
    broker.route(SimpleNamespace(request=req, abort=lambda: aborted.append(True)))
    assert aborted and broker.error


@pytest.mark.browser
@pytest.mark.parametrize("engine", ["chromium", "firefox"])
@pytest.mark.parametrize("listing", [False, True])
def test_real_browser_runs_script_keeps_cookie_and_blocks_other_traffic(
    tmp_path, source, monkeypatch, engine, listing
):
    source = source.model_copy(update={"browser_engine": engine})
    pytest.importorskip("playwright.sync_api")
    no_pacing(monkeypatch)
    target = source.url if listing else URL
    listing_bytes = (
        b"ContentPlaceHolder1_MeetingGrid_newGrid.dataBuffer="
        + (Path(__file__).parent / "fixtures/simbli/listing.json").read_bytes()
        + b";"
    )
    recovered = listing_bytes if listing else PDF
    calls = []
    captures = []
    script = b"document.cookie='verified=yes; Domain=eboardsolutions.com; Path=/; Secure; SameSite=Lax'; fetch('/Services/api/SaveMeeting',{method:'POST'}).catch(()=>{}); fetch('https://example.com/blocked').catch(()=>{});"

    def handler(request):
        calls.append(
            (request.url.path, request.method, request.headers.get("cookie", ""))
        )
        assert request.url.host == "93.184.216.34"
        assert request.extensions["sni_hostname"] == "simbli.eboardsolutions.com"
        if request.url.path == "/fixture-challenge":
            return httpx.Response(
                200, headers={"content-type": "text/javascript"}, stream=Stream(script)
            )
        assert request.url.path in (
            "/Meetings/Attachment.aspx",
            "/SB_Meetings/SB_MeetingListing.aspx",
        )
        dynamic_html = HTML.replace(
            b'<script src="/fixture-challenge"></script>',
            b"<script>const scriptElement = document.createElement('script'); scriptElement.src = '/fixture-challenge'; document.head.appendChild(scriptElement);</script>",
        )
        body = (
            (
                recovered
                if request.url.path == target.split(".com")[1].split("?")[0]
                else PDF
            )
            if "verified=yes" in request.headers.get("cookie", "")
            else dynamic_html
        )
        return httpx.Response(
            200,
            headers={"content-type": "application/pdf" if body == PDF else "text/html"},
            stream=Stream(body),
        )

    mock_network(monkeypatch, handler)
    f = SimbliFetcher(source, tmp_path, lambda b, e: captures.append((b, e)))
    body, evidence = f.get(target)
    assert body == recovered and evidence.transport == "playwright"
    assert len(calls) == 3 and all(method == "GET" for _, method, _ in calls)
    assert f.received_bytes == sum(len(body) for body, _ in captures)
    assert b"scriptElement.src" in captures[0][0] and captures[-1][0] == recovered
    assert captures[-1][1].sha256 == digest(recovered)
    if listing:
        assert f.get(URL)[0] == PDF
        assert len(calls) == 4
        assert f.browser_attempted


def test_browser_user_agent_is_not_combined_with_updater_identity(
    tmp_path, source, monkeypatch
):
    no_pacing(monkeypatch)
    seen = []

    def handler(request):
        seen.extend(request.headers.get_list("user-agent"))
        return httpx.Response(200, stream=Stream(PDF))

    mock_network(monkeypatch, handler)
    fetcher = SimbliFetcher(source, tmp_path, lambda *_: None)
    fetcher.exchange(URL, headers={"user-agent": "Mozilla/5.0 test-browser"})
    assert seen == ["Mozilla/5.0 test-browser"]


def test_literal_dynamic_challenge_script_is_discovered_without_broadening_scope(
    source, tmp_path
):
    broker = BrowserAttachment(SimbliFetcher(source, tmp_path, lambda *_: None))
    broker.target = URL
    body = b"""<!doctype html><html>Pardon Our Interruption<script>
    const scriptElement = document.createElement('script');
    scriptElement.src = "/challenge-bootstrap/opaque-token?s=fixture";
    document.head.appendChild(scriptElement);
    </script></html>"""
    broker.scripts(body, URL)
    script_url = (
        "https://simbli.eboardsolutions.com/challenge-bootstrap/opaque-token?s=fixture"
    )
    assert broker.permitted(
        SimpleNamespace(url=script_url, method="GET", resource_type="script")
    )
    assert not broker.permitted(
        SimpleNamespace(url=script_url, method="POST", resource_type="script")
    )
    assert not broker.permitted(
        SimpleNamespace(
            url=script_url.replace("opaque-token", "unlisted"),
            method="GET",
            resource_type="script",
        )
    )


def test_listing_scope_and_content_validation(source):
    from civpulse_updater.simbli_browser import recovered_content, recovery_target

    assert recovery_target(source.url)
    assert not recovery_target(source.url + "&action=other")
    assert not recovery_target(source.url.replace("4013", "9999"))
    assert not recovered_content(source.url, HTML, source)
    assert not recovered_content(source.url, PDF, source)
    assert not recovered_content(
        source.url, b"ContentPlaceHolder1_MeetingGrid_newGrid.dataBuffer={};", source
    )


def test_session_cookie_scopes_and_expiry(tmp_path, source, monkeypatch):
    no_pacing(monkeypatch)
    seen = []

    def handler(request):
        seen.append(request.headers.get("cookie"))
        return httpx.Response(200, stream=Stream(PDF))

    mock_network(monkeypatch, handler)
    f = SimbliFetcher(source, tmp_path, lambda *_: None)
    f.session_user_agent = "test-browser"
    base = dict(domain="simbli.eboardsolutions.com", path="/", expires=-1, value="yes")
    f.session_cookies = [
        base | dict(name="good"),
        base | dict(name="parent", domain=".eboardsolutions.com"),
        base | dict(name="expired", expires=1),
        base | dict(name="wrong_path", path="/Meet"),
        base | dict(name="other_host", domain="example.org"),
    ]
    f.get(URL)
    assert seen == ["good=yes; parent=yes"]


def test_browser_session_does_not_change_api_identity(tmp_path, source, monkeypatch):
    no_pacing(monkeypatch)
    seen = []

    def handler(request):
        seen.append((request.headers["user-agent"], request.headers.get("cookie")))
        return httpx.Response(200, stream=Stream(b"{}"))

    mock_network(monkeypatch, handler)
    f = SimbliFetcher(source, tmp_path, lambda *_: None)
    f.session_user_agent = "Firefox fixture"
    f.session_cookies = [
        dict(
            name="browser",
            value="yes",
            domain="simbli.eboardsolutions.com",
            path="/",
            expires=-1,
        )
    ]
    f.get("https://simbli.eboardsolutions.com/Services/api/GetMeeting/?endid=fixture")
    f.get(URL)
    assert seen == [
        ("CivicPulseUpdater/0.1 (review-only; civpulse.org)", None),
        ("Firefox fixture", "browser=yes"),
    ]


def test_distinct_recovery_targets_wait_and_never_retry_same_target(
    tmp_path, source, monkeypatch
):
    from datetime import UTC, datetime

    from civpulse_updater.models import Evidence

    no_pacing(monkeypatch)
    mock_network(monkeypatch, lambda _: httpx.Response(200, stream=Stream(HTML)))
    waits = []
    monkeypatch.setattr(
        HostPolicy, "reserve_browser", lambda self, *, wait=False: waits.append(wait)
    )

    class Recovery:
        def __init__(self, fetcher):
            pass

        def recover(self, url, seed):
            return PDF, Evidence(
                sha256=digest(PDF),
                url=url,
                size=len(PDF),
                media_type="application/pdf",
                fetched_at=datetime.now(UTC),
                transport="playwright",
            )

    f = SimbliFetcher(source, tmp_path, lambda *_: None, browser=Recovery)
    assert f.get(URL)[0] == PDF
    assert f.get(URL.replace("AID=123", "AID=124"))[0] == PDF
    assert waits == [False, True]
    with pytest.raises(FetchError):
        f.get(URL)
    assert waits == [False, True]


def test_browser_wait_honors_full_cooldown(tmp_path, monkeypatch):
    clock = [10000.0]
    monkeypatch.setattr("civpulse_updater.simbli_browser.time.time", lambda: clock[0])
    monkeypatch.setattr(
        "civpulse_updater.simbli_browser.time.sleep",
        lambda n: clock.__setitem__(0, clock[0] + n),
    )
    p = HostPolicy(tmp_path)
    p.reserve_browser()
    p.reserve_browser(wait=True)
    assert clock[0] == 10030
    assert p.state["attempts"] == [10000, 10030]
    p.defer("600")
    with pytest.raises(FetchError):
        p.reserve_browser(wait=True)
    assert clock[0] == 10030


def test_run_recovery_limit_stops_before_launch(tmp_path, source, monkeypatch):
    from civpulse_updater.simbli_browser import MAX_BROWSER_ATTEMPTS_PER_RUN

    no_pacing(monkeypatch)
    mock_network(monkeypatch, lambda _: httpx.Response(200, stream=Stream(HTML)))
    f = SimbliFetcher(
        source,
        tmp_path,
        lambda *_: None,
        browser=lambda *_: pytest.fail("run budget exceeded"),
    )
    f.attempted_targets = {str(i) for i in range(MAX_BROWSER_ATTEMPTS_PER_RUN)}
    with pytest.raises(FetchError):
        f.get(URL)
    assert f.requests == 1


@pytest.mark.browser
def test_firefox_waits_for_delayed_challenge_completion(tmp_path, source, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    source = source.model_copy(update={"browser_engine": "firefox"})
    no_pacing(monkeypatch)
    calls = []
    script = b"setTimeout(()=>{document.cookie='ready=yes; Path=/; Secure'; location.reload();},4500);"

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/fixture-challenge":
            return httpx.Response(
                200, headers={"content-type": "text/javascript"}, stream=Stream(script)
            )
        body = PDF if "ready=yes" in request.headers.get("cookie", "") else HTML
        return httpx.Response(
            200,
            headers={"content-type": "application/pdf" if body == PDF else "text/html"},
            stream=Stream(body),
        )

    mock_network(monkeypatch, handler)
    f = SimbliFetcher(source, tmp_path, lambda *_: None)
    assert f.get(URL)[0] == PDF
    assert len(calls) == 3


@pytest.mark.browser
def test_firefox_bounded_challenge_verification_post(tmp_path, source, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    source = source.model_copy(update={"browser_engine": "firefox"})
    no_pacing(monkeypatch)
    calls = []
    captures = []
    html = HTML.replace(b"/fixture-challenge", b"/challenge-verify?s=fixture")
    script = b"fetch('/challenge-verify?d=simbli.eboardsolutions.com',{method:'POST',headers:{'Content-Type':'text/plain'},body:'fixture-proof'}).then(r=>r.json()).then(()=>location.reload());"

    def handler(request):
        calls.append(request.method)
        if request.url.path == "/challenge-verify":
            if request.method == "POST":
                assert request.content == b"fixture-proof"
                return httpx.Response(
                    200,
                    headers={
                        "content-type": "application/json",
                        "set-cookie": "verified=yes; Path=/; Secure; SameSite=Lax",
                    },
                    stream=Stream(b"{}"),
                )
            return httpx.Response(
                200, headers={"content-type": "text/javascript"}, stream=Stream(script)
            )
        body = PDF if "verified=yes" in request.headers.get("cookie", "") else html
        return httpx.Response(
            200,
            headers={"content-type": "application/pdf" if body == PDF else "text/html"},
            stream=Stream(body),
        )

    mock_network(monkeypatch, handler)
    f = SimbliFetcher(source, tmp_path, lambda b, e: captures.append((b, e)))
    assert f.get(URL)[0] == PDF
    assert calls == ["GET", "GET", "POST", "GET"]
    assert f.received_bytes == len(html) + len(script) + 2 + len(PDF)
    assert [(b, e.method) for b, e in captures if e.direction == "request"] == [
        (b"fixture-proof", "POST")
    ]


def test_verification_post_requires_fetched_script_and_obeys_caps(tmp_path, source):
    f = SimbliFetcher(
        source, tmp_path, lambda *_: pytest.fail("oversize evidence must not be stored")
    )
    f.exchange = lambda *_a, **_kw: pytest.fail("must not reach network")
    broker = BrowserAttachment(f)
    req = SimpleNamespace(
        url="https://simbli.eboardsolutions.com/challenge/verify",
        method="POST",
        resource_type="fetch",
        post_data_buffer=b"x" * 65537,
        all_headers=lambda: {},
    )
    assert not broker.permitted(req)
    broker.verified_scripts.add(("/challenge/verify", ""))
    assert broker.permitted(req)
    broker.deadline = time.monotonic() + 30
    aborted = []
    broker.route(SimpleNamespace(request=req, abort=lambda: aborted.append(True)))
    assert aborted and broker.error
    broker.verification_posts = 2
    assert not broker.permitted(req)
    broker.verification_posts = 0
    req.url = "https://simbli.eboardsolutions.com/Services/SaveMeeting"
    broker.verified_scripts.add(("/Services/SaveMeeting", ""))
    assert not broker.permitted(req)


def test_captcha_is_detected_without_contacting_provider(tmp_path, source):
    broker = BrowserAttachment(SimbliFetcher(source, tmp_path, lambda *_: None))
    request = SimpleNamespace(
        url="https://js.hcaptcha.com/1/api.js", method="GET", resource_type="script"
    )
    aborted = []
    broker.route(SimpleNamespace(request=request, abort=lambda: aborted.append(True)))
    assert broker.manual_verification and aborted and broker.requests == 0


def test_verification_query_is_exact_host_only(tmp_path, source):
    broker = BrowserAttachment(SimbliFetcher(source, tmp_path, lambda *_: None))
    broker.verified_scripts.add(("/challenge-verify", ""))
    for query, permitted in [
        ("d=simbli.eboardsolutions.com", True),
        ("d=example.com", False),
        ("d=simbli.eboardsolutions.com&action=save", False),
        ("d=simbli.eboardsolutions.com&d=simbli.eboardsolutions.com", False),
    ]:
        request = SimpleNamespace(
            url="https://simbli.eboardsolutions.com/challenge-verify?" + query,
            method="POST",
            resource_type="fetch",
        )
        assert broker.permitted(request) is permitted


@pytest.mark.parametrize(
    "url,method,allowed",
    [
        ("https://js.hcaptcha.com/1/api.js", "GET", True),
        ("https://api.hcaptcha.com/checkcaptcha/token", "POST", True),
        ("https://fonts.gstatic.com/font.woff2", "GET", True),
        ("https://simbli.eboardsolutions.com/_Incapsula_Resource", "POST", True),
        ("https://simbli.eboardsolutions.com/Services/api/SaveMeeting", "POST", False),
        ("https://civpulse.org/api", "GET", False),
        ("https://www.civpulse.org/api", "POST", False),
        ("http://js.hcaptcha.com/1/api.js", "GET", False),
        ("https://js.hcaptcha.com/1/api.js", "DELETE", False),
    ],
)
def test_manual_network_scope(tmp_path, source, url, method, allowed):
    source = SourceConfig.model_validate(
        source.model_dump() | {"browser_headed": True, "browser_engine": "firefox"}
    )
    f = SimbliFetcher(source, tmp_path, lambda *_: None)
    broker = BrowserAttachment(f)
    broker.verification_posts = 5  # Manual mode removes the automatic two-POST cap.
    request = SimpleNamespace(url=url, method=method, resource_type="fetch")
    assert broker.permitted(request) is allowed
    f.in_browser = True
    if allowed:
        assert f.validate_request(url, method, b"proof" if method == "POST" else None)
    else:
        with pytest.raises(FetchError):
            f.validate_request(url, method, b"proof" if method == "POST" else None)
    f.in_browser = False
    if "hcaptcha" in url:
        with pytest.raises(FetchError):
            f.validate_request(url, method, None)


@pytest.mark.browser
def test_manual_firefox_resumes_and_preserves_evidence(tmp_path, source, monkeypatch):
    from playwright.sync_api import BrowserType

    source = SourceConfig.model_validate(
        source.model_dump() | {"browser_headed": True, "browser_engine": "firefox"}
    )
    no_pacing(monkeypatch)
    original = BrowserType.launch
    launch_options = []

    def launch(self, **options):
        launch_options.append(options.copy())
        # CI has no display. Exercise the same broker/session with headless rendering.
        return original(self, **(options | {"headless": True}))

    monkeypatch.setattr(BrowserType, "launch", launch)
    captures = []
    calls = []
    html = b'<!doctype html><html>Pardon Our Interruption<script src="https://js.hcaptcha.com/1/api.js"></script></html>'
    # Synthetic verification fixture only; never interacts with a real CAPTCHA.
    script = b"fetch('/_Incapsula_Resource',{method:'POST',body:'x'.repeat(70000)}).then(()=>location.reload());"

    def handler(request):
        calls.append((request.headers["host"], request.method))
        if request.headers["host"] == "js.hcaptcha.com":
            return httpx.Response(
                200, headers={"content-type": "text/javascript"}, stream=Stream(script)
            )
        if request.method == "POST":
            return httpx.Response(
                200,
                headers={
                    "set-cookie": "verified=yes; Path=/; Secure",
                    "content-type": "text/plain",
                },
                stream=Stream(b"ok"),
            )
        body = PDF if "verified=yes" in request.headers.get("cookie", "") else html
        return httpx.Response(
            200,
            headers={"content-type": "application/pdf" if body == PDF else "text/html"},
            stream=Stream(body),
        )

    mock_network(monkeypatch, handler)
    f = SimbliFetcher(source, tmp_path, lambda b, e: captures.append((b, e)))
    assert f.get(URL)[0] == PDF
    assert launch_options[0]["headless"] is False
    assert len(calls) == 4
    assert any(e.direction == "request" and b == b"x" * 70000 for b, e in captures)
    assert f.session_cookies and f.session_user_agent
    assert f.received_bytes == len(html) + len(script) + 2 + len(PDF)


def test_manual_still_enforces_run_budget(tmp_path, source, monkeypatch):
    source = source.model_copy(
        update={"browser_headed": True, "browser_engine": "firefox", "max_requests": 1}
    )
    f = SimbliFetcher(source, tmp_path, lambda *_: None)
    f.in_browser = True
    mock_network(monkeypatch, lambda _: httpx.Response(200, stream=Stream(b"ok")))
    no_pacing(monkeypatch)
    assert f.exchange("https://js.hcaptcha.com/1/api.js")[0] == b"ok"
    with pytest.raises(FetchError, match="budget"):
        f.exchange("https://js.hcaptcha.com/1/api.js")


def test_manual_rejects_private_dns_before_connection(tmp_path, source, monkeypatch):
    source = source.model_copy(
        update={"browser_headed": True, "browser_engine": "firefox"}
    )
    f = SimbliFetcher(source, tmp_path, lambda *_: None)
    f.in_browser = True
    no_pacing(monkeypatch)
    monkeypatch.setattr(
        "civpulse_updater.transport.socket.getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("127.0.0.1", 443))],
    )
    monkeypatch.setattr(
        "civpulse_updater.transport.httpx.Client",
        lambda **k: pytest.fail("must not connect"),
    )
    with pytest.raises(FetchError, match="Non-public"):
        f.exchange("https://js.hcaptcha.com/1/api.js")


def test_manual_meeting_page_scope_and_content(source):
    from civpulse_updater.simbli_browser import recovered_content, recovery_target

    target = "https://simbli.eboardsolutions.com/SB_Meetings/ViewMeeting.aspx?S=4013&MID=138066"
    assert not recovery_target(target)
    assert recovery_target(target, headed=True)
    assert not recovery_target(target.replace("4013", "9999"), headed=True)
    assert not recovery_target(target + "&action=delete", headed=True)
    manual = source.model_copy(
        update={"browser_headed": True, "browser_engine": "firefox"}
    )
    assert not recovered_content(target, HTML, manual)
    assert not recovered_content(target, b"<html>login</html>", manual)
    body = b"<html><script>var sToken='a';var enDID='b';var enMeetingID='c';</script></html>"
    assert recovered_content(target, body, manual)
