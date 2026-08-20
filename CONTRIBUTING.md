# Repository participation

Opportunity Radar is publicly viewable proprietary software, not an open-source project.
Feedback, bug reports, and source suggestions are welcome through GitHub issues.
Unsolicited code contributions are not accepted unless the copyright holder has first agreed in writing to contribution terms, including any required intellectual-property assignment or license.
Public visibility and GitHub's fork functionality do not grant permission to use, modify, distribute, sublicense, or sell the software outside the rights provided by GitHub's Terms of Service.

## Before proposing work

- Open an issue before preparing a code change.
- Wait for written approval and any requested contributor agreement before submitting code.
- Use only official public source URLs and public job-board APIs.
- Do not include copied resume content, candidate data, credentials, generated dashboards, SQLite files, or signed URLs.
- Keep the scheduled path dependency-free unless a proposal demonstrates a compelling operational need.
- Preserve the zero-idle-CPU design and sequential request behavior.

## Development workflow

1. Confirm that the copyright holder has invited the change in writing.
2. Create a focused branch.
3. Reproduce a bug through the public command or dashboard path when applicable.
4. Add or update tests for the behavior.
5. Run `./scripts/check.sh`.
6. Review `git diff` and the privacy-check output before opening a pull request.

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
Explain user-visible behavior, tests, privacy implications, and the applicable written contribution terms in the pull request.
Never include secrets or personal application material, even in test fixtures.

See [LICENSE](LICENSE) for the governing proprietary terms.
