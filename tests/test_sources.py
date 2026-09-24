import json
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

from monitor import config


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
    "students-early-career",
    "space-aerospace",
    "robotics-autonomy",
    "education-social-impact",
}

NEW_STEM_PACK_IDS = {
    "mathematical-sciences",
    "physics-quantum-astronomy",
    "chemistry-materials",
    "earth-geospatial",
    "ecology-environment",
    "agriculture-food",
    "medicine-clinical",
    "public-health",
    "pharma-drug-discovery",
    "biomedical-neuroscience",
    "civil-infrastructure",
    "electronics-semiconductors",
    "ocean-marine",
    "veterinary-animal",
}

NEW_STEM_DOMAINS = {
    "mathematics",
    "statistics",
    "physics",
    "astronomy",
    "quantum",
    "chemistry",
    "materials_science",
    "geoscience",
    "geospatial",
    "environmental_science",
    "ecology",
    "meteorology",
    "hydrology",
    "agriculture",
    "food_science",
    "medicine",
    "clinical_research",
    "public_health",
    "epidemiology",
    "pharmacology",
    "drug_discovery",
    "biomedical_engineering",
    "medical_devices",
    "neuroscience",
    "civil_engineering",
    "infrastructure",
    "electrical_engineering",
    "semiconductors",
    "oceanography",
    "marine_science",
    "veterinary_science",
}

VERIFIED_2026_08_23_STEM_FEEDS = {
    "flatiron_health_greenhouse",
    "eikon_therapeutics_greenhouse",
    "neumora_therapeutics_greenhouse",
    "beam_therapeutics_greenhouse",
    "verve_therapeutics_greenhouse",
    "oscar_health_greenhouse",
    "adaptive_biotechnologies_greenhouse",
    "abcellera_greenhouse",
    "revolution_medicines_greenhouse",
    "relay_therapeutics_greenhouse",
    "sword_health_greenhouse",
    "zocdoc_greenhouse",
    "open_evidence_ashby",
    "freed_ashby",
    "insitro_ashby",
    "ambience_healthcare_ashby",
    "benchling_ashby",
    "abridge_ashby",
    "commure_ashby",
    "deep_genomics_lever",
    "lyra_health_lever",
    "xaira_therapeutics_greenhouse",
    "bond_vet_greenhouse",
    "generate_biomedicines_greenhouse",
    "schrodinger_greenhouse",
    "komodo_health_greenhouse",
    "precision_for_medicine_greenhouse",
    "formation_bio_greenhouse",
    "freenome_greenhouse",
    "natera_greenhouse",
    "profluent_greenhouse",
    "ionq_greenhouse",
    "psiquantum_greenhouse",
    "tenstorrent_greenhouse",
    "tenstorrent_university_greenhouse",
    "planet_greenhouse",
    "blacksky_greenhouse",
    "lightmatter_greenhouse",
    "astera_labs_greenhouse",
    "rebuild_manufacturing_greenhouse",
    "pronto_greenhouse",
    "scout_ai_greenhouse",
    "sma_america_greenhouse",
    "sound_agriculture_greenhouse",
    "atom_computing_lever",
    "rigetti_lever",
    "dronedeploy_lever",
    "oceanx_careers",
}

REPRESENTATIVE_STEM_FEEDS = {
    "precision_for_medicine_greenhouse": {
        "packs": {"medicine-clinical", "public-health", "pharma-drug-discovery"},
        "domains": {"medicine", "clinical_research", "epidemiology"},
    },
    "ionq_greenhouse": {
        "packs": {
            "mathematical-sciences",
            "physics-quantum-astronomy",
            "electronics-semiconductors",
        },
        "domains": {"physics", "quantum", "electrical_engineering"},
    },
    "planet_greenhouse": {
        "packs": {"earth-geospatial", "ecology-environment"},
        "domains": {"geospatial", "environmental_science", "meteorology"},
    },
    "sound_agriculture_greenhouse": {
        "packs": {"agriculture-food", "chemistry-materials"},
        "domains": {"agriculture", "food_science", "chemistry"},
    },
    "dronedeploy_lever": {
        "packs": {"earth-geospatial", "civil-infrastructure"},
        "domains": {"geospatial", "civil_engineering", "infrastructure"},
    },
    "tenstorrent_greenhouse": {
        "packs": {"electronics-semiconductors", "robotics-autonomy"},
        "domains": {"semiconductors", "electrical_engineering", "robotics"},
    },
    "bond_vet_greenhouse": {
        "packs": {"veterinary-animal", "medicine-clinical"},
        "domains": {"veterinary_science", "medicine"},
    },
    "oceanx_careers": {
        "packs": {"ocean-marine", "earth-geospatial", "ecology-environment"},
        "domains": {"oceanography", "marine_science", "environmental_science"},
    },
}

REPRESENTATIVE_MANUAL_STEM_RESOURCES = {
    "mathjobs_portal": ({"mathematical-sciences"}, {"mathematics", "statistics"}),
    "noaa_careers": ({"earth-geospatial", "ocean-marine"}, {"meteorology", "oceanography"}),
    "mayo_clinic_jobs": ({"medicine-clinical", "biomedical-neuroscience"}, {"medicine", "neuroscience"}),
    "cdc_careers": ({"public-health", "medicine-clinical"}, {"public_health", "epidemiology"}),
    "asce_career_connections": ({"civil-infrastructure"}, {"civil_engineering", "infrastructure"}),
    "whoi_careers": ({"ocean-marine"}, {"oceanography", "marine_science"}),
    "avma_jobs": ({"veterinary-animal"}, {"veterinary_science"}),
}

RELIABLE_LISTING_KINDS = {"ashby", "greenhouse", "lever", "jibe"}
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

REPRESENTATIVE_STAGE_FEEDS = {
    "climate_corps_greenhouse": {
        "packs": {"climate-energy", "fellowships", "students-early-career"},
        "types": {"fellowship", "internship"},
        "levels": {"undergraduate", "new_grad", "early_career"},
    },
    "aclu_internships_greenhouse": {
        "packs": {"public-interest", "students-early-career", "education-social-impact"},
        "types": {"internship"},
        "levels": {"undergraduate", "graduate", "law_student"},
    },
    "zinnia_internships_greenhouse": {
        "packs": {"finance-quant"},
        "types": {"internship"},
        "levels": {"undergraduate", "graduate"},
    },
    "rocket_lab_greenhouse": {
        "packs": {"engineering", "students-early-career", "space-aerospace"},
        "types": {"job", "internship"},
        "levels": {"new_grad", "early_career", "vocational"},
    },
    "field_ai_lever": {
        "packs": {"ai-research", "students-early-career", "robotics-autonomy"},
        "types": {"job", "internship"},
        "levels": {"undergraduate", "graduate", "phd"},
    },
    "vera_institute_greenhouse": {
        "packs": {"public-interest", "students-early-career", "education-social-impact"},
        "types": {"job", "internship", "fellowship"},
        "levels": {"undergraduate", "graduate", "new_grad"},
    },
}

VERIFIED_2026_08_20_FEEDS = {
    "locus_robotics_greenhouse": {
        "kind": "greenhouse",
        "packs": {"engineering", "students-early-career", "robotics-autonomy"},
        "types": {"internship", "co_op"},
    },
    "rugged_robotics_greenhouse": {
        "kind": "greenhouse",
        "packs": {"engineering", "skilled-technical", "students-early-career", "robotics-autonomy"},
        "types": {"internship", "co_op"},
    },
    "roboforce_greenhouse": {
        "kind": "greenhouse",
        "packs": {"engineering", "ai-research", "students-early-career", "robotics-autonomy"},
        "types": {"internship", "residency"},
    },
    "givedirectly_greenhouse": {
        "kind": "greenhouse",
        "packs": {"public-interest", "students-early-career", "education-social-impact"},
        "types": {"job", "internship", "fellowship"},
    },
    "earth_species_project_lever": {
        "kind": "lever",
        "packs": {"biotech-health", "public-interest", "academia-research", "ai-research"},
        "types": {"job", "internship", "research_program"},
    },
    "handshake_ashby": {
        "kind": "ashby",
        "packs": {"data-software", "ai-research", "students-early-career"},
        "types": {"job", "internship"},
    },
    "root_access_ashby": {
        "kind": "ashby",
        "packs": {"academia-research", "fellowships", "ai-research", "students-early-career"},
        "types": {"job", "fellowship", "research_program"},
    },
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
        self.assertGreaterEqual(len(pack_ids), 19)
        self.assertTrue(REQUIRED_PACK_IDS.issubset(pack_ids))
        self.assertEqual(
            [pack["id"] for pack in self.packs if pack.get("default")],
            ["starter-diverse"],
        )
        for pack in self.packs:
            self.assertRegex(pack["id"], r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
            self.assertTrue(pack["name"].strip())
            self.assertTrue(pack["description"].strip())

    def test_cross_stem_catalog_is_large_balanced_and_opt_in(self):
        pack_ids = {pack["id"] for pack in self.packs}
        self.assertGreaterEqual(len(self.sources), 230)
        self.assertTrue(NEW_STEM_PACK_IDS <= pack_ids)

        packs = {pack["id"]: pack for pack in self.packs}
        for pack_id in NEW_STEM_PACK_IDS:
            self.assertFalse(packs[pack_id].get("default", False), pack_id)
            members = [source for source in self.sources if pack_id in source["packs"]]
            self.assertGreaterEqual(len(members), 5, pack_id)
            self.assertTrue(all(not source["enabled"] for source in members), pack_id)
            listing_feeds = [
                source for source in members if source["source_type"] == "listing_feed"
            ]
            self.assertGreaterEqual(len(listing_feeds), 1, pack_id)

    def test_cross_stem_domains_cover_full_taxonomy(self):
        domains = {domain for source in self.sources for domain in source["domains"]}
        self.assertTrue(NEW_STEM_DOMAINS <= domains)

    def test_verified_cross_stem_structured_feeds_are_supported_opt_in(self):
        sources = {source["id"]: source for source in self.sources}
        self.assertTrue(VERIFIED_2026_08_23_STEM_FEEDS <= sources.keys())
        for source_id in VERIFIED_2026_08_23_STEM_FEEDS:
            source = sources[source_id]
            self.assertFalse(source["enabled"], source_id)
            self.assertTrue(source.get("auto_enable", True), source_id)
            self.assertEqual(source["verified_at"], "2026-08-23", source_id)
            self.assertEqual(source["source_type"], "listing_feed", source_id)
            self.assertEqual(source["support_level"], "supported", source_id)
            self.assertIn(source["kind"], {"ashby", "greenhouse", "lever"}, source_id)

        for source_id, expected in REPRESENTATIVE_STEM_FEEDS.items():
            source = sources[source_id]
            self.assertTrue(expected["packs"] <= set(source["packs"]), source_id)
            self.assertTrue(expected["domains"] <= set(source["domains"]), source_id)

    def test_manual_stem_directories_never_auto_enable_or_publish(self):
        sources = {source["id"]: source for source in self.sources}
        manual = [
            source
            for source in self.sources
            if source["verified_at"] == "2026-08-23" and source["kind"] == "watch_page"
        ]
        self.assertGreaterEqual(len(manual), 100)
        for source in manual:
            self.assertFalse(source["enabled"], source["id"])
            self.assertFalse(source["auto_enable"], source["id"])
            self.assertFalse(source["publish_as_opportunity"], source["id"])
            self.assertEqual(source["support_level"], "manual", source["id"])
            self.assertIn(
                source["source_type"],
                {"official_portal", "manual_page", "program_calendar"},
                source["id"],
            )

        for source_id, (packs, domains) in REPRESENTATIVE_MANUAL_STEM_RESOURCES.items():
            source = sources[source_id]
            self.assertTrue(packs <= set(source["packs"]), source_id)
            self.assertTrue(domains <= set(source["domains"]), source_id)

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
            if source["kind"] == "ashby":
                expected = "https://api.ashbyhq.com/posting-api/job-board/{}".format(
                    source["board"]
                )
                self.assertEqual(source["api_url"], expected, source["id"])
                self.assertEqual(
                    source["url"],
                    "https://jobs.ashbyhq.com/{}".format(source["board"]),
                    source["id"],
                )
            elif source["kind"] == "greenhouse":
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
            if source.get("include_content") is False:
                self.assertEqual(
                    source.get("description_availability"),
                    "title_only",
                    source["id"],
                )
            if "description_availability" in source:
                self.assertEqual(
                    source["description_availability"],
                    "title_only",
                    source["id"],
                )
                self.assertEqual(source["source_type"], "listing_feed", source["id"])

    def test_ashby_feeds_are_supported_opt_in_and_cover_distinct_domains(self):
        ashby = {source["id"]: source for source in self.sources if source["kind"] == "ashby"}
        self.assertGreaterEqual(len(ashby), 2)
        self.assertIn("openai_ashby", ashby)
        self.assertIn("heirloom_ashby", ashby)
        for source in ashby.values():
            self.assertFalse(source["enabled"], source["id"])
            self.assertEqual(source["support_level"], "supported", source["id"])
            self.assertEqual(source["source_type"], "listing_feed", source["id"])
            self.assertTrue(source["board"], source["id"])

        self.assertIn("ai-research", ashby["openai_ashby"]["packs"])
        self.assertIn("residency", ashby["openai_ashby"]["opportunity_types"])
        self.assertEqual(ashby["heirloom_ashby"]["board"], "heirloomcarbon")
        self.assertIn("climate-energy", ashby["heirloom_ashby"]["packs"])
        self.assertIn("vocational", ashby["heirloom_ashby"]["career_levels"])

    def test_failed_public_sources_follow_current_official_destinations(self):
        sources = {source["id"]: source for source in self.sources}

        self.assertNotIn("campus_opportunities_greenhouse", sources)
        withum = sources["withum_campus"]
        self.assertEqual(withum["kind"], "watch_page")
        self.assertEqual(withum["source_type"], "manual_page")
        self.assertEqual(withum["support_level"], "manual")
        self.assertEqual(
            withum["url"],
            "https://withum.wd108.myworkdayjobs.com/CR_Career_Site",
        )
        self.assertEqual(withum["packs"], ["finance-quant"])

        heirloom = sources["heirloom_ashby"]
        self.assertEqual(heirloom["board"], "heirloomcarbon")
        self.assertEqual(
            heirloom["api_url"],
            "https://api.ashbyhq.com/posting-api/job-board/heirloomcarbon",
        )

        rise = sources["apl_rise"]
        self.assertEqual(rise["expected_http_statuses"], [403])
        self.assertEqual(rise["support_level"], "manual")
        self.assertTrue({"internship", "research_program"} <= set(rise["opportunity_types"]))
        self.assertTrue({"undergraduate", "graduate", "phd"} <= set(rise["career_levels"]))

        emerging_talent = sources["openai_emerging_talent"]
        self.assertEqual(emerging_talent["expected_http_statuses"], [403])
        self.assertEqual(emerging_talent["verified_at"], "2026-08-21")
        self.assertEqual(emerging_talent["support_level"], "manual")

        nreip = sources["nreip"]
        self.assertEqual(
            nreip["url"],
            "https://www.onr.navy.mil/education-outreach/undergraduate-graduate/nreip-naval-internship",
        )
        self.assertEqual(nreip["support_level"], "manual")

        jpl = sources["jpl_internships"]
        self.assertEqual(jpl["url"], "https://www.jpl.jobs/students-and-postdocs")
        self.assertNotIn("expected_http_statuses", jpl)
        self.assertTrue({"internship", "research_program", "postdoc"} <= set(jpl["opportunity_types"]))
        self.assertTrue({"undergraduate", "graduate", "phd", "postdoc"} <= set(jpl["career_levels"]))

        for source_id in ("citadel_students", "pppl_internships"):
            self.assertEqual(sources[source_id]["expected_http_statuses"], [403], source_id)
            self.assertEqual(sources[source_id]["support_level"], "manual", source_id)
            self.assertEqual(sources[source_id]["verified_at"], "2026-08-20", source_id)

        pppl_feed = sources["pppl_internships_icims"]
        self.assertEqual(pppl_feed["kind"], "html_links")
        self.assertEqual(pppl_feed["source_type"], "listing_feed")
        self.assertEqual(pppl_feed["support_level"], "experimental")
        self.assertEqual(pppl_feed["pages"], 1)
        self.assertEqual(pppl_feed["default_opportunity_type"], "internship")
        self.assertIn("searchKeyword=intern", pppl_feed["url"])
        self.assertIn("/jobs/", pppl_feed["include"])
        self.assertTrue(pppl_feed["same_domain"])

        for source_id in (
            "withum_campus",
            "heirloom_ashby",
            "apl_rise",
            "nreip",
            "jpl_internships",
            "pppl_internships_icims",
        ):
            self.assertFalse(sources[source_id]["enabled"], source_id)
            self.assertEqual(sources[source_id]["verified_at"], "2026-08-20", source_id)

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

    def test_representative_stage_focused_feeds_have_auditable_taxonomy(self):
        sources = {source["id"]: source for source in self.sources}
        self.assertTrue(REPRESENTATIVE_STAGE_FEEDS.keys() <= sources.keys())
        for source_id, expected in REPRESENTATIVE_STAGE_FEEDS.items():
            source = sources[source_id]
            self.assertFalse(source["enabled"], source_id)
            self.assertEqual(source["source_type"], "listing_feed", source_id)
            self.assertEqual(source["support_level"], "supported", source_id)
            self.assertTrue(expected["packs"] <= set(source["packs"]), source_id)
            self.assertTrue(expected["types"] <= set(source["opportunity_types"]), source_id)
            self.assertTrue(expected["levels"] <= set(source["career_levels"]), source_id)

    def test_final_verified_cross_industry_feeds_are_supported_and_opt_in(self):
        sources = {source["id"]: source for source in self.sources}
        self.assertTrue(VERIFIED_2026_08_20_FEEDS.keys() <= sources.keys())
        for source_id, expected in VERIFIED_2026_08_20_FEEDS.items():
            source = sources[source_id]
            self.assertFalse(source["enabled"], source_id)
            self.assertEqual(source["verified_at"], "2026-08-20", source_id)
            self.assertEqual(source["source_type"], "listing_feed", source_id)
            self.assertEqual(source["support_level"], "supported", source_id)
            self.assertEqual(source["kind"], expected["kind"], source_id)
            self.assertTrue(expected["packs"] <= set(source["packs"]), source_id)
            self.assertTrue(expected["types"] <= set(source["opportunity_types"]), source_id)

    def test_jane_street_program_feed_tracks_current_application_index(self):
        source = next(source for source in self.sources if source["id"] == "jane_street_programs")
        self.assertFalse(source["enabled"])
        self.assertEqual(source["verified_at"], "2026-08-20")
        self.assertEqual(source["kind"], "html_links")
        self.assertEqual(source["source_type"], "listing_feed")
        self.assertEqual(source["support_level"], "experimental")
        self.assertEqual(source["default_opportunity_type"], "program")
        self.assertEqual(source["pages"], 1)
        self.assertTrue(source["same_domain"])
        self.assertIn("program-type=accepting-applications", source["url"])
        self.assertIn(
            "/programs-and-events/graduate-research-fellowship",
            source["include"],
        )
        self.assertTrue(
            {"academia-research", "fellowships", "finance-quant"}
            <= set(source["packs"])
        )
        self.assertTrue(
            {"fellowship", "internship", "research_program"}
            <= set(source["opportunity_types"])
        )

    def test_new_domain_packs_have_multiple_structured_opt_in_feeds(self):
        required_counts = {
            "students-early-career": 20,
            "space-aerospace": 3,
            "robotics-autonomy": 5,
            "education-social-impact": 6,
        }
        for pack_id, minimum in required_counts.items():
            feeds = [
                source
                for source in self.sources
                if source["source_type"] == "listing_feed" and pack_id in source["packs"]
            ]
            self.assertGreaterEqual(len(feeds), minimum, pack_id)
            self.assertTrue(all(not source["enabled"] for source in feeds), pack_id)

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
        source_urls = [source["url"].rstrip("/") for source in self.sources]
        self.assertEqual(len(source_ids), len(set(source_ids)))
        self.assertEqual(len(source_urls), len(set(source_urls)))
        self.assertGreaterEqual(len(self.sources), 230)

        for source in self.sources:
            self.assertRegex(source["id"], r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
            self.assertTrue(source["official"], source["id"])
            self.assertIsInstance(source["enabled"], bool, source["id"])
            self.assertIn(
                source["support_level"],
                {"supported", "experimental", "manual"},
                source["id"],
            )
            self.assertIn(
                source["source_type"],
                {"listing_feed", "official_portal", "manual_page", "program_calendar"},
                source["id"],
            )
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
            self.assertFalse(source["auto_enable"], source["id"])
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
        self.assertFalse(REQUIRED_PACK_IDS - feed_packs)
        self.assertIn("national-labs", feed_packs)
        self.assertIn("national-labs", manual_packs)

    def test_finance_pack_removal_disables_finance_employers_but_keeps_programs(self):
        finance_employers = {
            "point72_greenhouse",
            "xtx_greenhouse",
            "zinnia_internships_greenhouse",
            "withum_campus",
            "binance_lever",
            "coinbase_greenhouse",
            "jump_trading_greenhouse",
            "hrt_openings",
            "jane_street_students",
            "citadel_students",
            "deshaw_students",
        }
        sources = {source["id"]: source for source in self.sources}
        for source_id in finance_employers:
            self.assertEqual(
                [pack for pack in sources[source_id]["packs"] if pack != "cross-industry"],
                ["finance-quant"], source_id,
            )

        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory) / "sources.local.json"
            local.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "coverage_preset": "manual",
                        "selected_packs": [
                            "engineering",
                            "data-software",
                            "academia-research",
                            "fellowships",
                            "students-early-career",
                        ],
                        "sources": [],
                    }
                ),
                encoding="utf-8",
            )
            with patch("monitor.config.source_files", return_value=[SOURCES_PATH, local]):
                enabled = {source["id"] for source in config.load_sources()}

        self.assertFalse(finance_employers.intersection(enabled))
        self.assertIn("jane_street_programs", enabled)


if __name__ == "__main__":
    unittest.main()
