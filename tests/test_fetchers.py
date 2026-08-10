import json
import unittest
import urllib.request
from unittest.mock import patch

from monitor.fetchers import (
    ResponseTooLargeError,
    _PublicHTTPSConnection,
    _SafeRedirectHandler,
    _infer_opportunity_type,
    _jibe_pages,
    _request,
    _validate_remote_url,
    fetch_greenhouse,
    fetch_html_links,
    fetch_jibe,
    fetch_lever,
    fetch_watch_page,
    filter_items,
)
from monitor.models import FetchResult, Opportunity


class FakeResponse:
    def __init__(self, payload, headers=None, url="https://example.com"):
        self.payload = payload
        self.offset = 0
        self.headers = headers or {}
        self.read_sizes = []
        self.url = url

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


class FetcherTests(unittest.TestCase):
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
        self.assertIn("data", item.category)
        self.assertEqual(item.metadata["domains"], ["data", "software"])
        self.assertEqual(item.metadata["ats"], "greenhouse")
        self.assertNotIn("base_score", item.metadata)

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
        self.assertEqual(item.metadata["workplace_type"], "remote")

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
        self.assertEqual(result.opportunities[0].external_id, "60001")
        self.assertEqual(result.opportunities[0].url, "https://careers.example.org/jobs/60001?lang=en-us")
        self.assertEqual(result.opportunities[0].opportunity_type, "internship")

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
