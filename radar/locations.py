"""Resolve location evidence against the bundled offline gazetteer."""

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache

from monitor.taxonomy import COUNTRY_ALIASES, load_taxonomy_catalog


MODES = ("onsite", "hybrid", "remote")
POSTAL_REGIONS = {
    # Official postal abbreviations for subdivisions whose gazetteer IDs are numeric.
    "AU": dict(
        zip(
            ("01", "02", "03", "04", "05", "06", "07", "08"),
            ("ACT", "NSW", "NT", "QLD", "SA", "TAS", "VIC", "WA"),
        )
    ),
    "CA": dict(
        zip(
            (
                "01",
                "02",
                "03",
                "04",
                "05",
                "07",
                "08",
                "09",
                "10",
                "11",
                "12",
                "13",
                "14",
            ),
            (
                "AB",
                "BC",
                "MB",
                "NB",
                "NL",
                "NS",
                "ON",
                "PE",
                "QC",
                "SK",
                "YT",
                "NT",
                "NU",
            ),
        )
    ),
}
MODE_WORDS = re.compile(
    r"\b(?:on site|onsite|in person|hybrid|remote|telecommute|work from home)\b"
)
SCOPE_RESTRICTION = re.compile(r"\b(?:except|excluding|excludes|outside|other than)\b")
ALTERNATIVES = re.compile(r"\s*(?:[;|/•·\n]|\s+or\s+)\s*", re.IGNORECASE)


def location_words(value):
    text = unicodedata.normalize("NFKD", value.casefold())
    text = "".join(
        character for character in text if not unicodedata.combining(character)
    )
    return " ".join(re.findall(r"[^\W_]+", text))


@dataclass(frozen=True)
class Place:
    id: str
    label: str
    level: str
    path: str
    aliases: tuple
    label_key: str
    population: int = 0


@lru_cache(maxsize=1)
def _catalog():
    data = load_taxonomy_catalog()
    countries = {code: name for code, name, _ in data["countries"]}
    regions = {(country, code): name for country, code, name, *_ in data["regions"]}
    places = {}
    exact = defaultdict(list)

    def add(id, label, level, path, aliases, population=0):
        if id in places:
            return
        aliases = tuple(
            dict.fromkeys(location_words(alias) for alias in (*aliases, label) if alias)
        )
        place = Place(
            id, label, level, path, aliases, location_words(label), population
        )
        places[id] = place
        for alias in aliases:
            exact[alias].append(place)

    for code, name, iso3 in data["countries"]:
        add(
            f"country:{code}",
            name,
            "country",
            code,
            (name, code, iso3, *COUNTRY_ALIASES.get(code, ())),
        )
    for country, code, name, *ascii_names in data["regions"]:
        postal = POSTAL_REGIONS.get(country, {}).get(code)
        postal_names = (postal, f"{country}-{postal}") if postal else ()
        add(
            f"region:{country}:{code}",
            f"{name}, {countries[country]}",
            "region",
            f"{country}/{code}",
            (name, *ascii_names, code, f"{country}-{code}", *postal_names),
        )
    for name, country, code, population, *ascii_names in data["cities"]:
        if country not in countries:
            continue
        slug = location_words(name).replace(" ", "-")
        label = ", ".join(
            dict.fromkeys(
                filter(None, (name, regions.get((country, code)), countries[country]))
            )
        )
        alternates = (
            ("New York", "NYC") if country == "US" and name == "New York City" else ()
        )
        if country == "US" and name == "Washington" and code == "DC":
            alternates = ("Washington DC", "Washington D.C.")
        path = f"{country}/{code}/{slug}"
        add(
            f"city:{country}:{code}:{slug}",
            label,
            "city",
            path,
            (name, *ascii_names, *alternates),
            population,
        )
    return places, exact


def _ancestors(path):
    parts = path.split("/")
    return tuple("/".join(parts[:index]) for index in range(1, len(parts)))


def _more_specific(path, parent):
    return path.startswith(parent + "/")


def _candidates(parts, retain_parent):
    _, exact = _catalog()
    groups = [exact.get(part, ()) for part in parts]
    if not groups[0] and retain_parent:
        # Small towns outside the gazetteer can still have a known region.
        groups = groups[1:]
    if not groups or not all(groups):
        return []
    return [
        place
        for place in dict.fromkeys(place for group in groups for place in group)
        if all(
            any(
                place.path == parent.path or _more_specific(place.path, parent.path)
                for parent in group
            )
            for group in groups
        )
    ]


@lru_cache(maxsize=8192)
def _resolve(value, retain_parent=False):
    places, _ = _catalog()
    if value in places:
        return places[value]
    parts = [
        clean for part in re.split(r"[,()]", value) if (clean := location_words(part))
    ]
    if not parts:
        return None
    choices = _candidates(parts, retain_parent)
    if len(parts) == 1:
        named_country = next(
            (
                place
                for place in choices
                if place.level == "country" and parts[0] == place.label_key
            ),
            None,
        )
        same_named_regions = [
            place
            for place in choices
            if place.level == "region" and parts[0] == place.aliases[0]
        ]
        if named_country and not same_named_regions:
            return named_country
    else:
        # "New York, NY" identifies the city beneath the same-named state.
        choices = [
            place
            for place in choices
            if not any(_more_specific(other.path, place.path) for other in choices)
        ]
    return choices[0] if len(choices) == 1 else None


def suggest_places(query, limit=12):
    if (
        not isinstance(query, str)
        or len(query) > 120
        or type(limit) is not int
        or not 1 <= limit <= 30
    ):
        raise ValueError(
            "Enter a location query up to 120 characters and a limit of 1 to 30"
        )
    if not query.strip():
        return []
    places, exact = _catalog()
    if query in places:
        place = places[query]
        return [{"id": place.id, "label": place.label, "level": place.level}]
    query = location_words(query)
    if not query:
        return []
    words = query.split()

    def rank(place):
        if query in place.aliases:
            return 0
        if any(alias.startswith(query) for alias in place.aliases):
            return 1
        label = place.label_key.split()
        return (
            2
            if all(any(token.startswith(word) for token in label) for word in words)
            else None
        )

    options = exact.get(query, ()) if len(query) == 1 else places.values()
    ranked = sorted(
        (score, -place.population, place.label, place.id, place.level)
        for place in options
        if (score := rank(place)) is not None
    )
    return [
        {"id": id, "label": label, "level": level}
        for _, _, label, id, level in ranked[:limit]
    ]


def normalize_location(value, default_policy="prefer"):
    if value is None:
        return {}
    if not isinstance(value, dict) or value.keys() - {
        "places",
        "modes",
        "policy",
        "include_unknown",
    }:
        raise ValueError("Location accepts places, modes, policy and include_unknown")
    places, modes = value.get("places", []), value.get("modes", [])
    policy = value.get("policy", default_policy)
    unknown = value.get("include_unknown", False)
    if (
        not isinstance(places, list)
        or len(places) > 12
        or any(
            not isinstance(place, str) or not place.strip() or len(place) > 120
            for place in places
        )
        or not isinstance(modes, list)
        or len(modes) > len(MODES)
        or any(mode not in MODES for mode in modes)
        or policy not in ("prefer", "require")
        or type(unknown) is not bool
    ):
        raise ValueError(
            "Choose up to 12 locations, supported work modes and a location policy"
        )
    resolved = []
    for value in places:
        place = _resolve(value.strip())
        if place is None:
            raise ValueError(
                "Choose a recognized location with its state or country to disambiguate"
            )
        resolved.append(place.id)
    if not resolved and not modes:
        return {}
    return {
        "places": list(dict.fromkeys(resolved)),
        "modes": list(dict.fromkeys(modes)),
        "policy": policy,
        "include_unknown": unknown,
    }


def place_labels(ids):
    if (
        not isinstance(ids, list)
        or len(ids) > 1200
        or any(not isinstance(id, str) or len(id) > 120 for id in ids)
    ):
        raise ValueError("Choose up to 1,200 location identifiers")
    places, _ = _catalog()
    return [
        {"id": place.id, "label": place.label, "level": place.level}
        for id in ids
        if (place := places.get(id)) is not None
    ]


def workplace_mode(value):
    """Interpret a publisher's explicit work-arrangement enum."""
    if not isinstance(value, str) or len(value) > 30:
        return ""
    mode = location_words(value)
    return "onsite" if mode == "on site" else (mode if mode in MODES else "")


def _strip_mode(part):
    match = MODE_WORDS.search(part)
    if not match:
        return "", part
    label = match.group()
    mode = (
        "remote"
        if label in {"remote", "telecommute", "work from home"}
        else ("hybrid" if label == "hybrid" else "onsite")
    )
    return mode, part[: match.start()] + " " + part[match.end() :]


@lru_cache(maxsize=16384)
def observed_places(value, published_mode=""):
    """Return (work mode, geographic path) alternatives from source fields."""
    alternatives = []
    inherit_remote_scope = False
    # The Schema.org adapter emits this prefix only for applicant scopes.
    # Preserve its distinction from "Remote or Boston, MA" work alternatives.
    value = value or ""
    scoped_remote = re.match(r"^\s*remote\s*·", value, re.IGNORECASE)
    if scoped_remote:
        value = value[scoped_remote.end() :]
        if SCOPE_RESTRICTION.search(location_words(value)):
            return (("remote", ""),)
    for raw in ALTERNATIVES.split(value):
        clean = location_words(raw)
        if not clean:
            continue
        mode, _ = _strip_mode(clean)
        if scoped_remote:
            mode = "remote"
        # Keep comma-separated publisher qualifiers for hierarchy resolution.
        # Dashes around work-mode qualifiers separate labels, not names.
        pieces = re.split(r"[,()]|\s+[-\u2013\u2014]\s+", raw)
        geographic = []
        for part in pieces:
            part = location_words(part)
            _, part = _strip_mode(part)
            part = re.sub(
                r"\b(?:only|based in|eligible in|fully|100)\b", "", part
            ).strip()
            if part:
                geographic.append(part)
        if mode == "remote" and geographic in (["anywhere"], ["worldwide"], ["global"]):
            alternatives.append((mode, "*"))
        elif mode == "remote" and SCOPE_RESTRICTION.search(clean):
            # A geographic restriction we cannot represent must stay unconfirmed.
            alternatives.append((mode, ""))
        else:
            place = _resolve(", ".join(geographic), retain_parent=True)
            if (
                not mode
                and inherit_remote_scope
                and place is not None
                and place.level == "country"
            ):
                mode = "remote"
            alternatives.append((mode, place.path if place else ""))
            inherit_remote_scope = bool(
                mode == "remote" and place is not None and place.level == "country"
            )
    fallback_mode = published_mode or ("remote" if scoped_remote else "")
    if not alternatives:
        return ((fallback_mode, ""),)
    return tuple(
        dict.fromkeys(
            (
                mode or fallback_mode,
                "" if not mode and fallback_mode == "remote" else place,
            )
            for mode, place in alternatives
        )
    )


def _paths(location):
    places, _ = _catalog()
    return (
        tuple(places[id].path for id in location["places"])
        if location["places"]
        else ()
    )


def _geo_matches(observed, wanted, mode):
    if not wanted or observed == "*":
        return True
    if not observed:
        return None
    if any(observed == place or _more_specific(observed, place) for place in wanted):
        return True
    if any(_more_specific(place, observed) for place in wanted):
        return True if mode == "remote" else None
    return False


def assess_location(observations, location):
    wanted, modes = _paths(location), location["modes"]
    options = []
    for observation in observations:
        value = observation.get("location") or ""
        published_mode = workplace_mode(
            observation.get("evidence", {}).get("workplace_type")
        )
        for mode, path in observed_places(value, published_mode):
            geo = _geo_matches(path, wanted, mode)
            work = mode in modes if mode and modes else (True if not modes else None)
            state = (
                "mismatch"
                if geo is False or work is False
                else ("match" if geo is True and work is True else "unknown")
            )
            options.append((state, value, mode))
    states = ("match", "mismatch", "unknown")
    for state in states:
        evidence = next(
            ((value, mode) for result, value, mode in options if result == state),
            None,
        )
        if evidence is not None:
            value, mode = evidence
            return {"state": state, "evidence": value, "work_mode": mode or None}
    return {"state": "unknown", "evidence": "", "work_mode": None}


def location_predicate(location, alias="l"):
    """Match indexed evidence before the ranking/pagination window is applied."""
    paths, modes = _paths(location), location["modes"]
    scope = f"SELECT 1 FROM listing_places g WHERE g.listing_id={alias}.id AND"
    mode_match, mode_miss, mode_params = "1", "0", tuple(modes)
    if modes:
        slots = ",".join("?" for _ in modes)
        mode_match = f"g.mode IN ({slots})"
        mode_miss = f"(g.mode <> '' AND g.mode NOT IN ({slots}))"
    place_match, place_miss, place_params, ancestor_params = "1", "0", (), ()
    if paths:
        ancestors = tuple(
            dict.fromkeys(parent for path in paths for parent in _ancestors(path))
        )
        clauses = ["g.place='*'"]
        for path in paths:
            clauses.append("(g.place = ? OR g.place GLOB ?)")
            place_params += (path, path + "/*")
        if ancestors:
            slots = ",".join("?" for _ in ancestors)
            clauses.append(f"(g.mode = 'remote' AND g.place IN ({slots}))")
            ancestor_params = ancestors
            place_params += ancestors
        place_match = "(" + " OR ".join(clauses) + ")"
        uncertain = f" AND g.place NOT IN ({slots})" if ancestors else ""
        place_miss = f"(g.place <> ''{uncertain} AND NOT {place_match})"
    expression = f"EXISTS ({scope} {mode_match} AND {place_match})"
    values = mode_params + place_params
    if location["include_unknown"]:
        expression = (
            f"({expression} OR NOT EXISTS ({scope} ({mode_miss} OR {place_miss})))"
        )
        values += mode_params + ancestor_params + place_params
    return expression, values


def index_listing_places(db, listing_id, locations):
    db.execute("DELETE FROM listing_places WHERE listing_id=?", (listing_id,))
    db.executemany(
        "INSERT INTO listing_places VALUES (?,?,?)",
        {
            (listing_id, mode, place)
            for value, published_mode in locations
            for mode, place in observed_places(value, published_mode)
        },
    )
