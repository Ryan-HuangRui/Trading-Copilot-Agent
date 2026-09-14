# Earnings Research Contract — schema version 1

P0 defines this contract and configuration. P1/P2 implement the commands below; P3 adds silent scheduled delivery. Command names here are not proof they are already executable. See `docs/earnings-research-workflow-plan.md` for accepted scope and milestones.

## Boundary and status

Research-only, independent of the trading watchlist, price-action rulebook, account state, trading signal journal and Vibe preset. SEC/issuer disclosures are the primary financial evidence. Market prices, if separately requested, retain the repository market-provider policy. No broker writes or deterministic trade instructions.

Commands return the existing envelope: `status` = success/skipped/failed, `workflow`, `date`, `artifacts`, `skipped`, `reason`. Financial completeness is a separate field, not a fourth envelope status. `date` is the daily batch date in Asia/Shanghai; financial periods and UTC cutoff are explicit separate fields. Scripts prepare, calculate, validate and persist; they do not assume an MCP or LLM is callable.

## Configuration

`config/earnings_research.json` contains versioned source policy, daily/season schedule, model profiles, budget limits and delivery policy. `config/earnings_universe.json` contains stable sample ids, industry templates, issuer tickers to resolve and key issuers. Tickers are seeds, not verified identities; live ingestion must resolve CIK/company and retain source/time. No guessed CIKs or account credentials belong in these tracked files.

Live SEC requests require `TCA_SEC_USER_AGENT` with real operator contact information. Limit total SEC requests across workers with one shared limiter; initial cap is 2 requests/second, with caching and bounded backoff. Keep per-source progress and failure sets. A failed request never means no filing.

NAS secret overrides belong in environment or ignored runtime config, never tracked JSON. Configuration changes affect new tasks; running tasks retain the configuration hash used at creation.

## Planned commands and ownership

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

Implement argument parsing and workflow integration consistently with existing scripts. Common inputs: repo root, config, universe, report date, cutoff, bounded symbols/industry, run/task id and explicit input/output files as applicable. Offline test input and live mode must be distinguishable in provenance. Missing live inputs cannot silently fall back to fixtures.

## Entity and storage contract

Runtime paths: `raw_data/earnings/`, `runtime/earnings/`, `report/earnings/`. Use SQLite on NAS local disk for state and immutable versioned files for originals and reports. No generated artifacts are committed.

Identifiers: issuer_id (CIK when verified, stable local id before IPO resolution), document_id (accession/document identity when applicable), event_id (issuer and actual earnings period, or IPO event), task_id and run_id. Distinct documents for one earnings event remain separately versioned. Duplicate media coverage shares its underlying source event.

Document record: schema_version, document_id, issuer_id, form/source_type, source_url, provider/backend, reporting_start/end (nullable for IPO), published_at, accepted_at (nullable), fetched_at, public_time_precision, original_path, content_sha256, version, supersedes, source_mode. Unknown public timestamps remain unknown; historical contexts must exclude or explicitly flag unprovable availability. Revisions never overwrite originals.

Evidence record: schema_version, evidence_id, issuer_id, symbol (nullable), segment (nullable), industry_ids, document_id/version/hash, source locator, short quote, source URL, public timestamp, reporting_start/end, evidence_kind (fact/management_outlook/inference), summary, numeric_facts, limitations. Numeric facts contain metric, value (nullable), unit, currency (nullable), accounting_basis, duration/instant, period, derivation and source evidence ids. Percentages and monetary values must preserve scale.

Financial normalization must handle cumulative vs standalone quarters, annual vs Q4, restatements, custom tags and segment dimensions. No unsupported subtraction across currencies/accounting scopes. Missing values are null. Loss-to-profit transitions are described without meaningless growth ratios. No consensus-surprise claim without pre-release consensus evidence.

## Role artifact contract

All role JSON reports require schema_version, report_id, report_type (company/industry/challenge/synthesis), task_id, run_id, scope, cutoff, generated_at, input_manifest_hash, source_mode, provenance, evidence, claims, limitations, completeness.

`scope` identifies issuer/industry, actual periods and frozen universe version. `provenance` includes actual provider/model/effort, method version, configuration hash, input document and predecessor report hashes; usage fields are nullable. Reported model names must match the role runner manifest rather than an LLM guess.

Each claim requires claim_id, statement, kind, evidence_ids, direction (support/oppose/mixed/neutral), alternative_explanation, limitations. IDs must resolve in the supplied evidence graph. Major unsupported inferences must be rejected or retained as explicitly unresolved, never promoted to fact.

Company/industry/synthesis add thesis_state (emerging/strengthening/validating/weakening/invalidated/insufficient_data), change_summary, invalidation_conditions, next_checks, coverage. Completeness is full/partial/insufficient with missing inputs. Coverage distinguishes expected/disclosed/fetched/researched issuer counts and key missing issuers, with fixed denominator and counts that reconcile.

Challenge reports add findings: finding_id, disputed_claim_id (nullable for independent discoveries), evidence_ids, competing_explanation, materiality, requested_check. Quarterly synthesis requires challenge_dispositions for every material finding: accepted/rejected/unresolved, rationale and supporting evidence ids. Rejected findings require evidence, not voting among agents.

Input manifests specify assigned role, output paths, cutoff, source versions, previous artifacts, actual scope and model profile. Every referenced runtime path must resolve inside allowed input/output roots; prevent traversal and accidental writes to code/config/secrets. A role cannot mutate the frozen manifest.

Validator errors: missing identity/provenance, nonexistent or hash-mismatched input, citation/claim references not resolvable, impossible periods/units, known post-cutoff input, invalid counts/state, undeclared fixture evidence, broker command fields, or missing material challenge dispositions. Warnings: missing optional call transcript, incomplete peer coverage, unverified optional metadata. Validator success proves structural/evidence integrity, not economic correctness; manual sample review remains mandatory.

## Queue, idempotency and budget

Task key = research mode + issuer/industry + periods + input hashes + method version. State = queued/running/completed/retryable_failed/terminal_failed. Lease owner/expiry, attempts, dependencies and output manifest are persisted. Only eligible expired leases can be reclaimed. Batch/run output directories are isolated; atomic completion records follow validated artifacts. Unchanged inputs do not automatically re-run models.

Advance a source watermark only after safely registering each discovered item or its explicit pending failure. Distinguish a successful empty result from unavailable source. Scan overlapping windows and periodically reconcile amendments/deletions; resume from watermarks after downtime. Budget-limited work remains queued, not dropped.

Profiles: daily Sol medium, review Sol high, quarterly Astra high, escalation Astra xhigh. Initial extraction profile is disabled. Daily, quarterly, upgrades and initialization have separate allowances. One active research process initially. Model effort is not a hard token cap; only enforce observable configured limits and persist unavailable usage as null. Check unsupported profiles before executing; no silent model fallback. Reuse cache and completed artifacts before spending budget on repeated analysis.

## Daily and quarterly trigger contract

Daily trigger is 10:00 Asia/Shanghai, seven days/week. Keep actual public cutoff in UTC and original timezone metadata. No trading-day skip for earnings work. No-work requires checking new inputs, queue, retries and due reviews; then skip model use.

Season windows are resource-policy defaults from configuration. Quarterly mature trigger requires >= 0.9 disclosed coverage of the frozen applicable core sample, key issuer disclosures, completed necessary research and explicit resolution/disclosure of critical supply-chain gaps. A quarter is mapped from actual operating periods, not only filing months. Foreign issuer obligations and Q4 annual reporting differ.

At configured tail cutoff, produce a partial/insufficient stage report if maturity fails. Material subsequent evidence creates a versioned revision. Manual P2 quarterly runs use the same completeness rules without requiring P4 scheduling. Cross-industry synthesis discloses unavailable industry reports and incomparable metrics.

## Silent delivery contract (P3)

cc-connect cron must be muted and must not automatically forward role/final output. Roles never send messages. The outer finalizer produces one decision: should_send, notification_kind, rationale, report_versions, body_path, project, session, content_hash. No material change means suppressed; temporary retries and ordinary progress remain local. Qualifying failures are handled at failure finalization.

Only the verified repository NAS cc-connect project/session may send. Read CC_CONNECT_BIN/CC_CONNECT_PROJECT/CC_CONNECT_SESSION from the intended deployment config; do not fall back to another workspace's Feishu CLI or bot. Require explicit nonempty routing. Normal notifications should be combined per daily batch; weekly/quarterly completions can join it. No standalone intraday earnings alert scheduler.

Notification state = pending/ready/sent/retryable_failed/unknown/suppressed. The deduplication key includes destination and report/content version. Record receipts when available; send success precedes marking sent. Unknown result requires reconciliation, not automatic double-send. Delivery failure only retries delivery; full report archive and summary notification status are separate. NAS-local paths must not be presented as externally usable links. Attachment support is optional until verified.

## P0 acceptance cases

Use `config/earnings_acceptance_cases.json` as an offline methodology review set, never live evidence. It includes a clear operating improvement, contradictory accounting/demand signals, and an incomplete IPO disclosure. Expected constraints demonstrate distinctions rather than mandate exact prose or arbitrary model confidence scores.
