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
    "national-labs",
    "national-security",
}

RELIABLE_LISTING_KINDS = {"greenhouse", "lever", "jibe"}
LISTING_KINDS = RELIABLE_LISTING_KINDS | {"html_links"}

NEW_OPT_IN_FEED_PACKS = {
    "metr_lever": {"public-interest", "academia-research", "ai-research"},
    "ai2_greenhouse": {"engineering", "data-software", "academia-research", "ai-research"},
    "arc_institute_greenhouse": {"biotech-health", "academia-research", "ai-research"},
    "cloudflare_greenhouse": {"data-software", "cybersecurity", "product-design"},
    "wikimedia_greenhouse": {"data-software", "product-design", "public-interest"},
    "khan_academy_greenhouse": {"data-software", "product-design", "public-interest"},
    "redwood_materials_greenhouse": {"engineering", "climate-energy", "skilled-technical"},
    "anduril_greenhouse": {"engineering", "cybersecurity", "national-security"},
    "spacex_greenhouse": {"engineering", "skilled-technical", "national-security"},
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
            if "include_content" in source:
                self.assertIsInstance(source["include_content"], bool, source["id"])
            if "category_metadata_names" in source:
                self.assertIsInstance(source["category_metadata_names"], list, source["id"])
                self.assertTrue(source["category_metadata_names"], source["id"])
                self.assertTrue(
                    all(
                        isinstance(name, str) and name.strip()
                        for name in source["category_metadata_names"]
                    ),
                    source["id"],
                )

    def test_verified_structured_feed_expansion_is_opt_in_and_relevant(self):
        sources = {source["id"]: source for source in self.sources}
        self.assertTrue(NEW_OPT_IN_FEED_PACKS.keys() <= sources.keys())
        for source_id, required_packs in NEW_OPT_IN_FEED_PACKS.items():
            source = sources[source_id]
            self.assertFalse(source["enabled"], source_id)
            self.assertEqual(source["source_type"], "listing_feed", source_id)
            self.assertEqual(source["support_level"], "supported", source_id)
            self.assertIn(source["kind"], RELIABLE_LISTING_KINDS, source_id)
            self.assertTrue(required_packs <= set(source["packs"]), source_id)

    def test_google_careers_is_a_bounded_opt_in_official_feed(self):
        source = next(source for source in self.sources if source["id"] == "google_careers")
        self.assertEqual(source["kind"], "html_links")
        self.assertEqual(source["support_level"], "experimental")
        self.assertFalse(source["enabled"])
        self.assertGreater(source["pages"], 1)
        self.assertLessEqual(source["pages"], 20)
        self.assertTrue(source["same_domain"])

    def test_structured_feeds_do_not_pre_filter_job_families(self):
        disallowed_query_fields = {"category", "department", "keyword", "q", "query", "search", "tag", "tags", "tags9"}
        for source in self.sources:
            if source["source_type"] != "listing_feed":
                continue
            listing_url = source.get("api_url", source["url"])
            query_fields = {
                key.casefold()
                for key, _ in urllib.parse.parse_qsl(
                    urllib.parse.urlsplit(listing_url).query,
                    keep_blank_values=True,
                )
            }
            self.assertFalse(query_fields.intersection(disallowed_query_fields), source["id"])

        apl = next(source for source in self.sources if source["id"] == "jhu_apl")
        self.assertEqual(apl["url"], "https://careers.jhuapl.edu/jobs")
        self.assertIn("job", apl["opportunity_types"])
        self.assertNotIn("national-labs", apl["packs"])

    def test_sources_are_generic_structured_and_https_only(self):
        pack_ids = {pack["id"] for pack in self.packs}
        source_ids = [source["id"] for source in self.sources]
        self.assertEqual(len(source_ids), len(set(source_ids)))
        self.assertGreaterEqual(len(self.sources), 60)

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

    def test_pack_feed_coverage_excludes_manual_watch_pages(self):
        listing_feeds = [
            source
            for source in self.sources
            if source["source_type"] == "listing_feed"
        ]
        manual_watch_pages = [
            source
            for source in self.sources
            if source["source_type"] != "listing_feed"
        ]
        for source in listing_feeds:
            self.assertIn(source["kind"], LISTING_KINDS, source["id"])
            expected_support = (
                "experimental" if source["kind"] == "html_links" else "supported"
            )
            self.assertEqual(source["support_level"], expected_support, source["id"])
        for source in manual_watch_pages:
            self.assertEqual(source["kind"], "watch_page", source["id"])
            self.assertNotIn(source["kind"], RELIABLE_LISTING_KINDS, source["id"])

        feed_packs = {
            pack
            for source in listing_feeds
            for pack in source["packs"]
        }
        manual_packs = {
            pack
            for source in manual_watch_pages
            for pack in source["packs"]
        }
        self.assertEqual(REQUIRED_PACK_IDS - feed_packs, {"national-labs"})
        self.assertIn("national-labs", manual_packs)


if __name__ == "__main__":
    unittest.main()
