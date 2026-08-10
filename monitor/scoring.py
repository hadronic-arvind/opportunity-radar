"""Modular, deterministic opportunity matching."""

import re
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from .models import Opportunity


MATCH_FIELDS = {
    "title",
    "organization",
    "location",
    "description",
    "eligibility",
    "category",
    "opportunity_type",
}
DEFAULT_FIELDS = (
    "title",
    "organization",
    "location",
    "description",
    "eligibility",
    "category",
    "opportunity_type",
)


def _matches(text: str, terms: Iterable[str]) -> List[str]:
    found = []
    for raw_term in terms:
        term = str(raw_term).strip().lower()
        if term and re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text):
            found.append(str(raw_term))
    return found


def _item_values(item: Opportunity, fields: Sequence[str]) -> List[str]:
    values: List[str] = []
    for field in fields:
        if field not in MATCH_FIELDS:
            continue
        values.append(str(getattr(item, field, "") or "").lower())
    return values


def _item_matches(
    item: Opportunity, fields: Sequence[str], terms: Iterable[str]
) -> List[str]:
    values = _item_values(item, fields)
    found = []
    for term in terms:
        if any(_matches(value, [term]) for value in values):
            found.append(str(term))
    return found


def _matching_config(profile: Dict[str, Any]) -> Dict[str, Any]:
    configured = profile.get("matching", {})
    return configured if isinstance(configured, dict) else {}


def _rules(profile: Dict[str, Any]) -> List[Dict[str, Any]]:
    rules: List[Dict[str, Any]] = []
    configured = _matching_config(profile).get("rules", [])
    if isinstance(configured, list):
        rules.extend(rule for rule in configured if isinstance(rule, dict))
    for legacy in profile.get("positive_rules", []):
        if isinstance(legacy, dict):
            rules.append(dict(legacy, weight=abs(int(legacy.get("weight", 0)))))
    for legacy in profile.get("negative_rules", []):
        if isinstance(legacy, dict):
            rules.append(dict(legacy, weight=-abs(int(legacy.get("weight", 0)))))
    return rules


def _rule_result(item: Opportunity, rule: Dict[str, Any]) -> Tuple[int, List[str]]:
    terms = rule.get("terms", [])
    if not isinstance(terms, list) or not terms:
        return 0, []
    raw_fields = rule.get("fields", DEFAULT_FIELDS)
    fields = raw_fields if isinstance(raw_fields, list) else DEFAULT_FIELDS
    found = _item_matches(item, fields, terms)
    mode = str(rule.get("match", "any")).lower()
    if mode == "all" and len(found) != len(terms):
        return 0, []
    if not found:
        return 0, []
    weight = int(rule.get("weight", 0))
    if rule.get("per_term"):
        maximum = max(1, int(rule.get("max_hits", len(found))))
        weight *= min(len(found), maximum)
    return weight, found


def _tier(score: int, profile: Dict[str, Any]) -> str:
    thresholds = _matching_config(profile).get("tier_thresholds", {})
    priority = int(thresholds.get("priority", 75))
    strong = int(thresholds.get("strong", 55))
    watch = int(thresholds.get("watch", 25))
    if score >= priority:
        return "priority"
    if score >= strong:
        return "strong"
    if score >= watch:
        return "watch"
    return "skip"


def score_opportunity(item: Opportunity, profile: Dict[str, Any]) -> Opportunity:
    """Apply configured matching dimensions and attach an auditable breakdown."""
    matching = _matching_config(profile)
    score = int(matching.get("base_score", 50))
    reasons: List[str] = []
    warnings: List[str] = []
    components: List[Dict[str, Any]] = [
        {"id": "base", "label": "Starting score", "points": score, "evidence": []}
    ]

    for rule in _rules(profile):
        weight, found = _rule_result(item, rule)
        if not found or not weight:
            continue
        label = str(rule.get("label", "Configured match"))
        detail = "{}: {}".format(label, ", ".join(found[:3]))
        score += weight
        components.append(
            {
                "id": str(rule.get("id", label)).strip() or "configured_rule",
                "label": label,
                "points": weight,
                "evidence": found[:5],
            }
        )
        (reasons if weight > 0 else warnings).append(detail)

    priorities = {
        str(name).strip().casefold()
        for name in profile.get("priority_organizations", [])
        if str(name).strip()
    }
    if item.organization.strip().casefold() in priorities:
        bonus = int(matching.get("priority_organization_bonus", 10))
        score += bonus
        components.append(
            {
                "id": "preferred_organization",
                "label": "Preferred organization",
                "points": bonus,
                "evidence": [item.organization],
            }
        )
        reasons.append("Priority organization")

    item.score = max(0, min(100, score))
    item.tier = _tier(item.score, profile)
    item.reasons = reasons[:6]
    item.warnings = warnings[:4]
    item.metadata["match"] = {
        "fit_score": item.score,
        "components": components,
        "eligibility": str(item.metadata.get("eligibility_state", "unknown")),
    }
    if not item.recommended_resume:
        item.recommended_resume = recommend_document(item, profile)
    return item


def _document_routes(profile: Dict[str, Any]) -> Tuple[str, List[Dict[str, Any]]]:
    documents = profile.get("documents", {})
    if not isinstance(documents, dict):
        documents = {}
    default = str(documents.get("default", profile.get("default_resume_code", "General"))).strip()
    routes = documents.get("routes", [])
    combined = list(routes) if isinstance(routes, list) else []
    combined.extend(profile.get("resume_routing", []))
    return default, [route for route in combined if isinstance(route, dict)]


def recommend_document(item: Opportunity, profile: Dict[str, Any]) -> str:
    default, routes = _document_routes(profile)
    choices: List[Tuple[int, int, str]] = []
    for index, route in enumerate(routes):
        fields = route.get("fields", ["title", "description", "category"])
        hits = len(
            _item_matches(
                item,
                fields if isinstance(fields, list) else DEFAULT_FIELDS,
                route.get("terms", []),
            )
        )
        label = str(route.get("label", route.get("code", ""))).strip()
        if label:
            choices.append((hits, -index, label))
    best = max(choices) if choices else (0, 0, default)
    return best[2] if best[0] else default


def recommend_resume(item: Opportunity, profile: Dict[str, Any]) -> str:
    """Backward-compatible alias for integrations using the original name."""
    return recommend_document(item, profile)
