import json
from pathlib import Path

import polars as pl

from agent_swarm.reference import INCIDENTS, SOURCE_INCIDENT
from agent_swarm.schema import EVENTS_SCHEMA, TS_PRECISIONS, validate_events

TIMELINE_SCHEMA = {
    "date": pl.Date,
    "incident_id": pl.String,
    "event_type": pl.String,
    "n_events": pl.Int64,
    "ts_precision": pl.String,
    "origin": pl.String,
}


def _published_aggregates(interim_dir: Path) -> list[pl.DataFrame]:
    frames = []
    uploads = interim_dir / "rubyhack" / "uploads_per_day.parquet"
    if uploads.exists():
        frames.append(
            pl.read_parquet(uploads).select(
                pl.col("date_utc").cast(pl.Date).alias("date"),
                pl.lit(SOURCE_INCIDENT["rubyhack"]).alias("incident_id"),
                pl.lit("package_published").alias("event_type"),
                pl.col("total_uploads").cast(pl.Int64).alias("n_events"),
            )
        )
    daily = interim_dir / "transluce-urlquery" / "daily_counts.parquet"
    if daily.exists():
        frames.append(
            pl.read_parquet(daily).select(
                pl.col("date_utc").str.to_date().alias("date"),
                pl.lit(SOURCE_INCIDENT["transluce-urlquery"]).alias("incident_id"),
                pl.lit("url_scan").alias("event_type"),
                pl.col("total").cast(pl.Int64).alias("n_events"),
            )
        )
    return [
        f.filter(pl.col("n_events") > 0).with_columns(
            pl.lit("day").alias("ts_precision"), pl.lit("published_aggregate").alias("origin")
        )
        for f in frames
    ]


def timeline_daily(events: pl.DataFrame, interim_dir: Path) -> pl.DataFrame:
    row_level = (
        events.filter(pl.col("ts_precision") != "none", pl.col("dup_of_event_id").is_null())
        .group_by(
            pl.col("ts_utc").dt.date().alias("date"), "incident_id", "event_type", "ts_precision"
        )
        .agg(pl.len().cast(pl.Int64).alias("n_events"))
        .with_columns(pl.lit("row_level").alias("origin"))
    )
    frames = [row_level, *_published_aggregates(Path(interim_dir))]
    return (
        pl.concat([f.select(list(TIMELINE_SCHEMA)) for f in frames])
        .cast(TIMELINE_SCHEMA)
        .sort("date", "incident_id", "event_type", "origin")
    )


def data_quality(events: pl.DataFrame) -> dict:
    sources = {}
    for (source_id,), df in events.group_by("source_id", maintain_order=True):
        precision = dict(df["ts_precision"].value_counts().iter_rows())
        sources[source_id] = {
            "rows": df.height,
            "undated": precision.get("none", 0),
            "dup_linked": df["dup_of_event_id"].is_not_null().sum(),
            "ts_precision": {p: precision.get(p, 0) for p in TS_PRECISIONS},
            "event_types": dict(sorted(df["event_type"].value_counts().iter_rows())),
            "confidence": dict(sorted(df["confidence"].value_counts().iter_rows())),
            "null_rate": {
                c: round(df[c].null_count() / df.height, 4) for c in EVENTS_SCHEMA if df.height
            },
            "ts_range": [
                None if df["ts_utc"].min() is None else df["ts_utc"].min().isoformat(),
                None if df["ts_utc"].max() is None else df["ts_utc"].max().isoformat(),
            ],
        }
    return {
        "sources": dict(sorted(sources.items())),
        "totals": {
            "events": events.height,
            "undated": int((events["ts_precision"] == "none").sum()),
            "dup_linked": int(events["dup_of_event_id"].is_not_null().sum()),
        },
    }


def build(interim_dir: Path, processed_dir: Path) -> dict[str, int]:
    processed_dir = Path(processed_dir)
    parts = sorted(processed_dir.glob("events_*.parquet"))
    events = pl.concat([pl.read_parquet(p) for p in parts]).sort("source_id", "event_id")
    validate_events(events)
    timeline = timeline_daily(events, interim_dir)

    events.write_parquet(processed_dir / "events.parquet")
    INCIDENTS.write_parquet(processed_dir / "incidents.parquet")
    timeline.write_parquet(processed_dir / "timeline_daily.parquet")
    (processed_dir / "data_quality.json").write_text(
        json.dumps(data_quality(events), indent=2, default=int) + "\n"
    )
    return {"events": events.height, "timeline_rows": timeline.height, "sources": len(parts)}
