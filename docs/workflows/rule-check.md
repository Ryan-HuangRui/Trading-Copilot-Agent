# Rule Check Workflow

Use when the user asks whether a thesis, setup, report section, or trade plan conforms to the approved rule base.

## Preconditions

- Read `knowledge/refined/global/` before setup-specific files.
- Read only the relevant `knowledge/refined/setups/` files.
- If the thesis depends on missing market data, mark the result `unclear`.

## Steps

1. Extract the thesis into concrete claims.
2. Map each claim to global rules and setup rules.
3. Mark every claim as `pass`, `fail`, or `unclear`.
4. Identify missing data, stale data, and rule conflicts.
5. Rewrite the thesis into a compliant scenario if possible.

## Output Shape

Use a compact table:

| Area | Result | Evidence | Required fix |
|---|---|---|---|
| Market regime | pass/fail/unclear | rule file | change needed |

Then provide:

- Overall result.
- Required edits.
- Whether the compliant conclusion should be `NO TRADE`.

## Boundary

Do not invent missing setup rules. Prefer `NO TRADE` when a thesis depends on unresolved rule gaps.
