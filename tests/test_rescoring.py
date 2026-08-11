import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from monitor import cli, pipeline
from monitor.database import Database, PROFILE_FINGERPRINT_KEY
from monitor.models import FetchResult, Opportunity
from monitor.scoring import profile_fingerprint, score_opportunity


def matching_profile(
    base_score=30,
    term="python",
    document="Technical",
    default_document="General",
):
    return {
        "polite_delay_seconds": 0,
        "priority_organizations": [],
        "matching": {
            "base_score": base_score,
            "priority_organization_bonus": 10,
            "tier_thresholds": {"priority": 80, "strong": 60, "watch": 25},
            "rules": [
                {
                    "id": "preferred_work",
                    "label": "Preferred work",
                    "weight": 20,
                    "fields": ["title", "description"],
                    "terms": [term],
                }
            ],
        },
        "documents": {
            "default": default_document,
            "routes": [
                {
                    "label": document,
                    "fields": ["title", "description", "category"],
                    "terms": [term],
                }
            ],
        },
    }


class ProfileRescoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.database_path = self.root / "data" / "opportunities.sqlite3"
        self.database = Database(self.database_path)
        self.database.initialize()
        self.source = {
            "id": "test",
            "name": "Example Organization",
            "kind": "watch_page",
            "url": "https://example.com/jobs",
            "cadence_hours": 12,
            "enabled": True,
        }
        self.database.sync_source(self.source)

    def tearDown(self):
        self.database.close()
        self.tempdir.cleanup()

    def add_item(
        self,
        external_id,
        title="Python Engineer",
        recommended_resume="Old route",
        metadata=None,
    ):
        item = Opportunity(
            "test",
            external_id,
            title,
            "Example Organization",
            "https://example.com/jobs/{}".format(external_id),
            description="Reliable systems work",
            recommended_resume=recommended_resume,
            metadata=metadata or {"collector": "preserve"},
        )
        self.database.upsert_opportunity(item, seen_at="2028-01-02T03:04:05+00:00")
        return self.database.connection.execute(
            "SELECT id FROM opportunities WHERE external_id=?", (external_id,)
        ).fetchone()["id"]

    def test_schema_upgrade_creates_private_fingerprint_state(self):
        self.database.connection.execute("DROP TABLE runtime_state")
        self.database.connection.execute("PRAGMA user_version = 3")
        self.database.connection.commit()
        self.database.initialize()
        version = self.database.connection.execute("PRAGMA user_version").fetchone()[0]
        table = self.database.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='runtime_state'"
        ).fetchone()
        self.assertEqual(version, 5)
        self.assertEqual(table["name"], "runtime_state")

    def test_fingerprint_canonicalizes_current_and_legacy_equivalents(self):
        rule = {
            "id": "preferred",
            "label": "Preferred",
            "weight": 12,
            "terms": ["Python"],
        }
        negative_rule = {
            "id": "excluded",
            "label": "Excluded",
            "weight": -8,
            "terms": ["commission only"],
        }
        current = {
            "priority_organizations": ["Example Lab", "Second Org"],
            "matching": {
                "base_score": 40,
                "rules": [dict(rule), dict(negative_rule)],
            },
            "documents": {
                "default": "General",
                "routes": [{"label": "Research", "terms": ["research"]}],
            },
        }
        legacy = {
            "priority_organizations": ["second org", "example lab"],
            "matching": {"base_score": 40},
            "positive_rules": [dict(rule)],
            "negative_rules": [dict(negative_rule, weight=8)],
            "default_resume_code": "General",
            "resume_routing": [{"code": "Research", "terms": ["research"]}],
        }
        self.assertEqual(profile_fingerprint(current), profile_fingerprint(legacy))
        legacy["positive_rules"][0]["weight"] = 13
        self.assertNotEqual(profile_fingerprint(current), profile_fingerprint(legacy))

    def test_fingerprint_tracks_derived_explanations_but_normalizes_route_terms(self):
        profile = matching_profile()
        route_recased = matching_profile()
        route_recased["documents"]["routes"][0]["terms"] = ["PYTHON"]
        self.assertEqual(profile_fingerprint(profile), profile_fingerprint(route_recased))

        relabeled = matching_profile()
        relabeled["matching"]["rules"][0]["label"] = "Preferred engineering work"
        self.assertNotEqual(profile_fingerprint(profile), profile_fingerprint(relabeled))

        recased_evidence = matching_profile(term="Python")
        self.assertNotEqual(profile_fingerprint(profile), profile_fingerprint(recased_evidence))

    def test_fingerprint_changes_when_scoring_semantics_change(self):
        profile = matching_profile()
        current = profile_fingerprint(profile)
        with patch("monitor.scoring.SCORING_SCHEMA_VERSION", 2):
            revised = profile_fingerprint(profile)
        self.assertNotEqual(current, revised)

    def test_matching_fingerprint_no_op_skips_every_row(self):
        self.add_item("one")
        profile = matching_profile()
        first = self.database.rescore_for_profile(profile)
        self.assertTrue(first["changed"])
        self.assertEqual(first["rescored"], 1)
        stored = self.database.connection.execute(
            "SELECT value FROM runtime_state WHERE key=?", (PROFILE_FINGERPRINT_KEY,)
        ).fetchone()["value"]
        self.assertEqual(stored, profile_fingerprint(profile))

        changes_before = self.database.connection.total_changes
        with patch(
            "monitor.database.score_opportunity",
            side_effect=AssertionError("unchanged profiles must not rescore"),
        ):
            second = self.database.rescore_for_profile(profile)
        self.assertEqual(
            second,
            {"changed": False, "rescored": 0, "fingerprint": stored},
        )
        self.assertEqual(self.database.connection.total_changes, changes_before)

    def test_rescore_selects_active_and_inactive_tracked_rows_only(self):
        identifiers = {
            name: self.add_item(name)
            for name in ("active", "saved", "preparing", "applied", "untracked")
        }
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE opportunities SET active=0 WHERE external_id != 'active'"
            )
            connection.execute(
                "UPDATE opportunities SET bookmarked=1 WHERE external_id='saved'"
            )
            connection.execute(
                "UPDATE opportunities SET status='apply' WHERE external_id='preparing'"
            )
            connection.execute(
                "UPDATE opportunities SET status='applied' WHERE external_id='applied'"
            )

        result = self.database.rescore_for_profile(matching_profile())
        self.assertEqual(result["rescored"], 4)
        scores = {
            row["external_id"]: row["score"]
            for row in self.database.connection.execute(
                "SELECT external_id, score FROM opportunities"
            )
        }
        for tracked in ("active", "saved", "preparing", "applied"):
            self.assertEqual(scores[tracked], 50, identifiers[tracked])
        self.assertEqual(scores["untracked"], 0)

    def test_document_routes_refresh_but_curated_pins_are_preserved(self):
        auto_id = self.add_item(
            "auto",
            title="Python Research Engineer",
            metadata={
                "collector": "preserve",
                "document_routing": {
                    "provenance": "profile",
                    "collector_note": "preserve",
                },
            },
        )
        pinned_id = self.add_item(
            "pinned",
            title="Python Research Engineer",
            recommended_resume="Pinned CV",
            metadata={
                "curated": True,
                "document_routing": {"provenance": "curated_explicit"},
            },
        )
        legacy_id = self.add_item(
            "legacy-pin",
            title="Python Research Engineer",
            recommended_resume="Legacy pinned CV",
            metadata={"curated": True},
        )
        source_claim_id = self.add_item(
            "source-claim",
            title="Python Research Engineer",
            recommended_resume="Source-selected CV",
            metadata={
                "collector": "preserve",
                "document_routing": {"provenance": "curated_explicit"},
            },
        )

        self.database.rescore_for_profile(
            matching_profile(term="research", document="Research CV")
        )
        rows = {
            row["id"]: row
            for row in self.database.connection.execute(
                "SELECT id, recommended_resume, metadata_json FROM opportunities"
            )
        }
        self.assertEqual(rows[auto_id]["recommended_resume"], "Research CV")
        self.assertEqual(rows[pinned_id]["recommended_resume"], "Pinned CV")
        self.assertEqual(rows[legacy_id]["recommended_resume"], "Legacy pinned CV")
        self.assertEqual(rows[source_claim_id]["recommended_resume"], "Research CV")
        auto_metadata = json.loads(rows[auto_id]["metadata_json"])
        pinned_metadata = json.loads(rows[pinned_id]["metadata_json"])
        legacy_metadata = json.loads(rows[legacy_id]["metadata_json"])
        self.assertEqual(auto_metadata["document_routing"]["provenance"], "profile")
        self.assertEqual(
            auto_metadata["document_routing"]["collector_note"], "preserve"
        )
        self.assertEqual(
            pinned_metadata["document_routing"]["provenance"], "curated_explicit"
        )
        self.assertEqual(
            legacy_metadata["document_routing"]["provenance"], "curated_legacy"
        )

    def test_successful_rescore_preserves_non_derived_state(self):
        opportunity_id = self.add_item(
            "preserved",
            metadata={
                "collector": "keep me",
                "nested": {"source_fact": 7},
            },
        )
        self.database.set_status(opportunity_id, "applied")
        self.database.set_bookmarked(opportunity_id, True)
        self.database.source_success("test", "source-hash", 1)
        before = dict(
            self.database.connection.execute(
                "SELECT * FROM opportunities WHERE id=?", (opportunity_id,)
            ).fetchone()
        )
        source_before = dict(
            self.database.connection.execute(
                "SELECT * FROM sources WHERE id='test'"
            ).fetchone()
        )

        self.database.rescore_for_profile(matching_profile(base_score=40))
        after = dict(
            self.database.connection.execute(
                "SELECT * FROM opportunities WHERE id=?", (opportunity_id,)
            ).fetchone()
        )
        source_after = dict(
            self.database.connection.execute(
                "SELECT * FROM sources WHERE id='test'"
            ).fetchone()
        )
        preserved_fields = (
            "source_id",
            "external_id",
            "title",
            "organization",
            "location",
            "url",
            "description",
            "category",
            "opportunity_type",
            "posted_at",
            "deadline_at",
            "first_seen_at",
            "last_seen_at",
            "status",
            "status_updated_at",
            "applied_at",
            "bookmarked",
            "commitment",
            "eligibility",
            "raw_hash",
            "active",
        )
        for field in preserved_fields:
            self.assertEqual(after[field], before[field], field)
        metadata = json.loads(after["metadata_json"])
        self.assertEqual(metadata["collector"], "keep me")
        self.assertEqual(metadata["nested"], {"source_fact": 7})
        self.assertEqual(metadata["match"]["fit_score"], 60)
        self.assertEqual(source_after, source_before)

    def test_refetch_after_rescore_is_not_reported_as_a_source_update(self):
        self.add_item("unchanged-after-rescore")
        profile = matching_profile(base_score=45, document="Python Resume")
        self.database.rescore_for_profile(profile)

        refetched = Opportunity(
            "test",
            "unchanged-after-rescore",
            "Python Engineer",
            "Example Organization",
            "https://example.com/jobs/unchanged-after-rescore",
            description="Reliable systems work",
            metadata={"collector": "preserve"},
        )
        score_opportunity(refetched, profile)

        self.assertEqual(self.database.upsert_opportunity(refetched), "unchanged")

    def test_rescore_rolls_back_every_row_and_fingerprint_on_failure(self):
        self.add_item("first")
        self.add_item("second")
        initial_profile = matching_profile(base_score=20)
        self.database.rescore_for_profile(initial_profile)
        rows_before = [
            dict(row)
            for row in self.database.connection.execute(
                "SELECT * FROM opportunities ORDER BY id"
            )
        ]
        state_before = dict(
            self.database.connection.execute(
                "SELECT * FROM runtime_state WHERE key=?", (PROFILE_FINGERPRINT_KEY,)
            ).fetchone()
        )
        real_score = score_opportunity
        calls = 0

        def fail_on_second(item, profile):
            nonlocal calls
            calls += 1
            real_score(item, profile)
            if calls == 2:
                raise RuntimeError("synthetic scoring failure")
            return item

        with (
            patch("monitor.database.score_opportunity", side_effect=fail_on_second),
            self.assertRaisesRegex(RuntimeError, "synthetic scoring failure"),
        ):
            self.database.rescore_for_profile(matching_profile(base_score=45))

        rows_after = [
            dict(row)
            for row in self.database.connection.execute(
                "SELECT * FROM opportunities ORDER BY id"
            )
        ]
        state_after = dict(
            self.database.connection.execute(
                "SELECT * FROM runtime_state WHERE key=?", (PROFILE_FINGERPRINT_KEY,)
            ).fetchone()
        )
        self.assertEqual(rows_after, rows_before)
        self.assertEqual(state_after, state_before)

    def test_dashboard_command_rescores_without_network(self):
        self.add_item("dashboard")
        profile = matching_profile(base_score=35)
        output = self.root / "dashboard" / "index.html"
        with (
            patch("monitor.cli.project_path", side_effect=lambda *parts: self.root.joinpath(*parts)),
            patch("monitor.cli.load_profile", return_value=profile),
            patch("monitor.cli.render_dashboard", return_value=output) as render,
            patch.object(socket, "create_connection", side_effect=AssertionError("network used")),
        ):
            self.assertEqual(cli.command_dashboard(quiet=True), 0)
        payload = render.call_args.args[0]
        rendered = next(item for item in payload["opportunities"] if item["title"])
        self.assertEqual(rendered["score"], 55)

    def test_scan_rescores_before_the_first_source_fetch(self):
        self.add_item("scan")
        self.database.close()
        profile = matching_profile(base_score=45)

        def assert_rescored_before_fetch(_source):
            observer = Database(self.database_path)
            try:
                score = observer.connection.execute(
                    "SELECT score FROM opportunities WHERE external_id='scan'"
                ).fetchone()["score"]
            finally:
                observer.close()
            self.assertEqual(score, 65)
            return FetchResult([], "empty-feed")

        output = self.root / "dashboard" / "index.html"
        with (
            patch(
                "monitor.pipeline.project_path",
                side_effect=lambda *parts: self.root.joinpath(*parts),
            ),
            patch("monitor.pipeline.load_profile", return_value=profile),
            patch("monitor.pipeline.load_sources", return_value=[self.source]),
            patch("monitor.pipeline.fetch_source", side_effect=assert_rescored_before_fetch),
            patch("monitor.pipeline.render_dashboard", return_value=output),
            patch.object(socket, "create_connection", side_effect=AssertionError("network used")),
        ):
            result = pipeline.run_scan()
        self.assertEqual(result["status"], "ok")
        self.database = Database(self.database_path)


if __name__ == "__main__":
    unittest.main()
