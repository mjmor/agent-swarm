import gzip
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from agent_swarm.acquire import Artifact, fetch_all, load_sources

SOURCES_TOML = """
[[source]]
id = "demo"
title = "Demo report"
report_url = "https://demo.example/report"

[[source.artifact]]
name = "report.html"
url = "https://demo.example/report"
kind = "report_html"

[[source.artifact]]
name = "data.jsonl.gz"
url = "https://demo.example/data.jsonl.gz"
kind = "dataset"

[[source]]
id = "broken"
title = "Broken report"
report_url = "https://broken.example/"

[[source.artifact]]
name = "dump.tar.gz"
url = "https://broken.example/dump.tar.gz"
kind = "dataset"
"""

PAYLOADS = {
    "https://demo.example/report": b"<html>report</html>",
    "https://demo.example/data.jsonl.gz": gzip.compress(b'{"a": 1}\n'),
}


def mock_client(calls: list[str]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        body = PAYLOADS.get(str(request.url))
        if body is None:
            return httpx.Response(503, text="unavailable")
        return httpx.Response(200, content=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def sources_file(tmp_path: Path) -> Path:
    p = tmp_path / "sources.toml"
    p.write_text(SOURCES_TOML)
    return p


def test_load_sources_flattens_artifacts(sources_file: Path):
    artifacts = load_sources(sources_file)
    assert [a.key for a in artifacts] == [
        "demo/report.html",
        "demo/data.jsonl.gz",
        "broken/dump.tar.gz",
    ]
    assert artifacts[1] == Artifact(
        source_id="demo",
        name="data.jsonl.gz",
        url="https://demo.example/data.jsonl.gz",
        kind="dataset",
    )


def test_fetch_all_writes_files_and_manifest(sources_file: Path, tmp_path: Path):
    raw = tmp_path / "raw"
    manifest_path = tmp_path / "manifest.json"
    calls: list[str] = []

    manifest = fetch_all(load_sources(sources_file), raw, manifest_path, client=mock_client(calls))

    body = PAYLOADS["https://demo.example/data.jsonl.gz"]
    assert (raw / "demo" / "data.jsonl.gz").read_bytes() == body
    entry = manifest["demo/data.jsonl.gz"]
    assert entry["status"] == "ok"
    assert entry["sha256"] == hashlib.sha256(body).hexdigest()
    assert entry["bytes"] == len(body)
    assert entry["url"] == "https://demo.example/data.jsonl.gz"
    assert "fetched_at" in entry
    assert json.loads(manifest_path.read_text()) == manifest


def test_fetch_all_records_failures_without_aborting(sources_file: Path, tmp_path: Path):
    manifest = fetch_all(
        load_sources(sources_file), tmp_path / "raw", tmp_path / "m.json", client=mock_client([])
    )
    assert manifest["broken/dump.tar.gz"]["status"] == "error"
    assert "503" in manifest["broken/dump.tar.gz"]["error"]
    assert not (tmp_path / "raw" / "broken" / "dump.tar.gz").exists()
    assert manifest["demo/report.html"]["status"] == "ok"


def test_fetch_all_skips_verified_files(sources_file: Path, tmp_path: Path):
    raw, manifest_path = tmp_path / "raw", tmp_path / "m.json"
    fetch_all(load_sources(sources_file), raw, manifest_path, client=mock_client([]))

    calls: list[str] = []
    fetch_all(load_sources(sources_file), raw, manifest_path, client=mock_client(calls))
    assert calls == ["https://broken.example/dump.tar.gz"]


def test_fetch_all_refetches_when_file_changed_on_disk(sources_file: Path, tmp_path: Path):
    raw, manifest_path = tmp_path / "raw", tmp_path / "m.json"
    fetch_all(load_sources(sources_file), raw, manifest_path, client=mock_client([]))
    (raw / "demo" / "report.html").write_bytes(b"tampered")

    calls: list[str] = []
    fetch_all(load_sources(sources_file), raw, manifest_path, client=mock_client(calls))
    assert "https://demo.example/report" in calls
    assert (raw / "demo" / "report.html").read_bytes() == PAYLOADS["https://demo.example/report"]


def test_fetch_all_only_filters_by_source(sources_file: Path, tmp_path: Path):
    calls: list[str] = []
    fetch_all(
        load_sources(sources_file),
        tmp_path / "raw",
        tmp_path / "m.json",
        client=mock_client(calls),
        only={"broken"},
    )
    assert calls == ["https://broken.example/dump.tar.gz"]


MANUAL_TOML = """
[[source]]
id = "demo"
title = "Demo report"
report_url = "https://demo.example/report"

[[source.artifact]]
name = "report.html"
url = "https://demo.example/report"
kind = "report_html"

[[source.artifact]]
name = "blog.pdf"
url = "https://blocked.example/blog"
kind = "reference"
manual = true
"""


@pytest.fixture
def manual_sources(tmp_path: Path) -> Path:
    p = tmp_path / "manual.toml"
    p.write_text(MANUAL_TOML)
    return p


def test_load_sources_reads_manual_flag(manual_sources: Path):
    assert [a.manual for a in load_sources(manual_sources)] == [False, True]


def test_manual_artifact_is_registered_not_downloaded(manual_sources: Path, tmp_path: Path):
    raw = tmp_path / "raw"
    (raw / "demo").mkdir(parents=True)
    (raw / "demo" / "blog.pdf").write_bytes(b"%PDF-1.4 blog")
    calls: list[str] = []

    manifest = fetch_all(
        load_sources(manual_sources), raw, tmp_path / "m.json", client=mock_client(calls)
    )

    assert "https://blocked.example/blog" not in calls
    entry = manifest["demo/blog.pdf"]
    assert entry["status"] == "ok"
    assert entry["origin"] == "manual"
    assert entry["sha256"] == hashlib.sha256(b"%PDF-1.4 blog").hexdigest()


def test_missing_manual_artifact_is_reported_not_fetched(manual_sources: Path, tmp_path: Path):
    calls: list[str] = []
    manifest = fetch_all(
        load_sources(manual_sources),
        tmp_path / "raw",
        tmp_path / "m.json",
        client=mock_client(calls),
    )
    assert "https://blocked.example/blog" not in calls
    entry = manifest["demo/blog.pdf"]
    assert entry["status"] == "missing_manual"
    assert "raw/demo/blog.pdf" in entry["error"]


def test_manual_artifact_duplicating_another_is_flagged(manual_sources: Path, tmp_path: Path):
    raw = tmp_path / "raw"
    (raw / "demo").mkdir(parents=True)
    (raw / "demo" / "blog.pdf").write_bytes(PAYLOADS["https://demo.example/report"])

    manifest = fetch_all(
        load_sources(manual_sources), raw, tmp_path / "m.json", client=mock_client([])
    )

    assert manifest["demo/blog.pdf"]["status"] == "duplicate"
    assert manifest["demo/blog.pdf"]["duplicate_of"] == "demo/report.html"


def test_full_run_prunes_artifacts_no_longer_in_sources(sources_file: Path, tmp_path: Path):
    manifest_path = tmp_path / "m.json"
    manifest_path.write_text(json.dumps({"gone/old.html": {"status": "error"}}))
    manifest = fetch_all(
        load_sources(sources_file), tmp_path / "raw", manifest_path, client=mock_client([])
    )
    assert "gone/old.html" not in manifest
