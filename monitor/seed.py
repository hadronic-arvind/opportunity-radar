"""Import an optional curated Markdown pipeline as durable baseline data."""

import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .models import Opportunity
from .text import canonical_url, clean_text, infer_opportunity_type, stable_hash


LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")
BACKTICK_RE = re.compile(r"`([^`]+)`")


def _organization(title: str) -> str:
    """Infer a display label without a private employer dictionary."""
    cleaned = clean_text(title)
    for separator in (" at ", " - ", " | ", ": "):
        if separator in cleaned:
            left, right = cleaned.split(separator, 1)
            if separator == " at " and right.strip():
                return right.strip()
            if left.strip():
                return left.strip()
    without_program = re.sub(
        r"\s+(?:summer\s+)?(?:internship|fellowship|scholarship|apprenticeship|"
        r"postdoctoral\s+program|postdoc|research\s+program|program|careers?|jobs?)\b.*$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip()
    generic_labels = {
        "data",
        "engineering",
        "graduate",
        "machine learning",
        "research",
        "software",
        "summer",
    }
    if without_program.casefold() in generic_labels:
        return "Organization not listed"
    return without_program or cleaned or "Organization not listed"


def parse_pipeline(
    path: Path,
    resume_codes: Iterable[str] = (),
    default_resume: str = "",
    default_year: Optional[str] = None,
) -> List[Opportunity]:
    lines = path.read_text(encoding="utf-8").splitlines()
    allowed_codes = {code for code in resume_codes if code}
    heading = ""
    items: List[Opportunity] = []
    for line in lines:
        if line.startswith("## ") or line.startswith("### "):
            heading = line.lstrip("# ").strip()
            if "open postings that look attractive" in heading.lower():
                break
            continue
        if not line.startswith("|") or "---" in line:
            continue
        cells = [clean_text(cell.replace("`", "")) for cell in line.strip("|").split("|")]
        raw_match = LINK_RE.search(line)
        if not raw_match:
            continue
        title, url = raw_match.groups()
        if title.lower() in {"opportunity", "program", "posting", "published rates"}:
            continue
        resume_matches = [token for token in BACKTICK_RE.findall(line) if token in allowed_codes]
        recommended_resume = resume_matches[0] if resume_matches else default_resume
        description = " ".join(cell for cell in cells if title not in cell and url not in cell)
        deadline = _extract_deadline(description, default_year=default_year)
        commitment = _extract_commitment(description)
        opportunity_type = infer_opportunity_type(title, default="job")
        items.append(
            Opportunity(
                source_id="curated_pipeline",
                external_id=stable_hash(canonical_url(url)),
                title=clean_text(title),
                organization=_organization(title),
                url=canonical_url(url),
                description=description,
                category=heading,
                opportunity_type=opportunity_type,
                deadline_at=deadline,
                recommended_resume=recommended_resume,
                commitment=commitment,
                metadata={"curated": True},
            )
        )
    deduplicated: Dict[str, Opportunity] = {}
    for item in items:
        deduplicated[item.url] = item
    return list(deduplicated.values())


def _extract_deadline(text: str, default_year: Optional[str] = None) -> Optional[str]:
    match = re.search(
        r"(?:deadline(?: is)?|close(?:s)?|by)\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})(?:,\s*(20\d{2}))?",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    month_name, day, year = match.groups()
    months = [
        "january", "february", "march", "april", "may", "june",
        "july", "august", "september", "october", "november", "december",
    ]
    month = months.index(month_name.lower()) + 1
    resolved_year = year or default_year
    if not resolved_year:
        return None
    return "{}-{:02d}-{:02d}".format(resolved_year, month, int(day))


def _extract_commitment(text: str) -> str:
    match = re.search(r"\b(Very high|High|Medium|Standard|Low)\s*:", text, re.IGNORECASE)
    return match.group(1).title() if match else ""
