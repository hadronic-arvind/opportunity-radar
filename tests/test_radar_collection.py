"""Offline contract and subprocess failure checks for public collection."""

import io
import json
import os
from contextlib import redirect_stdout
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Event
import time
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from radar import collection


def catalog_source(source_id):
    return next(
        source for source in collection.load_catalog() if source["id"] == source_id
    )


def perform(source):
    records = []
    state, message = collection._perform(
        source,
        time.monotonic() + 2,
        lambda encoded: records.append(json.loads(encoded)),
    )
    return records, state, message


class CollectionContractTests(unittest.TestCase):
    def test_catalog_is_opt_in_and_copies_are_independent(self):
        sources = collection.load_catalog()
        self.assertTrue(sources)
        self.assertTrue(all(not source["enabled"] for source in sources))
        self.assertEqual(len({source["id"] for source in sources}), len(sources))
        nrc = next(
            source for source in sources if source["id"] == "nrc_canada_postdoctoral"
        )
        nrc["include"].append("unexpected")
        self.assertNotIn("unexpected", catalog_source(nrc["id"])["include"])

    def test_rejects_unsafe_source_urls_and_catalog_impersonation(self):
        source = {"id": "test", "name": "Example", "kind": "syndication"}
        unsafe = [
            "http://example.org/feed",
            "https://localhost/feed",
            "https://printer/feed",
            "https://127.0.0.1/feed",
            "https://127.1/feed",
            "https://0x7f.0x1/feed",
            "https://[::1]/feed",
            "https://224.0.0.1/feed",
            "https://example.org:8443/feed",
            "https://example.org:0/feed",
            "https://user:password@example.org/feed",
            "https://example.org/feed?access_token=secret",
            "https://example.org/feed?api%5Fkey=secret",
            "https://example.org/feed#private",
            "https://example.org/feed\n",
        ]
        for url in unsafe:
            with self.subTest(url=url), self.assertRaises(ValueError):
                collection.validate_source({**source, "url": url})
        curated = catalog_source("grants_gov_individuals")
        with self.assertRaises(ValueError):
            collection.validate_source(
                {**curated, "url": "https://example.org/not-a-grant-source"}
            )

    def test_legacy_lever_board_region_and_request_do_not_carry_private_fields(self):
        source = collection.validate_source(
            {
                "id": "sample_lever",
                "name": "Example",
                "kind": "lever",
                "url": "https://jobs.eu.lever.co/example",
                "site": "example",
                "api_region": "eu",
                "api_key": "private",
                "personal_query": "private",
            }
        )
        with patch("monitor.fetchers._request", return_value=b"[]") as request:
            rows, state, _message = perform(source)
        self.assertEqual((rows, state), ([], "complete"))
        self.assertNotIn("api_key", source)
        self.assertNotIn("personal_query", source)
        request.assert_called_once_with(
            "https://api.eu.lever.co/v0/postings/example?mode=json",
            max_bytes=collection.fetchers.MAX_LISTING_RESPONSE_BYTES,
        )

    def test_saved_page_survives_rate_limit_and_keeps_page_provenance(self):
        source = catalog_source("euraxess")
        page = b'<a href="/jobs/123?page=0">Research Fellow in Coastal Modelling</a>'
        blocked = HTTPError(source["url"], 429, "contains private diagnostic", {}, None)
        with patch("monitor.fetchers._request", side_effect=[page, blocked]) as request:
            rows, state, message = perform(source)
        self.assertEqual(state, "partial")
        self.assertIn("HTTP 429", message)
        self.assertNotIn("private diagnostic", message)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["url"], "https://euraxess.ec.europa.eu/jobs/123")
        self.assertEqual(rows[0]["evidence"]["format"], "directory_link")
        self.assertEqual(rows[0]["evidence"]["source_url"], source["url"])
        self.assertFalse(rows[0]["authority"])
        self.assertEqual(rows[0]["requirements"], "")
        self.assertEqual(rows[0]["organization"], "")

    def test_repeated_page_keeps_deduplicated_rows(self):
        source = catalog_source("euraxess")
        page = b'<a href="/jobs/123">Research Fellow in Coastal Modelling</a>'
        with patch("monitor.fetchers._request", return_value=page) as request:
            rows, state, message = perform(source)
        self.assertEqual((len(rows), state), (1, "partial"))
        self.assertIn("repeated", message)
        self.assertEqual(request.call_count, 2)

    def test_malformed_empty_oversized_and_xml_entities_are_distinct(self):
        source = catalog_source("kent_university_careers")
        with patch("monitor.fetchers._request", return_value=b"<rss>"):
            self.assertEqual(perform(source)[1], "failed")
        with patch("monitor.fetchers._request", return_value=b"<rss><channel /></rss>"):
            self.assertEqual(perform(source)[:2], ([], "partial"))
        with patch(
            "monitor.fetchers._request",
            side_effect=collection.fetchers.ResponseTooLargeError(
                "unsafe publisher content"
            ),
        ):
            self.assertIn("size", perform(source)[2])
        payload = b'<!DOCTYPE rss [<!ENTITY danger "x">]><rss><channel>&danger;</channel></rss>'
        with patch("monitor.fetchers._request", return_value=payload):
            self.assertEqual(perform(source)[1], "failed")

    def test_public_auto_accepts_structured_listing_and_rejects_generic_links(self):
        source = collection.validate_source(
            {
                "id": "my_public_page",
                "name": "Example School",
                "kind": "public_page",
                "url": "https://example.org/opportunities",
                "authority": True,
            }
        )
        payload = b"""<script type="application/ld+json">
        {"@type":"EducationalOccupationalProgram","name":"Machining programme",
         "url":"/programme","programPrerequisites":"Trade certificate required"}
        </script>"""
        with patch("monitor.fetchers._request", return_value=payload):
            rows, state, _message = perform(source)
        self.assertEqual((len(rows), state), (1, "partial"))
        self.assertEqual(rows[0]["requirements"], "Trade certificate required")
        self.assertEqual(rows[0]["kinds"], ["training"])
        self.assertFalse(rows[0]["authority"])
        with patch(
            "monitor.fetchers._request", return_value=b'<a href="/news">Our news</a>'
        ):
            self.assertEqual(perform(source)[:2], ([], "failed"))

    def test_unsafe_listing_url_does_not_discard_valid_neighbors(self):
        source = catalog_source("kent_university_careers")
        payload = b"""<rss><channel>
        <item><title>Analyst</title><link>https://example.org/1</link></item>
        <item><title>Hostile link</title><link>https://127.1/private</link></item>
        </channel></rss>"""
        with patch("monitor.fetchers._request", return_value=payload):
            rows, state, _message = perform(source)
        self.assertEqual(state, "partial")
        self.assertEqual([row["title"] for row in rows], ["Analyst"])

    def test_response_observation_limit_is_partial(self):
        source = catalog_source("kent_university_careers")
        payload = b"""<rss><channel>
        <item><title>Analyst</title><link>https://example.org/1</link></item>
        <item><title>Designer</title><link>https://example.org/2</link></item>
        </channel></rss>"""
        with (
            patch("monitor.fetchers._request", return_value=payload),
            patch.object(collection, "MAX_ROWS", 1),
        ):
            rows, state, message = perform(source)
        self.assertEqual((len(rows), state), (1, "partial"))
        self.assertIn("limit", message)


@unittest.skipUnless(
    collection.sys.platform in {"darwin", "linux"}, "Unix public collector"
)
class CollectorProcessTests(unittest.TestCase):
    def setUp(self):
        self.source = catalog_source("figma_greenhouse")

    def test_timeout_keeps_complete_protocol_lines_and_reaps_worker(self):
        code = (
            "import time; "
            'print(\'{"external_id":"seen"}\', flush=True); '
            "print('{\"external_id\":', end='', flush=True); "
            "time.sleep(30)"
        )
        with (
            patch.object(collection, "WORKER_CODE", code),
            patch.object(
                collection.subprocess, "Popen", wraps=subprocess.Popen
            ) as popen,
        ):
            started = time.monotonic()
            rows, state, _message = collection.collect(self.source, 0.4)
            elapsed = time.monotonic() - started
        self.assertEqual((rows, state), ([{"external_id": "seen"}], "timed_out"))
        self.assertLess(elapsed, 2)
        self.assertEqual(popen.call_count, 1)

    def test_cancel_reaps_started_worker_and_does_not_inherit_secrets(self):
        spawned = []
        popen = subprocess.Popen

        def start_worker(*args, **kwargs):
            process = popen(*args, **kwargs)
            spawned.append(process)
            return process

        with TemporaryDirectory() as temp:
            marker = Path(temp) / "ready"
            code = (
                "import os, pathlib, time; "
                "assert 'RADAR_TEST_PRIVATE' not in os.environ; "
                'print(\'{"external_id":"seen"}\', flush=True); '
                f"pathlib.Path({str(marker)!r}).touch(); "
                "time.sleep(30)"
            )
            with (
                patch.dict(os.environ, {"RADAR_TEST_PRIVATE": "private"}),
                patch.object(collection, "WORKER_CODE", code),
                patch.object(collection.subprocess, "Popen", side_effect=start_worker),
            ):

                class Cancellation:
                    def is_set(self):
                        return marker.exists()

                rows, state, _message = collection.collect(
                    self.source, 3, Cancellation()
                )
        self.assertEqual((rows, state), ([{"external_id": "seen"}], "cancelled"))
        self.assertIsNotNone(spawned[0].poll())
        self.assertLess(spawned[0].returncode, 0)

    def test_collector_stops_if_parent_is_forcibly_killed(self):
        import fcntl

        with TemporaryDirectory() as temp:
            marker = Path(temp) / "collector-pid"
            lock = Path(temp) / "collector-lock"
            setup_worker = (
                "import fcntl, os, pathlib, sys, time; "
                "sys.path.insert(0, sys.argv[1]); "
                "from radar import collection; "
                f"marker = pathlib.Path({str(marker)!r}); "
                f"lock = open({str(lock)!r}, 'w'); "
                "fcntl.flock(lock, fcntl.LOCK_EX); "
                "collection.fetchers._request = lambda *a, **k: "
                "(marker.write_text(str(os.getpid())), time.sleep(30)); "
                "collection._worker(int(sys.argv[2]))"
            )
            setup_parent = (
                "from radar import collection; "
                f"collection.WORKER_CODE = {setup_worker!r}; "
                f"collection.collect({self.source!r}, 30)"
            )
            parent = subprocess.Popen(
                [sys.executable, "-c", setup_parent],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                deadline = time.monotonic() + 5
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(marker.exists(), "Collector never entered its request")
                parent.kill()
                parent.wait(timeout=3)
                deadline = time.monotonic() + 2
                with lock.open("r+") as probe:
                    while time.monotonic() < deadline:
                        try:
                            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        except BlockingIOError:
                            time.sleep(0.02)
                        else:
                            break
                    else:
                        try:
                            os.kill(int(marker.read_text()), 9)
                        except ProcessLookupError:
                            pass
                        self.fail("Collector remained active after its parent died")
            finally:
                if parent.poll() is None:
                    parent.kill()
                    parent.wait(timeout=3)

    def test_cli_cancellation_receipt_wins_worker_exit_race(self):
        from radar.__main__ import main
        from radar.store import Store

        event = Event()
        worker = Mock()
        worker.poll.return_value = 0

        def interrupt_and_return(*args, **kwargs):
            event.set()
            return b'{"external_id":"seen","title":"Observed listing"}\n', None

        worker.communicate.side_effect = interrupt_and_return
        with TemporaryDirectory() as temp:
            workspace = Path(temp) / "workspace.sqlite3"
            Store(workspace).add_source({**self.source, "enabled": True})
            output = io.StringIO()
            with (
                patch("radar.__main__.Event", return_value=event),
                patch.object(collection.subprocess, "Popen", return_value=worker),
                redirect_stdout(output),
            ):
                code = main(["--workspace", str(workspace), "--json", "collect"])
        receipt = json.loads(output.getvalue())["receipts"][0]
        self.assertEqual(code, 130)
        self.assertEqual((receipt["state"], receipt["seen"]), ("cancelled", 1))

    def test_cancel_before_launch_and_invalid_budgets(self):
        with patch.object(collection.subprocess, "Popen") as popen:

            class AlreadyCancelled:
                def is_set(self):
                    return True

            result = collection.collect(self.source, 1, AlreadyCancelled())
        self.assertEqual(result[:2], ([], "cancelled"))
        popen.assert_not_called()
        for seconds in (0, 121, float("nan"), float("inf"), True, "2"):
            with self.subTest(seconds=seconds), self.assertRaises(ValueError):
                collection.collect(self.source, seconds)
