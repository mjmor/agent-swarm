import json
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl
import pytest

import agent_swarm
from agent_swarm import build as build_mod
from agent_swarm.extract.common import finalize
from agent_swarm.paths import INTERIM_DIR, PROCESSED_DIR
from agent_swarm.schema import SchemaError, make_event_id, validate_events


def ev(source, incident, native, event_type, ts=None, precision="none", dup_of=None):
    return {
        "source_id": source,
        "incident_id": incident,
        "artifact": f"{source}/a",
        "native_id": native,
        "event_type": event_type,
        "ts_utc": ts,
        "ts_precision": precision,
        "dup_of_event_id": dup_of,
        "confidence": "medium",
        "actor_role": "agent",
    }


def write_events(processed: Path, source: str, rows: list[dict]) -> None:
    df = finalize(pl.DataFrame(rows, schema_overrides={"ts_utc": pl.Datetime("us", "UTC")}))
    processed.mkdir(parents=True, exist_ok=True)
    df.write_parquet(processed / f"events_{source}.parquet")


@pytest.fixture
def dirs(tmp_path: Path) -> tuple[Path, Path]:
    interim, processed = tmp_path / "interim", tmp_path / "processed"
    t1 = datetime(2026, 6, 18, 10, 0, tzinfo=UTC)
    write_events(
        processed,
        "collusion-wiki",
        [
            ev("collusion-wiki", "wiki-collusion", "r1", "wiki_save", t1, "second"),
            ev("collusion-wiki", "wiki-collusion", "r2", "wiki_save", t1, "second"),
            ev(
                "collusion-wiki",
                "wiki-collusion",
                "p1",
                "venue_post",
                t1,
                "second",
                dup_of=make_event_id("collusion-wiki", "collusion-wiki/a", "r1"),
            ),
            ev(
                "collusion-wiki",
                "wiki-collusion",
                "p2",
                "venue_post",
                datetime(2026, 6, 1, tzinfo=UTC),
                "day",
            ),
            ev("collusion-wiki", "wiki-collusion", "p3", "venue_post"),
        ],
    )
    write_events(
        processed,
        "swarmtraces",
        [ev("swarmtraces", "hf-hack", f"s{i}", "chain_payload") for i in range(3)],
    )
    (interim / "rubyhack").mkdir(parents=True)
    pl.DataFrame(
        {
            "date_utc": [date(2026, 5, 12)],
            "new_package_names": [2126],
            "subsequent_versions": [60],
            "total_uploads": [2186],
        }
    ).write_parquet(interim / "rubyhack" / "uploads_per_day.parquet")
    (interim / "transluce-urlquery").mkdir(parents=True)
    pl.DataFrame(
        {"date_utc": ["2026-06-20"], "significant": ["1"], "suggestive": ["1"], "total": ["2"]}
    ).write_parquet(interim / "transluce-urlquery" / "daily_counts.parquet")
    return interim, processed


@pytest.fixture
def built(dirs):
    interim, processed = dirs
    return build_mod.build(interim, processed)


def test_union_is_valid_and_complete(dirs, built):
    events = pl.read_parquet(dirs[1] / "events.parquet")
    validate_events(events)
    assert events.height == 8
    assert built["events"] == 8


def test_duplicate_event_ids_across_sources_are_rejected(dirs):
    interim, processed = dirs
    clash = pl.read_parquet(processed / "events_swarmtraces.parquet").head(1)
    clash.write_parquet(processed / "events_zz-clash.parquet")
    with pytest.raises(SchemaError, match="duplicate event_id"):
        build_mod.build(interim, processed)


def test_incidents_table_written(dirs, built):
    incidents = pl.read_parquet(dirs[1] / "incidents.parquet")
    assert set(incidents["incident_id"]) == {
        "wiki-collusion",
        "rubygems-attack",
        "urlquery-relay",
        "hf-hack",
    }


def test_timeline_counts_dated_non_duplicate_rows_only(dirs, built):
    tl = pl.read_parquet(dirs[1] / "timeline_daily.parquet")
    rows = tl.filter(pl.col("origin") == "row_level").sort("date", "event_type")
    assert rows.select(
        "date", "incident_id", "event_type", "n_events", "ts_precision"
    ).to_dicts() == [
        {
            "date": date(2026, 6, 1),
            "incident_id": "wiki-collusion",
            "event_type": "venue_post",
            "n_events": 1,
            "ts_precision": "day",
        },
        {
            "date": date(2026, 6, 18),
            "incident_id": "wiki-collusion",
            "event_type": "wiki_save",
            "n_events": 2,
            "ts_precision": "second",
        },
    ]


def test_published_aggregates_sit_beside_row_counts(dirs, built):
    tl = pl.read_parquet(dirs[1] / "timeline_daily.parquet")
    agg = tl.filter(pl.col("origin") == "published_aggregate").sort("incident_id")
    assert agg.select(
        "date", "incident_id", "event_type", "n_events", "ts_precision"
    ).to_dicts() == [
        {
            "date": date(2026, 5, 12),
            "incident_id": "rubygems-attack",
            "event_type": "package_published",
            "n_events": 2186,
            "ts_precision": "day",
        },
        {
            "date": date(2026, 6, 20),
            "incident_id": "urlquery-relay",
            "event_type": "url_scan",
            "n_events": 2,
            "ts_precision": "day",
        },
    ]


def test_data_quality_reports_undated_and_dup_linked(dirs, built):
    dq = json.loads((dirs[1] / "data_quality.json").read_text())
    wiki = dq["sources"]["collusion-wiki"]
    assert wiki["rows"] == 5
    assert wiki["undated"] == 1
    assert wiki["dup_linked"] == 1
    assert wiki["ts_precision"] == {"second": 3, "day": 1, "none": 1}
    assert dq["sources"]["swarmtraces"]["undated"] == 3
    assert wiki["null_rate"]["text"] == 1.0
    assert dq["totals"]["events"] == 8


def test_build_cli_uses_processed_dir(monkeypatch):
    calls = []
    monkeypatch.setattr(build_mod, "build", lambda i, p: calls.append((i, p)) or {"events": 0})
    agent_swarm.main(["build"])
    assert calls == [(INTERIM_DIR, PROCESSED_DIR)]


@pytest.mark.realdata
def test_real_build(tmp_path):
    from agent_swarm.extract import EXTRACTORS
    from agent_swarm.paths import RAW_DIR

    interim, processed = tmp_path / "interim", tmp_path / "processed"
    for extract in EXTRACTORS.values():
        extract(RAW_DIR, interim, processed)
    stats = build_mod.build(interim, processed)
    assert stats["events"] == 36_308 + 38_160 + 189_579 + 286
    tl = pl.read_parquet(processed / "timeline_daily.parquet")
    published = tl.filter(pl.col("origin") == "published_aggregate")
    assert published.filter(pl.col("incident_id") == "rubygems-attack")["n_events"].sum() == 2_654
    assert published.filter(pl.col("incident_id") == "urlquery-relay")["n_events"].sum() == 37_638
    dq = json.loads((processed / "data_quality.json").read_text())
    assert dq["sources"]["swarmtraces"]["undated"] == 189_579
