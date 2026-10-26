"""Pinned HTTP transport with allowlists and bounded challenge verification."""

import ipaddress
import re
import socket
import time
from datetime import UTC, datetime
from urllib.parse import urljoin, urlsplit

import httpx

from .models import Evidence, SourceConfig, digest


class FetchError(ValueError):
    """A safe, non-sensitive transport failure."""

    def __init__(self, message: str, *, retryable: bool = False, delay: float = 0):
        super().__init__(message)
        self.retryable = retryable
        self.delay = delay


class ResponseLimitError(FetchError):
    """Safe numeric diagnostics without session URLs or response contents."""

    def __init__(self, limit, expected=None, *, aggregate=False):
        self.limit = limit
        self.expected = expected
        self.aggregate = aggregate
        kind = "total" if aggregate else "response"
        size = f"; advertised bytes={expected}" if expected is not None else ""
        super().__init__(f"Response exceeds {kind} byte limit ({limit}{size})")


def validate_url(url: str, hosts: tuple[str, ...]) -> str:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in hosts
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or parsed.fragment
    ):
        raise FetchError("URL outside approved HTTPS source scope")
    return parsed.hostname


def public_address(host: str) -> str:
    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    ips = sorted({entry[4][0] for entry in addresses})
    if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
        raise FetchError("Non-public destination rejected")
    # Prefer IPv4 for portability; all returned addresses were checked.
    return next((ip for ip in ips if ":" not in ip), ips[0])


class Fetcher:
    def __init__(self, config: SourceConfig):
        self.config = config
        self.requests = 0
        self.received_bytes = 0
        self.last_request = 0.0

    def get(self, url: str) -> tuple[bytes, Evidence]:
        for attempt in range(3):
            try:
                return self._get_once(url)
            except FetchError as error:
                if not error.retryable or attempt == 2:
                    raise
                time.sleep(max(error.delay, 0.5 * 2**attempt))
        raise FetchError("Retry budget exhausted")

    def before_request(self, deadline=None):
        self.requests += 1
        limit = min(
            self.config.max_requests,
            (self.config.max_pages + self.config.max_events + self.config.max_resources)
            * 18,
        )
        if self.requests > limit:
            raise FetchError("Request budget exhausted")
        delay = max(
            0, self.config.request_interval - (time.monotonic() - self.last_request)
        )
        if deadline is not None and time.monotonic() + delay >= deadline:
            raise FetchError("Request deadline exhausted")
        time.sleep(delay)
        self.last_request = time.monotonic()

    def consume(self, count):
        self.received_bytes += count
        if self.received_bytes > self.config.max_total_bytes:
            raise FetchError("Collection exceeds total byte limit")

    def validate_request(self, url, method, content):
        host = validate_url(url, self.config.allowed_hosts)
        if method != "GET":
            if (
                method != "POST"
                or host != "simbli.eboardsolutions.com"
                or not re.fullmatch(
                    r"/[A-Za-z0-9_-]{1,160}(?:/[A-Za-z0-9_-]{1,160})?",
                    urlsplit(url).path,
                )
                or urlsplit(url).path.startswith("/Services/")
                or not isinstance(content, bytes)
                or len(content) > 65536
            ):
                raise FetchError("Unsupported transport request")
        elif content is not None:
            raise FetchError("GET request body rejected")
        return host

    def exchange(
        self,
        url,
        *,
        headers=None,
        deadline=None,
        max_bytes=None,
        method="GET",
        content=None,
    ):
        """One pinned request, no redirects/retries; POST restricted to verification."""
        host = self.validate_request(url, method, content)
        self.before_request(deadline)
        deadline = min(
            deadline or float("inf"), time.monotonic() + self.config.response_seconds
        )
        try:
            ip = public_address(host)
            pinned = httpx.URL(url).copy_with(host=ip)
            request_headers = httpx.Headers(headers or {})
            request_headers.update({"Host": host, "Accept-Encoding": "identity"})
            request_headers.setdefault(
                "User-Agent", "CivicPulseUpdater/0.1 (review-only; civpulse.org)"
            )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise FetchError("Request deadline exhausted")
            with httpx.Client(timeout=min(20, remaining), trust_env=False) as client:
                with client.stream(
                    method,
                    pinned,
                    content=content,
                    headers=request_headers,
                    extensions={"sni_hostname": host},
                ) as response:
                    limit = min(
                        max_bytes or self.config.max_bytes, self.config.max_bytes
                    )
                    if (
                        response.headers.get("content-encoding", "identity")
                        != "identity"
                    ):
                        raise FetchError("Compressed response rejected")
                    length = response.headers.get("content-length")
                    if length and length.isdigit():
                        advertised = int(length)
                        if advertised > limit:
                            raise ResponseLimitError(limit, advertised)
                        remaining_bytes = (
                            self.config.max_total_bytes - self.received_bytes
                        )
                        if advertised > remaining_bytes:
                            raise ResponseLimitError(
                                remaining_bytes, advertised, aggregate=True
                            )
                    data = bytearray()
                    for chunk in (
                        response.iter_raw() if response.status_code == 200 else ()
                    ):
                        self.consume(len(chunk))
                        data.extend(chunk)
                        if len(data) > limit:
                            raise ResponseLimitError(limit)
                        if time.monotonic() > deadline:
                            raise FetchError("Response exceeds duration limit")
                    body = bytes(data)
                    evidence = Evidence(
                        method=method,
                        sha256=digest(body),
                        url=url,
                        media_type=response.headers.get(
                            "content-type", "application/octet-stream"
                        ),
                        size=len(body),
                        fetched_at=datetime.now(UTC),
                    )
                    return body, evidence, response.status_code, response.headers
        except (httpx.HTTPError, OSError) as error:
            category = (
                "read_timeout"
                if isinstance(error, httpx.ReadTimeout)
                else "connect_timeout"
                if isinstance(error, httpx.ConnectTimeout)
                else "connection_error"
            )
            raise FetchError(
                f"Source connection failed ({category})", retryable=True
            ) from error

    def _get_once(self, url):
        redirects = []
        for _ in range(6):
            body, evidence, status, headers = self.exchange(url)
            if status in (301, 302, 303, 307, 308):
                if "location" not in headers:
                    raise FetchError("Redirect missing location")
                redirects.append(url)
                url = urljoin(url, headers["location"])
                continue
            if status != 200:
                retry_after = headers.get("retry-after", "0")
                delay = float(retry_after) if retry_after.isdigit() else 0
                retryable = (
                    status in (429, 502, 503, 504)
                    and delay <= 5
                    and retry_after.isdigit()
                )
                raise FetchError(
                    f"Source returned HTTP {status}", retryable=retryable, delay=delay
                )
            return body, evidence.model_copy(update={"redirects": tuple(redirects)})
        raise FetchError("Redirect limit exceeded")
