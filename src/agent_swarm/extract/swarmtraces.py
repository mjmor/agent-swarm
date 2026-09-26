import re
from pathlib import Path

import polars as pl

from agent_swarm.extract.common import assemble, finalize, read_jsonl_gz, write_parquet
from agent_swarm.reference import normalize_confidence
from agent_swarm.schema import make_event_id
from agent_swarm.textutil import is_placeholder

SOURCE_ID = "swarmtraces"
ARTIFACT = f"{SOURCE_ID}/redacted.jsonl.gz"
CONFIDENCE = "vendor_confirmed_match"
EVENT_TYPES = {
    "payload": "chain_payload",
    "recovered_text": "chain_decoded",
    "response": "chain_response",
}
MAX_DEPTH = 256
AGENT_ID_RE = re.compile(r"""["']?agent_?id["']?\s*[:=]\s*["']([^"'\\]{1,80})["']""", re.I)
DATE_RE = re.compile(r"\b(2026-0[1-9]-[0-3]\d)\b")


def tree(df: pl.DataFrame) -> pl.DataFrame:
    parent = dict(zip(df["id"], df["parent_id"], strict=True))
    resolved: dict[str, tuple[str | None, int, bool]] = {}
    for start in df["id"]:
        path, node = [], start
        while node is not None and node not in resolved and node not in path:
            if len(path) >= MAX_DEPTH:
                break
            path.append(node)
            node = parent.get(node)
        if node is None:
            root, depth, cycle = path[-1], len(path) - 1, False
        elif node in resolved:
            root, base, cycle = resolved[node]
            depth = base + len(path)
        else:
            root, depth, cycle = None, -1, True
        for i, n in enumerate(path):
            resolved[n] = (root, -1 if cycle else depth - i, cycle)
    return df.select("id", "kind", "parent_id").with_columns(
        pl.col("id")
        .replace_strict({k: v[0] for k, v in resolved.items()}, return_dtype=pl.String)
        .alias("root_id"),
        pl.col("id")
        .replace_strict({k: v[1] for k, v in resolved.items()}, return_dtype=pl.Int64)
        .alias("depth"),
        pl.col("id")
        .replace_strict({k: v[2] for k, v in resolved.items()}, return_dtype=pl.Boolean)
        .alias("cycle"),
    )


def _handle(text: str | None) -> str | None:
    for value in AGENT_ID_RE.findall(text or ""):
        if not is_placeholder(value) and not value.startswith("["):
            return value
    return None


def _ts_hints(text: str | None) -> list[str]:
    return sorted(set(DATE_RE.findall(text or "")))


def extract(raw_dir: Path, interim_dir: Path, processed_dir: Path) -> pl.DataFrame:
    raw = read_jsonl_gz(Path(raw_dir) / SOURCE_ID / "redacted.jsonl.gz")
    shape = tree(raw)
    df = raw.join(
        shape.select("id", "root_id", "depth", "cycle"), on="id", how="left"
    ).with_columns(
        pl.when(pl.col("tags") == "")
        .then(None)
        .otherwise(pl.col("tags").str.split(";"))
        .alias("tags"),
        pl.col("text").map_elements(_ts_hints, return_dtype=pl.List(pl.String)).alias("ts_hints"),
        pl.when(pl.col("depth") < 0).then(None).otherwise(pl.col("depth")).alias("depth"),
    )
    parent = pl.col("parent_id").map_elements(
        lambda p: make_event_id(SOURCE_ID, ARTIFACT, p), return_dtype=pl.String
    )
    events = finalize(
        assemble(
            df,
            source_id=SOURCE_ID,
            artifact=ARTIFACT,
            event_type=pl.col("kind").replace_strict(EVENT_TYPES),
            mapped={
                "native_id": pl.col("id"),
                "ts_utc": pl.lit(None, dtype=pl.Datetime("us", "UTC")),
                "ts_precision": pl.lit("none"),
                "venue_host": pl.lit("[shortener]"),
                "venue_locator": pl.col("cite"),
                "actor_handle": pl.col("text").map_elements(_handle, return_dtype=pl.String),
                "actor_role": pl.when(pl.col("kind") == "response")
                .then(pl.lit("unknown"))
                .otherwise(pl.lit("agent")),
                "text": pl.col("text"),
                "parent_event_id": parent,
                "source_confidence": pl.lit(CONFIDENCE),
                "confidence": pl.lit(normalize_confidence(SOURCE_ID, CONFIDENCE)),
            },
            consumed={"id", "kind", "parent_id", "time_utc", "text"},
        )
    )
    write_parquet(shape, Path(interim_dir) / SOURCE_ID / "tree.parquet")
    write_parquet(events, Path(processed_dir) / f"events_{SOURCE_ID}.parquet")
    return events
