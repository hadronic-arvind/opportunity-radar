"""Label source passages for comparison without inferring guarantees."""

import re

PATTERNS = {
    "Activities": r"\b(?:you will|conduct|design|build|develop|prepare|teach|perform|analy[sz]e|train alongside|run experiments)\b",
    "Outcomes": r"\b(?:publish|certificate|academic credit|credential|portfolio|completion)\b",
    "Compensation": r"\b(?:stipend|salary|hourly wage|paid placement|unpaid|compensation)\b",
    "Funding and support": r"\b(?:funding|tuition|waiver|travel reimbursement|mentorship|housing|grant)\b",
    "Prizes": r"\b(?:prizes?|cash awards?|finalists?)\b",
    "Obligations": r"\b(?:commitment|hours per week|full.time|part.time|relocat|attendance|in person)\w*\b",
    "Qualifications": r"\b(?:qualified|eligib\w*|require\w*|must|preferred|enrolled|degree|citizen\w*|experience)\b",
    "Application": r"\b(?:apply by|applications? close|deadline|portfolio required|submit|references?|transcripts?)\b",
}
COMPILED = {group: re.compile(pattern, re.I) for group, pattern in PATTERNS.items()}
CONDITIONAL = re.compile(
    r"\b(?:if|unless|may|might|subject to|depends on|contingent|finalists?|winner)\b",
    re.I,
)
NEGATIVE = re.compile(r"\b(?:no|not|without|never|unpaid|ineligible)\b", re.I)
SENTENCE = re.compile(r".+?(?:(?<=[.!?])(?=\s|$)|$)")


def fragments(text):
    for line in text.splitlines():
        for match in SENTENCE.finditer(line):
            quote = match.group().strip()
            if quote:
                yield quote


def evidence_brief(rows):
    groups = {group: [] for group in PATTERNS}
    observed = set()
    for row in rows:
        for field in ("requirements", "description"):
            for quote in fragments(row[field]):
                for group, pattern in COMPILED.items():
                    if len(groups[group]) >= 6 or not pattern.search(quote):
                        continue
                    key = group, quote, row["source_id"]
                    if key in observed:
                        continue
                    observed.add(key)
                    # Keep the whole statement so a qualification or exception stays visible.
                    groups[group].append(
                        {
                            "quote": quote,
                            "url": row["url"],
                            "source_id": row["source_id"],
                            "field": field,
                            "conditional": bool(CONDITIONAL.search(quote)),
                            "negative_language": bool(NEGATIVE.search(quote)),
                        }
                    )
    return groups
