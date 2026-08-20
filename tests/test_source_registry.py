import contextlib
import json
import os
import sqlite3
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from monitor import config
from monitor.database import Database
from monitor.models import Opportunity
from monitor.profile import ProfileValidationError
from monitor.source_registry import (
    CUSTOM_PACK_ID,
    add_source,
    build_custom_source,
    detect_source,
    remove_source,
    set_source_enabled,
    source_summary,
    validate_source,
)


class SourceRegistryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "config").mkdir()
        (self.root / "data").mkdir()
        self.public_source = {
            "id": "built_in",
            "name": "Built-in Board",
            "kind": "greenhouse",
            "url": "https://boards.greenhouse.io/builtin",
            "board": "builtin",
            "packs": ["starter"],
            "cadence_hours": 12,
            "enabled": True,
        }
        (self.root / "config" / "sources.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "packs": [
                        {
                            "id": "starter",
                            "name": "Starter",
                            "description": "Starter sources",
                            "default": True,
                        }
                    ],
                    "sources": [self.public_source],
                }
            ),
            encoding="utf-8",
        )
        self.local_path = self.root / "config" / "sources.local.json"
        self.local_path.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "selected_packs": ["starter"],
                    "packs": [],
                    "sources": [],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (self.root / "config" / "profile.json").write_text(
            "{}\n",
            encoding="utf-8",
        )
        database = Database(self.root / "data" / "opportunities.sqlite3")
        database.initialize()
        database.sync_source(self.public_source)
        database.close()

        project_root = patch.object(config, "PROJECT_ROOT", self.root)
        project_root.start()
        self.addCleanup(project_root.stop)
        lifecycle = patch(
            "monitor.source_registry.profile_lifecycle_lock",
            side_effect=contextlib.nullcontext,
        )
        lifecycle.start()
        self.addCleanup(lifecycle.stop)
        dashboard = patch("monitor.dashboard.render_dashboard")
        dashboard.start()
        self.addCleanup(dashboard.stop)
        environment = patch.dict(os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def _local_payload(self):
        return json.loads(self.local_path.read_text(encoding="utf-8"))

    def _database_snapshot(self):
        connection = sqlite3.connect(str(self.root / "data" / "opportunities.sqlite3"))
        connection.row_factory = sqlite3.Row
        try:
            return {
                table: [dict(row) for row in connection.execute(
                    "SELECT * FROM {} ORDER BY {}".format(
                        table,
                        "id" if table != "runtime_state" else "key",
                    )
                ).fetchall()]
                for table in ("sources", "opportunities", "runtime_state", "source_events")
            }
        finally:
            connection.close()

    def test_detects_supported_boards_and_falls_back_to_bounded_html_links(self):
        cases = (
            (
                "https://boards.greenhouse.io/acme/jobs/42",
                ("greenhouse", {"board": "acme"}),
            ),
            (
                "https://job-boards.greenhouse.io/acme?gh_src=test",
                ("greenhouse", {"board": "acme"}),
            ),
            ("https://jobs.lever.co/acme/role", ("lever", {"site": "acme"})),
            ("https://jobs.ashbyhq.com/acme/role", ("ashby", {"board": "acme"})),
            ("https://careers.example.org/openings", ("html_links", {})),
        )
        for url, expected in cases:
            with self.subTest(url=url):
                self.assertEqual(detect_source(url), expected)

        for url in (
            "http://jobs.example.org",
            "https://user:secret@jobs.example.org",
            "https://localhost/jobs",
            "https://127.0.0.1/jobs",
            "https://jobs.example.org:8443/jobs",
        ):
            with self.subTest(url=url), self.assertRaisesRegex(
                ProfileValidationError, "public HTTPS URL"
            ):
                detect_source(url)

    def test_build_custom_source_normalizes_each_supported_user_facing_kind(self):
        ashby = build_custom_source(
            "  Acme Research  ",
            "https://jobs.ashbyhq.com/acme-research/role",
            cadence_hours=6,
        )
        self.assertEqual(
            {
                key: ashby[key]
                for key in (
                    "id",
                    "name",
                    "kind",
                    "board",
                    "packs",
                    "cadence_hours",
                    "enabled",
                    "support_level",
                    "source_type",
                )
            },
            {
                "id": "acme_research",
                "name": "Acme Research",
                "kind": "ashby",
                "board": "acme-research",
                "packs": [CUSTOM_PACK_ID],
                "cadence_hours": 6,
                "enabled": True,
                "support_level": "supported",
                "source_type": "listing_feed",
            },
        )

        explicit = build_custom_source(
            "Example Company",
            "https://careers.example.org",
            source_id="Example Company Roles",
            kind="lever",
            adapter="example-company",
            packs=["starter", "custom"],
        )
        self.assertEqual(explicit["id"], "example_company_roles")
        self.assertEqual(explicit["site"], "example-company")
        self.assertEqual(explicit["packs"], ["starter", "custom"])

        links = build_custom_source(
            "Community Fellowships",
            "https://example.org/fellowships",
        )
        self.assertEqual(links["kind"], "html_links")
        self.assertEqual(links["pages"], 1)
        self.assertTrue(links["same_domain"])
        self.assertIn("/fellow", links["include"])
        self.assertEqual(links["support_level"], "experimental")

        watch = build_custom_source(
            "Program Calendar",
            "https://example.org/programs",
            kind="watch_page",
        )
        self.assertFalse(watch["publish_as_opportunity"])
        self.assertEqual(watch["source_type"], "change_monitor")
        self.assertEqual(watch["support_level"], "manual")

    def test_build_and_validation_reject_malformed_or_unbounded_sources(self):
        invalid_builds = (
            (
                lambda: build_custom_source("", "https://example.org/jobs"),
                "Source id",
            ),
            (
                lambda: build_custom_source(
                    "Example", "https://example.org/jobs", kind="ashby"
                ),
                "provide --adapter",
            ),
            (
                lambda: build_custom_source(
                    "Example",
                    "https://example.org/jobs",
                    kind="jibe",
                    adapter="example",
                ),
                "Unsupported source kind for add",
            ),
            (
                lambda: build_custom_source(
                    "Example", "https://example.org/jobs", cadence_hours=0
                ),
                "Source cadence must be from",
            ),
            (
                lambda: build_custom_source(
                    "Example", "https://example.org/jobs", cadence_hours=True
                ),
                "Source cadence must be an integer",
            ),
        )
        for operation, message in invalid_builds:
            with self.subTest(message=message), self.assertRaisesRegex(
                ProfileValidationError, message
            ):
                operation()

        valid = build_custom_source("Example", "https://example.org/jobs")
        invalid_values = (
            ({**valid, "id": "Not-Lowercase"}, "Source id"),
            ({**valid, "enabled": 1}, "enabled value"),
            ({**valid, "cadence_hours": 745}, "Source cadence must be from"),
            (
                {**valid, "packs": ["pack-{}".format(index) for index in range(65)]},
                "too many values",
            ),
            ({**valid, "item_filter_scope": "metadata"}, "title or full"),
            ({**valid, "expected_http_statuses": [99]}, "HTTP status integers"),
            (
                {**valid, "pages": 21},
                "HTML page count must be from",
            ),
            ({**valid, "same_domain": "yes"}, "same_domain must be true or false"),
            (
                {**valid, "link_base_url": "https://127.0.0.1/jobs"},
                "public HTTPS URL",
            ),
            (
                {**valid, "default_opportunity_type": "not-a-real-type"},
                "default_opportunity_type is invalid",
            ),
        )
        for source, message in invalid_values:
            with self.subTest(message=message), self.assertRaisesRegex(
                ProfileValidationError, message
            ):
                validate_source(source)

        deduplicated = validate_source(
            {
                **valid,
                "packs": ["Research", "research", "Engineering"],
                "expected_http_statuses": [200, 200, 403],
            }
        )
        self.assertEqual(deduplicated["packs"], ["Research", "Engineering"])
        self.assertEqual(deduplicated["expected_http_statuses"], [200, 403])
        self.assertEqual(
            validate_source({**valid, "default_opportunity_type": "Research Program"})[
                "default_opportunity_type"
            ],
            "research_program",
        )

    def test_add_enable_disable_and_remove_refresh_config_and_database_together(self):
        invalid_pack = build_custom_source(
            "Invalid Pack Board",
            "https://jobs.lever.co/invalid-pack-board",
            packs=["missing-pack"],
        )
        before_invalid = self.local_path.read_bytes()
        with self.assertRaisesRegex(ProfileValidationError, "[Ss]ource pack"):
            add_source(invalid_pack)
        self.assertEqual(self.local_path.read_bytes(), before_invalid)

        custom = build_custom_source(
            "Acme Research", "https://jobs.ashbyhq.com/acme-research"
        )
        added = add_source(custom)
        self.assertTrue(added["saved"])
        self.assertEqual(added["action"], "added")
        self.assertTrue(added["dashboard_rebuilt"])
        payload = self._local_payload()
        self.assertIn(CUSTOM_PACK_ID, {pack["id"] for pack in payload["packs"]})
        self.assertEqual(payload["sources"][0]["id"], "acme_research")
        self.assertEqual(self.local_path.stat().st_mode & 0o777, 0o600)
        before_duplicate = self.local_path.read_bytes()
        with self.assertRaisesRegex(ProfileValidationError, "Source already exists"):
            add_source(custom)
        self.assertEqual(self.local_path.read_bytes(), before_duplicate)

        database = Database(self.root / "data" / "opportunities.sqlite3")
        database.initialize()
        row = database.connection.execute(
            "SELECT name, kind, enabled FROM sources WHERE id='acme_research'"
        ).fetchone()
        database.close()
        self.assertEqual((row["name"], row["kind"], row["enabled"]), ("Acme Research", "ashby", 1))
        self.assertTrue(source_summary("acme_research")["custom"])
        self.assertFalse(source_summary("built_in")["custom"])

        disabled = set_source_enabled("acme_research", False)
        self.assertEqual(disabled["action"], "disabled")
        self.assertFalse(
            next(
                source
                for source in self._local_payload()["sources"]
                if source["id"] == "acme_research"
            )["enabled"]
        )
        database = Database(self.root / "data" / "opportunities.sqlite3")
        database.initialize()
        self.assertEqual(
            database.connection.execute(
                "SELECT enabled FROM sources WHERE id='acme_research'"
            ).fetchone()["enabled"],
            0,
        )
        database.close()

        set_source_enabled("acme_research", True)
        set_source_enabled("built_in", False)
        built_in_override = next(
            source
            for source in self._local_payload()["sources"]
            if source["id"] == "built_in"
        )
        self.assertEqual(built_in_override, {"id": "built_in", "enabled": False})

        removed = remove_source("acme_research")
        self.assertEqual(removed["action"], "removed")
        self.assertNotIn(
            "acme_research",
            {source["id"] for source in self._local_payload()["sources"]},
        )
        database = Database(self.root / "data" / "opportunities.sqlite3")
        database.initialize()
        self.assertEqual(
            database.connection.execute(
                "SELECT enabled FROM sources WHERE id='acme_research'"
            ).fetchone()["enabled"],
            0,
        )
        database.close()

        with self.assertRaisesRegex(ProfileValidationError, "not removed"):
            remove_source("built_in")
        with self.assertRaisesRegex(ProfileValidationError, "Unknown custom source"):
            remove_source("acme_research")
        with self.assertRaisesRegex(ProfileValidationError, "Unknown source"):
            set_source_enabled("missing_source", True)

    def test_dry_run_validates_every_mutation_without_writing_or_refreshing(self):
        custom = build_custom_source(
            "Acme Research", "https://jobs.ashbyhq.com/acme-research"
        )
        add_source(custom)
        before_file = self.local_path.read_bytes()
        before_database = self._database_snapshot()

        with patch("monitor.source_registry.refresh_profile_state") as refresh:
            results = (
                add_source(
                    build_custom_source(
                        "Another Board", "https://jobs.lever.co/another-board"
                    ),
                    dry_run=True,
                ),
                set_source_enabled("built_in", False, dry_run=True),
                set_source_enabled("acme_research", False, dry_run=True),
                remove_source("acme_research", dry_run=True),
            )

        self.assertEqual(
            [result["action"] for result in results],
            ["added", "disabled", "disabled", "removed"],
        )
        self.assertTrue(all(result["status"] == "valid" for result in results))
        self.assertTrue(all(result["saved"] is False for result in results))
        self.assertEqual(self.local_path.read_bytes(), before_file)
        self.assertEqual(self._database_snapshot(), before_database)
        refresh.assert_not_called()

    def test_every_mutation_restores_exact_config_and_database_on_refresh_failure(self):
        enabled_source = build_custom_source(
            "Enabled Board", "https://jobs.ashbyhq.com/enabled-board"
        )
        disabled_source = build_custom_source(
            "Disabled Board", "https://jobs.lever.co/disabled-board"
        )
        add_source(enabled_source)
        add_source(disabled_source)
        set_source_enabled("disabled_board", False)

        database = Database(self.root / "data" / "opportunities.sqlite3")
        database.initialize()
        item = Opportunity(
            "built_in",
            "existing",
            "Existing Role",
            "Built-in Board",
            "https://example.org/existing",
            description="Existing description",
            score=91,
            tier="priority",
            reasons=["Existing reason"],
        )
        database.upsert_opportunity(item)
        database.close()
        before_file = self.local_path.read_bytes()
        before_database = deepcopy(self._database_snapshot())

        added_source = build_custom_source(
            "Rollback Board", "https://jobs.ashbyhq.com/rollback-board"
        )
        operations = (
            ("add", lambda: add_source(added_source)),
            ("disable", lambda: set_source_enabled("enabled_board", False)),
            ("enable", lambda: set_source_enabled("disabled_board", True)),
            ("remove", lambda: remove_source("enabled_board")),
        )
        for action, operation in operations:
            with (
                self.subTest(action=action),
                patch(
                    "monitor.dashboard.render_dashboard",
                    side_effect=RuntimeError("dashboard failed"),
                ),
                self.assertRaisesRegex(RuntimeError, "dashboard failed"),
            ):
                operation()

            self.assertEqual(self.local_path.read_bytes(), before_file)
            self.assertEqual(self._database_snapshot(), before_database)


if __name__ == "__main__":
    unittest.main()
