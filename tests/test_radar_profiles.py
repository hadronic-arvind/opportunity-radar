"""Profile pack validation and isolated workspace import journeys."""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from radar import feed
from radar.profiles import import_profiles, preview_profiles
from radar.recovery import backup, restore
from radar.store import APPLICATION_ID, SCHEMA_V1, Store


def document(*names, **settings):
    return {
        "version": 1,
        "profiles": [
            {"name": name, "rules": {"prefer": ["scientific computing"]}}
            for name in names
        ],
        **settings,
    }


class ProfileImportTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.store = Store(self.root / "workspace.sqlite3")

    def cli(self, *args, expected=0):
        run = subprocess.run(
            [
                sys.executable,
                "-m",
                "radar",
                "--workspace",
                str(self.store.path),
                "--json",
                *args,
            ],
            cwd=Path(__file__).resolve().parents[1],
            text=True,
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(run.returncode, expected, run.stderr)
        return json.loads(run.stdout) if expected == 0 else run.stderr

    def test_cli_preview_replace_updates_feed_without_changing_tracking(self):
        self.cli("sample")
        listing = self.cli("search", "scientific")[0]["id"]
        self.cli("track", listing, "--save")
        self.store.put_goal("Previous", {"prefer": ["teaching"]})
        self.store.put_goal("Methods", {"prefer": ["hydrology"]})
        feed.set_preferences(self.store, "Previous")
        file = self.root / "pack.json"
        file.write_text(
            json.dumps(document("methods", "Career", active_profile="METHODS"))
        )
        args = ("import-profiles", str(file), "--replace")
        plan = self.cli(*args, "--preview")
        self.assertEqual(plan["active_profile"], "Methods")
        self.assertEqual(plan["conflicts"], ["Methods"])
        self.assertEqual(plan["removed"], ["Previous"])
        self.assertEqual(self.cli("feed")["goal"], "Previous")
        self.assertEqual(len(self.cli("goals")), 2)
        self.cli(*args, "--expected-revision", plan["revision"])
        self.assertEqual(
            [profile["name"] for profile in self.cli("goals")], ["Career", "Methods"]
        )
        result = self.cli("feed")
        self.assertEqual((result["goal"], result["mode"]), ("Methods", "personalized"))
        self.assertTrue(self.cli("inspect", listing)["saved"])
        self.cli("undo-goal", "Previous")
        self.assertEqual(
            [profile["name"] for profile in self.cli("goals")],
            ["Career", "Methods", "Previous"],
        )

    def test_invalid_and_stale_documents_leave_profiles_unchanged(self):
        self.store.put_goal("Existing", {"prefer": ["teaching"]})
        inputs = [
            ('{\n  "version":\n}', "line 3, column 1"),
            (json.dumps(document("Valid", "Same", "SAME")), "Duplicate profile"),
            (
                json.dumps(document("Valid", active_profile="Missing")),
                "must be included",
            ),
            ('{"version":1,"version":1,"profiles":[]}', "duplicate key"),
            (json.dumps(document("Valid", version=True)), "version 1"),
            (json.dumps(document("Invalid\nname")), "control characters"),
        ]
        for text, error in inputs:
            with self.subTest(error=error), self.assertRaisesRegex(ValueError, error):
                import_profiles(self.store, text=text, replace=True)
        file = self.root / "pack.json"
        file.write_text(json.dumps(document("Valid")))
        plan = preview_profiles(self.store, path=file, replace=True)
        file.write_text(json.dumps(document("Changed")))
        with self.assertRaisesRegex(ValueError, "changed"):
            import_profiles(
                self.store, path=file, replace=True, expected_revision=plan["revision"]
            )
        self.assertEqual(
            [profile["name"] for profile in self.store.goals()], ["Existing"]
        )
        plan = preview_profiles(self.store, path=file, replace=True)
        feed.set_preferences(self.store, None)
        with self.assertRaisesRegex(ValueError, "changed"):
            import_profiles(
                self.store, path=file, replace=True, expected_revision=plan["revision"]
            )

    def test_merge_selection_omission_and_transaction_rollback(self):
        self.store.put_goal("Existing", {"prefer": ["teaching"]})
        feed.set_preferences(self.store, "Existing")
        import_profiles(self.store, text=json.dumps(document("Added")))
        self.assertEqual(feed.get_feed(self.store)["goal"], "Existing")
        before = self.store.goals()
        text = json.dumps(document("New", active_profile=None))
        with patch(
            "radar.profiles.set_preferences", side_effect=sqlite3.OperationalError
        ):
            with self.assertRaises(sqlite3.OperationalError):
                import_profiles(self.store, text=text, replace=True)
        self.assertEqual(self.store.goals(), before)
        self.assertEqual(feed.get_feed(self.store)["goal"], "Existing")
        import_profiles(self.store, text=text)
        self.assertIsNone(feed.get_feed(self.store)["goal"])
        self.assertEqual(len(self.store.goals()), 3)

    def test_v1_restore_and_upgrade_keep_goals_and_new_preference_is_backed_up(self):
        v1 = self.root / "v1.sqlite"
        with closing(sqlite3.connect(v1)) as old, old:
            old.executescript(SCHEMA_V1)
            old.execute(f"PRAGMA application_id = {APPLICATION_ID}")
            old.execute("PRAGMA user_version = 1")
            old.execute(
                "INSERT INTO goals VALUES (?,?)",
                ("Previous", '{"prefer":["teaching"]}'),
            )
        v1.chmod(0o600)
        separate_v1 = self.root / "older-workspace.sqlite"
        separate_v1.write_bytes(v1.read_bytes())
        separate_v1.chmod(0o600)
        settings = self.root / (separate_v1.name + ".dashboard.json")
        settings.write_text('{"goal":null}')
        settings.chmod(0o600)
        self.assertIsNone(feed.get_feed(Store(separate_v1))["goal"])
        restore(self.store, v1)
        self.assertEqual(feed.get_feed(self.store)["goal"], "Previous")
        import_profiles(
            self.store, text=json.dumps(document("New", active_profile=None))
        )
        snapshot = self.root / "v2.sqlite"
        backup(self.store, snapshot)
        fresh = Store(self.root / "restored.sqlite")
        restore(fresh, snapshot)
        self.assertIsNone(feed.get_feed(fresh)["goal"])
        self.assertEqual(len(fresh.goals()), 2)


if __name__ == "__main__":
    unittest.main()
