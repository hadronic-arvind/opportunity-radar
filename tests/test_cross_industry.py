"""Exercise cross-industry collection through anonymous public CLI flows."""

import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from monitor import cli, config
from monitor.database import Database
from monitor.fetchers import _source_metadata
from monitor.models import FetchResult, Opportunity
from monitor.scoring import score_opportunity

ROOT = Path(__file__).resolve().parents[1]


class CrossIndustryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for folder, names in (
            ("config", ("profile.json", "sources.json")),
            ("dashboard", ("template.html", "styles.css", "app.js")),
        ):
            (self.root / folder).mkdir()
            for name in names:
                shutil.copyfile(ROOT / folder / name, self.root / folder / name)
        for guard in (
            patch.object(config, "PROJECT_ROOT", self.root),
            patch.dict(os.environ, {}, clear=True),
            patch(
                "monitor.profile._lifecycle_lock_path",
                return_value=self.root / "lifecycle",
            ),
            patch("monitor.pipeline.polite_pause"),
        ):
            guard.start()
            self.addCleanup(guard.stop)

    def command(self, *arguments):
        output, error = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            status = cli.main(list(arguments))
        self.assertEqual(status, 0, error.getvalue())
        return json.loads(output.getvalue()) if output.getvalue().strip() else None

    def select(self, mode="automatic", roles=None, packs=None, overrides=None):
        (self.root / "config/profile.local.json").write_text(
            json.dumps(
                {
                    "targets": {
                        "role_families": roles or ["accountant"],
                        "domains": ["accounting"],
                    }
                }
            )
        )
        (self.root / "config/sources.local.json").write_text(
            json.dumps(
                {
                    "coverage_preset": mode,
                    "selected_packs": packs
                    if packs is not None
                    else ["starter-diverse"],
                    "sources": overrides or [],
                }
            )
        )
        return {source["id"] for source in self.command("sources", "list", "--json")}

    def test_automatic_and_named_coverage_cross_employer_industries(self):
        expected = {"spacex_greenhouse", "natera_greenhouse", "discord_greenhouse"}
        for mode, roles in (
            ("automatic", ["accountant"]),
            ("automatic", ["nurse"]),
            ("automatic", ["software engineer"]),
            ("medicine-health", ["research assistant"]),
        ):
            with self.subTest(mode=mode, roles=roles):
                self.assertTrue(expected <= self.select(mode, roles))

    def test_manual_starter_and_explicit_disabling_remain_authoritative(self):
        starter = {source["id"] for source in self.command("sources", "list", "--json")}
        self.assertEqual(len(starter), 5)
        self.assertEqual(self.select("manual"), starter)
        broad = self.select("manual", packs=["cross-industry"])
        self.assertIn("spacex_greenhouse", broad)
        disabled = self.select(
            overrides=[{"id": "spacex_greenhouse", "enabled": False}]
        )
        self.assertNotIn("spacex_greenhouse", disabled)
        self.assertIn("natera_greenhouse", disabled)

    def test_catalog_cross_industry_pack_contains_only_supported_structured_feeds(self):
        catalog = json.loads((self.root / "config/sources.json").read_text())
        members = [
            source
            for source in catalog["sources"]
            if "cross-industry" in source["packs"]
        ]
        eligible = [
            source
            for source in catalog["sources"]
            if source["kind"] in {"greenhouse", "lever", "ashby", "jibe"}
            and source["support_level"] == "supported"
            and source["source_type"] == "listing_feed"
            and source.get("auto_enable", True)
        ]
        self.assertGreaterEqual(len(members), 120)
        self.assertEqual(
            {source["id"] for source in members}, {source["id"] for source in eligible}
        )
        for source in members:
            self.assertFalse(source.get("item_include"), source["id"])
            self.assertFalse(source.get("item_exclude"), source["id"])
            self.assertFalse(source.get("category"), source["id"])

    def test_scan_matches_jobs_and_preserves_cadence_and_saved_status(self):
        self.command(
            "profile",
            "import",
            "--file",
            str(ROOT / "examples/profiles/accounting-graduate.json"),
            "--quiet",
        )

        def fetch(source):
            items = []
            if source["id"] in {
                "spacex_greenhouse",
                "natera_greenhouse",
                "discord_greenhouse",
            }:
                for identifier, title, description in (
                    (
                        "accountant",
                        "Staff Accountant",
                        "Accounting and financial reporting. Excel. Bachelor's degree in accounting. No experience required.",
                    ),
                    (
                        "other",
                        "Senior Software Engineer",
                        "Build distributed systems. Requires 8 years of software development experience.",
                    ),
                ):
                    items.append(
                        Opportunity(
                            source["id"],
                            identifier,
                            title,
                            source["name"],
                            "https://example.org/{}/{}".format(
                                source["id"], identifier
                            ),
                            description=description,
                            opportunity_type="job",
                            category="Corporate Finance"
                            if identifier == "accountant"
                            else "Engineering",
                            metadata=_source_metadata(source),
                        )
                    )
            return FetchResult(items, "fixture-hash")

        with patch("monitor.pipeline.fetch_source", side_effect=fetch) as collector:
            result = self.command("scan", "--force")
            self.assertEqual(result["status"], "ok")
            fetched = [call.args[0]["id"] for call in collector.call_args_list]
            self.assertEqual(len(fetched), len(set(fetched)))
        matches = self.command("opportunities", "search", "Staff Accountant", "--json")
        self.assertEqual(
            {row["organization"] for row in matches}, {"SpaceX", "Natera", "Discord"}
        )
        database = Database(self.root / "data/opportunities.sqlite3")
        self.addCleanup(database.close)
        self.assertEqual(
            database.connection.execute(
                "SELECT COUNT(*) FROM opportunities WHERE external_id='other' AND tier='skip'"
            ).fetchone()[0],
            3,
        )
        saved = matches[0]["id"]
        self.command("status", saved, "apply", "--quiet")
        with patch(
            "monitor.pipeline.fetch_source",
            side_effect=AssertionError("Cadence should avoid refetch"),
        ):
            self.assertEqual(self.command("scan")["checked_sources"], 0)
        self.assertEqual(
            database.connection.execute(
                "SELECT status FROM opportunities WHERE id=?", (saved,)
            ).fetchone()[0],
            "apply",
        )

    def test_employer_sector_metadata_does_not_change_job_fit(self):
        self.command(
            "profile",
            "import",
            "--file",
            str(ROOT / "examples/profiles/accounting-graduate.json"),
            "--quiet",
        )
        results = []
        for domains in (["aerospace"], ["accounting", "finance"], ["healthcare"]):
            item = Opportunity(
                "fixture",
                "1",
                "Staff Accountant",
                "Example Employer",
                "https://example.org/job",
                description="Accounting and financial reporting. Excel. No experience required.",
                opportunity_type="job",
                metadata={"domains": domains, "packs": domains},
            )
            score_opportunity(item, config.load_profile())
            results.append((item.score, item.tier, item.reasons))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[1], results[2])

    def test_fellowship_labels_do_not_make_unrelated_work_a_technical_match(self):
        profile = {
            "targets": {
                "role_families": ["machine learning", "Fellow", "Fellowship"],
                "domains": ["physics", "Research", "Graduate Fellowships"],
                "supporting_skills": ["Python", "data analysis"],
                "opportunity_types": ["fellowship"],
                "locations": ["United States"],
            },
            "matching": {
                "engine": "structured_v2",
                "base_score": 25,
                "minimum_display_score": 40,
                "tier_thresholds": {"priority": 75, "strong": 55, "watch": 25},
                "rules": [],
            },
        }
        (self.root / "config/profile.local.json").write_text(json.dumps(profile))
        source = {
            "id": "fixture",
            "name": "Anonymous programs",
            "kind": "greenhouse",
            "url": "https://example.org/jobs",
            "cadence_hours": 24,
        }
        titles = [
            "Human Rights Fellowship",
            "School Leadership Fellowship",
            "Machine Learning Research Fellowship",
        ]
        items = [
            Opportunity(
                "fixture",
                str(index),
                title,
                "Example",
                "https://example.org/" + str(index),
                location="United States",
                opportunity_type="fellowship",
                description="Research, Python and data analysis support the program.",
            )
            for index, title in enumerate(titles)
        ]
        with (
            patch("monitor.pipeline.load_sources", return_value=[source]),
            patch(
                "monitor.pipeline.fetch_source",
                return_value=FetchResult(items, "fixture"),
            ),
        ):
            self.command("scan", "--force")
        rows = self.command("opportunities", "search", "Fellowship", "--json")
        visible = [row["title"] for row in rows if row["tier"] != "skip"]
        self.assertEqual(visible, [titles[2]])
        database = Database(self.root / "data/opportunities.sqlite3")
        self.addCleanup(database.close)
        payload = database.dashboard_payload()
        self.assertEqual(
            [row["title"] for row in payload["opportunities"]], [titles[2]]
        )

    def test_medical_student_does_not_match_required_physician_residency(self):
        self.command(
            "profile",
            "import",
            "--file",
            str(ROOT / "examples/profiles/medical-student.json"),
            "--quiet",
        )
        source = {
            "id": "fixture",
            "name": "Clinical careers",
            "kind": "greenhouse",
            "url": "https://example.org/jobs",
            "cadence_hours": 24,
        }
        items = [
            Opportunity(
                "fixture",
                "research",
                "Clinical Research Assistant",
                "Example",
                "https://example.org/research",
                opportunity_type="job",
                description="Clinical research and data collection. No experience required.",
            ),
            Opportunity(
                "fixture",
                "physician",
                "Early Career - Family Medicine Physician",
                "Example",
                "https://example.org/physician",
                opportunity_type="job",
                description="Required for this role: Currently enrolled in, or completed within the last year, an accredited Family or Internal Medicine/Pediatrics residency program.",
            ),
            Opportunity(
                "fixture",
                "practitioner",
                "Family Medicine Nurse Practitioner or Physician Assistant",
                "Example",
                "https://example.org/practitioner",
                opportunity_type="job",
                description="Completed an accredited FNP or PA program with a national certification.",
            ),
        ]
        with (
            patch("monitor.pipeline.load_sources", return_value=[source]),
            patch(
                "monitor.pipeline.fetch_source",
                return_value=FetchResult(items, "clinical"),
            ),
        ):
            self.command("scan", "--force")
        rows = self.command("opportunities", "list", "--json")
        self.assertEqual(
            [row["title"] for row in rows if row["tier"] != "skip"], [items[0].title]
        )

    def test_positive_interest_gate_keeps_matching_listings_visible(self):
        profile = {
            "matching": {
                "engine": "structured_v2",
                "base_score": 50,
                "rules": [
                    {
                        "id": "required_subject",
                        "dimension": "interest",
                        "hard_gate": True,
                        "weight": 1,
                        "fields": ["title"],
                        "terms": ["accountant"],
                    }
                ],
            },
        }
        (self.root / "config/profile.local.json").write_text(json.dumps(profile))
        source = {
            "id": "fixture",
            "name": "Anonymous employer",
            "kind": "greenhouse",
            "url": "https://example.org/jobs",
            "cadence_hours": 24,
        }
        items = [
            Opportunity(
                "fixture",
                str(index),
                title,
                "Example",
                "https://example.org/" + str(index),
                opportunity_type="job",
            )
            for index, title in enumerate(["Staff Accountant", "Mechanical Engineer"])
        ]
        with (
            patch("monitor.pipeline.load_sources", return_value=[source]),
            patch(
                "monitor.pipeline.fetch_source",
                return_value=FetchResult(items, "fixture"),
            ),
        ):
            self.command("scan", "--force")
        database = Database(self.root / "data/opportunities.sqlite3")
        self.addCleanup(database.close)
        self.assertEqual(
            [row["title"] for row in database.dashboard_payload()["opportunities"]],
            ["Staff Accountant"],
        )


if __name__ == "__main__":
    unittest.main()
