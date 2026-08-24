"""Target-aware source coverage presets shared by onboarding and profiles."""

import re
from typing import Any, Dict, Iterable, List, Mapping, Sequence


AUTOMATIC_PRESET = "automatic"
MANUAL_PRESET = "manual"

PACK_ORDER = [
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
]

COVERAGE_PRESETS = [
    {
        "id": "software-data-ai",
        "name": "Software, data, and AI",
        "description": "Software engineering, data science, AI, machine learning, and computing research.",
        "packs": ["data-software", "engineering", "ai-research", "product-design"],
        "keywords": [
            "software", "developer", "programming", "computer science", "data", "analytics",
            "machine learning", "artificial intelligence", "ai", "ml", "scientific computing",
        ],
    },
    {
        "id": "engineering-robotics",
        "name": "Engineering and robotics",
        "description": "Mechanical, electrical, systems, manufacturing, robotics, and autonomous systems.",
        "packs": ["engineering", "robotics-autonomy", "skilled-technical", "electronics-semiconductors"],
        "keywords": [
            "engineering", "engineer", "robotics", "robot", "autonomy", "autonomous",
            "controls", "mechanical", "mechatronics", "manufacturing", "systems engineering",
        ],
    },
    {
        "id": "mathematics-quant",
        "name": "Mathematics and quantitative science",
        "description": "Mathematics, statistics, operations research, actuarial work, and quantitative finance.",
        "packs": ["mathematical-sciences", "finance-quant", "data-software", "academia-research"],
        "keywords": [
            "mathematics", "math", "statistics", "statistician", "probability", "actuarial",
            "operations research", "quantitative", "quant", "optimization",
        ],
    },
    {
        "id": "physics-space",
        "name": "Physics, quantum, and space",
        "description": "Physics, astronomy, quantum technology, space science, and national laboratories.",
        "packs": [
            "physics-quantum-astronomy", "space-aerospace", "national-labs",
            "academia-research", "engineering",
        ],
        "keywords": [
            "physics", "physicist", "quantum", "astronomy", "astrophysics", "cosmology",
            "particle", "nuclear", "optics", "photonics", "space", "aerospace",
        ],
    },
    {
        "id": "chemistry-materials",
        "name": "Chemistry and materials",
        "description": "Chemistry, materials science, polymers, nanotechnology, and chemical engineering.",
        "packs": ["chemistry-materials", "academia-research", "engineering", "pharma-drug-discovery"],
        "keywords": [
            "chemistry", "chemist", "chemical", "materials", "polymer", "nanotechnology",
            "nanomaterials", "catalysis", "spectroscopy",
        ],
    },
    {
        "id": "earth-climate-environment",
        "name": "Earth, climate, and environment",
        "description": "Earth science, climate, energy, ecology, environment, and geospatial work.",
        "packs": [
            "earth-geospatial", "ecology-environment", "climate-energy",
            "public-interest", "academia-research",
        ],
        "keywords": [
            "earth science", "geoscience", "geology", "geospatial", "gis", "remote sensing",
            "climate", "energy", "environment", "environmental", "ecology", "conservation",
            "hydrology", "meteorology", "atmospheric",
        ],
    },
    {
        "id": "life-sciences",
        "name": "Biology and life sciences",
        "description": "Biology, biotechnology, genomics, laboratory science, and translational research.",
        "packs": [
            "biotech-health", "academia-research", "biomedical-neuroscience",
            "pharma-drug-discovery",
        ],
        "keywords": [
            "biology", "biologist", "biotech", "biotechnology", "genomics", "genetics",
            "molecular", "cell biology", "microbiology", "immunology", "wet lab", "laboratory",
            "translational", "bioinformatics", "computational biology",
        ],
    },
    {
        "id": "medicine-health",
        "name": "Medicine and health",
        "description": "Clinical care, clinical research, healthcare, medical technology, and health data.",
        "packs": [
            "medicine-clinical", "biotech-health", "public-health",
            "pharma-drug-discovery", "biomedical-neuroscience", "academia-research",
        ],
        "keywords": [
            "medicine", "medical", "clinical", "clinician", "physician", "doctor", "nursing",
            "nurse", "healthcare", "health care", "hospital", "patient", "residency",
            "medical device", "allied health",
        ],
    },
    {
        "id": "pharma-drug-discovery",
        "name": "Pharma and drug discovery",
        "description": "Pharmaceutical science, therapeutics, drug discovery, trials, and regulatory science.",
        "packs": [
            "pharma-drug-discovery", "biotech-health", "medicine-clinical",
            "chemistry-materials", "biomedical-neuroscience",
        ],
        "keywords": [
            "pharma", "pharmaceutical", "drug discovery", "therapeutics", "clinical trial",
            "pharmacology", "toxicology", "medicinal chemistry", "regulatory science",
        ],
    },
    {
        "id": "public-health",
        "name": "Public health and epidemiology",
        "description": "Public health, epidemiology, biostatistics, population health, and health policy.",
        "packs": [
            "public-health", "medicine-clinical", "biotech-health",
            "public-interest", "mathematical-sciences",
        ],
        "keywords": [
            "public health", "epidemiology", "epidemiologist", "biostatistics", "population health",
            "global health", "health policy", "health equity", "infectious disease",
        ],
    },
    {
        "id": "agriculture-food",
        "name": "Agriculture and food science",
        "description": "Agriculture, plant science, soil science, agronomy, and food technology.",
        "packs": [
            "agriculture-food", "biotech-health", "chemistry-materials",
            "ecology-environment", "academia-research",
        ],
        "keywords": [
            "agriculture", "agricultural", "agronomy", "crop", "plant science", "soil",
            "food science", "food technology", "fermentation",
        ],
    },
    {
        "id": "civil-infrastructure",
        "name": "Civil engineering and infrastructure",
        "description": "Civil, structural, transportation, construction, and infrastructure engineering.",
        "packs": ["civil-infrastructure", "engineering", "skilled-technical", "climate-energy"],
        "keywords": [
            "civil engineering", "civil engineer", "structural", "transportation", "infrastructure",
            "construction", "geotechnical", "water resources", "urban planning",
        ],
    },
    {
        "id": "electronics-semiconductors",
        "name": "Electronics and semiconductors",
        "description": "Electrical engineering, electronics, microelectronics, chips, and semiconductor fabrication.",
        "packs": [
            "electronics-semiconductors", "engineering", "skilled-technical",
            "physics-quantum-astronomy",
        ],
        "keywords": [
            "electrical engineering", "electrical engineer", "electronics", "semiconductor",
            "microelectronics", "integrated circuit", "vlsi", "chip design", "fabrication",
        ],
    },
    {
        "id": "ocean-marine",
        "name": "Ocean and marine science",
        "description": "Oceanography, marine biology, fisheries, and ocean engineering.",
        "packs": [
            "ocean-marine", "ecology-environment", "earth-geospatial",
            "academia-research", "engineering",
        ],
        "keywords": [
            "ocean", "oceanography", "marine", "marine biology", "fisheries", "aquatic",
            "coastal", "ocean engineering",
        ],
    },
    {
        "id": "veterinary-animal",
        "name": "Veterinary and animal science",
        "description": "Veterinary medicine, animal health, zoology, and animal science.",
        "packs": [
            "veterinary-animal", "medicine-clinical", "biotech-health",
            "agriculture-food", "academia-research",
        ],
        "keywords": [
            "veterinary", "veterinarian", "animal science", "animal health", "zoology",
            "wildlife biology", "livestock",
        ],
    },
]

TARGET_DEFAULTS = {
    "software-data-ai": {
        "role_families": [
            "software engineer", "data scientist", "machine learning engineer", "research engineer",
        ],
        "domains": ["software", "data science", "artificial intelligence", "machine learning"],
        "supporting_skills": ["programming", "data analysis", "statistics"],
    },
    "engineering-robotics": {
        "role_families": [
            "mechanical engineer", "electrical engineer", "systems engineer", "robotics engineer",
        ],
        "domains": ["engineering", "robotics", "manufacturing", "autonomous systems"],
        "supporting_skills": ["design", "controls", "testing"],
    },
    "mathematics-quant": {
        "role_families": ["mathematician", "statistician", "quantitative analyst", "operations researcher"],
        "domains": ["mathematics", "statistics", "operations research", "quantitative science"],
        "supporting_skills": ["modeling", "optimization", "data analysis"],
    },
    "physics-space": {
        "role_families": ["physicist", "research scientist", "instrumentation engineer", "aerospace engineer"],
        "domains": ["physics", "quantum science", "astronomy", "space science"],
        "supporting_skills": ["scientific computing", "instrumentation", "data analysis"],
    },
    "chemistry-materials": {
        "role_families": ["chemist", "materials scientist", "research associate", "chemical engineer"],
        "domains": ["chemistry", "materials science", "nanotechnology", "chemical engineering"],
        "supporting_skills": ["laboratory", "characterization", "spectroscopy"],
    },
    "earth-climate-environment": {
        "role_families": ["geoscientist", "climate scientist", "environmental scientist", "geospatial analyst"],
        "domains": ["Earth science", "climate", "environment", "geospatial science"],
        "supporting_skills": ["GIS", "remote sensing", "data analysis"],
    },
    "life-sciences": {
        "role_families": ["biologist", "research associate", "laboratory technician", "bioinformatician"],
        "domains": ["biology", "biotechnology", "genomics", "life sciences"],
        "supporting_skills": ["wet lab", "molecular biology", "data analysis"],
    },
    "medicine-health": {
        "role_families": [
            "clinical research coordinator", "research associate", "laboratory technician", "clinical data analyst",
        ],
        "domains": ["medicine", "clinical research", "healthcare", "biomedical science", "neuroscience"],
        "supporting_skills": ["clinical protocols", "wet lab", "data analysis"],
    },
    "pharma-drug-discovery": {
        "role_families": ["research scientist", "research associate", "clinical scientist", "regulatory scientist"],
        "domains": ["pharmaceutical science", "drug discovery", "therapeutics", "clinical trials"],
        "supporting_skills": ["pharmacology", "assay development", "clinical protocols"],
    },
    "public-health": {
        "role_families": ["epidemiologist", "biostatistician", "public health analyst", "research coordinator"],
        "domains": ["public health", "epidemiology", "population health", "global health"],
        "supporting_skills": ["biostatistics", "data analysis", "program evaluation"],
    },
    "agriculture-food": {
        "role_families": ["agricultural scientist", "agronomist", "food scientist", "plant scientist"],
        "domains": ["agriculture", "food science", "plant science", "soil science"],
        "supporting_skills": ["field research", "laboratory", "data analysis"],
    },
    "civil-infrastructure": {
        "role_families": ["civil engineer", "structural engineer", "transportation engineer", "project engineer"],
        "domains": ["civil engineering", "infrastructure", "construction", "transportation"],
        "supporting_skills": ["design", "modeling", "project management"],
    },
    "electronics-semiconductors": {
        "role_families": ["electrical engineer", "hardware engineer", "semiconductor engineer", "process engineer"],
        "domains": ["electronics", "semiconductors", "microelectronics", "electrical engineering"],
        "supporting_skills": ["circuit design", "fabrication", "testing"],
    },
    "ocean-marine": {
        "role_families": ["oceanographer", "marine biologist", "ocean engineer", "fisheries scientist"],
        "domains": ["ocean science", "marine science", "fisheries", "coastal science"],
        "supporting_skills": ["field research", "data analysis", "instrumentation"],
    },
    "veterinary-animal": {
        "role_families": ["veterinarian", "veterinary technician", "animal scientist", "wildlife biologist"],
        "domains": ["veterinary medicine", "animal health", "animal science", "zoology"],
        "supporting_skills": ["clinical care", "laboratory", "field research"],
    },
}


def coverage_preset_ids() -> List[str]:
    return [AUTOMATIC_PRESET, MANUAL_PRESET] + [str(item["id"]) for item in COVERAGE_PRESETS]


def validate_coverage_preset(value: Any) -> str:
    preset = str(value or AUTOMATIC_PRESET).strip().casefold()
    if preset not in set(coverage_preset_ids()):
        raise ValueError("Unknown STEM coverage preset: {}".format(preset or "missing"))
    return preset


def coverage_preset_payload() -> List[Dict[str, Any]]:
    return [
        {
            "id": str(item["id"]),
            "name": str(item["name"]),
            "description": str(item["description"]),
            "packs": list(item["packs"]),
            "target_defaults": target_defaults_for_preset(str(item["id"])),
        }
        for item in COVERAGE_PRESETS
    ]


def target_defaults_for_preset(preset: str) -> Dict[str, List[str]]:
    selected = validate_coverage_preset(preset)
    defaults = TARGET_DEFAULTS.get(selected, {})
    return {
        key: list(defaults.get(key, []))
        for key in ("role_families", "domains", "supporting_skills")
    }


def packs_for_preset(preset: str) -> List[str]:
    selected = validate_coverage_preset(preset)
    if selected in {AUTOMATIC_PRESET, MANUAL_PRESET}:
        return []
    entry = next(item for item in COVERAGE_PRESETS if item["id"] == selected)
    return _ordered_unique(entry["packs"])


def recommend_source_packs(profile: Mapping[str, Any]) -> List[str]:
    signals = _profile_signals(profile)
    selected = ["starter-diverse"]
    for preset in COVERAGE_PRESETS:
        if any(_keyword_matches(keyword, signals) for keyword in preset["keywords"]):
            selected.extend(preset["packs"])

    targets = profile.get("targets", {})
    targets = targets if isinstance(targets, Mapping) else {}
    opportunity_types = {
        _normalize(value) for value in _string_values(targets.get("opportunity_types"))
    }
    if opportunity_types.intersection({"fellowship", "postdoc", "research program", "scholarship"}):
        selected.extend(["academia-research", "fellowships", "national-labs"])

    candidate = profile.get("candidate", {})
    candidate = candidate if isinstance(candidate, Mapping) else {}
    stage = _normalize(candidate.get("current_stage", candidate.get("career_stage", "")))
    early_stage_tokens = {
        "student", "undergraduate", "undergraduate student", "graduate student",
        "masters student", "phd student", "new grad", "early career",
    }
    if stage in early_stage_tokens or _keyword_matches("postbacc", signals):
        selected.extend(["students-early-career", "fellowships"])

    return _ordered_unique(selected)


def effective_source_packs(
    profile: Mapping[str, Any],
    selected_packs: Sequence[str],
    preset: str,
) -> List[str]:
    effective = list(selected_packs)
    chosen = validate_coverage_preset(preset)
    if chosen == AUTOMATIC_PRESET:
        effective.extend(recommend_source_packs(profile))
    elif chosen != MANUAL_PRESET:
        effective.extend(packs_for_preset(chosen))
    return _ordered_unique(effective)


def _profile_signals(profile: Mapping[str, Any]) -> str:
    values: List[str] = []
    targets = profile.get("targets", {})
    if isinstance(targets, Mapping):
        for key in ("domains", "role_families", "roles", "supporting_skills", "skills"):
            values.extend(_string_values(targets.get(key)))
    candidate = profile.get("candidate", {})
    if isinstance(candidate, Mapping):
        for key in ("completed_degrees", "skills", "current_stage", "career_stage"):
            values.extend(_string_values(candidate.get(key)))
    matching = profile.get("matching", {})
    if isinstance(matching, Mapping):
        rules = matching.get("rules", [])
        if isinstance(rules, list):
            for rule in rules:
                if not isinstance(rule, Mapping):
                    continue
                weight = rule.get("weight", 0)
                if isinstance(weight, (int, float)) and not isinstance(weight, bool) and weight < 0:
                    continue
                values.extend(_string_values(rule.get("terms")))
                values.extend(_string_values(rule.get("label")))
    return " ".join(_normalize(value) for value in values if _normalize(value))


def _keyword_matches(keyword: str, signals: str) -> bool:
    clean = _normalize(keyword)
    if not clean or not signals:
        return False
    if " " in clean:
        return clean in signals
    return re.search(r"(?:^|\s){}(?:$|\s)".format(re.escape(clean)), signals) is not None


def _normalize(value: Any) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(value or "").casefold()))


def _string_values(value: Any) -> List[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Iterable) and not isinstance(value, (bytes, Mapping)):
        return [str(item) for item in value if str(item).strip()]
    return []


def _ordered_unique(values: Iterable[str]) -> List[str]:
    chosen = {str(value) for value in values if str(value).strip()}
    ordered = [pack for pack in PACK_ORDER if pack in chosen]
    ordered.extend(sorted(chosen - set(ordered)))
    return ordered
