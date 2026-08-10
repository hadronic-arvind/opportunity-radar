import unittest
from pathlib import Path
from unittest.mock import patch

from monitor.notifications import (
    MAX_NOTIFICATION_BODY_CHARS,
    MAX_NOTIFICATION_TITLE_CHARS,
    NOTIFICATION_SCRIPT,
    notify_macos,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class NotificationTests(unittest.TestCase):
    @patch("monitor.notifications.subprocess.run")
    @patch("monitor.notifications.os.path.isfile", return_value=True)
    @patch("monitor.notifications.platform.system", return_value="Darwin")
    def test_notification_content_is_passed_as_arguments(self, _system, _isfile, run):
        title = 'Title " \\ newline\nUnicode Ω'
        body = 'do shell script "unsafe"\nSecond line'
        self.assertTrue(notify_macos(title, body))
        command = run.call_args.args[0]
        self.assertEqual(command[:3], ["/usr/bin/osascript", "-e", NOTIFICATION_SCRIPT])
        self.assertEqual(command[3:], [title, body])
        self.assertNotIn(title, NOTIFICATION_SCRIPT)
        self.assertNotIn(body, NOTIFICATION_SCRIPT)
        self.assertEqual(run.call_args.kwargs, {"check": False, "timeout": 10})

    @patch("monitor.notifications.platform.system", return_value="Linux")
    def test_notification_is_noop_off_macos(self, _system):
        self.assertFalse(notify_macos("Title", "Body"))

    @patch("monitor.notifications.subprocess.run")
    @patch("monitor.notifications.os.path.isfile", return_value=True)
    @patch("monitor.notifications.platform.system", return_value="Darwin")
    def test_notification_arguments_are_bounded(self, _system, _isfile, run):
        notify_macos("T" * 1000, "B" * 10000)
        command = run.call_args.args[0]
        self.assertEqual(len(command[3]), MAX_NOTIFICATION_TITLE_CHARS)
        self.assertEqual(len(command[4]), MAX_NOTIFICATION_BODY_CHARS)


class WebhookConfigurationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (PROJECT_ROOT / "scripts" / "configure_webhook.sh").read_text(
            encoding="utf-8"
        )

    def test_keychain_prompts_without_putting_secret_in_process_arguments(self):
        command = next(
            line.strip()
            for line in self.source.splitlines()
            if "security add-generic-password" in line
        )
        self.assertTrue(command.endswith("-U -w"), command)
        self.assertNotIn("WEBHOOK_URL", self.source)
        self.assertNotIn("read -r -s", self.source)
        self.assertIn('CURRENT_USER="$(/usr/bin/id -un)"', self.source)
        self.assertNotRegex(command, r"-(?:w|p|X)\s+[^-\s]")

    def test_keychain_value_is_validated_over_stdin_and_removed_when_invalid(self):
        self.assertIn("security find-generic-password", self.source)
        self.assertIn("-w |", self.source)
        self.assertIn("/usr/bin/grep -Eq '^https://", self.source)
        self.assertIn("security delete-generic-password", self.source)


if __name__ == "__main__":
    unittest.main()
