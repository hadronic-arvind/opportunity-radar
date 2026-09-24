# Developer workflow improvement

Status: complete and merged in [PR #13](https://github.com/hadronic-arvind/opportunity-radar/pull/13).

## Acceptance criteria

- A fresh session can find the relevant Python symbol and tests without reading whole modules.
- Shared agent instructions stay short; detailed procedures have one developer-facing home.
- Interrupted work records the next action and validation evidence in a small checkpoint.
- Checks use deterministic tools and bounded output, with a targeted loop and a full release gate.
- The app, scheduler, and normal user setup acquire no development dependencies or services.
- Existing application behavior, personal state, installation, and licensing remain intact.

## Audit evidence

The starting tree was clean on `main` at `2b59486`.
It contains 95 tracked files and roughly 32,500 lines of Python, JavaScript, Swift, HTML, and Markdown.
Python 3.9+ uses only the standard library, SQLite, and unittest.
The dashboard is static HTML/CSS/JavaScript; the optional macOS host uses Swift and WKWebView.
The largest implementation files are dashboard/app.js (3,175 lines), monitor/profile.py (2,097), the Swift host (1,862), and monitor/scoring.py (1,830).
There is no shared AGENTS.md, formatter, linter, type checker, repository-local MCP configuration, or symbol index.
Ignored local agent memory repeats detailed behavior already owned by source, tests, and docs/PIPELINE.md.
CI already tests Python 3.9/3.14 on Linux and 3.13 on macOS, validates JavaScript and shell syntax, and builds/signs the native app.
Dependabot owns Actions updates; CodeQL analyzes Python and JavaScript.
The GitHub dashboard showed no open issues or pull requests during this audit.
The scheduler installer copies monitor/, selected config/dashboard files, and one runtime shell script; it does not copy the repository wholesale.
Run history, source health, JSON scan summaries, doctor, and bounded history retention already provide local diagnostics.
README and CONTRIBUTING currently describe a proprietary license; public release needs a separate owner-selected licensing change.

## Work and dependencies

| Step | Depends on | State |
| --- | --- | --- |
| Audit and select tools | none | complete |
| Shared instructions, developer guide, small ADR | audit | complete |
| Bounded source navigation and regression tests | audit | complete |
| Developer-only lint/check loop and CI | navigation | complete |
| Full verification and handoff | all above | complete |

## Checkpoint

Decision: use standard-library AST navigation and existing unittest/GitHub infrastructure, with one pinned developer-only Ruff dependency.
Keep full release checks separate from the short edit/test loop.
Do not refactor large runtime modules or mass-format legacy code during tooling work.
Implemented AGENTS.md, docs/DEVELOPMENT.md, the pipeline flow diagram, ADR 0001, scripts/dev_nav.py and its eight CLI tests, requirements-dev.txt, scripts/dev_check.sh, and CI/Dependabot integration.
Removed the developer release-check command from user maintenance instructions and ignored virtual environments, Ruff cache, and build products.
Pruned ignored local memory to pointers instead of duplicating detailed runtime behavior.
The first full gate exposed an existing audience-audit test dependency on a clean checkout; a disposable clean copy passed all 353 tests.
The test now supplies temporary anonymous configuration and a clean test environment, retaining an explicit assertion that the audit rejects private configuration.
The final full gate also passes in the original checkout with its private configuration intact.

Validation on the pending changes based on `2b59486` (macOS, Python 3.13, Ruff 0.15.7):

- `./scripts/dev_check.sh discover -s tests -p 'test_dev_nav.py'`: passed, eight navigation CLI tests and static checks.
- `./scripts/dev_check.sh discover -s tests -p 'test_audience_profiles.py'`: passed, six tests including isolated offline audit and private-state rejection.
- `./scripts/check.sh`: passed, all 353 tests, native host compilation, configuration/compilation checks, doctor, launch-agent checks, and publication privacy/history.
- YAML and TOML parsing, Python 3.9 syntax parsing for the new Python files, new documentation links, ignore rules, and `git diff --check`: passed.

All eleven GitHub checks passed, including Linux Python 3.9/3.14, macOS tests/native app build, developer checks, and CodeQL.
No implementation or publication work remains for this change.
An owner-selected open-source license and corresponding contribution terms remain a separate release decision.
Broader Pyflakes inspection found four pre-existing unused imports and one unused local; leave those outside this change and enforce the clean correctness subset instead.
No application installation, scheduler mutation, or license change is part of this tooling change.
