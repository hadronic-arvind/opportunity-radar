import contextlib
import csv
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from monitor import cli, config
from monitor import profile as profile_service
from monitor.database import Database
from monitor.models import FetchResult, Opportunity


class CliTests(unittest.TestCase):
    def test_help_uses_public_product_identity_without_changing_commands(self):
        parser = cli.build_parser()
        help_text = parser.format_help()
        self.assertEqual(parser.prog, "opportunity-radar")
        self.assertIn("Opportunity Radar scans and tracks opportunities", help_text)
        commands = (
            "init",
            "profile",
            "scan",
            "dashboard",
            "sources",
            "opportunities",
            "status",
            "bookmark",
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertIn(command, help_text)
        self.assertNotIn("opportunity-monitor", help_text)

    def test_workflow_parser_rejects_untrusted_identifiers(self):
        parser = cli.build_parser()
        for value in ("../escape", "A" * 24, "a" * 23, "a" * 25, "g" * 24):
            with (
                self.subTest(value=value),
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit),
            ):
                parser.parse_args(["status", value, "apply", "--quiet"])
            with (
                self.subTest(opportunity_value=value),
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit),
            ):
                parser.parse_args(["opportunities", "show", value])

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

    def test_dashboard_and_workflow_commands_refuse_an_active_lifecycle(self):
        with tempfile.TemporaryDirectory() as tempdir:
            lifecycle = Path(tempdir) / ".OpportunityRadar.lifecycle-lock"
            lifecycle.mkdir(mode=0o700)
            with (
                patch.object(
                    profile_service, "_lifecycle_lock_path", return_value=lifecycle
                ),
                patch("monitor.pipeline.sys.platform", "darwin"),
            ):
                with self.assertRaisesRegex(RuntimeError, "already running"):
                    cli.command_dashboard(quiet=True)
                with self.assertRaisesRegex(RuntimeError, "already running"):
                    cli.command_status("a" * 24, "apply", quiet=True)
                errors = io.StringIO()
                with contextlib.redirect_stderr(errors):
                    self.assertEqual(
                        cli.main(["dashboard", "--quiet"]),
                        2,
                    )
                self.assertIn("already running", errors.getvalue())

    def test_dashboard_and_workflow_recheck_lifecycle_after_scan_lock(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            commands = (
                lambda: cli.command_dashboard(quiet=True),
                lambda: cli.command_status("a" * 24, "apply", quiet=True),
            )
            for command in commands:
                with (
                    self.subTest(command=command),
                    patch(
                        "monitor.cli.ensure_profile_lifecycle_idle",
                        side_effect=[None, RuntimeError("install started")],
                    ) as lifecycle_check,
                    patch(
                        "monitor.cli.project_path",
                        side_effect=lambda *parts: root.joinpath(*parts),
                    ),
                ):
                    with self.assertRaisesRegex(RuntimeError, "install started"):
                        command()
                    self.assertEqual(lifecycle_check.call_count, 2)

    def test_source_management_cli_parses_add_and_dispatches_mutations(self):
        parser = cli.build_parser()
        parsed = parser.parse_args(
            [
                "sources",
                "add",
                "--name",
                "Example Research",
                "--url",
                "https://jobs.ashbyhq.com/example",
                "--kind",
                "auto",
                "--packs",
                "research,fellowships",
                "--cadence-hours",
                "8",
                "--skip-test",
                "--dry-run",
            ]
        )
        self.assertEqual(parsed.source_command, "add")
        self.assertEqual(parsed.name, "Example Research")
        self.assertEqual(parsed.kind, "auto")
        self.assertEqual(parsed.cadence_hours, 8)
        self.assertTrue(parsed.skip_test)
        self.assertTrue(parsed.dry_run)

        source = {
            "id": "example_research",
            "name": "Example Research",
            "kind": "ashby",
            "url": "https://jobs.ashbyhq.com/example",
            "board": "example",
        }
        output = io.StringIO()
        with (
            patch("monitor.cli.build_custom_source", return_value=source) as build,
            patch(
                "monitor.cli.add_source",
                return_value={
                    "action": "added",
                    "source": source,
                    "saved": False,
                    "status": "valid",
                },
            ) as add,
            patch("monitor.cli.fetch_source") as fetch,
            contextlib.redirect_stdout(output),
        ):
            self.assertEqual(
                cli.main(
                    [
                        "sources",
                        "add",
                        "--name",
                        "Example Research",
                        "--url",
                        "https://jobs.ashbyhq.com/example",
                        "--packs",
                        "research,fellowships",
                        "--cadence-hours",
                        "8",
                        "--skip-test",
                        "--dry-run",
                    ]
                ),
                0,
            )
        build.assert_called_once_with(
            name="Example Research",
            url="https://jobs.ashbyhq.com/example",
            source_id="",
            kind="auto",
            adapter="",
            packs=["research", "fellowships"],
            cadence_hours=8,
        )
        add.assert_called_once_with(source, dry_run=True)
        fetch.assert_not_called()
        self.assertFalse(json.loads(output.getvalue())["saved"])

        mutation_cases = (
            ("enable", "set_source_enabled", True),
            ("disable", "set_source_enabled", False),
            ("remove", "remove_source", None),
        )
        for action, target, enabled in mutation_cases:
            result = {"action": action, "source_id": "example", "saved": False}
            with (
                self.subTest(action=action),
                patch("monitor.cli.{}".format(target), return_value=result) as mutation,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(
                    cli.main(["sources", action, "example", "--dry-run"]),
                    0,
                )
            if enabled is None:
                mutation.assert_called_once_with("example", dry_run=True)
            else:
                mutation.assert_called_once_with("example", enabled, dry_run=True)

    def test_source_add_does_not_save_after_a_failed_live_test(self):
        source = {
            "id": "example",
            "name": "Example",
            "kind": "ashby",
            "url": "https://jobs.ashbyhq.com/example",
            "board": "example",
        }
        errors = io.StringIO()
        with (
            patch("monitor.cli.build_custom_source", return_value=source),
            patch(
                "monitor.cli.fetch_source",
                return_value=FetchResult([], "hash", status="error", message="bad feed"),
            ),
            patch("monitor.cli.add_source") as add,
            contextlib.redirect_stderr(errors),
        ):
            self.assertEqual(
                cli.main(
                    [
                        "sources",
                        "add",
                        "--name",
                        "Example",
                        "--url",
                        "https://jobs.ashbyhq.com/example",
                    ]
                ),
                2,
            )
        add.assert_not_called()
        self.assertIn("Source test returned status error", errors.getvalue())

    def test_source_list_and_show_support_machine_readable_output(self):
        sources = [
            {
                "id": "enabled",
                "name": "Enabled source",
                "kind": "ashby",
                "enabled": True,
            },
            {
                "id": "disabled",
                "name": "Disabled source",
                "kind": "greenhouse",
                "enabled": False,
            },
        ]
        output = io.StringIO()
        with (
            patch("monitor.cli.load_sources", return_value=sources) as load,
            contextlib.redirect_stdout(output),
        ):
            self.assertEqual(
                cli.main(["sources", "list", "--all", "--json"]),
                0,
            )
        load.assert_called_once_with(include_disabled=True)
        self.assertEqual(json.loads(output.getvalue()), sources)

        summary = {**sources[1], "custom": False, "board": "disabled"}
        output = io.StringIO()
        with (
            patch("monitor.cli.source_summary", return_value=summary) as show,
            contextlib.redirect_stdout(output),
        ):
            self.assertEqual(cli.main(["sources", "show", "disabled"]), 0)
        show.assert_called_once_with("disabled")
        self.assertEqual(json.loads(output.getvalue()), summary)

    def test_opportunities_cli_supports_json_csv_search_show_and_filters(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            database_path = root / "data" / "opportunities.sqlite3"
            database = Database(database_path)
            database.initialize()
            for source in (
                {
                    "id": "alpha",
                    "name": "Alpha Source",
                    "kind": "ashby",
                    "url": "https://jobs.ashbyhq.com/alpha",
                    "enabled": True,
                },
                {
                    "id": "beta",
                    "name": "Beta Source",
                    "kind": "greenhouse",
                    "url": "https://boards.greenhouse.io/beta",
                    "enabled": False,
                },
            ):
                database.sync_source(source)
            opportunities = (
                Opportunity(
                    "alpha",
                    "percent",
                    "100% Research Intern",
                    "Alpha Labs",
                    "https://example.org/percent",
                    location="Remote",
                    description="Research systems and scientific computing",
                    opportunity_type="internship",
                    posted_at="2026-08-01T00:00:00+00:00",
                    deadline_at="2026-09-15",
                    metadata={"team": "Research"},
                    score=88,
                    tier="priority",
                    reasons=["Research match"],
                    warnings=["Competitive"],
                ),
                Opportunity(
                    "alpha",
                    "designer",
                    "Product Designer",
                    "Alpha Labs",
                    "https://example.org/designer",
                    location="New York",
                    opportunity_type="job",
                    score=55,
                    tier="watch",
                ),
                Opportunity(
                    "beta",
                    "fellow",
                    "Public Interest Fellow",
                    "Beta Foundation",
                    "https://example.org/fellow",
                    opportunity_type="fellowship",
                    score=95,
                    tier="priority",
                ),
            )
            for opportunity in opportunities:
                database.upsert_opportunity(opportunity)
            identifiers = {
                row["external_id"]: row["id"]
                for row in database.connection.execute(
                    "SELECT id, external_id FROM opportunities"
                ).fetchall()
            }
            with database.transaction() as connection:
                connection.execute(
                    "UPDATE opportunities SET status='applied', active=0 "
                    "WHERE external_id='designer'"
                )
                connection.execute(
                    "UPDATE sources SET last_status='ok' WHERE id='alpha'"
                )
            database.close()

            project_path = lambda *parts: root.joinpath(*parts)
            output = io.StringIO()
            with (
                patch("monitor.cli.project_path", side_effect=project_path),
                contextlib.redirect_stdout(output),
            ):
                self.assertEqual(
                    cli.main(
                        [
                            "opportunities",
                            "list",
                            "--status",
                            "new",
                            "--type",
                            "internship",
                            "--source",
                            "alpha",
                            "--min-score",
                            "80",
                            "--limit",
                            "10",
                            "--json",
                        ]
                    ),
                    0,
                )
            rows = json.loads(output.getvalue())
            self.assertEqual([row["id"] for row in rows], [identifiers["percent"]])
            self.assertEqual(set(rows[0]), set(cli.OPPORTUNITY_OUTPUT_FIELDS))
            self.assertEqual(rows[0]["source_name"], "Alpha Source")

            output = io.StringIO()
            with (
                patch("monitor.cli.project_path", side_effect=project_path),
                contextlib.redirect_stdout(output),
            ):
                self.assertEqual(
                    cli.main(["opportunities", "search", "100%", "--json"]),
                    0,
                )
            self.assertEqual(
                [row["id"] for row in json.loads(output.getvalue())],
                [identifiers["percent"]],
            )

            output = io.StringIO()
            with (
                patch("monitor.cli.project_path", side_effect=project_path),
                contextlib.redirect_stdout(output),
            ):
                self.assertEqual(
                    cli.main(
                        [
                            "opportunities",
                            "list",
                            "--include-inactive",
                            "--limit",
                            "3",
                            "--csv",
                        ]
                    ),
                    0,
                )
            reader = csv.DictReader(io.StringIO(output.getvalue()))
            csv_rows = list(reader)
            self.assertEqual(tuple(reader.fieldnames or ()), cli.OPPORTUNITY_OUTPUT_FIELDS)
            self.assertEqual(
                [row["id"] for row in csv_rows],
                [identifiers["fellow"], identifiers["percent"], identifiers["designer"]],
            )

            output = io.StringIO()
            with (
                patch("monitor.cli.project_path", side_effect=project_path),
                contextlib.redirect_stdout(output),
            ):
                self.assertEqual(
                    cli.main(
                        [
                            "opportunities",
                            "show",
                            identifiers["percent"],
                            "--json",
                        ]
                    ),
                    0,
                )
            shown = json.loads(output.getvalue())
            self.assertEqual(shown["reasons"], ["Research match"])
            self.assertEqual(shown["warnings"], ["Competitive"])
            self.assertEqual(shown["metadata"]["team"], "Research")
            self.assertEqual(shown["source_name"], "Alpha Source")
            self.assertEqual(shown["source_status"], "ok")
            self.assertNotIn("reasons_json", shown)

            output = io.StringIO()
            with (
                patch("monitor.cli.project_path", side_effect=project_path),
                contextlib.redirect_stdout(output),
            ):
                self.assertEqual(
                    cli.main(["opportunities", "show", identifiers["percent"]]),
                    0,
                )
            self.assertIn("100% Research Intern at Alpha Labs", output.getvalue())
            self.assertIn("Research systems and scientific computing", output.getvalue())

    def test_opportunity_query_bounds_are_enforced_with_or_without_a_database(self):
        for database_exists in (False, True):
            with self.subTest(database_exists=database_exists), tempfile.TemporaryDirectory() as tempdir:
                root = Path(tempdir)
                if database_exists:
                    database = Database(root / "data" / "opportunities.sqlite3")
                    database.initialize()
                    database.close()
                for arguments, message in (
                    (["--limit", "0"], "--limit must be from 1 to 500"),
                    (["--limit", "501"], "--limit must be from 1 to 500"),
                    (["--min-score", "-1"], "--min-score must be from 0 to 100"),
                    (["--min-score", "101"], "--min-score must be from 0 to 100"),
                    (["--query", "x" * 241], "Search query must be at most 240 characters"),
                ):
                    errors = io.StringIO()
                    with (
                        self.subTest(arguments=arguments),
                        patch(
                            "monitor.cli.project_path",
                            side_effect=lambda *parts: root.joinpath(*parts),
                        ),
                        contextlib.redirect_stderr(errors),
                        contextlib.redirect_stdout(io.StringIO()),
                    ):
                        self.assertEqual(
                            cli.main(["opportunities", "list"] + arguments),
                            2,
                        )
                    self.assertIn(message, errors.getvalue())

    def test_profile_apply_stdin_uses_exact_editor_contract_and_private_files(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "config").mkdir()
            (root / "config" / "profile.json").write_text(
                json.dumps(
                    {
                        "priority_organizations": [],
                        "matching": {
                            "engine": "structured_v2",
                            "base_score": 20,
                            "priority_organization_bonus": 5,
                            "minimum_display_score": 40,
                            "anchor_min_strength": 0.5,
                            "target_type_bonus": 10,
                            "target_timeframe_bonus": 8,
                            "score_ceilings": {"description_exclusion": 45},
                            "tier_thresholds": {
                                "priority": 80,
                                "strong": 65,
                                "watch": 40,
                            },
                            "rules": [],
                        },
                        "documents": {"default": "General", "routes": []},
                    }
                ),
                encoding="utf-8",
            )
            (root / "config" / "sources.json").write_text(
                json.dumps(
                    {
                        "packs": [{"id": "technical", "default": True}],
                        "sources": [],
                    }
                ),
                encoding="utf-8",
            )
            lifecycle = root / "Application Support" / ".OpportunityRadar.lifecycle-lock"
            with (
                patch.object(config, "PROJECT_ROOT", root),
                patch.object(profile_service, "_lifecycle_lock_path", return_value=lifecycle),
                patch.dict(os.environ, {}, clear=True),
            ):
                payload = profile_service.profile_editor_payload()
                self.assertEqual(
                    set(payload),
                    {
                        "version",
                        "expected_revision",
                        "timeframes",
                        "selected_packs",
                        "candidate",
                        "targets",
                        "priority_organizations",
                        "matching",
                        "documents",
                    },
                )
                payload["timeframes"] = ["Summer 2028"]
                payload["targets"]["cycles"] = [
                    {"label": "Summer 2028", "season": "summer", "year": 2028}
                ]
                payload["candidate"]["max_required_experience_years"] = 3
                payload["matching"]["rules"] = [
                    {
                        "id": "research",
                        "label": "Research roles",
                        "dimension": "interest",
                        "anchor": True,
                        "hard_gate": False,
                        "weight": 12,
                        "fields": ["title", "category"],
                        "terms": ["research"],
                    }
                ]
                with patch.object(sys, "stdin", io.StringIO(json.dumps(payload))):
                    self.assertEqual(
                        cli.main(["profile", "apply", "--stdin", "--quiet"]),
                        0,
                    )

            saved = json.loads(
                (root / "config" / "profile.local.json").read_text(encoding="utf-8")
            )
            rule = saved["matching"]["rules"][0]
            self.assertEqual(saved["timeframes"], ["Summer 2028"])
            self.assertEqual(saved["candidate"]["max_required_experience_years"], 3)
            self.assertEqual(saved["matching"]["engine"], "structured_v2")
            self.assertEqual(saved["matching"]["anchor_min_strength"], 0.5)
            self.assertEqual(saved["matching"]["target_type_bonus"], 10)
            self.assertEqual(saved["matching"]["target_timeframe_bonus"], 8)
            self.assertEqual(
                saved["matching"]["score_ceilings"]["description_exclusion"],
                45,
            )
            self.assertEqual(rule["dimension"], "interest")
            self.assertTrue(rule["anchor"])
            self.assertFalse(rule["hard_gate"])
            self.assertEqual((root / "config" / "profile.local.json").stat().st_mode & 0o777, 0o600)

    def test_profile_editor_rejects_an_unknown_matching_engine(self):
        with patch(
            "monitor.profile.config.load_source_packs",
            return_value=[{"id": "technical"}],
        ):
            payload = {
                "version": 1,
                "expected_revision": "",
                "timeframes": [],
                "selected_packs": ["technical"],
                "candidate": {"completed_degrees": [], "skills": []},
                "targets": {
                    "opportunity_types": [],
                    "role_families": [],
                    "domains": [],
                    "supporting_skills": [],
                    "locations": [],
                    "exclusions": [],
                    "work_arrangements": [],
                    "cycles": [],
                },
                "priority_organizations": [],
                "matching": {
                    "engine": "mystery",
                    "base_score": 25,
                    "priority_organization_bonus": 10,
                    "tier_thresholds": {"priority": 80, "strong": 65, "watch": 40},
                    "rules": [],
                },
                "documents": {"default": "General", "routes": []},
            }
            with self.assertRaisesRegex(ValueError, "engine"):
                profile_service.validate_editor_payload(payload)

    def test_profile_set_updates_common_preferences_and_rejects_unknown_packs(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "config").mkdir()
            (root / "config" / "profile.json").write_text(
                json.dumps(
                    {
                        "priority_organizations": [],
                        "matching": {
                            "base_score": 25,
                            "priority_organization_bonus": 10,
                            "tier_thresholds": {
                                "priority": 80,
                                "strong": 65,
                                "watch": 40,
                            },
                            "rules": [],
                        },
                        "documents": {"default": "General", "routes": []},
                    }
                ),
                encoding="utf-8",
            )
            (root / "config" / "sources.json").write_text(
                json.dumps(
                    {
                        "packs": [
                            {"id": "technical", "default": True},
                            {"id": "research"},
                        ],
                        "sources": [],
                    }
                ),
                encoding="utf-8",
            )
            lifecycle = root / "Application Support" / ".OpportunityRadar.lifecycle-lock"
            with (
                patch.object(config, "PROJECT_ROOT", root),
                patch.object(profile_service, "_lifecycle_lock_path", return_value=lifecycle),
                patch.dict(os.environ, {}, clear=True),
            ):
                self.assertEqual(
                    cli.main(
                        [
                            "profile",
                            "set",
                            "--include",
                            "scientific computing,detector physics",
                            "--exclude",
                            "sales internship",
                            "--locations",
                            "remote,Baltimore",
                            "--remote-preference",
                            "remote_preferred",
                            "--organizations",
                            "Example Lab",
                            "--timeframe",
                            "Summer 2028",
                            "--timeframe",
                            "Fall 2028",
                            "--packs",
                            "technical,research",
                            "--quiet",
                        ]
                    ),
                    0,
                )
                errors = io.StringIO()
                with contextlib.redirect_stderr(errors):
                    self.assertEqual(
                        cli.main(
                            [
                                "profile",
                                "set",
                                "--packs",
                                "not-a-pack",
                                "--quiet",
                            ]
                        ),
                        2,
                    )
                self.assertIn("Unknown source pack", errors.getvalue())

            saved = json.loads(
                (root / "config" / "profile.local.json").read_text(encoding="utf-8")
            )
            self.assertEqual(saved["timeframes"], ["Summer 2028", "Fall 2028"])
            self.assertEqual(saved["targets"]["locations"], ["remote", "Baltimore"])
            self.assertEqual(saved["targets"]["remote_preference"], "remote_preferred")
            self.assertEqual(
                saved["targets"]["role_families"],
                ["scientific computing", "detector physics"],
            )
            self.assertEqual(saved["targets"]["exclusions"], ["sales internship"])
            self.assertEqual(saved["priority_organizations"], ["Example Lab"])
            self.assertEqual(saved["matching"]["engine"], "structured_v2")
            self.assertEqual(saved["matching"]["rules"], [])

    def test_doctor_rejects_malformed_matching_and_source_values(self):
        profile = {
            "matching": {
                "base_score": True,
                "tier_thresholds": {"priority": 20, "strong": 80, "watch": 10},
                "rules": [
                    {
                        "terms": ["python"],
                        "fields": ["not_a_field"],
                        "weight": 1000,
                        "match": "sometimes",
                        "per_term": "yes",
                    }
                ],
            },
            "documents": {
                "default": "",
                "routes": [{"label": "", "terms": [], "fields": ["unknown"]}],
            },
            "priority_organizations": [""],
        }
        sources = [
            {
                "id": "example",
                "name": "Example",
                "kind": "greenhouse",
                "url": "https://example.com/jobs",
                "enabled": 1,
                "support_level": "unknown",
                "packs": ["starter"],
                "expected_http_statuses": [99],
            }
        ]
        output = io.StringIO()
        with (
            patch("monitor.cli.load_profile", return_value=profile),
            patch("monitor.cli.load_sources", return_value=sources),
            patch("monitor.cli.load_source_packs", return_value=[{"id": "starter"}]),
            patch("monitor.cli.profile_files", return_value=[]),
            patch("monitor.cli.project_path", return_value=Path(__file__)),
            contextlib.redirect_stdout(output),
        ):
            self.assertEqual(cli.command_doctor(), 1)
        failures = json.loads(output.getvalue())["failures"]
        joined = "\n".join(failures)
        for expected in (
            "requires a non-empty board",
            "non-boolean enabled",
            "invalid support_level",
            "invalid expected_http_statuses",
            "base_score",
            "tier thresholds must be ordered",
            "invalid fields",
            "invalid weight",
            "invalid match mode",
            "non-boolean per_term",
            "documents.default",
            "document route 1",
            "priority_organizations",
        ):
            self.assertIn(expected, joined)

    def test_doctor_rejects_structured_sources_missing_adapter_keys(self):
        profile = {
            "matching": {"rules": []},
            "documents": {"default": "General", "routes": []},
            "priority_organizations": [],
        }
        sources = [
            {
                "id": "missing_board",
                "name": "Missing board",
                "kind": "greenhouse",
                "url": "https://example.org/jobs",
                "packs": ["starter"],
            },
            {
                "id": "missing_site",
                "name": "Missing site",
                "kind": "lever",
                "url": "https://example.org/jobs",
                "packs": ["starter"],
            },
            {
                "id": "missing_api",
                "name": "Missing API",
                "kind": "jibe",
                "url": "https://example.org/jobs",
                "packs": ["starter"],
            },
        ]
        output = io.StringIO()
        with (
            patch("monitor.cli.load_profile", return_value=profile),
            patch("monitor.cli.load_sources", return_value=sources),
            patch("monitor.cli.load_source_packs", return_value=[{"id": "starter"}]),
            patch("monitor.cli.profile_files", return_value=[]),
            patch("monitor.cli.project_path", return_value=Path(__file__)),
            contextlib.redirect_stdout(output),
        ):
            self.assertEqual(cli.command_doctor(), 1)

        failures = json.loads(output.getvalue())["failures"]
        self.assertIn("missing_board requires a non-empty board", failures)
        self.assertIn("missing_site requires a non-empty site", failures)
        self.assertIn("missing_api requires a non-empty api_url", failures)
        self.assertIn(
                "missing_api does not have a valid public HTTPS api_url",
                failures,
            )

    def test_doctor_rejects_runtime_invalid_cadence_and_url_syntax(self):
        profile = {
            "matching": {"rules": []},
            "documents": {"default": "General", "routes": []},
            "priority_organizations": [],
        }
        sources = [
            {
                "id": "unsafe_source",
                "name": "Unsafe source",
                "kind": "watch_page",
                "url": "https://user:secret@example.org:8443/jobs",
                "cadence_hours": True,
                "packs": ["starter"],
            }
        ]
        output = io.StringIO()
        with (
            patch("monitor.cli.load_profile", return_value=profile),
            patch("monitor.cli.load_sources", return_value=sources),
            patch("monitor.cli.load_source_packs", return_value=[{"id": "starter"}]),
            patch("monitor.cli.profile_files", return_value=[]),
            patch("monitor.cli.project_path", return_value=Path(__file__)),
            contextlib.redirect_stdout(output),
        ):
            self.assertEqual(cli.command_doctor(), 1)

        failures = json.loads(output.getvalue())["failures"]
        self.assertIn(
            "unsafe_source does not have a valid public HTTPS url",
            failures,
        )
        self.assertIn("unsafe_source has an invalid cadence_hours", failures)

    def test_doctor_accepts_documented_experimental_adapter(self):
        profile = {
            "matching": {"rules": []},
            "documents": {"default": "General", "routes": []},
            "priority_organizations": [],
        }
        source = {
            "id": "community_roles",
            "name": "Community roles",
            "kind": "html_links",
            "url": "https://example.org/jobs",
            "enabled": False,
            "support_level": "experimental",
            "packs": ["community"],
        }
        output = io.StringIO()
        with (
            patch("monitor.cli.load_profile", return_value=profile),
            patch("monitor.cli.load_sources", return_value=[source]),
            patch("monitor.cli.load_source_packs", return_value=[{"id": "community"}]),
            patch("monitor.cli.profile_files", return_value=[]),
            patch("monitor.cli.project_path", return_value=Path(__file__)),
            contextlib.redirect_stdout(output),
        ):
            self.assertEqual(cli.command_doctor(), 0)
        self.assertTrue(json.loads(output.getvalue())["ok"])

    def test_doctor_rejects_unbounded_html_link_configuration(self):
        profile = {
            "matching": {"rules": []},
            "documents": {"default": "General", "routes": []},
            "priority_organizations": [],
        }
        source = {
            "id": "bad_links",
            "name": "Bad links",
            "kind": "html_links",
            "url": "https://example.org/jobs",
            "link_base_url": "https://other.example/jobs/",
            "pages": 21,
            "include": [""],
            "same_domain": "yes",
            "packs": ["community"],
        }
        output = io.StringIO()
        with (
            patch("monitor.cli.load_profile", return_value=profile),
            patch("monitor.cli.load_sources", return_value=[source]),
            patch("monitor.cli.load_source_packs", return_value=[{"id": "community", "default": True}]),
            patch("monitor.cli.profile_files", return_value=[]),
            patch("monitor.cli.project_path", return_value=Path(__file__)),
            patch("monitor.cli.profile_editor_payload"),
            contextlib.redirect_stdout(output),
        ):
            self.assertEqual(cli.command_doctor(), 1)
        failures = json.loads(output.getvalue())["failures"]
        self.assertIn("bad_links has an invalid pages value", failures)
        self.assertIn("bad_links has an invalid include list", failures)
        self.assertIn("bad_links has an invalid same_domain value", failures)
        self.assertIn("bad_links has an invalid link_base_url", failures)

    def test_doctor_rejects_string_enabled_override_with_selected_packs(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "config").mkdir()
            (root / "dashboard").mkdir()
            (root / "config" / "profile.json").write_text(
                json.dumps(
                    {
                        "matching": {"rules": []},
                        "documents": {"default": "General", "routes": []},
                        "priority_organizations": [],
                    }
                ),
                encoding="utf-8",
            )
            (root / "config" / "sources.json").write_text(
                json.dumps(
                    {
                        "packs": [{"id": "starter"}],
                        "sources": [
                            {
                                "id": "example",
                                "name": "Example",
                                "kind": "greenhouse",
                                "url": "https://example.com/jobs",
                                "enabled": False,
                                "support_level": "supported",
                                "packs": ["starter"],
                                "cadence_hours": 12,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (root / "config" / "sources.local.json").write_text(
                json.dumps(
                    {
                        "selected_packs": ["starter"],
                        "sources": [{"id": "example", "enabled": "false"}],
                    }
                ),
                encoding="utf-8",
            )
            for name in ("template.html", "styles.css", "app.js"):
                (root / "dashboard" / name).write_text("asset", encoding="utf-8")

            output = io.StringIO()
            with (
                patch.object(config, "PROJECT_ROOT", root),
                patch.dict(os.environ, {}, clear=True),
                contextlib.redirect_stdout(output),
            ):
                self.assertEqual(
                    config.load_sources(include_disabled=True)[0]["enabled"],
                    "false",
                )
                self.assertEqual(cli.command_doctor(), 1)

            failures = json.loads(output.getvalue())["failures"]
            self.assertIn("example has a non-boolean enabled value", failures)


if __name__ == "__main__":
    unittest.main()
