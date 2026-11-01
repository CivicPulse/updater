import copy
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from civpulse_updater.adapters.georgia_turnout import APP
from civpulse_updater.cli import app
from civpulse_updater.qlik_archive import (
    ArchiveDB,
    ArchiveError,
    ArchiveLimit,
    ArchiveTransport,
    RPCError,
    capture_app,
    rectangular,
    verify_archive,
    walk_data,
)


def cube_def(mode="S"):
    return {
        "qMode": mode,
        "qDimensions": [{"qDef": {"qFieldDefs": ["Category"]}}],
        "qMeasures": [{"qDef": {"qDef": "Sum(Value)", "qLabel": "Count"}}],
    }


class Engine:
    """Small complete public app: nested maps, fields, pivots, variables, raw tables."""

    def __init__(self):
        self.objects = {
            "sheet": {"qInfo": {"qId": "sheet", "qType": "sheet"}},
            "straight": {
                "qInfo": {"qId": "straight", "qType": "barchart"},
                "qHyperCubeDef": cube_def(),
            },
            "pivot": {
                "qInfo": {"qId": "pivot", "qType": "pivot-table"},
                "qHyperCubeDef": cube_def("P"),
            },
            "stack": {
                "qInfo": {"qId": "stack", "qType": "barchart"},
                "qHyperCubeDef": cube_def("K"),
            },
            "map": {
                "qInfo": {"qId": "map", "qType": "map"},
                "gaLayers": [
                    {"qHyperCubeDef": cube_def()},
                    {"qHyperCubeDef": cube_def()},
                ],
            },
            "list": {
                "qInfo": {"qId": "list", "qType": "listbox"},
                "qListObjectDef": {"qDef": {"qFieldDefs": ["Category"]}},
            },
            "hidden-child": {
                "qInfo": {"qId": "hidden-child", "qType": "kpi"},
                "qHyperCubeDef": cube_def(),
            },
            "master": {
                "qInfo": {"qId": "master", "qType": "measure"},
                "qMeasure": {"qDef": "Sum(Value)"},
            },
            "variable": {
                "qInfo": {"qId": "variable", "qType": "variable"},
                "qName": "vMode",
                "qDefinition": "Congress",
            },
            "bookmark": {"qInfo": {"qId": "bookmark", "qType": "bookmark"}},
        }
        self.handles = {}
        self.calls = []
        self.next = 2
        self.selected = None
        self.fault = None
        self.limit = None
        self.layout_reads = 0

    def handle(self, definition):
        handle = self.next
        self.next += 1
        self.handles[handle] = definition
        return {"qReturn": {"qHandle": handle}}

    def call(self, handle, method, params):
        self.calls.append((handle, method, copy.deepcopy(params)))
        if self.limit is not None and len(self.calls) > self.limit:
            raise ArchiveLimit("Synthetic request budget reached")
        if method == "OpenDoc":
            assert params == [APP]
            return {"qReturn": {"qHandle": 1}}
        if method == "GetAppLayout":
            self.layout_reads += 1
            return {
                "qLayout": {
                    "qFileName": APP,
                    "qHasData": True,
                    "qLastReloadTime": "2026-11-02T00:00:00Z"
                    if self.fault == "reload" and self.layout_reads > 2
                    else "2026-11-01T05:00:00Z",
                }
            }
        if method == "GetAppProperties":
            return {"qProp": {"qTitle": "Public fixture"}}
        if method == "ClearAll":
            self.selected = None
            return {}
        if method == "GetTablesAndKeys":
            return {
                "qtr": [
                    {
                        "qName": "raw-table",
                        "qNoOfRows": 3,
                        "qFields": [{"qName": "Category"}, {"qName": "%hidden"}],
                    }
                ],
                "qk": [],
            }
        if method == "GetAllInfos":
            return {
                "qInfos": [
                    v["qInfo"]
                    for k, v in self.objects.items()
                    if k not in ("hidden-child", "variable")
                ]
            }
        if method in ("GetObject", "GetMeasure", "GetVariableById", "GetBookmark"):
            if self.fault == "inaccessible" and params[0] == "straight":
                raise RPCError(403)
            return self.handle(copy.deepcopy(self.objects[params[0]]))
        if method == "CreateSessionObject":
            value = copy.deepcopy(params[0])
            value["qInfo"]["qId"] = f"session-{self.next}"
            return self.handle(value)
        if method == "DestroySessionObject":
            return {"qSuccess": True}
        if method == "GetTableData":
            offset, count, synthetic, name = params
            assert synthetic is True and name == "raw-table"
            rows = [
                {"qValue": [{"qText": f"category-{i}"}, {"qText": f"opaque-{i}"}]}
                for i in range(3)
            ][offset : offset + count]
            if self.fault == "short-table":
                rows = rows[:-1]
            return {"qData": rows}
        obj = self.handles[handle]
        if method == "GetProperties":
            return {"qProp": obj}
        if method == "GetChildInfos":
            return {
                "qInfos": [self.objects["hidden-child"]["qInfo"]]
                if obj["qInfo"]["qId"] == "sheet"
                else []
            }
        if method == "SelectListObjectValues":
            self.selected = params[1][0]
            return {"qSuccess": True}
        if method == "GetLayout":
            layout = {"qInfo": obj["qInfo"]}
            if "qSelectionObjectDef" in obj:
                layout["qSelectionObject"] = {
                    "qSelections": []
                    if self.selected is None
                    else [{"qField": "Election Date", "qSelectedCount": 1}]
                }
            elif "qFieldListDef" in obj:
                assert (
                    obj["qFieldListDef"]["qShowHidden"]
                    and obj["qFieldListDef"]["qShowSystem"]
                )
                layout["qFieldList"] = {
                    "qItems": [
                        {"qName": "Category", "qCardinal": 3},
                        {"qName": "%hidden", "qCardinal": 3},
                    ]
                }
            elif "qVariableListDef" in obj:
                assert obj["qVariableListDef"]["qType"] == "variable"
                layout["qVariableList"] = {
                    "qItems": [
                        {
                            "qInfo": self.objects["variable"]["qInfo"],
                            "qName": "vMode",
                            "definition": "Congress",
                        }
                    ]
                }
            elif "qListObjectDef" in obj:
                field = obj["qListObjectDef"]["qDef"]["qFieldDefs"][0]
                layout["qListObject"] = {
                    "qSize": {"qcx": 1, "qcy": 2 if field == "Election Date" else 3}
                }
            elif "qHyperCubeDef" in obj:
                layout["qHyperCube"] = {
                    "qMode": obj["qHyperCubeDef"]["qMode"],
                    "qSize": {"qcx": 2, "qcy": 3},
                }
            elif "gaLayers" in obj:
                layout["gaLayers"] = [
                    {"qHyperCube": {"qMode": "S", "qSize": {"qcx": 2, "qcy": 3}}}
                    for layer in obj["gaLayers"]
                ]
            return {"qLayout": layout}
        if method in (
            "GetHyperCubeData",
            "GetListObjectData",
            "GetHyperCubePivotData",
            "GetHyperCubeStackData",
        ):
            path, areas = params
            area = areas[0]
            offset = area["qTop"]
            count = area["qHeight"]
            field = (
                obj.get("qListObjectDef", {})
                .get("qDef", {})
                .get("qFieldDefs", [None])[0]
            )
            rows = []
            for i in range(offset, offset + count):
                cell = {
                    "qText": f"date-{i}" if field == "Election Date" else f"value-{i}",
                    "qElemNumber": i,
                    "qNum": i,
                    "qState": "S" if i == self.selected else "O",
                }
                rows.append(
                    [cell]
                    + [{"qText": str(i), "qNum": i} for _ in range(area["qWidth"] - 1)]
                )
            page = {"qArea": area, "qMatrix": rows}
            if method == "GetHyperCubePivotData":
                page = {"qArea": area, "qLeft": [], "qTop": [], "qData": rows}
            if method == "GetHyperCubeStackData":
                page = {
                    "qArea": area,
                    "qData": [
                        {
                            "qType": "R",
                            "qDown": 0,
                            "qSubNodes": [
                                {"qType": "V", "qRow": i, "qValue": i}
                                for i in range(offset, offset + count)
                            ],
                        }
                    ],
                }
            if self.fault == "short-chart" and method == "GetHyperCubeData":
                page["qMatrix"] = page["qMatrix"][:-1]
            return {"qDataPages": [page]}
        raise AssertionError((handle, method, params))


def test_every_object_field_table_and_context_with_offline_verification(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("civpulse_updater.qlik_archive.PAGE_CELLS", 4)
    monkeypatch.setattr("civpulse_updater.qlik_archive.TABLE_ROWS", 2)
    db = ArchiveDB(tmp_path)
    engine = Engine()
    result = capture_app(engine, db, APP)
    assert result["status"] == "complete", result
    manifest = db.manifest(result["snapshot"])
    assert any(i["key"].startswith("object:hidden-child:") for i in manifest["items"])
    assert any(i["key"].endswith("/gaLayers/1/qHyperCube") for i in manifest["items"])
    assert {i["key"] for i in manifest["items"] if i["kind"] == "model-table"} == {
        "table:raw-table"
    }
    assert any(i["key"] == "field:%hidden" for i in manifest["items"])
    assert any("Election Date:date-1" in i["key"] for i in manifest["items"])
    methods = {m for _, m, _ in engine.calls}
    assert {
        "GetHyperCubePivotData",
        "GetHyperCubeStackData",
        "GetHyperCubeData",
        "GetVariableById",
        "GetMeasure",
        "GetBookmark",
        "GetTableData",
    } <= methods
    assert not methods & {"DoSave", "SetProperties", "CreateObject", "Reload"}
    sid = result["snapshot"]
    db.close()
    assert verify_archive(tmp_path, sid)["complete"] is True


def test_resume_skips_valid_pages_and_rejects_corruption(tmp_path, monkeypatch):
    monkeypatch.setattr("civpulse_updater.qlik_archive.PAGE_CELLS", 4)
    db = ArchiveDB(tmp_path)
    engine = Engine()
    engine.limit = 70
    partial = capture_app(engine, db, APP)
    assert partial["status"] == "partial"
    sid = partial["snapshot"]
    first_pages = db.db.execute("SELECT count(*) FROM pages").fetchone()[0]
    assert first_pages > 0
    full = capture_app(Engine(), db, APP)
    assert full["status"] == "complete", full
    completed_pages = db.db.execute("SELECT count(*) FROM pages").fetchone()[0]
    assert completed_pages > first_pages
    again = Engine()
    assert capture_app(again, db, APP)["status"] == "complete"
    assert not any(
        m
        in (
            "GetTableData",
            "GetHyperCubeData",
            "GetHyperCubePivotData",
            "GetHyperCubeStackData",
        )
        for _, m, _ in again.calls
    )
    assert db.db.execute("SELECT count(*) FROM pages").fetchone()[0] == completed_pages
    sha = db.db.execute("SELECT sha FROM pages LIMIT 1").fetchone()[0]
    db.store.path(sha).write_bytes(b"corrupt")
    db.close()
    with pytest.raises(ValueError, match="checksum"):
        verify_archive(tmp_path, sid)


@pytest.mark.parametrize(
    "fault", ["reload", "short-table", "short-chart", "inaccessible"]
)
def test_partial_data_never_claims_complete(tmp_path, fault):
    db = ArchiveDB(tmp_path)
    engine = Engine()
    engine.fault = fault
    result = capture_app(engine, db, APP)
    assert result["status"] == "partial", result
    assert result["issues"]
    db.close()


def test_inventory_only_is_explicit_and_not_data_complete(tmp_path):
    db = ArchiveDB(tmp_path)
    result = capture_app(Engine(), db, APP, inventory_only=True)
    assert result["status"] == "inventory-only" and result["items"] == 0
    inv = json.loads(db.store.read(db.manifest(result["snapshot"])["inventory"]))
    assert "variable" in inv["objects"] and "hidden-child" in inv["objects"]
    db.close()
    assert verify_archive(tmp_path, result["snapshot"])["complete"] is False


def test_manifest_paths_never_use_source_names(tmp_path):
    db = ArchiveDB(tmp_path)
    sid = db.begin(APP, "now")
    db.item(sid, "../../outside", "test", {}, 1)
    db.page(sid, "../../outside", 0, 1, {"value": True})
    db.done(sid, "../../outside")
    assert not (tmp_path.parent / "outside").exists()
    db.close()


def test_walks_nested_maps_and_rejects_short_pages():
    paths = [
        p
        for p, _ in walk_data(
            {"layers": [{"qHyperCube": {}}], "qUndoExclude": {"qListObject": {}}}
        )
    ]
    assert paths == ["/layers/0/qHyperCube", "/qUndoExclude/qListObject"]
    area = {"qTop": 0, "qLeft": 0, "qWidth": 2, "qHeight": 2}
    with pytest.raises(ArchiveError):
        rectangular({"qDataPages": [{"qArea": area, "qMatrix": [[{}]]}]}, area, "S")


def test_cli_rejects_retired_full_inventory_download(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "civpulse_updater.qlik_archive.run_archive",
        lambda *a, **kw: pytest.fail("Summary CLI must not invoke a full archive"),
    )
    result = CliRunner().invoke(
        app, ["--state-dir", str(tmp_path), "capture-qlik", "all", "--inventory-only"]
    )
    assert result.exit_code == 2
    assert "No such option" in result.output


def test_transport_never_accepts_mutating_rpcs(tmp_path):
    from civpulse_updater.config import load_source

    db = ArchiveDB(tmp_path)
    transport = ArchiveTransport(
        load_source(Path("sources"), "georgia-sos-registration"), db
    )
    for method in (
        "DoSave",
        "CreateObject",
        "SetProperties",
        "DoReload",
        "DeleteObject",
    ):
        with pytest.raises(ArchiveError, match="Unsupported"):
            transport.call(1, method, [])
    db.close()


def test_chart_control_variants_are_captured_and_restored(tmp_path):
    from civpulse_updater.qlik_archive import control_values

    class Controls(Engine):
        def __init__(self):
            super().__init__()
            self.objects["sheet"]["actions"] = [
                {
                    "actionType": "setVariable",
                    "variable": "vMode",
                    "value": {"qStringExpression": {"qExpr": "'House'"}},
                },
                {
                    "actionType": "setVariable",
                    "variable": "vMode",
                    "value": {"qStringExpression": {"qExpr": "'Congress'"}},
                },
            ]
            self.values = []
            self.current_value = "Congress"

        def call(self, handle, method, params):
            if method == "GetVariableByName":
                assert params == ["vMode"]
                return self.handle(copy.deepcopy(self.objects["variable"]))
            if method == "SetStringValue":
                self.current_value = params[0]
                self.values.append(params[0])
                return {}
            if (
                method == "GetLayout"
                and self.handles.get(handle, {}).get("qName") == "vMode"
            ):
                return {"qLayout": {"qText": self.current_value, "qNum": "NaN"}}
            return super().call(handle, method, params)

    engine = Controls()
    db = ArchiveDB(tmp_path)
    result = capture_app(engine, db, APP)
    assert result["status"] == "complete", result
    manifest = db.manifest(result["snapshot"])
    contexts = [i["key"] for i in manifest["items"] if i["kind"] == "chart-context"]
    assert any("House" in key for key in contexts) and any(
        "Congress" in key for key in contexts
    )
    assert engine.values[-1] == "Congress"
    inv = {
        "objects": {
            "x": {
                "properties": {
                    "qProp": {
                        "actions": [
                            {
                                "actionType": "setVariable",
                                "variable": "vFlag",
                                "value": {
                                    "qStringExpression": {
                                        "qExpr": "=If(vFlag = 0,  1, 0)"
                                    }
                                },
                            },
                            {
                                "actionType": "setVariable",
                                "variable": "vUnsafe",
                                "value": {
                                    "qStringExpression": {
                                        "qExpr": "=SomeExternalFunction()"
                                    }
                                },
                            },
                        ]
                    }
                }
            }
        }
    }
    assert control_values(inv) == {"vFlag": [0, 1]}
    db.close()


def test_stale_container_ref_is_metadata_not_an_invented_object(tmp_path):
    engine = Engine()
    engine.objects["sheet"]["children"] = [{"refId": "old-migration-reference"}]
    db = ArchiveDB(tmp_path)
    result = capture_app(engine, db, APP)
    assert result["status"] == "complete", result
    assert not any(
        method == "GetObject" and params == ["old-migration-reference"]
        for _, method, params in engine.calls
    )
    db.close()


def test_real_tls_transport_archive_resume_and_no_authentication_evidence(
    tmp_path, monkeypatch, caplog
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

    from civpulse_updater.adapters.georgia_registration import LIVE_APP, MONTHLY_APP
    from civpulse_updater.adapters.georgia_turnout import ORIGIN, TENANT
    from civpulse_updater.config import load_source
    from civpulse_updater.qlik_archive import run_archive

    ca = trustme.CA()
    server_ssl = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ca.issue_cert(TENANT).configure_cert(server_ssl)
    client_ssl = ssl.create_default_context()
    ca.configure_trust(client_ssl)
    token = "synthetic-archive-token-never-store"
    errors = []
    calls = []

    def handler(ws):
        try:
            assert ws.request.headers["Authorization"] == "Bearer " + token
            assert ws.request.headers["Origin"] == ORIGIN
            engine = Engine()
            for raw in ws:
                request = json.loads(raw)
                calls.append(request)
                response = engine.call(
                    request["handle"], request["method"], request["params"]
                )
                ws.send(json.dumps({"id": request["id"], "result": response}))
        except Exception as error:
            errors.append(error)

    def http_handler(request):
        path = request.url.path
        status = 200
        if path == "/qlik-oauth-create-user":
            content = json.dumps({"access_token": token})
            status = 201
        elif path == "/shared-config.js":
            content = f'var tenant = "https://{TENANT}";'
        elif path == "/DH.ELECTION2024/js/vars.js":
            content = f'var appId = "{APP}";'
        elif path == "/DH.VOTERS/js/vars.js":
            content = (
                f'var primaryAppId = "{LIVE_APP}"; var tableAppId = "{MONTHLY_APP}";'
            )
        else:
            content = "<html>Public dashboard</html>"
        return httpx.Response(status, stream=httpx.ByteStream(content.encode()))

    install_transport(monkeypatch, http_handler)
    monkeypatch.setattr(
        "civpulse_updater.qlik_archive.public_address", lambda host: "93.184.216.34"
    )
    tcp = socket.create_connection
    with serve(
        handler,
        "127.0.0.1",
        0,
        ssl=server_ssl,
        logger=logging.Logger("test.archive.server", level=logging.CRITICAL + 1),
    ) as server:
        port = server.socket.getsockname()[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def pinned_socket(address, **kwargs):
            assert address == ("93.184.216.34", 443)
            return tcp(("127.0.0.1", port), **kwargs)

        def trusted_connect(uri, **kwargs):
            assert uri == f"wss://{TENANT}/app/{APP}"
            assert kwargs["server_hostname"] == TENANT and kwargs["proxy"] is None
            return real_connect(uri, ssl=client_ssl, **kwargs)

        monkeypatch.setattr(
            "civpulse_updater.qlik_archive.socket.create_connection", pinned_socket
        )
        monkeypatch.setattr("civpulse_updater.qlik_archive.connect", trusted_connect)
        caplog.set_level(logging.DEBUG)
        config = load_source(Path("sources"), "georgia-sos-registration")
        result = run_archive(tmp_path, config, "turnout")
        server.shutdown()
        thread.join(timeout=5)
    assert not errors
    assert result["status"] == "complete", result
    assert all(c["method"] not in ("DoSave", "DoReload", "CreateObject") for c in calls)
    assert token not in caplog.text
    for path in (tmp_path / "evidence").glob("*/*"):
        assert token.encode() not in path.read_bytes()
    sid = result["apps"][0]["snapshot"]
    verification = verify_archive(tmp_path, sid)
    assert verification["complete"] is True
    db = ArchiveDB(tmp_path)
    manifest = db.manifest(sid)
    assert manifest["frames"]
    raw = db.store.read(manifest["frames"][0]["response_sha"])
    assert b'"result": ' in raw  # Exact wire formatting, not JSON reserialization.
    db.close()


def test_stacked_outer_node_count_can_be_smaller_than_leaf_count(tmp_path):
    class OuterNodes(Engine):
        def call(self, handle, method, params):
            response = super().call(handle, method, params)
            if method == "GetHyperCubeStackData":
                page = response["qDataPages"][0]
                page["qArea"] = dict(page["qArea"], qHeight=2)
                page["qData"][0]["qSubNodes"] = page["qData"][0]["qSubNodes"][:2]
            return response

    db = ArchiveDB(tmp_path)
    result = capture_app(OuterNodes(), db, APP)
    assert result["status"] == "complete", result
    items = db.manifest(result["snapshot"])["items"]
    native = next(i for i in items if i["key"] == "object:stack:unfiltered/qHyperCube")
    assert native["expected"] == native["captured"] == 2
    assert native["descriptor"]["source_size"]["qcy"] == 3
    db.close()


def test_inaccessible_object_recovery_does_not_skip_its_chart(tmp_path):
    db = ArchiveDB(tmp_path)
    engine = Engine()
    engine.fault = "inaccessible"
    partial = capture_app(engine, db, APP)
    assert partial["status"] == "partial"
    marker = next(
        i
        for i in db.manifest(partial["snapshot"])["items"]
        if i["key"] == "context:unfiltered"
    )
    assert marker["status"] == "pending"
    recovered = capture_app(Engine(), db, APP)
    assert recovered["status"] == "complete", recovered
    assert any(
        i["key"] == "object:straight:unfiltered/qHyperCube"
        for i in db.manifest(recovered["snapshot"])["items"]
    )
    db.close()


def test_definition_drift_during_inaccessible_inventory_cannot_mix_data(tmp_path):
    db = ArchiveDB(tmp_path)
    engine = Engine()
    engine.fault = "inaccessible"
    first = capture_app(engine, db, APP)
    before = db.db.execute("SELECT count(*) FROM pages").fetchone()[0]
    changed = Engine()
    changed.objects["master"]["qMeasure"]["qDef"] = "Sum(Value)+1"
    second = capture_app(changed, db, APP)
    assert second["status"] == "partial"
    assert first["snapshot"] == second["snapshot"]
    assert db.db.execute("SELECT count(*) FROM pages").fetchone()[0] == before
    assert not any(m == "GetTableData" for _, m, _ in changed.calls)
    db.close()
