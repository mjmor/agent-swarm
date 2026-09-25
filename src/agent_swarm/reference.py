from datetime import date

import polars as pl

INCIDENTS = pl.DataFrame(
    [
        {
            "incident_id": "wiki-collusion",
            "source_id": "collusion-wiki",
            "title": "Discovery of a new OpenAI agent message board",
            "publisher": "Nightingale Collective (Von Arx, Byrd, Kitts, Larsen)",
            "report_url": "https://collusion.wiki/",
            "published": date(2026, 9, 4),
            "window_start": date(2026, 5, 24),
            "window_end": date(2026, 6, 22),
            "attributed_to": "OpenAI (publisher inference)",
            "victims": ["prowiki.org", "wikiservice.at"],
        },
        {
            "incident_id": "rubygems-attack",
            "source_id": "rubyhack",
            "title": "OpenAI agents carried out an undisclosed cyber-attack on RubyGems",
            "publisher": "Kitts, Larsen, Von Arx",
            "report_url": "https://rubyhack.ai/",
            "published": date(2026, 9, 11),
            "window_start": date(2026, 5, 5),
            "window_end": date(2026, 6, 18),
            "attributed_to": "OpenAI (publisher inference)",
            "victims": ["rubygems.org", "rubydoc.info"],
        },
        {
            "incident_id": "urlquery-relay",
            "source_id": "transluce-urlquery",
            "title": "Early rogue AI agent activity and attempts to hack found on urlquery.net",
            "publisher": "Transluce (with Corridor, MIT, AIUC)",
            "report_url": "https://transluce.org/agent-activity",
            "published": date(2026, 9, 23),
            "window_start": date(2026, 3, 6),
            "window_end": date(2026, 9, 16),
            "attributed_to": "OpenAI (publisher inference; AIHW activity acknowledged by OpenAI)",
            "victims": ["urlquery.net", "aihw.gov.au", "datausa.io", "nmdigital.unm.edu"],
        },
        {
            "incident_id": "hf-hack",
            "source_id": "swarmtraces",
            "title": "Revealing the details of how OpenAI agents hacked Hugging Face",
            "publisher": "Forman, Kharlov, Tom, Ladish, Kitts, Byrd, McKenzie, Piecha",
            "report_url": "https://swarmtraces.org/",
            "published": date(2026, 9, 25),
            "window_start": date(2026, 7, 9),
            "window_end": date(2026, 7, 13),
            "attributed_to": "OpenAI (vendor-confirmed)",
            "victims": ["huggingface.co"],
        },
    ]
)

SOURCE_INCIDENT = dict(zip(INCIDENTS["source_id"], INCIDENTS["incident_id"], strict=True))

CONFIDENCE_MAP: dict[str, dict[str | None, str]] = {
    "transluce-urlquery": {
        "significant": "high",
        "suggestive": "medium",
        "background": "low",
        "review_required": "unrated",
    },
    "collusion-wiki": {"not_independently_authenticated": "medium"},
    "swarmtraces": {"vendor_confirmed_match": "high"},
    "rubyhack": {"publisher_listed": "medium"},
}


def normalize_confidence(source_id: str, raw: str | None) -> str:
    return CONFIDENCE_MAP[source_id].get(raw, "unrated")
