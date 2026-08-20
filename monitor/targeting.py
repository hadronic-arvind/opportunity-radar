"""Generic reconciliation between basic profile targets and advanced rules."""

import re
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple


PROFILE_SEMANTICS_SCHEMA_VERSION = 3

# These words describe broad opportunity shapes rather than a user's chosen
# domain or specialty. Ignoring them prevents a shared word such as "research"
# from making an unrelated advanced rule look aligned with every research role.
GENERIC_TARGET_WORDS = {
    "and",
    "career",
    "developer",
    "engineer",
    "intern",
    "internship",
    "job",
    "opportunity",
    "or",
    "program",
    "research",
    "role",
    "science",
    "scientist",
    "specialist",
    "the",
    "work",
}


def _semantic_words(value: Any) -> List[str]:
    words = re.findall(r"[a-z0-9]+", str(value or "").casefold().replace("&", " and "))
    aliases = {
        "careers": "career",
        "developers": "developer",
        "development": "developer",
        "engineers": "engineer",
        "engineering": "engineer",
        "internships": "internship",
        "jobs": "job",
        "opportunities": "opportunity",
        "programs": "program",
        "researchers": "research",
        "researcher": "research",
        "researching": "research",
        "roles": "role",
        "scientists": "scientist",
        "specialists": "specialist",
    }
    return [aliases.get(word, word) for word in words]


def _semantic_phrase(value: Any) -> str:
    return " ".join(_semantic_words(value))


def _significant_words(value: Any) -> set:
    return {
        word
        for word in _semantic_words(value)
        if word not in GENERIC_TARGET_WORDS and len(word) >= 2
    }


def _semantic_terms(*values: Any) -> List[str]:
    terms: List[str] = []
    seen = set()
    for value in values:
        entries = value if isinstance(value, list) else [value]
        for entry in entries:
            if not isinstance(entry, str):
                continue
            cleaned = " ".join(entry.split())[:120]
            key = _semantic_phrase(cleaned)
            if cleaned and key and key not in seen:
                seen.add(key)
                terms.append(cleaned)
    return terms


def _rule_values(rule: Mapping[str, Any]) -> List[str]:
    values = [str(rule.get("id", "")), str(rule.get("label", ""))]
    terms = rule.get("terms", [])
    if isinstance(terms, list):
        values.extend(str(term) for term in terms)
    return [value for value in values if value.strip()]


def _meaningful_overlap(left: Any, right: Any) -> bool:
    left_phrase = _semantic_phrase(left)
    right_phrase = _semantic_phrase(right)
    if not left_phrase or not right_phrase:
        return False
    if left_phrase == right_phrase:
        return True
    left_words = left_phrase.split()
    right_words = right_phrase.split()
    left_significant = _significant_words(left)
    right_significant = _significant_words(right)
    if left_significant.intersection(right_significant):
        return True
    # A multiword target can still be meaningful when it consists of generic
    # role words, but only as a complete phrase rather than a one-word overlap.
    if len(right_words) >= 2 and " {} ".format(right_phrase) in " {} ".format(
        left_phrase
    ):
        return True
    return len(left_words) >= 2 and " {} ".format(left_phrase) in " {} ".format(
        right_phrase
    )


def _rule_overlaps(rule: Mapping[str, Any], targets: Iterable[str]) -> bool:
    return any(
        _meaningful_overlap(value, target)
        for value in _rule_values(rule)
        for target in targets
    )


def _positive_interest_rule(rule: Mapping[str, Any]) -> bool:
    try:
        weight = int(rule.get("weight", 0))
    except (TypeError, ValueError):
        return False
    return (
        weight > 0
        and str(rule.get("dimension", "interest")).casefold() == "interest"
        and not bool(rule.get("hard_gate", False))
    )


def _target_scope(targets: Mapping[str, Any]) -> List[str]:
    return _semantic_terms(
        targets.get("domains"),
        targets.get("role_families"),
        targets.get("roles"),
    )


def reconcile_matching_rules(
    previous_targets: Mapping[str, Any],
    next_targets: Mapping[str, Any],
    rules: Sequence[Dict[str, Any]],
    previous_schema_version: int,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Retire stale positive rules while preserving retained target overlap."""
    previous_scope = _target_scope(previous_targets)
    selected_scope = _target_scope(next_targets)
    selected_keys = {_semantic_phrase(value) for value in selected_scope}
    removed_scope = [
        value
        for value in previous_scope
        if _semantic_phrase(value) not in selected_keys
    ]
    migration = previous_schema_version < PROFILE_SEMANTICS_SCHEMA_VERSION
    # Adding a basic target broadens the profile and must not silently delete
    # an independent advanced interest rule. Only legacy migration or an
    # actual removal makes the retained basic scope authoritative.
    enforce_selected_scope = bool(selected_scope) and (
        migration or bool(removed_scope)
    )
    removed_all_scope = bool(removed_scope) and not selected_scope

    retained: List[Dict[str, Any]] = []
    retired: List[Dict[str, str]] = []
    for rule in rules:
        if not isinstance(rule, dict) or not _positive_interest_rule(rule):
            retained.append(rule)
            continue
        if enforce_selected_scope and _rule_overlaps(rule, selected_scope):
            retained.append(rule)
            continue
        if removed_all_scope and not _rule_overlaps(rule, removed_scope):
            retained.append(rule)
            continue
        if not enforce_selected_scope and not removed_all_scope:
            retained.append(rule)
            continue
        reason = (
            "removed_target"
            if removed_scope and _rule_overlaps(rule, removed_scope)
            else "outside_selected_targets"
        )
        retired.append(
            {
                "id": str(rule.get("id", "configured_rule")),
                "label": str(rule.get("label", "Configured match")),
                "reason": reason,
            }
        )
    return retained, {
        "semantic_schema_upgraded": migration,
        "retired_matching_rules": retired,
    }


def effective_matching_rules(
    profile: Mapping[str, Any],
    rules: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Apply a pending v2 reconciliation in memory without rewriting files."""
    raw_schema = profile.get("schema_version", 2)
    schema_version = (
        raw_schema
        if isinstance(raw_schema, int) and not isinstance(raw_schema, bool)
        else 2
    )
    targets = profile.get("targets", {})
    targets = targets if isinstance(targets, dict) else {}
    effective, _adjustments = reconcile_matching_rules(
        targets,
        targets,
        rules,
        schema_version,
    )
    return effective
