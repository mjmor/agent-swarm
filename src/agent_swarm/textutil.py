import ipaddress
import re
import unicodedata
from urllib.parse import urlsplit

INVISIBLE = "".join(map(chr, (0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF)))
URL_RE = re.compile(rf"https?://[^\s<>\"'`{{}}|\\^\[\]{INVISIBLE}]+", re.IGNORECASE)
TRAILING_PUNCT = ".,;:!?'\""
PLACEHOLDER_RE = re.compile(
    r"\[(?:REDACTED|SHORTENER|SERVICE|API KEY|CREDENTIAL|REDIRECT|ENCODED|operational URL omitted)"
    r"[^\]]*\]"
)
REDACTION_RE = re.compile(r"\[REDACTED:([a-z_]+)")
UNTYPED_PLACEHOLDERS = {
    "api_key": re.compile(r"\[API KEY[^\]]*\]"),
    "credential": re.compile(r"\[CREDENTIAL[^\]]*\]"),
    "encoded_blob": re.compile(r"\[ENCODED BLOB[^\]]*\]"),
    "operational_url": re.compile(r"\[operational URL omitted[^\]]*\]"),
    "redirect_url": re.compile(r"\[REDIRECT URL[^\]]*\]"),
    "sensitive_content": re.compile(r"\[REDACTED SENSITIVE CONTENT\]"),
    "service": re.compile(r"\[SERVICE [^\]]*\]"),
    "shortener": re.compile(r"\[SHORTENER[^\]]*\]"),
}
LABEL_RE = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")


def _trim(url: str) -> str:
    while url:
        if url[-1] in TRAILING_PUNCT:
            url = url[:-1]
        elif url[-1] == ")" and url.count(")") > url.count("("):
            url = url[:-1]
        else:
            break
    return url


def extract_urls(text: str | None) -> list[str]:
    if not text:
        return []
    seen: dict[str, None] = {}
    for match in URL_RE.findall(text):
        url = _trim(match)
        if len(url) > len("http://"):
            seen.setdefault(url, None)
    return list(seen)


def is_placeholder(s: str) -> bool:
    return bool(PLACEHOLDER_RE.search(s))


def normalize_host(url_or_host: str) -> str | None:
    if not url_or_host or is_placeholder(url_or_host):
        return None
    candidate = url_or_host if "://" in url_or_host else f"http://{url_or_host}"
    try:
        host = urlsplit(candidate).hostname
    except ValueError:
        return None
    if not host:
        return None
    host = host.rstrip(".").removeprefix("www.")
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        pass
    labels = host.split(".")
    if len(labels) < 2 or not all(LABEL_RE.match(label) for label in labels):
        return None
    return host


def redaction_types(text: str | None) -> list[str]:
    if not text or "[" not in text:
        return []
    found = set(REDACTION_RE.findall(text))
    found.update(kind for kind, pattern in UNTYPED_PLACEHOLDERS.items() if pattern.search(text))
    return sorted(found)


def nfkc_handle(s: str) -> str:
    folded = unicodedata.normalize("NFKC", s)
    visible = "".join(ch for ch in folded if unicodedata.category(ch) != "Cf")
    return visible.strip().casefold()
