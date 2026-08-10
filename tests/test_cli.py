import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from monitor import cli
from monitor.database import Database
from monitor.models import Opportunity


class CliTests(unittest.TestCase):
    def test_workflow_parser_rejects_untrusted_identifiers(self):
        parser = cli.build_parser()
        for value in ("../escape", "A" * 24, "a" * 23, "a" * 25, "g" * 24):
            with (
                self.subTest(value=value),
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit),
            ):
                parser.parse_args(["status", value, "apply", "--quiet"])

    def test_status_and_bookmark_commands_update_private_database(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            database_path = root / "data" / "opportunities.sqlite3"
            database = Database(database_path)
            database.initialize()
            database.sync_source(
                {
                    "id": "test",
                    "name": "Test",
                    "kind": "greenhouse",
                    "url": "https://example.com/jobs",
                }
            )
            database.upsert_opportunity(
                Opportunity("test", "one", "Role", "Example", "https://example.com/one")
            )
            opportunity_id = database.connection.execute(
                "SELECT id FROM opportunities"
            ).fetchone()["id"]
            database.close()

            with (
                patch("monitor.cli.project_path", side_effect=lambda *parts: root.joinpath(*parts)),
                patch("monitor.cli.render_dashboard"),
                patch("monitor.cli.load_profile", return_value={}),
            ):
                self.assertEqual(cli.command_status(opportunity_id, "apply", quiet=True), 0)
                self.assertEqual(cli.command_bookmark(opportunity_id, "true", quiet=True), 0)

            database = Database(database_path)
            database.initialize()
            row = database.connection.execute(
                "SELECT status, bookmarked, status_updated_at FROM opportunities WHERE id=?",
                (opportunity_id,),
            ).fetchone()
            database.close()
            self.assertEqual(row["status"], "apply")
            self.assertEqual(row["bookmarked"], 1)
            self.assertIsNotNone(row["status_updated_at"])


if __name__ == "__main__":
    unittest.main()
