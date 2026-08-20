import io
import json
import os
import re
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from monitor import cli, config
from monitor.database import Database
from monitor.models import Opportunity


ROOT = Path(__file__).resolve().parents[1]


def profile_payload(
    *,
    timeframes,
    packs,
    candidate,
    targets,
    organizations,
    rules,
    default_document,
    routes,
):
    return {
        "version": 1,
        "expected_revision": "",
        "timeframes": timeframes,
        "selected_packs": packs,
        "candidate": candidate,
        "targets": targets,
        "priority_organizations": organizations,
        "matching": {
            "engine": "structured_v2",
            "base_score": 36,
            "priority_organization_bonus": 9,
            "minimum_display_score": 25,
            "tier_thresholds": {"priority": 78, "strong": 60, "watch": 25},
            "anchor_min_strength": 0.7,
            "target_type_bonus": 10,
            "target_timeframe_bonus": 6,
            "field_weights": {
                "title": 1.0,
                "organization": 0.9,
                "opportunity_type": 0.9,
                "category": 0.75,
                "location": 0.7,
                "eligibility": 0.6,
                "description": 0.25,
            },
            "score_ceilings": {
                "no_anchor": 49,
                "description_only": 49,
                "description_exclusion": 49,
                "unknown_eligibility": 79,
            },
            "rules": rules,
        },
        "documents": {"default": default_document, "routes": routes},
    }


ROBOTICS_STUDENT = profile_payload(
    timeframes=["Summer 2028"],
    packs=[
        "engineering",
        "students-early-career",
        "space-aerospace",
        "robotics-autonomy",
    ],
    candidate={
        "current_stage": "undergraduate",
        "expected_graduation": "May 2029",
        "completed_degrees": [],
        "skills": ["embedded C", "ROS 2", "CAD", "control systems"],
        "max_required_experience_years": 2,
    },
    targets={
        "cycles": [{"label": "Summer 2028", "season": "summer", "year": 2028}],
        "opportunity_types": ["internship", "co_op", "research_program"],
        "role_families": ["robotics", "avionics", "embedded systems", "controls"],
        "domains": ["robotics", "aerospace", "autonomy"],
        "supporting_skills": ["Python", "C++", "electronics"],
        "locations": ["Pittsburgh", "Boston", "Los Angeles"],
        "exclusions": ["senior", "principal", "commission only"],
        "work_arrangements": ["onsite", "hybrid"],
        "remote_preference": "onsite_preferred",
        "strict_opportunity_types": True,
        "strict_timeframes": False,
    },
    organizations=["Field AI", "Astranis", "Diligent Robotics"],
    rules=[
        {
            "id": "robotics_core",
            "label": "Robotics and embedded systems",
            "weight": 28,
            "fields": ["title", "category", "description"],
            "terms": ["robotics", "autonomy", "embedded", "controls", "avionics"],
            "match": "any",
            "per_term": True,
            "max_hits": 3,
            "dimension": "interest",
            "anchor": True,
            "hard_gate": False,
        },
        {
            "id": "student_program",
            "label": "Student opportunity",
            "weight": 18,
            "fields": ["title", "opportunity_type"],
            "terms": ["intern", "co-op", "student"],
            "match": "any",
            "per_term": False,
            "dimension": "target",
            "anchor": False,
            "hard_gate": False,
        },
    ],
    default_document="Student engineering resume",
    routes=[
        {
            "label": "Robotics portfolio",
            "terms": ["robotics", "autonomy", "ROS"],
            "fields": ["title", "description", "category"],
        },
        {
            "label": "Hardware resume",
            "terms": ["avionics", "embedded", "electrical"],
            "fields": ["title", "description", "category"],
        },
    ],
)


SOCIAL_IMPACT_LEADER = profile_payload(
    timeframes=["Fall 2027", "Anytime"],
    packs=[
        "public-interest",
        "fellowships",
        "product-design",
        "education-social-impact",
    ],
    candidate={
        "current_stage": "experienced career changer",
        "expected_graduation": "",
        "completed_degrees": ["B.A. Sociology", "M.P.H."],
        "skills": [
            "community partnerships",
            "program evaluation",
            "qualitative research",
            "service design",
        ],
        "max_required_experience_years": 20,
    },
    targets={
        "cycles": [
            {"label": "Fall 2027", "season": "fall", "year": 2027},
            {"label": "Anytime", "season": "anytime"},
        ],
        "opportunity_types": ["job", "fellowship", "returnship"],
        "role_families": ["program leadership", "policy", "service design"],
        "domains": ["public health", "civil rights", "education", "social impact"],
        "supporting_skills": ["facilitation", "research", "strategy"],
        "locations": ["Remote", "Chicago", "Washington DC"],
        "exclusions": ["unpaid", "commission only", "partisan campaign"],
        "work_arrangements": ["remote", "hybrid", "part-time"],
        "remote_preference": "remote_preferred",
        "strict_opportunity_types": False,
        "strict_timeframes": False,
    },
    organizations=["Vera Institute of Justice", "Nava PBC", "ACLU"],
    rules=[
        {
            "id": "community_impact",
            "label": "Community and public impact",
            "weight": 27,
            "fields": ["title", "category", "description"],
            "terms": ["community", "public health", "civil rights", "social impact"],
            "match": "any",
            "per_term": True,
            "max_hits": 3,
            "dimension": "interest",
            "anchor": True,
            "hard_gate": False,
        },
        {
            "id": "leadership_and_design",
            "label": "Program leadership and service design",
            "weight": 19,
            "fields": ["title", "category", "description"],
            "terms": ["program director", "service design", "policy", "evaluation"],
            "match": "any",
            "per_term": True,
            "max_hits": 2,
            "dimension": "target",
            "anchor": True,
            "hard_gate": False,
        },
    ],
    default_document="Social-impact leadership resume",
    routes=[
        {
            "label": "Policy and evaluation CV",
            "terms": ["policy", "evaluation", "research"],
            "fields": ["title", "description", "category"],
        },
        {
            "label": "Service-design portfolio",
            "terms": ["service design", "user research", "community"],
            "fields": ["title", "description", "category"],
        },
    ],
)


SAMPLE_OPPORTUNITIES = (
    Opportunity(
        source_id="field_ai_lever",
        external_id="robotics-intern",
        title="Robotics Controls Intern",
        organization="Field AI",
        url="https://example.com/field-ai/robotics-intern",
        location="Los Angeles, CA",
        description="Summer student role building ROS 2 autonomy and embedded controls.",
        category="Robotics Engineering",
        opportunity_type="internship",
        posted_at="2026-08-01T12:00:00+00:00",
        deadline_at="2028-02-15",
        commitment="Internship",
        eligibility="Currently enrolled undergraduate students may apply.",
    ),
    Opportunity(
        source_id="astranis_greenhouse",
        external_id="avionics-coop",
        title="Avionics Embedded Systems Co-op",
        organization="Astranis",
        url="https://example.com/astranis/avionics-coop",
        location="San Francisco, CA",
        description="Student hardware role in electrical design, C++, and spacecraft controls.",
        category="Space Systems Engineering",
        opportunity_type="co_op",
        posted_at="2026-08-02T12:00:00+00:00",
        commitment="Co-op",
    ),
    Opportunity(
        source_id="vera_institute_greenhouse",
        external_id="community-director",
        title="Director of Community Program Evaluation",
        organization="Vera Institute of Justice",
        url="https://example.com/vera/community-director",
        location="Chicago, IL or Remote",
        description="Lead civil-rights partnerships, qualitative research, policy, and program evaluation.",
        category="Public Interest and Social Impact",
        opportunity_type="job",
        posted_at="2026-08-03T12:00:00+00:00",
        commitment="Full-time",
        eligibility="Experienced program leaders are encouraged to apply.",
    ),
    Opportunity(
        source_id="aclu_greenhouse",
        external_id="policy-fellow",
        title="Public Health Policy Fellow",
        organization="ACLU",
        url="https://example.com/aclu/policy-fellow",
        location="Washington, DC or Remote",
        description="A paid fellowship in civil rights policy, community research, and service design.",
        category="Policy and Community Impact",
        opportunity_type="fellowship",
        posted_at="2026-08-04T12:00:00+00:00",
        deadline_at="2027-05-01",
        commitment="Fellowship",
    ),
)


class PersonaEndToEndTests(unittest.TestCase):
    def _sandbox(self, destination):
        (destination / "config").mkdir(parents=True)
        (destination / "dashboard").mkdir()
        (destination / "data").mkdir()
        shutil.copy2(ROOT / "config" / "profile.json", destination / "config" / "profile.json")
        shutil.copy2(ROOT / "config" / "sources.json", destination / "config" / "sources.json")
        for name in ("template.html", "styles.css", "app.js"):
            shutil.copy2(ROOT / "dashboard" / name, destination / "dashboard" / name)

    def _seed(self, destination):
        catalog = json.loads((destination / "config" / "sources.json").read_text(encoding="utf-8"))
        database = Database(destination / "data" / "opportunities.sqlite3")
        try:
            database.initialize()
            for source in catalog["sources"]:
                database.sync_source(source)
            for opportunity in SAMPLE_OPPORTUNITIES:
                database.upsert_opportunity(
                    opportunity,
                    seen_at="2026-08-11T12:00:00+00:00",
                )
        finally:
            database.close()

    def _run_persona(self, payload, expected_external_id, expected_document, query):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            self._sandbox(root)
            self._seed(root)
            editor_path = root / "profile-editor.json"
            editor_path.write_text(json.dumps(payload), encoding="utf-8")
            environment = {
                "OPPORTUNITY_RADAR_PROFILE": "",
                "OPPORTUNITY_MONITOR_PROFILE": "",
                "OPPORTUNITY_RADAR_SOURCES": "",
                "OPPORTUNITY_MONITOR_SOURCES": "",
                "OPPORTUNITY_RADAR_CURATED_PATH": "",
                "OPPORTUNITY_MONITOR_CURATED_PATH": "",
            }
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                patch.object(config, "PROJECT_ROOT", root),
                patch(
                    "monitor.profile._lifecycle_lock_path",
                    return_value=root / ".profile-lifecycle-lock",
                ),
                patch.dict(os.environ, environment, clear=False),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                result = cli.main(
                    ["profile", "apply", "--file", str(editor_path), "--quiet"]
                )
                self.assertEqual(result, 0, stderr.getvalue())
                self.assertEqual(cli.main(["dashboard", "--quiet"]), 0, stderr.getvalue())

                database = Database(root / "data" / "opportunities.sqlite3")
                try:
                    row = database.connection.execute(
                        "SELECT id, score, tier, recommended_resume FROM opportunities "
                        "WHERE external_id=?",
                        (expected_external_id,),
                    ).fetchone()
                finally:
                    database.close()
                self.assertIsNotNone(row)
                self.assertGreaterEqual(row["score"], 60)
                self.assertIn(row["tier"], {"strong", "priority"})
                self.assertEqual(row["recommended_resume"], expected_document)

                stdout.seek(0)
                stdout.truncate(0)
                self.assertEqual(
                    cli.main(["opportunities", "search", query, "--json"]),
                    0,
                    stderr.getvalue(),
                )
                search_results = json.loads(stdout.getvalue())
                self.assertTrue(
                    any(item["id"] == row["id"] for item in search_results),
                    search_results,
                )

                self.assertEqual(
                    cli.main(["status", row["id"], "apply", "--quiet"]),
                    0,
                    stderr.getvalue(),
                )
                self.assertEqual(
                    cli.main(["bookmark", row["id"], "true", "--quiet"]),
                    0,
                    stderr.getvalue(),
                )

            local_profile = json.loads(
                (root / "config" / "profile.local.json").read_text(encoding="utf-8")
            )
            local_sources = json.loads(
                (root / "config" / "sources.local.json").read_text(encoding="utf-8")
            )
            self.assertEqual(local_profile["candidate"]["current_stage"], payload["candidate"]["current_stage"])
            self.assertEqual(local_sources["selected_packs"], payload["selected_packs"])
            self.assertEqual(os.stat(root / "config" / "profile.local.json").st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(root / "config" / "sources.local.json").st_mode & 0o777, 0o600)

            rendered = (root / "dashboard" / "index.html").read_text(encoding="utf-8")
            match = re.search(
                r'<script id="opportunity-data"[^>]*>(.*?)</script>',
                rendered,
                flags=re.DOTALL,
            )
            self.assertIsNotNone(match)
            dashboard_data = json.loads(match.group(1))
            self.assertEqual(
                dashboard_data["settings"]["profile_editor"]["candidate"]["current_stage"],
                payload["candidate"]["current_stage"],
            )
            tracked = next(item for item in dashboard_data["opportunities"] if item["id"] == row["id"])
            self.assertEqual(tracked["status"], "apply")
            self.assertTrue(tracked["bookmarked"])
            self.assertEqual(tracked["recommended_resume"], expected_document)

    def test_undergraduate_robotics_and_aerospace_profile(self):
        self._run_persona(
            ROBOTICS_STUDENT,
            expected_external_id="robotics-intern",
            expected_document="Robotics portfolio",
            query="robotics controls",
        )

    def test_experienced_social_impact_career_changer_profile(self):
        self._run_persona(
            SOCIAL_IMPACT_LEADER,
            expected_external_id="community-director",
            expected_document="Policy and evaluation CV",
            query="community evaluation",
        )


if __name__ == "__main__":
    unittest.main()
