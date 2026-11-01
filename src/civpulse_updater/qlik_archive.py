"""Resumable, local archives of the three public Georgia SOS Qlik apps.

Unlike reviewed aggregate adapters, this captures original model tables, field
symbols, all object definitions and chart data. No persistent source writes or app saves.
"""

import copy
import itertools
import json
import logging
import os
import re
import socket
import sqlite3
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from websockets.exceptions import WebSocketException
from websockets.sync.client import connect

from .adapters.georgia_registration import LIVE_APP, MONTHLY_APP
from .adapters.georgia_turnout import APP, ORIGIN, TENANT
from .evidence import EvidenceStore
from .models import canonical, digest
from .transport import Fetcher, FetchError, public_address

APP_GROUPS = {
    "turnout": (APP,),
    "registration": (LIVE_APP, MONTHLY_APP),
    "all": (APP, LIVE_APP, MONTHLY_APP),
}
BOOTSTRAP = (
    "/shared-config.js",
    "/DH.ELECTION2024/index.html",
    "/DH.ELECTION2024/js/main.js",
    "/DH.ELECTION2024/js/vars.js",
    "/DH.VOTERS/index.html",
    "/DH.VOTERS/js/main.js",
    "/DH.VOTERS/js/vars.js",
)
ARCHIVE_METHODS = frozenset(
    {
        "OpenDoc",
        "GetAppLayout",
        "GetAppProperties",
        "GetAllInfos",
        "GetTablesAndKeys",
        "GetTableData",
        "GetObject",
        "GetDimension",
        "GetMeasure",
        "GetVariableById",
        "GetVariableByName",
        "SetNumValue",
        "SetStringValue",
        "GetBookmark",
        "GetProperties",
        "GetLayout",
        "GetChildInfos",
        "CreateSessionObject",
        "DestroySessionObject",
        "ClearAll",
        "GetListObjectData",
        "SelectListObjectValues",
        "GetHyperCubeData",
        "GetHyperCubePivotData",
        "GetHyperCubeStackData",
    }
)
GETTER = {
    "dimension": "GetDimension",
    "measure": "GetMeasure",
    "variable": "GetVariableById",
    "bookmark": "GetBookmark",
}
PAGE_CELLS = 10000
TABLE_ROWS = 10000
MAX_OBJECTS = 10000


class WireResult(dict):
    """A result whose original RPC bytes are already in the evidence store."""

    def __init__(self, result, wire_sha):
        super().__init__(result)
        self.wire_sha = wire_sha


def page_value(store, sha):
    value = json.loads(store.read(sha))
    return value["result"] if "id" in value and "result" in value else value


class ArchiveError(ValueError):
    """Fixed, safe diagnostics; never includes source values or bearer material."""


class ArchiveLimit(ArchiveError):
    pass


class RPCError(ArchiveError):
    def __init__(self, code):
        self.code = code if type(code) is int else None
        super().__init__("Public Qlik method returned an error")


class ArchiveDB:
    def __init__(self, root):
        root = Path(root)
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.root = root
        self.store = EvidenceStore(root / "evidence")
        self.db = sqlite3.connect(root / "archive.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS snapshots (
              id TEXT PRIMARY KEY, app TEXT NOT NULL, reload TEXT NOT NULL,
              status TEXT NOT NULL, inventory TEXT, signature TEXT, verified TEXT);
            CREATE TABLE IF NOT EXISTS items (
              snapshot TEXT NOT NULL, key TEXT NOT NULL, kind TEXT NOT NULL,
              descriptor TEXT NOT NULL, expected INTEGER NOT NULL, captured INTEGER NOT NULL DEFAULT 0,
              status TEXT NOT NULL DEFAULT 'pending', PRIMARY KEY(snapshot,key));
            CREATE TABLE IF NOT EXISTS pages (
              snapshot TEXT NOT NULL, item TEXT NOT NULL, offset INTEGER NOT NULL,
              count INTEGER NOT NULL, sha TEXT NOT NULL, PRIMARY KEY(snapshot,item,offset));
            CREATE TABLE IF NOT EXISTS frames (
              id INTEGER PRIMARY KEY AUTOINCREMENT, snapshot TEXT, app TEXT NOT NULL,
              method TEXT NOT NULL, request_sha TEXT NOT NULL, response_sha TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS issues (
              snapshot TEXT NOT NULL, key TEXT NOT NULL, code TEXT NOT NULL,
              PRIMARY KEY(snapshot,key));
        """)

    def close(self):
        self.db.close()

    def begin(self, app, reload):
        sid = digest(canonical({"app": app, "reload": reload, "version": 1}).encode())
        self.db.execute(
            "INSERT OR IGNORE INTO snapshots(id,app,reload,status) VALUES(?,?,?,'partial')",
            (sid, app, reload),
        )
        self.db.execute(
            "UPDATE snapshots SET status='partial',verified=NULL WHERE id=?", (sid,)
        )
        self.db.commit()
        return sid

    def issue(self, sid, key, code):
        self.db.execute("INSERT OR REPLACE INTO issues VALUES(?,?,?)", (sid, key, code))
        self.db.commit()

    def clear_issue(self, sid, key):
        self.db.execute("DELETE FROM issues WHERE snapshot=? AND key=?", (sid, key))
        self.db.commit()

    def inventory(self, sid, inventory):
        # Layouts contain evaluation timestamps; definitions and model identities
        # control safe resume, rather than volatile presentation values.
        signature = digest(
            canonical(
                {
                    "tables": inventory["tables"],
                    "fields": inventory["fields"],
                    "objects": {
                        k: v.get("properties") for k, v in inventory["objects"].items()
                    },
                    "variables": inventory["variables"],
                }
            ).encode()
        )
        old = self.db.execute(
            "SELECT signature FROM snapshots WHERE id=?", (sid,)
        ).fetchone()[0]
        if old is not None and old != signature:
            raise ArchiveError(
                "App definitions changed without a reload; start a new archive directory"
            )
        if old is None:
            previous_sha = self.db.execute(
                "SELECT inventory FROM snapshots WHERE id=?", (sid,)
            ).fetchone()[0]
            if previous_sha:
                previous = json.loads(self.store.read(previous_sha))
                if any(
                    previous[k] != inventory[k]
                    for k in ("tables", "fields", "variables")
                ) or set(previous["objects"]) != set(inventory["objects"]):
                    raise ArchiveError(
                        "Incomplete source inventory changed during resume"
                    )
                for oid, obj in previous["objects"].items():
                    if (
                        "properties" in obj
                        and inventory["objects"][oid].get("properties")
                        != obj["properties"]
                    ):
                        raise ArchiveError(
                            "Source definitions changed during incomplete inventory resume"
                        )
        sha = self.store.put(canonical(inventory).encode())
        self.db.execute(
            "UPDATE snapshots SET inventory=?,signature=? WHERE id=?",
            (sha, signature if not inventory["issues"] else None, sid),
        )
        self.db.commit()

    def item(self, sid, key, kind, descriptor, expected):
        if type(expected) is not int or expected < 0:
            raise ArchiveError("Invalid source size")
        body = canonical(descriptor)
        old = self.db.execute(
            "SELECT * FROM items WHERE snapshot=? AND key=?", (sid, key)
        ).fetchone()
        if old and (old["descriptor"] != body or old["expected"] != expected):
            raise ArchiveError("Source item changed during resume")
        self.db.execute(
            "INSERT OR IGNORE INTO items(snapshot,key,kind,descriptor,expected) VALUES(?,?,?,?,?)",
            (sid, key, kind, body, expected),
        )
        self.db.commit()

    def page(self, sid, key, offset, count, response):
        sha = getattr(response, "wire_sha", None) or self.store.put(
            canonical(response).encode()
        )
        with self.db:
            self.db.execute(
                "INSERT INTO pages VALUES(?,?,?,?,?)", (sid, key, offset, count, sha)
            )
            self.db.execute(
                "UPDATE items SET captured=captured+? WHERE snapshot=? AND key=?",
                (count, sid, key),
            )

    def offset(self, sid, key):
        # Verify saved bytes before skipping any page, including a completed item.
        next_offset = 0
        for row in self.db.execute(
            "SELECT * FROM pages WHERE snapshot=? AND item=? ORDER BY offset",
            (sid, key),
        ):
            if row["offset"] != next_offset or row["count"] <= 0:
                raise ArchiveError("Non-contiguous archived pages")
            self.store.read(row["sha"])
            next_offset += row["count"]
        return next_offset

    def done(self, sid, key):
        row = self.db.execute(
            "SELECT expected FROM items WHERE snapshot=? AND key=?", (sid, key)
        ).fetchone()
        if self.offset(sid, key) != row[0]:
            raise ArchiveError("Archive item is incomplete")
        self.db.execute(
            "UPDATE items SET status='complete' WHERE snapshot=? AND key=?", (sid, key)
        )
        self.clear_issue(sid, key)

    def manifest(self, sid):
        row = dict(
            self.db.execute("SELECT * FROM snapshots WHERE id=?", (sid,)).fetchone()
        )
        row["items"] = [
            dict(r)
            for r in self.db.execute(
                "SELECT * FROM items WHERE snapshot=? ORDER BY key", (sid,)
            )
        ]
        for item in row["items"]:
            item["descriptor"] = json.loads(item["descriptor"])
            item["pages"] = [
                dict(r)
                for r in self.db.execute(
                    "SELECT offset,count,sha FROM pages WHERE snapshot=? AND item=? ORDER BY offset",
                    (sid, item["key"]),
                )
            ]
        row["frames"] = [
            dict(r)
            for r in self.db.execute(
                "SELECT method,request_sha,response_sha FROM frames WHERE snapshot=? ORDER BY id",
                (sid,),
            )
        ]
        row["issues"] = [
            dict(r)
            for r in self.db.execute(
                "SELECT key,code FROM issues WHERE snapshot=? ORDER BY key", (sid,)
            )
        ]
        return row

    def finish(self, sid, verified, inventory_only=False):
        manifest = self.manifest(sid)
        complete = (
            bool(manifest["items"])
            and bool(manifest["inventory"])
            and not manifest["issues"]
            and all(
                i["status"] == "complete" and i["captured"] == i["expected"]
                for i in manifest["items"]
            )
        )
        status = (
            "inventory-only"
            if inventory_only and manifest["inventory"] and not manifest["issues"]
            else "complete"
            if complete
            else "partial"
        )
        self.db.execute(
            "UPDATE snapshots SET status=?,verified=? WHERE id=?",
            (status, verified, sid),
        )
        self.db.commit()
        result = self.manifest(sid)
        path = self.root / f"{sid}.json"
        temporary = path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as output:
            output.write(canonical(result) + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        return {
            "app_id": result["app"],
            "snapshot": sid,
            "status": status,
            "manifest": str(path.resolve()),
            "items": len(result["items"]),
            "complete_items": sum(i["status"] == "complete" for i in result["items"]),
            "captured_rows": sum(i["captured"] for i in result["items"]),
            "issues": result["issues"],
        }


def walk_data(value, path=""):
    """Find every real cube/list in layouts, including nested map/undo layers."""
    if isinstance(value, dict):
        for key, child in value.items():
            pointer = path + "/" + key.replace("~", "~0").replace("/", "~1")
            if key in ("qHyperCube", "qListObject") and isinstance(child, dict):
                yield pointer, child
            else:
                yield from walk_data(child, pointer)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk_data(child, path + f"/{index}")


def pointer(value, path):
    for part in path.strip("/").split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def session(client, app, definition):
    return client.call(app, "CreateSessionObject", [definition])["qReturn"]["qHandle"]


def get_object(client, app, oid, kind):
    return client.call(app, GETTER.get(kind, "GetObject"), [oid])["qReturn"]["qHandle"]


def inventory(client, app):
    result = {
        "app_layout": client.call(app, "GetAppLayout", []),
        "app_properties": client.call(app, "GetAppProperties", []),
        "tables": client.call(
            app,
            "GetTablesAndKeys",
            [{"qcx": 1000, "qcy": 1000}, {"qcx": 0, "qcy": 0}, 30, True, False],
        ),
        "objects": {},
        "issues": [],
    }
    field_handle = session(
        client,
        app,
        {
            "qInfo": {"qType": "archive-fields"},
            "qFieldListDef": {
                "qShowSystem": True,
                "qShowHidden": True,
                "qShowSemantic": True,
                "qShowDerivedFields": True,
            },
        },
    )
    result["fields"] = client.call(field_handle, "GetLayout", [])["qLayout"][
        "qFieldList"
    ]
    variable_handle = session(
        client,
        app,
        {
            "qInfo": {"qType": "archive-variables"},
            "qVariableListDef": {
                "qType": "variable",
                "qShowSession": True,
                "qShowReserved": True,
                "qShowConfig": True,
                "qData": {"definition": "/qDefinition"},
            },
        },
    )
    result["variables"] = client.call(variable_handle, "GetLayout", [])["qLayout"][
        "qVariableList"
    ]
    queue = client.call(app, "GetAllInfos", [])["qInfos"]
    # GetAllInfos need not contain variables, master items or nested children.
    queue = [o for o in queue if not o["qType"].startswith("archive-")] + [
        v["qInfo"]
        for v in result["variables"].get("qItems", [])
        if v.get("qInfo", {}).get("qId")
    ]
    seen = set()
    while queue:
        obj = queue.pop(0)
        oid, kind = obj["qId"], obj["qType"]
        if oid in seen:
            continue
        seen.add(oid)
        if len(seen) > MAX_OBJECTS:
            raise ArchiveError("Object inventory exceeds bounded scope")
        try:
            handle = get_object(client, app, oid, kind)
            if handle < 0:
                raise ArchiveError("Object inaccessible")
            props = client.call(handle, "GetProperties", [])
            layout = client.call(handle, "GetLayout", [])
            children = (
                client.call(handle, "GetChildInfos", [])
                if kind not in GETTER
                else {"qInfos": []}
            )
            result["objects"][oid] = {
                "kind": kind,
                "properties": props,
                "layout": layout,
                "children": children,
            }
            queue.extend(children["qInfos"])
            # GetChildInfos is authoritative. Old refIds in migrated container
            # properties can be stale; retain those properties verbatim rather
            # than inventing accessible objects from their historical references.
        except RPCError as error:
            result["objects"][oid] = {"kind": kind, "rpc_error": error.code}
            result["issues"].append({"key": "object:" + oid, "code": "inaccessible"})
    if (
        not result["tables"].get("qtr")
        or not result["fields"].get("qItems")
        or not result["objects"]
    ):
        raise ArchiveError("Source inventory is unexpectedly empty")
    # Inventory metadata handles are session-local and not needed afterwards.
    for handle in (field_handle, variable_handle):
        client.call(
            app,
            "DestroySessionObject",
            [client.call(handle, "GetLayout", [])["qLayout"]["qInfo"]["qId"]],
        )
    return result


def rectangular(response, area, mode):
    pages = response.get("qDataPages", [])
    if len(pages) != 1:
        raise ArchiveError("Missing or extra Qlik data pages")
    page = pages[0]
    if page.get("qArea") != area:
        raise ArchiveError("Short or misplaced Qlik page")
    if mode in ("S", "L"):
        rows = page.get("qMatrix", [])
        if len(rows) != area["qHeight"] or any(len(r) != area["qWidth"] for r in rows):
            raise ArchiveError("Truncated Qlik matrix")
    elif mode == "P":
        rows = page.get("qData", [])
        if len(rows) != area["qHeight"] or any(len(r) != area["qWidth"] for r in rows):
            raise ArchiveError("Truncated pivot page")
    elif mode == "K":
        if not page.get("qData") and area["qHeight"]:
            raise ArchiveError("Empty stacked page")


def capture_cube(client, db, sid, key, handle, path, data, descriptor):
    mode = "L" if path.endswith("qListObject") else data.get("qMode", "S")
    method = {
        "S": "GetHyperCubeData",
        "P": "GetHyperCubePivotData",
        "K": "GetHyperCubeStackData",
        "L": "GetListObjectData",
    }.get(mode)
    if method is None:
        db.issue(sid, key, "unsupported-cube-mode")
        return
    width, height = data.get("qSize", {}).get("qcx"), data.get("qSize", {}).get("qcy")
    if (
        type(width) is not int
        or type(height) is not int
        or width < 0
        or height < 0
        or width > PAGE_CELLS
    ):
        raise ArchiveError("Invalid Qlik cube size")
    if data.get("qError") or data.get("qCalcCondMsg"):
        if data.get("qError", {}).get("qErrorCode") == 7005:
            # The original visual is explicitly suppressed in this context. Its
            # layout is retained, and a separate projection captures its values.
            db.item(
                sid,
                key,
                "source-suppressed-chart",
                descriptor
                | {
                    "path": path,
                    "source_error": data.get("qError"),
                    "message": data.get("qCalcCondMsg"),
                },
                0,
            )
            db.done(sid, key)
        else:
            db.issue(sid, key, "source-calculation-unavailable")
        return
    if mode == "K":
        capture_stack(client, db, sid, key, handle, path, data, descriptor)
        return
    definition_path = path + "Def"
    descriptor = descriptor | {"path": path, "mode": mode, "width": width}
    db.item(
        sid,
        key,
        "field-values" if "field" in descriptor else "chart",
        descriptor,
        height if width else 0,
    )
    top = db.offset(sid, key)
    step = PAGE_CELLS // max(1, width)
    while width and top < height:
        count = min(step, height - top)
        area = {"qTop": top, "qLeft": 0, "qHeight": count, "qWidth": width}
        response = client.call(handle, method, [definition_path, [area]])
        rectangular(response, area, mode)
        db.page(sid, key, top, count, response)
        top += count
    db.done(sid, key)


def capture_stack(client, db, sid, key, handle, path, data, descriptor):
    """Qlik stacked pages count outer nodes, while qSize counts leaf rows."""
    width = data["qSize"]["qcx"]
    leaf_rows = data["qSize"]["qcy"]
    descriptor = descriptor | {
        "path": path,
        "mode": "K",
        "width": width,
        "source_size": data["qSize"],
        "row_semantics": "outer-nodes",
    }
    saved = db.db.execute(
        "SELECT expected FROM items WHERE snapshot=? AND key=?", (sid, key)
    ).fetchone()
    top = db.offset(sid, key) if saved else 0
    if saved and top == saved["expected"]:
        db.done(sid, key)
        return
    if not width or not leaf_rows:
        db.item(sid, key, "chart", descriptor, 0)
        db.done(sid, key)
        return
    while True:
        count = min(PAGE_CELLS // width, leaf_rows)
        area = {"qTop": top, "qLeft": 0, "qHeight": count, "qWidth": width}
        response = client.call(handle, "GetHyperCubeStackData", [path + "Def", [area]])
        pages = response.get("qDataPages", [])
        if len(pages) != 1:
            raise ArchiveError("Missing stacked chart page")
        page = pages[0]
        actual = page.get("qArea", {})
        height = actual.get("qHeight")
        if (
            type(height) is not int
            or not 0 < height <= count
            or actual != area | {"qHeight": height}
        ):
            raise ArchiveError("Misplaced stacked chart page")
        roots = page.get("qData", [])
        if not roots or any(
            r.get("qType") != "R" or len(r.get("qSubNodes", [])) != height
            for r in roots
        ):
            raise ArchiveError("Truncated stacked chart tree")
        tails = {r.get("qDown") for r in roots}
        if len(tails) != 1 or any(type(v) is not int or v < 0 for v in tails):
            raise ArchiveError("Invalid stacked chart continuation")
        tail = next(iter(tails))
        expected = top + height + tail
        db.item(sid, key, "chart", descriptor, expected)
        db.page(sid, key, top, height, response)
        top += height
        if not tail:
            break
    db.done(sid, key)


def capture_objects(client, db, sid, app, inv, context):
    context_key = "context:" + context
    db.item(
        sid,
        context_key,
        "chart-context",
        {"context": context, "objects": sorted(inv["objects"])},
        1,
    )
    if db.offset(sid, context_key):
        for item in db.manifest(sid)["items"]:
            if item["descriptor"].get("context") == context:
                db.offset(sid, item["key"])
        return
    selection_handle = session(
        client,
        app,
        {"qInfo": {"qType": "archive-selections"}, "qSelectionObjectDef": {}},
    )
    selection_layout = client.call(selection_handle, "GetLayout", [])["qLayout"]
    selection = selection_layout["qSelectionObject"]
    client.call(app, "DestroySessionObject", [selection_layout["qInfo"]["qId"]])
    for oid, obj in sorted(inv["objects"].items()):
        if "layout" not in obj:
            continue
        handle = get_object(client, app, oid, obj["kind"])
        layout = client.call(handle, "GetLayout", [])["qLayout"]
        key = f"object:{oid}:{context}"
        db.item(sid, key, "object-layout", {"object": oid, "context": context}, 1)
        if not db.offset(sid, key):
            db.page(sid, key, 0, 1, {"qLayout": layout})
        db.done(sid, key)
        for path, cube in walk_data(layout):
            cube_key = key + path
            try:
                if path.endswith("qHyperCube") and (
                    cube.get("qMode") in ("P", "K")
                    or cube.get("qError")
                    or cube.get("qCalcCondMsg")
                ):
                    capture_projection(
                        client,
                        db,
                        sid,
                        app,
                        cube_key,
                        obj["properties"]["qProp"],
                        path,
                        context,
                    )
                capture_cube(
                    client,
                    db,
                    sid,
                    cube_key,
                    handle,
                    path,
                    cube,
                    {"object": oid, "context": context},
                )
            except ArchiveError as error:
                if isinstance(error, ArchiveLimit):
                    raise
                db.issue(
                    sid,
                    cube_key,
                    "rpc-error"
                    if isinstance(error, RPCError)
                    else "incomplete-chart-page",
                )

    manifest = db.manifest(sid)
    related = [
        i
        for i in manifest["items"]
        if i["descriptor"].get("context") == context and i["key"] != context_key
    ]
    prefixes = ["object:" + oid + ":" + context for oid in inv["objects"]]
    if (
        all("layout" in obj for obj in inv["objects"].values())
        and all(i["status"] == "complete" for i in related)
        and not any(
            any(e["key"].startswith(prefix) for prefix in prefixes)
            for e in manifest["issues"]
        )
    ):
        db.page(
            sid,
            context_key,
            0,
            1,
            {
                "context": context,
                "objects": sorted(inv["objects"]),
                "selections": selection,
            },
        )
        db.done(sid, context_key)


def capture_projection(client, db, sid, app, key, props, path, context):
    definition_path = path + "Def"
    try:
        definition = copy.deepcopy(pointer(props, definition_path))
    except (KeyError, IndexError, TypeError):
        db.issue(sid, key + ":projection", "missing-chart-definition")
        return
    definition.update(
        qMode="S",
        qAlwaysFullyExpanded=True,
        qInitialDataFetch=[],
        qCalcCond={},
        qCalcCondition={"qCond": {}, "qMsg": {}},
    )
    definition["qNoOfLeftDims"] = -1
    handle = session(
        client,
        app,
        {"qInfo": {"qType": "archive-chart-projection"}, "qHyperCubeDef": definition},
    )
    layout = client.call(handle, "GetLayout", [])["qLayout"]
    descriptor = {
        "context": context,
        "derived": True,
        "source_path": path,
        "source_definition": pointer(props, definition_path),
        "projection_definition": definition,
    }
    capture_cube(
        client,
        db,
        sid,
        key + ":projection",
        handle,
        "/qHyperCube",
        layout["qHyperCube"],
        descriptor,
    )
    client.call(app, "DestroySessionObject", [layout["qInfo"]["qId"]])


def capture_fields(client, db, sid, app, inv):
    for field in sorted(inv["fields"]["qItems"], key=lambda f: f["qName"]):
        name = field["qName"]
        key = "field:" + name
        saved = db.db.execute(
            "SELECT status,expected FROM items WHERE snapshot=? AND key=?", (sid, key)
        ).fetchone()
        if (
            saved is not None
            and saved["status"] == "complete"
            and db.offset(sid, key) == saved["expected"]
        ):
            continue
        handle = session(
            client,
            app,
            {
                "qInfo": {"qType": "archive-field-values"},
                "qListObjectDef": {
                    "qDef": {"qFieldDefs": [name]},
                    "qShowAlternatives": True,
                    "qFrequencyMode": "V",
                },
            },
        )
        layout = client.call(handle, "GetLayout", [])["qLayout"]
        if layout["qListObject"]["qSize"]["qcy"] < field.get("qCardinal", 0):
            raise ArchiveError(
                "Field symbol list is smaller than its source cardinality"
            )
        try:
            capture_cube(
                client,
                db,
                sid,
                key,
                handle,
                "/qListObject",
                layout["qListObject"],
                {"field": name, "metadata": field},
            )
        except ArchiveError as error:
            if isinstance(error, ArchiveLimit):
                raise
            db.issue(sid, key, "field-values-unavailable")
        client.call(app, "DestroySessionObject", [layout["qInfo"]["qId"]])


def capture_tables(client, db, sid, app, inv):
    for table in sorted(
        inv["tables"]["qtr"], key=lambda t: (t["qNoOfRows"], t["qName"])
    ):
        key = "table:" + table["qName"]
        total = table["qNoOfRows"]
        db.item(
            sid, key, "model-table", {"table": table, "synthetic_mode": True}, total
        )
        offset = db.offset(sid, key)
        while offset < total:
            count = min(TABLE_ROWS, total - offset)
            response = client.call(
                app, "GetTableData", [offset, count, True, table["qName"]]
            )
            rows = response.get("qData", [])
            if len(rows) != count or any(
                len(r.get("qValue", [])) != len(table["qFields"]) for r in rows
            ):
                raise ArchiveError("Truncated source model table")
            db.page(sid, key, offset, count, response)
            offset += count
        db.done(sid, key)


def control_values(inv):
    """Only literal values and the source's simple numeric toggle are executable."""
    values = {}
    for obj in inv["objects"].values():
        for action in obj.get("properties", {}).get("qProp", {}).get("actions", []):
            name = action.get("variable")
            if action.get("actionType") != "setVariable" or not isinstance(name, str):
                continue
            expr = action.get("value", {})
            expr = (
                expr.get("qStringExpression", {}).get("qExpr")
                if isinstance(expr, dict)
                else expr
            )
            if not isinstance(expr, str):
                continue
            literal = re.fullmatch(r"['\"]([^'\"]*)['\"]", expr.strip())
            toggle = re.fullmatch(
                r"=?If\(\s*" + re.escape(name) + r"\s*=\s*0\s*,\s*1\s*,\s*0\s*\)",
                expr.strip(),
                re.IGNORECASE,
            )
            if literal:
                values.setdefault(name, set()).add(literal.group(1))
            elif toggle:
                values.setdefault(name, set()).update((0, 1))
    return {name: sorted(v, key=str) for name, v in sorted(values.items())}


def capture_variants(client, db, sid, app, inv, context):
    capture_objects(client, db, sid, app, inv, context)
    controls = control_values(inv)
    if not controls:
        return
    handles, originals = {}, {}
    try:
        for name in controls:
            handle = client.call(app, "GetVariableByName", [name])["qReturn"]["qHandle"]
            handles[name] = handle
            originals[name] = client.call(handle, "GetLayout", [])["qLayout"]
        for variant in itertools.product(*controls.values()):
            selection = dict(zip(controls, variant, strict=True))
            for name, value in selection.items():
                method = "SetNumValue" if isinstance(value, int) else "SetStringValue"
                client.call(handles[name], method, [value])
                actual = client.call(handles[name], "GetLayout", [])["qLayout"]
                if (isinstance(value, int) and actual.get("qNum") != value) or (
                    isinstance(value, str) and actual.get("qText") != value
                ):
                    raise ArchiveError("Chart control value was not applied")
            capture_objects(
                client, db, sid, app, inv, context + ";controls=" + canonical(selection)
            )
    finally:
        # Variable changes are confined to this anonymous, unsaved session.
        # On budget/socket failure, closing the session also discards them.
        for name, value in originals.items():
            number = value.get("qNum")
            if type(number) in (int, float):
                client.call(handles[name], "SetNumValue", [number])
            else:
                client.call(handles[name], "SetStringValue", [value.get("qText", "")])


def capture_contexts(client, db, sid, app, inv, app_id):
    capture_variants(client, db, sid, app, inv, "unfiltered")
    field = (
        "Election Date"
        if app_id == APP
        else "AsOfDate"
        if app_id == MONTHLY_APP
        else None
    )
    if field is None:
        return
    handle = session(
        client,
        app,
        {
            "qInfo": {"qType": "archive-contexts"},
            "qListObjectDef": {"qDef": {"qFieldDefs": [field]}},
        },
    )
    layout = client.call(handle, "GetLayout", [])["qLayout"]
    count = layout["qListObject"]["qSize"]["qcy"]
    if type(count) is not int or not 0 <= count <= PAGE_CELLS:
        raise ArchiveError("Context inventory exceeds bounded scope")
    if count:
        area = {"qTop": 0, "qLeft": 0, "qHeight": count, "qWidth": 1}
        result = client.call(handle, "GetListObjectData", ["/qListObjectDef", [area]])
        rectangular(result, area, "L")
        values = result["qDataPages"][0]["qMatrix"]
        for row in values:
            client.call(app, "ClearAll", [True])
            elem = row[0]["qElemNumber"]
            if (
                client.call(
                    handle, "SelectListObjectValues", ["/qListObjectDef", [elem], False]
                ).get("qSuccess")
                is not True
            ):
                raise ArchiveError("Chart context selection failed")
            selected = client.call(
                handle, "GetListObjectData", ["/qListObjectDef", [area]]
            )
            rectangular(selected, area, "L")
            if [
                r[0]["qElemNumber"]
                for r in selected["qDataPages"][0]["qMatrix"]
                if r[0]["qState"] in ("S", "L")
            ] != [elem]:
                raise ArchiveError("Chart context selection mismatch")
            label = field + ":" + row[0].get("qText", str(elem))
            capture_variants(client, db, sid, app, inv, label)
    client.call(app, "ClearAll", [True])
    client.call(app, "DestroySessionObject", [layout["qInfo"]["qId"]])


def capture_app(client, db, app_id, *, inventory_only=False, progress=None):
    app = client.call(-1, "OpenDoc", [app_id])["qReturn"]["qHandle"]
    before = client.call(app, "GetAppLayout", [])["qLayout"]
    if before["qFileName"] != app_id or not before["qHasData"]:
        raise ArchiveError("Unexpected public app identity")
    sid = db.begin(app_id, before["qLastReloadTime"])
    client.snapshot_id = sid
    try:
        client.call(app, "ClearAll", [True])
        inv = inventory(client, app)
        if hasattr(client, "bootstrap_files"):
            inv["bootstrap"] = client.bootstrap_files
        db.inventory(sid, inv)
        for oid, obj in inv["objects"].items():
            if "properties" in obj:
                db.clear_issue(sid, "object:" + oid)
        for issue in inv["issues"]:
            db.issue(sid, issue["key"], issue["code"])
        if progress:
            progress(
                {
                    "app_id": app_id,
                    "phase": "inventory",
                    "objects": len(inv["objects"]),
                    "fields": len(inv["fields"]["qItems"]),
                    "tables": len(inv["tables"]["qtr"]),
                    "model_rows": sum(t["qNoOfRows"] for t in inv["tables"]["qtr"]),
                }
            )
        if not inventory_only:
            capture_contexts(client, db, sid, app, inv, app_id)
            capture_fields(client, db, sid, app, inv)
            capture_tables(client, db, sid, app, inv)
            require_coverage(db, sid, inv)
        after = client.call(app, "GetAppLayout", [])["qLayout"]
        if before["qLastReloadTime"] != after["qLastReloadTime"]:
            raise ArchiveError(
                "App reloaded; captured generations must not be combined"
            )
        db.clear_issue(sid, "capture")
        return db.finish(sid, datetime.now(UTC).isoformat(), inventory_only)
    except (
        ValueError,
        FetchError,
        OSError,
        KeyError,
        TypeError,
        WebSocketException,
    ) as error:
        db.issue(
            sid,
            "capture",
            str(error) if isinstance(error, ArchiveError) else type(error).__name__,
        )
        return db.finish(sid, None)


class ArchiveTransport(Fetcher):
    def __init__(self, config, db):
        super().__init__(config)
        self.db = db
        self.app_id = None
        self.sequence = 0

    def before_request(self, deadline=None):
        if self.requests >= self.config.max_requests:
            raise ArchiveLimit("Archive request budget reached; run again to resume")
        delay = max(
            0, self.config.request_interval - (time.monotonic() - self.last_request)
        )
        if deadline is not None and time.monotonic() + delay >= deadline:
            raise ArchiveLimit("Archive response deadline reached")
        time.sleep(delay)
        self.requests += 1
        self.last_request = time.monotonic()

    def bootstrap(self):
        files = {}
        for path in BOOTSTRAP:
            body, evidence = self.get(ORIGIN + path)
            files[path] = self.db.store.put(body)
        variables = self.db.store.read(files["/DH.ELECTION2024/js/vars.js"]).decode()
        registration = self.db.store.read(files["/DH.VOTERS/js/vars.js"]).decode()
        tenant = self.db.store.read(files["/shared-config.js"]).decode()
        for name, value, text in (
            ("appId", APP, variables),
            ("primaryAppId", LIVE_APP, registration),
            ("tableAppId", MONTHLY_APP, registration),
            ("tenant", "https://" + TENANT, tenant),
        ):
            if not re.search(
                r"var\s+" + name + r'\s*=\s*["\']' + re.escape(value) + r'["\']', text
            ):
                raise ArchiveError("Public dashboard configuration changed")
        self.bootstrap_files = files
        return files

    @contextmanager
    def session(self, app_id):
        if app_id not in APP_GROUPS["all"]:
            raise ArchiveError("App outside approved scope")
        body, _, status, headers = self.exchange(
            ORIGIN + "/qlik-oauth-create-user",
            max_bytes=65536,
            body_statuses=(200, 201),
        )
        if status not in (200, 201) or "retry-after" in headers:
            raise ArchiveError("Anonymous public authentication unavailable")
        token = json.loads(body)["access_token"]
        if (
            not isinstance(token, str)
            or not token
            or len(token) > 32768
            or "\r" in token
            or "\n" in token
        ):
            raise ArchiveError("Invalid public authentication response")
        self.before_request()
        self.app_id = app_id
        self.snapshot_id = None
        with socket.create_connection(
            (public_address(TENANT), 443), timeout=self.config.response_seconds
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
                    "civpulse.archive.qlik", level=logging.CRITICAL + 1
                ),
            ) as ws:
                self.ws = ws
                try:
                    yield self
                finally:
                    self.ws = None

    def call(self, handle, method, params):
        if method not in ARCHIVE_METHODS:
            raise ArchiveError("Unsupported archive RPC")
        self.before_request()
        self.sequence += 1
        request = {
            "jsonrpc": "2.0",
            "id": self.sequence,
            "handle": handle,
            "method": method,
            "params": params,
        }
        self.ws.send(canonical(request))
        deadline = time.monotonic() + self.config.response_seconds
        for _ in range(100):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ArchiveLimit("Archive response deadline reached")
            raw = self.ws.recv(timeout=remaining)
            if not isinstance(raw, str) or len(raw.encode()) > self.config.max_bytes:
                raise ArchiveLimit("Archive response exceeds byte budget")
            self.consume(len(raw.encode()))
            response = json.loads(raw)
            if response.get("id") != self.sequence:
                if "id" in response:
                    raise ArchiveError("Unexpected archive RPC identity")
                continue
            if "error" in response:
                raise RPCError(response["error"].get("code"))
            request_sha = self.db.store.put(canonical(request).encode())
            response_sha = self.db.store.put(raw.encode())
            self.db.db.execute(
                "INSERT INTO frames(snapshot,app,method,request_sha,response_sha) VALUES(?,?,?,?,?)",
                (
                    getattr(self, "snapshot_id", None),
                    self.app_id,
                    method,
                    request_sha,
                    response_sha,
                ),
            )
            self.db.db.commit()
            if getattr(self, "progress", None) and self.requests % 100 == 0:
                self.progress(
                    {
                        "app_id": self.app_id,
                        "phase": "requests",
                        "requests": self.requests,
                        "received_bytes": self.received_bytes,
                    }
                )
            return WireResult(response["result"], response_sha)
        raise ArchiveLimit("Archive notification budget reached")


def run_archive(root, config, group="all", *, inventory_only=False, progress=None):
    if group not in APP_GROUPS:
        raise ArchiveError("Unknown public app group")
    db = ArchiveDB(root)
    results = []
    try:
        transport = ArchiveTransport(config, db)
        transport.progress = progress
        bootstrap = transport.bootstrap()
        for app_id in APP_GROUPS[group]:
            try:
                with transport.session(app_id) as client:
                    result = capture_app(
                        client,
                        db,
                        app_id,
                        inventory_only=inventory_only,
                        progress=progress,
                    )
                    results.append(result)
            except (
                ValueError,
                FetchError,
                OSError,
                KeyError,
                TypeError,
                WebSocketException,
            ):
                results.append(
                    {
                        "app_id": app_id,
                        "status": "partial",
                        "issues": [
                            {
                                "key": "session",
                                "code": "unavailable-or-budget-exhausted",
                            }
                        ],
                    }
                )
        return {
            "mode": "local-archive",
            "status": "inventory-only"
            if inventory_only and all(r["status"] == "inventory-only" for r in results)
            else "complete"
            if all(r["status"] == "complete" for r in results)
            else "partial",
            "bootstrap": bootstrap,
            "apps": results,
            "requests": transport.requests,
            "received_bytes": transport.received_bytes,
        }
    finally:
        db.close()


def require_coverage(db, sid, inv):
    items = {item["key"] for item in db.manifest(sid)["items"]}
    expected = {"table:" + table["qName"] for table in inv["tables"]["qtr"]}
    expected.update("field:" + field["qName"] for field in inv["fields"]["qItems"])
    expected.update(
        "object:" + oid + ":unfiltered"
        for oid, obj in inv["objects"].items()
        if "layout" in obj
    )
    expected.add("context:unfiltered")
    if not expected.issubset(items):
        raise ArchiveError("Source inventory has uncaptured items")


def verify_archive(root, snapshot):
    """Offline checksum, RPC identity and page-coverage verification."""
    db = ArchiveDB(root)
    try:
        row = db.db.execute(
            "SELECT * FROM snapshots WHERE id=?", (snapshot,)
        ).fetchone()
        if row is None:
            raise ArchiveError("Unknown archive snapshot")
        result = db.manifest(snapshot)
        if not result["inventory"]:
            raise ArchiveError("Archive inventory is missing")
        inv = json.loads(db.store.read(result["inventory"]))
        for sha in inv.get("bootstrap", {}).values():
            db.store.read(sha)
        if (
            inv["app_layout"]["qLayout"]["qFileName"] != result["app"]
            or inv["app_layout"]["qLayout"]["qLastReloadTime"] != result["reload"]
        ):
            raise ArchiveError("Archive generation identity mismatch")
        for frame in result["frames"]:
            request = json.loads(db.store.read(frame["request_sha"]))
            response = json.loads(db.store.read(frame["response_sha"]))
            if (
                request["method"] not in ARCHIVE_METHODS
                or request["id"] != response["id"]
                or "error" in response
            ):
                raise ArchiveError("Invalid archived RPC identity")
        for item in result["items"]:
            count = db.offset(snapshot, item["key"])
            if (
                count != item["captured"]
                or count > item["expected"]
                or (item["status"] == "complete" and count != item["expected"])
            ):
                raise ArchiveError("Archive item coverage mismatch")
            descriptor = item["descriptor"]
            for page in item["pages"]:
                value = page_value(db.store, page["sha"])
                if item["kind"] in ("chart", "field-values"):
                    area = {
                        "qLeft": 0,
                        "qTop": page["offset"],
                        "qWidth": descriptor["width"],
                        "qHeight": page["count"],
                    }
                    rectangular(value, area, descriptor["mode"])
                elif item["kind"] == "model-table":
                    if len(value.get("qData", [])) != page["count"] or any(
                        len(r.get("qValue", [])) != len(descriptor["table"]["qFields"])
                        for r in value["qData"]
                    ):
                        raise ArchiveError("Archived model table is truncated")
        if result["status"] == "complete":
            require_coverage(db, snapshot, inv)
        complete = (
            result["status"] == "complete"
            and bool(result["items"])
            and not result["issues"]
            and result["verified"] is not None
            and all(i["status"] == "complete" for i in result["items"])
        )
        return {
            "snapshot": snapshot,
            "integrity": "verified",
            "complete": complete,
            "status": result["status"],
            "items": len(result["items"]),
        }
    finally:
        db.close()
