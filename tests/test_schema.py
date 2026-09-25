from datetime import UTC, datetime

import polars as pl
import pytest

from agent_swarm.schema import (
    EVENTS_SCHEMA,
    SchemaError,
    conform,
    empty_events,
    make_event_id,
    validate_events,
)


def event(**overrides) -> dict:
    row = {
        "event_id": make_event_id("collusion-wiki", "collusion-wiki/revisions.jsonl.gz", "dse~X@1"),
        "source_id": "collusion-wiki",
        "incident_id": "wiki-collusion",
        "artifact": "collusion-wiki/revisions.jsonl.gz",
        "native_id": "dse~X@1",
        "event_type": "wiki_save",
        "ts_utc": datetime(2026, 6, 1, 12, 0, tzinfo=UTC),
        "ts_precision": "second",
        "confidence": "medium",
        "actor_role": "agent",
    }
    return row | overrides


def frame(*rows: dict) -> pl.DataFrame:
    return conform(pl.DataFrame(list(rows)))


def test_event_id_is_stable_and_provenance_sensitive():
    a = make_event_id("collusion-wiki", "collusion-wiki/revisions.jsonl.gz", "dse~X@1")
    assert a == make_event_id("collusion-wiki", "collusion-wiki/revisions.jsonl.gz", "dse~X@1")
    assert a != make_event_id("collusion-wiki", "collusion-wiki/events.jsonl.gz", "dse~X@1")
    assert a != make_event_id("collusion-wiki", "collusion-wiki/revisions.jsonl.gz", "dse~X@2")
    assert len(a) == 16


def test_event_id_fields_cannot_collide_by_concatenation():
    assert make_event_id("a", "bc", "d") != make_event_id("ab", "c", "d")


def test_empty_events_has_canonical_schema():
    df = empty_events()
    assert df.height == 0
    assert dict(df.schema) == EVENTS_SCHEMA


def test_conform_fills_missing_columns_and_orders_them():
    df = frame(event())
    assert list(df.columns) == list(EVENTS_SCHEMA)
    assert dict(df.schema) == EVENTS_SCHEMA
    assert df["text"].to_list() == [None]


def test_conform_rejects_unknown_columns():
    with pytest.raises(SchemaError, match="bogus"):
        conform(pl.DataFrame([event(bogus=1)]))


def test_valid_frame_passes():
    validate_events(frame(event(), event(event_id="0" * 16, native_id="dse~X@2")))


def test_validate_rejects_timestamp_with_precision_none():
    with pytest.raises(SchemaError, match="ts_precision"):
        validate_events(frame(event(ts_precision="none")))


def test_validate_rejects_missing_timestamp_with_precision_second():
    with pytest.raises(SchemaError, match="ts_precision"):
        validate_events(frame(event(ts_utc=None)))


def test_validate_accepts_undated_rows():
    validate_events(frame(event(ts_utc=None, ts_precision="none")))


def test_validate_rejects_unknown_enum_values():
    with pytest.raises(SchemaError) as e:
        validate_events(frame(event(event_type="tweet", confidence="certain", actor_role="bot")))
    assert "event_type" in str(e.value)
    assert "confidence" in str(e.value)
    assert "actor_role" in str(e.value)


def test_validate_rejects_duplicate_event_ids():
    with pytest.raises(SchemaError, match="duplicate event_id"):
        validate_events(frame(event(), event(native_id="other")))


def test_validate_rejects_null_provenance():
    with pytest.raises(SchemaError, match="artifact"):
        validate_events(frame(event(artifact=None)))
