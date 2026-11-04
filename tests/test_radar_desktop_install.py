"""Safety checks for updating the existing Finder shortcut."""

import importlib.util
import json
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / "extras" / "radar-desktop" / "install.py"
SPEC = importlib.util.spec_from_file_location("radar_desktop_install", SOURCE)
INSTALL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSTALL)


def app(path, identifier):
    info = path / "Contents" / "Info.plist"
    info.parent.mkdir(parents=True)
    info.write_bytes(plistlib.dumps({"CFBundleIdentifier": identifier}))
    return path


class BundledRuntimeTests(unittest.TestCase):
    def test_public_payload_runs_without_the_checkout_on_its_import_path(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            runtime = directory / "Independent app resources"
            INSTALL.copy_runtime(runtime)
            packaged = {
                str(path.relative_to(runtime))
                for path in runtime.rglob("*")
                if path.is_file()
            }
            self.assertEqual(packaged, set(INSTALL.RUNTIME_FILES))
            self.assertTrue(os.access(runtime / "scripts/open_radar.sh", os.X_OK))
            workspace = directory / "sample.sqlite3"
            env = {**os.environ, "RADAR_WORKSPACE": str(workspace)}
            env.pop("PYTHONPATH", None)

            def request(op, **args):
                process = subprocess.run(
                    [sys.executable, "-B", "-s", "-m", "radar.desktop_bridge"],
                    cwd=runtime,
                    env=env,
                    input=json.dumps({"op": op, "args": args}) + "\n",
                    text=True,
                    capture_output=True,
                    check=True,
                    timeout=15,
                )
                output = json.loads(process.stdout)
                self.assertTrue(output["ok"], output)
                return output["result"]

            request("sample")
            self.assertGreater(len(request("feed", view="for-you")["items"]), 0)
            path = directory / "Profiles with spaces.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "active_profile": "Scientific computing",
                        "profiles": [
                            {
                                "name": "Scientific computing",
                                "rules": {"prefer": ["research"]},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            preview = request("preview_profiles", path=str(path))
            self.assertEqual(request("goals"), [])
            request(
                "import_profiles", path=str(path), expected_revision=preview["revision"]
            )
            self.assertEqual(
                request("feed", view="for-you")["goal"], "Scientific computing"
            )
            self.assertTrue(workspace.is_file())


class DesktopShortcutTests(unittest.TestCase):
    def test_retarget_preserves_both_app_bundles_and_is_reversible(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            legacy = app(directory / "old.app", INSTALL.LEGACY_ID)
            rebuilt = app(directory / "new.app", INSTALL.BUNDLE_ID)
            shortcut = directory / "Opportunity Radar.app"
            shortcut.symlink_to(legacy)
            with patch.object(INSTALL, "verify_bundle") as verify:
                INSTALL.retarget(shortcut, rebuilt)
                self.assertTrue(shortcut.is_symlink())
                self.assertEqual(shortcut.resolve(), rebuilt.resolve())
                verify.assert_called_once_with(rebuilt.resolve())
                INSTALL.retarget(shortcut, legacy)
            self.assertEqual(shortcut.resolve(), legacy.resolve())
            self.assertTrue(legacy.is_dir())
            self.assertTrue(rebuilt.is_dir())

    def test_refuses_foreign_shortcut_or_unrelated_desktop_file(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            unrelated = app(directory / "other.app", "org.other.app")
            rebuilt = app(directory / "new.app", INSTALL.BUNDLE_ID)
            shortcut = directory / "Opportunity Radar.app"
            shortcut.symlink_to(unrelated)
            with patch.object(INSTALL, "verify_bundle"):
                with self.assertRaisesRegex(RuntimeError, "another application"):
                    INSTALL.retarget(shortcut, rebuilt)
                self.assertEqual(shortcut.resolve(), unrelated.resolve())
                shortcut.unlink()
                shortcut.write_text("Keep this file", encoding="utf-8")
                with self.assertRaisesRegex(RuntimeError, "other than a shortcut"):
                    INSTALL.retarget(shortcut, rebuilt)
                self.assertEqual(shortcut.read_text(encoding="utf-8"), "Keep this file")

    def test_failed_bundle_verification_does_not_retarget(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            legacy = app(directory / "old.app", INSTALL.LEGACY_ID)
            rebuilt = app(directory / "new.app", INSTALL.BUNDLE_ID)
            shortcut = directory / "Opportunity Radar.app"
            shortcut.symlink_to(legacy)
            with patch.object(
                INSTALL, "verify_bundle", side_effect=RuntimeError("Invalid signature")
            ):
                with self.assertRaisesRegex(RuntimeError, "Invalid signature"):
                    INSTALL.retarget(shortcut, rebuilt)
            self.assertEqual(shortcut.resolve(), legacy.resolve())


if __name__ == "__main__":
    unittest.main()
