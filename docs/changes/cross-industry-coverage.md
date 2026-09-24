# Cross-industry employer coverage

Status: complete, merged in [PR #15](https://github.com/hadronic-arvind/opportunity-radar/pull/15), and installed locally as v0.8.0.
The developer workflow is merged in GitHub PR #13; all eleven remote checks passed.

## Problem and behavior

An anonymous accounting profile with automatic coverage currently enables only seven sources through `monitor sources list --json`.
It excludes SpaceX, Natera, and Discord before their individual jobs can be evaluated.
Employer pack labels are therefore an unintended limit on job discovery.

Automatic and named coverage should include a shared cross-industry pack of supported structured employer feeds, independent of the profile's profession.
Existing field packs still add specialized resources; manual coverage and explicit source disabling remain authoritative.
The unconfigured five-feed starter remains small.
Job content, requirements, and the user's preferences determine relevance; employer sectors are descriptive metadata, not job qualifications or fit points.

## Acceptance criteria

- Accounting, healthcare, software, and other automatic/named profiles can see the same cross-industry employer baseline.
- Manual coverage, explicit disabling, custom sources, per-source cadences, failure isolation, and saved application state remain intact.
- Add a substantial batch of live-verified official employer feeds spanning technology, finance, retail, logistics, media, healthcare, manufacturing, energy, and public interest.
- Exclude failed, empty, unsupported, or oversized candidate feeds from the new supported additions.
- Preserve sequential, bounded, read-only collection and zero-idle runtime behavior.
- Cover source selection and job-level matching through public CLI paths with anonymous fixtures.
- Update user-facing explanations, catalog maintenance guidance, validation evidence, and GitHub.

## Steps and checkpoint

| Step | Depends on | State |
| --- | --- | --- |
| Publish developer tooling | none | complete, PR #13 merged |
| Reproduce selection gap and define behavior | tooling | complete |
| Validate candidate employer feeds | design | complete: 69 nonempty feeds from 110 candidates |
| Implement cross-industry selection, catalog, and tests | design | complete |
| Validate and publish feature | implementation and feed validation | complete; all eleven GitHub checks passed |
| Upgrade and verify local app if installed | passing release checks | complete; runtime, native app, signature, and doctor verified |

Reproduction used temporary public config copies plus an anonymous accounting profile, with no private app writes.
Source validation runs sequentially through the existing fetch adapters and saves compact evidence outside the repository.
The new regressions failed before implementation, including the public scan/search flow that could not find accounting jobs at aerospace, biotech, and software employers.
The completed change adds the shared pack to automatic/named coverage, retains manual behavior and explicit exclusions, and adds 69 verified feeds on a 24-hour cadence.
The catalog now has 352 resources and 189 supported structured feeds in the shared employer set.
Existing source values are unchanged except for pack membership; existing cadence, request limits, and adapter behavior are preserved.
The new sources returned 11,566 listings during validation; 35 failed and six empty candidates were excluded.
Version 0.8.0 marks the runtime behavior change so older installed runtimes cannot silently run against the new checkout.

Validation on macOS/Python 3.13:

- `./scripts/dev_check.sh discover -s tests -p 'test_cross_industry.py'`: five tests pass, including CLI profile import, scan, search, saved status, deduplication, and cadence.
- `./scripts/dev_check.sh discover -s tests -p 'test_sources.py'`: 22 tests pass.
- `python3 -m unittest discover -s tests -p 'test_dashboard.py'`: passes.
- `./scripts/check.sh`: all 358 tests and the full release/privacy gate pass, including native host compilation.
- Python 3.9 syntax parsing and a semantic comparison of all existing source objects pass.

GitHub verified Python 3.9/3.14, macOS tests/native app build, developer checks, and CodeQL before merge.
The existing installer completed its staged scan and promoted v0.8.0, then the native app was rebuilt and its signature verified.
Private configuration hashes and every saved application-state record matched the pre-upgrade snapshot.
Doctor reported no failures; the existing scheduler and scan times were preserved, and developer files are absent from the installed runtime.
The verification scan succeeded for all 69 added feeds; one existing Anduril feed hit its bounded request deadline and retained prior records as designed.
No implementation, publication, or installation work remains.
