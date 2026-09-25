import gzip
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from agent_swarm.reference import SOURCE_INCIDENT
from agent_swarm.schema import conform, make_event_id, validate_events
from agent_swarm.textutil import extract_urls, redaction_types

DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_when(s: str | None) -> tuple[datetime | None, str]:
    if not s:
        return None, "none"
    s = s.strip()
    if DATE_ONLY.match(s):
        return datetime.fromisoformat(s).replace(tzinfo=UTC), "day"
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None, "none"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC), "second"


def when_columns(col: str) -> list[pl.Expr]:
    parsed = pl.col(col).map_elements(
        lambda s: dict(zip(("ts_utc", "ts_precision"), parse_when(s), strict=True)),
        return_dtype=pl.Struct({"ts_utc": pl.Datetime("us", "UTC"), "ts_precision": pl.String}),
        skip_nulls=False,
    )
    return [
        parsed.struct.field("ts_utc").alias("ts_utc"),
        parsed.struct.field("ts_precision").alias("ts_precision"),
    ]


def _drop_nulls(value):
    if isinstance(value, dict):
        return {k: _drop_nulls(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_drop_nulls(v) for v in value]
    return value


def json_extra(cols: list[str]) -> pl.Expr:
    def encode(row: dict) -> str | None:
        kept = _drop_nulls(row)
        return json.dumps(kept, ensure_ascii=False, default=str) if kept else None

    if not cols:
        return pl.lit(None, dtype=pl.String)
    return pl.struct(cols).map_elements(encode, return_dtype=pl.String, skip_nulls=False)


def assemble(
    df: pl.DataFrame,
    *,
    source_id: str,
    artifact: str,
    event_type: str | pl.Expr,
    mapped: dict[str, pl.Expr],
    consumed: set[str],
) -> pl.DataFrame:
    rest = [c for c in df.columns if c not in consumed]
    if isinstance(event_type, str):
        event_type = pl.lit(event_type)
    return df.select(
        pl.lit(source_id).alias("source_id"),
        pl.lit(SOURCE_INCIDENT[source_id]).alias("incident_id"),
        pl.lit(artifact).alias("artifact"),
        event_type.alias("event_type"),
        *(expr.alias(name) for name, expr in mapped.items()),
        json_extra(rest).alias("extra"),
    )


def read_jsonl_gz(path: Path) -> pl.DataFrame:
    return pl.read_ndjson(gzip.decompress(Path(path).read_bytes()), infer_schema_length=None)


def read_json_gz(path: Path) -> dict:
    return json.loads(gzip.decompress(Path(path).read_bytes()))


def event_id_expr() -> pl.Expr:
    return pl.struct("source_id", "artifact", "native_id").map_elements(
        lambda r: make_event_id(r["source_id"], r["artifact"], r["native_id"]),
        return_dtype=pl.String,
    )


def _sha256(text: str | None) -> str | None:
    return None if text is None else hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def finalize(df: pl.DataFrame) -> pl.DataFrame:
    text = pl.col("text")
    out = df.with_columns(
        event_id_expr().alias("event_id"),
        text.map_elements(_sha256, return_dtype=pl.String, skip_nulls=False).alias("text_sha256"),
        text.map_elements(extract_urls, return_dtype=pl.List(pl.String), skip_nulls=False).alias(
            "urls"
        ),
        text.map_elements(redaction_types, return_dtype=pl.List(pl.String), skip_nulls=False).alias(
            "redaction_types"
        ),
    )
    out = conform(out)
    validate_events(out)
    return out


def write_parquet(df: pl.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(path)
