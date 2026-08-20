import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from monitor.database import Database, SCHEMA_VERSION, _source_content_hash
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

    def test_upsert_preserves_listing_dates_and_provenance_when_feed_turns_sparse(self):
        original = Opportunity(
            "test",
            "dated",
            "Research Fellow",
            "Lab",
            "https://example.com/dated",
            posted_at="2026-07-15T09:30:00+00:00",
            deadline_at="2026-09-30",
            metadata={
                "dates": {
                    "posted": {
                        "state": "present",
                        "kind": "posted",
                        "value": "2026-07-15T09:30:00+00:00",
                        "provenance": "example.published_at",
                        "confidence": "high",
                    },
                    "deadline": {
                        "state": "date",
                        "value": "2026-09-30",
                        "provenance": "example.deadline",
                        "confidence": "high",
                    },
                }
            },
        )
        self.assertEqual(self.database.upsert_opportunity(original), "new")

        sparse = Opportunity(
            "test",
            "dated",
            "Research Fellow",
            "Lab",
            "https://example.com/dated",
            metadata={
                "dates": {
                    "posted": {
                        "state": "unknown",
                        "kind": "posted",
                        "provenance": "example.published_at",
                        "confidence": "low",
                    },
                    "deadline": {
                        "state": "not_listed",
                        "provenance": "none",
                        "confidence": "low",
                    },
                }
            },
        )
        self.assertEqual(self.database.upsert_opportunity(sparse), "unchanged")

        stored = self.database.connection.execute(
            "SELECT posted_at, deadline_at, metadata_json FROM opportunities WHERE external_id='dated'"
        ).fetchone()
        metadata = json.loads(stored["metadata_json"])
        self.assertEqual(stored["posted_at"], "2026-07-15T09:30:00+00:00")
        self.assertEqual(stored["deadline_at"], "2026-09-30")
        self.assertEqual(
            metadata["dates"]["posted"]["provenance"],
            "example.published_at",
        )
        self.assertEqual(
            metadata["dates"]["deadline"]["provenance"],
            "example.deadline",
        )
        rendered = next(
            item
            for item in self.database.dashboard_payload()["opportunities"]
            if item["title"] == "Research Fellow"
        )
        self.assertEqual(
            rendered["dates"]["posted"]["value"],
            "2026-07-15T09:30:00+00:00",
        )

    def test_html_listing_identity_migration_preserves_history_when_page_query_is_removed(self):
        self.database.sync_source(
            {
                "id": "html",
                "name": "HTML Careers",
                "kind": "html_links",
                "url": "https://example.com/careers",
            }
        )
        legacy = Opportunity(
            "html",
            "legacy-page-hash",
            "Software Intern",
            "Example",
            "https://example.com/jobs/123?page=7&ref=careers#details",
        )
        self.assertEqual(
            self.database.upsert_opportunity(
                legacy,
                seen_at="2026-08-01T12:00:00+00:00",
            ),
            "new",
        )
        original = self.database.connection.execute(
            "SELECT id FROM opportunities WHERE source_id='html'"
        ).fetchone()
        self.assertTrue(self.database.set_status(original["id"], "reviewed"))
        self.assertTrue(self.database.set_bookmarked(original["id"], True))

        stable = Opportunity(
            "html",
            "stable-path-hash",
            "Software Intern",
            "Example",
            "https://example.com/jobs/123?ref=careers#details",
        )
        self.assertEqual(
            self.database.upsert_opportunity(
                stable,
                seen_at="2026-08-20T12:00:00+00:00",
            ),
            "updated",
        )

        rows = self.database.connection.execute(
            """
            SELECT id, external_id, url, first_seen_at, last_seen_at,
                   status, bookmarked
            FROM opportunities
            WHERE source_id='html'
            """
        ).fetchall()
        self.assertEqual(len(rows), 1)
        migrated = rows[0]
        self.assertEqual(migrated["id"], original["id"])
        self.assertEqual(migrated["external_id"], "stable-path-hash")
        self.assertEqual(
            migrated["url"],
            "https://example.com/jobs/123?ref=careers#details",
        )
        self.assertEqual(migrated["first_seen_at"], "2026-08-01T12:00:00+00:00")
        self.assertEqual(migrated["last_seen_at"], "2026-08-20T12:00:00+00:00")
        self.assertEqual(migrated["status"], "reviewed")
        self.assertEqual(migrated["bookmarked"], 1)

    def test_html_listing_identity_merge_consolidates_existing_canonical_and_aliases(self):
        self.database.sync_source(
            {
                "id": "html",
                "name": "HTML Careers",
                "kind": "html_links",
                "url": "https://example.com/careers",
            }
        )
        canonical = Opportunity(
            "html",
            "stable-path-hash",
            "Software Intern",
            "Example",
            "https://example.com/jobs/123?ref=careers#details",
        )
        self.database.upsert_opportunity(
            canonical,
            seen_at="2026-08-01T12:00:00+00:00",
        )
        canonical_id = self.database.connection.execute(
            "SELECT id FROM opportunities WHERE external_id='stable-path-hash'"
        ).fetchone()["id"]
        self.assertTrue(self.database.set_status(canonical_id, "reviewed"))
        self.database.mark_source_stale("html", [])

        skipped_alias = Opportunity(
            "html",
            "legacy-page-four",
            "Software Intern",
            "Example",
            "https://example.com/jobs/123?page=4&ref=careers#details",
        )
        applied_alias = Opportunity(
            "html",
            "legacy-page-five",
            "Software Intern",
            "Example",
            "https://example.com/jobs/123?page=5&ref=careers#details",
        )
        unrelated = Opportunity(
            "html",
            "other-page",
            "Research Intern",
            "Example",
            "https://example.com/jobs/124?page=4&ref=careers#details",
        )
        self.database.upsert_opportunity(
            skipped_alias,
            seen_at="2026-08-02T12:00:00+00:00",
        )
        self.database.upsert_opportunity(
            applied_alias,
            seen_at="2026-08-03T12:00:00+00:00",
        )
        self.database.upsert_opportunity(
            unrelated,
            seen_at="2026-08-04T12:00:00+00:00",
        )
        skipped_id = self.database.connection.execute(
            "SELECT id FROM opportunities WHERE external_id='legacy-page-four'"
        ).fetchone()["id"]
        applied_id = self.database.connection.execute(
            "SELECT id FROM opportunities WHERE external_id='legacy-page-five'"
        ).fetchone()["id"]
        self.assertTrue(self.database.set_status(skipped_id, "skip"))
        self.assertTrue(self.database.set_bookmarked(skipped_id, True))
        self.assertTrue(self.database.set_status(applied_id, "applied"))

        self.assertEqual(
            self.database.upsert_opportunity(
                canonical,
                seen_at="2026-08-20T12:00:00+00:00",
            ),
            "unchanged",
        )

        matching = self.database.connection.execute(
            """
            SELECT id, external_id, first_seen_at, status, status_updated_at,
                   applied_at, bookmarked, active
            FROM opportunities
            WHERE url LIKE 'https://example.com/jobs/123%'
            """
        ).fetchall()
        self.assertEqual(len(matching), 1)
        merged = matching[0]
        self.assertEqual(merged["id"], canonical_id)
        self.assertEqual(merged["external_id"], "stable-path-hash")
        self.assertEqual(merged["first_seen_at"], "2026-08-01T12:00:00+00:00")
        self.assertEqual(merged["status"], "applied")
        self.assertIsNotNone(merged["status_updated_at"])
        self.assertIsNotNone(merged["applied_at"])
        self.assertEqual(merged["bookmarked"], 1)
        self.assertEqual(merged["active"], 1)
        self.assertEqual(
            self.database.connection.execute(
                "SELECT COUNT(*) FROM opportunities WHERE external_id='other-page'"
            ).fetchone()[0],
            1,
        )

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
        self.assertEqual(
            self.database.connection.execute("PRAGMA user_version").fetchone()[0],
            SCHEMA_VERSION,
        )

    def test_initialize_rejects_a_newer_schema_without_downgrading_it(self):
        self.database.connection.execute(
            "PRAGMA user_version = {}".format(SCHEMA_VERSION + 1)
        )
        self.database.connection.commit()

        with self.assertRaisesRegex(RuntimeError, "newer than this build supports"):
            self.database.initialize()

        self.assertEqual(
            self.database.connection.execute("PRAGMA user_version").fetchone()[0],
            SCHEMA_VERSION + 1,
        )

    def test_source_hash_ignores_profile_derived_fields(self):
        first = Opportunity(
            "test",
            "derived",
            "Systems Engineer",
            "Example",
            "https://example.com/derived",
            location="Remote",
            metadata={
                "collector": "stable",
                "match": {"fit_score": 40},
                "document_routing": {"provenance": "profile"},
            },
            recommended_resume="General",
            score=40,
            tier="watch",
        )
        self.assertEqual(self.database.upsert_opportunity(first), "new")

        rescored = Opportunity(
            "test",
            "derived",
            "Systems Engineer",
            "Example",
            "https://example.com/derived",
            location="Remote",
            metadata={
                "collector": "stable",
                "match": {"fit_score": 95, "components": [{"id": "preferred"}]},
                "document_routing": {"provenance": "profile"},
            },
            recommended_resume="Technical",
            score=95,
            tier="priority",
            reasons=["Preferred work"],
        )
        self.assertEqual(self.database.upsert_opportunity(rescored), "unchanged")

        rescored.location = "New York"
        self.assertEqual(self.database.upsert_opportunity(rescored), "updated")

    def test_source_hash_tracks_explicit_curated_document_pins(self):
        item = Opportunity(
            "test",
            "curated-pin",
            "Research Fellowship",
            "Example",
            "https://example.com/curated-pin",
            recommended_resume="Academic CV",
            metadata={
                "curated": True,
                "document_routing": {"provenance": "curated_explicit"},
            },
        )
        self.assertEqual(self.database.upsert_opportunity(item), "new")
        item.recommended_resume = "Research Resume"
        self.assertEqual(self.database.upsert_opportunity(item), "updated")

    def test_schema_v5_migrates_legacy_source_hashes(self):
        item = Opportunity(
            "test",
            "migrated",
            "Platform Engineer",
            "Example",
            "https://example.com/migrated",
            metadata={"collector": "stable"},
        )
        self.database.upsert_opportunity(item)
        self.database.connection.execute(
            "UPDATE opportunities SET raw_hash='legacy-derived-hash'"
        )
        self.database.connection.execute("PRAGMA user_version = 4")
        self.database.connection.commit()

        self.database.initialize()

        row = self.database.connection.execute(
            "SELECT raw_hash FROM opportunities WHERE external_id='migrated'"
        ).fetchone()
        self.assertEqual(
            self.database.connection.execute("PRAGMA user_version").fetchone()[0],
            SCHEMA_VERSION,
        )
        self.assertNotEqual(row["raw_hash"], "legacy-derived-hash")
        self.assertEqual(self.database.upsert_opportunity(item), "unchanged")

    def test_schema_v5_hash_migration_rolls_back_as_one_transaction(self):
        for index in range(251):
            self.database.upsert_opportunity(
                Opportunity(
                    "test",
                    "migration-{:03d}".format(index),
                    "Role {:03d}".format(index),
                    "Example",
                    "https://example.com/migration-{:03d}".format(index),
                )
            )
        self.database.connection.execute(
            "UPDATE opportunities SET raw_hash='legacy-' || external_id"
        )
        self.database.connection.execute("PRAGMA user_version = 4")
        self.database.connection.commit()
        calls = 0

        def fail_after_first_batch(values):
            nonlocal calls
            calls += 1
            if calls == 251:
                raise RuntimeError("synthetic migration failure")
            return _source_content_hash(values)

        with (
            patch(
                "monitor.database._source_content_hash",
                side_effect=fail_after_first_batch,
            ),
            self.assertRaisesRegex(RuntimeError, "synthetic migration failure"),
        ):
            self.database.initialize()

        self.assertEqual(
            self.database.connection.execute("PRAGMA user_version").fetchone()[0],
            4,
        )
        remaining = self.database.connection.execute(
            "SELECT COUNT(*) FROM opportunities WHERE raw_hash='legacy-' || external_id"
        ).fetchone()[0]
        self.assertEqual(remaining, 251)

        self.database.initialize()
        self.assertEqual(
            self.database.connection.execute("PRAGMA user_version").fetchone()[0],
            SCHEMA_VERSION,
        )

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

    def test_watch_page_changes_require_two_matching_successes(self):
        self.database.source_success("test", "baseline", 0)

        self.assertFalse(
            self.database.source_watch_success(
                "test",
                "dynamic-one",
                0,
                "Page changed",
                "https://example.com/jobs",
            )
        )
        self.assertFalse(
            self.database.source_watch_success(
                "test",
                "dynamic-two",
                0,
                "Page changed",
                "https://example.com/jobs",
            )
        )
        self.assertFalse(
            self.database.source_watch_success(
                "test",
                "baseline",
                0,
                "Page changed",
                "https://example.com/jobs",
            )
        )
        self.assertFalse(
            self.database.source_watch_success(
                "test",
                "stable-change",
                0,
                "Page changed",
                "https://example.com/jobs",
            )
        )
        self.assertTrue(
            self.database.source_watch_success(
                "test",
                "stable-change",
                0,
                "Page changed",
                "https://example.com/jobs",
            )
        )

        source = self.database.connection.execute(
            """
            SELECT last_content_hash, pending_content_hash,
                   pending_content_checks, last_status
            FROM sources WHERE id='test'
            """
        ).fetchone()
        self.assertEqual(source["last_content_hash"], "stable-change")
        self.assertEqual(source["pending_content_hash"], "")
        self.assertEqual(source["pending_content_checks"], 0)
        self.assertEqual(source["last_status"], "ok")
        events = self.database.connection.execute(
            "SELECT previous_hash, content_hash FROM source_events"
        ).fetchall()
        self.assertEqual(
            [(row["previous_hash"], row["content_hash"]) for row in events],
            [("baseline", "stable-change")],
        )

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
            metadata={
                "collector_internal": "not for the dashboard",
                "dates": {
                    "deadline": {
                        "state": "rolling",
                        "provenance": "text.rolling",
                    }
                },
            },
        )
        self.database.upsert_opportunity(item)
        rendered = next(
            entry
            for entry in self.database.dashboard_payload()["opportunities"]
            if entry["title"] == "Verbose Role"
        )
        self.assertEqual(len(rendered["description"]), 1600)
        self.assertNotIn("external_id", rendered)
        self.assertNotIn("metadata", rendered)
        self.assertEqual(rendered["match"], {})
        self.assertEqual(rendered["dates"]["deadline"]["state"], "rolling")

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

    def test_dashboard_source_payload_includes_its_official_url(self):
        source = next(
            entry
            for entry in self.database.dashboard_payload()["sources"]
            if entry["id"] == "test"
        )
        self.assertEqual(source["url"], "https://example.com/jobs")

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
