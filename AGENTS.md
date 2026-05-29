# Agent instructions (scope: repository)

## Scope and layout
- This is a single Python trading-copilot project, not a monorepo.
- `script/`: executable Python tools for market-data fetches, report context generation, monitor scans, read-only account snapshots, paper-trading previews/reviews, report delivery guards, and knowledge import.
- `agent/`: Codex App automation execution prompts. Keep only prompts that automation actually reads.
- `docs/`: runbooks for Codex App automation and human operation.
- `knowledge/refined/`: approved trading rules. Use this for trading conclusions.
- `knowledge/source/`: raw/imported reference material. Treat as research input, not production rule authority.
- `config/`: watchlists and local runtime state paths. Secrets live in `.env`, never in tracked files.
- Generated runtime data belongs in ignored `raw_data/`, `report/`, `runtime/`, `config/rate_limit_state.json`, and `config/longbridge_rate_limit_state.json`.

## Component map
| Area | Path | Owns | Primary commands | Nested guidance |
|---|---|---|---|---|
| Scripts | `script/` | market data providers, trading-day guard, daily snapshot generation, report context generation, monitor scan | `python3 script/prepare_market_snapshot.py --watchlist config/watchlist.json --skip-non-trading-day` | `script/AGENTS.md` |
| Agent prompts | `agent/` | Daily report generation prompts used by automation | Read/edit Markdown prompts | `agent/AGENTS.md` |
| Docs | `docs/` | Automation runbooks and operation notes | Read Markdown docs | none |
| Knowledge base | `knowledge/` | Refined trading rules and source imports | `python3 script/import_priceactions_knowledge.py` | `knowledge/AGENTS.md` |

## Trading safety rules
- `AGENTS.md` is engineering guidance for maintaining this repo; it is not a trading-analysis prompt.
- This repo supports research and process discipline only; do not present output as investment advice.
- Longbridge account workflows must be read-only. Paper-trading broker writes are allowed only in dedicated paper execution workflows, only against `lb_papertrading`, and only when both `--execute` and `TRADING_COPILOT_PAPER_EXECUTION=enabled` are present. Real-account writes remain forbidden.
- Do not output deterministic buy/sell instructions. Use scenarios, triggers, invalidation, risk, and `NO TRADE` where appropriate.
- For current/recent symbol analysis, fetch real market data first through the repository market-data provider stack or clearly state that no concrete price conclusion can be made.
- Batch data fetches must respect provider rate-limit state files. Longbridge is the primary source; Twelve Data is the fallback source.
- S&P 500 dynamic candidates are an observation universe only; they must not be treated as trading recommendations or written back to the fixed watchlist.
- Do not invent prices, indicators, setup rules, or market state when data or refined rules are missing.

## Cross-component workflows
- Daily snapshot flow:
  - `python3 script/prepare_market_snapshot.py --watchlist config/watchlist.json --skip-non-trading-day`
  - Writes raw bars to `raw_data/<SNAPSHOT_DATE>/<INTERVAL>/<SYMBOL>.json`.
  - Writes the reusable snapshot to `report/<SNAPSHOT_DATE>/daily-snapshot.json`.
- Optional S&P 500 dynamic universe:
  - `python3 script/prepare_market_snapshot.py --watchlist config/watchlist.json --skip-non-trading-day --sp500-screen --sp500-top 100 --sp500-candidates 15`
  - Uses iShares IVV holdings CSV as the default S&P 500 universe source, writes `report/<SNAPSHOT_DATE>/candidate-universe.json`, and merges selected candidates into the snapshot without editing `config/watchlist.json`.
- Post-market review flow:
  - Agent reads `agent/post_market_analysis_prompt.md`, `knowledge/refined/`, and `report/<SNAPSHOT_DATE>/daily-snapshot.json`.
  - Agent writes `report/<SNAPSHOT_DATE>/post-market.md` and `report/<SNAPSHOT_DATE>/post-market-signals.json`.
  - Run `python3 script/trading_copilot.py validate-trade-plan --session post-market --date <SNAPSHOT_DATE>` before journal append or sync.
  - Run `python3 script/trading_copilot.py data-quality --date <SNAPSHOT_DATE>` before Feishu summary so focused-symbol fallback and stale data are disclosed.
  - Run account/position review before `plan-review` when account context is enabled, so plan review can include position discipline.
  - Run `python3 script/trading_copilot.py plan-review --date <SNAPSHOT_DATE> --append-lessons` after outcomes, signals, and optional position review are appended.
  - Run `python3 script/trading_copilot.py learning-review --lookback-days 20` to aggregate repeated candidate lessons. Only promote with `promote-lesson --apply` after explicit human approval.
  - Run `python3 script/trading_copilot.py feishu-summary --session post-market --date <SNAPSHOT_DATE>` for concise Feishu delivery.
- Pre-market plan flow:
  - `python3 script/prepare_daily_context.py --watchlist config/watchlist.json --skip-non-trading-day`
  - Agent reads `agent/daily_analysis_prompt.md`, `knowledge/refined/`, and `report/<PRE_MARKET_DATE>/pre-market-context.json`.
  - Agent writes `report/<PRE_MARKET_DATE>/exec-brief.md`, `report/<PRE_MARKET_DATE>/pre-market.md`, and `report/<PRE_MARKET_DATE>/pre-market-signals.json`.
  - Run `python3 script/trading_copilot.py validate-trade-plan --session pre-market --date <PRE_MARKET_DATE>` before journal append or sync.
- Direct scripted report flow:
  - `python3 script/pre_market_report.py --watchlist config/watchlist.json`
  - Produces generated report files and raw market data under ignored runtime directories.
- Monitoring flow:
  - `python3 script/monitor_scan.py --state config/monitor_state.json --interval 5min`
  - Uses Longbridge market data by default, falls back to Twelve Data when configured, and writes `report/latest-monitor.json`.
- Read-only position review flow:
  - `python3 script/trading_copilot.py account-snapshot --date <DATE>`
  - `python3 script/trading_copilot.py position-review --date <DATE> --append`
- Knowledge import flow:
  - Edit/import raw material under `knowledge/source/priceactions/docs/`.
  - Run `python3 script/import_priceactions_knowledge.py` to refresh metadata under `knowledge/source/priceactions/meta/`.
  - Promote only reviewed rules into `knowledge/refined/`.

## Verification
- Install: `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`.
- Syntax check: `python3 -m py_compile script/*.py`.
- Trading-day guard smoke test: `python3 script/trading_day_guard.py --date 2026-05-06 --format text`.
- Data-fetch smoke tests use Longbridge CLI by default. Twelve Data fallback tests require `.env` with `TWELVE_DATA_API_KEY`.
- Prefer quiet first runs. When debugging a specific symbol or script, re-run the narrow command with fewer symbols or smaller `--outputsize`.

## Global conventions
- Keep Python scripts standard-library-only unless `requirements.txt` is intentionally updated.
- Read large knowledge files only when the task requires them; prefer `knowledge/refined/` before `knowledge/source/`.
- Preserve simplified Chinese output contracts in prompts and generated reports.
- Never commit `.env`, generated `raw_data/`, generated `report/`, or local runtime state.
