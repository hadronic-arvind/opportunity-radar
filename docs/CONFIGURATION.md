# Configuration

## Start with onboarding

Run the interactive setup from the repository root:

```bash
python3 -m monitor init
```

The command writes `config/profiles.local.json` as an owner-only named-profile store.
The file is ignored by Git and written with mode `0600`.
Existing local configuration is never replaced unless you explicitly pass `--force`.

For scripts or reproducible setup, use non-interactive flags:

```bash
python3 -m monitor init --non-interactive \
  --coverage-preset software-data-ai \
  --packs data-software,cybersecurity \
  --include "backend,distributed systems,security engineering" \
  --exclude "unpaid,commission only" \
  --locations "remote,Baltimore" \
  --organizations "Example Lab" \
  --default-document Software \
  --target "Summer 2028"
```

Onboarding asks only about work preferences.
It does not request demographic or other protected personal attributes.

The optional macOS app presents the same profile as a guided setup screen with separate Basics, Sources, and Advanced pages.
It provides searchable local suggestions for city, state, country, and field of study, a structured degree-level control, and a searchable source-pack picker with source counts.
Advanced contains exact scoring controls, matching rules, and document routing, while the most intuitive bounded values use accessible sliders.

After onboarding, inspect or change the profile at any time:

```bash
python3 -m monitor profile show
python3 -m monitor profile set \
  --timeframe "Summer 2028" \
  --include "research software,scientific machine learning" \
  --exclude "sales,marketing" \
  --opportunity-types "internship,research_program" \
  --coverage-preset automatic \
  --packs "engineering,data-software,academia-research"
python3 -m monitor profile validate
```

`profile show --json` produces the same bounded editor object used by the app.
`profile apply --file PATH` and `profile apply --stdin` validate and atomically apply that object for advanced or scripted changes.
Every successful change rescores stored opportunities and rebuilds the dashboard without fetching the network.

## One-shot profile import

`profile template` prints the stable version 1 JSON shape intended for direct editing or generation by an AI model from a resume and writing samples.
`profile import` validates the bounded input, creates a separate named profile, activates it, rescores existing listings, and rebuilds the dashboard atomically.

```bash
python3 -m monitor profile template > my-profile.json
python3 -m monitor profile import --file my-profile.json --dry-run
python3 -m monitor profile import --file my-profile.json
```

The top-level keys are `version`, `name`, and optional `candidate`, `search`, `sources`, and `documents` objects.
Candidate keys are `stage`, `graduation`, `degrees`, `skills`, `max_experience_years`, `filter_nationality`, `citizenships`, `permanent_residencies`, and `us_national`.
Each degree can be a string or an object with `type` and `field`, where common forms such as `BS`, `bachelors`, `MS`, and `PhD` are normalized.
Search keys are `timeframes`, `opportunity_types`, `roles`, `domains`, `skills`, `locations`, `strict_locations`, `work_arrangements`, `remote_preference`, `exclude`, and `organizations`.
Sources accepts `mode` plus `packs`, and documents uses the same `default` and `routes` shape as the advanced editor.
Unknown keys are rejected so an AI-generated profile cannot silently misspell a preference.

Locations are canonicalized using an offline catalog derived from GeoNames country, first-level administrative region, and cities-with-population-over-15,000 datasets under CC BY 4.0.
Field-of-study suggestions use the U.S. Department of Education NCES 2020 Classification of Instructional Programs.
No location query or profile content is sent to a geocoding service.
Country selections match recognized states and cities in that country, state selections match recognized cities in that state, and city selections remain city-specific.
With `strict_locations` set to `true`, only confidently resolved mismatches are hidden, while ambiguous or missing locations remain visible for review.

## Degree and citizenship eligibility

The current stage determines enrollment eligibility; completed degrees do not turn a doctoral student into an undergraduate applicant.
Explicit bachelor's/master's-only audiences are hidden from doctoral profiles, while minimum-degree requirements and alternatives including doctoral students remain available.

In Profile > Basics, enable **Filter by citizenship eligibility**, select all citizenship countries, and add any permanent residencies.
Country choices use the existing offline catalog and support dual citizenship.
Leave permanent residency empty if you have none; leave citizenship empty if you do not want to declare it.
The separate U.S. national checkbox supports noncitizen nationals.
Birthplace, study location, and nationality labels alone are not substitutes for citizenship or residency status.

For imports, `citizenships` and `permanent_residencies` are lists of country names or ISO codes, and `filter_nationality` and `us_national` are booleans.
For example, `"filter_nationality": true, "citizenships": ["IN"], "permanent_residencies": []` filters known citizenship mismatches for an Indian citizen without permanent residency elsewhere.
The filter is off by default for existing profiles.
Saving changes rescores stored opportunities as well as future collections.

Explicit single-country citizenship and permanent-residency requirements produce eligibility gates with the source wording as evidence.
Sparse NSF GRFP listings also use its [official citizenship requirement](https://www.nsf.gov/funding/initiatives/grfp/eligibility), verified September 18, 2026: U.S. citizens, nationals, and permanent residents meet that particular requirement.
This does not certify other GRFP requirements such as academic stage or prior applications.
Complex multi-country rules and ambiguous exceptions remain unknown for review, and missing text is not proof of eligibility.
The parser is deterministic and offline; it does not infer visa sponsorship or immigration status.

## Named profiles

Each saved profile has its own name, matching preferences, coverage preset, source packs, source overrides, and private additions.
Only one profile is active at a time.
The active profile controls dashboard scores, discovery visibility, enabled source packs, scheduled scans, CLI searches, and notifications.

```bash
python3 -m monitor profile list --json
python3 -m monitor profile create "Clinical research"
python3 -m monitor profile duplicate "Clinical research" "Public health"
python3 -m monitor profile activate "Public health"
python3 -m monitor profile rename "Public health" "Epidemiology"
python3 -m monitor profile delete "Clinical research"
```

Profile arguments accept an immutable profile id or a unique case-insensitive name.
Creating a profile starts from neutral matching defaults and the starter pack.
Duplicating copies the selected profile, including private source definitions.
An active profile cannot be deleted until another profile is activated, and the final profile cannot be deleted.
Every mutating command supports `--dry-run`; output-producing mutations also support `--quiet`.

The optional app provides the same create, duplicate, rename, activate, delete, and edit workflows with labeled controls and confirmation for deletion.
Activating a profile takes the existing lifecycle lock and scan lock, changes the active id atomically, refreshes source registration, rescores stored opportunities, and rebuilds the dashboard.
Bookmarks and application workflow status remain shared across profiles.

## Configuration layers

Tracked defaults live in `config/profile.json` and `config/sources.json`.
They are anonymous and neutral.

The ignored `config/profiles.local.json` store overrides the tracked defaults through its active entry.
Older `config/profile.local.json` and `config/sources.local.json` files remain supported as a single legacy profile.
The first named-profile mutation imports that canonical legacy pair without deleting or rewriting it, so it remains a recovery backup.
An explicit `OPPORTUNITY_RADAR_PROFILE` or `OPPORTUNITY_RADAR_SOURCES` path has the highest precedence.
The older `OPPORTUNITY_MONITOR_*` names remain accepted for compatibility.

After the scheduler is installed, both the clone CLI and native app use the recognized private runtime as the canonical writable location for ignored settings.
Installer upgrades preserve that canonical copy, so app changes are not replaced by an older file in the clone.

Objects merge recursively.
Lists replace the corresponding lower-precedence list.
Sources merge by `id`, so a local entry can change one field without copying the public entry.

## Source packs

List the available packs and their source counts:

```bash
python3 -m monitor sources packs
```

The public catalog includes these packs:

- `starter-diverse`
- `engineering`
- `data-software`
- `cybersecurity`
- `product-design`
- `biotech-health`
- `climate-energy`
- `public-interest`
- `academia-research`
- `fellowships`
- `finance-quant`
- `ai-research`
- `skilled-technical`
- `national-labs`
- `national-security`
- `students-early-career`
- `space-aerospace`
- `robotics-autonomy`
- `education-social-impact`
- `mathematical-sciences`
- `physics-quantum-astronomy`
- `chemistry-materials`
- `earth-geospatial`
- `ecology-environment`
- `agriculture-food`
- `medicine-clinical`
- `public-health`
- `pharma-drug-discovery`
- `biomedical-neuroscience`
- `civil-infrastructure`
- `electronics-semiconductors`
- `ocean-marine`
- `veterinary-animal`

A source may belong to multiple packs but is fetched only once per scan.
The starter pack enables five structured, no-secret employer feeds.
Other packs are selected directly or by a coverage preset, while a new clone without onboarding remains fast and predictable.

## STEM coverage presets

The default `automatic` preset recalculates effective source packs from the profile's roles, domains, skills, career stage, and opportunity types whenever the profile is saved.
It recognizes broad software, data, AI, engineering, robotics, mathematics, quantitative science, physics, space, chemistry, materials, Earth science, climate, life science, medicine, pharma, public health, agriculture, infrastructure, electronics, ocean science, and veterinary targets.
The editor and onboarding also offer named presets for each of those areas when a user wants a predictable fixed baseline.
Selecting a named preset merges a short editable set of representative roles, domains, and skills into the profile, without replacing targets the user already entered.
The `manual` preset uses only explicitly checked packs.
Explicitly checked packs are retained in automatic and named modes, so a user can extend a preset without cloning its whole definition into private configuration.

```bash
python3 -m monitor profile set --coverage-preset medicine-health
python3 -m monitor profile set --coverage-preset automatic
python3 -m monitor profile set --coverage-preset manual --packs medicine-clinical,public-health
```

## Matching rules

The fit engine is deterministic and source-independent.
Source identity, source health, deadlines, and publication recency do not change fit.

New onboarding profiles and profiles saved through the current app or CLI use `matching.engine: "structured_v2"`.
The editor migrates supported legacy role, skill, arrangement, exclusion, stage, and timeframe aliases into the visible canonical fields, then removes those hidden aliases on save.
The structured engine treats title and organization evidence as stronger than category or eligibility evidence, and treats a description-only mention as supporting evidence rather than a strong match.
It separates interest, target, qualification, and preference dimensions, retains the exact evidence behind each component, and applies explicit compatibility gates before assigning a visible tier.
Senior roles, clearly incompatible degree requirements, excessive required experience, and conflicting named search cycles can therefore be hidden even when a generic keyword appears in the description.
Unknown information stays unknown instead of being treated as either a pass or a failure.

`matching.minimum_display_score` hides low-value discovery matches.
`matching.score_ceilings` can cap records with no strong interest anchor, description-only evidence, or unknown eligibility.
`matching.field_weights` changes the relative strength of each listing field.
`matching.target_type_bonus`, `matching.target_timeframe_bonus`, and `matching.anchor_min_strength` tune the structured engine without changing its evidence model.

`matching.base_score` sets the neutral starting point.
`matching.tier_thresholds` maps the final 0 to 100 score to `priority`, `strong`, `watch`, or `skip`.
`matching.priority_organization_bonus` applies only to exact names in `priority_organizations`.

Each rule supports:

- `id` as a stable local identifier.
- `label` as the explanation shown in the dashboard.
- `weight` as positive or negative integer points.
- `terms` as phrases matched without case sensitivity.
- `fields` chosen from `title`, `organization`, `location`, `description`, `eligibility`, `category`, and `opportunity_type`.
- `match` set to `any` by default or `all` when every term is required.
- `per_term` to multiply the weight by the number of matched terms.
- `max_hits` to bound a per-term rule.
- `dimension` set to `interest`, `target`, `qualification`, or `preference`.
- `anchor` to allow strong evidence from the rule to establish core role relevance.
- `hard_gate` to require a positive rule or reject a matched negative rule.

Punctuation-bearing terms such as `C++`, `C#`, `.NET`, `R&D`, and hyphenated phrases are supported.

```json
{
  "matching": {
    "base_score": 45,
    "tier_thresholds": {
      "priority": 80,
      "strong": 65,
      "watch": 25
    },
    "rules": [
      {
        "id": "research_software",
        "label": "Research software",
        "weight": 14,
        "fields": ["title", "description", "category"],
        "terms": ["scientific computing", "C++", "research software"],
        "per_term": true,
        "max_hits": 3
      },
      {
        "id": "unwanted_work",
        "label": "Excluded work",
        "weight": -45,
        "fields": ["title", "description"],
        "terms": ["commission only", "unpaid"]
      }
    ]
  }
}
```

Missing eligibility information remains unknown.
The legacy engine changes scores only through explicit rules.
The structured engine also uses the candidate stage, maximum acceptable experience requirement, requested opportunity types, and named search cycles as auditable compatibility checks.

## Resume and CV routing

Document labels are local and can represent resumes, academic CVs, portfolios, or cover-letter tracks.
The first route with the most matched terms wins, so route order resolves ties.

```json
{
  "dashboard": {
    "document_label": "Application track"
  },
  "documents": {
    "default": "General",
    "routes": [
      {
        "label": "Research CV",
        "fields": ["title", "description", "category"],
        "terms": ["postdoc", "research fellow", "university"]
      },
      {
        "label": "Software",
        "terms": ["software engineer", "backend", "infrastructure"]
      }
    ]
  }
}
```

Legacy `positive_rules`, `negative_rules`, `resume_routing`, and `default_resume_code` fields still load, but new configurations should use `matching` and `documents`.
For structured profiles, selected basic roles and domains are authoritative over positive advanced interest rules.
Removing a target retires positive rules tied only to that target, while cross-target, negative, qualification, and hard-gate rules are retained.

## Source registry

Prefer a documented official feed.
The supported structured adapters are `ashby`, `greenhouse`, `lever`, and `jibe`.
`html_links` is an experimental conservative link extractor.
`watch_page` checks semantic visible-page changes without creating an active opportunity by default.

Every complete source object uses these common fields:

- `id` is a stable lowercase identifier using underscores.
- `name` is the organization or program name shown to users.
- `kind` selects one of the six adapters described below.
- `url` is the official public careers, program, or listing page.
- `api_url` records the official structured endpoint when the adapter uses one.
- `source_type` classifies the resource as `listing_feed`, `change_monitor`, `program_calendar`, `official_portal`, or `manual_page`.
- `packs`, `domains`, `opportunity_types`, `career_levels`, and `regions` provide reusable taxonomy arrays.
- `default_opportunity_type` supplies the fallback type when a listing title has no more specific fellowship, internship, postdoc, or similar marker.
- `support_level` records whether the adapter is `supported`, `experimental`, or `manual` for that source.
- `official`, `verified_at`, `cadence_hours`, and `enabled` record provenance and scan behavior.
- `description_availability: "title_only"` identifies feeds that cannot safely provide full listing text, so the dashboard can explain their limited matching detail.

Collection URLs must use HTTPS on the standard port, contain no credentials, and resolve only to public addresses.
Use `career_levels: ["any"]` or `regions: ["global"]` when a narrower value would be misleading.

Source-pack choices are stored inside each entry in `config/profiles.local.json`.
The source object within an entry uses the same registry shape as the older `config/sources.local.json` file:

```json
{
  "schema_version": 2,
  "coverage_preset": "automatic",
  "selected_packs": ["data-software", "public-interest"],
  "sources": [
    {"id": "figma_greenhouse", "enabled": false},
    {"id": "cfs_lever", "enabled": true}
  ]
}
```

Pack selection and coverage presets apply dynamically, so future catalog sources in an effective pack follow the user's choice without rewriting the local file.
An individual `enabled` entry in the same local registry overrides pack membership.
Saving a pack change through the profile editor removes positive built-in overrides that no longer belong to any selected pack, so turning off a pack also hides those sources and their prior listings.
Explicit disables and complete private source definitions are preserved.
`python3 -m monitor sources packs` shows both total resources and currently supported listing feeds in each pack.
Manual directories use `auto_enable: false`, remain browseable in the dashboard resource library, and are not fetched merely because a pack is selected.
An explicit private `enabled: true` override can opt one of those resources into change monitoring.

### Add and manage private sources

The CLI recognizes hosted Greenhouse, Lever, and Ashby board URLs, validates the source, performs a read-only test fetch, and atomically saves it to the canonical private registry.

```bash
python3 -m monitor sources add \
  --name "Example Organization" \
  --url "https://boards.greenhouse.io/example" \
  --packs "engineering,students-early-career"
python3 -m monitor sources show example_organization
python3 -m monitor sources disable example_organization
python3 -m monitor sources enable example_organization
python3 -m monitor sources remove example_organization
```

Use `--dry-run` to validate without writing.
Use `--skip-test` only when the official service is temporarily unavailable and you have independently verified the provider URL.
A generic careers page cannot reliably become a structured feed, so automatic detection falls back to a one-page bounded `html_links` source.
Choose `--kind watch_page` when you only want change detection and no individual opportunity records.
Built-in sources can be enabled or disabled, but only private sources can be removed.

### Ashby

Set `board` to the organization slug from its `jobs.ashbyhq.com` URL.
The adapter reads Ashby's documented public job-board endpoint without credentials and normalizes listed jobs, locations, departments, teams, work arrangements, employment types, and publication dates.

```json
{
  "id": "example_ashby",
  "name": "Example Research Company",
  "kind": "ashby",
  "board": "example",
  "url": "https://jobs.ashbyhq.com/example",
  "api_url": "https://api.ashbyhq.com/posting-api/job-board/example",
  "source_type": "listing_feed",
  "packs": ["ai-research"],
  "domains": ["software", "ai_ml"],
  "opportunity_types": ["job", "internship"],
  "career_levels": ["early_career", "experienced"],
  "regions": ["global"],
  "official": true,
  "support_level": "supported",
  "verified_at": "2028-01-15",
  "cadence_hours": 12,
  "enabled": true
}
```

### Greenhouse

Set `board` to the organization slug from its Greenhouse job-board URL.
The adapter reads the canonical `boards-api.greenhouse.io` endpoint and includes full job content.
Keep the matching canonical endpoint in `api_url` so configuration checks and readers can inspect it.

```json
{
  "id": "example_greenhouse",
  "name": "Example Organization",
  "kind": "greenhouse",
  "board": "example",
  "url": "https://example.org/careers",
  "api_url": "https://boards-api.greenhouse.io/v1/boards/example/jobs",
  "source_type": "listing_feed",
  "packs": ["data-software"],
  "domains": ["software", "data"],
  "opportunity_types": ["job", "internship"],
  "career_levels": ["early_career", "experienced"],
  "regions": ["global"],
  "official": true,
  "support_level": "supported",
  "verified_at": "2028-01-15",
  "cadence_hours": 12,
  "enabled": true
}
```

### Lever

Set `site` to the organization slug used by `api.lever.co`.
The adapter reads the canonical JSON postings endpoint and derives job type from the title and Lever categories.

```json
{
  "id": "example_lever",
  "name": "Example Cooperative",
  "kind": "lever",
  "site": "example",
  "url": "https://example.org/careers",
  "api_url": "https://api.lever.co/v0/postings/example?mode=json",
  "source_type": "listing_feed",
  "packs": ["engineering"],
  "domains": ["engineering"],
  "opportunity_types": ["job", "internship"],
  "career_levels": ["any"],
  "regions": ["global"],
  "official": true,
  "support_level": "supported",
  "verified_at": "2028-01-15",
  "cadence_hours": 12,
  "enabled": true
}
```

### Jibe

Set `api_url` to a Jibe endpoint that returns a `jobs` list and `totalCount`.
The adapter increments the `page` query parameter until every advertised job has been read within its safety limits.
An optional `job_url_template` may use `{slug}` and `{req_id}` placeholders.

```json
{
  "id": "example_jibe",
  "name": "Example Laboratory",
  "kind": "jibe",
  "url": "https://careers.example.org/jobs",
  "api_url": "https://careers.example.org/api/jobs?page=1&limit=100",
  "job_url_template": "https://careers.example.org/jobs/{slug}",
  "source_type": "listing_feed",
  "packs": ["academia-research"],
  "domains": ["academia_research"],
  "opportunity_types": ["job", "internship", "postdoc"],
  "career_levels": ["any"],
  "regions": ["global"],
  "official": true,
  "support_level": "supported",
  "verified_at": "2028-01-15",
  "cadence_hours": 12,
  "enabled": true
}
```

### HTML links

Use `html_links` only when no structured feed is available and the official page contains stable links with meaningful anchor text.
`include` keeps links whose title or URL contains at least one term.
`exclude` removes links whose title or URL contains any term.
`same_domain` rejects links to another host.
`pages` reads a bounded sequence of page-numbered results, from 1 through 20 pages.
Relative links honor a valid HTML `base` element, while `link_base_url` can provide an explicit base for an unusual official page.
Program cards beginning with `PROGRAM` and an `ACCEPTING APPLICATIONS` marker are reduced to their concise program names.

```json
{
  "id": "example_links",
  "name": "Example Foundation",
  "kind": "html_links",
  "url": "https://example.org/opportunities",
  "pages": 3,
  "include": ["fellowship", "internship"],
  "exclude": ["newsletter", "archive"],
  "same_domain": true,
  "source_type": "official_portal",
  "packs": ["fellowships"],
  "domains": ["fellowships"],
  "opportunity_types": ["fellowship", "internship"],
  "default_opportunity_type": "program",
  "career_levels": ["any"],
  "regions": ["global"],
  "official": true,
  "support_level": "experimental",
  "verified_at": "2028-01-15",
  "cadence_hours": 24,
  "enabled": true
}
```

### Watch pages

A normal `watch_page` hashes visible semantic text, records health, and creates an event when that text changes after the first successful check.
It does not turn the page into a current opportunity.
Set `notify_page_changes` to `false` to retain dashboard events without sending change notifications.
Set `publish_as_opportunity` to `true` only when one durable page-level record is genuinely useful, and use `watch_title` to name that record.

```json
{
  "id": "example_program",
  "name": "Example Summer Program",
  "kind": "watch_page",
  "url": "https://example.edu/program",
  "publish_as_opportunity": false,
  "notify_page_changes": true,
  "source_type": "program_calendar",
  "packs": ["academia-research"],
  "domains": ["academia_research"],
  "opportunity_types": ["research_program"],
  "career_levels": ["undergraduate"],
  "regions": ["united_states"],
  "official": true,
  "support_level": "manual",
  "verified_at": "2028-01-15",
  "cadence_hours": 24,
  "enabled": true
}
```

### Optional source controls

Local structured and HTML-link sources may use `item_include` and `item_exclude` to filter normalized listings.
The default `item_filter_scope` is `title`, while `full` also checks description and location.
Use these filters only in private configuration because broad official feeds make the public catalog reusable.
Set singular `opportunity_type` only when every record from a private source has one known type.
Otherwise, keep the broader `opportunity_types` taxonomy and let the adapter infer each record from its title and structured fields.

`expected_http_statuses` accepts HTTP status integers that an official source is known to return when automated access is unavailable.
An expected response is recorded as `blocked`, prior records remain active, and the scan does not treat it as a source failure.
Do not list a status merely to hide an unexpected outage.

```json
{
  "id": "example_program",
  "expected_http_statuses": [403]
}
```

### Custom packs

Add private pack definitions and complete private source objects through the CLI or to the active entry's `sources` object in `config/profiles.local.json`.
`selected_packs` replaces the lower-precedence selection, so include every pack you want enabled.
A directly managed source-level `enabled` value overrides pack membership until a later profile-editor pack removal retires an out-of-pack positive override.

```json
{
  "schema_version": 2,
  "packs": [
    {
      "id": "robotics-local",
      "name": "Robotics",
      "description": "Robotics research, controls, hardware, and autonomy."
    }
  ],
  "selected_packs": ["robotics-local"],
  "sources": [
    {
      "id": "example_robotics",
      "name": "Example Robotics Institute",
      "kind": "greenhouse",
      "board": "examplerobotics",
      "url": "https://example.org/careers",
      "api_url": "https://boards-api.greenhouse.io/v1/boards/examplerobotics/jobs",
      "source_type": "listing_feed",
      "packs": ["robotics-local"],
      "domains": ["engineering", "software"],
      "opportunity_types": ["job", "internship"],
      "career_levels": ["any"],
      "regions": ["global"],
      "official": true,
      "support_level": "supported",
      "verified_at": "2028-01-15",
      "cadence_hours": 12,
      "enabled": true
    }
  ]
}
```

## Optional curated Markdown

`curated_pipeline_path` may point to a private Markdown file containing table rows with HTTP or HTTPS links.
Relative paths resolve from the repository root.
The macOS scheduler copies the chosen seed into its private runtime.

```markdown
| Opportunity | Notes | Deadline | Track |
|---|---|---|---|
| [Example Institute Research Internship](https://example.org/jobs/1) | Scientific computing | February 15, 2028 | `Research CV` |
```

The importer infers broad opportunity types and preserves a matching backticked document label.
It does not assign a supposedly easier or safer acceptance tier.

## Validate changes

```bash
python3 -m monitor doctor
python3 -m monitor sources list
python3 -m monitor sources show SOURCE_ID
python3 -m monitor sources test SOURCE_ID
python3 -m monitor opportunities search QUERY --json
```

`sources test` performs one read-only live fetch and does not save its results.
Review any configuration received from someone else before running it because local configuration is trusted input.
