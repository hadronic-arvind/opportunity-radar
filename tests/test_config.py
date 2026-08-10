import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from monitor import config
from monitor import onboarding


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
        self.assertEqual(profile["selected_source_packs"], ["starter-diverse"])

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
            json.dumps({"sources": [{"id": "base", "enabled": False}, {"id": "local", "enabled": True}]}),
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
            {"id": "one", "packs": ["starter-diverse"]},
            {"id": "two", "packs": ["engineering"]},
        ]
        packs = [
            {"id": "starter-diverse", "default": True},
            {"id": "engineering"},
        ]
        with (
            patch("monitor.onboarding.project_path", side_effect=lambda *parts: self.root.joinpath(*parts)),
            patch("monitor.onboarding.load_sources", return_value=sources),
            patch("monitor.onboarding.load_source_packs", return_value=packs),
        ):
            result = onboarding.initialize(
                ["engineering"],
                include_terms=["distributed systems"],
                default_document="Software",
            )
            self.assertEqual(result["enabled_sources"], 1)
            profile = json.loads((self.root / "config" / "profile.local.json").read_text())
            registry = json.loads((self.root / "config" / "sources.local.json").read_text())
            self.assertEqual(profile["selected_source_packs"], ["engineering"])
            self.assertEqual(profile["documents"]["default"], "Software")
            self.assertEqual(registry["selected_packs"], ["engineering"])
            self.assertEqual(registry["sources"], [])
            for name in ("profile.local.json", "sources.local.json"):
                self.assertEqual((self.root / "config" / name).stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                onboarding.initialize(["starter-diverse"])

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


if __name__ == "__main__":
    unittest.main()
