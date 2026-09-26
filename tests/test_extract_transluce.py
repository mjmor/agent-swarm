import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import pytest

from agent_swarm.extract import transluce
from agent_swarm.paths import RAW_DIR
from agent_swarm.schema import validate_events

ZIP = "urlquery-agent-activity-2026-09-23.zip"
PREFIX = "urlquery-agent-activity-2026-09-22-v5/"
COLUMNS = [
    "report_id",
    "report_url",
    "report_date_utc",
    "timestamp_precision",
    "disposition",
    "confidence",
    "broad_class",
    "why_included",
    "caveat",
]


def csv(rows: list[dict], columns: list[str]) -> bytes:
    buf = io.BytesIO()
    pl.DataFrame(rows, schema=dict.fromkeys(columns, pl.String)).write_csv(buf)
    return buf.getvalue()


def report(rid: str, when: str, disposition: str, confidence: str | None, broad="source_request"):
    return {
        "report_id": rid,
        "report_url": f"https://urlquery.net/report/{rid}",
        "report_date_utc": when,
        "timestamp_precision": "second",
        "disposition": disposition,
        "confidence": confidence,
        "broad_class": broad,
        "why_included": "Related data source or exact task identifier: AIHW.",
        "caveat": "Candidate evidence, not authenticated AI attribution.",
    }


MAIN = [
    report("r1", "2026-06-20T01:02:03Z", "included", "significant", "custom_program"),
    report("r2", "2026-06-20T05:00:00Z", "included", "suggestive"),
    report("r3", "2026-06-21T00:00:00Z", "review_required", None),
]
SUPPLEMENT = [report("r4", "2026-06-21T09:00:00Z", "background", None, "indirection")]


@pytest.fixture
def raw(tmp_path: Path) -> Path:
    d = tmp_path / "raw" / "transluce-urlquery"
    d.mkdir(parents=True)
    files = {
        "reports.csv": csv(MAIN, COLUMNS),
        "additional-cited-reports.csv": csv(SUPPLEMENT, COLUMNS),
        "all-reports.csv": csv(MAIN + SUPPLEMENT, COLUMNS),
        "report-sources.csv": csv(
            [
                {
                    "report_id": "r1",
                    "report_date_utc": "2026-06-20T01:02:03Z",
                    "data_source": "AIHW",
                    "source_basis": "source_method",
                    "matched_sources": "AIHW",
                },
                {
                    "report_id": "r2",
                    "report_date_utc": "2026-06-20T05:00:00Z",
                    "data_source": "DataUSA",
                    "source_basis": "source_method",
                    "matched_sources": None,
                },
            ],
            ["report_id", "report_date_utc", "data_source", "source_basis", "matched_sources"],
        ),
        "daily-counts.csv": csv(
            [{"date_utc": "2026-06-20", "significant": "1", "suggestive": "1", "total": "2"}],
            ["date_utc", "significant", "suggestive", "total"],
        ),
        "daily-source-counts.csv": csv(
            [{"date_utc": "2026-06-20", "data_source": "AIHW", "reports": "1"}],
            ["date_utc", "data_source", "reports"],
        ),
        "selection-provenance.csv": csv(
            [{"report_id": "r4", "source": "x"}], ["report_id", "source"]
        ),
        "methods.json": json.dumps(
            [
                {
                    "id": "source:aihw",
                    "label": "AIHW",
                    "query": "http.url.domain:aihw.gov.au",
                    "markers": ["aihw.gov.au"],
                    "reason": "r",
                    "default_confidence": "suggestive",
                    "broad_class": "source_request",
                    "discovery_kind": "indexed_source_query",
                    "requires_review": False,
                },
                {
                    "id": "override:x",
                    "label": "X",
                    "reason": "r",
                    "discovery_kind": "manual",
                    "report_ids": ["r1"],
                },
            ]
        ).encode(),
        "README.md": b"# readme",
    }
    with zipfile.ZipFile(d / ZIP, "w") as z:
        for name, body in files.items():
            z.writestr(PREFIX + name, body)
    return tmp_path / "raw"


@pytest.fixture
def events(raw: Path, tmp_path: Path) -> pl.DataFrame:
    return transluce.extract(raw, tmp_path / "interim", tmp_path / "processed")


def row(events: pl.DataFrame, rid: str) -> dict:
    rows = events.filter(pl.col("native_id") == rid).to_dicts()
    assert len(rows) == 1
    return rows[0]


def test_one_url_scan_per_report(events):
    validate_events(events)
    assert events.height == 4
    assert set(events["event_type"]) == {"url_scan"}
    assert set(events["incident_id"]) == {"urlquery-relay"}
    assert set(events["venue_host"]) == {"urlquery.net"}


def test_mapping_of_an_included_report(events):
    r1 = row(events, "r1")
    assert r1["ts_utc"] == datetime(2026, 6, 20, 1, 2, 3, tzinfo=UTC)
    assert (r1["ts_precision"], r1["ts_field"]) == ("second", "report_date_utc")
    assert r1["venue_locator"] == "r1"
    assert (r1["source_confidence"], r1["confidence"], r1["actor_role"]) == (
        "significant",
        "high",
        "agent",
    )
    assert r1["text"] is None
    extra = json.loads(r1["extra"])
    assert extra["data_source"] == "AIHW"
    assert extra["broad_class"] == "custom_program"
    assert extra["report_url"] == "https://urlquery.net/report/r1"
    assert extra["catalog"] == "main"


def test_unrated_and_background_reports_are_kept_but_not_agents(events):
    r3, r4 = row(events, "r3"), row(events, "r4")
    assert (r3["source_confidence"], r3["confidence"], r3["actor_role"]) == (
        "review_required",
        "unrated",
        "unknown",
    )
    assert (r4["source_confidence"], r4["confidence"], r4["actor_role"]) == (
        "background",
        "low",
        "unknown",
    )
    assert json.loads(r4["extra"])["catalog"] == "supplement"


def test_catalog_mismatch_is_rejected(raw, tmp_path):
    zpath = raw / "transluce-urlquery" / ZIP
    with zipfile.ZipFile(zpath) as z:
        files = {n: z.read(n) for n in z.namelist()}
    files[PREFIX + "all-reports.csv"] = csv(MAIN, COLUMNS)
    with zipfile.ZipFile(zpath, "w") as z:
        for n, body in files.items():
            z.writestr(n, body)
    with pytest.raises(ValueError, match="all-reports"):
        transluce.extract(raw, tmp_path / "interim", tmp_path / "processed")


def test_row_level_counts_reproduce_published_daily_counts(events, tmp_path):
    daily = pl.read_parquet(tmp_path / "interim" / "transluce-urlquery" / "daily_counts.parquet")
    assert transluce.reconcile_daily(events, daily).height == 0


def test_methods_table_keeps_heterogeneous_detectors(events, tmp_path):
    methods = pl.read_parquet(tmp_path / "interim" / "transluce-urlquery" / "methods.parquet")
    aihw = methods.filter(pl.col("id") == "source:aihw").row(0, named=True)
    assert aihw["query"] == "http.url.domain:aihw.gov.au"
    assert aihw["markers"] == ["aihw.gov.au"]
    override = methods.filter(pl.col("id") == "override:x").row(0, named=True)
    assert json.loads(override["raw"])["report_ids"] == ["r1"]


def test_writes_interim_and_processed(events, tmp_path):
    interim = tmp_path / "interim" / "transluce-urlquery"
    for name in [
        "all_reports",
        "report_sources",
        "methods",
        "daily_counts",
        "daily_source_counts",
        "selection_provenance",
    ]:
        assert (interim / f"{name}.parquet").exists(), name
    assert pl.read_parquet(tmp_path / "processed" / "events_transluce-urlquery.parquet").equals(
        events
    )


@pytest.mark.realdata
def test_real_data(tmp_path):
    ev = transluce.extract(RAW_DIR, tmp_path / "interim", tmp_path / "processed")
    validate_events(ev)
    assert ev.height == 38_160
    assert dict(ev["confidence"].value_counts().iter_rows()) == {
        "high": 6_467,
        "medium": 31_182,
        "low": 79,
        "unrated": 432,
    }
    daily = pl.read_parquet(tmp_path / "interim" / "transluce-urlquery" / "daily_counts.parquet")
    assert transluce.reconcile_daily(ev, daily).height == 0
