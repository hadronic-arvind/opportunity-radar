"""Shared data models for collectors and storage."""

import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


MAX_OPPORTUNITIES_PER_SOURCE = 5000
MAX_TITLE_CHARS = 500
MAX_ORGANIZATION_CHARS = 300
MAX_LOCATION_CHARS = 500
MAX_URL_CHARS = 2048
MAX_DESCRIPTION_CHARS = 20000
MAX_CATEGORY_CHARS = 2000
MAX_ELIGIBILITY_CHARS = 5000
MAX_METADATA_STRING_CHARS = 1000


def _bounded_metadata(value: Any, depth: int = 0) -> Any:
    if depth >= 4:
        return None
    if isinstance(value, dict):
        return {
            str(key)[:100]: _bounded_metadata(child, depth + 1)
            for key, child in list(value.items())[:50]
        }
    if isinstance(value, list):
        return [_bounded_metadata(child, depth + 1) for child in value[:100]]
    if isinstance(value, str):
        return value[:MAX_METADATA_STRING_CHARS]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:MAX_METADATA_STRING_CHARS]


@dataclass
class Opportunity:
    source_id: str
    external_id: str
    title: str
    organization: str
    url: str
    location: str = ""
    description: str = ""
    category: str = ""
    opportunity_type: str = "opportunity"
    posted_at: Optional[str] = None
    deadline_at: Optional[str] = None
    recommended_resume: str = ""
    commitment: str = ""
    eligibility: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    score: int = 0
    tier: str = "watch"
    reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.source_id = str(self.source_id)
        if not self.source_id or len(self.source_id) > 200:
            raise ValueError("Opportunity source id must contain 1-200 characters")
        self.external_id = str(self.external_id)
        if len(self.external_id) > 500:
            self.external_id = hashlib.sha256(self.external_id.encode("utf-8")).hexdigest()
        self.title = str(self.title or "")[:MAX_TITLE_CHARS]
        self.organization = str(self.organization or "")[:MAX_ORGANIZATION_CHARS]
        self.url = str(self.url or "")[:MAX_URL_CHARS]
        self.location = str(self.location or "")[:MAX_LOCATION_CHARS]
        self.description = str(self.description or "")[:MAX_DESCRIPTION_CHARS]
        self.category = str(self.category or "")[:MAX_CATEGORY_CHARS]
        self.opportunity_type = str(self.opportunity_type or "opportunity")[:80]
        self.posted_at = str(self.posted_at)[:100] if self.posted_at else None
        self.deadline_at = str(self.deadline_at)[:100] if self.deadline_at else None
        self.recommended_resume = str(self.recommended_resume or "")[:300]
        self.commitment = str(self.commitment or "")[:300]
        self.eligibility = str(self.eligibility or "")[:MAX_ELIGIBILITY_CHARS]
        bounded = _bounded_metadata(self.metadata)
        self.metadata = bounded if isinstance(bounded, dict) else {}


@dataclass
class FetchResult:
    opportunities: List[Opportunity]
    content_hash: str
    status: str = "ok"
    message: str = ""

    def __post_init__(self) -> None:
        if len(self.opportunities) > MAX_OPPORTUNITIES_PER_SOURCE:
            raise ValueError(
                "Source produced more than {} opportunities".format(
                    MAX_OPPORTUNITIES_PER_SOURCE
                )
            )
        self.content_hash = str(self.content_hash)[:128]
        self.status = str(self.status)[:40]
        self.message = str(self.message)[:1000]
