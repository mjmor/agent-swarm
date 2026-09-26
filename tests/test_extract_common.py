import json
from datetime import UTC, datetime

import polars as pl

from agent_swarm.extract.common import finalize, json_extra, parse_when
from agent_swarm.schema import EVENTS_SCHEMA, make_event_id


def test_parse_when_formats():
    assert parse_when("2026-06-17T02:10:42Z") == (
        datetime(2026, 6, 17, 2, 10, 42, tzinfo=UTC),
        "second",
    )
    assert parse_when("2026-05-26T14:41:00+02:00") == (
        datetime(2026, 5, 26, 12, 41, tzinfo=UTC),
        "second",
    )
    assert parse_when("2026-06-01") == (datetime(2026, 6, 1, tzinfo=UTC), "day")
    assert parse_when("") == (None, "none")
    assert parse_when(None) == (None, "none")
    assert parse_when("sometime in June") == (None, "none")


def test_json_extra_drops_nulls_and_roundtrips():
    df = pl.DataFrame({"a": [1, None], "b": ["x", None], "c": [[1, 2], None]})
    out = df.select(json_extra(["a", "b", "c"]).alias("extra"))["extra"].to_list()
    assert json.loads(out[0]) == {"a": 1, "b": "x", "c": [1, 2]}
    assert out[1] is None


def test_finalize_derives_ids_hashes_urls_and_validates():
    df = pl.DataFrame(
        {
            "source_id": ["collusion-wiki"],
            "incident_id": ["wiki-collusion"],
            "artifact": ["collusion-wiki/revisions.jsonl.gz"],
            "native_id": ["dse~A@1"],
            "event_type": ["wiki_save"],
            "ts_utc": [datetime(2026, 6, 1, tzinfo=UTC)],
            "ts_precision": ["second"],
            "text": ["see https://r.jina.ai/https://x.org [REDACTED:secret_key]"],
            "source_confidence": ["publisher_selected_agent_content"],
            "confidence": ["medium"],
            "actor_role": ["agent"],
        }
    )
    out = finalize(df)
    assert dict(out.schema) == EVENTS_SCHEMA
    row = out.row(0, named=True)
    assert row["event_id"] == make_event_id(
        "collusion-wiki", "collusion-wiki/revisions.jsonl.gz", "dse~A@1"
    )
    assert row["urls"] == ["https://r.jina.ai/https://x.org"]
    assert row["redaction_types"] == ["secret_key"]
    assert len(row["text_sha256"]) == 64


def test_finalize_handles_null_text():
    df = pl.DataFrame(
        {
            "source_id": ["s"],
            "incident_id": ["wiki-collusion"],
            "artifact": ["s/a"],
            "native_id": ["1"],
            "event_type": ["wiki_probe"],
            "ts_utc": [None],
            "ts_precision": ["none"],
            "text": [None],
            "confidence": ["medium"],
            "actor_role": ["unknown"],
        },
        schema_overrides={"ts_utc": pl.Datetime("us", "UTC"), "text": pl.String},
    )
    row = finalize(df).row(0, named=True)
    assert row["urls"] == [] and row["redaction_types"] == [] and row["text_sha256"] is None


def test_finalize_accepts_frames_without_text_column():
    df = pl.DataFrame(
        {
            "source_id": ["transluce-urlquery"],
            "incident_id": ["urlquery-relay"],
            "artifact": ["t/a.zip"],
            "native_id": ["r1"],
            "event_type": ["url_scan"],
            "ts_utc": [datetime(2026, 6, 1, tzinfo=UTC)],
            "ts_precision": ["second"],
            "confidence": ["high"],
            "actor_role": ["agent"],
        }
    )
    row = finalize(df).row(0, named=True)
    assert row["text"] is None and row["urls"] == []
