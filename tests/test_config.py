import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from monitor import cli
from monitor import config
from monitor import onboarding
from monitor import pipeline
from monitor import profile as profile_service
from monitor.database import Database
from monitor.models import Opportunity
from monitor.scoring import score_opportunity


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        (self.root / "config").mkdir()
        (self.root / "config" / "profile.json").write_text(
            json.dumps({"value": "public", "nested": {"public": True}}), encoding="utf-8"
        )
        (self.root / "config" / "sources.json").write_text(
            json.dumps({"sources": [{"id": "base", "enabled": True, "cadence_hours": 12}]}),
            encoding="utf-8",
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def load_profile(self, environment=None):
        with patch.object(config, "PROJECT_ROOT", self.root), patch.dict(
            os.environ, environment or {}, clear=True
        ):
            return config.load_profile()

    def test_public_profile_is_default(self):
        self.assertEqual(self.load_profile()["value"], "public")

    def test_tracked_starter_profile_is_neutral(self):
        project = Path(__file__).resolve().parents[1]
        profile = json.loads((project / "config" / "profile.json").read_text(encoding="utf-8"))
        self.assertNotIn("candidate", profile)
        self.assertEqual(profile["priority_organizations"], [])
        self.assertEqual(profile["matching"]["rules"], [])
        self.assertEqual(profile["documents"]["routes"], [])
        self.assertNotIn("candidate", profile)
        self.assertNotIn("selected_source_packs", profile)
        for legacy_field in (
            "positive_rules",
            "negative_rules",
            "default_resume_code",
            "resume_routing",
        ):
            self.assertNotIn(legacy_field, profile)

    def test_local_profile_precedes_public_profile(self):
        (self.root / "config" / "profile.local.json").write_text(
            json.dumps({"value": "local", "nested": {"local": True}}), encoding="utf-8"
        )
        profile = self.load_profile()
        self.assertEqual(profile["value"], "local")
        self.assertEqual(profile["nested"], {"public": True, "local": True})

    def test_environment_profile_precedes_local_profile(self):
        (self.root / "config" / "profile.local.json").write_text(
            json.dumps({"value": "local"}), encoding="utf-8"
        )
        override = self.root / "override.json"
        override.write_text(json.dumps({"value": "environment"}), encoding="utf-8")
        profile = self.load_profile({"OPPORTUNITY_RADAR_PROFILE": str(override)})
        self.assertEqual(profile["value"], "environment")

    def test_missing_explicit_profile_fails_loudly(self):
        with self.assertRaises(FileNotFoundError):
            self.load_profile({"OPPORTUNITY_RADAR_PROFILE": str(self.root / "missing.json")})

    def test_profile_loads_are_independent(self):
        first = self.load_profile()
        first["nested"]["public"] = False
        self.assertTrue(self.load_profile()["nested"]["public"])

    def test_local_sources_override_and_disable_public_source(self):
        (self.root / "config" / "sources.local.json").write_text(
            json.dumps(
                {
                    "sources": [
                        {"id": "base", "enabled": False},
                        {
                            "id": "local",
                            "name": "Example Cooperative",
                            "kind": "watch_page",
                            "url": "https://example.org/opportunities",
                            "enabled": True,
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        with patch.object(config, "PROJECT_ROOT", self.root), patch.dict(os.environ, {}, clear=True):
            self.assertEqual([source["id"] for source in config.load_sources()], ["local"])
            self.assertEqual(
                [source["id"] for source in config.load_sources(include_disabled=True)],
                ["base", "local"],
            )

    def test_onboarding_writes_only_private_local_files(self):
        sources = [
            {
                "id": "one",
                "kind": "watch_page",
                "support_level": "manual",
                "packs": ["starter-diverse"],
            },
            {
                "id": "two",
                "kind": "greenhouse",
                "support_level": "supported",
                "packs": ["engineering"],
            },
        ]
        packs = [
            {"id": "starter-diverse", "default": True},
            {"id": "engineering"},
        ]
        with (
            patch.object(config, "PROJECT_ROOT", self.root),
            patch.object(
                profile_service,
                "_lifecycle_lock_path",
                return_value=self.root / "Application Support" / ".OpportunityRadar.lifecycle-lock",
            ),
            patch("monitor.onboarding.load_sources", return_value=sources),
            patch("monitor.onboarding.load_source_packs", return_value=packs),
        ):
            result = onboarding.initialize(
                ["engineering"],
                include_terms=["distributed systems"],
                default_document="Software",
                timeframes=["Summer 2028", "Fall 2028"],
            )
            self.assertEqual(result["enabled_sources"], 1)
            self.assertEqual(result["listing_feeds"], 1)
            self.assertEqual(result["manual_pages"], 0)
            profile = json.loads((self.root / "config" / "profile.local.json").read_text())
            registry = json.loads((self.root / "config" / "sources.local.json").read_text())
            self.assertNotIn("selected_source_packs", profile)
            self.assertEqual(profile["documents"]["default"], "Software")
            self.assertEqual(profile["matching"]["engine"], "structured_v2")
            self.assertEqual(profile["matching"]["minimum_display_score"], 40)
            self.assertEqual(profile["timeframes"], ["Summer 2028", "Fall 2028"])
            self.assertEqual(
                profile["targets"]["cycles"],
                [{"label": "Summer 2028"}, {"label": "Fall 2028"}],
            )
            self.assertEqual(registry["selected_packs"], ["engineering"])
            self.assertEqual(registry["sources"], [])
            for name in ("profile.local.json", "sources.local.json"):
                self.assertEqual((self.root / "config" / name).stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                onboarding.initialize(["starter-diverse"])

    def test_fresh_onboarding_rejects_values_the_profile_editor_cannot_load(self):
        packs = [{"id": "technical", "default": True}]
        sources = [
            {
                "id": "example",
                "name": "Example",
                "kind": "watch_page",
                "url": "https://example.org/jobs",
                "packs": ["technical"],
            }
        ]
        lifecycle = (
            self.root
            / "Application Support"
            / ".OpportunityRadar.lifecycle-lock"
        )
        invalid_values = (
            {"timeframes": ["Cycle {}".format(index) for index in range(13)]},
            {"default_document": "x" * 121},
        )
        with (
            patch.object(config, "PROJECT_ROOT", self.root),
            patch.object(
                profile_service,
                "_lifecycle_lock_path",
                return_value=lifecycle,
            ),
            patch("monitor.onboarding.load_sources", return_value=sources),
            patch("monitor.onboarding.load_source_packs", return_value=packs),
        ):
            for values in invalid_values:
                with (
                    self.subTest(values=values),
                    self.assertRaises(profile_service.ProfileValidationError),
                ):
                    onboarding.initialize(["technical"], **values)

        self.assertFalse((self.root / "config" / "profile.local.json").exists())
        self.assertFalse((self.root / "config" / "sources.local.json").exists())

    def test_recognized_runtime_is_canonical_for_local_configuration(self):
        runtime = self.root / "private-runtime"
        for directory in (
            runtime,
            runtime / "monitor",
            runtime / "config",
            runtime / "dashboard",
            runtime / "data",
        ):
            directory.mkdir(exist_ok=True)
            directory.chmod(0o700)
        markers = (
            runtime / "monitor" / "__main__.py",
            runtime / "config" / "profile.json",
            runtime / "dashboard" / "template.html",
            runtime / "dashboard" / "styles.css",
            runtime / "dashboard" / "app.js",
        )
        for marker in markers:
            marker.write_text("{}" if marker.suffix == ".json" else "marker", encoding="utf-8")
            marker.chmod(0o600)
        database = runtime / "data" / "opportunities.sqlite3"
        database.write_text("private state", encoding="utf-8")
        database.chmod(0o600)
        (runtime / "config" / "profile.local.json").write_text(
            json.dumps({"value": "runtime"}), encoding="utf-8"
        )
        (runtime / "config" / "profile.local.json").chmod(0o600)
        (self.root / "config" / "profile.local.json").write_text(
            json.dumps({"value": "stale-clone"}), encoding="utf-8"
        )
        (self.root / "data").mkdir()
        (self.root / "data" / "opportunities.sqlite3").symlink_to(database)

        with patch.object(config, "PROJECT_ROOT", self.root), patch.dict(
            os.environ, {}, clear=True
        ):
            self.assertEqual(config.local_configuration_root(), runtime.resolve())
            self.assertEqual(
                config.local_profile_path(),
                runtime.resolve() / "config" / "profile.local.json",
            )
            self.assertEqual(config.load_profile()["value"], "runtime")

    def test_profile_apply_preserves_unexposed_fields_and_source_overrides(self):
        (self.root / "config" / "profile.json").write_text(
            json.dumps(
                {
                    "priority_organizations": [],
                    "matching": {
                        "base_score": 30,
                        "priority_organization_bonus": 10,
                        "tier_thresholds": {"priority": 80, "strong": 65, "watch": 40},
                        "rules": [],
                    },
                    "documents": {"default": "General", "routes": []},
                }
            ),
            encoding="utf-8",
        )
        (self.root / "config" / "sources.json").write_text(
            json.dumps(
                {
                    "packs": [
                        {"id": "technical", "default": True},
                        {"id": "research"},
                    ],
                    "sources": [
                        {
                            "id": "example",
                            "name": "Example",
                            "kind": "watch_page",
                            "url": "https://example.org/jobs",
                            "packs": ["technical"],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        (self.root / "config" / "profile.local.json").write_text(
            json.dumps(
                {
                    "candidate": {"name": "Private name", "program": "Private program"},
                    "curated_pipeline_path": "/private/curated.md",
                }
            ),
            encoding="utf-8",
        )
        source_override = {
            "schema_version": 2,
            "selected_packs": ["technical"],
            "sources": [{"id": "example", "enabled": False}],
            "private_note": "preserve",
        }
        (self.root / "config" / "sources.local.json").write_text(
            json.dumps(source_override), encoding="utf-8"
        )
        lifecycle = self.root / "Application Support" / ".OpportunityRadar.lifecycle-lock"

        with (
            patch.object(config, "PROJECT_ROOT", self.root),
            patch.object(profile_service, "_lifecycle_lock_path", return_value=lifecycle),
            patch.dict(os.environ, {}, clear=True),
        ):
            payload = profile_service.profile_editor_payload()
            self.assertNotIn("name", payload["candidate"])
            payload["timeframes"] = ["Summer 2028", "Fall 2028"]
            payload["targets"]["cycles"] = [
                {"label": "Summer 2028", "season": "summer", "year": 2028},
                {"label": "Fall 2028", "season": "fall", "year": 2028},
            ]
            payload["selected_packs"] = ["technical", "research"]
            payload["priority_organizations"] = ["Example Lab"]
            result = profile_service.apply_editor_payload(payload, rebuild=False)
            self.assertTrue(result["saved"])
            with self.assertRaisesRegex(
                profile_service.ProfileValidationError, "changed after it was opened"
            ):
                profile_service.apply_editor_payload(payload, rebuild=False)

        saved_profile = json.loads(
            (self.root / "config" / "profile.local.json").read_text(encoding="utf-8")
        )
        saved_sources = json.loads(
            (self.root / "config" / "sources.local.json").read_text(encoding="utf-8")
        )
        self.assertEqual(saved_profile["candidate"]["name"], "Private name")
        self.assertEqual(saved_profile["candidate"]["program"], "Private program")
        self.assertEqual(saved_profile["curated_pipeline_path"], "/private/curated.md")
        self.assertEqual(saved_profile["timeframes"], ["Summer 2028", "Fall 2028"])
        self.assertEqual(saved_sources["sources"], source_override["sources"])
        self.assertEqual(saved_sources["private_note"], "preserve")
        self.assertEqual(saved_sources["selected_packs"], ["technical", "research"])
        self.assertEqual((self.root / "config" / "profile.local.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.root / "config" / "sources.local.json").stat().st_mode & 0o777, 0o600)

    def test_profile_pack_removal_retires_positive_public_source_overrides(self):
        (self.root / "config" / "profile.json").write_text(
            json.dumps(
                {
                    "priority_organizations": [],
                    "matching": {
                        "base_score": 25,
                        "tier_thresholds": {
                            "priority": 75,
                            "strong": 60,
                            "watch": 40,
                        },
                        "rules": [],
                    },
                    "documents": {"default": "General", "routes": []},
                }
            ),
            encoding="utf-8",
        )
        (self.root / "config" / "sources.json").write_text(
            json.dumps(
                {
                    "packs": [
                        {"id": "technical", "default": True},
                        {"id": "finance"},
                    ],
                    "sources": [
                        {
                            "id": "technical_board",
                            "name": "Technical Board",
                            "kind": "watch_page",
                            "url": "https://example.org/technical",
                            "packs": ["technical"],
                        },
                        {
                            "id": "finance_board",
                            "name": "Finance Board",
                            "kind": "watch_page",
                            "url": "https://example.org/finance",
                            "packs": ["finance"],
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        private_source = {
            "id": "private_board",
            "name": "Private Board",
            "kind": "watch_page",
            "url": "https://example.net/opportunities",
            "packs": ["technical"],
            "enabled": True,
        }
        (self.root / "config" / "sources.local.json").write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "selected_packs": ["technical", "finance"],
                    "sources": [
                        {"id": "finance_board", "enabled": True},
                        {"id": "technical_board", "enabled": False},
                        private_source,
                    ],
                }
            ),
            encoding="utf-8",
        )
        lifecycle = self.root / "Application Support" / ".OpportunityRadar.lifecycle-lock"

        with (
            patch.object(config, "PROJECT_ROOT", self.root),
            patch.object(profile_service, "_lifecycle_lock_path", return_value=lifecycle),
            patch.dict(os.environ, {}, clear=True),
        ):
            payload = profile_service.profile_editor_payload()
            payload["selected_packs"] = ["technical"]
            profile_service.apply_editor_payload(payload, rebuild=False)

        saved = json.loads(
            (self.root / "config" / "sources.local.json").read_text(encoding="utf-8")
        )
        self.assertEqual(saved["selected_packs"], ["technical"])
        self.assertEqual(
            saved["sources"],
            [{"id": "technical_board", "enabled": False}, private_source],
        )

    def test_profile_editor_migrates_and_removes_hidden_legacy_aliases(self):
        (self.root / "config" / "profile.json").write_text(
            json.dumps(
                {
                    "priority_organizations": [],
                    "matching": {
                        "base_score": 25,
                        "tier_thresholds": {"priority": 75, "strong": 60, "watch": 40},
                        "rules": [],
                    },
                    "documents": {"default": "General", "routes": []},
                }
            ),
            encoding="utf-8",
        )
        (self.root / "config" / "sources.json").write_text(
            json.dumps(
                {
                    "packs": [{"id": "technical", "default": True}],
                    "sources": [
                        {
                            "id": "example",
                            "name": "Example",
                            "kind": "watch_page",
                            "url": "https://example.org/jobs",
                            "packs": ["technical"],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        legacy = {
            "candidate": {
                "career_stage": "doctoral_student",
                "target_season": "Summer 2028",
            },
            "targets": {
                "roles": ["legacy role"],
                "skills": ["legacy skill"],
                "workplace_types": ["remote"],
            },
            "exclusions": ["legacy exclusion"],
        }
        (self.root / "config" / "profile.local.json").write_text(
            json.dumps(legacy), encoding="utf-8"
        )
        lifecycle = self.root / "Application Support" / ".OpportunityRadar.lifecycle-lock"
        with (
            patch.object(config, "PROJECT_ROOT", self.root),
            patch.object(profile_service, "_lifecycle_lock_path", return_value=lifecycle),
            patch.dict(os.environ, {}, clear=True),
        ):
            payload = profile_service.profile_editor_payload()
            self.assertEqual(payload["candidate"]["current_stage"], "doctoral_student")
            self.assertEqual(payload["timeframes"], ["Summer 2028"])
            self.assertEqual(payload["targets"]["role_families"], ["legacy role"])
            self.assertEqual(payload["targets"]["supporting_skills"], ["legacy skill"])
            self.assertEqual(payload["targets"]["work_arrangements"], ["remote"])
            self.assertEqual(payload["targets"]["exclusions"], ["legacy exclusion"])
            self.assertEqual(payload["matching"]["engine"], "structured_v2")

            payload["candidate"].pop("current_stage", None)
            payload["timeframes"] = []
            payload["targets"]["cycles"] = []
            payload["targets"]["role_families"] = []
            payload["targets"]["supporting_skills"] = []
            payload["targets"]["work_arrangements"] = []
            payload["targets"]["exclusions"] = []
            profile_service.apply_editor_payload(payload, rebuild=False)
            saved = json.loads(
                (self.root / "config" / "profile.local.json").read_text(encoding="utf-8")
            )
            reopened = profile_service.profile_editor_payload()

        self.assertNotIn("career_stage", saved["candidate"])
        self.assertNotIn("target_season", saved["candidate"])
        for key in ("roles", "skills", "workplace_types"):
            self.assertNotIn(key, saved["targets"])
        self.assertNotIn("exclusions", saved)
        self.assertEqual(saved["matching"]["engine"], "structured_v2")
        self.assertEqual(reopened["timeframes"], [])
        self.assertEqual(reopened["targets"]["role_families"], [])

    def test_profile_editor_materializes_effective_rule_boolean_defaults(self):
        positive_interest = profile_service._normalize_rule(
            {
                "label": "Research",
                "weight": 10,
                "terms": ["research"],
                "fields": ["title"],
            },
            0,
            set(),
        )
        qualification = profile_service._normalize_rule(
            {
                "label": "Python",
                "dimension": "qualification",
                "weight": 10,
                "terms": ["python"],
                "fields": ["description"],
            },
            1,
            set(),
        )

        self.assertTrue(positive_interest["anchor"])
        self.assertFalse(positive_interest["hard_gate"])
        self.assertFalse(qualification["anchor"])
        self.assertFalse(qualification["hard_gate"])

        (self.root / "config" / "sources.json").write_text(
            json.dumps(
                {
                    "packs": [{"id": "technical", "default": True}],
                    "sources": [],
                }
            ),
            encoding="utf-8",
        )
        profile = {
            "priority_organizations": [],
            "matching": {
                "engine": "structured_v2",
                "base_score": 25,
                "tier_thresholds": {"priority": 75, "strong": 60, "watch": 40},
                "rules": [
                    {
                        "label": "Research",
                        "weight": 10,
                        "terms": ["research"],
                        "fields": ["title"],
                    }
                ],
            },
            "documents": {"default": "General", "routes": []},
        }
        with patch.object(config, "PROJECT_ROOT", self.root):
            projected = profile_service.profile_editor_payload(profile)

        self.assertTrue(projected["matching"]["rules"][0]["anchor"])

    def test_profile_refresh_failure_restores_both_private_files(self):
        (self.root / "config" / "profile.json").write_text(
            json.dumps(
                {
                    "priority_organizations": [],
                    "matching": {
                        "base_score": 25,
                        "tier_thresholds": {"priority": 75, "strong": 60, "watch": 40},
                        "rules": [],
                    },
                    "documents": {"default": "General", "routes": []},
                }
            ),
            encoding="utf-8",
        )
        (self.root / "config" / "sources.json").write_text(
            json.dumps(
                {
                    "packs": [{"id": "technical", "default": True}],
                    "sources": [],
                }
            ),
            encoding="utf-8",
        )
        profile_path = self.root / "config" / "profile.local.json"
        sources_path = self.root / "config" / "sources.local.json"
        profile_path.write_text(json.dumps({"private_note": "before"}), encoding="utf-8")
        sources_path.write_text(
            json.dumps({"selected_packs": ["technical"], "sources": []}),
            encoding="utf-8",
        )
        original_profile = profile_path.read_bytes()
        original_sources = sources_path.read_bytes()
        lifecycle = self.root / "Application Support" / ".OpportunityRadar.lifecycle-lock"
        with (
            patch.object(config, "PROJECT_ROOT", self.root),
            patch.object(profile_service, "_lifecycle_lock_path", return_value=lifecycle),
            patch.object(
                profile_service,
                "refresh_profile_state",
                side_effect=RuntimeError("render failed"),
            ),
            patch.dict(os.environ, {}, clear=True),
        ):
            payload = profile_service.profile_editor_payload()
            payload["targets"]["role_families"] = ["new role"]
            with self.assertRaisesRegex(RuntimeError, "render failed"):
                profile_service.apply_editor_payload(payload)

        self.assertEqual(profile_path.read_bytes(), original_profile)
        self.assertEqual(sources_path.read_bytes(), original_sources)

    def test_profile_editor_rejects_non_finite_numbers(self):
        with self.assertRaisesRegex(
            profile_service.ProfileValidationError, "non-finite number"
        ):
            profile_service.read_editor_json('{"weight": NaN}')
        payload = {
            "version": 1,
            "expected_revision": "",
            "timeframes": [],
            "selected_packs": ["technical"],
            "candidate": {"completed_degrees": [], "skills": []},
            "targets": {
                "cycles": [],
                "opportunity_types": [],
                "role_families": [],
                "domains": [],
                "supporting_skills": [],
                "locations": [],
                "exclusions": [],
                "work_arrangements": [],
            },
            "priority_organizations": [],
            "matching": {
                "engine": "structured_v2",
                "base_score": 25,
                "priority_organization_bonus": 10,
                "tier_thresholds": {"priority": 75, "strong": 60, "watch": 40},
                "rules": [],
                "field_weights": {"title": float("inf")},
            },
            "documents": {"default": "General", "routes": []},
        }
        with (
            patch("monitor.profile.config.load_source_packs", return_value=[{"id": "technical"}]),
            self.assertRaisesRegex(
                profile_service.ProfileValidationError, "finite number"
            ),
        ):
            profile_service.validate_editor_payload(payload)

    def test_two_file_writer_rolls_back_chmod_and_second_replace_failures(self):
        profile_path = self.root / "config" / "profile.local.json"
        source_path = self.root / "config" / "sources.local.json"
        profile_path.write_text(json.dumps({"revision": "old-profile"}), encoding="utf-8")
        source_path.write_text(json.dumps({"revision": "old-sources"}), encoding="utf-8")
        before = (profile_path.read_bytes(), source_path.read_bytes())
        replacement = ({"revision": "new-profile"}, {"revision": "new-sources"})

        with (
            patch.object(config, "PROJECT_ROOT", self.root),
            patch.object(
                profile_service.os,
                "chmod",
                side_effect=OSError("chmod failed"),
            ),
            self.assertRaisesRegex(OSError, "chmod failed"),
        ):
            profile_service.write_local_configuration(*replacement, force=True)
        self.assertEqual(profile_path.read_bytes(), before[0])
        self.assertEqual(source_path.read_bytes(), before[1])

        real_replace = os.replace
        replace_calls = 0

        def fail_second_replace(source, destination):
            nonlocal replace_calls
            replace_calls += 1
            if replace_calls == 2:
                raise OSError("second replace failed")
            return real_replace(source, destination)

        with (
            patch.object(config, "PROJECT_ROOT", self.root),
            patch.object(profile_service.os, "replace", side_effect=fail_second_replace),
            self.assertRaisesRegex(OSError, "second replace failed"),
        ):
            profile_service.write_local_configuration(*replacement, force=True)
        self.assertEqual(profile_path.read_bytes(), before[0])
        self.assertEqual(source_path.read_bytes(), before[1])

    def test_profile_write_respects_installer_lifecycle_lock(self):
        lock = self.root / "Application Support" / ".OpportunityRadar.lifecycle-lock"
        lock.mkdir(parents=True)
        lock.chmod(0o700)
        with (
            patch.object(profile_service, "_lifecycle_lock_path", return_value=lock),
            patch.object(profile_service.sys, "platform", "darwin"),
            self.assertRaisesRegex(
                profile_service.ProfileValidationError,
                "install, uninstall, or profile update",
            ),
        ):
            with profile_service.profile_lifecycle_lock():
                self.fail("an existing lifecycle lock must block profile writes")

    def test_dead_lifecycle_owner_is_recovered_before_the_next_operation(self):
        lock = self.root / "Application Support" / ".OpportunityRadar.lifecycle-lock"
        lock.mkdir(parents=True, mode=0o700)
        owner = lock / profile_service.LIFECYCLE_OWNER_FILE
        owner.write_text(
            "99999999\n{}\n".format(profile_service.time.time()),
            encoding="ascii",
        )
        owner.chmod(0o600)

        with (
            patch.object(profile_service, "_lifecycle_lock_path", return_value=lock),
            patch.object(profile_service.sys, "platform", "darwin"),
            patch.object(pipeline.sys, "platform", "darwin"),
        ):
            pipeline.ensure_profile_lifecycle_idle()
            self.assertFalse(lock.exists())
            with profile_service.profile_lifecycle_lock():
                self.assertTrue(lock.is_dir())
                replacement_owner = lock / profile_service.LIFECYCLE_OWNER_FILE
                self.assertEqual(
                    replacement_owner.read_text(encoding="ascii").splitlines()[0],
                    str(os.getpid()),
                )

        self.assertFalse(lock.exists())

    def test_live_lifecycle_owner_is_never_recovered(self):
        lock = self.root / "Application Support" / ".OpportunityRadar.lifecycle-lock"
        lock.mkdir(parents=True, mode=0o700)
        owner = lock / profile_service.LIFECYCLE_OWNER_FILE
        owner.write_text(
            "{}\n{}\n".format(os.getpid(), profile_service.time.time()),
            encoding="ascii",
        )
        owner.chmod(0o600)

        self.assertFalse(profile_service.recover_stale_lifecycle_lock(lock))
        self.assertTrue(lock.is_dir())

    def test_onboarding_force_respects_scan_and_lifecycle_locks(self):
        from monitor.pipeline import exclusive_lock

        packs = [{"id": "technical", "default": True}]
        sources = [
            {
                "id": "example",
                "name": "Example",
                "kind": "watch_page",
                "url": "https://example.org/jobs",
                "packs": ["technical"],
            }
        ]
        profile_path = self.root / "config" / "profile.local.json"
        source_path = self.root / "config" / "sources.local.json"
        profile_path.write_text(json.dumps({"private_note": "before"}), encoding="utf-8")
        source_path.write_text(
            json.dumps({"selected_packs": ["technical"], "sources": []}),
            encoding="utf-8",
        )
        before = (profile_path.read_bytes(), source_path.read_bytes())
        lifecycle = self.root / "Application Support" / ".OpportunityRadar.lifecycle-lock"
        scan_lock = self.root / "data" / "scan.lock"
        with (
            patch.object(config, "PROJECT_ROOT", self.root),
            patch.object(profile_service, "_lifecycle_lock_path", return_value=lifecycle),
            patch("monitor.onboarding.load_sources", return_value=sources),
            patch("monitor.onboarding.load_source_packs", return_value=packs),
            patch.dict(os.environ, {}, clear=True),
        ):
            with exclusive_lock(scan_lock), self.assertRaisesRegex(
                RuntimeError, "scan is already running"
            ):
                onboarding.initialize(["technical"], force=True)
            lifecycle.mkdir(parents=True)
            lifecycle.chmod(0o700)
            with (
                patch.object(profile_service.sys, "platform", "darwin"),
                self.assertRaisesRegex(
                    profile_service.ProfileValidationError,
                    "install, uninstall, or profile update",
                ),
            ):
                onboarding.initialize(["technical"], force=True)

        self.assertEqual(profile_path.read_bytes(), before[0])
        self.assertEqual(source_path.read_bytes(), before[1])

    def test_pack_selection_follows_future_catalog_sources(self):
        public = {
            "packs": [{"id": "engineering"}, {"id": "design"}],
            "sources": [
                {"id": "existing", "packs": ["engineering"], "enabled": False},
                {"id": "other", "packs": ["design"], "enabled": True},
            ],
        }
        (self.root / "config" / "sources.json").write_text(json.dumps(public))
        (self.root / "config" / "sources.local.json").write_text(
            json.dumps({"selected_packs": ["engineering"], "sources": []})
        )
        with patch.object(config, "PROJECT_ROOT", self.root), patch.dict(
            os.environ, {}, clear=True
        ):
            self.assertEqual([source["id"] for source in config.load_sources()], ["existing"])
            public["sources"].append(
                {"id": "future", "packs": ["engineering"], "enabled": False}
            )
            (self.root / "config" / "sources.json").write_text(json.dumps(public))
            self.assertEqual(
                [source["id"] for source in config.load_sources()],
                ["existing", "future"],
            )

    def test_higher_pack_selection_preserves_lower_per_source_override(self):
        public = {
            "packs": [{"id": "engineering"}, {"id": "design"}],
            "sources": [
                {"id": "engineer", "packs": ["engineering"], "enabled": True},
                {"id": "designer", "packs": ["design"], "enabled": False},
            ],
        }
        (self.root / "config" / "sources.json").write_text(json.dumps(public))
        (self.root / "config" / "sources.local.json").write_text(
            json.dumps(
                {
                    "selected_packs": ["engineering"],
                    "sources": [{"id": "designer", "enabled": False}],
                }
            )
        )
        environment_registry = self.root / "environment-sources.json"
        environment_registry.write_text(
            json.dumps({"selected_packs": ["design"], "sources": []})
        )
        with patch.object(config, "PROJECT_ROOT", self.root), patch.dict(
            os.environ,
            {"OPPORTUNITY_RADAR_SOURCES": str(environment_registry)},
            clear=True,
        ):
            sources = config.load_sources(include_disabled=True)
        enabled = {source["id"]: source["enabled"] for source in sources}
        self.assertEqual(enabled, {"engineer": False, "designer": False})

    def test_removed_catalog_source_does_not_survive_as_a_stale_local_override(self):
        public = {
            "packs": [{"id": "engineering"}],
            "sources": [
                {
                    "id": "current",
                    "name": "Current Example",
                    "kind": "watch_page",
                    "url": "https://example.org/current",
                    "packs": ["engineering"],
                    "enabled": False,
                }
            ],
        }
        local = {
            "schema_version": 2,
            "selected_packs": ["engineering"],
            "sources": [
                {"id": "retired", "enabled": True},
                {
                    "id": "private_addition",
                    "name": "Private Example",
                    "kind": "watch_page",
                    "url": "https://example.net/opportunities",
                    "packs": ["engineering"],
                    "enabled": True,
                },
            ],
        }
        (self.root / "config" / "sources.json").write_text(json.dumps(public))
        (self.root / "config" / "sources.local.json").write_text(json.dumps(local))
        with patch.object(config, "PROJECT_ROOT", self.root), patch.dict(
            os.environ, {}, clear=True
        ):
            sources = config.load_sources(include_disabled=True)
        self.assertEqual([source["id"] for source in sources], ["current", "private_addition"])
        self.assertTrue(all(source.get("name") and source.get("kind") for source in sources))

    def test_private_state_link_requires_recognized_runtime(self):
        state = self.root / "data" / "opportunities.sqlite3"
        state.parent.mkdir()
        unrelated = self.root / "unrelated" / "data" / "opportunities.sqlite3"
        unrelated.parent.mkdir(parents=True)
        unrelated.write_text("not a database")
        state.symlink_to(unrelated)
        with self.assertRaisesRegex(ValueError, "runtime"):
            config.resolve_private_state_path(
                state,
                "data",
                "opportunities.sqlite3",
            )


class ProfileSemanticReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        (self.root / "config").mkdir()
        (self.root / "config" / "profile.json").write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "priority_organizations": [],
                    "matching": {
                        "engine": "structured_v2",
                        "base_score": 25,
                        "minimum_display_score": 40,
                        "tier_thresholds": {
                            "priority": 75,
                            "strong": 60,
                            "watch": 40,
                        },
                        "rules": [],
                    },
                    "documents": {"default": "General", "routes": []},
                }
            ),
            encoding="utf-8",
        )
        (self.root / "config" / "sources.json").write_text(
            json.dumps(
                {
                    "packs": [
                        {"id": "research", "default": True},
                        {"id": "finance"},
                    ],
                    "sources": [
                        {
                            "id": "mixed",
                            "name": "Mixed opportunities",
                            "kind": "watch_page",
                            "url": "https://example.org/mixed",
                            "packs": ["research"],
                        },
                        {
                            "id": "finance_only",
                            "name": "Finance opportunities",
                            "kind": "watch_page",
                            "url": "https://example.org/finance",
                            "packs": ["finance"],
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        (self.root / "config" / "sources.local.json").write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "selected_packs": ["research"],
                    "sources": [],
                }
            ),
            encoding="utf-8",
        )
        self.lifecycle = (
            self.root
            / "Application Support"
            / ".OpportunityRadar.lifecycle-lock"
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def _context(self):
        return (
            patch.object(config, "PROJECT_ROOT", self.root),
            patch.object(
                profile_service,
                "_lifecycle_lock_path",
                return_value=self.lifecycle,
            ),
            patch.dict(os.environ, {}, clear=True),
        )

    @staticmethod
    def _rule(rule_id, terms):
        return {
            "id": rule_id,
            "label": rule_id.replace("_", " ").title(),
            "weight": 30,
            "fields": ["title", "category", "description"],
            "terms": terms,
            "match": "any",
            "per_term": False,
            "dimension": "interest",
            "anchor": True,
            "hard_gate": False,
        }

    def test_schema_migration_retires_off_domain_rules_and_refreshes_existing_rows(self):
        local_profile = {
            "schema_version": 2,
            "targets": {
                "opportunity_types": ["job"],
                "cycles": [],
                "role_families": ["field technician", "data engineer"],
                "domains": ["marine ecology", "geospatial analysis"],
                "supporting_skills": [],
                "locations": [],
                "exclusions": [],
                "work_arrangements": [],
            },
            "matching": {
                "engine": "structured_v2",
                "base_score": 25,
                "minimum_display_score": 40,
                "tier_thresholds": {
                    "priority": 75,
                    "strong": 60,
                    "watch": 40,
                },
                "rules": [
                    self._rule(
                        "retail_merchandising",
                        ["retail", "store assortment"],
                    ),
                    self._rule(
                        "retail_geospatial_crossover",
                        ["store location", "geospatial analysis"],
                    ),
                    self._rule("data_role", ["data engineer"]),
                ],
            },
        }
        (self.root / "config" / "profile.local.json").write_text(
            json.dumps(local_profile),
            encoding="utf-8",
        )

        database = Database(self.root / "data" / "opportunities.sqlite3")
        database.initialize()
        old_effective = {
            **json.loads(
                (self.root / "config" / "profile.json").read_text(encoding="utf-8")
            ),
            **local_profile,
        }
        for source_id in ("mixed", "finance_only"):
            database.sync_source(
                {
                    "id": source_id,
                    "name": source_id,
                    "kind": "watch_page",
                    "url": "https://example.org/{}".format(source_id),
                    "enabled": True,
                }
            )
        off_target = Opportunity(
            "mixed",
            "retail-planner",
            "Retail Merchandise Planner",
            "Example Stores",
            "https://example.org/mixed/retail-planner",
            opportunity_type="job",
        )
        crossover = Opportunity(
            "mixed",
            "retail-geospatial",
            "Geospatial Store Location Analyst",
            "Example Stores",
            "https://example.org/mixed/retail-geospatial",
            opportunity_type="job",
        )
        retired_source = Opportunity(
            "finance_only",
            "prior-source-row",
            "Retail Merchandise Planner",
            "Old Disabled Source",
            "https://example.org/finance/prior-source-row",
            opportunity_type="job",
        )
        old_effective["schema_version"] = 3
        for item in (off_target, crossover, retired_source):
            score_opportunity(item, old_effective)
            database.upsert_opportunity(item)
        self.assertNotEqual(off_target.tier, "skip")
        database.close()

        rendered = {}

        def capture(payload, profile):
            rendered["payload"] = payload
            rendered["profile"] = profile
            return self.root / "dashboard" / "index.html"

        with self._context()[0], self._context()[1], self._context()[2], patch(
            "monitor.dashboard.render_dashboard",
            side_effect=capture,
        ):
            payload = profile_service.profile_editor_payload()
            self.assertEqual(
                [rule["id"] for rule in payload["matching"]["rules"]],
                ["retail_geospatial_crossover", "data_role"],
            )
            output = io.StringIO()
            with (
                patch.object(cli.sys, "stdin", io.StringIO(json.dumps(payload))),
                redirect_stdout(output),
            ):
                self.assertEqual(
                    cli.main(["profile", "apply", "--stdin"]),
                    0,
                )
            result = json.loads(output.getvalue())

        self.assertTrue(result["saved"])
        self.assertTrue(result["profile_adjustments"]["semantic_schema_upgraded"])
        self.assertEqual(
            [entry["id"] for entry in result["profile_adjustments"]["retired_matching_rules"]],
            ["retail_merchandising"],
        )
        saved = json.loads(
            (self.root / "config" / "profile.local.json").read_text(encoding="utf-8")
        )
        self.assertEqual(saved["schema_version"], 3)
        self.assertEqual(
            [rule["id"] for rule in saved["matching"]["rules"]],
            ["retail_geospatial_crossover", "data_role"],
        )
        visible_titles = {
            item["title"] for item in rendered["payload"]["opportunities"]
        }
        self.assertNotIn("Retail Merchandise Planner", visible_titles)
        self.assertIn("Geospatial Store Location Analyst", visible_titles)
        refreshed = Database(self.root / "data" / "opportunities.sqlite3")
        try:
            pure_row = refreshed.connection.execute(
                "SELECT tier FROM opportunities WHERE external_id='retail-planner'"
            ).fetchone()
            finance_source = refreshed.connection.execute(
                "SELECT enabled FROM sources WHERE id='finance_only'"
            ).fetchone()
            self.assertEqual(pure_row["tier"], "skip")
            self.assertEqual(finance_source["enabled"], 0)
        finally:
            refreshed.close()

    def test_removing_a_domain_retires_only_single_domain_rules(self):
        local_profile = {
            "schema_version": 3,
            "targets": {
                "opportunity_types": [],
                "cycles": [],
                "role_families": [],
                "domains": ["retail operations", "geospatial analysis"],
                "supporting_skills": [],
                "locations": [],
                "exclusions": [],
                "work_arrangements": [],
            },
            "matching": {
                "engine": "structured_v2",
                "base_score": 25,
                "minimum_display_score": 40,
                "tier_thresholds": {
                    "priority": 75,
                    "strong": 60,
                    "watch": 40,
                },
                "rules": [
                    self._rule("retail_focus", ["retail operations"]),
                    self._rule(
                        "retail_geospatial_crossover",
                        ["retail operations", "geospatial analysis"],
                    ),
                ],
            },
        }
        (self.root / "config" / "profile.local.json").write_text(
            json.dumps(local_profile),
            encoding="utf-8",
        )

        with self._context()[0], self._context()[1], self._context()[2]:
            payload = profile_service.profile_editor_payload()
            payload["targets"]["domains"] = ["geospatial analysis"]
            result = profile_service.apply_editor_payload(payload, rebuild=False)

        self.assertFalse(result["profile_adjustments"]["semantic_schema_upgraded"])
        self.assertEqual(
            [entry["id"] for entry in result["profile_adjustments"]["retired_matching_rules"]],
            ["retail_focus"],
        )
        saved = json.loads(
            (self.root / "config" / "profile.local.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            [rule["id"] for rule in saved["matching"]["rules"]],
            ["retail_geospatial_crossover"],
        )

    def test_removing_last_domain_retires_its_rule_and_keeps_independent_rules(self):
        local_profile = {
            "schema_version": 3,
            "targets": {
                "opportunity_types": [],
                "cycles": [],
                "role_families": [],
                "domains": ["finance"],
                "supporting_skills": [],
                "locations": [],
                "exclusions": [],
                "work_arrangements": [],
            },
            "matching": {
                "engine": "structured_v2",
                "base_score": 25,
                "minimum_display_score": 40,
                "tier_thresholds": {
                    "priority": 75,
                    "strong": 60,
                    "watch": 40,
                },
                "rules": [
                    self._rule("quant_finance", ["quantitative finance"]),
                    self._rule("independent_marine", ["marine ecology"]),
                ],
            },
        }
        (self.root / "config" / "profile.local.json").write_text(
            json.dumps(local_profile),
            encoding="utf-8",
        )

        with self._context()[0], self._context()[1], self._context()[2]:
            payload = profile_service.profile_editor_payload()
            payload["targets"]["domains"] = []
            result = profile_service.apply_editor_payload(payload, rebuild=False)

        self.assertEqual(
            [
                entry["id"]
                for entry in result["profile_adjustments"]["retired_matching_rules"]
            ],
            ["quant_finance"],
        )
        saved = json.loads(
            (self.root / "config" / "profile.local.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            [rule["id"] for rule in saved["matching"]["rules"]],
            ["independent_marine"],
        )

    def test_removing_a_role_keeps_a_rule_that_still_matches_a_selected_role(self):
        local_profile = {
            "schema_version": 3,
            "targets": {
                "opportunity_types": [],
                "cycles": [],
                "role_families": ["store planner", "data engineer"],
                "domains": [],
                "supporting_skills": [],
                "locations": [],
                "exclusions": [],
                "work_arrangements": [],
            },
            "matching": {
                "engine": "structured_v2",
                "base_score": 25,
                "minimum_display_score": 40,
                "tier_thresholds": {
                    "priority": 75,
                    "strong": 60,
                    "watch": 40,
                },
                "rules": [
                    self._rule("store_role", ["store planner"]),
                    self._rule(
                        "planning_crossover",
                        ["store planner", "data engineer"],
                    ),
                ],
            },
        }
        (self.root / "config" / "profile.local.json").write_text(
            json.dumps(local_profile),
            encoding="utf-8",
        )

        with self._context()[0], self._context()[1], self._context()[2]:
            payload = profile_service.profile_editor_payload()
            payload["targets"]["role_families"] = ["data engineer"]
            result = profile_service.apply_editor_payload(payload, rebuild=False)

        self.assertEqual(
            [entry["id"] for entry in result["profile_adjustments"]["retired_matching_rules"]],
            ["store_role"],
        )
        saved = json.loads(
            (self.root / "config" / "profile.local.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            [rule["id"] for rule in saved["matching"]["rules"]],
            ["planning_crossover"],
        )


if __name__ == "__main__":
    unittest.main()
