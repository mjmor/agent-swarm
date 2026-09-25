from pathlib import Path

import polars as pl

from agent_swarm.extract.common import (
    assemble,
    finalize,
    read_json_gz,
    read_jsonl_gz,
    when_columns,
    write_parquet,
)
from agent_swarm.reference import normalize_confidence
from agent_swarm.schema import make_event_id
from agent_swarm.textutil import normalize_host

SOURCE_ID = "collusion-wiki"
PUBLISHER_SELECTED = "publisher_selected_agent_content"
WIKI_HOSTS = {
    "dse": "prowiki.org",
    "wiki4d": "prowiki.org",
    "probier": "wikiservice.at",
    "fractal": "wikiservice.at",
    "dorfwiki": "dorfwiki.org",
    "publictestwiki": "publictestwiki.com",
    "uncyclopedia": "uncyclopedia.com",
    "usemod": "usemod.org",
}
EVENT_TYPES = {"delete": "wiki_delete", "revert": "wiki_revert", "probe": "wiki_probe"}
PLACEHOLDER_LABEL = r"^\[.*\]$"
WIKI_IN_EVENT_ID = rf"_({'|'.join(WIKI_HOSTS)})_"


def art(name: str) -> str:
    return f"{SOURCE_ID}/{name}"


def _confidence(raw: pl.Expr) -> pl.Expr:
    return raw.map_elements(
        lambda r: normalize_confidence(SOURCE_ID, r), return_dtype=pl.String, skip_nulls=False
    )


def _host(wiki: pl.Expr) -> pl.Expr:
    return wiki.replace_strict(WIKI_HOSTS, default=None, return_dtype=pl.String)


def _role(handle: pl.Expr, moderators: set[str], default: str) -> pl.Expr:
    return (
        pl.when(handle.is_in(list(moderators))).then(pl.lit("moderator")).otherwise(pl.lit(default))
    )


def _revisions(rv: pl.DataFrame, saves: pl.DataFrame, moderators: set[str]) -> pl.DataFrame:
    artifact = art("revisions.jsonl.gz")
    df = rv.join(saves, on="rev_id", how="left")
    handle = pl.when(pl.col("label").str.strip_chars() == "").then(None).otherwise(pl.col("label"))
    parent = pl.col("diff_base").map_elements(
        lambda b: make_event_id(SOURCE_ID, artifact, b), return_dtype=pl.String
    )
    return assemble(
        df,
        source_id=SOURCE_ID,
        artifact=artifact,
        event_type="wiki_save",
        mapped={
            "native_id": pl.col("rev_id"),
            "ts_utc": pl.col("time").str.to_datetime(time_zone="UTC"),
            "ts_precision": pl.lit("second"),
            "ts_field": pl.lit("revision.time"),
            "venue_host": _host(pl.col("wiki")),
            "venue_locator": pl.col("page_id"),
            "actor_handle": handle,
            "actor_role": _role(handle, moderators, "agent"),
            "network_ip16": pl.col("ip16"),
            "text": pl.col("body"),
            "parent_event_id": parent,
            "source_confidence": pl.lit(PUBLISHER_SELECTED),
            "confidence": _confidence(pl.lit(PUBLISHER_SELECTED)),
        },
        consumed={"rev_id", "time", "wiki", "page_id", "label", "ip16", "body", "diff_base"},
    )


def _wiki_events(ev: pl.DataFrame, page_ids: dict[str, str], moderators: set[str]) -> pl.DataFrame:
    df = ev.filter(pl.col("event_type") != "save")
    wiki = pl.coalesce(pl.col("wiki"), pl.col("event_id").str.extract(WIKI_IN_EVENT_ID))
    handle = (
        pl.when(pl.col("actor_label").str.contains(PLACEHOLDER_LABEL))
        .then(None)
        .otherwise(pl.col("actor_label"))
    )
    role = (
        pl.when(pl.col("event_type") == "delete")
        .then(pl.lit("moderator"))
        .otherwise(_role(handle, moderators, "agent"))
    )
    locator = pl.col("page_key").replace_strict(page_ids, default=pl.col("page_key"))
    text = pl.when(pl.col("event_type") == "probe").then(pl.col("request_action"))
    return assemble(
        df,
        source_id=SOURCE_ID,
        artifact=art("events.jsonl.gz"),
        event_type=pl.col("event_type").replace_strict(EVENT_TYPES),
        mapped={
            "native_id": pl.col("event_id"),
            "ts_utc": pl.col("time").str.to_datetime(time_zone="UTC"),
            "ts_precision": pl.lit("second"),
            "ts_field": pl.lit("event.time"),
            "venue_host": _host(wiki),
            "venue_locator": locator,
            "actor_handle": handle,
            "actor_role": role,
            "network_ip16": pl.col("ip16"),
            "text": text,
            "source_confidence": pl.lit(PUBLISHER_SELECTED),
            "confidence": _confidence(pl.lit(PUBLISHER_SELECTED)),
        },
        consumed={"event_id", "event_type", "time", "ip16"},
    )


def _records(rec: pl.DataFrame, rv: pl.DataFrame) -> pl.DataFrame:
    artifact = art("records.jsonl.gz")
    rev_artifact = art("revisions.jsonl.gz")
    first_rev = (
        rv.group_by("page_id", "time")
        .agg(pl.col("rev_id").sort_by("seq").first())
        .select(
            pl.col("page_id").alias("origin_source_id"),
            pl.col("time").alias("origin_source_date_literal"),
            pl.col("rev_id").alias("dup_rev_id"),
        )
    )
    df = (
        rec.with_columns(pl.int_ranges(0, pl.col("origins").list.len()).alias("origin_idx"))
        .explode("origins", "origin_idx", empty_as_null=True)
        .with_columns(pl.col("origins").name.prefix_fields("origin_"))
        .unnest("origins")
        .join(first_rev, on=["origin_source_id", "origin_source_date_literal"], how="left")
    )
    dup = pl.col("dup_rev_id").map_elements(
        lambda r: make_event_id(SOURCE_ID, rev_artifact, r), return_dtype=pl.String
    )
    return assemble(
        df.with_columns(when_columns("origin_source_date_literal")),
        source_id=SOURCE_ID,
        artifact=artifact,
        event_type="venue_post",
        mapped={
            "native_id": pl.format("{}#{}", pl.col("id"), pl.col("origin_idx")),
            "ts_utc": pl.col("ts_utc"),
            "ts_precision": pl.col("ts_precision"),
            "ts_field": pl.lit("origin.source_date_literal"),
            "venue_host": pl.col("origin_site").map_elements(
                normalize_host, return_dtype=pl.String
            ),
            "venue_locator": pl.col("origin_url"),
            "actor_role": pl.lit("agent"),
            "text": pl.col("text"),
            "dup_of_event_id": dup,
            "source_confidence": pl.col("selection_basis"),
            "confidence": _confidence(pl.col("selection_basis")),
        },
        consumed={"id", "origin_idx", "ts_utc", "ts_precision", "origin_url", "text", "dup_rev_id"},
    )


def _shortener_logs(logs: dict) -> tuple[pl.DataFrame, pl.DataFrame]:
    rows = [link | {"site": s["site"]} for s in logs["sites"] for link in s["links"]]
    flat = pl.DataFrame(rows, infer_schema_length=None)
    events = assemble(
        flat,
        source_id=SOURCE_ID,
        artifact=art("shortener-logs.json.gz"),
        event_type="venue_post",
        mapped={
            "native_id": pl.format("{}/{}", pl.col("site"), pl.col("keyword")),
            "ts_utc": pl.col("time").str.to_datetime(time_zone="UTC"),
            "ts_precision": pl.lit("second"),
            "ts_field": pl.lit("link.time"),
            "venue_host": pl.col("site"),
            "venue_locator": pl.col("keyword"),
            "actor_role": pl.lit("agent"),
            "network_ip16": pl.col("ip16"),
            "text": pl.col("url"),
            "source_confidence": pl.lit(PUBLISHER_SELECTED),
            "confidence": _confidence(pl.lit(PUBLISHER_SELECTED)),
        },
        consumed={"site", "keyword", "time", "ip16", "url"},
    )
    return flat, events


def _other_wikis(other: dict) -> tuple[pl.DataFrame, pl.DataFrame]:
    rows = [
        rev | {"page_id": p["page_id"], "page_key": p["page_key"], "wiki": p["wiki"]}
        for p in other["pages"]
        for rev in p["revisions"]
    ]
    flat = pl.DataFrame(rows, infer_schema_length=None)
    events = assemble(
        flat,
        source_id=SOURCE_ID,
        artifact=art("other-wikis.json.gz"),
        event_type="wiki_save",
        mapped={
            "native_id": pl.format("{}@{}", pl.col("page_key"), pl.col("seq")),
            "ts_utc": pl.col("time").str.to_datetime(time_zone="UTC"),
            "ts_precision": pl.lit("second"),
            "ts_field": pl.lit("revision.time"),
            "venue_host": _host(pl.col("wiki")),
            "venue_locator": pl.col("page_id"),
            "actor_role": pl.lit("agent"),
            "network_ip16": pl.col("ip16"),
            "text": pl.col("added").list.join("\n"),
            "source_confidence": pl.lit(PUBLISHER_SELECTED),
            "confidence": _confidence(pl.lit(PUBLISHER_SELECTED)),
        },
        consumed={"page_key", "seq", "time", "wiki", "page_id", "ip16", "added"},
    )
    return flat, events


def extract(raw_dir: Path, interim_dir: Path, processed_dir: Path) -> pl.DataFrame:
    raw = Path(raw_dir) / SOURCE_ID
    interim = Path(interim_dir) / SOURCE_ID

    rv = read_jsonl_gz(raw / "revisions.jsonl.gz")
    ev = read_jsonl_gz(raw / "events.jsonl.gz")
    labels = read_jsonl_gz(raw / "labels.jsonl.gz")
    pages = read_jsonl_gz(raw / "pages.jsonl.gz")
    rec = read_jsonl_gz(raw / "records.jsonl.gz")
    links = read_jsonl_gz(raw / "links.jsonl.gz")
    coverage = pl.read_csv(raw / "site-coverage.csv", infer_schema_length=0)
    gaps = pl.read_csv(raw / "coverage-gaps.csv", infer_schema_length=0)
    shortener_flat, shortener_events = _shortener_logs(read_json_gz(raw / "shortener-logs.json.gz"))
    other_flat, other_events = _other_wikis(read_json_gz(raw / "other-wikis.json.gz"))

    moderators = set(labels.filter(pl.col("is_human_handle"))["label"])
    page_ids = dict(
        pl.concat([rv.select("page_key", "page_id"), pages.select("page_key", "page_id")])
        .unique("page_key")
        .iter_rows()
    )
    saves = ev.filter(pl.col("event_type") == "save").select(
        pl.col("revision_ref").alias("rev_id"),
        pl.struct(pl.all().exclude("revision_ref")).alias("save_event"),
    )

    events = finalize(
        pl.concat(
            [
                _revisions(rv, saves, moderators),
                _wiki_events(ev, page_ids, moderators),
                _records(rec, rv),
                shortener_events,
                other_events,
            ],
            how="diagonal_relaxed",
        )
    )

    for name, df in {
        "revisions": rv,
        "events": ev,
        "labels": labels,
        "pages": pages,
        "records": rec,
        "links": links,
        "site_coverage": coverage,
        "coverage_gaps": gaps,
        "shortener_logs": shortener_flat,
        "other_wikis": other_flat,
    }.items():
        write_parquet(df, interim / f"{name}.parquet")
    write_parquet(events, Path(processed_dir) / f"events_{SOURCE_ID}.parquet")
    return events
