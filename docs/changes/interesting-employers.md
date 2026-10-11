# Distinctive employer catalog and discovery

Status: complete.
Catalog and dashboard resource discovery verified.
Goal: add Bose and a broad company selection for every profile, searchable by occupations and fields.
The distinctive-employer pack can be selected without changing role or field preferences.
Changed: config/sources.json, dashboard/app.js, dashboard/template.html, monitor/dashboard.py, dashboard tests, README.md, source-coverage documentation.
Reproduced: production resource-search functions returned no company for a software engineer query when its role metadata was present.
Verified: dashboard behavior and 13 dashboard tests pass after role, opportunity-type, and career-stage metadata were included.
Validation: ./scripts/dev_check.sh discover -s tests -p test_dashboard.py passed 13 tests; test_sources.py passed 22 tests; test_cross_industry.py passed 8 tests; ./scripts/check.sh passed all 368 tests and publication privacy checks.
After preserving existing catalog formatting and updating the Thorlabs official URL, catalog checks passed again.
Next action: none.
