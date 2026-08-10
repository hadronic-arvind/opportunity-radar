# Contributing

Contributions that improve correctness, privacy, portability, source reliability, tests, or documentation are welcome.

## Before opening a change

- Discuss large architecture changes in an issue first.
- Use only official public source URLs and public job-board APIs.
- Do not include copied resume content, candidate data, credentials, generated dashboards, SQLite files, or signed URLs.
- Keep the scheduled path dependency-free unless a proposal demonstrates a compelling operational need.
- Preserve the zero-idle-CPU design and sequential request behavior.

## Development workflow

1. Create a focused branch.
2. Reproduce a bug through the public command or dashboard path when applicable.
3. Add or update tests for the behavior.
4. Run `./scripts/check.sh`.
5. Review `git diff` and the privacy-check output before opening a pull request.

Keep changes small enough to review and explain any source-specific assumptions.
Do not hide fetch failures or deactivate prior opportunities after a failed response.

## Adding a source

Prefer a documented first-party API.
Use conservative HTML-link extraction only when structured data is unavailable.
Use a watch page for a stable program page without a listing feed.
Include an explicit cadence and narrowly scoped filters.
Add a normalization test or a representative parser fixture.

## Commit and pull-request content

Use a concise imperative commit subject.
Explain user-visible behavior, tests, and privacy implications in the pull request.
Never include secrets or personal application material, even in test fixtures.
