"""SOS public Qlik aggregate tables, with the same recipe for capture and replay.

Only existing aggregate tables are read. Selection changes and the selection
metadata object are session-local; no persistent app objects or data are changed.
"""

import json
import math
from datetime import datetime

from ..models import Envelope, Manifest, Reference, TurnoutRecord, canonical, digest

ORIGIN = "https://d11khjvhu86vzz.cloudfront.net"
HUB = ORIGIN + "/DH.ELECTION2024/index.html"
SOURCE = "https://sos.ga.gov/page/election-data-hub-unofficial-turnout"
TENANT = "sos-ga-gov.us.qlikcloudgov.com"
APP = "137c0a43-928b-427a-a267-eb55aa7fa8a9"
ELECTION_FILTER = "02358b37-2a62-4658-ab66-39a1f6db440d"
# Fixed public aggregate objects, not arbitrary fields or user-supplied RPCs.
TABLES = {
    "early-voting-by-county": (
        "7cc869cd-2124-43be-82f4-1c8d394dc6d8",
        ("County",),
        ("Ballots Accepted",),
    ),
    "absentee-by-county": (
        "CTgPMg",
        ("County",),
        (
            "Ballots Requested",
            "Ballots Issued",
            "Ballots Returned",
            "Ballots Accepted",
            "% Accepted of Returned",
            "Ballots Rejected",
            "Other (Spoiled & Canceled)",
            "Ballots Outstanding",
        ),
    ),
    "turnout-by-county-and-ballot-style": (
        "QDFdpV",
        ("County", "Ballot Style"),
        ("Turnout",),
    ),
}
MAX_ELECTIONS = 100
MAX_ROWS = 2000
PAGE_CELLS = 1000
# Pin the published calculated Ballot Style dimension, including source comments.
# Inspect a changed expression before widening the aggregate collection scope.
BALLOT_STYLE_FIELDS_SHA = (
    "aab2c41a0d192b0869d594125cd63d25621eb0fb84971ab787e66fff5f7a8a95"
)


def object_handle(client, app_handle, oid):
    return client.call(app_handle, "GetObject", [oid])["qReturn"]["qHandle"]


def election_rows(client, handle):
    layout = client.call(handle, "GetLayout", [])["qLayout"]["qListObject"]
    count = layout["qSize"]["qcy"]
    if (
        type(count) is not int
        or not 1 <= count <= MAX_ELECTIONS
        or layout["qDimensionInfo"]["qGroupFieldDefs"] != ["Election Date"]
    ):
        raise ValueError("Unexpected election filter")
    pages = client.call(
        handle,
        "GetListObjectData",
        ["/qListObjectDef", [{"qTop": 0, "qLeft": 0, "qHeight": count, "qWidth": 1}]],
    )["qDataPages"]
    rows = matrix(pages, 0, count, 1)
    values = []
    dates, elements = set(), set()
    for row in rows:
        cell = row[0]
        day = datetime.strptime(cell["qText"], "%m/%d/%Y").date()
        elem = cell["qElemNumber"]
        if type(elem) is not int or elem < 0 or day in dates or elem in elements:
            raise ValueError("Duplicate or invalid election identity")
        dates.add(day)
        elements.add(elem)
        values.append((day, elem, cell["qState"]))
    return values


def matrix(pages, top, height, width):
    if len(pages) != 1:
        raise ValueError("Unexpected data page count")
    page = pages[0]
    if page["qArea"] != {"qTop": top, "qLeft": 0, "qHeight": height, "qWidth": width}:
        raise ValueError("Incomplete or misplaced data page")
    rows = page["qMatrix"]
    if len(rows) != height or any(len(row) != width for row in rows):
        raise ValueError("Incomplete data matrix")
    return rows


def table(client, app_handle, key):
    oid, dimensions, measures = TABLES[key]
    handle = object_handle(client, app_handle, oid)
    props = client.call(handle, "GetProperties", [])["qProp"]
    fields = [d["qDef"]["qFieldDefs"] for d in props["qHyperCubeDef"]["qDimensions"]]
    if (
        len(fields) != len(dimensions)
        or fields[0] != ["County"]
        or (
            len(fields) == 2
            and digest(canonical({"fields": fields[1]}).encode())
            != BALLOT_STYLE_FIELDS_SHA
        )
    ):
        raise ValueError("Aggregate dimension definitions changed")
    layout = client.call(handle, "GetLayout", [])["qLayout"]
    cube = layout["qHyperCube"]
    if (
        props["qInfo"]["qId"] != oid
        or layout["qInfo"]["qId"] != oid
        or props["qHyperCubeDef"]["qMode"] != "S"
        or cube.get("qError")
        or cube.get("qCalcCondMsg")
        or tuple(d["qFallbackTitle"] for d in cube["qDimensionInfo"]) != dimensions
        or tuple(m["qFallbackTitle"] for m in cube["qMeasureInfo"]) != measures
    ):
        raise ValueError("Aggregate table schema changed")
    size = cube["qSize"]
    width, height = size["qcx"], size["qcy"]
    if (
        type(width) is not int
        or width != len(dimensions) + len(measures)
        or type(height) is not int
        or not 0 <= height <= MAX_ROWS
    ):
        raise ValueError("Aggregate table size exceeds scope")
    rows = []
    step = PAGE_CELLS // width
    for top in range(0, height, step):
        count = min(step, height - top)
        pages = client.call(
            handle,
            "GetHyperCubeData",
            [
                "/qHyperCubeDef",
                [{"qTop": top, "qLeft": 0, "qHeight": count, "qWidth": width}],
            ],
        )["qDataPages"]
        rows.extend(matrix(pages, top, count, width))
    after = client.call(handle, "GetLayout", [])["qLayout"]
    if after != layout:
        raise ValueError("Aggregate layout changed during capture")
    normalized, seen = [], set()
    missing = False
    for row in rows:
        dims = tuple(cell["qText"] for cell in row[: len(dimensions)])
        if (
            any(
                cell.get("qIsNull")
                or type(cell.get("qElemNumber")) is not int
                or cell["qElemNumber"] < 0
                for cell in row[: len(dimensions)]
            )
            or any(not isinstance(value, str) or not value.strip() for value in dims)
            or dims in seen
        ):
            raise ValueError("Duplicate or missing aggregate dimensions")
        seen.add(dims)
        values = {}
        for label, cell in zip(measures, row[len(dimensions) :], strict=True):
            value = cell.get("qNum")
            if cell.get("qIsNull") or value in (None, "NaN"):
                value = None
                missing = True
            elif (
                type(value) not in (int, float)
                or value < 0
                or value > 2**53 - 1
                or not math.isfinite(value)
            ):
                raise ValueError("Invalid turnout measure")
            elif label != "% Accepted of Returned" and value != int(value):
                raise ValueError("Nonintegral turnout count")
            values[label] = {"value": value, "display": cell["qText"]}
        normalized.append(
            {"dimensions": dict(zip(dimensions, dims, strict=True)), "measures": values}
        )
    normalized.sort(key=lambda row: canonical(row["dimensions"]))
    return {
        "table": key,
        "object_id": oid,
        "source_title": layout["title"],
        "source_as_of": layout.get("footnote", ""),
        "dimensions": list(dimensions),
        "measures": list(measures),
        # Expressions/formatting establish the source meaning of each measure.
        "definition": props["qHyperCubeDef"],
        "rows": normalized,
    }, missing


def read_tables(client, window):
    app_handle = client.call(-1, "OpenDoc", [APP])["qReturn"]["qHandle"]
    before = client.call(app_handle, "GetAppLayout", [])["qLayout"]
    if (
        before["qFileName"] != APP
        or not before["qHasData"]
        or before.get("qStateNames")
    ):
        raise ValueError("Unexpected turnout application")
    filter_handle = object_handle(client, app_handle, ELECTION_FILTER)
    available = election_rows(client, filter_handle)
    wanted = [
        (day, elem) for day, elem, _ in available if window.start <= day <= window.end
    ]
    results = []
    selections_handle = None
    if wanted:
        selections_handle = client.call(
            app_handle,
            "CreateSessionObject",
            [{"qInfo": {"qType": "CurrentSelections"}, "qSelectionObjectDef": {}}],
        )["qReturn"]["qHandle"]
    for day, elem in wanted:
        client.call(app_handle, "ClearAll", [])
        selected = client.call(
            filter_handle, "SelectListObjectValues", ["/qListObjectDef", [elem], False]
        )
        if selected.get("qSuccess") is not True:
            raise ValueError("Election selection failed")
        current = election_rows(client, filter_handle)
        if [(d, e) for d, e, state in current if state in ("S", "L")] != [(day, elem)]:
            raise ValueError("Election selection mismatch")
        selections = client.call(selections_handle, "GetLayout", [])["qLayout"][
            "qSelectionObject"
        ]["qSelections"]
        if (
            len(selections) != 1
            or selections[0]["qField"] != "Election Date"
            or selections[0]["qSelectedCount"] != 1
        ):
            raise ValueError("Unexpected additional dashboard selections")
        for key in TABLES:
            data, missing = table(client, app_handle, key)
            results.append((day, data, missing))
        if election_rows(client, filter_handle) != current:
            raise ValueError("Election filter changed during capture")
        if (
            client.call(selections_handle, "GetLayout", [])["qLayout"][
                "qSelectionObject"
            ]["qSelections"]
            != selections
        ):
            raise ValueError("Dashboard selections changed during capture")
    after = client.call(app_handle, "GetAppLayout", [])["qLayout"]
    if before["qLastReloadTime"] != after["qLastReloadTime"]:
        raise ValueError("Application reloaded during capture")
    return results


class SavedClient:
    def __init__(self, messages):
        self.messages = iter(messages)

    def call(self, handle, method, params):
        try:
            item = next(self.messages)
        except StopIteration:
            raise ValueError("Incomplete Qlik transcript") from None
        request = json.loads(item["request"])
        if (request["handle"], request["method"], request["params"]) != (
            handle,
            method,
            params,
        ):
            raise ValueError("Qlik transcript request mismatch")
        response = json.loads(item["response"])
        if response.get("id") != request["id"] or "error" in response:
            raise ValueError("Failed Qlik response")
        return response["result"]

    def finish(self):
        if next(self.messages, None) is not None:
            raise ValueError("Unexpected trailing Qlik messages")


class GeorgiaTurnout:
    manifest = Manifest(
        name="georgia-sos-turnout",
        version="1.0.0",
        capabilities=("turnout", "aggregate-tables"),
    )

    def __init__(self, config):
        if config.url != HUB or config.allowed_hosts != (
            "d11khjvhu86vzz.cloudfront.net",
            TENANT,
        ):
            raise ValueError("Turnout requires the official public dashboard hosts")
        self.config = config

    def discover(self, content, url, sha):
        data = json.loads(content)
        if url != HUB or data["schema_version"] != 1 or data["app_id"] != APP:
            raise ValueError("Unexpected turnout snapshot")
        return [Reference(url=url, page_url=url, listing_hash=sha)], []

    def uses_listing_content(self, ref):
        return ref.url == ref.page_url == HUB

    def parse(self, content, ref, sha, window):
        data = json.loads(content)
        if (
            not self.uses_listing_content(ref)
            or sha != ref.listing_hash
            or data["schema_version"] != 1
            or data["app_id"] != APP
            or data["window"] != window.model_dump(mode="json")
        ):
            raise ValueError("Turnout snapshot identity mismatch")
        client = SavedClient(data["messages"])
        tables = read_tables(client, window)
        client.finish()
        records = []
        for day, table_data, missing in tables:
            uid = f"ga:sos:turnout:{day.isoformat()}:{table_data['table']}"
            warnings = (
                "Unofficial turnout; county voter-credit uploads and source corrections may change these aggregates.",
                "Source tables can suppress missing or zero rows; absence is not zero or proof of complete county coverage.",
            ) + (
                (
                    "Source contains unavailable measures, retained as null rather than zero.",
                )
                if missing
                else ()
            )
            records.append(
                Envelope(
                    event=TurnoutRecord(
                        source_id=self.config.id,
                        uid=uid,
                        title=f"Georgia {day.isoformat()} — {table_data['table']}",
                        source_url=SOURCE,
                        election_date=day,
                        data=table_data,
                        warnings=warnings,
                    ),
                    evidence_hashes=(sha,),
                    parser_version=self.manifest.version,
                    locator=uid,
                )
            )
        return records
