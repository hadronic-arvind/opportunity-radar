# Opportunity Radar discovery overhaul

## Product model

Opportunity Radar is a local search workspace for people deciding what to pursue next.
The result should be a shortlist with enough source evidence to decide whether to act.
A useful opportunity can offer paid work, supervised practice, research access, funding, education, or a time-limited event.
These attributes can coexist and the listing brief preserves source evidence for each.
The organization is the publisher or host of an opportunity, and its industry does not define what the participant will do.

1. **Collect** public listings and keep their source, dates, and freshness state.
2. **Explore** the collected inventory with direct search even when the profile has no matching evidence.
3. **Evaluate** source facts, relevance to goals, and explicit compatibility separately.
4. **Track** decisions in local storage keyed by stable opportunity IDs.
5. **Inspect coverage** to see which sources were checked, deferred, blocked, or available only as directories.

A first-time user can start from the opportunity kind, a desired role, or a named profile.
Profiles guide recommendations and source selection.
A search can reach outside a profile without changing it.
Saving and application status survive later source changes and profile switches.

## Audit and implementation

| Observed behavior | Implemented response |
| --- | --- |
| A due scan checked every eligible source in one sequential run before publishing results | Regular scans work in a 45-second collection window and publish partial progress after the current source finishes. Explicit source and force modes support their own budgets |
| An unchanged 1,200-row fixture was scored 1,200 times on every refresh | Complete snapshots verify membership and skip repeat scoring. Changed snapshots score only new or changed rows |
| A 40-listing Workday fixture made 42 requests, including a detail request for every listing | Keep all 40 listing summaries and rotate through a bounded detail budget. The same fixture now makes 14 requests |
| A temporary failure could make previously detailed results sparse | Preserve cached details only when enrichment explicitly failed or was deferred, and label the assessment's source completeness |
| Cross-industry employer feeds were one network check per employer, with most custom pages limited to HTML links | Add rolling multi-employer feeds, RSS and Atom, and public structured listing extraction. Separate program directories from listing evidence |
| A listing below the detailed snapshot cutoff or assigned the skip tier was unreachable in dashboard search | Provide an explicit All collected scope with compact rows beyond the detailed result set |
| The CLI missed category-only and accent-insensitive matches and stopped on an active scan lock | Search across listing and source fields and read committed SQLite state during collection |
| A profile requesting only hackathons recommended none in a representative fixture | An explicitly requested type can establish interest when no role or subject is configured |
| Literal role labels missed common cross-industry title variants | Add narrow auditable title equivalents for derived role preferences |
| A compound enrollment requirement could disqualify someone before checking its alternative | Mark unparsed alternatives and exceptions for review and show the original language |
| A past event and a rolling-feed record looked like ordinary active listings | Store the event schedule independently of the application deadline and display known closure and unconfirmed availability |
| Multiple feeds could display duplicate copies of one listing | Group conservative duplicates in Discover and expose equivalent grouping in CLI search. Keep source-specific records and workflow IDs |
| Browser review and CLI inspection offered different evidence | Use a shared source brief for activities, benefits, requirements, attribution, and availability |
| Saved and application records were mixed with discovery controls | Give saved, planned, applied, and dismissed items a dedicated tracker with its own filters |
| Optional notifications could send a summary to a configured external webhook | Keep change alerts local and remove webhook configuration and delivery |

## Architecture decisions

The local SQLite database remains authoritative for the CLI and optional macOS host.
The browser dashboard is an atomic static file with bounded detailed cards and compact catalog rows.
A regular browser keeps convenience actions in that browser's storage.
CLI reads use a committed snapshot while scans, profile writes, and tracker mutations share the existing lock.
Each source commits independently so partial runs are usable and safe to resume.

Collection reads public HTTPS URLs from configured sources.
The same privacy and network validation apply to the new adapters.
Profiles, search terms, fit evidence, and application decisions stay local.
There is no model download, service dependency, or idle scanner.
Matching uses auditable text evidence and conservative handling of requirements.

The application can observe an open-looking listing or an explicit closure.
A rolling feed omitting a previously seen item only establishes that it fell out of that window.
Publisher and official listing links remain available for checking ambiguous dates, eligibility, and status.

## Boundaries and verification

Coverage is limited to configured sources and the fields those publishers expose.
The CLI can search the full collected database.
The static dashboard summarizes records outside its detailed review set and has a separate catalog limit.
The first scan and a changed profile still process new records, and publisher response times affect scan duration.
Workday and SmartRecruiters listing text may fill in across several checks.
The supported sources and verification notes are recorded in [Source coverage](../SOURCE_COVERAGE.md).

Version 0.10.0 separates this checkout from older installed runtimes.
The baseline release gate passed 385 tests on revision 7fb1641.
