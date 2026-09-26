# Parked work

Things deliberately deferred, with what unblocks them. Add an entry whenever something is parked, and delete it (or move it to "Done") when it's picked up. Dates are UTC.

## Waiting on something external

- **Community agent-pastes export** (`termina-swarm-map/agent-pastes-2026-09-08.tar.gz`, linked from rubyhack.ai). `swarm.termina.digital` has returned 503 "public exports are temporarily unavailable" since 2026-09-25. A session cron retries every 4 h; also re-run `uv run agent-swarm acquire --only termina-swarm-map` at the start of each work session. **When it lands:** survey it in `agents.md`, add an extractor, and fold it into Stage 9 linkage (it's a venue/handle map, so it's likely rich in bridging identifiers).

## Deferred by decision

- **Stage 8: technique tagging** (parked 2026-09-26). Writing even a report-level technique-category catalog was stopped twice by the assistant's safety classifier, so it's parked rather than attempted in another form. Stage 9 links the incidents on identifiers and timing only, with no technique overlap. **To resume:** decide on a form for the technique layer (e.g. a human-authored catalog committed by the owner, or an existing external taxonomy mapping), then add tagging over structural signals only.

- **Detection rules for attack techniques over payload text** (parked 2026-09-26 by the owner). Stage 8 tags only the technique *categories* the published reports already describe, using structural signals (event types, publisher labels, extracted indicators) with no text-level rule detail. Follow-up:
  - Survey existing detection libraries and rule sets that could run over the payload text (e.g. YARA/Sigma-style rule collections, secret scanners, static analysers for JS/Python/Ruby), and weigh them against writing our own.
  - Define how to run them safely over adversarial text: static only, never executed, outputs aggregated.
  - Decide what precision audit they need before their tags feed findings.
