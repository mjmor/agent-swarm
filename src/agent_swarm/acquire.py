import hashlib
import json
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

USER_AGENT = "agent-swarm-research/0.1 (+https://github.com/mjmor/agent-swarm)"


@dataclass(frozen=True)
class Artifact:
    source_id: str
    name: str
    url: str
    kind: str

    @property
    def key(self) -> str:
        return f"{self.source_id}/{self.name}"


def load_sources(path: Path) -> list[Artifact]:
    config = tomllib.loads(Path(path).read_text())
    return [
        Artifact(source_id=src["id"], name=a["name"], url=a["url"], kind=a["kind"])
        for src in config["source"]
        for a in src.get("artifact", [])
    ]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_verified(dest: Path, entry: dict | None) -> bool:
    return bool(
        entry and entry.get("status") == "ok" and dest.exists() and _sha256(dest) == entry["sha256"]
    )


def _download(client: httpx.Client, artifact: Artifact, dest: Path) -> dict:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    with client.stream("GET", artifact.url) as resp:
        resp.raise_for_status()
        with tmp.open("wb") as f:
            for chunk in resp.iter_bytes():
                f.write(chunk)
        content_type = resp.headers.get("content-type")
    tmp.replace(dest)
    return {
        "status": "ok",
        "sha256": _sha256(dest),
        "bytes": dest.stat().st_size,
        "content_type": content_type,
    }


def fetch_all(
    artifacts: list[Artifact],
    raw_dir: Path,
    manifest_path: Path,
    client: httpx.Client | None = None,
    only: set[str] | None = None,
) -> dict:
    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    client = client or httpx.Client(
        follow_redirects=True, timeout=120, headers={"User-Agent": USER_AGENT}
    )

    for artifact in artifacts:
        if only and artifact.source_id not in only:
            continue
        dest = Path(raw_dir) / artifact.source_id / artifact.name
        if _is_verified(dest, manifest.get(artifact.key)):
            continue
        base = {
            "url": artifact.url,
            "kind": artifact.kind,
            "fetched_at": datetime.now(UTC).isoformat(),
        }
        try:
            manifest[artifact.key] = base | _download(client, artifact, dest)
        except httpx.HTTPError as e:
            manifest[artifact.key] = base | {"status": "error", "error": f"{type(e).__name__}: {e}"}
        print(f"[{manifest[artifact.key]['status']:>5}] {artifact.key}")

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(dict(sorted(manifest.items())), indent=2) + "\n")
    return json.loads(manifest_path.read_text())
