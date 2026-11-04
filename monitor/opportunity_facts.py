"""Project source evidence into a shared, local opportunity brief."""

import re
from datetime import date
from typing import Any, Dict, Mapping, Optional

from .text import clean_text, normalize_opportunity_type


EVIDENCE_PATTERNS = {
    "activities": (
        (
            "Research",
            r"\b(?:conduct|carry out|perform|propose|participate in|undertake)\s+(?:[\w-]+\s+){0,5}research\b",
        ),
        (
            "Building or competing",
            r"\b(?:participants|teams|you)\s+(?:will|can)\s+(?:build|design|develop|compete|pitch)\b",
        ),
        (
            "Mentored practice",
            r"\b(?:work|train|learn)\s+(?:under|alongside|with)\s+(?:[\w-]+\s+){0,3}(?:mentors?|supervisors?)\b",
        ),
        (
            "Training",
            r"\b(?:hands-on training|on-the-job training|coursework|skills workshops?)\b",
        ),
        (
            "Community service",
            r"\b(?:volunteer duties|community service|service projects?)\b",
        ),
    ),
    "benefits": (
        (
            "Compensation",
            r"\b(?:stipend|salary|hourly wage|paid placement|prize money|cash prize)\b",
        ),
        (
            "Funding",
            r"\b(?:research funding|project funding|tuition waiver|tuition remission|tuition assistance|travel grant|travel reimbursement)\b",
        ),
        (
            "Housing",
            r"\b(?:housing allowance|housing (?:is )?(?:included|provided)|accommodation (?:is )?(?:included|provided))\b",
        ),
        ("Mentorship", r"\b(?:mentor(?:ship|ing)|faculty mentor|industry mentor)\b"),
        (
            "Credential",
            r"\b(?:certificate of completion|professional certification|academic credit)\b",
        ),
    ),
    "requirements": (
        (
            "Education or enrollment",
            r"\b(?:must be (?:currently )?enrolled|eligib(?:le|ility)\s+(?:applicants?|participants?)|minimum gpa|required degree|degree (?:is )?required)\b",
        ),
        (
            "Applicant conditions",
            r"\b(?:eligibility requirements|applicants? must|candidates? must|participants? must|only open to|must be eligible|must hold|must have|must submit|are required to)\b",
        ),
        (
            "Work authorization",
            r"\b(?:visa sponsorship|work authorization|citizenship required|citizens only|permanent residents?)\b",
        ),
        (
            "Application materials",
            r"\b(?:letters? of recommendation|research proposals?|portfolio (?:is )?required|official transcripts?)\b",
        ),
        (
            "Participation terms",
            r"\b(?:application fee|registration fee|team size|minimum age|at least \d+ years? old)\b",
        ),
    ),
}
COMPILED_EVIDENCE = {
    group: tuple(
        (label, re.compile(pattern, re.IGNORECASE)) for label, pattern in patterns
    )
    for group, patterns in EVIDENCE_PATTERNS.items()
}
CLAUSE_END = re.compile(r"[.!?;](?:\s|$)")
CLAUSE_START = re.compile(r"[.!?;]\s+")


def _passage(text: str, start: int, end: int) -> str:
    left_bound = max(0, start - 100)
    prefix = text[left_bound:start]
    prior = list(CLAUSE_START.finditer(prefix))
    left = left_bound + prior[-1].end() if prior else left_bound
    right_bound = min(len(text), end + 145)
    following = CLAUSE_END.search(text, end, right_bound)
    right = following.start() + 1 if following else right_bound
    return "{}{}{}".format(
        "…" if left == left_bound and left else "",
        text[left:right].strip(),
        "…" if not following and right < len(text) else "",
    )


def _evidence(group: str, texts: tuple[str, ...]) -> list:
    entries = []
    passages = set()
    for label, pattern in COMPILED_EVIDENCE[group]:
        for text in texts:
            match = pattern.search(text)
            if not match:
                continue
            evidence = _passage(text, match.start(), match.end())
            if evidence not in passages:
                passages.add(evidence)
                entries.append({"label": label, "evidence": evidence})
            break
    return entries


def listing_availability(
    active: bool,
    deadline: Any,
    collection: Mapping[str, Any],
    schedule: Mapping[str, Any],
    today: date,
) -> str:
    if not active:
        return "archived"
    if collection.get("context") == "directory":
        return "resource"
    event_status = str(schedule.get("event_status") or "").casefold()
    if event_status.endswith(("eventcancelled", "eventcanceled")):
        return "closed"
    for value in (deadline, schedule.get("ends_at")):
        if not value:
            continue
        try:
            if date.fromisoformat(str(value)[:10]) < today:
                return "closed"
        except ValueError:
            continue
    if collection.get("coverage") == "partial" or event_status.endswith(
        "eventpostponed"
    ):
        return "unconfirmed"
    return "listed"


def opportunity_facts(
    row: Mapping[str, Any],
    *,
    compact: bool = False,
    evidence_limit: int = 20000,
    today: Optional[date] = None,
) -> Dict[str, Any]:
    """Keep excerpts and source attribution separate from personal fit."""

    description = str(row.get("description", ""))
    eligibility = str(row.get("eligibility", ""))
    metadata = row.get("metadata", {})
    collection = metadata.get("collection", {}) if isinstance(metadata, dict) else {}
    if not isinstance(collection, dict):
        collection = {}
    listing = metadata.get("listing", {}) if isinstance(metadata, dict) else {}
    listing = listing if isinstance(listing, dict) else {}
    schedule = {
        key: listing[key]
        for key in (
            "starts_at",
            "ends_at",
            "applications_open_at",
            "attendance",
            "event_status",
            "duration",
            "credential",
        )
        if isinstance(listing.get(key), str) and listing[key]
    }
    listing_url = str(row.get("url") or "")
    listing_kind = str(row.get("opportunity_type") or "opportunity")
    result: Dict[str, Any] = {
        "kind": normalize_opportunity_type(listing_kind) or listing_kind,
        "information": "detailed"
        if row.get("description_length", len(description)) >= 300 or eligibility
        else ("summary" if description else "title_only"),
        "source": {
            "id": row.get("source_id", ""),
            "name": row.get("source_name", ""),
            "url": collection.get("publisher_url") or row.get("source_url", ""),
        },
        "application_url": listing_url,
    }
    if collection.get("publisher_url"):
        result["publisher_url"] = collection["publisher_url"]
    if collection.get("coverage"):
        result["coverage"] = collection["coverage"]
    if collection.get("context"):
        result["context"] = collection["context"]
    result["availability"] = listing_availability(
        bool(row.get("active", True) and row.get("source_enabled", True)),
        row.get("deadline_at"),
        collection,
        schedule,
        today or date.today(),
    )
    if schedule:
        result["schedule"] = schedule
    if isinstance(metadata, dict) and metadata.get("detail_status") in {
        "failed",
        "deferred",
        "cached",
    }:
        result["detail_status"] = metadata["detail_status"]
    if collection.get("context") == "directory":
        result["application_url"] = ""
    if compact:
        return result

    description = clean_text(description[:evidence_limit])
    eligibility = clean_text(eligibility)
    result.update(
        {
            group: _evidence(
                group,
                (description, eligibility)
                if group != "requirements"
                else (eligibility, description),
            )
            for group in COMPILED_EVIDENCE
        }
    )
    if eligibility and all(
        eligibility != item["evidence"] for item in result["requirements"]
    ):
        result["requirements"].insert(
            0,
            {
                "label": "Source eligibility statement",
                "evidence": eligibility[:500] + ("…" if len(eligibility) > 500 else ""),
            },
        )
    return result
