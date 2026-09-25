import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("AGENT_SWARM_DATA_DIR", REPO_ROOT / "data"))
RAW_DIR = DATA_DIR / "raw"
INTERIM_DIR = DATA_DIR / "interim"
PROCESSED_DIR = DATA_DIR / "processed"
SOURCES_TOML = REPO_ROOT / "sources" / "sources.toml"
MANIFEST = REPO_ROOT / "sources" / "manifest.json"
FIGURES_DIR = REPO_ROOT / "reports" / "figures"
