# For You dashboard

The previous desktop opens a spreadsheet-style catalog and a large blank inspection pane.
Useful recommendations require navigating away from the initial view and choosing a goal lens.
The redesign opens a finite feed of recommended opportunities, with a plainly labeled recent view when no recommendation is available.
Save and hide are available where a decision happens, while evidence, application tracking and feed controls open on demand.
Explore, saved opportunities and advanced operations remain separately reachable.
No viewing behavior is used as a recommendation signal.

The design review used current [Apple sidebar guidance](https://developer.apple.com/design/human-interface-guidelines/sidebars?changes=_11) and [TikTok feed controls](https://newsroom.tiktok.com/more-ways-to-discover-new-content-and-creators-you-love?lang=en).
The feed uses explicit preferences, visible reasons and reversible feedback, and ends each page with a deliberate next action.
The macOS shell renders local assets through WebKit and sends local operations to the existing Python workspace.
The terminal remains available, and the Tk workspace is retained for other platforms and detailed operations during the transition.

Verification starts with the existing desktop launch and checks the new landing view, sparse and no-goal states, recommendations, save and hide with undo, source evidence, navigation, resize, keyboard control, and the existing Desktop icon.
Tests use disposable data until the new bundle is ready.


## Verification and deployment

The dashboard defaults to the active migrated goal when available.
The ranking evaluates a bounded lexical candidate set, diversifies the first results by organization, and excludes explicitly inactive opportunities.
When that set has no eligible matches, the interface labels the recent fallback.
The direct search view still reaches the local index.
Feed operations are also available via `python3 -m radar feed --help`, and tracking stays in the same SQLite workspace.

The review exercised the dashboard in the native window at 1180×780 and 680×550, and in a rendered browser at 1240 and 510 pixels wide.
A short-window pass reduced the header footprint so decisions stay close to the first result.
Independent interactions covered keyboard search, details, saving, hiding and restoring, goal controls, comparison, and Tools.
The 70,933-listing workspace was timed read-only, with about two seconds for its first personalized page and about one hundredth of a second for a representative direct query.
Ranking quality and market coverage remain bounded by the matching engine and collected sources.

Run `./scripts/dev_check.sh` and `./scripts/check.sh` for lint and regression coverage.
Build the native macOS bundle with `python3 extras/radar-desktop/install.py build`, then launch it with `./scripts/open_radar_desktop.sh`.
The installer places the signed bundle under Applications, outside the Desktop checkout where Finder metadata can invalidate a newly signed bundle.
The dashboard and its Python code are included in the signed bundle so Finder launch needs no access to the checkout.
The optional `link` action retargets an existing owned Radar shortcut and refuses unrelated targets.
