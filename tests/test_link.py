from datetime import UTC, date, datetime

import polars as pl
import pytest

from agent_swarm import link
from agent_swarm.indicators import INDICATOR_SCHEMA


@pytest.fixture
def events() -> pl.DataFrame:
    rows = [
        ("w1", "wiki-collusion", datetime(2026, 6, 17, tzinfo=UTC), None, "agent", "medium", None),
        ("w2", "wiki-collusion", datetime(2026, 6, 20, tzinfo=UTC), None, "agent", "medium", None),
        ("u1", "urlquery-relay", datetime(2026, 6, 16, tzinfo=UTC), None, "agent", "high", None),
        ("u2", "urlquery-relay", datetime(2026, 5, 1, tzinfo=UTC), None, "agent", "medium", None),
        (
            "u3",
            "urlquery-relay",
            datetime(2026, 5, 2, tzinfo=UTC),
            "urlquery.net",
            "agent",
            "high",
            None,
        ),
        ("r1", "rubygems-attack", None, None, "agent", "medium", None),
        ("h1", "hf-hack", None, "[shortener]", "agent", "high", None),
        (
            "m1",
            "wiki-collusion",
            datetime(2026, 6, 25, tzinfo=UTC),
            None,
            "moderator",
            "medium",
            None,
        ),
        ("b1", "urlquery-relay", datetime(2024, 5, 2, tzinfo=UTC), None, "unknown", "low", None),
        ("d1", "wiki-collusion", datetime(2026, 6, 1, tzinfo=UTC), None, "agent", "medium", "w1"),
    ]
    return pl.DataFrame(
        rows,
        schema={
            "event_id": pl.String,
            "incident_id": pl.String,
            "ts_utc": pl.Datetime("us", "UTC"),
            "venue_host": pl.String,
            "actor_role": pl.String,
            "confidence": pl.String,
            "dup_of_event_id": pl.String,
        },
        orient="row",
    )


def ind(event_id, incident, kind, norm, extractor="x"):
    return (event_id, incident, kind, norm, norm, extractor)


@pytest.fixture
def indicators() -> pl.DataFrame:
    rows = [
        ind("w1", "wiki-collusion", "linked_host", "viz.aihw.gov.au"),
        ind("w2", "wiki-collusion", "relay_service", "aihw.gov.au"),
        ind("u1", "urlquery-relay", "task_target_domain", "aihw.gov.au"),
        ind("u2", "urlquery-relay", "task_target_domain", "unctad.org"),
        ind("w1", "wiki-collusion", "linked_host", "google.com"),
        ind("h1", "hf-hack", "linked_host", "google.com"),
        ind("w1", "wiki-collusion", "gem_name", "adep97117"),
        ind("r1", "rubygems-attack", "gem_name", "adep97117"),
        ind("w1", "wiki-collusion", "naming_pattern", "oai·{w}·{unixts}"),
        ind("r1", "rubygems-attack", "naming_pattern", "oai·{w}·{unixts}"),
        ind("w1", "wiki-collusion", "naming_pattern", "{w}·{6d}"),
        ind("r1", "rubygems-attack", "naming_pattern", "{w}·{6d}"),
        ind("w1", "wiki-collusion", "email", "a@b.com"),
        ind("r1", "rubygems-attack", "email", "a@b.com"),
        ind("h1", "hf-hack", "redacted_placeholder", "shortener_code"),
        ind("w1", "wiki-collusion", "redacted_placeholder", "shortener_code"),
        ind("w1", "wiki-collusion", "oai_token", "oai"),
        ind("r1", "rubygems-attack", "oai_token", "oai"),
        ind("w1", "wiki-collusion", "url_scanner", "urlquery.net"),
        ind("m1", "wiki-collusion", "linked_host", "zzz.org"),
        ind("b1", "urlquery-relay", "task_target_domain", "zzz.org"),
        ind("d1", "wiki-collusion", "linked_host", "yyy.org"),
        ind("u2", "urlquery-relay", "task_target_domain", "yyy.org"),
    ]
    return pl.DataFrame(rows, schema=INDICATOR_SCHEMA, orient="row")


@pytest.fixture
def keys(indicators, events) -> pl.DataFrame:
    return link.link_keys(indicators, events)


def test_host_indicators_merge_on_registrable_domain(keys):
    aihw = keys.filter(pl.col("key_type") == "domain", pl.col("key") == "aihw.gov.au").sort(
        "incident_id"
    )
    assert aihw["incident_id"].to_list() == ["urlquery-relay", "wiki-collusion"]
    wiki = aihw.filter(pl.col("incident_id") == "wiki-collusion").row(0, named=True)
    assert wiki["events"] == 2
    assert sorted(wiki["roles"]) == ["linked_host", "relay_service"]
    assert (wiki["first_seen"], wiki["last_seen"]) == (date(2026, 6, 17), date(2026, 6, 20))


def test_excluded_kinds_and_generic_domains(keys):
    assert not keys.filter(pl.col("key_type").is_in(["email", "redacted_placeholder"])).height
    assert "google.com" not in set(keys["key"])


def test_only_informative_name_shapes_are_keys(keys):
    shapes = set(keys.filter(pl.col("key_type") == "name_shape")["key"])
    assert shapes == {"oai·{w}·{unixts}"}


def test_undated_events_leave_seen_dates_null(keys):
    gem = keys.filter(pl.col("key") == "adep97117", pl.col("incident_id") == "rubygems-attack").row(
        0, named=True
    )
    assert gem["first_seen"] is None and gem["events"] == 1


def test_bridges_need_two_incidents(keys):
    bridges = link.bridges(keys)
    got = {(r["key_type"], r["key"]): r["incidents"] for r in bridges.to_dicts()}
    assert got == {
        ("domain", "aihw.gov.au"): ["urlquery-relay", "wiki-collusion"],
        ("gem", "adep97117"): ["rubygems-attack", "wiki-collusion"],
        ("name_shape", "oai·{w}·{unixts}"): ["rubygems-attack", "wiki-collusion"],
        ("oai_token", "oai"): ["rubygems-attack", "wiki-collusion"],
        ("domain", "urlquery.net"): ["urlquery-relay", "wiki-collusion"],
    }


def test_bridge_first_seen_order(keys):
    aihw = link.bridges(keys).filter(pl.col("key") == "aihw.gov.au").row(0, named=True)
    assert aihw["first_incident"] == "urlquery-relay"


def test_pairwise_overlap(keys):
    ov = link.incident_overlap(keys)
    row = ov.filter(
        pl.col("key_type") == "domain",
        pl.col("incident_a") == "urlquery-relay",
        pl.col("incident_b") == "wiki-collusion",
    ).row(0, named=True)
    assert (row["shared"], row["keys_a"], row["keys_b"]) == (2, 4, 2)
    assert row["jaccard"] == pytest.approx(0.5)
    assert ov.filter(pl.col("incident_a") >= pl.col("incident_b")).height == 0


def test_outputs_are_deterministic(indicators, events):
    a = link.link_keys(indicators, events)
    b = link.link_keys(indicators.reverse(), events.reverse())
    assert a.equals(b)


def test_build_links_writes_tables(tmp_path, indicators, events, monkeypatch):
    processed = tmp_path / "processed"
    processed.mkdir()
    indicators.write_parquet(processed / "indicators.parquet")
    events.write_parquet(processed / "events.parquet")
    stats = link.build_links(processed)
    for name in ("link_keys", "bridges", "incident_overlap"):
        assert (processed / "links" / f"{name}.parquet").exists()
    assert stats["bridges"] == 5

    import agent_swarm
    from agent_swarm.paths import PROCESSED_DIR

    calls = []
    monkeypatch.setattr(link, "build_links", lambda p: calls.append(p) or {})
    agent_swarm.main(["link"])
    assert calls == [PROCESSED_DIR]


def test_event_venues_are_domain_keys(keys):
    venue = keys.filter(pl.col("key") == "urlquery.net", pl.col("incident_id") == "urlquery-relay")
    assert venue.row(0, named=True)["roles"] == ["venue"]
    assert "[shortener]" not in set(keys["key"])


def test_moderator_low_confidence_and_duplicate_rows_do_not_link(keys):
    assert {"zzz.org", "yyy.org"} & set(
        keys.filter(pl.col("incident_id") == "wiki-collusion")["key"]
    ) == set()
    assert "zzz.org" not in set(keys["key"])
