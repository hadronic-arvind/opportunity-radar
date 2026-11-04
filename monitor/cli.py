"""Command-line interface for scans, dashboard generation, and local state."""

import argparse
import csv
import json
import platform
import re
import sqlite3
import sys
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from .config import (
    automatic_dashboard_enabled,
    load_profile,
    load_source_packs,
    load_sources,
    profile_files,
    project_path,
    resolve_private_state_path,
    resolve_project_value,
)
from .coverage import AUTOMATIC_PRESET, coverage_preset_ids, target_defaults_for_preset
from .dashboard import render_dashboard
from .database import Database, SCHEMA_VERSION
from .fetchers import MAX_HTML_LINK_PAGES, _remote_url_parts, fetch_source
from .onboarding import comma_values, default_pack_ids, initialize, interactive_values
from .opportunity_facts import listing_availability, opportunity_facts
from .opportunity_search import listing_key, search_ranker
from .pipeline import ensure_profile_lifecycle_idle, exclusive_lock, run_scan
from .profile import (
    MAX_EDITOR_BYTES,
    ProfileValidationError,
    apply_editor_payload,
    apply_profile_management_payload,
    profile_catalog_payload,
    profile_editor_payload,
    read_editor_file,
    read_editor_json,
    resolve_profile_selector,
    validate_editor_payload,
)
from .scoring import MATCH_FIELDS
from .text import normalize_opportunity_type
from .source_registry import (
    SUPPORTED_KINDS,
    add_source,
    build_custom_source,
    remove_source,
    set_source_enabled,
    source_summary,
)


OPPORTUNITY_ID = re.compile(r"^[a-f0-9]{24}$")


def _opportunity_id(value: str) -> str:
    if not OPPORTUNITY_ID.fullmatch(value):
        raise argparse.ArgumentTypeError("opportunity id must be 24 lowercase hexadecimal characters")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="opportunity-radar",
        description="Opportunity Radar scans and tracks opportunities from your configured sources.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan", help="Run due source checks and rebuild the dashboard")
    scan.add_argument("--source", action="append", default=None, help="Check only an enabled source ID; repeat for multiple sources")
    scan.add_argument("--force", action="store_true", help="Ignore per-source cadence")
    scan.add_argument("--notify", action="store_true", help="Send change-only notifications")
    scan.add_argument("--max-sources", type=int, help="Maximum source checks in this burst")
    scan.add_argument("--max-seconds", type=float, help="Soft time budget checked between sources")
    scan.add_argument("--progress", action="store_true", help="Show progress on standard error")
    scan.add_argument("--quiet", action="store_true", help="Suppress JSON output for scheduled runs")

    dashboard = subparsers.add_parser("dashboard", help="Rebuild the dashboard from the local database")
    dashboard.add_argument("--quiet", action="store_true", help="Suppress the output path")
    subparsers.add_parser("doctor", help="Check configuration and runtime prerequisites")
    sources = subparsers.add_parser("sources", help="Inspect and manage opportunity sources")
    source_commands = sources.add_subparsers(dest="source_command")
    source_list = source_commands.add_parser("list", help="List configured sources")
    source_list.add_argument("--all", action="store_true", help="Include disabled sources")
    source_list.add_argument("--json", action="store_true", help="Print JSON")
    source_discover = source_commands.add_parser("discover", help="Search the local source directory")
    source_discover.add_argument("query", nargs="?", default="")
    source_discover.add_argument("--pack")
    source_discover.add_argument("--kind", choices=sorted(SUPPORTED_KINDS))
    source_discover.add_argument("--enabled", action="store_true")
    source_discover.add_argument("--limit", type=int, default=40)
    source_discover.add_argument("--json", action="store_true")
    source_commands.add_parser("packs", help="List available source packs")
    source_commands.add_parser("health", help="Show persisted source health")
    source_test = source_commands.add_parser("test", help="Fetch one source without saving results")
    source_test.add_argument("source_id")
    source_show = source_commands.add_parser("show", help="Show one source definition")
    source_show.add_argument("source_id")
    source_add = source_commands.add_parser("add", help="Add a private company or program source")
    source_add.add_argument("--name", required=True, help="Company or program name")
    source_add.add_argument("--url", required=True, help="Official HTTPS jobs or careers URL")
    source_add.add_argument("--id", default="", help="Optional lowercase source id")
    source_add.add_argument(
        "--kind",
        choices=["auto", *sorted(SUPPORTED_KINDS - {"jibe"})],
        default="auto",
        help="Provider type; auto recognizes hosted ATS URLs",
    )
    source_add.add_argument(
        "--adapter",
        default="",
        help="Provider board/site slug when it cannot be read from the URL",
    )
    source_add.add_argument("--opportunity-type", default="", help="Default kind for ambiguous source listings")
    source_add.add_argument("--packs", default="", help="Comma-separated source pack ids")
    source_add.add_argument("--cadence-hours", type=int, default=12)
    source_add.add_argument(
        "--skip-test",
        action="store_true",
        help="Save without a live read-only provider check",
    )
    source_add.add_argument("--dry-run", action="store_true", help="Validate without writing")
    for action, help_text in (
        ("enable", "Enable a built-in or private source"),
        ("disable", "Disable a built-in or private source"),
        ("remove", "Remove a private source"),
    ):
        command = source_commands.add_parser(action, help=help_text)
        command.add_argument("source_id")
        command.add_argument("--dry-run", action="store_true", help="Validate without writing")

    opportunities = subparsers.add_parser(
        "opportunities", help="Search and export opportunities from the local database"
    )
    opportunity_commands = opportunities.add_subparsers(dest="opportunity_command")
    for action, help_text in (
        ("list", "List opportunities"),
        ("search", "Search opportunity text"),
    ):
        command = opportunity_commands.add_parser(action, help=help_text)
        if action == "search":
            command.add_argument("query")
        else:
            command.add_argument("--query", default="")
        command.add_argument(
            "--status",
            choices=["new", "reviewed", "apply", "applied", "skip"],
        )
        command.add_argument("--type", dest="opportunity_type")
        command.add_argument("--source")
        command.add_argument("--organization", help="Filter by part of an organization name")
        command.add_argument("--location", help="Filter by part of a listed location")
        command.add_argument("--saved", action="store_true", help="Include only bookmarked opportunities, including archived ones")
        command.add_argument("--matched", action="store_true", help="Include only opportunities visible under your profile")
        command.add_argument("--available", action="store_true", help="Hide archives, directories, and known closed listings")
        command.add_argument("--unique", action="store_true", help="Group equivalent listings from multiple sources")
        command.add_argument(
            "--sort", choices=["relevance", "fit", "recent", "deadline"], default="relevance"
        )
        command.add_argument("--offset", type=int, default=0, help="Skip this many results")
        command.add_argument("--min-score", type=int, default=0)
        command.add_argument("--limit", type=int, default=50)
        command.add_argument("--include-inactive", action="store_true")
        output = command.add_mutually_exclusive_group()
        output.add_argument("--json", action="store_true")
        output.add_argument("--csv", action="store_true")
    opportunity_show = opportunity_commands.add_parser("show", help="Show one opportunity")
    opportunity_show.add_argument("opportunity_id", type=_opportunity_id)
    opportunity_show.add_argument("--json", action="store_true")

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
        "--coverage-preset",
        choices=coverage_preset_ids(),
        default=AUTOMATIC_PRESET,
        help="Automatic, manual, or field-specific source coverage",
    )
    initialize_parser.add_argument(
        "--include", default="", help="Comma-separated roles or interests to favor"
    )
    initialize_parser.add_argument("--domains", default="", help="Comma-separated fields or topics")
    initialize_parser.add_argument(
        "--opportunity-types", default="",
        help="Comma-separated listing kinds, such as job, fellowship, grant, or hackathon",
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
        "--default-document", default="General", help="Default application document label"
    )
    initialize_parser.add_argument("--target", default="", help="Optional season or cycle label")
    initialize_parser.add_argument(
        "--timeframe",
        action="append",
        default=None,
        help="Target season or cycle; repeat to select more than one",
    )
    initialize_parser.add_argument(
        "--force", action="store_true", help="Replace existing local configuration files"
    )

    profile_parser = subparsers.add_parser(
        "profile", help="Inspect, validate, and update private matching preferences"
    )
    profile_commands = profile_parser.add_subparsers(dest="profile_command")
    profile_show = profile_commands.add_parser("show", help="Show the editable profile")
    profile_show.add_argument("--json", action="store_true", help="Print the app-facing JSON object")
    profile_list = profile_commands.add_parser("list", help="List saved search profiles")
    profile_list.add_argument("--json", action="store_true", help="Print the app-facing catalog")
    profile_commands.add_parser(
        "template", help="Print the one-shot JSON profile format for people or AI models"
    )

    profile_import = profile_commands.add_parser(
        "import", help="Create and activate a saved profile from one-shot JSON"
    )
    import_input = profile_import.add_mutually_exclusive_group(required=True)
    import_input.add_argument("--stdin", action="store_true", help="Read one-shot JSON from stdin")
    import_input.add_argument("--file", type=Path, help="Read one-shot JSON from a file")
    profile_import.add_argument("--dry-run", action="store_true", help="Validate without writing")
    profile_import.add_argument("--quiet", action="store_true", help="Suppress confirmation output")

    for action, help_text in (
        ("create", "Create and activate a blank named profile"),
        ("duplicate", "Duplicate and activate a saved profile"),
        ("rename", "Rename a saved profile"),
        ("activate", "Use a saved profile for searches and scans"),
        ("delete", "Delete an inactive saved profile"),
    ):
        command = profile_commands.add_parser(action, help=help_text)
        if action in {"duplicate", "rename", "activate", "delete"}:
            command.add_argument("profile", help="Saved profile id or exact name")
        if action in {"create", "duplicate", "rename"}:
            command.add_argument("name", help="Saved profile name")
        command.add_argument("--dry-run", action="store_true", help="Validate without writing")
        command.add_argument("--quiet", action="store_true", help="Suppress confirmation output")

    profile_validate = profile_commands.add_parser(
        "validate", help="Validate the current profile or an editor JSON file"
    )
    validate_input = profile_validate.add_mutually_exclusive_group()
    validate_input.add_argument("--stdin", action="store_true", help="Read editor JSON from stdin")
    validate_input.add_argument("--file", type=Path, help="Read editor JSON from a file")

    profile_apply = profile_commands.add_parser(
        "apply", help="Validate and atomically apply an editor JSON object"
    )
    apply_input = profile_apply.add_mutually_exclusive_group(required=True)
    apply_input.add_argument("--stdin", action="store_true", help="Read editor JSON from stdin")
    apply_input.add_argument("--file", type=Path, help="Read editor JSON from a file")
    profile_apply.add_argument("--dry-run", action="store_true", help="Validate without writing")
    profile_apply.add_argument("--quiet", action="store_true", help="Suppress confirmation output")

    profile_set = profile_commands.add_parser(
        "set", help="Replace common preferences without editing JSON"
    )
    profile_set.add_argument("--include", help="Comma-separated desired roles, skills, or domains")
    profile_set.add_argument("--exclude", help="Comma-separated explicit exclusions")
    profile_set.add_argument("--locations", help="Comma-separated preferred locations")
    location_gate = profile_set.add_mutually_exclusive_group()
    location_gate.add_argument("--strict-locations", dest="strict_locations", action="store_true", help="Hide confidently resolved listings outside preferred locations")
    location_gate.add_argument("--no-strict-locations", dest="strict_locations", action="store_false", help="Use preferred locations for scoring only")
    profile_set.set_defaults(strict_locations=None)
    profile_set.add_argument("--organizations", help="Comma-separated preferred organizations")
    profile_set.add_argument(
        "--timeframe", action="append", default=None,
        help="Target season or cycle; repeat to select more than one",
    )
    profile_set.add_argument("--packs", help="Comma-separated source pack ids")
    profile_set.add_argument(
        "--coverage-preset",
        choices=coverage_preset_ids(),
        help="Automatic, manual, or field-specific source coverage",
    )
    profile_set.add_argument("--current-stage", help="Current education or career stage")
    profile_set.add_argument("--expected-graduation", help="Expected graduation month or year")
    profile_set.add_argument("--opportunity-types", help="Comma-separated target opportunity types")
    profile_set.add_argument("--role-families", help="Comma-separated target role families")
    profile_set.add_argument("--domains", help="Comma-separated target domains")
    profile_set.add_argument("--supporting-skills", help="Comma-separated supporting skills")
    profile_set.add_argument(
        "--remote-preference",
        choices=[
            "no_preference",
            "remote_preferred",
            "remote_required",
            "hybrid_preferred",
            "onsite_preferred",
        ],
        help="Preferred work-location mode",
    )
    profile_set.add_argument("--maximum-experience-years", type=int)
    profile_set.add_argument("--default-document", help="Default application document label")
    profile_set.add_argument("--base-score", type=int)
    profile_set.add_argument("--minimum-display-score", type=int)
    profile_set.add_argument("--priority-threshold", type=int)
    profile_set.add_argument("--strong-threshold", type=int)
    profile_set.add_argument("--watch-threshold", type=int)
    profile_set.add_argument("--dry-run", action="store_true", help="Validate without writing")
    profile_set.add_argument("--quiet", action="store_true", help="Suppress confirmation output")

    status = subparsers.add_parser("status", help="Update application workflow status")
    status.add_argument("opportunity_id", type=_opportunity_id)
    status.add_argument("value", choices=["new", "reviewed", "apply", "applied", "skip"])
    status.add_argument("--quiet", action="store_true", help="Suppress confirmation output")

    bookmark = subparsers.add_parser("bookmark", help="Save or unsave an opportunity")
    bookmark.add_argument("opportunity_id", type=_opportunity_id)
    bookmark.add_argument("value", choices=["true", "false"])
    bookmark.add_argument("--quiet", action="store_true", help="Suppress confirmation output")
    return parser


def _scan_progress(event: Dict[str, Any]) -> None:
    if event["event"] == "start":
        print(
            "{} sources due, up to {} selected for this run".format(
                event["due_sources"], event["total_sources"],
            ),
            file=sys.stderr, flush=True,
        )
    elif event["event"] == "source_start":
        print(
            "[{}/{}] {}".format(
                event["checked_sources"] + 1, event["total_sources"],
                event.get("source_name", event.get("source_id", "Source")),
            ),
            file=sys.stderr, flush=True,
        )
    elif event["event"] == "render":
        print("Rebuilding the local dashboard", file=sys.stderr, flush=True)
    elif event["event"] == "finish":
        print(
            "Checked {} sources, with {} still due".format(
                event["checked_sources"], event["remaining_due"]
            ),
            file=sys.stderr, flush=True,
        )


def command_scan(
    force: bool, notify: bool, quiet: bool = False,
    source_ids: Optional[List[str]] = None, *,
    max_sources: Optional[int] = None, max_seconds: Optional[float] = None,
    show_progress: bool = False,
) -> int:
    options = {}
    if max_sources is not None:
        options["max_sources"] = max_sources
    if max_seconds is not None:
        options["max_seconds"] = max_seconds
    if show_progress or (not quiet and sys.stderr.isatty()):
        options["progress"] = _scan_progress
    result = run_scan(force=force, send_notifications=notify, source_ids=source_ids, **options)
    if not quiet:
        print(json.dumps(result, indent=2))
    return 0 if result["status"] in {"ok", "partial"} else 1


def command_dashboard(quiet: bool = False) -> int:
    ensure_profile_lifecycle_idle()
    database_path = resolve_private_state_path(
        project_path("data", "opportunities.sqlite3"),
        "data",
        "opportunities.sqlite3",
    )
    with exclusive_lock(database_path.with_name("scan.lock")):
        ensure_profile_lifecycle_idle()
        profile = load_profile()
        database = Database(database_path)
        try:
            database.initialize()
            database.rescore_for_profile(profile)
            output = render_dashboard(database.dashboard_payload(), profile=profile)
        finally:
            database.close()
    if not quiet:
        print(output)
    return 0


def command_doctor() -> int:
    failures = []

    def bounded_integer(value: object, minimum: int, maximum: int) -> bool:
        return (
            isinstance(value, int)
            and not isinstance(value, bool)
            and minimum <= value <= maximum
        )

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
    try:
        resolve_private_state_path(
            project_path("data", "opportunities.sqlite3"),
            "data",
            "opportunities.sqlite3",
        )
    except (OSError, ValueError, RuntimeError) as error:
        failures.append(str(error))
    try:
        profile_editor_payload(profile)
    except (OSError, ValueError) as error:
        failures.append("profile is invalid: {}".format(error))
    if sys.version_info < (3, 9):
        failures.append("Python 3.9 or newer is required")
    if not checks["dashboard_assets"]:
        failures.append("one or more dashboard assets are unavailable")
    if curated_path is not None and not curated_path.is_file():
        failures.append("curated_pipeline is unavailable: {}".format(curated_path.name))
    allowed_kinds = set(SUPPORTED_KINDS)
    pack_ids = {str(pack.get("id", "")) for pack in load_source_packs()}
    for source in all_sources:
        source_id = str(source.get("id", "source"))
        kind = source.get("kind")
        if not re.fullmatch(r"[a-z0-9]+(?:_[a-z0-9]+)*", source_id) or len(source_id) > 100:
            failures.append("{} has an invalid source id".format(source_id[:100]))
        if not isinstance(source.get("name"), str) or not source["name"].strip():
            failures.append("{} does not have a non-empty name".format(source_id))
        if kind not in allowed_kinds:
            failures.append("{} has an unsupported source kind".format(source_id))
        adapter_key = {
            "ashby": "board",
            "greenhouse": "board",
            "lever": "site",
            "jibe": "api_url",
            "smartrecruiters": "company",
            "rippling": "board",
            "workable": "board",
        }.get(kind)
        if adapter_key:
            adapter_value = source.get(adapter_key)
            if not isinstance(adapter_value, str) or not adapter_value.strip():
                failures.append(
                    "{} requires a non-empty {}".format(source_id, adapter_key)
                )
            elif adapter_key in {"board", "site", "company"} and not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_-]{0,199}", adapter_value
            ):
                failures.append("{} has an invalid {}".format(source_id, adapter_key))
        if "enabled" in source and not isinstance(source["enabled"], bool):
            failures.append("{} has a non-boolean enabled value".format(source_id))
        if "auto_enable" in source and not isinstance(source["auto_enable"], bool):
            failures.append("{} has a non-boolean auto_enable value".format(source_id))
        if source.get("support_level", "supported") not in {"supported", "experimental", "manual"}:
            failures.append("{} has an invalid support_level".format(source_id))
        required_urls = ["url"] + (["api_url"] if kind == "jibe" else [])
        for field in required_urls:
            try:
                _remote_url_parts(str(source.get(field) or ""))
            except ValueError:
                failures.append(
                    "{} does not have a valid public HTTPS {}".format(source_id, field)
                )
        if kind != "jibe" and source.get("api_url"):
            try:
                _remote_url_parts(str(source["api_url"]))
            except ValueError:
                failures.append(
                    "{} does not have a valid public HTTPS api_url".format(source_id)
                )
        if not bounded_integer(source.get("cadence_hours", 12), 1, 24 * 31):
            failures.append("{} has an invalid cadence_hours".format(source_id))
        if kind == "workday":
            from .employer_feeds import workday_board

            try:
                workday_board(str(source.get("url", "")))
            except ValueError:
                failures.append("{} has an invalid Workday board".format(source_id))
        if kind == "html_links":
            if not bounded_integer(source.get("pages", 1), 1, MAX_HTML_LINK_PAGES):
                failures.append("{} has an invalid pages value".format(source_id))
            for field in ("include", "exclude"):
                values = source.get(field, [])
                if not isinstance(values, list) or any(
                    not isinstance(value, str) or not value.strip()
                    for value in values
                ):
                    failures.append("{} has an invalid {} list".format(source_id, field))
            if "same_domain" in source and not isinstance(source["same_domain"], bool):
                failures.append("{} has an invalid same_domain value".format(source_id))
            if source.get("link_base_url"):
                try:
                    _base_url, base_host, _base_port = _remote_url_parts(
                        str(source["link_base_url"])
                    )
                    _source_url, source_host, _source_port = _remote_url_parts(
                        str(source.get("url") or "")
                    )
                    if base_host != source_host:
                        raise ValueError("host mismatch")
                except ValueError:
                    failures.append(
                        "{} has an invalid link_base_url".format(source_id)
                    )
        packs = source.get("packs", [])
        if not isinstance(packs, list) or any(str(pack) not in pack_ids for pack in packs):
            failures.append("{} references an invalid source pack".format(source_id))
        for field in ("domains", "opportunity_types", "career_levels", "regions"):
            if field in source and (
                not isinstance(source[field], list)
                or any(not isinstance(value, str) for value in source[field])
            ):
                failures.append("{} has an invalid {} field".format(source_id, field))
        statuses = source.get("expected_http_statuses", [])
        if (
            not isinstance(statuses, list)
            or any(isinstance(value, bool) or not isinstance(value, int) or value < 100 or value > 599 for value in statuses)
        ):
            failures.append("{} has invalid expected_http_statuses".format(source_id))
    matching = profile.get("matching", {})
    if not isinstance(matching, dict):
        failures.append("matching must be an object")
        matching = {}
    if not bounded_integer(matching.get("base_score", 50), 0, 100):
        failures.append("matching.base_score must be an integer from 0 to 100")
    if not bounded_integer(matching.get("priority_organization_bonus", 10), -100, 100):
        failures.append("matching.priority_organization_bonus must be an integer from -100 to 100")
    thresholds = matching.get("tier_thresholds", {})
    if not isinstance(thresholds, dict):
        failures.append("matching.tier_thresholds must be an object")
        thresholds = {}
    threshold_values = {
        name: thresholds.get(name, default)
        for name, default in (("priority", 75), ("strong", 55), ("watch", 25))
    }
    if any(not bounded_integer(value, 0, 100) for value in threshold_values.values()):
        failures.append("matching tier thresholds must be integers from 0 to 100")
    elif not (
        threshold_values["priority"] >= threshold_values["strong"] >= threshold_values["watch"]
    ):
        failures.append("matching tier thresholds must be ordered priority >= strong >= watch")
    rules = matching.get("rules", [])
    if not isinstance(rules, list):
        failures.append("matching.rules must be a list")
    else:
        for index, rule in enumerate(rules):
            label = "matching rule {}".format(index + 1)
            if not isinstance(rule, dict):
                failures.append("{} is invalid".format(label))
                continue
            terms = rule.get("terms", [])
            fields = rule.get("fields", list(MATCH_FIELDS))
            if (
                not isinstance(terms, list)
                or not terms
                or any(not isinstance(term, str) or not term.strip() for term in terms)
            ):
                failures.append("{} must have non-empty string terms".format(label))
            if (
                not isinstance(fields, list)
                or not fields
                or any(field not in MATCH_FIELDS for field in fields)
            ):
                failures.append("{} has invalid fields".format(label))
            if not bounded_integer(rule.get("weight", 0), -100, 100):
                failures.append("{} has an invalid weight".format(label))
            if str(rule.get("match", "any")).lower() not in {"any", "all"}:
                failures.append("{} has an invalid match mode".format(label))
            if "per_term" in rule and not isinstance(rule["per_term"], bool):
                failures.append("{} has a non-boolean per_term value".format(label))
            if "max_hits" in rule and not bounded_integer(rule["max_hits"], 1, 100):
                failures.append("{} has an invalid max_hits".format(label))
    documents = profile.get("documents", {})
    if not isinstance(documents, dict):
        failures.append("documents must be an object")
        documents = {}
    default_document = documents.get("default", "General")
    if not isinstance(default_document, str) or not default_document.strip() or len(default_document) > 120:
        failures.append("documents.default must be a non-empty label of at most 120 characters")
    routes = documents.get("routes", [])
    if not isinstance(routes, list):
        failures.append("documents.routes must be a list")
    else:
        for index, route in enumerate(routes):
            label = "document route {}".format(index + 1)
            if not isinstance(route, dict):
                failures.append("{} is invalid".format(label))
                continue
            route_label = route.get("label", route.get("code", ""))
            route_terms = route.get("terms", [])
            route_fields = route.get("fields", ["title", "description", "category"])
            if not isinstance(route_label, str) or not route_label.strip():
                failures.append("{} needs a label".format(label))
            if (
                not isinstance(route_terms, list)
                or not route_terms
                or any(not isinstance(term, str) or not term.strip() for term in route_terms)
            ):
                failures.append("{} must have non-empty string terms".format(label))
            if (
                not isinstance(route_fields, list)
                or not route_fields
                or any(field not in MATCH_FIELDS for field in route_fields)
            ):
                failures.append("{} has invalid fields".format(label))
    organizations = profile.get("priority_organizations", [])
    if (
        not isinstance(organizations, list)
        or any(not isinstance(name, str) or not name.strip() for name in organizations)
    ):
        failures.append("priority_organizations must contain non-empty strings")
    print(json.dumps({"ok": not failures, "checks": checks, "failures": failures}, indent=2))
    return 1 if failures else 0


def command_sources(args: argparse.Namespace) -> int:
    action = args.source_command or "list"
    if action == "discover":
        if not 1 <= args.limit <= 500 or len(args.query) > 240:
            raise ValueError("Use a query up to 240 characters and --limit from 1 to 500")
        rank = search_ranker(args.query)
        matches = []
        for source in load_sources(include_disabled=not args.enabled):
            if args.pack and args.pack not in source.get("packs", []):
                continue
            if args.kind and args.kind != source.get("kind"):
                continue
            taxonomy = " ".join(
                str(value).replace("_", " ")
                for key in ("domains", "role_families", "opportunity_types", "career_levels", "regions", "packs")
                for value in source.get(key, [])
            )
            relevance = rank(source["name"], source["id"], taxonomy, source.get("category"), source.get("url"))
            if relevance >= 0:
                matches.append((relevance, source))
        matches.sort(key=lambda entry: (-entry[0], not entry[1].get("enabled", True), entry[1]["name"].casefold()))
        rows = [entry[1] for entry in matches[:args.limit]]
        if args.json:
            print(json.dumps(rows, indent=2, ensure_ascii=False))
        elif not rows:
            print("No configured sources found.")
        else:
            for source in rows:
                print("{}  {}  {}  {}".format(
                    source["id"], "enabled" if source.get("enabled", True) else "disabled",
                    source["name"], source.get("publisher_url") or source.get("url", ""),
                ))
        return 0
    if action == "packs":
        sources = load_sources(include_disabled=True)
        for pack in load_source_packs():
            members = [
                source for source in sources
                if str(pack["id"]) in source.get("packs", [])
            ]
            supported = sum(source.get("kind") != "watch_page" for source in members)
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
                "SELECT id, name, last_status, last_error, item_count, last_checked_at "
                "FROM sources WHERE enabled=1 ORDER BY name"
            ).fetchall()
        finally:
            database.close()
        for row in rows:
            print(
                "{:<24} {:<8} {:>5} items  {}".format(
                    row["id"],
                    row["last_status"],
                    row["item_count"],
                    row["last_checked_at"] or "never",
                )
            )
            if row["last_error"]:
                print("  {}".format(str(row["last_error"]).replace("\n", " ")[:240]))
        return 0
    if action == "test":
        source_id = args.source_id
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
    if action == "show":
        try:
            print(json.dumps(source_summary(args.source_id), indent=2, ensure_ascii=False))
        except (OSError, ValueError) as error:
            print(str(error), file=sys.stderr)
            return 2
        return 0
    if action == "add":
        try:
            source = build_custom_source(
                name=args.name,
                url=args.url,
                source_id=args.id,
                kind=args.kind,
                adapter=args.adapter,
                packs=comma_values(args.packs) or None,
                cadence_hours=args.cadence_hours,
                **({"opportunity_type": args.opportunity_type} if args.opportunity_type else {}),
            )
            live = None
            if not args.skip_test:
                result = fetch_source(source)
                live = {
                    "status": result.status,
                    "items": len(result.opportunities),
                    "message": result.message,
                }
                if result.status not in {"ok", "blocked"}:
                    raise ProfileValidationError(
                        "Source test returned status {}".format(result.status)
                    )
            saved = add_source(source, dry_run=bool(args.dry_run))
            if live is not None:
                saved["test"] = live
            print(json.dumps(saved, indent=2, ensure_ascii=False))
        except (OSError, ValueError, RuntimeError) as error:
            print(str(error), file=sys.stderr)
            return 2
        return 0
    if action in {"enable", "disable", "remove"}:
        try:
            if action == "remove":
                result = remove_source(args.source_id, dry_run=bool(args.dry_run))
            else:
                result = set_source_enabled(
                    args.source_id,
                    action == "enable",
                    dry_run=bool(args.dry_run),
                )
            print(json.dumps(result, indent=2, ensure_ascii=False))
        except (OSError, ValueError, RuntimeError) as error:
            print(str(error), file=sys.stderr)
            return 2
        return 0
    sources = load_sources(include_disabled=bool(getattr(args, "all", False)))
    if getattr(args, "json", False):
        print(json.dumps(sources, indent=2, ensure_ascii=False))
        return 0
    for source in sources:
        marker = "" if source.get("enabled", True) else " [disabled]"
        print(
            "{:<28} {:<12} {:>3}h  {}{}".format(
                source["id"],
                source["kind"],
                source.get("cadence_hours", 12),
                source["name"],
                marker,
            )
        )
    return 0


def command_init(args: argparse.Namespace) -> int:
    if not args.non_interactive and sys.stdin.isatty():
        values: Dict[str, Any] = interactive_values()
    else:
        values = {
            "pack_ids": comma_values(args.packs) or default_pack_ids(),
            "coverage_preset": args.coverage_preset,
            "include_terms": comma_values(args.include),
            "domains": comma_values(args.domains),
            "opportunity_types": comma_values(args.opportunity_types),
            "exclude_terms": comma_values(args.exclude),
            "locations": comma_values(args.locations),
            "organizations": comma_values(args.organizations),
            "default_document": str(args.default_document).strip() or "General",
            "target": str(args.target).strip()[:120],
            "timeframes": [
                value
                for raw in (args.timeframe or [])
                for value in comma_values(raw)
            ],
        }
    try:
        result = initialize(force=args.force, **values)
    except (FileExistsError, ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


def _bounded_stdin() -> str:
    raw = sys.stdin.read(MAX_EDITOR_BYTES + 1)
    if len(raw.encode("utf-8")) > MAX_EDITOR_BYTES:
        raise ProfileValidationError("Profile editor input is too large")
    return raw


def _profile_input(args: argparse.Namespace) -> Dict[str, Any]:
    if getattr(args, "stdin", False):
        return read_editor_json(_bounded_stdin())
    path = getattr(args, "file", None)
    if path is not None:
        return read_editor_file(path)
    return profile_editor_payload()


def _print_profile_summary(payload: Dict[str, Any]) -> None:
    candidate = payload["candidate"]
    targets = payload["targets"]
    matching = payload["matching"]
    documents = payload["documents"]
    thresholds = matching.get("tier_thresholds", {})
    print("Profile revision: {}".format(payload["expected_revision"][:12]))
    print("Timeframes: {}".format(", ".join(payload["timeframes"]) or "Any timeframe"))
    print("Coverage preset: {}".format(payload["coverage_preset"]))
    print("Source packs: {}".format(", ".join(payload["selected_packs"])))
    print("Current stage: {}".format(candidate.get("current_stage", "Not specified")))
    print("Expected graduation: {}".format(candidate.get("expected_graduation", "Not specified")))
    print("Opportunity types: {}".format(", ".join(targets.get("opportunity_types", [])) or "Any"))
    print("Role families: {}".format(", ".join(targets.get("role_families", [])) or "Not specified"))
    print("Domains: {}".format(", ".join(targets.get("domains", [])) or "Not specified"))
    print("Locations: {}".format(", ".join(targets.get("locations", [])) or "Any"))
    print("Preferred organizations: {}".format(", ".join(payload["priority_organizations"]) or "None"))
    print("Matching rules: {}".format(len(matching.get("rules", []))))
    print(
        "Thresholds: priority {}, strong {}, visible {}".format(
            thresholds.get("priority", 75),
            thresholds.get("strong", 55),
            matching.get("minimum_display_score", thresholds.get("watch", 25)),
        )
    )
    print("Default document: {}".format(documents.get("default", "General")))
    print("Document routes: {}".format(len(documents.get("routes", []))))


def command_profile_show(as_json: bool = False) -> int:
    try:
        payload = profile_editor_payload()
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
    if as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        _print_profile_summary(payload)
    return 0


def command_profile_list(as_json: bool = False) -> int:
    try:
        catalog = profile_catalog_payload()
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
    if as_json:
        print(json.dumps(catalog, indent=2, ensure_ascii=False))
        return 0
    for entry in catalog["profiles"]:
        marker = "*" if entry["id"] == catalog["active_profile_id"] else " "
        print("{} {:<36} {}".format(marker, entry["id"], entry["name"]))
    return 0


def command_profile_template() -> int:
    print(json.dumps({
        "version": 1,
        "name": "Research and ML",
        "candidate": {
            "stage": "early_career",
            "graduation": "May 2027",
            "degrees": [{"type": "bachelors", "field": "Computer Science"}],
            "skills": ["Python", "PyTorch"],
            "max_experience_years": 3,
        },
        "search": {
            "timeframes": ["Fall 2026"],
            "opportunity_types": ["job", "internship"],
            "roles": ["Machine Learning Engineer"],
            "domains": ["Artificial Intelligence"],
            "skills": ["PyTorch"],
            "locations": ["United States"],
            "strict_locations": False,
            "work_arrangements": ["remote", "hybrid"],
            "remote_preference": "remote_preferred",
            "exclude": ["unpaid"],
            "organizations": [],
        },
        "sources": {"mode": "automatic", "packs": ["starter-diverse"]},
        "documents": {"default": "General", "routes": []},
    }, indent=2, ensure_ascii=False))
    return 0


def command_profile_import(args: argparse.Namespace) -> int:
    try:
        portable = _profile_input(args)
        catalog = profile_catalog_payload()
        result = apply_profile_management_payload({
            "version": catalog["version"],
            "operation": "import",
            "expected_revision": catalog["expected_revision"],
            "profile": portable,
        }, dry_run=bool(args.dry_run))
    except (OSError, ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 2
    if not args.quiet:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def command_profile_validate(args: argparse.Namespace) -> int:
    try:
        payload = validate_editor_payload(_profile_input(args))
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "ok": True,
                "version": payload["version"],
                "expected_revision": payload["expected_revision"],
            },
            indent=2,
        )
    )
    return 0


def command_profile_apply(args: argparse.Namespace) -> int:
    try:
        payload = _profile_input(args)
        if "operation" in payload:
            result = apply_profile_management_payload(
                payload,
                dry_run=bool(args.dry_run),
            )
        else:
            result = apply_editor_payload(
                payload,
                dry_run=bool(args.dry_run),
            )
    except (OSError, ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 2
    if not args.quiet:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def command_profile_management(args: argparse.Namespace) -> int:
    try:
        catalog = profile_catalog_payload()
        operation = str(args.profile_command)
        payload: Dict[str, Any] = {
            "version": catalog["version"],
            "operation": operation,
            "expected_revision": catalog["expected_revision"],
        }
        if hasattr(args, "profile"):
            payload["profile_id"] = resolve_profile_selector(args.profile)
        if hasattr(args, "name"):
            payload["name"] = args.name
        result = apply_profile_management_payload(
            payload,
            dry_run=bool(args.dry_run),
        )
    except (OSError, ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 2
    if not args.quiet:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def _replace_reserved_rule(
    rules: List[Dict[str, Any]],
    rule_id: str,
    terms: List[str],
    label: str,
    weight: int,
    fields: List[str],
    dimension: str,
    hard_gate: bool = False,
) -> None:
    existing = next((rule for rule in rules if rule.get("id") == rule_id), None)
    if not terms:
        rules[:] = [rule for rule in rules if rule.get("id") != rule_id]
        return
    replacement = dict(existing or {})
    replacement.update(
        {
            "id": rule_id,
            "label": str(replacement.get("label", label)),
            "weight": int(replacement.get("weight", weight)),
            "fields": list(replacement.get("fields", fields)),
            "terms": terms,
            "match": str(replacement.get("match", "any")),
            "per_term": bool(replacement.get("per_term", False)),
            "dimension": str(replacement.get("dimension", dimension)),
            "anchor": bool(replacement.get("anchor", dimension == "interest")),
            "hard_gate": bool(replacement.get("hard_gate", hard_gate)),
        }
    )
    if existing is None:
        rules.append(replacement)
    else:
        rules[rules.index(existing)] = replacement


def command_profile_set(args: argparse.Namespace) -> int:
    try:
        payload = profile_editor_payload()
        changed = False
        targets = payload["targets"]
        candidate = payload["candidate"]
        matching = payload["matching"]
        rules = matching.setdefault("rules", [])
        structured = str(matching.get("engine", "")).casefold() == "structured_v2"

        if args.include is not None:
            values = comma_values(args.include)
            targets["role_families"] = values
            if not structured:
                _replace_reserved_rule(
                    rules,
                    "preferred_work",
                    values,
                    "Preferred work",
                    24,
                    ["title", "description", "category", "opportunity_type"],
                    "interest",
                )
            changed = True
        if args.exclude is not None:
            values = comma_values(args.exclude)
            targets["exclusions"] = values
            if not structured:
                _replace_reserved_rule(
                    rules,
                    "excluded_work",
                    values,
                    "Excluded work",
                    -45,
                    ["title", "description", "category", "opportunity_type"],
                    "eligibility",
                    hard_gate=True,
                )
            changed = True
        if args.locations is not None:
            values = comma_values(args.locations)
            targets["locations"] = values
            if not structured:
                _replace_reserved_rule(
                    rules,
                    "preferred_location",
                    values,
                    "Preferred location",
                    10,
                    ["location"],
                    "preference",
                )
            changed = True
        if args.strict_locations is not None:
            targets["strict_locations"] = bool(args.strict_locations)
            changed = True
        if args.organizations is not None:
            payload["priority_organizations"] = comma_values(args.organizations)
            changed = True
        if args.timeframe is not None:
            timeframes = [
                value for raw in args.timeframe for value in comma_values(raw)
            ]
            payload["timeframes"] = timeframes
            targets["cycles"] = [{"label": value} for value in timeframes]
            if not structured:
                _replace_reserved_rule(
                    rules,
                    "target_timing",
                    timeframes,
                    "Target timeframe",
                    8,
                    ["title", "description"],
                    "target",
                )
            changed = True
        if args.packs is not None:
            payload["selected_packs"] = comma_values(args.packs)
            changed = True
        if args.coverage_preset is not None:
            payload["coverage_preset"] = args.coverage_preset
            for key, values in target_defaults_for_preset(args.coverage_preset).items():
                current = targets.get(key, [])
                current = current if isinstance(current, list) else []
                targets[key] = list(
                    dict.fromkeys(
                        [str(value) for value in current if str(value).strip()] + values
                    )
                )
            changed = True
        if args.current_stage is not None:
            if args.current_stage.strip():
                candidate["current_stage"] = args.current_stage
            else:
                candidate.pop("current_stage", None)
            changed = True
        if args.expected_graduation is not None:
            if args.expected_graduation.strip():
                candidate["expected_graduation"] = args.expected_graduation
            else:
                candidate.pop("expected_graduation", None)
            changed = True
        for argument, target_key in (
            ("opportunity_types", "opportunity_types"),
            ("role_families", "role_families"),
            ("domains", "domains"),
            ("supporting_skills", "supporting_skills"),
        ):
            value = getattr(args, argument)
            if value is not None:
                targets[target_key] = comma_values(value)
                changed = True
        if args.remote_preference is not None:
            targets["remote_preference"] = args.remote_preference
            changed = True
        if args.maximum_experience_years is not None:
            candidate["max_required_experience_years"] = args.maximum_experience_years
            changed = True
        if args.default_document is not None:
            payload["documents"]["default"] = args.default_document
            changed = True
        if args.base_score is not None:
            matching["base_score"] = args.base_score
            changed = True
        if args.minimum_display_score is not None:
            matching["minimum_display_score"] = args.minimum_display_score
            changed = True
        thresholds = matching.setdefault("tier_thresholds", {})
        for argument, threshold in (
            ("priority_threshold", "priority"),
            ("strong_threshold", "strong"),
            ("watch_threshold", "watch"),
        ):
            value = getattr(args, argument)
            if value is not None:
                thresholds[threshold] = value
                changed = True
        if not changed:
            raise ProfileValidationError("No profile changes were requested")
        result = apply_editor_payload(payload, dry_run=bool(args.dry_run))
    except (OSError, ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 2
    if not args.quiet:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def command_profile(args: argparse.Namespace) -> int:
    action = args.profile_command or "show"
    if action == "show":
        return command_profile_show(getattr(args, "json", False))
    if action == "list":
        return command_profile_list(getattr(args, "json", False))
    if action == "template":
        return command_profile_template()
    if action == "import":
        return command_profile_import(args)
    if action == "validate":
        return command_profile_validate(args)
    if action == "apply":
        return command_profile_apply(args)
    if action == "set":
        return command_profile_set(args)
    if action in {"create", "duplicate", "rename", "activate", "delete"}:
        return command_profile_management(args)
    return 2


OPPORTUNITY_OUTPUT_FIELDS = (
    "id",
    "score",
    "tier",
    "status",
    "title",
    "organization",
    "location",
    "opportunity_type",
    "posted_at",
    "deadline_at",
    "first_seen_at",
    "source_id",
    "source_name",
    "active",
    "bookmarked",
    "url",
)


def _opportunity_database_path() -> Path:
    return resolve_private_state_path(
        project_path("data", "opportunities.sqlite3"),
        "data",
        "opportunities.sqlite3",
    )


@contextmanager
def _read_opportunity_database(path: Path) -> Iterator[sqlite3.Connection]:
    uri = path.absolute().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5)
    try:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError("Database schema version {} is newer than this build supports".format(version))
        if version < SCHEMA_VERSION:
            connection.close()
            ensure_profile_lifecycle_idle()
            with exclusive_lock(path.with_name("scan.lock")):
                ensure_profile_lifecycle_idle()
                database = Database(path)
                try:
                    database.initialize()
                finally:
                    database.close()
            connection = sqlite3.connect(uri, uri=True, timeout=5)
        connection.row_factory = sqlite3.Row
        yield connection
    finally:
        connection.close()


def _opportunity_rows(args: argparse.Namespace) -> List[Dict[str, Any]]:
    limit = int(getattr(args, "limit", 50))
    offset = int(getattr(args, "offset", 0))
    minimum = int(getattr(args, "min_score", 0))
    if limit < 1 or limit > 500:
        raise ValueError("--limit must be from 1 to 500")
    if offset < 0 or offset > 1_000_000:
        raise ValueError("--offset must be from 0 to 1000000")
    if minimum < 0 or minimum > 100:
        raise ValueError("--min-score must be from 0 to 100")
    query = str(getattr(args, "query", "") or "").strip()
    organization = str(getattr(args, "organization", "") or "").strip()
    location = str(getattr(args, "location", "") or "").strip()
    if any(len(value) > 240 for value in (query, organization, location)):
        raise ValueError("Search query must be at most 240 characters")
    path = _opportunity_database_path()
    if not path.is_file():
        return []
    clauses = ["opportunities.score >= ?"]
    values: List[Any] = [minimum]
    if getattr(args, "saved", False):
        clauses.append("opportunities.bookmarked=1")
    elif not getattr(args, "include_inactive", False):
        clauses.extend(("opportunities.active=1", "sources.enabled=1"))
    if getattr(args, "matched", False):
        clauses.append("opportunities.tier != 'skip'")
    for attribute, column in (
        ("status", "opportunities.status"),
        ("opportunity_type", "opportunities.opportunity_type"),
        ("source", "opportunities.source_id"),
    ):
        value = getattr(args, attribute, None)
        if value:
            if attribute == "opportunity_type":
                value = normalize_opportunity_type(value) or value
            clauses.append("{}=?".format(column))
            values.append(str(value))

    # Readers use the latest committed snapshot, including during collection.
    with _read_opportunity_database(path) as connection:
        if query:
            connection.create_function("radar_rank", 8, search_ranker(query), deterministic=True)
            rank = (
                "radar_rank(opportunities.title, opportunities.organization, "
                "REPLACE(opportunities.opportunity_type, '_', ' '), opportunities.category, "
                "opportunities.location, sources.name, opportunities.eligibility, "
                "opportunities.description)"
            )
            clauses.append(rank + " >= 0")
        else:
            rank = ""
        for name, value in (("organization", organization), ("location", location)):
            if value:
                function_name = "radar_" + name
                connection.create_function(function_name, 1, search_ranker(value), deterministic=True)
                clauses.append("{}(opportunities.{}) >= 0".format(function_name, name))

        if getattr(args, "available", False):
            today = date.today()

            def available(active, enabled, deadline, metadata):
                value = json.loads(metadata)
                collection = value.get("collection", {}) if isinstance(value, dict) else {}
                schedule = value.get("listing", {}) if isinstance(value, dict) else {}
                return int(listing_availability(
                    bool(active and enabled), deadline,
                    collection if isinstance(collection, dict) else {},
                    schedule if isinstance(schedule, dict) else {},
                    today,
                ) in {"listed", "unconfirmed"})

            connection.create_function("radar_available", 4, available, deterministic=True)
            clauses.append("radar_available(opportunities.active, sources.enabled, opportunities.deadline_at, opportunities.metadata_json)")
        orders = {
            "fit": "",
            "relevance": rank + " DESC, " if rank else "",
            "recent": "opportunities.first_seen_at DESC, ",
            "deadline": "COALESCE(opportunities.deadline_at, '9999') ASC, ",
        }
        fields = ", ".join(
            "opportunities.{}".format(field)
            for field in OPPORTUNITY_OUTPUT_FIELDS if field != "source_name"
        )
        order = (
            orders[getattr(args, "sort", "relevance")]
            + "opportunities.score DESC, COALESCE(opportunities.deadline_at, '9999') ASC, "
            + "opportunities.first_seen_at DESC, opportunities.id"
        )
        query_sql = (
            "SELECT {}, sources.name AS source_name FROM opportunities "
            "JOIN sources ON sources.id=opportunities.source_id WHERE {} ORDER BY {}"
        ).format(fields, " AND ".join(clauses), order)
        if not getattr(args, "unique", False):
            rows = connection.execute(query_sql + " LIMIT ? OFFSET ?", values + [limit, offset])
            return [dict(row) for row in rows]
        seen = set()
        results = []
        for row in connection.execute(query_sql, values):
            key = listing_key(row["url"], row["title"], row["organization"])
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            if offset:
                offset -= 1
                continue
            results.append(dict(row))
            if len(results) == limit:
                break
        return results


def _print_opportunity_rows(
    rows: List[Dict[str, Any]],
    as_json: bool,
    as_csv: bool,
) -> None:
    if as_json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return
    if as_csv:
        writer = csv.DictWriter(sys.stdout, fieldnames=OPPORTUNITY_OUTPUT_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in OPPORTUNITY_OUTPUT_FIELDS})
        return
    if not rows:
        print("No opportunities found.")
        return
    for row in rows:
        deadline = row.get("deadline_at") or "no deadline"
        print(
            "{}  {:>3}  {:<8} {:<9} {} at {} ({})".format(
                row["id"],
                row["score"],
                row["tier"],
                row["status"],
                row["title"],
                row["organization"],
                deadline,
            )
        )


def command_opportunities(args: argparse.Namespace) -> int:
    action = args.opportunity_command or "list"
    try:
        if action in {"list", "search"}:
            rows = _opportunity_rows(args)
            _print_opportunity_rows(
                rows,
                bool(getattr(args, "json", False)),
                bool(getattr(args, "csv", False)),
            )
            return 0
        if action == "show":
            path = _opportunity_database_path()
            if not path.is_file():
                print("Opportunity not found", file=sys.stderr)
                return 1
            with _read_opportunity_database(path) as connection:
                row = connection.execute(
                    "SELECT opportunities.*, sources.name AS source_name, "
                    "sources.url AS source_url, sources.enabled AS source_enabled, "
                    "sources.last_status AS source_status "
                    "FROM opportunities JOIN sources ON sources.id=opportunities.source_id "
                    "WHERE opportunities.id=?",
                    (args.opportunity_id,),
                ).fetchone()
            if row is None:
                print("Opportunity not found", file=sys.stderr)
                return 1
            payload = dict(row)
            for field in ("reasons_json", "warnings_json", "metadata_json"):
                value = payload.pop(field, None)
                if value is not None:
                    payload[field.removesuffix("_json")] = json.loads(value)
            facts = opportunity_facts(payload)
            payload["facts"] = facts
            if args.json:
                print(json.dumps(payload, indent=2, ensure_ascii=False))
            else:
                print("{} at {}".format(payload["title"], payload["organization"]))
                print("ID: {}".format(payload["id"]))
                print("Fit: {} ({})".format(payload["score"], payload["tier"]))
                print("Type: {}".format(facts["kind"].replace("_", " ")))
                print("Status: {}{}".format(
                    payload["status"],
                    "" if payload["active"] and payload["source_enabled"] else " (archived)",
                ))
                print("Location: {}".format(payload["location"] or "Not listed"))
                print("Deadline: {}".format(payload["deadline_at"] or "Not listed"))
                print("Source: {} ({})".format(payload["source_name"], payload["source_status"]))
                if facts.get("publisher_url"):
                    print("Publisher: {}".format(facts["publisher_url"]))
                print("URL: {}".format(payload["url"]))
                print("Listing information: {}".format(facts["information"].replace("_", " ")))
                print("Availability: {}".format(facts["availability"]))
                for key, value in facts.get("schedule", {}).items():
                    print("{}: {}".format(key.replace("_", " ").capitalize(), value))
                match = payload.get("metadata", {}).get("match", {})
                if isinstance(match, dict) and match.get("eligibility"):
                    print("Profile checks: {}".format(match["eligibility"]))
                    for gate in match.get("gates", []):
                        evidence = "; ".join(str(value) for value in gate.get("evidence", []))
                        print("  {} - {}{}".format(
                            str(gate.get("id", "check")).replace("_", " "),
                            gate.get("state", "unknown"), " (" + evidence + ")" if evidence else "",
                        ))
                for label, entries in (("Fit evidence", payload["reasons"]), ("Review", payload["warnings"])):
                    for entry in entries:
                        print("{}: {}".format(label, entry))
                for group in ("activities", "benefits", "requirements"):
                    for entry in facts[group]:
                        print("{} evidence - {}: {}".format(group.capitalize(), entry["label"], entry["evidence"]))
                if payload["description"]:
                    print("\n{}".format(payload["description"]))
            return 0
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
        print(str(error), file=sys.stderr)
        return 2
    return 2


def _command_workflow(opportunity_id: str, value: object, kind: str, quiet: bool) -> int:
    ensure_profile_lifecycle_idle()
    database_path = resolve_private_state_path(
        project_path("data", "opportunities.sqlite3"),
        "data",
        "opportunities.sqlite3",
    )
    with exclusive_lock(database_path.with_name("scan.lock")):
        ensure_profile_lifecycle_idle()
        profile = load_profile()
        database = Database(database_path)
        try:
            database.initialize()
            if kind == "status":
                changed = database.set_status(opportunity_id, str(value))
            else:
                changed = database.set_bookmarked(opportunity_id, bool(value))
            if changed:
                database.rescore_for_profile(profile)
                if automatic_dashboard_enabled():
                    render_dashboard(database.dashboard_payload(), profile=profile)
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
    try:
        if args.command == "scan":
            return command_scan(
                args.force, args.notify, args.quiet, args.source,
                max_sources=args.max_sources, max_seconds=args.max_seconds,
                show_progress=args.progress,
            )
        if args.command == "dashboard":
            return command_dashboard(args.quiet)
        if args.command == "doctor":
            return command_doctor()
        if args.command == "sources":
            return command_sources(args)
        if args.command == "opportunities":
            return command_opportunities(args)
        if args.command == "init":
            return command_init(args)
        if args.command == "profile":
            return command_profile(args)
        if args.command == "status":
            return command_status(args.opportunity_id, args.value, args.quiet)
        if args.command == "bookmark":
            return command_bookmark(args.opportunity_id, args.value, args.quiet)
    except (ProfileValidationError, RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
