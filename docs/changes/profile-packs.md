# Portable profile packs

Radar accepts version 1 JSON packs containing named profiles and an optional active profile.
The native dashboard reviews normalized rules, overwrites, and removals before applying a pack.
The review is tied to the input and workspace state, so a changed file or workspace requires a new review.
The authoring prompt lets users choose the source files they share with an AI assistant.
Profile import and review run locally.

Workspace schema v2 stores the active profile in SQLite so imports, selection changes, and backups share the same state.
Existing v1 databases upgrade in place, and validated v1 backups can be restored.
Existing file-based dashboard settings remain readable until the workspace stores an explicit selection.
Profile replacement records prior rules in goal history and leaves listing tracking in place.

New default workspaces and explicit `init` start with six bundled example profiles and Recently collected selected.
Existing workspaces, restores, and implicit custom workspace creation preserve their original behavior.
Legacy migration can replace an otherwise empty workspace's unedited and inactive starter set.
Use `init --empty` to opt out when creating a workspace.

## Verification

- The [example pack](../../examples/profile-packs/README.md) includes student and professional profiles in 14 domains.
- All 28 isolated workspaces passed feed and tracking checks against 61 fictional listings, including recommendations for both career stages, negative examples, evidence, comparison, filters, pagination, save, hide, undo, and application status.
- Packaged WebKit checks covered all 28 independent workspaces at alternating compact and standard widths, and verified recommendation order, card explanations, details, saved state, and horizontal overflow.
- Native macOS checks covered JSON file selection, import preview, personalized recommendations, invalid draft feedback, evidence disclosure, save, hide and undo, Explore, and both import entry points.
- `./scripts/dev_check.sh` and `./scripts/check.sh` passed, including 468 tests with the Tk desktop journey skipped because Tk 8.6+ was unavailable.
- The rebuilt macOS bundle passed property-list and code-signature checks.
- Deployment was rehearsed on a snapshot and then verified in the installed app.
