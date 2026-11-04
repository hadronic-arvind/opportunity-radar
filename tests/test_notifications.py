import unittest
from unittest.mock import patch

from monitor.notifications import (
    MAX_NOTIFICATION_BODY_CHARS,
    MAX_NOTIFICATION_TITLE_CHARS,
    NOTIFICATION_SCRIPT,
    notify_macos,
)


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


if __name__ == "__main__":
    unittest.main()
