import unittest

from monitor.targeting import effective_matching_rules, reconcile_matching_rules


def rule(rule_id, terms, dimension="interest", weight=20, hard_gate=False):
    return {
        "id": rule_id,
        "label": rule_id.replace("_", " ").title(),
        "terms": terms,
        "weight": weight,
        "dimension": dimension,
        "hard_gate": hard_gate,
    }


class TargetingTests(unittest.TestCase):
    def test_pending_migration_filters_only_unaligned_positive_interest_rules(self):
        profile = {
            "schema_version": 2,
            "targets": {
                "domains": ["marine ecology", "geospatial analysis"],
                "role_families": ["field technician"],
            },
        }
        rules = [
            rule("retail", ["retail merchandising"]),
            rule("geospatial", ["geospatial analysis"]),
            rule("negative", ["commission only"], weight=-30),
            rule("qualification", ["boat license"], dimension="qualification"),
            rule("required", ["work permit"], hard_gate=True),
            rule("target", ["summer program"], dimension="target"),
        ]

        effective = effective_matching_rules(profile, rules)

        self.assertEqual(
            [entry["id"] for entry in effective],
            ["geospatial", "negative", "qualification", "required", "target"],
        )
        self.assertEqual(profile["schema_version"], 2)
        self.assertEqual(len(rules), 6)

    def test_current_schema_does_not_reconcile_without_a_target_scope_change(self):
        targets = {
            "domains": ["marine ecology"],
            "role_families": ["field technician"],
        }
        rules = [rule("independent_advanced_rule", ["retail merchandising"])]

        effective, adjustments = reconcile_matching_rules(
            targets,
            targets,
            rules,
            previous_schema_version=3,
        )

        self.assertEqual(effective, rules)
        self.assertFalse(adjustments["semantic_schema_upgraded"])
        self.assertEqual(adjustments["retired_matching_rules"], [])

    def test_adding_a_target_does_not_retire_an_independent_advanced_rule(self):
        previous = {
            "domains": ["marine ecology"],
            "role_families": [],
        }
        selected = {
            "domains": ["marine ecology", "geospatial analysis"],
            "role_families": [],
        }
        rules = [rule("independent_advanced_rule", ["retail merchandising"])]

        effective, adjustments = reconcile_matching_rules(
            previous,
            selected,
            rules,
            previous_schema_version=3,
        )

        self.assertEqual(effective, rules)
        self.assertFalse(adjustments["semantic_schema_upgraded"])
        self.assertEqual(adjustments["retired_matching_rules"], [])

    def test_removed_target_retires_only_rules_without_retained_overlap(self):
        previous = {
            "domains": ["retail operations", "geospatial analysis"],
            "role_families": [],
        }
        selected = {
            "domains": ["geospatial analysis"],
            "role_families": [],
        }
        rules = [
            rule("retail", ["retail operations"]),
            rule("crossover", ["retail operations", "geospatial analysis"]),
        ]

        effective, adjustments = reconcile_matching_rules(
            previous,
            selected,
            rules,
            previous_schema_version=3,
        )

        self.assertEqual([entry["id"] for entry in effective], ["crossover"])
        self.assertEqual(
            adjustments["retired_matching_rules"],
            [
                {
                    "id": "retail",
                    "label": "Retail",
                    "reason": "removed_target",
                }
            ],
        )

    def test_removing_last_target_retires_only_rules_tied_to_removed_scope(self):
        previous = {
            "domains": ["finance"],
            "role_families": [],
        }
        selected = {
            "domains": [],
            "role_families": [],
        }
        rules = [
            rule("quant", ["quantitative finance"]),
            rule("independent", ["marine ecology"]),
            rule("negative", ["commission only"], weight=-30),
        ]

        effective, adjustments = reconcile_matching_rules(
            previous,
            selected,
            rules,
            previous_schema_version=3,
        )

        self.assertEqual(
            [entry["id"] for entry in effective],
            ["independent", "negative"],
        )
        self.assertEqual(
            adjustments["retired_matching_rules"],
            [
                {
                    "id": "quant",
                    "label": "Quant",
                    "reason": "removed_target",
                }
            ],
        )

    def test_profiles_without_basic_role_or_domain_scope_remain_open_ended(self):
        rules = [rule("independent", ["retail merchandising"])]

        effective = effective_matching_rules(
            {"schema_version": 2, "targets": {"domains": [], "role_families": []}},
            rules,
        )

        self.assertEqual(effective, rules)


if __name__ == "__main__":
    unittest.main()
