"""Offline location hierarchy and education taxonomy helpers."""

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Set, Tuple


CATALOG_PATH = Path(__file__).with_name("taxonomy_catalog.json")
CATALOG_VERSION = 1
MAX_LOCATION_PREFERENCES = 100
COUNTRY_ALIASES = {
    "US": ["USA", "United States of America", "U.S.", "U.S.A."],
    "GB": ["UK", "Great Britain", "U.K."],
    "AE": ["UAE", "U.A.E."],
    "KR": ["South Korea", "Republic of Korea"],
    "KP": ["North Korea"],
    "RU": ["Russia"],
    "TW": ["Taiwan"],
    "VN": ["Vietnam"],
}


def _normalized(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(character for character in text if not unicodedata.combining(character))
    return " ".join(re.findall(r"[a-z0-9]+", text.casefold()))


def _components(value: Any) -> List[str]:
    text = str(value or "")
    parts = re.split(r"\s*(?:,|;|/|\||•|\(|\)|\s+-\s+)\s*", text)
    values = [_normalized(part) for part in parts]
    whole = _normalized(text)
    return list(dict.fromkeys([value for value in values + [whole] if value]))


@lru_cache(maxsize=1)
def load_taxonomy_catalog() -> Dict[str, Any]:
    payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("version") != CATALOG_VERSION:
        raise ValueError("Taxonomy catalog is unavailable or incompatible")
    for key in ("countries", "regions", "cities", "education_fields"):
        if not isinstance(payload.get(key), list):
            raise ValueError("Taxonomy catalog is malformed")
    return payload


def taxonomy_payload() -> Dict[str, Any]:
    """Return the trusted public catalog for the self-contained dashboard."""
    return load_taxonomy_catalog()


@lru_cache(maxsize=1)
def _location_indexes() -> Dict[str, Any]:
    payload = load_taxonomy_catalog()
    countries: Dict[str, str] = {}
    country_labels: Dict[str, str] = {}
    country_names: Dict[str, str] = {}
    for entry in payload["countries"]:
        code, name, iso3 = str(entry[0]), str(entry[1]), str(entry[2])
        country_names[code] = name
        country_labels[_normalized(name)] = code
        for alias in [code, iso3, name] + COUNTRY_ALIASES.get(code, []):
            key = _normalized(alias)
            if key:
                countries[key] = code

    regions: Dict[str, List[Tuple[str, str]]] = {}
    region_names: Dict[Tuple[str, str], str] = {}
    for entry in payload["regions"]:
        country, code, name = str(entry[0]), str(entry[1]), str(entry[2])
        ascii_name = str(entry[3]) if len(entry) > 3 else ""
        region_names[(country, code)] = name
        for alias in (name, ascii_name, code):
            key = _normalized(alias)
            if key:
                regions.setdefault(key, []).append((country, code))

    cities: Dict[str, List[Tuple[str, str, str, int]]] = {}
    for entry in payload["cities"]:
        name, country, region = str(entry[0]), str(entry[1]), str(entry[2])
        population = int(entry[3])
        ascii_name = str(entry[4]) if len(entry) > 4 else ""
        city = (country, region, name, population)
        for alias in (name, ascii_name):
            key = _normalized(alias)
            if key:
                cities.setdefault(key, []).append(city)
    for values in cities.values():
        values.sort(key=lambda entry: entry[3], reverse=True)
    return {
        "countries": countries,
        "country_labels": country_labels,
        "country_names": country_names,
        "regions": regions,
        "region_names": region_names,
        "cities": cities,
    }


def _geography(value: Any) -> Dict[str, Set[Any]]:
    indexes = _location_indexes()
    components = _components(value)
    countries: Set[str] = set()
    named_countries = {
        indexes["country_labels"][component]
        for component in components
        if component in indexes["country_labels"]
    }
    regions: Set[Tuple[str, str]] = set()
    city_values: Set[Tuple[str, str, str]] = set()

    for component in components:
        country = indexes["countries"].get(component)
        if country:
            countries.add(country)
    # A single exact country label is authoritative. This keeps names such as
    # Georgia and Mexico from being reinterpreted as same-named regions.
    if len(components) == 1 and components[0] in indexes["country_labels"]:
        return {
            "countries": {indexes["country_labels"][components[0]]},
            "regions": set(),
            "cities": set(),
        }

    region_choices = []
    for component in components:
        region_choices.extend(indexes["regions"].get(component, []))
    city_choices = []
    for component in components:
        city_choices.extend(indexes["cities"].get(component, []))
    for component in components:
        if len(component) != 2:
            continue
        subdivision = next(
            (
                entry for entry in region_choices
                if entry[0] == "US" and component == _normalized(entry[1])
            ),
            None,
        )
        ambiguous_country = indexes["countries"].get(component)
        if not subdivision or not ambiguous_country or ambiguous_country in named_countries:
            continue
        country_population = max(
            (entry[3] for entry in city_choices if entry[0] == ambiguous_country),
            default=0,
        )
        subdivision_population = max(
            (
                entry[3] for entry in city_choices
                if (entry[0], entry[1]) == subdivision
            ),
            default=0,
        )
        if country_population > subdivision_population:
            region_choices = [entry for entry in region_choices if entry != subdivision]
        else:
            countries.discard(ambiguous_country)
    if countries:
        narrowed = [entry for entry in region_choices if entry[0] in countries]
        if narrowed:
            region_choices = narrowed
    regions.update(region_choices)
    countries.update(country for country, _region in regions)

    if countries:
        narrowed = [entry for entry in city_choices if entry[0] in countries]
        if narrowed:
            city_choices = narrowed
    if regions:
        narrowed = [entry for entry in city_choices if (entry[0], entry[1]) in regions]
        if narrowed:
            city_choices = narrowed
    if city_choices and not countries and not regions:
        largest = max(entry[3] for entry in city_choices)
        city_choices = [entry for entry in city_choices if entry[3] == largest]
    for country, region, name, _population in city_choices:
        city_values.add((country, region, _normalized(name)))
        countries.add(country)
        if region:
            regions.add((country, region))
    return {"countries": countries, "regions": regions, "cities": city_values}


def canonical_location(value: Any) -> str:
    """Return a canonical country, region, or city label when resolvable."""
    clean = " ".join(str(value or "").strip().split())
    if not clean:
        return ""
    geography = _geography(clean)
    indexes = _location_indexes()
    if len(geography["cities"]) == 1:
        country, region, city_key = next(iter(geography["cities"]))
        choices = indexes["cities"].get(city_key, [])
        name = next(
            (entry[2] for entry in choices if entry[0] == country and entry[1] == region),
            clean,
        )
        region_name = indexes["region_names"].get((country, region), "")
        return ", ".join(
            part for part in (name, region_name, indexes["country_names"].get(country, "")) if part
        )
    if len(geography["regions"]) == 1 and not geography["cities"]:
        country, region = next(iter(geography["regions"]))
        return ", ".join(
            part
            for part in (
                indexes["region_names"].get((country, region), ""),
                indexes["country_names"].get(country, ""),
            )
            if part
        )
    if len(geography["countries"]) == 1 and not geography["regions"]:
        return indexes["country_names"].get(next(iter(geography["countries"])), clean)
    return clean[:120]


def normalize_locations(values: Sequence[str]) -> List[str]:
    output = []
    seen = set()
    for value in values[:MAX_LOCATION_PREFERENCES]:
        canonical = canonical_location(value)
        key = _normalized(canonical)
        if canonical and key not in seen:
            seen.add(key)
            output.append(canonical)
    return output


def _fallback_match(preference: str, listing: str) -> bool:
    term = _normalized(preference)
    text = _normalized(listing)
    return bool(term and re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", text))


def location_match_details(preferences: Iterable[str], listing: Any) -> Dict[str, Any]:
    """Match city, region, and country preferences against a listing location."""
    clean_preferences = [str(value) for value in preferences if str(value).strip()]
    listing_text = str(listing or "").strip()
    listing_geo = _geography(listing_text)
    evidence = []
    resolved_preferences = False
    for preference in clean_preferences:
        preferred_geo = _geography(preference)
        resolved = any(preferred_geo.values())
        resolved_preferences = resolved_preferences or resolved
        matched = False
        if preferred_geo["cities"]:
            matched = bool(preferred_geo["cities"].intersection(listing_geo["cities"]))
        elif preferred_geo["regions"]:
            matched = bool(preferred_geo["regions"].intersection(listing_geo["regions"]))
        elif preferred_geo["countries"]:
            matched = bool(preferred_geo["countries"].intersection(listing_geo["countries"]))
        else:
            matched = _fallback_match(preference, listing_text)
        if matched:
            evidence.append(canonical_location(preference))
    listing_resolved = any(listing_geo.values())
    return {
        "matched": bool(evidence),
        "evidence": list(dict.fromkeys(evidence)),
        "state": "match" if evidence else "mismatch" if resolved_preferences and listing_resolved else "unknown",
        "listing_resolved": listing_resolved,
    }
