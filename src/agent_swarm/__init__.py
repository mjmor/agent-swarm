import argparse
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCES_TOML = REPO_ROOT / "sources" / "sources.toml"
MANIFEST = REPO_ROOT / "sources" / "manifest.json"
RAW_DIR = REPO_ROOT / "data" / "raw"


def main() -> None:
    parser = argparse.ArgumentParser(prog="agent-swarm")
    sub = parser.add_subparsers(dest="command", required=True)
    acquire = sub.add_parser("acquire", help="download pinned source artifacts into data/raw")
    acquire.add_argument("--only", nargs="*", help="source ids to fetch (default: all)")
    args = parser.parse_args()

    if args.command == "acquire":
        from agent_swarm.acquire import fetch_all, load_sources

        manifest = fetch_all(
            load_sources(SOURCES_TOML),
            RAW_DIR,
            MANIFEST,
            only=set(args.only) if args.only else None,
        )
        failed = [k for k, v in manifest.items() if v["status"] != "ok"]
        if failed:
            print(f"{len(failed)} artifact(s) failed: {', '.join(failed)}")
