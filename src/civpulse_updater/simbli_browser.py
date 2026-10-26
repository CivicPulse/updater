"""Opt-in Simbli listing/attachment fallback with bounded verification POSTs with a pinned network broker.

The browser never connects to the source directly. All permitted requests are
fulfilled through Fetcher.exchange, sharing TLS/DNS, pacing and byte budgets.
"""

import json
import math
import os
import re
import tempfile
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup
from loguru import logger

from .adapters.simbli import ARCHIVE, LISTING, Simbli
from .models import Evidence, digest
from .transport import Fetcher, FetchError, validate_url

HOST = "simbli.eboardsolutions.com"
MIN_REQUEST_INTERVAL = 1.0
COOLDOWN = 30
MAX_BROWSER_ATTEMPTS_PER_HOUR = 45
MAX_BROWSER_ATTEMPTS_PER_RUN = 5
MAX_BROWSER_REQUESTS = 12
BROWSER_SECONDS = 45
MAX_RUN_REQUESTS = 120
MAX_SYSTEMATIC_REQUESTS = 2000
MAX_SCRIPT_BYTES = 2_000_000
MANUAL_SECONDS = 600


def validate_archive_request(content):
    """No caller-supplied filter expressions, identities, or write operations."""
    if not isinstance(content, bytes) or len(content) > 16384:
        raise FetchError("Invalid public archive request")
    try:
        data = json.loads(content)
        fixed = {
            "ListingType": 0,
            "CustomSort": 0,
            "SortColName": "DateTime",
            "IsSortDesc": True,
            "RecordCount": 50,
            "FilterExp": None,
            "ParentGroup": None,
            "IsUserLoggedIn": False,
            "UserID": "",
            "UserRole": None,
            "EncUserId": None,
            "HasAutoAuthAccess": False,
            "Id": 0,
            "SchoolID": "4013",
            "TimeZone": "0",
            "CreatedOn": "0001-01-01T00:00:00",
            "CreatedBy": None,
            "ModifiedOn": "0001-01-01T00:00:00",
            "ModifiedBy": None,
            "DeletedBy": None,
            "DeletedOnUTC": None,
            "IsDeleted": False,
        }
        if not isinstance(data, dict) or set(data) != set(fixed) | {
            "RecordStart",
            "ConnectionString",
            "SecurityToken",
        }:
            raise ValueError
        if any(type(data[k]) is not type(v) or data[k] != v for k, v in fixed.items()):
            raise ValueError
        start = data["RecordStart"]
        if type(start) is not int or not 51 <= start <= 2451 or (start - 1) % 50:
            raise ValueError
        if any(
            not isinstance(data[k], str) or not 1 <= len(data[k]) <= 4096
            for k in ("ConnectionString", "SecurityToken")
        ):
            raise ValueError
    except (ValueError, KeyError, TypeError, UnicodeDecodeError) as error:
        raise FetchError("Invalid public archive request") from error


def manual_host(url):
    """Interactive browser resources: public HTTPS, with Civic Pulse excluded."""
    host = urlsplit(url).hostname or ""
    if host == "civpulse.org" or host.endswith(".civpulse.org"):
        raise FetchError("Civic Pulse connections are disabled")
    return validate_url(url, (host,))


def attachment_url(url):
    """Accept only the public Bibb document routes already emitted by the adapter."""
    validate_url(url, (HOST,))
    parts = urlsplit(url)
    query = parse_qs(parts.query, keep_blank_values=True)
    if query.get("S") != ["4013"]:
        return False
    if parts.path == "/Meetings/Attachment.aspx":
        return set(query) == {"S", "AID", "MID"} and all(
            len(query[k]) == 1 and re.fullmatch(r"[1-9][0-9]{0,12}", query[k][0])
            for k in ("AID", "MID")
        )
    if parts.path in (
        "/SB_Meetings/ViewMeetingMinuteDocument.aspx",
        "/SB_Meetings/ViewAgendaMinuteDocument.aspx",
    ):
        return (
            set(query) == {"S", "aid"}
            and len(query["aid"]) == 1
            and bool(re.fullmatch(r"[a-zA-Z0-9+/=]{1,200}", query["aid"][0]))
        )
    return False


def meeting_page(url):
    validate_url(url, (HOST,))
    parts = urlsplit(url)
    query = parse_qs(parts.query, keep_blank_values=True)
    return (
        parts.path == "/SB_Meetings/ViewMeeting.aspx"
        and set(query) == {"S", "MID"}
        and query["S"] == ["4013"]
        and len(query["MID"]) == 1
        and bool(re.fullmatch(r"[1-9][0-9]{0,12}", query["MID"][0]))
    )


def recovery_target(url, *, headed=False):
    return url == LISTING or attachment_url(url) or (headed and meeting_page(url))


def recovered_content(url, body, config):
    if config.browser_headed and meeting_page(url):
        if challenge(body):
            return False
        html = body.decode("utf-8", errors="replace")
        return all(
            re.search(r"\bvar\s+" + variable + r"\s*=\s*'[^'\r\n]+'", html)
            for variable in ("sToken", "enDID", "enMeetingID")
        )
    if url != LISTING:
        if (
            config.browser_headed
            and attachment_url(url)
            and urlsplit(url).path == "/Meetings/Attachment.aspx"
            and len(body) <= 16384
            and not challenge(body)
        ):
            # After human verification a missing legacy attachment can return
            # this empty official form. End the browser wait and retain cookies;
            # normal document validation must still reject its HTML bytes.
            html = BeautifulSoup(body, "html.parser")
            title = "Attachment | Bibb County School District"
            if (
                html.title is not None
                and html.title.get_text(" ", strip=True) == title
                and html.get_text(" ", strip=True) == title
                and html.find("form") is not None
                and html.find(["iframe", "embed", "object", "a"]) is None
            ):
                return True
        return binary_document(body)
    if challenge(body):
        return False
    try:
        Simbli(config).discover(body, LISTING, digest(body))
        return True
    except (ValueError, KeyError, TypeError):
        return False


def challenge(body):
    head = body[:65536].lower()
    return head.lstrip().startswith((b"<!doctype html", b"<html")) and any(
        marker in head
        for marker in (
            b"incapsula",
            b"imperva",
            b"pardon our interruption",
            b"_incapsula_resource",
        )
    )


def binary_document(body):
    return body.startswith(
        (
            b"%PDF-",
            b"PK\x03\x04",
            bytes.fromhex("d0cf11e0a1b11ae1"),
            b"\x89PNG\r\n\x1a\n",
            b"\xff\xd8\xff",
        )
    )


class ManualVerificationRequired(FetchError):
    """The source requests interactive verification; never solve it automatically."""


class HostPolicy:
    """Persist pacing/cooldown under the CLI's existing exclusive state lock."""

    def __init__(self, root: Path):
        self.path = root / "simbli-access.json"
        self.state = {
            "not_before": 0,
            "next_request": 0,
            "browser_not_before": 0,
            "attempts": [],
        }
        if self.path.exists():
            self.state = json.loads(self.path.read_text())
            for key in ("not_before", "next_request", "browser_not_before"):
                value = self.state[key]
                if (
                    not isinstance(value, (float, int))
                    or not math.isfinite(value)
                    or value < 0
                ):
                    raise FetchError("Invalid Simbli cooldown state")
            if not isinstance(self.state["attempts"], list) or any(
                not isinstance(v, (float, int)) or not math.isfinite(v)
                for v in self.state["attempts"]
            ):
                raise FetchError("Invalid Simbli cooldown state")

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, path = tempfile.mkstemp(dir=self.path.parent, prefix=".simbli-policy-")
        try:
            with os.fdopen(fd, "w") as handle:
                json.dump(self.state, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(path, self.path)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def check(self):
        if time.time() < self.state["not_before"]:
            raise FetchError("Simbli cooldown active; defer collection")

    def pace(self, deadline=None):
        self.check()
        delay = max(0, self.state["next_request"] - time.time())
        if delay > 10 or (deadline and time.monotonic() + delay >= deadline):
            raise FetchError("Simbli pacing deadline exhausted")
        time.sleep(delay)
        self.state["next_request"] = time.time() + MIN_REQUEST_INTERVAL
        self.save()  # Reserve before sending, including if this process crashes.

    def reserve_browser(self, *, wait=False):
        self.check()
        # A successful earlier recovery may be followed by a different document
        # challenge. Wait locally, without network traffic or bypassing deferrals.
        while wait and time.time() < self.state["browser_not_before"]:
            delay = self.state["browser_not_before"] - time.time()
            if delay > COOLDOWN + 1:
                raise FetchError("Simbli browser cooldown exceeds local wait budget")
            self.check()
            logger.info("Waiting for Simbli browser cooldown: {} seconds", round(delay))
            time.sleep(min(60, delay))
        self.check()
        now = time.time()
        attempts = [v for v in self.state["attempts"] if v > now - 3600]
        if (
            now < self.state["browser_not_before"]
            or len(attempts) >= MAX_BROWSER_ATTEMPTS_PER_HOUR
        ):
            raise FetchError("Simbli browser attempt cooldown active")
        self.state["attempts"] = attempts + [now]
        self.state["browser_not_before"] = now + COOLDOWN
        self.save()

    def defer(self, retry_after=None):
        until = time.time() + COOLDOWN
        if retry_after:
            try:
                wait = float(retry_after)
                if math.isfinite(wait) and wait > 0:
                    until = max(until, time.time() + wait)
            except ValueError:
                try:
                    until = max(until, parsedate_to_datetime(retry_after).timestamp())
                except (ValueError, TypeError, OverflowError):
                    pass
        self.state["not_before"] = max(until, self.state["not_before"])
        self.save()


class SimbliFetcher(Fetcher):
    def __init__(self, config, root, capture, browser=None):
        if config.adapter != "simbli" or config.allowed_hosts != (HOST,):
            raise ValueError("Simbli transport scope mismatch")
        super().__init__(
            config.model_copy(
                update={
                    "request_interval": max(
                        MIN_REQUEST_INTERVAL, config.request_interval
                    ),
                    "max_requests": min(
                        MAX_SYSTEMATIC_REQUESTS
                        if config.systematic
                        else MAX_RUN_REQUESTS,
                        config.max_requests,
                    ),
                }
            )
        )
        self.policy = HostPolicy(root)
        self.capture = capture
        self.browser = browser or BrowserAttachment
        self.browser_attempted = False
        self.attempted_targets = set()
        self.last_exchange = None
        self.in_browser = False
        self.session_cookies = []
        self.session_user_agent = None

    def validate_request(self, url, method, content):
        if method == "POST" and url == ARCHIVE and not self.in_browser:
            validate_archive_request(content)
            return validate_url(url, (HOST,))
        if self.config.browser_headed and self.in_browser:
            host = manual_host(url)
            if method == "GET" and content is None:
                return host
            # Interactive verification only; civic API mutation remains disabled.
            if (
                method == "POST"
                and isinstance(content, bytes)
                and len(content) <= self.config.max_bytes
                and not (host == HOST and urlsplit(url).path.startswith("/Services/"))
            ):
                return host
            raise FetchError("Unsupported manual verification request")
        return super().validate_request(url, method, content)

    def get_listing_page(self, request):
        """The sole civic POST is the observed public, read-only archive query."""
        if request.url != ARCHIVE:
            raise FetchError("Unsupported listing POST destination")
        validate_archive_request(request.content)
        self.policy.check()
        self.capture(
            request.content,
            Evidence(
                method="POST",
                direction="request",
                sha256=digest(request.content),
                url=ARCHIVE,
                media_type="application/json",
                size=len(request.content),
                fetched_at=datetime.now(UTC),
            ),
        )
        body, evidence, status, headers = self.exchange(
            ARCHIVE,
            method="POST",
            content=request.content,
            headers={"content-type": "application/json"},
        )
        if status != 200 or challenge(body):
            self.capture(body, evidence)
            self.policy.defer(headers.get("retry-after"))
            raise FetchError("Public archive query unavailable; defer collection")
        return body, evidence

    def before_request(self, deadline=None):
        self.policy.pace(deadline)
        super().before_request(deadline)

    def exchange(self, url, **kwargs):
        # Reuse only this run's successful browser session, through the same
        # pinned transport and budgets. Respect cookie host/path/expiry scopes.
        # The public JSON API uses its own updater identity: replaying the
        # Firefox identity there caused reproducible response read timeouts.
        headers = {}
        if (
            self.session_user_agent
            and urlsplit(url).hostname == HOST
            and not urlsplit(url).path.startswith("/Services/api/")
        ):
            headers["user-agent"] = self.session_user_agent
            path = urlsplit(url).path or "/"
            cookies = []
            for cookie in self.session_cookies:
                cp = cookie["path"]
                if (
                    cookie["domain"].lstrip(".") in (HOST, "eboardsolutions.com")
                    and (
                        path == cp
                        or path.startswith(cp if cp.endswith("/") else cp + "/")
                    )
                    and (cookie["expires"] == -1 or cookie["expires"] > time.time())
                ):
                    cookies.append(f"{cookie['name']}={cookie['value']}")
            if cookies:
                headers["cookie"] = "; ".join(cookies)
        headers.update(kwargs.pop("headers", None) or {})
        result = super().exchange(url, headers=headers, **kwargs)
        self.last_exchange = result
        body, evidence, status, headers = result
        if self.in_browser:
            evidence = evidence.model_copy(update={"transport": "playwright"})
            result = body, evidence, status, headers
            self.capture(body, evidence)
        if status in (401, 403, 429, 503) or "retry-after" in headers:
            self.policy.defer(headers.get("retry-after"))
            raise FetchError("Simbli requested deferral; collection paused by cooldown")
        return result

    def get(self, url):
        self.policy.check()
        # Deliberately no HTTP retry loop for Simbli: one attempt per request.
        body, evidence = self._get_once(url)
        if not challenge(body):
            return body, evidence
        self.capture(body, evidence)
        if (
            not self.config.browser_fallback
            or not recovery_target(url, headed=self.config.browser_headed)
            or url in self.attempted_targets
            or len(self.attempted_targets) >= MAX_BROWSER_ATTEMPTS_PER_RUN
        ):
            self.policy.defer()
            raise FetchError("Simbli challenge encountered; defer collection")
        self.policy.reserve_browser(wait=self.browser_attempted)
        self.attempted_targets.add(url)
        self.browser_attempted = True
        seed = self.last_exchange
        try:
            self.in_browser = True
            result = self.browser(self).recover(url, seed)
            if not recovered_content(url, result[0], self.config):
                raise FetchError(
                    "Browser did not return a valid listing or supported document"
                )
            return result
        except (ValueError, OSError) as error:
            self.policy.defer()
            if isinstance(error, ManualVerificationRequired):
                raise FetchError(
                    "Simbli requires interactive CAPTCHA verification; defer collection"
                ) from error
            raise FetchError(
                "Simbli browser recovery failed; defer collection"
            ) from error
        finally:
            self.in_browser = False


class BrowserAttachment:
    def __init__(self, fetcher):
        self.fetcher = fetcher
        self.result = None
        self.error = None
        self.requests = 0
        self.allowed_scripts = set()
        self.verified_scripts = set()
        self.verification_posts = 0
        self.manual_verification = False
        self.target = None
        self.seed = None
        self.deadline = 0

    def scripts(self, body, url):
        # No general site crawl. Only challenge scripts explicitly referenced by HTML.
        if not challenge(body):
            return
        soup = BeautifulSoup(body, "html.parser")
        candidates = [script["src"] for script in soup.select("script[src]")]
        # Simbli's saved interstitial creates a script element dynamically. Only
        # recognize a literal root-relative assignment, never evaluate source or
        # permit arbitrary URLs requested by its JavaScript.
        for script in soup.select("script:not([src])"):
            candidates.extend(
                match.group(2)
                for match in re.finditer(
                    r"\bscriptElement\.src\s*=\s*([\"'])(/[^\"'\s\\]{1,1024})\1\s*;",
                    script.get_text(),
                )
            )
        for candidate in candidates:
            src = urljoin(url, candidate)
            try:
                validate_url(src, (HOST,))
            except ValueError:
                continue
            path = urlsplit(src).path
            if path == "/_Incapsula_Resource" or re.fullmatch(
                r"/[A-Za-z0-9_-]{1,160}(?:/[A-Za-z0-9_-]{1,160})?", path
            ):
                self.allowed_scripts.add(src)

    def permitted(self, request):
        if self.fetcher.config.browser_headed:
            try:
                host = manual_host(request.url)
                return len(request.url) <= 8192 and (
                    request.method == "GET"
                    or (
                        request.method == "POST"
                        and not (
                            host == HOST
                            and urlsplit(request.url).path.startswith("/Services/")
                        )
                    )
                )
            except ValueError:
                return False
        try:
            validate_url(request.url, (HOST,))
        except ValueError:
            return False
        if len(request.url) > 8192:
            return False
        if request.method == "POST":
            parts = urlsplit(request.url)
            return (
                self.verification_posts < 2
                and request.resource_type in ("fetch", "xhr")
                and (
                    (parts.path, parts.query) in self.verified_scripts
                    or (
                        (parts.path, "") in self.verified_scripts
                        and parse_qs(parts.query, keep_blank_values=True)
                        == {"d": [HOST]}
                    )
                )
                and not parts.path.startswith("/Services/")
                and bool(
                    re.fullmatch(
                        r"/[A-Za-z0-9_-]{1,160}(?:/[A-Za-z0-9_-]{1,160})?", parts.path
                    )
                )
            )
        if request.method != "GET":
            return False
        if request.url == self.target:
            return request.resource_type in ("document", "fetch", "xhr")
        if request.url in self.allowed_scripts:
            return request.resource_type == "script"
        return urlsplit(
            request.url
        ).path == "/_Incapsula_Resource" and request.resource_type in (
            "document",
            "script",
            "image",
            "fetch",
            "xhr",
        )

    def route(self, route):
        request = route.request
        if urlsplit(request.url).hostname in (
            "js.hcaptcha.com",
            "hcaptcha.com",
            "www.hcaptcha.com",
        ):
            self.manual_verification = True
        if self.result or self.error or not self.permitted(request):
            route.abort()
            return
        try:
            if time.monotonic() >= self.deadline:
                raise FetchError("Browser duration budget exhausted")
            if self.seed is not None and request.url == self.target:
                body, evidence, status, headers = self.seed
                self.seed = None
            else:
                self.requests += 1
                if (
                    not self.fetcher.config.browser_headed
                    and self.requests > MAX_BROWSER_REQUESTS
                ):
                    raise FetchError("Browser request budget exhausted")
                # Keep only ordinary browser headers. No credentials or proxy headers.
                headers_in = {
                    k: v
                    for k, v in request.all_headers().items()
                    if k.lower()
                    in {
                        "user-agent",
                        "accept",
                        "accept-language",
                        "cookie",
                        "referer",
                        "content-type",
                        "origin",
                    }
                }
                content = None
                if request.method == "POST":
                    content = request.post_data_buffer or b""
                    post_limit = (
                        self.fetcher.config.max_bytes
                        if self.fetcher.config.browser_headed
                        else 65536
                    )
                    if len(content) > post_limit:
                        raise FetchError(
                            "Challenge verification request exceeds byte limit"
                        )
                    self.verification_posts += 1
                    self.fetcher.capture(
                        content,
                        Evidence(
                            sha256=digest(content),
                            url=request.url,
                            size=len(content),
                            media_type=headers_in.get(
                                "content-type", "application/octet-stream"
                            ),
                            fetched_at=datetime.now(UTC),
                            transport="playwright",
                            method="POST",
                            direction="request",
                        ),
                    )
                body, evidence, status, headers = self.fetcher.exchange(
                    request.url,
                    method=request.method,
                    content=content,
                    headers=headers_in,
                    deadline=self.deadline,
                    max_bytes=(
                        None
                        if request.url == self.target
                        or self.fetcher.config.browser_headed
                        else MAX_SCRIPT_BYTES
                    ),
                )
            accepted = (200, 204) if request.method == "POST" else (200,)
            if self.fetcher.config.browser_headed:
                accepted += (301, 302, 303, 307, 308)
            if status not in accepted:
                raise FetchError("Browser source returned non-success status")
            if (
                request.method == "GET"
                and request.url in self.allowed_scripts
                and "javascript" in evidence.media_type
            ):
                parts = urlsplit(request.url)
                self.verified_scripts.update(
                    {(parts.path, parts.query), (parts.path, "")}
                )
            if request.url == self.target and recovered_content(
                self.target, body, self.fetcher.config
            ):
                self.result = (
                    body,
                    evidence.model_copy(update={"transport": "playwright"}),
                )
                route.abort()  # Bytes are captured; do not render or download the file.
                return
            self.scripts(body, request.url)
            # Preserve cookies/CSP while stripping transfer-specific headers.
            outgoing = {
                k: v
                for k, v in headers.items()
                if k.lower()
                not in {
                    "content-length",
                    "transfer-encoding",
                    "connection",
                    "content-encoding",
                    "set-cookie",
                }
            }
            cookies = headers.get_list("set-cookie")
            if cookies:
                outgoing["set-cookie"] = "\n".join(cookies)
            route.fulfill(status=status, headers=outgoing, body=body)
        except (ValueError, OSError) as error:
            self.error = error
            route.abort()

    def recover(self, url, seed):
        if not recovery_target(url, headed=self.fetcher.config.browser_headed):
            raise FetchError("Browser target is not a Bibb listing or attachment")
        try:
            from playwright.sync_api import Error, sync_playwright
        except ImportError as error:
            raise FetchError(
                "Install the browser extra and selected Playwright browser"
            ) from error
        self.target, self.seed = url, seed
        self.deadline = time.monotonic() + (
            MANUAL_SECONDS if self.fetcher.config.browser_headed else BROWSER_SECONDS
        )
        self.scripts(seed[0], url)
        try:
            with sync_playwright() as playwright:
                engine = self.fetcher.config.browser_engine
                options = {
                    "headless": not self.fetcher.config.browser_headed,
                    "timeout": 15000,
                    "proxy": {"server": "http://127.0.0.1:9", "bypass": ""},
                }
                if engine == "chromium":
                    options.update(
                        chromium_sandbox=True,
                        args=[
                            "--proxy-bypass-list=<-loopback>",
                            "--disable-quic",
                            "--disable-background-networking",
                            "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                        ],
                    )
                else:
                    options["firefox_user_prefs"] = {
                        "media.peerconnection.enabled": False,
                        "network.proxy.allow_hijacking_localhost": True,
                        "network.http.http3.enable": False,
                        "network.dns.disablePrefetch": True,
                        "network.prefetch-next": False,
                    }
                with getattr(playwright, engine).launch(**options) as browser:
                    context = browser.new_context(
                        service_workers="block", accept_downloads=False
                    )
                    context.route("**/*", self.route)
                    context.route_web_socket("**/*", lambda ws: ws.close())
                    page = context.new_page()
                    if self.fetcher.config.browser_headed:
                        logger.info(
                            "Firefox manual verification: solve the CAPTCHA in the window. "
                            "Collection resumes automatically on success. "
                            "Ten minutes available; pacing, run request and download limits apply. "
                            "close the window to cancel."
                        )
                    browser_user_agent = page.evaluate("navigator.userAgent")
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    except Error:
                        # Downloads and page-initiated navigations can interrupt
                        # goto. Keep servicing routes until the shared deadline.
                        pass
                    reload_at = time.monotonic() + 10
                    reloaded = False
                    while not self.result and not self.error:
                        remaining = self.deadline - time.monotonic()
                        if remaining <= 0:
                            break
                        try:
                            if (
                                not self.fetcher.config.browser_headed
                                and not reloaded
                                and time.monotonic() >= reload_at
                            ):
                                reloaded = True
                                page.reload(
                                    wait_until="domcontentloaded",
                                    timeout=min(15000, remaining * 1000),
                                )
                            else:
                                page.wait_for_timeout(min(100, remaining * 1000))
                        except Error:
                            if page.is_closed():
                                break
                    logger.info(
                        "Simbli browser recovery: recovered={}, requests={}",
                        self.result is not None,
                        self.requests,
                    )
                    if self.result:
                        self.fetcher.session_cookies = context.cookies()
                        self.fetcher.session_user_agent = browser_user_agent
                    context.close()
            if self.error:
                if isinstance(self.error, FetchError):
                    logger.warning("Simbli browser stopped: {}", self.error)
                raise FetchError("Browser broker rejected recovery") from self.error
            if self.result is None:
                if self.manual_verification:
                    raise ManualVerificationRequired(
                        "Simbli requires interactive CAPTCHA verification"
                    )
                raise FetchError("Browser challenge remained unresolved")
            return self.result
        except Error as error:
            raise FetchError(
                "Playwright browser unavailable or recovery failed"
            ) from error
