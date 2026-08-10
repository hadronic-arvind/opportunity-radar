"""Small normalization helpers used throughout the pipeline."""

import hashlib
import html
import re
from typing import Optional


TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")
OPPORTUNITY_TYPE_PATTERNS = (
    ("postdoc", re.compile(r"\bpost[ -]?doc(?:toral)?\b", re.IGNORECASE)),
    ("fellowship", re.compile(r"\b(?:fellowships?|fellows?)\b", re.IGNORECASE)),
    ("apprenticeship", re.compile(r"\bapprentice(?:ship)?\b", re.IGNORECASE)),
    ("co_op", re.compile(r"\bco[ -]?op\b", re.IGNORECASE)),
    (
        "residency",
        re.compile(r"\b(?:residency|resident(?:ial)? program)\b", re.IGNORECASE),
    ),
    ("scholarship", re.compile(r"\bscholarship\b", re.IGNORECASE)),
    ("internship", re.compile(r"\bintern(?:ship)?\b", re.IGNORECASE)),
    (
        "research_program",
        re.compile(
            r"\b(?:research (?:experience|program)|summer school)\b",
            re.IGNORECASE,
        ),
    ),
    ("training", re.compile(r"\b(?:training program|trainee)\b", re.IGNORECASE)),
)


def clean_text(value: Optional[str]) -> str:
    if not value:
        return ""
    return SPACE_RE.sub(" ", html.unescape(TAG_RE.sub(" ", value))).strip()


def normalize_opportunity_type(value: object) -> str:
    if not isinstance(value, (str, int, float)):
        return ""
    normalized = clean_text(str(value)).lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "fulltime": "job",
        "full_time": "job",
        "parttime": "job",
        "part_time": "job",
        "permanent": "job",
        "regular": "job",
        "intern": "internship",
        "internship": "internship",
        "fellow": "fellowship",
        "fellowship": "fellowship",
        "post_doc": "postdoc",
        "postdoctoral": "postdoc",
        "post_doctoral": "postdoc",
        "apprentice": "apprenticeship",
        "apprenticeship": "apprenticeship",
        "coop": "co_op",
        "co_op": "co_op",
        "resident": "residency",
        "residency": "residency",
        "scholarship": "scholarship",
        "research_program": "research_program",
        "training": "training",
        "program": "program",
        "job": "job",
        "opportunity": "opportunity",
    }
    return aliases.get(normalized, "")


def infer_opportunity_type(
    title: object,
    structured_values: tuple[object, ...] = (),
    declared_types: tuple[object, ...] = (),
    explicit: object = "",
    default: str = "job",
) -> str:
    """Normalize common opportunity types from title and structured evidence."""
    override = normalize_opportunity_type(explicit)
    if override:
        return override
    evidence = " ".join(
        clean_text(str(value))
        for value in (title,) + structured_values
        if isinstance(value, (str, int, float)) and clean_text(str(value))
    )
    for opportunity_type, pattern in OPPORTUNITY_TYPE_PATTERNS:
        if pattern.search(evidence):
            return opportunity_type
    for value in structured_values:
        normalized = normalize_opportunity_type(value)
        if normalized:
            return normalized
    declared = {
        normalized
        for normalized in (normalize_opportunity_type(value) for value in declared_types)
        if normalized
    }
    return declared.pop() if len(declared) == 1 else default


def stable_hash(value: str, length: int = 20) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def canonical_url(value: str) -> str:
    value = value.strip()
    value = re.sub(r"([?&])(gh_jid|lever-source)=[^&#]+", "", value)
    value = value.replace("?&", "?").rstrip("?&/")
    return value


def iso_from_millis(value: object) -> Optional[str]:
    if value in (None, ""):
        return None
    try:
        from datetime import datetime, timezone

        return datetime.fromtimestamp(float(value) / 1000, timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        return None
