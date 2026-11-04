"""Geography behavior across profile import, collection, search and recovery."""

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from monitor.models import Opportunity
from radar.app import import_rows
from radar.collection import _observations
from radar.feed import get_feed
from radar.locations import normalize_location, observed_places, suggest_places
from radar.profiles import import_profiles
from radar.recovery import restore
from radar.store import APPLICATION_ID, SCHEMA_V2, Store


def _row(title, location, **fields):
    return {"external_id": title, "title": title, "location": location, **fields}


class LocationAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.store = Store(self.root / "workspace.sqlite3")
        import_rows(
            self.store,
            [
                _row("Baltimore site", "Baltimore, MD (Hybrid)"),
                _row("Cambridge office", "Cambridge, MA (On-site)"),
                _row("Houston site", "Houston, TX"),
                _row("Small site", "Starbase, TX"),
                _row("Paris site", "Paris, France"),
                _row("Ambiguous site", "Springfield"),
                _row("Remote US", "Remote - United States"),
                _row("Remote Canada", "Remote - Canada"),
                _row("Remote anywhere", "Remote worldwide"),
                _row("Remote unclear", "Remote"),
                _row("Remote exclusions", "Remote - US (excluding Maryland)"),
            ],
        )

    def _titles(self, **criteria):
        return {
            row["title"]
            for row in get_feed(
                self.store, view="explore", location=criteria, limit=30
            )["items"]
        }

    def test_geography_is_hierarchical_without_assigning_ambiguous_names(self):
        suggestions = suggest_places("Springfield")
        self.assertGreater(len(suggestions), 1)
        self.assertEqual(suggest_places(suggestions[0]["id"]), suggestions[:1])
        with self.assertRaisesRegex(ValueError, "disambiguate"):
            normalize_location({"places": ["Springfield"]})
        with self.assertRaisesRegex(ValueError, "disambiguate"):
            normalize_location({"places": ["A typo, Texas"]})
        self.assertEqual(
            self._titles(places=["United States"]),
            {
                "Baltimore site",
                "Cambridge office",
                "Houston site",
                "Small site",
                "Remote US",
                "Remote anywhere",
            },
        )
        self.assertEqual(
            self._titles(places=["Maryland, United States"]),
            {"Baltimore site", "Remote US", "Remote anywhere"},
        )
        self.assertEqual(
            self._titles(places=["Paris, France"]), {"Paris site", "Remote anywhere"}
        )
        self.assertEqual(
            self._titles(places=["Houston, TX"]),
            {"Houston site", "Remote US", "Remote anywhere"},
        )
        self.assertEqual(
            observed_places("New York, NY"), (("", "US/NY/new-york-city"),)
        )
        self.assertEqual(
            observed_places("Paris, France / Paris, TX"),
            (("", "FR/11/paris"), ("", "US/TX/paris")),
        )
        self.assertEqual(observed_places("Portland, OR"), (("", "US/OR/portland"),))
        self.assertEqual(observed_places("Brampton, ON"), (("", "CA/08/brampton"),))
        self.assertEqual(observed_places("Sydney, NSW"), (("", "AU/02/sydney"),))
        self.assertEqual(observed_places("Georgia"), (("", ""),))
        self.assertEqual(observed_places("Georgia, US"), (("", "US/GA"),))
        self.assertEqual(observed_places("Remote, United States"), (("remote", "US"),))
        self.assertEqual(
            observed_places("Remote US or Canada"), (("remote", "US"), ("remote", "CA"))
        )
        self.assertEqual(
            observed_places("Remote · United States · Canada"),
            (("remote", "US"), ("remote", "CA")),
        )
        self.assertEqual(
            observed_places("Remote · Worldwide · excluding US"), (("remote", ""),)
        )
        self.assertEqual(
            observed_places("Paris, France · Toronto, ON"),
            (("", "FR/11/paris"), ("", "CA/08/toronto")),
        )

    def test_remote_scope_work_mode_and_unknown_evidence_are_independent(self):
        required = {"places": ["Baltimore, MD"], "modes": ["remote"]}
        self.assertEqual(self._titles(**required), {"Remote US", "Remote anywhere"})
        self.assertEqual(
            self._titles(**required, include_unknown=True),
            {
                "Remote US",
                "Remote anywhere",
                "Remote unclear",
                "Remote exclusions",
                "Ambiguous site",
            },
        )
        self.assertEqual(self._titles(modes=["onsite"]), {"Cambridge office"})
        self.assertEqual(self._titles(modes=["hybrid"]), {"Baltimore site"})

        fields = {"url": "https://example.com/jobs/123", "cycle": "2027"}
        import_rows(
            self.store, [_row("Split evidence", "Remote - France", **fields)], "remote"
        )
        import_rows(
            self.store,
            [_row("Split evidence", "Boston, MA (On-site)", **fields)],
            "office",
        )
        self.assertNotIn(
            "Split evidence", self._titles(places=["United States"], modes=["remote"])
        )

    def test_published_workplace_type_does_not_invent_remote_jurisdiction(self):
        source = {
            "id": "example_ats",
            "name": "Example ATS",
            "kind": "ashby",
            "url": "https://jobs.ashbyhq.com/example-ats",
            "board": "example-ats",
        }
        self.store.add_source(source)
        jobs = [
            Opportunity(
                source["id"],
                title,
                title,
                "Example ATS",
                f"https://example.com/jobs/{index}",
                "Baltimore, MD",
                metadata={"workplace_type": mode},
            )
            for index, (title, mode) in enumerate(
                [("Published hybrid", "Hybrid"), ("Published remote", "Remote")], 1
            )
        ]
        rows = list(_observations(source, jobs, source["url"]))
        self.store.ingest(source["id"], rows, "complete", "Offline test")
        self.assertIn(
            "Published hybrid", self._titles(places=["United States"], modes=["hybrid"])
        )
        self.assertIn("Published remote", self._titles(modes=["remote"]))
        self.assertNotIn(
            "Published remote", self._titles(places=["United States"], modes=["remote"])
        )

    def test_profile_location_requirement_applies_before_feed_pagination(self):
        pack = {
            "version": 1,
            "profiles": [
                {
                    "name": "Nearby",
                    "rules": {
                        "prefer": ["site"],
                        "location": {
                            "places": ["Maryland, United States"],
                            "policy": "require",
                        },
                    },
                }
            ],
            "active_profile": "Nearby",
        }
        import_profiles(self.store, text=json.dumps(pack))
        page = get_feed(self.store, limit=1)
        self.assertEqual(page["mode"], "personalized")
        self.assertEqual([item["title"] for item in page["items"]], ["Baltimore site"])
        self.assertEqual(page["items"][0]["location_match"]["state"], "match")
        self.assertEqual(
            [
                row["title"]
                for row in self.store.search(goal="Nearby", scope="recommended")
            ],
            ["Baltimore site"],
        )
        no_match = get_feed(self.store, query="Houston", limit=1)
        self.assertEqual(no_match["items"], [])
        inspect = self.store.inspect(page["items"][0]["id"], goal="Nearby")
        self.assertEqual(
            inspect["match"]["location"]["evidence"], "Baltimore, MD (Hybrid)"
        )
        initial = get_feed(self.store, view="explore")
        import_rows(self.store, [_row("New city listing", "Baltimore, MD")])
        inbox = get_feed(
            self.store,
            view="explore",
            after_rowid=initial["review_cursor"],
            location={"places": ["United States"]},
        )
        self.assertEqual(
            [item["title"] for item in inbox["items"]], ["New city listing"]
        )
        self.assertGreater(inbox["review_cursor"], initial["review_cursor"])

    def test_existing_observation_is_backfilled_on_v2_upgrade_and_restore(self):
        before = self.root / "v2.sqlite3"
        before.touch(mode=0o600)
        with closing(sqlite3.connect(before)) as old, old:
            old.executescript(SCHEMA_V2)
            old.execute(f"PRAGMA application_id = {APPLICATION_ID}")
            old.execute("PRAGMA user_version = 2")
            old.execute("INSERT INTO sources VALUES (?,?)", ("demo", '{"name":"Demo"}'))
            old.execute(
                "INSERT INTO listings VALUES (?,?,?)",
                ("record", "identity", "2026-01-01"),
            )
            old.execute(
                "INSERT INTO observations VALUES (?,?,?,?,?)",
                (
                    "record",
                    "demo",
                    "published",
                    "2026-01-01",
                    '{"location":"Paris, France"}',
                ),
            )
        restore(self.store, before)
        for workspace in (self.store, Store(before)):
            with closing(workspace.connect()) as db:
                result = db.execute("SELECT * FROM listing_places").fetchone()
                self.assertEqual(tuple(result), ("record", "", "FR/11/paris"))
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 3)
