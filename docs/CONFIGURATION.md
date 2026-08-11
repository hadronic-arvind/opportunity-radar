# Configuration

## Start with onboarding

Run the interactive setup from the repository root:

```bash
python3 -m monitor init
```

The command writes only `config/profile.local.json` and `config/sources.local.json`.
Both files are ignored by Git and written with mode `0600`.
Existing local configuration is never replaced unless you explicitly pass `--force`.

For scripts or reproducible setup, use non-interactive flags:

```bash
python3 -m monitor init --non-interactive \
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

The optional macOS app presents the same profile as a guided setup screen.
It uses selectable single- and multiple-choice controls for source packs, career stage, opportunity types, work arrangements, and remote preference, with open text entries for timeframes, roles, skills, locations, exclusions, and organizations.

After onboarding, inspect or change the profile at any time:

```bash
python3 -m monitor profile show
python3 -m monitor profile set \
  --timeframe "Summer 2028" \
  --include "research software,scientific machine learning" \
  --exclude "sales,marketing" \
  --opportunity-types "internship,research_program" \
  --packs "engineering,data-software,academia-research"
python3 -m monitor profile validate
```

`profile show --json` produces the same bounded editor object used by the app.
`profile apply --file PATH` and `profile apply --stdin` validate and atomically apply that object for advanced or scripted changes.
Every successful change rescores stored opportunities and rebuilds the dashboard without fetching the network.

## Configuration layers

Tracked defaults live in `config/profile.json` and `config/sources.json`.
They are anonymous and neutral.

Ignored local files override the tracked defaults.
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

A source may belong to multiple packs but is fetched only once per scan.
The starter pack enables five structured, no-secret employer feeds.
Other packs are opt-in so a new clone remains fast and predictable.

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

## Source registry

Prefer a documented official feed.
The supported structured adapters are `greenhouse`, `lever`, and `jibe`.
`html_links` is an experimental conservative link extractor.
`watch_page` checks semantic visible-page changes without creating an active opportunity by default.

Every complete source object uses these common fields:

- `id` is a stable lowercase identifier using underscores.
- `name` is the organization or program name shown to users.
- `kind` selects one of the five adapters described below.
- `url` is the official public careers, program, or listing page.
- `api_url` records the official structured endpoint when the adapter uses one.
- `source_type` classifies the resource as `listing_feed`, `program_calendar`, `official_portal`, or `manual_page`.
- `packs`, `domains`, `opportunity_types`, `career_levels`, and `regions` provide reusable taxonomy arrays.
- `support_level` records whether the adapter is `supported`, `experimental`, or `manual` for that source.
- `official`, `verified_at`, `cadence_hours`, and `enabled` record provenance and scan behavior.

Collection URLs must use HTTPS on the standard port, contain no credentials, and resolve only to public addresses.
Use `career_levels: ["any"]` or `regions: ["global"]` when a narrower value would be misleading.

Select packs in `config/sources.local.json`:

```json
{
  "schema_version": 2,
  "selected_packs": ["data-software", "public-interest"],
  "sources": [
    {"id": "figma_greenhouse", "enabled": false},
    {"id": "cfs_lever", "enabled": true}
  ]
}
```

Pack selection applies dynamically, so future catalog sources in a selected pack follow the user's choice without rewriting the local file.
An individual `enabled` entry in the same local registry overrides pack membership.
`python3 -m monitor sources packs` shows both total resources and currently supported listing feeds in each pack.

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

Add private pack definitions and complete private source objects to `config/sources.local.json`.
`selected_packs` replaces the lower-precedence selection, so include every pack you want enabled.
A source-level `enabled` value still overrides pack membership.

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
python3 -m monitor sources test SOURCE_ID
```

`sources test` performs one read-only live fetch and does not save its results.
Review any configuration received from someone else before running it because local configuration is trusted input.
