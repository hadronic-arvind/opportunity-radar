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
    def test_configuration_guide_documents_supported_source_contracts(self):
        project = Path(__file__).resolve().parents[1]
        guide = (project / "docs" / "CONFIGURATION.md").read_text(encoding="utf-8")
        for heading in (
            "### Greenhouse",
            "### Lever",
            "### Jibe",
            "### HTML links",
            "### Watch pages",
            "### Custom packs",
        ):
            with self.subTest(heading=heading):
                self.assertIn(heading, guide)
        for field in (
            "`board`",
            "`site`",
            "`api_url`",
            "`job_url_template`",
            "`same_domain`",
            "`publish_as_opportunity`",
            "`notify_page_changes`",
            "`expected_http_statuses`",
            "`selected_packs`",
        ):
            with self.subTest(field=field):
                self.assertIn(field, guide)

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

    def test_privacy_gate_rejects_tailored_staged_public_profile(self):
        module = load_privacy_module()
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "config").mkdir()
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            public_profile = root / "config" / "profile.json"
            public_profile.write_text(
                json.dumps(
                    {
                        "candidate": {
                            "name": "Example Candidate",
                            "program": "Example doctoral program",
                            "completed_degrees": ["Example degree"],
                        },
                        "priority_organizations": ["Example Laboratory"],
                    }
                ),
                encoding="utf-8",
            )
            subprocess.run(
                ["git", "add", "config/profile.json"], cwd=root, check=True
            )
            public_profile.write_text(
                json.dumps({"matching": {"rules": []}}), encoding="utf-8"
            )

            module.PROJECT_ROOT = root
            failures = module.scan(include_history=True)

            self.assertIn("tailored public profile is publishable", failures)

    def test_privacy_gate_rejects_targeted_staged_public_profile(self):
        module = load_privacy_module()
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "config").mkdir()
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            public_profile = root / "config" / "profile.json"
            public_profile.write_text(
                json.dumps(
                    {
                        "timeframes": ["Summer 2030"],
                        "targets": {
                            "role_families": ["Rare personal work"],
                            "locations": ["Private place"],
                        },
                        "matching": {"rules": []},
                    }
                ),
                encoding="utf-8",
            )
            subprocess.run(
                ["git", "add", "config/profile.json"], cwd=root, check=True
            )
            public_profile.write_text(
                json.dumps({"matching": {"rules": []}}), encoding="utf-8"
            )

            module.PROJECT_ROOT = root

            self.assertIn(
                "tailored public profile is publishable",
                module.scan(include_history=True),
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

    def test_privacy_gate_rejects_staged_and_untracked_symbolic_links(self):
        module = load_privacy_module()
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            staged = root / "staged-resume-link"
            untracked = root / "untracked-resume-link"
            private_prefix = "../Desk" + "top/" + "Work/resumes/"
            staged.symlink_to(private_prefix + "private.pdf")
            untracked.symlink_to(private_prefix + "private-draft.pdf")
            subprocess.run(
                ["git", "add", "staged-resume-link"], cwd=root, check=True
            )

            module.PROJECT_ROOT = root
            failures = module.scan()

            self.assertIn(
                "symbolic link is publishable: staged-resume-link", failures
            )
            self.assertIn(
                "symbolic link is publishable: untracked-resume-link", failures
            )

    def test_privacy_gate_rejects_quoted_local_labels_and_matching_rule(self):
        module = load_privacy_module()
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "config").mkdir()
            (root / "config" / "profile.json").write_text(
                json.dumps({"documents": {"default": "General"}}),
                encoding="utf-8",
            )
            (root / "config" / "profile.local.json").write_text(
                json.dumps(
                    {
                        "documents": {
                            "default": "Private Research CV",
                            "routes": [
                                {
                                    "label": "Private Systems Draft",
                                    "terms": [
                                        "private accelerator route",
                                        "rare trigger pipeline",
                                    ],
                                }
                            ],
                        },
                        "dashboard": {"document_label": "Private document map"},
                        "priority_organizations": [
                            "Quiet Example Laboratory",
                            "Private Systems Cooperative",
                        ],
                        "matching": {
                            "rules": [
                                {
                                    "label": "Private detector preference",
                                    "terms": [
                                        "rare lattice workflow",
                                        "private detector stack",
                                    ],
                                }
                            ]
                        },
                    }
                ),
                encoding="utf-8",
            )
            (root / "README.md").write_text(
                'Use "Private Research CV" and "Private Systems Draft" under '
                '"Private document map" for "rare lattice workflow" and '
                '"private detector stack". The private organization list is '
                '"Quiet Example Laboratory" and "Private Systems Cooperative".\n',
                encoding="utf-8",
            )
            module.PROJECT_ROOT = root
            failures = module.scan()
            self.assertIn("local application label in README.md", failures)
            self.assertIn("local matching rule copied into README.md", failures)

    def test_privacy_gate_reads_runtime_only_private_values_and_labels(self):
        module = load_privacy_module()
        with tempfile.TemporaryDirectory() as tempdir:
            base = Path(tempdir)
            project = base / "project"
            runtime = base / "runtime"
            for directory in (
                project,
                project / "config",
                project / "data",
                runtime,
                runtime / "monitor",
                runtime / "config",
                runtime / "dashboard",
                runtime / "data",
            ):
                directory.mkdir(exist_ok=True)
            for directory in (
                runtime,
                runtime / "monitor",
                runtime / "config",
                runtime / "dashboard",
                runtime / "data",
            ):
                directory.chmod(0o700)

            markers = (
                runtime / "monitor" / "__main__.py",
                runtime / "config" / "profile.json",
                runtime / "dashboard" / "template.html",
                runtime / "dashboard" / "styles.css",
                runtime / "dashboard" / "app.js",
            )
            for marker in markers:
                marker.write_text(
                    "{}" if marker.suffix == ".json" else "marker",
                    encoding="utf-8",
                )
                marker.chmod(0o600)

            database = runtime / "data" / "opportunities.sqlite3"
            database.write_text("private database", encoding="utf-8")
            database.chmod(0o600)
            runtime_profile = runtime / "config" / "profile.local.json"
            runtime_profile.write_text(
                json.dumps(
                    {
                        "candidate": {"program": "Runtime-only astronomy cohort"},
                        "documents": {
                            "default": "Runtime-only tailored document",
                        },
                    }
                ),
                encoding="utf-8",
            )
            runtime_profile.chmod(0o600)

            (project / "config" / "profile.json").write_text(
                json.dumps({"documents": {"default": "General"}}),
                encoding="utf-8",
            )
            (project / "data" / "opportunities.sqlite3").symlink_to(database)
            (project / "README.md").write_text(
                'Runtime-only astronomy cohort uses "Runtime-only tailored document".\n',
                encoding="utf-8",
            )

            module.PROJECT_ROOT = project
            failures = module.scan()

            self.assertIn("local profile value in README.md", failures)
            self.assertIn("local application label in README.md", failures)
            self.assertTrue(all(str(runtime) not in failure for failure in failures))

    def test_history_gate_rejects_old_tailored_public_profile(self):
        module = load_privacy_module()
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "config").mkdir()
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            profile = {
                "timeframes": ["Summer 2030"],
                "targets": {
                    "role_families": ["Rare personal work"],
                    "locations": ["Private place"],
                },
                "matching": {"rules": []},
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
