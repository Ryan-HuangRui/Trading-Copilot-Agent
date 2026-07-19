# Vibe Swarm Independent Research

Use this workflow when the user explicitly asks for a deeper independent
research review, or when the pre/post-market analysis skill escalates one focus
symbol under the bounded criteria below. It is never part of intraday monitor,
watchlist-sync, Feishu-delivery, or paper-execution steps.

## Boundary

- Research artifacts only; never place broker orders or call broker mutation APIs.
- Do not output deterministic buy/sell/short instructions or portfolio weights.
- Swarm results cannot raise `execution_status`, create a Trade Plan Card, edit
  the canonical rulebook, or update Longbridge `今日关注`.
- Shadow Account tools remain outside the repo MCP allowlist; this workflow does
  not create or update Vibe shadow state.
- Trading Copilot's Longbridge-first snapshot remains canonical for current
  price conclusions. Vibe market data is secondary evidence and must disclose
  provider, freshness, and limitations.
- Only the repo-owned `tca_research_review` preset is runnable. Bundled Vibe
  presets remain blocked until separately reviewed and added to adapter policy.
- The approved preset uses provider-qualified `openai-codex/gpt-5.6-sol` for
  every Swarm role. The Codex adapter sends effective model `gpt-5.6-sol`; the
  bare name `5.6-sol` is invalid. The repo-only MCP defaults
  `LANGCHAIN_REASONING_EFFORT=medium`; preserve provider, effective model, and
  reasoning effort in run audit artifacts.
- The normal agent research chain runs first. Swarm is an escalation source for
  evidence collection and adversarial review, not a replacement analyst.

## Analysis escalation gate

The current analysis skill may request deep research only when at least one of
these is true for a focus symbol:

- a material bull/bear/risk contradiction cannot be resolved from current evidence;
- fundamentals or news evidence needed for the thesis is fixture-only or missing;
- an abnormal move lacks a verifiable explanation;
- a repeated failed thesis needs independent evidence and falsification review.

Each scheduled pre/post-market session may escalate at most one symbol. Record a
specific research objective; do not launch broad or open-ended Swarms for every
watchlist symbol.

## Start

1. Call `list_swarm_presets` and confirm `tca_research_review` is available.
   Confirm the returned defaults report `default_provider=openai-codex`,
   `default_model=openai-codex/gpt-5.6-sol`,
   `effective_model=gpt-5.6-sol`, and `reasoning_effort=medium`; if runtime
   metadata differs, disclose the actual values and do not mislabel the result.
2. Call `run_swarm` with:

```json
{
  "preset_name": "tca_research_review",
  "variables": {
    "target": "NVDA.US",
    "market": "US",
    "objective": "Evaluate the evidence for and against sustained AI infrastructure demand",
    "as_of": "2026-07-18"
  }
}
```

The adapter always starts the run asynchronously and returns a `run_id`
immediately. It does not keep an MCP call open for the full research run.

Immediately index a newly started run as pending so later sessions can recover
it without depending on chat history:

```bash
python3 script/trading_copilot.py vibe-research-context \
  --date <DATE> \
  --session pre-market \
  --run-id <RUN_ID> \
  --target NVDA.US \
  --symbol NVDA \
  --objective "Evaluate the evidence for and against sustained AI infrastructure demand" \
  --as-of <DATE> \
  --status pending
```

## Follow-up

1. Poll `get_swarm_status(run_id)` until the run is terminal.
2. Read `get_run_result(run_id)`.
3. Preserve the raw JSON and a Chinese synthesis under:

```text
report/research/vibe-swarm/<run_id>/result.json
report/research/vibe-swarm/<run_id>/summary.md
```

4. The synthesis must retain provider/as-of metadata, contradictions, missing
   evidence, confidence, and the final `RESEARCH_ONLY`, `WATCH`, or `NO_TRADE`
   research-quality label.
5. If this evidence is later referenced by a session report, cite the artifact
   path and keep it evidence-only. The normal report and Trade Plan validators
   remain mandatory.
6. Replace the pending index record with the completed, hash-recorded artifacts:

```bash
python3 script/trading_copilot.py vibe-research-context \
  --date <DATE> \
  --session post-market \
  --run-id <RUN_ID> \
  --target NVDA.US \
  --symbol NVDA \
  --objective "Evaluate the evidence for and against sustained AI infrastructure demand" \
  --as-of <DATE> \
  --status completed \
  --result report/research/vibe-swarm/<RUN_ID>/result.json \
  --summary report/research/vibe-swarm/<RUN_ID>/summary.md \
  --quality-label RESEARCH_ONLY \
  --confidence 0.6
```

The command writes or updates
`report/<DATE>/agents/vibe-research-context.json`. `pre-market-plan` and
`post-market-review` consume completed runs with `--include-vibe-research`.

## Pre/post-market behavior

- Pre-market consumes completed, fresh research already indexed for the report.
  A new escalation starts asynchronously and is recorded as pending; the report
  continues without waiting.
- Post-market reconciles pending run ids, consumes completed results when
  available, and may launch at most one new escalation. A pending/failed Swarm
  remains non-blocking.
- The report-generation analysis must compare Swarm conclusions with the normal
  agent bull/bear/risk artifacts. Swarm disagreement lowers confidence or opens
  a research question; it does not automatically change the session sidecar.

## Recovery

- Use `list_runs` to find the run after a client restart.
- Use `reap_stale_runs` only when a run is orphaned and past its stale threshold.
- Use `retry_run` only for a failed approved preset run. The adapter rejects
  retries for presets outside the allowlist.
- A failed swarm is non-blocking for unrelated Trading Copilot workflows. Do
  not silently replace missing swarm evidence with invented conclusions.
