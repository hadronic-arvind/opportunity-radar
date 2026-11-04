"""Regression checks for listing comparison and sourced evidence."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from radar import app
from radar.store import Store

PROJECT = Path(__file__).resolve().parents[1]


class IntelligenceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.db = Path(directory.name) / "workspace.sqlite3"
        self.store = Store(self.db)

    def bridge(self, op, expected=True, **args):
        result = subprocess.run(
            [sys.executable, "-m", "radar.desktop_bridge", "--workspace", str(self.db)],
            input=json.dumps({"op": op, "args": args}) + "\n",
            text=True,
            capture_output=True,
            check=True,
            cwd=PROJECT,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        response = json.loads(result.stdout)
        self.assertEqual(response["ok"], expected, response)
        return response["result" if expected else "error"]

    def test_revision_pairs_follow_one_channel_and_preserve_changed_excerpt(self):
        initial = {
            "external_id": "water",
            "title": "Aquifer Research Fellow",
            "url": "https://example.com/jobs/842",
            "cycle": "fall",
            "deadline_at": "2040-09-10",
            "description": "Water samples. " * 90 + "Before: references optional.",
        }
        app.import_rows(self.store, [initial], "direct")
        app.import_rows(
            self.store, [{**initial, "description": "Syndicated copy."}], "partner"
        )
        updated = {
            **initial,
            "deadline_at": "2040-09-18",
            "description": "Water samples. " * 90 + "After: references required.",
        }
        app.import_rows(self.store, [updated], "direct")
        app.import_rows(self.store, [updated], "direct")
        listing = self.store.search()
        self.assertEqual(len(listing), 1)
        revisions = self.bridge("listing_revisions", id=listing[0]["id"])
        self.assertEqual([revision["source_id"] for revision in revisions], ["direct"])
        changes = {change["field"]: change for change in revisions[0]["changes"]}
        self.assertEqual(changes["deadline_at"]["before"]["text"], "2040-09-10")
        self.assertEqual(changes["deadline_at"]["after"]["text"], "2040-09-18")
        self.assertIn("references optional.", changes["description"]["before"]["text"])
        self.assertIn("references required.", changes["description"]["after"]["text"])
        self.assertTrue(changes["description"]["before"]["truncated"])

    def test_related_uses_specific_terms_and_excludes_hidden_and_closed_listings(self):
        app.import_rows(
            self.store,
            [
                {
                    "external_id": "focus",
                    "title": "Aquifer Hydrology Research Fellow",
                },
                {"external_id": "related", "title": "Hydrology Specialist"},
                {
                    "external_id": "body",
                    "title": "Water Resources Internship",
                    "description": "Study aquifer hydrology with a laboratory mentor.",
                },
                {"external_id": "hidden", "title": "Aquifer Hydrology Engineer"},
                {
                    "external_id": "closed",
                    "title": "Aquifer Internship",
                    "deadline_at": "2000-01-01",
                },
                {"external_id": "generic", "title": "Research Fellow"},
            ],
        )
        focus = self.store.search("aquifer hydrology research")[0]["id"]
        hidden = self.store.search("aquifer hydrology engineer")[0]["id"]
        self.store.track(hidden, status="skip")
        related = self.bridge("related_opportunities", id=focus)
        self.assertEqual(
            [(row["title"], row["shared_terms"]) for row in related],
            [
                ("Hydrology Specialist", ["hydrology"]),
                ("Water Resources Internship", ["aquifer", "hydrology"]),
            ],
        )
        generic = next(
            row["id"]
            for row in self.store.search()
            if row["title"] == "Research Fellow"
        )
        self.assertEqual(self.bridge("related_opportunities", id=generic), [])
        self.assertIn(
            "Limit",
            self.bridge("related_opportunities", expected=False, id=focus, limit=True),
        )

    def test_cost_evidence_preserves_unknown_conditional_and_negative_wording(self):
        app.import_rows(
            self.store,
            [
                {
                    "external_id": "cost",
                    "title": "Research placement",
                    "url": "https://example.com/research",
                    "description": "The stipend may be reduced if other funding is awarded. "
                    "There is no application fee. No travel reimbursement is available.",
                    "requirements": "Housing support depends on availability.",
                },
                {"external_id": "unknown", "title": "Teaching placement"},
            ],
            "publisher",
        )
        listing = self.store.search("research placement")[0]["id"]
        evidence = self.bridge("inspect", id=listing)["cost_evidence"]
        pay = evidence["Pay and stipends"]
        self.assertEqual(
            pay[0]["quote"], "The stipend may be reduced if other funding is awarded."
        )
        self.assertTrue(pay[0]["conditional"])
        self.assertEqual(pay[0]["url"], "https://example.com/research")
        self.assertEqual(pay[0]["source_id"], "publisher")
        self.assertTrue(evidence["Fees and costs"][0]["negative_language"])
        self.assertTrue(evidence["Funding and support"][0]["conditional"])
        unknown = self.store.search("teaching placement")[0]["id"]
        self.assertFalse(
            any(self.bridge("inspect", id=unknown)["cost_evidence"].values())
        )


if __name__ == "__main__":
    unittest.main()
