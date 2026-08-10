import unittest

from monitor.models import Opportunity
from monitor.scoring import score_opportunity


class ScoringTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
