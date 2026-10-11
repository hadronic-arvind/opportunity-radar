# Distinctive employer catalog and discovery

Status: shared discovery and automatic coverage for 38 employers shipped; 10 employers still need collectors.
Catalog and dashboard resource discovery verified.
Goal: add Bose and a broad company selection for every profile, searchable by occupations and fields.
The distinctive-employer pack can be selected without changing role or field preferences.
Changed: config/sources.json, dashboard/app.js, dashboard/template.html, monitor/dashboard.py, dashboard tests, README.md, source-coverage documentation.
Reproduced: production resource-search functions returned no company for a software engineer query when its role metadata was present.
Verified: dashboard behavior and 13 dashboard tests pass after role, opportunity-type, and career-stage metadata were included.
Validation: ./scripts/dev_check.sh discover -s tests -p test_dashboard.py passed 13 tests; test_sources.py passed 22 tests; test_cross_industry.py passed 8 tests; ./scripts/check.sh passed all 368 tests and publication privacy checks.
After preserving existing catalog formatting and updating the Thorlabs official URL, catalog checks passed again.
Original expansion was pushed; automatic collection follow-up remains active.

## Automatic collection follow-up

Reproduced through the public CLI with isolated anonymous configuration: selecting automatic coverage and the distinctive-employer pack does not enable Bose because it is a disabled watch_page.
Requested behavior: all added companies publish actual openings into the normal search for every interested profile, with matching based on each job.
Next: discover public board endpoints, implement bounded collectors, test anonymous scan/search flows, verify live sources in isolation, and synchronize the installed runtime.


## Verified collection and search update

Changed: public catalog, new bounded employer adapters, source detection/validation, coverage defaults, targeted CLI scans, dashboard company matches, regression tests, and coverage documentation.
All 48 employers appear in ordinary company search for every profile, even when no matching job exists.
Automatic and named coverage include the distinctive-employer pack; manual mode and explicit exclusions stay authoritative.
Automatic collectors cover 38 employers through 39 resources, including separate MKS regional boards.
Ten unsupported employers remain explicitly manual: Dolby, HARMAN, Bang & Olufsen, MathWorks, Dassault Systemes, Rohde & Schwarz, Hamamatsu Photonics, DJI, Universal Robots, and TAE Technologies.
Their provider work remains incomplete; do not claim automatic coverage of all 48.
Verified: Bose search snapshot contains 87 jobs; Workday sentinel pagination, incomplete search handling, stable identities, optional missing descriptions, and safety bounds pass focused tests.
Verified: real Bose collector -> targeted public CLI scan -> ordinary opportunity search succeeds for an accounting profile.
Verified: 15 employer adapter tests, 9 cross-industry CLI tests, 22 catalog tests, 51 existing fetcher tests, and dashboard behavior checks pass.
First full gate passed 383 tests; additional provider fixes require the final gate below.
Next: finish corrected-source live checks, run the final release gate, update the installed runtime, refresh working sources, and push the fix.


Final validation: ./scripts/check.sh passed all 384 tests, doctor, compilation, shell/native contract checks, publication privacy/history, and diff whitespace.
Corrected live checks passed: Bose 87, Shure 125, Sennheiser 29, Wolfram 6, Thorlabs 89, Gore 87, Quantum Machines 64, MKS EMEA 167, MKS Americas 184, and Teledyne 755.
The installed public runtime now matches the tested files; private profile settings and saved opportunities were preserved.
A recovery backup of replaced public files was created outside the repository.
Project memory review found no new recurring guidance absent from existing documentation; no memory or skill changes were made.
Next: finish the installed Bose refresh, refresh other newly enabled employer feeds, and push the verified public changes.


## Installed refresh

Pushed implementation commit 94e905b to main; GitHub CI and CodeQL passed.
Installed Bose refresh succeeded: 87 active openings, all with descriptions, and matching cards verified in the generated dashboard.
The other 31 newly enabled resources refreshed successfully: 13,426 new openings, zero collection errors.
Current SRI board has 36 jobs on its first page and no second-page jobs.
A public CLI fixture reproduced a future pagination omission: without explicit pr=0, the next request skips page one and imports only one of two jobs.
The SRI catalog now declares pr=0; the regression passes and guards later board expansion.
Next: final 385-test gate, synchronize the small SRI configuration follow-up, verify all employer statuses and dashboard search data, and push the checkpoint.

Final follow-up gate exposed an installed-runtime deployment mistake: dashboard assets had been replaced with mode 0644 instead of their original private modes.
The app's private-runtime marker validation correctly rejected those permissions.
Restored all replaced files to their backup permissions and the new module to 0600; profile and opportunity data were intact.
Next: rerun the final gate, preserve file modes when synchronizing the SRI catalog setting, and verify the final dashboard.

Final follow-up validation: ./scripts/check.sh passed all 385 tests, doctor, compilation, publication privacy/history, and diff whitespace after restoring private file permissions.
All 39 automatic distinctive-employer resources have successful installed collection status and no current errors.
The final dataset retains Bose search results; all 48 employers remain in the shared company-search catalog.
SRI's pr=0 setting was synchronized with the destination file mode preserved.
Added one verified operational note to ignored local project memory about private-runtime file modes; no reusable skill was needed.
Remaining limitation: the ten documented manual employers still lack automatic listing collectors.
Next: finish SRI's final refresh, push the small pagination/checkpoint follow-up, and report the collection limitation clearly.

Final rollout verification: SRI's corrected installed refresh succeeded with zero errors and no duplicate or changed records.
Clone CLI doctor passed; the installed dashboard contains 401 resources and all 48 distinctive company names.
Matching Bose cards remain present after the complete employer refresh.
Supported-provider rollout is complete; the ten documented manual providers remain a separate collection limitation.
