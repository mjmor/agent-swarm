import json
from datetime import UTC, datetime

import polars as pl
import pytest

from agent_swarm import indicators as ind
from agent_swarm.extract.common import finalize

CATALOG = {
    "r.jina.ai": "relay_service",
    "jqp.vercel.app": "relay_service",
    "is.gd": "url_shortener",
    "anna.fyi": "pastebin",
    "urlquery.net": "url_scanner",
    "prowiki.org": "agent_venue",
}
METHODS = pl.DataFrame({"label": ["AIHW"], "markers": [["aihw.gov.au"]]})


def event(native, source="collusion-wiki", incident="wiki-collusion", **kw):
    row = {
        "source_id": source,
        "incident_id": incident,
        "artifact": f"{source}/a",
        "native_id": native,
        "event_type": kw.pop("event_type", "wiki_save"),
        "ts_utc": datetime(2026, 6, 1, tzinfo=UTC),
        "ts_precision": "second",
        "confidence": "medium",
        "actor_role": "agent",
    }
    return row | kw


@pytest.fixture
def events() -> pl.DataFrame:
    rows = [
        event("nested", text="see https://r.jina.ai/https://api.datausa.io/tesseract?x=1"),
        event(
            "unknown-relay",
            text="via https://foo.example.dev/?url=https%3A%2F%2Fsec.gov%2Ffiles%2Fcounty.json",
        ),
        event("withheld", text="[operational URL omitted; host=jqp.vercel.app; sha256=abc]"),
        event("junk", text="broken http://...html link"),
        event("shortener", text="posted at https://is.gd/abc and https://docs.google.com/x"),
        event(
            "h1", actor_handle="ＯｐｅｎＡＩResearcher", venue_locator="dse/OpenAIResearcherJuly"
        ),
        event("h2", actor_handle="OpenAIResearcher", network_ip16="20.165"),
        event("gemlink", text="uploaded https://rubygems.org/gems/lambyard17/versions/0.0.1"),
        event("mail", text="contact Some.Agent@Gmail.com please"),
        event(
            "gem",
            source="rubyhack",
            incident="rubygems-attack",
            event_type="package_published",
            venue_locator="oaitest1778473828",
            actor_handle="oai",
        ),
        event(
            "chain",
            source="swarmtraces",
            incident="hf-hack",
            event_type="chain_payload",
            ts_utc=None,
            ts_precision="none",
            text="fetch('https://[SERVICE HOST 3]/x') then [SHORTENER CODE 12]",
        ),
        event(
            "scan",
            source="transluce-urlquery",
            incident="urlquery-relay",
            event_type="url_scan",
            venue_host="urlquery.net",
            extra=json.dumps({"data_source": "AIHW"}),
        ),
    ]
    return finalize(pl.DataFrame(rows, schema_overrides={"ts_utc": pl.Datetime("us", "UTC")}))


@pytest.fixture
def found(events) -> pl.DataFrame:
    return ind.extract_indicators(events, CATALOG, METHODS)


def of(found, native_prefix, events):
    ids = events.filter(pl.col("native_id") == native_prefix)["event_id"].to_list()
    return {
        (r["indicator_type"], r["value_norm"], r["extractor"])
        for r in found.filter(pl.col("event_id").is_in(ids)).to_dicts()
    }


def test_schema_and_referential_integrity(found, events):
    assert found.columns == list(ind.INDICATOR_SCHEMA)
    assert set(found["event_id"]) <= set(events["event_id"])
    assert (
        found.join(events.select("event_id", "incident_id"), on="event_id")["incident_id"].to_list()
        == found["incident_id"].to_list()
    )


def test_nested_relay_yields_relay_and_target(found, events):
    assert of(found, "nested", events) == {
        ("relay_service", "r.jina.ai", "url.catalog"),
        ("task_target_domain", "api.datausa.io", "url.nested_target"),
    }


def test_unknown_relay_detected_from_nesting(found, events):
    assert of(found, "unknown-relay", events) == {
        ("relay_service", "foo.example.dev", "url.nested_relay"),
        ("task_target_domain", "sec.gov", "url.nested_target"),
    }


def test_withheld_host_is_recovered_from_placeholder(found, events):
    assert of(found, "withheld", events) == {
        ("relay_service", "jqp.vercel.app", "url.withheld_host")
    }


def test_junk_hosts_are_dropped(found, events):
    assert of(found, "junk", events) == set()


def test_catalog_and_uncatalogued_hosts(found, events):
    assert of(found, "shortener", events) == {
        ("url_shortener", "is.gd", "url.catalog"),
        ("linked_host", "docs.google.com", "url.host"),
    }


def test_lookalike_handles_normalize_together(found, events):
    h1 = {v for t, v, _ in of(found, "h1", events) if t == "actor_handle"}
    h2 = {v for t, v, _ in of(found, "h2", events) if t == "actor_handle"}
    assert h1 == h2 == {"openairesearcher"}


def test_oai_tokens_and_naming_shapes(found, events):
    h1 = of(found, "h1", events)
    assert ("oai_token", "openai", "name.oai") in h1
    assert ("naming_pattern", "openai·researcher", "name.shape") in h1
    assert ("naming_pattern", "openai·researcher·{w}", "name.shape") in h1


def test_ip16(found, events):
    assert ("ip16", "20.165", "event.ip16") in of(found, "h2", events)


def test_gem_names_from_rubyhack_and_from_wiki_links(found, events):
    assert ("gem_name", "lambyard17", "url.rubygems_gem") in of(found, "gemlink", events)
    gem = of(found, "gem", events)
    assert ("gem_name", "oaitest1778473828", "event.gem") in gem
    assert ("unix_ts_in_name", "1778473828", "name.unix_ts") in gem
    assert ("oai_token", "oai", "name.oai") in gem
    assert ("actor_handle", "oai", "event.actor_handle") in gem


def test_email_normalized(found, events):
    assert of(found, "mail", events) == {("email", "some.agent@gmail.com", "text.email")}


def test_placeholders_only_yield_redacted_placeholder(found, events):
    assert of(found, "chain", events) == {
        ("redacted_placeholder", "service_host", "text.placeholder"),
        ("redacted_placeholder", "shortener_code", "text.placeholder"),
    }


def test_transluce_data_source_maps_to_marker_domain(found, events):
    assert ("task_target_domain", "aihw.gov.au", "transluce.data_source") in of(
        found, "scan", events
    )


@pytest.mark.parametrize(
    ("name", "shape"),
    [
        ("AgentMassPointer13", "agent·{w}·{2d}"),
        ("OpenAIResearchSec2028", "openai·research·{w}·{year}"),
        ("oaibx0092307", "oai·{w}·{7d}"),
        ("chatoaitestgit1778552630", "{w}·oai·{w}·{unixts}"),
        ("zz-oai-test12", "{w}·oai·test·{2d}"),
        ("LinkHelper771", "link·helper·{3d}"),
    ],
)
def test_naming_shape(name, shape):
    assert ind.naming_shape(name) == shape


def test_build_indicators_reads_catalog_and_methods(tmp_path, events):
    interim, processed = tmp_path / "interim", tmp_path / "processed"
    (interim / "collusion-wiki").mkdir(parents=True)
    (interim / "transluce-urlquery").mkdir(parents=True)
    processed.mkdir()
    pl.DataFrame({"host": ["www.anna.fyi"], "category": ["pastebins"]}).write_parquet(
        interim / "collusion-wiki" / "site_coverage.parquet"
    )
    pl.DataFrame({"host": ["prowiki.org"], "category": ["wikis"]}).write_parquet(
        interim / "collusion-wiki" / "coverage_gaps.parquet"
    )
    METHODS.write_parquet(interim / "transluce-urlquery" / "methods.parquet")
    events.write_parquet(processed / "events.parquet")

    catalog = ind.service_catalog(interim)
    assert catalog["anna.fyi"] == "pastebin"
    assert catalog["prowiki.org"] == "agent_venue"
    assert catalog["r.jina.ai"] == "relay_service"
    out = ind.build_indicators(interim, processed)
    assert pl.read_parquet(processed / "indicators.parquet").equals(out)


def test_indicators_cli(monkeypatch):
    import agent_swarm
    from agent_swarm.paths import INTERIM_DIR, PROCESSED_DIR

    calls = []
    monkeypatch.setattr(
        ind, "build_indicators", lambda i, p: calls.append((i, p)) or pl.DataFrame()
    )
    agent_swarm.main(["indicators"])
    assert calls == [(INTERIM_DIR, PROCESSED_DIR)]


@pytest.mark.realdata
def test_real_indicators():
    from agent_swarm.paths import INTERIM_DIR, PROCESSED_DIR

    if not (PROCESSED_DIR / "events.parquet").exists():
        pytest.skip("run `agent-swarm all` first")
    out = ind.build_indicators(INTERIM_DIR, PROCESSED_DIR)
    events = pl.read_parquet(PROCESSED_DIR / "events.parquet", columns=["event_id"])
    assert set(out["event_id"]) <= set(events["event_id"])
    relays = out.filter(pl.col("indicator_type") == "relay_service")
    assert "jqp.vercel.app" in set(relays["value_norm"])
    oai = out.filter(pl.col("indicator_type") == "oai_token")
    assert {"wiki-collusion", "rubygems-attack"} <= set(oai["incident_id"])


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://cors.bwa.workers.dev/x",
            ("relay_service", "cors.bwa.workers.dev", "url.heuristic"),
        ),
        (
            "https://my-proxy.vercel.app/x",
            ("relay_service", "my-proxy.vercel.app", "url.heuristic"),
        ),
        ("https://proxy-itunes.apple.com/x", ("linked_host", "proxy-itunes.apple.com", "url.host")),
    ],
)
def test_relay_heuristic_needs_cors_or_proxy_on_free_hosting(url, expected):
    got = {(k, n, e) for k, _, n, e in ind._url_indicators(url, {})}
    assert got == {expected}
