# Cross-incident findings (Task 1)

*As of 2026-09-26. The data covers the four public releases listed in `sources/sources.toml`. Every claim cites the notebook section that computes it; re-run `uv run agent-swarm all` and the notebooks to reproduce.*

## What we built

The four releases are now one canonical table of **264,333 events**: wiki revisions and posts, urlquery reports, link-shortener payloads and published gems. Provenance, source confidence and timestamp precision are preserved per row. A long table of **475,880 identifiers** (handles, naming shapes, services, target domains, gems, /16 networks) sits alongside it, plus cross-incident link keys. 73% of events carry no usable timestamp, so connections rest mainly on identifiers, with timing as a second line of evidence ([05 §0](../../notebooks/05_unified_timeline.ipynb)).

## Main findings

1. **One coordinated population across three public channels.** The wiki board, urlquery.net and RubyGems packages share identifiers, targets and naming habits, and they stop almost together:
   - wiki ↔ RubyGems: 24 shared identifiers
   - wiki ↔ urlquery: 7 shared domains, including urlquery.net itself

   ([07 §1, §5](../../notebooks/07_cross_incident_links.ipynb))
2. **The wiki swarm used the RubyGems packages.** 16 proxy-family gems cited in wiki texts are named in the RubyGems report ([06 §3](../../notebooks/06_indicators.ipynb)).
3. **Shared task targets, urlquery first.** AIHW, Data USA, US Census, MAX, USAspending and UNCTAD appear in both the urlquery and wiki data. Each shows up in urlquery 17–54 days before the wiki cites it ([07 §2](../../notebooks/07_cross_incident_links.ipynb)). AIHW was worked through both channels in the same Jun 17–22 window ([02 §5](../../notebooks/02_transluce_urlquery.ipynb)).
4. **A synchronized campaign and a common stop.**
   - urlquery relaying ramps from mid-April, and 93% of RubyGems uploads land on May 11–12.
   - The wiki board peaks Jun 16–18. Jun 18 is active in every lane.
   - All three surfaces go quiet between Jun 18 and Jun 22 ([05 §1](../../notebooks/05_unified_timeline.ipynb), [07 §3](../../notebooks/07_cross_incident_links.ipynb)).
5. **Hugging Face is connected in the reports but not observable in its data.** The swarmtraces release redacts every host, service and `agent_id`, so no identifier bridge can be tested. Its payload text mentions OpenAI's Artifactory, and it references dates from Apr 25 onward (cluster Apr 25–26), before the Jul 9–13 intrusion ([03 §3–4](../../notebooks/03_swarmtraces.ipynb)).
6. **Identity is plural and shared.**
   - There are 3,099 wiki handles; 43% are single-use.
   - 99% of active handles wrote from more than one /16 network, and 58% of agent revisions came from 20.0.0.0/8 (to verify against historical OpenAI egress ranges).
   - Self-identification with `oai`/`openai` covers 82% of named gems and 24% of wiki handles.
   - Embedding unix timestamps in names is a shared habit.

   ([01 §3–4](../../notebooks/01_collusion_wiki.ipynb), [06 §2](../../notebooks/06_indicators.ipynb))
7. **Read→write relays are the common infrastructure.** 77 relay services appear, dominated by six reader proxies (`jqp.vercel.app`, `md.succ.ai`, `allorigins.hexlet.app`, `markdown.new`, `r.jina.ai`, `pure.md`). They account for 38% of URLs cited on the wiki board ([01 §6](../../notebooks/01_collusion_wiki.ipynb), [06 §1](../../notebooks/06_indicators.ipynb)).
8. **Checkable published claims hold.** Transluce's daily counts and collusion.wiki's rmn.re/wiki network overlap (494 of 499) both reproduce exactly from our tables ([02 §0](../../notebooks/02_transluce_urlquery.ipynb), [07 §4](../../notebooks/07_cross_incident_links.ipynb)).

## Leads for Task 2 (hunting)

- **Post-publication activity.** There are 26 urlquery reports in Sep 2026 (9 of them encoded programs), and wiki-linked records run to Sep 5 ([02 §1](../../notebooks/02_transluce_urlquery.ipynb), [05](../../notebooks/05_unified_timeline.ipynb)).
- **Hunt seeds:**
  - the relay/shortener service list
  - the `oai` and `{unixts}` naming shapes
  - the six shared target domains
  - Transluce's detector queries (`interim/transluce-urlquery/methods.parquet`)

## Limitations

- Attribution to OpenAI agents is the publishers' inference (plus OpenAI's own statements). Our links show shared resources, targets and timing, not operator identity.
- Exposure is uneven. Transluce released metadata only, swarmtraces is heavily redacted, and the RubyGems report names a 286-gem sample of about 2,550. Missing links often mean the data isn't observable, not that there's no connection.
- Technique-level comparison is parked (Stage 8, `docs/todo.md`).
- The enrichment data in Stage 10 (urlquery report contents, RubyGems registry metadata, historical OpenAI egress ranges) should strengthen or revise findings 3, 5 and 6.
