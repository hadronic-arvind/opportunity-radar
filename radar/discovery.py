"""Local search worklists, explicit review cursors, and manual capture."""

import hashlib
import json
from contextlib import closing
from datetime import date

from .locations import normalize_location
from .matching import tokens
from .store import _canonical, encode, safe_url


def _name(name):
    if (
        not isinstance(name, str)
        or not name.strip()
        or len(name.strip()) > 100
        or any(not character.isprintable() for character in name)
    ):
        raise ValueError("Enter a saved search name between 1 and 100 characters")
    return name.strip()


def _record(row):
    return {
        "name": row["name"],
        "criteria": json.loads(row["criteria"]),
        "reviewed_through": row["reviewed_through"],
    }


def list_searches(store):
    with closing(store.connect()) as db:
        return [_record(row) for row in db.execute(
            "SELECT * FROM saved_searches ORDER BY name COLLATE NOCASE"
        )]


def get_search(store, name):
    with closing(store.connect()) as db:
        row = db.execute(
            "SELECT * FROM saved_searches WHERE name=?", (_name(name),)
        ).fetchone()
        if row is None:
            raise ValueError("That saved search no longer exists")
        return _record(row)


def save_search(store, name, query="", kind=None, location=None):
    name = _name(name)
    if not isinstance(query, str) or len(query) > 500:
        raise ValueError("Enter a search of 500 characters or fewer")
    if kind is not None and (not isinstance(kind, str) or len(kind) > 40):
        raise ValueError("Unknown opportunity type")
    criteria = {"query": query.strip(), "kind": kind or None}
    places = normalize_location(location, default_policy="require")
    if places:
        criteria["location"] = places
    with store.transaction() as db:
        if db.execute("SELECT 1 FROM saved_searches WHERE name=?", (name,)).fetchone():
            raise ValueError("A search with that name already exists")
        if db.execute("SELECT count(*) FROM saved_searches").fetchone()[0] >= 50:
            raise ValueError("Remove a saved search before adding another (limit 50)")
        cursor = db.execute("SELECT coalesce(max(rowid),0) FROM listings").fetchone()[0]
        db.execute("INSERT INTO saved_searches VALUES (?,?,?)", (name, encode(criteria), cursor))
    return {"name": name, "criteria": criteria, "reviewed_through": cursor}


def remove_search(store, name):
    with store.transaction() as db:
        if not db.execute("DELETE FROM saved_searches WHERE name=?", (_name(name),)).rowcount:
            raise ValueError("That saved search no longer exists")
    return {"removed": True}


def review_search(store, name, cursor, expected_cursor):
    name = _name(name)
    if any(type(value) is not int or not 0 <= value < 2**63 for value in (cursor, expected_cursor)):
        raise ValueError("Refresh the saved search before marking it reviewed")
    with store.transaction() as db:
        latest = db.execute("SELECT coalesce(max(rowid),0) FROM listings").fetchone()[0]
        if not expected_cursor <= cursor <= latest:
            raise ValueError("Refresh the saved search before marking it reviewed")
        result = db.execute(
            "UPDATE saved_searches SET reviewed_through=? WHERE name=? AND reviewed_through=?",
            (cursor, name, expected_cursor),
        )
        if not result.rowcount:
            raise ValueError("This saved search changed. Open it again to review the latest batch.")
        return _record(db.execute("SELECT * FROM saved_searches WHERE name=?", (name,)).fetchone())


_CAPTURE_SOURCE = "quick_capture"
_CAPTURE_FIELDS = {
    "title": 500, "organization": 300, "location": 500,
    "description": 20000, "requirements": 5000, "deadline_at": 10,
}


def capture(store, title, url="", organization="", location="", description="",
            requirements="", deadline_at="", kind="opportunity"):
    """Save user-entered listing evidence in one local transaction."""
    row = {
        "title": title, "organization": organization, "location": location,
        "description": description, "requirements": requirements, "deadline_at": deadline_at,
    }
    for key, maximum in _CAPTURE_FIELDS.items():
        value = row[key]
        if not isinstance(value, str) or len(value) > maximum:
            raise ValueError(f"{key.replace('_', ' ').capitalize()} must be at most {maximum:,} characters")
        row[key] = value.strip()
    if not row["title"]:
        raise ValueError("Enter the opportunity title")
    if row["deadline_at"]:
        try:
            if date.fromisoformat(row["deadline_at"]).isoformat() != row["deadline_at"]:
                raise ValueError
        except ValueError as exc:
            raise ValueError("Enter a deadline in YYYY-MM-DD format") from exc
    if not isinstance(kind, str) or not kind.strip() or len(kind) > 40:
        raise ValueError("Choose an opportunity type")
    row["url"] = safe_url(url)
    row["kinds"] = [kind.strip()]
    locator = [_canonical(row["url"]), tokens(row["title"])]
    if not row["url"]:
        locator.extend([tokens(row["organization"]), tokens(row["location"]), row["deadline_at"]])
    external_id = hashlib.sha256(encode(locator).encode("utf-8")).hexdigest()
    row.update(external_id=external_id, evidence={"origin": "user capture"})
    with store.transaction() as db:
        existing = db.execute(
            "SELECT listing_id FROM observations WHERE source_id=? AND external_id=? ORDER BY rowid DESC LIMIT 1",
            (_CAPTURE_SOURCE, external_id),
        ).fetchone()
        if existing is None:
            store.add_source(
                {"id": _CAPTURE_SOURCE, "name": "Quick capture", "kind": "manual", "url": ""},
                conn=db,
            )
            store.ingest(_CAPTURE_SOURCE, [row], "imported", "Manual opportunity capture", conn=db)
            existing = db.execute(
                "SELECT listing_id FROM observations WHERE source_id=? AND external_id=?",
                (_CAPTURE_SOURCE, external_id),
            ).fetchone()
        listing_id = existing["listing_id"]
        status = db.execute("SELECT status FROM decisions WHERE listing_id=?", (listing_id,)).fetchone()
        store.track(listing_id, status="new" if status and status[0] == "skip" else None, saved=True, conn=db)
    return {"id": listing_id}
