# Opportunity Radar

Opportunity Radar is a local workspace for discovering opportunities and deciding which ones to pursue.
Explore public jobs, research placements, fellowships, training, competitions and funding, then build a shortlist backed by source evidence.
The desktop and terminal share one private SQLite workspace.

## Start

Clone the repository and run these commands from its root.
Use Python 3.9 or newer with SQLite FTS5 and JSON support.
The app has no Python package dependencies.

```sh
git clone https://github.com/hadronic-arvind/opportunity-radar.git
cd opportunity-radar
python3 -m radar init
python3 extras/radar-desktop/install.py build
./scripts/open_radar_desktop.sh
```

The macOS dashboard requires Apple Command Line Tools to build the local app bundle.
It opens directly to For You, with separate views for Explore, Saved, Applications, and Tools.
The app uses the current checkout, so rebuild the bundle if you move the checkout or change Python installations.
To point an existing Opportunity Radar Desktop shortcut at the new bundle, run `python3 extras/radar-desktop/install.py link` after building.

The classic desktop workspace is available on macOS and Linux via `./scripts/open_radar.sh` or `python3 -m radar gui` with Python and Tk 8.6 or newer.
Set `RADAR_PYTHON` if that launcher needs a specific Python interpreter.
The terminal and collector are supported on macOS and Linux.
Use `python3 -m radar --help` for all commands.

The workspace defaults to `~/.opportunity-radar/workspace.sqlite3`.
Set `RADAR_WORKSPACE` or pass `--workspace PATH` before the command to use a separate workspace.
Use an empty workspace for an offline demo, an upgrade rehearsal, or development.

## Find and decide

On an empty desktop dashboard, choose **Try the sample**, or run the following offline journey.

```sh
python3 -m radar sample
python3 -m radar search research
python3 -m radar inspect LISTING_ID
python3 -m radar compare FIRST_ID SECOND_ID
python3 -m radar track LISTING_ID --save --status apply
python3 -m radar search --scope tracked
```

The examples are fictional.
Use `python3 -m radar feed` to see the same For You page in the terminal.
Save opportunities to compare them later, or hide them and restore them from Saved → View hidden.
You can explore everything without a profile, or create named goals to see recommendations.
A goal can hold an objective and separate preferred, required, avoided, and exploratory listing phrases.
The first preferred phrase carries extra weight.
A required phrase needs observed evidence for the recommendation and does not make an eligibility decision.
Goal edits can be undone independently of tracking.

```sh
printf '%s\n' '{"objective":"Work on numerical methods","prefer":["scientific computing","research"],"explore":["fellowship"]}' > goal.json
python3 -m radar set-goal Methods goal.json
python3 -m radar search --scope recommended --goal Methods
python3 -m radar undo-goal Methods
```

Keep files containing private goals outside the repository.
Pass `--json` before a command for structured output.
Use `export` for a spreadsheet-safe CSV page.
Use `import FILE` to add a local JSON array of listing objects with `external_id` and `title`, plus optional `description`, `organization`, `location`, `kinds`, `url`, `posted_at`, `deadline_at`, `requirements` and `cycle`.
Explicit ISO dates and a stable publisher ID improve cycle tracking.
Omit a URL when the listing has no public HTTPS link.

A listing's evidence view separates duties, outcomes, benefits, obligations, qualifications, and application language.
Quotes retain wording about exceptions, conditions, and negation.
Fit scores are local ordering aids.
Eligibility and availability can remain unknown, including when a feed lists a role without its application rules.
See the publisher before acting on a deadline or eligibility requirement.

## Collect public listings

```sh
python3 -m radar catalog
python3 -m radar add-source --catalog-id nrc_canada_postdoctoral
python3 -m radar collect --seconds 15
python3 -m radar receipts
```

Collection is opt-in and checks at most three enabled sources per default run.
Use `add-source --file source.json` or **Sources** to add a supported public HTTPS feed.
For example, an RSS feed uses `{"id":"my_feed","name":"My feed","kind":"syndication","url":"https://example.org/feed.xml","enabled":true}`.
Select an actual public feed URL before collecting.
The catalog is a bounded starting set across several sectors, not a measure of all opportunities on the web.
The coverage ledger distinguishes complete, partial, failed and canceled checks.
An omitted record stays searchable and retains its tracking state.

Use Ctrl-C or **Cancel collection** to end a burst.
Completed observations are retained when the collector can return them.
You can schedule the same one-shot command using your operating system after trying it manually.
Specify an absolute Python path, the repository working directory, and the desired `RADAR_WORKSPACE`.
The app starts no schedule by default and runs no idle scanner.

## Back up, upgrade and recover

Use a new workspace for the upgrade.
Leave your previous installation and its backups available until you have verified the import.

1. Use the legacy checkout's `scripts/copy_database.py LEGACY_DB SNAPSHOT` to create a standalone SQLite snapshot.
   Keep the snapshot and a copy of the legacy local configuration in a private directory.
2. Run `python3 -m radar --workspace NEW_DB migrate SNAPSHOT --config-dir COPIED_CONFIG_DIR`.
   The optional config directory is the directory containing the legacy `profile.local.json`, `profiles.local.json`, and `sources.local.json`.
3. Compare tracked results and goal names, then run `python3 -m radar --workspace NEW_DB doctor`.
   Set `RADAR_WORKSPACE=NEW_DB` in your launcher or shell once satisfied.
4. Save an owner-only backup with `python3 -m radar --workspace NEW_DB backup BACKUP_FILE`.
   Restore a validated snapshot with `python3 -m radar --workspace NEW_DB restore BACKUP_FILE`.
   You can restore into another empty workspace first to rehearse offline.

The importer preserves listing IDs, tracking, recorded dates and named profile data.
It converts compatible rules into goals and retains unsupported configuration privately in the workspace archive.
Unsupported old feeds remain viewable as historical sources.
The old command `python3 -m monitor` and its runtime remain available for legacy recovery.

## Verification

See the [design, audit, and acceptance record](docs/REBUILD.md) for tested environments, release gates, measured results and coverage gaps.
Run `./scripts/check.sh` for the offline suite and `python3 -m scripts.benchmark_radar` for the disposable workload benchmark.
Run `python3 -m radar.collection --smoke` only when you want to make bounded requests to public catalog sources.
