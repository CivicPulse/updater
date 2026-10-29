"""Bounded public dashboard session; bearer tokens never enter saved evidence."""

import json
import logging
import re
import socket
import time
from datetime import UTC, datetime

from websockets.exceptions import WebSocketException
from websockets.sync.client import connect

from .adapters.georgia_turnout import APP, HUB, ORIGIN, TENANT, read_tables
from .models import Evidence, canonical, digest
from .transport import Fetcher, FetchError, public_address

ENGINE_URL = f"https://{TENANT}/app/{APP}"
TOKEN_URL = ORIGIN + "/qlik-oauth-create-user"


class QlikClient:
    def __init__(self, ws, fetcher):
        self.ws = ws
        self.fetcher = fetcher
        self.messages = []
        self.sequence = 0

    def call(self, handle, method, params):
        # This client is private to read_tables; there is no CLI for custom RPCs.
        if method not in {
            "OpenDoc",
            "GetAppLayout",
            "GetObject",
            "GetLayout",
            "GetProperties",
            "GetListObjectData",
            "ClearAll",
            "SelectListObjectValues",
            "GetHyperCubeData",
            "CreateSessionObject",
        }:
            raise FetchError("Unsupported turnout RPC")
        self.fetcher.before_request()
        self.sequence += 1
        request = canonical(
            {
                "jsonrpc": "2.0",
                "id": self.sequence,
                "handle": handle,
                "method": method,
                "params": params,
            }
        )
        self.fetcher.save_frame(request, "request")
        self.ws.send(request)
        deadline = time.monotonic() + self.fetcher.config.response_seconds
        # Notifications are bounded by the same bytes/deadline as RPC replies.
        for _ in range(100):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise FetchError("Turnout RPC deadline exhausted")
            raw = self.ws.recv(timeout=remaining)
            if not isinstance(raw, str):
                raise FetchError("Unexpected binary turnout response")
            self.fetcher.consume(len(raw.encode()))
            self.fetcher.save_frame(raw, "response")
            response = json.loads(raw)
            if response.get("id") != self.sequence:
                if "id" in response:
                    raise FetchError("Unexpected turnout RPC identity")
                continue
            if "error" in response:
                raise FetchError("Public turnout RPC failed")
            self.messages.append({"request": request, "response": raw})
            return response["result"]
        raise FetchError("Turnout notification budget exhausted")


class GeorgiaTurnoutFetcher(Fetcher):
    def __init__(self, config, window, capture):
        super().__init__(config)
        self.window = window
        self.capture = capture

    def save_frame(self, raw, direction):
        body = raw.encode()
        if len(body) > self.config.max_bytes:
            raise FetchError("Turnout frame exceeds byte limit")
        self.capture(
            body,
            Evidence(
                method="RPC",
                transport="qlik",
                direction=direction,
                url=ENGINE_URL,
                sha256=digest(body),
                size=len(body),
                media_type="application/json",
                fetched_at=datetime.now(UTC),
            ),
        )

    def get(self, url):
        if url != HUB:
            raise FetchError("URL outside public turnout capture scope")
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
            # Exceptions can contain bearer headers; never surface their text.
            raise FetchError(
                "Public turnout capture failed; retained evidence may be replayed only if complete"
            ) from None

    def snapshot(self):
        bootstrap = {}
        for stage, url in (
            ("dashboard HTML", HUB),
            ("app configuration", ORIGIN + "/DH.ELECTION2024/js/vars.js"),
            ("tenant configuration", ORIGIN + "/shared-config.js"),
        ):
            try:
                body, evidence = super().get(url)
            except FetchError as error:
                # FetchError messages are sanitized by the pinned HTTP transport.
                # Identify the fixed public stage, never headers or response bodies.
                raise FetchError(f"Turnout {stage}: {error}") from None
            self.capture(body, evidence)
            bootstrap[url] = body.decode("utf-8")
        if not re.search(
            r'var\s+appId\s*=\s*["\']' + re.escape(APP) + r'["\']',
            bootstrap[ORIGIN + "/DH.ELECTION2024/js/vars.js"],
        ) or not re.search(
            r'var\s+tenant\s*=\s*["\']https://' + re.escape(TENANT) + r'["\']',
            bootstrap[ORIGIN + "/shared-config.js"],
        ):
            raise FetchError("Public turnout app configuration changed")
        # The same anonymous GET used by the dashboard's Go Interactive button.
        # Neither token bytes nor response metadata are persisted.
        token_body, _, status, headers = self.exchange(
            TOKEN_URL, max_bytes=65536, body_statuses=(200, 201)
        )
        if status not in (200, 201):
            raise FetchError(f"Turnout anonymous authentication returned HTTP {status}")
        if "retry-after" in headers:
            raise FetchError("Public dashboard authentication unavailable")
        token = json.loads(token_body)["access_token"]
        if (
            not isinstance(token, str)
            or not token
            or len(token) > 32768
            or "\r" in token
            or "\n" in token
        ):
            raise FetchError("Invalid public dashboard token")
        self.before_request()
        ip = public_address(TENANT)
        # Pin the validated address; websockets still verifies TLS against TENANT.
        # A passed socket disables proxy selection. Sync connect doesn't redirect.
        with socket.create_connection(
            (ip, 443), timeout=self.config.response_seconds
        ) as sock:
            with connect(
                ENGINE_URL.replace("https://", "wss://", 1),
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
                logger=logging.Logger("civpulse.qlik", level=logging.CRITICAL + 1),
            ) as ws:
                client = QlikClient(ws, self)
                read_tables(client, self.window)
        body = canonical(
            {
                "schema_version": 1,
                "app_id": APP,
                "window": self.window.model_dump(mode="json"),
                "bootstrap": bootstrap,
                "messages": client.messages,
            }
        ).encode()
        if len(body) > self.config.max_bytes:
            raise FetchError("Turnout snapshot exceeds byte limit")
        # Snapshot duplicates captured frames on disk, but isn't network traffic.
        return body, Evidence(
            method="RPC",
            transport="qlik",
            url=HUB,
            sha256=digest(body),
            size=len(body),
            media_type="application/json",
            fetched_at=datetime.now(UTC),
        )
