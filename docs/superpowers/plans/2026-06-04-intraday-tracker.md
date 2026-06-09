# Intraday Tracker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a three-phase intraday workflow: read-only pre-market plan tracking, monitor dry-run candidate review, and a separately gated paper intraday entry path.

**Architecture:** Add a focused intraday tracker script that reads pre-market signals, a manual watchlist, monitor scan evidence, the prior intraday Markdown log, and structured runtime state. Keep broker writes out of monitor; Phase 3 gets its own command and explicit paper action gate.

**Tech Stack:** Python standard library, existing `trading_copilot.py` wrapper, existing monitor sidecar validation, existing paper preview/submit dry-run and Longbridge paper adapter guards.

---

### Task 1: Phase 1 Read-Only Plan Tracker

**Files:**
- Create: `script/intraday_tracker.py`
- Create: `tests/test_intraday_tracker.py`
- Create: `.codex/skills/intraday-tracker/SKILL.md`
- Modify: `script/trading_copilot.py`
- Modify: `README.md`
- Modify: `docs/contracts/workflows.md`

- [ ] **Step 1: Write failing tests** for merging pre-market topN and manual watchlist symbols, reading/appending `report/<DATE>/intraday.md`, updating `runtime/intraday/<DATE>/state.json`, and appending only changed-state events to `events.jsonl`.
- [ ] **Step 2: Run `python3 -m unittest tests.test_intraday_tracker` and confirm failure** because `intraday_tracker` does not exist.
- [ ] **Step 3: Implement `script/intraday_tracker.py`** with read-only status evaluation and Markdown/state/event outputs.
- [ ] **Step 4: Add `trading_copilot.py intraday-tracker` wrapper** with shared response fields and artifacts.
- [ ] **Step 5: Run targeted tests and commit** with `feat(intraday): add read-only plan tracker`.

### Task 2: Phase 2 Monitor Dry-Run Orchestration

**Files:**
- Modify: `script/trading_copilot.py`
- Modify: `tests/test_trading_copilot_wrapper.py`
- Modify: `README.md`
- Modify: `docs/contracts/workflows.md`

- [ ] **Step 1: Write failing wrapper tests** for `intraday-dry-run` command sequencing: `extract_monitor_signals.py`, `validate_trade_plan.py`, `paper_trade_preview.py`, `paper_trade_submit.py`, and `feishu_summary.py`.
- [ ] **Step 2: Run the targeted wrapper test and confirm failure** because `intraday-dry-run` is unavailable.
- [ ] **Step 3: Implement wrapper orchestration** that never passes `--execute`, uses monitor session, and reports artifacts/counts.
- [ ] **Step 4: Run targeted tests and commit** with `feat(intraday): add monitor dry-run loop`.

### Task 3: Phase 3 Separately Gated Intraday Paper Entry

**Files:**
- Modify: `script/paper_execution_config.py`
- Modify: `script/paper_trade_submit.py`
- Modify: `script/trading_copilot.py`
- Modify: `tests/test_paper_execution_config.py`
- Modify: `tests/test_paper_trade_submit.py`
- Modify: `tests/test_trading_copilot_wrapper.py`
- Modify: `README.md`
- Modify: `docs/paper-execution-runbook.md`

- [ ] **Step 1: Write failing tests** for a new `intraday_entry_submit` gate that is independent from `entry_submit` and disabled by default.
- [ ] **Step 2: Write failing wrapper tests** for `intraday-paper-entry`, including dry-run default and explicit `--execute` gating.
- [ ] **Step 3: Implement config capability and submit support** while keeping plain `paper-trade-submit --session monitor --execute` hard-disabled.
- [ ] **Step 4: Implement the standalone wrapper command** that calls paper submit with action `intraday_entry_submit` only through the explicit intraday workflow.
- [ ] **Step 5: Run targeted tests, `python3 -m py_compile script/*.py`, and commit** with `feat(intraday): gate paper entry workflow`.
