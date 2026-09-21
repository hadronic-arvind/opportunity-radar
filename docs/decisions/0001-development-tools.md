# 0001: Keep development tools outside the local runtime

Status: accepted.

## Context

The local application has no third-party Python runtime dependencies.
Long source files make repeated whole-file reading expensive, and changes can span interrupted coding sessions.
The existing tests, privacy gate, runtime installer, and GitHub automation are useful foundations.

## Decision

Use small shared instructions, on-demand source navigation, ordinary Markdown change records, and a separate developer check command.
Developer dependencies live only in requirements-dev.txt and an optional virtual environment.
Neither app startup nor installation invokes developer tooling.
Keep architecture in docs/PIPELINE.md, contributor commands in docs/DEVELOPMENT.md, and task state in an issue or its linked change record.

| Idea | Classification | Repository-specific reason |
| --- | --- | --- |
| Concise AGENTS.md and developer guide | ADOPT NOW | No shared entry point; avoids repeated setup discovery. |
| Generated symbol map and exact source ranges | ADOPT NOW | Python AST handles the largest backend modules without services or an index to refresh. |
| Serena / language server integration | DEFER | Useful for resolved references later; first measure whether AST lookup plus editor navigation is insufficient. |
| OpenSpec | ADAPT EXISTING SYSTEM | Small Markdown change records cover multi-session intent without a framework or retrospective specification rewrite. |
| Beads | ADAPT EXISTING SYSTEM | GitHub issues own shared tasks; a linked change checkpoint owns implementation state, with no second task database. |
| Task-specific agent skills | DEFER | Existing procedures fit ordinary docs; no recurring conditional procedure warrants another layer yet. |
| ast-grep | DEFER | No bulk migration is required; Python AST already solves current retrieval needs. |
| Repomix / generic RAG | NOT USEFUL HERE | A small selected source range is sufficient; bundling risks private runtime data and duplicates navigation. |
| Text architecture and short ADRs | ADAPT EXISTING SYSTEM | Add a flow to PIPELINE.md and preserve this tool/runtime boundary decision. |
| Ruff | ADOPT NOW | Deterministic Python correctness lint, plus formatting for the new tooling without legacy formatting churn. |
| mypy / pyright | DEFER | Partial annotations and dictionary-heavy configuration need a separate scoped baseline to avoid a noisy rollout. |
| pre-commit | DEFER | One developer command and CI provide the gate without hook setup or overlapping configuration. |
| Context7 | NOT USEFUL HERE | No third-party Python runtime APIs; consult version-specific official docs on demand. |
| Renovate / OSV-Scanner | ADAPT EXISTING SYSTEM | Keep Dependabot and CodeQL; extend Dependabot to the new developer dependency instead of adding another service. |
| OpenTelemetry | NOT USEFUL HERE | Burst-only local scans already expose bounded run records and source diagnostics; no distributed runtime. |
| Worktrees / Jujutsu | ADAPT EXISTING SYSTEM | Document isolated Git worktrees; another VCS interface has no demonstrated benefit. |
| Large-file splitting | DEFER | Symbol navigation is lower risk; split modules only with a concrete behavioral change and relevant tests. |

## Consequences

Developers opt into one pinned tool; users do not install it.
Python navigation parses source without importing application code or reading private state.
Reference search is lexical, not a claim of type-aware call resolution; editors remain the option for semantic navigation.
No persistent map, external index, runtime telemetry service, or global MCP configuration is added.
Full release checks remain mandatory before release, while focused validation keeps ordinary edits inexpensive.

## References

- [Ruff configuration](https://docs.astral.sh/ruff/configuration/) describes project-local lint settings.
- [Ruff installation](https://docs.astral.sh/ruff/installation/) documents installing it separately with pip.
- [Serena overview](https://oraios.github.io/serena/01-about/000_intro.html) describes the language-server approach considered for later use.
