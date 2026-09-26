import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import polars as pl

from agent_swarm.acquire import USER_AGENT

LOG_NAME = "_fetch_log.jsonl"
LOG_SCHEMA = {
    "key": pl.String,
    "url": pl.String,
    "status": pl.Int64,
    "bytes": pl.Int64,
    "sha256": pl.String,
    "fetched_at": pl.String,
    "error": pl.String,
}
POST_CAMPAIGN = datetime(2026, 7, 1, tzinfo=UTC)


def _default_client() -> httpx.Client:
    return httpx.Client(follow_redirects=True, timeout=60, headers={"User-Agent": USER_AGENT})


def fetch_many(
    items: list[tuple[str, str]],
    out_dir: Path,
    client: httpx.Client | None = None,
    min_interval: float = 1.0,
    clock=time,
    ext: str = ".json",
) -> pl.DataFrame:
    """Fetch each (key, url) once into out_dir/<key><ext>; append every attempt to a log.

    Items whose file already exists are skipped, so re-running resumes a partial fetch and
    retries only failures. Requests are spaced at least `min_interval` seconds apart.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    client = client or _default_client()
    last_request: float | None = None
    with (out_dir / LOG_NAME).open("a") as log:
        for key, url in items:
            path = out_dir / f"{key}{ext}"
            if path.exists():
                continue
            if (
                last_request is not None
                and (wait := min_interval - (clock.time() - last_request)) > 0
            ):
                clock.sleep(wait)
            last_request = clock.time()
            entry = {"key": key, "url": url, "fetched_at": datetime.now(UTC).isoformat()}
            try:
                resp = client.get(url)
                entry |= {"status": resp.status_code, "bytes": len(resp.content)}
                if resp.status_code == 200:
                    path.write_bytes(resp.content)
                    entry["sha256"] = hashlib.sha256(resp.content).hexdigest()
            except httpx.HTTPError as e:
                entry |= {"status": -1, "error": f"{type(e).__name__}: {e}"}
            log.write(json.dumps(entry) + "\n")
    history = pl.read_ndjson(out_dir / LOG_NAME, schema=LOG_SCHEMA)
    wanted = pl.DataFrame({"key": [k for k, _ in items]}, schema={"key": pl.String})
    return wanted.join(history.unique("key", keep="last"), on="key", how="left")


def wayback_items(cdx_rows: list[list[str]]) -> list[tuple[str, str]]:
    header, *rows = cdx_rows
    col = {name: i for i, name in enumerate(header)}
    seen: set[str] = set()
    items = []
    for row in rows:
        if row[col["statuscode"]] != "200" or row[col["digest"]] in seen:
            continue
        seen.add(row[col["digest"]])
        ts, original = row[col["timestamp"]], row[col["original"]]
        name = original.rstrip("/").rsplit("/", 1)[-1].removesuffix(".json")
        items.append((f"{name}_{ts}", f"https://web.archive.org/web/{ts}id_/{original}"))
    return items


def rubygems_items(indicators: pl.DataFrame) -> list[tuple[str, str]]:
    names = sorted(set(indicators.filter(pl.col("indicator_type") == "gem_name")["value_norm"]))
    return [
        item
        for name in names
        for item in (
            (f"{name}.gem", f"https://rubygems.org/api/v1/gems/{name}.json"),
            (f"{name}.versions", f"https://rubygems.org/api/v1/versions/{name}.json"),
        )
    ]


def urlquery_sample(events: pl.DataFrame, per_stratum: int, seed: int) -> list[tuple[str, str]]:
    """Every included report from July 2026 on, plus up to `per_stratum` per (class, month)."""
    extra = pl.Struct({"disposition": pl.String, "broad_class": pl.String})
    included = (
        events.with_columns(pl.col("extra").str.json_decode(extra).alias("x"))
        .unnest("x")
        .filter(pl.col("disposition") == "included")
        .sort("native_id")
    )
    recent = included.filter(pl.col("ts_utc") >= POST_CAMPAIGN)
    strata = included.filter(pl.col("ts_utc") < POST_CAMPAIGN).with_columns(
        pl.col("ts_utc").dt.strftime("%Y-%m").alias("month")
    )
    sampled = [
        group.sample(n=min(per_stratum, group.height), seed=seed)
        for _, group in strata.group_by("broad_class", "month", maintain_order=True)
    ]
    ids = sorted(set(recent["native_id"]) | {i for g in sampled for i in g["native_id"]})
    return [(i, f"https://urlquery.net/report/{i}/json") for i in ids]


WAYBACK_FILES = [
    "https://openai.com/chatgpt-user.json",
    "https://openai.com/gptbot.json",
    "https://openai.com/searchbot.json",
]
CDX_URL = "https://web.archive.org/cdx/search/cdx"
CDX_ATTEMPTS = 4
URLQUERY_PER_STRATUM = 25
URLQUERY_SEED = 20260926
TARGETS = {
    "rubygems": 0.2,
    "urlquery-sample": 1.0,
    "wayback": 1.0,
}


def _cdx_rows(client: httpx.Client, url: str, clock) -> list[list[str]]:
    params = {"url": url, "output": "json", "from": "2025", "to": "2026"}
    for attempt in range(CDX_ATTEMPTS):
        try:
            resp = client.get(CDX_URL, params=params)
            if resp.status_code == 200:
                return resp.json() or []
        except httpx.HTTPError:
            pass
        if attempt < CDX_ATTEMPTS - 1:
            clock.sleep(2**attempt * 5)
    print(f"[wayback] CDX unavailable for {url} after {CDX_ATTEMPTS} attempts; skipped")
    return []


def _items_for(
    target: str, processed_dir: Path, client: httpx.Client, clock=time
) -> list[tuple[str, str]]:
    if target == "rubygems":
        return rubygems_items(pl.read_parquet(processed_dir / "indicators.parquet"))
    if target == "urlquery-sample":
        events = pl.read_parquet(
            processed_dir / "events.parquet", columns=["source_id", "native_id", "ts_utc", "extra"]
        ).filter(pl.col("source_id") == "transluce-urlquery")
        return urlquery_sample(events, URLQUERY_PER_STRATUM, URLQUERY_SEED)
    if target == "wayback":
        items = []
        for url in WAYBACK_FILES:
            if rows := _cdx_rows(client, url, clock):
                items += wayback_items(rows)
        return items
    raise KeyError(target)


def run_enrichment(
    target: str,
    processed_dir: Path,
    raw_dir: Path,
    lists_dir: Path,
    client: httpx.Client | None = None,
    clock=time,
) -> pl.DataFrame:
    client = client or _default_client()
    items = _items_for(target, Path(processed_dir), client, clock)
    Path(lists_dir).mkdir(parents=True, exist_ok=True)
    (Path(lists_dir) / f"{target}.items.tsv").write_text("".join(f"{k}\t{u}\n" for k, u in items))
    return fetch_many(
        items,
        Path(raw_dir) / f"enrich-{target}",
        client=client,
        min_interval=TARGETS[target],
        clock=clock,
    )
