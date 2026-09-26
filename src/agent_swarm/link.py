from itertools import combinations
from pathlib import Path

import polars as pl

from agent_swarm.reference import GENERIC_DOMAINS
from agent_swarm.textutil import normalize_host, registrable_domain

HOST_TYPES = (
    "task_target_domain",
    "linked_host",
    "relay_service",
    "url_shortener",
    "pastebin",
    "url_scanner",
    "agent_venue",
)
DIRECT_TYPES = {
    "gem_name": "gem",
    "actor_handle": "handle",
    "oai_token": "oai_token",
    "ip16": "ip16",
}
INFORMATIVE_SHAPE = r"oai|openai|agent|test|unixts"


EVENT_COLUMNS = [
    "event_id",
    "incident_id",
    "ts_utc",
    "venue_host",
    "actor_role",
    "confidence",
    "dup_of_event_id",
]


def _linkable(events: pl.DataFrame) -> pl.DataFrame:
    return events.filter(
        pl.col("actor_role") != "moderator",
        pl.col("confidence") != "low",
        pl.col("dup_of_event_id").is_null(),
    )


def _venue_indicators(events: pl.DataFrame) -> pl.DataFrame:
    return events.select(
        "event_id",
        "incident_id",
        pl.lit("venue").alias("indicator_type"),
        pl.col("venue_host").alias("value"),
        pl.col("venue_host")
        .map_elements(normalize_host, return_dtype=pl.String)
        .alias("value_norm"),
        pl.lit("event.venue_host").alias("extractor"),
    ).filter(pl.col("value_norm").is_not_null())


def link_keys(indicators: pl.DataFrame, events: pl.DataFrame) -> pl.DataFrame:
    events = _linkable(events)
    kind = pl.col("indicator_type")
    host_kind = kind.is_in([*HOST_TYPES, "venue"])
    keyed = (
        pl.concat([indicators, _venue_indicators(events)], how="vertical_relaxed")
        .with_columns(
            pl.when(host_kind)
            .then(pl.lit("domain"))
            .when(kind == "naming_pattern")
            .then(pl.lit("name_shape"))
            .otherwise(kind.replace_strict(DIRECT_TYPES, default=None))
            .alias("key_type"),
            pl.when(host_kind)
            .then(pl.col("value_norm").map_elements(registrable_domain, return_dtype=pl.String))
            .otherwise(pl.col("value_norm"))
            .alias("key"),
        )
        .filter(
            pl.col("key_type").is_not_null(),
            ~((pl.col("key_type") == "domain") & pl.col("key").is_in(list(GENERIC_DOMAINS))),
            (pl.col("key_type") != "name_shape") | pl.col("key").str.contains(INFORMATIVE_SHAPE),
        )
        .join(events.select("event_id", pl.col("ts_utc").dt.date().alias("day")), on="event_id")
    )
    return (
        keyed.group_by("key_type", "key", "incident_id")
        .agg(
            pl.col("event_id").n_unique().alias("events"),
            pl.col("indicator_type").unique().sort().alias("roles"),
            pl.col("day").min().alias("first_seen"),
            pl.col("day").max().alias("last_seen"),
        )
        .sort("key_type", "key", "incident_id")
    )


def bridges(keys: pl.DataFrame) -> pl.DataFrame:
    return (
        keys.sort("key_type", "key", "first_seen", "incident_id", nulls_last=True)
        .group_by("key_type", "key", maintain_order=True)
        .agg(
            pl.col("incident_id").unique().sort().alias("incidents"),
            pl.col("incident_id").n_unique().alias("n_incidents"),
            pl.col("events").sum().alias("total_events"),
            pl.col("incident_id")
            .filter(pl.col("first_seen").is_not_null())
            .first()
            .alias("first_incident"),
            pl.col("first_seen").min().alias("first_seen"),
        )
        .filter(pl.col("n_incidents") >= 2)
        .sort("key_type", "key")
    )


def incident_overlap(keys: pl.DataFrame) -> pl.DataFrame:
    incidents = sorted(keys["incident_id"].unique())
    rows = []
    for (key_type,), frame in keys.group_by("key_type", maintain_order=True):
        sets = {i: set(frame.filter(pl.col("incident_id") == i)["key"]) for i in incidents}
        for a, b in combinations(incidents, 2):
            shared, union = sets[a] & sets[b], sets[a] | sets[b]
            rows.append(
                {
                    "key_type": key_type,
                    "incident_a": a,
                    "incident_b": b,
                    "keys_a": len(sets[a]),
                    "keys_b": len(sets[b]),
                    "shared": len(shared),
                    "jaccard": len(shared) / len(union) if union else 0.0,
                }
            )
    return pl.DataFrame(rows).sort("key_type", "incident_a", "incident_b")


def build_links(processed_dir: Path) -> dict[str, int]:
    processed_dir = Path(processed_dir)
    indicators = pl.read_parquet(processed_dir / "indicators.parquet")
    events = pl.read_parquet(processed_dir / "events.parquet", columns=EVENT_COLUMNS)
    keys = link_keys(indicators, events)
    tables = {
        "link_keys": keys,
        "bridges": bridges(keys),
        "incident_overlap": incident_overlap(keys),
    }
    out = processed_dir / "links"
    out.mkdir(parents=True, exist_ok=True)
    for name, table in tables.items():
        table.write_parquet(out / f"{name}.parquet")
    return {name: table.height for name, table in tables.items()}
