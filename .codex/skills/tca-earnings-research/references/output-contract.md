# Output selection

The authoritative field definitions and validation rules are in `docs/contracts/earnings-research.md`; do not maintain a second divergent schema here.

- Company/IPO: structured `company_report.json` plus concise Chinese `company_report.md`.
- Industry daily: `industry_report.json` and Chinese explanation of changes; unchanged theses retain their previous cutoff.
- Challenger: `challenge_report.json`, citing source evidence and the exact disputed claim, including unresolved findings.
- Quarterly/cross-industry synthesis: `synthesis_report.json` and readable Chinese report; include scope, coverage, operating changes, supply-chain evidence, alternative explanations, challenge dispositions and next tests.
- Every role: structured completion metadata with output paths and actual execution provenance.

No synthetic evidence or successful fixture output may masquerade as a live production report. Keep fixture and live provenance explicit. A missing fact stays missing. If only a release is available, label the document-level completeness and do not claim to have reviewed a formal quarterly filing.

Report status expresses emerging/strengthening/validating/weakening/invalidated/insufficient_data; report completeness separately expresses full/partial/insufficient. Every major inference cites evidence and a competing explanation or an explicit reason none was found. A thesis includes an observable invalidation and next review condition.

Do not write a notification for every company. The delivery program builds a combined, self-contained change summary with source links where usable. Data quality, run validation and delivery audit each occupy one sentence; there is no standalone boundary section. Full reports remain archived locally unless a verified repository-owned attachment mechanism is enabled.
