"""End-to-end and persistence checks for the shared Radar workspace."""

import csv
import io
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

from radar.matching import eligibility
from radar.store import Store, safe_url


class RadarWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name)
        self.store = Store(self.path / "workspace")
        self.cli = [sys.executable, "-m", "radar", "--workspace", str(self.store.path)]

    def command(self, *args, json_mode=True):
        flags = ["--json"] if json_mode else []
        result = subprocess.run(
            [*self.cli, *flags, *args], capture_output=True, text=True, timeout=15
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout) if json_mode else result.stdout

    def test_offline_decision_journey_and_undo(self):
        self.assertEqual(self.command("search"), [])
        self.command("sample")
        (art,) = self.command("search", "art")
        self.assertIn("Museum", art["title"])
        goal = self.path / "goal.json"
        goal.write_text('{"prefer":["scientific computing"]}')
        self.command("set-goal", "Methods", str(goal))
        recommended = self.command(
            "search", "--goal", "Methods", "--scope", "recommended"
        )
        self.assertGreaterEqual(len(recommended), 2)
        lead, second = recommended[:2]
        self.assertIn("Scientific Software", lead["title"])
        self.assertEqual(
            len(self.command("compare", lead["id"], second["id"], "--goal", "Methods")),
            2,
        )
        self.command("track", lead["id"], "--save", "--status", "apply")
        self.assertEqual(self.store.inspect(lead["id"])["status"], "apply")
        self.store.track(lead["id"], status="skip", note="Verify the funding condition")
        self.assertIn(
            lead["id"],
            {row["id"] for row in self.command("search", "scientific software")},
        )
        self.assertNotIn(
            lead["id"],
            {
                row["id"]
                for row in self.command(
                    "search", "--goal", "Methods", "--scope", "recommended"
                )
            },
        )
        self.assertEqual(
            self.command("search", "--scope", "tracked")[0]["note"],
            "Verify the funding condition",
        )
        self.command("undo-goal", "Methods")
        self.assertEqual(self.command("goals"), [])
        self.assertTrue(self.store.inspect(lead["id"])["saved"])

    def test_untrusted_import_is_inert_and_csv_escaped(self):
        fixture = self.path / "import.json"
        fixture.write_text(
            json.dumps(
                [
                    {
                        "external_id": "spreadsheet",
                        "title": "=2+3",
                        "description": "<script>upload a local file</script>",
                        "authority": True,
                        "evidence": {"eligibility_condition": {"fact": "anyone"}},
                    }
                ]
            )
        )
        self.command("import", str(fixture))
        (result,) = self.store.search()
        (observation,) = self.store.inspect(result["id"])["observations"]
        self.assertFalse(observation["authority"])
        self.assertNotIn("eligibility_condition", observation["evidence"])
        rows = list(
            csv.DictReader(io.StringIO(self.command("export", json_mode=False)))
        )
        self.assertEqual(rows[0]["title"], "'=2+3")
        for url in (
            "https://localhost./",
            "https://127.0.0.1/",
            "https://127.1/",
            "https://[::1]/",
            "file:///etc/passwd",
            "https://user@example.org/",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                safe_url(url)

    def test_observations_cycle_deduplication_history_and_tracking(self):
        source_a, source_b = "publisher", "mirror"
        for source in (source_a, source_b):
            self.store.add_source(
                {"id": source, "name": source, "url": "", "kind": "manual"}
            )
        base = {
            "external_id": "cohort",
            "title": "Research fellowship",
            "url": "https://example.org/program?utm_source=publisher",
            "cycle": "a",
        }
        self.store.ingest(source_a, [base], "complete", "")
        (first,) = self.store.search()
        self.store.track(first["id"], status="applied", saved=True)
        mirror = {**base, "external_id": "mirror", "url": "https://example.org/program"}
        self.assertEqual(
            self.store.ingest(source_b, [mirror], "partial", "")["duplicates"], 1
        )
        self.store.ingest(
            source_a, [{**base, "description": "New publisher detail"}], "partial", ""
        )
        self.store.ingest(source_a, [], "failed", "Stalled feed")
        current = self.store.inspect(first["id"])
        self.assertEqual(len(current["observations"]), 2)
        self.assertEqual(len(current["history"]), 1)
        self.store.ingest(source_a, [{**base, "cycle": "b"}], "complete", "")
        self.assertEqual(
            self.store.ingest(source_b, [{**mirror, "cycle": "b"}], "partial", "")[
                "duplicates"
            ],
            1,
        )
        latest, previous = self.store.search()
        self.assertNotEqual(latest["id"], previous["id"])
        self.assertEqual((latest["status"], previous["status"]), ("new", "applied"))

    def test_eligibility_alternatives_negation_and_conflict(self):
        quote = "Applicants must be enrolled or qualify for a partner exemption."
        source = {
            "authority": True,
            "url": "https://example.org",
            "description": quote,
            "evidence": {
                "eligibility_quote": quote,
                "eligibility_condition": {
                    "any": [
                        {"fact": "enrolled"},
                        {"all": [{"fact": "partner"}, {"not": {"fact": "expired"}}]},
                    ]
                },
            },
        }
        facts = {"enrolled": False, "partner": True}
        self.assertEqual(eligibility([source], facts)["state"], "unknown")
        self.assertEqual(
            eligibility([source], {**facts, "expired": False})["state"],
            "meets recorded condition",
        )
        self.assertEqual(
            eligibility([source], {**facts, "expired": True})["state"],
            "does not meet recorded condition",
        )
        conflict = {
            **source,
            "evidence": {
                **source["evidence"],
                "eligibility_condition": {"fact": "enrolled"},
            },
        }
        self.assertEqual(
            eligibility([source, conflict], {**facts, "expired": False})["state"],
            "unknown",
        )

    def test_parallel_initialization_and_partial_decision_updates(self):
        with ThreadPoolExecutor(max_workers=6) as workers:
            list(workers.map(lambda _: self.store.search(), range(12)))
        self.command("sample")
        row = self.store.search()[0]
        with ThreadPoolExecutor(max_workers=3) as workers:
            futures = [
                workers.submit(self.store.track, row["id"], status="applied"),
                workers.submit(self.store.track, row["id"], saved=True),
                workers.submit(self.store.track, row["id"], note="Verify"),
            ]
            for future in futures:
                future.result()
        stored = self.store.inspect(row["id"])
        self.assertEqual(
            (stored["status"], stored["saved"], stored["note"]),
            ("applied", True, "Verify"),
        )
        with closing(self.store.connect()) as connection:
            self.assertEqual(
                connection.execute("PRAGMA integrity_check").fetchone()[0], "ok"
            )
        with closing(sqlite3.connect(self.store.path)) as connection:
            self.assertEqual(
                connection.execute("SELECT count(*) FROM decisions").fetchone()[0], 1
            )


if __name__ == "__main__":
    unittest.main()
