# Relevance and demo readiness

Status: implementation and local release validation complete; publication and installed upgrade pending.
Branch: relevance-demo-readiness, based on 8c5cbd8 on main.

## Goal and constraints

Improve personalized relevance and the real-feed demonstration experience across disciplines.
Use real collected listings in the product; do not add fictional demo listings.
Preserve private configuration, saved opportunities, application history, bounded sequential collection, and zero idle processes.
Publish the verified release to GitHub and upgrade the installed Mac runtime and native app.

## Verified changes

The public scan/search/dashboard regression reproduced unrelated fellowships earning high fit through broad Fellowship and Research terms.
Specific profiles now require actual interest evidence; broad program labels cannot supply subject fit.
Deliberately broad profiles remain supported, and weak substantive description matches retain the established Watch ceiling.
The same Human Rights Fellowship is relevant to a human-rights profile and hidden for a machine-learning profile.
An isolated replay of 28,613 stored listings changed visible matches from 108 to 84 without changing any workflow record.
Technical fellowships remained visible while unrelated school-leadership and human-rights programs dropped out.

A scan-path regression reproduced partial writes and inflated counts after a mid-source processing failure.
Source refreshes now use one transaction, restoring the previous snapshot on failure and avoiding per-listing disk commits.
The dashboard supports every present opportunity type, useful empty-state actions, accurate saved-record availability labels, and requirement/score-limit disclosures.
No fictional demo feature remains.

## Current validation and next action

`./scripts/dev_check.sh` and focused suites pass for cross-industry matching, scoring, pipeline, and dashboard behavior.
`./scripts/check.sh` passes all 363 tests and the full release/privacy gate after the final code changes.
Python 3.9 syntax parsing passes for every Python module in monitor, tests, and scripts.
A 500-listing local persistence benchmark reduced commits from 500 to one and elapsed time from 0.161 seconds to 0.022 seconds; this does not measure network scan duration.
The JavaScript behavior check exercises actual filter/search functions, saved records, and inactive applications with anonymous test fixtures.
Visual UI inspection is blocked because macOS Computer Use permissions are not granted.
The cross-discipline audit compares baseline/current matching on real stored public listings without changing the active profile or reading application state.
Next: complete that audit, verify the disposable native build, publish with passing GitHub checks, and upgrade locally while preserving private configuration and the existing scan schedule.
