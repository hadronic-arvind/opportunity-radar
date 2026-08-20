"""Modular, deterministic opportunity matching."""

import hashlib
import json
import re
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from .models import Opportunity
from .targeting import effective_matching_rules


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
CURATED_DOCUMENT_PROVENANCE = "curated_explicit"
LEGACY_CURATED_DOCUMENT_PROVENANCE = "curated_legacy"
PROFILE_DOCUMENT_PROVENANCE = "profile"
SCORING_SCHEMA_VERSION = 5
STRUCTURED_ENGINE = "structured_v2"
STRUCTURED_DIMENSIONS = ("interest", "target", "qualification", "preference")
DEFAULT_FIELD_WEIGHTS = {
    "title": 1.0,
    "organization": 0.9,
    "opportunity_type": 0.9,
    "category": 0.75,
    "location": 0.7,
    "eligibility": 0.6,
    "description": 0.25,
}
EARLY_CAREER_STAGES = {
    "student",
    "undergraduate",
    "undergraduate_student",
    "graduate",
    "graduate_student",
    "masters_student",
    "phd",
    "phd_student",
    "doctoral_student",
    "new_grad",
    "early_career",
}
STRONG_SENIOR_TITLE_RE = re.compile(
    r"\b(?:senior|staff|principal|director|head|vice president|vp)\b",
    re.IGNORECASE,
)
MANAGER_TITLE_RE = re.compile(r"\b(?:manager|team leader)\b", re.IGNORECASE)
EARLY_TITLE_RE = re.compile(
    r"\b(?:intern(?:ship)?|co[ -]?op|new grad(?:uate)?|associate|early[ -]?career|"
    r"graduate program|student)\b",
    re.IGNORECASE,
)
EXPERIENCE_RE = re.compile(
    r"\b(?:(?:minimum(?:\s+of)?|at\s+least|requires?|must\s+have)\s+)?"
    r"(?P<years>\d{1,2})\+?\s+years?\s+(?:of\s+)?"
    r"(?:[a-z][a-z0-9+#./-]*\s+){0,4}experience\b",
    re.IGNORECASE,
)
UNDERGRADUATE_ONLY_RE = re.compile(
    r"\b(?:undergraduate students? only|pursuing (?:a |an )?bachelor(?:'s)?(?: degree)?|"
    r"currently enrolled in (?:a |an )?bachelor(?:'s)?(?: degree)?)\b",
    re.IGNORECASE,
)
ADVANCED_DEGREE_ALTERNATIVE_RE = re.compile(
    r"\b(?:master(?:'s)?|doctoral|doctorate|ph\.?d\.?)\b",
    re.IGNORECASE,
)
COMPLETED_PHD_RE = re.compile(
    r"\b(?:completed|earned|hold(?:s|ing)?|have)\s+(?:a\s+)?"
    r"(?:ph\.?d\.?|doctoral degree)\b|"
    r"\b(?:ph\.?d\.?|doctoral degree)\s+(?:must\s+be\s+)?"
    r"(?:completed|earned|awarded|conferred)\b",
    re.IGNORECASE,
)
PURSUING_PHD_RE = re.compile(
    r"\b(?:currently\s+)?(?:pursuing|enrolled\s+in|working\s+toward)\s+"
    r"(?:a\s+)?(?:ph\.?d\.?|doctoral degree|doctoral program)\b",
    re.IGNORECASE,
)
GRADUATION_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
GRADUATION_MONTH_PATTERN = (
    r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|"
    r"Nov(?:ember)?|Dec(?:ember)?"
)
GRADUATION_POINT_PATTERN = (
    r"(?:" + GRADUATION_MONTH_PATTERN + r")\.?\s+(?:\d{1,2}(?:st|nd|rd|th)?,?\s+)?20\d{2}"
    r"|20\d{2}-(?:0?[1-9]|1[0-2])"
    r"|20\d{2}"
)
GRADUATION_RANGE_RE = re.compile(
    r"\b(?:(?:(?:expected\s+)?graduation\s+date|expected\s+graduation)"
    r"(?:\s+(?:must|should)\s+be|\s+is)?|"
    r"(?:applicants?|candidates?|students?|you)\s+"
    r"(?:must\s+|should\s+|are\s+expected\s+to\s+)?"
    r"(?:graduat(?:e|ing)|be\s+graduating)|"
    r"(?:must|expected\s+to)\s+graduate)\s*[:\-]?\s*"
    r"(?:between|from)\s+(?P<start>" + GRADUATION_POINT_PATTERN + r")\s+"
    r"(?:and|through|to|[-–])\s+(?P<end>" + GRADUATION_POINT_PATTERN + r")",
    re.IGNORECASE,
)
GRADUATION_EXACT_RE = re.compile(
    r"\b(?:(?:(?:expected\s+)?graduation\s+date|expected\s+graduation)"
    r"\s*(?:must\s+be|is|of|:)?|"
    r"(?:applicants?|candidates?|students?|you)\s+"
    r"(?:must\s+|should\s+|are\s+expected\s+to\s+)?graduat(?:e|ing)\s+"
    r"(?:in|during)|expected\s+to\s+graduate\s+(?:in|during))\s*"
    r"(?P<point>" + GRADUATION_POINT_PATTERN + r")",
    re.IGNORECASE,
)
GRADUATE_COHORT_TITLE_RE = re.compile(
    r"\b(?P<year>20\d{2})\s+(?:new[ -])?graduate\b",
    re.IGNORECASE,
)
REMOTE_LOCATION_RE = re.compile(r"\b(?:fully\s+remote|remote)\b", re.IGNORECASE)
HYBRID_LOCATION_RE = re.compile(r"\bhybrid\b", re.IGNORECASE)
ONSITE_LOCATION_RE = re.compile(r"\b(?:on[ -]?site|in[ -]?person)\b", re.IGNORECASE)
REMOTE_DESCRIPTION_RE = re.compile(
    r"\b(?:fully|100\s*%)\s+remote\b|"
    r"\bremote[ -]?(?:role|position|workplace|eligible)\b|"
    r"\bwork(?:ing)?\s+remotely\b",
    re.IGNORECASE,
)
HYBRID_DESCRIPTION_RE = re.compile(
    r"\bhybrid[ -]?(?:role|position|schedule|workplace)\b",
    re.IGNORECASE,
)
ONSITE_DESCRIPTION_RE = re.compile(
    r"\b(?:on[ -]?site|in[ -]?person)[ -]?(?:role|position|workplace|work)\b",
    re.IGNORECASE,
)
ONSITE_ONLY_DESCRIPTION_RE = re.compile(
    r"\b(?:fully|entirely|exclusively|100\s*%)\s+"
    r"(?:on[ -]?site|in[ -]?person)\b|"
    r"\b(?:on[ -]?site|in[ -]?person)\s+only\b|"
    r"\b(?:on[ -]?site|in[ -]?person)\s+"
    r"(?:attendance|presence|work)\s+(?:is\s+)?(?:mandatory|required)\b|"
    r"\b(?:must|required\s+to)\s+(?:work|be|report)\s+"
    r"(?:fully\s+)?(?:on[ -]?site|in[ -]?person)\b",
    re.IGNORECASE,
)
NONREMOTE_RE = re.compile(
    r"\bnot\s+(?:a\s+)?(?:fully\s+)?remote(?:\s+(?:role|position|workplace))?\b|"
    r"\bremote\s+(?:work|option)s?\s+(?:is|are)\s+not\s+available\b",
    re.IGNORECASE,
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


def _score_legacy(item: Opportunity, profile: Dict[str, Any]) -> Opportunity:
    """Apply the original additive scorer without changing its semantics."""
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
    document_routing = item.metadata.get("document_routing", {})
    provenance = (
        str(document_routing.get("provenance", ""))
        if isinstance(document_routing, dict)
        else ""
    )
    pinned = item.metadata.get("curated") is True and provenance in {
        CURATED_DOCUMENT_PROVENANCE,
        LEGACY_CURATED_DOCUMENT_PROVENANCE,
    }
    if not pinned:
        item.recommended_resume = recommend_document(item, profile)
        routing_metadata = (
            dict(document_routing) if isinstance(document_routing, dict) else {}
        )
        routing_metadata["provenance"] = PROFILE_DOCUMENT_PROVENANCE
        item.metadata["document_routing"] = routing_metadata
    return item


def _values(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def _normalized_token(value: Any) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", str(value).casefold())).strip("_")


def _structured_field_weights(matching: Dict[str, Any]) -> Dict[str, float]:
    configured = matching.get("field_weights", {})
    configured = configured if isinstance(configured, dict) else {}
    weights: Dict[str, float] = {}
    for field, default in DEFAULT_FIELD_WEIGHTS.items():
        try:
            value = float(configured.get(field, default))
        except (TypeError, ValueError):
            value = default
        weights[field] = max(0.0, min(1.0, value))
    return weights


def _structured_rule_evidence(
    item: Opportunity,
    rule: Dict[str, Any],
    field_weights: Dict[str, float],
    field_texts: Any = None,
    match_cache: Any = None,
) -> List[Dict[str, Any]]:
    terms = rule.get("terms", [])
    if not isinstance(terms, list) or not terms:
        return []
    raw_fields = rule.get("fields", DEFAULT_FIELDS)
    fields = raw_fields if isinstance(raw_fields, list) else DEFAULT_FIELDS
    texts = (
        field_texts
        if isinstance(field_texts, dict)
        else {
            field: str(getattr(item, field, "") or "").casefold()
            for field in MATCH_FIELDS
        }
    )
    cache = match_cache if isinstance(match_cache, dict) else {}
    evidence: List[Dict[str, Any]] = []
    for term in terms:
        normalized_term = str(term).strip().casefold()
        if not normalized_term:
            continue
        choices = []
        for order, field in enumerate(fields):
            if field not in MATCH_FIELDS:
                continue
            cache_key = (field, normalized_term)
            present = cache.get(cache_key)
            if present is None:
                text = texts.get(field, "")
                start = text.find(normalized_term)
                present = False
                while start >= 0:
                    end = start + len(normalized_term)
                    before_word = start > 0 and (
                        text[start - 1].isalnum() or text[start - 1] == "_"
                    )
                    after_word = end < len(text) and (
                        text[end].isalnum() or text[end] == "_"
                    )
                    if not before_word and not after_word:
                        present = True
                        break
                    start = text.find(normalized_term, start + 1)
                cache[cache_key] = present
            if present:
                choices.append((field_weights.get(field, 0.0), -order, field))
        if choices:
            strength, _order, field = max(choices)
            evidence.append(
                {
                    "term": str(term),
                    "field": field,
                    "strength": round(strength, 3),
                }
            )
    if str(rule.get("match", "any")).lower() == "all" and len(evidence) != len(terms):
        return []
    return evidence


def _structured_points(rule: Dict[str, Any], evidence: List[Dict[str, Any]]) -> int:
    if not evidence:
        return 0
    weight = int(rule.get("weight", 0))
    strengths = sorted((float(entry["strength"]) for entry in evidence), reverse=True)
    if rule.get("per_term"):
        maximum = max(1, int(rule.get("max_hits", len(strengths))))
        factor = sum(strengths[:maximum])
    else:
        factor = strengths[0]
    return int(round(weight * factor))


def _candidate_stage(profile: Dict[str, Any]) -> str:
    candidate = profile.get("candidate", {})
    candidate = candidate if isinstance(candidate, dict) else {}
    explicit = candidate.get("current_stage", candidate.get("career_stage", ""))
    stage = _normalized_token(explicit)
    if stage:
        return stage
    program = str(candidate.get("program", ""))
    if re.search(r"\bph\.?d\.?|doctoral\b", program, re.IGNORECASE):
        return "phd_student"
    if re.search(r"\bmaster(?:'s)?\b", program, re.IGNORECASE):
        return "masters_student"
    if re.search(r"\bbachelor(?:'s)?|undergraduate\b", program, re.IGNORECASE):
        return "undergraduate_student"
    return ""


def _completed_degree_levels(value: Any) -> List[str]:
    levels = set()
    for degree in _values(value):
        text = str(degree or "").casefold()
        if re.search(
            r"\b(?:ph\s*\.?\s*d\.?|d\s*\.?\s*phil\.?|doctorate|"
            r"doctoral\s+degree|doctor\s+of\s+philosophy)\b",
            text,
        ):
            levels.add("doctorate")
        elif re.search(
            r"\bmaster(?:'s|s)?\b|\bm\s*\.?\s*s\.?\s*(?:\b|$)|\bm\s*\.?\s*sc\.?\s*(?:\b|$)",
            text,
        ):
            levels.add("masters")
        elif re.search(
            r"\bbachelor(?:'s|s)?\b|\bb\s*\.?\s*s\.?\s*(?:\b|$)|\bb\s*\.?\s*sc\.?\s*(?:\b|$)",
            text,
        ):
            levels.add("bachelors")
        elif re.search(
            r"\bassociate(?:'s|s)?\b|\ba\s*\.?\s*[as]\.?\s*(?:\b|$)",
            text,
        ):
            levels.add("associates")
    return sorted(levels)


def _graduation_interval(value: Any) -> Any:
    """Normalize a graduation value to an inclusive month interval."""
    text = " ".join(str(value or "").strip().split())
    if not text or len(text) > 120:
        return None
    text = re.sub(
        r"\b(?:expected|anticipated|graduation|graduate|date)\b",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    text = " ".join(text.split()).strip(" ,:;()")
    match = re.fullmatch(r"(?P<year>20\d{2})-(?P<month>0?[1-9]|1[0-2])(?:-\d{1,2})?", text)
    if not match:
        match = re.fullmatch(r"(?P<month>0?[1-9]|1[0-2])[/-](?P<year>20\d{2})", text)
    if match:
        year = int(match.group("year"))
        month = int(match.group("month"))
        index = year * 12 + month - 1
        return {
            "start": index,
            "end": index,
            "precision": "month",
            "label": "{:04d}-{:02d}".format(year, month),
        }

    month_first = re.fullmatch(
        r"(?P<month>" + GRADUATION_MONTH_PATTERN + r")\.?\s+"
        r"(?:\d{1,2}(?:st|nd|rd|th)?,?\s+)?(?P<year>20\d{2})",
        text,
        flags=re.IGNORECASE,
    )
    day_first = re.fullmatch(
        r"(?:\d{1,2}(?:st|nd|rd|th)?\s+)?"
        r"(?P<month>" + GRADUATION_MONTH_PATTERN + r")\.?\s+(?P<year>20\d{2})",
        text,
        flags=re.IGNORECASE,
    )
    written = month_first or day_first
    if written:
        year = int(written.group("year"))
        month = GRADUATION_MONTHS[written.group("month").rstrip(".").casefold()]
        index = year * 12 + month - 1
        return {
            "start": index,
            "end": index,
            "precision": "month",
            "label": "{:04d}-{:02d}".format(year, month),
        }

    year_only = re.fullmatch(r"(?P<year>20\d{2})", text)
    if year_only:
        year = int(year_only.group("year"))
        return {
            "start": year * 12,
            "end": year * 12 + 11,
            "precision": "year",
            "label": str(year),
        }
    return None


def _listing_graduation_window(item: Opportunity) -> Any:
    cohort = GRADUATE_COHORT_TITLE_RE.search(str(item.title or "")[:240])
    if cohort:
        point = _graduation_interval(cohort.group("year"))
        if point:
            return {
                "start": point["start"],
                "end": point["end"],
                "label": point["label"],
            }
    text = "{} {}".format(item.eligibility, item.description)[:40000]
    ranged = GRADUATION_RANGE_RE.search(text)
    if ranged:
        start = _graduation_interval(ranged.group("start"))
        end = _graduation_interval(ranged.group("end"))
        if start and end and start["start"] <= end["end"]:
            return {
                "start": start["start"],
                "end": end["end"],
                "label": "{} to {}".format(start["label"], end["label"]),
            }
    exact = GRADUATION_EXACT_RE.search(text)
    point = _graduation_interval(exact.group("point")) if exact else None
    if point:
        return {
            "start": point["start"],
            "end": point["end"],
            "label": point["label"],
        }
    return None


def _normalized_remote_preference(value: Any) -> str:
    token = _normalized_token(value)
    if token in {
        "remote",
        "prefer_remote",
        "remote_preferred",
        "preferred",
        "yes",
    }:
        return "remote_preferred"
    if token in {"remote_only", "remote_required", "require_remote", "required"}:
        return "remote_required"
    if token in {
        "onsite",
        "on_site",
        "prefer_onsite",
        "onsite_preferred",
        "avoid_remote",
        "no_remote",
    }:
        return "onsite_preferred"
    if token in {"hybrid", "prefer_hybrid", "hybrid_preferred"}:
        return "hybrid_preferred"
    if token in {"none", "any", "no_preference", "flexible", "either"}:
        return "no_preference"
    return "unrecognized" if token else "not_configured"


def _listing_work_arrangement(item: Opportunity) -> Tuple[str, str]:
    location = str(item.location or "")
    location_signals = {
        name
        for name, pattern in (
            ("remote", REMOTE_LOCATION_RE),
            ("hybrid", HYBRID_LOCATION_RE),
            ("onsite", ONSITE_LOCATION_RE),
        )
        if pattern.search(location)
    }
    # Hybrid wording is intentionally non-blocking even when the same text also
    # says the role is not fully remote.
    if "hybrid" in location_signals:
        if len(location_signals) > 1:
            return "flexible", "location"
        return "hybrid", "location"
    if NONREMOTE_RE.search(location):
        return "nonremote", "location"
    if len(location_signals) == 1:
        return next(iter(location_signals)), "location"
    if len(location_signals) > 1:
        return "flexible", "location"

    description = str(item.description or "")[:20000]
    description_signals = {
        name
        for name, pattern in (
            ("remote", REMOTE_DESCRIPTION_RE),
            ("hybrid", HYBRID_DESCRIPTION_RE),
            ("onsite", ONSITE_DESCRIPTION_RE),
        )
        if pattern.search(description)
    }
    if "hybrid" in description_signals:
        if len(description_signals) > 1:
            return "flexible", "description"
        return "hybrid", "description"
    if NONREMOTE_RE.search(description):
        return "nonremote", "description"
    if len(description_signals) == 1:
        return next(iter(description_signals)), "description"
    if len(description_signals) > 1:
        return "flexible", "description"
    return "unknown", "none"


def _strong_remote_requirement_conflict(
    item: Opportunity, arrangement: str, field: str
) -> bool:
    """Return true only for strong evidence that contradicts remote-only intent."""
    if arrangement not in {"onsite", "nonremote"}:
        return False
    if field == "location":
        location = str(item.location or "")
        if arrangement == "nonremote":
            residual = NONREMOTE_RE.sub(" ", location)
            return bool(NONREMOTE_RE.search(location)) and not (
                HYBRID_LOCATION_RE.search(location)
                or REMOTE_LOCATION_RE.search(residual)
            )
        # A dedicated location value containing only an onsite signal is strong
        # structured evidence. Flexible and hybrid values are classified above.
        description = str(item.description or "")[:20000]
        return not (
            REMOTE_DESCRIPTION_RE.search(description)
            or HYBRID_DESCRIPTION_RE.search(description)
        )
    if field == "description":
        description = str(item.description or "")[:20000]
        if arrangement == "nonremote":
            residual = NONREMOTE_RE.sub(" ", description)
            return bool(NONREMOTE_RE.search(description)) and not (
                HYBRID_DESCRIPTION_RE.search(description)
                or REMOTE_DESCRIPTION_RE.search(residual)
            )
        return bool(ONSITE_ONLY_DESCRIPTION_RE.search(description))
    return False


def _remote_preference_effect(preference: str, arrangement: str, field: str) -> Dict[str, Any]:
    points_by_preference = {
        "remote_preferred": {
            "remote": 6,
            "hybrid": 2,
            "flexible": 2,
            "onsite": -4,
            "nonremote": -4,
        },
        "remote_required": {
            "remote": 6,
            "flexible": 2,
            "hybrid": -4,
            "onsite": -6,
            "nonremote": -6,
        },
        "onsite_preferred": {
            "onsite": 6,
            "nonremote": 6,
            "hybrid": 2,
            "flexible": 2,
            "remote": -4,
        },
        "hybrid_preferred": {
            "hybrid": 6,
            "flexible": 4,
            "remote": -2,
            "onsite": -2,
            "nonremote": -2,
        },
    }
    points = points_by_preference.get(preference, {}).get(arrangement, 0)
    if preference == "not_configured":
        state = "not_configured"
    elif preference == "unrecognized":
        state = "informational_unrecognized"
    elif preference == "no_preference":
        state = "informational_no_preference"
    elif arrangement == "unknown":
        state = "no_listing_evidence"
    else:
        state = "matched" if points > 0 else "mismatched" if points < 0 else "neutral"
    return {
        "mode": "preference_score",
        "state": state,
        "preference": preference,
        "listing_arrangement": arrangement,
        "evidence_field": field,
        "points": points,
    }


def _target_settings(profile: Dict[str, Any]) -> Dict[str, Any]:
    targets = profile.get("targets", {})
    return targets if isinstance(targets, dict) else {}


def _profile_terms(*values: Any) -> List[str]:
    terms: List[str] = []
    seen = set()
    for value in values:
        for entry in _values(value):
            if isinstance(entry, dict):
                raw = next(
                    (
                        entry.get(key)
                        for key in ("label", "name", "value", "id")
                        if entry.get(key) not in (None, "")
                    ),
                    "",
                )
            else:
                raw = entry
            term = " ".join(str(raw or "").replace("_", " ").split())[:120]
            key = term.casefold()
            if term and key not in seen:
                seen.add(key)
                terms.append(term)
            if len(terms) >= 100:
                return terms
    return terms


def _profile_rule_weight(matching: Dict[str, Any], name: str, default: int) -> int:
    weights = matching.get("profile_weights", {})
    weights = weights if isinstance(weights, dict) else {}
    try:
        return max(-100, min(100, int(weights.get(name, default))))
    except (TypeError, ValueError):
        return default


def _derived_profile_rules(profile: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Translate simple editable profile fields into bounded auditable rules."""
    targets = _target_settings(profile)
    candidate = profile.get("candidate", {})
    candidate = candidate if isinstance(candidate, dict) else {}
    matching = _matching_config(profile)
    rules: List[Dict[str, Any]] = []

    roles = _profile_terms(targets.get("role_families"), targets.get("roles"))
    if roles:
        rules.append(
            {
                "id": "profile_role_families",
                "label": "Preferred role",
                "dimension": "interest",
                "anchor": True,
                "weight": _profile_rule_weight(matching, "role_family", 28),
                "fields": ["title", "category", "description"],
                "terms": roles,
                "per_term": True,
                "max_hits": 2,
                "derived_profile": True,
            }
        )
    domains = _profile_terms(targets.get("domains"))
    if domains:
        rules.append(
            {
                "id": "profile_domains",
                "label": "Preferred domain",
                "dimension": "interest",
                "anchor": True,
                "weight": _profile_rule_weight(matching, "domain", 18),
                "fields": ["title", "category", "description"],
                "terms": domains,
                "per_term": True,
                "max_hits": 2,
                "derived_profile": True,
            }
        )
    skills = _profile_terms(
        targets.get("supporting_skills"),
        targets.get("skills"),
        candidate.get("skills"),
    )
    if skills:
        rules.append(
            {
                "id": "profile_skills",
                "label": "Relevant skills",
                "dimension": "qualification",
                "anchor": False,
                "weight": _profile_rule_weight(matching, "skill", 6),
                "fields": ["title", "category", "eligibility", "description"],
                "terms": skills,
                "per_term": True,
                "max_hits": 4,
                "derived_profile": True,
            }
        )
    locations = _profile_terms(targets.get("locations"))
    if locations:
        rules.append(
            {
                "id": "profile_locations",
                "label": "Preferred location",
                "dimension": "preference",
                "anchor": False,
                "weight": _profile_rule_weight(matching, "location", 8),
                "fields": ["location"],
                "terms": locations,
                "derived_profile": True,
            }
        )
    arrangements = _profile_terms(
        targets.get("work_arrangements"),
        targets.get("workplace_types"),
    )
    if arrangements:
        rules.append(
            {
                "id": "profile_work_arrangements",
                "label": "Preferred work arrangement",
                "dimension": "preference",
                "anchor": False,
                "weight": _profile_rule_weight(matching, "work_arrangement", 6),
                "fields": ["location", "description"],
                "terms": arrangements,
                "derived_profile": True,
            }
        )
    exclusions = _profile_terms(targets.get("exclusions"), profile.get("exclusions"))
    if exclusions:
        rules.extend(
            [
                {
                    "id": "profile_exclusion_gate",
                    "label": "Excluded work",
                    "dimension": "interest",
                    "anchor": False,
                    "hard_gate": True,
                    "weight": -1,
                    "fields": ["title", "category", "organization"],
                    "terms": exclusions,
                    "derived_profile": True,
                },
                {
                    "id": "profile_exclusion_description",
                    "label": "Possible excluded work",
                    "dimension": "interest",
                    "anchor": False,
                    "weight": _profile_rule_weight(
                        matching,
                        "description_exclusion",
                        -20,
                    ),
                    "fields": ["description"],
                    "terms": exclusions,
                    "description_exclusion": True,
                    "derived_profile": True,
                },
            ]
        )
    return rules


def _structured_rules(profile: Dict[str, Any]) -> List[Dict[str, Any]]:
    configured = effective_matching_rules(profile, _rules(profile))
    return configured + _derived_profile_rules(profile)


def _configured_timeframes(profile: Dict[str, Any]) -> Tuple[List[int], List[str]]:
    targets = _target_settings(profile)
    values: List[Any] = []
    for key in ("cycles", "timeframes", "target_cycles"):
        values.extend(_values(targets.get(key)))
    if not values:
        dashboard = profile.get("dashboard", {})
        candidate = profile.get("candidate", {})
        if isinstance(dashboard, dict):
            values.extend(_values(dashboard.get("target_season")))
        if isinstance(candidate, dict):
            values.extend(_values(candidate.get("target_season")))
    years = set()
    seasons = set()
    for value in values:
        if isinstance(value, dict):
            candidates = [value.get("year"), value.get("season"), value.get("label")]
        else:
            candidates = [value]
        text = " ".join(str(part) for part in candidates if part not in (None, ""))
        years.update(int(year) for year in re.findall(r"\b(20\d{2})\b", text))
        seasons.update(
            season.casefold()
            for season in re.findall(
                r"\b(spring|summer|fall|autumn|winter)\b",
                text,
                re.IGNORECASE,
            )
        )
    if "autumn" in seasons:
        seasons.add("fall")
        seasons.discard("autumn")
    return sorted(years), sorted(seasons)


def _listing_timeframes(item: Opportunity) -> Tuple[List[int], List[str]]:
    title_category = "{} {}".format(item.title, item.category)
    full_text = "{} {}".format(title_category, item.description[:20000])
    years = {int(year) for year in re.findall(r"\b(20\d{2})\b", item.title)}
    years.update(
        int(year)
        for year in re.findall(
            r"\b(20\d{2})\b(?=.{0,28}\b(?:intern(?:ship)?|fellowship|program|graduate)\b)",
            full_text,
            re.IGNORECASE,
        )
    )
    years.update(
        int(year)
        for year in re.findall(
            r"\b(?:spring|summer|fall|autumn|winter)\s+(20\d{2})\b",
            full_text,
            re.IGNORECASE,
        )
    )
    seasons = {
        season.casefold()
        for season in re.findall(
            r"\b(spring|summer|fall|autumn|winter)\b(?=\s+20\d{2}\b)",
            full_text,
            re.IGNORECASE,
        )
    }
    if _normalized_token(item.opportunity_type) in {
        "internship",
        "co_op",
        "fellowship",
        "research_program",
        "residency",
        "training",
    }:
        seasons.update(
            season.casefold()
            for season in re.findall(
                r"\b(spring|summer|fall|autumn|winter)\b",
                item.title,
                re.IGNORECASE,
            )
        )
    if "autumn" in seasons:
        seasons.add("fall")
        seasons.discard("autumn")
    return sorted(years), sorted(seasons)


def _required_experience_years(item: Opportunity) -> Any:
    values = []
    text = "{} {}".format(item.eligibility, item.description)
    for match in EXPERIENCE_RE.finditer(text):
        try:
            years = int(match.group("years"))
        except (TypeError, ValueError):
            continue
        phrase = match.group(0).casefold()
        before = re.split(r"[.\n]", text[max(0, match.start() - 80):match.start()])[-1]
        after = re.split(r"[.\n]", text[match.end():match.end() + 40])[0]
        context = "{} {} {}".format(before, phrase, after).casefold()
        hard_cue = bool(
            re.search(
                r"(?:minimum(?:\s+qualifications?)?|at\s+least|requires?|required|"
                r"must\s+have|(?:candidate|applicant|you)\s+(?:must\s+)?"
                r"(?:have|bring|possess))\b",
                context,
            )
        )
        preferred_only = bool(
            re.search(r"\b(?:preferred|nice\s+to\s+have|desired)\b", context)
        ) and not hard_cue
        if not preferred_only and ("+" in phrase or hard_cue):
            values.append(years)
    return min(values) if values else None


def _sentence_around(text: str, start: int, end: int) -> str:
    left = max(text.rfind(".", 0, start), text.rfind("\n", 0, start)) + 1
    period = text.find(".", end)
    newline = text.find("\n", end)
    right_candidates = [value for value in (period, newline) if value >= 0]
    right = min(right_candidates) if right_candidates else min(len(text), end + 240)
    return text[max(left, start - 240):min(right, end + 240)]


def _structured_gates(
    item: Opportunity,
    profile: Dict[str, Any],
    rule_results: List[Tuple[Dict[str, Any], List[Dict[str, Any]]]],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    targets = _target_settings(profile)
    candidate = profile.get("candidate", {})
    candidate = candidate if isinstance(candidate, dict) else {}
    completed_degrees_declared = (
        "completed_degrees" in candidate
        and candidate.get("completed_degrees") is not None
    )
    completed_degree_entries = _profile_terms(candidate.get("completed_degrees"))
    completed_degree_levels = _completed_degree_levels(completed_degree_entries)
    expected_graduation_raw = str(candidate.get("expected_graduation", "")).strip()
    candidate_graduation = _graduation_interval(expected_graduation_raw)
    remote_preference = _normalized_remote_preference(
        targets.get("remote_preference")
    )
    listing_arrangement, arrangement_field = _listing_work_arrangement(item)
    remote_effect = _remote_preference_effect(
        remote_preference,
        listing_arrangement,
        arrangement_field,
    )
    degree_effect: Dict[str, Any] = {
        "mode": "degree_completion_gate",
        "state": (
            "configured"
            if completed_degree_levels
            else "informational_unrecognized"
            if completed_degree_entries
            else "declared_empty"
            if completed_degrees_declared
            else "not_configured"
        ),
        "normalized_levels": completed_degree_levels,
    }
    graduation_effect: Dict[str, Any] = {
        "mode": "explicit_graduation_window_gate",
        "state": (
            "no_listing_constraint"
            if candidate_graduation
            else "informational_unparsed"
            if expected_graduation_raw
            else "not_configured"
        ),
    }
    if candidate_graduation:
        graduation_effect["normalized"] = candidate_graduation["label"]
    gates: List[Dict[str, Any]] = []
    remote_conflict = _strong_remote_requirement_conflict(
        item,
        listing_arrangement,
        arrangement_field,
    )
    if remote_preference == "remote_required":
        remote_effect["requirement_state"] = (
            "incompatible" if remote_conflict else "not_contradicted"
        )
        if remote_conflict:
            evidence = (
                "onsite-only {}".format(arrangement_field)
                if listing_arrangement == "onsite"
                else "explicit nonremote {}".format(arrangement_field)
            )
            gates.append(
                {
                    "id": "remote_requirement",
                    "state": "fail",
                    "evidence": [evidence],
                }
            )
    else:
        remote_effect["requirement_state"] = "not_applicable"
    target_types = {
        _normalized_token(value)
        for value in _values(targets.get("opportunity_types"))
        if _normalized_token(value)
    }
    item_type = _normalized_token(item.opportunity_type)
    if target_types:
        if item_type in target_types:
            gates.append({"id": "opportunity_type", "state": "pass", "evidence": [item_type]})
        elif item_type in {"", "opportunity", "program"}:
            gates.append({"id": "opportunity_type", "state": "unknown", "evidence": []})
        elif targets.get("strict_opportunity_types", True):
            gates.append({"id": "opportunity_type", "state": "fail", "evidence": [item_type]})

    target_years, target_seasons = _configured_timeframes(profile)
    listing_years, listing_seasons = _listing_timeframes(item)
    if target_years or target_seasons:
        year_mismatch = bool(
            target_years
            and listing_years
            and not set(target_years).intersection(listing_years)
        )
        season_mismatch = bool(
            target_seasons
            and listing_seasons
            and not set(target_seasons).intersection(listing_seasons)
        )
        has_evidence = bool(
            (target_years and listing_years)
            or (target_seasons and listing_seasons)
        )
        if year_mismatch or season_mismatch:
            state = "fail" if targets.get("strict_timeframes", True) else "unknown"
        else:
            state = "pass" if has_evidence else "unknown"
        gates.append(
            {
                "id": "timeframe",
                "state": state,
                "evidence": [str(year) for year in listing_years] + listing_seasons,
            }
        )

    stage = _candidate_stage(profile)
    seniority = (
        "senior"
        if STRONG_SENIOR_TITLE_RE.search(item.title)
        or (MANAGER_TITLE_RE.search(item.title) and not EARLY_TITLE_RE.search(item.title))
        else "not_detected"
    )
    if stage in EARLY_CAREER_STAGES and seniority == "senior":
        gates.append({"id": "career_stage", "state": "fail", "evidence": ["senior title"]})

    requirements_text = "{} {}".format(item.eligibility, item.description)
    undergraduate_only = UNDERGRADUATE_ONLY_RE.search(requirements_text)
    undergraduate_context = (
        _sentence_around(
            requirements_text,
            undergraduate_only.start(),
            undergraduate_only.end(),
        )
        if undergraduate_only
        else ""
    )
    if (
        stage in EARLY_CAREER_STAGES - {"undergraduate", "undergraduate_student"}
        and undergraduate_only
        and not ADVANCED_DEGREE_ALTERNATIVE_RE.search(undergraduate_context)
    ):
        gates.append(
            {"id": "degree_stage", "state": "fail", "evidence": ["undergraduate-only wording"]}
        )
    completed_phd = COMPLETED_PHD_RE.search(requirements_text)
    completed_phd_context = (
        _sentence_around(
            requirements_text,
            completed_phd.start(),
            completed_phd.end(),
        )
        if completed_phd
        else ""
    )
    if completed_phd and not PURSUING_PHD_RE.search(completed_phd_context):
        degree_effect["listing_constraint"] = "completed_doctorate"
        if "doctorate" in completed_degree_levels:
            degree_effect["state"] = "evaluated_match"
            gates.append(
                {
                    "id": "degree_completion",
                    "state": "pass",
                    "evidence": ["completed doctorate on profile"],
                }
            )
        elif completed_degrees_declared or stage in {
            "undergraduate",
            "undergraduate_student",
            "masters_student",
            "phd",
            "phd_student",
            "doctoral_student",
        }:
            degree_effect["state"] = "evaluated_no_match"
            gates.append(
                {
                    "id": "degree_completion",
                    "state": "fail",
                    "evidence": ["completed doctorate required"],
                }
            )
        else:
            degree_effect["state"] = "missing_for_listing_constraint"
            gates.append(
                {
                    "id": "degree_completion",
                    "state": "unknown",
                    "evidence": ["completed doctorate required"],
                }
            )

    graduation_window = _listing_graduation_window(item)
    if graduation_window:
        graduation_effect["listing_constraint"] = graduation_window["label"]
        if not candidate_graduation:
            graduation_effect["state"] = (
                "unparsed_for_listing_constraint"
                if expected_graduation_raw
                else "missing_for_listing_constraint"
            )
            gates.append(
                {
                    "id": "graduation_window",
                    "state": "unknown",
                    "evidence": ["required {}".format(graduation_window["label"])],
                }
            )
        else:
            outside = (
                candidate_graduation["end"] < graduation_window["start"]
                or candidate_graduation["start"] > graduation_window["end"]
            )
            inside = (
                candidate_graduation["start"] >= graduation_window["start"]
                and candidate_graduation["end"] <= graduation_window["end"]
            )
            state = "fail" if outside else "pass" if inside else "unknown"
            graduation_effect["state"] = (
                "evaluated_match"
                if state == "pass"
                else "evaluated_mismatch"
                if state == "fail"
                else "evaluated_overlap"
            )
            gates.append(
                {
                    "id": "graduation_window",
                    "state": state,
                    "evidence": [
                        candidate_graduation["label"],
                        "required {}".format(graduation_window["label"]),
                    ],
                }
            )

    required_years = _required_experience_years(item)
    maximum_years = candidate.get("max_required_experience_years")
    if isinstance(maximum_years, int) and not isinstance(maximum_years, bool):
        if required_years is None:
            gates.append({"id": "experience", "state": "unknown", "evidence": []})
        elif required_years > maximum_years:
            gates.append(
                {"id": "experience", "state": "fail", "evidence": ["{}+ years".format(required_years)]}
            )
        else:
            gates.append(
                {"id": "experience", "state": "pass", "evidence": ["{} years".format(required_years)]}
            )

    for rule, evidence in rule_results:
        if not rule.get("hard_gate"):
            continue
        weight = int(rule.get("weight", 0))
        failed = (weight < 0 and bool(evidence)) or (weight >= 0 and not evidence)
        gates.append(
            {
                "id": str(rule.get("id", "configured_gate")),
                "state": "fail" if failed else "pass",
                "evidence": [str(entry["term"]) for entry in evidence[:5]],
            }
        )

    features = {
        "opportunity_type": item_type or "unknown",
        "candidate_stage": stage or "unknown",
        "seniority": seniority,
        "required_experience_years": required_years,
        "target_years": target_years,
        "listing_years": listing_years,
        "target_seasons": target_seasons,
        "listing_seasons": listing_seasons,
        "profile_effects": {
            "completed_degrees": degree_effect,
            "expected_graduation": graduation_effect,
            "remote_preference": remote_effect,
        },
    }
    return gates, features


def _score_structured(item: Opportunity, profile: Dict[str, Any]) -> Opportunity:
    matching = _matching_config(profile)
    score = int(matching.get("base_score", 25))
    field_weights = _structured_field_weights(matching)
    field_texts = {
        field: str(getattr(item, field, "") or "").casefold()
        for field in MATCH_FIELDS
    }
    match_cache: Dict[Tuple[str, str], bool] = {}
    reasons: List[str] = []
    warnings: List[str] = []
    components: List[Dict[str, Any]] = [
        {"id": "base", "label": "Starting score", "points": score, "evidence": []}
    ]
    dimensions: Dict[str, Dict[str, Any]] = {
        name: {"points": 0, "matches": []} for name in STRUCTURED_DIMENSIONS
    }
    rule_results: List[Tuple[Dict[str, Any], List[Dict[str, Any]]]] = []
    anchor_matched = False
    has_interest_rules = False
    strongest_positive = 0.0
    description_exclusion_matched = False

    for rule in _structured_rules(profile):
        dimension = str(rule.get("dimension", "interest")).casefold()
        if dimension not in dimensions:
            dimension = "interest"
        weight = int(rule.get("weight", 0))
        if dimension == "interest" and weight > 0:
            has_interest_rules = True
        evidence = _structured_rule_evidence(
            item,
            rule,
            field_weights,
            field_texts,
            match_cache,
        )
        rule_results.append((rule, evidence))
        if not evidence:
            continue
        if rule.get("description_exclusion"):
            description_exclusion_matched = True
        points = _structured_points(rule, evidence)
        strongest = max(float(entry["strength"]) for entry in evidence)
        if weight > 0:
            strongest_positive = max(strongest_positive, strongest)
        default_anchor = dimension == "interest" and weight > 0
        if bool(rule.get("anchor", default_anchor)) and strongest >= float(
            matching.get("anchor_min_strength", 0.7)
        ):
            anchor_matched = True
        if rule.get("hard_gate"):
            continue
        score += points
        label = str(rule.get("label", "Configured match"))
        detail = "{}: {}".format(label, ", ".join(str(entry["term"]) for entry in evidence[:3]))
        component = {
            "id": str(rule.get("id", label)).strip() or "configured_rule",
            "label": label,
            "points": points,
            "dimension": dimension,
            "evidence": evidence[:5],
        }
        components.append(component)
        dimensions[dimension]["points"] += points
        dimensions[dimension]["matches"].append(component["id"])
        (reasons if points > 0 else warnings).append(detail)

    gates, features = _structured_gates(item, profile, rule_results)
    gate_states = {entry["state"] for entry in gates}
    target_types = {
        _normalized_token(value)
        for value in _values(_target_settings(profile).get("opportunity_types"))
        if _normalized_token(value)
    }
    if target_types and features["opportunity_type"] in target_types:
        bonus = int(matching.get("target_type_bonus", 10))
        score += bonus
        dimensions["target"]["points"] += bonus
        dimensions["target"]["matches"].append("target_type")
        components.append(
            {
                "id": "target_type",
                "label": "Requested opportunity type",
                "points": bonus,
                "dimension": "target",
                "evidence": [features["opportunity_type"]],
            }
        )
    year_match = bool(
        features["target_years"]
        and set(features["target_years"]).intersection(features["listing_years"])
    )
    season_match = bool(
        features["target_seasons"]
        and set(features["target_seasons"]).intersection(
            features["listing_seasons"]
        )
    )
    year_mismatch = bool(
        features["target_years"]
        and features["listing_years"]
        and not year_match
    )
    season_mismatch = bool(
        features["target_seasons"]
        and features["listing_seasons"]
        and not season_match
    )
    if (year_match or season_match) and not (year_mismatch or season_mismatch):
        bonus = int(matching.get("target_timeframe_bonus", 10))
        score += bonus
        dimensions["target"]["points"] += bonus
        dimensions["target"]["matches"].append("target_timeframe")
        components.append(
            {
                "id": "target_timeframe",
                "label": "Requested timeframe",
                "points": bonus,
                "dimension": "target",
                "evidence": [str(value) for value in features["listing_years"]]
                + features["listing_seasons"],
            }
        )

    remote_effect = features["profile_effects"]["remote_preference"]
    remote_points = int(remote_effect.get("points", 0))
    if remote_points:
        score += remote_points
        dimensions["preference"]["points"] += remote_points
        dimensions["preference"]["matches"].append("profile_remote_preference")
        components.append(
            {
                "id": "profile_remote_preference",
                "label": "Remote-work preference",
                "points": remote_points,
                "dimension": "preference",
                "evidence": [
                    {
                        "term": remote_effect["listing_arrangement"],
                        "field": remote_effect["evidence_field"],
                        "strength": (
                            1.0
                            if remote_effect["evidence_field"] == "location"
                            else 0.6
                        ),
                    }
                ],
            }
        )
        detail = "Remote preference: {}".format(
            remote_effect["listing_arrangement"]
        )
        (reasons if remote_points > 0 else warnings).append(detail)

    priorities = {
        str(name).strip().casefold()
        for name in profile.get("priority_organizations", [])
        if str(name).strip()
    }
    if item.organization.strip().casefold() in priorities:
        bonus = int(matching.get("priority_organization_bonus", 10))
        score += bonus
        dimensions["preference"]["points"] += bonus
        dimensions["preference"]["matches"].append("preferred_organization")
        components.append(
            {
                "id": "preferred_organization",
                "label": "Preferred organization",
                "points": bonus,
                "dimension": "preference",
                "evidence": [item.organization],
            }
        )
        reasons.append("Priority organization")

    raw_score = max(0, min(100, score))
    ceilings = matching.get("score_ceilings", {})
    ceilings = ceilings if isinstance(ceilings, dict) else {}
    applied_ceilings: List[Dict[str, Any]] = []
    if has_interest_rules and not anchor_matched:
        ceiling = int(ceilings.get("no_anchor", 49))
        score = min(score, ceiling)
        applied_ceilings.append({"id": "no_anchor", "score": ceiling})
    if strongest_positive and strongest_positive <= field_weights["description"]:
        ceiling = int(ceilings.get("description_only", 49))
        score = min(score, ceiling)
        applied_ceilings.append({"id": "description_only", "score": ceiling})
    if description_exclusion_matched:
        ceiling = int(ceilings.get("description_exclusion", 49))
        score = min(score, ceiling)
        applied_ceilings.append({"id": "description_exclusion", "score": ceiling})
    if "unknown" in gate_states:
        ceiling = int(ceilings.get("unknown_eligibility", 79))
        score = min(score, ceiling)
        applied_ceilings.append({"id": "unknown_eligibility", "score": ceiling})

    failed_gates = [entry for entry in gates if entry["state"] == "fail"]
    if failed_gates:
        score = 0
        for gate in failed_gates:
            warnings.append(
                "{} mismatch{}".format(
                    str(gate["id"]).replace("_", " ").title(),
                    ": {}".format(", ".join(gate["evidence"])) if gate["evidence"] else "",
                )
            )
    item.score = max(0, min(100, int(round(score))))
    minimum_display = int(matching.get("minimum_display_score", 40))
    visible = not failed_gates and item.score >= minimum_display
    item.tier = _tier(item.score, profile) if visible else "skip"
    item.reasons = reasons[:6]
    item.warnings = warnings[:6]
    eligibility = "ineligible" if failed_gates else ("unknown" if "unknown" in gate_states else "compatible")
    item.metadata["eligibility_state"] = eligibility
    item.metadata["match"] = {
        "engine": STRUCTURED_ENGINE,
        "fit_score": item.score,
        "raw_score": raw_score,
        "components": components,
        "dimensions": dimensions,
        "features": features,
        "gates": gates,
        "eligibility": eligibility,
        "visibility": {
            "state": "visible" if visible else "hidden",
            "minimum_score": minimum_display,
            "anchor_matched": anchor_matched,
            "ceilings": applied_ceilings,
            "reasons": [entry["id"] for entry in failed_gates]
            or (["below_minimum_score"] if item.score < minimum_display else []),
        },
    }

    document_routing = item.metadata.get("document_routing", {})
    provenance = (
        str(document_routing.get("provenance", ""))
        if isinstance(document_routing, dict)
        else ""
    )
    pinned = item.metadata.get("curated") is True and provenance in {
        CURATED_DOCUMENT_PROVENANCE,
        LEGACY_CURATED_DOCUMENT_PROVENANCE,
    }
    if not pinned:
        item.recommended_resume = recommend_document(item, profile)
        routing_metadata = dict(document_routing) if isinstance(document_routing, dict) else {}
        routing_metadata["provenance"] = PROFILE_DOCUMENT_PROVENANCE
        item.metadata["document_routing"] = routing_metadata
    return item


def score_opportunity(item: Opportunity, profile: Dict[str, Any]) -> Opportunity:
    """Select the configured deterministic engine and attach an auditable result."""
    if str(_matching_config(profile).get("engine", "legacy")).casefold() == STRUCTURED_ENGINE:
        return _score_structured(item, profile)
    return _score_legacy(item, profile)


def _document_routes(profile: Dict[str, Any]) -> Tuple[str, List[Dict[str, Any]]]:
    documents = profile.get("documents", {})
    if not isinstance(documents, dict):
        documents = {}
    default = str(documents.get("default", profile.get("default_resume_code", "General"))).strip()
    routes = documents.get("routes", [])
    combined = list(routes) if isinstance(routes, list) else []
    combined.extend(profile.get("resume_routing", []))
    return default, [route for route in combined if isinstance(route, dict)]


def _canonical_fields(value: Any, default: Sequence[str]) -> List[str]:
    configured = value if isinstance(value, list) else default
    return sorted({str(field) for field in configured if str(field) in MATCH_FIELDS})


def _canonical_rule_terms(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    # Rule evidence is stored and displayed, so its original spelling and order
    # are part of the effective derived output even though matching ignores case.
    return [str(term) for term in value]


def _canonical_route_terms(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    # Document routes use only their hit count, so order and case are immaterial.
    return sorted(str(term).strip().lower() for term in value if str(term).strip())


def _canonical_rule(rule: Dict[str, Any]) -> Dict[str, Any]:
    label = str(rule.get("label", "Configured match"))
    per_term = bool(rule.get("per_term", False))
    weight = int(rule.get("weight", 0))
    dimension = str(rule.get("dimension", "interest")).casefold()
    default_anchor = dimension == "interest" and weight > 0
    maximum = None
    if per_term and "max_hits" in rule:
        maximum = max(1, int(rule["max_hits"]))
    return {
        "id": str(rule.get("id", label)).strip() or "configured_rule",
        "label": label,
        "weight": weight,
        "terms": _canonical_rule_terms(rule.get("terms", [])),
        "fields": _canonical_fields(rule.get("fields", DEFAULT_FIELDS), DEFAULT_FIELDS),
        "match": "all" if str(rule.get("match", "any")).lower() == "all" else "any",
        "per_term": per_term,
        "max_hits": maximum,
        "dimension": dimension,
        "anchor": bool(rule.get("anchor", default_anchor)),
        "hard_gate": bool(rule.get("hard_gate", False)),
    }


def _canonical_document_route(route: Dict[str, Any]) -> Dict[str, Any]:
    fields = route.get("fields", ["title", "description", "category"])
    return {
        "label": str(route.get("label", route.get("code", ""))).strip(),
        "terms": _canonical_route_terms(route.get("terms", [])),
        "fields": _canonical_fields(fields, DEFAULT_FIELDS),
    }


def profile_fingerprint(profile: Dict[str, Any]) -> str:
    """Hash only the effective profile values which can change fit or routing."""
    matching = _matching_config(profile)
    thresholds = matching.get("tier_thresholds", {})
    if not isinstance(thresholds, dict):
        thresholds = {}
    document_default, document_routes = _document_routes(profile)
    priorities = sorted(
        {
            str(name).strip().casefold()
            for name in profile.get("priority_organizations", [])
            if str(name).strip()
        }
    )
    engine = str(matching.get("engine", "legacy")).casefold()
    structured_profile: Dict[str, Any] = {}
    if engine == STRUCTURED_ENGINE:
        candidate = profile.get("candidate", {})
        candidate = candidate if isinstance(candidate, dict) else {}
        targets = _target_settings(profile)
        target_years, target_seasons = _configured_timeframes(profile)
        completed_degrees_declared = (
            "completed_degrees" in candidate
            and candidate.get("completed_degrees") is not None
        )
        completed_entries = _profile_terms(candidate.get("completed_degrees"))
        completed_levels = _completed_degree_levels(completed_entries)
        expected_raw = str(candidate.get("expected_graduation", "")).strip()
        expected_graduation = _graduation_interval(expected_raw)
        structured_profile = {
            "candidate": {
                "current_stage": candidate.get("current_stage"),
                "career_stage": candidate.get("career_stage"),
                "program": candidate.get("program"),
                "completed_degrees": {
                    "state": (
                        "configured"
                        if completed_levels
                        else "unrecognized"
                        if completed_entries
                        else "declared_empty"
                        if completed_degrees_declared
                        else "not_configured"
                    ),
                    "normalized_levels": completed_levels,
                },
                "expected_graduation": (
                    {
                        "state": "parsed",
                        "start": expected_graduation["start"],
                        "end": expected_graduation["end"],
                        "precision": expected_graduation["precision"],
                    }
                    if expected_graduation
                    else {
                        "state": "unparsed" if expected_raw else "not_configured"
                    }
                ),
                "max_required_experience_years": candidate.get(
                    "max_required_experience_years"
                ),
            },
            "targets": {
                "opportunity_types": sorted(
                    {
                        _normalized_token(value)
                        for value in _values(targets.get("opportunity_types"))
                        if _normalized_token(value)
                    }
                ),
                "years": target_years,
                "seasons": target_seasons,
                "strict_opportunity_types": bool(
                    targets.get("strict_opportunity_types", True)
                ),
                "strict_timeframes": bool(targets.get("strict_timeframes", True)),
                "remote_preference": _normalized_remote_preference(
                    targets.get("remote_preference")
                ),
            },
        }
    ceilings = matching.get("score_ceilings", {})
    ceilings = ceilings if isinstance(ceilings, dict) else {}
    effective = {
        "scoring_schema_version": SCORING_SCHEMA_VERSION,
        "matching": {
            "engine": engine,
            "base_score": int(
                matching.get(
                    "base_score",
                    25 if engine == STRUCTURED_ENGINE else 50,
                )
            ),
            "priority_organization_bonus": int(
                matching.get("priority_organization_bonus", 10)
            ),
            "tier_thresholds": {
                "priority": int(thresholds.get("priority", 75)),
                "strong": int(thresholds.get("strong", 55)),
                "watch": int(thresholds.get("watch", 25)),
            },
            "minimum_display_score": int(
                matching.get("minimum_display_score", 40)
                if engine == STRUCTURED_ENGINE
                else thresholds.get("watch", 25)
            ),
            "anchor_min_strength": float(matching.get("anchor_min_strength", 0.7)),
            "target_type_bonus": int(matching.get("target_type_bonus", 10)),
            "target_timeframe_bonus": int(
                matching.get("target_timeframe_bonus", 10)
            ),
            "field_weights": _structured_field_weights(matching),
            "score_ceilings": {
                "no_anchor": int(ceilings.get("no_anchor", 49)),
                "description_only": int(ceilings.get("description_only", 49)),
                "description_exclusion": int(
                    ceilings.get("description_exclusion", 49)
                ),
                "unknown_eligibility": int(
                    ceilings.get("unknown_eligibility", 79)
                ),
            },
            "rules": [
                _canonical_rule(rule)
                for rule in (
                    _structured_rules(profile)
                    if engine == STRUCTURED_ENGINE
                    else _rules(profile)
                )
            ],
        },
        "structured_profile": structured_profile,
        "priority_organizations": priorities,
        "documents": {
            "default": document_default,
            "routes": [
                _canonical_document_route(route) for route in document_routes
            ],
        },
    }
    payload = json.dumps(
        effective,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


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
