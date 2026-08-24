import unittest

from monitor.coverage import (
    COVERAGE_PRESETS,
    PACK_ORDER,
    effective_source_packs,
    recommend_source_packs,
    target_defaults_for_preset,
)


class CoveragePresetTests(unittest.TestCase):
    def test_medicine_profile_gets_broad_automatic_coverage(self):
        profile = {
            "candidate": {"current_stage": "undergraduate_student"},
            "targets": {
                "domains": ["Clinical", "Wet lab", "Translational"],
                "role_families": ["research scientist", "postbacc"],
                "opportunity_types": ["job", "research_program"],
            },
        }

        packs = set(recommend_source_packs(profile))

        self.assertTrue(
            {
                "biotech-health",
                "medicine-clinical",
                "public-health",
                "pharma-drug-discovery",
                "biomedical-neuroscience",
                "academia-research",
                "students-early-career",
                "fellowships",
            }.issubset(packs)
        )

    def test_physics_profile_does_not_pick_unrelated_health_presets(self):
        profile = {
            "targets": {
                "domains": ["particle physics", "scientific computing"],
                "role_families": ["research scientist"],
            }
        }

        packs = set(recommend_source_packs(profile))

        self.assertIn("physics-quantum-astronomy", packs)
        self.assertIn("data-software", packs)
        self.assertNotIn("medicine-clinical", packs)

    def test_manual_and_named_presets_preserve_explicit_packs(self):
        profile = {"targets": {"domains": ["clinical medicine"]}}

        self.assertEqual(
            effective_source_packs(profile, ["engineering"], "manual"),
            ["engineering"],
        )
        named = set(
            effective_source_packs(profile, ["starter-diverse"], "ocean-marine")
        )
        self.assertTrue(
            {"starter-diverse", "ocean-marine", "ecology-environment"}.issubset(named)
        )

    def test_every_preset_maps_to_known_unique_packs(self):
        known = set(PACK_ORDER)
        for preset in COVERAGE_PRESETS:
            self.assertTrue(preset["packs"], preset["id"])
            self.assertEqual(len(preset["packs"]), len(set(preset["packs"])), preset["id"])
            self.assertTrue(set(preset["packs"]).issubset(known), preset["id"])
            defaults = target_defaults_for_preset(str(preset["id"]))
            self.assertTrue(defaults["role_families"], preset["id"])
            self.assertTrue(defaults["domains"], preset["id"])
            self.assertTrue(defaults["supporting_skills"], preset["id"])


if __name__ == "__main__":
    unittest.main()
