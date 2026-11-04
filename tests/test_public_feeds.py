import contextlib
import io
import json
import unittest
from unittest.mock import patch

from monitor.cli import main
from monitor.fetchers import fetch_source
from monitor.source_registry import build_custom_source


def source(kind="public_page", **fields):
    return {
        "id": "test_feed",
        "name": "Test publisher",
        "kind": kind,
        "url": "https://example.org/opportunities/",
        **fields,
    }


class PublicFeedTests(unittest.TestCase):
    def test_source_test_finds_actual_event_record_instead_of_returning_empty_success(
        self,
    ):
        configured = build_custom_source(
            "Test publisher",
            "https://example.org/opportunities/",
            opportunity_type="hackathon",
        )
        page = b"""
        <script type="application/ld+json">
        {"@type":"Event","name":"Ocean Data Hackathon","url":"/events/ocean-data",
         "startDate":"2027-01-12","endDate":"2027-01-14","organizer":{"name":"Harbor Lab"},
         "eventAttendanceMode":"https://schema.org/OnlineEventAttendanceMode",
         "description":"Build with public ocean data. Apply by January 5, 2027."}
        </script>
        """
        output = io.StringIO()
        with (
            patch("monitor.cli.load_sources", return_value=[configured]),
            patch("monitor.fetchers._request", return_value=page) as request,
            contextlib.redirect_stdout(output),
        ):
            self.assertEqual(main(["sources", "test", configured["id"]]), 0)
            request.assert_called_once_with(configured["url"])
        self.assertEqual(json.loads(output.getvalue())["items"], 1)

        with patch("monitor.fetchers._request", return_value=page):
            result = fetch_source(configured)
        self.assertFalse(result.complete_snapshot)
        event = result.opportunities[0]
        self.assertEqual(event.opportunity_type, "hackathon")
        self.assertEqual(event.organization, "Harbor Lab")
        self.assertEqual(event.location, "Remote")
        self.assertEqual(event.deadline_at, "2027-01-05")
        self.assertEqual(event.metadata["listing"]["starts_at"], "2027-01-12")
        self.assertEqual(event.metadata["listing"]["ends_at"], "2027-01-14")
        self.assertEqual(event.metadata["collection"]["evidence"], "structured_listing")

    def test_schema_graph_retains_distinct_job_and_education_facts(self):
        data = {
            "@graph": [
                {
                    "@id": "#university",
                    "@type": "Organization",
                    "name": "Example University",
                },
                {
                    "@type": "JobPosting",
                    "title": "Records Coordinator",
                    "url": "/jobs/42",
                    "hiringOrganization": {"@id": "#university"},
                    "datePosted": "2026-11-03",
                    "validThrough": "2026-12-01",
                    "employmentType": ["FULL_TIME"],
                    "jobLocationType": "TELECOMMUTE",
                    "applicantLocationRequirements": {"name": "Canada"},
                },
                {
                    "@type": "EducationalOccupationalProgram",
                    "name": "Welding Certificate",
                    "url": "/programs/welding",
                    "applicationDeadline": "2026-12-10",
                    "educationalCredentialAwarded": "Certificate",
                    "programPrerequisites": "High school diploma",
                },
            ]
        }
        body = (
            '<script type="application/ld+json">' + json.dumps(data) + "</script>"
        ).encode()
        with patch("monitor.fetchers._request", return_value=body):
            result = fetch_source(source("json_ld"))
        job, program = result.opportunities
        self.assertEqual(job.organization, "Example University")
        self.assertEqual(job.location, "Remote · Canada")
        self.assertEqual(job.posted_at, "2026-11-03")
        self.assertEqual(job.deadline_at, "2026-12-01")
        self.assertEqual(program.opportunity_type, "training")
        self.assertEqual(program.deadline_at, "2026-12-10")
        self.assertEqual(program.eligibility, "High school diploma")
        self.assertEqual(program.metadata["listing"]["credential"], "Certificate")

    def test_microdata_uses_visible_heading_and_separates_event_dates(self):
        page = b"""
        <a href="https://hack.example.org/" itemscope itemtype="https://schema.org/Event">
          <meta itemprop="startDate" content="2027-01-12T09:00:00Z"/>
          <meta itemprop="endDate" content="2027-01-14T10:00:00Z"/>
          <meta itemprop="isAccessibleForFree" content="false"/>
          <div itemprop="location" itemscope itemtype="https://schema.org/Place">
            <div itemprop="address" itemscope itemtype="https://schema.org/PostalAddress">
              <meta itemprop="addressLocality" content="New York"/>
              <meta itemprop="addressCountry" content="US"/>
            </div>
          </div>
          <h4>Ocean <strong>Data</strong> Hackathon</h4>
        </a>
        """
        with patch("monitor.fetchers._request", return_value=page):
            result = fetch_source(source("json_ld"))
        self.assertEqual(len(result.opportunities), 1)
        event = result.opportunities[0]
        self.assertEqual(event.title, "Ocean Data Hackathon")
        self.assertEqual(event.location, "New York, US")
        self.assertIsNone(event.deadline_at)
        self.assertEqual(
            event.metadata["listing"]["ends_at"], "2027-01-14T10:00:00+00:00"
        )
        self.assertIs(event.metadata["listing"]["is_free"], False)

    def test_directory_fallback_reuses_payload_and_does_not_publish_navigation(self):
        page = b"""
        <a href="/opportunities/">All opportunities</a>
        <a href="/opportunities/1">Urban Planning Fellowship</a>
        <a href="https://unrelated.example.org/opportunities/1">External</a>
        <a href="https://127.0.0.1/opportunities/1">Private</a>
        """
        configured = build_custom_source("Test publisher", source()["url"])
        with patch("monitor.fetchers._request", return_value=page) as request:
            result = fetch_source(configured)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(
            [item.title for item in result.opportunities], ["Urban Planning Fellowship"]
        )
        self.assertFalse(result.complete_snapshot)
        self.assertEqual(
            result.opportunities[0].metadata["collection"]["context"], "directory"
        )

    def test_structured_only_source_reports_parser_miss_as_error(self):
        with patch(
            "monitor.fetchers._request",
            return_value=b"<h1>Catalog temporarily unavailable</h1>",
        ):
            with self.assertRaisesRegex(
                ValueError, "recognizable opportunity listings"
            ):
                fetch_source(source("json_ld"))

    def test_atom_with_relative_url_and_no_invented_publication_date(self):
        page = b"""
        <feed xmlns="http://www.w3.org/2005/Atom" xml:base="/careers/">
          <entry xml:base="2027/">
            <title>Community Arts Fellowship</title>
            <id>urn:example:arts</id>
            <link rel="self" href="feed.xml"/>
            <link href="arts"/>
            <summary>Apply by January 2, 2027.</summary>
            <updated>2026-11-04T09:00:00Z</updated>
          </entry>
        </feed>
        """
        with patch("monitor.fetchers._request", return_value=page):
            result = fetch_source(source())
        self.assertFalse(result.complete_snapshot)
        item = result.opportunities[0]
        self.assertEqual(item.url, "https://example.org/careers/2027/arts")
        self.assertEqual(item.metadata["feed_entry_id"], "urn:example:arts")
        self.assertIsNone(item.posted_at)
        self.assertEqual(item.deadline_at, "2027-01-02")
        self.assertEqual(item.metadata["dates"]["source_updated"]["kind"], "updated")

    def test_rss_blocks_document_types_before_xml_entity_processing(self):
        xml = b'<!DOCTYPE rss [<!ENTITY data "not processed">]><rss><channel>&data;</channel></rss>'
        with patch("monitor.fetchers._request", return_value=xml):
            with self.assertRaisesRegex(ValueError, "must not declare"):
                fetch_source(source("syndication"))

    def test_rolling_multi_employer_feed_keeps_publisher_and_organization_separate(
        self,
    ):
        response = {
            "data": [
                {
                    "company_name": "Quince",
                    "title": "Staff Data Analyst, Marketing",
                    "description": "&lt;p&gt;Work across retail and data teams.&lt;/p&gt;",
                    "url": "https://www.arbeitnow.com/jobs/companies/quince/123",
                    "remote": True,
                    "location": "Berlin",
                    "created_at": 1793740800,
                }
            ]
        }
        configured = source(
            "arbeitnow", url="https://www.arbeitnow.com/api/job-board-api"
        )
        with patch(
            "monitor.fetchers._request", return_value=json.dumps(response).encode()
        ) as request:
            result = fetch_source(configured)
        request.assert_called_once_with(configured["url"])
        self.assertFalse(result.complete_snapshot)
        item = result.opportunities[0]
        self.assertEqual(item.organization, "Quince")
        self.assertEqual(item.location, "Remote · Berlin")
        self.assertEqual(
            item.metadata["collection"]["publisher_url"], "https://www.arbeitnow.com/"
        )

    def test_grant_open_date_and_deadline_are_distinct_and_tokens_do_not_change_hash(
        self,
    ):
        first = {
            "errorcode": 0,
            "token": "ephemeral",
            "data": {
                "hitCount": 1,
                "oppHits": [
                    {
                        "id": "352845",
                        "title": "Cooperative Agriculture",
                        "agency": "Fish and Wildlife Service",
                        "openDate": "03/06/2024",
                        "closeDate": "03/07/2029",
                        "oppStatus": "posted",
                    }
                ],
            },
        }
        second = {**first, "token": "rotated"}
        with patch(
            "monitor.fetchers._request",
            side_effect=[json.dumps(item).encode() for item in (first, second)],
        ) as request:
            configured = source(
                "grants_gov", url="https://www.grants.gov/search-grants"
            )
            result = fetch_source(configured)
            repeated = fetch_source(configured)
        self.assertTrue(result.complete_snapshot)
        self.assertEqual(result.content_hash, repeated.content_hash)
        self.assertEqual(request.call_args.kwargs["json_body"]["eligibilities"], "21")
        item = result.opportunities[0]
        self.assertEqual(item.opportunity_type, "grant")
        self.assertIsNone(item.posted_at)
        self.assertEqual(item.deadline_at, "2029-03-07")
        self.assertEqual(item.metadata["listing"]["applications_open_at"], "2024-03-06")


if __name__ == "__main__":
    unittest.main()
