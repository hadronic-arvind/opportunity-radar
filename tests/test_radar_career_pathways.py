"""Exercise the public profile pack against a shared, fictional listing pool."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
PACK = PROJECT / "examples" / "profile-packs" / "career-pathways.json"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
LISTINGS = FIXTURES / "career-pathway-listings.json"
EXPECTATIONS = FIXTURES / "career-pathway-expectations.json"


class CareerPathwayAcceptanceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="radar-career-pathways-")
        self.addCleanup(directory.cleanup)
        self.workspace = Path(directory.name) / "workspace.sqlite3"

    def _radar(self, *arguments):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "radar",
                "--workspace",
                str(self.workspace),
                "--json",
                *map(str, arguments),
            ],
            cwd=PROJECT,
            text=True,
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_student_and_professional_profiles_rank_stage_appropriate_listings(self):
        listing_titles = {
            row["external_id"]: row["title"]
            for row in json.loads(LISTINGS.read_text(encoding="utf-8"))
        }
        expectations = json.loads(EXPECTATIONS.read_text(encoding="utf-8"))
        preview = self._radar("import-profiles", PACK, "--preview")
        self.assertEqual(self._radar("goals"), [])
        self._radar("import-profiles", PACK, "--expected-revision", preview["revision"])
        self._radar("import", LISTINGS)
        self.assertEqual(
            self._radar("feed", "--limit", 1)["goal"], "Medicine - student"
        )
        self.assertEqual(
            {goal["name"] for goal in self._radar("goals")}, set(expectations)
        )

        for name, expected in expectations.items():
            with self.subTest(profile=name):
                feed = self._radar("feed", "--goal", name)
                titles = [card["title"] for card in feed["items"]]
                self.assertEqual(feed["mode"], "personalized")
                self.assertEqual(
                    titles,
                    [listing_titles[expected[key]] for key in ("first", "also")],
                )
                self.assertEqual(feed["items"][0]["reason_kind"], "match")

        education = self._radar("feed", "--goal", "K-12 education - student")
        conditional_title = listing_titles["conditional-practicum"]
        self.assertNotIn(
            conditional_title, [card["title"] for card in education["items"]]
        )
        explored = self._radar("feed", "residency observer", "--view", "explore")
        self.assertEqual(explored["items"][0]["title"], conditional_title)

    def test_nearby_phrases_keep_short_subject_words_and_allow_reordering(self):
        listings = [
            {
                "external_id": "law",
                "title": "Legal Research Internship",
                "description": "Support statute research as a legal research intern.",
            },
            {
                "external_id": "aide",
                "title": "Museum Research Internship",
                "description": "The research internship includes onboarding as an aide.",
            },
            {
                "external_id": "ai",
                "title": "Computing Student Placement",
                "description": "The research group offers a summer internship in AI methods.",
            },
            {
                "external_id": "ux",
                "title": "Design Student Placement",
                "description": "Conduct UX interviews as a research intern.",
            },
            {
                "external_id": "modeling",
                "title": "Applied Modeling Placement",
                "description": "Practice modeling through a summer research internship.",
            },
        ]
        listing_file = self.workspace.with_name("listings.json")
        listing_file.write_text(json.dumps(listings), encoding="utf-8")
        self._radar("import", listing_file)

        for phrase, title in (
            ("AI research internship", "Computing Student Placement"),
            ("UX research intern", "Design Student Placement"),
            ("research internship for modeling", "Applied Modeling Placement"),
        ):
            with self.subTest(phrase=phrase):
                profile_file = self.workspace.with_name("goal.json")
                profile_file.write_text(
                    json.dumps({"prefer": [phrase]}), encoding="utf-8"
                )
                self._radar("set-goal", "Test", profile_file)
                feed = self._radar("feed", "--goal", "Test")
                self.assertEqual(feed["mode"], "personalized")
                self.assertEqual([item["title"] for item in feed["items"]], [title])
                item = self._radar("inspect", feed["items"][0]["id"], "--goal", "Test")
                self.assertEqual(item["match"]["rules"][0]["method"], "nearby terms")
