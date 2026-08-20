"""End-to-end scan orchestration."""

import fcntl
import os
import re
import sys
import urllib.error
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from .config import (
    load_profile,
    load_sources,
    project_path,
    resolve_private_state_path,
    resolve_project_value,
)
from .dashboard import render_dashboard
from .database import Database
from .fetchers import fetch_source, polite_pause
from .notifications import notify_macos, notify_webhook, summarize
from .scoring import score_opportunity
from .seed import parse_pipeline


MAX_NOTIFICATION_ITEMS = 100
LIFECYCLE_OWNER_ENV = "OPPORTUNITY_RADAR_LIFECYCLE_OWNER"


def ensure_profile_lifecycle_idle() -> None:
    """Prevent a scan from crossing a runtime install or profile replacement."""
    if sys.platform != "darwin" or os.environ.get(LIFECYCLE_OWNER_ENV) == "installer":
        return
    from .profile import _lifecycle_lock_path, recover_stale_lifecycle_lock

    lock = _lifecycle_lock_path()
    recover_stale_lifecycle_lock(lock)
    if lock.exists() or lock.is_symlink():
        raise RuntimeError(
            "An Opportunity Radar install, uninstall, or profile update is already running"
        )


@contextmanager
def exclusive_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("w")
    os.chmod(path, 0o600)
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise RuntimeError("Another Opportunity Radar scan is already running")
    try:
        handle.write(str(os.getpid()))
        handle.flush()
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _curated_path(profile: Dict[str, Any]) -> Optional[Path]:
    configured = str(profile.get("curated_pipeline_path", "")).strip()
    return resolve_project_value(configured) if configured else None


def _register_sources(database: Database, sources: List[Dict[str, Any]], profile: Dict[str, Any]) -> None:
    registered_ids = []
    if _curated_path(profile):
        database.sync_source(
            {
                "id": "curated_pipeline",
                "name": str(profile.get("curated_pipeline_name", "Curated opportunity pipeline")),
                "kind": "markdown_seed",
                "url": "curated://local-pipeline",
                "category": "curated",
                "cadence_hours": 12,
                "enabled": True,
            }
        )
        registered_ids.append("curated_pipeline")
    for source in sources:
        database.sync_source(source)
        registered_ids.append(str(source["id"]))
    database.reconcile_sources(registered_ids)


def _target_year(profile: Dict[str, Any]) -> Optional[str]:
    """Return the earliest configured search-cycle year for legacy seed dates."""
    values: List[Any] = []
    targets = profile.get("targets", {})
    if isinstance(targets, dict):
        cycles = targets.get("cycles", [])
        if isinstance(cycles, list):
            for cycle in cycles:
                if isinstance(cycle, dict):
                    values.extend((cycle.get("year"), cycle.get("label")))
                else:
                    values.append(cycle)
    timeframes = profile.get("timeframes", [])
    if isinstance(timeframes, list):
        values.extend(timeframes)
    dashboard = profile.get("dashboard", {})
    candidate = profile.get("candidate", {})
    if isinstance(dashboard, dict):
        values.append(dashboard.get("target_season"))
    if isinstance(candidate, dict):
        values.append(candidate.get("target_season"))
    years = {
        match.group(1)
        for value in values
        for match in [re.search(r"\b(20\d{2})\b", str(value or ""))]
        if match
    }
    return min(years) if years else None


def _import_curated(database: Database, profile: Dict[str, Any]) -> Tuple[Dict[str, int], Optional[str]]:
    path = _curated_path(profile)
    counts = {"new": 0, "updated": 0, "unchanged": 0}
    if path is None:
        return counts, None
    if not path.exists():
        message = "Curated pipeline not found: {}".format(path.name)
        database.source_error("curated_pipeline", message)
        return counts, message
    documents = profile.get("documents", {})
    if not isinstance(documents, dict):
        documents = {}
    routes = documents.get("routes", [])
    document_labels = [
        entry.get("label", entry.get("code", ""))
        for entry in routes if isinstance(entry, dict)
    ]
    document_labels.extend(
        entry.get("code", "")
        for entry in profile.get("resume_routing", []) if isinstance(entry, dict)
    )
    items = parse_pipeline(
        path,
        resume_codes=document_labels,
        default_resume=str(
            documents.get("default", profile.get("default_resume_code", ""))
        ),
        default_year=_target_year(profile),
    )
    identifiers = []
    for item in items:
        score_opportunity(item, profile)
        result = database.upsert_opportunity(item)
        counts[result] += 1
        identifiers.append(item.external_id)
    database.mark_source_stale("curated_pipeline", identifiers)
    database.source_success("curated_pipeline", str(path.stat().st_mtime_ns), len(items))
    return counts, None


def run_scan(force: bool = False, send_notifications: bool = False) -> Dict[str, Any]:
    ensure_profile_lifecycle_idle()
    database_path = resolve_private_state_path(
        project_path("data", "opportunities.sqlite3"),
        "data",
        "opportunities.sqlite3",
    )
    with exclusive_lock(database_path.with_name("scan.lock")):
        # Close the check/acquire race: an installer that began after the first
        # check now owns the lifecycle and this scan must release its lock.
        ensure_profile_lifecycle_idle()
        database = Database(database_path)
        try:
            database.initialize()
            profile = load_profile()
            database.rescore_for_profile(profile)
            all_sources = load_sources(include_disabled=True)
            sources = [source for source in all_sources if source.get("enabled", True)]
            _register_sources(database, all_sources, profile)
            run_id, started_at = database.begin_run()
        except Exception:
            database.close()
            raise
        checked = 0
        new_count = 0
        updated_count = 0
        errors: List[str] = []
        notification_errors: List[str] = []
        new_high_fit: List[Dict[str, Any]] = []
        try:
            curated_counts, curated_error = _import_curated(database, profile)
            new_count += curated_counts["new"]
            updated_count += curated_counts["updated"]
            if curated_error:
                errors.append(curated_error)

            for source in sources:
                if not database.source_due(source["id"], force=force):
                    continue
                checked += 1
                try:
                    result = fetch_source(source)
                    current_ids = []
                    for item in result.opportunities:
                        score_opportunity(item, profile)
                        outcome = database.upsert_opportunity(item)
                        current_ids.append(item.external_id)
                        if outcome == "new":
                            new_count += 1
                            if (
                                item.tier in {"priority", "strong"}
                                and len(new_high_fit) < MAX_NOTIFICATION_ITEMS
                            ):
                                new_high_fit.append(
                                    {
                                        "title": item.title,
                                        "organization": item.organization,
                                        "score": item.score,
                                        "url": item.url,
                                        "tier": item.tier,
                                    }
                                )
                        elif outcome == "updated":
                            updated_count += 1
                    database.mark_source_stale(source["id"], current_ids)
                    if source["kind"] == "watch_page":
                        created = database.source_watch_success(
                            source["id"],
                            result.content_hash,
                            len(result.opportunities),
                            "Page changed: {}".format(source["name"]),
                            str(source.get("url", "")),
                        )
                        if (
                            created
                            and source.get("notify_page_changes", True)
                            and len(new_high_fit) < MAX_NOTIFICATION_ITEMS
                        ):
                            new_high_fit.append(
                                {
                                    "title": "Page changed",
                                    "organization": source["name"],
                                    "score": 0,
                                    "url": source.get("url", ""),
                                    "tier": "watch",
                                }
                            )
                    else:
                        database.source_success(
                            source["id"], result.content_hash, len(result.opportunities)
                        )
                except Exception as error:
                    if (
                        isinstance(error, urllib.error.HTTPError)
                        and error.code in source.get("expected_http_statuses", [])
                    ):
                        database.source_blocked(
                            source["id"],
                            "Official page returned HTTP {}; prior records remain active".format(error.code),
                        )
                        polite_pause(float(profile.get("polite_delay_seconds", 0.15)))
                        continue
                    message = "{}: {}".format(source["name"], error)
                    errors.append(message)
                    if database.source_status(source["id"]) != "error":
                        notification_errors.append(message)
                    database.source_error(source["id"], str(error))
                polite_pause(float(profile.get("polite_delay_seconds", 0.15)))

            status = "ok" if not errors else "partial"
            database.finish_run(
                run_id,
                started_at,
                status,
                checked,
                new_count,
                updated_count,
                len(errors),
                "\n".join(errors),
            )
            database.prune_history()
            payload = database.dashboard_payload()
            dashboard_path = render_dashboard(payload, profile=profile)
            database.optimize()

            if send_notifications and (new_high_fit or notification_errors):
                title = str(profile.get("dashboard", {}).get("title", "Opportunity Radar"))
                body = summarize(new_high_fit) if new_high_fit else "{} source checks newly failed".format(len(notification_errors))
                notify_macos(title, body)
                try:
                    notify_webhook(
                        {
                            "title": title,
                            "summary": body,
                            "new_opportunities": new_high_fit[:10],
                            "errors": notification_errors,
                        }
                    )
                except Exception as error:
                    errors.append("Webhook notification failed ({})".format(type(error).__name__))

            return {
                "status": status,
                "checked_sources": checked,
                "new_count": new_count,
                "updated_count": updated_count,
                "errors": errors,
                "new_high_fit": new_high_fit,
                "dashboard": str(dashboard_path),
            }
        except Exception as error:
            database.finish_run(run_id, started_at, "failed", checked, new_count, updated_count, len(errors) + 1, str(error))
            raise
        finally:
            database.close()
