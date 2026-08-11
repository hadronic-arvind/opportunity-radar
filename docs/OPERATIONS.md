# Operations guide

## Everyday use

Open the optional macOS app or run:

```bash
./scripts/open_dashboard.sh
```

Both paths show the latest dashboard immediately.
Neither waits for a network scan before first paint.

The native dashboard buttons can check due sources or force all enabled sources.
The native profile editor updates keywords, targets, timeframes, source packs, preferred organizations, score visibility, and document routes without opening a terminal.
In a normal browser, run one of these terminal commands:

```bash
python3 -m monitor scan
./scripts/run_now.sh
```

The first command respects source cadences.
The second forces every enabled source and omits notifications.

Use the CLI for the same profile workflow when the app is not installed:

```bash
python3 -m monitor profile show
python3 -m monitor profile set --include "research software,physics" --timeframe "Summer 2028"
python3 -m monitor profile validate
```

A successful profile change immediately rescores the local database and rebuilds the dashboard.
The next scheduled or forced scan loads that saved revision before collecting sources.

## macOS schedule

Install the twice-daily schedule once:

```bash
./scripts/install_launch_agent.sh
```

The installer validates paths and configuration before mutation, creates a private staged runtime, copies the database with SQLite's backup API, verifies a full scan, swaps the runtime, and then loads the schedule.
A failed deployment attempts to preserve or restore the previous runtime and scheduler file.

The default times are 07:30 and 16:30 in the user's local timezone.
The launch agent uses background process type, low-priority I/O, and nice value 10.
It has no `KeepAlive` entry or short interval.

If the user's LaunchAgents directory is not writable, the installer uses a bounded managed crontab block.
Malformed managed markers fail closed and never remove unrelated crontab content.
If an exact managed cron schedule already exists and no launchd installation exists, upgrades preserve that working backend instead of forcing a protected crontab rewrite.

Run the installer again after pulling code changes.
Profile and source-pack edits made through the app or CLI use the canonical installed settings and do not require an upgrade.

## Optional app

Install the native app separately:

```bash
python3 extras/macos-app/install.py
```

The app goes to `~/Applications/Opportunity Radar.app` by default.
It can use the clone directly when the private scheduled runtime is not installed.
The installer refuses symlink destinations, repository-contained destinations, and replacement of an app with another bundle identifier.
It builds locally with Swift, creates the icon locally, applies owner-only permissions, ad-hoc signs the bundle, and verifies the signature.

Add a Desktop shortcut only by passing `--desktop-shortcut`.
The shortcut points to the signed app in `~/Applications`.

## Private runtime

The scheduled copy lives under `~/Library/Application Support/OpportunityRadar`.
This avoids giving an app permanent access to a clone stored under Desktop.

After installation, these project paths point into the private runtime:

- `dashboard/index.html`
- `data/opportunities.sqlite3`
- `logs/scheduler.out.log`
- `logs/scheduler.err.log`

The runtime, database, profile, source overrides, seed, dashboard, logs, and Python cache use private permissions.
The previous runtime remains beside the current runtime as a recovery copy after an upgrade.
Installer upgrades preserve the current runtime's local profile and source preferences before swapping the replaceable code and assets.

## Health and troubleshooting

```bash
./scripts/doctor.sh
python3 -m monitor sources health
python3 -m monitor sources test SOURCE_ID
```

The dashboard source panel shows the same current health at a glance.
The scheduler uses quiet mode, so normal success does not grow logs continuously.
Unexpected exceptions and scheduler diagnostics appear in the linked error log.

Inactive listings are retained for one year unless they are saved, planned, or applied.
The database also retains the latest 200 scan runs and 500 source-change events.

If the app cannot find a valid dashboard in a valid runtime, it generates an empty local dashboard once without accessing the network.
If the trusted runtime or Python path is invalid, reinstall the scheduler and then reinstall the app.

## Backups and recovery

Copy SQLite only while no scan is running or use SQLite's backup API.
The scheduler installer uses the backup API during upgrades.

A new scan can reconstruct current discovery data after a database loss.
It cannot reconstruct application state, bookmarks, or original first-seen timestamps.

## Disable automation

```bash
./scripts/uninstall_launch_agent.sh
```

The script validates its label and target, unloads the service, and moves the property list to Trash.
It leaves the runtime, database, dashboard, and optional app intact.

## Verification

```bash
./scripts/check.sh
./scripts/doctor.sh
```

On macOS, `launchctl print gui/$(id -u)/io.github.opportunity-radar.monitor` shows the installed schedule.
