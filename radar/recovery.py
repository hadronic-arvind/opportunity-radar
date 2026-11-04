"""Offline SQLite backups, validated recovery, and a read-only legacy import."""

import json
import os
import sqlite3
import stat
import tempfile
import time
from contextlib import closing, contextmanager
from functools import lru_cache
from pathlib import Path

from .collection import _public_url, load_catalog, validate_source
from .locking import exclusive_work
from .matching import tokens
from .profiles import clear_pristine_starters
from .store import APPLICATION_ID, SCHEMA_VERSION, safe_url, schema, upgrade_schema

LEGACY_VERSIONS = range(3, 8)
MAX_DATABASE_BYTES = 4 * 1024**3
MAX_CONFIG_BYTES = 16 * 1024**2
MAX_RECOVERY_SECONDS = 120
# Python 3.9 returns SQLite's C result codes without exposing the constants.
SQLITE_BUSY = 5
SQLITE_LOCKED = 6
SQLITE_DONE = 101
CONFIG_FILES = (
    "profile.json",
    "sources.json",
    "profile.local.json",
    "sources.local.json",
    "profiles.local.json",
)
LEGACY_STATUSES = {"new", "reviewed", "apply", "applied", "skip"}


def _private_path(path):
    path = Path(path).expanduser()
    if path.is_symlink():
        raise ValueError("Recovery paths must not be symbolic links")
    return path


def _input_path(path, store):
    path = _private_path(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("Recovery input must be a regular file")
    if info.st_size > MAX_DATABASE_BYTES:
        raise ValueError("Recovery input exceeds the 4 GiB limit")
    if path.resolve() == store.path.resolve() or (
        store.path.exists() and path.samefile(store.path)
    ):
        raise ValueError("Recovery input must be separate from the active workspace")
    if any(path.with_name(path.name + suffix).exists() for suffix in ("-wal", "-journal")):
        raise ValueError("Recovery requires a standalone SQLite backup without sidecar files")
    return path


def _read_database(path):
    connection = sqlite3.connect(
        path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True, timeout=2
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA trusted_schema = OFF")
    connection.execute("PRAGMA query_only = ON")
    return connection


@contextmanager
def _stage(directory):
    descriptor, name = tempfile.mkstemp(prefix=".radar-recovery-", dir=directory)
    os.close(descriptor)
    path = Path(name)
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


def _copy_database(source, destination):
    wait_since = None
    deadline = time.monotonic() + MAX_RECOVERY_SECONDS

    def progress(state, _remaining, _total):
        nonlocal wait_since
        if state != SQLITE_DONE and time.monotonic() >= deadline:
            raise TimeoutError("SQLite copy exceeded its recovery time budget")
        if state not in {SQLITE_BUSY, SQLITE_LOCKED}:
            wait_since = None
            return
        if wait_since is None:
            wait_since = time.monotonic()
        elif time.monotonic() - wait_since >= 2:
            raise TimeoutError("Workspace is busy. Retry when the active operation finishes.")

    destination.execute("PRAGMA busy_timeout = 200")
    source.backup(destination, pages=1024, progress=progress, sleep=0.05)


def _schema(connection):
    return [
        tuple(row)
        for row in connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "ORDER BY type, name"
        )
    ]


def _version(connection):
    return tuple(
        connection.execute(f"PRAGMA {field}").fetchone()[0]
        for field in ("application_id", "user_version")
    )


@lru_cache(maxsize=3)
def _expected_schema(version):
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.executescript(schema(version))
        return _schema(connection)


def _check_snapshot(source):
    app, version = _version(source)
    if (
        app != APPLICATION_ID
        or version not in {1, 2, SCHEMA_VERSION}
        or _schema(source) != _expected_schema(version)
    ):
        raise ValueError("Snapshot schema does not match this Opportunity Radar version")
    if source.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        raise ValueError("Snapshot failed SQLite integrity verification")
    if source.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise ValueError("Snapshot contains invalid references")
    for table, column in (
        ("sources", "config"), ("observations", "data"), ("observation_history", "data"),
        ("goals", "rules"), ("migration_archive", "payload"),
    ):
        if source.execute(
            f"SELECT 1 FROM {table} WHERE NOT json_valid({column}) LIMIT 1"
        ).fetchone() is not None:
            raise ValueError("Snapshot contains malformed workspace records")


def backup(store, destination):
    """Publish a consistent, owner-only backup without replacing an existing file."""
    destination = _private_path(destination)
    if destination.exists():
        raise FileExistsError("A backup already exists at the requested path")
    with _stage(destination.parent) as temporary:
        with closing(store.connect()) as source, closing(sqlite3.connect(temporary)) as copy:
            _copy_database(source, copy)
            _check_snapshot(copy)
        if temporary.stat().st_size > MAX_DATABASE_BYTES:
            raise ValueError("Workspace backup exceeds the 4 GiB limit")
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        os.link(temporary, destination)
    return {"path": str(destination), "bytes": destination.stat().st_size}


def restore(store, snapshot):
    """Verify a staged copy, then restore it as one SQLite transaction."""
    source_path = _input_path(snapshot, store)
    with exclusive_work(store), _stage(store.path.parent) as temporary:
        with closing(_read_database(source_path)) as source, closing(
            sqlite3.connect(temporary)
        ) as staged:
            _copy_database(source, staged)
            staged.execute("PRAGMA trusted_schema = OFF")
            _check_snapshot(staged)
            version = _version(staged)[1]
            if version < SCHEMA_VERSION:
                with staged:
                    staged.execute("BEGIN IMMEDIATE")
                    upgrade_schema(staged, version)
            with closing(store.connect()) as destination:
                _copy_database(staged, destination)
    return {"restored": True}


def _json_document(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError("Legacy configuration must use regular files without symlinks")
    with path.open("rb") as handle:
        content = handle.read(MAX_CONFIG_BYTES + 1)
    if len(content) > MAX_CONFIG_BYTES:
        raise ValueError("Legacy configuration exceeds the 16 MiB per-file limit")
    try:
        payload = json.loads(content)
        if not isinstance(payload, dict):
            raise ValueError
        json.dumps(payload, allow_nan=False)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise ValueError(f"Invalid legacy configuration in {path.name}") from error
    return payload


def _read_config(legacy_db, config_dir):
    directory = Path(config_dir) if config_dir is not None else legacy_db.parent.parent / "config"
    if config_dir is not None and not directory.is_dir():
        raise ValueError("Legacy configuration directory does not exist")
    return {
        name: _json_document(directory / name)
        for name in CONFIG_FILES
        if (directory / name).exists() or (directory / name).is_symlink()
    }


def _profiles(config):
    store = config.get("profiles.local.json")
    if store is None:
        profile = config.get("profile.local.json")
        sources = config.get("sources.local.json", {})
        entries = [{"id": "legacy", "name": "Legacy profile", "profile": profile or {},
                    "sources": sources}] if profile is not None or sources else []
        return entries, "legacy" if entries else None
    entries = store.get("profiles")
    active = store.get("active_profile_id")
    if store.get("schema_version") != 1 or not isinstance(entries, list) or not 1 <= len(entries) <= 32:
        raise ValueError("Legacy named profile store has an unsupported format")
    ids = set()
    names = set()
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("id"), str)
            or not isinstance(entry.get("name"), str)
            or not 1 <= len(entry["name"]) <= 80
            or any(ord(char) < 32 or ord(char) == 127 for char in entry["name"])
            or not isinstance(entry.get("profile"), dict)
            or not isinstance(entry.get("sources"), dict)
            or entry["id"] in ids
            or entry["name"].casefold() in names
        ):
            raise ValueError("Legacy named profile store contains an invalid entry")
        ids.add(entry["id"])
        names.add(entry["name"].casefold())
    if not isinstance(active, str) or active not in ids:
        raise ValueError("Legacy named profile store has no valid active profile")
    return entries, active


def _legacy_rules(profile):
    targets = profile.get("targets", {})
    if not isinstance(targets, dict):
        targets = {}

    def phrases(*keys):
        values = []
        for key in keys:
            terms = targets.get(key, [])
            if isinstance(terms, list):
                values.extend(
                    term.strip().replace("_", " ")
                    for term in terms if isinstance(term, str) and 1 <= len(term.strip()) <= 120
                    and tokens(term)
                )
        return list(dict.fromkeys(values))[:30]

    return {
        "prefer": phrases(
            "role_families", "roles", "domains", "supporting_skills", "skills",
            "locations", "opportunity_types", "work_arrangements",
        ),
        "avoid": phrases("exclusions", "exclude_keywords", "excluded_keywords", "avoid"),
    }


def _source_overrides(config, profile):
    layers = [config.get("sources.json", {}), config.get("sources.local.json", {})]
    if profile is not None:
        layers.append(profile["sources"])
    result = {}
    for layer in layers:
        entries = layer.get("sources", [])
        if not isinstance(entries, list):
            raise ValueError("Legacy configuration has an invalid source list")
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
                raise ValueError("Legacy configuration has an invalid source entry")
            result[entry["id"]] = {**result.get(entry["id"], {}), **entry}
    return result


def _archive(connection, name, value):
    connection.execute(
        "INSERT INTO migration_archive(name, payload) VALUES (?, ?)",
        (name, json.dumps(value, ensure_ascii=False, allow_nan=False)),
    )


def _legacy_shape(connection):
    if (
        _version(connection)[0] != 0
        or _version(connection)[1] not in LEGACY_VERSIONS
    ):
        raise ValueError("This SQLite schema is not a supported legacy Opportunity Radar database")
    tables = {
        row[1]: row[3] or ""
        for row in _schema(connection)
        if row[0] == "table"
    }
    required = {
        "sources": {"id", "name", "kind", "url", "enabled"},
        "opportunities": {
            "id", "source_id", "external_id", "title", "organization", "description",
            "url", "first_seen_at", "last_seen_at", "active", "status",
        },
    }
    for table, expected in required.items():
        if not tables.get(table, "").lstrip().upper().startswith("CREATE TABLE "):
            raise ValueError("Legacy database is missing expected Opportunity Radar tables")
        if not expected <= {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}:
            raise ValueError("Legacy database is missing expected Opportunity Radar columns")
    if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        raise ValueError("Legacy database failed SQLite integrity verification")
    if connection.execute(
        "SELECT 1 FROM opportunities LEFT JOIN sources ON sources.id=opportunities.source_id "
        "WHERE sources.id IS NULL LIMIT 1"
    ).fetchone() is not None:
        raise ValueError("Legacy opportunities reference missing sources")


def _legacy_observation(row):
    row = dict(row)
    evidence = {
        "format": "legacy_snapshot",
        "legacy_status": row["status"],
        "legacy_active": bool(row["active"]),
    }
    try:
        metadata = json.loads(row.get("metadata_json", "{}"))
    except (ValueError, TypeError):
        metadata = {}
    if isinstance(metadata, dict):
        for key in ("dates", "listing"):
            if key in metadata:
                evidence[key] = metadata[key]
    for key in ("commitment", "category"):
        if row.get(key):
            evidence[key] = row[key]
    return {
        "legacy_id": row["id"],
        "legacy_status": row["status"] if row["status"] in LEGACY_STATUSES else "new",
        "status_updated_at": row.get("status_updated_at"),
        "applied_at": row.get("applied_at"),
        "bookmarked": bool(row.get("bookmarked", False)),
        "first_seen_at": row["first_seen_at"],
        "last_seen_at": row["last_seen_at"],
        "external_id": row["external_id"],
        "title": row["title"],
        "organization": row["organization"],
        "url": row["url"],
        "location": row.get("location", ""),
        "description": row["description"],
        "kinds": [row.get("opportunity_type") or "opportunity"],
        "posted_at": row.get("posted_at"),
        "deadline_at": row.get("deadline_at"),
        "requirements": row.get("eligibility", ""),
        "authority": False,
        "evidence": evidence,
    }


def _legacy_source(record, override, known):
    source_id = record["id"]
    old_kind = override.get("kind", record.get("kind", ""))
    old_url = override.get("url", record.get("url", ""))
    name = override.get("name", record.get("name", source_id))
    enabled = override.get("enabled", bool(record.get("enabled"))) is True
    candidate = {
        **override, "id": source_id, "kind": old_kind, "name": name,
        "url": old_url, "enabled": enabled,
    }
    if known and old_kind == known["kind"] and old_url == known["url"]:
        candidate = {**known, "enabled": enabled}
    try:
        return validate_source(candidate)
    except ValueError:
        return {"id": source_id, "name": str(name)[:160], "kind": "legacy", "url": "", "enabled": False}


def _local_source_ids(config, active_profile):
    layers = [config.get("sources.local.json", {})]
    if active_profile is not None:
        layers.append(active_profile["sources"])
    return {
        source["id"]
        for layer in layers for source in layer.get("sources", [])
    }


def migrate(store, legacy_db, config_dir=None):
    """Import a standalone legacy snapshot into an empty workspace, atomically."""
    path = _input_path(legacy_db, store)
    config = _read_config(path, config_dir)
    profiles, active = _profiles(config)
    active_profile = next((entry for entry in profiles if entry["id"] == active), None)
    overrides = _source_overrides(config, active_profile)
    unobserved = _local_source_ids(config, active_profile)
    catalog = {source["id"]: source for source in load_catalog()}
    deadline = time.monotonic() + MAX_RECOVERY_SECONDS
    with exclusive_work(store), closing(_read_database(path)) as legacy:
        _legacy_shape(legacy)
        with closing(store.connect()) as target, target:
            target.execute("BEGIN IMMEDIATE")
            previous = target.execute(
                "SELECT payload FROM migration_archive WHERE name='summary'"
            ).fetchone()
            if previous is not None:
                return {**json.loads(previous[0]), "already_migrated": True}
            if any(target.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
                   for table in ("sources", "listings")):
                raise ValueError("Import into a new, empty workspace")
            if (
                target.execute("SELECT 1 FROM goals LIMIT 1").fetchone()
                and not clear_pristine_starters(store, target)
            ):
                raise ValueError("Import into a new, empty workspace")
            _archive(target, "configuration", config)
            for entry in profiles:
                store.put_goal(entry["name"], _legacy_rules(entry["profile"]), conn=target)

            summary = {
                "listings": 0, "sources": 0, "inactive_adapters": 0, "archived_fields": 0,
                "profiles": len(profiles), "active_profile_id": active,
                "active_goal": active_profile["name"] if active_profile is not None else None,
            }
            records = {row["id"]: dict(row) for row in legacy.execute("SELECT * FROM sources")}
            for source_id in unobserved:
                records.setdefault(source_id, {"id": source_id})
            for source_id, record in records.items():
                source = _legacy_source(record, overrides.get(source_id, {}), catalog.get(source_id))
                store.add_source(source, conn=target)
                _archive(target, f"source:{source_id}", record)
                summary["sources"] += 1
                summary["inactive_adapters"] += source["kind"] == "legacy"
                rows = legacy.execute(
                    "SELECT * FROM opportunities WHERE source_id=? ORDER BY id", (source_id,)
                )
                while batch := rows.fetchmany(250):
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Legacy import exceeded its recovery time budget")
                    observations = []
                    for row in batch:
                        item = _legacy_observation(row)
                        archived = {}
                        if row["url"]:
                            try:
                                _public_url(row["url"])
                                safe_url(row["url"])
                            except ValueError:
                                archived["url"] = row["url"]
                                item["url"] = ""
                        if "recommended_resume" in row.keys() and row["recommended_resume"]:
                            archived["recommended_resume"] = row["recommended_resume"]
                        if archived:
                            _archive(target, f"listing:{row['id']}", archived)
                            summary["archived_fields"] += len(archived)
                        observations.append(item)
                    store.ingest(source_id, observations, "imported", "Legacy offline snapshot", conn=target)
                    summary["listings"] += len(batch)
            _archive(target, "summary", summary)
    return {**summary, "already_migrated": False}
