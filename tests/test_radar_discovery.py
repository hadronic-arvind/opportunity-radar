"""Exercise discovery workflows through the desktop and terminal contracts."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]


class DiscoveryJourneyTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "workspace.sqlite3"

    def run_entry(self, module, *args, request=None):
        process = subprocess.run(
            [sys.executable, "-B", "-m", module, "--workspace", str(self.path), *args],
            cwd=PROJECT, input=request, capture_output=True, text=True, timeout=15,
        )
        return process

    def desktop(self, op, *, ok=True, **args):
        process = self.run_entry(
            "radar.desktop_bridge", request=json.dumps({"op": op, "args": args}) + "\n"
        )
        payload = json.loads(process.stdout)
        self.assertEqual(payload["ok"], ok, payload)
        return payload["result" if ok else "error"]

    def terminal(self, *args):
        process = self.run_entry("radar", "--json", *args)
        self.assertEqual(process.returncode, 0, process.stderr)
        return json.loads(process.stdout)

    def capture(self, title, location="Baltimore, MD", **fields):
        return self.desktop("capture_opportunity", title=title, kind="internship", location=location, **fields)

    def test_saved_search_remembers_filters_and_review_does_not_acknowledge_later_arrival(self):
        self.capture("Detector research original")
        search = self.desktop("save_saved_search", name="Maryland work", query="detector research",
            kind="internship", location={"places": ["Maryland, United States"]})
        self.assertEqual(search["criteria"]["location"]["places"], ["region:US:MD"])
        self.assertEqual(self.terminal("saved-searches"), [search])
        self.assertEqual(
            self.terminal("feed", "--saved-search", search["name"], "--new-only")["items"], []
        )
        first = self.capture("Detector research new")
        self.capture("Detector research outside", location="Chicago, IL")
        batch = self.terminal("feed", "--saved-search", search["name"], "--new-only")
        self.assertEqual([item["id"] for item in batch["items"]], [first["id"]])
        self.assertEqual(batch["items"][0]["location_match"]["state"], "match")
        late = self.capture("Detector research later")
        self.desktop("review_saved_search", name=search["name"], cursor=batch["review_cursor"],
            expected_cursor=search["reviewed_through"])
        remaining = self.terminal("feed", "--saved-search", search["name"], "--new-only")
        self.assertEqual([item["id"] for item in remaining["items"]], [late["id"]])
        self.assertIn("changed", self.desktop(
            "review_saved_search", ok=False, name=search["name"], cursor=batch["review_cursor"],
            expected_cursor=search["reviewed_through"],
        ))
        self.desktop("remove_saved_search", name=search["name"])
        self.assertEqual(self.desktop("list_saved_searches"), [])

    def test_capture_saves_once_and_preserves_tracking(self):
        capture = self.capture("Physics fellowship", url="https://publisher.example.org/jobs/482",
            description="A funded placement working on collider data.", deadline_at="2031-01-07")
        id = capture["id"]
        self.desktop("track", id=id, status="apply", note="Check thesis timing")
        self.assertEqual(
            self.capture("Physics fellowship", url="https://publisher.example.org/jobs/482"), capture
        )
        item = self.desktop("inspect", id=id)
        self.assertEqual((item["saved"], item["status"], item["note"]), (True, "apply", "Check thesis timing"))
        self.assertEqual(item["description"], "A funded placement working on collider data.")
        self.desktop("track", id=id, status="reviewed")
        self.desktop("hide", id=id)
        self.assertEqual(
            self.capture("Physics fellowship", url="https://publisher.example.org/jobs/482"), capture
        )
        self.assertEqual(self.desktop("inspect", id=id)["status"], "new")
        self.assertEqual(len(self.desktop("feed", view="saved")["items"]), 1)
        self.desktop("capture_opportunity", ok=False, title="Unsafe", url="http://127.0.0.1/")
        self.desktop("capture_opportunity", ok=False, title="Bad date", deadline_at="2031-02-29")
        self.assertEqual(self.desktop("feed", view="saved")["summary"]["total"], 1)
