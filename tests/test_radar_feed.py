"""Local feed selection and subprocess protocol regression tests."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from radar import app, feed
from radar.store import Store


class DashboardFeedTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "workspace.sqlite3"
        self.store = Store(self.path)

    def import_jobs(self):
        app.import_rows(
            self.store,
            [
                {
                    "external_id": "one",
                    "title": "Hydrology researcher",
                    "organization": "Lake Lab",
                    "kinds": ["job"],
                    "location": "Remote",
                    "description": "Study hydrology and prepare reports.",
                    "url": "https://example.com/one",
                },
                {
                    "external_id": "two",
                    "title": "Conservation research",
                    "organization": "Field Lab",
                    "kinds": ["internship"],
                    "description": "Map habitats for the conservation research team.",
                },
                {
                    "external_id": "expired",
                    "title": "Past hydrology job",
                    "organization": "Old Lab",
                    "kinds": ["job"],
                    "deadline_at": "2000-01-01",
                    "description": "Study hydrology.",
                },
                {
                    "external_id": "three",
                    "title": "Dental technician",
                    "organization": "Clinic",
                    "kinds": ["job"],
                    "description": "Prepare instruments.",
                },
            ],
        )

    def test_cold_start_and_real_goal_do_not_mislabel_suggestions(self):
        empty = feed.get_feed(self.store)
        self.assertEqual(empty["mode"], "recent")
        self.assertEqual(empty["items"], [])
        self.import_jobs()
        recent = feed.get_feed(self.store)
        self.assertEqual(recent["summary"]["total"], 4)
        self.assertEqual(len(recent["items"]), 3)
        self.assertEqual({item["reason_kind"] for item in recent["items"]}, {"recent"})
        self.store.put_goal("Water science", {"prefer": ["hydrology"]})
        personalized = feed.get_feed(self.store)
        self.assertEqual(personalized["goal"], "Water science")
        self.assertEqual(personalized["mode"], "personalized")
        self.assertEqual(
            [item["title"] for item in personalized["items"]], ["Hydrology researcher"]
        )
        item = personalized["items"][0]
        self.assertEqual(item["reason"], "Matches your interest in hydrology")
        self.assertEqual(item["reason_kind"], "match")
        self.assertIn("hydrology", item["excerpt"].lower())
        self.assertTrue(item["source_name"])
        self.assertEqual(item["url"], "https://example.com/one")
        fallback = feed.get_feed(self.store, kind="internship")
        self.assertEqual(fallback["mode"], "recent")
        self.assertTrue(fallback["recent_fallback"])
        self.assertEqual(fallback["items"][0]["title"], "Conservation research")
        self.assertEqual(fallback["items"][0]["reason_kind"], "recent")
        self.assertEqual(
            feed.get_feed(self.store, view="explore", query="hydrology")["summary"][
                "total"
            ],
            4,
        )

    def test_migrated_active_goal_and_explicit_recent_persist(self):
        self.store.put_goal("First choice", {"prefer": ["conservation"]})
        self.store.put_goal("Migrated", {"prefer": ["hydrology"]})
        with self.store.transaction() as db:
            db.execute(
                "INSERT INTO migration_archive VALUES (?,?)",
                ("summary", json.dumps({"active_goal": "Migrated"})),
            )
        self.assertEqual(feed.get_feed(self.store)["goal"], "Migrated")
        path = self.path.with_name(self.path.name + ".dashboard.json")
        path.write_text(json.dumps({"goal": None}), encoding="utf-8")
        path.chmod(0o600)
        self.assertEqual(feed.get_feed(self.store)["mode"], "recent")
        self.assertEqual(
            feed.set_preferences(self.store, "FIRST CHOICE"), {"goal": "First choice"}
        )
        self.assertEqual(feed.get_feed(Store(self.path))["goal"], "First choice")
        self.assertEqual(
            feed.get_feed(Store(self.path), goal="Migrated")["goal"], "Migrated"
        )
        feed.set_preferences(self.store, None)
        self.assertEqual(feed.get_feed(Store(self.path))["mode"], "recent")
        path.unlink()
        self.assertEqual(feed.get_feed(Store(self.path))["mode"], "recent")
        with self.assertRaisesRegex(ValueError, "Unknown goal"):
            feed.set_preferences(self.store, "unknown")

    def test_recent_pagination_does_not_duplicate_or_show_expired(self):
        self.import_jobs()
        titles, offset = [], 0
        while offset is not None:
            page = feed.get_feed(self.store, limit=1, offset=offset)
            titles.extend(item["title"] for item in page["items"])
            offset = page["next_offset"]
        self.assertEqual(
            set(titles),
            {"Hydrology researcher", "Conservation research", "Dental technician"},
        )
        self.assertEqual(len(titles), 3)

    def test_hide_keeps_personalized_and_recent_pagination_stable(self):
        self.import_jobs()
        self.store.put_goal(
            "Lab work", {"prefer": ["hydrology", "conservation", "dental"]}
        )
        for selected in ("Lab work", ""):
            before = feed.get_feed(self.store, goal=selected)
            first = feed.get_feed(self.store, goal=selected, limit=1)
            receipt = feed.hide(self.store, first["items"][0]["id"])
            after = feed.get_feed(
                self.store, goal=selected, offset=first["next_offset"]
            )
            self.assertEqual(after["items"], before["items"][1:])
            feed.undo_hide(self.store, receipt["id"], token=receipt["token"])

    def test_hide_and_undo_preserve_saved_note_and_compare_version(self):
        self.import_jobs()
        id = feed.get_feed(self.store, view="explore", query="dental")["items"][0]["id"]
        feed.track(self.store, id, status="reviewed", saved=True, note="Read later")
        receipt = feed.hide(self.store, id)
        hidden = feed.get_feed(self.store, view="hidden")
        self.assertEqual([item["id"] for item in hidden["items"]], [id])
        self.assertEqual(feed.get_feed(self.store, view="saved")["items"], [])
        self.assertNotIn(
            id,
            [item["id"] for item in feed.get_feed(self.store, view="explore")["items"]],
        )
        direct = feed.get_feed(self.store, view="explore", query="dental")["items"]
        self.assertEqual([item["status"] for item in direct], ["skip"])
        with self.assertRaisesRegex(ValueError, "Tracking changed"):
            feed.undo_hide(
                self.store, id, status=receipt["previous_status"], token="old"
            )
        feed.undo_hide(
            self.store, id, status=receipt["previous_status"], token=receipt["token"]
        )
        item = feed.get_feed(self.store, view="saved")["items"][0]
        self.assertEqual(
            (item["id"], item["status"], item["note"]), (id, "reviewed", "Read later")
        )
        feed.hide(self.store, id)
        reopened = Store(self.path)
        self.assertEqual(feed.undo_hide(reopened, id)["status"], "reviewed")
        self.assertEqual(reopened.inspect(id)["note"], "Read later")
        # A later raw CLI decision must not restore an unrelated undo receipt.
        feed.hide(self.store, id)
        reopened.track(id, status="new")
        reopened.track(id, status="skip")
        self.assertEqual(feed.undo_hide(reopened, id)["status"], "new")
        feed.track(self.store, id, status="apply")
        with self.assertRaisesRegex(ValueError, "in Applications"):
            feed.hide(self.store, id)
        self.assertEqual(
            feed.get_feed(self.store, view="applications")["items"][0]["id"], id
        )

    def test_large_payload_is_rejected_and_cli_matches_desktop(self):
        def bridge(op, args=None):
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "radar.desktop_bridge",
                    "--workspace",
                    str(self.path),
                ],
                input=json.dumps({"op": op, "args": args or {}}),
                capture_output=True,
                check=True,
                text=True,
                timeout=5,
            )
            return json.loads(result.stdout)

        self.import_jobs()
        self.assertEqual(bridge("ping"), {"ok": True, "result": {"ready": True}})
        self.assertFalse(bridge("anything")["ok"])
        self.assertTrue(bridge("set_goal", {"name": "Temporary", "rules": {}})["ok"])
        self.assertTrue(bridge("undo_goal", {"name": "Temporary"})["ok"])
        self.assertEqual(bridge("goals")["result"], [])
        self.assertFalse(bridge("feed", {"limit": 100})["ok"])
        self.assertFalse(bridge("feed", {"query": "x" * (2 * 1024 * 1024)})["ok"])
        desktop = bridge("feed", {"view": "explore", "query": "hydrology"})
        cli = subprocess.check_output(
            [
                sys.executable,
                "-m",
                "radar",
                "--workspace",
                str(self.path),
                "--json",
                "feed",
                "hydrology",
                "--view",
                "explore",
            ],
            text=True,
            timeout=5,
        )
        self.assertEqual(json.loads(cli), desktop["result"])

    def test_owner_guard_does_not_break_live_pipe(self):
        with subprocess.Popen(
            [
                sys.executable,
                "-m",
                "radar.desktop_bridge",
                "--workspace",
                str(self.path),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "RADAR_DESKTOP_WATCH_OWNER": "1"},
        ) as process:
            process.stdin.write(b'{"op":"ping"}\n')
            process.stdin.flush()
            try:
                self.assertEqual(process.wait(timeout=5), 0)
                self.assertEqual(json.load(process.stdout)["result"], {"ready": True})
            finally:
                if process.poll() is None:
                    process.kill()
