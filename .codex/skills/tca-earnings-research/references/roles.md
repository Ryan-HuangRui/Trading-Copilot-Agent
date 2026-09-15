# Roles and orchestration

| Role | Work | Output | Profile |
|---|---|---|---|
| company | Analyze one issuer/event and relevant segments from originals | company report | daily; review for a justified complex case |
| industry | Compare issuer evidence and supply-chain relationships | industry draft | daily for incremental; quarterly for full quarter |
| challenger | Independently inspect raw evidence and adverse/omitted samples, then examine the draft | challenge report | review |
| synthesizer | Resolve supported, opposed and unresolved claims; produce industry or cross-industry judgment | synthesis report | quarterly |
| publication writer | Explain accepted research to a non-specialist without strengthening it | reader Markdown draft | daily |
| publication checker | Compare draft with frozen research for facts, periods, links, counterevidence and readability | semantic check | review |
| gap reviewer | Independently dispose frozen coverage limitations and audit omitted/negative samples; unresolved evidence stays unresolved | hash-bound gap review | review |

The deterministic coordinator owns due checks, leases, input manifests, allowed model profiles, task dependencies and delivery. It is not an LLM role. An LLM role may request missing evidence in its output, but must not recursively launch unrestricted research.

Daily DAG: company tasks -> affected-industry tasks -> validation. A bounded challenger is added only for a material contradiction or important thesis revision. Quarterly DAG: deterministic coverage audit -> bounded independent gap reviewer -> industry draft -> independent challenge -> synthesis -> validation -> reader writer -> checker. A changed accepted-company fingerprint may supersede an incomplete stage edition without waiting for the cross-industry market stage. Cross-industry synthesis is an independent quarterly role and its formal edition depends on every frozen industry report; an explicitly labeled stage overview may expose missing dependencies at deadline.

Each role has its own task_id/run directory; no shared writable final report. It receives an evidence manifest, role, scope, cutoff, profile and permitted output paths. Initial concurrency is one; future parallel company tasks must remain isolated. A challenger first records findings from originals without treating the draft as ground truth, then reviews the draft. Synthesis cites the challenge dispositions. Agreement between agents is not additional source evidence.

Do not invoke Vibe Swarm as an implicit dependency. Its existing allowlisted preset/model limits remain separate. A future adapter must be explicit and persist actual provenance.
