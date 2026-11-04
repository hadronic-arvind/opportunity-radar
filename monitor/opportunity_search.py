"""Local search across role, listing type, organization, and source evidence."""

import re
import unicodedata
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


COMBINING_MARKS = re.compile(r"[̀-ͯ᪰-᫿᷀-᷿⃐-⃿︠-︯]")


def normalize_search_text(value: str) -> str:
    if value.isascii():
        return value.lower()
    folded = unicodedata.normalize("NFKD", value.casefold())
    return COMBINING_MARKS.sub("", folded)


def search_ranker(query: str) -> Callable[..., int]:
    phrase = " ".join(normalize_search_text(query).split())
    terms = tuple(dict.fromkeys(phrase.split()))
    weights = (14, 10, 9, 8, 6, 4, 2, 1)

    def rank(*fields: Any) -> int:
        texts = []
        strengths = dict.fromkeys(terms, 0)
        missing = len(terms)
        for field, weight in zip(fields, weights):
            if not field:
                continue
            text = normalize_search_text(str(field))
            texts.append((text, weight))
            for term, strength in strengths.items():
                if not strength and term in text:
                    strengths[term] = weight
                    missing -= 1
            if not missing:
                break
        if missing:
            return -1
        phrase_bonus = 0
        if len(terms) > 1:
            phrase_bonus = next((weight for text, weight in texts if phrase in text), 0)
        return sum(strengths.values()) + phrase_bonus

    return rank


def listing_key(url: str, title: str, organization: str) -> str:
    """Group equivalent records for browsing while keeping original IDs intact."""
    if not title or not organization:
        return ""
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"https", "http"}
            or not parsed.hostname
            or parsed.username
        ):
            return ""
        query = sorted(
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if not key.casefold().startswith("utm_")
        )
        clean_url = urlunsplit(
            (
                parsed.scheme,
                parsed.netloc.lower(),
                parsed.path or "/",
                urlencode(query),
                parsed.fragment,
            )
        )
    except ValueError:
        return ""
    return "\n".join(
        (clean_url, normalize_search_text(title), normalize_search_text(organization))
    )
