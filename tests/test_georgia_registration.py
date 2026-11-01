import copy
import json
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from civpulse_updater.adapters.georgia_registration import (
    APPS,
    HUB,
    LIVE_APP,
    LIVE_TABLES,
    MONTHLY_APP,
    GeorgiaRegistration,
    read_registration,
)
from civpulse_updater.cli import app
from civpulse_updater.config import load_source
from civpulse_updater.evidence import EvidenceStore
from civpulse_updater.models import Evidence, Window, canonical, digest
from civpulse_updater.pipeline import collect, replay
from civpulse_updater.registration_transport import GeorgiaRegistrationFetcher
from civpulse_updater.state import State


class Engine:
    """Session-local aggregate engine, shared by in-process and TLS tests."""

    def __init__(self, app_id):
        self.app_id = app_id
        self.objects = {}
        self.messages = []
        self.selected = None
        self.fault = None
        self.reads = 0
        self.count = 100
        self.date_rows = [
            [dict(qText="10/01/2026", qElemNumber=1, qState="O")],
            [dict(qText="11/01/2026", qElemNumber=2, qState="O")],
        ]

    def call(self, handle, method, params):
        result = self.respond(handle, method, params)
        n = len(self.messages) + 1
        self.messages.append(
            {
                "request": canonical(
                    dict(id=n, handle=handle, method=method, params=params)
                ),
                "response": canonical(dict(id=n, result=result)),
            }
        )
        return copy.deepcopy(result)

    def add(self, definition):
        handle = len(self.objects) + 2
        self.objects[handle] = definition
        return {"qReturn": {"qHandle": handle}}

    def respond(self, handle, method, params):
        if method == "OpenDoc":
            assert params == [self.app_id]
            return {"qReturn": {"qHandle": 1}}
        if method == "GetAppLayout":
            self.reads += 1
            return {
                "qLayout": {
                    "qFileName": self.app_id,
                    "qHasData": True,
                    "qLastReloadTime": "2026-11-02T05:18:14Z"
                    if self.fault == "reload" and self.reads > 1
                    else "2026-11-01T05:18:14Z",
                }
            }
        if method == "ClearAll":
            self.selected = None
            return {}
        if method == "CreateSessionObject":
            return self.add(params[0])
        if method == "GetObject":
            oid = params[0]
            fixtures = json.loads(
                (
                    Path(__file__).parent / "fixtures/georgia-registration/tables.json"
                ).read_text()
            )
            return self.add(copy.deepcopy(fixtures[oid]["properties"]))
        definition = self.objects[handle]
        if method == "SelectListObjectValues":
            self.selected = 1 if self.fault == "wrong_date" else params[1][0]
            return {"qSuccess": True}
        if method == "GetListObjectData":
            rows = copy.deepcopy(self.date_rows)
            for row in rows:
                row[0]["qState"] = (
                    "S" if row[0]["qElemNumber"] == self.selected else "O"
                )
            return {"qDataPages": [{"qArea": params[1][0], "qMatrix": rows}]}
        if "qListObjectDef" in definition:
            return {
                "qLayout": {
                    "qListObject": {
                        "qSize": {"qcx": 1, "qcy": 2},
                        "qDimensionInfo": {"qGroupFieldDefs": ["AsOfDate"]},
                    }
                }
            }
        if "qSelectionObjectDef" in definition:
            selections = (
                [{"qField": "AsOfDate", "qSelectedCount": 1}] if self.selected else []
            )
            if self.fault == "extra_selection":
                selections.append({"qField": "County", "qSelectedCount": 1})
            return {"qLayout": {"qSelectionObject": {"qSelections": selections}}}
        if method == "GetProperties":
            props = copy.deepcopy(definition)
            if self.fault == "measure":
                props["qHyperCubeDef"]["qMeasures"][0]["qDef"]["qDef"] = "Concat(USER)"
            if self.fault == "dimension":
                props["qHyperCubeDef"]["qDimensions"][0]["qDef"]["qFieldDefs"] = [
                    "USER"
                ]
            return {"qProp": props}
        cube = definition["qHyperCubeDef"]
        fields = [d["qDef"]["qFieldDefs"][0] for d in cube["qDimensions"]]
        if method == "GetLayout":
            return {
                "qLayout": {
                    "qHyperCube": {
                        "qDimensionInfo": [{"qFallbackTitle": f} for f in fields],
                        "qMeasureInfo": [{"qFallbackTitle": "Voter Count"}],
                        "qSize": {
                            "qcx": len(fields) + 1,
                            "qcy": 500001
                            if self.fault == "oversize"
                            else (
                                4
                                if "AsOfDate" in fields
                                and (
                                    self.selected is None
                                    or "SUM({1}" in cube["qMeasures"][0]["qDef"]["qDef"]
                                )
                                else 2
                            ),
                        },
                    }
                }
            }
        if method == "GetHyperCubeData":
            area = params[1][0]
            values = {
                "County Name": "BIBB",
                "County": "BIBB",
                "VOTER_STATUS": "Active",
                "Voter Status": "Active",
                "Age Group": "40-45",
                "Gender_Clean": "Female",
                "Gender": "Female",
                "RACE_DESC": "Black",
                "Race": "Black",
                "Municipal Precinct": "-",
                "Congress.DistrictNumber": "2",
                "House.DistrictNumber": "142",
                "Senate.DistrictNumber": "18",
            }
            rows = []
            dates = [
                r[0]["qText"]
                for r in self.date_rows
                if self.selected is None
                or "SUM({1}" in cube["qMeasures"][0]["qDef"]["qDef"]
                or r[0]["qElemNumber"] == self.selected
            ]
            total_rows = len(dates) * 2 if "AsOfDate" in fields else 2
            for i in range(total_rows):
                row = [
                    dict(
                        qText=("HOUSTON" if i % 2 else "BIBB")
                        if f in ("County", "County Name")
                        else (dates[i // 2] if f == "AsOfDate" else values[f]),
                        qElemNumber=i,
                        qState="O",
                    )
                    for f in fields
                ]
                if "Municipal Precinct" in fields and i == 1:
                    row[fields.index("Municipal Precinct")] = dict(
                        qNum="NaN", qElemNumber=0, qState="O"
                    )
                number = -1 if self.fault == "negative" else self.count + i
                row.append(dict(qText=str(number), qNum=number, qElemNumber=0))
                rows.append(row)
            if self.fault == "distinct_nulls" and "Municipal Precinct" in fields:
                rows[1] = copy.deepcopy(rows[0])
                index = fields.index("Municipal Precinct")
                rows[0][index] = dict(qNum="NaN", qElemNumber=0, qState="O")
                rows[1][index] = dict(
                    qText="-", qIsNull=True, qNum="NaN", qElemNumber=-2, qState="L"
                )
            if self.fault == "duplicate":
                rows[1] = copy.deepcopy(rows[0])
            if self.fault == "null":
                rows[0][-1].update(qNum="NaN", qText="-")
            selected = rows[area["qTop"] : area["qTop"] + area["qHeight"]]
            if self.fault == "short":
                selected = selected[:-1]
            return {"qDataPages": [{"qArea": area, "qMatrix": selected}]}
        raise AssertionError((handle, method, params))


@pytest.fixture
def config():
    return load_source(Path("sources"), "georgia-sos-registration")


@pytest.fixture
def window():
    return Window(start=date(2026, 11, 1), end=date(2026, 11, 1))


def snapshot(window, engines=None):
    engines = engines or [Engine(a) for a in APPS]
    for engine in engines:
        read_registration(engine, engine.app_id, window)
    return canonical(
        {
            "schema_version": 1,
            "app_ids": list(APPS),
            "window": window.model_dump(mode="json"),
            "messages": {e.app_id: e.messages for e in engines},
        }
    ).encode()


class FixtureFetcher:
    def __init__(self, body):
        self.body = body

    def get(self, url):
        assert url == HUB
        from datetime import UTC, datetime

        return self.body, Evidence(
            url=HUB,
            method="RPC",
            transport="qlik",
            sha256=digest(self.body),
            size=len(self.body),
            media_type="application/json",
            fetched_at=datetime.now(UTC),
        )


def test_pipeline_cli_review_replay_and_corrections(
    config, window, tmp_path, monkeypatch
):
    body = snapshot(window)
    monkeypatch.setattr(
        "civpulse_updater.registration_transport.GeorgiaRegistrationFetcher",
        lambda *a: FixtureFetcher(body),
    )
    runner = CliRunner()
    args = ["--state-dir", str(tmp_path)]
    result = runner.invoke(
        app, args + ["collect", config.id, "--from", "2026-11-01", "--to", "2026-11-01"]
    )
    assert result.exit_code == 0, result.output
    output = json.loads(result.stdout)
    assert output["records"] == output["proposed_changes"] == 7
    state, store = State(tmp_path), EvidenceStore(tmp_path / "evidence")
    records = [
        json.loads(r["payload"]) for r in state.rows("SELECT payload FROM records")
    ]
    assert all(
        r["record_type"] == "registration" and r["as_of_date"] == "2026-11-01"
        for r in records
    )
    precinct = next(
        r for r in records if r["data"]["table"] == "live-demographics-precinct"
    )
    assert {
        r["dimensions"]["Municipal Precinct"] for r in precinct["data"]["rows"]
    } == {"-", None}
    assert all("USER" not in r["data"]["dimensions"] for r in records)
    change = state.rows("SELECT * FROM changes")[0]
    with pytest.raises(ValueError, match="Warnings"):
        state.review(change["id"], "approved", "tester", "verified", False)
    assert (
        state.review(change["id"], "approved", "tester", "verified", True)["published"]
        is False
    )
    monkeypatch.setattr(
        GeorgiaRegistrationFetcher, "get", lambda *a: pytest.fail("Replay used network")
    )
    assert replay(state, store, output["run_id"])["proposed_changes"] == 0
    assert (
        collect(state, store, config, window, FixtureFetcher(body))["proposed_changes"]
        == 0
    )
    engines = [Engine(a) for a in APPS]
    engines[0].count = 90
    assert collect(
        state, store, config, window, FixtureFetcher(snapshot(window, engines))
    )["proposed_changes"] == len(LIVE_TABLES)
    previous = state.rows("SELECT hash FROM records")
    broken = json.loads(body)
    broken["messages"][MONTHLY_APP].pop()
    failed = collect(
        state, store, config, window, FixtureFetcher(canonical(broken).encode())
    )
    assert failed["status"] == "failed" and failed["proposed_changes"] == 0
    assert state.rows("SELECT hash FROM records") == previous
    assert runner.invoke(app, args + ["publish"]).exit_code == 4


@pytest.mark.parametrize(
    "fault",
    [
        "reload",
        "wrong_date",
        "extra_selection",
        "dimension",
        "measure",
        "oversize",
        "negative",
        "short",
        "duplicate",
    ],
)
def test_source_failures_are_rejected(window, fault):
    engine = Engine(MONTHLY_APP)
    engine.fault = fault
    with pytest.raises(ValueError):
        read_registration(engine, MONTHLY_APP, window)


def test_every_page_and_snapshot_is_collected(window, monkeypatch):
    monkeypatch.setattr("civpulse_updater.adapters.georgia_registration.PAGE_CELLS", 7)
    engine = Engine(MONTHLY_APP)
    wide = Window(start=date(2026, 10, 1), end=window.end)
    records = read_registration(engine, MONTHLY_APP, wide)
    assert len(records) == 4
    assert all(len(data["rows"]) == 2 for _, data in records)
    requests = [json.loads(m["request"]) for m in engine.messages]
    assert any(
        r["method"] == "GetHyperCubeData" and r["params"][1][0]["qTop"] == 1
        for r in requests
    )
    assert (
        read_registration(
            Engine(LIVE_APP),
            LIVE_APP,
            Window(start=date(2026, 10, 1), end=date(2026, 10, 1)),
        )
        == []
    )


def test_null_is_not_zero(window):
    engine = Engine(MONTHLY_APP)
    engine.fault = "null"
    records = read_registration(engine, MONTHLY_APP, window)
    assert records[0][1]["rows"][0]["measures"]["Voter Count"]["value"] is None


def test_scope(config, window):
    from civpulse_updater.transport import FetchError

    with pytest.raises(ValueError):
        GeorgiaRegistration(config.model_copy(update={"url": "https://example.com/"}))
    with pytest.raises(FetchError, match="scope"):
        GeorgiaRegistrationFetcher(config, window, lambda *a: None).get(
            "https://example.com/"
        )


def test_real_tls_capture_both_apps_and_offline_replay(
    tmp_path, config, window, monkeypatch, caplog
):
    import logging
    import socket
    import ssl
    import threading

    import httpx
    import trustme
    from test_transport import install_transport
    from websockets.sync.client import connect as real_connect
    from websockets.sync.server import serve

    from civpulse_updater.adapters.georgia_registration import ORIGIN, TENANT

    ca = trustme.CA()
    server_ssl = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ca.issue_cert(TENANT).configure_cert(server_ssl)
    client_ssl = ssl.create_default_context()
    ca.configure_trust(client_ssl)
    token = "synthetic-registration-token-never-save"
    calls, errors, app_ids = [], [], []

    def handler(ws):
        try:
            assert ws.request.headers["Authorization"] == "Bearer " + token
            assert ws.request.headers["Origin"] == ORIGIN
            app_id = ws.request.path.rsplit("/", 1)[1]
            app_ids.append(app_id)
            engine = Engine(app_id)
            for raw in ws:
                request = json.loads(raw)
                calls.append(request)
                result = engine.respond(
                    request["handle"], request["method"], request["params"]
                )
                ws.send(canonical({"id": request["id"], "result": result}))
        except Exception as error:
            errors.append(error)

    def http_handler(request):
        if request.url.path == "/qlik-oauth-create-user":
            content, status = canonical({"access_token": token}), 201
        elif request.url.path.endswith("/vars.js"):
            content, status = (
                f'var primaryAppId = "{LIVE_APP}"; var tableAppId = "{MONTHLY_APP}";',
                200,
            )
        elif request.url.path == "/shared-config.js":
            content, status = f'var tenant = "https://{TENANT}";', 200
        else:
            content, status = "<html>Public registration dashboard</html>", 200
        return httpx.Response(status, stream=httpx.ByteStream(content.encode()))

    install_transport(monkeypatch, http_handler)
    monkeypatch.setattr(
        "civpulse_updater.registration_transport.public_address",
        lambda host: "93.184.216.34",
    )
    tcp = socket.create_connection
    with serve(
        handler,
        "127.0.0.1",
        0,
        ssl=server_ssl,
        logger=logging.Logger("test.registration", level=logging.CRITICAL + 1),
    ) as server:
        port = server.socket.getsockname()[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def pinned_socket(address, **kwargs):
            assert address == ("93.184.216.34", 443)
            return tcp(("127.0.0.1", port), **kwargs)

        def trusted_connect(uri, **kwargs):
            assert kwargs["server_hostname"] == TENANT and kwargs["proxy"] is None
            assert uri in [f"wss://{TENANT}/app/{a}" for a in APPS]
            return real_connect(uri, ssl=client_ssl, **kwargs)

        monkeypatch.setattr(
            "civpulse_updater.registration_transport.socket.create_connection",
            pinned_socket,
        )
        monkeypatch.setattr(
            "civpulse_updater.registration_transport.connect", trusted_connect
        )
        caplog.set_level(logging.DEBUG)
        state, store = State(tmp_path), EvidenceStore(tmp_path / "evidence")
        result = collect(state, store, config, window)
        server.shutdown()
        thread.join(timeout=5)
    assert not errors
    assert result["status"] == "complete" and result["records"] == 6, result
    assert app_ids == list(APPS)
    assert all(
        c["method"] not in ("SetProperties", "DoSave", "GetField") for c in calls
    )
    for c in calls:
        if c["method"] == "CreateSessionObject" and "qHyperCubeDef" in c["params"][0]:
            dims = c["params"][0]["qHyperCubeDef"]["qDimensions"]
            assert all(d["qDef"]["qFieldDefs"] != ["USER"] for d in dims)
    assert token not in caplog.text
    for row in state.rows("SELECT metadata FROM evidence"):
        metadata = json.loads(row["metadata"])
        assert token.encode() not in store.read(metadata["sha256"])
        assert not metadata["url"].endswith("qlik-oauth-create-user")
    monkeypatch.setattr(
        GeorgiaRegistrationFetcher,
        "get",
        lambda *a: pytest.fail("Replay contacted network"),
    )
    assert replay(state, store, result["run_id"])["proposed_changes"] == 0


@pytest.mark.parametrize(
    "path",
    [
        "/DH.VOTERS/index.html",
        "/DH.VOTERS/js/vars.js",
        "/shared-config.js",
        "/qlik-oauth-create-user",
    ],
)
def test_blocked_http_retains_no_credentials(
    tmp_path, config, window, monkeypatch, path
):
    import httpx
    from test_transport import install_transport

    from civpulse_updater.adapters.georgia_registration import TENANT

    secret = "private-authentication-body"

    def handler(request):
        if request.url.path == path:
            return httpx.Response(403, stream=httpx.ByteStream(secret.encode()))
        if request.url.path.endswith("/vars.js"):
            content = (
                f'var primaryAppId = "{LIVE_APP}"; var tableAppId = "{MONTHLY_APP}";'
            )
        elif request.url.path == "/shared-config.js":
            content = f'var tenant = "https://{TENANT}";'
        else:
            content = "<html>Public registration</html>"
        return httpx.Response(200, stream=httpx.ByteStream(content.encode()))

    install_transport(monkeypatch, handler)
    monkeypatch.setattr(
        "civpulse_updater.registration_transport.connect",
        lambda *a, **kw: pytest.fail("Blocked bootstrap opened Qlik"),
    )
    state, store = State(tmp_path), EvidenceStore(tmp_path / "evidence")
    result = collect(state, store, config, window)
    assert result["status"] == "failed" and result["proposed_changes"] == 0
    assert "HTTP 403" in result["errors"][0]["detail"]
    assert secret not in canonical(result)
    for row in state.rows("SELECT metadata FROM evidence"):
        metadata = json.loads(row["metadata"])
        assert secret.encode() not in store.read(metadata["sha256"])


def test_distinct_missing_text_and_explicit_null_groups_are_not_collapsed(window):
    engine = Engine(LIVE_APP)
    engine.fault = "distinct_nulls"
    tables = dict(
        (table["table"], table)
        for _, table in read_registration(engine, LIVE_APP, window)
    )
    rows = tables["live-demographics-precinct"]["rows"]
    assert len(rows) == 2
    assert all(r["dimensions"]["Municipal Precinct"] is None for r in rows)
    assert {r["dimension_elements"]["Municipal Precinct"] for r in rows} == {0, -2}
