import tomllib

import pytest

from agent_swarm.paths import SOURCES_TOML
from agent_swarm.reference import INCIDENTS, normalize_confidence


def test_incidents_cover_the_four_reports():
    assert sorted(INCIDENTS["incident_id"]) == [
        "hf-hack",
        "rubygems-attack",
        "urlquery-relay",
        "wiki-collusion",
    ]


def test_incident_sources_exist_in_sources_toml():
    ids = {s["id"] for s in tomllib.loads(SOURCES_TOML.read_text())["source"]}
    assert set(INCIDENTS["source_id"]) <= ids


def test_incident_windows_are_ordered():
    assert (INCIDENTS["window_start"] <= INCIDENTS["window_end"]).all()
    assert (INCIDENTS["window_end"] <= INCIDENTS["published"]).all()


@pytest.mark.parametrize(
    ("source_id", "raw", "expected"),
    [
        ("transluce-urlquery", "significant", "high"),
        ("transluce-urlquery", "suggestive", "medium"),
        ("transluce-urlquery", "background", "low"),
        ("transluce-urlquery", "review_required", "unrated"),
        ("transluce-urlquery", None, "unrated"),
        ("collusion-wiki", "not_independently_authenticated", "medium"),
        ("collusion-wiki", "publisher_selected_agent_content", "medium"),
        ("collusion-wiki", "task_or_exchange_signal", "medium"),
        ("collusion-wiki", "publisher_selected_plus_task_or_exchange_signal", "medium"),
        ("collusion-wiki", "linked_artifact", "medium"),
        ("collusion-wiki", "prior_reviewed_task_reference", "medium"),
        ("swarmtraces", "vendor_confirmed_match", "high"),
        ("rubyhack", "publisher_listed", "medium"),
    ],
)
def test_normalize_confidence(source_id, raw, expected):
    assert normalize_confidence(source_id, raw) == expected


def test_normalize_confidence_never_upgrades_unknown_values():
    assert normalize_confidence("transluce-urlquery", "definitely_an_agent") == "unrated"


def test_normalize_confidence_rejects_unknown_source():
    with pytest.raises(KeyError):
        normalize_confidence("nope", "significant")
