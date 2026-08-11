"""Small, private onboarding flow for source packs and matching preferences."""

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .config import load_source_packs, load_sources
from .profile import initialize_local_configuration


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


def source_selection_counts(pack_ids: Sequence[str]) -> Dict[str, int]:
    chosen = set(validate_pack_ids(pack_ids))
    counts = {"enabled_sources": 0, "listing_feeds": 0, "manual_pages": 0}
    for source in load_sources(include_disabled=True):
        source_packs = {str(value) for value in source.get("packs", [])}
        if not chosen.intersection(source_packs):
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
    packs = list(available_packs().values())
    sources = load_sources(include_disabled=True)
    print("Choose source packs by number or id, separated by commas:")
    for index, pack in enumerate(packs, 1):
        suffix = " (default)" if pack.get("default") else ""
        members = [source for source in sources if pack["id"] in source.get("packs", [])]
        feeds = sum(source.get("kind") != "watch_page" for source in members)
        print(
            "  {:>2}. {}{} - {} feeds, {} manual pages - {}".format(
                index,
                pack["id"],
                suffix,
                feeds,
                len(members) - feeds,
                pack.get("description", ""),
            )
        )
    raw = input("Packs [{}]: ".format(",".join(default_pack_ids()))).strip()
    tokens = comma_values(raw) if raw else default_pack_ids()
    selected = []
    for token in tokens:
        if token.isdigit() and 1 <= int(token) <= len(packs):
            selected.append(str(packs[int(token) - 1]["id"]))
        else:
            selected.append(token)
    return {
        "pack_ids": validate_pack_ids(selected),
        "include_terms": comma_values(input("Roles, skills, or domains to favor (optional): ")),
        "exclude_terms": comma_values(input("Terms to exclude (optional): ")),
        "locations": comma_values(input("Preferred locations or remote (optional): ")),
        "organizations": comma_values(input("Preferred organizations (optional): ")),
        "default_document": input("Default resume/CV label [General]: ").strip() or "General",
        "timeframes": comma_values(
            input("Target seasons or cycles, separated by commas (optional): ")
        ),
        "target": "",
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
    force: bool = False,
) -> Dict[str, Any]:
    selected = validate_pack_ids(pack_ids)
    source_counts = source_selection_counts(selected)
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
    profile_path, sources_path = write_local_configuration(
        profile,
        {"schema_version": 2, "selected_packs": selected, "sources": []},
        force=force,
        known_pack_ids=available_packs(),
    )
    return {
        "packs": selected,
        **source_counts,
        "profile": profile_path.name,
        "sources": sources_path.name,
    }
