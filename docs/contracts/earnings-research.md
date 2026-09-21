# Earnings Research Contract — schema version 1 (P4 additive artifacts)

P0 defines the core schema. P1/P2 implement collection and role research; P3 implements silent daily orchestration; P4 adds reader publications, user-cloud document delivery and automatic quarterly/cross-industry review behind default-off activation flags. Existing role reports remain schema-compatible.

## Boundary and status

Research-only, independent of the trading watchlist, price-action rulebook, account state, trading signal journal and Vibe preset. SEC/issuer disclosures are the primary financial evidence. Market prices, if separately requested, retain the repository market-provider policy. No broker writes or deterministic trade instructions.

Commands return the existing envelope: `status` = success/skipped/failed, `workflow`, `date`, `artifacts`, `skipped`, `reason`. Financial completeness is a separate field, not a fourth envelope status. `date` is the daily batch date in Asia/Shanghai; financial periods and UTC cutoff are explicit separate fields. Scripts prepare, calculate, validate and persist; they do not assume an MCP or LLM is callable.

## Configuration

`config/earnings_research.json` contains versioned source policy, daily/season schedule, model profiles, budget limits and delivery policy. `config/earnings_universe.json` contains stable sample ids, industry templates, issuer tickers to resolve and key issuers. Tickers are seeds, not verified identities; live ingestion must resolve CIK/company and retain source/time. No guessed CIKs or account credentials belong in these tracked files.

Live SEC requests require `TCA_SEC_USER_AGENT` with real operator contact information. Limit total SEC requests across workers with one shared limiter; initial cap is 2 requests/second, with caching and bounded backoff. Keep per-source progress and failure sets. A failed request never means no filing.

NAS secret overrides belong in environment or ignored runtime config, never tracked JSON. Configuration changes affect new tasks; running tasks retain the configuration hash used at creation.

## Commands and ownership

| Command | Phase | Result |
|---|---|---|
| earnings-collect | P1 | Persist source manifests/documents and discover new or revised events; support bounded live and offline fixture inputs |
| earnings-context | P1 | Normalize financials, apply cutoff, claim eligible task batches and write immutable role inputs |
| earnings-status | P1 | Read-only coverage, per-source watermarks, queue age/failures and usage summary |
| earnings-industry-context | P2 | Gather validated company evidence and prior theses; record missing/negative/unselected samples |
| earnings-record | P2 | Validate provenance and register role artifacts atomically; reject incomplete or stale dependencies |
| validate-earnings-research | P2 | Validate facts/citations/periods/claim references/output contracts; return errors and warnings |
| earnings-daily | P3 | Daily orchestration, role runner, bounded retries, due review checks, final notification decision |
| earnings-deliver | P3 | Repository-bound cc-connect notification, deduplication, receipts, no model calls |
| earnings-review-context | P4 | Automatic quarterly/weekly due checks and frozen review scopes; P2 supports manual quarterly contexts |
| earnings-market-context | P4 | Require every frozen industry acceptance before formal cross-industry synthesis |
| earnings-publication-runner | P4 | Run the daily-profile writer and independent review-profile checker from frozen accepted research |
| earnings-publication | P4 | Validate and archive immutable reader Markdown/HTML plus source mapping and version chain |
| earnings-lark-document | P4 | Preview or perform explicit-user document create/update/fetch/readback; never send messages |

Implement argument parsing and workflow integration consistently with existing scripts. Common inputs: repo root, config, universe, report date, cutoff, bounded symbols/industry, run/task id and explicit input/output files as applicable. Offline test input and live mode must be distinguishable in provenance. Missing live inputs cannot silently fall back to fixtures.

## Entity and storage contract

Runtime paths: `raw_data/earnings/`, `runtime/earnings/`, `report/earnings/`. Use SQLite on NAS local disk for state and immutable versioned files for originals and reports. No generated artifacts are committed.

Identifiers: issuer_id (CIK when verified, stable local id before IPO resolution), document_id (accession/document identity when applicable), event_id (issuer and actual earnings period, or IPO event), task_id and run_id. Distinct documents for one earnings event remain separately versioned. Duplicate media coverage shares its underlying source event.

Document record: schema_version, document_id, issuer_id, form/source_type, source_url, provider/backend, reporting_start/end (nullable for IPO), published_at, accepted_at (nullable), fetched_at, public_time_precision, original_path, content_sha256, version, supersedes, source_mode. Unknown public timestamps remain unknown; date-only timestamps on the cutoff date are also unprovable. Compact SEC acceptance timestamps use the documented America/New_York convention and are normalized to UTC. Historical contexts must exclude or explicitly flag unprovable availability. Revisions never overwrite originals. Filing document type is preferred for EX-99.1; filename heuristics support embedded `ex991` names only for text-like files and persist an explicit limitation. An 8-K/6-K filing date is not a fiscal quarter.

Evidence record: schema_version, evidence_id, issuer_id, symbol (nullable), segment (nullable), industry_ids, document_id/version/hash, source locator, short quote, source URL, public timestamp, reporting_start/end, evidence_kind (fact/management_outlook/inference), summary, numeric_facts, limitations. Numeric facts contain metric, value (nullable), unit, currency (nullable), accounting_basis, duration/instant, period, derivation and source evidence ids. Percentages and monetary values must preserve scale.

Financial normalization must handle cumulative vs standalone quarters, annual vs Q4, restatements, custom tags and segment dimensions. Derived differences retain both component accessions, tags, periods, values and units. SEC Company Facts is a current aggregate endpoint, not a point-in-time archive: filed-date filtering is a calculation aid, not proof that the aggregate payload was historically available. Historical factual claims still require contemporaneous original filing evidence. No unsupported subtraction across currencies/accounting scopes. Missing values are null. Loss-to-profit transitions are described without meaningless growth ratios. No consensus-surprise claim without pre-release consensus evidence.

## Role artifact contract

All role JSON reports require schema_version, report_id, report_type (company/industry/challenge/synthesis), task_id, run_id, scope, cutoff, generated_at, input_manifest_hash, source_mode, provenance, evidence, claims, limitations, completeness.

`scope` identifies issuer/industry, actual periods and frozen universe version. `provenance` includes actual provider/model/effort, method version, configuration hash, input document and predecessor report hashes; usage fields are nullable. Reported model names must match the role runner manifest rather than an LLM guess.

Each claim requires claim_id, statement, kind, evidence_ids, direction (support/oppose/mixed/neutral), alternative_explanation, limitations. IDs must resolve in the supplied evidence graph. Major unsupported inferences must be rejected or retained as explicitly unresolved, never promoted to fact.

Company/industry/synthesis add thesis_state (emerging/strengthening/validating/weakening/invalidated/insufficient_data), change_summary, invalidation_conditions, next_checks, coverage. Completeness is full/partial/insufficient with missing inputs. Coverage distinguishes expected/disclosed/fetched/researched issuer counts and key missing issuers, with fixed denominator and counts that reconcile.

Challenge reports add findings: finding_id, disputed_claim_id (nullable for independent discoveries), evidence_ids, competing_explanation, materiality, requested_check. Quarterly synthesis requires challenge_dispositions for every material finding: accepted/rejected/unresolved, rationale and supporting evidence ids. Rejected findings require evidence, not voting among agents.

Input manifests specify assigned role, output paths, cutoff, source versions, previous artifacts, actual scope and model profile. Every referenced runtime path must resolve inside allowed input/output roots; prevent traversal and accidental writes to code/config/secrets. A role cannot mutate the frozen manifest.

## Reader publication contract (P4)

A publication series key is report type + stable subject + research quarter. Every accepted revision has a new immutable publication_id and version, retains the prior manifest hash, and records source report paths/hashes, content hash, source claim/evidence mapping, checker result and Markdown/HTML artifact hashes. Markdown is authoritative; HTML is derived. Internal model, usage, task hashes and raw JSON are not reader正文.

The writer uses the `daily` profile and only accepted frozen research. The checker uses the `review` profile and independently checks numbers, units, actual fiscal period, cutoff, source URLs/locators, counterevidence, inference strength, unknown consensus/valuation and readability. Decimal string values and signs remain exact. Every prose occurrence and table cell carries an explicit evidence/metric/currency/unit/period/accounting-basis/derivation binding; the runner injects the draft/input/source hashes programmatically. Deterministic checking is an additional gate. Missing or ambiguous bindings, required sections, financial numbers/dates/links, counterevidence or unsupported certainty block archive and cloud sync. A passing schema alone is not a passing publication.

Company/IPO publications are archived for every researched company-quarter. Industry and market publication completeness is `stage` or `full`; version kind is independently `initial` or `revision`. A partial revision therefore remains `stage` and cannot enter the full-market gate. Same-event release and later 10-Q update one versioned series; a changed source hash creates a bounded revision rather than overwriting history. The runner supplies the checker a deterministic occurrence inventory: prose spans use Python codepoint offsets into exact unmodified Markdown and table cells use one-based line/column coordinates. Writer prompts require supported explicit units, unit-bearing table headers and explicit duration/instant period wording. Models do not count offsets or calculate hashes. Checked stage and full quarterly publications are deliverable with explicit labels even if thesis_state is unchanged.

## Fiscal-quarter and cross-industry contract (P4)

The cohort mapping policy is `maximum-calendar-quarter-overlap-v1`: only an actual 70–110 day standalone operating period is mapped to the natural quarter with greatest day overlap. The original start/end, overlap and cross-period difference remain visible. Missing boundaries, cumulative half-year/nine-month periods and annual reports cannot masquerade as a standalone quarter.

Each industry-quarter freezes its expected and key issuer lists before maturity evaluation. Disclosed, fetched and researched counts remain separate and require cutoff-valid live provenance, valid source/report files and completed accepted tasks. Rolling stage work starts when the configured disclosure ratio is reached (default 60%, rounded up: four of six) or a configured key issuer has disclosed; it does not wait for every company research report. Gap review is part of that eligible stage/final DAG and must not spend a model call before the trigger or tail deadline. If verified accepted research is not ready, the scope waits for those facts and immediately continues the same DAG when they arrive. Full maturity remains independent and requires the configured full disclosed threshold, every key issuer, research for disclosed members and a separately recorded, input-hash-bound critical-gap result. Coverage cannot manufacture resolved status. Historical, annual, cumulative, wrong-quarter and calculation-only aggregate documents are exclusion diagnostics, not current membership gaps; missing or invalid current-quarter originals and genuinely missing expected members remain critical. Tail deadline is a finalization/gap checkpoint rather than the initial creation gate. A deadline final with unresolved gaps remains a clearly labeled stage final and cannot masquerade as full. Initial automatic backfill is bounded to the configured most recent ended quarter. Sunday review is a due check for gaps/backlog/assumptions and remains silent when its fingerprint is unchanged or non-actionable.

Quarterly DAG state is durable across days and revisions: deterministic coverage → a bounded independent `review`-profile gap audit → industry deep research → independent challenge → synthesis → writer → checker → optional cloud document → market. Gap attempts have immutable manifests, leases, daily review budgets, cross-day retry and a terminal attempt cap. Only `record_gap_review` may accept their hash-bound outputs; incomplete evidence remains unresolved and Python never promotes coverage to resolved. Each in-flight revision freezes a public cutoff and exact accepted-input hash. Public cutoff governs source availability; research cutoff records the accepted artifact boundary, but report creation time never substitutes for evidence legality. Every cited source must match an immutable registered document id/version/hash whose public timestamp is at or before the public cutoff. Later accepted company inputs preserve prior stage history and enter a following revision without resetting the in-flight DAG or waiting for the cross-industry market stage; a terminal publication repair is retained as a blocked historical DAG and cannot permanently trap pending evidence. Frozen membership does not change. Actual fiscal periods may be resolved from accepted report evidence/fact periods bound to an exact document id/version/hash when event or document starts are null, without mutating source records; the binding is applied only after the report period matches the requested quarter. Disclosed, fetched and researched remain separate states. Formal full “美股重点行业季度研究” requires all frozen industries to have current accepted full publications. When every industry is durably delivered and sealed as either finalized full or finalized stage-with-gaps, an explicitly labeled stage market report may proceed with frozen per-industry finalization/completeness/gap metadata; an arbitrary unfinished stage snapshot cannot qualify, and a stage market never masquerades as full. Cross-industry work independently examines breadth, profit transmission, shared-customer double counting, negative evidence and metric comparability rather than concatenating industry summaries.

## User-cloud document contract (P4)

Runtime deployment configuration supplies an absolute lark-cli path, explicit profile, `as=user`, target folder token and activation switch. These values and all user/document/folder identities remain ignored runtime data. The adapter allowlists document create/update/fetch only, passes argv without a shell, never changes login, permissions or default identity, never falls back to bot, and never sends a message.

Only a checked publication may sync. Create/update, readback and cc-connect notification have independent state. A create timeout/non-definitive result is `unknown` and cannot be retried until an operator supplies a document identity for read-only reconciliation. Update first fetches the verified baseline; a changed remote body is `conflict` and is never overwritten. Full success requires readback-equivalent Markdown and an accessible URL. Notification remains solely the verified repository cc-connect route and includes every same-day report entry or a complete accessible directory link.

### Exact JSON field shapes

Use these exact keys; descriptive names above are not aliases. Every report also copies `research_mode` from the manifest.

- `provenance.input_document_hashes`: array of SHA-256 strings from all `documents` and `calculation_inputs`, not objects or `input_documents`.
- `provenance.predecessor_report_hashes`: array of SHA-256 strings from `previous_artifacts`, including an empty array when none exist.
- Each evidence uses `document_id`, `document_version`, and `document_hash` (the source's `content_sha256`), plus `public_timestamp` copied from `accepted_at` or `published_at`.
- `numeric_facts[].period` is `{"kind":"duration","start":"YYYY-MM-DD","end":"YYYY-MM-DD"}` or `{"kind":"instant","start":null,"end":"YYYY-MM-DD"}`. Unknown dates are null, never prose. `duration` at the numeric-fact top level cannot replace `period.kind`.
- `coverage` is `{"expected_issuers":1,"disclosed_issuers":1,"fetched_issuers":1,"researched_issuers":1,"key_missing_issuers":[]}` for a researched single issuer. For industry/synthesis copy `manifest.coverage_audit.counts` exactly, preserving additional frozen count fields. Do not nest another `counts` object.
- `completeness` is `{"status":"partial","missing_inputs":["specific missing material"]}` when incomplete. This is separate from `thesis_state`.
- Challenge findings use unique `finding_id`, `disputed_claim_id` (nullable), `evidence_ids` (array), `competing_explanation` (string), `materiality` (`high`/`medium`/`low`; `material` is an accepted alias for high), and `requested_check` (string). Synthesis copies every ID in `manifest.material_challenge_finding_ids` into `challenge_dispositions`, with a disposition, rationale and resolvable `supporting_evidence_ids`.

`short_quote` must be a contiguous verbatim excerpt from the source or its HTML text after tag removal, entity decoding and whitespace folding. Paraphrases, inserted table labels and disconnected sentence concatenation fail validation. The original byte hash remains authoritative even when matching rendered HTML text. Cite table/section locators separately from the quote.

Validator errors: missing identity/provenance, nonexistent or hash-mismatched input, citation/claim references not resolvable, impossible periods/units, known post-cutoff input, invalid counts/state, undeclared fixture evidence, broker command fields, or missing material challenge dispositions. Warnings: missing optional call transcript, incomplete peer coverage, unverified optional metadata. Validator success proves structural/evidence integrity, not economic correctness; manual sample review remains mandatory.

## Queue, idempotency and budget

Task key = research mode + issuer/industry + periods + input hashes + method version. State = queued/running/completed/retryable_failed/terminal_failed. Lease owner/expiry, attempts, dependencies and output manifest are persisted. Each attempt has an immutable input manifest and isolated output directory. Only eligible expired leases can be reclaimed; leases at the maximum attempt count become terminal. Registration rechecks the live lease, frozen input, dependency versions, superseding tasks and attempt manifest inside one transaction. Supersession uses a monotonic database task revision/row order, not wall-clock timestamp ordering. A legitimate current amendment may compare with its frozen prior artifact, but unrelated newer evidence still blocks publication. Identical completion is idempotent; conflicting completion is rejected. Unchanged inputs do not automatically re-run models.

Dependency recovery is exact-target and preview-first. Reusing an older completion requires exact frozen source/calculation/method/profile equality; differing legacy raw configuration hashes require matching immutable semantic-basis proofs for both tasks. A one-sided snapshot never proves compatibility. If a proof is unavailable, reuse remains forbidden; an operator may instead apply the separately audited `exclude-dependency` action. Exclusion retains attempts and the original failure in its audit, releases the blocked DAG only for limited-stage work, and creates a critical exclusion gap that prevents full eligibility.

Advance a source checkpoint only after safely registering every item in the bounded discovery window or its explicit pending failure. A fresh incremental issuer begins at cutoff minus configured overlap; subsequent runs begin at the successful checkpoint minus overlap. Reconciliation has its own persisted cadence, and downtime traverses only historical submission files whose declared date ranges overlap the missed window. Discovery and fetch are separate persistent states: bounded-fetch leftovers survive checkpoints and later runs. Distinguish a successful empty result from unavailable source, and resolve failures per recovered item rather than clearing an issuer scope. Budget-limited work remains queued, not dropped. Initialization loads historical indexes newest-first only until the configured actual period coverage is met or indexes are exhausted. Annual disclosures occupy at most the expected year-end slots; eight annual 20-F files are not eight quarters.

Profiles: daily Sol medium, review Sol high, quarterly Astra high, escalation Astra xhigh. Initial extraction profile is disabled. Daily, quarterly, upgrades and initialization have separate allowances. One active research process initially. Model effort is not a hard token cap; only enforce observable configured limits and persist unavailable usage as null. Check unsupported profiles before executing; no silent model fallback. Reuse cache and completed artifacts before spending budget on repeated analysis.

Publication recovery separates batch remaining time, stage start thresholds and per-execution timeout. A completed writer is immutable and may be followed on a later day by one new audited checker attempt; an expired absolute deadline or changed invocation timeout cannot permanently poison the job. Cached failed results consume no new model allowance, retain semantic/deterministic feedback, and may schedule at most one repair writer+checker pair. Only successful results with a manifest may enter cloud state. Explicit operator recovery is exact-target, preview-first, backed up and one-shot.

Before a role claim, every dependency must still be the current accepted version for its task identity; stale dependencies block model execution and are regenerated or awaited. Expired leases are projected as retryable/terminal/superseded rather than indefinitely displayed as running. Company claims prioritize each issuer's newest pending period and round-robin issuers before deeper history; total daily company limits remain unchanged. Explicit quota exhaustion opens a batch circuit, while capacity errors use bounded retry timing and remain a separate class.

## Daily and quarterly trigger contract

Daily trigger is 10:00 Asia/Shanghai, seven days/week. Keep actual public cutoff in UTC and original timezone metadata. No trading-day skip for earnings work. No-work requires checking new inputs, queue, retries and due reviews; then skip model use.

The trigger starts or wakes one persistent research round with a frozen batch date, public cutoff
and revision. Disclosures after that cutoff belong to the next round. Company claims, daily industry
heads, quarterly fingerprints, publication discovery and final delivery must all remain cutoff-bound;
they cannot reread a newer global head during continuation. Configured role counts are bounded
per-execution-window soft quotas, not an all-day completion limit. A bounded background worker may
handoff to another bounded generation while durable progress continues, so the scheduler timeout is
not a research-completion deadline. It stops on completion or an explicit durable reason such as
quota, capacity, terminal/manual blocker or repeated no progress; these states are not completion.

Season windows are resource-policy defaults from configuration. Quarterly mature trigger requires >= 0.9 disclosed coverage of the frozen applicable core sample, key issuer disclosures, completed necessary research and explicit resolution/disclosure of critical supply-chain gaps. A quarter is mapped from actual operating periods, not only filing months. Foreign issuer obligations and Q4 annual reporting differ.

At configured tail cutoff, produce a partial/insufficient stage report if maturity fails. Material subsequent evidence creates a versioned revision. Manual P2 quarterly runs use the same completeness rules without requiring P4 scheduling. Cross-industry synthesis discloses unavailable industry reports and incomparable metrics.

## Silent delivery contract (P3)

cc-connect cron must be muted and must not automatically forward role/final output. Roles never send messages. The outer finalizer produces one decision: should_send, notification_kind, rationale, report_versions, body_path, project, session, content_hash. No material change means suppressed; temporary retries and ordinary progress remain local. Qualifying failures are handled at failure finalization.

Only the verified repository NAS cc-connect project/session may send. Read CC_CONNECT_BIN/CC_CONNECT_PROJECT/CC_CONNECT_SESSION from the intended deployment config; do not fall back to another workspace's Feishu CLI or bot. Require explicit nonempty routing. Normal notifications should be combined per daily batch; weekly/quarterly completions can join it. No standalone intraday earnings alert scheduler.

Notification state = pending/ready/sent/retryable_failed/unknown/suppressed. The deduplication key includes destination and report/content version. Record receipts when available; send success precedes marking sent. Unknown result requires reconciliation, not automatic double-send. Delivery failure only retries delivery; full report archive and summary notification status are separate. NAS-local paths must not be presented as externally usable links. Attachment support is optional until verified.

## P0 acceptance cases

Use `config/earnings_acceptance_cases.json` as an offline methodology review set, never live evidence. It includes a clear operating improvement, contradictory accounting/demand signals, and an incomplete IPO disclosure. Expected constraints demonstrate distinctions rather than mandate exact prose or arbitrary model confidence scores.
