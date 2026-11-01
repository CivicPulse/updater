import copy
import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
from test_transport import install_transport
from typer.testing import CliRunner

from civpulse_updater.adapters.georgia_turnout import (
    APP,
    ELECTION_FILTER,
    HUB,
    ORIGIN,
    TABLES,
    TENANT,
    GeorgiaTurnout,
    SavedClient,
    read_tables,
)
from civpulse_updater.cli import app
from civpulse_updater.config import load_source
from civpulse_updater.evidence import EvidenceStore
from civpulse_updater.models import Evidence, Reference, Window, canonical, digest
from civpulse_updater.pipeline import collect, replay
from civpulse_updater.state import State
from civpulse_updater.transport import Fetcher, FetchError
from civpulse_updater.turnout_transport import (
    TOKEN_URL,
    GeorgiaTurnoutFetcher,
    QlikClient,
)

FIXTURE = Path(__file__).parent / "fixtures/georgia-turnout/tables.json"


@pytest.fixture
def config():
    return load_source(Path("sources"), "georgia-sos-turnout")


@pytest.fixture
def window():
    return Window(start=date(2026, 11, 3), end=date(2026, 11, 3))


class Engine:
    """Synthetic QIX session using projected official aggregate responses."""

    def __init__(self):
        self.tables = json.loads(FIXTURE.read_text())
        self.objects = {
            2: ELECTION_FILTER,
            **{i + 3: oid for i, (oid, _, _) in enumerate(TABLES.values())},
        }
        self.selected = False
        self.short = False
        self.reload = False
        self.layout_reads = 0
        self.wrong_selection = False
        self.extra_selection = False
        self.messages = []

    def call(self, handle, method, params):
        result = self.respond(handle, method, params)
        n = len(self.messages) + 1
        request = canonical(
            dict(jsonrpc="2.0", id=n, handle=handle, method=method, params=params)
        )
        self.messages.append(
            {"request": request, "response": canonical(dict(id=n, result=result))}
        )
        return copy.deepcopy(result)

    def respond(self, handle, method, params):
        if method == "OpenDoc":
            assert handle == -1 and params == [APP]
            return {"qReturn": {"qHandle": 1}}
        if method == "GetAppLayout":
            self.layout_reads += 1
            return {
                "qLayout": {
                    "qFileName": APP,
                    "qHasData": True,
                    "qLastReloadTime": "new"
                    if self.reload and self.layout_reads > 1
                    else "old",
                }
            }
        if method == "GetObject":
            return {
                "qReturn": {
                    "qHandle": next(
                        h for h, oid in self.objects.items() if oid == params[0]
                    )
                }
            }
        if method == "CreateSessionObject":
            return {"qReturn": {"qHandle": 20}}
        if handle == 20 and method == "GetLayout":
            return {
                "qLayout": {
                    "qSelectionObject": {
                        "qSelections": [
                            {"qField": "Election Date", "qSelectedCount": 1}
                        ]
                        + (
                            [{"qField": "County", "qSelectedCount": 1}]
                            if self.extra_selection
                            else []
                        )
                    }
                }
            }
        if method == "ClearAll":
            self.selected = False
            return {}
        if method == "SelectListObjectValues":
            assert handle == 2 and params == ["/qListObjectDef", [1], False]
            self.selected = not self.wrong_selection
            return {"qSuccess": True}
        if handle == 2:
            if method == "GetLayout":
                return {
                    "qLayout": {
                        "qListObject": {
                            "qSize": {"qcx": 1, "qcy": 2},
                            "qDimensionInfo": {"qGroupFieldDefs": ["Election Date"]},
                        }
                    }
                }
            if method == "GetListObjectData":
                rows = [
                    [
                        dict(
                            qText="11/03/2026",
                            qElemNumber=1,
                            qState="S" if self.selected else "A",
                        )
                    ],
                    [
                        dict(
                            qText="11/05/2024",
                            qElemNumber=2,
                            qState="A" if self.selected else "S",
                        )
                    ],
                ]
                return {"qDataPages": [{"qArea": params[1][0], "qMatrix": rows}]}
        table = self.tables[self.objects[handle]]
        if method == "GetProperties":
            return {"qProp": table["properties"]}
        if method == "GetLayout":
            return {"qLayout": table["layout"]}
        if method == "GetHyperCubeData":
            page = params[1][0]
            rows = table["rows"][page["qTop"] : page["qTop"] + page["qHeight"]]
            return {
                "qDataPages": [
                    {"qArea": page, "qMatrix": rows[:-1] if self.short else rows}
                ]
            }
        raise AssertionError((handle, method, params))


def snapshot(window, engine=None):
    engine = engine or Engine()
    read_tables(engine, window)
    return canonical(
        dict(
            schema_version=1,
            app_id=APP,
            window=window.model_dump(mode="json"),
            messages=engine.messages,
        )
    ).encode()


class FixtureFetcher:
    def __init__(self, body):
        self.body = body
        self.calls = []

    def get(self, url):
        assert url == HUB and not self.calls
        self.calls.append(url)
        return self.body, Evidence(
            url=url,
            method="RPC",
            transport="qlik",
            sha256=digest(self.body),
            size=len(self.body),
            media_type="application/json",
            fetched_at=datetime.now(UTC),
        )


def parse(config, window, body):
    sha = digest(body)
    return GeorgiaTurnout(config).parse(
        body, Reference(url=HUB, page_url=HUB, listing_hash=sha), sha, window
    )


def test_collection_review_corrections_and_offline_replay(
    tmp_path, config, window, monkeypatch
):
    state, store = State(tmp_path), EvidenceStore(tmp_path / "evidence")
    body = snapshot(window)
    result = collect(state, store, config, window, FixtureFetcher(body))
    assert (
        result["status"] == "complete"
        and result["records"] == result["proposed_changes"] == 3
    )
    payloads = [
        json.loads(r["payload"]) for r in state.rows("SELECT payload FROM records")
    ]
    assert all(
        r["unofficial"]
        and r["record_type"] == "turnout"
        and r["election_date"] == "2026-11-03"
        for r in payloads
    )
    early = next(r for r in payloads if r["data"]["table"] == "early-voting-by-county")
    assert early["data"]["rows"][0]["measures"]["Ballots Accepted"]["value"] == 2497
    assert early["data"]["rows"][0]["dimensions"] == {"County": "APPLING"}
    change = state.rows("SELECT * FROM changes")[0]
    with pytest.raises(ValueError, match="Warnings"):
        state.review(change["id"], "approved", "tester", "verified", False)
    assert (
        state.review(change["id"], "approved", "tester", "verified", True)["published"]
        is False
    )
    monkeypatch.setattr(
        GeorgiaTurnoutFetcher,
        "get",
        lambda *a: pytest.fail("Replay must remain offline"),
    )
    assert replay(state, store, result["run_id"])["proposed_changes"] == 0
    assert (
        collect(state, store, config, window, FixtureFetcher(body))["proposed_changes"]
        == 0
    )
    # A downward correction replaces a cumulative source value, never adds it.
    engine = Engine()
    engine.tables[next(iter(TABLES.values()))[0]]["rows"][0][1].update(
        qNum=2400, qText="2,400"
    )
    revised = collect(
        state, store, config, window, FixtureFetcher(snapshot(window, engine))
    )
    assert revised["status"] == "complete" and revised["proposed_changes"] == 1
    assert len(state.rows("SELECT * FROM records")) == 3
    previous = state.rows("SELECT hash FROM records")
    broken = json.loads(body)
    broken["messages"].pop()
    failed = collect(
        state, store, config, window, FixtureFetcher(canonical(broken).encode())
    )
    assert failed["status"] == "failed" and failed["proposed_changes"] == 0
    assert state.rows("SELECT hash FROM records") == previous


def test_cli_collect_and_replay(tmp_path, config, window, monkeypatch):
    body = snapshot(window)
    monkeypatch.setattr(
        "civpulse_updater.turnout_transport.GeorgiaTurnoutFetcher",
        lambda *a: FixtureFetcher(body),
    )
    runner = CliRunner()
    args = ["--state-dir", str(tmp_path)]
    result = runner.invoke(
        app, args + ["collect", config.id, "--from", "2026-11-03", "--to", "2026-11-03"]
    )
    assert result.exit_code == 0, result.output
    run_id = json.loads(result.stdout)["run_id"]
    repeated = runner.invoke(app, args + ["replay", run_id])
    assert (
        repeated.exit_code == 0 and json.loads(repeated.stdout)["proposed_changes"] == 0
    )
    assert runner.invoke(app, args + ["publish"]).exit_code == 4


@pytest.mark.parametrize(
    "fault",
    [
        "short",
        "reload",
        "wrong_selection",
        "extra_selection",
        "dimension_definition",
        "huge_count",
        "dimensions",
        "count",
        "duplicate",
        "fraction",
        "negative",
    ],
)
def test_incomplete_or_changed_source_fails_closed(window, fault):
    engine = Engine()
    target = engine.tables[next(iter(TABLES.values()))[0]]
    if fault in ("short", "reload", "wrong_selection", "extra_selection"):
        setattr(engine, fault, True)
    elif fault == "dimensions":
        target["layout"]["qHyperCube"]["qDimensionInfo"][0]["qFallbackTitle"] = (
            "Voter ID"
        )
    elif fault == "dimension_definition":
        target["properties"]["qHyperCubeDef"]["qDimensions"][0]["qDef"][
            "qFieldDefs"
        ] = ["Voter ID"]
    elif fault == "huge_count":
        target["rows"][0][1]["qNum"] = 10**1000
    elif fault == "count":
        target["layout"]["qHyperCube"]["qSize"]["qcy"] = 2001
    elif fault == "duplicate":
        target["rows"][1] = copy.deepcopy(target["rows"][0])
    else:
        target["rows"][0][1]["qNum"] = 1.2 if fault == "fraction" else -1
    with pytest.raises(ValueError):
        read_tables(engine, window)


def test_null_is_not_zero_and_percent_is_not_count(config, window):
    engine = Engine()
    oid = TABLES["absentee-by-county"][0]
    engine.tables[oid]["rows"][0][1].update(qNum="NaN", qText="-")
    events = parse(config, window, snapshot(window, engine))
    absentee = next(
        e.event for e in events if e.event.data["table"] == "absentee-by-county"
    )
    measures = absentee.data["rows"][0]["measures"]
    assert measures["Ballots Requested"]["value"] is None
    assert measures["Ballots Rejected"]["value"] == 0
    assert measures["% Accepted of Returned"]["value"] == 1
    assert any("unavailable" in w for w in absentee.warnings)


def test_empty_election_window_and_transcript_mismatch(config):
    window = Window(start=date(2026, 1, 1), end=date(2026, 1, 1))
    assert parse(config, window, snapshot(window)) == []
    with pytest.raises(ValueError, match="mismatch"):
        SavedClient(
            [
                {
                    "request": canonical(
                        dict(id=1, handle=-1, method="DeleteObject", params=[])
                    ),
                    "response": "{}",
                }
            ]
        ).call(-1, "OpenDoc", [APP])


def test_pagination_captures_every_row(window, monkeypatch):
    monkeypatch.setattr("civpulse_updater.adapters.georgia_turnout.PAGE_CELLS", 9)
    engine = Engine()
    result = read_tables(engine, window)
    assert all(len(data["rows"]) == 2 for _, data, _ in result)
    pages = [
        json.loads(m["request"])
        for m in engine.messages
        if json.loads(m["request"])["method"] == "GetHyperCubeData"
    ]
    assert any(p["params"][1][0]["qTop"] == 1 for p in pages)


def test_transport_rejects_outside_scope(config, window):
    fetcher = GeorgiaTurnoutFetcher(config, window, lambda *a: None)
    with pytest.raises(FetchError, match="scope"):
        fetcher.get("https://example.org/")
    with pytest.raises(FetchError, match="Unsupported"):
        QlikClient(None, fetcher).call(1, "DeleteObject", [])


def test_201_token_body_remains_private(config, monkeypatch):
    secret = "test-only-token-do-not-store"

    def handler(request):
        return httpx.Response(
            201,
            stream=httpx.ByteStream(canonical({"access_token": secret}).encode()),
            headers={"content-type": "application/json"},
        )

    install_transport(monkeypatch, handler)
    fetcher = Fetcher(config)
    body, _, status, _ = fetcher.exchange(
        TOKEN_URL, max_bytes=65536, body_statuses=(200, 201)
    )
    assert status == 201 and json.loads(body)["access_token"] == secret
    # Generic GET semantics still require HTTP 200.
    with pytest.raises(FetchError, match="201"):
        fetcher.get(TOKEN_URL)


@pytest.mark.parametrize(
    ("failed_path", "stage"),
    [
        ("/DH.ELECTION2024/index.html", "dashboard HTML"),
        ("/DH.ELECTION2024/js/vars.js", "app configuration"),
        ("/shared-config.js", "tenant configuration"),
        ("/qlik-oauth-create-user", "anonymous authentication"),
    ],
)
def test_http_403_identifies_turnout_stage_without_leaking_body(
    config, window, monkeypatch, tmp_path, failed_path, stage
):
    secret = "private-response-or-token"
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == failed_path:
            return httpx.Response(403, stream=httpx.ByteStream(secret.encode()))
        if request.url.path.endswith("/vars.js"):
            body = f'var appId = "{APP}";'
        elif request.url.path == "/shared-config.js":
            body = f'var tenant = "https://{TENANT}";'
        else:
            body = "<html>Public dashboard</html>"
        return httpx.Response(200, stream=httpx.ByteStream(body.encode()))

    install_transport(monkeypatch, handler)
    monkeypatch.setattr(
        "civpulse_updater.turnout_transport.connect",
        lambda *a, **kw: pytest.fail("A blocked source must not open Qlik"),
    )
    state, store = State(tmp_path), EvidenceStore(tmp_path / "evidence")
    result = collect(state, store, config, window)
    assert result["status"] == "failed"
    assert result["records"] == result["proposed_changes"] == 0
    detail = result["errors"][0]["detail"]
    assert stage in detail and "HTTP 403" in detail
    assert secret not in canonical(result)
    assert calls.count(failed_path) == 1  # 403 is not transient/retryable.
    for row in state.rows("SELECT metadata FROM evidence"):
        metadata = json.loads(row["metadata"])
        assert metadata["url"] != TOKEN_URL
        assert secret.encode() not in store.read(metadata["sha256"])


def test_real_tls_websocket_capture_and_replay(
    tmp_path, config, window, monkeypatch, caplog
):
    """Exercise the new socket/TLS/RPC boundary with a disposable local server."""
    import logging
    import socket
    import ssl
    import threading

    import trustme
    from websockets.sync.client import connect as real_connect
    from websockets.sync.server import serve

    ca = trustme.CA()
    server_ssl = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ca.issue_cert(TENANT).configure_cert(server_ssl)
    client_ssl = ssl.create_default_context()
    ca.configure_trust(client_ssl)
    sni = []
    server_ssl.set_servername_callback(lambda sock, name, ctx: sni.append(name))
    token = "synthetic-token-never-evidence"
    engine = Engine()
    calls = []
    errors = []

    def handler(ws):
        try:
            assert ws.request.headers["Authorization"] == "Bearer " + token
            assert ws.request.headers["Origin"] == ORIGIN
            for raw in ws:
                request = json.loads(raw)
                calls.append(request)
                result = engine.respond(
                    request["handle"], request["method"], request["params"]
                )
                if len(calls) == 1:
                    ws.send(canonical({"method": "OnConnected", "params": {}}))
                ws.send(canonical({"id": request["id"], "result": result}))
        except Exception as error:
            errors.append(error)

    def http_handler(request):
        path = request.url.path
        if path == "/qlik-oauth-create-user":
            content, code = canonical({"access_token": token}), 201
        elif path.endswith("/js/vars.js"):
            content, code = f'var appId = "{APP}";', 200
        elif path == "/shared-config.js":
            content, code = f'var tenant = "https://{TENANT}";', 200
        else:
            content, code = "<html>Public dashboard</html>", 200
        return httpx.Response(code, stream=httpx.ByteStream(content.encode()))

    install_transport(monkeypatch, http_handler)
    monkeypatch.setattr(
        "civpulse_updater.turnout_transport.public_address",
        lambda host: "93.184.216.34",
    )
    tcp = socket.create_connection
    with serve(
        handler,
        "127.0.0.1",
        0,
        ssl=server_ssl,
        logger=logging.Logger("test.qlik.server", level=logging.CRITICAL + 1),
    ) as server:
        port = server.socket.getsockname()[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def pinned_socket(address, **kwargs):
            assert address == ("93.184.216.34", 443)
            return tcp(("127.0.0.1", port), **kwargs)

        monkeypatch.setattr(
            "civpulse_updater.turnout_transport.socket.create_connection", pinned_socket
        )

        def trusted_connect(uri, **kwargs):
            assert uri == f"wss://{TENANT}/app/{APP}"
            assert kwargs["server_hostname"] == TENANT and kwargs["proxy"] is None
            return real_connect(uri, ssl=client_ssl, **kwargs)

        monkeypatch.setattr(
            "civpulse_updater.turnout_transport.connect", trusted_connect
        )
        caplog.set_level(logging.DEBUG)
        state, store = State(tmp_path), EvidenceStore(tmp_path / "evidence")
        result = collect(state, store, config, window)
        server.shutdown()
        thread.join(timeout=5)
    assert not errors
    assert result["status"] == "complete", result
    assert sni == [TENANT]
    assert len(calls) > 20
    assert all(
        c["method"] not in ("SetProperties", "DeleteObject", "DoSave") for c in calls
    )
    assert token not in caplog.text
    for row in state.rows("SELECT metadata FROM evidence"):
        metadata = json.loads(row["metadata"])
        assert token.encode() not in store.read(metadata["sha256"])
        assert metadata["url"] != TOKEN_URL
    monkeypatch.setattr(
        GeorgiaTurnoutFetcher,
        "get",
        lambda *a: pytest.fail("Offline replay made a request"),
    )
    assert replay(state, store, result["run_id"])["proposed_changes"] == 0


@pytest.mark.parametrize(
    "failure", ["rpc", "identity", "oversize", "notifications", "budget"]
)
def test_rpc_failure_limits(config, window, failure):
    class Socket:
        def send(self, request):
            self.request = json.loads(request)

        def recv(self, timeout):
            if failure == "rpc":
                return canonical(
                    {"id": 1, "error": {"message": "private upstream detail"}}
                )
            if failure == "identity":
                return canonical({"id": 999, "result": {}})
            if failure == "oversize":
                return "x" * 2048
            return canonical({"method": "OnChanged"})

    config = config.model_copy(update={"max_bytes": 1024, "max_requests": 1})
    captured = []
    fetcher = GeorgiaTurnoutFetcher(
        config, window, lambda b, e: captured.append((b, e))
    )
    if failure == "budget":
        fetcher.requests = 1
    with pytest.raises(FetchError):
        QlikClient(Socket(), fetcher).call(-1, "OpenDoc", [APP])
    assert fetcher.received_bytes <= config.max_total_bytes
