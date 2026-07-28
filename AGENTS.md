# Agent instructions (scope: repository)

## Scope and layout
- This is a single Python trading-copilot project, not a monorepo.
- `script/`: executable Python tools for market-data fetches, report context generation, monitor scans, read-only account snapshots, paper-trading previews/reviews, report delivery guards, and knowledge import.
- `agent/`: thin Codex App compatibility triggers. Analysis workflows live in repo-only Skills under `.codex/skills/`.
- `docs/`: runbooks for Codex App automation and human operation.
- `config/knowledge_source.json`: canonical Obsidian-vault rulebook location. Use it for all approved trading-rule reads; `TCA_KNOWLEDGE_ROOT` may override it by deployment.
- The local `knowledge/` directory is limited to runtime learning candidates and is not a source of approved trading rules.
- `config/`: watchlists and local runtime state paths. Secrets live in `.env`, never in tracked files.
- Generated runtime data belongs in ignored `raw_data/`, `report/`, `runtime/`, `config/rate_limit_state.json`, and `config/longbridge_rate_limit_state.json`.
- Feishu summaries render `数据质量`, `运行校验`, and `交付审计` as one sentence each and omit a standalone `边界` section.

## Component map
| Area | Path | Owns | Primary commands | Nested guidance |
|---|---|---|---|---|
| Scripts | `script/` | market data providers, trading-day guard, daily snapshot generation, report context generation, monitor scan | `python3 script/prepare_market_snapshot.py --watchlist config/watchlist.json --skip-non-trading-day` | `script/AGENTS.md` |
| Agent prompts | `agent/` | Daily report generation prompts used by automation | Read/edit Markdown prompts | `agent/AGENTS.md` |
| Docs | `docs/` | Automation runbooks and operation notes | Read Markdown docs | none |
| Canonical rulebook | `config/knowledge_source.json` → Obsidian vault | Approved price-action rules and raw provenance | `script/knowledge_source.py` consumers | vault `topics/trading` |
| Runtime learning | `knowledge/evolution/` | Candidate lessons and explicit human-review evidence | `python3 script/learning_review.py` | `knowledge/AGENTS.md` |

## Trading safety rules
- `AGENTS.md` is engineering guidance for maintaining this repo; it is not a trading-analysis prompt.
- This repo supports research and process discipline only; do not present output as investment advice.
- Longbridge account workflows must be read-only. Paper-trading broker writes are allowed only in dedicated paper execution workflows, only against `lb_papertrading`, and only when both `--execute` and the matching `config/paper_execution.json` action gate are enabled. Real-account writes remain forbidden.
- Do not output deterministic buy/sell instructions. Use scenarios, triggers, invalidation, risk, and `NO TRADE` where appropriate.
- For current/recent symbol analysis, fetch real market data first through the repository market-data provider stack or clearly state that no concrete price conclusion can be made.
- Batch data fetches must respect provider rate-limit state files. Longbridge is the primary source; Twelve Data is the fallback source.
- S&P 500 dynamic candidates are an observation universe only; they must not be treated as trading recommendations or written back to the fixed watchlist.
- Do not invent prices, indicators, setup rules, or market state when data or canonical rules are missing.

## Longbridge data-source routing
- For interactive Codex market research and symbol analysis, prefer the connected Longbridge app/MCP read-only tools when they are available.
- For repository Python scripts, scheduled automations, batch snapshots, report-context generation, and monitor scans, continue using the existing Longbridge CLI provider stack. Do not assume Codex app/MCP tools are directly callable from Python or unattended shell jobs.
- In interactive work, if Longbridge MCP is unavailable or incomplete, use the repository Longbridge CLI path. Use Twelve Data only after the applicable Longbridge path fails, and disclose the fallback in the user-facing result or generated artifact.
- In scheduled or scripted work, keep Longbridge CLI as primary and Twelve Data as fallback. MCP fallback requires an explicit Codex-orchestrated workflow that normalizes and persists MCP results; it is not enabled by repository configuration alone.
- Keep all Longbridge real-account app/MCP usage read-only. Do not call order submission, replacement, cancellation, or other real-account mutation tools.
- Record the actual provider/backend in generated artifacts whenever the workflow supports source metadata; never label MCP-derived data as CLI-derived data or the reverse.
- Daily Codex pre-market/post-market analysis may read both IBKR and Longbridge
  connected-app positions and account metrics through read-only tools. Persist
  structured payloads only under ignored `runtime/account/<DATE>/`, normalize them
  with `plugin-account-snapshot`, retain broker provenance, and pass the normalized
  artifact explicitly to delivery. Repository Python and unattended shell jobs must
  not assume they can call app/MCP tools directly.
- Cross-broker holdings must be aggregated by symbol/currency for portfolio exposure
  while preserving broker rows. Never synthesize FX conversions or combined
  concentration when currencies/account totals are incomplete.
- Post-market Codex analysis may read IBKR/Longbridge completed executions and order
  context through read-only tools. Normalize them with `plugin-trade-snapshot`;
  executions are facts, orders are context only. Never infer "no trades" from a
  failed broker scope, and never auto-append real-account fills to `trades.jsonl`.
- Pre-market holding advice must remain conditional: use `HOLD_WATCH`, `NO_ADD`,
  `RISK_REVIEW`, `EXIT_IF_INVALIDATED`, or `DATA_INSUFFICIENT` with explicit
  structure, invalidation, and risk conditions. Never issue deterministic position
  changes; losing positions default to `NO_ADD`.

## Cross-component workflows
- Daily snapshot flow:
  - `python3 script/prepare_market_snapshot.py --watchlist config/watchlist.json --skip-non-trading-day`
  - Writes Longbridge-first raw bars to `raw_data/<SNAPSHOT_DATE>/<INTERVAL>/<SYMBOL>.json`; default intervals are `1day`, `1h`, `15min`, and `5min`.
  - Writes the reusable snapshot to `report/<SNAPSHOT_DATE>/daily-snapshot.json`.
- Optional S&P 500 dynamic universe:
  - `python3 script/prepare_market_snapshot.py --watchlist config/watchlist.json --skip-non-trading-day --sp500-screen --sp500-top 100 --sp500-candidates 15`
  - Uses iShares IVV holdings CSV as the default S&P 500 universe source, writes `report/<SNAPSHOT_DATE>/candidate-universe.json`, and merges selected candidates into the snapshot without editing `config/watchlist.json`.
- Post-market review flow:
  - Agent uses `.codex/skills/tca-post-market-review/SKILL.md`, the canonical rulebook returned in `next_agent_inputs`, and `report/<SNAPSHOT_DATE>/daily-snapshot.json`; `agent/post_market_analysis_prompt.md` is only a thin trigger.
  - Agent writes `report/<SNAPSHOT_DATE>/post-market.md` and `report/<SNAPSHOT_DATE>/post-market-signals.json`.
  - Run `python3 script/trading_copilot.py validate-trade-plan --session post-market --date <SNAPSHOT_DATE>` before journal append or sync.
  - Run `python3 script/trading_copilot.py data-quality --date <SNAPSHOT_DATE>` before Feishu summary so focused-symbol fallback and stale data are disclosed.
  - Run account/position review before `plan-review` when account context is enabled, so plan review can include position discipline.
  - Run `python3 script/trading_copilot.py plan-review --date <SNAPSHOT_DATE> --append-lessons` after outcomes, signals, and optional position review are appended.
  - Run `python3 script/trading_copilot.py learning-review --lookback-days 20` to aggregate repeated candidate lessons. Only promote with `promote-lesson --apply` after explicit human approval.
  - Run `python3 script/trading_copilot.py feishu-summary --session post-market --date <SNAPSHOT_DATE>` for concise Feishu delivery.
- Pre-market plan flow:
  - `python3 script/prepare_daily_context.py --watchlist config/watchlist.json --skip-non-trading-day`
  - Agent uses `.codex/skills/tca-pre-market-analysis/SKILL.md`, the canonical rulebook returned in `next_agent_inputs`, and `report/<PRE_MARKET_DATE>/pre-market-context.json`; `agent/daily_analysis_prompt.md` is only a thin trigger.
  - Agent writes `report/<PRE_MARKET_DATE>/exec-brief.md`, `report/<PRE_MARKET_DATE>/pre-market.md`, and `report/<PRE_MARKET_DATE>/pre-market-signals.json`.
  - Run `python3 script/trading_copilot.py validate-trade-plan --session pre-market --date <PRE_MARKET_DATE>` before journal append or sync.
- Direct scripted report flow:
  - `python3 script/pre_market_report.py --watchlist config/watchlist.json`
  - Produces generated report files and raw market data under ignored runtime directories.
- Monitoring flow:
  - `python3 script/monitor_scan.py --state config/monitor_state.json --interval 5min`
  - Uses Longbridge market data by default for 5m, 15m, 1h, and daily evidence, falls back to Twelve Data when configured, and writes `report/latest-monitor.json`.
- Read-only position review flow:
  - Codex app path: `python3 script/trading_copilot.py plugin-account-snapshot --date <DATE> ...`
  - `python3 script/trading_copilot.py account-snapshot --date <DATE>`
  - `python3 script/trading_copilot.py position-review --date <DATE> --append`
- Knowledge update flow:
  - Edit raw materials and approved rules only in the canonical Obsidian vault.
  - Keep `knowledge/evolution/` as non-authoritative runtime evidence.
  - Promote candidates into the vault rulebook only after explicit human approval.

## Verification
- Install: `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`.
- Syntax check: `python3 -m py_compile script/*.py`.
- Trading-day guard smoke test: `python3 script/trading_day_guard.py --date 2026-05-06 --format text`.
- Data-fetch smoke tests use Longbridge CLI by default. Twelve Data fallback tests require `.env` with `TWELVE_DATA_API_KEY`.
- Prefer quiet first runs. When debugging a specific symbol or script, re-run the narrow command with fewer symbols or smaller `--outputsize`.

## Global conventions
- Keep Python scripts standard-library-only unless `requirements.txt` is intentionally updated.
- Read large knowledge files only when the task requires them; prefer the canonical rulebook before raw vault sources.
- Preserve simplified Chinese output contracts in prompts and generated reports.
- Never commit `.env`, generated `raw_data/`, generated `report/`, or local runtime state.
