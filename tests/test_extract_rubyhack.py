import json
from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import pytest

from agent_swarm.extract import rubyhack
from agent_swarm.paths import RAW_DIR
from agent_swarm.schema import validate_events

REPORT = """<html><body>
<p>Hundreds of the packages contain oai. <a href="https://rubygems.org/gems/lambyard17/versions/0.0.1">x</a>
<a href="https://my.diffend.io/gems/oaibx0092307/0.0.1">diff</a>
<a href="https://my.diffend.io/gems/southnewsprobe1778550995/0.0.1/0.0.2">diff</a></p>
<figure class="ex" id="ev-2"><pre class="ex-body"><span class="ex-focus">oaitest1778473828
oaibx0092307</span><span class="cut ex-cut"> […]</span><span class="ex-ctx">
chatoaifetch177855288717
zz-oai-test12</span></pre></figure>
<figure class="ex" id="ev-3"><pre class="ex-body">lambcal434a1 0.0.1  —  author: oai
lambprobe4340 0.0.2  —  author: oai
lambQ4346 0.0.1  —  author: oai</pre></figure>
<figure class="ex" id="ev"><pre class="ex-body"># disable evil in next version
File.write('.yardopts',"README.md")
system("gem build x.gemspec")</pre></figure>
<pre>KEY='rubygems_9feada919'</pre>
<script>var notAGem = "https://rubygems.org/gems/scriptgem/versions/1";</script>
</body></html>"""

CSV = (
    "date_utc,new_package_names,subsequent_versions,total_uploads\n"
    "2026-05-11,283,11,294\n"
    "2026-05-12,2126,60,2186\n"
)


@pytest.fixture
def raw(tmp_path: Path) -> Path:
    d = tmp_path / "raw" / "rubyhack"
    d.mkdir(parents=True)
    (d / "report.html").write_text(REPORT, encoding="utf-8")
    (d / "uploads-per-day.csv").write_text(CSV)
    return tmp_path / "raw"


@pytest.fixture
def events(raw: Path, tmp_path: Path) -> pl.DataFrame:
    return rubyhack.extract(raw, tmp_path / "interim", tmp_path / "processed")


def row(events, name):
    rows = events.filter(pl.col("venue_locator") == name).to_dicts()
    assert len(rows) == 1, name
    return rows[0] | {"x": json.loads(rows[0]["extra"] or "{}")}


def test_one_event_per_unique_gem_name(events):
    validate_events(events)
    assert sorted(events["venue_locator"]) == sorted(
        [
            "oaitest1778473828",
            "oaibx0092307",
            "chatoaifetch177855288717",
            "zz-oai-test12",
            "lambcal434a1",
            "lambprobe4340",
            "lambQ4346",
            "lambyard17",
            "southnewsprobe1778550995",
        ]
    )
    assert set(events["event_type"]) == {"package_published"}
    assert set(events["venue_host"]) == {"rubygems.org"}
    assert set(events["incident_id"]) == {"rubygems-attack"}


def test_code_blocks_and_scripts_are_not_gem_lists(events):
    names = set(events["venue_locator"])
    assert not names & {"system", "scriptgem", "file.write"}


def test_evidence_is_merged_across_mentions(events):
    assert row(events, "oaibx0092307")["x"]["evidence"] == ["diffend_link", "ev-2"]


def test_author_and_version_lines(events):
    lp = row(events, "lambprobe4340")
    assert lp["actor_handle"] == "oai"
    assert lp["x"]["versions"] == ["0.0.2"]
    assert row(events, "lambyard17")["x"]["versions"] == ["0.0.1"]


def test_unix_timestamp_in_name_becomes_second_precision_time(events):
    r = row(events, "oaitest1778473828")
    assert r["ts_utc"] == datetime.fromtimestamp(1778473828, UTC)
    assert (r["ts_precision"], r["ts_field"]) == ("second", "name_unix_ts")
    assert r["x"]["name_ts"] == 1778473828


def test_ambiguous_or_missing_timestamps_stay_undated(events):
    for name in ("chatoaifetch177855288717", "zz-oai-test12", "lambcal434a1"):
        r = row(events, name)
        assert (r["ts_utc"], r["ts_precision"]) == (None, "none"), name


def test_confidence_and_role(events):
    assert set(events["confidence"]) == {"medium"}
    assert set(events["source_confidence"]) == {"publisher_listed"}
    assert set(events["actor_role"]) == {"agent"}


def test_writes_interim_and_processed(events, tmp_path):
    interim = tmp_path / "interim" / "rubyhack"
    uploads = pl.read_parquet(interim / "uploads_per_day.parquet")
    assert uploads["total_uploads"].sum() == 294 + 2186
    assert uploads.schema["date_utc"] == pl.Date
    assert pl.read_parquet(interim / "gems.parquet").height == events.height
    assert pl.read_parquet(tmp_path / "processed" / "events_rubyhack.parquet").equals(events)


def test_name_timestamp_rules():
    assert rubyhack.name_timestamp("injecthack1778550335") == 1778550335
    assert rubyhack.name_timestamp("x1778550335123") == 1778550335
    assert rubyhack.name_timestamp("chatoaifetch177855288717") is None
    assert rubyhack.name_timestamp("oaibx0092307") is None
    assert rubyhack.name_timestamp("gem1600000000") is None


@pytest.mark.realdata
def test_real_data(tmp_path):
    ev = rubyhack.extract(RAW_DIR, tmp_path / "interim", tmp_path / "processed")
    validate_events(ev)
    assert 250 <= ev.height <= 320
    assert ev.filter(pl.col("actor_handle") == "oai").height == 15
    uploads = pl.read_parquet(tmp_path / "interim" / "rubyhack" / "uploads_per_day.parquet")
    assert uploads["total_uploads"].sum() == 2_654
    dated = ev.filter(pl.col("ts_precision") == "second")
    assert dated["ts_utc"].min() >= datetime(2026, 5, 1, tzinfo=UTC)
    assert dated["ts_utc"].max() <= datetime(2026, 7, 1, tzinfo=UTC)
