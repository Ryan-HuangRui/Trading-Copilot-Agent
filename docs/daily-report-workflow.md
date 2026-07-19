# Daily Report Workflow

Production scheduling is expected to run through cc connect. cc connect triggers Codex and sends Feishu messages; the repository owns workflow contracts, validation, journal writes, and review generation. Do not run the same production pre-market or post-market workflow from both cc connect and Codex App automation.

Paper execution is an execution extension to this report workflow, not a report-generation step. It consumes validated `pre-market-signals.json` / `post-market-signals.json` artifacts, and any paper broker write must run through a separate paper execution task. See `docs/paper-execution-runbook.md`.

## Responsibility split
- `script/`: deterministic data work, market-date checks, path layout, cache fallback, and context generation.
- `.codex/skills/`: report-analysis workflows and output contracts.
- `agent/`: thin compatibility triggers that select a dedicated Skill.
- `canonical rulebook/`: the only trading-rule source for analysis conclusions.
- `docs/`: runbooks and operational documentation for humans and automation prompts.
- `AGENTS.md`: engineering guidance for Codex when maintaining this repository. It is not a trading-analysis prompt.

## Skill trigger rules
- Codex App automation does not automatically load every repo-only Skill.
- Post-market automation explicitly triggers `$tca-post-market-review`; its Skill owns the analysis and artifact contract.
- Pre-market automation explicitly triggers `$tca-pre-market-analysis`; its Skill owns the analysis and artifact contract.
- `agent/daily_analysis_prompt.md` and `agent/post_market_analysis_prompt.md` remain thin compatibility triggers only.
- Legacy OpenClaw/general coaching prompts are archived under `docs/legacy-prompts/` and are not part of scheduled report generation.

## Canonical data flow
1. After market close, generate one reusable daily snapshot:
   ```bash
   python3 script/prepare_market_snapshot.py --watchlist config/watchlist.json --skip-non-trading-day --include-journal-signals --include-position-symbols
   ```
   Optional dynamic universe:
   ```bash
   python3 script/prepare_market_snapshot.py --watchlist config/watchlist.json --skip-non-trading-day --sp500-screen --sp500-top 100 --sp500-candidates 15 --include-journal-signals --include-position-symbols
   ```
2. The snapshot writes:
   - `raw_data/<SNAPSHOT_DATE>/<INTERVAL>/<SYMBOL>.json`
   - `report/<SNAPSHOT_DATE>/daily-snapshot.json`
   - with `--sp500-screen`: `report/<SNAPSHOT_DATE>/candidate-universe.json`
3. Post-market review reads the snapshot and writes:
   - `report/<SNAPSHOT_DATE>/post-market.md`
   - `report/<SNAPSHOT_DATE>/post-market-signals.json`
4. Record LLM report-generation provenance after Codex writes the report artifacts:
   ```bash
   python3 script/trading_copilot.py llm-generation-manifest --session post-market --date <SNAPSHOT_DATE> --model <MODEL> --prompt .codex/skills/tca-post-market-review/SKILL.md --input report/<SNAPSHOT_DATE>/daily-snapshot.json --generated-output report/<SNAPSHOT_DATE>/post-market.md --generated-output report/<SNAPSHOT_DATE>/post-market-signals.json
   ```
5. Validate and deliver the generated post-market bundle through the deterministic gates:
   ```bash
   python3 script/trading_copilot.py post-market-deliver --date <SNAPSHOT_DATE> --sync-longbridge --execute-sync --append-outcomes --append-lessons --append-self-review
   ```
   This wrapper runs validation, data-quality, focus-selection, outcome backfill, journal append, optional read-only account/position review, plan/learning/self review, same-day workflow review, Feishu summary, run manifest, and optional Longbridge watchlist sync.
6. Validate the generated post-market artifacts manually only when debugging an individual gate:
   ```bash
   python3 script/trading_copilot.py validate-report --session post-market --date <SNAPSHOT_DATE>
   python3 script/trading_copilot.py validate-trade-plan --session post-market --date <SNAPSHOT_DATE>
   ```
7. Backfill outcomes for plans whose target date is the completed snapshot date:
   ```bash
   python3 script/trading_copilot.py backfill-signal-outcomes --date <SNAPSHOT_DATE> --append
   ```
   If this fails, send or log a status note, but do not treat it as a trading report quality failure.
8. Append the focused post-market observation plan to `runtime/journal/signals.jsonl`:
   ```bash
   python3 script/trading_copilot.py extract-report-signals --session post-market --date <SNAPSHOT_DATE> --require-validation --append
   ```
9. Generate the plan review and candidate lessons:
   ```bash
   python3 script/trading_copilot.py plan-review --date <SNAPSHOT_DATE> --append-lessons
   ```
10. Generate the daily self-review:
   ```bash
   python3 script/trading_copilot.py daily-self-review --date <SNAPSHOT_DATE> --append
   ```
11. Generate the same-day workflow review when debugging the deterministic delivery wrapper:
   ```bash
   python3 script/trading_copilot.py daily-workflow-review --date <SNAPSHOT_DATE>
   ```
   `post-market-deliver` runs this automatically before `feishu-summary` and writes `report/<SNAPSHOT_DATE>/workflow-review.json` plus `report/<SNAPSHOT_DATE>/workflow-review.md`.
12. Post-market Longbridge sync fully replaces the `今日关注` group from the generated post-market focus list:
   ```bash
   python3 script/trading_copilot.py sync-longbridge-watchlist --session post-market --date <SNAPSHOT_DATE> --group-name 今日关注 --sync-mode replace --require-validation --execute --no-create
   ```
   This removes stale symbols from the `今日关注` group only; it must not globally unfollow securities or remove them from other watchlists.
13. Next pre-market context reuses the previous trading day's snapshot:
   ```bash
   python3 script/trading_copilot.py pre-market-plan --watchlist config/watchlist.json --skip-non-trading-day
   ```
14. Inspect the pre-market context when the automation or operator needs a stable schema summary:
   ```bash
   python3 script/trading_copilot.py inspect-pre-market-context --date <PRE_MARKET_DATE>
   ```
15. Pre-market report generation reads:
   - `report/<PRE_MARKET_DATE>/pre-market-context.json`
   - `report/<PRE_MARKET_DATE>/external-disclosures/trump-trades.json` when available
16. Pre-market output writes:
   - `report/<PRE_MARKET_DATE>/exec-brief.md`
   - `report/<PRE_MARKET_DATE>/pre-market.md`
   - `report/<PRE_MARKET_DATE>/pre-market-signals.json`
   - both Markdown reports must include `## 消息层汇总` with a dedicated `### 特朗普持仓与交易变化` subsection; if no structured or freshly verified disclosure input is available, the subsection must explicitly state the data gap.
17. Record LLM report-generation provenance after Codex writes the report artifacts:
   ```bash
   python3 script/trading_copilot.py llm-generation-manifest --session pre-market --date <PRE_MARKET_DATE> --model <MODEL> --prompt .codex/skills/tca-pre-market-analysis/SKILL.md --input report/<PRE_MARKET_DATE>/pre-market-context.json --generated-output report/<PRE_MARKET_DATE>/exec-brief.md --generated-output report/<PRE_MARKET_DATE>/pre-market.md --generated-output report/<PRE_MARKET_DATE>/pre-market-signals.json
   ```
18. Validate and deliver the generated pre-market bundle through the deterministic gates:
   ```bash
   python3 script/trading_copilot.py pre-market-deliver --date <PRE_MARKET_DATE> --sync-longbridge --execute-sync
   ```
   This wrapper runs validation, data-quality, focus-selection, journal append, optional read-only account/position review, Feishu summary, run manifest, and optional Longbridge watchlist sync.
19. Validate the generated pre-market reports manually only when debugging an individual gate:
   ```bash
   python3 script/trading_copilot.py validate-report --session pre-market --date <PRE_MARKET_DATE>
   python3 script/trading_copilot.py validate-trade-plan --session pre-market --date <PRE_MARKET_DATE>
   ```
20. Run the manual append/review/sync commands only when debugging the deterministic delivery wrapper:
   ```bash
   python3 script/trading_copilot.py extract-report-signals --session pre-market --date <PRE_MARKET_DATE> --require-validation --append
   python3 script/trading_copilot.py account-snapshot --date <PRE_MARKET_DATE>
   python3 script/trading_copilot.py position-review --date <PRE_MARKET_DATE> --append
   python3 script/trading_copilot.py sync-longbridge-watchlist --session pre-market --date <PRE_MARKET_DATE> --group-name 今日关注 --sync-mode add --require-validation --execute --no-create
   ```

## Data freshness rules
- `daily-snapshot.json` contains `latest_bar_dates` and `stale_data`.
- If `stale_data=true`, Codex must not treat the snapshot as the completed session for `snapshot_date`.
- For post-market automation, `stale_data=true` should produce a skip/status note instead of a formal post-market review.
- For pre-market automation, stale data may still be usable only if the source snapshot is intentionally the previous completed trading session.

## cc connect-triggered Codex prompts

### Post-market task
Run:
```bash
python3 script/prepare_market_snapshot.py --watchlist config/watchlist.json --skip-non-trading-day --sp500-screen --sp500-top 100 --sp500-candidates 15 --include-journal-signals --include-position-symbols
```

If output contains `skipped=true`, stop. If the generated `daily-snapshot.json` contains `stale_data=true`, write a short status note and stop. Otherwise use `$tca-post-market-review` with the canonical rulebook and `report/<SNAPSHOT_DATE>/daily-snapshot.json`, then generate `report/<SNAPSHOT_DATE>/post-market.md` and `report/<SNAPSHOT_DATE>/post-market-signals.json`.

The dynamic universe uses iShares IVV holdings CSV as the default source and falls back to Slickcharts if the primary source fails. If the screener itself fails, the snapshot still continues with the fixed watchlist and records the failure in `candidate-universe.json`.

After `post-market.md` is generated, record the LLM generation manifest and hand the bundle to the deterministic delivery wrapper:
```bash
python3 script/trading_copilot.py llm-generation-manifest --session post-market --date <SNAPSHOT_DATE> --model <MODEL> --prompt .codex/skills/tca-post-market-review/SKILL.md --input report/<SNAPSHOT_DATE>/daily-snapshot.json --generated-output report/<SNAPSHOT_DATE>/post-market.md --generated-output report/<SNAPSHOT_DATE>/post-market-signals.json
python3 script/trading_copilot.py post-market-deliver --date <SNAPSHOT_DATE> --sync-longbridge --execute-sync --append-outcomes --append-lessons --append-self-review
```

The wrapper is a full replacement of the old manual validation/backfill/extract/review/summary/sync sequence. It generates `workflow-review.json/md` before the Feishu summary so same-day pre-market, intraday, and post-market execution can be reviewed with price evidence. `--sync-longbridge --execute-sync` replaces the `今日关注` group for tomorrow's focus list. Removing a symbol here only removes it from `今日关注`; do not delete the security globally or from other Longbridge watchlist groups.

### Pre-market task
Run:
```bash
python3 script/trading_copilot.py pre-market-plan --watchlist config/watchlist.json --skip-non-trading-day
```

The wrapper runs `external_disclosure_provider.py` by default and writes `report/<PRE_MARKET_DATE>/external-disclosures/trump-trades.json`. If the disclosure source fails, keep the generated status artifact as message-layer context and continue with the report; the report must state the data gap. Use `--no-external-disclosures` only when this source is intentionally disabled.

If output contains `skipped=true`, stop. Otherwise use `$tca-pre-market-analysis` with the canonical rulebook and `report/<PRE_MARKET_DATE>/pre-market-context.json`, then generate:
- `report/<PRE_MARKET_DATE>/exec-brief.md`
- `report/<PRE_MARKET_DATE>/pre-market.md`
- `report/<PRE_MARKET_DATE>/pre-market-signals.json`

The two Markdown reports must include a `## 消息层汇总` section with a dedicated `### 特朗普持仓与交易变化` subsection. This subsection may summarize OGE/Open Cabinet/Quiver/InsiderCat disclosure evidence when such input is available, but it must not invent missing holdings or trades. 消息层本身不能升级 `watch_only` or `no_trade` symbols into `conditional_executable`; 报告生成 LLM may still mark a symbol `conditional_executable` only when price action, refined setup rules, risk framing, and a 完整 Trade Plan Card independently support the upgrade.

After `exec-brief.md` and `pre-market.md` are generated, record the LLM generation manifest and hand the bundle to the deterministic delivery wrapper:
```bash
python3 script/trading_copilot.py llm-generation-manifest --session pre-market --date <PRE_MARKET_DATE> --model <MODEL> --prompt .codex/skills/tca-pre-market-analysis/SKILL.md --input report/<PRE_MARKET_DATE>/pre-market-context.json --generated-output report/<PRE_MARKET_DATE>/exec-brief.md --generated-output report/<PRE_MARKET_DATE>/pre-market.md --generated-output report/<PRE_MARKET_DATE>/pre-market-signals.json
python3 script/trading_copilot.py pre-market-deliver --date <PRE_MARKET_DATE> --sync-longbridge --execute-sync
```

The wrapper is additive for `今日关注` by default. It may add new focus symbols from the pre-market plan, but it must not remove existing `今日关注` symbols.

## Analysis boundaries
- Reports are research and process support only; they are not investment advice.
- Paper execution tasks may consume validated report artifacts, but report tasks must not submit, cancel, replace, or modify broker orders.
- Do not output deterministic buy/sell instructions.
- Every candidate must include setup reference, trigger, invalidation, and risk constraint.
- `--require-validation` on report extraction and Longbridge sync runs both report validation and trade-plan validation; do not continue when either gate fails.
- If market regime is unclear, data is insufficient, or refined rules do not support a setup, output `NO TRADE`.
- Dynamic S&P 500 candidates are only an observation universe; they must still pass refined setup rules before appearing as executable candidates.
- Trump holdings and trade disclosures are news-layer evidence only. They may identify overlap with the current observation universe and risk context, but they are not trading signals and must not alter watchlist membership or raise execution readiness.
