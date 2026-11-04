"""Offline acceptance checks using the user-facing terminal entry point."""

import csv
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]


class TerminalAcceptanceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="radar-acceptance-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.db = self.root / "state" / "workspace.sqlite3"

    def _command(self, *arguments, input_text=None, expected=0):
        result = subprocess.run(
            [sys.executable, "-m", "radar", "--workspace", str(self.db), "--json", *arguments],
            cwd=PROJECT, text=True, input=input_text, capture_output=True, timeout=10,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(result.returncode, expected, result.stderr)
        return result

    def _radar(self, *arguments, **options):
        return json.loads(self._command(*arguments, **options).stdout)

    def _document(self, filename, payload):
        path = self.root / filename
        path.write_text(json.dumps(payload), encoding="utf-8")
        return str(path)

    def test_offline_journey_persists_and_recovers_through_normal_commands(self):
        self.assertEqual(self._radar("init", "--empty")["indexed"], 0)
        self.assertEqual(self._radar("search"), [])
        self.assertEqual(self._radar("collect")["receipts"], [])
        self.assertEqual(self._radar("sample")["seen"], 8)
        self.assertEqual(self._radar("sample")["new"], 0)
        self.assertEqual(len(self._radar("search")), 8)
        self.assertEqual([row["title"] for row in self._radar("search", "art")],
                         ["Museum Education Assistant"])
        goal = self._document("goal.json", {"prefer": ["scientific computing"]})
        self._radar("set-goal", "Research", goal)
        results = self._radar("search", "--goal", "Research", "--scope", "recommended")
        self.assertTrue(any(row["title"] == "Scientific Software Fellow" for row in results))
        self.assertGreaterEqual(len(results), 2)
        first, second = (row["id"] for row in results[:2])
        self.assertEqual(len(self._radar("compare", first, second)), 2)
        self._radar("track", first, "--save", "--status", "apply", "--note-file", "-", input_text="Private note")
        self.assertEqual(self._radar("inspect", first)["note"], "Private note")
        snapshot = str(self.root / "backup.sqlite3")
        self._radar("backup", snapshot)
        self._radar("track", first, "--status", "skip", "--unsave")
        self.assertNotIn(first, {row["id"] for row in self._radar(
            "search", "--goal", "Research", "--scope", "recommended"
        )})
        self.assertIn(first, {row["id"] for row in self._radar("search")})
        self.assertIn(first, {row["id"] for row in self._radar("search", "--scope", "tracked")})
        self._radar("undo-goal", "Research")
        self.assertEqual(self._radar("goals"), [])
        self._radar("restore", snapshot)
        recovered = self._radar("inspect", first)
        self.assertEqual((recovered["status"], recovered["saved"], recovered["note"]),
                         ("apply", True, "Private note"))
        self.assertEqual(self._radar("goals")[0]["name"], "Research")
        self.assertEqual(self._radar("doctor")["check"], "ok")

    def test_new_workspace_has_starter_profiles_without_selecting_one(self):
        self.assertEqual(self._radar("init")["indexed"], 0)
        dashboard = self._radar("feed")
        names = {profile["name"] for profile in dashboard["goals"]}
        self.assertEqual(names, {
            f"{domain} - {stage}"
            for domain in ("Medicine", "AI and ML research", "Clean energy")
            for stage in ("student", "professional")
        })
        self.assertIsNone(dashboard["goal"])
        self.assertEqual(dashboard["items"], [])
        self.assertEqual(self._radar("sources"), [])

        name = "Medicine - student"
        self._radar("feed-preferences", "--goal", name)
        self.assertEqual(self._radar("feed")["goal"], name)
        self._radar("undo-goal", name)
        self._radar("init")
        self.assertEqual({profile["name"] for profile in self._radar("goals")}, names - {name})

    def test_untrusted_text_and_authority_use_public_cli_path(self):
        items = self._document("items.json", [{
            "external_id": "spreadsheet", "title": "=2+3", "url": "https://example.org/item",
            "description": "<script>alert(1)</script> You must be enrolled.",
            "authority": True, "evidence": {
                "eligibility_condition": {"fact": "enrolled"},
                "eligibility_quote": "You must be enrolled.",
            },
        }])
        self._radar("import", items)
        listing = self._radar("search")[0]
        goal = self._document("goal.json", {"facts": {"enrolled": False}})
        self._radar("set-goal", "Local", goal)
        evidence = self._radar("inspect", listing["id"], "--goal", "Local")
        self.assertEqual(evidence["match"]["eligibility"]["state"], "unknown")
        self.assertFalse(evidence["observations"][0]["authority"])
        self.assertEqual(evidence["description"], "<script>alert(1)</script> You must be enrolled.")
        csv_result = self._command("export").stdout
        self.assertEqual(list(csv.DictReader(io.StringIO(csv_result)))[0]["title"], "'=2+3")
        source = self._document("source.json", {
            "id": "private", "name": "Private", "kind": "syndication", "url": "https://127.0.0.1/rss",
        })
        self.assertIn("public", self._command("add-source", "--file", source, expected=1).stderr)
        self.assertEqual(self._radar("search")[0]["id"], listing["id"])


if __name__ == "__main__":
    unittest.main()
