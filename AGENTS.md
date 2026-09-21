# Working in Opportunity Radar

Read only what the task needs; start with `git status --short` and the relevant issue or change checkpoint.
For an interrupted change, read its record under docs/changes/ before repeating discovery or tests.
See [the developer guide](docs/DEVELOPMENT.md) for setup, navigation, validation, and checkpoint procedures.

## Boundaries

- Runtime: standard-library Python 3.9+, local SQLite, static dashboard, optional Swift/WKWebView host.
- Preserve burst-only sequential scans, zero idle processes, and lifecycle-then-scan lock ordering.
- Never add developer dependencies, agent services, or test setup to app startup or installation.
- Keep profiles, databases, logs, generated dashboards, credentials, and local `.agents/` files private and ignored.
- Do not edit generated monitor/taxonomy_catalog.json manually; its generator is scripts/build_taxonomy_catalog.py.
- Do not run installers or live scans as a substitute for tests; they can touch the user's installed runtime or network.

## Locate and verify

- Use `python3 scripts/dev_nav.py map` for a bounded source map, then `symbols`, `show`, or `refs`; see `--help`.
- Use `rg -n` in the selected module for text, JavaScript, or Swift; read specific ranges before whole files.
- Reproduce bugs through the affected CLI/dashboard path before fixing them; use temporary state, not personal data.
- Fast validation: `./scripts/dev_check.sh discover -s tests -p 'test_<area>.py'`.
- Full release gate: `./scripts/check.sh`; native build commands and special checks live in the developer guide.
- Keep command output concise; save long output outside the tracked tree and inspect failures.
- Update the active checkpoint after each verified chunk and before yielding: changed files, exact checks/results, next action, blockers.

## Knowledge ownership

docs/PIPELINE.md owns architecture and runtime behavior; docs/CONFIGURATION.md owns configuration contracts.
docs/decisions/ owns lasting architectural choices; docs/changes/ owns linked multi-session change intent and implementation checkpoints.
GitHub issues own shared work requests; do not create a second task database.
Ignored `.agents/project-memory.md`, when present, is only for verified local pitfalls absent from shared docs.
