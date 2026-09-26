# Stage 10: enrichment data, cursory findings and integration plan

*Status: **proposed, awaiting owner review** (2026-09-26). The fetching code is in PR #10. Nothing below has been built beyond the cursory checks.*

## What was fetched

| Enrichment | Command / source | Result | Where |
|---|---|---|---|
| RubyGems live API (gem + versions) for the 349 gems named in the report or cited in wiki texts | `enrich rubygems` | **698/698 → 404**: every gem is gone from the live registry | `data/raw/enrich-rubygems/` |
| RubyGems public DB dump, 2026-07-27 (648 MB, plain SQL) | `acquire --only rubygems-dump` | Downloaded and sha-pinned. **All dumps before 2026-07-27 are in S3 Glacier** (not publicly retrievable), and this one expires 2026-10-21 per the bucket's lifecycle rule | `data/raw/rubygems-dump/` |
| urlquery.net report JSON, stratified sample (25 per method class × month, plus every report from July on) | `enrich urlquery-sample` | 434/434 → 200 at 1 req/s. That's 1.1% of the 38,160 reports | `data/raw/enrich-urlquery-sample/` |
| Wayback snapshots of OpenAI egress lists (`chatgpt-user.json`, `gptbot.json`, `searchbot.json`) | `enrich wayback` | 51 fetched, 38 parse as JSON (28 chatgpt-user 2025-01 → 2026-09, 10 gptbot → 2026-04). The `searchbot.json` CDX query was unavailable | `data/raw/enrich-wayback/` |

## Cursory findings (aggregate only)

1. **The RubyGems campaign is recoverable at package level.**
   - All 349 named gems are still in the 2026-07-27 dump: 378 version rows, 288 already yanked, with `created_at` timestamps from May 10 to Jun 18 2026.
   - The 45 accounts that pushed them (`pusher_id`) pushed **1,106 distinct gems in May–June, 757 of them never named in any public source**. 92% of those were yanked.
   - Their per-day profile reproduces the published registry curve (May 11–12, May 26, and **exactly 84 on Jun 18**, matching `uploads-per-day.csv`).
   - The gemspec `authors` field is mostly throwaway values (`x`, `a`, `Reader`, `R`) plus the 15 `oai`.
   - About 1,450 of the ~2,550 published new names are still missing. They were likely removed on May 13, before any retrievable dump.
2. **urlquery submissions came through cloud infrastructure, largely aimed at the wiki's relays.**
   - Submitter networks in the sample: Cloudflare (253), Amazon/AWS (94), DigitalOcean (19). The country code is withheld (`zz`) on 253.
   - **38% of sampled submissions target a known relay** (httpbin.org 96, r.jina.ai, markdown.new, allorigins, plus httpbun.com, pie.dev and htmlpreview.github.io, which aren't in our catalog yet). That's a new identifier bridge to the wiki swarm, invisible in the metadata-only release.
   - Only 1 of 423 submitter /16s matches a wiki-edit /16, so the two channels used different egress.
   - **Every one of the 388 id-bearing reports carries the same urlquery account id**, and there are only 2 scanner exit-node ids. Without a control sample we can't tell whether that id means "one shared account" or "anonymous submitter".
3. **OpenAI egress overlap is suggestive but weak at /16 resolution.** The 11 snapshots from Mar–Jul 2026 cover 131 distinct /16s. 31% of agent wiki revisions (40 of 190 wiki /16s) fall in one of them. Large shared cloud /16s (e.g. 20.0.0.0/8, much of it Microsoft Azure) make this weak without a null baseline. The urlquery reports carry **full submitter IPs**, which allow an exact test against the /28 prefixes.

## Proposed integration (for review)

Each step would be its own stacked PR with tests, following the existing extract → build → indicators → link pattern.

**10a. RubyGems dump extractor** (new source `rubygems-dump`):
- Stream-parse the `rubygems` and `versions` COPY blocks, with no database needed. Keep (i) the 349 named gems and (ii) every gem pushed by the same accounts in May–June 2026.
- Emit one `package_published` event per version: `ts_utc = created_at` (second precision) and `actor_handle = rubygems-user:<pusher_id>`. Gemspec `summary`/`description` go in `text` (metadata only; the dump contains no gem code).
- Account-expanded gems get `source_confidence = inferred_same_pusher` → **low**. Named gems stay **medium**.
- Reconcile row-level daily counts against the published `uploads-per-day.csv`.
- Re-run indicators and linkage: new gem names and shapes, and whether wiki texts cite any of the 757 newly recovered gems.

**10b. urlquery report extractor** (new source `urlquery-reports`, joined to the Transluce events by `report_id`):
- Interim table of per-report metadata: submitted host, contacted hosts, submitter /16 + ASN, exit node, account-id presence.
- New indicators: submitted-relay hosts and contacted hosts. Canonical `network_ip16` is filled from submitter /16s.
- Submitted URLs are stored but only aggregated in notebooks, never printed; they can embed encoded programs.

**10c. Egress matching:**
- Snapshot table of OpenAI prefixes with validity windows.
- Exact /28 matching for urlquery submitter IPs; /16 matching for the wiki, with a **null baseline** (random /16s from the same ASNs).
- Retry `searchbot.json`, and diagnose the 13 unparsable snapshots.

**10d. Refresh:** re-run build → indicators → link; add `notebooks/08_enrichment.ipynb`; update `docs/findings/cross-incident.md` in its own PR.

## Decisions needed from the owner

1. **Full urlquery crawl?** All 38,160 reports at 1 req/s ≈ 10.6 h (resumable). The terms allow personal, non-commercial research but forbid redistribution; we'd publish aggregates only. The alternative is to stay with the 434-report sample.
2. **Control sample:** fetch ~100 random public urlquery reports *not* attributed to agents, to interpret the shared account id and exit nodes.
3. **RubyGems `pusher_id`s** are numeric account ids from a public dump. Proposal: use them internally for grouping, and publish only counts or hashed ids.
4. **Pre-July RubyGems dumps** are in Glacier. Should we ask the RubyGems maintainers for the May/June dumps? That's outward-facing, so it's the owner's call. The 2026-07-27 dump itself expires from the bucket on 2026-10-21 (our copy is local and sha-pinned).
5. **Confidence for account-expanded gems:** proposed `low` (inferred through a shared pusher). `medium` is the alternative.
