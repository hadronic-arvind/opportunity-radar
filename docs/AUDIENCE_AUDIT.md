# Cross-discipline audience audit

## Method

On 2026-09-21, the `eligibility-release` branch was cloned directly from GitHub into a separate temporary checkout at baseline commit `382a81b18e23028595143f1cbd21e630f15a7cec`.
Nine anonymous portable JSON profiles were processed through the application's actual profile import validation, automatic pack selection, public feed adapters, and deterministic scoring engine.
No personal profiles, resumes, application records, or installed runtime were used or modified.
The audit collected each source once and reused the public listing cache across profiles and comparison runs.
All 124 selected public sources ultimately returned successfully; Natera needed one retry after a timeout.
This was a feed and matching audit, not a visual interaction test or an application submission test.

## Improvements

The baseline had no automatic presets for business, accounting, classroom teaching, law, design, or skilled trades.
Business, accounting, teaching, and design examples each selected the same 42-source generic baseline despite substantially different interests.
Seven additional coverage presets now select relevant packs automatically and are available in onboarding and the profile editor.
Five new packs cover business operations, accounting and finance, teaching, legal policy, and biomedical devices.
Design and skilled-trades presets reuse existing packs.

Nine verified structured feeds were added: The Engine, Guidepoint, BPM, Aprio, Lansing School District, Head-Royce School, Noah Medical, AtriCure, and RTW Investments.
Existing ACLU and Vera Institute sources also participate in the legal-policy pack.
New sources remain disabled in the neutral starter configuration and are selected through profile coverage or explicit pack choices.
Manual resource pages are not counted as scanned jobs.

The audit also exposed and reproduced matching defects.
Staff accountants no longer fail the seniority gate merely because their title includes “Staff,” while senior staff accountants and staff engineers still do.
The `Sr.` abbreviation is recognized.
Encoded HTML, parenthesized experience numbers, numeric ranges, and required experience stated as years in a field or role are now parsed.
Preferred-only experience remains a preference.
The scoring schema version was increased so stored opportunities can be rescored under the corrected rules.

## Results

The following are source counts and counts scoring at least 65, before and after the changes.
They measure retrieval and relevance, not verified applicant eligibility or unique occupations.
The final audit deduplicates repeated URLs, includes the recovered source, and improves the law example's role terms, so these are end-to-end results rather than a controlled source-only experiment.

| Profile | Selected feeds, before -> after | Scores >= 65, before -> after |
| --- | --- | --- |
| accounting-graduate | 42 -> 44 | 4 -> 48 |
| biomedical-engineering | 106 -> 112 | 23 -> 21 |
| design-graduate | 42 -> 65 | 11 -> 29 |
| education-graduate | 42 -> 47 | 7 -> 51 |
| international-phd | 115 -> 115 | 13 -> 9 |
| law-student | 64 -> 70 | 2 -> 4 |
| mba-student | 42 -> 47 | 4 -> 46 |
| medical-student | 109 -> 112 | 10 -> 8 |
| skilled-technician | 98 -> 98 | 42 -> 41 |

## Reviewed listing examples

- **Medicine:** [Clinical Research Associate, Precision for Medicine](https://job-boards.greenhouse.io/pfm/jobs/6133936004) is relevant clinical research, but requires prior CRA or equivalent experience, German and English, and substantial travel.
  It is not an unconditional recommendation for a medical student.
  A separate CRA II listing exposed the parenthesized experience parsing defect and is now excluded by the student's one-year limit.
- **Business:** [Strategic Partnerships Intern, The Engine](https://jobs.lever.co/engine/ef44ca88-30cc-4446-a578-8d789b169876) explicitly welcomes graduate and MBA candidates with a completed bachelor's degree.
  It remains a lower-score discovery result when the profile requests narrower business analyst titles; users can add “strategic partnerships” to their roles.
- **Biomedical engineering:** [Quality Engineer, AtriCure](https://job-boards.greenhouse.io/atricure/jobs/4372675009) is a medical-device role involving validation and engineering quality, with an engineering degree and two to five years of experience requested.
  It fits the example's two-year search ceiling but is not necessarily suitable for a graduate with no experience.
- **Accounting:** [Accounting Advisory Supervisor, BPM](https://jobs.lever.co/bpmcpa/cb0838ac-e237-4c02-9848-385c93d20db4) exposed the “Minimum 3–5 years in public accounting” parsing gap.
  That explicit requirement now excludes it for the two-year accounting profile.
  BPM and Aprio also supply accounting associate and internship discovery results.
- **Teaching:** [GSRP Associate Teacher, Lansing School District](https://jobs.lever.co/lansingschools/86d403ef-a7fd-41a8-aa36-8747c07797db) is classroom work rather than an education-themed software job.
  Its CDA credential and training approval requirements still require manual review.
- **Law:** [Policy Intern, RTW](https://job-boards.greenhouse.io/rtwinvestments/jobs/5164335008) accepts graduate students or recent graduates from relevant policy, public-health, business, and life-science fields.
  The profile now also discovers an ACLU legal internship; individual program membership and enrollment conditions must be checked.
- **Design:** [Graphic Designer, Rocket Lab](https://job-boards.greenhouse.io/rocketlab/jobs/7919465003) provides a directly relevant title, while the expanded design preset also retrieves product and UX work.
  Portfolio and experience requirements remain listing-specific.
- **Skilled trades:** [Avionics Manufacturing Technician II, Rocket Lab](https://job-boards.greenhouse.io/rocketlab/jobs/7964886003) is technical manufacturing work, with shift, experience, and export-control conditions requiring review.
- **International PhD:** [Machine Learning Fellow, Human Frontier Collective](https://job-boards.greenhouse.io/scaleai/jobs/4661650005) is retrieved for the research profile, but its Canadian location does not establish work authorization.
  Separate deterministic eligibility tests cover NSF GRFP citizenship restrictions and lower-degree-only enrollment exclusions.

## Remaining coverage limits

This expansion makes the catalog useful across more disciplines; it does not establish universal coverage or guaranteed eligibility.
Many employers and universities use unsupported or login-dependent portals, and local teaching, clinical training, regional employers, and non-US programs remain unevenly represented.
Broad titles such as “quality engineer,” broad departments such as “business operations,” and missing experience information can still produce weak or misleading matches.
Listing text may be stale even when an employer feed returns successfully.
The examples deliberately leave location and dates open; users should set those constraints and tailor role terms for a practical search.
Licensing, professional certifications, field-of-degree equivalence, visa sponsorship, languages, program membership, and export-control details are not comprehensively inferred.
Unknown eligibility stays reviewable instead of being represented as a confirmed match.

## Reproduce safely

Use a fresh checkout without private local configuration, a linked runtime database, or `OPPORTUNITY_RADAR_*` / `OPPORTUNITY_MONITOR_*` environment overrides.
Run against the desired Git revision.
The audit refuses a checkout with those private-state indicators and never imports or activates a profile.
An explicit `--fetch` permits bounded, sequential public network collection; without it, only cached listings are used.
Reports and cached listings are written with private permissions to the chosen output directory.

```bash
python3 scripts/audit_profiles.py --fetch --output /tmp/radar-audience-audit
python3 scripts/audit_profiles.py --output /tmp/radar-audience-audit
python3 -m unittest discover -s tests -p test_audience_profiles.py
./scripts/check.sh
```

Keep the cache and full descriptions outside version control.
The repeatable regression suite validates all nine imports and their relevant feeds, seniority interpretation, formatted experience requirements, preferred-only experience, encoded HTML, and offline execution without network or profile writes.
Live counts are deliberately not CI assertions because employer listings change.
The existing license remains unchanged and this work does not publish a release.
