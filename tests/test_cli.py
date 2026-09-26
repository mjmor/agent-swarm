import polars as pl
import pytest

import agent_swarm
from agent_swarm.extract import EXTRACTORS


def test_extract_registry_lists_sources():
    assert "collusion-wiki" in EXTRACTORS


def test_extract_rejects_unknown_source():
    with pytest.raises(SystemExit):
        agent_swarm.main(["extract", "nope"])


def test_extract_runs_registered_extractor(monkeypatch):
    calls = []

    def fake(*dirs):
        calls.append(dirs)
        return pl.DataFrame()

    monkeypatch.setitem(EXTRACTORS, "collusion-wiki", fake)
    agent_swarm.main(["extract", "collusion-wiki"])
    assert len(calls) == 1 and len(calls[0]) == 3
