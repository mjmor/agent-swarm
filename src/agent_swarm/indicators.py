import json
import re
import unicodedata
from pathlib import Path
from urllib.parse import unquote, urlsplit

import polars as pl

from agent_swarm.extract.rubyhack import name_timestamp
from agent_swarm.reference import SERVICE_SEEDS
from agent_swarm.textutil import extract_urls, is_placeholder, nfkc_handle, normalize_host

INDICATOR_SCHEMA: dict[str, pl.DataType] = {
    "event_id": pl.String(),
    "incident_id": pl.String(),
    "indicator_type": pl.String(),
    "value": pl.String(),
    "value_norm": pl.String(),
    "extractor": pl.String(),
}
COVERAGE_CATEGORIES = {
    "relays": "relay_service",
    "shorteners": "url_shortener",
    "pastebins": "pastebin",
    "wikis": "agent_venue",
    "documents": "agent_venue",
    "hosting": "agent_venue",
    "new_discord_posting_surface": "agent_venue",
}
NAME_KEYWORDS = {
    "agent",
    "openai",
    "oai",
    "research",
    "researcher",
    "helper",
    "test",
    "proxy",
    "fetch",
    "relay",
    "bot",
    "link",
}
NAME_TOKEN_RE = re.compile(r"OPENAI|OAI|[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")
NESTED_RE = re.compile(r"https?(?::|%3a|%253a)(?://|%2f%2f|%252f%252f)", re.I)
WITHHELD_RE = re.compile(r"\[operational URL omitted; host=([^;\]\s]+)")
TEXT_PLACEHOLDER_RE = re.compile(r"\[(SHORTENER CODE|SERVICE HOST|SERVICE \d+ URL)[^\]]*\]")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
GEM_URL_RE = re.compile(r"rubygems\.org/gems/([A-Za-z0-9_.-]+)")
FILE_SUFFIX_RE = re.compile(r"\.(pdf|json|csv|xlsx?|html?|zip|txt)$", re.I)
HEURISTIC_RELAY_RE = re.compile(
    r"cors|proxy.*\.(workers\.dev|vercel\.app|herokuapp\.com|netlify\.app|glitch\.me|deno\.dev)$"
)
MAX_NESTING = 4


def _clean(name: str) -> str:
    folded = unicodedata.normalize("NFKC", name)
    return "".join(ch for ch in folded if unicodedata.category(ch) != "Cf").strip()


def naming_shape(name: str) -> str:
    marked = re.sub(r"(?i)oai", " OAI ", re.sub(r"(?i)openai", " OPENAI ", _clean(name)))
    shape: list[str] = []
    for token in NAME_TOKEN_RE.findall(marked):
        low = token.lower()
        if token.isdigit():
            if len(token) == 4 and token[:2] in ("19", "20"):
                tag = "{year}"
            elif len(token) == 10 and token.startswith("17"):
                tag = "{unixts}"
            else:
                tag = f"{{{len(token)}d}}"
        elif low in NAME_KEYWORDS:
            tag = low
        else:
            tag = "{w}"
        if not (tag == "{w}" and shape and shape[-1] == "{w}"):
            shape.append(tag)
    return "·".join(shape)


def service_catalog(interim_dir: Path) -> dict[str, str]:
    catalog: dict[str, str] = {}
    for name in ("site_coverage", "coverage_gaps"):
        path = Path(interim_dir) / "collusion-wiki" / f"{name}.parquet"
        if not path.exists():
            continue
        for host, category in pl.read_parquet(path).select("host", "category").iter_rows():
            norm = normalize_host(host or "")
            if norm and category in COVERAGE_CATEGORIES:
                catalog.setdefault(norm, COVERAGE_CATEGORIES[category])
    return catalog | SERVICE_SEEDS


def _classify(host: str, catalog: dict[str, str]) -> str | None:
    parts = host.split(".")
    for i in range(len(parts) - 1):
        if (hit := catalog.get(".".join(parts[i:]))) is not None:
            return hit
    return None


def _url_indicators(url: str, catalog: dict[str, str], depth: int = 0) -> set[tuple]:
    host = normalize_host(url)
    if host is None:
        return set()
    out: set[tuple] = set()
    try:
        rest = urlsplit(url)
        tail = (rest.path or "") + ("?" + rest.query if rest.query else "")
    except ValueError:
        tail = ""
    nested = NESTED_RE.search(tail) if depth < MAX_NESTING else None
    kind = _classify(host, catalog)
    if kind:
        out.add((kind, url, host, "url.catalog"))
    elif nested:
        out.add(("relay_service", url, host, "url.nested_relay"))
    elif HEURISTIC_RELAY_RE.search(host):
        out.add(("relay_service", url, host, "url.heuristic"))
    elif depth > 0:
        out.add(("task_target_domain", url, host, "url.nested_target"))
    else:
        out.add(("linked_host", url, host, "url.host"))
    if m := GEM_URL_RE.search(url):
        out.add(("gem_name", url, m.group(1).lower(), "url.rubygems_gem"))
    if nested:
        inner_raw = tail[nested.start() :]
        for _ in range(3):
            decoded = unquote(inner_raw)
            if decoded == inner_raw:
                break
            inner_raw = decoded
        for inner in extract_urls(inner_raw)[:1]:
            out |= _url_indicators(inner, catalog, depth + 1)
    return out


def _name_indicators(name: str) -> set[tuple]:
    out = {("naming_pattern", name, naming_shape(name), "name.shape")}
    low = _clean(name).lower()
    if "openai" in low:
        out.add(("oai_token", name, "openai", "name.oai"))
    elif "oai" in low:
        out.add(("oai_token", name, "oai", "name.oai"))
    if (ts := name_timestamp(name)) is not None:
        out.add(("unix_ts_in_name", name, str(ts), "name.unix_ts"))
    return out


def _marker_domains(methods: pl.DataFrame | None) -> dict[str, list[str]]:
    if methods is None:
        return {}
    domains: dict[str, list[str]] = {}
    for label, markers in methods.select("label", "markers").iter_rows():
        for marker in markers or []:
            host = normalize_host(marker)
            if host and not FILE_SUFFIX_RE.search(marker):
                domains.setdefault(label, []).append(host)
    return domains


def _event_indicators(row: dict, catalog: dict[str, str], markers: dict) -> set[tuple]:
    found: set[tuple] = set()
    text = row["text"] or ""
    handle = row["actor_handle"]
    if handle and not is_placeholder(handle):
        found.add(("actor_handle", handle, nfkc_handle(handle), "event.actor_handle"))
        found |= _name_indicators(handle)
    locator = row["venue_locator"] or ""
    if row["source_id"] == "rubyhack" and locator:
        found.add(("gem_name", locator, locator.lower(), "event.gem"))
        found |= _name_indicators(locator)
    elif row["event_type"].startswith("wiki_") and "/" in locator:
        found |= _name_indicators(locator.rsplit("/", 1)[-1])
    if row["network_ip16"]:
        found.add(("ip16", row["network_ip16"], row["network_ip16"], "event.ip16"))
    for url in row["urls"] or []:
        found |= _url_indicators(url, catalog)
    if "[" in text:
        for host in WITHHELD_RE.findall(text):
            if norm := normalize_host(host):
                kind = _classify(norm, catalog) or "linked_host"
                found.add((kind, host, norm, "url.withheld_host"))
        for m in TEXT_PLACEHOLDER_RE.finditer(text):
            kind = re.sub(r"\s+\d+\s+", "_", m.group(1)).lower().replace(" ", "_")
            kind = "service_url" if kind.startswith("service_") and kind.endswith("url") else kind
            found.add(("redacted_placeholder", m.group(0), kind, "text.placeholder"))
    if "@" in text:
        for email in EMAIL_RE.findall(text):
            found.add(("email", email, email.lower(), "text.email"))
    if row["source_id"] == "transluce-urlquery" and row["extra"]:
        source = json.loads(row["extra"]).get("data_source")
        for host in markers.get(source, []):
            found.add(("task_target_domain", source, host, "transluce.data_source"))
    return found


def extract_indicators(
    events: pl.DataFrame, catalog: dict[str, str], methods: pl.DataFrame | None
) -> pl.DataFrame:
    markers = _marker_domains(methods)
    cols = [
        "event_id",
        "incident_id",
        "source_id",
        "event_type",
        "text",
        "actor_handle",
        "venue_locator",
        "network_ip16",
        "urls",
        "extra",
    ]
    rows = []
    for row in events.select(cols).iter_rows(named=True):
        for kind, value, norm, extractor in sorted(_event_indicators(row, catalog, markers)):
            rows.append((row["event_id"], row["incident_id"], kind, value, norm, extractor))
    return pl.DataFrame(rows, schema=INDICATOR_SCHEMA, orient="row")


def build_indicators(interim_dir: Path, processed_dir: Path) -> pl.DataFrame:
    events = pl.read_parquet(Path(processed_dir) / "events.parquet")
    methods_path = Path(interim_dir) / "transluce-urlquery" / "methods.parquet"
    methods = pl.read_parquet(methods_path) if methods_path.exists() else None
    out = extract_indicators(events, service_catalog(interim_dir), methods)
    out.write_parquet(Path(processed_dir) / "indicators.parquet")
    return out
