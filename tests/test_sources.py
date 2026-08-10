import json
import unittest
import urllib.parse
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCES_PATH = ROOT / "config" / "sources.json"

STARTER_SOURCE_IDS = {
    "figma_greenhouse",
    "code_for_america_greenhouse",
    "ginkgo_greenhouse",
    "cfs_lever",
    "anthropic_greenhouse",
}

REQUIRED_PACK_IDS = {
    "starter-diverse",
    "engineering",
    "data-software",
    "cybersecurity",
    "product-design",
    "biotech-health",
    "climate-energy",
    "public-interest",
    "academia-research",
    "fellowships",
    "finance-quant",
    "ai-research",
    "skilled-technical",
}

REQUIRED_DOMAINS = {
    "engineering",
    "software",
    "data",
    "cybersecurity",
    "product",
    "design",
    "biotech_health",
    "climate_energy",
    "public_interest",
    "academia_research",
    "fellowships",
    "finance_quant",
    "ai_ml",
    "skilled_technical",
}

FORBIDDEN_KEYS = {
    "base_score",
    "tier",
    "recommended_resume",
    "category",
    "item_include",
    "item_exclude",
    "target_season",
    "acceptance_rate",
    "acceptance_chance",
    "acceptance_odds",
    "high_chance",
}


def all_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from all_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from all_keys(child)


class PublicSourceCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
        cls.packs = cls.catalog["packs"]
        cls.sources = cls.catalog["sources"]

    def test_catalog_has_named_pack_metadata(self):
        self.assertEqual(self.catalog["schema_version"], 1)
        pack_ids = [pack["id"] for pack in self.packs]
        self.assertEqual(len(pack_ids), len(set(pack_ids)))
        self.assertTrue(REQUIRED_PACK_IDS.issubset(pack_ids))
        self.assertEqual(
            [pack["id"] for pack in self.packs if pack.get("default")],
            ["starter-diverse"],
        )
        for pack in self.packs:
            self.assertRegex(pack["id"], r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
            self.assertTrue(pack["name"].strip())
            self.assertTrue(pack["description"].strip())

    def test_starter_is_small_diverse_structured_and_no_secret(self):
        enabled = {source["id"] for source in self.sources if source["enabled"]}
        self.assertEqual(enabled, STARTER_SOURCE_IDS)
        starter = [source for source in self.sources if source["id"] in enabled]
        self.assertEqual({source["kind"] for source in starter}, {"greenhouse", "lever"})
        self.assertGreaterEqual(len({domain for source in starter for domain in source["domains"]}), 8)
        for source in starter:
            self.assertIn("starter-diverse", source["packs"])
            self.assertEqual(source["source_type"], "listing_feed")
            self.assertEqual(source["support_level"], "supported")
            self.assertNotIn("requires_env", source)

    def test_supported_ats_sources_follow_public_api_conventions(self):
        for source in self.sources:
            if source["kind"] == "greenhouse":
                expected = "https://boards-api.greenhouse.io/v1/boards/{}/jobs".format(source["board"])
                self.assertEqual(source["api_url"], expected, source["id"])
            elif source["kind"] == "lever":
                expected = "https://api.lever.co/v0/postings/{}?mode=json".format(source["site"])
                self.assertEqual(source["api_url"], expected, source["id"])
            elif source["kind"] == "jibe":
                self.assertTrue(source["api_url"].startswith("https://"), source["id"])
                self.assertIn("{slug}", source["job_url_template"])

    def test_structured_feeds_do_not_pre_filter_job_families(self):
        disallowed_query_fields = {"category", "department", "keyword", "q", "query", "search", "tag", "tags", "tags9"}
        for source in self.sources:
            if source["source_type"] != "listing_feed":
                continue
            query_fields = {
                key.casefold()
                for key, _ in urllib.parse.parse_qsl(
                    urllib.parse.urlsplit(source["api_url"]).query,
                    keep_blank_values=True,
                )
            }
            self.assertFalse(query_fields.intersection(disallowed_query_fields), source["id"])

        apl = next(source for source in self.sources if source["id"] == "jhu_apl")
        self.assertEqual(apl["url"], "https://careers.jhuapl.edu/jobs")
        self.assertIn("job", apl["opportunity_types"])

    def test_sources_are_generic_structured_and_https_only(self):
        pack_ids = {pack["id"] for pack in self.packs}
        source_ids = [source["id"] for source in self.sources]
        self.assertEqual(len(source_ids), len(set(source_ids)))
        self.assertGreaterEqual(len(self.sources), 40)

        for source in self.sources:
            self.assertRegex(source["id"], r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
            self.assertTrue(source["official"], source["id"])
            self.assertRegex(source["verified_at"], r"^\d{4}-\d{2}-\d{2}$")
            self.assertIsInstance(source["cadence_hours"], int)
            self.assertGreater(source["cadence_hours"], 0)
            for key in ("packs", "domains", "opportunity_types", "career_levels", "regions"):
                self.assertIsInstance(source[key], list, "{} {}".format(source["id"], key))
                self.assertTrue(source[key], "{} {}".format(source["id"], key))
                self.assertTrue(all(isinstance(value, str) and value for value in source[key]))
                self.assertEqual(len(source[key]), len(set(source[key])))
            self.assertTrue(set(source["packs"]).issubset(pack_ids), source["id"])
            for key in ("url", "api_url", "job_url_template"):
                if key not in source:
                    continue
                parsed = urllib.parse.urlsplit(source[key])
                self.assertEqual(parsed.scheme, "https", "{} {}".format(source["id"], key))
                self.assertTrue(parsed.netloc, "{} {}".format(source["id"], key))

    def test_requested_domains_have_opt_in_coverage(self):
        domains = {domain for source in self.sources for domain in source["domains"]}
        self.assertTrue(REQUIRED_DOMAINS.issubset(domains))
        pack_ids = {pack["id"] for pack in self.packs}
        self.assertTrue(REQUIRED_PACK_IDS.issubset(pack_ids))

    def test_catalog_has_no_personal_or_ranking_metadata(self):
        keys = set(all_keys(self.catalog))
        self.assertFalse(keys.intersection(FORBIDDEN_KEYS))

    def test_manual_and_calendar_pages_are_disabled_non_listings(self):
        manual_types = {"official_portal", "manual_page", "program_calendar"}
        manual_sources = [source for source in self.sources if source["source_type"] in manual_types]
        self.assertGreater(len(manual_sources), 0)
        for source in manual_sources:
            self.assertFalse(source["enabled"], source["id"])
            self.assertEqual(source["kind"], "watch_page", source["id"])
            self.assertEqual(source["support_level"], "manual", source["id"])
            self.assertFalse(source.get("publish_as_opportunity", False), source["id"])


if __name__ == "__main__":
    unittest.main()
