# Relevance and demo readiness

Status: release implementation, validation, real-listing audit, and installed Mac upgrade complete.
Publication: [PR #18](https://github.com/hadronic-arvind/opportunity-radar/pull/18).
Branch: relevance-demo-readiness, based on 8c5cbd8 on main.

## Goal and constraints

Improve personalized relevance and the real-feed demonstration experience across disciplines.
Use real collected listings in the product; do not add fictional demo listings.
Preserve private configuration, saved opportunities, application history, bounded sequential collection, and zero idle processes.
Publish the verified release to GitHub and upgrade the installed Mac runtime and native app.

## Verified changes

The public scan/search/dashboard regression reproduced unrelated fellowships earning high fit through broad Fellowship and Research terms.
Specific profiles now require actual interest evidence; broad program labels cannot supply subject fit.
The cross-discipline audit additionally found AI research-assistant jobs ranking highly for a law profile through the generic academic role label.
Generic academic roles such as research assistant and research scientist now also need evidence for a specific profile's chosen field.
Deliberately broad profiles remain supported, and weak substantive description matches retain the established Watch ceiling.
The same Human Rights Fellowship is relevant to a human-rights profile and hidden for a machine-learning profile.
An isolated replay of 28,613 stored listings changed visible matches from 108 to 84 without changing any workflow record.
Technical fellowships remained visible while unrelated school-leadership and human-rights programs dropped out.

The real-listing medical-student audit reproduced physician and advanced-practice roles outranking research opportunities despite explicit clinical training requirements.
Explicit medical residency and NP/PA program requirements now reject student profiles with known unrelated completed degrees.
Missing or potentially relevant clinical credentials remain unknown and require verification rather than being presented as confirmed eligibility.
Clinical evidence is included in the scoring fingerprint so field-of-degree changes trigger rescoring.

A scan-path regression reproduced partial writes and inflated counts after a mid-source processing failure.
Source refreshes now use one transaction, restoring the previous snapshot on failure and avoiding per-listing disk commits.
The dashboard supports every present opportunity type, useful empty-state actions, accurate saved-record availability labels, and requirement/score-limit disclosures.
No fictional demo feature remains.

## Release validation

`./scripts/dev_check.sh` and focused suites pass for cross-industry matching, scoring, pipeline, and dashboard behavior.
`./scripts/check.sh` passes all 368 tests and the full release/privacy gate after the final code changes.
PR review identified positive interest hard gates being incorrectly counted as scored interest rules.
A public scan-to-dashboard regression reproduced the hidden matching listing; excluding hard gates from scored-interest detection fixes it, and all eight cross-industry tests pass.
The final gate ran without an installer active and passed; an earlier overlapping run was rejected by the existing lifecycle guard.
Python 3.9 syntax parsing passes for every Python module in monitor, tests, and scripts.
A 500-listing local persistence benchmark reduced commits from 500 to one and elapsed time from 0.161 seconds to 0.022 seconds; this does not measure network scan duration.
The JavaScript behavior check exercises actual filter/search functions, saved records, and inactive applications with anonymous test fixtures.
Visual UI inspection is blocked because macOS Computer Use permissions are not granted.
The cross-discipline audit compares baseline/current matching on real stored public listings without changing the active profile or reading application state.
The disposable native app build, plist lint, and signature verification passed.
The installed `~/Applications/Opportunity Radar.app` was rebuilt for 0.9.0; both plists and its strict deep signature verification pass.
All 11 GitHub CI and CodeQL checks passed on PR #18 at ece26c9, including the final academic-role, clinical-training, and positive-hard-gate changes.
The final supported installer completed a full real-source scan and promoted the release runtime.
Hash checks confirm installed monitor code, public configuration, and dashboard assets exactly match the release checkout.
All three private configuration files and all 38,425 original workflow records retained their pre-upgrade values.
The existing 07:30 and 16:30 cron schedule is unchanged, with no scanner running between scans.
The supported installation doctor reports no failures and confirms the runtime, dashboard assets, and cron fallback.
Project-memory review found no new verified recurring guidance absent from the shared repository documentation.
PR #18 owns the release publication; GitHub checks must remain passing when merged.

## Real-listing cross-discipline audit

Nine anonymous public example profiles were imported through the standard profile preparation path and scored against 28,155-28,555 real active public listings selected by their configured sources.
The audit used an isolated configuration and the pre-upgrade stored snapshot; it did not switch the active profile or include personal workflow state.
Visible counts include lower-confidence Watch matches; these are coverage and noise measurements, not a claim of measured precision or universal eligibility.
The final hard-gate correction does not affect these example profiles because none uses a positive custom interest hard gate.

| Public profile | Previous visible | Revised visible | Score 65+ | Organizations |
| --- | ---: | ---: | ---: | ---: |
| accounting-graduate | 10991 | 754 | 118 | 90 |
| biomedical-engineering | 11102 | 47 | 26 | 26 |
| design-graduate | 11149 | 236 | 49 | 63 |
| education-graduate | 10991 | 2015 | 52 | 119 |
| international-phd | 620 | 92 | 25 | 29 |
| law-student | 9996 | 2206 | 58 | 88 |
| mba-student | 10871 | 292 | 131 | 55 |
| medical-student | 10159 | 407 | 10 | 26 |
| skilled-technician | 10389 | 60 | 51 | 11 |
