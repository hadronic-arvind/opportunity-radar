import json
import signal
import time
import unittest
import urllib.request
from unittest.mock import Mock, patch

from monitor.dates import normalize_timestamp
from monitor.fetchers import (
    MAX_ASHBY_RESPONSE_BYTES,
    MAX_RESPONSE_BYTES,
    READ_CHUNK_BYTES,
    ResponseTooLargeError,
    _PublicHTTPSConnection,
    _SafeRedirectHandler,
    _infer_opportunity_type,
    _jibe_pages,
    _request,
    _validate_remote_url,
    fetch_ashby,
    fetch_greenhouse,
    fetch_html_links,
    fetch_jibe,
    fetch_lever,
    fetch_source,
    fetch_watch_page,
    filter_items,
)
from monitor.models import FetchResult, MAX_OPPORTUNITIES_PER_SOURCE, Opportunity
from monitor.onboarding import build_profile
from monitor.scoring import score_opportunity
from monitor.text import stable_hash


class FakeResponse:
    def __init__(self, payload, headers=None, url=None):
        self.payload = payload
        self.offset = 0
        self.headers = headers or {}
        self.read_sizes = []
        self.url = url
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        self.read_sizes.append(size)
        if size is None or size < 0:
            size = len(self.payload) - self.offset
        chunk = self.payload[self.offset:self.offset + size]
        self.offset += len(chunk)
        return chunk

    def geturl(self):
        return self.url

    def close(self):
        self.closed = True


class FetcherTests(unittest.TestCase):
    def test_timestamp_normalization_accepts_compact_utc_offsets_on_python_39(self):
        self.assertEqual(
            normalize_timestamp("2027-08-09T00:00:00+0000"),
            "2027-08-09T00:00:00+00:00",
        )
        self.assertEqual(
            normalize_timestamp("2027-08-09T12:34:56-0430"),
            "2027-08-09T17:04:56+00:00",
        )

    def setUp(self):
        resolver = patch(
            "monitor.fetchers.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 443))],
        )
        resolver.start()
        self.addCleanup(resolver.stop)

    @patch("monitor.fetchers._open_remote")
    def test_greenhouse_normalization(self, urlopen):
        payload = {
            "jobs": [
                {
                    "id": 42,
                    "title": "Data Engineering Intern",
                    "absolute_url": "https://example.com/jobs/42?gh_jid=42",
                    "location": {"name": "New York"},
                    "content": "<p>SQL and Python</p>",
                    "departments": [{"name": "Data"}],
                    "offices": [{"name": "New York Office"}],
                    "updated_at": "2026-08-09T00:00:00Z",
                }
            ]
        }
        urlopen.return_value = FakeResponse(json.dumps(payload).encode())
        result = fetch_greenhouse({
            "id": "firm",
            "name": "Firm",
            "board": "firm",
            "domains": ["data", "software"],
            "packs": ["data-software"],
            "official": True,
        })
        self.assertEqual(len(result.opportunities), 1)
        item = result.opportunities[0]
        self.assertEqual(item.external_id, "42")
        self.assertEqual(item.description, "SQL and Python")
        self.assertEqual(item.opportunity_type, "internship")
        self.assertEqual(item.category, "Data")
        self.assertNotIn("software", item.category.casefold())
        self.assertEqual(item.metadata["domains"], ["data", "software"])
        self.assertEqual(item.metadata["ats"], "greenhouse")
        self.assertEqual(item.metadata["departments"], ["Data"])
        self.assertEqual(item.metadata["offices"], ["New York Office"])
        self.assertIsNone(item.posted_at)
        self.assertEqual(item.metadata["dates"]["posted"]["state"], "unknown")
        self.assertEqual(
            item.metadata["dates"]["source_updated"]["provenance"],
            "greenhouse.updated_at",
        )
        self.assertNotIn("base_score", item.metadata)
        self.assertTrue(urlopen.call_args.args[0].full_url.endswith("?content=true"))

    @patch("monitor.fetchers._open_remote")
    def test_greenhouse_uses_published_and_deadline_fields_when_present(self, urlopen):
        payload = {
            "jobs": [
                {
                    "id": 45,
                    "title": "Research Intern",
                    "absolute_url": "https://example.com/jobs/45",
                    "content": "Technical research",
                    "first_published": "2026-08-08T12:00:00Z",
                    "updated_at": "2026-08-09T12:00:00Z",
                    "application_deadline": "2026-09-15T23:59:00Z",
                }
            ]
        }
        urlopen.return_value = FakeResponse(json.dumps(payload).encode())
        item = fetch_greenhouse(
            {"id": "firm", "name": "Firm", "board": "firm"}
        ).opportunities[0]
        self.assertEqual(item.posted_at, "2026-08-08T12:00:00+00:00")
        self.assertEqual(item.deadline_at, "2026-09-15")
        self.assertEqual(item.metadata["dates"]["posted"]["kind"], "posted")
        self.assertEqual(
            item.metadata["dates"]["posted"]["provenance"],
            "greenhouse.first_published",
        )
        self.assertEqual(item.metadata["dates"]["deadline"]["state"], "date")

    @patch("monitor.fetchers._open_remote")
    def test_greenhouse_can_skip_large_description_payloads(self, urlopen):
        payload = {
            "jobs": [{
                "id": 44,
                "title": "Manufacturing Engineer",
                "absolute_url": "https://example.com/jobs/44",
                "location": {"name": "California"},
                "metadata": [
                    {"name": "Discipline", "value": "Manufacturing"},
                    {"name": "Employment Type", "value": "Full-time"},
                ],
            }]
        }
        urlopen.return_value = FakeResponse(json.dumps(payload).encode())
        result = fetch_greenhouse({
            "id": "large_board",
            "name": "Large Board",
            "board": "large-board",
            "include_content": False,
            "category_metadata_names": ["Discipline"],
        })

        item = result.opportunities[0]
        self.assertEqual(item.title, "Manufacturing Engineer")
        self.assertEqual(item.description, "")
        self.assertEqual(item.category, "Manufacturing")
        self.assertEqual(
            item.metadata["category_metadata"],
            {"Discipline": "Manufacturing"},
        )
        self.assertEqual(
            urlopen.call_args.args[0].full_url,
            "https://boards-api.greenhouse.io/v1/boards/large-board/jobs",
        )

    @patch("monitor.fetchers._open_remote")
    def test_source_domains_do_not_pollute_onboarding_category_matches(self, urlopen):
        payload = {
            "jobs": [
                {
                    "id": 1,
                    "title": "Commercial Counsel",
                    "absolute_url": "https://example.com/jobs/1",
                    "departments": [{"name": "Legal"}],
                    "content": "Review contracts and advise the business",
                },
                {
                    "id": 2,
                    "title": "Platform Engineer",
                    "absolute_url": "https://example.com/jobs/2",
                    "departments": [{"name": "Cybersecurity"}],
                    "content": "Build reliable internal platforms",
                },
            ]
        }
        urlopen.return_value = FakeResponse(json.dumps(payload).encode())
        result = fetch_greenhouse({
            "id": "broad_employer",
            "name": "Broad Employer",
            "board": "broad-employer",
            "domains": ["software", "cybersecurity"],
        })
        profile = build_profile(
            ["cybersecurity"],
            include_terms=["cybersecurity"],
        )
        unrelated, relevant = result.opportunities
        score_opportunity(unrelated, profile)
        score_opportunity(relevant, profile)

        self.assertEqual(unrelated.category, "Legal")
        self.assertEqual(unrelated.metadata["domains"], ["software", "cybersecurity"])
        self.assertEqual(unrelated.score, 25)
        self.assertEqual(unrelated.tier, "skip")
        self.assertEqual(unrelated.reasons, [])
        self.assertEqual(relevant.category, "Cybersecurity")
        self.assertEqual(relevant.score, 46)
        self.assertEqual(relevant.tier, "watch")
        self.assertTrue(relevant.reasons)

    @patch("monitor.fetchers._open_remote")
    def test_greenhouse_full_time_role_is_a_job(self, urlopen):
        payload = {
            "jobs": [{
                "id": 43,
                "title": "Senior Software Engineer",
                "absolute_url": "https://example.com/jobs/43",
                "location": {"name": "Remote"},
                "content": "Build reliable systems",
                "departments": [{"name": "Engineering"}],
                "metadata": [{"name": "Employment Type", "value": "Full-time"}],
            }]
        }
        urlopen.return_value = FakeResponse(json.dumps(payload).encode())
        result = fetch_greenhouse({
            "id": "firm",
            "name": "Firm",
            "board": "firm",
            "domains": ["software"],
        })
        item = result.opportunities[0]
        self.assertEqual(item.opportunity_type, "job")
        self.assertEqual(item.commitment, "Full-time")

    @patch("monitor.fetchers._open_remote")
    def test_ashby_normalizes_listed_jobs_and_skips_unlisted_or_incomplete_rows(self, urlopen):
        payload = {
            "jobs": [
                {
                    "id": "ashby-1",
                    "title": "Research Engineering Intern",
                    "jobUrl": "https://jobs.ashbyhq.com/acme/ashby-1",
                    "location": "Remote - US",
                    "secondaryLocations": [
                        {"location": "New York, NY"},
                        {"location": "Remote - US"},
                        {"name": "Ignored shape"},
                    ],
                    "descriptionPlain": "Build research systems. Applications close September 15, 2027.",
                    "department": "Engineering",
                    "team": "Research",
                    "employmentType": "Intern",
                    "workplaceType": "Remote",
                    "publishedAt": "2027-08-09T12:30:00Z",
                    "isListed": True,
                },
                {
                    "id": "hidden",
                    "title": "Hidden Role",
                    "jobUrl": "https://jobs.ashbyhq.com/acme/hidden",
                    "isListed": False,
                },
                {
                    "id": "missing-title",
                    "jobUrl": "https://jobs.ashbyhq.com/acme/missing-title",
                },
                {
                    "id": "missing-url",
                    "title": "Missing URL",
                },
                "not an object",
            ]
        }
        urlopen.return_value = FakeResponse(json.dumps(payload).encode())

        result = fetch_ashby(
            {
                "id": "acme_ashby",
                "name": "Acme",
                "kind": "ashby",
                "board": "acme/research",
                "domains": ["software", "academia_research"],
                "packs": ["engineering"],
                "official": True,
            }
        )

        self.assertEqual(len(result.opportunities), 1)
        self.assertEqual(result.message, "1 Ashby jobs")
        item = result.opportunities[0]
        self.assertEqual(item.external_id, "ashby-1")
        self.assertEqual(item.title, "Research Engineering Intern")
        self.assertEqual(item.organization, "Acme")
        self.assertEqual(item.location, "Remote - US, New York, NY")
        self.assertEqual(item.category, "Engineering Research")
        self.assertEqual(item.opportunity_type, "internship")
        self.assertEqual(item.commitment, "Intern")
        self.assertEqual(item.posted_at, "2027-08-09T12:30:00+00:00")
        self.assertEqual(item.deadline_at, "2027-09-15")
        self.assertEqual(item.metadata["ats"], "ashby")
        self.assertEqual(item.metadata["department"], "Engineering")
        self.assertEqual(item.metadata["team"], "Research")
        self.assertEqual(item.metadata["workplace_type"], "Remote")
        self.assertEqual(
            item.metadata["dates"]["posted"]["provenance"],
            "ashby.publishedAt",
        )
        self.assertEqual(
            urlopen.call_args.args[0].full_url,
            "https://api.ashbyhq.com/posting-api/job-board/acme%2Fresearch",
        )

    @patch("monitor.fetchers._request", return_value=b'{"jobs":[]}')
    def test_ashby_uses_its_explicit_bounded_response_budget(self, request):
        result = fetch_ashby(
            {"id": "large", "name": "Large Board", "board": "large/board"}
        )

        self.assertEqual(result.opportunities, [])
        request.assert_called_once_with(
            "https://api.ashbyhq.com/posting-api/job-board/large%2Fboard",
            max_bytes=MAX_ASHBY_RESPONSE_BYTES,
        )
        self.assertGreater(MAX_ASHBY_RESPONSE_BYTES, MAX_RESPONSE_BYTES)
        self.assertLessEqual(MAX_ASHBY_RESPONSE_BYTES, 32 * 1024 * 1024)

    @patch("monitor.fetchers._open_remote")
    def test_ashby_uses_secondary_location_without_leading_separator(self, urlopen):
        payload = {
            "jobs": [
                {
                    "title": "Program Fellow",
                    "applyUrl": "https://jobs.ashbyhq.com/acme/secondary-only",
                    "secondaryLocations": [{"location": "New York, NY"}],
                    "descriptionHtml": "<p>Community research &amp; policy</p>",
                }
            ]
        }
        urlopen.return_value = FakeResponse(json.dumps(payload).encode())

        item = fetch_ashby(
            {"id": "acme", "name": "Acme", "board": "acme"}
        ).opportunities[0]

        self.assertEqual(item.location, "New York, NY")
        self.assertEqual(item.url, "https://jobs.ashbyhq.com/acme/secondary-only")
        self.assertRegex(item.external_id, r"^[a-f0-9]{20}$")
        self.assertEqual(item.description, "Community research & policy")
        self.assertEqual(item.opportunity_type, "fellowship")

    @patch("monitor.fetchers._open_remote")
    def test_ashby_rejects_malformed_and_unbounded_responses(self, urlopen):
        urlopen.side_effect = [
            FakeResponse(json.dumps({"jobs": {}}).encode()),
            FakeResponse(
                json.dumps(
                    {"jobs": [None] * (MAX_OPPORTUNITIES_PER_SOURCE + 1)}
                ).encode()
            ),
        ]
        source = {"id": "acme", "name": "Acme", "board": "acme"}

        with self.assertRaisesRegex(ValueError, "jobs list"):
            fetch_ashby(source)
        with self.assertRaisesRegex(ResponseTooLargeError, "too many jobs"):
            fetch_ashby(source)

    @patch("monitor.fetchers._open_remote")
    def test_fetch_source_dispatches_ashby_and_applies_source_filters(self, urlopen):
        payload = {
            "jobs": [
                {
                    "id": "research",
                    "title": "Research Engineer",
                    "jobUrl": "https://jobs.ashbyhq.com/acme/research",
                },
                {
                    "id": "sales",
                    "title": "Sales Engineer",
                    "jobUrl": "https://jobs.ashbyhq.com/acme/sales",
                },
            ]
        }
        urlopen.return_value = FakeResponse(json.dumps(payload).encode())

        result = fetch_source(
            {
                "id": "acme",
                "name": "Acme",
                "kind": "ashby",
                "board": "acme",
                "item_include": ["research"],
            }
        )

        self.assertEqual(
            [item.external_id for item in result.opportunities],
            ["research"],
        )

    @patch("monitor.fetchers._open_remote")
    def test_lever_normalization(self, urlopen):
        payload = [{
            "id": "abc",
            "text": "Research Engineer",
            "hostedUrl": "https://jobs.lever.co/firm/abc",
            "createdAt": 1786233600000,
            "descriptionPlain": "Scientific computing",
            "categories": {
                "location": "Remote",
                "team": "Research",
                "department": "Engineering",
                "commitment": "Full-time",
            },
            "lists": [],
            "workplaceType": "remote",
        }]
        urlopen.return_value = FakeResponse(json.dumps(payload).encode())
        result = fetch_lever({
            "id": "firm",
            "name": "Firm",
            "site": "firm",
            "domains": ["software", "data"],
        })
        item = result.opportunities[0]
        self.assertEqual(item.location, "Remote")
        self.assertIn("Scientific computing", item.description)
        self.assertEqual(item.opportunity_type, "job")
        self.assertEqual(item.commitment, "Full-time")
        self.assertEqual(item.category, "Research Engineering")
        self.assertNotIn("data", item.category.casefold())
        self.assertEqual(item.metadata["team"], "Research")
        self.assertEqual(item.metadata["department"], "Engineering")
        self.assertEqual(item.metadata["workplace_type"], "remote")

    @patch("monitor.fetchers._open_remote")
    def test_lever_extracts_explicit_deadline_with_provenance(self, urlopen):
        payload = [{
            "id": "deadline",
            "text": "Research Intern",
            "hostedUrl": "https://jobs.lever.co/firm/deadline",
            "createdAt": 1786233600000,
            "descriptionPlain": "Applications close on September 15, 2027.",
            "categories": {"commitment": "Intern"},
            "lists": [],
        }]
        urlopen.return_value = FakeResponse(json.dumps(payload).encode())
        item = fetch_lever(
            {"id": "firm", "name": "Firm", "site": "firm"}
        ).opportunities[0]
        self.assertEqual(item.deadline_at, "2027-09-15")
        self.assertEqual(
            item.metadata["dates"]["posted"]["provenance"],
            "lever.createdAt",
        )
        self.assertEqual(item.metadata["dates"]["posted"]["confidence"], "medium")

    @patch("monitor.fetchers._open_remote")
    def test_html_link_filters_navigation(self, urlopen):
        urlopen.return_value = FakeResponse(b'<a href="/jobs/ml-intern">ML Graduate Intern</a><a href="/privacy">Privacy</a>')
        source = {
            "id": "lab", "name": "Lab", "kind": "html_links",
            "url": "https://example.com/careers", "include": ["intern"],
            "exclude": ["privacy"], "same_domain": True,
        }
        result = fetch_html_links(source)
        self.assertEqual([item.title for item in result.opportunities], ["ML Graduate Intern"])
        self.assertEqual(result.opportunities[0].opportunity_type, "internship")
        self.assertNotIn("base_score", result.opportunities[0].metadata)

    @patch("monitor.fetchers._open_remote")
    def test_html_link_program_cards_have_short_titles_and_program_default(self, urlopen):
        urlopen.return_value = FakeResponse(
            b'<a href="/programs/amp">PROGRAM AMP ACCEPTING APPLICATIONS '
            b'A five-week summer experience. Learn More</a>'
            b'<a href="/programs/grf">PROGRAM Graduate Research Fellowship '
            b'ACCEPTING APPLICATIONS A doctoral award. Learn More</a>'
        )
        source = {
            "id": "programs",
            "name": "Programs",
            "kind": "html_links",
            "url": "https://example.com/programs",
            "include": ["/programs/"],
            "same_domain": True,
            "opportunity_types": ["fellowship", "internship", "program"],
            "default_opportunity_type": "program",
        }
        result = fetch_html_links(source)
        self.assertEqual(
            [item.title for item in result.opportunities],
            ["AMP", "Graduate Research Fellowship"],
        )
        self.assertEqual(
            [item.opportunity_type for item in result.opportunities],
            ["program", "fellowship"],
        )

    @patch("monitor.fetchers._open_remote")
    def test_html_links_support_bounded_page_query_pagination_and_deduplication(self, urlopen):
        first = b'<a href="/jobs/1?page=1&amp;ref=careers#details">Software Intern</a>'
        second = (
            b'<a href="/jobs/1?page=2&amp;ref=careers#details">Software Intern</a>'
            b'<a href="/jobs/2">Research Intern</a>'
        )
        urlopen.side_effect = [FakeResponse(first), FakeResponse(second)]
        source = {
            "id": "google",
            "name": "Google",
            "kind": "html_links",
            "url": "https://careers.google.com/jobs/results/?q=intern",
            "include": ["intern"],
            "same_domain": True,
            "pages": 2,
        }
        result = fetch_html_links(source)
        self.assertEqual(
            [item.title for item in result.opportunities],
            ["Software Intern", "Research Intern"],
        )
        self.assertEqual(
            result.opportunities[0].url,
            "https://careers.google.com/jobs/1?ref=careers#details",
        )
        self.assertEqual(
            result.opportunities[0].external_id,
            stable_hash("https://careers.google.com/jobs/1?ref=careers#details"),
        )
        self.assertEqual(result.opportunities[0].metadata["page_count"], 2)
        self.assertEqual(len(urlopen.call_args_list), 2)
        self.assertNotIn("page=", urlopen.call_args_list[0].args[0].full_url)
        self.assertIn("page=2", urlopen.call_args_list[1].args[0].full_url)

    @patch("monitor.fetchers._open_remote")
    def test_html_links_honor_safe_html_base_for_relative_job_links(self, urlopen):
        urlopen.return_value = FakeResponse(
            b'<base href="https://www.google.com/about/careers/applications/">'
            b'<a href="jobs/results/123">Software Intern</a>'
        )
        source = {
            "id": "google",
            "name": "Google",
            "kind": "html_links",
            "url": "https://www.google.com/about/careers/applications/jobs/results/?q=intern",
            "include": ["intern"],
            "same_domain": True,
        }

        item = fetch_html_links(source).opportunities[0]

        self.assertEqual(
            item.url,
            "https://www.google.com/about/careers/applications/jobs/results/123",
        )

    @patch("monitor.fetchers._open_remote")
    def test_html_links_use_bounded_accessible_title_when_anchor_text_is_empty(self, urlopen):
        urlopen.return_value = FakeResponse(
            b'<base href="https://www.google.com/about/careers/applications/">'
            b'<a href="jobs/results/456" '
            b'aria-label="Learn more about Strategy Associate, YouTube"></a>'
        )
        source = {
            "id": "google",
            "name": "Google",
            "kind": "html_links",
            "url": "https://www.google.com/about/careers/applications/jobs/results/?q=associate",
            "include": ["associate"],
            "same_domain": True,
        }

        item = fetch_html_links(source).opportunities[0]

        self.assertEqual(item.title, "Strategy Associate, YouTube")
        self.assertEqual(
            item.url,
            "https://www.google.com/about/careers/applications/jobs/results/456",
        )

    @patch("monitor.fetchers._open_remote")
    def test_html_links_reject_cross_host_configured_link_base_before_fetch(self, urlopen):
        with self.assertRaisesRegex(ValueError, "link_base_url"):
            fetch_html_links(
                {
                    "id": "invalid-base",
                    "name": "Invalid base",
                    "kind": "html_links",
                    "url": "https://example.com/jobs",
                    "link_base_url": "https://other.example/jobs/",
                }
            )
        urlopen.assert_not_called()

    @patch("monitor.fetchers._open_remote")
    def test_html_links_reject_invalid_page_bounds_before_fetch(self, urlopen):
        with self.assertRaisesRegex(ValueError, "pages must be an integer"):
            fetch_html_links(
                {
                    "id": "bad-pages",
                    "name": "Bad pages",
                    "kind": "html_links",
                    "url": "https://example.com/jobs",
                    "pages": 21,
                }
            )
        urlopen.assert_not_called()

    @patch("monitor.fetchers._open_remote")
    def test_jibe_normalization(self, urlopen):
        payload = {"jobs": [{"data": {
            "slug": "60001", "req_id": "60001", "title": "2028 Operations Intern",
            "full_location": "Raleigh, North Carolina", "description": "<p>Process improvement</p>",
            "posted_date": "2027-08-09T00:00:00+0000", "categories": [{"name": "Operations"}],
        }}]}
        urlopen.return_value = FakeResponse(json.dumps(payload).encode())
        source = {
            "id": "example",
            "name": "Example Organization",
            "api_url": "https://example.com/api/jobs",
            "job_url_template": "https://careers.example.org/jobs/{slug}?lang=en-us",
            "domains": ["operations", "public_interest"],
            "opportunity_types": ["internship"],
        }
        result = fetch_jibe(source)
        item = result.opportunities[0]
        self.assertEqual(item.external_id, "60001")
        self.assertEqual(item.url, "https://careers.example.org/jobs/60001?lang=en-us")
        self.assertEqual(item.opportunity_type, "internship")
        self.assertEqual(item.category, "Operations")
        self.assertNotIn("public interest", item.category.casefold())
        self.assertEqual(item.metadata["categories"], ["Operations"])
        self.assertEqual(
            item.metadata["domains"],
            ["operations", "public_interest"],
        )
        self.assertEqual(item.posted_at, "2027-08-09T00:00:00+00:00")
        self.assertEqual(
            item.metadata["dates"]["posted"]["provenance"],
            "jibe.posted_date",
        )

    @patch("monitor.fetchers._open_remote")
    def test_jibe_prefers_structured_deadline(self, urlopen):
        payload = {"jobs": [{"data": {
            "slug": "deadline",
            "req_id": "deadline",
            "title": "Research Intern",
            "description": "Apply when ready.",
            "application_deadline": "October 1, 2027",
        }}]}
        urlopen.return_value = FakeResponse(json.dumps(payload).encode())
        item = fetch_jibe(
            {
                "id": "example",
                "name": "Example",
                "api_url": "https://example.com/api/jobs",
                "job_url_template": "https://example.com/jobs/{slug}",
            }
        ).opportunities[0]
        self.assertEqual(item.deadline_at, "2027-10-01")
        self.assertEqual(
            item.metadata["dates"]["deadline"]["provenance"],
            "jibe.structured_deadline",
        )

    @patch("monitor.fetchers._open_remote")
    def test_jibe_follows_bounded_pagination(self, urlopen):
        first = {
            "jobs": [{"data": {
                "slug": "1",
                "req_id": "1",
                "title": "Software Engineer",
                "employment_type": "FULL_TIME",
            }}],
            "totalCount": 2,
        }
        second = {
            "jobs": [{"data": {
                "slug": "2",
                "req_id": "2",
                "title": "Research Intern",
                "employment_type": "FULL_TIME",
            }}],
            "totalCount": 2,
        }
        urlopen.side_effect = [
            FakeResponse(json.dumps(first).encode()),
            FakeResponse(json.dumps(second).encode()),
        ]
        source = {
            "id": "lab",
            "name": "Lab",
            "api_url": "https://example.com/api/jobs?page=1&limit=1",
            "job_url_template": "https://example.com/jobs/{slug}",
            "opportunity_types": ["job", "internship"],
        }
        result = fetch_jibe(source)
        self.assertEqual([item.external_id for item in result.opportunities], ["1", "2"])
        self.assertEqual(
            [item.opportunity_type for item in result.opportunities],
            ["job", "internship"],
        )
        second_request = urlopen.call_args_list[1].args[0]
        self.assertIn("page=2", second_request.full_url)

    @patch("monitor.fetchers._open_remote")
    def test_jibe_rejects_unbounded_advertised_result_set(self, urlopen):
        payload = {"jobs": [], "totalCount": 5001}
        urlopen.return_value = FakeResponse(json.dumps(payload).encode())
        with self.assertRaises(ResponseTooLargeError):
            fetch_jibe({
                "id": "lab",
                "name": "Lab",
                "api_url": "https://example.com/api/jobs?page=1&limit=100",
            })

    @patch("monitor.fetchers._request")
    def test_jibe_rechecks_actual_count_after_pagination(self, request):
        first = {"jobs": [{"data": {"req_id": "first"}}], "totalCount": 5000}
        overflow = {
            "jobs": [
                {"data": {"req_id": "extra-{}".format(index)}}
                for index in range(5000)
            ],
            "totalCount": 5000,
        }
        request.side_effect = [
            json.dumps(first).encode(),
            json.dumps(overflow).encode(),
        ]
        with self.assertRaises(ResponseTooLargeError):
            _jibe_pages({"api_url": "https://example.com/jobs?page=1"})

    def test_type_inference_covers_common_program_and_job_types(self):
        cases = {
            "Backend Software Engineer": "job",
            "AI Safety Fellows Program": "fellowship",
            "Postdoctoral Researcher in Ecology": "postdoc",
            "Electrical Apprentice": "apprenticeship",
            "Hardware Engineering Co-op": "co_op",
            "Research Residency": "residency",
            "Resident Engineer": "job",
            "Graduate Scholarship": "scholarship",
            "Machine Learning Internship": "internship",
            "Summer Research Experience": "research_program",
            "Technician Training Program": "training",
        }
        for title, expected in cases.items():
            with self.subTest(title=title):
                self.assertEqual(_infer_opportunity_type({}, title), expected)

    def test_type_inference_uses_single_declared_feed_type_as_fallback(self):
        source = {"opportunity_types": ["internship"]}
        self.assertEqual(_infer_opportunity_type(source, "Graduate Researcher"), "internship")

    def test_legacy_explicit_type_override_is_preserved(self):
        source = {"opportunity_type": "program"}
        self.assertEqual(_infer_opportunity_type(source, "Software Engineer"), "program")

    def test_source_filter_uses_title_not_boilerplate(self):
        result = FetchResult([
            Opportunity("firm", "1", "Senior Engineer", "Firm", "https://example.com/1", description="Learn about our internships"),
            Opportunity("firm", "2", "Machine Learning Intern", "Firm", "https://example.com/2"),
        ], "hash")
        filtered = filter_items({"item_include": ["intern"]}, result)
        self.assertEqual([item.external_id for item in filtered.opportunities], ["2"])

    def test_opportunity_fields_and_source_item_count_are_bounded(self):
        item = Opportunity(
            "firm",
            "one",
            "T" * 800,
            "O" * 500,
            "https://example.com/" + "u" * 3000,
            metadata={"employer": "m" * 2000},
        )
        self.assertEqual(len(item.title), 500)
        self.assertEqual(len(item.organization), 300)
        self.assertEqual(len(item.url), 2048)
        self.assertEqual(len(item.metadata["employer"]), 1000)
        with self.assertRaisesRegex(ValueError, "more than 5000"):
            FetchResult([item] * 5001, "hash")

    @patch("monitor.fetchers._open_remote")
    def test_watch_hash_ignores_script_noise(self, urlopen):
        source = {"id": "program", "name": "Program", "url": "https://example.com", "kind": "watch_page"}
        urlopen.return_value = FakeResponse(b"<h1>Applications open</h1><script>timestamp=1</script>")
        first = fetch_watch_page(source)
        urlopen.return_value = FakeResponse(b"<h1>Applications open</h1><script>timestamp=2</script>")
        second = fetch_watch_page(source)
        self.assertEqual(first.content_hash, second.content_hash)
        self.assertEqual(first.opportunities, [])
        self.assertIn("no listing records", first.message)

    @patch("monitor.fetchers._open_remote")
    def test_closed_watch_page_does_not_create_active_opportunity(self, urlopen):
        source = {"id": "program", "name": "Program", "url": "https://example.com", "kind": "watch_page"}
        urlopen.return_value = FakeResponse(b"<h1>Applications closed</h1><p>Check back next year.</p>")
        result = fetch_watch_page(source)
        self.assertEqual(result.opportunities, [])

    @patch("monitor.fetchers._open_remote")
    def test_watch_page_can_be_published_by_explicit_local_override(self, urlopen):
        source = {
            "id": "program",
            "name": "Research Program",
            "url": "https://example.com",
            "kind": "watch_page",
            "publish_as_opportunity": True,
            "opportunity_type": "program",
            "domains": ["academia_research"],
        }
        urlopen.return_value = FakeResponse(b"<h1>Applications open</h1>")
        result = fetch_watch_page(source)
        self.assertEqual(len(result.opportunities), 1)
        item = result.opportunities[0]
        self.assertEqual(item.opportunity_type, "program")
        self.assertEqual(item.metadata["domains"], ["academia_research"])
        self.assertNotIn("base_score", item.metadata)
        self.assertNotIn("tier", item.metadata)

    @patch("monitor.fetchers._open_remote")
    def test_published_watch_page_exposes_open_until_filled_state(self, urlopen):
        source = {
            "id": "program",
            "name": "Research Program",
            "url": "https://example.com",
            "kind": "watch_page",
            "publish_as_opportunity": True,
        }
        urlopen.return_value = FakeResponse(b"<p>Applications are open until filled.</p>")
        item = fetch_watch_page(source).opportunities[0]
        self.assertIsNone(item.deadline_at)
        self.assertEqual(
            item.metadata["dates"]["deadline"]["state"],
            "open_until_filled",
        )

    @patch("monitor.fetchers._open_remote")
    def test_request_accepts_body_at_limit(self, urlopen):
        response = FakeResponse(b"12345")
        urlopen.return_value = response
        self.assertEqual(_request("https://example.com", max_bytes=5), b"12345")
        self.assertTrue(all(size <= 6 for size in response.read_sizes))

    @patch("monitor.fetchers._open_remote")
    def test_request_rejects_body_over_limit(self, urlopen):
        urlopen.return_value = FakeResponse(b"123456")
        with self.assertRaises(ResponseTooLargeError):
            _request("https://example.com", max_bytes=5)

    @patch("monitor.fetchers._open_remote")
    def test_request_rejects_oversized_content_length_before_read(self, urlopen):
        response = FakeResponse(b"", headers={"Content-Length": "6"})
        urlopen.return_value = response
        with self.assertRaises(ResponseTooLargeError):
            _request("https://example.com", max_bytes=5)
        self.assertEqual(response.read_sizes, [])

    def test_request_rejects_non_http_source(self):
        with self.assertRaises(ValueError):
            _request("file:///tmp/private")

    @patch("monitor.fetchers._open_remote")
    def test_request_enforces_total_wall_clock_deadline(self, open_remote):
        open_remote.return_value = FakeResponse(b"12345")
        with patch(
            "monitor.fetchers.time.monotonic",
            side_effect=[0.0, 0.0, 0.0, 2.0],
        ):
            with self.assertRaisesRegex(TimeoutError, "wall-clock"):
                _request("https://example.com", timeout=1)

    @unittest.skipUnless(hasattr(signal, "setitimer"), "bounded resolver requires Unix signals")
    @patch("monitor.fetchers._open_remote")
    def test_request_deadline_bounds_dns_and_restores_signal_state(self, open_remote):
        previous_handler = signal.getsignal(signal.SIGALRM)

        def stalled_resolver(*_args, **_kwargs):
            time.sleep(1)
            return []

        started = time.monotonic()
        with (
            patch("monitor.fetchers.socket.getaddrinfo", side_effect=stalled_resolver),
            self.assertRaisesRegex(TimeoutError, "resolution exceeded"),
        ):
            _request("https://resolver.example/jobs", timeout=0.1)
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual(signal.getsignal(signal.SIGALRM), previous_handler)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))
        open_remote.assert_not_called()

    def test_request_rejects_plain_http_and_url_credentials(self):
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            _request("http://example.com/jobs")
        with self.assertRaisesRegex(ValueError, "credentials"):
            _request("https://user:secret@example.com/jobs")

    def test_request_rejects_private_literal_and_resolved_addresses(self):
        with self.assertRaisesRegex(ValueError, "public addresses"):
            _validate_remote_url("https://127.0.0.1/jobs")
        with patch(
            "monitor.fetchers.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("10.0.0.4", 443))],
        ):
            with self.assertRaisesRegex(ValueError, "public addresses"):
                _validate_remote_url("https://careers.example/jobs")

    def test_redirect_handler_rejects_private_target_before_following(self):
        handler = _SafeRedirectHandler()
        with self.assertRaisesRegex(ValueError, "public addresses"):
            handler.redirect_request(
                None,
                None,
                302,
                "Found",
                {},
                "https://169.254.169.254/latest/meta-data",
            )

    def test_redirect_handler_rejects_cross_host_target(self):
        handler = _SafeRedirectHandler()
        request = urllib.request.Request("https://careers.example/jobs")
        with self.assertRaisesRegex(ValueError, "configured host"):
            handler.redirect_request(
                request,
                None,
                302,
                "Found",
                {},
                "https://redirect.example/jobs",
            )

    def test_redirect_handler_bounds_every_intermediate_response_body(self):
        handler = _SafeRedirectHandler()
        handler.parent = Mock()
        payload = b"x" * (MAX_RESPONSE_BYTES + 1)

        for code in (301, 302, 303, 307, 308):
            with self.subTest(code=code):
                request = urllib.request.Request("https://careers.example/jobs")
                request.timeout = 5
                response = FakeResponse(payload)
                with self.assertRaisesRegex(ResponseTooLargeError, "redirect response"):
                    getattr(handler, "http_error_{}".format(code))(
                        request,
                        response,
                        code,
                        "Redirect",
                        {"location": "https://careers.example/next"},
                    )
                self.assertTrue(response.closed)
                self.assertNotIn(None, response.read_sizes)
                self.assertNotIn(-1, response.read_sizes)
                self.assertTrue(
                    response.read_sizes
                    and max(response.read_sizes) <= READ_CHUNK_BYTES
                )
        handler.parent.open.assert_not_called()

    def test_redirect_handler_preserves_bounded_same_host_redirects(self):
        handler = _SafeRedirectHandler()
        handler.parent = Mock()
        marker = object()
        handler.parent.open.return_value = marker

        for code in (302, 308):
            with self.subTest(code=code):
                handler.parent.open.reset_mock()
                request = urllib.request.Request("https://careers.example/jobs")
                request.timeout = 5
                response = FakeResponse(b"small redirect body")

                result = getattr(handler, "http_error_{}".format(code))(
                    request,
                    response,
                    code,
                    "Redirect",
                    {"location": "https://careers.example/next"},
                )

                self.assertIs(result, marker)
                self.assertTrue(response.closed)
                self.assertNotIn(None, response.read_sizes)
                self.assertNotIn(-1, response.read_sizes)
                followed = handler.parent.open.call_args.args[0]
                self.assertEqual(followed.full_url, "https://careers.example/next")

    def test_https_connection_pins_the_validated_numeric_address(self):
        connection = _PublicHTTPSConnection("careers.example", timeout=5)
        with patch("monitor.fetchers.socket.create_connection") as create_connection:
            marker = object()
            create_connection.return_value = marker
            result = connection._create_pinned_connection(
                ("careers.example", 443),
                timeout=5,
                source_address=None,
            )
        self.assertIs(result, marker)
        self.assertEqual(
            create_connection.call_args.args[0],
            ("93.184.216.34", 443),
        )

    @unittest.skipUnless(hasattr(signal, "setitimer"), "aggregate connect guard requires Unix signals")
    def test_https_connection_shares_one_deadline_across_addresses(self):
        connection = _PublicHTTPSConnection("careers.example", timeout=0.1)
        connection._pinned_addresses = ["93.184.216.34", "93.184.216.35", "93.184.216.36"]

        def stalled_connect(*_args, **_kwargs):
            time.sleep(0.08)
            raise OSError("synthetic timeout")

        started = time.monotonic()
        with (
            patch("monitor.fetchers.socket.create_connection", side_effect=stalled_connect),
            self.assertRaisesRegex(TimeoutError, "connection exceeded"),
        ):
            connection._create_pinned_connection(
                ("careers.example", 443),
                timeout=0.1,
                source_address=None,
            )
        self.assertLess(time.monotonic() - started, 0.2)

    @unittest.skipUnless(hasattr(signal, "setitimer"), "aggregate request guard requires Unix signals")
    @patch("monitor.fetchers._open_remote")
    def test_request_deadline_interrupts_stalled_open_phase(self, open_remote):
        def stalled_open(*_args, **_kwargs):
            time.sleep(1)
            return FakeResponse(b"late")

        open_remote.side_effect = stalled_open
        started = time.monotonic()
        with self.assertRaisesRegex(TimeoutError, "request exceeded"):
            _request("https://example.com/jobs", timeout=0.1)
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))

    @patch("monitor.fetchers._open_remote")
    def test_request_rejects_unsafe_final_url(self, open_remote):
        open_remote.return_value = FakeResponse(
            b"safe-looking body",
            url="https://[::1]/private",
        )
        with self.assertRaisesRegex(ValueError, "public addresses"):
            _request("https://example.com/jobs")


if __name__ == "__main__":
    unittest.main()
