import gzip
import json
from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import pytest

from agent_swarm.extract import collusion_wiki
from agent_swarm.paths import RAW_DIR
from agent_swarm.schema import make_event_id, validate_events

ART = "collusion-wiki/revisions.jsonl.gz"


def write_jsonl_gz(path: Path, rows: list[dict]) -> None:
    path.write_bytes(gzip.compress("".join(json.dumps(r) + "\n" for r in rows).encode()))


def revision(page_id: str, seq: int, time: str, label: str, body: str, diff_base=None) -> dict:
    wiki, name = page_id.split("/", 1)
    return {
        "rev_id": f"{wiki}~{name}@{seq}",
        "page_id": page_id,
        "page_key": f"{wiki}~{name}",
        "wiki": wiki,
        "name": name,
        "seq": seq,
        "body": body,
        "body_sha256": "x",
        "label": label,
        "ip16": "52.230",
        "time": time,
        "time_grade": "reqlog",
        "change_summary": "coordination update",
        "diff_base": diff_base,
        "body_encoding": "ascii",
    }


@pytest.fixture
def raw(tmp_path: Path) -> Path:
    d = tmp_path / "raw" / "collusion-wiki"
    d.mkdir(parents=True)
    write_jsonl_gz(
        d / "revisions.jsonl.gz",
        [
            revision(
                "dse/A",
                1,
                "2026-06-17T02:10:42Z",
                "OpenAIResearcher",
                "hi https://jqp.vercel.app/x",
            ),
            revision("dse/A", 2, "2026-06-17T03:00:00Z", "", "hi again", diff_base="dse~A@1"),
            revision("dse/B", 1, "2026-06-18T00:00:00Z", "ModX", "please stop"),
            revision("dorfwiki/C", 1, "2026-06-22T08:42:57Z", "AgentZ", "Beschreibe"),
        ],
    )
    probes = [
        {
            "event_id": f"probe:attacklog_raw_dse_2605.jsonl:{i}",
            "event_type": "probe",
            "wiki": None,
            "page_key": None,
            "time": "2026-05-17T05:46:45Z",
            "request_action": "<script>alert('XSS')</script>",
            "ip16": "135.136",
            "related_event_id": None,
        }
        for i in range(120)
    ]
    saves = [
        {
            "event_id": f"save:{r}",
            "event_type": "save",
            "wiki": r.split("~")[0],
            "page_key": r.split("@")[0],
            "time": "2026-06-17T02:10:42Z",
            "time_grade": "reqlog",
            "revision_ref": r,
        }
        for r in ["dse~A@1", "dse~A@2", "dse~B@1", "dorfwiki~C@1"]
    ]
    write_jsonl_gz(
        d / "events.jsonl.gz",
        saves
        + probes
        + [
            {
                "event_id": "delete:dse:rclog:1",
                "event_type": "delete",
                "wiki": "dse",
                "page_key": "dse~A",
                "time": "2026-06-19T00:00:00Z",
                "actor_label": "[Admin1]",
                "ip16": "2.202",
                "change_summary": "Seite gelöscht.",
            },
            {
                "event_id": "revert:delete:dse:rclog:1",
                "event_type": "revert",
                "wiki": "dse",
                "page_key": "dse~A",
                "time": "2026-06-19T23:19:13Z",
                "actor_label": "OpenAIResearchHelper",
                "ip16": "52.230",
                "related_event_id": "delete:dse:rclog:1",
            },
        ],
    )
    write_jsonl_gz(
        d / "labels.jsonl.gz",
        [
            {"label": "OpenAIResearcher", "is_human_handle": False},
            {"label": "ModX", "is_human_handle": True},
        ],
    )
    write_jsonl_gz(
        d / "pages.jsonl.gz",
        [{"page_id": "dse/A", "page_key": "dse~A", "labels": ["OpenAIResearcher"]}],
    )
    write_jsonl_gz(
        d / "links.jsonl.gz", [{"url": "https://jqp.vercel.app/x", "host": "jqp.vercel.app"}]
    )
    write_jsonl_gz(
        d / "records.jsonl.gz",
        [
            {
                "id": "r1",
                "text": "hi https://jqp.vercel.app/x",
                "authorship": "not_independently_authenticated",
                "selection_basis": "task_or_exchange_signal",
                "origins": [
                    {
                        "source_id": "dse/A",
                        "site": "prowiki.org/dse",
                        "url": "https://collusion.wiki/explorer/page/dse~A.html",
                        "source_date_literal": "2026-06-17T02:10:42Z",
                        "kind": "revision_addition",
                    }
                ],
            },
            {
                "id": "r2",
                "text": "relay R5 here",
                "authorship": "not_independently_authenticated",
                "selection_basis": "linked_artifact",
                "origins": [
                    {
                        "source_id": "v1",
                        "site": "vanderbi.lt",
                        "url": "https://vanderbi.lt/a",
                        "source_date_literal": "",
                        "kind": "shortener_record",
                    },
                    {
                        "source_id": "p1",
                        "site": "anna.fyi",
                        "url": "https://anna.fyi/view/1",
                        "source_date_literal": "2026-06-01",
                        "kind": "paste_candidate",
                    },
                ],
            },
        ],
    )
    (d / "site-coverage.csv").write_text("site,host,category\nDSEWiki,prowiki.org,wikis\n")
    (d / "coverage-gaps.csv").write_text("site,host,category\nusemod.org,usemod.org,wikis\n")
    (d / "shortener-logs.json.gz").write_bytes(
        gzip.compress(
            json.dumps(
                {
                    "sites": [
                        {
                            "site": "rmn.re",
                            "links": [
                                {
                                    "keyword": "jo",
                                    "url": "https://api.beta.ons.gov.uk/v1/x",
                                    "title": "t",
                                    "time": "2026-05-26T18:01:21Z",
                                    "ip16": "20.97",
                                    "clicks": 3,
                                }
                            ],
                        }
                    ]
                }
            ).encode()
        )
    )
    (d / "other-wikis.json.gz").write_bytes(
        gzip.compress(
            json.dumps(
                {
                    "pages": [
                        {
                            "page_id": "usemod/SandBox",
                            "page_key": "usemod~SandBox",
                            "wiki": "usemod",
                            "revisions": [
                                {
                                    "seq": 1,
                                    "time": "2026-05-11T04:10:47Z",
                                    "time_grade": "write_date",
                                    "ip16": "20.230",
                                    "append": True,
                                    "added": ["a", "b"],
                                    "removed": [],
                                }
                            ],
                        }
                    ]
                }
            ).encode()
        )
    )
    return tmp_path / "raw"


@pytest.fixture
def events(raw: Path, tmp_path: Path) -> pl.DataFrame:
    return collusion_wiki.extract(raw, tmp_path / "interim", tmp_path / "processed")


def by_native(events: pl.DataFrame, native_id: str) -> dict:
    rows = events.filter(pl.col("native_id") == native_id).to_dicts()
    assert len(rows) == 1, rows
    return rows[0]


def test_output_validates(events):
    validate_events(events)
    assert set(events["source_id"]) == {"collusion-wiki"}
    assert set(events["incident_id"]) == {"wiki-collusion"}


def test_revisions_become_wiki_saves_one_to_one(events):
    saves = events.filter(pl.col("artifact") == ART)
    assert saves.height == 4
    assert set(saves["event_type"]) == {"wiki_save"}
    a1 = by_native(events, "dse~A@1")
    assert a1["ts_utc"] == datetime(2026, 6, 17, 2, 10, 42, tzinfo=UTC)
    assert a1["ts_precision"] == "second"
    assert a1["actor_handle"] == "OpenAIResearcher"
    assert a1["actor_role"] == "agent"
    assert a1["network_ip16"] == "52.230"
    assert a1["venue_host"] == "prowiki.org"
    assert a1["venue_locator"] == "dse/A"
    assert a1["urls"] == ["https://jqp.vercel.app/x"]
    assert by_native(events, "dorfwiki~C@1")["venue_host"] == "dorfwiki.org"


def test_blank_label_is_null_handle(events):
    assert by_native(events, "dse~A@2")["actor_handle"] is None


def test_diff_base_becomes_parent(events):
    assert by_native(events, "dse~A@2")["parent_event_id"] == make_event_id(
        "collusion-wiki", ART, "dse~A@1"
    )


def test_human_handle_is_moderator(events):
    assert by_native(events, "dse~B@1")["actor_role"] == "moderator"


def test_save_events_fold_into_revisions(events):
    assert events.filter(pl.col("native_id").str.starts_with("save:")).height == 0
    extra = json.loads(by_native(events, "dse~A@1")["extra"])
    assert extra["save_event"]["time_grade"] == "reqlog"
    assert extra["change_summary"] == "coordination update"


def test_delete_revert_probe(events):
    d = by_native(events, "delete:dse:rclog:1")
    assert (d["event_type"], d["actor_role"], d["actor_handle"]) == (
        "wiki_delete",
        "moderator",
        None,
    )
    assert json.loads(d["extra"])["actor_label"] == "[Admin1]"
    r = by_native(events, "revert:delete:dse:rclog:1")
    assert (r["event_type"], r["actor_role"], r["actor_handle"]) == (
        "wiki_revert",
        "agent",
        "OpenAIResearchHelper",
    )
    probes = events.filter(pl.col("event_type") == "wiki_probe")
    assert probes.height == 120
    assert set(probes["venue_host"]) == {"prowiki.org"}


def test_late_typed_related_event_id_survives(events):
    r = by_native(events, "revert:delete:dse:rclog:1")
    assert json.loads(r["extra"])["related_event_id"] == "delete:dse:rclog:1"


def test_records_emit_one_event_per_origin(events):
    posts = events.filter(
        pl.col("event_type") == "venue_post",
        pl.col("artifact") == "collusion-wiki/records.jsonl.gz",
    )
    assert posts.height == 3
    dse = by_native(events, "r1#0")
    assert dse["venue_host"] == "prowiki.org"
    assert dse["dup_of_event_id"] == make_event_id("collusion-wiki", ART, "dse~A@1")
    assert dse["source_confidence"] == "task_or_exchange_signal"
    assert dse["confidence"] == "medium"
    v = by_native(events, "r2#0")
    assert (v["venue_host"], v["ts_precision"], v["ts_utc"], v["dup_of_event_id"]) == (
        "vanderbi.lt",
        "none",
        None,
        None,
    )
    a = by_native(events, "r2#1")
    assert (a["ts_precision"], a["ts_utc"]) == ("day", datetime(2026, 6, 1, tzinfo=UTC))


def test_shortener_logs_and_other_wikis(events):
    s = by_native(events, "rmn.re/jo")
    assert (s["event_type"], s["venue_host"], s["network_ip16"]) == (
        "venue_post",
        "rmn.re",
        "20.97",
    )
    assert s["urls"] == ["https://api.beta.ons.gov.uk/v1/x"]
    w = by_native(events, "usemod~SandBox@1")
    assert (w["event_type"], w["venue_host"], w["text"]) == ("wiki_save", "usemod.org", "a\nb")


def test_writes_interim_and_processed(raw, tmp_path, events):
    interim = tmp_path / "interim" / "collusion-wiki"
    for name in [
        "revisions",
        "events",
        "labels",
        "pages",
        "records",
        "links",
        "site_coverage",
        "coverage_gaps",
    ]:
        assert (interim / f"{name}.parquet").exists(), name
    written = pl.read_parquet(tmp_path / "processed" / "events_collusion-wiki.parquet")
    assert written.equals(events)


@pytest.mark.realdata
def test_real_counts(tmp_path):
    ev = collusion_wiki.extract(RAW_DIR, tmp_path / "interim", tmp_path / "processed")
    validate_events(ev)
    counts = {(a, t): n for a, t, n in ev.group_by("artifact", "event_type").len().iter_rows()}
    assert counts[(ART, "wiki_save")] == 14_591
    assert counts[("collusion-wiki/events.jsonl.gz", "wiki_delete")] == 5_217
    assert counts[("collusion-wiki/events.jsonl.gz", "wiki_probe")] == 101
    assert counts[("collusion-wiki/events.jsonl.gz", "wiki_revert")] == 4
    assert counts[("collusion-wiki/records.jsonl.gz", "venue_post")] == 15_806
    assert counts[("collusion-wiki/shortener-logs.json.gz", "venue_post")] == 499
    saves = ev.filter(pl.col("artifact") == ART)
    assert saves["ts_utc"].min() >= datetime(2026, 5, 1, tzinfo=UTC)
    assert saves["ts_utc"].max() <= datetime(2026, 7, 31, tzinfo=UTC)
