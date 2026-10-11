"""Exercise public board pagination, bounds, identity, and source detection."""

import json
import unittest
from unittest.mock import patch

from monitor.employer_feeds import fetch_workday, workday_board
from monitor.fetchers import ResponseTooLargeError, fetch_source
from monitor.source_registry import build_custom_source, detect_source, validate_source
from monitor.profile import ProfileValidationError

SOURCE = {
    "id": "bose",
    "name": "Bose",
    "kind": "workday",
    "url": "https://example.wd1.myworkdayjobs.com/en-US/External",
    "opportunity_types": ["job", "internship", "co_op"],
}


def payload(total, *paths):
    return json.dumps(
        {
            "total": total,
            "jobPostings": [
                {
                    "title": "Software Engineer Co-op",
                    "externalPath": path,
                    "locationsText": "Boston, MA",
                }
                for path in paths
            ],
        }
    ).encode()


def detail():
    return json.dumps(
        {
            "jobPostingInfo": {
                "jobReqId": "R1",
                "jobDescription": "<p>Software development with Python. Current graduate student required.</p>",
                "startDate": "2026-10-01",
                "timeType": "Full time",
            }
        }
    ).encode()


class WorkdayTests(unittest.TestCase):
    def test_paginated_search_normalizes_details_and_has_no_occupation_filter(self):
        with patch(
            "monitor.employer_feeds._request",
            side_effect=[
                payload(2, "/job/Boston/R1"),
                payload(0, "/job/Boston/R2"),
                detail(),
                detail(),
            ],
        ) as request:
            result = fetch_source(SOURCE)
        self.assertEqual(len(result.opportunities), 2)
        self.assertIn("Python", result.opportunities[0].description)
        self.assertEqual(result.opportunities[0].opportunity_type, "co_op")
        self.assertEqual(result.opportunities[0].posted_at, "2026-10-01")
        self.assertEqual(result.opportunities[0].metadata["ats"], "workday")
        self.assertEqual(
            result.opportunities[0].url,
            "https://example.wd1.myworkdayjobs.com/en-US/External/job/Boston/R1",
        )
        searches = [
            call.kwargs["json_body"]
            for call in request.call_args_list
            if "json_body" in call.kwargs
        ]
        self.assertEqual([body["offset"] for body in searches], [0, 1])
        self.assertTrue(
            all(
                body["searchText"] == "" and body["appliedFacets"] == {}
                for body in searches
            )
        )

    def test_empty_board_is_valid(self):
        with patch(
            "monitor.employer_feeds._request", return_value=payload(0)
        ) as request:
            self.assertEqual(fetch_workday(SOURCE).opportunities, [])
        self.assertEqual(request.call_count, 1)

    def test_missing_detail_does_not_change_identity_or_drop_real_jobs(self):
        with patch(
            "monitor.employer_feeds._request",
            side_effect=[payload(1, "/job/Boston/R1"), OSError("unavailable")],
        ):
            summary = fetch_workday(SOURCE).opportunities[0]
        with patch(
            "monitor.employer_feeds._request",
            side_effect=[payload(1, "/job/Boston/R1"), detail()],
        ):
            full = fetch_workday(SOURCE).opportunities[0]
        self.assertEqual(summary.external_id, full.external_id)
        self.assertEqual(summary.description, "")
        self.assertEqual(summary.metadata["description_availability"], "title_only")
        self.assertEqual(full.metadata["description_availability"], "full")

    def test_incomplete_duplicate_and_changing_pages_fail_closed(self):
        pages = [
            [payload(2, "/job/Boston/R1"), payload(2)],
            [payload(2, "/job/Boston/R1"), payload(2, "/job/Boston/R1")],
            [payload(2, "/job/Boston/R1"), payload(1)],
            [payload(1, "/job/Boston/R1", "/job/Boston/R2")],
            [payload(1, "//evil.example/job/R1")],
        ]
        for responses in pages:
            with (
                self.subTest(responses=responses),
                patch("monitor.employer_feeds._request", side_effect=responses),
            ):
                with self.assertRaises(ValueError):
                    fetch_workday(SOURCE)

    def test_response_and_listing_caps_apply(self):
        with patch("monitor.employer_feeds._request", return_value=payload(5001)):
            with self.assertRaises(ResponseTooLargeError):
                fetch_workday(SOURCE)
        with (
            patch("monitor.employer_feeds.MAX_JIBE_TOTAL_BYTES", 1),
            patch("monitor.employer_feeds._request", return_value=payload(0)),
        ):
            with self.assertRaises(ResponseTooLargeError):
                fetch_workday(SOURCE)

    def test_invalid_shape_is_not_successfully_empty(self):
        for data in [
            {},
            [],
            {"total": True, "jobPostings": []},
            {"total": 1, "jobPostings": [None]},
        ]:
            with (
                self.subTest(data=data),
                patch(
                    "monitor.employer_feeds._request",
                    return_value=json.dumps(data).encode(),
                ),
            ):
                with self.assertRaises(ValueError):
                    fetch_workday(SOURCE)

    def test_workday_urls_are_detected_and_validated(self):
        self.assertEqual(workday_board(SOURCE["url"]), ("example", "External"))
        self.assertEqual(detect_source(SOURCE["url"]), ("workday", {}))
        self.assertEqual(build_custom_source("Bose", SOURCE["url"])["kind"], "workday")
        self.assertEqual(validate_source(SOURCE)["kind"], "workday")
        for url in [
            "https://example.com/External",
            "https://example.wd1.myworkdayjobs.com/",
            "https://example.wd1.myworkdayjobs.com/en-US/../External",
        ]:
            with self.subTest(url=url):
                with self.assertRaises(ProfileValidationError):
                    validate_source(dict(SOURCE, url=url))


class SmartRecruitersTests(unittest.TestCase):
    def test_complete_search_before_optional_details_and_malformed_optional_fields(
        self,
    ):
        from monitor.employer_feeds import fetch_smartrecruiters

        source = {
            "id": "bosch",
            "name": "Bosch",
            "kind": "smartrecruiters",
            "company": "BoschGroup",
        }
        jobs = [
            {
                "id": "123",
                "name": "Accounting Intern",
                "location": "unexpected",
                "function": [],
                "typeOfEmployment": None,
            }
        ]
        responses = [
            json.dumps({"totalFound": 2, "content": jobs}).encode(),
            json.dumps(
                {"totalFound": 2, "content": [dict(jobs[0], id="456")]}
            ).encode(),
            json.dumps({"jobAd": []}).encode(),
            OSError("detail unavailable"),
        ]
        with patch("monitor.employer_feeds._request", side_effect=responses) as request:
            result = fetch_smartrecruiters(source)
        self.assertEqual(len(result.opportunities), 2)
        self.assertEqual(result.opportunities[0].opportunity_type, "internship")
        self.assertEqual(result.opportunities[0].description, "")
        self.assertIn("offset=1", request.call_args_list[1].args[0])
        self.assertEqual(
            result.opportunities[0].url,
            "https://jobs.smartrecruiters.com/BoschGroup/123",
        )

    def test_incomplete_and_duplicate_search_fails_before_any_details(self):
        from monitor.employer_feeds import fetch_smartrecruiters

        source = {"id": "bosch", "name": "Bosch", "company": "BoschGroup"}
        job = {"id": "123", "name": "Engineer"}
        for second in [[], [job]]:
            responses = [
                {"totalFound": 2, "content": [job]},
                {"totalFound": 2, "content": second},
            ]
            with (
                self.subTest(second=second),
                patch(
                    "monitor.employer_feeds._request",
                    side_effect=[json.dumps(x).encode() for x in responses],
                ),
            ):
                with self.assertRaises(ValueError):
                    fetch_smartrecruiters(source)


class HtmlPaginationTests(unittest.TestCase):
    def test_zero_based_and_row_offset_providers(self):
        from monitor.fetchers import _html_page_url

        source = {
            "url": "https://jobs.example.com/search?q=&startrow=0",
            "pagination_parameter": "startrow",
            "pagination_step": 25,
        }
        self.assertIn("startrow=50", _html_page_url(source, 2))
        source = {
            "url": "https://jobs.example.com/search?pr=0",
            "pagination_parameter": "pr",
        }
        self.assertIn("pr=1", _html_page_url(source, 1))
        for value in ["../bad", "x&y", ""]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                _html_page_url(dict(source, pagination_parameter=value), 1)


class EmbeddedBoardTests(unittest.TestCase):
    def rippling_page(self, page, jobs, total=2):
        data = {
            "props": {
                "pageProps": {
                    "dehydratedState": {
                        "queries": [
                            {
                                "queryKey": ["board", "example", "job-posts"],
                                "state": {
                                    "data": {
                                        "items": jobs,
                                        "page": page,
                                        "totalItems": total,
                                        "totalPages": 2,
                                    }
                                },
                            }
                        ]
                    }
                }
            }
        }
        return (
            '<script id="__NEXT_DATA__" type="application/json">'
            + json.dumps(data)
            + "</script>"
        ).encode()

    def test_rippling_reads_all_advertised_pages_and_rejects_missing_snapshot(self):
        from monitor.employer_feeds import fetch_rippling

        source = {"id": "example", "name": "Example", "board": "example"}
        job = {
            "id": "a1",
            "name": "Software Engineer",
            "locations": [{"name": "Boston"}],
        }
        with patch(
            "monitor.employer_feeds._request",
            side_effect=[
                self.rippling_page(0, [job]),
                self.rippling_page(1, [dict(job, id="a2")]),
            ],
        ) as request:
            items = fetch_rippling(source).opportunities
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].location, "Boston")
        self.assertIn("page=1", request.call_args_list[1].args[0])
        with patch(
            "monitor.employer_feeds._request",
            side_effect=[self.rippling_page(0, [job]), self.rippling_page(1, [])],
        ):
            with self.assertRaises(ValueError):
                fetch_rippling(source)
        with patch(
            "monitor.employer_feeds._request",
            return_value=b"<html>Service unavailable</html>",
        ):
            with self.assertRaises(ValueError):
                fetch_rippling(source)

    def test_recruiterbox_parses_literal_data_and_never_executes_script(self):
        from monitor.employer_feeds import fetch_recruiterbox

        source = {
            "id": "example",
            "name": "Example",
            "widget": "1234",
            "company": "example",
        }
        jobs = [
            {
                "hash_id": "abc",
                "title": "Accounting Intern",
                "location": {"city": "Boston"},
            }
        ]
        body = ("untrusted(); rbox = new Rbox(" + json.dumps(jobs) + ", []);").encode()
        with patch("monitor.employer_feeds._request", return_value=body):
            items = fetch_recruiterbox(source).opportunities
        self.assertEqual(items[0].title, "Accounting Intern")
        self.assertEqual(items[0].url, "https://example.hire.trakstar.com/jobs/abc")
        with patch("monitor.employer_feeds._request", return_value=b"untrusted();"):
            with self.assertRaises(ValueError):
                fetch_recruiterbox(source)


class OtherEmployerTests(unittest.TestCase):
    def test_sap_public_pagination_is_unfiltered_and_complete(self):
        from monitor.employer_feeds import fetch_sap_public

        source = {"id": "audio", "name": "Audio", "url": "https://jobs.example.com/"}
        entry = {
            "response": {
                "id": "123",
                "unifiedStandardTitle": "Accounting Intern",
                "unifiedUrlTitle": "Accounting-%28Intern%29",
                "jobLocationShort": ["Boston"],
            }
        }
        responses = [
            {"totalJobs": 2, "jobSearchResult": [entry]},
            {
                "totalJobs": 2,
                "jobSearchResult": [dict(response=dict(entry["response"], id="456"))],
            },
        ]
        with patch(
            "monitor.employer_feeds._request",
            side_effect=[json.dumps(x).encode() for x in responses],
        ) as request:
            items = fetch_sap_public(source).opportunities
        self.assertEqual(len(items), 2)
        self.assertEqual(
            items[0].url, "https://jobs.example.com/job/Accounting-%28Intern%29/123/"
        )
        self.assertEqual(request.call_args_list[1].kwargs["json_body"]["pageNumber"], 1)
        self.assertEqual(request.call_args_list[0].kwargs["json_body"]["keywords"], "")
        with patch(
            "monitor.employer_feeds._request",
            side_effect=[
                json.dumps(responses[0]).encode(),
                json.dumps({"totalJobs": 2, "jobSearchResult": []}).encode(),
            ],
        ):
            with self.assertRaises(ValueError):
                fetch_sap_public(source)

    def test_comeet_keeps_full_descriptions_and_rejects_cross_board_links(self):
        from monitor.employer_feeds import fetch_comeet

        source = {
            "id": "quantum",
            "name": "Quantum",
            "url": "https://www.comeet.com/jobs/quantum/D6.000",
        }
        job = {
            "uid": "C1.37A-47.20D",
            "name": "Software Intern",
            "url_comeet_hosted_page": source["url"] + "/software-intern/C1.37A",
            "location": {"name": "Boston"},
            "custom_fields": {
                "details": [{"value": "<p>Current student. Python development.</p>"}]
            },
        }
        body = ("COMPANY_POSITIONS_DATA = " + json.dumps([job]) + ";").encode()
        with patch("monitor.employer_feeds._request", return_value=body):
            items = fetch_comeet(source).opportunities
        self.assertIn("Python", items[0].description)
        self.assertEqual(items[0].opportunity_type, "internship")
        job["url_comeet_hosted_page"] = "https://www.comeet.com/jobs/other/abc"
        with patch(
            "monitor.employer_feeds._request",
            return_value=(
                "COMPANY_POSITIONS_DATA = " + json.dumps([job]) + ";"
            ).encode(),
        ):
            with self.assertRaises(ValueError):
                fetch_comeet(source)


class WorkableTests(unittest.TestCase):
    def test_all_jobs_widget_has_stable_safe_urls_and_published_dates(self):
        from monitor.employer_feeds import fetch_workable

        source = {"id": "optics", "name": "Optics", "board": "optics"}
        job = {
            "shortcode": "ABC123",
            "title": "Optical Engineer Intern",
            "published_on": "2026-10-01",
            "url": "https://evil.example/",
            "city": "Boston",
        }
        with patch(
            "monitor.employer_feeds._request",
            return_value=json.dumps({"jobs": [job]}).encode(),
        ):
            item = fetch_workable(source).opportunities[0]
        self.assertEqual(item.url, "https://apply.workable.com/j/ABC123")
        self.assertEqual(item.posted_at, "2026-10-01")
        self.assertEqual(item.opportunity_type, "internship")
        for data in [{}, {"jobs": [job, job]}]:
            with (
                self.subTest(data=data),
                patch(
                    "monitor.employer_feeds._request",
                    return_value=json.dumps(data).encode(),
                ),
            ):
                with self.assertRaises(ValueError):
                    fetch_workable(source)
