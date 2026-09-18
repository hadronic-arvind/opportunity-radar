"""Conservative, clause-scoped eligibility gates with inspectable evidence."""

import re
from functools import lru_cache

from .taxonomy import load_taxonomy_catalog


DEGREES = {
    "bachelors": r"\b(?:bachelor(?:'s|s)?|undergraduates?|b[ .]*[as](?:c)?\b)\b",
    "masters": r"\b(?:master(?:'s|s)?|m[ .]*[as](?:c)?\b|mba\b)\b",
    "doctorate": r"\b(?:phd|doctoral|doctorate|doctor of philosophy)\b",
}
STAGES = {
    "undergraduate": "bachelors", "undergraduate_student": "bachelors",
    "bachelors_student": "bachelors", "masters_student": "masters",
    "phd": "doctorate", "phd_student": "doctorate", "doctoral_student": "doctorate",
}
RANK = {"bachelors": 1, "masters": 2, "doctorate": 3}


def _text(value):
    text = str(value or "").replace("’", "'").replace("‘", "'")
    # Remove abbreviation dots, but retain a sentence boundary before a capital
    # letter or at the end of the field. Never consume a plain "PhD." terminator.
    abbreviations = ((r"\bu\.\s*s\.(?:\s*a\.)?", "us"),
                     (r"\bp\s*h\.\s*d\.?", "phd"),
                     (r"\b([bm])\.\s*([as])\.(?:c\.)?", None))
    for pattern, replacement in abbreviations:
        def normalize(match):
            token = replacement or (match.group(1) + match.group(2)).lower()
            following = text[match.end():].lstrip()
            boundary = match.group().endswith(".") and (not following or following[0].isupper())
            return token + ("." if boundary else "")
        text = re.sub(pattern, normalize, text, flags=re.IGNORECASE)
    return text.casefold()


def _clauses(value):
    return [part.strip() for part in re.split(r"[.!?;\n]+", _text(value)) if part.strip()]


def _degree_levels(clause):
    levels = {name for name, pattern in DEGREES.items() if re.search(pattern, clause)}
    if re.search(r"(?<!under)\bgraduate students?\b", clause):
        levels.update(("masters", "doctorate"))
    return levels


def degree_gates(item, stage, completed):
    """Enrollment is an exact level; a minimum completed degree is not a ceiling."""
    level = STAGES.get(stage)
    highest = max([RANK.get(value, 0) for value in completed] + [RANK.get(level, 0)])
    gates = []
    clauses = _clauses("\n".join((item.title, item.eligibility, item.description)))
    for index, clause in enumerate(clauses):
        levels = _degree_levels(clause)
        if not levels:
            continue
        exclusion = re.search(r"(?:not eligible|ineligible|not accepted|need not apply|excluded)", clause)
        if exclusion:
            if level in levels or (not level and any(value in levels for value in completed)):
                gates.append({"id": "degree_stage", "state": "fail", "evidence": [clause[:240]]})
            continue
        if re.search(r"\b(?:preferred|preference|not required|no requirement)\b", clause):
            continue
        enrollment = re.search(r"\b(?:pursuing|enrolled|working toward|students? only|students? must)\b", clause)
        enrollment = enrollment or (re.search(r"\bmust be\b", clause) and re.search(r"\bstudents?\b", clause))
        restricted = re.search(r"\b(?:only|exclusively|restricted to|limited to|reserved for)\b", clause)
        title_audience = clause == _text(item.title).strip() and re.search(r"\b(?:intern(?:ship)?|fellowship|students?)\b", clause)
        if not (enrollment or restricted or title_audience):
            continue
        if re.search(r"\b(?:or higher|or above|at least|minimum)\b", clause):
            continue
        # Explicit adjacent alternatives may be a separate sentence or list item.
        # A mention of colleagues holding a doctorate is not an alternative.
        if index + 1 < len(clauses):
            following = clauses[index + 1]
            if re.search(r"\b(?:may also apply|also eligible|also welcome)\b", following):
                levels.update(_degree_levels(following))
        # A lower degree already earned does not make a PhD an undergraduate student.
        mismatch = (level not in levels if level else highest > max(RANK[v] for v in levels))
        if mismatch:
            gates.append({"id": "degree_stage", "state": "fail" if highest else "unknown", "evidence": [clause[:240]]})
    return gates


@lru_cache(maxsize=1)
def country_aliases():
    aliases = {}
    for entry in load_taxonomy_catalog()["countries"]:
        code, name, iso3 = entry[:3]
        for value in (code, name, iso3, *(entry[3] if len(entry) > 3 and isinstance(entry[3], list) else [])):
            aliases[_text(value)] = code
    aliases.update({"american": "US", "british": "GB", "canadian": "CA", "indian": "IN",
                    "australian": "AU", "united states of america": "US", "uk": "GB"})
    return aliases


def country_code(value):
    return country_aliases().get(_text(value).strip().rstrip("."))


@lru_cache(maxsize=1)
def _country_pattern():
    # ISO codes such as IN, BE, AS, OR and ARE are ordinary English words.
    aliases = [alias for alias in country_aliases()
               if len(alias) > 3 or alias in {"us", "usa", "uk", "uae"}]
    return re.compile(r"(?<!\w)(?:" + "|".join(re.escape(alias) for alias in sorted(aliases, key=len, reverse=True)) + r")(?!\w)")


def _countries(clause):
    return {country_aliases()[match.group()] for match in _country_pattern().finditer(clause)}


def nationality_gates(item, candidate):
    if not candidate.get("filter_nationality", False):
        return []
    citizens = {country_code(value) for value in candidate.get("citizenships", [])} - {None}
    residents = {country_code(value) for value in candidate.get("permanent_residencies", [])} - {None}
    known = bool(citizens)
    gates = []
    identity = _text(item.title + " " + item.organization)
    # Verified NSF rule, including sparse watch-page listings. Do not match mentions
    # of GRFP buried in an unrelated role's description.
    grfp = bool(re.search(r"\bgrfp\b|graduate research fellowship program", identity)
                and re.search(r"\bnsf\b|national science foundation", identity))
    clauses = _clauses(item.eligibility + "\n" + item.description)
    if grfp:
        clauses.append("applicants must be us citizens, nationals, or permanent residents")
    for index, clause in enumerate(clauses):
        if not re.search(r"\b(?:citizens?|citizenship|nationals?|permanent residents?)\b", clause):
            continue
        if re.search(r"\b(?:no citizenship|citizenship is not required|regardless of|any nationality|all nationalities|not required|preferred)\b", clause):
            continue
        mandatory = re.search(r"\b(?:must|only|restricted|limited|reserved|required|requirement|eligible|eligibility)\b", clause)
        countries = _countries(clause)
        if index + 1 < len(clauses) and len(countries) == 1:
            following = clauses[index + 1]
            if (re.fullmatch(r"(?:[a-z .]+ )?permanent residents are also eligible", following)
                    and (not _countries(following) or _countries(following) == countries)):
                clause += "; " + following
        if not mandatory or not countries:
            continue
        # More complex multi-country clauses need review rather than guessing which
        # status belongs to which country.
        negated_status = re.search(
            r"\b(?:not[ -]+|non[- ])(?:[a-z]+[ -]+){0,4}(?:citizens?|nationals?|permanent residents?)\b",
            clause,
        )
        if (len(countries) != 1 or negated_status
                or re.search(r"\b(?:not eligible|ineligible|except|unless)\b", clause)):
            gates.append({"id": "nationality", "state": "unknown", "evidence": [clause[:240]]})
            continue
        if re.search(r"\b(?:international|foreign) (?:students?|applicants?|nationals?)\b", clause):
            gates.append({"id": "nationality", "state": "unknown", "evidence": [clause[:240]]})
            continue
        if re.search(r"\bor\b", clause) and re.search(r"visa|work authori[sz]ation|sponsorship", clause):
            gates.append({"id": "nationality", "state": "unknown", "evidence": [clause[:240]]})
            continue
        country = next(iter(countries))
        allows_citizens = bool(re.search(r"\bcitizens?|citizenship\b", clause))
        allows_residents = bool(re.search(r"permanent residents?", clause))
        allows_nationals = bool(re.search(r"\bnationals?\b", clause))
        match = (((allows_citizens or allows_nationals) and country in citizens)
                 or (allows_residents and country in residents)
                 or (allows_nationals and country == "US" and candidate.get("us_national", False)))
        gates.append({"id": "nationality", "state": "pass" if match else "fail" if known else "unknown",
                      "evidence": [clause[:240]]})
    unrestricted = any(re.search(
            r"no citizenship (?:requirement|restriction)|citizenship is not required|"
            r"regardless of (?:citizenship|nationality)|all nationalities|"
            r"international students (?:are )?(?:welcome|eligible)", clause
    ) for clause in clauses)
    if unrestricted and not grfp:
        for gate in gates:
            if gate["state"] == "fail":
                gate["state"] = "unknown"
                gate["evidence"].append("Conflicting citizenship exception; review the listing")
    if not gates:
        gates.append({"id": "nationality", "state": "pass" if unrestricted else "unknown",
                      "evidence": ["No citizenship restriction stated" if unrestricted
                                   else "Citizenship eligibility details incomplete"]})
    return gates
