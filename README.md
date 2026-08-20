# Opportunity Radar

<p align="center"><img src="assets/opportunity-radar-icon-v2.png" width="144" alt="Opportunity Radar clock and radar icon"></p>

[![CI](https://github.com/hadronic-arvind/opportunity-radar/actions/workflows/ci.yml/badge.svg)](https://github.com/hadronic-arvind/opportunity-radar/actions/workflows/ci.yml)
[![License: Proprietary](https://img.shields.io/badge/license-proprietary-red.svg)](LICENSE)

Opportunity Radar collects opportunities from official sources, compares them with your preferences, and keeps your search in a private local dashboard.

It runs in short bursts and exits after each scan.
Nothing polls in the background, and no local web server is opened.
The setup and operation instructions below are for the copyright holder and anyone who has received prior written permission under the [proprietary license](LICENSE).

## Quick start

Requirements are Python 3.9 or newer on macOS or another Unix-like system.
The scanner has no third-party Python dependencies.

```bash
git clone https://github.com/hadronic-arvind/opportunity-radar.git
cd opportunity-radar
python3 -m monitor init
./scripts/run_now.sh
./scripts/open_dashboard.sh
```

`init` creates private, ignored configuration files and lets you choose source packs, preferred work, locations, organizations, and a default resume or CV label.
Skip it to use the neutral starter profile and five diverse structured feeds.

## Choose an interface

The CLI works immediately and installs no app.

```bash
./scripts/open_dashboard.sh
python3 -m monitor scan
```

On macOS, the native app is optional.
It opens the dashboard directly and enables profile editing, Refresh, Scan all, Save, and application-status controls.

```bash
./scripts/install_launch_agent.sh
python3 extras/macos-app/install.py
```

The schedule and app are independent, so you can omit either one.

Add `--desktop-shortcut` only if you want an icon on the Desktop.

```bash
python3 extras/macos-app/install.py --desktop-shortcut
```

Cloning the repository never creates an app or Desktop icon by itself.

## Daily use

The macOS scheduler checks due sources at 07:30 and 16:30 local time, then exits.
Open Opportunity Radar whenever you want to review the latest dashboard.
Use Refresh for due sources or Scan all when you deliberately want to ignore source cadences.

Without the app, use `python3 -m monitor scan` for a due-only refresh or `./scripts/run_now.sh` for a full scan.
`./scripts/open_dashboard.sh` opens immediately and does not wait for the network.

The Discover view supports field-aware search, sorting, filters, saved items, a compact system/light/dark selector, and 24-item pages.
In the native app, Plan application and Mark applied persist the workflow in SQLite.
In a regular browser, the same controls use bounded local browser storage because a static file cannot safely write to SQLite.

Your profile can be changed at any time from the native app or the CLI.

```bash
python3 -m monitor profile show
python3 -m monitor profile set \
  --timeframe "Summer 2028" \
  --include "scientific computing,machine learning" \
  --opportunity-types "internship,research_program"
python3 -m monitor profile validate
```

Profile changes immediately rescore the existing dashboard without a network request.
Every later due or forced scan reads the latest saved profile.
If a scan is already running, the app keeps the profile editor available and applies one saved update immediately afterward, so the active scan finishes with its original snapshot and the next scan uses the new profile.
The app keeps everyday profile choices on the Basics page and scoring, matching rules, and document routing on Advanced.
Removing a basic role or domain retires obsolete positive advanced rules tied only to the removed target, while retained cross-domain, negative, qualification, and hard-gate rules stay intact.

Fit scores are deterministic triage from your rules.
They are not acceptance probabilities.

## Sources and customization

The public catalog contains more than 100 official resources across software, data, engineering, design, product, cybersecurity, biotech and health, climate and energy, public-interest work, academia, fellowships, quantitative finance, AI, skilled technical work, national laboratories, national security, early-career programs, aerospace, robotics, education, and social impact.
The five-source starter stays small, while the broader packs remain opt-in.

```bash
python3 -m monitor sources packs
python3 -m monitor sources list
python3 -m monitor sources health
python3 -m monitor sources test figma_greenhouse
python3 -m monitor sources add \
  --name "Example Robotics" \
  --url "https://jobs.ashbyhq.com/example"
```

Hosted Greenhouse, Lever, and Ashby URLs are recognized automatically.
Other careers pages use a bounded experimental link extractor unless you explicitly choose a change-only watch page.
New sources are live-tested before saving by default and are written atomically to the private registry.
Use `sources show`, `enable`, `disable`, and `remove` to manage them later.

The CLI can also search, inspect, and export stored opportunities without opening the dashboard.

```bash
python3 -m monitor opportunities list --min-score 65
python3 -m monitor opportunities search "robotics internship" --json
python3 -m monitor opportunities list --status apply --csv
python3 -m monitor opportunities show OPPORTUNITY_ID
```

The app and CLI write personal settings to one canonical private location.
Before scheduler installation that location is the clone, and afterward it is the installed private runtime.
These ignored files hold the data:

- `config/profile.local.json` for matching, organizations, labels, and document routing.
- `config/sources.local.json` for enabled packs, source overrides, and private additions.

See [Configuration](docs/CONFIGURATION.md) for the schema and examples.
Official source names in the dashboard are clickable, including manual resources that do not expose a structured listing feed.

## Privacy and permissions

Opportunity Radar performs read-only requests to configured public HTTPS sources.
It never signs in, applies, fills a form, or sends application material.

The generated dashboard, SQLite database, local configuration, logs, seeds, and scheduler files are excluded from Git and use owner-only permissions.
The dashboard is a self-contained local file with a strict content-security policy and no remote scripts or assets.
The native app exposes only a validated local action bridge and opens listing links in the default browser.

The scheduler stores its private runtime under `~/Library/Application Support/OpportunityRadar`.
The optional app is installed under `~/Applications`.
Desktop access is needed only when you explicitly request a Desktop shortcut or keep the clone there.

Read [Security](SECURITY.md) for the trust model and vulnerability reporting process.

## Maintenance

```bash
./scripts/doctor.sh
./scripts/check.sh
./scripts/uninstall_launch_agent.sh
```

Run the scheduler installer again after pulling code changes.
Profile and source-pack changes made through the app or CLI do not require reinstallation.
Uninstalling the scheduler leaves the database and dashboard intact.

Developer details are in [Pipeline design](docs/PIPELINE.md), [Operations](docs/OPERATIONS.md), and [Repository participation](CONTRIBUTING.md).

Opportunity Radar is publicly viewable but proprietary software.
All rights are reserved under the [license and copyright notice](LICENSE).
