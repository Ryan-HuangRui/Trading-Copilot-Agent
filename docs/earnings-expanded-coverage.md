# Expanded earnings coverage

Effective 2026-09-24, `config/earnings_universe.json` contains 10 observation cohorts and 50 unique company ticker seeds. SEC collection resolves issuer identities; the seed file does not assert verified CIKs.

| Added cohort | Company seeds | Key issuers |
|---|---|---|
| banking-credit | JPM, BAC, WFC, C | JPM, BAC |
| energy | XOM, CVX, COP, EOG | XOM, CVX |
| transport-logistics | UNP, CSX, UPS, FDX | UNP, CSX |
| homebuilding | DHI, LEN, PHM, NVR | DHI, LEN |
| pharmaceuticals | LLY, MRK, ABBV, BMY | LLY, MRK |

The research skill's `references/industry-metrics.md` defines each cohort's metrics and comparability limits. Energy separates integrated and upstream businesses; transport separates rail and parcel networks. Pharmaceuticals initially covers commercial-stage businesses rather than pre-revenue development companies.

## Supplemental initialization

The authorized one-off expansion uses ignored `runtime/earnings/supplemental-2026-09-24/config.json` and `deployment.json`, copied from the live production configuration. Initialization allows the 20 new issuers; continuation uses 3,600-second windows and 14,400-second worker generations. The original daily configuration and its limits are unchanged. Company research semantic configuration is unchanged, so accepted results remain reusable by daily monitoring.

Quota reservations use the supplemental round's unique execution-window IDs, not the calendar-day or previous scheduled-window keys. The shared database, leases and process locks prevent simultaneous analysis of the same work. This separates task allowances, not the underlying Codex account usage or serial worker capacity. A scheduled trigger encountered while this worker is active observes the existing worker.

The first cutoff is frozen at round start. Company disclosures first published in the configured calendar window take priority; historical company analysis and standalone publication backfill remain zero. Initialization may archive historical disclosures and enqueue historical tasks to preserve comparative evidence, but this run does not spend analysis allowance on those historical tasks. Later disclosures are admitted by subsequent daily rounds.

The canonical universe feeds subsequent daily collection automatically. Existing quarterly scopes retain frozen membership; new scopes use the expanded universe. All publication checks, evidence gaps, bounded retries and failure stops remain in force. The supplemental launch omits `--send`; no extra chat notification is requested.

Local start result and quota baseline are stored in the supplemental directory. Inspect `runtime/earnings/continuation.sqlite`, `daily.sqlite`, and the round checkpoints to distinguish collection, accepted research, checked publication and terminal blockers. A running background round is not proof of completed analysis.

## Calendar disclosure window correction

The live daily configuration (`runtime/earnings/p4-config.json`) and supplemental configuration explicitly select `disclosure_window = {"start":"2026-09-01","end_exclusive":"2026-11-01"}`. This means September and October public disclosures, bounded by each round's actual public cutoff. The generic config template leaves this optional selector null for explicit historical/manual workflows; production uses the dated runtime configuration. Set a new explicit window for the next reporting cycle; there is no silent fallback to the latest old report.

A June-period report first published in September is eligible. A report published in June is historical even if it remains that company's newest report. Missing in-window disclosures stay waiting. Historical originals may remain archived for comparisons. SEC filing/public availability is used when no earlier issuer release has been registered; this is not a verified issuer earnings-call date. Amendments alone and later fetch timestamps cannot refresh an old event into the current window.

The selector gates company claims, daily industry inputs, quarterly company membership, and publication discovery/recovery. Industry and market manifests retain the selector. Existing historical outputs remain archived; they cannot be reused as current industry outputs without rebuilding under the new selector. Industry cohorts still compare actual fiscal periods separately, so a common release window does not imply identical operating periods.

The first expansion round was stopped during initialization before any model reservation. Its successor uses a fresh frozen cutoff and independent quota windows after the release-date correction.

Unresolved-period 8-Ks (including personnel changes and investor-day material) are not counted as quarterly releases merely because they were filed in-window; they remain outside current financial coverage until a valid earnings period is established.

## Collection and continuation repair

Window-scoped initialization now discovers all eligible filings from the start of the disclosure window, including earnings releases older than the three-day incremental overlap. A completed window scan does not require eight historical quarters: a newly reorganized registrant such as XOM can finish current discovery while historical depth remains a separate limitation. Pending or failed downloads prevent scan completion. The `sec_disclosure_window` watermark stores the exact window, frozen cutoff and per-issuer coverage; subsequent windows in the same round reuse completed scans and retry incomplete issuers. A newer daily cutoff requires a new scan.

An 8-K becomes a resolved earnings event only when its primary document identifies Item 2.02 and a results exhibit explicitly states a completed quarter-end date. The resolver retains the supporting URL and quote, rejects future dates and leaves ambiguous releases unresolved. It does not use the 8-K event date as the fiscal period. This recovered LEN's September 16 release for the quarter ended August 31, 2026.

Some accepted 8-K research retains a null start date. Fiscal-period review can now derive a calendar start from an explicit `three months ended <month-end date>` statement in the exact cited, archived original, after checking issuer/event, document version/hash, file hash and the report's public cutoff. Week-based calendars and ambiguous evidence remain unresolved. The result preserves original reports and stores source-bound period proofs in publication inputs and their semantic hash. For LEN this yields June 1–August 31, mapped by maximum overlap to natural 2026-Q3; it does not claim an identical July–September operating period. Newly collected releases retain that verified start directly.

Continuation checks collection completeness, terminal blockers and errors before accepting a waiting outcome. Current-window publication failures are reported separately from historical terminal jobs. Mixed-unit reader tables can use an explicit row unit when the column is ambiguous; contradictory row/column units still fail validation. ORCL's unchanged, previously semantically approved draft was recovered with preview-first deterministic revalidation, without another company or writer model call.

The existing enabled daily cron remains at 10:00 Asia/Shanghai and reads the expanded universe and dated production window. Calendar announcements below are monitoring aids, not substitutes for released financial statements and not admission gates. All 20 new companies remain enrolled in SEC discovery even if the announced call is outside October.

## Official calendar audit, 2026-09-24

These are announced dates observed on September 24, not completion claims. `date_unverified` means the retrieved primary material did not establish a next date; it does not prove the company has made no announcement. Call-only entries do not establish the exact release date. The per-company operational audit is stored locally as `runtime/earnings/supplemental-2026-09-24/disclosure-calendar.json`.

| Symbol | Announced date | Evidence type | Primary source |
|---|---|---|---|
| JPM | 2026-10-13 | results_release | [Issuer IR](https://www.jpmorganchase.com/ir/news/2026/jpmc-to-host-third-quarter-2026-earnings-call) |
| BAC | 2026-10-14 | results_release | [Issuer IR](https://newsroom.bankofamerica.com/content/newsroom/press-releases/2025/05/bank-of-america-announces-2026-financial-reporting-dates.html) |
| WFC | 2026-10-13 | earnings_event | [Issuer IR](https://www.wellsfargo.com/about/investor-relations/quarterly-earnings/) |
| C | 2026-10-13 | earnings_call | [Issuer IR](https://www.citigroup.com/global/news/press-release/2025/citi-third-fourth-quarter-2025-and-2026-earnings-calls) |
| XOM | Unverified | date_unverified | [Issuer IR](https://investor.exxonmobil.com/) |
| CVX | Unverified | date_unverified | [Issuer IR](https://www.chevron.com/investors) |
| COP | Unverified | date_unverified | [Issuer IR](https://www.conocophillips.com/investor-relations/investor-presentations/) |
| EOG | 2026-11-06 | earnings_call | [Issuer IR](https://investors.eogresources.com/2026-09-22-EOG-Resources-Schedules-Conference-Call-and-Webcast-of-Third-Quarter-2026-Results-for-November-6,-2026) |
| UNP | Unverified | date_unverified | [Issuer IR](https://investor.unionpacific.com/events-presentations) |
| CSX | Unverified | date_unverified | [Issuer IR](https://investors.csx.com/news-and-events/news/default.aspx) |
| UPS | 2026-10-27 | earnings_call | [Issuer IR](https://investors.ups.com/) |
| FDX | 2026-10-28 | earnings_call | [Issuer IR](https://investors.fedex.com/news-and-events/upcoming-events/upcoming-events-details/2026/FedEx-Earnings-Call/default.aspx) |
| DHI | 2026-10-29 | results_release | [Issuer IR](https://investor.drhorton.com/news-and-events/press-releases/2026/09-10-2026-210511837) |
| LEN | 2026-09-16 | released_results | [Issuer IR](https://newsroom.lennar.com/2026-09-16-Lennar-Reports-Third-Quarter-2026-Results) |
| PHM | 2026-10-22 | results_release | [Issuer IR](https://newsroom.pultegroupinc.com/270499-pultegroup-s-third-quarter-2026-earnings-release-and-webcast-conference-call-scheduled-for-october-22-2026/) |
| NVR | Unverified | date_unverified | [Issuer IR](https://investor.nvrinc.com/) |
| LLY | 2026-10-29 | earnings_call | [Issuer IR](https://investor.lilly.com/webcasts-and-presentations) |
| MRK | Unverified | date_unverified | [Issuer IR](https://www.merck.com/investor-relations/) |
| ABBV | Unverified | date_unverified | [Issuer IR](https://investors.abbvie.com/) |
| BMY | 2026-10-29 | results_release | [Issuer IR](https://investors.bms.com/iframes/press-releases/press-release-details/2026/Bristol-Myers-Squibb-to-Report-Results-for-Third-Quarter-2026-on-October-29-2026/default.aspx) |

LEN is the confirmed released-results entry recovered in this repair. EOG has an announced November 6 call outside the selected window; its release date remains unverified and old June results do not count. The remaining companies continue through daily SEC monitoring; future releases cannot be analyzed in advance.

## Explicitly requested hourly progress monitor

On September 24 the user explicitly requested hourly checks and progress messages for this supplemental run. `script/earnings_heartbeat.py` reads the ledgers in SQLite read-only mode, verifies live worker PIDs, and reports accepted company/industry coverage, checked publications, pending publication jobs and collection failures. It never invokes a model or starts research. The trusted delivery outbox uses a separate `heartbeat` kind, preserving the daily notification slot and deduplicating each hourly message. This is an explicit opt-in exception to ordinary local-only progress reporting; research roles still cannot send messages.

The initially enabled cc-connect job `2bf4e820` was scheduled at `0 * * * *` in Asia/Shanghai, using the supplemental config/deployment and the five new industry IDs. Its wrapper is silent/muted because the script performs the single authorized delivery itself. The first delivery was verified as `sent`; activation evidence is in `runtime/earnings/heartbeat/activation-result.json`. A completed/waiting round sends its final status and suppresses subsequent hourly messages; ordinary daily discovery continues independently.

At activation, round 8 was `paused_quota` with zero live research workers: the configured writer backend returned a usage-limit error while publishing LEN. The checkpoint is retained, with 1/20 company analyses (LEN, partial), 1/5 incremental industry analyses (homebuilding, insufficient coverage), and 0/5 quarterly syntheses. This is not completion of the expanded research objective. Model profiles remain unchanged; quota recovery uses the existing continuation entrypoint, not hourly retry loops.

The user subsequently enabled a 60% remaining-account-quota reserve. The runtime policy is `runtime/earnings/quota-policy.json`; `earnings_quota_guard.py` queries the authenticated Codex `account/rateLimits/read` interface without model calls, storing only percentages, reset times and guard decisions. Every hourly check includes the remaining percentage. With this policy enabled, monitoring continues even after a research round is waiting/completed. The smallest remaining percentage across returned Codex primary/secondary windows controls the gate: strictly below 60% pauses, exactly 60% passes. Unknown quota or denied ordinary usage pauses new calls conservatively. Extra purchased credits and reset credits do not override the reserve and are never consumed by the guard.

The continuation entrypoint, subsequent execution windows, and company/industry, gap-review and publication model launchers enforce the same policy. In-flight calls may finish and save results; quota pauses retain retryable work and do not burn failure attempts. A successful later quota check may clear the gate, but the hourly monitor does not itself start research. Processes load these guards at startup; changing code does not retrofit already-loaded execution windows.

## Final operational handoff, 2026-09-24

At the user's request, hourly job `2bf4e820` is now disabled and supplemental round `earnings-round-8-95489414e0f4` is cancelled. Cancellation verified no live research/model processes and no resumable active round; accepted artifacts are retained. The original daily 10:00 job remains enabled and reads the expanded 10-industry, 50-company universe and September–October disclosure window. Its new rounds continue eligible work under the retained 60% quota reserve. Scheduler state, dated production configuration, account quota snapshots and cancellation audit remain local ignored runtime data.

The requested `gpt-6-sol` migration was tested but rejected by the current ChatGPT-authenticated backend. Effective daily/review profiles remain `gpt-5.6-sol` at medium/high; quarterly/escalation profiles remain unchanged. No unsupported model configuration is deployed.
