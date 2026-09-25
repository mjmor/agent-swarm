import pytest

from agent_swarm.paths import RAW_DIR


def pytest_collection_modifyitems(items):
    if RAW_DIR.exists():
        return
    skip = pytest.mark.skip(reason=f"no downloaded data at {RAW_DIR}")
    for item in items:
        if "realdata" in item.keywords:
            item.add_marker(skip)
