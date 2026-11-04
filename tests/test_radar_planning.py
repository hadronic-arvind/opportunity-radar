"""Application planning and calendar export through the desktop bridge."""

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from radar import app
from radar.store import Store

PROJECT = Path(__file__).resolve().parents[1]


class PlanningTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="radar-planning-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.store = Store(self.root / "workspace.sqlite3")
        self.tomorrow = (date.today() + timedelta(days=1)).isoformat()
        self.next_week = (date.today() + timedelta(days=7)).isoformat()
        self.yesterday = (date.today() - timedelta(days=1)).isoformat()

    def bridge(self, operation, *, ok=True, **args):
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "radar.desktop_bridge",
                "--workspace",
                str(self.store.path),
            ],
            input=json.dumps({"op": operation, "args": args}) + "\n",
            text=True,
            capture_output=True,
            check=True,
            timeout=10,
            cwd=PROJECT,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        response = json.loads(completed.stdout)
        self.assertEqual(response["ok"], ok, response)
        return response["result" if ok else "error"]

    def add_listing(self, title, deadline="", source="manual", **extra):
        app.import_rows(
            self.store,
            [{"external_id": title, "title": title, "deadline_at": deadline, **extra}],
            source,
        )
        return next(
            item["id"] for item in self.store.search() if item["title"] == title
        )

    def test_plans_are_private_optimistic_edits_and_a_separate_tracking_signal(self):
        listing = self.add_listing("Water Quality Internship")
        initial = self.bridge("application_plan", id=listing)
        self.assertEqual(initial["checklist"], [])
        self.assertIsNone(initial["revision"])
        fields = {
            "next_action": "Ask advisor about reference",
            "follow_up": self.tomorrow,
            "checklist": [
                {"label": "CV", "done": True},
                {"label": "Transcript", "done": False},
            ],
        }
        first = self.bridge(
            "save_application_plan", id=listing, expected_revision=None, **fields
        )
        self.assertEqual(self.bridge("application_plan", id=listing), first)
        self.assertEqual(
            self.bridge(
                "save_application_plan",
                id=listing,
                expected_revision=first["revision"],
                **fields,
            ),
            first,
        )
        stale = self.bridge(
            "save_application_plan",
            ok=False,
            id=listing,
            expected_revision=None,
            **fields,
        )
        self.assertIn("This plan changed", stale)
        invalid_date = self.bridge(
            "save_application_plan",
            ok=False,
            id=listing,
            **{**fields, "follow_up": "2026-02-30"},
        )
        self.assertIn("valid calendar date", invalid_date)
        duplicate = self.bridge(
            "save_application_plan",
            ok=False,
            id=listing,
            **{
                **fields,
                "checklist": [
                    {"label": " CV ", "done": False},
                    {"label": "cv", "done": True},
                ],
            },
        )
        self.assertIn("unique", duplicate)
        self.assertEqual(self.bridge("application_plan", id=listing), first)
        agenda = self.bridge("application_agenda")
        self.assertEqual(agenda["items"][0]["materials_done"], 1)
        self.assertEqual(agenda["items"][0]["materials_total"], 2)
        self.assertEqual(agenda["items"][0]["next_action"], fields["next_action"])
        self.assertEqual(self.store.inspect(listing)["status"], "new")
        self.assertFalse(self.store.inspect(listing)["saved"])
        cleared = self.bridge(
            "save_application_plan",
            id=listing,
            expected_revision=first["revision"],
            next_action="",
            follow_up=None,
            checklist=[],
        )
        self.assertNotEqual(cleared["revision"], first["revision"])
        self.assertEqual(self.bridge("application_agenda")["items"], [])

    def test_agenda_orders_due_dates_and_marks_uncertain_publisher_data(self):
        follow_up = self.add_listing("Advisor follow-up")
        deadline = self.add_listing("Laboratory placement", self.tomorrow)
        expired = self.add_listing("Closed grant", self.yesterday)
        missing_zone = self.add_listing(
            "Local time fellowship", self.next_week + "T17:00"
        )
        conflict = self.add_listing(
            "Conflicting dates",
            self.tomorrow,
            url="https://example.com/jobs/156",
            cycle="cycle",
        )
        self.assertEqual(
            self.add_listing(
                "Conflicting dates",
                self.next_week,
                "second",
                url="https://example.com/jobs/156",
                cycle="cycle",
            ),
            conflict,
        )
        self.bridge(
            "save_application_plan",
            id=follow_up,
            next_action="Send update",
            follow_up=self.yesterday,
            checklist=[],
        )
        for id in (deadline, expired, missing_zone, conflict):
            self.store.track(id, saved=True)
        self.store.track(
            self.add_listing("Hidden application", self.tomorrow),
            saved=True,
            status="skip",
        )
        self.add_listing("Untracked opportunity", self.tomorrow)

        page = self.bridge("application_agenda", limit=2)
        self.assertEqual([row["id"] for row in page["items"]], [follow_up, deadline])
        self.assertEqual(page["next_offset"], 2)
        self.assertEqual(page["calendar_events"], 1)
        second = self.bridge("application_agenda", offset=2)
        by_id = {row["id"]: row["deadline"] for row in second["items"]}
        self.assertEqual(set(by_id), {expired, missing_zone, conflict})
        self.assertEqual(by_id[expired]["state"], "passed")
        self.assertEqual(by_id[missing_zone]["state"], "verify")
        self.assertEqual(by_id[conflict]["state"], "verify")
        self.assertEqual(
            set(by_id[conflict]["candidates"]), {self.tomorrow, self.next_week}
        )

    def test_calendar_exports_only_upcoming_exportable_dates_and_no_private_notes(self):
        title = "Bourses " + "研究" * 37 + ", dossier; été\\mars\nATTENDEE:untrusted"
        opportunity = self.add_listing(title, self.next_week + "T17:00:00-05:00")
        another = self.add_listing("Already applied", self.tomorrow)
        self.store.track(opportunity, saved=True, note="Private note stays here")
        self.store.track(another, status="applied")
        self.bridge(
            "save_application_plan",
            id=another,
            next_action="Private action stays here",
            follow_up=self.tomorrow,
            checklist=[{"label": "Private material stays here", "done": False}],
        )
        path = self.root / "calendar.ics"
        result = self.bridge("application_calendar", path=str(path))
        self.assertEqual(result, {"event_count": 2, "path": str(path)})
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        raw = path.read_bytes()
        self.assertTrue(all(len(line) <= 75 for line in raw.split(b"\r\n")))
        self.assertTrue(raw.endswith(b"END:VCALENDAR\r\n"))
        unfolded = raw.decode("utf-8").replace("\r\n ", "")
        self.assertIn(
            "DTSTART:" + self.next_week.replace("-", "") + "T220000Z", unfolded
        )
        self.assertIn("DTSTART;VALUE=DATE:" + self.tomorrow.replace("-", ""), unfolded)
        self.assertIn("dossier\\; été\\\\mars\\nATTENDEE:untrusted", unfolded)
        self.assertNotIn("\r\nATTENDEE", unfolded)
        self.assertNotIn("Private note", unfolded)
        self.assertNotIn("Private action", unfolded)
        self.assertNotIn("Private material", unfolded)
        self.assertNotIn("Deadline: Already applied", unfolded)
        raw_again = self.bridge("application_calendar")["text"].replace("\r\n ", "")
        self.assertEqual(
            [line for line in unfolded.splitlines() if line.startswith("UID:")],
            [line for line in raw_again.splitlines() if line.startswith("UID:")],
        )
        self.assertIn(
            "already", self.bridge("application_calendar", ok=False, path=str(path))
        )
        self.assertEqual(path.read_bytes(), raw)

    def test_agenda_retains_collector_time_and_rolling_evidence(self):
        self.store.add_source(
            {"id": "publisher", "name": "Publisher", "kind": "manual", "url": ""}
        )
        exact = self.next_week + "T17:30:00+02:00"
        self.store.ingest(
            "publisher",
            [
                {
                    "external_id": "timed",
                    "title": "Timed application",
                    "deadline_at": self.next_week,
                    "evidence": {
                        "dates": {"deadline": {"state": "date", "evidence": exact}}
                    },
                },
                {
                    "external_id": "rolling",
                    "title": "Rolling application",
                    "evidence": {"dates": {"deadline": {"state": "rolling"}}},
                },
            ],
            "imported",
            "Fixture",
        )
        for row in self.store.search():
            self.store.track(row["id"], saved=True)
        items = self.bridge("application_agenda")["items"]
        self.assertEqual(
            items[0]["deadline"]["timestamp"], self.next_week + "T15:30:00+00:00"
        )
        self.assertEqual(items[0]["deadline"]["candidates"], [exact])
        self.assertEqual(items[1]["deadline"]["state"], "rolling")
        self.assertEqual(self.bridge("application_calendar")["event_count"], 1)


if __name__ == "__main__":
    unittest.main()
