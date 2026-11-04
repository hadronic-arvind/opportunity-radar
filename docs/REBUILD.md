# Opportunity Radar rebuild

## Design and audit before implementation

A blind product proposal compared an exploration workspace, a guided decision brief, and an evidence notebook.
The exploration workspace makes the first decision possible with no profile, while a guided brief adds setup and a notebook adds transcription.
The candidate design keeps named goals optional and independent of the shortlist.
Only one blind agent could be started alongside the three incumbent audit tracks under the four-agent limit.
The audits had the full brief and incumbent context.

On the incumbent revision 1be2332, independent audits reproduced a failed second page discarding a valid first page, substring search ranking Partner Accountant for `art`, a missed scientific computing paraphrase, and a raw spreadsheet formula in CSV output.
The standalone browser stores tracking separately from SQLite.
Existing public feed connectors, public-only network protections, source identity, application states, and saved profile data are compatibility inputs.

Compare whole-token lexical retrieval with the incumbent substring reference and literal ranking with field-aware evidence ranking on the same frozen fixtures.
Keep semantic matches transparent and retain unknowns whenever a listing lacks authoritative eligibility evidence.
A new SQLite database supports isolated, transactional import while retaining the incumbent as a recovery path.
A desktop window reads the same database as the terminal and avoids a listening service and idle background scan.
Reuse public connector transport and parsers because they already have offline contract tests and public-address validation.
Keep collection isolated in a bounded child process so blocked network calls can be canceled.

## Initial verification plan

Use disposable homes and state for every development journey.
Exercise onboarding, discovery, goal edits, comparison, tracking, cancellation, offline backup and restoration, and legacy migration through normal entry points.
Check that dismissals and recommendations never prevent direct retrieval.
Evaluate frozen sparse and detailed goals with candidate recall of at least 90% and precision@5 of at least 60% per persona on the declared holdout, no false definitive eligibility assessments, and exact source support for each displayed explanation.
Inspect the actual rendered window and verify keyboard, resize, responsiveness, shared state, idle activity, and exit.
Run a bounded public-source smoke separately and use deterministic offline failure fixtures for release gates.
Measure startup, first result, query latency, write cost, cancellation, idle and exit using an isolated macOS arm64 environment and a 30,000-record catalog.
Report median and worst observed results, with targets of two seconds for a warmed first result and three seconds after cancellation, and zero idle workers, periodic writes or network.
Record each unverified condition with its reason.

## What shipped

The desktop and terminal invoke one workspace and one set of commands over SQLite.
Local collection runs in a short-lived child with a public-only transport, a time limit, a sanitized environment, and a pipe that closes when the caller exits.
Per-source receipts distinguish observations, new records, updates, duplicate channels, failures, and incomplete coverage.
Absence never establishes closure.
Strong posting URLs and explicit cycles can group equivalent observations, while separate or ambiguous cycles retain separate identity.
Tracking and observations are separate, so changes from a source cannot silently reset an application decision.
The workspace stores changed evidence separately from the latest observation.
Backup validates schema and integrity, while replacement and collection serialize without excluding concurrent reads and tracking.

Direct search uses whole tokens and the full index.
Recommendations retrieve phrase variants and nearby inflections, then rank supporting passages from duties, titles, locations and opportunity forms.
The engine compares a scoped goal's requirements and avoidances separately from positive activity signals.
Evidence views identify conditional and negative language and preserve source wording.
A requirement with missing evidence can stay outside recommendations while remaining directly discoverable.
Explicit Boolean eligibility logic supports alternatives, exceptions, unknown facts and conflicting claims on structured authoritative records.
Current public connectors expose evidence text and do not produce structured eligibility conditions, so live assessments ordinarily remain unknown.

## Acceptance record

The incumbent baseline was Git revision `1be2332`, with 417 offline tests passing in 39.2 seconds on macOS 26.4.1 arm64 and Python 3.13.
The v1.0 replacement evidence below was collected on the same Mac from the release candidate and disposable SQLite workspaces.
The release commit and CI checks identify the exact published tree.

| Gate | Status | Reproducible evidence |
| --- | --- | --- |
| Offline terminal journey and privacy | Pass | `./scripts/check.sh` and `./scripts/dev_check.sh`. Empty onboarding, examples, search, goal edits and undo, shortlist, comparison, hostile listing input, CSV escaping, migration, rollback, permissions, and privacy checks. Replacement tests also ran on Apple Python 3.9.6. |
| Live source integration | Pass within catalog | `python3 -m radar.collection --smoke --seconds 7` observed 13 of 14 sources on one pass. A separate 7-second EURAXESS retry found 10 records and reported a repeated page as partial. Combined 14 of 14 endpoints returned usable observations, 1,777 in total. Successful checks took 0.192 to 2.469 seconds. |
| Partial and stalled collection | Pass | `python3 -m unittest discover -s tests -p 'test_radar_collection.py'` covers unsafe requests, rate limiting after a useful page, repeated pages, response and output limits, cancellation and parent death. Five stalled-worker cancellations took 0.099 seconds median and 0.103 worst. A 0.4-second timeout returned a complete observation after 0.403 seconds. Abrupt parent death released the child's test lock within two seconds. |
| Upgrade, backup and recovery | Pass within tested versions | `python3 -m unittest discover -s tests -p 'test_radar_recovery.py'` verifies older states, idempotence, interrupted import, concurrent readers, workspace locking, simulated SQLite page exhaustion and forged backups. An isolated rehearsal from the installed database and copied configuration had zero differences in listing IDs, tracking or recorded timestamps. A 629 MB snapshot copied in 0.850 seconds, migrated in 30.021 seconds, and peaked at 83.7 MB RSS. |
| Desktop and terminal parity | Pass on tested Mac | `RADAR_TEST_DESKTOP=1 python -m unittest discover -s tests -p 'test_radar_gui.py'` with Python 3.13.9 and Tk 8.6 in a graphical session. Screen review at 720×520 and 1140×760, keyboard input, search, evidence, comparison, tracking, and close. A second tester created a goal and backup in the desktop, verified them through the CLI, undid the goal in the CLI, and restored it in the desktop. Apple Tk 8.5 rendered a blank window and is rejected. |
| Accessible interaction | Unverified in part | Keyboard navigation and rendered compact layout were exercised on macOS. Screen-reader behavior, varied display zoom and Linux desktop rendering are unverified. |
| Independent relevance | Fail | The second synthetic holdout contained 12 distinct groups. Before the general inflection and proximity change, restoration recalled 1/4 and safety 2/5 relevant groups, with 1/5 useful slots each. The rerun found 2/4 and 3/5, with 2/5 useful slots each. No irrelevant recommendations appeared after evidence filtering. The weakest slices include untranslated Polish and Arabic and English paraphrases that share few terms. |
| Eligibility and explanation | Pass on synthetic corpus, live inference unverified | `python3 -m scripts.evaluate_radar_holdout --fixture tests/fixtures/rebuild_independent_holdout.json` evaluated 24 listing-persona pairs. One supported alternative and one mandatory condition were assessed correctly and 22 stayed unknown. Quotes were checked as exact source substrings. These labels do not validate eligibility inference from public prose. |
| Coverage and representativeness | Unverified outside 14 configured endpoints | Smoke data measures connector reach, not relevance. A permission-cleared, independently labeled public snapshot corpus is still needed. No all-market coverage estimate is made. |
| Interactive resource target | Pass on tested workloads | `python3 -m scripts.benchmark_radar` on 30,000 records measured empty process startup at 46 ms median and 48 ms worst, sparse recommendation first page at 45 and 48 ms, and ingestion in 1.75 s. A second 30,000-candidate broad goal benchmark measured 1.583 s median and 1.606 s worst, down from 2.330 and 2.976 s. The open desktop was sampled at 0.0% CPU and about 143 MiB RSS, with no children, Internet sockets, or database changes while idle, and no app process after exit. |
| Extended resource behavior | Unverified | The repeatable 30,000-record benchmark and short idle/exit probes do not establish hours-long stability, behavior on low-powered hardware or Linux desktop responsiveness. |

The first frozen fixture contained 12 synthetic observations across 10 groups, plus an unconfigured explorer.
At the initial run, research and access recalled 2/4 and 1/3 relevant groups and each included irrelevant boilerplate.
The lexical reference recalled 1/4 and 2/3 and included the same irrelevant item.
After broader phrase variants and boilerplate checks, both replacement personas recalled all labeled groups and produced 3/5 useful slots without irrelevant recommendations.
This is an adaptive comparison, so the second frozen fixture is reported separately with both its initial and adaptive results.
The evaluator source and labels are reproducible with `python3 -m scripts.evaluate_radar_holdout`.
Cases sharing a publication family are grouped for reporting.
The small synthetic sets cannot establish quality for whole sectors or languages.

Eight catalog sources are organization boards across software, rights advocacy, climate, biotech, healthcare, transportation and finance.
The other six cover university careers, government postdoctoral programs, research mobility, federal grants, events, and a regional multi-employer feed.
North America and Europe dominate.
Apprenticeships, scholarships, clinical and legal paths, small institutions, and sources elsewhere remain underrepresented.
Custom supported public feeds and manual imports broaden the user's reachable inventory.
