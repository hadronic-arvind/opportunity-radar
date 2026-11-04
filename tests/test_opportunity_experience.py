"""Regression tests across the CLI, local catalog, and listing evidence."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from monitor import cli, fetchers, pipeline
from monitor.database import Database
from monitor.fetchers import _infer_opportunity_type
from monitor.models import FetchResult, Opportunity
from monitor.pipeline import exclusive_lock
from monitor.scoring import score_opportunity
from monitor.text import infer_opportunity_type


class OpportunityExperienceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "opportunities.sqlite3"
        self.database = Database(self.path)
        self.addCleanup(self.database.close)
        self.database.initialize()
        for source_id, kind in (("university", "json_ld"), ("board", "greenhouse")):
            self.database.sync_source(
                {
                    "id": source_id,
                    "name": source_id.title(),
                    "kind": kind,
                    "url": "https://example.org/" + source_id,
                }
            )

    def command(self, *arguments):
        output = io.StringIO()
        with (
            patch("monitor.cli._opportunity_database_path", return_value=self.path),
            contextlib.redirect_stdout(output),
        ):
            self.assertEqual(cli.main(list(arguments)), 0)
        return output.getvalue()

    def insert(self, source, identifier, title, **fields):
        url = fields.pop("url", "https://example.org/" + identifier)
        self.database.upsert_opportunity(
            Opportunity(source, identifier, title, "Example Institute", url, **fields)
        )
        return self.database.connection.execute(
            "SELECT id FROM opportunities WHERE source_id=? AND external_id=?",
            (source, identifier),
        ).fetchone()[0]

    def test_search_reads_committed_catalog_during_scan_and_reaches_hidden_listings(
        self,
    ):
        identifier = self.insert(
            "university",
            "funding",
            "Bourse de recherche",
            category="Field ecology",
            location="Montréal, Canada",
            opportunity_type="grant",
            tier="skip",
        )
        with exclusive_lock(self.path.with_name("scan.lock")):
            result = self.command(
                "opportunities",
                "search",
                "ecology montreal",
                "--type",
                "grant",
                "--json",
            )
            matched = self.command("opportunities", "list", "--matched", "--json")
        self.assertEqual([entry["id"] for entry in json.loads(result)], [identifier])
        self.assertEqual(json.loads(matched), [])
        payload = self.database.dashboard_payload()
        self.assertEqual(payload["opportunities"], [])
        self.assertEqual(payload["catalog"][0]["id"], identifier)
        self.assertEqual(payload["catalog"][0]["facts"]["kind"], "grant")

    def test_cli_only_scan_collects_and_searches_without_rendering(self):
        source = {
            "id": "board",
            "name": "Board",
            "kind": "greenhouse",
            "url": "https://example.org/board",
        }
        item = Opportunity(
            "board",
            "new",
            "Supply Chain Apprentice",
            "Example",
            "https://example.org/apprentice",
            opportunity_type="apprenticeship",
        )
        with (
            patch.dict(os.environ, {"OPPORTUNITY_RADAR_CLI_ONLY": "1"}),
            patch.object(pipeline, "project_path", return_value=self.path),
            patch.object(pipeline, "load_sources", return_value=[source]),
            patch.object(pipeline, "load_profile", return_value={}),
            patch.object(
                pipeline, "fetch_source", return_value=FetchResult([item], "new")
            ),
            patch.object(pipeline, "ensure_profile_lifecycle_idle"),
            patch.object(pipeline, "render_dashboard") as render,
        ):
            scan = json.loads(self.command("scan", "--force"))
        render.assert_not_called()
        self.assertIsNone(scan["dashboard"])
        result = json.loads(
            self.command("opportunities", "search", "supply chain", "--json")
        )
        self.assertEqual([entry["title"] for entry in result], [item.title])

    def test_facets_overlap_listing_kinds_and_require_source_evidence(self):
        funded = self.insert(
            "university",
            "fellow",
            "Open Field Fellowship",
            opportunity_type="fellowship",
            tier="skip",
            description=(
                "Fellows will conduct field research. "
                "A travel grant is available. No stipend is provided."
            ),
        )
        paid = self.insert(
            "board",
            "job",
            "Research Analyst",
            opportunity_type="job",
            description="You will perform ecological research.",
            metadata={
                "listing": {"base_salary": {"minimum": 40000, "currency": "USD"}}
            },
        )
        prize = self.insert(
            "university",
            "prize",
            "Field Research Challenge",
            opportunity_type="competition",
            description="Teams will conduct field research. The winning team receives a cash prize.",
        )
        result = self.command(
            "opportunities", "list", "--activity", "research", "--json"
        )
        self.assertEqual(
            {row["id"] for row in json.loads(result)}, {funded, paid, prize}
        )
        result = self.command(
            "opportunities",
            "list",
            "--activity",
            "research",
            "--benefit",
            "funding",
            "--json",
        )
        self.assertEqual([row["id"] for row in json.loads(result)], [funded])
        result = self.command(
            "opportunities", "list", "--benefit", "compensation", "--json"
        )
        self.assertEqual([row["id"] for row in json.loads(result)], [paid])
        result = self.command("opportunities", "list", "--benefit", "prize", "--json")
        self.assertEqual([row["id"] for row in json.loads(result)], [prize])
        catalog = self.database.dashboard_payload()["catalog"]
        self.assertEqual(
            catalog[0]["facts"]["facets"]["benefits"],
            [{"id": "funding", "label": "Funding"}],
        )
        show = json.loads(self.command("opportunities", "show", funded, "--json"))
        self.assertEqual(show["facts"]["facets"]["activities"][0]["id"], "research")
        self.assertIn(
            "travel grant", show["facts"]["facets"]["benefits"][0]["evidence"]
        )

    def test_facets_backfill_existing_rows_and_follow_source_updates(self):
        identifier = self.insert(
            "university",
            "program",
            "Open Field Fellowship",
            opportunity_type="fellowship",
            description="Fellows will conduct field research. Travel reimbursement is provided.",
        )
        self.database.set_bookmarked(identifier, True)
        with self.database.transaction() as connection:
            connection.execute("DROP TABLE opportunity_facets")
            connection.execute("PRAGMA user_version = 6")
        result = self.command(
            "opportunities", "list", "--benefit", "funding", "--saved", "--json"
        )
        self.assertEqual([row["id"] for row in json.loads(result)], [identifier])
        self.insert(
            "university",
            "program",
            "Open Field Fellowship",
            opportunity_type="fellowship",
            description="Fellows will conduct field research. Travel reimbursement is not provided.",
        )
        result = self.command(
            "opportunities", "list", "--benefit", "funding", "--saved", "--json"
        )
        self.assertEqual(json.loads(result), [])
        self.assertEqual(
            [
                row["id"]
                for row in json.loads(
                    self.command("opportunities", "list", "--saved", "--json")
                )
            ],
            [identifier],
        )

    def test_listing_review_exposes_evidence_dates_and_publisher(self):
        identifier = self.insert(
            "university",
            "hack",
            "Open Climate Hackathon",
            opportunity_type="hackathon",
            description=(
                "Participants will build tools for local climate resilience. "
                "A cash prize is available. Participants must apply as a team."
            ),
            eligibility="Applicants must be at least 18 years old or attend with a guardian.",
            metadata={
                "collection": {
                    "coverage": "partial",
                    "context": "listing",
                    "publisher_url": "https://example.org/publisher",
                },
                "listing": {"ends_at": "2001-01-01", "attendance": "online"},
            },
        )
        payload = json.loads(
            self.command("opportunities", "show", identifier, "--json")
        )
        facts = payload["facts"]
        self.assertEqual(facts["availability"], "closed")
        self.assertEqual(facts["schedule"]["attendance"], "online")
        self.assertEqual(facts["source"]["url"], "https://example.org/publisher")
        self.assertIn("cash prize", facts["benefits"][0]["evidence"])
        self.assertTrue(
            any("guardian" in entry["evidence"] for entry in facts["requirements"])
        )
        self.assertEqual(
            json.loads(self.command("opportunities", "list", "--available", "--json")),
            [],
        )
        self.assertIn("guardian", self.command("opportunities", "show", identifier))

    def test_unique_results_keep_distinct_application_ids_and_saved_history(self):
        link = "https://example.org/apply?position=30"
        first = self.insert("board", "30", "Legal Assistant", url=link, score=65)
        self.insert(
            "university",
            "duplicate",
            "Legal Assistant",
            url=link + "&utm_source=board",
        )
        other = self.insert(
            "university",
            "31",
            "Legal Assistant",
            url=link.replace("30", "31"),
        )
        result = self.command("opportunities", "search", "legal", "--unique", "--json")
        self.assertEqual([entry["id"] for entry in json.loads(result)], [first, other])
        self.database.set_bookmarked(first, True)
        self.database.mark_source_stale("board", [])
        saved = self.command("opportunities", "list", "--saved", "--json")
        self.assertEqual([entry["id"] for entry in json.loads(saved)], [first])
        self.assertFalse(json.loads(saved)[0]["active"])

    def test_searches_disabled_resources_by_subject_and_opportunity_type(self):
        source = {
            "id": "public_law",
            "name": "Public Service Directory",
            "kind": "watch_page",
            "domains": ["law"],
            "opportunity_types": ["fellowship"],
            "enabled": False,
            "url": "https://example.org/public-service",
        }
        with patch("monitor.cli.load_sources", return_value=[source]) as load:
            output = self.command("sources", "discover", "law fellowship", "--json")
        load.assert_called_once_with(include_disabled=True)
        self.assertEqual(json.loads(output), [source])

    def test_fit_distinguishes_role_equivalents_from_employer_industry(self):
        profile = {
            "targets": {"role_families": ["Technical writer"]},
            "matching": {"engine": "structured_v2"},
        }
        item = Opportunity(
            "board",
            "one",
            "Technical Documentation Writer",
            "Example Aerospace",
            "https://example.org/one",
            opportunity_type="job",
        )
        score_opportunity(item, profile)
        self.assertNotEqual(item.tier, "skip")
        self.assertIn("technical documentation writer", " ".join(item.reasons))
        unrelated = Opportunity(
            "board",
            "two",
            "Propulsion engineer",
            "Example Aerospace",
            "https://example.org/two",
            opportunity_type="job",
            description="You will collaborate with technical writers.",
        )
        score_opportunity(unrelated, profile)
        self.assertEqual(unrelated.tier, "skip")

    def test_curated_link_feed_preserves_distinct_query_based_application_urls(self):
        source = {
            "id": "board",
            "name": "Board",
            "kind": "html_links",
            "source_type": "listing_feed",
            "url": "https://example.org/careers",
            "include": ["gh_jid="],
        }
        html = (
            b'<a href="/apply?gh_jid=1">Laboratory Apprentice</a>'
            b'<a href="/apply?gh_jid=2">Legal Apprentice</a>'
        )
        with patch.object(fetchers, "_request", return_value=html):
            result = fetchers.fetch_source(source)
        self.assertEqual(len(result.opportunities), 2)
        for item in result.opportunities:
            self.assertIn("gh_jid=", item.url)
            self.assertEqual(item.metadata["collection"]["context"], "listing")

    def test_employer_role_is_not_mislabeled_as_the_program_it_supports(self):
        self.assertEqual(
            _infer_opportunity_type({"kind": "greenhouse"}, "Conference Coordinator"),
            "job",
        )
        self.assertEqual(infer_opportunity_type("Graduate Scholarship"), "scholarship")
        self.assertEqual(
            infer_opportunity_type("City Climate Hackathon", default="event"),
            "hackathon",
        )
        self.assertEqual(
            infer_opportunity_type("Ausbildung zum Industriemechaniker"),
            "apprenticeship",
        )

    def test_a_kind_only_profile_recommends_the_requested_opportunity(self):
        profile = {
            "targets": {"opportunity_types": ["hackathon"]},
            "matching": {"engine": "structured_v2", "minimum_display_score": 40},
        }
        event = Opportunity(
            "university",
            "hack",
            "City Climate Hackathon",
            "Example",
            "https://example.org/hack",
            opportunity_type="hackathon",
        )
        score_opportunity(event, profile)
        self.assertNotEqual(event.tier, "skip")

    def test_unresolved_alternative_is_not_a_hard_rejection(self):
        profile = {
            "candidate": {"current_stage": "phd_student"},
            "matching": {"engine": "structured_v2", "base_score": 85},
        }
        item = Opportunity(
            "university",
            "program",
            "Research Fellowship",
            "Example Institute",
            "https://example.org/program",
            description="Applicants must be enrolled in a master's program or have equivalent industry experience.",
            opportunity_type="fellowship",
        )
        score_opportunity(item, profile)
        self.assertEqual(item.metadata["match"]["eligibility"], "unknown")
        self.assertNotEqual(item.tier, "skip")


if __name__ == "__main__":
    unittest.main()
