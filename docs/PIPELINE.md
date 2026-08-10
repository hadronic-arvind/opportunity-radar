# Pipeline design

## Run lifecycle

1. A manual command or operating-system schedule starts one Python process.
2. The process obtains the shared scan and workflow lock.
3. It registers the effective public and local source configuration.
4. It imports an optional private curated Markdown seed.
5. It fetches enabled sources whose individual cadence is due.
6. It validates and normalizes each successful response.
7. It applies local matching and document-routing rules.
8. It updates SQLite while preserving first-seen and application state.
9. It generates one private self-contained dashboard atomically.
10. It sends change-only notifications when configured, closes every handle, and exits.

There is no resident scanner, polling loop, filesystem watcher, browser automation, model inference, web server, or database server.

## Collection

Structured Greenhouse, Lever, and Jibe adapters use official public listing endpoints.
They normalize job type from ATS fields and title evidence instead of labeling every record an internship.

The HTML-link adapter is available for conservative official-page extraction.
The watch-page adapter hashes visible semantic text while ignoring scripts, styles, SVG content, and similar noise.
A watch page publishes no active listing unless an explicit trusted local override enables `publish_as_opportunity`.

Every source runs inside its own failure boundary.
Collection rejects non-HTTPS targets, credentials in URLs, nonstandard ports, and hosts that resolve to non-public addresses.
The same checks run before every redirect and against the final response URL.
Timeouts, denied requests, oversized responses, malformed data, and parser failures affect only that source.
A failed fetch never deactivates its prior listings.
A validated empty structured feed is a successful result.
Each response is capped at 8 MiB, each source is capped at 5,000 normalized records, and paginated Jibe collection has a 90-second aggregate deadline.
Normalized text, URLs, metadata values, macOS notification arguments, and in-memory notification lists all have explicit limits.

The public catalog groups sources into overlapping packs.
A source selected by several packs is still registered and fetched once.
Only a small structured starter pack is enabled before onboarding.

## Matching

Fit is calculated only from the user's profile.
Source identity, fetch health, deadline, and recency do not add fit points.
Source metadata cannot force a score or tier.

Each matching component records its points and matched evidence in opportunity metadata.
The dashboard shows concise human-readable reasons and warnings.
The score is deterministic triage rather than an acceptance estimate.

Document routing uses the same boundary-aware phrase matcher.
The configured route order resolves equal matches, and the configured default handles no match.

## Persistence

`source_id` plus `external_id` is unique.
The public workflow identifier is a stable 24-character lowercase hexadecimal hash.
`first_seen_at` never changes after insertion.
`last_seen_at` changes when a source observes the record again.

Source refreshes never overwrite `status`, `status_updated_at`, `applied_at`, or `bookmarked`.
Inactive opportunities remain available when their status is `apply` or `applied`.
Disabled-source history remains in SQLite without appearing in Discover.
Inactive discovery records older than 365 days are pruned unless they are saved, planned, or applied.
Run history retains the most recent 200 records, and source-change history retains the most recent 500 events.

All scan, status, bookmark, and dashboard writes share one file lock.
SQLite uses parameter binding and a bounded busy timeout.
Database and dashboard files use owner-only permissions.

## Dashboard

The renderer combines a tracked HTML shell, stylesheet, JavaScript application, and sanitized data into one mode-`0600` local file.
It creates a fresh CSP nonce for each render and does not permit `unsafe-inline`, network connections, frames, remote media, or remote assets.

Untrusted values are placed into the document with DOM text nodes.
JSON escapes opening angle brackets so employer text cannot terminate the data block.
Only absolute HTTP or HTTPS listing URLs survive rendering.

The browser renders the first 36 matching records, loads additional batches near the viewport, and uses `content-visibility` for efficient revisits.
Searches and view changes reset the rendered batch without refetching data.
The generated dashboard includes at most the 5,000 highest-fit active discovery records while always preserving every planned or applied record.
When that safety limit is reached, the result summary says so explicitly.

In a regular browser, workflow state has a bounded local-storage fallback and scan controls show the corresponding CLI command.
In the optional macOS app, SQLite is authoritative.

## Native macOS boundary

The optional app embeds the exact generated dashboard in a nonpersistent `WKWebView` using `loadFileURL`.
It opens no listening socket and exposes no runtime directory over HTTP.

The native bridge accepts only main-frame messages from the exact trusted dashboard file.
It validates an exact key set, protocol version, action allowlist, workflow status, boolean types, theme values, and opportunity identifiers.
It invokes fixed Python argument arrays without a shell and supplies a minimal environment.

All HTTP or HTTPS listing links open in the default browser.
Other navigation is blocked.
Closing the last app window exits the app, and no app helper stays resident.

## Extension rules

Prefer a documented first-party feed over page extraction.
Keep manual and experimental sources disabled by default.
Use structured taxonomy arrays instead of personal score hints in source entries.
Never use organization identity as a proxy for attainability.

Add representative normalization tests for adapters and source-specific assumptions.
Run the full check gate and a deliberate live test before changing a supported source.
