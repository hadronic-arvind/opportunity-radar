import json
import unittest
from pathlib import Path

from monitor.models import Opportunity
from monitor.scoring import profile_fingerprint, score_opportunity


class ScoringTests(unittest.TestCase):
    def test_tracked_profile_uses_current_schema_without_legacy_fields(self):
        project = Path(__file__).resolve().parents[1]
        profile = json.loads(
            (project / "config" / "profile.json").read_text(encoding="utf-8")
        )
        item = Opportunity(
            "source",
            "neutral",
            "Example role",
            "Example organization",
            "https://example.com/role",
        )
        score_opportunity(item, profile)
        self.assertEqual(item.score, 50)
        self.assertEqual(item.tier, "watch")
        self.assertEqual(item.recommended_resume, "General")

    def test_high_fit_modular_role(self):
        item = Opportunity(
            "source", "one", "Distributed Systems Intern", "Example Systems", "https://example.com",
            description="Backend services with Python and C++ for reliable infrastructure",
        )
        profile = {
            "matching": {
                "base_score": 35,
                "priority_organization_bonus": 10,
                "tier_thresholds": {"priority": 75, "strong": 55, "watch": 25},
                "rules": [
                    {
                        "id": "preferred_domain",
                        "label": "Preferred domain",
                        "weight": 25,
                        "terms": ["distributed systems", "infrastructure"],
                        "match": "all",
                    },
                    {
                        "id": "preferred_tools",
                        "label": "Preferred tools",
                        "weight": 15,
                        "terms": ["Python", "C++"],
                        "match": "all",
                    },
                ],
            },
            "priority_organizations": ["Example Systems"],
            "documents": {
                "default": "General",
                "routes": [{"label": "Systems", "terms": ["distributed", "infrastructure"]}],
            },
        }
        score_opportunity(item, profile)
        self.assertGreaterEqual(item.score, 75)
        self.assertEqual(item.tier, "priority")
        self.assertEqual(item.recommended_resume, "Systems")

    def test_source_metadata_cannot_force_score_or_tier(self):
        item = Opportunity(
            "source", "two", "Graduate Research Program", "Lab", "https://example.com",
            metadata={"tier": "priority", "base_score": 99},
        )
        profile = {
            "matching": {
                "base_score": 30,
                "tier_thresholds": {"priority": 80, "strong": 65, "watch": 25},
                "rules": [],
            }
        }
        score_opportunity(item, profile)
        self.assertEqual(item.score, 30)
        self.assertEqual(item.tier, "watch")

    def test_exclusion_warning_reduces_score(self):
        item = Opportunity(
            "source", "three", "Software Intern", "Company", "https://example.com",
            description="This is an unpaid, commission-only role",
        )
        profile = {
            "matching": {
                "base_score": 25,
                "tier_thresholds": {"priority": 75, "strong": 55, "watch": 25},
                "rules": [
                    {
                        "id": "excluded_terms",
                        "label": "Excluded terms",
                        "weight": -35,
                        "terms": ["unpaid", "commission-only"],
                    }
                ],
            }
        }
        score_opportunity(item, profile)
        self.assertEqual(item.tier, "skip")
        self.assertTrue(item.warnings)

    def test_punctuation_terms_and_first_document_route_tie(self):
        item = Opportunity(
            "source",
            "four",
            "C++ and .NET R&D Engineer",
            "Company",
            "https://example.com",
            description="Uses C# in a high-performance research team",
        )
        profile = {
            "matching": {
                "base_score": 40,
                "tier_thresholds": {"priority": 80, "strong": 60, "watch": 20},
                "rules": [
                    {
                        "id": "punctuation",
                        "label": "Punctuation skills",
                        "weight": 5,
                        "per_term": True,
                        "terms": ["C++", "C#", ".NET", "R&D", "high-performance"],
                    }
                ],
            },
            "documents": {
                "default": "General",
                "routes": [
                    {"label": "First", "terms": ["engineer"]},
                    {"label": "Second", "terms": ["research"]},
                ],
            },
        }
        score_opportunity(item, profile)
        self.assertEqual(item.score, 65)
        self.assertEqual(item.recommended_resume, "First")
        self.assertEqual(item.metadata["match"]["components"][1]["points"], 25)

    def test_phrase_does_not_match_across_field_boundaries(self):
        item = Opportunity(
            "source",
            "boundary",
            "Alpha",
            "Beta",
            "https://example.com/boundary",
        )
        profile = {
            "matching": {
                "base_score": 20,
                "rules": [
                    {
                        "id": "boundary",
                        "label": "Boundary phrase",
                        "weight": 30,
                        "fields": ["title", "organization"],
                        "terms": ["alpha beta"],
                    }
                ],
            }
        }
        score_opportunity(item, profile)
        self.assertEqual(item.score, 20)
        self.assertEqual(item.reasons, [])

    def test_unlisted_or_metadata_fields_cannot_affect_matching(self):
        item = Opportunity(
            "hidden-term-source",
            "hidden-term-id",
            "Example role",
            "Example organization",
            "https://example.com/hidden-term",
            posted_at="hidden-term",
            deadline_at="hidden-term",
            commitment="hidden-term",
            metadata={"private_context": "hidden-term"},
        )
        profile = {
            "matching": {
                "base_score": 20,
                "rules": [
                    {
                        "id": "unsupported_fields",
                        "label": "Unsupported fields",
                        "weight": 50,
                        "fields": [
                            "source_id",
                            "external_id",
                            "posted_at",
                            "deadline_at",
                            "commitment",
                            "metadata",
                        ],
                        "terms": ["hidden-term"],
                    }
                ],
            }
        }
        score_opportunity(item, profile)
        self.assertEqual(item.score, 20)
        self.assertEqual(item.reasons, [])

    def structured_profile(self):
        return {
            "candidate": {"current_stage": "phd_student"},
            "targets": {
                "opportunity_types": ["internship"],
                "cycles": [{"season": "spring", "year": 2031}],
            },
            "matching": {
                "engine": "structured_v2",
                "base_score": 25,
                "minimum_display_score": 40,
                "tier_thresholds": {"priority": 80, "strong": 65, "watch": 40},
                "rules": [
                    {
                        "id": "ml_role",
                        "label": "Machine learning role",
                        "dimension": "interest",
                        "anchor": True,
                        "weight": 40,
                        "fields": ["title", "category", "description"],
                        "terms": ["machine learning"],
                    },
                    {
                        "id": "python_skill",
                        "label": "Python",
                        "dimension": "qualification",
                        "weight": 20,
                        "fields": ["title", "description"],
                        "terms": ["python"],
                    },
                ],
            },
        }

    def test_structured_engine_uses_field_hierarchy_and_anchor_ceiling(self):
        profile = self.structured_profile()
        title_match = Opportunity(
            "source",
            "structured-title",
            "Spring 2031 Machine Learning Intern",
            "Example",
            "https://example.com/title",
            description="Build research prototypes with Python.",
            opportunity_type="internship",
        )
        description_only = Opportunity(
            "source",
            "structured-description",
            "Spring 2031 Operations Intern",
            "Example",
            "https://example.com/description",
            description="Coordinate a team that happens to use machine learning and Python.",
            opportunity_type="internship",
        )

        score_opportunity(title_match, profile)
        score_opportunity(description_only, profile)

        self.assertGreater(title_match.score, description_only.score)
        self.assertEqual(description_only.score, 49)
        self.assertEqual(description_only.tier, "watch")
        self.assertTrue(title_match.metadata["match"]["visibility"]["anchor_matched"])
        self.assertFalse(description_only.metadata["match"]["visibility"]["anchor_matched"])
        self.assertEqual(title_match.metadata["match"]["engine"], "structured_v2")
        self.assertIn("interest", title_match.metadata["match"]["dimensions"])
        evidence = title_match.metadata["match"]["components"][1]["evidence"][0]
        self.assertEqual(evidence["field"], "title")
        self.assertEqual(evidence["strength"], 1.0)

    def test_structured_engine_hard_gates_type_and_timeframe(self):
        profile = self.structured_profile()
        wrong_type = Opportunity(
            "source",
            "wrong-type",
            "Spring 2031 Machine Learning Engineer",
            "Example",
            "https://example.com/type",
            opportunity_type="job",
        )
        wrong_year = Opportunity(
            "source",
            "wrong-year",
            "Spring 2030 Machine Learning Intern",
            "Example",
            "https://example.com/year",
            opportunity_type="internship",
        )

        score_opportunity(wrong_type, profile)
        score_opportunity(wrong_year, profile)

        for item, gate_id in ((wrong_type, "opportunity_type"), (wrong_year, "timeframe")):
            self.assertEqual(item.score, 0)
            self.assertEqual(item.tier, "skip")
            self.assertEqual(item.metadata["match"]["visibility"]["state"], "hidden")
            self.assertIn(
                gate_id,
                [gate["id"] for gate in item.metadata["match"]["gates"] if gate["state"] == "fail"],
            )

    def test_structured_engine_seniority_is_context_aware(self):
        profile = self.structured_profile()
        manager_intern = Opportunity(
            "source",
            "manager-intern",
            "Spring 2031 Product Manager Intern",
            "Example",
            "https://example.com/manager-intern",
            opportunity_type="internship",
        )
        senior = Opportunity(
            "source",
            "senior-intern",
            "Spring 2031 Senior Machine Learning Intern",
            "Example",
            "https://example.com/senior",
            opportunity_type="internship",
        )

        score_opportunity(manager_intern, profile)
        score_opportunity(senior, profile)

        manager_gates = manager_intern.metadata["match"]["gates"]
        self.assertNotIn(
            "career_stage",
            [gate["id"] for gate in manager_gates if gate["state"] == "fail"],
        )
        self.assertIn(
            "career_stage",
            [gate["id"] for gate in senior.metadata["match"]["gates"] if gate["state"] == "fail"],
        )

    def test_structured_engine_gates_experience_and_configured_exclusions(self):
        profile = self.structured_profile()
        profile["candidate"]["max_required_experience_years"] = 2
        profile["matching"]["rules"].append(
            {
                "id": "unpaid",
                "label": "Unpaid work",
                "dimension": "qualification",
                "hard_gate": True,
                "weight": -1,
                "fields": ["title", "description"],
                "terms": ["unpaid"],
            }
        )
        experience = Opportunity(
            "source",
            "experience",
            "Spring 2031 Machine Learning Intern",
            "Example",
            "https://example.com/experience",
            description="Minimum qualifications: 5 years of software engineering experience.",
            opportunity_type="internship",
        )
        unpaid = Opportunity(
            "source",
            "unpaid",
            "Spring 2031 Machine Learning Intern",
            "Example",
            "https://example.com/unpaid",
            description="This is an unpaid opportunity.",
            opportunity_type="internship",
        )

        score_opportunity(experience, profile)
        score_opportunity(unpaid, profile)

        self.assertIn("experience", experience.metadata["match"]["visibility"]["reasons"])
        self.assertIn("unpaid", unpaid.metadata["match"]["visibility"]["reasons"])

    def test_structured_engine_enforces_a_season_only_timeframe(self):
        profile = self.structured_profile()
        profile["targets"]["cycles"] = [{"season": "summer"}]
        winter = Opportunity(
            "source",
            "winter-cycle",
            "Winter Machine Learning Intern",
            "Example",
            "https://example.com/winter-cycle",
            opportunity_type="internship",
        )

        score_opportunity(winter, profile)

        self.assertIn(
            "timeframe",
            winter.metadata["match"]["visibility"]["reasons"],
        )

    def test_structured_engine_scores_editable_profile_fields(self):
        profile = {
            "candidate": {"skills": ["Python"]},
            "targets": {
                "role_families": ["research engineer"],
                "domains": ["instrumentation"],
                "supporting_skills": ["C++"],
                "locations": ["New York"],
                "work_arrangements": ["hybrid"],
            },
            "matching": {
                "engine": "structured_v2",
                "base_score": 25,
                "minimum_display_score": 40,
            },
        }
        item = Opportunity(
            "source",
            "editable-fields",
            "Instrumentation Research Engineer Intern",
            "Example",
            "https://example.com/editable-fields",
            location="New York - Hybrid",
            description="Build scientific systems using Python and C++.",
            opportunity_type="internship",
        )

        score_opportunity(item, profile)

        match = item.metadata["match"]
        component_ids = {component["id"] for component in match["components"]}
        self.assertTrue(match["visibility"]["anchor_matched"])
        self.assertEqual(match["visibility"]["state"], "visible")
        self.assertIn("profile_role_families", component_ids)
        self.assertIn("profile_domains", component_ids)
        self.assertIn("profile_skills", component_ids)
        self.assertIn("profile_locations", component_ids)
        self.assertIn("profile_work_arrangements", component_ids)
        self.assertGreater(match["dimensions"]["qualification"]["points"], 0)
        self.assertGreater(match["dimensions"]["preference"]["points"], 0)

    def test_structured_profile_exclusions_gate_strong_fields_but_only_cap_description(self):
        profile = {
            "targets": {
                "role_families": ["research engineer"],
                "domains": ["machine learning"],
                "exclusions": ["sales"],
            },
            "matching": {
                "engine": "structured_v2",
                "base_score": 25,
                "minimum_display_score": 40,
            },
        }
        title_exclusion = Opportunity(
            "source",
            "title-exclusion",
            "Sales Research Engineer",
            "Example",
            "https://example.com/title-exclusion",
        )
        description_exclusion = Opportunity(
            "source",
            "description-exclusion",
            "Machine Learning Research Engineer",
            "Example",
            "https://example.com/description-exclusion",
            description="Build models and occasionally support sales enablement.",
        )

        score_opportunity(title_exclusion, profile)
        score_opportunity(description_exclusion, profile)

        self.assertEqual(title_exclusion.score, 0)
        self.assertIn(
            "profile_exclusion_gate",
            title_exclusion.metadata["match"]["visibility"]["reasons"],
        )
        description_match = description_exclusion.metadata["match"]
        self.assertNotIn(
            "profile_exclusion_gate",
            description_match["visibility"]["reasons"],
        )
        self.assertEqual(description_exclusion.score, 49)
        self.assertIn(
            "description_exclusion",
            [entry["id"] for entry in description_match["visibility"]["ceilings"]],
        )
        self.assertTrue(description_exclusion.warnings)

    def test_structured_phd_completion_gate_accepts_currently_pursuing_alternative(self):
        profile = self.structured_profile()
        pursuing = Opportunity(
            "source",
            "phd-pursuing",
            "Spring 2031 Machine Learning Intern",
            "Example",
            "https://example.com/phd-pursuing",
            description="PhD required or currently pursuing a PhD.",
            opportunity_type="internship",
        )
        completed = Opportunity(
            "source",
            "phd-completed",
            "Spring 2031 Machine Learning Intern",
            "Example",
            "https://example.com/phd-completed",
            description="Candidates must have completed a PhD.",
            opportunity_type="internship",
        )

        score_opportunity(pursuing, profile)
        score_opportunity(completed, profile)

        pursuing_failures = {
            gate["id"]
            for gate in pursuing.metadata["match"]["gates"]
            if gate["state"] == "fail"
        }
        completed_failures = {
            gate["id"]
            for gate in completed.metadata["match"]["gates"]
            if gate["state"] == "fail"
        }
        self.assertNotIn("degree_completion", pursuing_failures)
        self.assertIn("degree_completion", completed_failures)

    def test_completed_doctorate_prevents_a_false_degree_completion_gate(self):
        profile = self.structured_profile()
        profile["candidate"]["completed_degrees"] = ["Ph.D. Computer Science"]
        item = Opportunity(
            "source",
            "completed-doctorate",
            "Spring 2031 Machine Learning Intern",
            "Example",
            "https://example.com/completed-doctorate",
            description="Candidates must have completed a PhD.",
            opportunity_type="internship",
        )

        score_opportunity(item, profile)

        degree_gate = next(
            gate
            for gate in item.metadata["match"]["gates"]
            if gate["id"] == "degree_completion"
        )
        degree_effect = item.metadata["match"]["features"]["profile_effects"][
            "completed_degrees"
        ]
        self.assertEqual(degree_gate["state"], "pass")
        self.assertEqual(degree_effect["state"], "evaluated_match")
        self.assertEqual(degree_effect["normalized_levels"], ["doctorate"])
        self.assertNotEqual(item.tier, "skip")

    def test_completed_phd_requirement_rejects_declared_non_doctoral_degrees(self):
        for degrees in ([], ["B.S. Mathematics"]):
            with self.subTest(degrees=degrees):
                profile = self.structured_profile()
                profile["candidate"] = {
                    "current_stage": "early_career",
                    "completed_degrees": degrees,
                }
                item = Opportunity(
                    "source",
                    "declared-non-doctorate",
                    "Spring 2031 Machine Learning Intern",
                    "Example",
                    "https://example.com/declared-non-doctorate",
                    description="Candidates must have completed a PhD.",
                    opportunity_type="internship",
                )

                score_opportunity(item, profile)

                degree_gate = next(
                    gate
                    for gate in item.metadata["match"]["gates"]
                    if gate["id"] == "degree_completion"
                )
                degree_effect = item.metadata["match"]["features"][
                    "profile_effects"
                ]["completed_degrees"]
                self.assertEqual(degree_gate["state"], "fail")
                self.assertEqual(degree_effect["state"], "evaluated_no_match")
                self.assertEqual(item.score, 0)
                self.assertEqual(item.tier, "skip")

    def test_completed_phd_requirement_is_unknown_when_completion_data_is_absent(self):
        profile = self.structured_profile()
        profile["candidate"] = {}
        item = Opportunity(
            "source",
            "unknown-completion",
            "Spring 2031 Machine Learning Intern",
            "Example",
            "https://example.com/unknown-completion",
            description="Candidates must have completed a PhD.",
            opportunity_type="internship",
        )

        score_opportunity(item, profile)

        degree_gate = next(
            gate
            for gate in item.metadata["match"]["gates"]
            if gate["id"] == "degree_completion"
        )
        degree_effect = item.metadata["match"]["features"]["profile_effects"][
            "completed_degrees"
        ]
        self.assertEqual(degree_gate["state"], "unknown")
        self.assertEqual(degree_effect["state"], "missing_for_listing_constraint")
        self.assertEqual(item.metadata["match"]["eligibility"], "unknown")
        self.assertNotIn(
            "degree_completion",
            item.metadata["match"]["visibility"]["reasons"],
        )

    def test_expected_graduation_only_gates_explicit_listing_windows(self):
        profile = self.structured_profile()
        profile["candidate"]["expected_graduation"] = "November 2091"
        within_window = Opportunity(
            "source",
            "graduation-within",
            "Spring 2031 Machine Learning Intern",
            "Example",
            "https://example.com/graduation-within",
            description=(
                "Expected graduation date must be between September 2090 "
                "and December 2091."
            ),
            opportunity_type="internship",
        )
        outside_profile = json.loads(json.dumps(profile))
        outside_profile["candidate"]["expected_graduation"] = "January 2093"
        outside_window = Opportunity(
            "source",
            "graduation-outside",
            "Spring 2031 Machine Learning Intern",
            "Example",
            "https://example.com/graduation-outside",
            description=within_window.description,
            opportunity_type="internship",
        )
        weak_context = Opportunity(
            "source",
            "graduation-weak-context",
            "Spring 2031 Machine Learning Intern",
            "Example",
            "https://example.com/graduation-weak-context",
            description="Our founders graduated in 2029 and 2030.",
            opportunity_type="internship",
        )
        explicit_cohort_title = Opportunity(
            "source",
            "graduation-cohort-title",
            "2091 Graduate - Machine Learning Engineer",
            "Example",
            "https://example.com/graduation-cohort-title",
            opportunity_type="job",
        )

        score_opportunity(within_window, profile)
        score_opportunity(outside_window, outside_profile)
        score_opportunity(weak_context, outside_profile)
        score_opportunity(explicit_cohort_title, outside_profile)

        within_gate = next(
            gate
            for gate in within_window.metadata["match"]["gates"]
            if gate["id"] == "graduation_window"
        )
        self.assertEqual(within_gate["state"], "pass")
        self.assertEqual(
            within_window.metadata["match"]["features"]["profile_effects"][
                "expected_graduation"
            ]["state"],
            "evaluated_match",
        )
        self.assertIn(
            "graduation_window",
            outside_window.metadata["match"]["visibility"]["reasons"],
        )
        self.assertNotIn(
            "graduation_window",
            [gate["id"] for gate in weak_context.metadata["match"]["gates"]],
        )
        cohort_gate = next(
            gate
            for gate in explicit_cohort_title.metadata["match"]["gates"]
            if gate["id"] == "graduation_window"
        )
        self.assertEqual(cohort_gate["state"], "fail")

    def test_remote_preference_adds_bounded_auditable_preference_evidence(self):
        profile = self.structured_profile()
        profile["targets"]["remote_preference"] = "remote preferred"
        remote = Opportunity(
            "source",
            "remote-match",
            "Spring 2031 Machine Learning Intern",
            "Example",
            "https://example.com/remote-match",
            location="Remote - United States",
            opportunity_type="internship",
        )
        onsite = Opportunity(
            "source",
            "remote-mismatch",
            "Spring 2031 Machine Learning Intern",
            "Example",
            "https://example.com/remote-mismatch",
            location="On-site - Example City",
            opportunity_type="internship",
        )
        weak_context = Opportunity(
            "source",
            "remote-weak-context",
            "Spring 2031 Machine Learning Intern",
            "Example",
            "https://example.com/remote-weak-context",
            description="Collaborate with remote teams across the organization.",
            opportunity_type="internship",
        )
        negated_remote = Opportunity(
            "source",
            "remote-negated",
            "Spring 2031 Machine Learning Intern",
            "Example",
            "https://example.com/remote-negated",
            description="This is not a remote position.",
            opportunity_type="internship",
        )

        for item in (remote, onsite, weak_context, negated_remote):
            score_opportunity(item, profile)

        def preference_component(item):
            return next(
                (
                    component
                    for component in item.metadata["match"]["components"]
                    if component["id"] == "profile_remote_preference"
                ),
                None,
            )

        self.assertEqual(preference_component(remote)["points"], 6)
        self.assertEqual(preference_component(onsite)["points"], -4)
        self.assertIsNone(preference_component(weak_context))
        self.assertEqual(preference_component(negated_remote)["points"], -4)
        self.assertEqual(
            negated_remote.metadata["match"]["features"]["profile_effects"][
                "remote_preference"
            ]["listing_arrangement"],
            "nonremote",
        )
        remote_effect = remote.metadata["match"]["features"]["profile_effects"][
            "remote_preference"
        ]
        self.assertEqual(remote_effect["state"], "matched")
        self.assertEqual(remote_effect["evidence_field"], "location")
        self.assertGreaterEqual(preference_component(remote)["points"], -6)
        self.assertLessEqual(preference_component(remote)["points"], 6)

    def test_remote_required_only_gates_strong_onsite_only_evidence(self):
        profile = self.structured_profile()
        profile["targets"]["remote_preference"] = "remote required"
        onsite = Opportunity(
            "source",
            "onsite-only",
            "Spring 2031 Machine Learning Intern",
            "Example",
            "https://example.com/onsite-only",
            location="On-site - Example City",
            opportunity_type="internship",
        )
        described_onsite = Opportunity(
            "source",
            "described-onsite-only",
            "Spring 2031 Machine Learning Intern",
            "Example",
            "https://example.com/described-onsite-only",
            description="This is a fully on-site role.",
            opportunity_type="internship",
        )
        hybrid = Opportunity(
            "source",
            "hybrid-safe",
            "Spring 2031 Machine Learning Intern",
            "Example",
            "https://example.com/hybrid-safe",
            location="Hybrid - Example City",
            opportunity_type="internship",
        )
        flexible = Opportunity(
            "source",
            "flexible-safe",
            "Spring 2031 Machine Learning Intern",
            "Example",
            "https://example.com/flexible-safe",
            location="Remote or On-site - Example City",
            opportunity_type="internship",
        )
        remote = Opportunity(
            "source",
            "remote-safe",
            "Spring 2031 Machine Learning Intern",
            "Example",
            "https://example.com/remote-safe",
            location="Remote - United States",
            opportunity_type="internship",
        )
        conflicting = Opportunity(
            "source",
            "conflicting-safe",
            "Spring 2031 Machine Learning Intern",
            "Example",
            "https://example.com/conflicting-safe",
            location="On-site - Example City",
            description="This position has a hybrid schedule.",
            opportunity_type="internship",
        )

        for item in (onsite, described_onsite, hybrid, flexible, remote, conflicting):
            score_opportunity(item, profile)

        for item in (onsite, described_onsite):
            gate = next(
                gate
                for gate in item.metadata["match"]["gates"]
                if gate["id"] == "remote_requirement"
            )
            self.assertEqual(gate["state"], "fail")
            self.assertEqual(item.score, 0)
            self.assertIn(
                "remote_requirement",
                item.metadata["match"]["visibility"]["reasons"],
            )

        for item in (hybrid, flexible, remote, conflicting):
            failed_gate_ids = {
                gate["id"]
                for gate in item.metadata["match"]["gates"]
                if gate["state"] == "fail"
            }
            self.assertNotIn("remote_requirement", failed_gate_ids)

        self.assertEqual(
            hybrid.metadata["match"]["features"]["profile_effects"][
                "remote_preference"
            ]["listing_arrangement"],
            "hybrid",
        )
        self.assertEqual(
            flexible.metadata["match"]["features"]["profile_effects"][
                "remote_preference"
            ]["listing_arrangement"],
            "flexible",
        )
        self.assertEqual(
            remote.metadata["match"]["features"]["profile_effects"][
                "remote_preference"
            ]["requirement_state"],
            "not_contradicted",
        )

    def test_remote_required_gates_explicit_nonremote_evidence(self):
        profile = self.structured_profile()
        profile["targets"]["remote_preference"] = "remote required"
        location_evidence = Opportunity(
            "source",
            "not-remote-location",
            "Spring 2031 Machine Learning Intern",
            "Example",
            "https://example.com/not-remote-location",
            location="Not remote - New York",
            opportunity_type="internship",
        )
        description_evidence = Opportunity(
            "source",
            "not-remote-description",
            "Spring 2031 Machine Learning Intern",
            "Example",
            "https://example.com/not-remote-description",
            description="Remote work is not available.",
            opportunity_type="internship",
        )

        for item, evidence_field in (
            (location_evidence, "location"),
            (description_evidence, "description"),
        ):
            with self.subTest(evidence_field=evidence_field):
                score_opportunity(item, profile)

                remote_effect = item.metadata["match"]["features"][
                    "profile_effects"
                ]["remote_preference"]
                gate = next(
                    gate
                    for gate in item.metadata["match"]["gates"]
                    if gate["id"] == "remote_requirement"
                )
                self.assertEqual(remote_effect["listing_arrangement"], "nonremote")
                self.assertEqual(remote_effect["requirement_state"], "incompatible")
                self.assertEqual(gate["state"], "fail")
                self.assertEqual(
                    gate["evidence"],
                    ["explicit nonremote {}".format(evidence_field)],
                )
                self.assertEqual(item.score, 0)
                self.assertEqual(item.tier, "skip")

    def test_structured_engine_overwrites_untrusted_derived_metadata(self):
        item = Opportunity(
            "source",
            "metadata",
            "Spring 2031 Operations Intern",
            "Example",
            "https://example.com/metadata",
            opportunity_type="internship",
            metadata={
                "match": {"fit_score": 100, "visibility": {"state": "visible"}},
                "eligibility_state": "compatible",
            },
        )
        score_opportunity(item, self.structured_profile())
        self.assertNotEqual(item.metadata["match"]["fit_score"], 100)
        self.assertEqual(item.metadata["match"]["engine"], "structured_v2")

    def test_structured_fingerprint_covers_engine_rules_targets_and_visibility(self):
        profile = self.structured_profile()
        original = profile_fingerprint(profile)
        changed_target = json.loads(json.dumps(profile))
        changed_target["targets"]["cycles"][0]["year"] = 2028
        changed_visibility = json.loads(json.dumps(profile))
        changed_visibility["matching"]["minimum_display_score"] = 50
        changed_rule = json.loads(json.dumps(profile))
        changed_rule["matching"]["rules"][0]["hard_gate"] = True
        changed_role = json.loads(json.dumps(profile))
        changed_role["targets"]["role_families"] = ["research scientist"]
        changed_skill = json.loads(json.dumps(profile))
        changed_skill["candidate"]["skills"] = ["Rust"]
        changed_degree = json.loads(json.dumps(profile))
        changed_degree["candidate"]["completed_degrees"] = ["B.S. Mathematics"]
        declared_empty_degree = json.loads(json.dumps(profile))
        declared_empty_degree["candidate"]["completed_degrees"] = []
        changed_graduation = json.loads(json.dumps(profile))
        changed_graduation["candidate"]["expected_graduation"] = "November 2091"
        changed_remote = json.loads(json.dumps(profile))
        changed_remote["targets"]["remote_preference"] = "remote preferred"

        self.assertNotEqual(original, profile_fingerprint(changed_target))
        self.assertNotEqual(original, profile_fingerprint(changed_visibility))
        self.assertNotEqual(original, profile_fingerprint(changed_rule))
        self.assertNotEqual(original, profile_fingerprint(changed_role))
        self.assertNotEqual(original, profile_fingerprint(changed_skill))
        self.assertNotEqual(original, profile_fingerprint(changed_degree))
        self.assertNotEqual(original, profile_fingerprint(declared_empty_degree))
        self.assertNotEqual(original, profile_fingerprint(changed_graduation))
        self.assertNotEqual(original, profile_fingerprint(changed_remote))

    def test_structured_fingerprint_canonicalizes_implicit_anchor_defaults(self):
        explicit_interest_anchor = self.structured_profile()
        implicit_interest_anchor = json.loads(json.dumps(explicit_interest_anchor))
        del implicit_interest_anchor["matching"]["rules"][0]["anchor"]

        implicit_qualification_anchor = self.structured_profile()
        explicit_qualification_anchor = json.loads(
            json.dumps(implicit_qualification_anchor)
        )
        explicit_qualification_anchor["matching"]["rules"][1]["anchor"] = False

        self.assertEqual(
            profile_fingerprint(explicit_interest_anchor),
            profile_fingerprint(implicit_interest_anchor),
        )
        self.assertEqual(
            profile_fingerprint(implicit_qualification_anchor),
            profile_fingerprint(explicit_qualification_anchor),
        )


if __name__ == "__main__":
    unittest.main()
