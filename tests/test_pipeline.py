import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from monitor import pipeline
from monitor.database import Database
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
            self.database.connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0], 0
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


if __name__ == "__main__":
    unittest.main()
