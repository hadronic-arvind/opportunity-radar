"""Validate and import local, versioned profile packs."""

import hashlib
import json
import unicodedata
from contextlib import closing
from pathlib import Path

from .app import read_file
from .feed import _selected_goal, set_preferences
from .matching import normalize_rules
from .store import encode

MAX_PROFILE_BYTES = 256 * 1024
STARTER_DOMAINS = ("AI and ML research", "Clean energy", "Medicine")
EXAMPLE_PACK = (
    Path(__file__).resolve().parents[1] / "examples/profile-packs/career-pathways.json"
)


def _unique_object(pairs):
    value = dict(pairs)
    if len(value) != len(pairs):
        raise ValueError("Profile JSON contains a duplicate key")
    return value


def _name(value):
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 100
        or any(unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in value)
    ):
        raise ValueError(
            "Profile names need 1 to 100 characters without control characters"
        )
    return value.strip()


def _read_pack(path, text):
    if (path is None) == (text is None):
        raise ValueError("Choose a profile JSON file or paste profile JSON")
    if path is not None:
        raw = read_file(path, MAX_PROFILE_BYTES)
    else:
        raw = text.encode("utf-8")
    if len(raw) > MAX_PROFILE_BYTES:
        raise ValueError("Profile JSON exceeds 256 KiB")
    try:
        pack = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_object)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Invalid profile JSON at line {exc.lineno}, column {exc.colno}."
        ) from exc
    except (UnicodeError, RecursionError) as exc:
        raise ValueError("Choose a valid UTF-8 profile JSON document") from exc
    if (
        not isinstance(pack, dict)
        or pack.keys() - {"version", "profiles", "active_profile"}
        or type(pack.get("version")) is not int
        or pack["version"] != 1
    ):
        raise ValueError("Expected a version 1 profile pack")
    items = pack.get("profiles")
    if not isinstance(items, list) or not 1 <= len(items) <= 100:
        raise ValueError("Profile packs need 1 to 100 profiles")
    names = {}
    profiles = []
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict) or item.keys() != {"name", "rules"}:
            raise ValueError(f"Profile {index} must have a name and rules")
        name = _name(item["name"])
        key = name.casefold()
        if key in names:
            raise ValueError(f"Duplicate profile name at profile {index}")
        try:
            rules = normalize_rules(item["rules"])
            encode(rules).encode("utf-8")
        except UnicodeError as exc:
            raise ValueError(f"Profile {index} rules contain invalid text") from exc
        except ValueError as exc:
            raise ValueError(f"Profile {index}: {exc}") from exc
        names[key] = name
        profiles.append({"name": name, "rules": rules})
    result = {"profiles": profiles}
    if "active_profile" in pack:
        active = pack["active_profile"]
        if active is not None:
            active = names.get(_name(active).casefold())
            if active is None:
                raise ValueError("The active profile must be included in this pack")
        result["active_profile"] = active
    return result, hashlib.sha256(raw).hexdigest()


def _starter_rules():
    pack, _ = _read_pack(EXAMPLE_PACK, None)
    profiles = {profile["name"]: profile["rules"] for profile in pack["profiles"]}
    names = (
        f"{domain} - {stage}"
        for domain in STARTER_DOMAINS
        for stage in ("student", "professional")
    )
    try:
        return {name: profiles[name] for name in names}
    except KeyError as exc:
        raise ValueError("The bundled profile pack is missing a starter profile") from exc


def install_starter_profiles(store, db):
    """Add the public starter profiles inside a new workspace transaction."""
    for name, rules in _starter_rules().items():
        store.put_goal(name, rules, conn=db)
    set_preferences(store, None, conn=db)


def clear_pristine_starters(store, db):
    """Allow legacy migration after the new dashboard has created its workspace."""
    starters = _starter_rules()
    history = [
        tuple(row)
        for row in db.execute(
            "SELECT name,previous FROM goal_history LIMIT ?", (len(starters) + 1,)
        )
    ]
    if len(history) != len(starters) or set(history) != {(name, None) for name in starters}:
        return False
    if _selected_goal(store, db) != (True, None):
        return False
    if {profile["name"]: profile["rules"] for profile in store.goals(db)} != starters:
        return False
    db.execute("DELETE FROM goals")
    db.execute("DELETE FROM goal_history")
    db.execute("DELETE FROM dashboard_preferences")
    return True


def _plan(store, db, pack, fingerprint, replace):
    if type(replace) is not bool:
        raise ValueError("Replace must be true or false")
    existing = store.goals(db)
    existing_names = [profile["name"] for profile in existing]
    by_name = {}
    for name in existing_names:
        by_name.setdefault(name.casefold(), []).append(name)
    profiles, conflicts = [], []
    for profile in pack["profiles"]:
        name = profile["name"]
        matches = by_name.get(name.casefold(), [])
        if len(matches) > 1:
            raise ValueError("More than one saved profile matches an imported name")
        if matches:
            name = matches[0]
            conflicts.append(name)
        profiles.append({**profile, "name": name})
    incoming = {profile["name"].casefold(): profile["name"] for profile in profiles}
    revision = hashlib.sha256(
        encode([fingerprint, replace, existing, _selected_goal(store, db)]).encode(
            "utf-8"
        )
    ).hexdigest()
    result = {
        "profiles": profiles,
        "existing_profiles": existing_names,
        "conflicts": conflicts,
        "removed": [
            name
            for name in existing_names
            if replace and name.casefold() not in incoming
        ],
        "revision": revision,
    }
    if "active_profile" in pack:
        active = pack["active_profile"]
        result["active_profile"] = (
            incoming[active.casefold()] if active is not None else None
        )
    return result


def preview_profiles(store, *, path=None, text=None, replace=False):
    pack, fingerprint = _read_pack(path, text)
    with closing(store.connect()) as db, db:
        db.execute("BEGIN")
        return _plan(store, db, pack, fingerprint, replace)


def import_profiles(
    store, *, path=None, text=None, replace=False, expected_revision=None
):
    pack, fingerprint = _read_pack(path, text)
    with store.transaction() as db:
        result = _plan(store, db, pack, fingerprint, replace)
        if expected_revision is not None and expected_revision != result["revision"]:
            raise ValueError(
                "The file or workspace profiles changed. Preview the import again."
            )
        for profile in result["profiles"]:
            store.put_goal(profile["name"], profile["rules"], conn=db)
        for name in result["removed"]:
            store.remove_goal(name, conn=db)
        if "active_profile" in result:
            set_preferences(store, result["active_profile"], conn=db)
    return result
