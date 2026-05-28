# Agent instructions (scope: knowledge/)

## Scope and layout
- `refined/global/`: approved non-negotiable global rules for analysis and risk management.
- `refined/setups/`: approved setup-specific rules.
- `evolution/`: reviewed or candidate learning artifacts from plan-review. Use as process feedback, not as approved setup rules unless promoted by a human.
- `source/`: raw or imported reference material used for research and normalization.
- `source/priceactions/meta/`: generated metadata from `script/import_priceactions_knowledge.py`.

## Rule priority
1. `refined/global/trading_philosophy.md`
2. `refined/global/market_regime_preconditions.md`
3. `refined/setups/*.md`
4. `refined/global/risk_position_management.md`
5. `refined/global/decision_process_checklist.md`
6. `refined/global/conflict_resolution.md`
7. `evolution/validated_lessons.md` as soft process feedback only; it must not override refined rules.

## Conventions
- Trading conclusions must be grounded in `knowledge/refined/`.
- `knowledge/source/` is research/input material only; do not cite it as an active trading rule unless the user is explicitly asking about source material.
- Promote source material into `refined/` only after simplifying it into explicit, testable rules.
- Promote learning artifacts into `refined/` only after repeated evidence and explicit human approval.
- When changing setup rules, keep regime applicability, trigger, invalidation, failure mode, and risk notes explicit.
- When changing global rules, check `agent/` execution prompts and `docs/` runbooks that repeat the same constraint.

## Commands
- Refresh source metadata after PriceActions source changes: `python3 script/import_priceactions_knowledge.py`.

## Do not
- Do not bulk rewrite imported source docs unless the task is an import/normalization task.
- Do not mix unreviewed source terminology into `refined/` without documenting the intended rule meaning.
