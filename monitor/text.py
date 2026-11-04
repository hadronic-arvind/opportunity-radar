"""Small normalization helpers used throughout the pipeline."""

import hashlib
import html
import re
from typing import Optional
from urllib.parse import urlsplit, urlunsplit


TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")
NON_EMPLOYMENT_TYPES = frozenset({
    "scholarship", "grant", "course", "conference", "event", "competition", "hackathon", "volunteer",
})
OPPORTUNITY_TYPE_PATTERNS = (
    ("hackathon", re.compile(r"\bhackathons?\b", re.IGNORECASE)),
    ("conference", re.compile(r"\b(?:conferences?|symposium|symposia)\b", re.IGNORECASE)),
    ("competition", re.compile(r"\b(?:competition|challenge)\s+(?:for|program|20\d{2})\b", re.IGNORECASE)),
    ("postdoc", re.compile(r"\bpost[ -]?doc(?:toral)?\b", re.IGNORECASE)),
    ("fellowship", re.compile(r"\b(?:fellowships?|fellows?)\b", re.IGNORECASE)),
    ("apprenticeship", re.compile(r"\b(?:apprentice(?:ships?)?|ausbildung|ausbildungsplatz)\b", re.IGNORECASE)),
    ("co_op", re.compile(r"\bco[ -]?op\b", re.IGNORECASE)),
    (
        "residency",
        re.compile(r"\b(?:residency|resident(?:ial)? program)\b", re.IGNORECASE),
    ),
    ("scholarship", re.compile(r"\bscholarship\b", re.IGNORECASE)),
    ("internship", re.compile(r"\b(?:intern(?:ships?)?|praktikum)\b", re.IGNORECASE)),
    (
        "research_program",
        re.compile(
            r"\b(?:research (?:experience|program)|summer school)\b",
            re.IGNORECASE,
        ),
    ),
    ("grant", re.compile(r"\b(?:research|travel|project|seed|dissertation) grants?\b", re.IGNORECASE)),
    ("volunteer", re.compile(r"\bvolunteer(?:ing)?\b", re.IGNORECASE)),
    ("training", re.compile(r"\b(?:training program|trainee)\b", re.IGNORECASE)),
)


def clean_text(value: Optional[str]) -> str:
    if not value:
        return ""
    # Some ATS feeds encode the entire HTML fragment, including its tags.
    for _ in range(2):
        value = html.unescape(value)
    return SPACE_RE.sub(" ", TAG_RE.sub(" ", value)).strip()


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
        "praktikum": "internship",
        "fellow": "fellowship",
        "fellowship": "fellowship",
        "post_doc": "postdoc",
        "postdoctoral": "postdoc",
        "post_doctoral": "postdoc",
        "apprentice": "apprenticeship",
        "apprenticeship": "apprenticeship",
        "ausbildung": "apprenticeship",
        "coop": "co_op",
        "co_op": "co_op",
        "resident": "residency",
        "residency": "residency",
        "residencies": "residency",
        "scholarship": "scholarship",
        "research_program": "research_program",
        "training": "training",
        "program": "program",
        "hackathon": "hackathon",
        "conference": "conference",
        "symposium": "conference",
        "event": "event",
        "competition": "competition",
        "grant": "grant",
        "funding": "grant",
        "course": "course",
        "volunteer": "volunteer",
        "volunteering": "volunteer",
        "job": "job",
        "opportunity": "opportunity",
    }
    return aliases.get(normalized, "") or (
        aliases.get(normalized[:-1], "") if normalized.endswith("s") else ""
    )


def infer_opportunity_type(
    title: object,
    structured_values: tuple[object, ...] = (),
    declared_types: tuple[object, ...] = (),
    explicit: object = "",
    default: str = "job",
    *,
    employment_feed: bool = False,
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
    structured = [kind for value in structured_values if (kind := normalize_opportunity_type(value))]
    declared = {kind for value in declared_types if (kind := normalize_opportunity_type(value))}
    only_employment = employment_feed and default == "job" and not (
        NON_EMPLOYMENT_TYPES.intersection(declared) or NON_EMPLOYMENT_TYPES.intersection(structured)
    )
    for opportunity_type, pattern in OPPORTUNITY_TYPE_PATTERNS:
        if only_employment and opportunity_type in NON_EMPLOYMENT_TYPES:
            continue
        if pattern.search(evidence):
            return opportunity_type
    if structured:
        return structured[0]
    return declared.pop() if len(declared) == 1 else default


def stable_hash(value: str, length: int = 20) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def canonical_url(value: str) -> str:
    value = value.strip()
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value
    if not parsed.query:
        return value.rstrip("?&/")
    path_job_id = parsed.path.rstrip("/").rsplit("/", 1)[-1]
    query = []
    for entry in parsed.query.split("&"):
        key, _, item = entry.partition("=")
        if key == "lever-source":
            continue
        if key == "gh_jid" and path_job_id.isdigit() and item == path_job_id:
            continue
        query.append(entry)
    return urlunsplit(parsed._replace(query="&".join(query))).rstrip("?&/")


def iso_from_millis(value: object) -> Optional[str]:
    if value in (None, ""):
        return None
    try:
        from datetime import datetime, timezone

        return datetime.fromtimestamp(float(value) / 1000, timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        return None
