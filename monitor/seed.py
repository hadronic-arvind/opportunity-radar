"""Import an optional curated Markdown pipeline as durable baseline data."""

import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .dates import extract_deadline
from .models import Opportunity
from .scoring import CURATED_DOCUMENT_PROVENANCE, PROFILE_DOCUMENT_PROVENANCE
from .text import canonical_url, clean_text, infer_opportunity_type, stable_hash


LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")
BACKTICK_RE = re.compile(r"`([^`]+)`")
ORGANIZATION_COLUMNS = {
    "company",
    "employer",
    "host",
    "host organization",
    "institution",
    "organization",
}
TYPE_COLUMNS = {"opportunity type", "program type", "type"}
TITLE_COLUMNS = {"job", "opportunity", "position", "posting", "program", "role"}


def _cells(line: str) -> List[str]:
    return [clean_text(cell.replace("`", "")) for cell in line.strip("|").split("|")]


def _column_value(headers: List[str], cells: List[str], names: set[str]) -> str:
    for index, header in enumerate(headers):
        if header.casefold().rstrip(":") in names and index < len(cells):
            return cells[index]
    return ""


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
    if default_resume:
        allowed_codes.add(default_resume)
    heading = ""
    headers: List[str] = []
    items: List[Opportunity] = []
    for line in lines:
        if line.startswith("## ") or line.startswith("### "):
            heading = line.lstrip("# ").strip()
            headers = []
            if "open postings that look attractive" in heading.lower():
                break
            continue
        if not line.startswith("|") or "---" in line:
            continue
        cells = _cells(line)
        raw_match = LINK_RE.search(line)
        if not raw_match:
            if any(cell.casefold().rstrip(":") in TITLE_COLUMNS for cell in cells):
                headers = cells
            continue
        title, url = raw_match.groups()
        if title.lower() in {"opportunity", "program", "posting", "published rates"}:
            continue
        resume_matches = [token for token in BACKTICK_RE.findall(line) if token in allowed_codes]
        recommended_resume = resume_matches[0] if resume_matches else default_resume
        document_provenance = (
            CURATED_DOCUMENT_PROVENANCE
            if resume_matches
            else PROFILE_DOCUMENT_PROVENANCE
        )
        description = " ".join(cell for cell in cells if title not in cell and url not in cell)
        deadline, deadline_metadata = extract_deadline(
            description,
            default_year=default_year,
        )
        commitment = _extract_commitment(description)
        organization = _column_value(headers, cells, ORGANIZATION_COLUMNS)
        declared_type = _column_value(headers, cells, TYPE_COLUMNS)
        opportunity_type = infer_opportunity_type(
            title,
            (heading,),
            explicit=declared_type,
            default="job",
        )
        items.append(
            Opportunity(
                source_id="curated_pipeline",
                external_id=stable_hash(canonical_url(url)),
                title=clean_text(title),
                organization=organization or _organization(title),
                url=canonical_url(url),
                description=description,
                category=heading,
                opportunity_type=opportunity_type,
                deadline_at=deadline,
                recommended_resume=recommended_resume,
                commitment=commitment,
                metadata={
                    "curated": True,
                    "dates": {"deadline": deadline_metadata},
                    "document_routing": {"provenance": document_provenance},
                },
            )
        )
    deduplicated: Dict[str, Opportunity] = {}
    for item in items:
        deduplicated[item.url] = item
    return list(deduplicated.values())


def _extract_deadline(text: str, default_year: Optional[str] = None) -> Optional[str]:
    """Backward-compatible value-only wrapper for the shared date parser."""
    return extract_deadline(text, default_year=default_year)[0]


def _extract_commitment(text: str) -> str:
    match = re.search(r"\b(Very high|High|Medium|Standard|Low)\s*:", text, re.IGNORECASE)
    return match.group(1).title() if match else ""
