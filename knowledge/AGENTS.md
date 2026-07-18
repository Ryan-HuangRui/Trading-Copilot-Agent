# Agent instructions (scope: knowledge/)

## Scope and layout
- The canonical Obsidian vault owns all approved global rules, setup-specific rules, raw sources, and source metadata. Its location is resolved by `config/knowledge_source.json` or `TCA_KNOWLEDGE_ROOT`.
- `evolution/`: reviewed or candidate learning artifacts from plan-review. Use as process feedback, not as approved setup rules unless promoted by a human into the vault.

## Rule priority
1. canonical rulebook `global/trading_philosophy.md`
2. canonical rulebook `global/market_regime_preconditions.md`
3. canonical rulebook `setups/*.md`
4. canonical rulebook `global/risk_position_management.md`
5. canonical rulebook `global/decision_process_checklist.md`
6. canonical rulebook `global/conflict_resolution.md`
7. `evolution/validated_lessons.md` as soft process feedback only; it must not override refined rules.

## Conventions
- Trading conclusions must be grounded in the canonical rulebook.
- Vault raw sources are research/input material only; do not cite them as active trading rules unless the user is explicitly asking about source material.
- Promote source material into the vault rulebook only after simplifying it into explicit, testable rules.
- Promote learning artifacts into the vault rulebook only after repeated evidence and explicit human approval.
- When changing setup rules, keep regime applicability, trigger, invalidation, failure mode, and risk notes explicit.
- When changing global rules, check `agent/` execution prompts and `docs/` runbooks that repeat the same constraint.

## Commands
- After a vault rulebook change, rerun Trading Copilot validation; no local knowledge import or sync is permitted.

## Do not
- Do not create a local copy of vault raw sources or approved rules.
- Do not mix unreviewed source terminology into the canonical rulebook without documenting the intended rule meaning.
