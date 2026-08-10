import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from monitor.database import Database
from monitor.models import Opportunity


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tempdir.name) / "test.sqlite3")
        self.database.initialize()
        self.database.sync_source(
            {
                "id": "test",
                "name": "Test Source",
                "kind": "watch_page",
                "url": "https://example.com/jobs",
                "category": "test",
                "cadence_hours": 12,
            }
        )

    def tearDown(self):
        self.database.close()
        self.tempdir.cleanup()

    def test_upsert_preserves_workflow_status(self):
        item = Opportunity("test", "one", "Systems Intern", "Lab", "https://example.com/one")
        self.assertEqual(self.database.upsert_opportunity(item, seen_at="2026-01-01T00:00:00+00:00"), "new")
        row = self.database.connection.execute("SELECT id, first_seen_at FROM opportunities").fetchone()
        original_id = row["id"]
        self.assertTrue(self.database.set_status(row["id"], "apply"))
        self.database.connection.execute("UPDATE opportunities SET active=0 WHERE id=?", (original_id,))
        self.database.connection.commit()
        item.location = "Seattle"
        self.assertEqual(
            self.database.upsert_opportunity(item, seen_at="2026-01-02T00:00:00+00:00"), "updated"
        )
        stored = self.database.connection.execute(
            "SELECT id, status, location, first_seen_at, last_seen_at, active FROM opportunities"
        ).fetchone()
        self.assertEqual(stored["id"], original_id)
        self.assertEqual(stored["status"], "apply")
        self.assertEqual(stored["location"], "Seattle")
        self.assertEqual(stored["first_seen_at"], "2026-01-01T00:00:00+00:00")
        self.assertEqual(stored["last_seen_at"], "2026-01-02T00:00:00+00:00")
        self.assertEqual(stored["active"], 1)

    def test_marks_only_missing_source_items_inactive(self):
        first = Opportunity("test", "one", "First", "Lab", "https://example.com/one")
        second = Opportunity("test", "two", "Second", "Lab", "https://example.com/two")
        self.database.upsert_opportunity(first)
        self.database.upsert_opportunity(second)
        self.database.mark_source_stale("test", ["two"])
        rows = self.database.connection.execute("SELECT external_id, active FROM opportunities ORDER BY external_id").fetchall()
        self.assertEqual([(row["external_id"], row["active"]) for row in rows], [("one", 0), ("two", 1)])

    def test_initialize_is_idempotent_and_sets_schema_version(self):
        self.database.initialize()
        self.assertEqual(self.database.connection.execute("PRAGMA user_version").fetchone()[0], 3)

    def test_prune_history_preserves_bookmarks_and_applications(self):
        for external_id in ("old", "saved", "applied"):
            self.database.upsert_opportunity(
                Opportunity(
                    "test",
                    external_id,
                    external_id.title(),
                    "Example",
                    "https://example.com/{}".format(external_id),
                ),
                seen_at="2020-01-01T00:00:00+00:00",
            )
        rows = self.database.connection.execute(
            "SELECT id, external_id FROM opportunities"
        ).fetchall()
        identifiers = {row["external_id"]: row["id"] for row in rows}
        self.database.connection.execute("UPDATE opportunities SET active=0")
        self.database.connection.commit()
        self.database.set_bookmarked(identifiers["saved"], True)
        self.database.set_status(identifiers["applied"], "applied")
        removed = self.database.prune_history(retention_days=30)
        self.assertEqual(removed["opportunities"], 1)
        remaining = {
            row[0]
            for row in self.database.connection.execute(
                "SELECT external_id FROM opportunities"
            ).fetchall()
        }
        self.assertEqual(remaining, {"saved", "applied"})

    def test_source_change_events_are_atomic_and_included_in_dashboard(self):
        self.assertTrue(
            self.database.source_change_success(
                "test",
                "hash-zero",
                "hash-one",
                0,
                "Page changed: Test Source",
                "https://example.com/jobs",
            )
        )
        self.assertTrue(
            self.database.source_change_success(
                "test",
                "hash-one",
                "hash-zero",
                0,
                "Page changed: Test Source",
                "https://example.com/jobs",
            )
        )
        events = self.database.dashboard_payload()["events"]
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["source_id"], "test")
        source = self.database.connection.execute(
            "SELECT last_status, last_content_hash FROM sources WHERE id='test'"
        ).fetchone()
        self.assertEqual(source["last_status"], "ok")
        self.assertEqual(source["last_content_hash"], "hash-zero")

    def test_workflow_metadata_and_inactive_applications_are_preserved(self):
        item = Opportunity("test", "tracked", "Research Role", "Lab", "https://example.com/tracked")
        self.database.upsert_opportunity(item, seen_at="2026-01-01T00:00:00+00:00")
        row = self.database.connection.execute(
            "SELECT id FROM opportunities WHERE external_id='tracked'"
        ).fetchone()
        self.assertTrue(self.database.set_bookmarked(row["id"], True))
        self.assertTrue(self.database.set_status(row["id"], "applied"))
        self.database.connection.execute(
            "UPDATE opportunities SET active=0 WHERE id=?", (row["id"],)
        )
        self.database.connection.commit()
        payload = self.database.dashboard_payload()
        tracked = next(entry for entry in payload["opportunities"] if entry["id"] == row["id"])
        self.assertEqual(tracked["status"], "applied")
        self.assertEqual(tracked["bookmarked"], 1)
        self.assertIsNotNone(tracked["status_updated_at"])
        self.assertIsNotNone(tracked["applied_at"])
        self.assertEqual(payload["counts"]["applied"], 1)

    def test_source_error_preserves_last_success_state(self):
        self.database.source_success("test", "content-hash", 3)
        self.database.source_error("test", "temporary failure")
        row = self.database.connection.execute(
            "SELECT last_success_at, last_content_hash, item_count, last_status FROM sources WHERE id='test'"
        ).fetchone()
        self.assertIsNotNone(row["last_success_at"])
        self.assertEqual(row["last_content_hash"], "content-hash")
        self.assertEqual(row["item_count"], 3)
        self.assertEqual(row["last_status"], "error")

    def test_dashboard_payload_is_bounded_and_excludes_raw_collector_state(self):
        item = Opportunity(
            "test",
            "verbose",
            "Verbose Role",
            "Lab",
            "https://example.com/verbose",
            description="x" * 5000,
            metadata={"collector_internal": "not for the dashboard"},
        )
        self.database.upsert_opportunity(item)
        rendered = next(
            entry
            for entry in self.database.dashboard_payload()["opportunities"]
            if entry["title"] == "Verbose Role"
        )
        self.assertEqual(len(rendered["description"]), 3000)
        self.assertNotIn("external_id", rendered)
        self.assertNotIn("metadata", rendered)
        self.assertEqual(rendered["match"], {})

    def test_dashboard_cap_preserves_application_records(self):
        identifiers = {}
        for external_id in ("one", "two", "three", "applied"):
            self.database.upsert_opportunity(
                Opportunity(
                    "test",
                    external_id,
                    external_id.title(),
                    "Example",
                    "https://example.com/{}".format(external_id),
                )
            )
        for row in self.database.connection.execute(
            "SELECT id, external_id FROM opportunities"
        ).fetchall():
            identifiers[row["external_id"]] = row["id"]
        self.database.set_status(identifiers["applied"], "applied")
        with patch("monitor.database.MAX_DASHBOARD_DISCOVERY_ITEMS", 2):
            payload = self.database.dashboard_payload()
        self.assertTrue(payload["display"]["discovery_truncated"])
        self.assertEqual(payload["display"]["discovery_total"], 3)
        self.assertEqual(len(payload["opportunities"]), 3)
        self.assertIn("applied", {item["status"] for item in payload["opportunities"]})

    def test_dashboard_cap_preserves_active_and_inactive_bookmarks(self):
        identifiers = {}
        for external_id, score in (
            ("high-one", 100),
            ("high-two", 90),
            ("low-saved", 1),
            ("inactive-saved", 2),
        ):
            self.database.upsert_opportunity(
                Opportunity(
                    "test",
                    external_id,
                    external_id.title(),
                    "Example",
                    "https://example.com/{}".format(external_id),
                    score=score,
                )
            )
        for row in self.database.connection.execute(
            "SELECT id, external_id FROM opportunities"
        ).fetchall():
            identifiers[row["external_id"]] = row["id"]
        self.database.set_bookmarked(identifiers["low-saved"], True)
        self.database.set_bookmarked(identifiers["inactive-saved"], True)
        self.database.mark_source_stale(
            "test", ["high-one", "high-two", "low-saved"]
        )

        with patch("monitor.database.MAX_DASHBOARD_DISCOVERY_ITEMS", 1):
            payload = self.database.dashboard_payload()

        rendered = {item["id"]: item for item in payload["opportunities"]}
        self.assertIn(identifiers["low-saved"], rendered)
        self.assertIn(identifiers["inactive-saved"], rendered)
        self.assertFalse(rendered[identifiers["inactive-saved"]]["active"])
        self.assertEqual(payload["counts"]["bookmarked"], 2)
        self.assertTrue(payload["display"]["discovery_truncated"])

    def test_disabled_source_is_hidden_without_deleting_history(self):
        self.database.upsert_opportunity(
            Opportunity("test", "one", "Research Intern", "Lab", "https://example.com/one")
        )
        self.database.sync_source(
            {
                "id": "test",
                "name": "Test Source",
                "kind": "watch_page",
                "url": "https://example.com/jobs",
                "enabled": False,
            }
        )
        payload = self.database.dashboard_payload()
        self.assertEqual(payload["opportunities"], [])
        self.assertEqual(payload["sources"], [])
        self.assertEqual(
            self.database.connection.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0], 1
        )

    def test_blocked_and_failed_sources_respect_cadence(self):
        self.assertTrue(self.database.source_due("test"))
        self.database.source_blocked("test", "expected denial")
        self.assertFalse(self.database.source_due("test"))
        self.database.connection.execute(
            "UPDATE sources SET last_checked_at='2020-01-01T00:00:00+00:00' WHERE id='test'"
        )
        self.database.connection.commit()
        self.assertTrue(self.database.source_due("test"))
        self.database.source_error("test", "temporary failure")
        self.assertFalse(self.database.source_due("test"))


if __name__ == "__main__":
    unittest.main()
