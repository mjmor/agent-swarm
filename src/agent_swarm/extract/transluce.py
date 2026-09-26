import json
import zipfile
from pathlib import Path

import polars as pl

from agent_swarm.extract.common import assemble, finalize, write_parquet
from agent_swarm.reference import normalize_confidence

SOURCE_ID = "transluce-urlquery"
ZIP_NAME = "urlquery-agent-activity-2026-09-23.zip"
ARTIFACT = f"{SOURCE_ID}/{ZIP_NAME}"
INCLUDED = ("significant", "suggestive")
METHOD_COLUMNS = (
    "id",
    "label",
    "discovery_kind",
    "broad_class",
    "default_confidence",
    "query",
    "reason",
    "requires_review",
)


class Package:
    def __init__(self, path: Path):
        self.zip = zipfile.ZipFile(path)
        self.names = {Path(n).name: n for n in self.zip.namelist() if not n.endswith("/")}

    def csv(self, name: str) -> pl.DataFrame:
        return pl.read_csv(self.zip.read(self.names[name]), infer_schema_length=0)

    def json(self, name: str):
        return json.loads(self.zip.read(self.names[name]))


def _methods(records: list[dict]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {c: (None if r.get(c) is None else str(r[c])) for c in METHOD_COLUMNS}
            | {
                "markers": [str(m) for m in r.get("markers", [])],
                "raw": json.dumps(r, ensure_ascii=False),
            }
            for r in records
        ],
        schema={c: pl.String for c in METHOD_COLUMNS}
        | {"markers": pl.List(pl.String), "raw": pl.String},
    )


def _check_catalog(pkg: Package, reports: pl.DataFrame) -> None:
    parts = set(pkg.csv("reports.csv")["report_id"]) | set(
        pkg.csv("additional-cited-reports.csv")["report_id"]
    )
    if parts != set(reports["report_id"]) or reports["report_id"].n_unique() != reports.height:
        raise ValueError(
            "all-reports.csv is not the deduplicated union of reports.csv and "
            "additional-cited-reports.csv; refusing to guess which catalog to trust"
        )


def extract(raw_dir: Path, interim_dir: Path, processed_dir: Path) -> pl.DataFrame:
    pkg = Package(Path(raw_dir) / SOURCE_ID / ZIP_NAME)
    interim = Path(interim_dir) / SOURCE_ID

    reports = pkg.csv("all-reports.csv")
    _check_catalog(pkg, reports)
    supplement_ids = set(pkg.csv("additional-cited-reports.csv")["report_id"])
    sources = pkg.csv("report-sources.csv").drop("report_date_utc")

    df = reports.join(sources, on="report_id", how="left").with_columns(
        pl.when(pl.col("report_id").is_in(list(supplement_ids)))
        .then(pl.lit("supplement"))
        .otherwise(pl.lit("main"))
        .alias("catalog"),
        pl.coalesce("confidence", "disposition").alias("raw_confidence"),
    )
    events = finalize(
        assemble(
            df,
            source_id=SOURCE_ID,
            artifact=ARTIFACT,
            event_type="url_scan",
            mapped={
                "native_id": pl.col("report_id"),
                "ts_utc": pl.col("report_date_utc").str.to_datetime(time_zone="UTC"),
                "ts_precision": pl.lit("second"),
                "ts_field": pl.lit("report_date_utc"),
                "venue_host": pl.lit("urlquery.net"),
                "venue_locator": pl.col("report_id"),
                "actor_role": pl.when(pl.col("disposition") == "included")
                .then(pl.lit("agent"))
                .otherwise(pl.lit("unknown")),
                "source_confidence": pl.col("raw_confidence"),
                "confidence": pl.col("raw_confidence").map_elements(
                    lambda r: normalize_confidence(SOURCE_ID, r),
                    return_dtype=pl.String,
                    skip_nulls=False,
                ),
            },
            consumed={"report_id", "report_date_utc", "raw_confidence", "timestamp_precision"},
        )
    )

    tables = {
        "all_reports": reports,
        "report_sources": pkg.csv("report-sources.csv"),
        "methods": _methods(pkg.json("methods.json")),
        "daily_counts": pkg.csv("daily-counts.csv"),
        "daily_source_counts": pkg.csv("daily-source-counts.csv"),
        "selection_provenance": pkg.csv("selection-provenance.csv"),
    }
    for name, table in tables.items():
        write_parquet(table, interim / f"{name}.parquet")
    write_parquet(events, Path(processed_dir) / f"events_{SOURCE_ID}.parquet")
    return events


def reconcile_daily(events: pl.DataFrame, daily: pl.DataFrame) -> pl.DataFrame:
    row_level = (
        events.filter(pl.col("source_confidence").is_in(INCLUDED))
        .group_by(pl.col("ts_utc").dt.strftime("%Y-%m-%d").alias("date_utc"))
        .len("row_level")
    )
    return (
        daily.select("date_utc", pl.col("total").cast(pl.Int64).alias("published"))
        .join(row_level, on="date_utc", how="left")
        .with_columns(pl.col("row_level").fill_null(0).cast(pl.Int64))
        .filter(pl.col("published") != pl.col("row_level"))
    )
