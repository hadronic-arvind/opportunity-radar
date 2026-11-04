"""Source comparison and local, evidence-linked opportunity discovery."""

import json
import re
from contextlib import closing
from os.path import commonprefix

from .brief import CONDITIONAL, NEGATIVE, fragments
from .matching import BOILERPLATE, tokens

COST_PATTERNS = {
    "Pay and stipends": re.compile(
        r"\b(?:base pay|salary|salaries|stipends?|wages?|hourly rate|compensation|"
        r"unpaid|paid (?:placements?|internships?|fellowships?)|honorari(?:um|a))\b",
        re.I,
    ),
    "Fees and costs": re.compile(
        r"\b(?:fees?|tuition|out.of.pocket|(?:travel|housing|relocation|living) (?:costs?|expenses?)|"
        r"at (?:your|the applicant.s) expense|self.funded)\b",
        re.I,
    ),
    "Funding and support": re.compile(
        r"\b(?:reimburs\w+|waiv\w+|scholarships?|funded|funding|housing (?:provided|support|allowance)|"
        r"(?:travel|relocation|childcare) (?:support|assistance|allowance)|health insurance)\b",
        re.I,
    ),
}
REVISION_FIELDS = {
    "title": "Title",
    "organization": "Organization",
    "location": "Location",
    "deadline_at": "Deadline",
    "posted_at": "Posted date",
    "kinds": "Opportunity type",
    "requirements": "Requirements",
    "description": "Description",
    "url": "Publisher link",
}
FILLER = frozenset(
    """also about across after along among apply area assistant associate available based before
    being between candidate candidates career center centre company conduct current degree department
    develop development director doctoral employment engineer engineering experience fellow fellowship
    first full from future graduate great group have hiring including industry institution intern
    internship internships junior laboratory lead leading level looking manager members more offer
    opening opportunities opportunity organization our partner people placement position postdoctoral
    preferred program programme provide quality recent related relevant remote require required
    requirements research researcher role science scientist seeking senior should specialist staff
    student students support team their them these this through title training university using
    various work working with within would years""".split()
)
SHORT_ANCHORS = frozenset("ai gis hpc hr llm ml nlp ux".split())


def cost_evidence(observations):
    """Report wording with provenance, including conditions and negative language."""
    groups = {name: [] for name in COST_PATTERNS}
    seen = set()
    for observation in observations:
        for field in ("requirements", "description"):
            for quote in fragments(observation[field]):
                if BOILERPLATE.search(quote):
                    continue
                for name, pattern in COST_PATTERNS.items():
                    key = (name, observation["source_id"], quote)
                    if (
                        len(groups[name]) >= 4
                        or key in seen
                        or not pattern.search(quote)
                    ):
                        continue
                    seen.add(key)
                    groups[name].append(
                        {
                            "quote": quote,
                            "source_id": observation["source_id"],
                            "source_name": observation.get("source_name"),
                            "observed_at": observation["observed_at"],
                            "url": observation["url"],
                            "field": field,
                            "conditional": bool(CONDITIONAL.search(quote)),
                            "negative_language": bool(NEGATIVE.search(quote)),
                        }
                    )
                if all(len(entries) == 4 for entries in groups.values()):
                    return groups
    return groups


def _validate_request(id, limit, maximum):
    if not isinstance(id, str) or not id or len(id) > 500:
        raise ValueError("Provide a valid opportunity ID")
    if type(limit) is not int or not 1 <= limit <= maximum:
        raise ValueError(f"Limit must be between 1 and {maximum}")


def _change(field, label, before, after):
    if field == "kinds":
        before, after = ", ".join(before), ", ".join(after)
    if before == after:
        return None
    start = 0
    if max(len(before), len(after)) > 480:
        # Highlight the first changed region even when it begins deep in a long posting.
        start = max(0, len(commonprefix((before, after))) - 90)

    def excerpt(value):
        return {
            "text": value[start : start + 480],
            "truncated": bool(value) and (start > 0 or start + 480 < len(value)),
        }

    return {
        "field": field,
        "label": label,
        "before": excerpt(before),
        "after": excerpt(after),
    }


def listing_revisions(store, id, limit=8):
    """Compare consecutive observations within each individual publisher record."""
    _validate_request(id, limit, 20)
    with closing(store.connect()) as db, db:
        db.execute("BEGIN")
        latest = store._observations(db, id)
        if not latest:
            raise ValueError("Unknown opportunity")
        newer = {(row["source_id"], row["external_id"]): row for row in latest}
        revisions = []
        for row in db.execute(
            """
            SELECT h.*,json_extract(s.config,'$.name') AS source_name
            FROM observation_history h JOIN sources s ON h.source_id=s.id
            WHERE h.listing_id=?
            ORDER BY h.id DESC
            LIMIT 120
            """,
            (id,),
        ):
            key = (row["source_id"], row["external_id"])
            previous = json.loads(row["data"])
            later = newer.get(key)
            newer[key] = {**previous, "observed_at": row["observed_at"]}
            if later is None:
                continue
            changes = [
                change
                for field, label in REVISION_FIELDS.items()
                if (change := _change(field, label, previous[field], later[field]))
            ]
            if changes:
                revisions.append(
                    {
                        "source_id": row["source_id"],
                        "source_name": row["source_name"],
                        "before_observed_at": row["observed_at"],
                        "after_observed_at": later["observed_at"],
                        "changes": changes,
                    }
                )
                if len(revisions) == limit:
                    break
        return revisions


def _anchors(source):
    place_words = set(tokens(source["location"] + " " + source["organization"]))
    return list(
        dict.fromkeys(
            token
            for token in tokens(source["title"])
            if (len(token) >= 4 or token in SHORT_ANCHORS)
            and not token.isdecimal()
            and token not in FILLER
            and token not in place_words
        )
    )[:12]


def _shared_terms(observations, anchors):
    title_words, description_words = set(), set()
    for row in observations:
        title_words.update(tokens(row["title"]))
        description_words.update(tokens(row["description"]))
    title = [term for term in anchors if term in title_words]
    body = [
        term
        for term in anchors
        if term not in title_words and term in description_words
    ]
    return title, body


def related_opportunities(store, id, limit=6):
    """Find other listings with specific terms in this listing's title."""
    _validate_request(id, limit, 12)
    with closing(store.connect()) as db, db:
        db.execute("BEGIN")
        observations = store._observations(db, id)
        if not observations:
            raise ValueError("Unknown opportunity")
        anchors = list(
            dict.fromkeys(token for row in observations[:3] for token in _anchors(row))
        )[:12]
        if not anchors:
            return []
        expression = (
            "{title body}: (" + " OR ".join(f'"{term}"' for term in anchors) + ")"
        )
        candidates = list(
            db.execute(
                """
            SELECT l.*,d.status,d.saved,d.note,d.status_updated_at,d.applied_at
            FROM inventory JOIN listings l ON l.rowid=inventory.rowid
            LEFT JOIN decisions d ON d.listing_id=l.id
            WHERE inventory MATCH ? AND l.id<>? AND (d.status IS NULL OR d.status<>'skip')
            ORDER BY bm25(inventory,0,8,0,0,0,1), l.rowid DESC
            LIMIT 96
            """,
                (expression, id),
            )
        )
        groups = (
            store._observation_page(db, [row["id"] for row in candidates])
            if candidates
            else {}
        )
        ranked = []
        for index, row in enumerate(candidates):
            observed = groups[row["id"]]
            title, body = _shared_terms(observed, anchors)
            if not title and len(body) < min(2, len(anchors)):
                continue
            result = store._summary(db, row, None, observations=observed)
            if result["availability"] in {
                "recorded deadline passed",
                "directory reference",
                "previously inactive, verify",
            }:
                continue
            result = {
                key: result[key]
                for key in ("id", "title", "organization", "location", "kinds")
            }
            result["shared_terms"] = title + body
            ranked.append((3 * len(title) + len(body), -index, result))
        return [entry[2] for entry in sorted(ranked, reverse=True)[:limit]]
