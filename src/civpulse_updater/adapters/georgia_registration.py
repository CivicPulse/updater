"""Public Georgia SOS registration aggregates, never individual voter exports."""

import json
import math
from datetime import date, datetime
from zoneinfo import ZoneInfo

from ..models import (
    Envelope,
    Manifest,
    Reference,
    RegistrationRecord,
    canonical,
    digest,
)
from .georgia_turnout import ORIGIN, TENANT, SavedClient, matrix, object_handle

HUB = ORIGIN + "/DH.VOTERS/index.html"
SOURCE = "https://sos.ga.gov/election-data-hub"
LIVE_APP = "b4b55ec0-57b4-4c2b-bbcf-602397bc6aa6"
MONTHLY_APP = "2977350b-191a-4615-a761-664579f15891"
APPS = (LIVE_APP, MONTHLY_APP)
PAGE_CELLS = 10000
MAX_ROWS = 500000
MAX_DATES = 1000
MONTHLY_TABLES = {
    "monthly-demographics": (
        "CuucTJm",
        ("County", "Voter Status", "Age Group", "Gender", "Race"),
    ),
    "monthly-status": ("dSyCvqD", ("County", "Voter Status")),
}
# Pin published measures before fetching their data; inspect source changes rather
# than accepting a changed expression solely because its label still says count.
MONTHLY_MEASURES_SHA = {
    "monthly-demographics": "c5d646b744181890ec87070e0e310b4e8867bda6a56cea576ab27f6ceecff1bd",
    "monthly-status": "a1e9cb1396f217e63f2d78a88899139a9175aa294fc20fdf0a0cab021d30db9f",
}
# Fixed aggregate projections of categorical fields exposed by the public app.
# USER is counted inside Qlik and is never returned as a dimension or saved.
LIVE_TABLES = {
    "live-county-status": ("County Name", "VOTER_STATUS"),
    "live-demographics-precinct": (
        "County Name",
        "VOTER_STATUS",
        "Age Group",
        "Gender_Clean",
        "RACE_DESC",
        "Municipal Precinct",
    ),
    "live-congressional-district": (
        "County Name",
        "VOTER_STATUS",
        "Congress.DistrictNumber",
    ),
    "live-house-district": ("County Name", "VOTER_STATUS", "House.DistrictNumber"),
    "live-senate-district": ("County Name", "VOTER_STATUS", "Senate.DistrictNumber"),
}


SUMMARY_LIVE_TABLES = {
    "live-county-status": ("County Name", "VOTER_STATUS"),
    "live-race": ("County Name", "VOTER_STATUS", "RACE_DESC"),
    "live-gender": ("County Name", "VOTER_STATUS", "Gender_Clean"),
    "live-age": ("County Name", "VOTER_STATUS", "Age Group"),
    "live-precinct": ("County Name", "VOTER_STATUS", "Municipal Precinct"),
}
SUMMARY_COUNT = 'COUNT(DISTINCT {<VOTER_STATUS={"Active","Inactive"}>} USER)'
SUMMARY_MAX_ROWS = 20000


def session_object(client, app, definition):
    return client.call(app, "CreateSessionObject", [definition])["qReturn"]["qHandle"]


def list_values(client, app, field):
    handle = session_object(
        client,
        app,
        {
            "qInfo": {"qType": "registration-list"},
            "qListObjectDef": {"qDef": {"qFieldDefs": [field]}},
        },
    )
    layout = client.call(handle, "GetLayout", [])["qLayout"]["qListObject"]
    size = layout["qSize"]
    if (
        layout["qDimensionInfo"]["qGroupFieldDefs"] != [field]
        or not 0 <= size["qcy"] <= MAX_DATES
    ):
        raise ValueError("Unexpected registration date list")
    count = size["qcy"]
    if not count:
        return handle, []
    pages = client.call(
        handle,
        "GetListObjectData",
        ["/qListObjectDef", [{"qTop": 0, "qLeft": 0, "qHeight": count, "qWidth": 1}]],
    )["qDataPages"]
    rows = matrix(pages, 0, count, 1)
    values, seen = [], set()
    for row in rows:
        cell = row[0]
        day = datetime.strptime(cell["qText"], "%m/%d/%Y").date()
        elem = cell["qElemNumber"]
        if day in seen or type(elem) is not int or elem < 0:
            raise ValueError("Duplicate or invalid registration snapshot date")
        seen.add(day)
        values.append((day, elem))
    return handle, sorted(values)


def cube(client, handle, dimensions, *, expected_measures=None, max_rows=MAX_ROWS):
    before = client.call(handle, "GetLayout", [])["qLayout"]
    data = before["qHyperCube"]
    if data.get("qError") or data.get("qCalcCondMsg"):
        raise ValueError("Registration cube unavailable")
    if tuple(d["qFallbackTitle"] for d in data["qDimensionInfo"]) != dimensions:
        raise ValueError("Registration dimensions changed")
    measures = tuple(m["qFallbackTitle"] for m in data["qMeasureInfo"])
    if expected_measures is not None and measures != expected_measures:
        raise ValueError("Registration measures changed")
    width, height = data["qSize"]["qcx"], data["qSize"]["qcy"]
    if (
        type(width) is not int
        or width != len(dimensions) + len(measures)
        or not measures
        or type(height) is not int
        or not 0 <= height <= max_rows
    ):
        raise ValueError("Registration cube exceeds scope")
    rows, seen = [], set()
    for top in range(0, height, max(1, PAGE_CELLS // width)):
        count = min(PAGE_CELLS // width, height - top)
        pages = client.call(
            handle,
            "GetHyperCubeData",
            [
                "/qHyperCubeDef",
                [{"qTop": top, "qLeft": 0, "qHeight": count, "qWidth": width}],
            ],
        )["qDataPages"]
        for row in matrix(pages, top, count, width):
            dims = tuple(
                None if c.get("qIsNull") else c.get("qText")
                for c in row[: len(dimensions)]
            )
            elements = tuple(c["qElemNumber"] for c in row[: len(dimensions)])
            identity = (dims, elements)
            if identity in seen:
                raise ValueError("Duplicate registration aggregate")
            seen.add(identity)
            values = {}
            for label, cell in zip(measures, row[len(dimensions) :], strict=True):
                number = cell.get("qNum")
                if cell.get("qIsNull") or number in (None, "NaN"):
                    number = None
                elif (
                    type(number) not in (int, float)
                    or number < 0
                    or number > 2**53 - 1
                    or not math.isfinite(number)
                    or number != int(number)
                ):
                    raise ValueError("Invalid registration count")
                values[label] = {"value": number, "display": cell.get("qText")}
            rows.append(
                {
                    "dimensions": dict(zip(dimensions, dims, strict=True)),
                    "dimension_elements": dict(zip(dimensions, elements, strict=True)),
                    "measures": values,
                }
            )
    if client.call(handle, "GetLayout", [])["qLayout"] != before:
        raise ValueError("Registration cube changed during capture")
    rows.sort(key=canonical)
    return {
        "dimensions": list(dimensions),
        "measures": list(measures),
        "rows": rows,
        "source_title": before.get("title", ""),
        "source_as_of": before.get("footnote", ""),
    }


def monthly_summaries(client, app, window, all_history):
    """Group historical rows server-side once; never fetch the model table."""
    date_handle, dates = list_values(client, app, "AsOfDate")
    wanted = [
        (day, elem)
        for day, elem in dates
        if all_history or window.start <= day <= window.end
    ]
    if not wanted:
        return []
    corrections = {date(2025, 7, 15): date(2025, 7, 1)}
    available = dict(dates)
    needed = {corrections.get(day, day) for day, _ in wanted}
    if not needed.issubset(available):
        raise ValueError("Required corrected history snapshot is unavailable")
    client.call(app, "ClearAll", [True])
    selections = session_object(
        client,
        app,
        {"qInfo": {"qType": "CurrentSelections"}, "qSelectionObjectDef": {}},
    )

    def selection_state():
        selected = client.call(selections, "GetLayout", [])["qLayout"][
            "qSelectionObject"
        ]["qSelections"]
        # The public app enforces one AsOfDate selection. SUM({1} ...)
        # aggregates across all available dates without changing that UI rule.
        if selected and (
            len(selected) != 1
            or selected[0]["qField"] != "AsOfDate"
            or selected[0]["qSelectedCount"] != 1
            or not selected[0].get("qOneAndOnlyOne")
        ):
            raise ValueError("Unexpected history summary selections")
        return selected

    before_selection = selection_state()
    oid = MONTHLY_TABLES["monthly-status"][0]
    handle = object_handle(client, app, oid)
    props = client.call(handle, "GetProperties", [])["qProp"]
    source = props["qHyperCubeDef"]
    if (
        props["qInfo"]["qId"] != oid
        or source["qMode"] != "P"
        or digest(canonical({"measures": source["qMeasures"]}).encode())
        != MONTHLY_MEASURES_SHA["monthly-status"]
    ):
        raise ValueError("Historical summary source measure changed")
    dimensions = ("AsOfDate", "County", "Voter Status")
    definition = {
        "qDimensions": [
            {"qDef": {"qFieldDefs": [field]}, "qNullSuppression": field == "AsOfDate"}
            for field in dimensions
        ],
        "qMeasures": [
            {"qDef": {"qDef": "SUM({1} VoterCount)", "qLabel": "Voter Count"}}
        ],
        "qMode": "S",
        "qSuppressZero": True,
        "qSuppressMissing": True,
    }
    handle = session_object(
        client,
        app,
        {
            "qInfo": {"qType": "registration-history-summary"},
            "qHyperCubeDef": definition,
        },
    )
    summary = cube(
        client,
        handle,
        dimensions,
        expected_measures=("Voter Count",),
        max_rows=MAX_DATES * 400,
    )
    if selection_state() != before_selection:
        raise ValueError("History selection changed during capture")
    grouped = {}
    for row in summary["rows"]:
        day = datetime.strptime(row["dimensions"]["AsOfDate"], "%m/%d/%Y").date()
        if day not in available:
            raise ValueError("Unexpected historical snapshot in summary")
        grouped.setdefault(day, []).append(
            {
                "dimensions": {
                    k: v for k, v in row["dimensions"].items() if k != "AsOfDate"
                },
                "dimension_elements": {
                    k: v
                    for k, v in row["dimension_elements"].items()
                    if k != "AsOfDate"
                },
                "measures": row["measures"],
            }
        )
    if not needed.issubset(grouped):
        raise ValueError("History summaries are missing required snapshot dates")
    results = []
    for day, _ in wanted:
        source_day = corrections.get(day, day)
        results.append(
            (
                day,
                {
                    "table": "monthly-status",
                    "object_id": oid,
                    "derived": False,
                    "dimensions": ["County", "Voter Status"],
                    "measures": ["Voter Count"],
                    "rows": grouped.get(source_day, []),
                    "source_title": "County registration history summary",
                    "source_as_of": "",
                    "definition": definition,
                    "source_definition": source,
                    "source_snapshot_date": source_day.isoformat(),
                    "source_date_correction_applied": source_day != day,
                    "aggregation": "county/status/date SUM({1} VoterCount) ignores the mandatory UI date selection; published date correction applied after grouping",
                },
            )
        )
    return results


def read_registration(client, app_id, window, *, compact=False, all_history=False):
    app = client.call(-1, "OpenDoc", [app_id])["qReturn"]["qHandle"]
    before = client.call(app, "GetAppLayout", [])["qLayout"]
    if (
        before["qFileName"] != app_id
        or not before["qHasData"]
        or before.get("qStateNames")
    ):
        raise ValueError("Unexpected registration app")
    client.call(app, "ClearAll", [True] if compact else [])
    records = []
    reload_time = datetime.fromisoformat(
        before["qLastReloadTime"].replace("Z", "+00:00")
    )
    if reload_time.tzinfo is None:
        raise ValueError("Registration reload time lacks timezone")
    if app_id == LIVE_APP:
        day = reload_time.astimezone(ZoneInfo("America/New_York")).date()
        if (compact and all_history) or window.start <= day <= window.end:
            live_tables = SUMMARY_LIVE_TABLES if compact else LIVE_TABLES
            for key, fields in live_tables.items():
                definition = {
                    "qInfo": {"qType": "registration-aggregate"},
                    "qHyperCubeDef": {
                        "qDimensions": [
                            {"qDef": {"qFieldDefs": [field]}, "qNullSuppression": False}
                            for field in fields
                        ],
                        "qMeasures": [
                            {
                                "qDef": {
                                    "qDef": SUMMARY_COUNT
                                    if compact
                                    else "Count(DISTINCT USER)",
                                    "qLabel": "Voter Count",
                                }
                            }
                        ],
                        "qMode": "S",
                        "qSuppressZero": True,
                        "qSuppressMissing": True,
                    },
                }
                handle = session_object(client, app, definition)
                result = cube(
                    client,
                    handle,
                    fields,
                    expected_measures=("Voter Count",),
                    max_rows=SUMMARY_MAX_ROWS if compact else MAX_ROWS,
                )
                if compact and key == "live-precinct":
                    all_rows = result["rows"]
                    result["rows"] = [
                        row
                        for row in all_rows
                        if row["dimensions"]["Municipal Precinct"]
                        not in (None, "", "-")
                    ]
                    result["omitted_missing_precinct_rows"] = len(all_rows) - len(
                        result["rows"]
                    )
                result.update(
                    table=key, definition=definition["qHyperCubeDef"], derived=True
                )
                records.append((day, result))
    elif app_id == MONTHLY_APP and compact:
        records.extend(monthly_summaries(client, app, window, all_history))
    elif app_id == MONTHLY_APP:
        date_handle, dates = list_values(client, app, "AsOfDate")
        selections = session_object(
            client,
            app,
            {"qInfo": {"qType": "CurrentSelections"}, "qSelectionObjectDef": {}},
        )
        for day, elem in dates:
            if not all_history and not window.start <= day <= window.end:
                continue
            client.call(app, "ClearAll", [True] if compact else [])
            if (
                client.call(
                    date_handle,
                    "SelectListObjectValues",
                    ["/qListObjectDef", [elem], False],
                ).get("qSuccess")
                is not True
            ):
                raise ValueError("Registration snapshot selection failed")

            def check_selection(day=day, elem=elem):
                selected = client.call(selections, "GetLayout", [])["qLayout"][
                    "qSelectionObject"
                ]["qSelections"]
                if (
                    len(selected) != 1
                    or selected[0]["qField"] != "AsOfDate"
                    or selected[0]["qSelectedCount"] != 1
                ):
                    raise ValueError("Unexpected registration selections")
                current = client.call(
                    date_handle,
                    "GetListObjectData",
                    [
                        "/qListObjectDef",
                        [{"qTop": 0, "qLeft": 0, "qHeight": len(dates), "qWidth": 1}],
                    ],
                )["qDataPages"]
                values = matrix(current, 0, len(dates), 1)
                if [
                    (r[0]["qText"], r[0]["qElemNumber"])
                    for r in values
                    if r[0]["qState"] in ("S", "L")
                ] != [(day.strftime("%m/%d/%Y"), elem)]:
                    raise ValueError("Wrong registration snapshot selected")

            check_selection()
            monthly_tables = (
                {"monthly-status": MONTHLY_TABLES["monthly-status"]}
                if compact
                else MONTHLY_TABLES
            )
            for key, (oid, fields) in monthly_tables.items():
                handle = object_handle(client, app, oid)
                props = client.call(handle, "GetProperties", [])["qProp"]
                definition = props["qHyperCubeDef"]
                expected_fields = [[f] for f in fields]
                pivot = key == "monthly-status"
                if pivot:
                    expected_fields[-1] = ["=[Voter Status] & ' Voters'"]
                if (
                    props["qInfo"]["qId"] != oid
                    or digest(canonical({"measures": definition["qMeasures"]}).encode())
                    != MONTHLY_MEASURES_SHA[key]
                    or (
                        not pivot
                        and any(
                            d.get("qAttributeExpressions")
                            or d.get("qAttributeDimensions")
                            or d.get("qLibraryId")
                            for d in definition["qDimensions"]
                        )
                    )
                    or definition["qMode"] != ("P" if pivot else "S")
                    or [d["qDef"]["qFieldDefs"] for d in definition["qDimensions"]]
                    != expected_fields
                ):
                    raise ValueError("Registration table definition changed")
                source_definition = definition
                if pivot:
                    # Read the pivot's measures as a straight aggregate, retaining
                    # the original definition and the explicit status projection.
                    definition = {
                        "qDimensions": [
                            {"qDef": {"qFieldDefs": [f]}, "qNullSuppression": False}
                            for f in fields
                        ],
                        "qMeasures": source_definition["qMeasures"],
                        "qMode": "S",
                        "qSuppressZero": True,
                        "qSuppressMissing": True,
                    }
                    handle = session_object(
                        client,
                        app,
                        {
                            "qInfo": {"qType": "registration-pivot-projection"},
                            "qHyperCubeDef": definition,
                        },
                    )
                result = cube(
                    client, handle, fields, expected_measures=("Voter Count",)
                )
                result.update(
                    table=key,
                    object_id=oid,
                    definition=definition,
                    source_definition=source_definition,
                    projected_pivot=pivot,
                    derived=False,
                )
                records.append((day, result))
            check_selection()
    else:
        raise ValueError("Registration app outside scope")
    after = client.call(app, "GetAppLayout", [])["qLayout"]
    if before["qLastReloadTime"] != after["qLastReloadTime"]:
        raise ValueError("Registration app reloaded during capture")
    for _, result in records:
        result.update(app_id=app_id, source_reload=before["qLastReloadTime"])
        if compact:
            result["profile"] = "statistics-summary"
    return records


class GeorgiaRegistration:
    manifest = Manifest(
        name="georgia-sos-registration",
        version="1.1.0",
        capabilities=("registration", "aggregate-tables"),
    )

    def __init__(self, config):
        if config.url != HUB or config.allowed_hosts != (
            "d11khjvhu86vzz.cloudfront.net",
            TENANT,
        ):
            raise ValueError("Registration requires official public dashboard hosts")
        self.config = config

    def discover(self, content, url, sha):
        data = json.loads(content)
        if (
            url != HUB
            or data["schema_version"] not in (1, 2)
            or data["app_ids"] != list(APPS)
        ):
            raise ValueError("Unexpected registration snapshot")
        return [Reference(url=url, page_url=url, listing_hash=sha)], []

    def uses_listing_content(self, ref):
        return ref.url == ref.page_url == HUB

    def parse(self, content, ref, sha, window):
        data = json.loads(content)
        self.discover(content, ref.url, sha)
        if (
            not self.uses_listing_content(ref)
            or digest(content) != sha
            or sha != ref.listing_hash
            or data["window"] != window.model_dump(mode="json")
            or set(data["messages"]) != set(APPS)
        ):
            raise ValueError("Registration snapshot identity mismatch")
        records = []
        for app_id in APPS:
            client = SavedClient(data["messages"][app_id])
            compact = data["schema_version"] == 2
            if compact and (
                data.get("profile") != "statistics-summary"
                or type(data.get("all_history")) is not bool
            ):
                raise ValueError("Unexpected registration summary scope")
            tables = read_registration(
                client,
                app_id,
                window,
                compact=compact,
                all_history=data.get("all_history", False),
            )
            client.finish()
            for day, table in tables:
                uid = f"ga:sos:registration:{day.isoformat()}:{table['table']}"
                warnings = (
                    "Snapshot labels and reload times are source metadata, not guaranteed observation dates; tables from different apps may differ in freshness.",
                    "Missing precincts and suppressed rows are not zero; municipal precincts are not county election precincts.",
                    "Tables overlap and must not be summed together. Distinct counts across demographic or district groups may not be additive.",
                )
                if table["derived"] and compact:
                    warnings += (
                        "Server-side aggregate of distinct active/inactive voters; no individual voter IDs are returned. Demographic tables overlap and are not additive. Missing municipal precinct groups are omitted; county totals remain authoritative.",
                    )
                elif table["derived"]:
                    warnings += (
                        "Derived aggregate of distinct USER values by source categorical fields; all source statuses are retained, including cancelled/pending/rejected. Only Active and Inactive are registration totals. Date is the app reload date in America/New_York, not a historical snapshot.",
                    )
                records.append(
                    Envelope(
                        event=RegistrationRecord(
                            source_id=self.config.id,
                            uid=uid,
                            title=f"Georgia {day.isoformat()} — {table['table']}",
                            source_url=SOURCE,
                            as_of_date=day,
                            data=table,
                            warnings=warnings,
                        ),
                        evidence_hashes=(sha,),
                        parser_version=self.manifest.version,
                        locator=uid,
                    )
                )
        return records
