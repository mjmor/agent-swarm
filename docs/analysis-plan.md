# Task 1 Plan: Systematize Incident Datasets and Link the Swarms

> **For agentic workers:** execute stage by stage (superpowers:executing-plans). Each stage is one stacked draft PR (see *Delivery* below). Steps use checkbox (`- [ ]`) syntax. Read `agents.md` first, especially the handling rules.

**Goal:** Turn the four published incident datasets into one canonical, provenance-preserving event table plus an indicator table. Use them to (a) show all incidents on one timeline and (b) find the identifiers and techniques that connect the incidents and can seed the Task 2 hunt.

**Architecture:** A layered ETL: `raw` (immutable downloads) → `interim` (one typed Parquet table per source file, source-shaped) → `processed` (canonical `events`, `indicators`, `timeline_daily`, `incidents`). All transformation logic lives in the `agent_swarm` package behind CLI subcommands and is unit-tested. Notebooks only *read* interim/processed outputs to explore, visualize and document. They never hold pipeline logic.

**Tech Stack:** Python 3.13, uv, polars (ETL), DuckDB (ad-hoc SQL in notebooks), Parquet/pyarrow, Altair + vl-convert (charts; follow the `dataviz` skill), networkx (Stage 8), pytest, ruff, JupyterLab.

**Spec:** the task statement in the owner's kickoff (see `agents.md` → *Purpose*) plus the dataset survey in `agents.md` → *Source datasets*.

## Global Constraints

- Data never enters git (`/data/` is gitignored). Commit code, tests, notebooks with outputs, and small aggregate figures only.
- Never execute, render or install anything from the datasets. Decoding happens only in pure-Python functions whose output is data.
- Every canonical row carries `source_id`, `incident_id`, `artifact` and `native_id`, so any row traces back to one line of one raw file.
- Fields that don't map to the canonical schema are preserved losslessly in `extra` (JSON string). Nothing is silently dropped.
- Source confidence and authorship caveats are carried verbatim (`source_confidence`) plus a normalized `confidence` of `high` | `medium` | `low` | `unrated`. Never upgrade confidence.
- Timestamps are UTC, and `ts_precision` ∈ {`second`, `day`, `none`}. Rows without timestamps are kept, not dropped.
- Source redaction placeholders (`[REDACTED:*]`, `[SHORTENER CODE n]`, `[SERVICE HOST n]`, `...`) are never treated as real indicator values.
- Tests use small **synthetic** fixtures committed under `tests/fixtures/`. Checks against the real data are marked `@pytest.mark.realdata` and skip when `data/raw` is absent (as in CI).
- Row-count conservation: every extract stage asserts that the input row count equals the output row count (or a documented, tested filter).
- Lint and format: `ruff check`, `ruff format --check`, line length 100. The CI workflow runs ruff + pytest.

## Review Focus

1. **Late-changing JSON types** (collusion `events.jsonl` has columns that are all-null for thousands of rows before holding strings). Expected: the reader infers the schema from the whole file, and the extract never raises. *Pinned in Stage 2.*
2. **Mixed timestamp precision.** Day-precision RubyGems counts and timestamp-less swarmtraces rows must not appear as midnight spikes or vanish from the timeline. *Pinned in Stages 5–6* (`timeline_daily` respects `ts_precision`; a `none` row count is reported separately).
3. **Double counting across overlapping files.** `full-wiki-logs.zip` duplicates the `.jsonl.gz` files. Transluce `all-reports.csv` = `reports.csv` ∪ `additional-cited-reports.csv`. Collusion `records.jsonl` re-publishes prowiki revision texts. Expected: each observation becomes one canonical event, and cross-file duplicates are linked via `dup_of_event_id`, not duplicated. *Pinned in Stages 2–3 and 6.*
4. **Redaction placeholders and obfuscated hosts leaking into indicators** (e.g. host `...` or `[SERVICE HOST 1]` counted as a relay). Expected: excluded or typed as `redacted_placeholder`. *Pinned in Stage 7.*
5. **Adversarial and odd text**: multi-MB bodies, non-UTF-8 (`body_encoding` ≠ `ascii`), HTML/JS, zero-width characters, look-alike Unicode in handles. Expected: extraction is total (never raises), handles are NFKC-normalized for matching while the original is kept, and nothing is evaluated. *Pinned in Stages 1 and 7.*

## Canonical data model (`data/processed/`)

### `incidents.parquet`: one row per incident (hand-curated in `src/agent_swarm/reference.py`)

| column | type | notes |
|---|---|---|
| `incident_id` | str | `wiki-collusion`, `urlquery-relay`, `hf-hack`, `rubygems-attack` |
| `source_id` | str | matches `sources/sources.toml` ids |
| `title`, `publisher`, `report_url` | str | |
| `published` | date | |
| `window_start`, `window_end` | date | as stated by the report |
| `attributed_to` | str | e.g. `OpenAI (publisher inference)`, `OpenAI (vendor-confirmed)` |
| `victims` | list[str] | e.g. `prowiki.org`, `rubygems.org`, `huggingface.co`, `aihw.gov.au` |

### `events.parquet`: one row per observed agent action or artifact

| column | type | notes |
|---|---|---|
| `event_id` | str | `sha1(source_id + artifact + native_id)[:16]`; stable across runs |
| `source_id`, `incident_id`, `artifact`, `native_id` | str | provenance (artifact = manifest key) |
| `event_type` | enum str | `wiki_save`, `wiki_delete`, `wiki_revert`, `wiki_probe`, `venue_post`, `url_scan`, `chain_payload`, `chain_decoded`, `chain_response`, `package_published` |
| `ts_utc` | datetime[us, UTC] | nullable |
| `ts_precision` | enum str | `second` / `day` / `none` |
| `ts_field` | str | which source field supplied `ts_utc` (e.g. `revision.time`) |
| `venue_host` | str | where the action happened (`prowiki.org`, `urlquery.net`, `rubygems.org`, `[shortener]`) |
| `venue_locator` | str | page / report id / gem name / chain id |
| `actor_handle` | str | self-chosen identifier as written (wiki label, gem author, `agent_id`), nullable |
| `actor_role` | enum str | `agent` / `moderator` / `unknown` |
| `network_ip16` | str | nullable; /16 only, never finer |
| `text` | str | body / payload / submitted content, nullable |
| `text_sha256` | str | |
| `urls` | list[str] | URLs found in `text` (see `textutil.extract_urls`) |
| `parent_event_id` | str | decode trees (swarmtraces), revision chains |
| `dup_of_event_id` | str | set when the same observation exists in another artifact |
| `source_confidence` | str | verbatim (e.g. `significant`, `publisher_selected_plus_task_or_exchange_signal`) |
| `confidence` | enum str | `high` / `medium` / `low` / `unrated` |
| `redaction_types` | list[str] | e.g. `["destination", "secret_key"]` |
| `extra` | str (JSON) | every source field not mapped above |

### `indicators.parquet`: long table of extracted identifiers (Stage 7)

`event_id`, `incident_id`, `indicator_type`, `value` (as observed), `value_norm` (NFKC, lowercased, host-normalized), `extractor` (rule id). `indicator_type` ∈ `actor_handle`, `oai_token`, `naming_pattern`, `relay_service`, `url_shortener`, `pastebin`, `url_scanner`, `task_target_domain`, `email`, `gem_name`, `ip16`, `unix_ts_in_name`, `redacted_placeholder`.

### `timeline_daily.parquet`

`date`, `incident_id`, `event_type`, `n_events`, `ts_precision`, `origin` (`row_level` | `published_aggregate`). Published aggregates (RubyGems `uploads-per-day.csv`, Transluce `daily-counts.csv`) sit beside row-level counts so they can be compared, and are never summed with them.

## File structure

```
src/agent_swarm/
  __init__.py            CLI: acquire | extract <source> | build | indicators | techniques | link | all
  paths.py               RAW/INTERIM/PROCESSED dirs, artifact path helpers (moved out of __init__)
  schema.py              canonical polars schemas, enums, validate_events(), make_event_id()
  textutil.py            extract_urls(), normalize_host(), redaction_types(), nfkc_handle(), safe_text()
  reference.py           INCIDENTS table, confidence mappings per source
  extract/
    collusion_wiki.py    raw -> interim/collusion-wiki/*.parquet -> events rows
    transluce.py         raw -> interim/transluce-urlquery/*.parquet -> events rows
    swarmtraces.py       raw -> interim/swarmtraces/*.parquet -> events rows
    rubyhack.py          raw -> interim/rubyhack/*.parquet -> events rows
  build.py               union + dedupe/link + timeline_daily + data-quality report
  indicators.py          rule-based indicator extraction
  techniques.py          technique taxonomy + regex/structural tagging rules
  link.py                cross-incident overlap metrics + incident/indicator graph
tests/
  fixtures/<source>/...  synthetic miniature raw files
  test_<module>.py
notebooks/
  01_collusion_wiki.ipynb   02_transluce_urlquery.ipynb   03_swarmtraces.ipynb
  04_rubyhack.ipynb         05_unified_timeline.ipynb     06_indicators.ipynb
  07_techniques.ipynb       08_cross_incident_links.ipynb
reports/figures/          exported SVG/PNG aggregates referenced from notebooks and agents.md
```

## Notebook conventions

- Each notebook starts with a markdown cell giving **Question · Inputs (exact parquet paths) · Outputs · Caveats**. It ends with a **Findings** cell of bullet points, each tied to a chart or table above it.
- Load via `agent_swarm.paths`, never with hard-coded paths. First cell: `%load_ext autoreload`.
- Charts use Altair and follow the `dataviz` skill (read it before the first chart). Every chart has a title stating the takeaway, labeled axes with units/UTC, and a source caption (`Source: collusion.wiki revisions.jsonl`). Figures that `agents.md` references are exported to `reports/figures/`.
- Outputs are committed. Never print raw payload bodies longer than 300 chars, and never print anything the source marked redacted.
- Before committing, run `uv run jupyter nbconvert --to notebook --execute --inplace notebooks/NN_*.ipynb` so outputs are reproducible from the pipeline, then `uv run nbqa ruff notebooks/`.

## Delivery: stacked draft PRs

Stage *n* is branch `stage-n-<slug>`, cut from `stage-(n-1)`'s branch (Stage 1 from `main`), in a worktree at `.claude/worktrees/stage-n-<slug>/`. PR base = previous stage branch. Before each PR: `ruff check`, `ruff format --check`, `pytest`, and execute the stage's notebook. Commit messages are one line.

---

### Stage 1: Canonical schema, text utilities, reference tables

**Files:** Create `src/agent_swarm/{paths,schema,textutil,reference}.py`, `tests/test_{schema,textutil,reference}.py`. Modify `src/agent_swarm/__init__.py` (import paths from `paths.py`).

**Interfaces (produces):**
- `schema.EVENTS_SCHEMA: dict[str, pl.DataType]`, `schema.EVENT_TYPES`, `schema.TS_PRECISIONS`, `schema.CONFIDENCES`
- `schema.make_event_id(source_id: str, artifact: str, native_id: str) -> str`
- `schema.empty_events() -> pl.DataFrame`
- `schema.conform(df: pl.DataFrame) -> pl.DataFrame`: adds missing canonical columns as null, casts, and orders columns
- `schema.validate_events(df) -> None`: raises `SchemaError` listing every violation (unknown enum values, duplicate `event_id`, null provenance, `ts_precision='none'` with non-null `ts_utc`, `ts_utc` non-null with `ts_precision='none'`)
- `textutil.extract_urls(text: str | None) -> list[str]`, `textutil.normalize_host(url_or_host: str) -> str | None` (returns None for placeholders), `textutil.redaction_types(text) -> list[str]`, `textutil.nfkc_handle(s) -> str`, `textutil.is_placeholder(s) -> bool`
- `reference.INCIDENTS: pl.DataFrame`, `reference.normalize_confidence(source_id: str, raw: str | None) -> str`

- [ ] Write failing tests. Key cases:
```python
def test_event_id_is_stable_and_provenance_sensitive():
    a = make_event_id("collusion-wiki", "collusion-wiki/revisions.jsonl.gz", "dse~X@1")
    assert a == make_event_id("collusion-wiki", "collusion-wiki/revisions.jsonl.gz", "dse~X@1")
    assert a != make_event_id("collusion-wiki", "collusion-wiki/events.jsonl.gz", "dse~X@1")
    assert len(a) == 16

def test_validate_rejects_timestamp_precision_mismatch():
    df = conform(pl.DataFrame({... "ts_utc": [datetime(2026,5,1,tzinfo=UTC)], "ts_precision": ["none"] ...}))
    with pytest.raises(SchemaError, match="ts_precision"):
        validate_events(df)

def test_extract_urls_handles_adversarial_text():
    text = "see https://r.jina.ai/https://x.org/a?b=1).​<script>alert(1)</script> http://...html"
    assert extract_urls(text) == ["https://r.jina.ai/https://x.org/a?b=1", "http://...html"]
    assert extract_urls(None) == []
    assert extract_urls("x" * 5_000_000) == []

def test_normalize_host_rejects_placeholders():
    assert normalize_host("https://WWW.Example.org:443/a") == "example.org"
    assert normalize_host("https://[SERVICE HOST 1]/x") is None
    assert normalize_host("http://...html") is None

def test_redaction_types():
    assert redaction_types("a [REDACTED:secret_key] b [REDACTED:destination:000002] [REDACTED:secret_key]") == ["destination", "secret_key"]

def test_nfkc_handle_folds_lookalikes():
    assert nfkc_handle("ＯｐｅｎＡＩ​Researcher") == "openairesearcher"

def test_normalize_confidence_never_upgrades():
    assert normalize_confidence("transluce-urlquery", "significant") == "high"
    assert normalize_confidence("transluce-urlquery", "suggestive") == "medium"
    assert normalize_confidence("transluce-urlquery", None) == "unrated"
    assert normalize_confidence("collusion-wiki", "not_independently_authenticated") == "medium"
```
- [ ] Run and confirm FAIL, implement, run and confirm PASS, then ruff and commit `Canonical event schema, text utilities and incident reference table`.

### Stage 2: Collusion.wiki extract

**Files:** `src/agent_swarm/extract/collusion_wiki.py`, `tests/test_extract_collusion_wiki.py`, `tests/fixtures/collusion-wiki/{revisions,events,labels,pages,records,links}.jsonl.gz` (synthetic, ~5–20 rows each, including a late-typed `related_event_id`), `notebooks/01_collusion_wiki.ipynb`.

**Interfaces:** `extract(raw_dir: Path, interim_dir: Path) -> pl.DataFrame` writes `interim/collusion-wiki/{revisions,events,labels,pages,records,links,site_coverage}.parquet` and returns canonical event rows.

**Mapping:**
- `revisions` → `wiki_save`: `ts_utc=time` (precision second), `actor_handle=label` (blank → null), `network_ip16=ip16`, `text=body`, `venue_host='prowiki.org'` for dse/wiki4d and `'wikiservice.at'` for probier/fractal/dorfwiki (map from `site-coverage.csv`), `venue_locator=page_id`, `native_id=rev_id`, `parent_event_id` = event of `diff_base`.
- `events` → `event_type='save'` rows are **not** re-emitted; they're joined onto the matching `wiki_save` via `revision_ref` and their fields go into `extra`. `delete` → `wiki_delete` with `actor_role='moderator'`. `revert` → `wiki_revert`. `probe` → `wiki_probe` (keep `request_action`, `param_family`).
- `labels` with `is_human_handle` → those handles get `actor_role='moderator'` on their events, and `'agent'` otherwise.
- `records` → `venue_post`, one per record per origin with `venue_host` from `origin.site`. Origins on `prowiki.org/dse` whose `source_text_sha256` matches a revision `body_sha256` get `dup_of_event_id`. Timestamps: parse `source_date_literal` if ISO-like (precision day/second) else `none`.
- `links` stays interim-only (URL inventory used by Stage 7).

- [ ] Tests (first failing): row conservation (`n wiki_save == n revisions`, `n wiki_delete == n delete events`), a late-typed column doesn't raise, blank label → null handle, the moderator handle gets the moderator role, record dup-linking, `validate_events` passes. `@realdata`: 14,591 saves, 5,217 deletes, 101 probes, 4 reverts; `ts_utc` range within 2026-05-01..2026-07-31.
- [ ] Notebook 01: edits/day by wiki (stacked bars), saves vs admin deletions over time (shows the moderation race), handle frequency (log-rank plot) with `OAI`/`OpenAI`/`Agent` token share, ip16 diversity per handle, the records venue mix, and top link hosts by category. Findings cell.

### Stage 3: Transluce urlquery extract

**Files:** `src/agent_swarm/extract/transluce.py`, test, fixtures (tiny `all-reports.csv`, `report-sources.csv`, `methods.json`, `daily-counts.csv`), `notebooks/02_transluce_urlquery.ipynb`.

**Mapping:** `all-reports.csv` → `url_scan` (`venue_host='urlquery.net'`, `venue_locator=report_id`, `ts_utc=report_date_utc` second precision, `source_confidence=confidence or disposition`, `confidence` via `normalize_confidence`; `background` rows kept with `confidence='low'`). `why_included`, `broad_class`, `disposition` and `caveat` go into `extra`. Join `report-sources.csv` → `extra.data_source`, `extra.source_basis`. Interim-only: `methods.parquet` (detector query + markers per source; reused in Stage 7 and Task 2) and `daily_counts.parquet` (published aggregate). Do **not** also ingest `reports.csv` / `additional-cited-reports.csv` (they're subsets of `all-reports.csv`); a test asserts the ID sets are equal.

- [ ] Tests: 38,160 rows `@realdata`; the union-equals-parts check; `review_required` → `unrated`; row-level daily counts for `included` reproduce `daily-counts.csv.total` exactly within the chart window. That last check is a strong validation of our parsing.
- [ ] Notebook 02: scans/day (log y) by broad class with incident windows shaded, data-source mix over time (small multiples for the top 8), confidence split, and a table of `methods.json` detectors. Findings.

### Stage 4: Swarmtraces extract

**Files:** `src/agent_swarm/extract/swarmtraces.py`, test, fixture (a 3-level synthetic tree with placeholders), `notebooks/03_swarmtraces.ipynb`.

**Mapping:** `payload` → `chain_payload`, `recovered_text` → `chain_decoded`, `response` → `chain_response`. `parent_event_id` comes from `parent_id`. Compute `extra.root_id`, `extra.depth` (iterative; guard against cycles), and `extra.tags`. `ts_precision='none'` everywhere. `extra.ts_hints` = sorted ISO dates found in the text within 2026-01-01..2026-09-30 (a hint only, never promoted to `ts_utc`). `redaction_types` via textutil. `venue_host='[shortener]'`. `actor_handle` comes from a JSON/JS `agent_id` value when it's a literal string that isn't a placeholder.
- [ ] Tests: 189,579 rows `@realdata`; every non-root parent exists; a cycle in the fixture doesn't hang (depth capped, flagged); `ts_utc` is all null; the placeholder `agent_id` isn't taken as a handle.
- [ ] Notebook 03: tree size/depth distributions, kind mix per depth, redaction-type frequencies, ts_hint histogram (hinted dates only, clearly labeled), and keyword prevalence (Hugging Face, Artifactory, OpenAI, heartbeat, LOOT) as bars. Findings.

### Stage 5: RubyHack extract

**Files:** `src/agent_swarm/extract/rubyhack.py`, test, fixtures (a mini report HTML + CSV), `notebooks/04_rubyhack.ipynb`.

**Mapping:** gem names parsed from `report.html`: the name lists, rubygems.org/diffend.io links and inline code spans matching `^[a-z0-9][a-z0-9_-]{2,}$` inside the package-list sections. Each becomes a `package_published` event with `venue_host='rubygems.org'`, `venue_locator=gem`, `ts_precision='none'` unless the report ties it to a date, and `source_confidence='publisher_listed'` → `medium`. Also emit `unix_ts_in_name` when a 10-digit 17xxxxxxxx number is present: the decoded timestamp goes into `extra.name_ts` and **is** promoted to `ts_utc` (precision second, `ts_field='name_unix_ts'`) because it's what the agent wrote. `uploads-per-day.csv` → `interim/rubyhack/uploads_per_day.parquet` (published aggregate).
- [ ] Tests: the HTML parser uses `html.parser` (stdlib, no JS); name extraction on the fixture exact-matches; `chatoaitestgit1778552630` → `2026-05-12T02:23:50Z`; CSV total = 2,654 `@realdata`.
- [ ] Notebook 04: uploads/day with the RubyGems response band, gem-name families (prefix clustering: `oai?x`, `lamb*`, `chatoaitest*`), and the embedded-timestamp scatter vs the published daily curve (does agent-written time agree with registry time?). Findings.

### Stage 6: Build unified tables and data-quality report

**Files:** `src/agent_swarm/build.py`, `tests/test_build.py`, `notebooks/05_unified_timeline.ipynb`.

**Interfaces:** `build(interim_dir, processed_dir) -> dict[str, int]` writes `events.parquet`, `incidents.parquet`, `timeline_daily.parquet` and `data_quality.json` (per source: rows, null rates per canonical column, ts precision mix, dup-linked counts).
- [ ] Tests: the union validates; `event_id` is globally unique; `timeline_daily` excludes `ts_precision='none'` rows and `data_quality.json` counts them; published aggregates carry `origin='published_aggregate'`.
- [ ] Notebook 05: **the consolidated timeline**, i.e. one swim-lane chart (incident × day, Nov 2025 → Sep 2026) with row-level counts, published aggregates as outlined bars, report publication dates as rules, and the OpenAI/HF milestones from `incidents`. Also a precision/coverage heatmap (source × month), explaining what each source can and can't see.

### Stage 7: Indicator extraction

**Files:** `src/agent_swarm/indicators.py`, `tests/test_indicators.py`, `notebooks/06_indicators.ipynb`.

**Rules (each with a rule id, stored as `extractor`):**
- `actor_handle` from `events.actor_handle`; `naming_pattern` = the handle/gem/page name reduced to a shape (`Agent{Word}{Num}`, `OpenAI{Word}{Year}`, `oai{a-k}x{7d}`) via `re.sub` of letters/digits runs, used to cluster conventions across incidents.
- `oai_token`: `(?i)(?<![a-z])(oai|openai)` inside handles, page names, gem names and payload identifiers.
- `relay_service` / `url_shortener` / `pastebin` / `url_scanner`: hosts from `urls`, classified by a seed list from `site-coverage.csv` categories plus a small curated list in `reference.py` (r.jina.ai, markdown.new, pure.md, md.succ.ai, jqp.vercel.app, allorigins, cors proxies, httpbin.org, urlquery.net, is.gd, tinyurl, …).
- `task_target_domain`: hosts that are the *object* of a relay URL (e.g. `https://r.jina.ai/https://api.datausa.io/...` → `api.datausa.io`), plus the Transluce `data_source` markers from `methods.parquet`.
- `email`, `gem_name`, `ip16`, `unix_ts_in_name`, `redacted_placeholder`.
- [ ] Tests: nested relay URL → both relay and target; placeholders → `redacted_placeholder` only; `...html` host is dropped; look-alike handles normalize together; every indicator's `event_id` exists in events.
- [ ] Notebook 06: indicator-type counts per incident (heatmap), top relay/shortener services per incident, naming-pattern families across incidents, and task-target domains shared across ≥2 incidents (table). Findings.

### Stage 8: Technique tagging

> **Parked (2026-09-26).** See `docs/todo.md`; Stage 9 proceeds without technique overlap.
>
> **Scope revised by the owner (2026-09-26).** Tag only the technique *categories* the published reports already describe. Tags come from structural signals already in the canonical data: event types, publisher labels (e.g. Transluce `broad_class`, collusion.wiki probe events) and Stage 7 indicators. There is no text-level rule detail and no per-technique payload regexes. Writing or adopting detection rules over payload text is parked in `docs/todo.md`. The precision audit below is therefore replaced by a check that every tag traces to its structural basis. The original design follows for reference.

**Files:** `src/agent_swarm/techniques.py`, `tests/test_techniques.py`, `notebooks/07_techniques.ipynb`.

A taxonomy of ~15 techniques, each mapped to MITRE ATT&CK where one applies: `get_to_write` (writes via GET/form params), `reader_proxy`, `cors_relay`, `shortener_chain`, `pastebin_dropbox`, `url_scanner_relay`, `xss_probe`, `ssrf_probe`, `sqli_or_param_fuzz`, `mass_account_creation`, `captcha_solving`, `dns_exfiltration`, `heartbeat_deadman`, `moderator_impersonation`, `layered_encoding`, `credential_harvest`, `k8s_recon`, `package_registry_abuse`. Rules are regex/structural (e.g. `layered_encoding` = decode depth ≥2 in swarmtraces). Output `techniques.parquet` (`event_id`, `technique`, `rule_id`).
- [ ] Tests: one positive and one near-miss negative fixture per technique.
- [ ] **Precision audit:** the notebook draws a seeded random sample of 20 events per technique for manual review (truncated text), records precision in a committed `reports/technique_precision.csv`, and flags rules under 0.8 for revision.
- [ ] Notebook 07: technique × incident matrix (share of events), technique onset dates per incident (first-seen timeline), and the reports' claimed techniques vs what our tags find.

### Stage 9: Cross-incident linkage and consolidated view

**Files:** `src/agent_swarm/link.py`, `tests/test_link.py`, `notebooks/08_cross_incident_links.ipynb`. Add `networkx` dependency.

**Analyses:**
- Indicator overlap: Jaccard between incident indicator sets per type, plus the list of **bridging indicators** (present in ≥2 incidents) with first/last seen per incident.
- Bipartite graph incident ↔ bridging indicator, plus handle ↔ venue graph for collusion (communities via greedy modularity) and whether communities span venues/incidents.
- Temporal: the ordering of first appearances of shared relays/targets across incidents (who used what first), and whether the wiki's ip16 blocks recur in rmn.re shortener logs (already claimed by collusion.wiki; we reproduce it as a validation).
- Naming-convention similarity across incidents (pattern families from Stage 7).
- Output `processed/links/{bridging_indicators,incident_overlap}.parquet` and `docs/findings/cross-incident.md`, a short findings write-up whose every claim cites a notebook cell or table. It feeds the Task 2 hunt signatures.
- [ ] Tests: overlap maths on a toy fixture, bridging-indicator definition (≥2 distinct incidents, placeholders excluded), and graph construction determinism.

### Stage 10+ (enrichment; each its own source + stage, run after Stage 9)

Ordered by value to linkage. Each adds a `[[source]]` to `sources.toml` and an extract module:
1. **urlquery report content** for the 38,160 Transluce IDs: fetch public report JSON/HTML read-only, rate-limited, cached by ID, resumable. This fills the biggest gap (submitted URLs and scripts), turning Transluce rows into text-bearing events.
2. **RubyGems registry metadata** for the listed gems (rubygems.org API, diffend.io diffs, and the public RubyGems data dump for yanked versions/authors/created_at). This turns the rubyhack aggregate into row-level events.
3. **Historical `chatgpt-user.json`** (Wayback snapshots May–Jul 2026) to test whether collusion `ip16` blocks fall inside OpenAI egress ranges.
4. **`agent-pastes` tarball** (retry `acquire --only termina-swarm-map`) and the community-found venues in `coverage-gaps.csv`.

## Self-review notes

- Spec coverage: consistent format and metadata → Stages 1–6. Unstructured leftovers → `extra`. Report provenance → provenance columns plus `incidents`. Common identifiers → Stage 7. Patterns (timeline, techniques/vulnerabilities) → Stages 6 and 8. Consolidated cross-incident view → Stage 9. The Task 2 hunt is out of scope here but seeded by `methods.parquet`, bridging indicators and technique rules.
- Stages 10+ are intentionally deferred because they add live fetching. They're listed so their absence is visible in the findings' limitations.
