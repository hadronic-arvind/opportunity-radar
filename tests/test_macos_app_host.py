import platform
import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

from monitor.profile import MAX_EDITOR_BYTES


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
        self.assertIn('"monitor",\n                "profile",\n                "apply",', self.source)
        self.assertIn('"--stdin",', self.source)
        self.assertIn('let profile = payload["profile"] as? [String: Any]', self.source)
        self.assertIn("boundedProfileValue(profile)", self.source)
        self.assertIn("numeric.isFinite && abs(numeric) <= 1_000_000", self.source)
        self.assertEqual(MAX_EDITOR_BYTES, 256 * 1024)
        self.assertIn("private let maximumProfilePayloadBytes = 256 * 1024", self.source)
        self.assertIn("process.standardInput = standardInput.pipe", self.source)
        self.assertIn("standardInput?.start()", self.source)
        self.assertIn("ApplicationStatus(rawValue: value) != nil", self.source)
        self.assertIn('let bookmarked = bridgeBoolean(payload["bookmarked"])', self.source)
        self.assertIn('let theme = Theme(rawValue: value)', self.source)
        self.assertIn('validRequestID(requestID)', self.source)
        self.assertIn('response["request"] = requestID', self.source)
        self.assertIn("window.OpportunityRadarNative?.complete(", self.source)
        self.assertIn("window.OpportunityRadarNative?.setTheme(", self.source)
        self.assertIn("action == .scan || action == .profile || action == .source", self.source)

    def test_source_bridge_validates_a_bounded_public_url_and_uses_fixed_argv(self):
        self.assertIn("case source", self.source)
        self.assertIn('Set(["version", "action", "name", "url", "request"])', self.source)
        self.assertIn('let name = payload["name"] as? String', self.source)
        self.assertIn("validSourceName(name)", self.source)
        self.assertIn('let url = payload["url"] as? String', self.source)
        self.assertIn("validPublicHTTPSURL(url)", self.source)
        self.assertIn('(1...120).contains(value.count)', self.source)
        self.assertIn('(12...2_000).contains(value.utf8.count)', self.source)
        self.assertIn('components.scheme?.lowercased() == "https"', self.source)
        self.assertIn("components.user == nil", self.source)
        self.assertIn("components.password == nil", self.source)
        self.assertIn("host.contains(\".\")", self.source)
        self.assertIn('"local" + "host", "local", "internal", "test", "invalid", "example", "onion"', self.source)
        self.assertIn('"sources",\n                "add",\n                "--name",\n                name,\n                "--url",\n                url,', self.source)
        self.assertIn("private func sourceFailureMessage(_ diagnostics: CommandDiagnostics)", self.source)
        self.assertNotIn('arguments: ["-m", "monitor", "sources", "add", name, url]', self.source)

    def test_runtime_python_lookup_and_process_boundary_are_explicit(self):
        self.assertIn('appendingPathComponent("python-path"', self.source)
        self.assertIn('payload["pythonExecutable"] as? String', self.source)
        self.assertIn("return bundledPythonExecutable", self.source)
        self.assertIn("process.executableURL = try configuration.pythonExecutable()", self.source)
        self.assertIn("process.arguments = arguments", self.source)
        self.assertIn("process.environment = commandEnvironment()", self.source)
        self.assertIn('"PYTHONNOUSERSITE": "1"', self.source)
        self.assertIn('environment["OPPORTUNITY_RADAR_CURATED_PATH"]', self.source)
        self.assertIn("let data = try handle.read(upToCount: 8_192)", self.source)
        self.assertNotIn("ProcessInfo.processInfo.environment", self.source)
        self.assertNotIn('executableURL = URL(fileURLWithPath: "/bin/sh")', self.source)

    def test_command_io_is_bounded_async_and_closes_unused_pipe_ends(self):
        self.assertIn("private let maximumCapturedOutputBytes = 32 * 1024", self.source)
        self.assertIn("private final class BoundedOutputCapture", self.source)
        self.assertIn("captured = Data(data.suffix(maximumBytes))", self.source)
        self.assertIn("captured.removeFirst(overflow)", self.source)
        self.assertIn("standardOutput.closeParentWriteEnd()", self.source)
        self.assertIn("standardError.closeParentWriteEnd()", self.source)
        self.assertIn("private func closeParentReadEnd()", self.source)
        self.assertIn("closeParentReadEnd()", self.source)
        self.assertIn("private final class AsyncInputWriter", self.source)
        self.assertIn("writerQueue.async", self.source)
        self.assertIn("try pipe.fileHandleForWriting.write(contentsOf: input)", self.source)
        self.assertIn("inputSucceeded: standardInput?.finish() ?? true", self.source)
        self.assertNotIn("standardInput.fileHandleForWriting.write(input)", self.source)

    def test_profile_failures_are_classified_without_returning_raw_output(self):
        self.assertIn("private func profileFailureMessage(_ diagnostics: CommandDiagnostics)", self.source)
        self.assertIn("profile changed after it was opened", self.source)
        self.assertIn("Reload the dashboard and try again.", self.source)
        self.assertIn("busy with another scan or profile update", self.source)
        self.assertIn("could not be validated", self.source)
        self.assertIn("standardError: standardError.finish()", self.source)
        self.assertIn("standardOutput: standardOutput.finish()", self.source)
        self.assertNotIn("return diagnostics.standardError", self.source)
        self.assertNotIn("return diagnostics.standardOutput", self.source)

    def test_profile_save_is_queued_behind_an_active_scan(self):
        self.assertIn("private struct QueuedProfileCommand", self.source)
        self.assertIn("private var queuedProfileCommand: QueuedProfileCommand?", self.source)
        self.assertIn("if scanIsRunning || scanCompletionPending", self.source)
        self.assertIn("guard queuedProfileCommand == nil else", self.source)
        self.assertIn("queuedProfileCommand = QueuedProfileCommand(", self.source)
        self.assertIn("private func finishScanCompletion()", self.source)
        self.assertIn("input: queued.input", self.source)
        self.assertIn("guard runningCommand != nil || queuedProfileCommand != nil else", self.source)

    def test_failed_queued_profile_preserves_the_page_retry_draft(self):
        dashboard = (PROJECT_ROOT / "dashboard" / "app.js").read_text(encoding="utf-8")
        self.assertIn(
            "const wasQueuedProfile = action === \"profile\" && Boolean(state.queuedProfile)",
            dashboard,
        )
        self.assertIn(
            'wasQueuedProfile ? " Your edits are preserved here; retry to load the completed scan."',
            dashboard,
        )
        self.assertIn("state.profileRetryDraft = completedProfile", dashboard)
        self.assertNotIn("reloadAfterQueuedProfile", self.source)
        profile_completion = self.source[
            self.source.index("private func finishBridgeCommand("):
            self.source.index("private func finishScanCompletion()")
        ]
        self.assertNotIn("reloadDashboard(after: .scan)", profile_completion)

    def test_quit_waits_for_helpers_and_cancels_safely_after_a_deadline(self):
        self.assertIn("private let terminationDeferralSeconds = 30.0", self.source)
        self.assertIn("NSWindowDelegate,", self.source)
        self.assertIn("window.delegate = self", self.source)
        self.assertIn("func applicationShouldTerminate(", self.source)
        self.assertIn("return .terminateLater", self.source)
        self.assertIn("DispatchQueue.main.asyncAfter(", self.source)
        self.assertIn("NSApp.reply(toApplicationShouldTerminate: true)", self.source)
        self.assertIn("NSApp.reply(toApplicationShouldTerminate: false)", self.source)
        self.assertIn("func windowShouldClose(_ sender: NSWindow) -> Bool", self.source)
        self.assertIn("NSApp.terminate(sender)", self.source)
        self.assertIn("Quit was canceled to avoid interrupting", self.source)
        self.assertNotIn("process.terminate()", self.source)

        finish_start = self.source.index("private func finishCommand(")
        finish_end = self.source.index("private func finishBridgeCommand(", finish_start)
        finish = self.source[finish_start:finish_end]
        self.assertLess(
            finish.index("runningCommand = nil"),
            finish.index("completeDeferredTerminationIfNeeded()"),
        )

    def test_application_icon_uses_vector_radar_clock_geometry(self):
        icon_start = self.source.index("private enum RadarIcon")
        icon_end = self.source.index("private final class AppDelegate", icon_start)
        icon = self.source[icon_start:icon_end]
        self.assertIn("private static func radarSweep", icon)
        self.assertIn("ring.windingRule = .evenOdd", icon)
        self.assertIn("let signalDots:", icon)
        self.assertIn("let handGradient", icon)
        self.assertIn("NSBezierPath", icon)
        self.assertNotIn("asymmetricTile", icon)
        self.assertNotIn("NSImage(contentsOf", icon)

    @unittest.skipUnless(platform.system() == "Darwin", "native app compiles only on macOS")
    def test_native_source_compiles_and_renders_declared_icon_contract(self):
        xcrun = shutil.which("xcrun")
        self.assertIsNotNone(xcrun)
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            executable = root / "opportunity-radar"
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
                    str(executable),
                ],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)

            icon = root / "icon.png"
            rendered = subprocess.run(
                [str(executable), "--render-icon", str(icon), "64"],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertEqual(rendered.returncode, 0, rendered.stderr)
            payload = icon.read_bytes()
            self.assertEqual(payload[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(struct.unpack(">II", payload[16:24]), (64, 64))


if __name__ == "__main__":
    unittest.main()
