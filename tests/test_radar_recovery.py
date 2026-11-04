import hashlib
import json
import os
import signal
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path

from monitor.database import Database as LegacyDatabase
from radar.locking import exclusive_work
from radar.recovery import backup, migrate, restore
from radar.store import Store


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = Store(self.root / "workspace.sqlite")

    def _workspace(self):
        self.store.add_source({"id": "manual", "name": "Manual", "kind": "manual"})
        self.store.ingest("manual", [
            {"external_id": "test", "title": "Training in data ethics", "organization": "University"},
        ], "imported", "fixture")
        listing = self.store.search("ethics")[0]["id"]
        self.store.track(listing, status="apply", saved=True, note="Original decision")
        self.store.put_goal("Research", {"prefer": ["data ethics"]})
        return listing

    def _legacy(self, version=7):
        directory = self.root / "old"
        database = directory / "data" / "opportunities.sqlite3"
        config = directory / "config"
        config.mkdir(parents=True)
        legacy = LegacyDatabase(database)
        try:
            legacy.initialize()
            with legacy.connection as db:
                db.execute(
                    "INSERT INTO sources(id,name,kind,url) VALUES (?,?,?,?)",
                    ("old_feed", "Old feed", "unsupported", "https://example.org/jobs"),
                )
                db.execute("""
                    INSERT INTO opportunities(
                        id,source_id,external_id,title,organization,url,description,
                        first_seen_at,last_seen_at,status,status_updated_at,applied_at,bookmarked,
                        active,raw_hash,recommended_resume
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    "old-id", "old_feed", "old-external-id", "Old Training", "Library",
                    "https://example.org/item?token=private-synthetic-value", "Teach data ethics",
                    "2023-02-09T12:00:00Z", "2023-02-10T12:00:00Z", "applied",
                    "2023-02-11T12:00:00Z", "2023-02-11T12:00:00Z", 1, 0, "hash",
                    "local-private-document-name",
                ))
                db.execute(f"PRAGMA user_version = {version}")
        finally:
            legacy.close()
        profiles = {
            "schema_version": 1,
            "active_profile_id": "legacy",
            "profiles": [{
                "id": "legacy",
                "name": "Local library",
                "profile": {"targets": {"role_families": ["education"], "exclusions": ["sales"]}},
                "sources": {"sources": [{
                    "id": "separate_feed", "kind": "syndication", "name": "Separate feed",
                    "url": "https://example.org/rss", "enabled": False,
                }]},
            }],
        }
        (config / "profiles.local.json").write_text(json.dumps(profiles), encoding="utf-8")
        return database, config

    def test_backup_restore_preserves_snapshot_and_decisions(self):
        listing = self._workspace()
        snapshot = self.root / "backup.sqlite"
        backup(self.store, snapshot)
        self.assertEqual(stat.S_IMODE(snapshot.stat().st_mode), 0o600)
        before = fingerprint(snapshot)
        with self.assertRaises(FileExistsError):
            backup(self.store, snapshot)
        self.store.track(listing, status="skip", saved=False, note="Changed decision")
        self.store.put_goal("Research", {"prefer": ["finance"]})
        restore(self.store, snapshot)
        self.assertEqual(fingerprint(snapshot), before)
        entry = self.store.inspect(listing)
        self.assertEqual((entry["status"], entry["saved"], entry["note"]),
                         ("apply", True, "Original decision"))
        self.assertEqual(self.store.goals()[0]["rules"]["prefer"], ["data ethics"])
        self.assertEqual(self.store.search("ethics")[0]["id"], listing)
        fresh = Store(self.root / "new-location" / "new.sqlite")
        restore(fresh, snapshot)
        self.assertEqual(fresh.inspect(listing)["note"], "Original decision")

    def test_restore_rejects_modified_schema_and_leaves_target_usable(self):
        listing = self._workspace()
        snapshot = self.root / "backup.sqlite"
        backup(self.store, snapshot)
        with closing(sqlite3.connect(snapshot)) as db, db:
            db.execute("CREATE TRIGGER malicious AFTER INSERT ON decisions BEGIN DELETE FROM goals; END")
        with self.assertRaisesRegex(ValueError, "schema"):
            restore(self.store, snapshot)
        self.assertEqual(self.store.inspect(listing)["status"], "apply")
        self.assertEqual(self.store.goals()[0]["name"], "Research")

    def test_restore_rejects_incomplete_copy_and_malformed_rows(self):
        listing = self._workspace()
        snapshot = self.root / "backup.sqlite"
        backup(self.store, snapshot)
        journal = snapshot.with_name(snapshot.name + "-journal")
        journal.write_bytes(b"an incomplete copy")
        with self.assertRaisesRegex(ValueError, "standalone"):
            restore(self.store, snapshot)
        journal.unlink()
        with closing(sqlite3.connect(snapshot)) as db, db:
            db.execute("UPDATE goals SET rules='garbage'")
        with self.assertRaisesRegex(ValueError, "malformed"):
            restore(self.store, snapshot)
        self.assertEqual(self.store.inspect(listing)["status"], "apply")

    def test_busy_restore_times_out_and_preserves_the_committed_workspace(self):
        listing = self._workspace()
        snapshot = self.root / "backup.sqlite"
        backup(self.store, snapshot)
        self.store.track(listing, status="reviewed")
        with closing(self.store.connect()) as reader:
            reader.execute("BEGIN")
            reader.execute("SELECT count(*) FROM listings").fetchone()
            started = time.monotonic()
            with self.assertRaisesRegex(TimeoutError, "busy"):
                restore(self.store, snapshot)
            self.assertLess(time.monotonic() - started, 4.0)
        self.assertEqual(self.store.inspect(listing)["status"], "reviewed")
        restore(self.store, snapshot)
        self.assertEqual(self.store.inspect(listing)["status"], "apply")

    def test_state_replacement_waits_for_collection_but_backup_is_available(self):
        self._workspace()
        legacy, config = self._legacy()
        snapshot = self.root / "backup.sqlite"
        with exclusive_work(self.store):
            backup(self.store, snapshot)
            with self.assertRaisesRegex(ValueError, "Another collection"):
                restore(self.store, snapshot)
            with self.assertRaisesRegex(ValueError, "Another collection"):
                migrate(self.store, legacy, config)
        self.assertTrue(restore(self.store, snapshot)["restored"])

    def test_migration_preserves_legacy_records_and_is_repeatable(self):
        legacy, config = self._legacy()
        before = fingerprint(legacy), fingerprint(config / "profiles.local.json")
        result = migrate(self.store, legacy, config)
        self.assertEqual(result["active_goal"], "Local library")
        self.assertEqual((result["listings"], result["sources"], result["profiles"]), (1, 2, 1))
        self.assertEqual(result["inactive_adapters"], 1)
        entry = self.store.inspect("old-id")
        self.assertEqual(entry["status"], "applied")
        self.assertTrue(entry["saved"])
        self.assertEqual(entry["applied_at"], "2023-02-11T12:00:00Z")
        self.assertEqual(entry["status_updated_at"], "2023-02-11T12:00:00Z")
        self.assertEqual(entry["first_seen"], "2023-02-09T12:00:00Z")
        self.assertEqual(entry["observations"][0]["observed_at"], "2023-02-10T12:00:00Z")
        self.assertEqual(entry["url"], "")
        self.assertIn("previously inactive", entry["availability"])
        self.assertEqual(self.store.goals()[0]["rules"]["prefer"], ["education"])
        self.assertEqual(self.store.goals()[0]["rules"]["avoid"], ["sales"])
        with closing(self.store.connect()) as db:
            payload = db.execute(
                "SELECT payload FROM migration_archive WHERE name='listing:old-id'"
            ).fetchone()[0]
        self.assertIn("private-synthetic-value", payload)
        self.assertIn("local-private-document-name", payload)
        self.assertEqual(before, (fingerprint(legacy), fingerprint(config / "profiles.local.json")))
        self.store.track("old-id", status="skip", saved=False, note="Local edit")
        self.assertTrue(migrate(self.store, legacy)["already_migrated"])
        self.assertEqual(self.store.inspect("old-id")["note"], "Local edit")
        self.assertEqual(len(self.store.search("training")), 1)

    def test_existing_workspace_refuses_legacy_import(self):
        listing = self._workspace()
        legacy, config = self._legacy()
        with self.assertRaisesRegex(ValueError, "empty"):
            migrate(self.store, legacy, config)
        self.assertEqual(self.store.inspect(listing)["note"], "Original decision")

    def test_migration_can_replace_pristine_starters(self):
        from radar.feed import get_feed, set_preferences

        store = Store(self.store.path, starter_profiles=True)
        legacy, config = self._legacy()
        starters = store.goals()
        self.assertEqual(len(starters), 6)
        set_preferences(store, starters[0]["name"])
        with self.assertRaisesRegex(ValueError, "empty"):
            migrate(store, legacy, config)
        self.assertEqual(store.goals(), starters)

        set_preferences(store, None)
        name = starters[0]["name"]
        store.put_goal(name, {"prefer": ["my own topic"]})
        with self.assertRaisesRegex(ValueError, "empty"):
            migrate(store, legacy, config)
        self.assertEqual(store.goals()[0]["rules"]["prefer"], ["my own topic"])

        store.undo_goal(name)
        self.assertEqual(migrate(store, legacy, config)["active_goal"], "Local library")
        self.assertEqual(get_feed(store)["goal"], "Local library")
        self.assertEqual([profile["name"] for profile in store.goals()], ["Local library"])

    def test_sqlite_page_exhaustion_rolls_back_migration(self):
        class OutOfSpaceStore(Store):
            def ingest(self, source_id, rows, state, message, conn=None):
                super().ingest(source_id, rows, state, message, conn=conn)
                count = conn.execute("PRAGMA page_count").fetchone()[0]
                size = conn.execute("PRAGMA page_size").fetchone()[0]
                conn.execute(f"PRAGMA max_page_count = {count}")
                conn.execute(
                    "INSERT INTO migration_archive VALUES ('space probe', zeroblob(?))",
                    (count * size,),
                )

        legacy, config = self._legacy()
        with self.assertRaisesRegex(sqlite3.OperationalError, "full"):
            migrate(OutOfSpaceStore(self.store.path), legacy, config)
        self.assertEqual(self.store.search(), [])
        self.assertEqual(self.store.sources(), [])
        self.assertEqual(self.store.goals(), [])
        self.assertEqual(migrate(self.store, legacy, config)["listings"], 1)

    @unittest.skipUnless(hasattr(signal, "SIGKILL"), "requires Unix process signals")
    def test_killed_migration_rolls_back_before_retry(self):
        legacy, config = self._legacy(version=3)
        script = """
import os, signal, sys
from radar.recovery import migrate
from radar.store import Store

class InterruptedStore(Store):
    def ingest(self, *args, **kwargs):
        super().ingest(*args, **kwargs)
        os.kill(os.getpid(), signal.SIGKILL)

migrate(InterruptedStore(sys.argv[1]), sys.argv[2], sys.argv[3])
"""
        result = subprocess.run(
            [sys.executable, "-c", script, str(self.store.path), str(legacy), str(config)],
            capture_output=True, timeout=20,
        )
        self.assertEqual(result.returncode, -signal.SIGKILL, result.stderr.decode())
        self.assertEqual(self.store.search(), [])
        self.assertEqual(self.store.goals(), [])
        self.assertEqual(self.store.sources(), [])
        self.assertEqual(migrate(self.store, legacy, config)["listings"], 1)
        self.assertEqual(self.store.inspect("old-id")["status"], "applied")
