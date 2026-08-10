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

## Configuration layers

Tracked defaults live in `config/profile.json` and `config/sources.json`.
They are anonymous and neutral.

Ignored local files override the tracked defaults.
An explicit `OPPORTUNITY_RADAR_PROFILE` or `OPPORTUNITY_RADAR_SOURCES` path has the highest precedence.
The older `OPPORTUNITY_MONITOR_*` names remain accepted for compatibility.

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
Only your explicit rules can lower or exclude an item.

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

Every public source describes:

- `id`, `name`, `kind`, `url`, and any adapter identifier.
- `source_type` as `listing_feed`, `program_calendar`, `official_portal`, or `manual_page`.
- `packs`, `domains`, `opportunity_types`, `career_levels`, and `regions` as arrays.
- `support_level` as `supported`, `experimental`, or `manual`.
- `official`, `verified_at`, `cadence_hours`, and `enabled`.

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

A new Greenhouse source looks like this:

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

Set `publish_as_opportunity` on a local watch-page override only when you deliberately want one durable page record in the results.
A normal watch page records health and page-change events without pretending the program is currently open.

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
