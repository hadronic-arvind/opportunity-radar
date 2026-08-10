# Security policy

## Supported version

Security fixes are applied to the current `main` branch.
The project has not yet declared additional maintained release branches.

## Report a vulnerability

Use GitHub's private vulnerability reporting or private security-advisory flow for this repository when it is available.
Do not place credentials, signed webhook URLs, private job-search data, or exploit details in a public issue.
If a private report option is unavailable, open a minimal public issue asking the maintainer to establish a private channel and omit the sensitive details.

## Runtime boundary

The scheduled runtime uses Python's standard library and SQLite.
It opens no listening port and keeps no resident process.
It performs read-only HTTPS requests to configured public sources and never automates applications or authenticated employer sessions.

External response bodies are capped at 8 MiB before parsing.
Each source is capped at 5,000 normalized records, paginated Jibe collection has a 90-second aggregate deadline, and normalized fields have explicit length limits.
The rendered dashboard caps active discovery data at 5,000 records while retaining every planned or applied record.
Collection URLs cannot contain credentials or nonstandard ports.
Each hostname must resolve only to public addresses, the connection is pinned to a validated numeric address while retaining hostname TLS verification, and redirects must remain on the configured host.
The collector ignores ambient proxy settings so scheduled requests cannot silently cross a different transport boundary.
Dashboard opportunity URLs are restricted to absolute HTTP and HTTPS links.
Generated dashboards are written atomically with mode `0600`.
SQLite files are also mode `0600`.
macOS notification content is passed as process arguments rather than interpolated into AppleScript source.
Notification arguments and the number of retained change items are bounded before invoking operating-system tools.
Webhook endpoints must be absolute HTTPS URLs and are loaded from macOS Keychain by the scheduled script.

## Trust model

Configuration files are trusted local input.
Do not install a profile, source registry, or curated Markdown seed from an untrusted party without reviewing it.
Do not run the project from a repository whose code you have not reviewed.

The monitor treats employer content as untrusted data.
It normalizes text, bounds storage, inserts rendered values through DOM text nodes, restricts action URL schemes, and escapes opening angle brackets inside embedded JSON.

The generated dashboard uses a per-render content-security-policy nonce and permits no network connection, frame, remote script, remote style, or remote media.
It opens directly from an owner-only local file and is never served from the runtime directory.

The optional macOS app uses a nonpersistent `WKWebView` and no HTTP listener.
Its native message bridge accepts only exact-schema, main-frame actions from the exact trusted dashboard file.
The bridge invokes fixed Python argument arrays with a minimal environment and never invokes a shell.
External listing navigation is limited to HTTP and HTTPS and opens in the default browser.

## Publication boundary

The following files must never be committed:

- Local profile and source overrides.
- Generated dashboards.
- SQLite databases and lock files.
- Curated private seeds.
- Logs and reports.
- Rendered launch-agent files.
- Project agent memory.

Stage the intended publication set, then run `./scripts/privacy_check.py` before publishing.
The gate reads bytes from the Git index, rejects private paths and unallowlisted binary blobs, and checks reachable Git history for tailored public configuration.
The CI workflow enforces the same checks against the committed tree and history available to the runner.
