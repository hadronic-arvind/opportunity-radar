"""Small, private onboarding flow for source packs and matching preferences."""

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .config import load_source_packs, load_sources
from .coverage import (
    AUTOMATIC_PRESET,
    MANUAL_PRESET,
    coverage_preset_payload,
    effective_source_packs,
    target_defaults_for_preset,
    validate_coverage_preset,
)
from .profile import initialize_local_configuration
from .text import normalize_opportunity_type


def comma_values(value: str) -> List[str]:
    output = []
    for entry in str(value or "").split(","):
        cleaned = " ".join(entry.strip().split())
        if cleaned and cleaned not in output:
            output.append(cleaned[:120])
    return output[:100]


def available_packs() -> Dict[str, Dict[str, Any]]:
    return {str(pack["id"]): pack for pack in load_source_packs()}


def default_pack_ids() -> List[str]:
    return [pack_id for pack_id, pack in available_packs().items() if pack.get("default")]


def validate_pack_ids(pack_ids: Iterable[str]) -> List[str]:
    catalog = available_packs()
    selected = []
    for pack_id in pack_ids:
        value = str(pack_id).strip()
        if value and value not in selected:
            if value not in catalog:
                raise ValueError("Unknown source pack: {}".format(value))
            selected.append(value)
    if not selected:
        raise ValueError("Select at least one source pack")
    return selected


def source_selection(pack_ids: Sequence[str]) -> int:
    return source_selection_counts(pack_ids)["enabled_sources"]


def source_selection_counts(
    pack_ids: Sequence[str], profile: Optional[Dict[str, Any]] = None, preset: str = MANUAL_PRESET,
) -> Dict[str, int]:
    chosen = set(effective_source_packs(profile or {}, validate_pack_ids(pack_ids), preset))
    counts = {"enabled_sources": 0, "listing_feeds": 0, "manual_pages": 0}
    for source in load_sources(include_disabled=True):
        source_packs = {str(value) for value in source.get("packs", [])}
        if not chosen.intersection(source_packs):
            continue
        if not source.get("auto_enable", True):
            counts["manual_pages"] += int(source.get("kind") == "watch_page")
            continue
        counts["enabled_sources"] += 1
        if source.get("kind") != "watch_page":
            counts["listing_feeds"] += 1
        else:
            counts["manual_pages"] += 1
    return counts


def build_profile(
    pack_ids: Sequence[str],
    include_terms: Sequence[str] = (),
    exclude_terms: Sequence[str] = (),
    locations: Sequence[str] = (),
    organizations: Sequence[str] = (),
    default_document: str = "General",
    target: str = "",
    timeframes: Sequence[str] = (),
) -> Dict[str, Any]:
    configured_timeframes = list(timeframes)
    if target and target not in configured_timeframes:
        configured_timeframes.append(target)
    profile: Dict[str, Any] = {
        "schema_version": 2,
        "timeframes": configured_timeframes,
        "candidate": {
            "completed_degrees": [],
            "skills": [],
        },
        "targets": {
            "opportunity_types": [],
            "cycles": [
                {"label": timeframe}
                for timeframe in configured_timeframes
            ],
            "role_families": list(include_terms),
            "domains": [],
            "supporting_skills": [],
            "locations": list(locations),
            "exclusions": list(exclude_terms),
            "work_arrangements": [],
        },
        "priority_organizations": list(organizations),
        "matching": {
            "engine": "structured_v2",
            "base_score": 25,
            "minimum_display_score": 40,
            "rules": [],
        },
        "documents": {"default": default_document or "General", "routes": []},
    }
    if configured_timeframes:
        profile["dashboard"] = {
            "timeframes": configured_timeframes,
            "target_season": (
                configured_timeframes[0] if len(configured_timeframes) == 1 else ""
            ),
        }
    return profile


def write_local_configuration(
    profile: Dict[str, Any],
    source_registry: Dict[str, Any],
    force: bool = False,
    known_pack_ids: Optional[Iterable[str]] = None,
) -> Tuple[Path, Path]:
    destinations, _refresh = initialize_local_configuration(
        profile,
        source_registry,
        force=force,
        known_pack_ids=known_pack_ids,
    )
    return destinations


def interactive_values() -> Dict[str, Any]:
    print("Set up your private opportunity search. Press Enter to skip any preference.")
    opportunity_types = comma_values(input(
        "What are you looking for? (jobs, internships, fellowships, grants, hackathons): "
    ))
    include_terms = comma_values(input("Roles you want to do (separate with commas): "))
    domains = comma_values(input("Fields or topics you want to explore: "))
    locations = comma_values(input("Preferred locations: "))
    preset = input("Coverage [automatic] (enter ? to see choices): ").strip().casefold()
    if preset == "?":
        print("automatic - Start across industries and add sources relevant to your goals")
        print("manual - Use only the packs you select")
        for option in coverage_preset_payload():
            print("{} - {}".format(option["id"], option["description"]))
        preset = input("Coverage [automatic]: ").strip().casefold()
    preset = validate_coverage_preset(preset or AUTOMATIC_PRESET)
    selected = default_pack_ids()
    if preset == MANUAL_PRESET:
        for pack in available_packs().values():
            print("{} - {}".format(pack["id"], pack.get("name", pack["id"])))
        raw = input("Packs [{}]: ".format(", ".join(selected)))
        selected = comma_values(raw) or selected
    return {
        "pack_ids": selected,
        "coverage_preset": preset,
        "include_terms": include_terms,
        "domains": domains,
        "locations": locations,
        "opportunity_types": opportunity_types,
    }


def initialize(
    pack_ids: Sequence[str],
    include_terms: Sequence[str] = (),
    exclude_terms: Sequence[str] = (),
    locations: Sequence[str] = (),
    organizations: Sequence[str] = (),
    default_document: str = "General",
    target: str = "",
    timeframes: Sequence[str] = (),
    coverage_preset: str = MANUAL_PRESET,
    force: bool = False,
    opportunity_types: Sequence[str] = (),
    domains: Sequence[str] = (),
) -> Dict[str, Any]:
    selected = validate_pack_ids(pack_ids)
    preset = validate_coverage_preset(coverage_preset)
    profile = build_profile(
        selected,
        include_terms,
        exclude_terms,
        locations,
        organizations,
        default_document,
        target,
        timeframes,
    )
    kinds = []
    for value in opportunity_types:
        kind = normalize_opportunity_type(value)
        if not kind:
            raise ValueError("Unrecognized opportunity type: {}".format(value))
        kinds.append(kind)
    profile["targets"]["opportunity_types"] = list(dict.fromkeys(kinds))
    profile["targets"]["domains"] = list(domains)
    defaults = target_defaults_for_preset(preset)
    for key, values in defaults.items():
        existing = profile["targets"].get(key, [])
        profile["targets"][key] = list(dict.fromkeys(list(existing) + values))
    profile_path, sources_path = write_local_configuration(
        profile,
        {
            "schema_version": 2,
            "coverage_preset": preset,
            "selected_packs": selected,
            "sources": [],
        },
        force=force,
        known_pack_ids=available_packs(),
    )
    return {
        "packs": selected,
        "coverage_preset": preset,
        **source_selection_counts(selected, profile, preset),
        "profile": profile_path.name,
        "sources": sources_path.name,
    }
