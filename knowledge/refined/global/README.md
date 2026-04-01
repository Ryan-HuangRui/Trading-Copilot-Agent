# Global Rules README

This directory contains **global, non-negotiable trading rules** that apply to **ALL trading analysis and decisions** performed by the agent.

These rules define:
- How the market must be interpreted
- How risk must be managed
- How decisions must be made
- How conflicts must be resolved

They are **foundational constraints**, not optional guidelines.

---

## Directory Structure

```

knowledge/refined/global/
├── trading_philosophy.md
├── market_regime_preconditions.md
├── risk_position_management.md
├── decision_process_checklist.md
├── conflict_resolution.md
└── README.md

```

---

## Rule Priority (VERY IMPORTANT)

When performing any analysis, rules must be applied in the following order:

1. **Trading Philosophy**
2. **Market Regime & Preconditions**
3. **Specific Setup Rules**
4. **Risk & Position Management**
5. **Decision Checklist**
6. **Conflict Resolution**

If any higher-priority rule is violated, the trade idea is **INVALID**, even if a setup appears to trigger.

---

## How the Agent Must Use These Files

### 1. Mandatory Global Application
- Every analysis MUST comply with all files in this directory.
- Global rules apply **before** any specific setup is considered.

### 2. Setup Is Not Sufficient by Itself
A valid trade idea requires:
- A matching setup from `knowledge/refined/setups/`
- AND full compliance with **ALL global rules**

If a setup triggers but violates global rules, the correct output is:

```

NO TRADE – Global rules not satisfied

```

---

## Market Regime Gate (Hard Gate)

Before evaluating any setup, the agent MUST:
1. Identify the current market regime
2. Explicitly state the regime
3. Confirm that the selected setup is valid for that regime

If the market regime is:
- Tight Trading Range (Barb Wire)
- Unclear / Not identifiable

→ **NO TRADE**

---

## Risk Rules Are Absolute

The following rules are **non-negotiable**:
- Risk per trade must not exceed 1–2%
- No averaging down in losing positions
- Physical stop-loss must exist at entry
- Position size must be emotionally neutral (“I Don’t Care Size”)

If risk cannot be defined **precisely**, the trade is invalid.

---

## Checklist Enforcement

The **Pre-Trade Checklist** in `decision_process_checklist.md` must be executed **in order**.

If any checklist item fails:
- The agent must stop
- Output must clearly state which checklist item failed
- No workaround is allowed

---

## Conflict Handling

When conflicts arise:
- **Price Action overrides SMC structures**
- **Price Action overrides indicators**
- Indicators must never invalidate price action signals

The agent must explicitly state which rule resolved the conflict.

---

## Source Boundary

- Files under `knowledge/source/` are **reference material only**
- They MUST NOT be used directly for analysis or decision-making
- Only rules that exist in `knowledge/refined/` are considered valid

If a concept exists only in source material but not refined rules:
→ It is treated as **NON-EXISTENT**

---

## Output Requirements (Global)

Every valid trade analysis MUST explicitly include:
- Identified market regime
- Referenced setup file
- Entry trigger
- Invalidation level
- Risk (in R and %)
- Profit-taking logic

Missing any of the above:
→ **INVALID ANALYSIS**

---

## Final Principle

> The purpose of this directory is not to help the agent be creative.  
> It is to **prevent mistakes, ambiguity, and rule-breaking**.

If the agent is unsure:
→ **NO TRADE is always the correct decision.**