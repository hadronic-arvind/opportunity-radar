import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from monitor.dashboard import APP_MARKER, DATA_MARKER, STYLE_MARKER, render_dashboard, safe_external_url
from monitor.config import PRIVATE_RUNTIME_MARKERS


class DashboardTests(unittest.TestCase):
    def copy_dashboard_assets(self, destination):
        source = Path(__file__).resolve().parents[1] / "dashboard"
        for name in ("template.html", "styles.css", "app.js"):
            (destination / name).write_text(
                (source / name).read_text(encoding="utf-8"), encoding="utf-8"
            )

    def test_external_url_policy(self):
        self.assertEqual(safe_external_url("https://example.com/job"), "https://example.com/job")
        self.assertEqual(safe_external_url("HTTP://example.com/job"), "HTTP://example.com/job")
        for value in ("javascript:alert(1)", "data:text/html,x", "file:///tmp/x", "//example.com", ""):
            self.assertEqual(safe_external_url(value), "")

    def test_render_is_atomic_private_and_script_safe(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "dashboard").mkdir()
            self.copy_dashboard_assets(root / "dashboard")
            payload = {
                "generated_at": "2026-08-09T00:00:00+00:00",
                "counts": {},
                "runs": [],
                "sources": [],
                "events": [{"title": "Changed", "url": "file:///tmp/private"}],
                "opportunities": [
                    {
                        "id": "x",
                        "title": "</script><img src=x onerror=alert(1)>",
                        "organization": "Example",
                        "url": "javascript:alert(1)",
                    }
                ],
            }
            with patch("monitor.dashboard.project_path", side_effect=lambda *parts: root.joinpath(*parts)):
                output = render_dashboard(payload, profile={"dashboard": {"title": "Test"}})
            rendered = output.read_text(encoding="utf-8")
            self.assertIn('http-equiv="Content-Security-Policy"', rendered)
            self.assertIn("connect-src 'none'", rendered)
            self.assertNotIn("'unsafe-inline'", rendered)
            self.assertIn("\\u003c/script>\\u003cimg", rendered)
            self.assertNotIn('"url":"javascript:', rendered)
            self.assertNotIn('"url":"file:', rendered)
            self.assertEqual(os.stat(output).st_mode & 0o777, 0o600)
            for marker in (STYLE_MARKER, DATA_MARKER, APP_MARKER):
                self.assertNotIn(marker, rendered)
            self.assertNotIn("innerHTML", rendered)
            nonces = re.findall(r'nonce="([A-Za-z0-9_-]+)"', rendered)
            self.assertGreaterEqual(len(nonces), 3)
            self.assertEqual(len(set(nonces)), 1)
            self.assertIn("'nonce-{}'".format(nonces[0]), rendered)

    def test_template_requires_exactly_one_marker(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "dashboard").mkdir()
            (root / "dashboard" / "template.html").write_text("no marker", encoding="utf-8")
            with patch("monitor.dashboard.project_path", side_effect=lambda *parts: root.joinpath(*parts)):
                with self.assertRaises(ValueError):
                    render_dashboard({}, profile={})

    def test_render_preserves_installed_dashboard_symlink(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            runtime = root / "runtime" / "dashboard"
            project = root / "project" / "dashboard"
            runtime.mkdir(parents=True)
            project.mkdir(parents=True)
            runtime.parent.chmod(0o700)
            runtime.chmod(0o700)
            for marker in PRIVATE_RUNTIME_MARKERS:
                marker_path = runtime.parent / marker
                marker_path.parent.mkdir(parents=True, exist_ok=True)
                marker_path.write_text("runtime marker", encoding="utf-8")
                marker_path.chmod(0o600)
            self.copy_dashboard_assets(project)
            target = runtime / "index.html"
            target.write_text("old", encoding="utf-8")
            target.chmod(0o600)
            output = project / "index.html"
            output.symlink_to(target)
            with patch("monitor.dashboard.project_path", side_effect=lambda *parts: root.joinpath("project", *parts)):
                rendered = render_dashboard({}, profile={})
            self.assertEqual(rendered, target.resolve())
            self.assertTrue(output.is_symlink())
            self.assertIn('id="opportunity-data"', target.read_text(encoding="utf-8"))

    def test_dashboard_progressively_renders_and_supports_native_actions(self):
        source = Path(__file__).resolve().parents[1] / "dashboard" / "app.js"
        script = source.read_text(encoding="utf-8")
        self.assertIn("const PAGE_SIZE = 36", script)
        self.assertIn("IntersectionObserver", script)
        self.assertIn("function appendNextPage()", script)
        self.assertIn('appendChild(fragment)', script)
        self.assertNotIn("state.limit", script)
        self.assertIn('action: "scan"', script)
        self.assertIn('action: "status"', script)
        self.assertIn('action: "bookmark"', script)
        self.assertIn("pendingRequest", script)
        self.assertIn("button.disabled = state.busy", script)
        self.assertIn("replaceChildren", script)
        self.assertNotIn("innerHTML", script)


if __name__ == "__main__":
    unittest.main()
