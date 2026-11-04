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
The signed bundle contains its dashboard and local workspace code.
Rebuild it after updating the checkout or changing Python installations.
To point an existing Opportunity Radar Desktop shortcut at the new bundle, run `python3 extras/radar-desktop/install.py link` after building.

The classic desktop workspace is available on macOS and Linux via `./scripts/open_radar.sh` or `python3 -m radar gui` with Python and Tk 8.6 or newer.
Set `RADAR_PYTHON` if that launcher needs a specific Python interpreter.
The terminal and collector are supported on macOS and Linux.
Use `python3 -m radar --help` for all commands.

The workspace defaults to `~/.opportunity-radar/workspace.sqlite3`.
A new default workspace comes with six example profiles for medicine, AI and ML research, and clean energy, with student and professional versions in each field.
Choose one under **For You → Tune feed**, or keep **Recently collected** selected while exploring.
Existing workspaces keep their own profiles.
Set `RADAR_WORKSPACE` or pass `--workspace PATH` before the command to use a separate workspace.
Run `python3 -m radar --workspace PATH init` to create a separate workspace with the starter profiles.
Use `init --empty` to create a blank workspace for an upgrade rehearsal or development.

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

### Import profiles from AI-generated JSON

In the desktop, choose **Import profiles**, then paste JSON or open a local JSON file and preview the import.
The dialog includes a prompt you can use with an AI assistant and the files you choose to share with it.
Opportunity Radar reads the finished JSON locally.
For example, a research profile pack could look like this:

```json
{
  "version": 1,
  "profiles": [
    {
      "name": "Research methods",
      "rules": {
        "objective": "Find research roles that develop numerical methods",
        "prefer": ["scientific computing", "numerical methods"],
        "require": [],
        "avoid": ["sales targets"],
        "explore": ["research fellowship"]
      }
    }
  ],
  "active_profile": "Research methods"
}
```

The pack can contain 1 to 100 profiles in up to 256 KiB of UTF-8 JSON.
Use unique profile names up to 100 characters.
Keep the objective at 2,000 characters or fewer and each rule list to at most 30 phrases of up to 120 characters.
Optional `facts` maps up to 50 self-reported eligibility facts to boolean values.
Set `active_profile` to a name from the pack, set it to `null` for the recent feed, or omit it to retain the current dashboard preference where possible.

```sh
python3 -m radar import-profiles profiles.json --preview
python3 -m radar import-profiles profiles.json
```

Import updates profiles with matching names and adds new ones.
Select **Remove existing profiles outside this file or draft**, or use `--replace`, to remove profiles absent from the pack.
Profile imports leave the collection and tracking decisions intact.

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

## Search by place and plan applications

Use **Location** in For You or Explore for a temporary filter.
Choose a country, region, or qualified city from the offline picker.
A country or region matches recognized locations inside it.
Choose work arrangements separately.
A remote listing with a stated geographic scope can match places inside that scope.
A listing labeled only Remote has an unknown geographic scope.
The picker can include unknown records when you want to review them manually.
These are listing evidence rules, and employers may have separate authorization or residency requirements.

Use **Tune feed → Edit an existing goal → Location preferences** to prioritize or require locations for a profile.
The app keeps the publisher's original text for verification.
The catalog is finite, so ambiguous or unrecognized place names remain unresolved.
Existing profiles gain location preferences only when edited or imported with them.

A profile pack can add this optional object under `rules`:

```json
{
  "location": {
    "places": ["country:CA", "city:US:MD:baltimore"],
    "modes": ["hybrid", "onsite"],
    "policy": "prefer",
    "include_unknown": false
  }
}
```

Use up to 12 places.
Unambiguous qualified names are also accepted.
A listing can match any selected place and any selected work arrangement on the same publisher observation.

```sh
python3 -m radar locations "Portland"
python3 -m radar feed --view explore --place "Maryland, United States"
python3 -m radar search --work-mode remote --place Canada
```

Use **Explore → Saved searches** to save the current keywords, opportunity type, and location filter.
**Check what’s new** shows matches collected since that search was saved or marked reviewed.
**Mark batch reviewed** advances only through the displayed batch's collection snapshot.
Use **Capture opportunity** to add a listing and its source link to Saved from outside your configured feeds.

Use **Applications → Deadline agenda** for due dates and follow-ups from Saved and Applications.
Use **Plan the application** in a listing to write a next action, set its follow-up date, and check off application materials.
Export an `.ics` snapshot to add dated tasks to your calendar.
Publisher deadlines with conflicting evidence stay visible for review.
The calendar export excludes private plan text.

A listing also includes **What changed**, **Related opportunities**, and **Pay, costs and support**.
Changes compare successive captures of one source record.
Related items show shared terms in your local collection.
Pay, cost and support evidence preserves the source wording and flags conditional language.
