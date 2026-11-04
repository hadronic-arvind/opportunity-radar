import tempfile
import unittest
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from monitor import pipeline
from monitor.database import Database
from monitor.fetchers import CollectionDeadlineExceeded
from monitor.models import FetchResult, Opportunity
from monitor.pipeline import _import_curated, _register_sources, _target_year


class PipelineConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.database = Database(self.root / "opportunities.sqlite3")
        self.database.initialize()

    def tearDown(self):
        self.database.close()
        self.tempdir.cleanup()

    def test_omitted_curated_path_is_valid(self):
        _register_sources(self.database, [], {"curated_pipeline_path": ""})
        counts, error = _import_curated(self.database, {"curated_pipeline_path": ""})
        self.assertEqual(counts, {"new": 0, "updated": 0, "unchanged": 0})
        self.assertIsNone(error)
        self.assertEqual(
            self.database.connection.execute("SELECT COUNT(*) FROM sources").fetchone()[
                0
            ],
            0,
        )

    def test_valid_curated_path_registers_and_imports(self):
        seed = self.root / "pipeline.md"
        seed.write_text(
            """# Pipeline
## Apply first
| Priority | Opportunity | Why | Deadline | Resume |
|---|---|---|---|---|
| 1 | [Research Internship](https://example.com/research) | Machine learning | Deadline is March 2 | `Research` |
""",
            encoding="utf-8",
        )
        profile = {
            "curated_pipeline_path": str(seed),
            "curated_pipeline_name": "My pipeline",
            "default_resume_code": "General",
            "resume_routing": [{"code": "Research", "terms": ["research"]}],
            "positive_rules": [],
            "negative_rules": [],
            "priority_organizations": [],
            "dashboard": {"target_season": "Summer 2028"},
        }
        _register_sources(self.database, [], profile)
        counts, error = _import_curated(self.database, profile)
        self.assertIsNone(error)
        self.assertEqual(counts["new"], 1)
        row = self.database.connection.execute(
            "SELECT deadline_at, recommended_resume FROM opportunities"
        ).fetchone()
        self.assertEqual(row["deadline_at"], "2028-03-02")
        self.assertEqual(row["recommended_resume"], "Research")

    def test_target_year_supports_multiple_structured_cycles(self):
        profile = {
            "timeframes": ["Fall 2029", "Summer 2028"],
            "targets": {
                "cycles": [
                    {"label": "Fall 2029", "season": "fall", "year": 2029},
                    {"label": "Summer 2028", "season": "summer", "year": 2028},
                ]
            },
            "dashboard": {"target_season": "Summer 2029"},
        }
        self.assertEqual(_target_year(profile), "2028")

    def test_missing_configured_seed_reports_only_basename(self):
        missing = self.root / "private" / "missing-pipeline.md"
        profile = {"curated_pipeline_path": str(missing)}
        _register_sources(self.database, [], profile)
        _counts, error = _import_curated(self.database, profile)
        self.assertEqual(error, "Curated pipeline not found: missing-pipeline.md")
        self.assertNotIn(str(self.root), error)

    def test_curated_import_uses_modular_document_labels(self):
        seed = self.root / "pipeline.md"
        seed.write_text(
            """# Pipeline
## Programs
| Opportunity | Resume |
|---|---|
| [Data Fellowship](https://example.com/data) | `Data CV` |
""",
            encoding="utf-8",
        )
        profile = {
            "curated_pipeline_path": str(seed),
            "documents": {
                "default": "General resume",
                "routes": [{"label": "Data CV", "terms": ["data"]}],
            },
            "matching": {"base_score": 50, "rules": []},
        }
        _register_sources(self.database, [], profile)
        counts, error = _import_curated(self.database, profile)
        self.assertIsNone(error)
        self.assertEqual(counts["new"], 1)
        row = self.database.connection.execute(
            "SELECT recommended_resume FROM opportunities"
        ).fetchone()
        self.assertEqual(row["recommended_resume"], "Data CV")

    def test_scan_refuses_an_active_profile_lifecycle(self):
        lifecycle = self.root / ".OpportunityRadar.lifecycle-lock"
        lifecycle.mkdir(mode=0o700)
        with (
            patch.object(pipeline.sys, "platform", "darwin"),
            patch("monitor.profile._lifecycle_lock_path", return_value=lifecycle),
            patch.dict(pipeline.os.environ, {}, clear=True),
            self.assertRaisesRegex(
                RuntimeError, "install, uninstall, or profile update"
            ),
        ):
            pipeline.ensure_profile_lifecycle_idle()

        with (
            patch.object(pipeline.sys, "platform", "darwin"),
            patch("monitor.profile._lifecycle_lock_path", return_value=lifecycle),
            patch.dict(
                pipeline.os.environ,
                {pipeline.LIFECYCLE_OWNER_ENV: "installer"},
                clear=True,
            ),
        ):
            pipeline.ensure_profile_lifecycle_idle()

    def test_scan_confirms_watch_page_changes_before_creating_events(self):
        runtime = self.root / "runtime"
        (runtime / "data").mkdir(parents=True)
        source = {
            "id": "program",
            "name": "Example Program",
            "kind": "watch_page",
            "url": "https://example.com/program",
            "cadence_hours": 1,
            "enabled": True,
        }
        hashes = iter(["baseline", "dynamic-one", "dynamic-two", "stable", "stable"])

        def fetch(_source):
            return FetchResult([], next(hashes))

        with (
            patch(
                "monitor.pipeline.project_path",
                side_effect=lambda *parts: runtime.joinpath(*parts),
            ),
            patch(
                "monitor.pipeline.load_profile",
                return_value={"polite_delay_seconds": 0},
            ),
            patch("monitor.pipeline.load_sources", return_value=[source]),
            patch("monitor.pipeline.fetch_source", side_effect=fetch),
            patch(
                "monitor.pipeline.render_dashboard",
                return_value=runtime / "dashboard" / "index.html",
            ),
            patch("monitor.pipeline.ensure_profile_lifecycle_idle"),
        ):
            for _index in range(5):
                self.assertEqual(pipeline.run_scan(force=True)["status"], "ok")

        observer = Database(runtime / "data" / "opportunities.sqlite3")
        try:
            events = observer.connection.execute(
                "SELECT previous_hash, content_hash FROM source_events"
            ).fetchall()
            source_state = observer.connection.execute(
                """
                SELECT last_content_hash, pending_content_hash,
                       pending_content_checks
                FROM sources WHERE id='program'
                """
            ).fetchone()
        finally:
            observer.close()
        self.assertEqual(
            [(row["previous_hash"], row["content_hash"]) for row in events],
            [("baseline", "stable")],
        )
        self.assertEqual(source_state["last_content_hash"], "stable")
        self.assertEqual(source_state["pending_content_hash"], "")
        self.assertEqual(source_state["pending_content_checks"], 0)

    def test_failed_source_refresh_rolls_back_earlier_rows_and_counts(self):
        runtime = self.root / "runtime"
        source = {
            "id": "fixture",
            "name": "Fixture",
            "kind": "greenhouse",
            "url": "https://example.org/jobs",
            "cadence_hours": 1,
        }
        items = [
            Opportunity(
                "fixture",
                str(i),
                "Role " + str(i),
                "Example",
                "https://example.org/" + str(i),
            )
            for i in range(2)
        ]
        real_score = pipeline.score_opportunity

        def score(item, profile):
            if item.external_id == "1":
                raise ValueError("Invalid fixture")
            return real_score(item, profile)

        with (
            patch(
                "monitor.pipeline.project_path",
                side_effect=lambda *p: runtime.joinpath(*p),
            ),
            patch(
                "monitor.pipeline.load_profile",
                return_value={"matching": {"base_score": 80}},
            ),
            patch("monitor.pipeline.load_sources", return_value=[source]),
            patch(
                "monitor.pipeline.fetch_source",
                return_value=FetchResult(items, "fixture"),
            ),
            patch("monitor.pipeline.score_opportunity", side_effect=score),
            patch(
                "monitor.pipeline.render_dashboard",
                return_value=runtime / "dashboard/index.html",
            ),
            patch("monitor.pipeline.ensure_profile_lifecycle_idle"),
            patch("monitor.pipeline.polite_pause"),
        ):
            result = pipeline.run_scan(force=True)
        observer = Database(runtime / "data/opportunities.sqlite3")
        try:
            self.assertEqual(result["status"], "partial")
            self.assertEqual(result["new_count"], 0)
            self.assertEqual(result["new_high_fit"], [])
            self.assertEqual(
                observer.connection.execute(
                    "SELECT COUNT(*) FROM opportunities"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(observer.source_status("fixture"), "error")
        finally:
            observer.close()


class ScanRefreshTests(unittest.TestCase):
    def setUp(self):
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        self.root = Path(tempdir.name)
        self.database_path = self.root / "data" / "opportunities.sqlite3"
        self.sources = [self.source("fixture")]
        self.start_patch("project_path", side_effect=self.root.joinpath)
        self.start_patch("ensure_profile_lifecycle_idle")
        self.start_patch("load_profile", return_value={"polite_delay_seconds": 0})
        self.start_patch("load_sources", side_effect=lambda **_: self.sources)
        self.start_patch(
            "render_dashboard", return_value=self.root / "dashboard/index.html"
        )

    def start_patch(self, attribute, **kwargs):
        mock = patch.object(pipeline, attribute, **kwargs)
        self.addCleanup(mock.stop)
        return mock.start()

    @staticmethod
    def source(identifier, **fields):
        return {
            "id": identifier,
            "name": identifier,
            "kind": "greenhouse",
            "cadence_hours": 24,
            **fields,
        }

    @staticmethod
    def item(identifier, description="", **metadata):
        return Opportunity(
            "fixture",
            identifier,
            "Research Associate",
            "Example",
            "https://example.org/" + identifier,
            description=description,
            metadata=metadata,
        )

    def test_unchanged_snapshot_and_rows_bypass_scoring_but_changed_evidence_does_not(
        self,
    ):
        self.start_patch(
            "fetch_source",
            side_effect=[
                FetchResult(
                    [self.item("one", "Initial"), self.item("two", "Stable")], "one"
                ),
                FetchResult(
                    [self.item("two", "Stable"), self.item("one", "Initial")], "two"
                ),
                FetchResult(
                    [self.item("one", "Changed"), self.item("two", "Stable")], "two"
                ),
            ],
        )
        scoring = self.start_patch(
            "score_opportunity", wraps=pipeline.score_opportunity
        )
        self.assertEqual(pipeline.run_scan(force=True)["new_count"], 2)
        unchanged = pipeline.run_scan(force=True)
        self.assertEqual(unchanged["unchanged_sources"], 1)
        self.assertEqual(unchanged["updated_count"], 0)
        self.assertEqual(scoring.call_count, 2)
        changed = pipeline.run_scan(force=True)
        self.assertEqual(changed["updated_count"], 1)
        self.assertEqual(scoring.call_count, 3)

        # A restored/missing row must never be ignored just because its source
        # still has the matching snapshot marker.
        observer = Database(self.database_path)
        self.addCleanup(observer.close)
        observer.connection.execute(
            "UPDATE opportunities SET active=0 WHERE external_id='one'"
        )
        observer.connection.commit()
        with patch.object(
            pipeline,
            "fetch_source",
            return_value=FetchResult(
                [
                    self.item("one", "Changed"),
                    self.item("two", "Stable"),
                ],
                "two",
            ),
        ):
            self.assertEqual(pipeline.run_scan(force=True)["unchanged_sources"], 0)
        row = observer.connection.execute(
            "SELECT active FROM opportunities WHERE external_id='one'"
        ).fetchone()
        self.assertEqual(row["active"], 1)
        self.assertEqual(scoring.call_count, 4)

    def test_rolling_feed_and_failed_empty_fetch_do_not_retire_rows(self):
        self.start_patch(
            "fetch_source",
            side_effect=[
                FetchResult([self.item("one")], "initial", complete_snapshot=False),
                FetchResult([self.item("two")], "next", complete_snapshot=False),
                FetchResult(
                    [], "invalid", status="error", message="Malformed response"
                ),
                FetchResult([], "complete"),
            ],
        )
        pipeline.run_scan(force=True)
        pipeline.run_scan(force=True)
        self.assertEqual(pipeline.run_scan(force=True)["status"], "partial")
        observer = Database(self.database_path)
        self.addCleanup(observer.close)
        self.assertEqual(
            observer.connection.execute(
                "SELECT SUM(active) FROM opportunities"
            ).fetchone()[0],
            2,
        )
        pipeline.run_scan(force=True)
        self.assertEqual(
            observer.connection.execute(
                "SELECT SUM(active) FROM opportunities"
            ).fetchone()[0],
            0,
        )

    def test_ordinary_bursts_resume_with_priority_and_progress(self):
        self.sources = [
            self.source("slow"),
            self.source("other"),
            self.source("broad", packs=["multi-employer"]),
            self.source("events", packs=["professional-events"]),
        ]
        seconds = [0.0]
        checked = []

        def fetch(source):
            checked.append(source["id"])
            seconds[0] += pipeline.DEFAULT_DUE_BURST_SECONDS + 1
            return FetchResult([], source["id"])

        self.start_patch("fetch_source", side_effect=fetch)
        events = []
        with patch.object(pipeline.time, "monotonic", side_effect=lambda: seconds[0]):
            first = pipeline.run_scan(progress=events.append)
            second = pipeline.run_scan(max_sources=1)
            third = pipeline.run_scan()
            fourth = pipeline.run_scan()
            target = pipeline.run_scan(force=True, source_ids=["other"])
        self.assertEqual(checked, ["events", "broad", "slow", "other", "other"])
        self.assertEqual(
            [result["remaining_due"] for result in (first, second, third, fourth)],
            [3, 2, 1, 0],
        )
        self.assertEqual(target["checked_sources"], 1)
        self.assertEqual(
            [event["event"] for event in events],
            ["start", "source_start", "source_complete", "render", "finish"],
        )
        self.assertEqual(events[-1]["remaining_due"], 3)
        self.assertEqual(events[2]["source_id"], "events")

    def test_cold_network_slice_leaves_time_for_other_sources_and_retries_later(self):
        self.sources = [self.source("slow"), self.source("fast")]
        seconds = [0.0]

        def fetch(source):
            if source["id"] == "slow" and seconds[0] == 0:
                seconds[0] += pipeline.FIRST_REFRESH_PER_SOURCE_SECONDS
                raise CollectionDeadlineExceeded("Cold source budget elapsed")
            return FetchResult([], source["id"])

        self.start_patch("fetch_source", side_effect=fetch)
        deadlines = self.start_patch(
            "collection_request_deadline", side_effect=lambda _: nullcontext()
        )
        with patch.object(pipeline.time, "monotonic", side_effect=lambda: seconds[0]):
            first = pipeline.run_scan()
            second = pipeline.run_scan()
        self.assertEqual(
            (
                first["checked_sources"],
                first["deferred_sources"],
                first["remaining_due"],
            ),
            (1, 1, 1),
        )
        self.assertEqual(
            (
                second["checked_sources"],
                second["deferred_sources"],
                second["remaining_due"],
            ),
            (1, 0, 0),
        )
        self.assertEqual(
            [call.args[0] for call in deadlines.call_args_list], [6, 12, None]
        )

    def test_explicit_network_deferral_keeps_source_health_and_rotates_next_turn(self):
        self.sources = [self.source("fixture"), self.source("fast")]
        fetch = self.start_patch(
            "fetch_source",
            side_effect=[
                FetchResult([self.item("preserved")], "first"),
                CollectionDeadlineExceeded("Explicit network deadline elapsed"),
                FetchResult([], "fast"),
            ],
        )
        pipeline.run_scan(force=True, source_ids=["fixture"])
        observer = Database(self.database_path)
        self.addCleanup(observer.close)
        observer.connection.execute(
            "UPDATE sources SET last_checked_at='2025-01-01T00:00:00+00:00'"
        )
        observer.connection.commit()
        events = []
        first = pipeline.run_scan(
            force=True, source_ids=["fixture"], max_seconds=0.05, progress=events.append
        )
        second = pipeline.run_scan(force=True, max_seconds=0.05, max_sources=1)
        self.assertEqual(
            (
                first["checked_sources"],
                first["deferred_sources"],
                first["remaining_due"],
            ),
            (0, 1, 1),
        )
        self.assertEqual(second["checked_sources"], 1)
        self.assertEqual(observer.source_status("fixture"), "ok")
        self.assertEqual(observer.source_status("fast"), "ok")
        self.assertEqual(
            observer.connection.execute("SELECT active FROM opportunities").fetchone()[
                0
            ],
            1,
        )
        self.assertEqual(events[2]["status"], "deferred")
        self.assertEqual(
            [call.args[0]["id"] for call in fetch.call_args_list],
            ["fixture", "fixture", "fast"],
        )

    def test_rolling_history_archives_old_unseen_rows_and_preserves_workflow(self):
        self.start_patch(
            "fetch_source",
            side_effect=[
                FetchResult(
                    [self.item("tracked"), self.item("recent")],
                    "initial",
                    complete_snapshot=False,
                ),
                FetchResult([], "error", status="error", complete_snapshot=False),
                FetchResult([], "quiet", complete_snapshot=False),
                FetchResult(
                    [self.item("tracked")], "reappeared", complete_snapshot=False
                ),
            ],
        )
        pipeline.run_scan(force=True)
        observer = Database(self.database_path)
        self.addCleanup(observer.close)
        now = datetime.now(timezone.utc).replace(microsecond=0)
        observer.connection.executemany(
            "UPDATE opportunities SET last_seen_at=? WHERE external_id=?",
            [
                ((now - timedelta(days=days)).isoformat(), identifier)
                for identifier, days in (("tracked", 91), ("recent", 89))
            ],
        )
        observer.connection.commit()

        def stored(identifier):
            return observer.connection.execute(
                "SELECT id, active, status, bookmarked FROM opportunities WHERE external_id=?",
                (identifier,),
            ).fetchone()

        identifier = stored("tracked")["id"]
        observer.set_status(identifier, "applied")
        observer.set_bookmarked(identifier, True)
        pipeline.run_scan(force=True)
        self.assertEqual(stored("tracked")["active"], 1)
        pipeline.run_scan(force=True)
        archived = stored("tracked")
        self.assertEqual(
            (archived["active"], archived["status"], archived["bookmarked"]),
            (0, "applied", 1),
        )
        self.assertEqual(stored("recent")["active"], 1)
        pipeline.run_scan(force=True)
        resurfaced = stored("tracked")
        self.assertEqual(
            (
                resurfaced["id"],
                resurfaced["active"],
                resurfaced["status"],
                resurfaced["bookmarked"],
            ),
            (identifier, 1, "applied", 1),
        )

    def test_detail_failures_preserve_prior_description_but_successful_empty_removes_it(
        self,
    ):
        self.sources = [self.source("fixture", kind="workday")]
        fetch = self.start_patch(
            "fetch_source",
            side_effect=[
                FetchResult(
                    [self.item("one", "Prior requirements", detail_status="current")],
                    "first",
                    next_detail_offset=7,
                ),
                FetchResult(
                    [self.item("one", detail_status="failed")],
                    "second",
                    next_detail_offset=8,
                ),
                FetchResult(
                    [self.item("one", detail_status="current")],
                    "third",
                    next_detail_offset=0,
                ),
            ],
        )
        pipeline.run_scan(force=True)
        pipeline.run_scan(force=True)
        observer = Database(self.database_path)
        self.addCleanup(observer.close)
        query = "SELECT description, metadata_json FROM opportunities WHERE external_id='one'"
        row = observer.connection.execute(query).fetchone()
        self.assertEqual(row["description"], "Prior requirements")
        self.assertIn('"detail_status": "cached"', row["metadata_json"])
        pipeline.run_scan(force=True)
        self.assertEqual(
            observer.connection.execute(query).fetchone()["description"], ""
        )
        self.assertEqual(
            [call.args[0]["_detail_offset"] for call in fetch.call_args_list], [0, 7, 8]
        )


if __name__ == "__main__":
    unittest.main()
