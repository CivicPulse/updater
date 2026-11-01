"""Georgia's public website token and narrowly scoped read-only bill search."""

import hashlib
import json
import re
import time
from urllib.parse import urlencode, urlsplit

from .models import canonical, digest
from .transport import Fetcher, FetchError

API = "https://www.legis.ga.gov/api/"
# Public client constants, published by the legislature's Angular application.
PUBLIC_CLIENT_KEY = "QFpCwKfd7fjVEXFFwSu36BwwcP83xYgxLAhLYmKkletvarconst"
SEARCH_SIZE = 200


class GeorgiaFetcher(Fetcher):
    def __init__(self, config, capture):
        super().__init__(config)
        self.capture = capture
        self.token = None

    def validate_request(self, url, method, content):
        if method == "POST":
            match = re.fullmatch(
                r"Legislation/Search/200/(\d{1,3})", url.removeprefix(API)
            )
            if (
                not url.startswith(API)
                or not match
                or int(match[1]) >= self.config.max_pages
                or content
                != canonical({"sessionId": self.config.legislature_session_id}).encode()
            ):
                raise FetchError("Georgia permits only bounded session bill search")
            return super().validate_request(url, "GET", None)
        return super().validate_request(url, method, content)

    def exchange(self, url, **kwargs):
        # Never forward the anonymous bearer token to another origin or redirects.
        if not url.startswith(API) or urlsplit(url).netloc != "www.legis.ga.gov":
            raise FetchError("Georgia request outside official API")
        headers = dict(kwargs.pop("headers", {}) or {})
        for attempt in range(2):
            if self.token is None:
                ms = str(int(time.time() * 1000))
                query = urlencode(
                    {
                        "key": hashlib.sha512(
                            (PUBLIC_CLIENT_KEY + ms).encode()
                        ).hexdigest(),
                        "ms": ms,
                    }
                )
                body, _, status, _ = super().exchange(
                    API + "authentication/token?" + query
                )
                if status != 200:
                    raise FetchError("Georgia anonymous token unavailable")
                token = json.loads(body)
                if not isinstance(token, str) or not token or len(token) > 16384:
                    raise FetchError("Invalid Georgia anonymous token")
                self.token = token  # Memory only; never evidence or logs.
            headers["Authorization"] = "Bearer " + self.token
            result = super().exchange(url, headers=headers, **kwargs)
            if result[2] != 401 or attempt == 1:
                return result
            # Long session captures can outlive a token. Renew once, retaining
            # the shared request/byte limits and ordinary pacing.
            self.token = None
        raise FetchError("Georgia authorization unavailable")

    def get_listing_page(self, request):
        # Validate before obtaining a token or making any network request.
        self.validate_request(request.url, "POST", request.content)
        body, evidence, status, _ = self.exchange(
            request.url,
            method="POST",
            content=request.content,
            headers={"Content-Type": "application/json"},
        )
        if status != 200:
            raise FetchError(f"Georgia search returned HTTP {status}")
        self.capture(
            request.content,
            evidence.model_copy(
                update={
                    "direction": "request",
                    "sha256": digest(request.content),
                    "size": len(request.content),
                    "media_type": "application/json",
                }
            ),
        )
        return body, evidence
