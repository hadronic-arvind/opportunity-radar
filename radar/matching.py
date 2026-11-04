"""Conservative, inspectable evidence ranking and structured eligibility."""

import re
import unicodedata
from functools import lru_cache

from .brief import fragments

ALIASES = (
    (
        "scientific computing",
        "scientific software",
        "numerical modeling",
        "numerical models",
        "numerical simulation",
        "numerical simulations",
        "computational science",
        "high performance computing",
        "parallel computing",
    ),
    ("climate modeling", "climate modelling", "climate models", "climate simulations"),
    (
        "accessibility",
        "accessible",
        "accessibles",
        "accessibilite",
        "accesible",
        "accesibles",
        "accesibilidad",
        "inclusive design",
        "assistive technology",
    ),
    (
        "museum education",
        "museum educator",
        "museum interpretation",
        "visitor education",
    ),
    ("data visualization", "data visualisation", "visualizing data", "data dashboards"),
    ("machine learning", "predictive modeling", "predictive models"),
    ("software engineering", "software development", "building software"),
    ("wet lab", "bench research", "laboratory experiments"),
    ("teaching", "instruction", "lesson planning"),
)
RULE_KEYS = ("prefer", "require", "avoid", "explore")
WORD = re.compile(r"[^\W_]+", re.UNICODE)
BOILERPLATE = re.compile(
    r"equal opportunity (?:employer|statement)|reasonable accommodations|privacy (?:policy|notice)"
    r"|all qualified applicants|\bregardless of\b|\beeo statement\b"
    r"|\b(?:ignore|disregard|override)\b.{0,35}\b(?:instructions?|rules?|policy)\b"
    r"|\b(?:rank|recommend|score|classify) this (?:as|first|higher)\b",
    re.I,
)
CONDITIONAL = re.compile(
    r"\b(?:if|unless|may|might|subject to|depends on|contingent)\b", re.I
)
CLAUSE = re.compile(r"[,;:]\s+(?=(?:but |however |although |no |not |without ))", re.I)
NEGATION = re.compile(r"\b(?:not|no|without|never|doesn.t)\b", re.I)


def tokens(value):
    if value.isascii():
        return WORD.findall(value.lower())
    folded = unicodedata.normalize("NFKD", value.casefold())
    return WORD.findall(
        "".join(char for char in folded if not unicodedata.combining(char))
    )


@lru_cache(maxsize=512)
def variants(term):
    phrase = " ".join(tokens(term))
    return next((group for group in ALIASES if phrase in group), (phrase,))


SUFFIXES = (
    "ations",
    "ation",
    "ments",
    "ment",
    "ances",
    "ance",
    "ities",
    "ity",
    "ing",
    "ers",
    "ed",
    "es",
    "s",
    "e",
)
SHORT_FUNCTION_WORDS = frozenset(
    "a an and are as at be but by did do for had has if in is may nor of on or "
    "our per so the to up via was we who yet you".split()
)


def _root(word):
    if not word.isascii() or not word.isalpha():
        return word
    for suffix in SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 5:
            return word[: -len(suffix)]
    return word


@lru_cache(maxsize=512)
def nearby_roots(term):
    """Allow inflections and reordering of multiple meaningful words in one passage."""
    words = tuple(
        dict.fromkeys(
            _root(word) for word in tokens(term) if word not in SHORT_FUNCTION_WORDS
        )
    )
    return (
        words if 2 <= len(words) <= 6 and all(word.isascii() for word in words) else ()
    )


@lru_cache(maxsize=256)
def _cached_fragments(text):
    return tuple(_prepared_fragments(text))


def _prepared_fragments(text):
    for sentence in fragments(text):
        if BOILERPLATE.search(sentence):
            continue
        for part in CLAUSE.split(sentence):
            fragment = part.strip()
            if fragment and not NEGATION.search(fragment):
                words = tokens(fragment)
                yield (
                    fragment,
                    " " + " ".join(words) + " ",
                    tuple(_root(word) for word in words),
                )


def _near(words, roots):
    if not roots or not all(root in words for root in roots):
        return False
    target = set(roots)
    for index, word in enumerate(words):
        if word in target and target.issubset(words[index : index + 8]):
            return True
    return False


def normalize_rules(rules):
    if not isinstance(rules, dict) or rules.keys() - {*RULE_KEYS, "objective", "facts"}:
        raise ValueError(
            "Rules accept objective, prefer, require, avoid, explore and facts"
        )
    objective = rules.get("objective", "")
    if not isinstance(objective, str) or len(objective) > 2000:
        raise ValueError("Objective must be text of at most 2,000 characters")
    result = {"objective": objective.strip()}
    for key in RULE_KEYS:
        values = rules.get(key, [])
        if (
            not isinstance(values, list)
            or len(values) > 30
            or any(
                not isinstance(value, str) or len(value) > 120 or not tokens(value)
                for value in values
            )
        ):
            raise ValueError(f"{key} accepts up to 30 nonempty text phrases")
        result[key] = list(dict.fromkeys(value.strip() for value in values))
    facts = rules.get("facts", {})
    if (
        not isinstance(facts, dict)
        or len(facts) > 50
        or any(
            not isinstance(key, str)
            or not key
            or len(key) > 80
            or type(value) is not bool
            for key, value in facts.items()
        )
    ):
        raise ValueError("facts must map up to 50 self-reported facts to true or false")
    if facts:
        result["facts"] = facts
    return result


def _fragments(row):
    for field, weight in (
        ("title", 5),
        ("description", 3),
        ("location", 2),
        ("kinds", 2),
    ):
        value = row.get(field) or ""
        parts = value if isinstance(value, list) else (value,)
        for part in parts:
            prepared = (
                _cached_fragments(part)
                if len(part) <= 2048
                else _prepared_fragments(part)
            )
            for quote, normalized, roots in prepared:
                yield field, quote, weight, normalized, roots


def _evaluate(condition, facts, depth=0):
    if depth >= 8 or not isinstance(condition, dict) or len(condition) != 1:
        return None
    key, value = next(iter(condition.items()))
    if key == "fact" and isinstance(value, str):
        return facts.get(value)
    if key == "not":
        result = _evaluate(value, facts, depth + 1)
        return None if result is None else not result
    if key in {"all", "any"} and isinstance(value, list) and 0 < len(value) <= 20:
        results = [_evaluate(item, facts, depth + 1) for item in value]
        decisive = key == "any"
        if decisive in results:
            return decisive
        return None if None in results else not decisive
    return None


def eligibility(observations, facts):
    """Evaluate only explicit structured requirements with stored supporting quotes."""
    assessments = []
    for row in observations:
        evidence = row.get("evidence") or {}
        condition = evidence.get("eligibility_condition")
        quote = evidence.get("eligibility_quote")
        if (
            row.get("authority") is True
            and condition
            and isinstance(quote, str)
            and quote.strip()
            and any(
                quote in row.get(field, "") for field in ("description", "requirements")
            )
        ):
            outcome = _evaluate(condition, facts)
            assessments.append({"outcome": outcome, "quote": quote, "url": row["url"]})
    if not assessments:
        return {"state": "unknown", "evidence": []}
    outcomes = {item["outcome"] for item in assessments}
    state = {
        frozenset({True}): "meets recorded condition",
        frozenset({False}): "does not meet recorded condition",
    }.get(frozenset(outcomes), "unknown")
    return {"state": state, "evidence": assessments}


def assess(observations, rules):
    """Rank scope-specific activity evidence separately from eligibility."""
    fragments = [
        (row, field, quote, weight, normalized, roots)
        for row in observations
        for field, quote, weight, normalized, roots in _fragments(row)
    ]
    details = []
    score = 0
    positive = False
    blocked = False
    for key in RULE_KEYS:
        for priority, term in enumerate(rules.get(key, [])):
            forms = tuple(" " + form + " " for form in variants(term))
            matches = [
                fragment
                for fragment in fragments
                if any(form in fragment[4] for form in forms)
                and (key != "require" or not CONDITIONAL.search(fragment[2]))
            ]
            best = max(matches, key=lambda fragment: fragment[3], default=None)
            method = "phrase"
            if best is None and key in {"prefer", "explore"}:
                roots = nearby_roots(term)
                if roots:
                    best = max(
                        (
                            fragment
                            for fragment in fragments
                            if _near(fragment[5], roots)
                        ),
                        key=lambda fragment: fragment[3],
                        default=None,
                    )
                    method = "nearby terms"
            detail = {"rule": key, "term": term, "state": "unknown"}
            if best:
                row, field, quote, weight, _, _ = best
                if method == "nearby terms":
                    weight = max(1, weight - 1)
                detail.update(
                    state="observed",
                    field=field,
                    quote=quote,
                    url=row["url"],
                    method=method,
                    conditional=bool(CONDITIONAL.search(quote)),
                )
                if key in {"prefer", "require", "explore"}:
                    score += weight * (
                        3
                        if key == "prefer" and priority == 0
                        else 2
                        if key == "prefer"
                        else 1
                    )
                    positive = True
                elif key == "avoid":
                    score -= 2 * weight
                    blocked = True
            elif key == "require":
                blocked = True
            details.append(detail)
    assessment = eligibility(observations, rules.get("facts", {}))
    return {
        "score": score,
        "recommended": positive
        and not blocked
        and assessment["state"] != "does not meet recorded condition",
        "rules": details,
        "eligibility": assessment,
    }
