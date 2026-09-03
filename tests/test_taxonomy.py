import unittest

from monitor.profile import ProfileValidationError, portable_profile_editor
from monitor.taxonomy import canonical_location, location_match_details, taxonomy_payload


class TaxonomyTests(unittest.TestCase):
    def test_catalog_contains_industry_taxonomies(self):
        catalog = taxonomy_payload()
        self.assertGreater(len(catalog["countries"]), 240)
        self.assertGreater(len(catalog["cities"]), 30_000)
        self.assertGreater(len(catalog["education_fields"]), 2_000)

    def test_location_matching_obeys_city_region_country_hierarchy(self):
        self.assertEqual(
            canonical_location("Baltimore"),
            "Baltimore, Maryland, United States",
        )
        self.assertTrue(
            location_match_details(["United States"], "Baltimore, MD")["matched"]
        )
        self.assertTrue(
            location_match_details(["Maryland, United States"], "Baltimore, MD")["matched"]
        )
        self.assertFalse(
            location_match_details(["Canada"], "Baltimore, MD")["matched"]
        )
        self.assertEqual(canonical_location("Georgia"), "Georgia")
        self.assertFalse(location_match_details(["Georgia"], "Atlanta, GA")["matched"])
        self.assertTrue(location_match_details(["United States"], "San Francisco, CA")["matched"])
        self.assertFalse(location_match_details(["Canada"], "San Francisco, CA")["matched"])
        self.assertTrue(location_match_details(["Germany"], "Berlin, DE")["matched"])
        self.assertFalse(location_match_details(["United States"], "Berlin, DE")["matched"])

    def test_one_shot_profile_translates_degree_and_locations(self):
        name, editor = portable_profile_editor({
            "version": 1,
            "name": "AI research",
            "candidate": {
                "stage": "early_career",
                "degrees": [{"type": "BS", "field": "Computer Science"}],
                "skills": ["Python"],
            },
            "search": {
                "roles": ["ML Engineer"],
                "locations": ["Baltimore"],
                "strict_locations": True,
            },
        })
        self.assertEqual(name, "AI research")
        self.assertEqual(
            editor["candidate"]["completed_degrees"],
            ["Bachelor's degree in Computer Science"],
        )
        self.assertEqual(
            editor["targets"]["locations"],
            ["Baltimore, Maryland, United States"],
        )
        self.assertTrue(editor["targets"]["strict_locations"])
        self.assertEqual(editor["matching"]["engine"], "structured_v2")

    def test_one_shot_profile_rejects_unknown_fields(self):
        with self.assertRaisesRegex(ProfileValidationError, "unsupported keys"):
            portable_profile_editor({"version": 1, "name": "Bad", "unknown": True})


if __name__ == "__main__":
    unittest.main()
