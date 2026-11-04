"""Score the frozen synthetic holdout without network or private workspace access.

Run from the repository root with python3 -m scripts.evaluate_radar_holdout.
"""

import argparse
import json
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from monitor.opportunity_search import search_ranker
from radar.store import Store, _goal_expression

FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "rebuild_holdout.json"


def _listing(item):
    url_key = {"simulation-mirror": "simulation", "simulation-next": "simulation"}.get(
        item["id"], item["id"]
    )
    return {
        "external_id": item["id"],
        "title": item["title"],
        "organization": item["organization"],
        "url": "https://synthetic.example.org/listing/" + url_key,
        "description": item["body"],
        "location": item["region"],
        "kinds": [item["kind"]],
        "cycle": {"simulation": "2027", "simulation-mirror": "2027", "simulation-next": "2028"}.get(item["id"], ""),
        "deadline_at": item.get(
            "deadline_at", {"closed": "2023-01-01", "simulation-next": "2028-03-03"}.get(item["id"])
        ),
        "authority": bool(item.get("eligibility_evidence")),
        "evidence": item.get("eligibility_evidence", {}),
    }


def _reference(items, phrases):
    rankers = [search_ranker(phrase) for phrase in phrases]
    ranked = []
    for item in items:
        score = max(
            rank(item["title"], item["organization"], item["kind"], "", item["region"], "", "", item["body"])
            for rank in rankers
        )
        if score >= 0:
            ranked.append((score, item["id"]))
    return [item_id for _score, item_id in sorted(ranked, key=lambda row: (-row[0], row[1]))]


def _metrics(persona, items, candidates, first):
    relevant = {
        item["group"] for item in items.values()
        if item["labels"][persona] == "relevant"
    }
    groups = {items[item_id]["group"] for item_id in candidates}
    first = first[:5]
    first_groups = {
        items[item_id]["group"] for item_id in first
        if items[item_id]["labels"][persona] == "relevant"
    }
    actionable_groups = {
        items[item_id]["group"] for item_id in first
        if items[item_id]["labels"][persona] == "relevant"
        and _eligibility_label(items[item_id], persona) != "does not meet recorded condition"
        and item_id != "closed"
    }
    group_recall = len(groups & relevant) / len(relevant)
    precision = len(first_groups) / 5
    slices = {}
    for field in ("kind", "region", "stage"):
        for value in sorted({item[field] for item in items.values()}):
            expected = {item["group"] for item in items.values()
                        if item[field] == value and item["labels"][persona] == "relevant"}
            if expected:
                slices[f"{field}={value}"] = {
                    "retrieved": len(groups & expected), "relevant": len(expected),
                }
    return {
        "candidate_recall": group_recall,
        "candidate_relevant_groups": len(groups & relevant),
        "relevant_groups": len(relevant),
        "top5_useful_groups_per_5_slots": precision,
        "top5_excluding_known_closed_or_ineligible_per_5_slots": len(actionable_groups) / 5,
        "first_ids": first,
        "first_irrelevant_ids": [item_id for item_id in first if items[item_id]["labels"][persona] == "irrelevant"],
        "candidate_missed_groups": sorted(relevant - groups),
        "top5_missed_groups": sorted(relevant - first_groups),
        "candidate_missed_ids": sorted(
            item_id for item_id, item in items.items()
            if item["labels"][persona] == "relevant" and item_id not in candidates
        ),
        "candidate_recall_slices": slices,
        "passes_recall_precision_thresholds": group_recall >= 0.90 and precision >= 0.60,
    }


def _eligibility_label(item, persona):
    label = item["eligibility"]
    return label if isinstance(label, str) else label.get(persona, "unknown")


def _evaluate(fixture_path):
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    items = {item["id"]: item for item in fixture["items"]}
    report = {
        "fixture_version": fixture["version"],
        "fixture": fixture_path.name,
        "records": len(items),
        "groups": len({item["group"] for item in items.values()}),
        "personas": {},
    }
    with TemporaryDirectory(prefix="radar-holdout-") as directory:
        store = Store(Path(directory) / "holdout.sqlite")
        for source_id in ("holdout", "mirror"):
            store.add_source({"id": source_id, "name": source_id, "kind": "manual"})
            store.ingest(
                source_id,
                [_listing(item) for item in items.values()
                 if (item["id"] == "simulation-mirror") == (source_id == "mirror")],
                "partial", "Synthetic evaluator snapshot",
            )
        all_listings = store.search(limit=200)
        observations = {
            listing["id"]: store.inspect(listing["id"])["observations"] for listing in all_listings
        }
        report["explorer"] = {
            "unconfigured_inventory_size": len(all_listings),
            "unconfigured_recommendation_size": len(store.search(scope="recommended")),
            "has_mirror_cluster": any(len(rows) == 2 for rows in observations.values()),
            "known_closed_label": next((
                listing["availability"] for listing in all_listings
                if listing["availability"] == "recorded deadline passed"
            ), None),
        }
        for persona in fixture["personas"]:
            name, goals = persona["id"], persona["goals"]
            if not goals:
                continue
            reference = _reference(list(items.values()), goals)
            rules = {"prefer": goals, "facts": {
                key: value for key, value in persona["facts"].items() if type(value) is bool
            }}
            store.put_goal(name, rules)
            with closing(store.connect()) as connection:
                matches = connection.execute(
                    "SELECT listing_id FROM inventory WHERE inventory MATCH ?",
                    (_goal_expression(rules),),
                )
                candidates = [
                    row["external_id"] for result in matches
                    for row in observations[result["listing_id"]]
                ]
            recommended = store.search(goal=name, scope="recommended", limit=200)
            first = [observations[row["id"]][0]["external_id"] for row in recommended]
            assessed = store.search(goal=name, limit=200)
            uncertain = false_definitive = supported = unsupported = 0
            correct_definitive = false_uncertain = eligibility_quotes = unsupported_eligibility = 0
            for listing in assessed:
                rows = observations[listing["id"]]
                match = listing["match"]
                expectations = {_eligibility_label(items[row["external_id"]], name) for row in rows}
                expected = next(iter(expectations)) if len(expectations) == 1 else "unknown"
                outcome = match["eligibility"]["state"]
                if outcome == expected == "unknown":
                    uncertain += 1
                elif outcome == expected:
                    correct_definitive += 1
                elif outcome == "unknown":
                    false_uncertain += 1
                else:
                    false_definitive += 1
                for claim in match["eligibility"]["evidence"]:
                    valid = any(
                        row["authority"] and row["url"] == claim["url"]
                        and claim["quote"] in row["description"]
                        for row in rows
                    )
                    eligibility_quotes += valid
                    unsupported_eligibility += not valid
                for explanation in match["rules"]:
                    if explanation["state"] != "observed":
                        continue
                    valid = any(
                        row["url"] == explanation["url"]
                        and explanation["quote"] in row[explanation["field"]]
                        for row in rows
                    )
                    supported += valid
                    unsupported += not valid
            report["personas"][name] = {
                "incumbent_lexical_reference": _metrics(name, items, reference, reference),
                "replacement": _metrics(name, items, candidates, first),
                "eligibility_unknown": uncertain,
                "false_definitive_eligibility": false_definitive,
                "correct_definitive_eligibility": correct_definitive,
                "missed_definitive_eligibility": false_uncertain,
                "supported_eligibility_quotes": eligibility_quotes,
                "unsupported_eligibility_quotes": unsupported_eligibility,
                "exact_supported_explanations": supported,
                "unsupported_explanations": unsupported,
            }
        choice = all_listings[0]
        store.track(choice["id"], status="skip")
        report["explorer"]["dismissed_directly_inspectable"] = store.inspect(choice["id"])["status"] == "skip"
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=FIXTURE)
    args = parser.parse_args()
    print(json.dumps(_evaluate(args.fixture), indent=2, ensure_ascii=False, sort_keys=True))
