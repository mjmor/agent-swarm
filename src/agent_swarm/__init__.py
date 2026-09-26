import argparse

from agent_swarm.paths import INTERIM_DIR, MANIFEST, PROCESSED_DIR, RAW_DIR, SOURCES_TOML


def main(argv: list[str] | None = None) -> None:
    from agent_swarm.extract import EXTRACTORS

    parser = argparse.ArgumentParser(prog="agent-swarm")
    sub = parser.add_subparsers(dest="command", required=True)
    acquire = sub.add_parser("acquire", help="download pinned source artifacts into data/raw")
    acquire.add_argument("--only", nargs="*", help="source ids to fetch (default: all)")
    extract = sub.add_parser("extract", help="raw -> interim parquet + processed events per source")
    extract.add_argument("sources", nargs="+", choices=sorted(EXTRACTORS))
    args = parser.parse_args(argv)

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

    if args.command == "extract":
        for source in args.sources:
            events = EXTRACTORS[source](RAW_DIR, INTERIM_DIR, PROCESSED_DIR)
            print(f"[{source}] {events.height:,} events")
