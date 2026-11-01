"""Bounded public registration sessions; authentication responses are never saved."""

import json
import logging
import re
import socket
from datetime import UTC, datetime

from websockets.exceptions import WebSocketException
from websockets.sync.client import connect

from .adapters.georgia_registration import APPS, HUB, ORIGIN, TENANT, read_registration
from .models import Evidence, canonical, digest
from .transport import Fetcher, FetchError, public_address
from .turnout_transport import QlikClient

TOKEN_URL = ORIGIN + "/qlik-oauth-create-user"


class GeorgiaRegistrationFetcher(Fetcher):
    def __init__(self, config, window, capture):
        super().__init__(config)
        self.window = window
        self.capture = capture
        self.active_app = None

    def save_frame(self, raw, direction):
        body = raw.encode()
        if len(body) > self.config.max_bytes:
            raise FetchError("Registration frame exceeds byte limit")
        self.capture(
            body,
            Evidence(
                method="RPC",
                transport="qlik",
                direction=direction,
                url=f"https://{TENANT}/app/{self.active_app}",
                sha256=digest(body),
                size=len(body),
                media_type="application/json",
                fetched_at=datetime.now(UTC),
            ),
        )

    def get(self, url):
        if url != HUB:
            raise FetchError("URL outside public registration capture scope")
        try:
            return self.snapshot()
        except FetchError:
            raise
        except (
            WebSocketException,
            OSError,
            TimeoutError,
            ValueError,
            KeyError,
            TypeError,
        ):
            raise FetchError(
                "Public registration capture failed; retained evidence may be replayed only if complete"
            ) from None

    def snapshot(self):
        bootstrap = {}
        for stage, url in (
            ("dashboard HTML", HUB),
            ("app configuration", ORIGIN + "/DH.VOTERS/js/vars.js"),
            ("tenant configuration", ORIGIN + "/shared-config.js"),
        ):
            try:
                body, evidence = super().get(url)
            except FetchError as error:
                raise FetchError(f"Registration {stage}: {error}") from None
            self.capture(body, evidence)
            bootstrap[url] = body.decode("utf-8")
        config = bootstrap[ORIGIN + "/DH.VOTERS/js/vars.js"]
        for name, app_id in zip(("primaryAppId", "tableAppId"), APPS, strict=True):
            if not re.search(
                r"var\s+" + name + r'\s*=\s*["\']' + re.escape(app_id) + r'["\']',
                config,
            ):
                raise FetchError("Public registration app configuration changed")
        if not re.search(
            r'var\s+tenant\s*=\s*["\']https://' + re.escape(TENANT) + r'["\']',
            bootstrap[ORIGIN + "/shared-config.js"],
        ):
            raise FetchError("Public registration tenant configuration changed")
        messages = {}
        for app_id in APPS:
            # Same anonymous endpoint used by Go Interactive. Keep token in memory.
            token_body, _, status, headers = self.exchange(
                TOKEN_URL, max_bytes=65536, body_statuses=(200, 201)
            )
            if status not in (200, 201) or "retry-after" in headers:
                raise FetchError(
                    f"Registration anonymous authentication returned HTTP {status}"
                )
            token = json.loads(token_body)["access_token"]
            if (
                not isinstance(token, str)
                or not token
                or len(token) > 32768
                or "\r" in token
                or "\n" in token
            ):
                raise FetchError("Invalid public registration token")
            self.active_app = app_id
            self.before_request()
            ip = public_address(TENANT)
            with socket.create_connection(
                (ip, 443), timeout=self.config.response_seconds
            ) as sock:
                with connect(
                    f"wss://{TENANT}/app/{app_id}",
                    sock=sock,
                    server_hostname=TENANT,
                    proxy=None,
                    origin=ORIGIN,
                    additional_headers={"Authorization": "Bearer " + token},
                    open_timeout=self.config.response_seconds,
                    close_timeout=2,
                    max_size=self.config.max_bytes,
                    max_queue=1,
                    compression=None,
                    logger=logging.Logger(
                        "civpulse.registration.qlik", level=logging.CRITICAL + 1
                    ),
                ) as ws:
                    client = QlikClient(ws, self)
                    read_registration(
                        client,
                        app_id,
                        self.window,
                        compact=True,
                        all_history=self.config.qlik_all_history,
                    )
                    messages[app_id] = client.messages
        body = canonical(
            {
                "schema_version": 2,
                "profile": "statistics-summary",
                "all_history": self.config.qlik_all_history,
                "app_ids": list(APPS),
                "window": self.window.model_dump(mode="json"),
                "bootstrap": bootstrap,
                "messages": messages,
            }
        ).encode()
        if len(body) > self.config.max_bytes:
            raise FetchError("Registration snapshot exceeds byte limit")
        return body, Evidence(
            method="RPC",
            transport="qlik",
            url=HUB,
            sha256=digest(body),
            size=len(body),
            media_type="application/json",
            fetched_at=datetime.now(UTC),
        )
