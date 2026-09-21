# Developer guide

This guide is for contributors and coding agents.
Using the app requires none of these developer tools; user setup stays in [README](../README.md).
Contribution and licensing terms remain in [CONTRIBUTING](../CONTRIBUTING.md).

## Setup

Run commands from the repository root with Python 3.9+.
The application and unittest suite need only the standard library.
Ruff is the sole optional contributor dependency, installed separately from the application:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
```

The developer check command uses `.venv/bin/python`; set `OPPORTUNITY_RADAR_DEV_PYTHON` to an absolute Python executable to use an existing development environment instead.
Node is used only for dashboard syntax checks and is required by CI; local checks explicitly report when it is unavailable.
The optional native build requires macOS and Apple Command Line Tools as described in README.
No Git hooks, global MCP servers, agent subscription, Node packages, or background indexers are required.

## Find the relevant code

| Area | Entry points | Tests to start with |
| --- | --- | --- |
| CLI and onboarding | monitor/cli.py, onboarding.py | test_cli.py |
| Profiles and private runtime selection | monitor/profile.py, config.py | test_config.py, test_rescoring.py |
| Matching and eligibility | monitor/scoring.py, eligibility.py, targeting.py | test_scoring.py, test_eligibility.py, test_targeting.py |
| Collection and source packs | monitor/fetchers.py, source_registry.py, coverage.py | test_fetchers.py, test_sources.py, test_coverage.py |
| Scan lifecycle and persistence | monitor/pipeline.py, database.py | test_pipeline.py, test_database.py |
| Dashboard and native bridge | monitor/dashboard.py, dashboard/, extras/macos-app/ | test_dashboard.py, test_macos_app.py, test_macos_app_host.py |
| Installation and privacy | scripts/install_launch_agent.sh, privacy_check.py | test_installation.py, test_public_tree.py |
| Developer navigation | scripts/dev_nav.py | test_dev_nav.py |

Use the [pipeline design](PIPELINE.md) for behavior and data flow; do not rederive it from every implementation file.
Generate only the needed map or symbol range, without storing a stale index:

```bash
python3 scripts/dev_nav.py map --path monitor/profile.py
python3 scripts/dev_nav.py symbols --path monitor/profile.py --query import
python3 scripts/dev_nav.py show --path monitor/pipeline.py --query run_scan --limit 60
python3 scripts/dev_nav.py refs --query run_scan
rg -n 'run_scan' monitor tests
rg -n 'function |=>' dashboard/app.js
rg -n 'func |class |struct |enum ' extras/macos-app/OpportunityRadar.swift
sed -n '160,220p' monitor/pipeline.py
```

The navigation command parses public Python source directories without importing code; it excludes JSON catalogs, logs, databases, generated dashboards, virtual environments, and symlinks.
`map` summarizes file sizes, symbol counts, and local imports; `symbols` includes qualified names, signatures, and line ranges.
`refs` finds identifier spellings, including attribute and import occurrences, but does not resolve types, aliases at use sites, dynamic dispatch, or a complete call graph.
Use editor language-server navigation when semantic resolution matters.
Output defaults to 60 result lines, permits at most 200, and caps content at 16,000 characters.
Use `--offset` to page results or narrow the path/query; very long source lines require direct inspection.
This is a navigation tool, not a privacy scrubber for arbitrary code or a repository export facility.
Search only relevant directories and avoid loading generated monitor/taxonomy_catalog.json unless changing the taxonomy generator.

## Edit and validate

Reproduce a behavioral bug through its public entry point using temporary configuration/database fixtures before making a fix.
Existing CLI, dashboard, and native tests provide examples; never point a test at real personal state.
Run one focused loop while editing:

```bash
./scripts/dev_check.sh discover -s tests -p 'test_dev_nav.py'
# A single test is also supported:
./scripts/dev_check.sh discover -s tests -p 'test_dev_nav.py' -k test_output_is_bounded
```

Without test arguments, `./scripts/dev_check.sh` runs only static checks.
It checks Python correctness rules across source/tests, formatting for the new navigation tool and its tests, shell syntax, available JavaScript syntax, and diff whitespace.
Format that baseline with `.venv/bin/python -m ruff format scripts/dev_nav.py tests/test_dev_nav.py`.
Legacy modules retain their established formatting; extend the explicit baseline in dev_check.sh only during an intentional module migration.
Type checking is not enabled yet; the tool adoption rationale is in [ADR 0001](decisions/0001-development-tools.md).

Before a release or a cross-cutting change, run the complete existing gate once after focused checks pass:

```bash
./scripts/check.sh > /tmp/opportunity-radar-check.log 2>&1
tail -n 25 /tmp/opportunity-radar-check.log
```

Inspect the command's exit status and failures; a displayed tail alone is not evidence of success.
The gate covers JSON, Python compilation, all unittests, doctor, shell syntax, launch-agent rendering, publication privacy/history, and diff whitespace.
The privacy check reads both staged bytes and publishable working-tree files; stage only intended release files and rerun `python3 scripts/privacy_check.py --history` before publication.
Run local tests and installed-runtime upgrades sequentially because macOS tests can share the per-user lifecycle lock.
Do not weaken failed checks or repeat a successful full suite unless code or relevant conditions change.

For native host or installer changes, also build into a disposable location on macOS:

```bash
build_dir="$(mktemp -d)"
python3 extras/macos-app/install.py --destination "$build_dir/Opportunity Radar.app"
plutil -lint "$build_dir/Opportunity Radar.app/Contents/Info.plist"
codesign --verify --deep --strict "$build_dir/Opportunity Radar.app"
```

For collector/catalog changes, use an isolated copy with anonymous fixtures for any deliberate live source test; see [configuration](CONFIGURATION.md) and [coverage audit](AUDIENCE_AUDIT.md).
For runtime failures, start with `./scripts/doctor.sh`, source health, and a bounded relevant run record before expanding source/log retrieval.
Do not attach private dashboards, profiles, whole logs, or databases to issues or model handoffs.

## Resume work across sessions

Small fixes need no planning document.
For a multi-session change, use one `docs/changes/<short-name>.md` containing the behavior to preserve/change, acceptance criteria, a few dependent steps, and a current checkpoint.
Link it from the existing issue when one exists; the issue owns the request, and the change record owns detailed implementation progress.
Use the same file throughout the change, then mark it complete instead of maintaining a second TODO list.
For private work, keep the equivalent under ignored `.agents/work/` and point the next session to it explicitly.

Save a checkpoint after every verified chunk, not just at the end of a session:

```text
Status and goal:
Current branch/base revision:
Completed steps and changed paths:
Exact validation commands, outcomes, skips, and relevant code revision:
Next smallest action (and what it depends on):
Blocker or decision needed, if any:
```

Record facts, not transcripts or raw test output, and omit personal data and machine-specific paths.
On resume, read AGENTS.md and that checkpoint, inspect `git status --short` and the relevant diff, then continue the next action.
If files changed since the recorded verification, rerun affected checks; otherwise reuse the evidence.
Rate limits can stop a session abruptly, so do not rely on the agent being able to write a final handoff or resume automatically.
The [developer-workflow change](changes/developer-workflow.md) is the initial example.

## Concurrent changes

Keep ordinary Git and use a separate worktree for independent work when needed:

```bash
git worktree add ../opportunity-radar-source-fix -b source-fix HEAD
```

Each worktree gets its own optional `.venv` and temporary fixtures.
Do not install its scheduler or link it to personal runtime state just to test code.
Run macOS lifecycle tests sequentially even across worktrees.
Separate worktrees isolate files, not shared per-user operating-system resources.
