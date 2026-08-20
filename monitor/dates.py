"""Conservative, provenance-aware listing date normalization."""

import re
from datetime import date, datetime, timezone
from typing import Any, Dict, Optional, Tuple


MONTHS = {
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
MONTH_PATTERN = (
    r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|"
    r"Nov(?:ember)?|Dec(?:ember)?"
)
DATE_PATTERN = (
    r"(?:"
    r"(?P<iso_year>20\d{2})-(?P<iso_month>0?[1-9]|1[0-2])-(?P<iso_day>0?[1-9]|[12]\d|3[01])"
    r"|(?P<month_name>" + MONTH_PATTERN + r")\.?\s+"
    r"(?P<month_day>\d{1,2})(?:st|nd|rd|th)?(?:,)?"
    r"(?:\s+(?P<month_year>20\d{2}))?"
    r"|(?P<day_first>\d{1,2})(?:st|nd|rd|th)?\s+"
    r"(?P<day_month>" + MONTH_PATTERN + r")\.?(?:,)?"
    r"(?:\s+(?P<day_year>20\d{2}))?"
    r")"
)
DEADLINE_PATTERN = re.compile(
    r"(?P<label>"
    r"applications?\s+(?:must\s+be\s+)?(?:submitted|received)\s+(?:by|before)"
    r"|applications?\s+(?:are\s+)?due(?:\s+on)?"
    r"|applications?\s+(?:will\s+)?(?:close|end)(?:s|d)?(?:\s+on)?"
    r"|applications?\s+(?:are\s+)?(?:accepted|open)\s+(?:through|until)"
    r"|application\s+(?:deadline|closing\s+date)(?:\s+is)?"
    r"|application\s+window\s+(?:close|end)(?:s|d)?(?:\s+on)?"
    r"|deadline\s+for\s+applications?(?:\s+is)?"
    r"|submissions?\s+(?:are\s+)?due(?:\s+on)?"
    r"|submit\s+(?:your\s+)?application\s+(?:by|before)"
    r"|last\s+(?:day|date)\s+to\s+apply"
    r"|closing\s+date(?:\s+is)?"
    r"|deadline(?:\s+is)?"
    r"|apply\s+(?:by|before)"
    r")\s*[:\-]?\s*"
    r"(?:at\s+\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)"
    r"(?:\s+(?:[A-Z]{2,5}|[A-Za-z]+\s+Time))?\s+(?:on\s+)?)?"
    r"(?:on\s+)?"
    r"(?:(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+)?"
    r"(?P<date>" + DATE_PATTERN + r")",
    re.IGNORECASE,
)
NUMERIC_DEADLINE_PATTERN = re.compile(
    r"(?P<label>application\s+deadline|closing\s+date|deadline|apply\s+(?:by|before))"
    r"\s*[:\-]?\s*(?:on\s+)?"
    r"(?P<first>\d{1,2})[/-](?P<second>\d{1,2})[/-](?P<year>20\d{2})",
    re.IGNORECASE,
)
ROLLING_PATTERN = re.compile(
    r"\b(?:applications?\s+(?:are\s+)?(?:accepted|reviewed|considered|processed)"
    r"\s+on\s+a\s+rolling\s+basis|"
    r"rolling\s+(?:application|admission)s?)\b",
    re.IGNORECASE,
)
OPEN_UNTIL_FILLED_PATTERN = re.compile(
    r"\b(?:open\s+until\s+filled|applications?\s+accepted\s+until\s+filled)\b",
    re.IGNORECASE,
)
NO_DEADLINE_PATTERN = re.compile(
    r"\bno\s+(?:fixed\s+)?deadline\b",
    re.IGNORECASE,
)


def _valid_date(year: Any, month: Any, day: Any) -> Optional[str]:
    try:
        return date(int(year), int(month), int(day)).isoformat()
    except (TypeError, ValueError, OverflowError):
        return None


def normalize_date(value: Any) -> Optional[str]:
    """Return an ISO calendar date without guessing locale-specific formats."""
    if value in (None, ""):
        return None
    raw = str(value).strip()
    match = re.match(r"^(20\d{2})-(\d{2})-(\d{2})(?:[T\s].*)?$", raw)
    if match:
        return _valid_date(*match.groups())
    return None


def normalize_timestamp(value: Any) -> Optional[str]:
    """Normalize a source timestamp while retaining date-only values."""
    if value in (None, ""):
        return None
    raw = str(value).strip()
    calendar_date = normalize_date(raw)
    if len(raw) == 10 and calendar_date:
        return calendar_date
    candidate = raw[:-1] + "+00:00" if raw.endswith(("Z", "z")) else raw
    # Python 3.9's datetime.fromisoformat does not accept the compact +HHMM
    # UTC offset emitted by Jibe and some other ATS feeds. Normalize only the
    # terminal offset, leaving the timestamp itself and explicit timezone
    # semantics unchanged.
    candidate = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", candidate)
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc)
        return parsed.replace(microsecond=0).isoformat()
    return parsed.replace(microsecond=0).isoformat()


def _word_date(match: re.Match, default_year: Optional[str]) -> Optional[str]:
    groups = match.groupdict()
    if groups.get("iso_year"):
        return _valid_date(groups["iso_year"], groups["iso_month"], groups["iso_day"])
    if groups.get("month_name"):
        month = MONTHS.get(groups["month_name"].rstrip(".").casefold())
        year = groups.get("month_year") or default_year
        return _valid_date(year, month, groups.get("month_day")) if year else None
    month = MONTHS.get(str(groups.get("day_month") or "").rstrip(".").casefold())
    year = groups.get("day_year") or default_year
    return _valid_date(year, month, groups.get("day_first")) if year else None


def deadline_evidence(
    value: Optional[str],
    state: str,
    provenance: str,
    confidence: str = "high",
    evidence: str = "",
) -> Dict[str, Any]:
    output: Dict[str, Any] = {
        "state": state,
        "provenance": provenance,
        "confidence": confidence,
    }
    if value:
        output["value"] = value
    if evidence:
        output["evidence"] = " ".join(evidence.split())[:160]
    return output


def structured_deadline(value: Any, provenance: str) -> Tuple[Optional[str], Dict[str, Any]]:
    """Normalize an authoritative source deadline without heuristic parsing."""
    raw = str(value or "").strip()
    normalized = normalize_date(raw)
    if normalized is None:
        written = re.fullmatch(DATE_PATTERN, raw, flags=re.IGNORECASE)
        normalized = _word_date(written, None) if written else None
    if normalized:
        return normalized, deadline_evidence(
            normalized,
            "date",
            provenance,
            evidence=raw,
        )
    return None, deadline_evidence(None, "not_listed", provenance, "low")


def extract_deadline(
    text: Any,
    default_year: Optional[str] = None,
    date_order: str = "",
    allow_generic_deadline: bool = True,
) -> Tuple[Optional[str], Dict[str, Any]]:
    """Extract only dates tied to an explicit application-deadline phrase."""
    content = " ".join(str(text or "").split())[:20000]
    for match in DEADLINE_PATTERN.finditer(content):
        if (
            not allow_generic_deadline
            and re.fullmatch(
                r"deadline(?:\s+is)?",
                match.group("label"),
                re.IGNORECASE,
            )
        ):
            continue
        normalized = _word_date(match, default_year)
        if normalized:
            return normalized, deadline_evidence(
                normalized,
                "date",
                "text.explicit_deadline",
                "high" if re.search(r"20\d{2}", match.group("date")) else "medium",
                match.group(0),
            )
        return None, deadline_evidence(
            None,
            "not_listed",
            "text.explicit_deadline",
            "low",
            match.group(0),
        )

    numeric = next(
        (
            candidate
            for candidate in NUMERIC_DEADLINE_PATTERN.finditer(content)
            if allow_generic_deadline
            or candidate.group("label").casefold() != "deadline"
        ),
        None,
    )
    if numeric and date_order in {"mdy", "dmy"}:
        first = numeric.group("first")
        second = numeric.group("second")
        month, day = (first, second) if date_order == "mdy" else (second, first)
        normalized = _valid_date(numeric.group("year"), month, day)
        if normalized:
            return normalized, deadline_evidence(
                normalized,
                "date",
                "text.explicit_deadline",
                "medium",
                numeric.group(0),
            )

    rolling = ROLLING_PATTERN.search(content)
    if rolling:
        return None, deadline_evidence(
            None,
            "rolling",
            "text.rolling",
            "high",
            rolling.group(0),
        )
    open_until_filled = OPEN_UNTIL_FILLED_PATTERN.search(content)
    if open_until_filled:
        return None, deadline_evidence(
            None,
            "open_until_filled",
            "text.open_until_filled",
            "high",
            open_until_filled.group(0),
        )
    no_deadline = NO_DEADLINE_PATTERN.search(content)
    if no_deadline:
        return None, deadline_evidence(
            None,
            "not_listed",
            "text.no_deadline",
            "high",
            no_deadline.group(0),
        )
    return None, deadline_evidence(None, "not_listed", "none", "low")


def posting_evidence(
    value: Any,
    provenance: str,
    confidence: str = "high",
    kind: str = "posted",
) -> Tuple[Optional[str], Dict[str, Any]]:
    normalized = normalize_timestamp(value)
    if not normalized:
        return None, {
            "state": "unknown",
            "kind": kind,
            "provenance": provenance,
            "confidence": "low",
        }
    return normalized, {
        "state": "present",
        "kind": kind,
        "value": normalized,
        "provenance": provenance,
        "confidence": confidence,
    }
