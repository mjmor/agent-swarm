import hashlib

import polars as pl

EVENT_TYPES = (
    "wiki_save",
    "wiki_delete",
    "wiki_revert",
    "wiki_probe",
    "venue_post",
    "url_scan",
    "chain_payload",
    "chain_decoded",
    "chain_response",
    "package_published",
)
TS_PRECISIONS = ("second", "day", "none")
CONFIDENCES = ("high", "medium", "low", "unrated")
ACTOR_ROLES = ("agent", "moderator", "unknown")

EVENTS_SCHEMA: dict[str, pl.DataType] = {
    "event_id": pl.String(),
    "source_id": pl.String(),
    "incident_id": pl.String(),
    "artifact": pl.String(),
    "native_id": pl.String(),
    "event_type": pl.String(),
    "ts_utc": pl.Datetime("us", "UTC"),
    "ts_precision": pl.String(),
    "ts_field": pl.String(),
    "venue_host": pl.String(),
    "venue_locator": pl.String(),
    "actor_handle": pl.String(),
    "actor_role": pl.String(),
    "network_ip16": pl.String(),
    "text": pl.String(),
    "text_sha256": pl.String(),
    "urls": pl.List(pl.String()),
    "parent_event_id": pl.String(),
    "dup_of_event_id": pl.String(),
    "source_confidence": pl.String(),
    "confidence": pl.String(),
    "redaction_types": pl.List(pl.String()),
    "extra": pl.String(),
}

REQUIRED = (
    "event_id",
    "source_id",
    "incident_id",
    "artifact",
    "native_id",
    "event_type",
    "ts_precision",
    "confidence",
    "actor_role",
)
ENUMS = {
    "event_type": EVENT_TYPES,
    "ts_precision": TS_PRECISIONS,
    "confidence": CONFIDENCES,
    "actor_role": ACTOR_ROLES,
}


class SchemaError(ValueError):
    pass


def make_event_id(source_id: str, artifact: str, native_id: str) -> str:
    key = "\x1f".join((source_id, artifact, native_id))
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def empty_events() -> pl.DataFrame:
    return pl.DataFrame(schema=EVENTS_SCHEMA)


def conform(df: pl.DataFrame) -> pl.DataFrame:
    unknown = [c for c in df.columns if c not in EVENTS_SCHEMA]
    if unknown:
        raise SchemaError(f"columns not in canonical schema (put them in extra): {unknown}")
    return df.select(
        (pl.col(name) if name in df.columns else pl.lit(None)).cast(dtype).alias(name)
        for name, dtype in EVENTS_SCHEMA.items()
    )


def validate_events(df: pl.DataFrame) -> None:
    errors = []
    if dict(df.schema) != EVENTS_SCHEMA:
        errors.append(f"schema mismatch: {dict(df.schema)}")
        raise SchemaError("; ".join(errors))

    for col in REQUIRED:
        if (n := df[col].null_count()) > 0:
            errors.append(f"{col}: {n} null value(s)")
    for col, allowed in ENUMS.items():
        bad = sorted(set(df[col].drop_nulls().unique()) - set(allowed))
        if bad:
            errors.append(f"{col}: unknown value(s) {bad}")
    if (n := df.height - df["event_id"].n_unique()) > 0:
        errors.append(f"duplicate event_id: {n} row(s)")

    undated = df["ts_precision"] == "none"
    if (n := (undated & df["ts_utc"].is_not_null()).sum()) > 0:
        errors.append(f"ts_precision='none' but ts_utc set: {n} row(s)")
    if (n := (~undated & df["ts_utc"].is_null()).sum()) > 0:
        errors.append(f"ts_precision set but ts_utc null: {n} row(s)")

    if errors:
        raise SchemaError("; ".join(errors))
