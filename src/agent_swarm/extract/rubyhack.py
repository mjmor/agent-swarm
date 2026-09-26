import re
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path

import polars as pl

from agent_swarm.extract.common import assemble, finalize, write_parquet
from agent_swarm.reference import normalize_confidence

SOURCE_ID = "rubyhack"
ARTIFACT = f"{SOURCE_ID}/report.html"
CONFIDENCE = "publisher_listed"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,}$")
AUTHOR_LINE_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.-]{2,})\s+(\S+)\s+—\s+author:\s*(\S+)$")
LINK_RES = {
    "rubygems_link": re.compile(
        r"rubygems\.org/gems/([A-Za-z0-9_.-]+)(?:/versions/([0-9][\w.]*))?"
    ),
    "diffend_link": re.compile(r"diffend\.io/gems/([A-Za-z0-9_.-]+)((?:/[0-9][\w.]*)*)"),
}
TS_WINDOW = (
    int(datetime(2026, 1, 1, tzinfo=UTC).timestamp()),
    int(datetime(2026, 10, 1, tzinfo=UTC).timestamp()),
)


class _Report(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs: list[str] = []
        self.pres: list[tuple[str | None, str]] = []
        self._figure: str | None = None
        self._pre: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "figure":
            self._figure = attrs.get("id")
        elif tag == "pre":
            self._pre = []
        elif tag == "a" and attrs.get("href"):
            self.hrefs.append(attrs["href"])

    def handle_endtag(self, tag):
        if tag == "pre" and self._pre is not None:
            self.pres.append((self._figure, "".join(self._pre)))
            self._pre = None
        elif tag == "figure":
            self._figure = None

    def handle_data(self, data):
        if self._pre is not None:
            self._pre.append(data)


def name_timestamp(name: str) -> int | None:
    for run in re.findall(r"\d+", name):
        if len(run) == 10:
            ts = int(run)
        elif len(run) == 13:
            ts = int(run[:10])
        else:
            continue
        if TS_WINDOW[0] <= ts < TS_WINDOW[1]:
            return ts
    return None


def parse_gems(report_html: str) -> pl.DataFrame:
    parser = _Report()
    parser.feed(report_html)
    gems: dict[str, dict] = {}

    def add(name: str, evidence: str, version: str | None = None, author: str | None = None):
        g = gems.setdefault(name, {"evidence": set(), "versions": set(), "author": None})
        g["evidence"].add(evidence)
        if version:
            g["versions"].add(version)
        if author:
            g["author"] = author

    for figure, text in parser.pres:
        lines = [ln.strip() for ln in text.replace("[…]", "").splitlines() if ln.strip()]
        if not lines or not figure:
            continue
        if all(NAME_RE.match(ln) for ln in lines):
            for ln in lines:
                add(ln, figure)
        elif all(AUTHOR_LINE_RE.match(ln) for ln in lines):
            for ln in lines:
                name, version, author = AUTHOR_LINE_RE.match(ln).groups()
                add(name, figure, version, author)

    for href in parser.hrefs:
        for evidence, pattern in LINK_RES.items():
            if m := pattern.search(href):
                name, tail = m.group(1), m.group(2) or ""
                versions = [v for v in tail.split("/") if v]
                add(name, evidence)
                for v in versions:
                    add(name, evidence, v)

    return pl.DataFrame(
        [
            {
                "name": name,
                "evidence": sorted(g["evidence"]),
                "versions": sorted(g["versions"]),
                "author": g["author"],
                "name_ts": name_timestamp(name),
            }
            for name, g in sorted(gems.items())
        ],
        schema={
            "name": pl.String,
            "evidence": pl.List(pl.String),
            "versions": pl.List(pl.String),
            "author": pl.String,
            "name_ts": pl.Int64,
        },
    )


def extract(raw_dir: Path, interim_dir: Path, processed_dir: Path) -> pl.DataFrame:
    raw = Path(raw_dir) / SOURCE_ID
    gems = parse_gems((raw / "report.html").read_text(encoding="utf-8"))
    uploads = pl.read_csv(raw / "uploads-per-day.csv", try_parse_dates=True)

    dated = pl.col("name_ts").is_not_null()
    events = finalize(
        assemble(
            gems,
            source_id=SOURCE_ID,
            artifact=ARTIFACT,
            event_type="package_published",
            mapped={
                "native_id": pl.col("name"),
                "ts_utc": pl.from_epoch("name_ts", time_unit="s").dt.replace_time_zone("UTC"),
                "ts_precision": pl.when(dated).then(pl.lit("second")).otherwise(pl.lit("none")),
                "ts_field": pl.when(dated).then(pl.lit("name_unix_ts")),
                "venue_host": pl.lit("rubygems.org"),
                "venue_locator": pl.col("name"),
                "actor_handle": pl.col("author"),
                "actor_role": pl.lit("agent"),
                "source_confidence": pl.lit(CONFIDENCE),
                "confidence": pl.lit(normalize_confidence(SOURCE_ID, CONFIDENCE)),
            },
            consumed={"name", "author"},
        )
    )
    interim = Path(interim_dir) / SOURCE_ID
    write_parquet(gems, interim / "gems.parquet")
    write_parquet(uploads, interim / "uploads_per_day.parquet")
    write_parquet(events, Path(processed_dir) / f"events_{SOURCE_ID}.parquet")
    return events
