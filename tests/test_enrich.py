import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import polars as pl
import pytest

from agent_swarm import enrich


def client(routes: dict[str, tuple[int, bytes]], calls: list[str]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        status, body = routes.get(str(request.url), (404, b"missing"))
        return httpx.Response(status, content=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.now += s


ITEMS = [("a", "https://x.test/a"), ("b", "https://x.test/b"), ("gone", "https://x.test/gone")]
ROUTES = {"https://x.test/a": (200, b'{"k": 1}'), "https://x.test/b": (200, b'{"k": 2}')}


def test_fetch_many_caches_logs_and_rate_limits(tmp_path: Path):
    calls, clock = [], FakeClock()
    log = enrich.fetch_many(
        ITEMS, tmp_path / "out", client=client(ROUTES, calls), min_interval=1.0, clock=clock
    )
    assert (tmp_path / "out" / "a.json").read_bytes() == b'{"k": 1}'
    assert not (tmp_path / "out" / "gone.json").exists()
    assert dict(zip(log["key"], log["status"], strict=True)) == {"a": 200, "b": 200, "gone": 404}
    assert len(clock.sleeps) == 2 and all(s == pytest.approx(1.0) for s in clock.sleeps)
    assert set(log.columns) >= {"key", "url", "status", "bytes", "sha256", "fetched_at"}


def test_fetch_many_resumes_without_refetching(tmp_path: Path):
    enrich.fetch_many(
        ITEMS, tmp_path / "out", client=client(ROUTES, []), min_interval=0, clock=FakeClock()
    )
    calls = []
    log = enrich.fetch_many(
        ITEMS, tmp_path / "out", client=client(ROUTES, calls), min_interval=0, clock=FakeClock()
    )
    assert calls == ["https://x.test/gone"]
    assert log.height == 3
    log_file = pl.read_ndjson(tmp_path / "out" / "_fetch_log.jsonl")
    assert log_file.height == 4


def test_fetch_many_survives_transport_errors(tmp_path: Path):
    def boom(request):
        raise httpx.ConnectError("down")

    log = enrich.fetch_many(
        [("a", "https://x.test/a")],
        tmp_path / "out",
        client=httpx.Client(transport=httpx.MockTransport(boom)),
        min_interval=0,
        clock=FakeClock(),
    )
    row = log.row(0, named=True)
    assert row["status"] == -1 and "ConnectError" in row["error"]


def test_wayback_items_from_cdx_rows():
    cdx = [
        ["timestamp", "original", "digest", "statuscode"],
        ["20260315000000", "https://openai.com/chatgpt-user.json", "AAA", "200"],
        ["20260601000000", "https://openai.com/chatgpt-user.json", "BBB", "200"],
        ["20260602000000", "https://openai.com/chatgpt-user.json", "BBB", "200"],
        ["20260701000000", "https://openai.com/chatgpt-user.json", "CCC", "404"],
    ]
    items = enrich.wayback_items(cdx)
    assert items == [
        (
            "chatgpt-user_20260315000000",
            "https://web.archive.org/web/20260315000000id_/https://openai.com/chatgpt-user.json",
        ),
        (
            "chatgpt-user_20260601000000",
            "https://web.archive.org/web/20260601000000id_/https://openai.com/chatgpt-user.json",
        ),
    ]


def test_rubygems_items_cover_report_and_wiki_gems():
    indicators = pl.DataFrame(
        {
            "indicator_type": ["gem_name", "gem_name", "gem_name", "relay_service"],
            "value_norm": ["oaibx0092307", "adep97117", "adep97117", "r.jina.ai"],
        }
    )
    items = enrich.rubygems_items(indicators)
    assert items == [
        ("adep97117.gem", "https://rubygems.org/api/v1/gems/adep97117.json"),
        ("adep97117.versions", "https://rubygems.org/api/v1/versions/adep97117.json"),
        ("oaibx0092307.gem", "https://rubygems.org/api/v1/gems/oaibx0092307.json"),
        ("oaibx0092307.versions", "https://rubygems.org/api/v1/versions/oaibx0092307.json"),
    ]


def test_urlquery_sample_is_stratified_seeded_and_includes_all_recent(tmp_path: Path):
    rows = []
    for i in range(400):
        month = 4 + i % 3
        rows.append(
            {
                "native_id": f"r{i}",
                "ts_utc": datetime(2026, month, 1 + i % 28, tzinfo=UTC),
                "extra": json.dumps(
                    {
                        "disposition": "included",
                        "broad_class": ["source_request", "indirection", "custom_program"][
                            (i // 3) % 3
                        ],
                    }
                ),
            }
        )
    rows += [
        {
            "native_id": f"sep{i}",
            "ts_utc": datetime(2026, 9, 10, tzinfo=UTC),
            "extra": json.dumps({"disposition": "included", "broad_class": "source_request"}),
        }
        for i in range(5)
    ]
    rows.append(
        {
            "native_id": "bg",
            "ts_utc": datetime(2026, 5, 1, tzinfo=UTC),
            "extra": json.dumps({"disposition": "background", "broad_class": "source_request"}),
        }
    )
    events = pl.DataFrame(rows, schema_overrides={"ts_utc": pl.Datetime("us", "UTC")})

    a = enrich.urlquery_sample(events, per_stratum=10, seed=7)
    b = enrich.urlquery_sample(events, per_stratum=10, seed=7)
    assert a == b
    ids = [k for k, _ in a]
    assert {f"sep{i}" for i in range(5)} <= set(ids)
    assert "bg" not in ids
    assert all(url == f"https://urlquery.net/report/{k}/json" for k, url in a)
    assert len(ids) == len(set(ids)) == 5 + 3 * 3 * 10


def test_run_enrichment_writes_item_list_and_fetches(tmp_path: Path, monkeypatch):
    processed, raw, lists = tmp_path / "processed", tmp_path / "raw", tmp_path / "lists"
    processed.mkdir()
    pl.DataFrame({"indicator_type": ["gem_name"], "value_norm": ["adep97117"]}).write_parquet(
        processed / "indicators.parquet"
    )
    routes = {"https://rubygems.org/api/v1/gems/adep97117.json": (200, b"{}")}
    log = enrich.run_enrichment(
        "rubygems",
        processed_dir=processed,
        raw_dir=raw,
        lists_dir=lists,
        client=client(routes, []),
        clock=FakeClock(),
    )
    assert (lists / "rubygems.items.tsv").read_text().splitlines()[0] == (
        "adep97117.gem\thttps://rubygems.org/api/v1/gems/adep97117.json"
    )
    assert (raw / "enrich-rubygems" / "adep97117.gem.json").exists()
    assert log.height == 2


def test_enrich_cli_dispatches(monkeypatch):
    import agent_swarm

    calls = []
    monkeypatch.setattr(
        enrich,
        "run_enrichment",
        lambda target, **kw: calls.append(target) or pl.DataFrame({"status": [200]}),
    )
    agent_swarm.main(["enrich", "wayback"])
    assert calls == ["wayback"]
