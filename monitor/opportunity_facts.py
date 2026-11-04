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
            r"\b(?:stipend|salary|hourly wage|paid placement)\b",
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
        ("Prize", r"\b(?:prize money|cash prizes?|cash awards?)\b"),
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


ACTIVITY_FACETS = {
    "work": "Employment",
    "placement": "Work placement",
    "research": "Research",
    "learn": "Courses and training",
    "build_compete": "Build or compete",
    "attend_present": "Attend or present",
    "service": "Community service",
}
BENEFIT_FACETS = {
    "compensation": "Compensation",
    "funding": "Funding",
    "prize": "Prize",
    "housing": "Housing",
    "mentorship": "Mentorship",
    "credential": "Credential or credit",
    "access": "Resource access",
    "free_participation": "Free participation",
}
FACET_LABELS = {"activities": ACTIVITY_FACETS, "benefits": BENEFIT_FACETS}

KIND_ACTIVITIES = {
    "job": ("work",),
    "internship": ("placement",),
    "apprenticeship": ("placement", "learn"),
    "residency": ("placement",),
    "co_op": ("placement",),
    "postdoc": ("research",),
    "research_program": ("research",),
    "course": ("learn",),
    "training": ("learn",),
    "hackathon": ("build_compete",),
    "competition": ("build_compete",),
    "conference": ("attend_present",),
    "event": ("attend_present",),
    "volunteer": ("service",),
}
FACET_ACTIVITY_PATTERNS = {
    "work": r"\b(?:full[- ]time employment|part[- ]time employment|salaried (?:position|role)|employment contract)\b",
    "placement": r"\b(?:work placement|clinical placement|practicum|industry placement|job shadowing)\b",
    "research": r"\b(?:conduct|carry out|perform|undertake|lead|pursue|propose)\s+(?:[\w-]+\s+){0,5}research\b",
    "learn": r"\b(?:hands-on training|on-the-job training|coursework|skills workshops?|structured training)\b",
    "build_compete": r"\b(?:participants|teams|you)\s+(?:will|can)\s+(?:build|design|develop|compete|pitch)\b",
    "attend_present": r"\b(?:present (?:their|your|original) (?:research|findings|work)|attend (?:the )?(?:conference|symposium))\b",
    "service": r"\b(?:volunteer duties|community service|service projects?)\b",
}
FACET_BENEFIT_PATTERNS = {
    "compensation": r"\b(?:stipend|salary range|hourly wage|paid (?:placement|internship|fellowship|residency|apprenticeship))\b",
    "prize": r"\b(?:cash prizes?|prize money|cash awards?)\b",
    "funding": r"\b(?:research funding|project funding|tuition waiver|tuition remission|tuition assistance|travel grant|travel reimbursement|funding (?:is )?available)\b",
    "housing": r"\b(?:housing allowance|housing (?:is )?(?:included|provided)|accommodation (?:is )?(?:included|provided))\b",
    "mentorship": r"\b(?:receive mentorship|mentorship (?:is )?(?:provided|included)|work (?:with|alongside) (?:a |an )?(?:faculty|industry|research) mentor|one[- ]on[- ]one mentorship|mentorship from)\b",
    "credential": r"\b(?:certificate of completion|earn (?:a |an )?(?:certificate|credential|degree)|academic credit)\b",
    "access": r"\b(?:access to (?:the |our )?(?:research facilities|laboratories|archives|special collections|computing resources|equipment)|compute credits)\b",
    "free_participation": r"\b(?:free (?:to attend|to participate|registration|admission|of charge)|no (?:application|registration|participation|tuition) fees?)\b",
}
COMPILED_FACETS = {
    group: {
        key: re.compile(pattern, re.IGNORECASE) for key, pattern in patterns.items()
    }
    for group, patterns in (
        ("activities", FACET_ACTIVITY_PATTERNS),
        ("benefits", FACET_BENEFIT_PATTERNS),
    )
}
DENIED_EVIDENCE = re.compile(
    r"\b(?:unpaid|not (?:paid|available|provided|included|offered|covered)|"
    r"no (?:\w+\s+){0,2}(?:stipend|salary|housing|funding|reimbursement|mentorship|credit|prize)s?)\b",
    re.IGNORECASE,
)
FACET_EVIDENCE_LIMIT = 10000


def _facet_passage(pattern: re.Pattern, text: str) -> str:
    for match in pattern.finditer(text):
        evidence = _passage(text, match.start(), match.end())
        if not DENIED_EVIDENCE.search(evidence):
            return evidence
    return ""


def _listed_amount(amount: Any) -> str:
    if not isinstance(amount, dict):
        return ""
    amounts = []
    for key in ("value", "minimum", "maximum"):
        number = amount.get(key)
        if type(number) in (int, float) and 0 < number < 1e15:
            qualifier = {"value": "", "minimum": "minimum ", "maximum": "maximum "}[key]
            amounts.append(qualifier + str(number))
    if not amounts:
        return ""
    currency = str(amount.get("currency") or "").strip()[:12]
    unit = str(amount.get("unit") or "").strip()[:20]
    return " ".join(part for part in (", ".join(amounts), currency, unit) if part)


def participation_facets(row: Mapping[str, Any]) -> Dict[str, list]:
    """Index overlapping source-backed activities and benefits independently."""

    metadata = row.get("metadata", {})
    metadata = metadata if isinstance(metadata, dict) else {}
    collection = metadata.get("collection", {})
    if isinstance(collection, dict) and collection.get("context") == "directory":
        return {"activities": [], "benefits": []}
    listing = metadata.get("listing", {})
    listing = listing if isinstance(listing, dict) else {}
    kind = normalize_opportunity_type(row.get("opportunity_type"))
    title = clean_text(str(row.get("title") or ""))[:250]
    description = clean_text(str(row.get("description") or "")[:FACET_EVIDENCE_LIMIT])
    evidence = {
        group: {
            key: passage
            for key, pattern in patterns.items()
            if (
                passage := _facet_passage(pattern, description)
                or _facet_passage(pattern, title)
            )
        }
        for group, patterns in COMPILED_FACETS.items()
    }
    activities = evidence["activities"]
    for activity in KIND_ACTIVITIES.get(kind, ()):
        if title:
            activities.setdefault(activity, title)

    benefits = evidence["benefits"]
    if kind in {"grant", "scholarship"} and title:
        benefits.setdefault("funding", title)
    salary = _listed_amount(listing.get("base_salary"))
    if salary:
        benefits["compensation"] = salary
    funding = _listed_amount(listing.get("grant_amount"))
    if funding:
        benefits["funding"] = funding
    if isinstance(listing.get("credential"), str) and listing["credential"].strip():
        benefits["credential"] = listing["credential"][:250]
    if listing.get("is_free") is True:
        benefits["free_participation"] = "The publisher lists participation as free."

    return {
        group: [
            {"id": key, "label": labels[key], "evidence": evidence[group][key]}
            for key in labels
            if key in evidence[group]
        ]
        for group, labels in FACET_LABELS.items()
    }
