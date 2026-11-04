"""Private application plans and a source-backed deadline agenda."""

import hashlib
import json
import os
import re
import tempfile
import unicodedata
from contextlib import closing
from datetime import date, datetime, timezone
from itertools import groupby
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

APPLICATION_PLANS_SCHEMA = """
CREATE TABLE application_plans (
    listing_id TEXT PRIMARY KEY REFERENCES listings(id),
    next_action TEXT NOT NULL, follow_up TEXT,
    checklist TEXT NOT NULL, revision TEXT NOT NULL
);
"""

CHECKLIST_LIMIT = 24
DATE_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
TIMESTAMP_PATTERN = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}"
    r"(?::[0-9]{2}(?:[.,][0-9]{1,6})?)?(?:Z|[+-][0-9]{2}:[0-9]{2})"
)


def _text(value, name, limit, required=False):
    if (
        not isinstance(value, str)
        or len(value) > limit
        or (required and not value.strip())
        or any(unicodedata.category(char) in {"Cc", "Cs"} for char in value)
    ):
        raise ValueError(
            f"{name} must be single-line text of at most {limit} characters"
        )
    return value.strip()


def _calendar_date(value):
    if not isinstance(value, str) or not DATE_PATTERN.fullmatch(value):
        raise ValueError("Follow-up date must use YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError("Follow-up date must be a valid calendar date") from error


def _normalize_checklist(value):
    if not isinstance(value, list) or len(value) > CHECKLIST_LIMIT:
        raise ValueError(f"Use at most {CHECKLIST_LIMIT} checklist items")
    items, labels = [], set()
    for item in value:
        if (
            not isinstance(item, dict)
            or item.keys() != {"label", "done"}
            or type(item["done"]) is not bool
        ):
            raise ValueError(
                "Checklist items need a label and a checked or unchecked state"
            )
        label = _text(item["label"], "Checklist label", 140, required=True)
        key = label.casefold()
        if key in labels:
            raise ValueError("Checklist labels must be unique")
        labels.add(key)
        items.append({"label": label, "done": item["done"]})
    return items


def _plan(db, id):
    row = db.execute(
        """SELECT p.* FROM listings l LEFT JOIN application_plans p ON p.listing_id=l.id
        WHERE l.id=?""",
        (id,),
    ).fetchone()
    if row is None:
        raise ValueError("Unknown opportunity")
    return {
        "id": id,
        "next_action": row["next_action"] or "",
        "follow_up": row["follow_up"],
        "checklist": json.loads(row["checklist"]) if row["checklist"] else [],
        "revision": row["revision"],
    }


def get_plan(store, id):
    with closing(store.connect()) as db:
        return _plan(db, id)


_UNSPECIFIED = object()


def save_plan(
    store, id, next_action, follow_up, checklist, expected_revision=_UNSPECIFIED
):
    action = _text(next_action, "Next action", 300)
    due = _calendar_date(follow_up).isoformat() if follow_up not in (None, "") else None
    materials = _normalize_checklist(checklist)
    with store.transaction() as db:
        previous = _plan(db, id)
        if (
            expected_revision is not _UNSPECIFIED
            and expected_revision != previous["revision"]
        ):
            raise ValueError("This plan changed. Reload it before saving your edits.")
        if (action, due, materials) == (
            previous["next_action"],
            previous["follow_up"],
            previous["checklist"],
        ):
            return previous
        revision = uuid4().hex
        db.execute(
            """INSERT INTO application_plans VALUES (?,?,?,?,?)
            ON CONFLICT(listing_id) DO UPDATE SET next_action=excluded.next_action,
                follow_up=excluded.follow_up, checklist=excluded.checklist,
                revision=excluded.revision""",
            (id, action, due, json.dumps(materials, ensure_ascii=False), revision),
        )
        return {
            "id": id,
            "next_action": action,
            "follow_up": due,
            "checklist": materials,
            "revision": revision,
        }


def _parse_deadline(value):
    try:
        if DATE_PATTERN.fullmatch(value):
            return date.fromisoformat(value)
        if TIMESTAMP_PATTERN.fullmatch(value):
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
                timezone.utc
            )
    except (ValueError, OverflowError):
        pass
    return None


def _observed_deadline(row):
    normalized, raw = row["deadline"], row["raw_deadline"]
    # The collection adapter may retain an exact publisher time in date evidence
    # while normalizing its indexed deadline to a day.
    if (
        isinstance(raw, str)
        and normalized == raw[:10]
        and isinstance(_parse_deadline(raw), datetime)
    ):
        return raw
    return normalized


def _deadline(observations, current):
    candidates = list(
        dict.fromkeys(
            value for row in observations if (value := _observed_deadline(row))
        )
    )
    parsed = {_parse_deadline(value) for value in candidates}
    inactive = any(row["inactive"] for row in observations)
    directory = all(row["directory"] for row in observations)
    result = {
        "state": "unknown",
        "date": None,
        "timestamp": None,
        "candidates": candidates[:5],
        "more_candidates": len(candidates) > 5,
    }
    if inactive or directory:
        result["state"] = "inactive" if inactive else "directory"
    elif None in parsed or len(parsed) > 1:
        result["state"] = "verify"
    elif parsed:
        value = parsed.pop()
        if isinstance(value, datetime):
            result["timestamp"] = value.isoformat()
            try:
                result["date"] = value.astimezone().date().isoformat()
            except (ValueError, OverflowError):
                result["date"] = value.date().isoformat()
            passed = value < current
        else:
            result["date"] = value.isoformat()
            passed = value < current.astimezone().date()
        result["state"] = "passed" if passed else "recorded"
    elif any(row["deadline_state"] == "open_until_filled" for row in observations):
        result["state"] = "open_until_filled"
    elif any(row["deadline_state"] == "rolling" for row in observations):
        result["state"] = "rolling"
    return result


def _agenda_items(store, current):
    with closing(store.connect()) as db:
        rows = db.execute(
            """WITH tracked AS (
                SELECT listing_id FROM decisions WHERE saved=1 OR status IN ('apply','applied')
                UNION SELECT listing_id FROM application_plans
                    WHERE next_action <> '' OR follow_up IS NOT NULL OR json_array_length(checklist) > 0
            )
            SELECT t.listing_id AS id, coalesce(d.status,'new') AS status,
                coalesce(d.saved,0) AS saved, p.next_action, p.follow_up, p.checklist,
                json_extract(o.data,'$.title') AS title,
                json_extract(o.data,'$.organization') AS organization,
                json_extract(o.data,'$.url') AS url,
                json_extract(o.data,'$.deadline_at') AS deadline,
                json_extract(o.data,'$.evidence.dates.deadline.evidence') AS raw_deadline,
                json_extract(o.data,'$.evidence.dates.deadline.state') AS deadline_state,
                json_type(o.data,'$.evidence.legacy_active')='false' AS inactive,
                json_extract(o.data,'$.evidence.format')='directory_link' AS directory
            FROM tracked t JOIN observations o ON o.listing_id=t.listing_id
            LEFT JOIN decisions d ON d.listing_id=t.listing_id
            LEFT JOIN application_plans p ON p.listing_id=t.listing_id
            WHERE coalesce(d.status,'new') <> 'skip'
            ORDER BY t.listing_id, json_extract(o.data,'$.authority') DESC, o.observed_at DESC,
                o.source_id, o.external_id"""
        )
        items = []
        for id, group in groupby(rows, key=lambda row: row["id"]):
            observations = list(group)
            lead = observations[0]
            deadline = _deadline(observations, current)
            checklist = json.loads(lead["checklist"] or "[]")
            items.append(
                {
                    "id": id,
                    "title": lead["title"],
                    "organization": lead["organization"],
                    "url": lead["url"],
                    "status": lead["status"],
                    "saved": bool(lead["saved"]),
                    "deadline": deadline,
                    "follow_up": lead["follow_up"],
                    "next_action": lead["next_action"] or "",
                    "materials_done": sum(item["done"] for item in checklist),
                    "materials_total": len(checklist),
                }
            )
    return items


def _agenda_order(item):
    deadline = item["deadline"]
    dates = [
        value
        for value in (
            item["follow_up"],
            deadline["date"]
            if deadline["state"] == "recorded" and item["status"] != "applied"
            else None,
        )
        if value
    ]
    priority = 0 if dates else 1 if deadline["state"] == "verify" else 2
    return priority, min(dates, default=""), item["title"].casefold(), item["id"]


def _calendar_slots(item, today):
    deadline = item["deadline"]
    if deadline["state"] == "recorded" and item["status"] != "applied":
        if deadline["timestamp"]:
            value = datetime.fromisoformat(deadline["timestamp"]).strftime(
                "%Y%m%dT%H%M%SZ"
            )
            yield "deadline", f"DTSTART:{value}"
        else:
            yield "deadline", "DTSTART;VALUE=DATE:" + deadline["date"].replace("-", "")
    if item["follow_up"] and item["follow_up"] >= today:
        yield "follow-up", "DTSTART;VALUE=DATE:" + item["follow_up"].replace("-", "")


def agenda(store, limit=30, offset=0):
    if (
        type(limit) is not int
        or not 1 <= limit <= 100
        or type(offset) is not int
        or offset < 0
    ):
        raise ValueError(
            "Agenda needs a page size of 1 to 100 and a nonnegative offset"
        )
    current = datetime.now(timezone.utc)
    today = current.astimezone().date().isoformat()
    items = sorted(_agenda_items(store, current), key=_agenda_order)
    return {
        "today": today,
        "items": items[offset : offset + limit],
        "total": len(items),
        "next_offset": offset + limit if offset + limit < len(items) else None,
        "calendar_events": sum(
            1 for item in items for _ in _calendar_slots(item, today)
        ),
    }


def _calendar_text(value):
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = "".join(
        " " if unicodedata.category(char) == "Cc" and char not in "\n\t" else char
        for char in value
    )
    return (
        value.replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace(";", "\\;")
        .replace(",", "\\,")
    )


def _fold(line):
    # RFC 5545 folds lines at 75 UTF-8 bytes, including continuation whitespace.
    parts, length = [], 0
    start = 0
    for index, char in enumerate(line):
        size = len(char.encode("utf-8"))
        if length + size > 75:
            parts.append(line[start:index])
            start, length = index, 1
        length += size
    parts.append(line[start:])
    return "\r\n ".join(parts)


def _calendar_event(item, kind, start, stamp):
    summary = ("Deadline" if kind == "deadline" else "Follow up") + ": " + item["title"]
    description = (
        "Publisher-recorded deadline. Confirm the current date with the publisher."
        if kind == "deadline"
        else "Follow-up date set in Opportunity Radar."
    )
    uid = hashlib.sha256(item["id"].encode("utf-8")).hexdigest()[:32]
    lines = [
        "BEGIN:VEVENT",
        f"UID:{uid}-{kind}@opportunity-radar.local",
        f"DTSTAMP:{stamp}",
        start,
        "SUMMARY:" + _calendar_text(summary),
        "DESCRIPTION:" + _calendar_text(description),
        "CLASS:PRIVATE",
        "TRANSP:TRANSPARENT",
    ]
    if item["url"]:
        lines.append("URL:" + quote(item["url"], safe=":/?#[]@!$&'()*+,;=%"))
    lines.append("END:VEVENT")
    return lines


def _save_calendar(path, content):
    path = Path(path).expanduser()
    if path.suffix.lower() != ".ics":
        raise ValueError("Choose an .ics filename for the calendar")
    descriptor, filename = tempfile.mkstemp(prefix=".radar-calendar-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content.encode("utf-8"))
        try:
            os.link(filename, path)
        except FileExistsError as error:
            raise ValueError(
                "A file already has that name. Choose a new calendar filename."
            ) from error
    finally:
        os.unlink(filename)
    return str(path.absolute())


def calendar_export(store, path=None):
    current = datetime.now(timezone.utc)
    stamp = current.strftime("%Y%m%dT%H%M%SZ")
    today = current.astimezone().date().isoformat()
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Opportunity Radar//Application Agenda//EN",
    ]
    count = 0
    for item in sorted(_agenda_items(store, current), key=_agenda_order):
        for kind, start in _calendar_slots(item, today):
            lines.extend(_calendar_event(item, kind, start, stamp))
            count += 1
    content = "\r\n".join(_fold(line) for line in [*lines, "END:VCALENDAR"]) + "\r\n"
    result = {"event_count": count}
    if path is None:
        result["text"] = content
    elif count:
        result["path"] = _save_calendar(path, content)
    else:
        raise ValueError("There are no upcoming dates to export")
    return result
