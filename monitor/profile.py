"""Validated, private profile editing shared by the CLI and native app."""

import hashlib
import hmac
import json
import math
import os
import re
import stat
import sys
import tempfile
import time
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple

from . import config
from .scoring import DEFAULT_FIELDS, MATCH_FIELDS


EDITOR_VERSION = 1
MAX_EDITOR_BYTES = 256 * 1024
MAX_CONFIG_BYTES = 2 * 1024 * 1024
MAX_LIST_VALUES = 100
MAX_RULES = 100
MAX_RULE_TERMS = 100
MAX_TOTAL_RULE_TERMS = 1200
MAX_DOCUMENT_ROUTES = 50
MAX_TIMEFRAMES = 12
MAX_LIFECYCLE_OWNER_AGE_SECONDS = 12 * 60 * 60
STALE_OWNERLESS_LOCK_AGE_SECONDS = 12 * 60 * 60
LIFECYCLE_OWNER_FILE = "owner.pid"
REVISION_RE = re.compile(r"^[a-f0-9]{64}$")
RULE_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
EXPECTED_EDITOR_KEYS = {
    "version",
    "expected_revision",
    "timeframes",
    "selected_packs",
    "candidate",
    "targets",
    "priority_organizations",
    "matching",
    "documents",
}
CANDIDATE_KEYS = {
    "current_stage",
    "expected_graduation",
    "completed_degrees",
    "skills",
    "max_required_experience_years",
}
TARGET_LIST_KEYS = {
    "opportunity_types",
    "role_families",
    "domains",
    "supporting_skills",
    "locations",
    "exclusions",
    "work_arrangements",
}
TARGET_BOOLEAN_KEYS = {"strict_opportunity_types", "strict_timeframes"}
TARGET_SCALAR_KEYS = {"remote_preference"}
MATCHING_KEYS = {
    "engine",
    "base_score",
    "priority_organization_bonus",
    "minimum_display_score",
    "tier_thresholds",
    "rules",
    "field_weights",
    "score_ceilings",
    "anchor_min_strength",
    "target_type_bonus",
    "target_timeframe_bonus",
    "profile_weights",
}
LEGACY_INERT_MATCHING_KEYS = {
    "visibility",
    "dimension_weights",
    "score_caps",
    "target_bonuses",
}
DOCUMENT_KEYS = {"default", "routes"}
LEGACY_PROFILE_KEYS = {
    "positive_rules",
    "negative_rules",
    "default_resume_code",
    "resume_routing",
    "exclusions",
}
LEGACY_CANDIDATE_KEYS = {"career_stage", "target_season"}
LEGACY_TARGET_KEYS = {
    "roles",
    "skills",
    "workplace_types",
    "timeframes",
    "target_cycles",
}


class ProfileValidationError(ValueError):
    """Raised when an editor request cannot safely become local configuration."""


def _reject_json_constant(value: str) -> None:
    raise ProfileValidationError("Profile editor input contains a non-finite number")


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        size = path.stat().st_size
    except OSError as error:
        raise ProfileValidationError("Configuration is unavailable: {}".format(path.name)) from error
    if size > MAX_CONFIG_BYTES:
        raise ProfileValidationError("Configuration is too large: {}".format(path.name))
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=_reject_json_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ProfileValidationError("Configuration is not valid JSON: {}".format(path.name)) from error
    if not isinstance(payload, dict):
        raise ProfileValidationError("Configuration must be a JSON object: {}".format(path.name))
    return payload


def read_editor_json(raw: str) -> Dict[str, Any]:
    encoded = raw.encode("utf-8")
    if len(encoded) > MAX_EDITOR_BYTES:
        raise ProfileValidationError("Profile editor input is too large")
    try:
        payload = json.loads(raw, parse_constant=_reject_json_constant)
    except json.JSONDecodeError as error:
        raise ProfileValidationError("Profile editor input is not valid JSON") from error
    if not isinstance(payload, dict):
        raise ProfileValidationError("Profile editor input must be a JSON object")
    return payload


def read_editor_file(path: Path) -> Dict[str, Any]:
    try:
        size = path.stat().st_size
    except OSError as error:
        raise ProfileValidationError("Profile input file is unavailable") from error
    if size > MAX_EDITOR_BYTES:
        raise ProfileValidationError("Profile editor input is too large")
    try:
        return read_editor_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as error:
        raise ProfileValidationError("Profile input file could not be read") from error


def _clean_string(value: Any, label: str, maximum: int = 120) -> str:
    if not isinstance(value, str):
        raise ProfileValidationError("{} must be text".format(label))
    cleaned = " ".join(value.strip().split())
    if not cleaned:
        raise ProfileValidationError("{} must not be empty".format(label))
    if len(cleaned) > maximum:
        raise ProfileValidationError("{} must be at most {} characters".format(label, maximum))
    if any(ord(character) < 32 or ord(character) == 127 for character in cleaned):
        raise ProfileValidationError("{} contains control characters".format(label))
    return cleaned


def _string_list(
    value: Any,
    label: str,
    maximum_items: int = MAX_LIST_VALUES,
    maximum_length: int = 120,
) -> List[str]:
    if not isinstance(value, list):
        raise ProfileValidationError("{} must be a list".format(label))
    if len(value) > maximum_items:
        raise ProfileValidationError("{} contains too many values".format(label))
    output: List[str] = []
    seen = set()
    for index, entry in enumerate(value):
        cleaned = _clean_string(
            entry,
            "{} item {}".format(label, index + 1),
            maximum_length,
        )
        key = cleaned.casefold()
        if key not in seen:
            seen.add(key)
            output.append(cleaned)
    return output


def _bounded_integer(value: Any, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProfileValidationError("{} must be an integer".format(label))
    if value < minimum or value > maximum:
        raise ProfileValidationError(
            "{} must be from {} to {}".format(label, minimum, maximum)
        )
    return value


def _bounded_number(value: Any, label: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProfileValidationError("{} must be a number".format(label))
    result = float(value)
    if not math.isfinite(result):
        raise ProfileValidationError("{} must be a finite number".format(label))
    if result < minimum or result > maximum:
        raise ProfileValidationError(
            "{} must be from {} to {}".format(label, minimum, maximum)
        )
    return result


def _rule_id(value: Any, fallback: str, used: set) -> str:
    candidate = str(value or "").strip().lower()
    candidate = re.sub(r"[^a-z0-9_]+", "_", candidate).strip("_")
    if not candidate:
        candidate = fallback
    if not RULE_ID_RE.fullmatch(candidate):
        raise ProfileValidationError("Matching rule id is invalid: {}".format(candidate[:80]))
    base = candidate
    suffix = 2
    while candidate in used:
        candidate = "{}_{}".format(base[:60], suffix)
        suffix += 1
    used.add(candidate)
    return candidate


def _normalize_rule(rule: Any, index: int, used: set) -> Dict[str, Any]:
    if not isinstance(rule, dict):
        raise ProfileValidationError("Matching rule {} must be an object".format(index + 1))
    allowed_rule_keys = {
        "id",
        "label",
        "weight",
        "fields",
        "terms",
        "match",
        "per_term",
        "max_hits",
        "dimension",
        "anchor",
        "hard_gate",
    }
    if set(rule) - allowed_rule_keys:
        raise ProfileValidationError(
            "Matching rule {} has unsupported keys".format(index + 1)
        )
    label = _clean_string(
        rule.get("label", "Configured match"),
        "Matching rule {} label".format(index + 1),
        100,
    )
    terms = _string_list(
        rule.get("terms", []),
        "Matching rule {} terms".format(index + 1),
        MAX_RULE_TERMS,
    )
    if not terms:
        raise ProfileValidationError("Matching rule {} needs at least one term".format(index + 1))
    fields = _string_list(
        rule.get("fields", list(DEFAULT_FIELDS)),
        "Matching rule {} fields".format(index + 1),
        len(MATCH_FIELDS),
        40,
    )
    if not fields or any(field not in MATCH_FIELDS for field in fields):
        raise ProfileValidationError("Matching rule {} has invalid fields".format(index + 1))
    mode = str(rule.get("match", "any")).strip().lower()
    if mode not in {"any", "all"}:
        raise ProfileValidationError("Matching rule {} has an invalid match mode".format(index + 1))
    per_term = rule.get("per_term", False)
    if not isinstance(per_term, bool):
        raise ProfileValidationError("Matching rule {} per_term must be true or false".format(index + 1))
    normalized: Dict[str, Any] = {
        "id": _rule_id(rule.get("id"), "rule_{}".format(index + 1), used),
        "label": label,
        "weight": _bounded_integer(
            rule.get("weight", 0),
            "Matching rule {} weight".format(index + 1),
            -100,
            100,
        ),
        "fields": fields,
        "terms": terms,
        "match": mode,
        "per_term": per_term,
    }
    if "dimension" in rule:
        normalized["dimension"] = _clean_string(
            rule["dimension"],
            "Matching rule {} dimension".format(index + 1),
            40,
        ).lower()
    default_anchor = (
        normalized.get("dimension", "interest") == "interest"
        and normalized["weight"] > 0
    )
    for boolean_key, default in (
        ("anchor", default_anchor),
        ("hard_gate", False),
    ):
        configured = rule.get(boolean_key, default)
        if not isinstance(configured, bool):
            raise ProfileValidationError(
                "Matching rule {} {} must be true or false".format(
                    index + 1, boolean_key
                )
            )
        normalized[boolean_key] = configured
    if per_term:
        normalized["max_hits"] = _bounded_integer(
            rule.get("max_hits", len(terms)),
            "Matching rule {} max_hits".format(index + 1),
            1,
            MAX_RULE_TERMS,
        )
    return normalized


def _legacy_rules(profile: Mapping[str, Any]) -> List[Dict[str, Any]]:
    matching = profile.get("matching", {})
    rules = matching.get("rules", []) if isinstance(matching, dict) else []
    output = [dict(rule) for rule in rules if isinstance(rule, dict)] if isinstance(rules, list) else []
    for rule in profile.get("positive_rules", []):
        if isinstance(rule, dict):
            migrated = dict(rule)
            migrated["weight"] = abs(int(rule.get("weight", 0)))
            output.append(migrated)
    for rule in profile.get("negative_rules", []):
        if isinstance(rule, dict):
            migrated = dict(rule)
            migrated["weight"] = -abs(int(rule.get("weight", 0)))
            output.append(migrated)
    return output


def _matching_projection(profile: Mapping[str, Any]) -> Dict[str, Any]:
    configured = profile.get("matching", {})
    matching = dict(configured) if isinstance(configured, dict) else {}
    matching["rules"] = _legacy_rules(profile)
    # Every profile edited through the app or current CLI uses the structured,
    # field-aware scorer. This makes simple editor fields effective even when a
    # pre-editor profile did not have an explicit engine setting.
    matching.setdefault("engine", "structured_v2")
    return {key: deepcopy(value) for key, value in matching.items() if key in MATCHING_KEYS}


def _normalize_matching(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ProfileValidationError("matching must be an object")
    unknown = sorted(set(value) - MATCHING_KEYS)
    if unknown:
        raise ProfileValidationError("matching has unsupported keys: {}".format(", ".join(unknown)))
    base = _bounded_integer(value.get("base_score", 50), "matching.base_score", 0, 100)
    bonus = _bounded_integer(
        value.get("priority_organization_bonus", 10),
        "matching.priority_organization_bonus",
        -100,
        100,
    )
    thresholds = value.get("tier_thresholds", {})
    if not isinstance(thresholds, dict):
        raise ProfileValidationError("matching.tier_thresholds must be an object")
    unknown_thresholds = sorted(set(thresholds) - {"priority", "strong", "watch"})
    if unknown_thresholds:
        raise ProfileValidationError("matching.tier_thresholds has unsupported keys")
    normalized_thresholds = {
        "priority": _bounded_integer(
            thresholds.get("priority", 75), "Priority threshold", 0, 100
        ),
        "strong": _bounded_integer(
            thresholds.get("strong", 55), "Strong threshold", 0, 100
        ),
        "watch": _bounded_integer(
            thresholds.get("watch", 25), "Visibility threshold", 0, 100
        ),
    }
    if not (
        normalized_thresholds["priority"]
        >= normalized_thresholds["strong"]
        >= normalized_thresholds["watch"]
    ):
        raise ProfileValidationError(
            "Thresholds must be ordered priority >= strong >= watch"
        )
    raw_rules = value.get("rules", [])
    if not isinstance(raw_rules, list) or len(raw_rules) > MAX_RULES:
        raise ProfileValidationError("matching.rules must be a bounded list")
    used: set = set()
    rules = [_normalize_rule(rule, index, used) for index, rule in enumerate(raw_rules)]
    if sum(len(rule["terms"]) for rule in rules) > MAX_TOTAL_RULE_TERMS:
        raise ProfileValidationError("matching.rules contains too many terms")
    normalized: Dict[str, Any] = {
        "base_score": base,
        "priority_organization_bonus": bonus,
        "tier_thresholds": normalized_thresholds,
        "rules": rules,
    }
    if "engine" in value:
        engine = _clean_string(value["engine"], "matching.engine", 40).casefold()
        if engine not in {"legacy", "structured_v2"}:
            raise ProfileValidationError("matching.engine is unsupported")
        normalized["engine"] = engine
    if "minimum_display_score" in value:
        normalized["minimum_display_score"] = _bounded_integer(
            value["minimum_display_score"],
            "matching.minimum_display_score",
            0,
            100,
        )
    if "anchor_min_strength" in value:
        normalized["anchor_min_strength"] = _bounded_number(
            value["anchor_min_strength"], "matching.anchor_min_strength", 0.0, 1.0
        )
    for bonus_key in ("target_type_bonus", "target_timeframe_bonus"):
        if bonus_key in value:
            normalized[bonus_key] = _bounded_integer(
                value[bonus_key], "matching.{}".format(bonus_key), -100, 100
            )
    for optional, minimum, maximum, integers, allowed_keys in (
        ("field_weights", 0.0, 1.0, False, MATCH_FIELDS),
        (
            "score_ceilings",
            0.0,
            100.0,
            True,
            {
                "no_anchor",
                "description_only",
                "description_exclusion",
                "unknown_eligibility",
            },
        ),
        (
            "profile_weights",
            -100.0,
            100.0,
            True,
            {
                "role_family",
                "domain",
                "skill",
                "location",
                "work_arrangement",
                "description_exclusion",
            },
        ),
    ):
        if optional not in value:
            continue
        raw_map = value[optional]
        if not isinstance(raw_map, dict) or len(raw_map) > 40:
            raise ProfileValidationError("matching.{} must be a bounded object".format(optional))
        if allowed_keys is not None and set(raw_map) - set(allowed_keys):
            raise ProfileValidationError("matching.{} has unsupported keys".format(optional))
        normalized_map: Dict[str, Any] = {}
        for raw_key, raw_value in raw_map.items():
            key = _clean_string(raw_key, "matching.{} key".format(optional), 50)
            if integers:
                normalized_map[key] = _bounded_integer(
                    raw_value,
                    "matching.{}.{}".format(optional, key),
                    int(minimum),
                    int(maximum),
                )
            else:
                normalized_map[key] = _bounded_number(
                    raw_value,
                    "matching.{}.{}".format(optional, key),
                    minimum,
                    maximum,
                )
        normalized[optional] = normalized_map
    return normalized


def _document_projection(profile: Mapping[str, Any]) -> Dict[str, Any]:
    configured = profile.get("documents", {})
    documents = dict(configured) if isinstance(configured, dict) else {}
    documents.setdefault("default", profile.get("default_resume_code", "General"))
    routes = documents.get("routes", [])
    combined = list(routes) if isinstance(routes, list) else []
    combined.extend(
        dict(route) for route in profile.get("resume_routing", []) if isinstance(route, dict)
    )
    documents["routes"] = combined
    return {key: deepcopy(value) for key, value in documents.items() if key in DOCUMENT_KEYS}


def _normalize_documents(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ProfileValidationError("documents must be an object")
    unknown = sorted(set(value) - DOCUMENT_KEYS)
    if unknown:
        raise ProfileValidationError("documents has unsupported keys: {}".format(", ".join(unknown)))
    default = _clean_string(value.get("default", "General"), "Default document", 120)
    raw_routes = value.get("routes", [])
    if not isinstance(raw_routes, list) or len(raw_routes) > MAX_DOCUMENT_ROUTES:
        raise ProfileValidationError("documents.routes must be a bounded list")
    routes: List[Dict[str, Any]] = []
    labels = set()
    for index, route in enumerate(raw_routes):
        if not isinstance(route, dict):
            raise ProfileValidationError("Document route {} must be an object".format(index + 1))
        unknown_route = sorted(set(route) - {"id", "label", "code", "terms", "fields"})
        if unknown_route:
            raise ProfileValidationError("Document route {} has unsupported keys".format(index + 1))
        label = _clean_string(
            route.get("label", route.get("code", "")),
            "Document route {} label".format(index + 1),
            120,
        )
        label_key = label.casefold()
        if label_key in labels:
            raise ProfileValidationError("Document route labels must be unique")
        labels.add(label_key)
        terms = _string_list(
            route.get("terms", []),
            "Document route {} terms".format(index + 1),
            MAX_RULE_TERMS,
        )
        if not terms:
            raise ProfileValidationError("Document route {} needs at least one term".format(index + 1))
        fields = _string_list(
            route.get("fields", ["title", "description", "category"]),
            "Document route {} fields".format(index + 1),
            len(MATCH_FIELDS),
            40,
        )
        if not fields or any(field not in MATCH_FIELDS for field in fields):
            raise ProfileValidationError("Document route {} has invalid fields".format(index + 1))
        normalized_route: Dict[str, Any] = {
            "label": label,
            "terms": terms,
            "fields": fields,
        }
        if route.get("id"):
            normalized_route["id"] = _clean_string(
                route["id"], "Document route {} id".format(index + 1), 64
            )
        routes.append(normalized_route)
    return {"default": default, "routes": routes}


def _normalize_candidate(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ProfileValidationError("candidate must be an object")
    unknown = sorted(set(value) - CANDIDATE_KEYS)
    if unknown:
        raise ProfileValidationError("candidate has unsupported keys: {}".format(", ".join(unknown)))
    output: Dict[str, Any] = {}
    for key in ("current_stage", "expected_graduation"):
        if key in value and str(value[key]).strip():
            output[key] = _clean_string(value[key], "candidate.{}".format(key), 80)
    for key in ("completed_degrees", "skills"):
        output[key] = _string_list(value.get(key, []), "candidate.{}".format(key))
    if "max_required_experience_years" in value and value[
        "max_required_experience_years"
    ] not in (None, ""):
        output["max_required_experience_years"] = _bounded_integer(
            value["max_required_experience_years"],
            "candidate.max_required_experience_years",
            0,
            50,
        )
    return output


def _cycle_label(cycle: Mapping[str, Any]) -> str:
    label = str(cycle.get("label", "")).strip()
    if label:
        return " ".join(label.split())
    season = str(cycle.get("season", "")).strip().title()
    year = cycle.get("year")
    return "{} {}".format(season, year).strip()


def _cycle_from_label(label: str) -> Dict[str, Any]:
    cycle: Dict[str, Any] = {"label": label}
    match = re.fullmatch(
        r"(spring|summer|fall|autumn|winter)\s+(20\d{2})",
        label.strip(),
        flags=re.IGNORECASE,
    )
    if match:
        season = match.group(1).lower()
        cycle["season"] = "fall" if season == "autumn" else season
        cycle["year"] = int(match.group(2))
    return cycle


def _normalize_cycles(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list) or len(value) > MAX_TIMEFRAMES:
        raise ProfileValidationError("targets.cycles must be a bounded list")
    cycles: List[Dict[str, Any]] = []
    labels = set()
    for index, raw_cycle in enumerate(value):
        if not isinstance(raw_cycle, dict):
            raise ProfileValidationError("Target cycle {} must be an object".format(index + 1))
        unknown = sorted(set(raw_cycle) - {"label", "season", "year"})
        if unknown:
            raise ProfileValidationError("Target cycle {} has unsupported keys".format(index + 1))
        label = _clean_string(
            _cycle_label(raw_cycle), "Target cycle {} label".format(index + 1), 120
        )
        key = label.casefold()
        if key in labels:
            continue
        labels.add(key)
        cycle: Dict[str, Any] = {"label": label}
        if "season" in raw_cycle and str(raw_cycle["season"]).strip():
            season = str(raw_cycle["season"]).strip().lower()
            if season not in {"spring", "summer", "fall", "winter", "anytime", "custom"}:
                raise ProfileValidationError("Target cycle {} has an invalid season".format(index + 1))
            cycle["season"] = season
        if "year" in raw_cycle and raw_cycle["year"] not in (None, ""):
            cycle["year"] = _bounded_integer(
                raw_cycle["year"], "Target cycle {} year".format(index + 1), 2000, 2200
            )
        cycles.append(cycle)
    return cycles


def _normalize_targets(value: Any, timeframes: Sequence[str]) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ProfileValidationError("targets must be an object")
    allowed = TARGET_LIST_KEYS | TARGET_SCALAR_KEYS | TARGET_BOOLEAN_KEYS | {"cycles"}
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ProfileValidationError("targets has unsupported keys: {}".format(", ".join(unknown)))
    output: Dict[str, Any] = {}
    for key in TARGET_LIST_KEYS:
        output[key] = _string_list(value.get(key, []), "targets.{}".format(key))
    cycles = _normalize_cycles(value.get("cycles", []))
    cycle_by_label = {cycle["label"].casefold(): cycle for cycle in cycles}
    output["cycles"] = [
        deepcopy(cycle_by_label.get(label.casefold(), _cycle_from_label(label)))
        for label in timeframes
    ]
    for key in TARGET_BOOLEAN_KEYS:
        if key in value:
            if not isinstance(value[key], bool):
                raise ProfileValidationError("targets.{} must be true or false".format(key))
            output[key] = value[key]
    if "remote_preference" in value and str(value["remote_preference"]).strip():
        preference = re.sub(
            r"[\s-]+",
            "_",
            _clean_string(
                value["remote_preference"], "targets.remote_preference", 40
            ).casefold(),
        )
        aliases = {
            "any": "no_preference",
            "flexible": "no_preference",
            "none": "no_preference",
            "no_preference": "no_preference",
            "remote": "remote_preferred",
            "remote_preferred": "remote_preferred",
            "remote_only": "remote_required",
            "remote_required": "remote_required",
            "hybrid": "hybrid_preferred",
            "hybrid_preferred": "hybrid_preferred",
            "onsite": "onsite_preferred",
            "on_site": "onsite_preferred",
            "onsite_preferred": "onsite_preferred",
        }
        if preference not in aliases:
            raise ProfileValidationError("targets.remote_preference is unsupported")
        output["remote_preference"] = aliases[preference]
    return output


def _profile_timeframes(profile: Mapping[str, Any]) -> List[str]:
    configured = profile.get("timeframes")
    if isinstance(configured, list):
        values = [str(value) for value in configured if isinstance(value, str)]
        # An explicit empty list means "any timeframe" and must not fall back
        # to a stale legacy season.
        return _string_list(values, "timeframes", MAX_TIMEFRAMES)
    targets = profile.get("targets", {})
    if isinstance(targets, dict):
        cycles = targets.get("cycles", targets.get("target_cycles", []))
        if isinstance(cycles, list):
            labels = [
                _cycle_label(cycle) if isinstance(cycle, dict) else str(cycle)
                for cycle in cycles
                if (isinstance(cycle, dict) and _cycle_label(cycle))
                or (isinstance(cycle, str) and cycle.strip())
            ]
            if labels:
                return _string_list(labels, "timeframes", MAX_TIMEFRAMES)
        legacy_timeframes = targets.get("timeframes")
        if isinstance(legacy_timeframes, list) and legacy_timeframes:
            return _string_list(legacy_timeframes, "timeframes", MAX_TIMEFRAMES)
    dashboard = profile.get("dashboard", {})
    legacy = dashboard.get("target_season", "") if isinstance(dashboard, dict) else ""
    if not str(legacy).strip():
        candidate = profile.get("candidate", {})
        legacy = candidate.get("target_season", "") if isinstance(candidate, dict) else ""
    return [_clean_string(legacy, "Target timeframe")] if str(legacy).strip() else []


def _candidate_projection(profile: Mapping[str, Any]) -> Dict[str, Any]:
    candidate = profile.get("candidate", {})
    if not isinstance(candidate, dict):
        return {}
    projected = {key: deepcopy(candidate[key]) for key in CANDIDATE_KEYS if key in candidate}
    if not str(projected.get("current_stage", "")).strip() and str(
        candidate.get("career_stage", "")
    ).strip():
        projected["current_stage"] = deepcopy(candidate["career_stage"])
    return projected


def _merged_profile_lists(*values: Any) -> List[str]:
    combined: List[str] = []
    for value in values:
        if isinstance(value, list):
            combined.extend(entry for entry in value if isinstance(entry, str))
        elif isinstance(value, str) and value.strip():
            combined.append(value)
    return _string_list(combined, "legacy profile values")


def _targets_projection(profile: Mapping[str, Any], timeframes: Sequence[str]) -> Dict[str, Any]:
    targets = profile.get("targets", {})
    projected = {
        key: deepcopy(value)
        for key, value in targets.items()
        if key in TARGET_LIST_KEYS | TARGET_SCALAR_KEYS | TARGET_BOOLEAN_KEYS | {"cycles"}
    } if isinstance(targets, dict) else {}
    projected.setdefault("cycles", [_cycle_from_label(label) for label in timeframes])
    if isinstance(targets, dict):
        projected["role_families"] = _merged_profile_lists(
            targets.get("role_families"), targets.get("roles")
        )
        projected["supporting_skills"] = _merged_profile_lists(
            targets.get("supporting_skills"), targets.get("skills")
        )
        projected["work_arrangements"] = _merged_profile_lists(
            targets.get("work_arrangements"), targets.get("workplace_types")
        )
        projected["exclusions"] = _merged_profile_lists(
            targets.get("exclusions"), profile.get("exclusions")
        )
    for key in TARGET_LIST_KEYS:
        projected.setdefault(key, [])
    return projected


def _source_layers() -> List[Dict[str, Any]]:
    return [_read_json(path) for path in config.source_files()]


def _selected_packs() -> List[str]:
    selected: Optional[List[str]] = None
    for payload in _source_layers():
        if "selected_packs" in payload:
            raw = payload["selected_packs"]
            if not isinstance(raw, list):
                raise ProfileValidationError("selected_packs must be a list")
            selected = [str(value) for value in raw if str(value).strip()]
    if selected is None:
        selected = [
            str(pack["id"])
            for pack in config.load_source_packs()
            if pack.get("default")
        ]
    return _string_list(selected, "selected_packs", 64, 80)


def _revision(profile: Mapping[str, Any]) -> str:
    source_layers = _source_layers()
    payload = {
        "profile": profile,
        "source_preferences": [
            {
                "selected_packs": layer.get("selected_packs"),
                "sources": layer.get("sources", []),
            }
            for layer in source_layers[1:]
        ],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def profile_editor_payload(profile: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return only fields the dashboard editor is allowed to read and change."""
    effective = config.load_profile() if profile is None else deepcopy(profile)
    timeframes = _profile_timeframes(effective)
    payload = {
        "version": EDITOR_VERSION,
        "expected_revision": _revision(effective),
        "timeframes": timeframes,
        "selected_packs": _selected_packs(),
        "candidate": _candidate_projection(effective),
        "targets": _targets_projection(effective, timeframes),
        "priority_organizations": deepcopy(effective.get("priority_organizations", [])),
        "matching": _matching_projection(effective),
        "documents": _document_projection(effective),
    }
    return validate_editor_payload(payload)


def validate_editor_payload(
    payload: Any,
    known_pack_ids: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ProfileValidationError("Profile editor payload must be an object")
    missing = sorted(EXPECTED_EDITOR_KEYS - set(payload))
    extra = sorted(set(payload) - EXPECTED_EDITOR_KEYS)
    if missing or extra:
        details = []
        if missing:
            details.append("missing {}".format(", ".join(missing)))
        if extra:
            details.append("unsupported {}".format(", ".join(extra)))
        raise ProfileValidationError("Profile editor payload has {}".format("; ".join(details)))
    version = payload["version"]
    if isinstance(version, bool) or not isinstance(version, int) or version != EDITOR_VERSION:
        raise ProfileValidationError("Unsupported profile editor version")
    expected_revision = payload["expected_revision"]
    if not isinstance(expected_revision, str) or (
        expected_revision and not REVISION_RE.fullmatch(expected_revision)
    ):
        raise ProfileValidationError("expected_revision must be empty or a revision hash")
    timeframes = _string_list(
        payload["timeframes"], "timeframes", MAX_TIMEFRAMES, 120
    )
    selected_packs = _string_list(payload["selected_packs"], "selected_packs", 64, 80)
    if not selected_packs:
        raise ProfileValidationError("Select at least one source pack")
    known_packs = (
        {str(pack_id) for pack_id in known_pack_ids}
        if known_pack_ids is not None
        else {str(pack["id"]) for pack in config.load_source_packs()}
    )
    unknown_packs = sorted(set(selected_packs) - known_packs)
    if unknown_packs:
        raise ProfileValidationError("Unknown source pack: {}".format(", ".join(unknown_packs)))
    candidate = _normalize_candidate(payload["candidate"])
    targets = _normalize_targets(payload["targets"], timeframes)
    organizations = _string_list(
        payload["priority_organizations"], "priority_organizations", 100, 120
    )
    matching = _normalize_matching(payload["matching"])
    documents = _normalize_documents(payload["documents"])
    return {
        "version": EDITOR_VERSION,
        "expected_revision": expected_revision,
        "timeframes": timeframes,
        "selected_packs": selected_packs,
        "candidate": candidate,
        "targets": targets,
        "priority_organizations": organizations,
        "matching": matching,
        "documents": documents,
    }


def _existing_local_payload(destination: Path, name: str) -> Dict[str, Any]:
    if destination.is_symlink():
        raise ProfileValidationError("Configuration file must not be a symbolic link")
    if destination.exists():
        return _read_json(destination)
    repository = config.PROJECT_ROOT / "config" / name
    if destination != repository and repository.is_symlink():
        raise ProfileValidationError("Configuration file must not be a symbolic link")
    if destination != repository and repository.exists():
        return _read_json(repository)
    return {}


def _replace_known(mapping: Dict[str, Any], known: Iterable[str], values: Mapping[str, Any]) -> None:
    for key in known:
        mapping.pop(key, None)
    mapping.update(deepcopy(dict(values)))


def _updated_local_profile(current: Dict[str, Any], editor: Mapping[str, Any]) -> Dict[str, Any]:
    updated = deepcopy(current)
    schema_version = updated.get("schema_version", 2)
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        schema_version = 2
    updated["schema_version"] = max(2, schema_version)
    updated["timeframes"] = deepcopy(editor["timeframes"])

    candidate = updated.get("candidate", {})
    candidate = dict(candidate) if isinstance(candidate, dict) else {}
    _replace_known(
        candidate,
        CANDIDATE_KEYS | LEGACY_CANDIDATE_KEYS,
        editor["candidate"],
    )
    updated["candidate"] = candidate

    targets = updated.get("targets", {})
    targets = dict(targets) if isinstance(targets, dict) else {}
    _replace_known(
        targets,
        TARGET_LIST_KEYS
        | TARGET_SCALAR_KEYS
        | TARGET_BOOLEAN_KEYS
        | LEGACY_TARGET_KEYS
        | {"cycles"},
        editor["targets"],
    )
    updated["targets"] = targets
    updated["priority_organizations"] = deepcopy(editor["priority_organizations"])

    matching = updated.get("matching", {})
    matching = dict(matching) if isinstance(matching, dict) else {}
    _replace_known(
        matching,
        MATCHING_KEYS | LEGACY_INERT_MATCHING_KEYS,
        editor["matching"],
    )
    matching.setdefault("engine", "structured_v2")
    updated["matching"] = matching

    documents = updated.get("documents", {})
    documents = dict(documents) if isinstance(documents, dict) else {}
    _replace_known(documents, DOCUMENT_KEYS, editor["documents"])
    updated["documents"] = documents

    dashboard = updated.get("dashboard", {})
    dashboard = dict(dashboard) if isinstance(dashboard, dict) else {}
    dashboard["timeframes"] = deepcopy(editor["timeframes"])
    dashboard["target_season"] = (
        editor["timeframes"][0] if len(editor["timeframes"]) == 1 else ""
    )
    updated["dashboard"] = dashboard
    for legacy in LEGACY_PROFILE_KEYS:
        updated.pop(legacy, None)
    return updated


def _updated_local_sources(current: Dict[str, Any], selected_packs: Sequence[str]) -> Dict[str, Any]:
    updated = deepcopy(current)
    schema_version = updated.get("schema_version", 2)
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        schema_version = 2
    updated["schema_version"] = max(2, schema_version)
    updated["selected_packs"] = list(selected_packs)
    sources = updated.get("sources", [])
    if not isinstance(sources, list):
        raise ProfileValidationError("Local source overrides must be a list")
    updated["sources"] = deepcopy(sources)
    return updated


def _normalize_initial_configuration(
    profile: Dict[str, Any],
    source_registry: Dict[str, Any],
    known_pack_ids: Optional[Iterable[str]],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Apply the editor's bounds and canonical schema to first-run onboarding."""
    if not isinstance(profile, dict) or not isinstance(source_registry, dict):
        raise ProfileValidationError("Initial configuration must use JSON objects")
    timeframes = _profile_timeframes(profile)
    editor = validate_editor_payload(
        {
            "version": EDITOR_VERSION,
            "expected_revision": "",
            "timeframes": timeframes,
            "selected_packs": source_registry.get("selected_packs", []),
            "candidate": _candidate_projection(profile),
            "targets": _targets_projection(profile, timeframes),
            "priority_organizations": deepcopy(
                profile.get("priority_organizations", [])
            ),
            "matching": _matching_projection(profile),
            "documents": _document_projection(profile),
        },
        known_pack_ids=known_pack_ids,
    )
    return (
        _updated_local_profile(profile, editor),
        _updated_local_sources(source_registry, editor["selected_packs"]),
    )


def _safe_destination(path: Path) -> None:
    parent = path.parent
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if parent.is_symlink():
        raise ProfileValidationError("Configuration directory must not be a symbolic link")
    details = parent.stat()
    if not stat.S_ISDIR(details.st_mode) or details.st_uid != os.getuid():
        raise ProfileValidationError("Configuration directory is not owned by the current user")
    if details.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise ProfileValidationError("Configuration directory is writable by another user")
    if path.exists() or path.is_symlink():
        entry = path.lstat()
        if stat.S_ISLNK(entry.st_mode):
            raise ProfileValidationError("Configuration file must not be a symbolic link")
        if not stat.S_ISREG(entry.st_mode) or entry.st_uid != os.getuid():
            raise ProfileValidationError("Configuration file is not owned by the current user")


def _stage_json(path: Path, payload: Mapping[str, Any]) -> Path:
    _safe_destination(path)
    descriptor, name = tempfile.mkstemp(
        dir=str(path.parent), prefix=".{}-".format(path.name), suffix=".tmp"
    )
    temporary = Path(name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(
                payload,
                handle,
                indent=2,
                ensure_ascii=False,
                sort_keys=True,
                allow_nan=False,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        if temporary.exists():
            temporary.unlink()
        raise
    return temporary


def write_local_configuration(
    profile: Dict[str, Any],
    source_registry: Dict[str, Any],
    force: bool = False,
) -> Tuple[Path, Path]:
    """Atomically replace the two canonical private local configuration files."""
    destinations = (config.local_profile_path(), config.local_sources_path())
    existing = [path.name for path in destinations if path.exists() or path.is_symlink()]
    if existing and not force:
        raise FileExistsError(
            "Local configuration already exists ({}); use profile edit to change it".format(
                ", ".join(existing)
            )
        )
    staged: List[Tuple[Path, Path]] = []
    previous: Dict[Path, Optional[bytes]] = {}
    replaced: List[Path] = []
    try:
        for destination, payload in zip(destinations, (profile, source_registry)):
            staged.append((_stage_json(destination, payload), destination))
            previous[destination] = destination.read_bytes() if destination.exists() else None
        for temporary, destination in staged:
            os.replace(temporary, destination)
            replaced.append(destination)
            os.chmod(destination, 0o600)
        for directory in {path.parent for path in destinations}:
            descriptor = os.open(str(directory), os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    except Exception:
        for destination in reversed(replaced):
            content = previous.get(destination)
            if content is None:
                try:
                    destination.unlink()
                except FileNotFoundError:
                    pass
            else:
                descriptor, rollback_name = tempfile.mkstemp(
                    dir=str(destination.parent),
                    prefix=".{}-rollback-".format(destination.name),
                    suffix=".tmp",
                )
                rollback = Path(rollback_name)
                try:
                    os.fchmod(descriptor, 0o600)
                    with os.fdopen(descriptor, "wb") as handle:
                        handle.write(content)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(rollback, destination)
                finally:
                    if rollback.exists():
                        rollback.unlink()
        raise
    finally:
        for temporary, _destination in staged:
            if temporary.exists():
                temporary.unlink()
    return destinations


def _restore_local_configuration(
    previous: Mapping[Path, Optional[bytes]],
) -> None:
    """Restore a consistent pair of private files after a post-save failure."""
    staged: List[Tuple[Path, Path]] = []
    try:
        for destination, content in previous.items():
            if content is None:
                continue
            _safe_destination(destination)
            descriptor, name = tempfile.mkstemp(
                dir=str(destination.parent),
                prefix=".{}-restore-".format(destination.name),
                suffix=".tmp",
            )
            temporary = Path(name)
            try:
                os.fchmod(descriptor, 0o600)
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
            except Exception:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
                if temporary.exists():
                    temporary.unlink()
                raise
            staged.append((temporary, destination))
        for temporary, destination in staged:
            os.replace(temporary, destination)
            os.chmod(destination, 0o600)
        for destination, content in previous.items():
            if content is None:
                try:
                    destination.unlink()
                except FileNotFoundError:
                    pass
        for directory in {path.parent for path in previous}:
            descriptor = os.open(str(directory), os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        for temporary, _destination in staged:
            if temporary.exists():
                temporary.unlink()


def _ensure_local_writes_are_effective() -> None:
    if any(
        os.environ.get(name, "").strip()
        for name in (
            "OPPORTUNITY_RADAR_PROFILE",
            "OPPORTUNITY_MONITOR_PROFILE",
            "OPPORTUNITY_RADAR_SOURCES",
            "OPPORTUNITY_MONITOR_SOURCES",
        )
    ):
        raise ProfileValidationError(
            "Local profile editing is unavailable while an environment configuration override is active"
        )


def _lifecycle_lock_path() -> Path:
    return (
        Path.home()
        / "Library"
        / "Application Support"
        / ".OpportunityRadar.lifecycle-lock"
    )


def _safe_lifecycle_directory(lock: Path) -> os.stat_result:
    entry = lock.lstat()
    if (
        stat.S_ISLNK(entry.st_mode)
        or not stat.S_ISDIR(entry.st_mode)
        or entry.st_uid != os.getuid()
        or entry.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
    ):
        raise ProfileValidationError("The profile lifecycle lock is unsafe")
    return entry


def _lifecycle_owner_is_running(owner: Path, now: float) -> bool:
    entry = owner.lstat()
    if (
        stat.S_ISLNK(entry.st_mode)
        or not stat.S_ISREG(entry.st_mode)
        or entry.st_uid != os.getuid()
        or entry.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
        or entry.st_size > 128
    ):
        raise ProfileValidationError("The profile lifecycle owner is unsafe")
    try:
        lines = owner.read_text(encoding="ascii").splitlines()
        pid = int(lines[0])
        started_at = float(lines[1])
    except (OSError, UnicodeError, ValueError, IndexError) as error:
        raise ProfileValidationError("The profile lifecycle owner is invalid") from error
    if pid <= 0 or not math.isfinite(started_at) or started_at <= 0:
        raise ProfileValidationError("The profile lifecycle owner is invalid")
    if now - started_at > MAX_LIFECYCLE_OWNER_AGE_SECONDS:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def recover_stale_lifecycle_lock(lock: Optional[Path] = None) -> bool:
    """Remove only a validated lifecycle lock whose recorded owner is gone."""
    candidate = lock or _lifecycle_lock_path()
    if not candidate.exists() and not candidate.is_symlink():
        return False
    directory = _safe_lifecycle_directory(candidate)
    owner = candidate / LIFECYCLE_OWNER_FILE
    children = list(candidate.iterdir())
    if owner.exists() or owner.is_symlink():
        if any(child.name != LIFECYCLE_OWNER_FILE for child in children):
            raise ProfileValidationError("The profile lifecycle lock is unsafe")
        if _lifecycle_owner_is_running(owner, time.time()):
            return False
        owner.unlink()
        candidate.rmdir()
        return True
    if children:
        raise ProfileValidationError("The profile lifecycle lock is unsafe")
    if time.time() - directory.st_mtime <= STALE_OWNERLESS_LOCK_AGE_SECONDS:
        return False
    candidate.rmdir()
    return True


def _write_lifecycle_owner(lock: Path) -> Path:
    owner = lock / LIFECYCLE_OWNER_FILE
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(str(owner), flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        payload = "{}\n{:.6f}\n".format(os.getpid(), time.time()).encode("ascii")
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    directory = os.open(str(lock), os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return owner


@contextmanager
def profile_lifecycle_lock() -> Iterator[None]:
    """Serialize profile writes with scheduler runtime install and uninstall."""
    if sys.platform != "darwin":
        yield
        return
    lock = _lifecycle_lock_path()
    parent = lock.parent
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if parent.is_symlink():
        raise ProfileValidationError("The profile lifecycle-lock parent is unsafe")
    details = parent.stat()
    if (
        not stat.S_ISDIR(details.st_mode)
        or details.st_uid != os.getuid()
        or details.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
    ):
        raise ProfileValidationError("The profile lifecycle-lock parent is unsafe")
    created = False
    owner: Optional[Path] = None
    try:
        try:
            lock.mkdir(mode=0o700)
        except FileExistsError:
            if recover_stale_lifecycle_lock(lock):
                lock.mkdir(mode=0o700)
            else:
                raise ProfileValidationError(
                    "An Opportunity Radar install, uninstall, or profile update is already running"
                )
        created = True
        _safe_lifecycle_directory(lock)
        owner = _write_lifecycle_owner(lock)
        yield
    finally:
        if created:
            try:
                if owner is not None and owner.exists() and not owner.is_symlink():
                    owner.unlink()
                lock.rmdir()
            except OSError:
                pass


def refresh_profile_state() -> Dict[str, Any]:
    """Rescore and rebuild existing private state without fetching the network."""
    from .dashboard import render_dashboard
    from .database import Database
    from .pipeline import _register_sources

    database_path = config.resolve_private_state_path(
        config.project_path("data", "opportunities.sqlite3"),
        "data",
        "opportunities.sqlite3",
    )
    if not database_path.exists():
        return {"rescored": 0, "dashboard_rebuilt": False}
    profile = config.load_profile()
    all_sources = config.load_sources(include_disabled=True)
    database = Database(database_path)
    try:
        database.initialize()
        _register_sources(database, all_sources, profile)
        rescore = database.rescore_for_profile(profile)
        render_dashboard(database.dashboard_payload(), profile=profile)
    finally:
        database.close()
    return {
        "rescored": int(rescore.get("rescored", 0)),
        "profile_changed": bool(rescore.get("changed", False)),
        "dashboard_rebuilt": True,
    }


def _persist_and_refresh(
    profile: Dict[str, Any],
    source_registry: Dict[str, Any],
    force: bool,
    rebuild: bool,
) -> Tuple[Tuple[Path, Path], Dict[str, Any]]:
    """Persist a profile pair under caller-held locks and roll back on refresh failure."""
    profile_path = config.local_profile_path()
    sources_path = config.local_sources_path()
    previous = {
        profile_path: profile_path.read_bytes() if profile_path.exists() else None,
        sources_path: sources_path.read_bytes() if sources_path.exists() else None,
    }
    destinations = write_local_configuration(profile, source_registry, force=force)
    try:
        refresh = refresh_profile_state() if rebuild else {
            "rescored": 0,
            "dashboard_rebuilt": False,
        }
    except Exception:
        try:
            _restore_local_configuration(previous)
        except Exception as rollback_error:
            raise RuntimeError(
                "Profile refresh failed and the prior settings could not be restored"
            ) from rollback_error
        # A render can fail after rescoring. Best-effort refresh with the
        # restored profile returns the database to its previous score revision.
        try:
            refresh_profile_state()
        except Exception:
            pass
        raise
    return destinations, refresh


def initialize_local_configuration(
    profile: Dict[str, Any],
    source_registry: Dict[str, Any],
    force: bool = False,
    rebuild: bool = True,
    known_pack_ids: Optional[Iterable[str]] = None,
) -> Tuple[Tuple[Path, Path], Dict[str, Any]]:
    """Create or replace onboarding settings through the shared profile lifecycle."""
    _ensure_local_writes_are_effective()
    profile, source_registry = _normalize_initial_configuration(
        profile,
        source_registry,
        known_pack_ids,
    )
    from .pipeline import exclusive_lock

    database_path = config.resolve_private_state_path(
        config.project_path("data", "opportunities.sqlite3"),
        "data",
        "opportunities.sqlite3",
    )
    with profile_lifecycle_lock():
        with exclusive_lock(database_path.with_name("scan.lock")):
            return _persist_and_refresh(
                profile,
                source_registry,
                force=force,
                rebuild=rebuild,
            )


def apply_editor_payload(
    payload: Dict[str, Any],
    rebuild: bool = True,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Validate and save one optimistic-concurrency editor request."""
    _ensure_local_writes_are_effective()
    editor = validate_editor_payload(payload)
    from .pipeline import exclusive_lock

    database_path = config.resolve_private_state_path(
        config.project_path("data", "opportunities.sqlite3"),
        "data",
        "opportunities.sqlite3",
    )
    lock_path = database_path.with_name("scan.lock")
    with profile_lifecycle_lock():
        with exclusive_lock(lock_path):
            effective = config.load_profile()
            current_revision = _revision(effective)
            expected = editor["expected_revision"]
            if expected and not hmac.compare_digest(expected, current_revision):
                raise ProfileValidationError(
                    "The profile changed after it was opened; reload it before saving"
                )
            profile_path = config.local_profile_path()
            sources_path = config.local_sources_path()
            local_profile = _existing_local_payload(profile_path, "profile.local.json")
            local_sources = _existing_local_payload(sources_path, "sources.local.json")
            updated_profile = _updated_local_profile(local_profile, editor)
            updated_sources = _updated_local_sources(
                local_sources, editor["selected_packs"]
            )
            if dry_run:
                return {
                    "status": "valid",
                    "saved": False,
                    "revision": current_revision,
                    "profile_path": str(profile_path),
                    "sources_path": str(sources_path),
                }
            _destinations, refresh = _persist_and_refresh(
                updated_profile,
                updated_sources,
                force=True,
                rebuild=rebuild,
            )
            new_revision = _revision(config.load_profile())
    return {
        "status": "saved",
        "saved": True,
        "revision": new_revision,
        "selected_packs": list(editor["selected_packs"]),
        "timeframes": list(editor["timeframes"]),
        **refresh,
    }
