"""SQLite persistence for source health, opportunity history, and run metrics."""

import json
import os
import sqlite3
import urllib.parse
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Tuple

from .models import Opportunity
from .opportunity_facts import opportunity_facts
from .scoring import (
    LEGACY_CURATED_DOCUMENT_PROVENANCE,
    profile_fingerprint,
    score_opportunity,
)
from .text import stable_hash


MAX_DASHBOARD_DISCOVERY_ITEMS = 5000
MAX_DASHBOARD_CATALOG_ITEMS = 50000
PROFILE_FINGERPRINT_KEY = "profile_fingerprint"
SCHEMA_VERSION = 6
SOURCE_HASH_SCHEMA_VERSION = 5
SOURCE_SNAPSHOT_KEY_PREFIX = "scan_snapshot:"
DETAIL_OFFSET_KEY_PREFIX = "scan_detail_offset:"
COLLECTION_SUMMARY_KEY = "collection_summary"
ROLLING_ARCHIVE_DAYS = 90
WORKFLOW_STATUS_RANK = {
    "new": 0,
    "reviewed": 1,
    "skip": 2,
    "apply": 3,
    "applied": 4,
}
SOURCE_HASH_FIELDS = (
    "title",
    "organization",
    "location",
    "url",
    "description",
    "category",
    "opportunity_type",
    "posted_at",
    "deadline_at",
    "commitment",
    "eligibility",
)


SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    url TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '',
    cadence_hours INTEGER NOT NULL DEFAULT 12,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_checked_at TEXT,
    last_success_at TEXT,
    last_status TEXT NOT NULL DEFAULT 'never',
    last_error TEXT NOT NULL DEFAULT '',
    item_count INTEGER NOT NULL DEFAULT 0,
    last_content_hash TEXT NOT NULL DEFAULT '',
    pending_content_hash TEXT NOT NULL DEFAULT '',
    pending_content_checks INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS opportunities (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(id),
    external_id TEXT NOT NULL,
    title TEXT NOT NULL,
    organization TEXT NOT NULL,
    location TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    opportunity_type TEXT NOT NULL DEFAULT 'opportunity',
    posted_at TEXT,
    deadline_at TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new',
    status_updated_at TEXT,
    applied_at TEXT,
    bookmarked INTEGER NOT NULL DEFAULT 0,
    score INTEGER NOT NULL DEFAULT 0,
    tier TEXT NOT NULL DEFAULT 'watch',
    reasons_json TEXT NOT NULL DEFAULT '[]',
    warnings_json TEXT NOT NULL DEFAULT '[]',
    recommended_resume TEXT NOT NULL DEFAULT '',
    commitment TEXT NOT NULL DEFAULT '',
    eligibility TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    raw_hash TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    UNIQUE(source_id, external_id)
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    checked_sources INTEGER NOT NULL DEFAULT 0,
    new_count INTEGER NOT NULL DEFAULT 0,
    updated_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS source_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL REFERENCES sources(id),
    event_type TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    previous_hash TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runtime_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS opportunities_active_score_idx
    ON opportunities(active, score DESC);
CREATE INDEX IF NOT EXISTS opportunities_status_score_idx
    ON opportunities(status, score DESC);
CREATE INDEX IF NOT EXISTS opportunities_deadline_idx
    ON opportunities(deadline_at);
CREATE INDEX IF NOT EXISTS opportunities_source_seen_idx
    ON opportunities(source_id, last_seen_at DESC);
CREATE INDEX IF NOT EXISTS runs_started_idx
    ON runs(started_at DESC);
CREATE INDEX IF NOT EXISTS source_events_occurred_idx
    ON source_events(occurred_at DESC);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _source_content_hash(values: Dict[str, Any], *, legacy: bool = False) -> str:
    metadata_value = values.get("metadata", values.get("metadata_json", {}))
    if isinstance(metadata_value, str):
        metadata_value = json.loads(metadata_value)
    metadata = dict(metadata_value) if isinstance(metadata_value, dict) else {}
    routing = metadata.pop("document_routing", {})
    metadata.pop("match", None)
    if not legacy:
        metadata.pop("eligibility_state", None)
    payload = {field: values.get(field) for field in SOURCE_HASH_FIELDS}
    payload["metadata"] = metadata
    if (
        metadata.get("curated") is True
        and isinstance(routing, dict)
        and routing.get("provenance") in {
            "curated_explicit",
            "curated_legacy",
        }
    ):
        payload["pinned_document"] = values.get("recommended_resume", "")
    return stable_hash(json.dumps(payload, sort_keys=True), 32)


def _opportunity_content(item: Opportunity) -> Dict[str, Any]:
    return {
        **{field: getattr(item, field) for field in SOURCE_HASH_FIELDS},
        "metadata": item.metadata,
        "recommended_resume": item.recommended_resume,
    }


def source_snapshot_key(items: Iterable[Opportunity]) -> str:
    # Hash what the collector actually produces. Reordering a feed and changes
    # to unconsumed parts of a response do not change its normalized content.
    entries = sorted(
        (item.external_id, _source_content_hash(_opportunity_content(item)))
        for item in items
    )
    return stable_hash(json.dumps(entries, separators=(",", ":")), 32)


def _preserve_listing_dates(item: Opportunity, existing: sqlite3.Row) -> None:
    """Keep authoritative dates and their provenance across sparse feed updates."""

    try:
        previous_metadata = json.loads(existing["metadata_json"])
    except (TypeError, ValueError, json.JSONDecodeError):
        previous_metadata = {}
    previous_dates = (
        previous_metadata.get("dates", {})
        if isinstance(previous_metadata, dict)
        and isinstance(previous_metadata.get("dates"), dict)
        else {}
    )
    metadata = dict(item.metadata) if isinstance(item.metadata, dict) else {}
    dates = dict(metadata.get("dates", {})) if isinstance(metadata.get("dates"), dict) else {}

    for attribute, key in (("posted_at", "posted"), ("deadline_at", "deadline")):
        previous_value = existing[attribute]
        if getattr(item, attribute) is not None or previous_value in (None, ""):
            continue
        setattr(item, attribute, previous_value)
        previous_evidence = previous_dates.get(key)
        if isinstance(previous_evidence, dict) and previous_evidence.get("value"):
            dates[key] = previous_evidence
        else:
            dates[key] = {
                "state": "present" if key == "posted" else "date",
                "kind": "posted" if key == "posted" else "deadline",
                "value": previous_value,
                "provenance": "database.preserved",
                "confidence": "medium",
            }

    if dates:
        metadata["dates"] = dates
        item.metadata = metadata


def _without_query_key(url: str, excluded_key: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    query = [
        (key, value)
        for key, value in urllib.parse.parse_qsl(
            parsed.query,
            keep_blank_values=True,
        )
        if key != excluded_key
    ]
    return urllib.parse.urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urllib.parse.urlencode(query),
            parsed.fragment,
        )
    )


def _merged_user_state(rows: Iterable[sqlite3.Row]) -> Dict[str, Any]:
    """Merge duplicate identity rows without weakening deliberate user state."""

    records = list(rows)
    strongest = max(
        records,
        key=lambda row: (
            WORKFLOW_STATUS_RANK.get(str(row["status"]), -1),
            str(row["status_updated_at"] or ""),
            str(row["id"]),
        ),
    )
    first_seen_at = min(str(row["first_seen_at"]) for row in records)
    applied_values = sorted(
        str(row["applied_at"])
        for row in records
        if row["applied_at"] not in (None, "")
    )
    return {
        "first_seen_at": first_seen_at,
        "status": strongest["status"],
        "status_updated_at": strongest["status_updated_at"],
        "applied_at": applied_values[0] if applied_values else None,
        "bookmarked": int(any(bool(row["bookmarked"]) for row in records)),
    }


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(path), timeout=15)
        os.chmod(path, 0o600)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA busy_timeout = 15000")

    def close(self) -> None:
        self.connection.close()

    def initialize(self) -> None:
        previous_version = int(self.connection.execute("PRAGMA user_version").fetchone()[0])
        if previous_version > SCHEMA_VERSION:
            raise RuntimeError(
                "Database schema version {} is newer than this build supports ({})".format(
                    previous_version, SCHEMA_VERSION
                )
            )
        self.connection.executescript(SCHEMA)
        with self.transaction() as connection:
            source_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(sources)").fetchall()
            }
            source_additions = {
                "pending_content_hash": "TEXT NOT NULL DEFAULT ''",
                "pending_content_checks": "INTEGER NOT NULL DEFAULT 0",
            }
            for name, declaration in source_additions.items():
                if name not in source_columns:
                    connection.execute(
                        "ALTER TABLE sources ADD COLUMN {} {}".format(
                            name, declaration
                        )
                    )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(opportunities)").fetchall()
            }
            additions = {
                "status_updated_at": "TEXT",
                "applied_at": "TEXT",
                "bookmarked": "INTEGER NOT NULL DEFAULT 0",
            }
            for name, declaration in additions.items():
                if name not in columns:
                    connection.execute(
                        "ALTER TABLE opportunities ADD COLUMN {} {}".format(
                            name, declaration
                        )
                    )
            if previous_version < SOURCE_HASH_SCHEMA_VERSION:
                last_identifier = ""
                selected = ", ".join(
                    ("id",)
                    + SOURCE_HASH_FIELDS
                    + ("metadata_json", "recommended_resume")
                )
                while True:
                    rows = connection.execute(
                        "SELECT {} FROM opportunities WHERE id>? ORDER BY id LIMIT 250".format(
                            selected
                        ),
                        (last_identifier,),
                    ).fetchall()
                    if not rows:
                        break
                    connection.executemany(
                        "UPDATE opportunities SET raw_hash=? WHERE id=?",
                        [
                            (_source_content_hash(dict(row)), row["id"])
                            for row in rows
                        ],
                    )
                    last_identifier = rows[-1]["id"]
            connection.execute("PRAGMA user_version = {}".format(SCHEMA_VERSION))

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        nested = self.connection.in_transaction
        savepoint = "opportunity_radar_nested"
        if nested:
            self.connection.execute("SAVEPOINT {}".format(savepoint))
        else:
            self.connection.execute("BEGIN")
        try:
            yield self.connection
            if nested:
                self.connection.execute("RELEASE SAVEPOINT {}".format(savepoint))
            else:
                self.connection.commit()
        except BaseException:
            if nested:
                self.connection.execute("ROLLBACK TO SAVEPOINT {}".format(savepoint))
                self.connection.execute("RELEASE SAVEPOINT {}".format(savepoint))
            else:
                self.connection.rollback()
            raise

    def sync_source(self, source: Dict[str, Any]) -> None:
        url = source.get("url") or source.get("api_url") or ""
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO sources(id, name, kind, url, category, cadence_hours, enabled)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    kind=excluded.kind,
                    url=excluded.url,
                    category=excluded.category,
                    cadence_hours=excluded.cadence_hours,
                    enabled=excluded.enabled
                """,
                (
                    source["id"],
                    source["name"],
                    source["kind"],
                    url,
                    source.get("category", ""),
                    int(source.get("cadence_hours", 12)),
                    int(source.get("enabled", True)),
                ),
            )

    def reconcile_sources(self, current_ids: Iterable[str]) -> None:
        identifiers = set(current_ids)
        with self.transaction() as connection:
            enabled = connection.execute("SELECT id FROM sources WHERE enabled=1").fetchall()
            connection.executemany(
                "UPDATE sources SET enabled=0 WHERE id=?",
                ((row["id"],) for row in enabled if row["id"] not in identifiers),
            )

    def source_due(self, source_id: str, force: bool = False) -> bool:
        if force:
            return True
        row = self.connection.execute(
            "SELECT last_checked_at, cadence_hours FROM sources WHERE id = ?",
            (source_id,),
        ).fetchone()
        if not row or not row["last_checked_at"]:
            return True
        last_check = datetime.fromisoformat(row["last_checked_at"])
        return datetime.now(timezone.utc) - last_check >= timedelta(hours=row["cadence_hours"])

    def sources_due(
        self, sources: Iterable[Dict[str, Any]], *, force: bool = False
    ) -> List[Dict[str, Any]]:
        """Prioritize sources never checked, then those waiting longest past cadence."""
        checks = {
            row["id"]: row
            for row in self.connection.execute(
                "SELECT id, last_checked_at, cadence_hours FROM sources WHERE enabled=1"
            )
        }
        due = []
        now = datetime.now(timezone.utc)
        for source in sources:
            check = checks.get(source["id"])
            if check is None or not check["last_checked_at"]:
                packs = set(source.get("packs", []))
                if packs.intersection(("professional-events", "funding-opportunities")):
                    priority = -2.0
                elif packs.intersection(("multi-employer", "starter-diverse", "custom-sources")):
                    priority = -1.0
                else:
                    priority = 0.0
                due.append((priority, source))
                continue
            last_check = datetime.fromisoformat(check["last_checked_at"])
            if last_check.tzinfo is None:
                last_check = last_check.replace(tzinfo=timezone.utc)
            next_check = last_check + timedelta(hours=check["cadence_hours"])
            if force or next_check <= now:
                due.append(((last_check if force else next_check).timestamp(), source))
        return [source for _, source in sorted(due, key=lambda entry: entry[0])]

    def source_hash(self, source_id: str) -> str:
        row = self.connection.execute(
            "SELECT last_content_hash FROM sources WHERE id = ?", (source_id,)
        ).fetchone()
        return row["last_content_hash"] if row else ""

    def source_status(self, source_id: str) -> str:
        row = self.connection.execute(
            "SELECT last_status FROM sources WHERE id = ?", (source_id,)
        ).fetchone()
        return row["last_status"] if row else "never"

    def source_success(
        self,
        source_id: str,
        content_hash: str,
        count: int,
        *,
        snapshot_key: Optional[str] = None,
        detail_offset: Optional[int] = None,
    ) -> None:
        now = utc_now()
        with self.transaction() as connection:
            connection.execute(
                """
                UPDATE sources SET last_checked_at=?, last_success_at=?, last_status='ok',
                    last_error='', item_count=?, last_content_hash=?,
                    pending_content_hash='', pending_content_checks=0
                WHERE id=?
                """,
                (now, now, count, content_hash, source_id),
            )
            state_key = SOURCE_SNAPSHOT_KEY_PREFIX + source_id
            if snapshot_key is None:
                connection.execute("DELETE FROM runtime_state WHERE key=?", (state_key,))
            else:
                connection.execute(
                    "INSERT INTO runtime_state(key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (state_key, snapshot_key),
                )
            if detail_offset is not None:
                connection.execute(
                    "INSERT INTO runtime_state(key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (DETAIL_OFFSET_KEY_PREFIX + source_id, str(detail_offset)),
                )

    def source_detail_offset(self, source_id: str) -> int:
        row = self.connection.execute(
            "SELECT value FROM runtime_state WHERE key=?", (DETAIL_OFFSET_KEY_PREFIX + source_id,)
        ).fetchone()
        return int(row["value"]) if row else 0

    def refresh_unchanged_snapshot(
        self, source_id: str, snapshot_key: str, identifiers: set[str], seen_at: str
    ) -> bool:
        """Touch known unchanged rows only when the complete membership still exists."""
        state = self.connection.execute(
            "SELECT value FROM runtime_state WHERE key=?",
            (SOURCE_SNAPSHOT_KEY_PREFIX + source_id,),
        ).fetchone()
        if state is None or state["value"] != snapshot_key:
            return False
        active_ids = {
            row["external_id"]
            for row in self.connection.execute(
                "SELECT external_id FROM opportunities WHERE source_id=? AND active=1",
                (source_id,),
            )
        }
        if active_ids != identifiers:
            return False
        self.connection.execute(
            "UPDATE opportunities SET last_seen_at=? WHERE source_id=? AND active=1 AND last_seen_at != ?",
            (seen_at, source_id, seen_at),
        )
        return True

    def source_watch_success(
        self,
        source_id: str,
        content_hash: str,
        count: int,
        title: str,
        url: str,
        confirmations: int = 2,
    ) -> bool:
        """Confirm a stable watch-page change before creating an event."""

        required = max(1, min(int(confirmations), 3))
        now = utc_now()
        with self.transaction() as connection:
            source = connection.execute(
                """
                SELECT last_content_hash, pending_content_hash,
                       pending_content_checks
                FROM sources WHERE id=?
                """,
                (source_id,),
            ).fetchone()
            if source is None:
                return False
            baseline = str(source["last_content_hash"] or "")
            pending = str(source["pending_content_hash"] or "")
            checks = int(source["pending_content_checks"] or 0)
            if not baseline or content_hash == baseline:
                connection.execute(
                    """
                    UPDATE sources SET last_checked_at=?, last_success_at=?,
                        last_status='ok', last_error='', item_count=?,
                        last_content_hash=?, pending_content_hash='',
                        pending_content_checks=0
                    WHERE id=?
                    """,
                    (now, now, count, content_hash, source_id),
                )
                return False

            next_checks = checks + 1 if pending == content_hash else 1
            if next_checks < required:
                connection.execute(
                    """
                    UPDATE sources SET last_checked_at=?, last_success_at=?,
                        last_status='ok', last_error='', item_count=?,
                        pending_content_hash=?, pending_content_checks=?
                    WHERE id=?
                    """,
                    (now, now, count, content_hash, next_checks, source_id),
                )
                return False

            cursor = connection.execute(
                """
                INSERT INTO source_events(
                    source_id, event_type, occurred_at, previous_hash,
                    content_hash, title, url
                ) VALUES (?, 'page_changed', ?, ?, ?, ?, ?)
                """,
                (source_id, now, baseline, content_hash, title[:240], url[:2000]),
            )
            connection.execute(
                """
                UPDATE sources SET last_checked_at=?, last_success_at=?,
                    last_status='ok', last_error='', item_count=?,
                    last_content_hash=?, pending_content_hash='',
                    pending_content_checks=0
                WHERE id=?
                """,
                (now, now, count, content_hash, source_id),
            )
        return cursor.rowcount > 0

    def source_error(self, source_id: str, error: str) -> None:
        with self.transaction() as connection:
            connection.execute(
                """
                UPDATE sources SET last_checked_at=?, last_status='error', last_error=?
                WHERE id=?
                """,
                (utc_now(), error[:800], source_id),
            )

    def source_blocked(self, source_id: str, message: str) -> None:
        with self.transaction() as connection:
            connection.execute(
                """
                UPDATE sources SET last_checked_at=?, last_status='blocked', last_error=?
                WHERE id=?
                """,
                (utc_now(), message[:800], source_id),
            )

    def source_change_success(
        self,
        source_id: str,
        previous_hash: str,
        content_hash: str,
        count: int,
        title: str,
        url: str,
    ) -> bool:
        now = utc_now()
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                INSERT INTO source_events(
                    source_id, event_type, occurred_at, previous_hash,
                    content_hash, title, url
                ) VALUES (?, 'page_changed', ?, ?, ?, ?, ?)
                """,
                (source_id, now, previous_hash, content_hash, title[:240], url[:2000]),
            )
            connection.execute(
                """
                UPDATE sources SET last_checked_at=?, last_success_at=?, last_status='ok',
                    last_error='', item_count=?, last_content_hash=?,
                    pending_content_hash='', pending_content_checks=0
                WHERE id=?
                """,
                (now, now, count, content_hash, source_id),
            )
        return cursor.rowcount > 0

    def _legacy_paginated_aliases(
        self,
        item: Opportunity,
        excluded_id: Optional[str] = None,
    ) -> List[sqlite3.Row]:
        """Find only bounded HTML aliases that normalize to this exact listing URL."""

        if not item.url:
            return []
        parsed_url = urllib.parse.urlsplit(item.url)
        if any(
            key == "page"
            for key, _value in urllib.parse.parse_qsl(
                parsed_url.query,
                keep_blank_values=True,
            )
        ):
            return []
        base_url = urllib.parse.urlunsplit(
            (parsed_url.scheme, parsed_url.netloc, parsed_url.path, "", "")
        )
        escaped_base = (
            base_url.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        exclusion = "AND opportunities.id != ?" if excluded_id else ""
        parameters = [item.source_id]
        if excluded_id:
            parameters.append(excluded_id)
        parameters.append(escaped_base + "%")
        candidates = self.connection.execute(
            """
            SELECT opportunities.id, opportunities.raw_hash,
                   opportunities.posted_at, opportunities.deadline_at,
                   opportunities.metadata_json, opportunities.url,
                   opportunities.status, opportunities.status_updated_at,
                   opportunities.applied_at, opportunities.bookmarked,
                   opportunities.first_seen_at, opportunities.last_seen_at,
                   opportunities.active
            FROM opportunities
            JOIN sources ON sources.id = opportunities.source_id
            WHERE opportunities.source_id=?
              AND sources.kind='html_links'
              {}
              AND opportunities.url LIKE ? ESCAPE '\\'
            ORDER BY opportunities.active DESC,
                     opportunities.first_seen_at ASC
            LIMIT 100
            """.format(exclusion),
            parameters,
        ).fetchall()
        return [
            candidate
            for candidate in candidates
            if any(
                key == "page"
                for key, _value in urllib.parse.parse_qsl(
                    urllib.parse.urlsplit(candidate["url"]).query,
                    keep_blank_values=True,
                )
            )
            and _without_query_key(candidate["url"], "page") == item.url
        ]

    def upsert_opportunity(
        self,
        item: Opportunity,
        seen_at: Optional[str] = None,
        *,
        score: Optional[Callable[[Opportunity], Opportunity]] = None,
    ) -> str:
        now = seen_at or utc_now()
        identifier = stable_hash("{}:{}".format(item.source_id, item.external_id), 24)
        existing = self.connection.execute(
            """
            SELECT id, raw_hash, posted_at, deadline_at, metadata_json,
                   status, status_updated_at, applied_at, bookmarked,
                   first_seen_at, last_seen_at, active, url
            FROM opportunities
            WHERE source_id=? AND external_id=?
            """,
            (item.source_id, item.external_id),
        ).fetchone()
        legacy_identity = False
        aliases = self._legacy_paginated_aliases(
            item,
            existing["id"] if existing is not None else None,
        )
        if existing is None and aliases:
            existing = aliases.pop(0)
            legacy_identity = True
        if existing is not None:
            _preserve_listing_dates(item, existing)
            if not item.description and item.metadata.get("detail_status") in ("failed", "deferred"):
                cached = self.connection.execute(
                    "SELECT description FROM opportunities WHERE id=?", (existing["id"],)
                ).fetchone()
                if cached["description"]:
                    item.description = cached["description"]
                    item.metadata["description_availability"] = "full"
                    item.metadata["detail_status"] = "cached"
            for alias in aliases:
                if item.posted_at is not None and item.deadline_at is not None:
                    break
                _preserve_listing_dates(item, alias)
        merged_state = _merged_user_state([existing] + aliases) if aliases else None
        content = _opportunity_content(item)
        raw_hash = _source_content_hash(content)
        same_content = existing is not None and existing["raw_hash"] == raw_hash
        if existing is not None and not same_content and score is not None:
            # Older databases included the derived eligibility state. Recognize
            # and repair that hash lazily without a whole-database rewrite.
            previous_metadata = json.loads(existing["metadata_json"])
            if isinstance(previous_metadata, dict) and "eligibility_state" in previous_metadata:
                legacy_content = dict(
                    content,
                    metadata=dict(item.metadata, eligibility_state=previous_metadata["eligibility_state"]),
                )
                same_content = existing["raw_hash"] == _source_content_hash(legacy_content, legacy=True)
        if score is not None and same_content and existing["active"] and not (legacy_identity or aliases):
            if existing["last_seen_at"] != now or existing["raw_hash"] != raw_hash:
                with self.transaction() as connection:
                    connection.execute(
                        "UPDATE opportunities SET last_seen_at=?, raw_hash=? WHERE id=?",
                        (now, raw_hash, existing["id"]),
                    )
            return "unchanged"
        if score is not None:
            score(item)
            raw_hash = _source_content_hash(_opportunity_content(item))
        result = "new" if existing is None else ("unchanged" if same_content else "updated")
        with self.transaction() as connection:
            if score is None:
                connection.execute(
                    "DELETE FROM runtime_state WHERE key=?", (SOURCE_SNAPSHOT_KEY_PREFIX + item.source_id,)
                )
            if legacy_identity:
                connection.execute(
                    "UPDATE opportunities SET external_id=? WHERE id=?",
                    (item.external_id, existing["id"]),
                )
            connection.execute(
                """
                INSERT INTO opportunities(
                    id, source_id, external_id, title, organization, location, url,
                    description, category, opportunity_type, posted_at, deadline_at,
                    first_seen_at, last_seen_at, score, tier, reasons_json, warnings_json,
                    recommended_resume, commitment, eligibility, metadata_json, raw_hash, active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(source_id, external_id) DO UPDATE SET
                    title=excluded.title,
                    organization=excluded.organization,
                    location=excluded.location,
                    url=excluded.url,
                    description=excluded.description,
                    category=excluded.category,
                    opportunity_type=excluded.opportunity_type,
                    posted_at=COALESCE(excluded.posted_at, opportunities.posted_at),
                    deadline_at=COALESCE(excluded.deadline_at, opportunities.deadline_at),
                    last_seen_at=excluded.last_seen_at,
                    score=excluded.score,
                    tier=excluded.tier,
                    reasons_json=excluded.reasons_json,
                    warnings_json=excluded.warnings_json,
                    recommended_resume=excluded.recommended_resume,
                    commitment=excluded.commitment,
                    eligibility=excluded.eligibility,
                    metadata_json=excluded.metadata_json,
                    raw_hash=excluded.raw_hash,
                    active=1
                """,
                (
                    identifier,
                    item.source_id,
                    item.external_id,
                    item.title,
                    item.organization,
                    item.location,
                    item.url,
                    item.description,
                    item.category,
                    item.opportunity_type,
                    item.posted_at,
                    item.deadline_at,
                    now,
                    now,
                    item.score,
                    item.tier,
                    json.dumps(item.reasons),
                    json.dumps(item.warnings),
                    item.recommended_resume,
                    item.commitment,
                    item.eligibility,
                    json.dumps(item.metadata, sort_keys=True),
                    raw_hash,
                ),
            )
            if merged_state is not None:
                canonical_id = existing["id"] if existing is not None else identifier
                connection.execute(
                    """
                    UPDATE opportunities
                    SET first_seen_at=?, status=?, status_updated_at=?,
                        applied_at=?, bookmarked=?
                    WHERE id=?
                    """,
                    (
                        merged_state["first_seen_at"],
                        merged_state["status"],
                        merged_state["status_updated_at"],
                        merged_state["applied_at"],
                        merged_state["bookmarked"],
                        canonical_id,
                    ),
                )
                placeholders = ",".join("?" for _alias in aliases)
                connection.execute(
                    "DELETE FROM opportunities WHERE id IN ({})".format(placeholders),
                    [alias["id"] for alias in aliases],
                )
        return result

    def rescore_for_profile(self, profile: Dict[str, Any]) -> Dict[str, Any]:
        """Refresh profile-derived fields once for each effective profile version."""
        fingerprint = profile_fingerprint(profile)
        rescored = 0
        with self.transaction() as connection:
            state = connection.execute(
                "SELECT value FROM runtime_state WHERE key=?",
                (PROFILE_FINGERPRINT_KEY,),
            ).fetchone()
            if state is not None and state["value"] == fingerprint:
                return {"changed": False, "rescored": 0, "fingerprint": fingerprint}

            last_identifier = ""
            while True:
                rows = connection.execute(
                    """
                    SELECT * FROM opportunities
                    WHERE id>?
                      AND (active=1 OR bookmarked=1 OR status IN ('apply', 'applied', 'skip'))
                    ORDER BY id LIMIT 250
                    """,
                    (last_identifier,),
                ).fetchall()
                if not rows:
                    break
                for row in rows:
                    metadata = json.loads(row["metadata_json"])
                    if not isinstance(metadata, dict):
                        metadata = {}
                    document_routing = metadata.get("document_routing")
                    if (
                        not isinstance(document_routing, dict)
                        and metadata.get("curated") is True
                        and str(row["recommended_resume"] or "").strip()
                    ):
                        metadata["document_routing"] = {
                            "provenance": LEGACY_CURATED_DOCUMENT_PROVENANCE
                        }
                    item = Opportunity(
                        source_id=row["source_id"],
                        external_id=row["external_id"],
                        title=row["title"],
                        organization=row["organization"],
                        url=row["url"],
                        location=row["location"],
                        description=row["description"],
                        category=row["category"],
                        opportunity_type=row["opportunity_type"],
                        posted_at=row["posted_at"],
                        deadline_at=row["deadline_at"],
                        recommended_resume=row["recommended_resume"],
                        commitment=row["commitment"],
                        eligibility=row["eligibility"],
                        metadata=metadata,
                    )
                    score_opportunity(item, profile)
                    connection.execute(
                        """
                        UPDATE opportunities
                        SET score=?, tier=?, reasons_json=?, warnings_json=?,
                            recommended_resume=?, metadata_json=?
                        WHERE id=?
                        """,
                        (
                            item.score,
                            item.tier,
                            json.dumps(item.reasons),
                            json.dumps(item.warnings),
                            item.recommended_resume,
                            json.dumps(item.metadata, sort_keys=True),
                            row["id"],
                        ),
                    )
                    rescored += 1
                last_identifier = rows[-1]["id"]
            connection.execute(
                """
                INSERT INTO runtime_state(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (PROFILE_FINGERPRINT_KEY, fingerprint),
            )
        return {"changed": True, "rescored": rescored, "fingerprint": fingerprint}

    def mark_source_stale(self, source_id: str, current_external_ids: Iterable[str]) -> None:
        identifiers = set(current_external_ids)
        with self.transaction() as connection:
            if not identifiers:
                connection.execute(
                    "UPDATE opportunities SET active=0 WHERE source_id=? AND active=1", (source_id,)
                )
                return
            active = connection.execute(
                "SELECT id, external_id FROM opportunities WHERE source_id=? AND active=1",
                (source_id,),
            ).fetchall()
            connection.executemany(
                "UPDATE opportunities SET active=0 WHERE id=?",
                ((row["id"],) for row in active if row["external_id"] not in identifiers),
            )

    def archive_rolling_source_history(self, source_id: str, seen_at: str) -> None:
        """Bound the local discovery window without treating absence as closure."""
        cutoff = (datetime.fromisoformat(seen_at) - timedelta(days=ROLLING_ARCHIVE_DAYS)).isoformat()
        with self.transaction() as connection:
            connection.execute(
                "UPDATE opportunities SET active=0 "
                "WHERE source_id=? AND active=1 AND last_seen_at < ?",
                (source_id, cutoff),
            )

    def begin_run(self) -> Tuple[int, str]:
        started = utc_now()
        with self.transaction() as connection:
            cursor = connection.execute("INSERT INTO runs(started_at) VALUES (?)", (started,))
        return int(cursor.lastrowid), started

    def finish_run(
        self,
        run_id: int,
        started_at: str,
        status: str,
        checked: int,
        new_count: int,
        updated_count: int,
        error_count: int,
        notes: str = "",
        *,
        collection: Optional[Dict[str, int]] = None,
    ) -> None:
        started = datetime.fromisoformat(started_at)
        finished = datetime.now(timezone.utc).replace(microsecond=0)
        duration_ms = max(0, int((finished - started).total_seconds() * 1000))
        with self.transaction() as connection:
            connection.execute(
                """
                UPDATE runs SET finished_at=?, status=?, checked_sources=?, new_count=?,
                    updated_count=?, error_count=?, duration_ms=?, notes=? WHERE id=?
                """,
                (
                    finished.isoformat(),
                    status,
                    checked,
                    new_count,
                    updated_count,
                    error_count,
                    duration_ms,
                    notes[:2000],
                    run_id,
                ),
            )
            if collection is not None:
                connection.execute(
                    "INSERT INTO runtime_state(key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (COLLECTION_SUMMARY_KEY, json.dumps(collection)),
                )

    def collection_summary(self) -> Dict[str, int]:
        row = self.connection.execute(
            "SELECT value FROM runtime_state WHERE key=?", (COLLECTION_SUMMARY_KEY,)
        ).fetchone()
        return json.loads(row["value"]) if row else {}

    def dashboard_payload(self) -> Dict[str, Any]:
        tracked_rows = self.connection.execute(
            """
            SELECT opportunities.*, sources.enabled AS source_enabled,
                   sources.name AS source_name, sources.url AS source_url
            FROM opportunities
            JOIN sources ON sources.id = opportunities.source_id
            WHERE opportunities.status IN ('apply', 'applied', 'skip')
            ORDER BY score DESC, COALESCE(deadline_at, '9999') ASC, first_seen_at DESC, opportunities.id
            """
        ).fetchall()
        bookmarked_rows = self.connection.execute(
            """
            SELECT opportunities.*, sources.enabled AS source_enabled,
                   sources.name AS source_name, sources.url AS source_url
            FROM opportunities
            JOIN sources ON sources.id = opportunities.source_id
            WHERE opportunities.bookmarked=1
              AND opportunities.status NOT IN ('apply', 'applied', 'skip')
            ORDER BY score DESC, COALESCE(deadline_at, '9999') ASC, first_seen_at DESC, opportunities.id
            """
        ).fetchall()
        discovery_rows = self.connection.execute(
            """
            SELECT opportunities.*, sources.enabled AS source_enabled,
                   sources.name AS source_name, sources.url AS source_url
            FROM opportunities
            JOIN sources ON sources.id = opportunities.source_id
            WHERE opportunities.active=1
              AND opportunities.tier != 'skip'
              AND opportunities.status NOT IN ('apply', 'applied', 'skip')
              AND opportunities.bookmarked=0
              AND sources.enabled=1
            ORDER BY score DESC, COALESCE(deadline_at, '9999') ASC, first_seen_at DESC, opportunities.id
            LIMIT ?
            """,
            (MAX_DASHBOARD_DISCOVERY_ITEMS,),
        ).fetchall()
        discovery_total = int(
            self.connection.execute(
                """
                SELECT COUNT(*) FROM opportunities
                JOIN sources ON sources.id = opportunities.source_id
                WHERE opportunities.active=1
                  AND opportunities.tier != 'skip'
                  AND opportunities.status NOT IN ('apply', 'applied', 'skip')
                  AND sources.enabled=1
                """
            ).fetchone()[0]
        )
        today = date.today()
        opportunities = [
            self._dashboard_row(row, today)
            for row in tracked_rows + bookmarked_rows + discovery_rows
        ]
        detailed_ids = {item["id"] for item in opportunities}
        catalog = []
        for row in self.connection.execute(
            """
            SELECT opportunities.id, title, organization, location, opportunities.url,
                   SUBSTR(description, 1, 400) AS description,
                   LENGTH(description) AS description_length,
                   opportunities.category, opportunity_type, posted_at, deadline_at, first_seen_at,
                   status, bookmarked, score, tier, active, source_id,
                   sources.enabled AS source_enabled, sources.name AS source_name,
                   sources.url AS source_url,
                   CASE WHEN sources.kind IN (
                       'arbeitnow', 'grants_gov', 'html_links', 'json_ld', 'public_page',
                       'smartrecruiters', 'syndication', 'watch_page', 'workday'
                   ) THEN metadata_json ELSE '{}' END AS evidence_metadata
            FROM opportunities
            JOIN sources ON sources.id = opportunities.source_id
            WHERE opportunities.active=1
              AND opportunities.status NOT IN ('apply', 'applied', 'skip')
              AND opportunities.bookmarked=0
              AND sources.enabled=1
            ORDER BY score DESC, COALESCE(deadline_at, '9999') ASC,
                     first_seen_at DESC, opportunities.id
            LIMIT ?
            """,
            (MAX_DASHBOARD_CATALOG_ITEMS + len(discovery_rows),),
        ):
            item = dict(row)
            if item["id"] in detailed_ids:
                continue
            if len(catalog) >= MAX_DASHBOARD_CATALOG_ITEMS:
                break
            item["metadata"] = json.loads(item.pop("evidence_metadata"))
            item["facts"] = opportunity_facts(item, compact=True, today=today)
            item.pop("metadata")
            item.pop("source_url")
            item.pop("description_length")
            item["detail_available"] = False
            catalog.append(item)
        available_bookmarks = sum(
            bool(row["active"])
            and row["tier"] != "skip"
            and bool(row["source_enabled"])
            for row in bookmarked_rows
        )
        sources = [
            dict(row)
            for row in self.connection.execute(
                """
                SELECT id, name, kind, category, url, cadence_hours, enabled,
                       last_checked_at, last_success_at, last_status, last_error,
                       item_count
                FROM sources
                WHERE enabled=1
                ORDER BY name
                """
            ).fetchall()
        ]
        runs = [
            dict(row)
            for row in self.connection.execute(
                """
                SELECT id, started_at, finished_at, status, checked_sources,
                       new_count, updated_count, error_count, duration_ms
                FROM runs
                ORDER BY id DESC
                LIMIT 12
                """
            ).fetchall()
        ]
        events = [
            dict(row)
            for row in self.connection.execute(
                """
                SELECT source_events.id, source_events.source_id, source_events.event_type,
                       source_events.occurred_at, source_events.title, source_events.url,
                       sources.name AS source_name
                FROM source_events
                JOIN sources ON sources.id = source_events.source_id
                ORDER BY source_events.id DESC
                LIMIT 30
                """
            ).fetchall()
        ]
        counts = dict(
            self.connection.execute(
                """
                SELECT SUM(CASE WHEN active=1 AND sources.enabled=1 THEN 1 ELSE 0 END) AS collected,
                       SUM(CASE WHEN active=1 AND tier != 'skip' AND sources.enabled=1
                                THEN 1 ELSE 0 END) AS active,
                       SUM(CASE WHEN active=1 AND tier='priority' AND sources.enabled=1
                                THEN 1 ELSE 0 END) AS priority,
                       SUM(CASE WHEN active=1 AND status='new' AND tier != 'skip'
                                     AND sources.enabled=1 THEN 1 ELSE 0 END) AS new,
                       SUM(CASE WHEN status='apply' THEN 1 ELSE 0 END) AS applying,
                       SUM(CASE WHEN status='applied' THEN 1 ELSE 0 END) AS applied,
                       SUM(CASE WHEN bookmarked=1 THEN 1 ELSE 0 END) AS bookmarked
                FROM opportunities
                JOIN sources ON sources.id = opportunities.source_id
                """
            ).fetchone()
        )
        return {
            "generated_at": utc_now(),
            "collection": self.collection_summary(),
            "opportunities": opportunities,
            "catalog": catalog,
            "sources": sources,
            "runs": runs,
            "events": events,
            "counts": {key: int(value or 0) for key, value in counts.items()},
            "display": {
                "catalog_total": int(counts["collected"] or 0),
                "catalog_truncated": int(counts["collected"] or 0) > (
                    len(catalog)
                    + sum(bool(item["active"] and item["source_enabled"]) for item in opportunities)
                ),
                "discovery_limit": MAX_DASHBOARD_DISCOVERY_ITEMS,
                "discovery_total": discovery_total,
                "discovery_truncated": discovery_total
                > len(discovery_rows) + available_bookmarks,
            },
        }

    def _decode_row(self, row: sqlite3.Row) -> Dict[str, Any]:
        item = dict(row)
        item["reasons"] = json.loads(item.pop("reasons_json"))
        item["warnings"] = json.loads(item.pop("warnings_json"))
        item["metadata"] = json.loads(item.pop("metadata_json"))
        return item

    def _dashboard_row(self, row: sqlite3.Row, today: date) -> Dict[str, Any]:
        item = self._decode_row(row)
        facts = opportunity_facts(item, evidence_limit=5000, today=today)
        item["description"] = str(item.get("description", ""))[:1600]
        item["eligibility"] = str(item.get("eligibility", ""))[:800]
        metadata = item.get("metadata", {})
        metadata = metadata if isinstance(metadata, dict) else {}
        match = metadata.get("match", {})
        dates = metadata.get("dates", {})
        allowed = {
            "id",
            "title",
            "organization",
            "location",
            "url",
            "description",
            "category",
            "opportunity_type",
            "posted_at",
            "deadline_at",
            "first_seen_at",
            "status",
            "status_updated_at",
            "applied_at",
            "bookmarked",
            "score",
            "tier",
            "reasons",
            "warnings",
            "recommended_resume",
            "commitment",
            "eligibility",
            "active",
            "source_enabled",
        }
        output = {key: value for key, value in item.items() if key in allowed}
        output["facts"] = facts
        output["match"] = match if isinstance(match, dict) else {}
        output["dates"] = dates if isinstance(dates, dict) else {}
        return output

    def set_status(self, opportunity_id: str, status: str) -> bool:
        if status not in {"new", "reviewed", "apply", "applied", "skip"}:
            raise ValueError("Unsupported status: {}".format(status))
        now = utc_now()
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE opportunities
                SET status=?, status_updated_at=?,
                    applied_at=CASE
                        WHEN ?='applied' THEN COALESCE(applied_at, ?)
                        ELSE applied_at
                    END
                WHERE id=?
                """,
                (status, now, status, now, opportunity_id),
            )
        return cursor.rowcount > 0

    def set_bookmarked(self, opportunity_id: str, bookmarked: bool) -> bool:
        with self.transaction() as connection:
            cursor = connection.execute(
                "UPDATE opportunities SET bookmarked=? WHERE id=?",
                (1 if bookmarked else 0, opportunity_id),
            )
        return cursor.rowcount > 0

    def prune_history(
        self,
        retention_days: int = 365,
        max_runs: int = 200,
        max_source_events: int = 500,
    ) -> Dict[str, int]:
        """Bound inactive discovery history while preserving user workflow state."""
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=max(30, retention_days))
        ).replace(microsecond=0).isoformat()
        with self.transaction() as connection:
            opportunities = connection.execute(
                """
                DELETE FROM opportunities
                WHERE active=0
                  AND status NOT IN ('apply', 'applied', 'skip')
                  AND bookmarked=0
                  AND last_seen_at < ?
                """,
                (cutoff,),
            ).rowcount
            runs = connection.execute(
                """
                DELETE FROM runs
                WHERE id NOT IN (SELECT id FROM runs ORDER BY id DESC LIMIT ?)
                """,
                (max(1, max_runs),),
            ).rowcount
            events = connection.execute(
                """
                DELETE FROM source_events
                WHERE id NOT IN (
                    SELECT id FROM source_events ORDER BY id DESC LIMIT ?
                )
                """,
                (max(1, max_source_events),),
            ).rowcount
        return {
            "opportunities": max(0, opportunities),
            "runs": max(0, runs),
            "source_events": max(0, events),
        }

    def optimize(self) -> None:
        self.connection.execute("PRAGMA optimize")
