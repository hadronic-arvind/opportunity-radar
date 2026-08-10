"""Small, private onboarding flow for source packs and matching preferences."""

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from .config import load_source_packs, load_sources, project_path


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
    chosen = set(validate_pack_ids(pack_ids))
    enabled_count = 0
    for source in load_sources(include_disabled=True):
        source_packs = {str(value) for value in source.get("packs", [])}
        enabled_count += int(bool(chosen.intersection(source_packs)))
    return enabled_count


def build_profile(
    pack_ids: Sequence[str],
    include_terms: Sequence[str] = (),
    exclude_terms: Sequence[str] = (),
    locations: Sequence[str] = (),
    organizations: Sequence[str] = (),
    default_document: str = "General",
    target: str = "",
) -> Dict[str, Any]:
    rules = []
    if include_terms:
        rules.append(
            {
                "id": "preferred_work",
                "label": "Preferred work",
                "weight": 24,
                "fields": ["title", "description", "category", "opportunity_type"],
                "terms": list(include_terms),
                "per_term": True,
                "max_hits": 3,
            }
        )
    if locations:
        rules.append(
            {
                "id": "preferred_location",
                "label": "Preferred location",
                "weight": 10,
                "fields": ["location"],
                "terms": list(locations),
            }
        )
    if exclude_terms:
        rules.append(
            {
                "id": "excluded_work",
                "label": "Excluded work",
                "weight": -45,
                "fields": ["title", "description", "category", "opportunity_type"],
                "terms": list(exclude_terms),
            }
        )
    profile: Dict[str, Any] = {
        "schema_version": 2,
        "selected_source_packs": list(pack_ids),
        "priority_organizations": list(organizations),
        "matching": {"rules": rules},
        "documents": {"default": default_document or "General", "routes": []},
    }
    if target:
        profile["dashboard"] = {"target_season": target}
    return profile


def _staged_json(path: Path, payload: Dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=".{}-".format(path.name), suffix=".tmp"
    )
    temporary = Path(temporary_name)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o600)
    return temporary


def write_local_configuration(
    profile: Dict[str, Any], source_registry: Dict[str, Any], force: bool = False
) -> Tuple[Path, Path]:
    profile_path = project_path("config", "profile.local.json")
    sources_path = project_path("config", "sources.local.json")
    existing = [path.name for path in (profile_path, sources_path) if path.exists()]
    if existing and not force:
        raise FileExistsError(
            "Local configuration already exists ({}); rerun with --force only to replace it".format(
                ", ".join(existing)
            )
        )
    staged: List[Tuple[Path, Path]] = []
    try:
        staged.append((_staged_json(profile_path, profile), profile_path))
        staged.append(
            (
                _staged_json(
                    sources_path,
                    source_registry,
                ),
                sources_path,
            )
        )
        for temporary, destination in staged:
            os.replace(temporary, destination)
            os.chmod(destination, 0o600)
    finally:
        for temporary, _destination in staged:
            if temporary.exists():
                temporary.unlink()
    return profile_path, sources_path


def interactive_values() -> Dict[str, Any]:
    packs = list(available_packs().values())
    print("Choose source packs by number or id, separated by commas:")
    for index, pack in enumerate(packs, 1):
        suffix = " (default)" if pack.get("default") else ""
        print("  {:>2}. {}{} - {}".format(index, pack["id"], suffix, pack.get("description", "")))
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
        "target": input("Target season or cycle (optional): ").strip()[:120],
    }


def initialize(
    pack_ids: Sequence[str],
    include_terms: Sequence[str] = (),
    exclude_terms: Sequence[str] = (),
    locations: Sequence[str] = (),
    organizations: Sequence[str] = (),
    default_document: str = "General",
    target: str = "",
    force: bool = False,
) -> Dict[str, Any]:
    selected = validate_pack_ids(pack_ids)
    enabled_count = source_selection(selected)
    profile = build_profile(
        selected,
        include_terms,
        exclude_terms,
        locations,
        organizations,
        default_document,
        target,
    )
    profile_path, sources_path = write_local_configuration(
        profile,
        {"schema_version": 2, "selected_packs": selected, "sources": []},
        force=force,
    )
    return {
        "packs": selected,
        "enabled_sources": enabled_count,
        "profile": profile_path.name,
        "sources": sources_path.name,
    }
