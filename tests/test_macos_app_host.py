import platform
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT_ROOT / "extras" / "macos-app" / "OpportunityRadar.swift"


class MacOSNativeHostSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE.read_text(encoding="utf-8")

    def test_uses_private_static_webkit_host_without_a_server_or_polling(self):
        self.assertIn("import WebKit", self.source)
        self.assertIn("websiteDataStore = .nonPersistent()", self.source)
        self.assertIn("loadFileURL(", self.source)
        self.assertIn("applicationShouldTerminateAfterLastWindowClosed", self.source)
        for forbidden in (
            "URLSession",
            "NWListener",
            "HTTPServer",
            "localhost",
            "Timer.scheduledTimer",
            "DispatchSource.makeTimerSource",
        ):
            self.assertNotIn(forbidden, self.source)

    def test_bridge_contract_and_cli_argv_are_fixed(self):
        self.assertIn('private let bridgeName = "opportunityRadar"', self.source)
        self.assertIn("message.frameInfo.isMainFrame", self.source)
        self.assertIn("configuration.isExactDashboardURL(message.frameInfo.request.url)", self.source)
        self.assertIn('["due", "all"].contains(mode)', self.source)
        self.assertIn('["-m", "monitor", "scan", "--quiet", "--force"]', self.source)
        self.assertIn('["-m", "monitor", "scan", "--quiet"]', self.source)
        self.assertIn('"monitor", "status", identifier, value, "--quiet"', self.source)
        self.assertIn('"monitor",\n                "bookmark",', self.source)
        self.assertIn("ApplicationStatus(rawValue: value) != nil", self.source)
        self.assertIn('let bookmarked = bridgeBoolean(payload["bookmarked"])', self.source)
        self.assertIn('let theme = Theme(rawValue: value)', self.source)
        self.assertIn("window.OpportunityRadarNative?.complete(", self.source)
        self.assertIn("window.OpportunityRadarNative?.setTheme(", self.source)

    def test_runtime_python_lookup_and_process_boundary_are_explicit(self):
        self.assertIn('appendingPathComponent("python-path"', self.source)
        self.assertIn("process.executableURL = try configuration.pythonExecutable()", self.source)
        self.assertIn("process.arguments = arguments", self.source)
        self.assertIn("process.environment = commandEnvironment()", self.source)
        self.assertIn('"PYTHONNOUSERSITE": "1"', self.source)
        self.assertIn('environment["OPPORTUNITY_RADAR_CURATED_PATH"]', self.source)
        self.assertIn("readabilityHandler", self.source)
        self.assertNotIn("ProcessInfo.processInfo.environment", self.source)
        self.assertNotIn('executableURL = URL(fileURLWithPath: "/bin/sh")', self.source)

    @unittest.skipUnless(platform.system() == "Darwin", "native app compiles only on macOS")
    def test_native_source_compiles_with_appkit_and_webkit(self):
        xcrun = shutil.which("xcrun")
        self.assertIsNotNone(xcrun)
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            completed = subprocess.run(
                [
                    xcrun,
                    "swiftc",
                    "-module-cache-path",
                    str(root / "module-cache"),
                    "-O",
                    "-framework",
                    "AppKit",
                    "-framework",
                    "WebKit",
                    str(SOURCE),
                    "-o",
                    str(root / "opportunity-radar"),
                ],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
