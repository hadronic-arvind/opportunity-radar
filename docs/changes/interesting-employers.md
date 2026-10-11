# Distinctive employer catalog and discovery

Status: reopened for automatic job coverage of all 48 added employers.
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
