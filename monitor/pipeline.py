"""End-to-end scan orchestration."""

import fcntl
import math
import os
import re
import subprocess
import sys
import time
import urllib.error
from contextlib import contextmanager
from functools import partial
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from .config import (
    automatic_dashboard_enabled,
    load_profile,
    load_sources,
    project_path,
    resolve_private_state_path,
    resolve_project_value,
)
from .dashboard import render_dashboard
from .database import Database, source_snapshot_key, utc_now
from .fetchers import fetch_source, polite_pause
from .models import FetchResult, Opportunity
from .notifications import notify_macos, summarize
from .scoring import score_opportunity
from .seed import parse_pipeline


MAX_NOTIFICATION_ITEMS = 100
DEFAULT_DUE_BURST_SECONDS = 45
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
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "r+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another Opportunity Radar scan is already running")
        try:
            os.fchmod(descriptor, 0o600)
            handle.truncate(0)
            handle.write(str(os.getpid()))
            handle.flush()
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _curated_path(profile: Dict[str, Any]) -> Optional[Path]:
    configured = str(profile.get("curated_pipeline_path", "")).strip()
    return resolve_project_value(configured) if configured else None


def _register_sources(
    database: Database, sources: List[Dict[str, Any]], profile: Dict[str, Any]
) -> None:
    with database.transaction():
        registered_ids = []
        if _curated_path(profile):
            database.sync_source(
                {
                    "id": "curated_pipeline",
                    "name": str(
                        profile.get(
                            "curated_pipeline_name", "Curated opportunity pipeline"
                        )
                    ),
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


def _import_curated(
    database: Database, profile: Dict[str, Any]
) -> Tuple[Dict[str, int], Optional[str]]:
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
        for entry in routes
        if isinstance(entry, dict)
    ]
    document_labels.extend(
        entry.get("code", "")
        for entry in profile.get("resume_routing", [])
        if isinstance(entry, dict)
    )
    items = parse_pipeline(
        path,
        resume_codes=document_labels,
        default_resume=str(
            documents.get("default", profile.get("default_resume_code", ""))
        ),
        default_year=_target_year(profile),
    )
    with database.transaction():
        for item in items:
            result = database.upsert_opportunity(
                item, score=partial(score_opportunity, profile=profile)
            )
            counts[result] += 1
        database.mark_source_stale(
            "curated_pipeline", (item.external_id for item in items)
        )
        database.source_success(
            "curated_pipeline", str(path.stat().st_mtime_ns), len(items)
        )
    return counts, None


def _ingest_source(
    database: Database,
    source: Dict[str, Any],
    result: FetchResult,
    score: Callable[[Opportunity], Opportunity],
) -> Tuple[int, int, bool, List[Dict[str, Any]]]:
    """Commit a source refresh and its freshness marker together."""
    if result.status != "ok":
        raise ValueError(
            result.message or "Source returned status {}".format(result.status)
        )
    source_id = source["id"]
    items = result.opportunities
    identifiers = {item.external_id for item in items}
    if len(identifiers) != len(items) or any(
        item.source_id != source_id for item in items
    ):
        raise ValueError(
            "Source returned duplicate identifiers or records from another source"
        )
    watch_page = source["kind"] == "watch_page"
    snapshot_key = (
        source_snapshot_key(items)
        if result.complete_snapshot and not watch_page
        else None
    )
    seen_at = utc_now()
    new_count = updated_count = 0
    highlights: List[Dict[str, Any]] = []
    with database.transaction():
        unchanged = snapshot_key is not None and database.refresh_unchanged_snapshot(
            source_id, snapshot_key, identifiers, seen_at
        )
        if not unchanged:
            for item in items:
                outcome = database.upsert_opportunity(item, seen_at, score=score)
                if outcome == "new":
                    new_count += 1
                    if (
                        item.tier in {"priority", "strong"}
                        and len(highlights) < MAX_NOTIFICATION_ITEMS
                    ):
                        highlights.append(
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
            if result.complete_snapshot:
                database.mark_source_stale(source_id, identifiers)
            else:
                database.archive_rolling_source_history(source_id, seen_at)

        if watch_page:
            created = database.source_watch_success(
                source_id,
                result.content_hash,
                len(items),
                "Page changed: {}".format(source["name"]),
                str(source.get("url", "")),
            )
            if created and source.get("notify_page_changes", True):
                highlights.append(
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
                source_id,
                result.content_hash,
                len(items),
                snapshot_key=snapshot_key,
                detail_offset=result.next_detail_offset,
            )
    return new_count, updated_count, unchanged, highlights


def run_scan(
    force: bool = False,
    send_notifications: bool = False,
    source_ids: Optional[List[str]] = None,
    *,
    max_sources: Optional[int] = None,
    max_seconds: Optional[float] = None,
    progress: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """Refresh due sources in a finite burst; an in-flight source keeps its own deadline."""
    if max_sources is not None and (
        isinstance(max_sources, bool)
        or not isinstance(max_sources, int)
        or max_sources < 1
    ):
        raise ValueError("Maximum sources must be a positive integer")
    if max_seconds is not None and (
        isinstance(max_seconds, bool)
        or not math.isfinite(max_seconds)
        or max_seconds <= 0
    ):
        raise ValueError("Maximum scan seconds must be positive and finite")
    if max_seconds is None and not force and not source_ids:
        max_seconds = DEFAULT_DUE_BURST_SECONDS

    began = time.monotonic()
    ensure_profile_lifecycle_idle()
    database_path = resolve_private_state_path(
        project_path("data", "opportunities.sqlite3"),
        "data",
        "opportunities.sqlite3",
    )
    with exclusive_lock(database_path.with_name("scan.lock")):
        # Close the check/acquire race with a lifecycle operation.
        ensure_profile_lifecycle_idle()
        database = Database(database_path)
        try:
            database.initialize()
            profile = load_profile()
            all_sources = load_sources(include_disabled=True)
            sources = [source for source in all_sources if source.get("enabled", True)]
            if source_ids:
                requested = set(source_ids)
                enabled_ids = {source["id"] for source in sources}
                if requested - enabled_ids:
                    raise ValueError(
                        "Requested scan sources are unknown or disabled: {}".format(
                            ", ".join(sorted(requested - enabled_ids))
                        )
                    )
                sources = [source for source in sources if source["id"] in requested]
            _register_sources(database, all_sources, profile)
            due = database.sources_due(sources, force=force)
            planned = due[:max_sources]
            run_id, started_at = database.begin_run()
        except (Exception, KeyboardInterrupt):
            database.close()
            raise

        checked = unchanged_sources = blocked_sources = new_count = updated_count = 0
        errors: List[str] = []
        notification_errors: List[str] = []
        new_high_fit: List[Dict[str, Any]] = []

        def report(event: str, **details: Any) -> None:
            if progress is not None:
                progress(
                    {
                        "event": event,
                        "total_sources": len(planned),
                        "due_sources": len(due),
                        "checked_sources": checked,
                        "remaining_due": len(due) - checked,
                        "elapsed_seconds": round(time.monotonic() - began, 3),
                        **details,
                    }
                )

        try:
            report("start")
            database.rescore_for_profile(profile)
            if not source_ids:
                curated_counts, curated_error = _import_curated(database, profile)
                new_count += curated_counts["new"]
                updated_count += curated_counts["updated"]
                if curated_error:
                    errors.append(curated_error)

            deadline = (
                time.monotonic() + max_seconds
                if max_seconds is not None
                else float("inf")
            )
            delay = float(profile.get("polite_delay_seconds", 0.15))
            score = partial(score_opportunity, profile=profile)
            for source in planned:
                if checked:
                    if time.monotonic() + delay >= deadline:
                        break
                    polite_pause(delay)
                    if time.monotonic() >= deadline:
                        break
                source_details = {
                    "source_id": source["id"],
                    "source_name": source["name"],
                }
                report("source_start", **source_details)
                checked += 1
                source_status = "ok"
                try:
                    request = source
                    if source["kind"] in {"workday", "smartrecruiters"}:
                        request = dict(
                            source,
                            _detail_offset=database.source_detail_offset(source["id"]),
                        )
                    result = fetch_source(request)
                    added, updated, unchanged, highlights = _ingest_source(
                        database, source, result, score
                    )
                    new_count += added
                    updated_count += updated
                    unchanged_sources += int(unchanged)
                    new_high_fit.extend(
                        highlights[: MAX_NOTIFICATION_ITEMS - len(new_high_fit)]
                    )
                    if unchanged:
                        source_status = "unchanged"
                except Exception as error:
                    if isinstance(
                        error, urllib.error.HTTPError
                    ) and error.code in source.get("expected_http_statuses", []):
                        database.source_blocked(
                            source["id"],
                            "Official page returned HTTP {}; prior records remain active".format(
                                error.code
                            ),
                        )
                        blocked_sources += 1
                        source_status = "blocked"
                    else:
                        message = "{}: {}".format(source["name"], error)
                        errors.append(message)
                        if database.source_status(source["id"]) != "error":
                            notification_errors.append(message)
                        database.source_error(source["id"], str(error))
                        source_status = "error"
                report("source_complete", status=source_status, **source_details)

            status = "partial" if errors or checked < len(due) else "ok"
            collection = {
                "due_sources": len(due),
                "checked_sources": checked,
                "remaining_due": len(due) - checked,
                "unchanged_sources": unchanged_sources,
            }
            database.finish_run(
                run_id,
                started_at,
                status,
                checked,
                new_count,
                updated_count,
                len(errors),
                "\n".join(errors),
                collection=None if source_ids else collection,
            )
            database.prune_history()
            dashboard_path = None
            if automatic_dashboard_enabled():
                report("render")
                dashboard_path = render_dashboard(database.dashboard_payload(), profile=profile)
            database.optimize()

            notification_error = None
            if send_notifications and (new_high_fit or notification_errors):
                title = str(
                    profile.get("dashboard", {}).get("title", "Opportunity Radar")
                )
                body = (
                    summarize(new_high_fit)
                    if new_high_fit
                    else "{} source checks newly failed".format(
                        len(notification_errors)
                    )
                )
                try:
                    notify_macos(title, body)
                except (OSError, subprocess.TimeoutExpired) as error:
                    notification_error = "Local notification failed ({})".format(
                        type(error).__name__
                    )

            report("finish", status=status)
            return {
                "status": status,
                **collection,
                "blocked_sources": blocked_sources,
                "duration_seconds": round(time.monotonic() - began, 3),
                "new_count": new_count,
                "updated_count": updated_count,
                "errors": errors,
                "notification_error": notification_error,
                "new_high_fit": new_high_fit,
                "dashboard": str(dashboard_path) if dashboard_path is not None else None,
            }
        except (Exception, KeyboardInterrupt) as error:
            database.finish_run(
                run_id,
                started_at,
                "failed",
                checked,
                new_count,
                updated_count,
                len(errors) + 1,
                str(error) or type(error).__name__,
            )
            raise
        finally:
            database.close()
