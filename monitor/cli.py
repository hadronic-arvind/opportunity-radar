"""Command-line interface for scans, dashboard generation, and local state."""

import argparse
import json
import platform
import re
import sqlite3
import sys
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from .config import (
    load_profile,
    load_source_packs,
    load_sources,
    profile_files,
    project_path,
    resolve_private_state_path,
    resolve_project_value,
)
from .dashboard import render_dashboard
from .database import Database
from .fetchers import fetch_source
from .onboarding import comma_values, default_pack_ids, initialize, interactive_values
from .pipeline import exclusive_lock, run_scan


OPPORTUNITY_ID = re.compile(r"^[a-f0-9]{24}$")


def _opportunity_id(value: str) -> str:
    if not OPPORTUNITY_ID.fullmatch(value):
        raise argparse.ArgumentTypeError("opportunity id must be 24 lowercase hexadecimal characters")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="opportunity-monitor")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan", help="Run due source checks and rebuild the dashboard")
    scan.add_argument("--force", action="store_true", help="Ignore per-source cadence")
    scan.add_argument("--notify", action="store_true", help="Send change-only notifications")
    scan.add_argument("--quiet", action="store_true", help="Suppress JSON output for scheduled runs")

    dashboard = subparsers.add_parser("dashboard", help="Rebuild the dashboard from the local database")
    dashboard.add_argument("--quiet", action="store_true", help="Suppress the output path")
    subparsers.add_parser("doctor", help="Check configuration and runtime prerequisites")
    sources = subparsers.add_parser("sources", help="Inspect source packs and health")
    source_commands = sources.add_subparsers(dest="source_command")
    source_commands.add_parser("list", help="List enabled sources")
    source_commands.add_parser("packs", help="List available source packs")
    source_commands.add_parser("health", help="Show persisted source health")
    source_test = source_commands.add_parser("test", help="Fetch one source without saving results")
    source_test.add_argument("source_id")

    initialize_parser = subparsers.add_parser(
        "init", help="Create ignored local source and matching configuration"
    )
    initialize_parser.add_argument(
        "--non-interactive", action="store_true", help="Use flags and defaults without prompts"
    )
    initialize_parser.add_argument(
        "--packs", default="", help="Comma-separated source pack ids"
    )
    initialize_parser.add_argument(
        "--include", default="", help="Comma-separated roles, skills, or domains to favor"
    )
    initialize_parser.add_argument(
        "--exclude", default="", help="Comma-separated terms to avoid"
    )
    initialize_parser.add_argument(
        "--locations", default="", help="Comma-separated preferred locations"
    )
    initialize_parser.add_argument(
        "--organizations", default="", help="Comma-separated preferred organizations"
    )
    initialize_parser.add_argument(
        "--default-document", default="General", help="Default resume or CV label"
    )
    initialize_parser.add_argument("--target", default="", help="Optional season or cycle label")
    initialize_parser.add_argument(
        "--force", action="store_true", help="Replace existing local configuration files"
    )

    status = subparsers.add_parser("status", help="Update application workflow status")
    status.add_argument("opportunity_id", type=_opportunity_id)
    status.add_argument("value", choices=["new", "reviewed", "apply", "applied", "skip"])
    status.add_argument("--quiet", action="store_true", help="Suppress confirmation output")

    bookmark = subparsers.add_parser("bookmark", help="Save or unsave an opportunity")
    bookmark.add_argument("opportunity_id", type=_opportunity_id)
    bookmark.add_argument("value", choices=["true", "false"])
    bookmark.add_argument("--quiet", action="store_true", help="Suppress confirmation output")
    return parser


def command_scan(force: bool, notify: bool, quiet: bool = False) -> int:
    result = run_scan(force=force, send_notifications=notify)
    if not quiet:
        print(json.dumps(result, indent=2))
    return 0 if result["status"] in {"ok", "partial"} else 1


def command_dashboard(quiet: bool = False) -> int:
    database_path = resolve_private_state_path(
        project_path("data", "opportunities.sqlite3"),
        "data",
        "opportunities.sqlite3",
    )
    with exclusive_lock(database_path.with_name("scan.lock")):
        database = Database(database_path)
        try:
            database.initialize()
            output = render_dashboard(database.dashboard_payload(), profile=load_profile())
        finally:
            database.close()
    if not quiet:
        print(output)
    return 0


def command_doctor() -> int:
    failures = []
    profile = load_profile()
    all_sources = load_sources(include_disabled=True)
    enabled_sources = [source for source in all_sources if source.get("enabled", True)]
    configured_curated = str(profile.get("curated_pipeline_path", "")).strip()
    curated_path = resolve_project_value(configured_curated) if configured_curated else None
    checks = {
        "project": "available",
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "sqlite": sqlite3.sqlite_version,
        "sources_enabled": len(enabled_sources),
        "sources_configured": len(all_sources),
        "profile_layers": [path.name for path in profile_files()],
        "dashboard_assets": all(
            project_path("dashboard", name).is_file()
            for name in ("template.html", "styles.css", "app.js")
        ),
        "curated_pipeline": (
            "not configured" if curated_path is None else "available" if curated_path.is_file() else "missing"
        ),
    }
    if sys.version_info < (3, 9):
        failures.append("Python 3.9 or newer is required")
    if not checks["dashboard_assets"]:
        failures.append("one or more dashboard assets are unavailable")
    if curated_path is not None and not curated_path.is_file():
        failures.append("curated_pipeline is unavailable: {}".format(curated_path.name))
    allowed_kinds = {"greenhouse", "lever", "jibe", "html_links", "watch_page"}
    pack_ids = {str(pack.get("id", "")) for pack in load_source_packs()}
    for source in all_sources:
        source_id = str(source.get("id", "source"))
        if not re.fullmatch(r"[a-z0-9]+(?:_[a-z0-9]+)*", source_id) or len(source_id) > 100:
            failures.append("{} has an invalid source id".format(source_id[:100]))
        if source.get("kind") not in allowed_kinds:
            failures.append("{} has an unsupported source kind".format(source_id))
        source_url = source.get("api_url") or source.get("url")
        parsed = urlparse(str(source_url or ""))
        if parsed.scheme != "https" or not parsed.netloc:
            failures.append("{} does not have an absolute HTTPS URL".format(source_id))
        try:
            cadence = int(source.get("cadence_hours", 12))
            if cadence < 1 or cadence > 24 * 31:
                raise ValueError
        except (TypeError, ValueError):
            failures.append("{} has an invalid cadence_hours".format(source_id))
        packs = source.get("packs", [])
        if not isinstance(packs, list) or any(str(pack) not in pack_ids for pack in packs):
            failures.append("{} references an invalid source pack".format(source_id))
        for field in ("domains", "opportunity_types", "career_levels", "regions"):
            if field in source and not isinstance(source[field], list):
                failures.append("{} has an invalid {} field".format(source_id, field))
    matching = profile.get("matching", {})
    rules = matching.get("rules", []) if isinstance(matching, dict) else []
    if not isinstance(rules, list):
        failures.append("matching.rules must be a list")
    else:
        for index, rule in enumerate(rules):
            if not isinstance(rule, dict) or not isinstance(rule.get("terms", []), list):
                failures.append("matching rule {} is invalid".format(index + 1))
    print(json.dumps({"ok": not failures, "checks": checks, "failures": failures}, indent=2))
    return 1 if failures else 0


def command_sources(action: Optional[str] = None, source_id: str = "") -> int:
    if action == "packs":
        sources = load_sources(include_disabled=True)
        for pack in load_source_packs():
            members = [
                source for source in sources
                if str(pack["id"]) in source.get("packs", [])
            ]
            supported = sum(
                source.get("support_level") == "supported" for source in members
            )
            marker = " default" if pack.get("default") else ""
            print(
                "{:<20} {:>3} sources, {:>2} feeds{}  {}".format(
                    pack["id"],
                    len(members),
                    supported,
                    marker,
                    pack.get("name", ""),
                )
            )
        return 0
    if action == "health":
        database_path = resolve_private_state_path(
            project_path("data", "opportunities.sqlite3"),
            "data",
            "opportunities.sqlite3",
        )
        if not database_path.is_file():
            print("No scan history yet.")
            return 0
        database = Database(database_path)
        try:
            database.initialize()
            rows = database.connection.execute(
                "SELECT id, name, last_status, item_count, last_checked_at "
                "FROM sources WHERE enabled=1 ORDER BY name"
            ).fetchall()
        finally:
            database.close()
        for row in rows:
            print("{:<24} {:<8} {:>5} items  {}".format(
                row["id"], row["last_status"], row["item_count"], row["last_checked_at"] or "never"
            ))
        return 0
    if action == "test":
        candidates = {
            str(source["id"]): source for source in load_sources(include_disabled=True)
        }
        if source_id not in candidates:
            print("Unknown source: {}".format(source_id), file=sys.stderr)
            return 2
        try:
            result = fetch_source(candidates[source_id])
        except Exception as error:
            print("Source check failed: {}: {}".format(type(error).__name__, error), file=sys.stderr)
            return 1
        print(json.dumps({"source": source_id, "status": result.status, "items": len(result.opportunities)}, indent=2))
        return 0
    for source in load_sources():
        print(
            "{:<24} {:<12} {:>3}h  {}".format(
                source["id"], source["kind"], source.get("cadence_hours", 12), source["name"]
            )
        )
    return 0


def command_init(args: argparse.Namespace) -> int:
    if not args.non_interactive and sys.stdin.isatty():
        values: Dict[str, Any] = interactive_values()
    else:
        values = {
            "pack_ids": comma_values(args.packs) or default_pack_ids(),
            "include_terms": comma_values(args.include),
            "exclude_terms": comma_values(args.exclude),
            "locations": comma_values(args.locations),
            "organizations": comma_values(args.organizations),
            "default_document": str(args.default_document).strip() or "General",
            "target": str(args.target).strip()[:120],
        }
    try:
        result = initialize(force=args.force, **values)
    except (FileExistsError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


def _command_workflow(opportunity_id: str, value: object, kind: str, quiet: bool) -> int:
    database_path = resolve_private_state_path(
        project_path("data", "opportunities.sqlite3"),
        "data",
        "opportunities.sqlite3",
    )
    with exclusive_lock(database_path.with_name("scan.lock")):
        database = Database(database_path)
        try:
            database.initialize()
            if kind == "status":
                changed = database.set_status(opportunity_id, str(value))
            else:
                changed = database.set_bookmarked(opportunity_id, bool(value))
            if changed:
                render_dashboard(database.dashboard_payload(), profile=load_profile())
        finally:
            database.close()
    if not quiet:
        print("updated" if changed else "opportunity not found")
    return 0 if changed else 1


def command_status(opportunity_id: str, value: str, quiet: bool = False) -> int:
    return _command_workflow(opportunity_id, value, "status", quiet)


def command_bookmark(opportunity_id: str, value: str, quiet: bool = False) -> int:
    return _command_workflow(opportunity_id, value == "true", "bookmark", quiet)


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "scan":
        return command_scan(args.force, args.notify, args.quiet)
    if args.command == "dashboard":
        return command_dashboard(args.quiet)
    if args.command == "doctor":
        return command_doctor()
    if args.command == "sources":
        return command_sources(args.source_command, getattr(args, "source_id", ""))
    if args.command == "init":
        return command_init(args)
    if args.command == "status":
        return command_status(args.opportunity_id, args.value, args.quiet)
    if args.command == "bookmark":
        return command_bookmark(args.opportunity_id, args.value, args.quiet)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
