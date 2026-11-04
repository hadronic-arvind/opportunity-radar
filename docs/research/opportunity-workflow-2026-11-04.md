# Opportunity discovery and application workflow research

Reviewed official product documentation and publisher requirements on November 4, 2026.
The review compared a general job marketplace, a government hiring portal, a campus career platform, a research mobility portal, a grant application portal, two application trackers, and a page-change monitor.
It focused on decisions that recur across jobs, internships, scholarships, fellowships, grants, and training.
The findings describe documented workflows.
They do not measure market share, user demand, coverage, or outcomes.

## What the tools optimize for

| Tool | Documented workflow | Design implication for Radar |
| --- | --- | --- |
| LinkedIn | Job recommendations use profile and preference signals, and searches support date, employer, and location filtering. | Keep a direct search path and make each recommendation's evidence legible. [LinkedIn recommendations](https://www.linkedin.com/help/linkedin/answer/a512279), [LinkedIn search filters](https://www.linkedin.com/help/linkedin/answer/a507441) |
| USAJOBS | Search supports multiple locations and separates remote work from telework. Saved alerts rerun criteria and users can sort by closing date. | Keep search criteria reusable and location controls explicit. Surface deadlines as planning inputs. [USAJOBS search](https://help.usajobs.gov/how-to/search), [USAJOBS location](https://help.usajobs.gov/how-to/search/filters/location), [USAJOBS remote work](https://help.usajobs.gov/how-to/search/filters/remote) |
| Handshake | Saved searches retain criteria. Saved jobs can trigger notices for changed postings and approaching deadlines. | Distinguish a reusable query, a saved listing, a new match, and a publisher revision. [Handshake search alerts](https://support.joinhandshake.com/hc/en-us/articles/218693388-Saving-Job-Searches-and-Receiving-Job-Alerts), [Handshake saved job notifications](https://support.joinhandshake.com/hc/en-us/articles/227072527-My-Saved-Jobs-in-Handshake) |
| EURAXESS | One interface searches jobs, funding, and hosting offers, with country, academic level, research field, and funding facets, and deadline sorting. | Keep typed cross-domain opportunities in one collection. An academic search may need a host or a funding call alongside a research position. [EURAXESS opportunities search](https://euraxess.ec.europa.eu/jobs/search) |
| Grants.gov | Users can follow all new calls, saved criteria, or individual calls. Applications collect forms and attachments in a separate workspace. | Treat new matching listings, changes to one listing, and applicant preparation as separate work. [Grants.gov subscriptions](https://www.grants.gov/connect/manage-subscriptions), [Grants.gov application workspace](https://www.grants.gov/help/applicants/how-to-apply-for-grants) |
| Teal and Huntr | Trackers organize applications across sites, attach material, record activities, and capture publisher pages. Teal offers per-application checklists and follow-up planning. | Let a user carry a private action plan across publisher sites and keep a local copy of opportunity text they supply. [Teal job tracker](https://www.tealhq.com/tools/job-tracker), [Huntr job tracker](https://huntr.co/product/job-tracker), [Huntr job clipper](https://huntr.co/product/job-clipper) |
| Visualping | A dedicated change monitor highlights changes between captured page versions. | Compare consecutive observations of the same source record and keep capture dates distinct from publication dates. [Visualping](https://visualping.io/) |

## Location model

A country selection represents the country and its descendants, while a city selection represents that city in its recorded country and region.
A country-only publisher location does not establish a particular city.
When two places share a name, a parent qualifier is evidence and name popularity is insufficient.
Treat multiple distinct workplace sites as alternatives for one opportunity.
Use separate work-arrangement and physical geography dimensions, because a remote posting may only accept workers from named countries or regions, and hybrid usually implies a reporting site.
A region restriction is a location observation, distinct from nationality, residence documentation, sponsorship, or general eligibility.
Expose unknown or incomplete evidence in filters and matching, and retain the publisher's literal location text for review.
[USAJOBS location filtering](https://help.usajobs.gov/how-to/search/filters/location), [USAJOBS remote and telework](https://help.usajobs.gov/how-to/search/filters/remote), [Google Search Central job location fields](https://developers.google.com/search/docs/appearance/structured-data/job-posting)

A bounded offline place vocabulary can improve recognition and disambiguation without requesting the user's coordinates or sending their selections to a service.
Potential expansion data include ISO-linked subdivision names from CLDR, multilingual variants and populated-place records from the attributed GeoNames export, and official US feature records from GNIS.
A larger gazetteer can improve coverage, but employer naming, regional abbreviations, inaccurate strings, and dispersed remote eligibility still limit what is inferable.
Consider radius or commute estimates only when coordinates, a chosen starting point, and the intended distance definition are available.
[Unicode CLDR territory subdivisions](https://www.unicode.org/cldr/charts/49/supplemental/territory_subdivisions.html), [GeoNames data and license](https://www.geonames.org/about.html), [USGS Geographic Names Information System](https://www.usgs.gov/faqs/what-geographic-names-information-system-gnis)

| Approach | Benefit | Cost for Radar |
| --- | --- | --- |
| Publisher text alone | No additional data or startup work. | A text query for a country misses a city-only record and cannot reliably distinguish shared place names. |
| Explicit place paths with local indexing | Country and region queries match descendant paths, and city matches remain reviewable. | The coverage of ambiguous and sparse publisher text depends on the local resolver and source fields. |
| Full offline gazetteer | Broader name, alias, coordinate, and subdivision coverage. | Requires licensing attribution, a reproducible data build, package size limits, and a refresh policy. |
| Online geocoding on each search | Potential access to frequently updated places and routing data. | Introduces query disclosure, network failure, rate limits, and online-service dependencies into a private local workflow. |

The architecture decision is to index normalized location evidence from captured listing data alongside the existing lexical inventory.
Use normalized paths for a country or region query, while keeping the original publisher text for review.
Treat location preferences, location-only discovery, and direct keyword retrieval as separate user actions.
The parser can keep a publisher's location unresolved, and an explicit strict filter should make that omission visible.
An expanded gazetteer can use the same stored hierarchy without changing the user's intent.

## Ten product decisions

Priority here reflects how often the task can recur, the cost of missing it, and whether Radar can implement the workflow locally with reliable inputs.
The evidence below supports the workflow need and the design guardrails.
It does not establish that other products lack the same capability.

| Priority | Feature | User task and Radar design | Primary evidence |
| --- | --- | --- | --- |
| 1 | Saved searches | Reopen a named query and its filters after the collection changes. Keep the query distinct from the saved listings it returns. | [USAJOBS job alerts](https://help.usajobs.gov/how-to/search/job-alerts), [Handshake saved searches](https://support.joinhandshake.com/hc/en-us/articles/218693388-Saving-Job-Searches-and-Receiving-Job-Alerts), [Grants.gov saved criteria](https://grants.gov/help/connect/subscribe-to-saved-searches) |
| 1 | New-match review | Review the locally collected matches added since an explicit review checkpoint. Local first-seen time describes Radar's observation, independent of when the publisher opened applications. | [Grants.gov subscriptions](https://www.grants.gov/connect/manage-subscriptions), [USAJOBS alerts](https://help.usajobs.gov/how-to/search/job-alerts) |
| 1 | Deadline agenda | Plan a shortlist by its recorded due dates. Preserve conflicting or uncertain dates for publisher verification. A listing without a date remains visible elsewhere. | [Handshake saved-job reminders](https://support.joinhandshake.com/hc/en-us/articles/227072527-My-Saved-Jobs-in-Handshake), [EURAXESS deadline sorting](https://euraxess.ec.europa.eu/jobs/search), [NIH application deadlines](https://www.grants.nih.gov/grants/policy/nihgps/HTML5/section_2/2.3.9_application_receipt_information_and_deadlines.htm) |
| 1 | Calendar export | Carry usable deadlines into the calendar the user already uses. Export a date-only event when the source records a day, and preserve provenance and caution about exact publisher cutoffs. | [iCalendar specification](https://datatracker.ietf.org/doc/rfc5545/), [Apple Calendar import](https://support.apple.com/en-euro/guide/calendar/icl1023/mac) |
| 1 | Next actions | Record the next step and its target date independently of application status and publisher deadline. Users can plan a reference request, draft review, or interview follow-up without inferring anything from a posting. | [Teal follow-ups](https://www.tealhq.com/tools/job-tracker), [Huntr activities](https://huntr.co/product/job-tracker), [NSF fellowship references](https://www.nsf.gov/funding/initiatives/grfp/references) |
| 1 | Application checklist | Keep user-confirmed tasks for letters, references, portfolios, forms, or advisor approvals beside the listing. A check marks the user's preparation and does not establish publisher receipt. | [Grants.gov application guide](https://www.grants.gov/help/applicants/how-to-apply-for-grants), [NSF reference requirements](https://www.nsf.gov/funding/initiatives/grfp/references/faq), [Teal checklists](https://www.tealhq.com/tools/job-tracker) |
| 1 | Publisher revision comparisons | Compare captured values for location, date, requirements, and description within one publisher record. Reuse stored observation history and avoid attributing syndicated differences to a publisher edit. | [Handshake posting changes](https://support.joinhandshake.com/hc/en-us/articles/227072527-My-Saved-Jobs-in-Handshake), [Grants.gov selected opportunity updates](https://www.grants.gov/connect/manage-subscriptions), [Visualping](https://visualping.io/) |
| 2 | Related opportunities | Use specific listing terms to surface adjacent items across jobs, fellowships, or other forms. Show which terms are shared and exclude hidden and explicitly expired suggestions. | [LinkedIn recommendation inputs](https://www.linkedin.com/help/linkedin/answer/a512279), [EURAXESS opportunity types](https://euraxess.ec.europa.eu/jobs/search), [SQLite FTS5 column weights and filters](https://www.sqlite.org/fts5.html) |
| 2 | Quick capture | Add a specific opportunity found outside configured feeds from its public link and user-supplied text. Keep its source visible and use the regular validation and import path. | [Huntr job clipper](https://huntr.co/product/job-clipper), [Teal bookmarking](https://www.tealhq.com/tools/job-tracker) |
| 2 | Pay, costs and support evidence | Group sourced quotes about salary or stipends, expenses or fees, and funding or reimbursement. Keep conditions, negation, and missing information explicit. A grant amount, training fee, and wage are different financial signals. | [Google employer salary schema](https://developers.google.com/search/docs/appearance/structured-data/job-posting), [USAJOBS pay and locality](https://help.usajobs.gov/how-to/search/filters/remote), [EURAXESS funding offers](https://euraxess.ec.europa.eu/jobs/search) |

Radar's architecture supports these workflows over the private SQLite workspace and explicit collection bursts.
The acceptance cases should cover save and reopen, a genuinely new local match, unchanged rescans, same-source revisions, conflict or absent deadline, multiple calendar clients or a standards parser, preservation of application notes and statuses, and job, fellowship, grant, and training examples.
The useful failure states are as important as the happy path, especially incomplete collection, uncertain location, conditional support, and a missing original posting.
