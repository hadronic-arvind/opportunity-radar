"""The shared SQLite workspace. Each operation owns its connection."""

import hashlib
import ipaddress
import json
import os
import re
import sqlite3
import stat
from contextlib import closing, contextmanager
from itertools import islice
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .brief import evidence_brief
from .matching import assess, nearby_roots, normalize_rules, tokens, variants

STATES = ("new", "reviewed", "skip", "apply", "applied")
RECEIPT_STATES = {"complete", "partial", "failed", "timed_out", "cancelled", "imported"}
SCHEMA_VERSION = 1
APPLICATION_ID = 0x52414452
SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id TEXT PRIMARY KEY, identity TEXT UNIQUE NOT NULL, first_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY, config TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS observations (
    listing_id TEXT NOT NULL REFERENCES listings(id),
    source_id TEXT NOT NULL REFERENCES sources(id),
    external_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY(listing_id, source_id, external_id)
);
CREATE INDEX IF NOT EXISTS observations_source ON observations(source_id, external_id);
CREATE TABLE IF NOT EXISTS observation_history (
    id INTEGER PRIMARY KEY, listing_id TEXT NOT NULL REFERENCES listings(id),
    source_id TEXT NOT NULL REFERENCES sources(id), external_id TEXT NOT NULL,
    observed_at TEXT NOT NULL, data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decisions (
    listing_id TEXT PRIMARY KEY REFERENCES listings(id), status TEXT NOT NULL,
    saved INTEGER NOT NULL, note TEXT NOT NULL, status_updated_at TEXT, applied_at TEXT
);
CREATE TABLE IF NOT EXISTS goals (
    name TEXT PRIMARY KEY COLLATE NOCASE, rules TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS goal_history (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL COLLATE NOCASE, previous TEXT
);
CREATE TABLE IF NOT EXISTS migration_archive (
    name TEXT PRIMARY KEY, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS receipts (
    id INTEGER PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id),
    state TEXT NOT NULL, message TEXT NOT NULL, seen INTEGER NOT NULL,
    new INTEGER NOT NULL, updated INTEGER NOT NULL, duplicates INTEGER NOT NULL,
    finished_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS receipts_source ON receipts(source_id, id DESC);
CREATE VIRTUAL TABLE IF NOT EXISTS inventory USING fts5(
    listing_id UNINDEXED, title, organization, location, kinds, body,
    tokenize='unicode61 remove_diacritics 2'
);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def safe_url(value):
    if not value:
        return ""
    if (
        not isinstance(value, str)
        or len(value) > 2048
        or any(ord(char) <= 32 or ord(char) == 127 or char == "\\" for char in value)
    ):
        raise ValueError("Invalid listing URL")
    from monitor.fetchers import _remote_url_parts

    _, host, _ = _remote_url_parts(value)
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        last_label = host.rsplit(".", 1)[-1]
        if (
            "." not in host
            or "%" in host
            or re.fullmatch(r"(?:[0-9]+|0x[0-9a-f]+)", last_label)
        ):
            raise ValueError("Listing links must use a public host")
    else:
        if address.is_multicast or address.is_reserved:
            raise ValueError("Listing links must use a public unicast host")
    return value


def _canonical(url):
    parts = urlsplit(url)
    query = urlencode(
        sorted(
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if not key.lower().startswith("utm_")
            and key.lower() not in {"gclid", "fbclid"}
        )
    )
    return urlunsplit(
        ("https", parts.netloc.lower(), parts.path.rstrip("/") or "/", query, "")
    )


def _iso_day(value):
    if (
        not isinstance(value, str)
        or len(value) < 10
        or (len(value) > 10 and value[10] != "T")
    ):
        return None
    try:
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        return None


def _row(value):
    if not isinstance(value, dict):
        raise ValueError("Each listing must be an object")
    result = {}
    for field, size in {
        "external_id": 500,
        "title": 500,
        "organization": 300,
        "location": 500,
        "description": 20000,
        "requirements": 5000,
        "posted_at": 100,
        "deadline_at": 100,
        "cycle": 120,
    }.items():
        item = value.get(field) or ""
        if not isinstance(item, str):
            raise ValueError(f"{field} must be text")
        result[field] = item.strip()[:size]
    if not result["external_id"] or not result["title"]:
        raise ValueError("Each listing needs external_id and title")
    result["url"] = safe_url(value.get("url"))
    kinds = value.get("kinds", ["opportunity"])
    if (
        not isinstance(kinds, list)
        or len(kinds) > 24
        or any(not isinstance(item, str) or len(item) > 80 for item in kinds)
    ):
        raise ValueError("kinds must contain at most 24 short labels")
    result["kinds"] = sorted(
        set(item.strip().lower() for item in kinds if item.strip())
    )
    evidence = value.get("evidence") or {}
    if not isinstance(evidence, dict) or len(encode(evidence)) > 64000:
        raise ValueError("evidence must be an object of at most 64,000 characters")
    result["authority"] = value.get("authority") is True
    result["evidence"] = evidence
    return result


def _identity(source_id, row):
    url = row["url"]
    cycle = row["cycle"] or _iso_day(row["deadline_at"])
    path = urlsplit(url).path
    posting = re.search(
        r"/(?:jobs|postings|vacancies)/[A-Za-z0-9_-]*[0-9][A-Za-z0-9_-]*/?$", path
    )
    if url and path.strip("/") and (cycle or posting):
        identity = (
            "url-posting",
            _canonical(url),
            cycle,
            " ".join(tokens(row["title"])),
        )
    else:
        identity = ("publisher", source_id, row["external_id"], row["cycle"])
    return encode(identity)


def _expression(query):
    return " AND ".join('"' + term + '"' for term in tokens(query)[:32])


def _goal_expression(rules):
    parts = {}
    for key in ("prefer", "require", "explore"):
        for phrase in rules.get(key, []):
            parts.update(dict.fromkeys(f'"{term}"' for term in variants(phrase)))
            if key != "require" and (roots := nearby_roots(phrase)):
                parts["(" + " AND ".join(f'"{root}"*' for root in roots) + ")"] = None
    return " OR ".join(parts)


class Store:
    def __init__(self, path):
        self.path = Path(path).expanduser().absolute()

    def connect(self):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        info = self.path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or info.st_nlink != 1
        ):
            raise ValueError("Workspace must be an owner-only, regular SQLite file")
        conn = sqlite3.connect(self.path, timeout=2)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA trusted_schema = OFF")
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            app = conn.execute("PRAGMA application_id").fetchone()[0]
            if version == app == 0:
                with conn:
                    conn.execute("BEGIN IMMEDIATE")
                    version = conn.execute("PRAGMA user_version").fetchone()[0]
                    app = conn.execute("PRAGMA application_id").fetchone()[0]
                    if version == app == 0:
                        if conn.execute(
                            "SELECT 1 FROM sqlite_master LIMIT 1"
                        ).fetchone():
                            raise ValueError(
                                "Select a new workspace. Use migrate for legacy databases."
                            )
                        for statement in SCHEMA.split(";"):
                            if statement.strip():
                                conn.execute(statement)
                        conn.execute(f"PRAGMA application_id = {APPLICATION_ID}")
                        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                        version, app = SCHEMA_VERSION, APPLICATION_ID
            if app != APPLICATION_ID or version != SCHEMA_VERSION:
                raise ValueError("Unsupported workspace schema")
            return conn
        except BaseException:
            conn.close()
            raise

    @contextmanager
    def transaction(self, conn=None):
        if conn is not None:
            yield conn
            return
        with closing(self.connect()) as own, own:
            own.execute("BEGIN IMMEDIATE")
            yield own

    def add_source(self, config, conn=None):
        from .collection import validate_source

        if isinstance(config, dict) and config.get("kind") in {
            "manual",
            "legacy",
            "sample",
        }:
            config = dict(config)
        else:
            config = validate_source(config)
        source_id = config.get("id")
        if not isinstance(source_id, str) or not source_id or len(source_id) > 200:
            raise ValueError("Source needs an ID of at most 200 characters")
        if len(encode(config)) > 64000:
            raise ValueError("Source configuration is too large")
        with self.transaction(conn) as db:
            existing = db.execute(
                "SELECT config FROM sources WHERE id=?", (source_id,)
            ).fetchone()
            if existing and any(
                json.loads(existing[0]).get(key) != config.get(key)
                for key in ("kind", "url", "board", "api_region")
            ):
                raise ValueError(
                    "Use a new source ID when changing publisher or adapter"
                )
            db.execute(
                "INSERT INTO sources VALUES (?,?) ON CONFLICT(id) DO UPDATE SET config=excluded.config",
                (source_id, encode(config)),
            )
        return config

    def sources(self):
        with closing(self.connect()) as db:
            return [
                {
                    **json.loads(row["config"]),
                    "last_receipt": json.loads(row["receipt"])
                    if row["receipt"]
                    else None,
                }
                for row in db.execute("""
                    SELECT sources.config, (
                        SELECT json_object('state', state, 'finished_at', finished_at,
                                           'message', message, 'seen', seen)
                        FROM receipts WHERE receipts.source_id = sources.id ORDER BY id DESC LIMIT 1
                    ) AS receipt FROM sources ORDER BY sources.id
                """)
            ]

    def receipts(self, limit=30):
        if not 1 <= limit <= 500:
            raise ValueError("Receipt limit must be from 1 to 500")
        with closing(self.connect()) as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM receipts ORDER BY id DESC LIMIT ?", (limit,)
                )
            ]

    def _observe(self, db, source_id, value, timestamp):
        row = _row(value)
        old_id = value.get("legacy_id")
        if old_id and (not isinstance(old_id, str) or len(old_id) > 200):
            raise ValueError("Invalid legacy listing identity")
        identity = _identity(source_id, row)
        previous = db.execute(
            """
            SELECT o.*, l.identity FROM observations o JOIN listings l ON l.id=o.listing_id
            WHERE source_id=? AND external_id=? ORDER BY observed_at DESC, listing_id
        """,
            (source_id, row["external_id"]),
        ).fetchall()
        if not old_id:
            for obs in previous:
                old_data = json.loads(obs["data"])
                old_deadline = _iso_day(old_data["deadline_at"])
                new_deadline = _iso_day(row["deadline_at"])
                explicit_change = row["cycle"] and row["cycle"] != old_data["cycle"]
                renewed = (
                    old_deadline
                    and new_deadline
                    and old_deadline < timestamp[:10] <= new_deadline
                )
                replaced = (
                    old_data["url"]
                    and row["url"]
                    and _canonical(old_data["url"]) != _canonical(row["url"])
                    and tokens(old_data["title"]) != tokens(row["title"])
                )
                if not explicit_change and not renewed and not replaced:
                    identity = obs["identity"]
                    break
            else:
                if any(obs["identity"] == identity for obs in previous):
                    identity = encode(
                        (
                            identity,
                            "replacement",
                            row["deadline_at"],
                            row["url"],
                            row["title"],
                        )
                    )
        listing_id = old_id or hashlib.sha256(identity.encode()).hexdigest()[:24]
        if old_id:
            identity = encode(("legacy", old_id))
        else:
            same = db.execute(
                "SELECT id FROM listings WHERE identity=?", (identity,)
            ).fetchone()
            if same:
                listing_id = same[0]
        created = db.execute(
            "INSERT OR IGNORE INTO listings VALUES (?,?,?)",
            (listing_id, identity, value.get("first_seen_at") or timestamp),
        ).rowcount
        before = db.execute(
            "SELECT * FROM observations WHERE listing_id=? AND source_id=? AND external_id=?",
            (listing_id, source_id, row["external_id"]),
        ).fetchone()
        content = encode(row)
        changed = before is None or before["data"] != content
        if changed and before is not None:
            db.execute(
                """
                INSERT INTO observation_history(listing_id,source_id,external_id,observed_at,data)
                VALUES (?,?,?,?,?)
            """,
                (
                    listing_id,
                    source_id,
                    row["external_id"],
                    before["observed_at"],
                    before["data"],
                ),
            )
        db.execute(
            """
            INSERT INTO observations VALUES (?,?,?,?,?)
            ON CONFLICT(listing_id,source_id,external_id)
            DO UPDATE SET observed_at=excluded.observed_at,data=excluded.data
        """,
            (
                listing_id,
                source_id,
                row["external_id"],
                value.get("last_seen_at") or timestamp,
                content,
            ),
        )
        if old_id:
            status = value.get("legacy_status", "new")
            if status not in STATES:
                raise ValueError(f"Unrecognized legacy status: {status}")
            db.execute(
                "INSERT OR IGNORE INTO decisions VALUES (?,?,?,?,?,?)",
                (
                    listing_id,
                    status,
                    bool(value.get("bookmarked")),
                    "",
                    value.get("status_updated_at"),
                    value.get("applied_at"),
                ),
            )
        return listing_id, bool(created), changed, before is None and not created

    def ingest(self, source_id, rows, state, message, conn=None):
        if (
            state not in RECEIPT_STATES
            or not isinstance(rows, list)
            or len(rows) > 5000
        ):
            raise ValueError(
                "Invalid collection receipt or more than 5,000 observations"
            )
        timestamp = now()
        new = updated = duplicates = 0
        reindex = set()
        with self.transaction(conn) as db:
            if not db.execute(
                "SELECT 1 FROM sources WHERE id=?", (source_id,)
            ).fetchone():
                raise ValueError("Unknown source")
            for value in rows:
                listing_id, created, changed, duplicate = self._observe(
                    db, source_id, value, timestamp
                )
                new += created
                updated += changed and not created and not duplicate
                duplicates += duplicate
                if changed:
                    reindex.add(listing_id)
            for listing_id in reindex:
                self._index(db, listing_id)
            cursor = db.execute(
                "INSERT INTO receipts VALUES (NULL,?,?,?,?,?,?,?,?)",
                (
                    source_id,
                    state,
                    str(message)[:1000],
                    len(rows),
                    new,
                    updated,
                    duplicates,
                    timestamp,
                ),
            )
            return dict(
                db.execute(
                    "SELECT * FROM receipts WHERE id=?", (cursor.lastrowid,)
                ).fetchone()
            )

    def _observation_page(self, db, ids, history=False):
        groups = {id: [] for id in ids}
        placeholders = ",".join("?" for _ in groups)
        table = "observation_history" if history else "observations"
        limit = "LIMIT 50" if history else ""
        for row in db.execute(
            f"""
            SELECT o.*, json_extract(s.config,'$.name') AS source_name
            FROM {table} o JOIN sources s ON o.source_id=s.id
            WHERE listing_id IN ({placeholders})
            ORDER BY json_extract(o.data,'$.authority') DESC, observed_at DESC,
                o.source_id, o.external_id
            {limit}
        """,
            tuple(groups),
        ):
            groups[row["listing_id"]].append(
                {
                    **json.loads(row["data"]),
                    "source_id": row["source_id"],
                    "observed_at": row["observed_at"],
                    "source_name": row["source_name"],
                }
            )
        return groups

    def _observations(self, db, listing_id, history=False):
        return self._observation_page(db, [listing_id], history)[listing_id]

    def _index(self, db, listing_id):
        rows = self._observations(db, listing_id)
        db.execute(
            "DELETE FROM inventory WHERE rowid=(SELECT rowid FROM listings WHERE id=?)",
            (listing_id,),
        )
        fields = ("title", "organization", "location", "kinds", "description")
        values = []
        for field in fields:
            values.append(
                "\n".join(
                    dict.fromkeys(
                        " ".join(row[field]) if field == "kinds" else row[field]
                        for row in rows
                    )
                )
            )
        db.execute(
            "INSERT INTO inventory(rowid,listing_id,title,organization,location,kinds,body) SELECT rowid,id,?,?,?,?,? FROM listings WHERE id=?",
            (*values, listing_id),
        )

    def goals(self):
        with closing(self.connect()) as db:
            return [
                {"name": row["name"], "rules": json.loads(row["rules"])}
                for row in db.execute("SELECT * FROM goals ORDER BY name")
            ]

    def put_goal(self, name, rules, conn=None):
        if not isinstance(name, str) or not name.strip() or len(name) > 100:
            raise ValueError("Goal name must contain 1 to 100 characters")
        name = name.strip()
        value = encode(normalize_rules(rules))
        with self.transaction(conn) as db:
            previous = db.execute(
                "SELECT rules FROM goals WHERE name=?", (name,)
            ).fetchone()
            if previous and previous[0] == value:
                return
            db.execute(
                "INSERT INTO goal_history(name,previous) VALUES (?,?)",
                (name, previous[0] if previous else None),
            )
            db.execute(
                "INSERT INTO goals VALUES (?,?) ON CONFLICT(name) DO UPDATE SET rules=excluded.rules",
                (name, value),
            )

    def undo_goal(self, name):
        with self.transaction() as db:
            row = db.execute(
                "SELECT * FROM goal_history WHERE name=? ORDER BY id DESC LIMIT 1",
                (name,),
            ).fetchone()
            if row is None:
                raise ValueError("No goal change to undo")
            if row["previous"] is None:
                db.execute("DELETE FROM goals WHERE name=?", (name,))
            else:
                db.execute(
                    "UPDATE goals SET rules=? WHERE name=?", (row["previous"], name)
                )
            db.execute("DELETE FROM goal_history WHERE id=?", (row["id"],))

    def track(self, id, status=None, saved=None, note=None, conn=None):
        if status is not None and status not in STATES:
            raise ValueError("Unknown application status")
        if saved is not None and type(saved) is not bool:
            raise ValueError("Saved must be a boolean")
        if note is not None and (not isinstance(note, str) or len(note) > 8000):
            raise ValueError("Note must be text of at most 8,000 characters")
        with self.transaction(conn) as db:
            if not db.execute("SELECT 1 FROM listings WHERE id=?", (id,)).fetchone():
                raise ValueError("Unknown opportunity")
            prev = db.execute(
                "SELECT * FROM decisions WHERE listing_id=?", (id,)
            ).fetchone()
            old = (
                dict(prev)
                if prev
                else dict(
                    status="new",
                    saved=False,
                    note="",
                    status_updated_at=None,
                    applied_at=None,
                )
            )
            timestamp = now()
            changed = status is not None and status != old["status"]
            db.execute(
                """
                INSERT INTO decisions VALUES (?,?,?,?,?,?)
                ON CONFLICT(listing_id) DO UPDATE SET status=excluded.status, saved=excluded.saved,
                note=excluded.note, status_updated_at=excluded.status_updated_at, applied_at=excluded.applied_at
            """,
                (
                    id,
                    status or old["status"],
                    old["saved"] if saved is None else saved,
                    old["note"] if note is None else note,
                    timestamp if changed else old["status_updated_at"],
                    old["applied_at"] or (timestamp if status == "applied" else None),
                ),
            )

    def _goal(self, db, name):
        if not name:
            return None
        row = db.execute("SELECT rules FROM goals WHERE name=?", (name,)).fetchone()
        if row is None:
            raise ValueError("Unknown goal")
        return json.loads(row[0])

    def _summary(self, db, listing, rules, detail=False, observations=None):
        result = {
            key: listing[key]
            for key in (
                "id",
                "first_seen",
                "status",
                "saved",
                "note",
                "status_updated_at",
                "applied_at",
            )
        }
        rows = (
            observations
            if observations is not None
            else self._observations(db, result["id"])
        )
        lead = rows[0]
        result.update(
            saved=bool(result["saved"]),
            status=result["status"] or "new",
            note=result["note"] or "",
        )
        result.update(
            {
                key: lead[key]
                for key in (
                    "title",
                    "organization",
                    "location",
                    "deadline_at",
                    "posted_at",
                )
            }
        )
        deadlines = {_iso_day(row["deadline_at"]) for row in rows} - {None}
        evidence_dates = {row["deadline_at"] for row in rows if row["deadline_at"]}
        updated = max(row["observed_at"] for row in rows)
        today = datetime.now(timezone.utc).date()
        kinds = sorted({kind for row in rows for kind in row["kinds"]})
        if all(row["evidence"].get("format") == "directory_link" for row in rows):
            availability = "directory reference"
        elif len(evidence_dates) > 1:
            availability = "conflicting dates"
        elif deadlines and next(iter(deadlines)) < today.isoformat():
            availability = "recorded deadline passed"
        elif any(row["evidence"].get("legacy_active") is False for row in rows):
            availability = "previously inactive, verify"
        elif (
            _iso_day(updated) and (today - date.fromisoformat(updated[:10])).days >= 30
        ):
            availability = "stale, verify with publisher"
        else:
            availability = "unconfirmed, verify with publisher"
        result.update(
            kinds=kinds,
            availability=availability,
            updated_at=updated,
            match=assess(rows, rules) if rules is not None else None,
            observation_count=len(rows),
        )
        if detail:
            result.update(
                {key: lead[key] for key in ("description", "url", "requirements")},
                observations=rows,
                history=self._observations(db, result["id"], history=True),
                brief=evidence_brief(rows),
            )
        return result

    def inspect(self, id, goal=None):
        return self.inspect_many([id], goal)[0]

    def inspect_many(self, ids, goal=None):
        with closing(self.connect()) as db, db:
            db.execute("BEGIN")
            rules = self._goal(db, goal)
            details = []
            for id in ids:
                row = db.execute(
                    """
                    SELECT l.*,d.status,d.saved,d.note,d.status_updated_at,d.applied_at
                    FROM listings l LEFT JOIN decisions d ON d.listing_id=l.id WHERE l.id=?
                """,
                    (id,),
                ).fetchone()
                if row is None:
                    raise ValueError("Unknown opportunity")
                details.append(self._summary(db, row, rules, detail=True))
            return details

    def search(self, query="", goal=None, scope="all", kind=None, limit=50, offset=0):
        if (
            scope not in {"all", "recommended", "tracked"}
            or not 1 <= limit <= 200
            or not 0 <= offset <= 100000
        ):
            raise ValueError(
                "Invalid scope or page. Use limit 1 to 200 and offset 0 to 100,000"
            )
        if len(query) > 2000:
            raise ValueError("Query exceeds 2,000 characters")
        with closing(self.connect()) as db, db:
            db.execute("BEGIN")
            rules = self._goal(db, goal)
            expression = _expression(query)
            if query.strip() and not expression:
                return []
            if scope == "recommended":
                goal_terms = _goal_expression(rules or {})
                if not goal_terms:
                    return []
                expression = (
                    f"({expression}) AND ({goal_terms})" if expression else goal_terms
                )
            values = []
            join = ""
            sort = "l.rowid DESC"
            predicates = []
            if expression:
                join = "JOIN inventory ON inventory.rowid=l.rowid"
                predicates.append("inventory MATCH ?")
                values.append(expression)
                sort = "bm25(inventory,0,8,3,2,2,1), l.rowid DESC"
            if scope == "tracked":
                predicates.append("(d.saved=1 OR d.status<>'new' OR d.note<>'')")
            if scope == "recommended":
                predicates.append("(d.status IS NULL OR d.status<>'skip')")
            if kind:
                predicates.append("""EXISTS (
                    SELECT 1 FROM observations o,json_each(o.data,'$.kinds') k
                    WHERE o.listing_id=l.id AND k.value=?
                )""")
                values.append(kind)
            where = " AND ".join(predicates) or "1"
            sql = f"""
                SELECT l.*,d.status,d.saved,d.note,d.status_updated_at,d.applied_at
                FROM listings l LEFT JOIN decisions d ON d.listing_id=l.id {join}
                WHERE {where} ORDER BY {sort}
            """
            if scope != "recommended":
                return [
                    self._summary(db, row, rules)
                    for row in db.execute(
                        sql + " LIMIT ? OFFSET ?", (*values, limit, offset)
                    )
                ]
            # Keep the full candidate set while retaining only one page of ranked results.
            from heapq import nlargest

            def ranked():
                cursor = db.execute(sql, values)
                index = 0
                while page := list(islice(cursor, 128)):
                    observations = self._observation_page(
                        db, [row["id"] for row in page]
                    )
                    for row in page:
                        summary = self._summary(
                            db, row, rules, observations=observations[row["id"]]
                        )
                        if summary["match"]["recommended"] and summary[
                            "availability"
                        ] not in {"recorded deadline passed", "directory reference"}:
                            yield summary["match"]["score"], -index, summary
                        index += 1

            return [
                summary
                for _, _, summary in nlargest(
                    offset + limit, ranked(), key=lambda entry: entry[:2]
                )[offset:]
            ]
