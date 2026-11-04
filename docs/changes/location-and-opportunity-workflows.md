# Location and opportunity workflows

The app resolves publisher location text against the bundled offline gazetteer.
Canonical country, region, and city IDs form a hierarchy.
The database retains the original publisher text and indexes recognized place and work arrangement alternatives before pagination.
Unqualified ambiguous names stay unknown.
Geographic scope and work arrangement are independent, so a generic remote label has unknown geographic scope.
Profiles can prefer or require geography.
Explore and For You also support temporary filters.

Schema v3 adds derived location rows, an observation-history index, saved search checkpoints, and private application plans.
Existing workspaces backfill from stored observations in a transaction.
Restoring v1 and v2 snapshots also rebuilds the derived location rows.
No new background tasks or network services are involved.

The [research and feature decisions](../research/opportunity-workflow-2026-11-04.md) cover the ten additions.
The implementation groups them into search and capture, application planning, and listing evidence.
The location preferences, capture, discovery, planning, and evidence workflows share one local workspace and expose terminal commands alongside the macOS UI.

## Verification checkpoint

- Focused checks cover canonical geography, nested and ambiguous names, remote scope, index-before-pagination, v2 upgrade, and recovery.
- Discovery tests exercise manual capture, repeat capture, saved location filters, new arrivals and review snapshots through the terminal and desktop bridge.
- Planning tests cover private state, recorded and conflicting deadlines, follow-ups, checklists, and calendar interoperability.
- Evidence tests cover consecutive source observations, specific shared terms, and conditional and missing cost evidence.
- The v3 geography backfill completed in 1.9 seconds in a disposable copy of a roughly 71,000-listing workspace.
- Native macOS verification exercised country selection, captured and reviewed a new match, expanded the three evidence views, saved an application plan, and exported a calendar event through the system file picker.
- The final developer and release checks passed with 482 tests and one classic Tk environment skip.
- A v2 backup from the installed workspace restored successfully into a disposable workspace, indexed 70,948 listings, and returned confirmed Maryland matches.
