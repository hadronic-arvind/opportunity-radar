"""Feed ranking, dashboard preferences, and reversible tracking actions."""

import json
import os
import re
import stat
from contextlib import closing
from datetime import datetime, timezone
from html import unescape

from .store import STATES, _expression, _goal_expression

VIEWS = ("for-you", "explore", "saved", "applications", "hidden")
PAGE_SIZE = 12
PERSONALIZED_LIMIT = 600
MAX_OFFSET = 100000
EXCLUDED_STATUSES = {"skip", "apply", "applied"}
UNSUITABLE = {
    "directory reference",
    "previously inactive, verify",
    "recorded deadline passed",
}
_TAG = re.compile(r"</?[A-Za-z][^>]{0,512}>")
_SPACE = re.compile(r"\s+")


def _settings_path(store):
    return store.path.with_name(store.path.name + ".dashboard.json")


def _selected_goal(store, db):
    """Distinguish an explicit recent feed from a workspace without settings."""
    row = db.execute("SELECT goal FROM dashboard_preferences WHERE id=1").fetchone()
    if row is not None:
        return True, row[0]
    try:
        fd = os.open(_settings_path(store), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return False, None
    with os.fdopen(fd, "rb") as settings:
        info = os.fstat(settings.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or info.st_nlink != 1
        ):
            raise ValueError("Dashboard preferences must be a private regular file")
        body = settings.read(2049)
    if len(body) > 2048:
        raise ValueError("Dashboard preferences exceed the size limit")
    data = json.loads(body)
    if not isinstance(data, dict) or (
        data.get("goal") is not None and not isinstance(data["goal"], str)
    ):
        raise ValueError("Dashboard preferences are invalid")
    return True, data.get("goal")


def set_preferences(store, goal, conn=None):
    """Persist the dashboard goal without changing saved goal definitions."""
    if goal == "":
        goal = None
    if goal is not None and (not isinstance(goal, str) or len(goal) > 100):
        raise ValueError("Choose a saved goal or the recent feed")
    with store.transaction(conn) as db:
        if goal is not None:
            row = db.execute("SELECT name FROM goals WHERE name=?", (goal,)).fetchone()
            if row is None:
                raise ValueError("Unknown goal")
            goal = row[0]
        db.execute(
            "INSERT INTO dashboard_preferences VALUES (1,?) "
            "ON CONFLICT(id) DO UPDATE SET goal=excluded.goal",
            (goal,),
        )
    return {"goal": goal}


def _goal_for_feed(store, db, goals, selected):
    names = {goal["name"].casefold(): goal["name"] for goal in goals}
    if selected is not None:
        if selected == "":
            return None
        if selected.casefold() not in names:
            raise ValueError("Unknown goal")
        return names[selected.casefold()]
    configured, choice = _selected_goal(store, db)
    if configured and choice is None:
        return None
    if choice is not None and choice.casefold() in names:
        return names[choice.casefold()]
    migrated = db.execute(
        "SELECT json_extract(payload, '$.active_goal') FROM migration_archive WHERE name='summary'"
    ).fetchone()
    if migrated and isinstance(migrated[0], str) and migrated[0].casefold() in names:
        return names[migrated[0].casefold()]
    return next(
        (goal["name"] for goal in goals if _goal_expression(goal["rules"])), None
    )


def _excerpt(value, max_chars=240):
    clean = _SPACE.sub(" ", _TAG.sub(" ", unescape(value or ""))).strip()
    if len(clean) <= max_chars:
        return clean
    prefix = clean[:max_chars]
    head, separator, _ = prefix.rpartition(" ")
    return (head if separator else prefix).rstrip(",.;:") + "…"


def _card(summary, observations, view, goal):
    card = dict(summary)
    lead = observations[0]
    quote = None
    if view == "for-you" and goal and card["match"] and card["match"]["recommended"]:
        rules = card["match"]["rules"]
        signal = next(
            (
                rule
                for rule in rules
                if rule["state"] == "observed"
                and rule["rule"] in {"prefer", "require", "explore"}
            ),
            None,
        )
        if signal:
            card["reason"] = "Matches your interest in " + signal["term"]
            card["reason_kind"] = "match"
            quote = (
                signal.get("quote") if signal.get("field") == "description" else None
            )
    if "reason" not in card:
        card["reason"] = (
            "Recently found in your collection" if view == "for-you" else ""
        )
        card["reason_kind"] = "recent" if view == "for-you" else "none"
    card["source_name"] = lead["source_name"] or lead["source_id"]
    card["url"] = lead["url"]
    card["excerpt"] = _excerpt(quote or lead["description"])
    # A card only needs its best reason. The inspection view exposes the full evidence.
    card.pop("match", None)
    return card


def _rows(db, view, kind, expression, limit, offset):
    join = ""
    params = []
    where = []
    ordering = "l.first_seen DESC,l.rowid DESC"
    if expression:
        join = "JOIN inventory ON inventory.rowid=l.rowid"
        where.append("inventory MATCH ?")
        params.append(expression)
        ordering = "bm25(inventory,0,8,3,2,2,1),l.first_seen DESC,l.rowid DESC"
    if view == "saved" or (view == "explore" and not expression):
        where.append("(d.status IS NULL OR d.status <> 'skip')")
    if view == "saved":
        where.append("d.saved=1")
    elif view == "applications":
        where.append("d.status IN ('apply','applied')")
    elif view == "hidden":
        where.append("d.status='skip'")
    if view in {"saved", "applications", "hidden"} and not expression:
        ordering = "d.status_updated_at DESC,l.first_seen DESC,l.rowid DESC"
    if kind:
        where.append("""EXISTS (
            SELECT 1 FROM observations o,json_each(o.data,'$.kinds') k
            WHERE o.listing_id=l.id AND k.value=?
        )""")
        params.append(kind)
    return db.execute(
        f"""
        SELECT l.id,l.first_seen,d.status,d.saved,d.note,d.status_updated_at,d.applied_at
        FROM listings l LEFT JOIN decisions d ON d.listing_id=l.id {join}
        WHERE {" AND ".join(where) or "1"} ORDER BY {ordering} LIMIT ? OFFSET ?
        """,
        (*params, limit, offset),
    ).fetchall()


def _cards(store, db, rows, rules, view, goal, kind=None):
    if not rows:
        return []
    groups = store._observation_page(db, [row["id"] for row in rows])
    result = []
    for row in rows:
        observations = groups[row["id"]]
        item = store._summary(db, row, rules, observations=observations)
        if kind and kind not in item["kinds"]:
            continue
        if view == "for-you" and (
            item["availability"] in UNSUITABLE
            or (rules is not None and not item["match"]["recommended"])
        ):
            continue
        card = _card(item, observations, view, goal)
        score = item["match"]["score"] if item["match"] else 0
        result.append((score, card))
    return result


def _diverse_rank(cards):
    """Give several employers a chance to appear before repeat listings."""
    cards.sort(
        key=lambda entry: (
            not entry[1]["availability"].startswith("stale"),
            entry[0],
        ),
        reverse=True,
    )
    first, rest = [], []
    organizations = set()
    for _, card in cards:
        name = card["organization"].strip().casefold()
        if name and name in organizations:
            rest.append(card)
        else:
            first.append(card)
            organizations.add(name)
    return first + rest


def _recent_cards(store, db, view, kind, expression, offset, limit):
    # Offsets count raw rows. Stop just before the first unreturned eligible row.
    items = []
    remaining = 1200
    while remaining and offset <= MAX_OFFSET:
        batch_size = min(remaining, 96 if view == "for-you" else limit + 1)
        rows = _rows(db, view, kind, expression, batch_size, offset)
        candidates = {
            card["id"]: card
            for _, card in _cards(store, db, rows, None, view, None)
            if view != "for-you" or card["status"] not in EXCLUDED_STATUSES
        }
        for index, row in enumerate(rows):
            if row["id"] in candidates:
                if len(items) == limit:
                    return items, offset + index
                items.append(candidates[row["id"]])
        offset += len(rows)
        remaining -= len(rows)
        if len(rows) < batch_size:
            return items, None
    return items, offset if offset <= MAX_OFFSET else None


def get_feed(
    store, view="for-you", query="", goal=None, kind=None, offset=0, limit=PAGE_SIZE
):
    if view not in VIEWS:
        raise ValueError("Unknown dashboard view")
    if not isinstance(query, str) or len(query) > 500:
        raise ValueError("Enter a search of 500 characters or fewer")
    if goal is not None and (not isinstance(goal, str) or len(goal) > 100):
        raise ValueError("Unknown goal")
    if kind is not None and (not isinstance(kind, str) or len(kind) > 40):
        raise ValueError("Unknown opportunity type")
    if type(limit) is not int or not 1 <= limit <= 40:
        raise ValueError("Page size must be between 1 and 40")
    if type(offset) is not int or not 0 <= offset <= MAX_OFFSET:
        raise ValueError("Invalid page offset")
    query = query.strip()
    kind = kind or None
    with closing(store.connect()) as db, db:
        db.execute("BEGIN")
        goals = store.goals(db)
        active = _goal_for_feed(store, db, goals, goal)
        rules = next(
            (entry["rules"] for entry in goals if entry["name"] == active), None
        )
        terms = _goal_expression(rules or {}) if view == "for-you" else ""
        expression = _expression(query)
        mode = ("personalized" if terms else "recent") if view == "for-you" else view
        items, next_offset = [], None
        if not query or expression:
            if terms:
                match_expression = (
                    f"({expression}) AND ({terms})" if expression else terms
                )
                kind_expression = _expression(kind) if kind else ""
                if kind_expression:
                    match_expression = (
                        f"({match_expression}) AND kinds : ({kind_expression})"
                    )
                rows = _rows(db, view, None, match_expression, PERSONALIZED_LIMIT, 0)
                ranked = _diverse_rank(
                    _cards(store, db, rows, rules, view, active, kind)
                )
                # Retain hidden and application rows in raw rank positions so
                # tracking actions cannot move another listing across a cursor.
                visible = [
                    (index, card)
                    for index, card in enumerate(ranked)
                    if card["status"] not in EXCLUDED_STATUSES
                ]
                if visible:
                    page = [
                        (index, card) for index, card in visible if index >= offset
                    ][: limit + 1]
                    items = [card for _, card in page[:limit]]
                    if len(page) > limit:
                        next_offset = page[limit][0]
                else:
                    mode = "recent"
            if mode != "personalized":
                items, next_offset = _recent_cards(
                    store, db, view, kind, expression, offset, limit
                )
        count = db.execute("""SELECT
            count(*) FILTER (WHERE saved=1 AND status <> 'skip'),
            count(*) FILTER (WHERE status='skip'),
            count(*) FILTER (WHERE status IN ('apply','applied')) FROM decisions
            """).fetchone()
        summary = {
            "total": db.execute("SELECT count(*) FROM listings").fetchone()[0],
            "saved": count[0],
            "hidden": count[1],
            "applied": count[2],
            "sources": db.execute("SELECT count(*) FROM sources").fetchone()[0],
        }
    return {
        "view": view,
        "mode": mode,
        "recent_fallback": bool(view == "for-you" and mode == "recent" and active),
        "goal": active,
        "goals": goals,
        "items": items,
        "has_more": next_offset is not None,
        "next_offset": next_offset,
        "summary": summary,
        "kind": kind,
        "query": query,
    }


def track(store, id, status=None, saved=None, note=None):
    if not isinstance(id, str) or not id or len(id) > 128:
        raise ValueError("Invalid listing")
    if status is None and saved is None and note is None:
        raise ValueError("No tracking changes specified")
    with store.transaction() as db:
        store.track(id, status=status, saved=saved, note=note, conn=db)
        row = db.execute(
            "SELECT status,saved,note FROM decisions WHERE listing_id=?", (id,)
        ).fetchone()
    return {"id": id, **dict(row), "saved": bool(row["saved"])}


def hide(store, id):
    if not isinstance(id, str) or not id or len(id) > 128:
        raise ValueError("Invalid listing")
    with store.transaction() as db:
        previous = db.execute(
            "SELECT status,status_updated_at FROM decisions WHERE listing_id=?", (id,)
        ).fetchone()
        previous_status = previous["status"] if previous else "new"
        if previous_status == "skip":
            raise ValueError("This opportunity is already hidden")
        if previous_status in {"apply", "applied"}:
            raise ValueError("This opportunity is in Applications")
        token = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        store.track(id, status="skip", conn=db)
        db.execute(
            "UPDATE decisions SET status_updated_at=? WHERE listing_id=?", (token, id)
        )
        # Keep the undo receipt in the backup-compatible workspace metadata
        # table without changing the decision schema or overwriting notes.
        db.execute(
            "INSERT INTO migration_archive(name,payload) VALUES (?,?) "
            "ON CONFLICT(name) DO UPDATE SET payload=excluded.payload",
            (_hide_key(id), json.dumps({"status": previous_status, "token": token})),
        )
    return {
        "id": id,
        "status": "skip",
        "previous_status": previous_status,
        "token": token,
    }


def _hide_key(id):
    return "dashboard:hidden:" + id


def undo_hide(store, id, status=None, token=None):
    if not isinstance(id, str) or not id or len(id) > 128:
        raise ValueError("Invalid listing")
    if status is not None and (status not in STATES or status == "skip"):
        raise ValueError("Invalid previous status")
    with store.transaction() as db:
        current = db.execute(
            "SELECT status,status_updated_at FROM decisions WHERE listing_id=?", (id,)
        ).fetchone()
        if not current or current["status"] != "skip":
            raise ValueError("This opportunity is no longer hidden")
        if token is not None and current["status_updated_at"] != token:
            raise ValueError("Tracking changed after this opportunity was hidden")
        if status is None:
            status = "new"
            saved = db.execute(
                "SELECT payload FROM migration_archive WHERE name=?", (_hide_key(id),)
            ).fetchone()
            if saved:
                try:
                    data = json.loads(saved[0])
                except ValueError:
                    data = None
                if (
                    isinstance(data, dict)
                    and data.get("token") == current["status_updated_at"]
                    and data.get("status") in {"new", "reviewed"}
                ):
                    status = data["status"]
        store.track(id, status=status, conn=db)
        db.execute("DELETE FROM migration_archive WHERE name=?", (_hide_key(id),))
    return {"id": id, "status": status}
