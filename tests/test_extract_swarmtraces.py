import gzip
import json
from pathlib import Path

import polars as pl
import pytest

from agent_swarm.extract import swarmtraces
from agent_swarm.paths import RAW_DIR
from agent_swarm.schema import make_event_id, validate_events

ART = "swarmtraces/redacted.jsonl.gz"


def rec(rid, kind, text, parent=None, tags=""):
    return {
        "id": rid,
        "cite": f"{rid}:abcd1234",
        "kind": kind,
        "parent_id": parent,
        "time_utc": None,
        "tags": tags,
        "text": text,
    }


ROWS = [
    rec(
        "R1",
        "payload",
        'fetch("https://[SERVICE HOST 1]",{body:\'{"agent_id":"[REDACTED:runtime_identifier]"}\'})',
    ),
    rec(
        "R2",
        "recovered_text",
        "import os # scan 2026-07-10 and 2011-06-15, again 2026-07-10, at 2026-04-25T10:11:12Z",
        parent="R1",
    ),
    rec(
        "R3",
        "recovered_text",
        "LOOT = [REDACTED:secret_key]",
        parent="R2",
        tags="article-evidence;slack-search;slackd3.gz",
    ),
    rec("R4", "response", "document.body.innerText='TINYOK'", parent="R1"),
    rec("R5", "payload", '{"agent_id": "agent-7", "cmd": "whoami"}'),
    rec("R6", "recovered_text", "orphan root layer"),
    rec("R7", "recovered_text", "cycle a", parent="R8"),
    rec("R8", "recovered_text", "cycle b", parent="R7"),
]


@pytest.fixture
def raw(tmp_path: Path) -> Path:
    d = tmp_path / "raw" / "swarmtraces"
    d.mkdir(parents=True)
    (d / "redacted.jsonl.gz").write_bytes(
        gzip.compress("".join(json.dumps(r) + "\n" for r in ROWS).encode())
    )
    return tmp_path / "raw"


@pytest.fixture
def events(raw: Path, tmp_path: Path) -> pl.DataFrame:
    return swarmtraces.extract(raw, tmp_path / "interim", tmp_path / "processed")


def row(events, rid):
    rows = events.filter(pl.col("native_id") == rid).to_dicts()
    assert len(rows) == 1
    return rows[0] | {"x": json.loads(rows[0]["extra"] or "{}")}


def test_one_event_per_row_with_kind_mapping(events):
    validate_events(events)
    assert events.height == len(ROWS)
    assert dict(events.group_by("event_type").len().iter_rows()) == {
        "chain_payload": 2,
        "chain_decoded": 5,
        "chain_response": 1,
    }
    assert set(events["incident_id"]) == {"hf-hack"}


def test_no_timestamps_are_invented(events):
    assert events["ts_utc"].null_count() == events.height
    assert set(events["ts_precision"]) == {"none"}


def test_parent_links_use_canonical_ids(events):
    assert row(events, "R3")["parent_event_id"] == make_event_id("swarmtraces", ART, "R2")
    assert row(events, "R1")["parent_event_id"] is None


def test_tree_root_and_depth(events):
    r3 = row(events, "R3")
    assert (r3["x"]["root_id"], r3["x"]["depth"]) == ("R1", 2)
    assert (row(events, "R1")["x"]["root_id"], row(events, "R1")["x"]["depth"]) == ("R1", 0)
    assert row(events, "R6")["x"]["depth"] == 0


def test_cycles_terminate_and_are_flagged(events):
    for rid in ("R7", "R8"):
        x = row(events, rid)["x"]
        assert x["cycle"] is True
        assert "root_id" not in x and "depth" not in x


def test_ts_hints_are_extra_only_and_in_window(events):
    r2 = row(events, "R2")
    assert r2["x"]["ts_hints"] == ["2026-04-25", "2026-07-10"]
    assert r2["ts_utc"] is None


def test_tags_split_and_redactions_typed(events):
    r3 = row(events, "R3")
    assert r3["x"]["tags"] == ["article-evidence", "slack-search", "slackd3.gz"]
    assert r3["redaction_types"] == ["secret_key"]
    assert row(events, "R1")["redaction_types"] == ["runtime_identifier", "service"]


def test_agent_id_handle_only_when_not_redacted(events):
    assert row(events, "R1")["actor_handle"] is None
    assert row(events, "R5")["actor_handle"] == "agent-7"


def test_roles_and_confidence(events):
    assert row(events, "R4")["actor_role"] == "unknown"
    assert row(events, "R1")["actor_role"] == "agent"
    assert set(events["confidence"]) == {"high"}
    assert set(events["source_confidence"]) == {"vendor_confirmed_match"}


def test_writes_interim_and_processed(events, tmp_path):
    tree = pl.read_parquet(tmp_path / "interim" / "swarmtraces" / "tree.parquet")
    assert tree.columns == ["id", "kind", "parent_id", "root_id", "depth", "cycle"]
    assert tree.height == len(ROWS)
    assert pl.read_parquet(tmp_path / "processed" / "events_swarmtraces.parquet").equals(events)


@pytest.mark.realdata
def test_real_data(tmp_path):
    ev = swarmtraces.extract(RAW_DIR, tmp_path / "interim", tmp_path / "processed")
    validate_events(ev)
    assert ev.height == 189_579
    assert dict(ev.group_by("event_type").len().iter_rows()) == {
        "chain_payload": 91_037,
        "chain_decoded": 75_534,
        "chain_response": 23_008,
    }
    ids = set(ev["event_id"])
    assert set(ev["parent_event_id"].drop_nulls()) <= ids
    assert ev["ts_utc"].null_count() == ev.height
