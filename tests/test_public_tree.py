import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


def load_privacy_module():
    project = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "opportunity_radar_privacy_check",
        project / "scripts" / "privacy_check.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PublicTreeTests(unittest.TestCase):
    def test_publication_set_passes_privacy_gate(self):
        project = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, str(project / "scripts" / "privacy_check.py")],
            cwd=project,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_privacy_gate_reads_staged_binary_bytes(self):
        module = load_privacy_module()
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            candidate = root / "artifact.bin"
            candidate.write_bytes(
                b"\xff\x00/" + b"Users" + b"/example/private-state\x00"
            )
            subprocess.run(["git", "add", "artifact.bin"], cwd=root, check=True)
            candidate.write_bytes(b"safe working tree")
            module.PROJECT_ROOT = root
            self.assertTrue(
                any("absolute macOS home path" in failure for failure in module.scan())
            )

    def test_privacy_gate_reads_unstaged_tracked_bytes(self):
        module = load_privacy_module()
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            candidate = root / "tracked.txt"
            candidate.write_text("safe indexed content", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=root, check=True)
            candidate.write_text(
                "/" + "Users" + "/example/private-state", encoding="utf-8"
            )
            module.PROJECT_ROOT = root
            self.assertTrue(
                any("absolute macOS home path" in failure for failure in module.scan())
            )

    def test_privacy_gate_rejects_force_tracked_private_paths(self):
        module = load_privacy_module()
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / ".gitignore").write_text(".env*\nseed/\n")
            (root / ".env.local").write_text("EXAMPLE=private\n")
            (root / "seed").mkdir()
            (root / "seed" / "private.md").write_text("private\n")
            subprocess.run(
                ["git", "add", "-f", ".env.local", "seed/private.md"],
                cwd=root,
                check=True,
            )
            module.PROJECT_ROOT = root
            failures = module.scan()
            self.assertTrue(any(".env.local" in failure for failure in failures))
            self.assertTrue(any("seed/private.md" in failure for failure in failures))

    def test_history_gate_rejects_old_tailored_public_profile(self):
        module = load_privacy_module()
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "config").mkdir()
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            profile = {
                "matching": {
                    "rules": [{"label": "Private preference", "terms": ["example"]}]
                }
            }
            (root / "config" / "profile.json").write_text(json.dumps(profile))
            subprocess.run(["git", "add", "config/profile.json"], cwd=root, check=True)
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=Test User",
                    "-c",
                    "user.email=test@example.invalid",
                    "commit",
                    "-qm",
                    "private history",
                ],
                cwd=root,
                check=True,
            )
            (root / "config" / "profile.json").write_text(
                json.dumps({"matching": {"rules": []}})
            )
            subprocess.run(["git", "add", "config/profile.json"], cwd=root, check=True)
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=Test User",
                    "-c",
                    "user.email=test@example.invalid",
                    "commit",
                    "-qm",
                    "generic profile",
                ],
                cwd=root,
                check=True,
            )
            module.PROJECT_ROOT = root
            self.assertTrue(
                any("tailored public profile" in failure for failure in module.history_failures())
            )


if __name__ == "__main__":
    unittest.main()
