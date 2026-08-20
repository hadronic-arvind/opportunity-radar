"""Validated, atomic management of private opportunity sources."""

import json
import os
import re
import urllib.parse
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from . import config
from .fetchers import MAX_HTML_LINK_PAGES, _remote_url_parts
from .pipeline import exclusive_lock
from .profile import (
    ProfileValidationError,
    _ensure_local_writes_are_effective,
    _existing_local_payload,
    _restore_local_configuration,
    _stage_json,
    profile_lifecycle_lock,
    refresh_profile_state,
)
from .text import normalize_opportunity_type


SOURCE_ID_RE = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,199}$")
SUPPORTED_KINDS = {
    "ashby",
    "greenhouse",
    "html_links",
    "jibe",
    "lever",
    "watch_page",
}
CUSTOM_PACK_ID = "custom-sources"
CUSTOM_PACK = {
    "id": CUSTOM_PACK_ID,
    "name": "My sources",
    "description": "Companies and programs added privately on this device.",
}
DEFAULT_LINK_INCLUDES = [
    "/apply",
    "/career",
    "/fellow",
    "/intern",
    "/job",
    "/opportunit",
    "/position",
    "/role",
]
DEFAULT_LINK_EXCLUDES = [
    "#",
    "/blog",
    "/cookie",
    "/privacy",
    "/signin",
    "/terms",
    "mailto:",
]


def _bounded_strings(
    value: Any,
    label: str,
    limit: int = 64,
    item_limit: int = 160,
) -> List[str]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise ProfileValidationError("{} must be a list".format(label))
    result: List[str] = []
    seen = set()
    for entry in value:
        if not isinstance(entry, str):
            raise ProfileValidationError("{} must contain strings".format(label))
        clean = entry.strip()
        key = clean.casefold()
        if not clean or len(clean) > item_limit:
            raise ProfileValidationError("{} contains an invalid value".format(label))
        if key not in seen:
            seen.add(key)
            result.append(clean)
    if len(result) > limit:
        raise ProfileValidationError("{} contains too many values".format(label))
    return result


def _https_url(value: Any, label: str) -> str:
    text = str(value or "").strip()
    try:
        validated, _host, _port = _remote_url_parts(text)
    except ValueError as error:
        raise ProfileValidationError("{} must be a public HTTPS URL".format(label)) from error
    if len(validated) > 2000:
        raise ProfileValidationError("{} is too long".format(label))
    return validated


def _bounded_integer(value: Any, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProfileValidationError("{} must be an integer".format(label))
    if value < minimum or value > maximum:
        raise ProfileValidationError(
            "{} must be from {} to {}".format(label, minimum, maximum)
        )
    return value


def validate_source(source: Any) -> Dict[str, Any]:
    """Validate one complete source object before it reaches a scan."""
    if not isinstance(source, dict):
        raise ProfileValidationError("Source must be an object")
    result = deepcopy(source)
    source_id = str(result.get("id", "")).strip()
    if len(source_id) > 100 or not SOURCE_ID_RE.fullmatch(source_id):
        raise ProfileValidationError("Source id must use lowercase words separated by underscores")
    name = str(result.get("name", "")).strip()
    if not name or len(name) > 160:
        raise ProfileValidationError("Source name must contain 1 to 160 characters")
    kind = str(result.get("kind", "")).strip().casefold()
    if kind not in SUPPORTED_KINDS:
        raise ProfileValidationError("Unsupported source kind: {}".format(kind or "missing"))
    result.update({"id": source_id, "name": name, "kind": kind})
    result["url"] = _https_url(result.get("url"), "Source URL")

    if "enabled" in result and not isinstance(result["enabled"], bool):
        raise ProfileValidationError("Source enabled value must be true or false")
    result["enabled"] = bool(result.get("enabled", True))
    result["cadence_hours"] = _bounded_integer(
        result.get("cadence_hours", 12), "Source cadence", 1, 24 * 31
    )
    result["packs"] = _bounded_strings(result.get("packs", []), "Source packs")
    for key in (
        "career_levels",
        "domains",
        "item_exclude",
        "item_include",
        "opportunity_types",
        "regions",
    ):
        if key in result:
            result[key] = _bounded_strings(result[key], key)
    if "default_opportunity_type" in result:
        default_type = normalize_opportunity_type(result["default_opportunity_type"])
        if not default_type:
            raise ProfileValidationError("default_opportunity_type is invalid")
        result["default_opportunity_type"] = default_type
    if "item_filter_scope" in result and result["item_filter_scope"] not in {"title", "full"}:
        raise ProfileValidationError("item_filter_scope must be title or full")
    statuses = result.get("expected_http_statuses", [])
    if statuses:
        if not isinstance(statuses, list) or any(
            isinstance(value, bool) or not isinstance(value, int) or value < 100 or value > 599
            for value in statuses
        ):
            raise ProfileValidationError("expected_http_statuses must contain HTTP status integers")
        result["expected_http_statuses"] = list(dict.fromkeys(statuses))[:20]

    adapter_key = {
        "ashby": "board",
        "greenhouse": "board",
        "lever": "site",
    }.get(kind)
    if adapter_key:
        adapter_value = str(result.get(adapter_key, "")).strip()
        if not SLUG_RE.fullmatch(adapter_value):
            raise ProfileValidationError(
                "{} source requires a valid {}".format(kind, adapter_key)
            )
        result[adapter_key] = adapter_value
    if kind == "jibe":
        result["api_url"] = _https_url(result.get("api_url"), "Jibe API URL")
        template = str(result.get("job_url_template", "")).strip()
        if template:
            _https_url(
                template.replace("{slug}", "opportunity-slug").replace(
                    "{req_id}", "opportunity-request"
                ),
                "Jibe job URL template",
            )
            result["job_url_template"] = template
    if kind == "html_links":
        result["pages"] = _bounded_integer(
            result.get("pages", 1), "HTML page count", 1, MAX_HTML_LINK_PAGES
        )
        result["include"] = _bounded_strings(result.get("include", []), "HTML include terms")
        result["exclude"] = _bounded_strings(result.get("exclude", []), "HTML exclude terms")
        if "same_domain" in result and not isinstance(result["same_domain"], bool):
            raise ProfileValidationError("same_domain must be true or false")
        result["same_domain"] = bool(result.get("same_domain", True))
        if result.get("link_base_url"):
            result["link_base_url"] = _https_url(result["link_base_url"], "Link base URL")
    if kind == "watch_page":
        if "publish_as_opportunity" in result and not isinstance(
            result["publish_as_opportunity"], bool
        ):
            raise ProfileValidationError("publish_as_opportunity must be true or false")
        if result.get("watch_title"):
            result["watch_title"] = str(result["watch_title"]).strip()[:160]

    support = str(result.get("support_level", "supported")).strip().casefold()
    if support not in {"supported", "experimental", "manual"}:
        raise ProfileValidationError("support_level must be supported, experimental, or manual")
    result["support_level"] = support
    result["source_type"] = str(
        result.get(
            "source_type",
            "change_monitor" if kind == "watch_page" else "listing_feed",
        )
    ).strip()[:80]
    return result


def _public_registry() -> Dict[str, Any]:
    path = config.PROJECT_ROOT / "config" / "sources.json"
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ProfileValidationError("Public source catalog is invalid")
    return payload


def public_source_ids() -> set:
    return {
        str(source.get("id"))
        for source in _public_registry().get("sources", [])
        if isinstance(source, dict) and source.get("id")
    }


def _local_registry() -> Tuple[Path, Dict[str, Any]]:
    path = config.local_sources_path()
    payload = _existing_local_payload(path, "sources.local.json")
    if not isinstance(payload, dict):
        raise ProfileValidationError("Local source configuration must be an object")
    payload = deepcopy(payload)
    payload.setdefault("schema_version", 2)
    payload.setdefault("packs", [])
    payload.setdefault("sources", [])
    if not isinstance(payload["packs"], list) or not isinstance(payload["sources"], list):
        raise ProfileValidationError("Local source configuration has invalid lists")
    return path, payload


def _ensure_custom_pack(payload: Dict[str, Any]) -> None:
    if any(
        isinstance(pack, dict) and str(pack.get("id")) == CUSTOM_PACK_ID
        for pack in payload["packs"]
    ):
        return
    payload["packs"].append(deepcopy(CUSTOM_PACK))


def _validate_registry(payload: Dict[str, Any]) -> Dict[str, Any]:
    result = deepcopy(payload)
    result["schema_version"] = max(2, int(result.get("schema_version", 2)))
    if not isinstance(result.get("packs", []), list):
        raise ProfileValidationError("Local source packs must be a list")
    if not isinstance(result.get("sources", []), list):
        raise ProfileValidationError("Local sources must be a list")
    seen = set()
    public_ids = public_source_ids()
    entries = []
    for entry in result.get("sources", []):
        if not isinstance(entry, dict) or not str(entry.get("id", "")).strip():
            raise ProfileValidationError("Every local source entry needs an id")
        source_id = str(entry["id"])
        if source_id in seen:
            raise ProfileValidationError("Duplicate local source id: {}".format(source_id))
        seen.add(source_id)
        complete = all(str(entry.get(key, "")).strip() for key in ("name", "kind", "url"))
        if complete:
            entries.append(validate_source(entry))
        elif source_id in public_ids and set(entry).issubset({"id", "enabled"}):
            if "enabled" not in entry or not isinstance(entry["enabled"], bool):
                raise ProfileValidationError("Public source override needs a boolean enabled value")
            entries.append({"id": source_id, "enabled": entry["enabled"]})
        else:
            raise ProfileValidationError("Local source {} is incomplete".format(source_id))
    result["sources"] = entries
    return result


def _write_registry(
    mutator: Any,
    dry_run: bool = False,
    rebuild: bool = True,
) -> Dict[str, Any]:
    _ensure_local_writes_are_effective()
    database_path = config.resolve_private_state_path(
        config.project_path("data", "opportunities.sqlite3"),
        "data",
        "opportunities.sqlite3",
    )
    with profile_lifecycle_lock():
        with exclusive_lock(database_path.with_name("scan.lock")):
            path, current = _local_registry()
            updated, result = mutator(deepcopy(current))
            updated = _validate_registry(updated)
            if dry_run:
                return {**result, "saved": False, "status": "valid", "path": str(path)}
            previous = path.read_bytes() if path.exists() else None
            staged = _stage_json(path, updated)
            try:
                os.replace(staged, path)
                os.chmod(path, 0o600)
                descriptor = os.open(str(path.parent), os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                refresh = refresh_profile_state() if rebuild else {}
            except Exception:
                _restore_local_configuration({path: previous})
                raise
            finally:
                if staged.exists():
                    staged.unlink()
    return {**result, **refresh, "saved": True, "status": "saved", "path": str(path)}


def _slug(value: str) -> str:
    slug = re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", value.casefold())).strip("_")
    return slug[:100]


def detect_source(url: str) -> Tuple[str, Dict[str, str]]:
    """Recognize safe public ATS URLs, otherwise use bounded HTML links."""
    validated = _https_url(url, "Source URL")
    parsed = urllib.parse.urlsplit(validated)
    host = (parsed.hostname or "").casefold()
    parts = [urllib.parse.unquote(value) for value in parsed.path.split("/") if value]
    if host in {"boards.greenhouse.io", "job-boards.greenhouse.io"} and parts:
        return "greenhouse", {"board": parts[0]}
    if host == "jobs.lever.co" and parts:
        return "lever", {"site": parts[0]}
    if host == "jobs.ashbyhq.com" and parts:
        return "ashby", {"board": parts[0]}
    return "html_links", {}


def build_custom_source(
    name: str,
    url: str,
    source_id: str = "",
    kind: str = "auto",
    adapter: str = "",
    packs: Optional[Sequence[str]] = None,
    cadence_hours: int = 12,
) -> Dict[str, Any]:
    clean_name = str(name or "").strip()
    clean_url = _https_url(url, "Source URL")
    detected_kind, detected = detect_source(clean_url)
    selected_kind = detected_kind if kind == "auto" else str(kind).casefold()
    if selected_kind not in SUPPORTED_KINDS - {"jibe"}:
        raise ProfileValidationError("Unsupported source kind for add: {}".format(selected_kind))
    source: Dict[str, Any] = {
        "id": _slug(source_id or clean_name),
        "name": clean_name,
        "kind": selected_kind,
        "url": clean_url,
        "packs": list(packs or [CUSTOM_PACK_ID]),
        "cadence_hours": cadence_hours,
        "enabled": True,
        "official": True,
        "managed_by": "opportunity-radar",
    }
    adapter_key = {
        "ashby": "board",
        "greenhouse": "board",
        "lever": "site",
    }.get(selected_kind)
    if adapter_key:
        value = str(adapter or detected.get(adapter_key, "")).strip()
        if not value:
            raise ProfileValidationError(
                "Paste the provider's jobs URL or provide --adapter for {}".format(adapter_key)
            )
        source[adapter_key] = value
        source["support_level"] = "supported"
        source["source_type"] = "listing_feed"
    elif selected_kind == "html_links":
        source.update(
            {
                "include": list(DEFAULT_LINK_INCLUDES),
                "exclude": list(DEFAULT_LINK_EXCLUDES),
                "pages": 1,
                "same_domain": True,
                "support_level": "experimental",
                "source_type": "listing_feed",
            }
        )
    elif selected_kind == "watch_page":
        source.update(
            {
                "publish_as_opportunity": False,
                "support_level": "manual",
                "source_type": "change_monitor",
            }
        )
    return validate_source(source)


def add_source(source: Dict[str, Any], dry_run: bool = False) -> Dict[str, Any]:
    validated = validate_source(source)
    known_packs = {
        str(pack.get("id"))
        for pack in config.load_source_packs()
        if isinstance(pack, dict) and pack.get("id")
    } | {CUSTOM_PACK_ID}
    unknown_packs = sorted(set(validated.get("packs", [])) - known_packs)
    if unknown_packs:
        raise ProfileValidationError(
            "Unknown source pack: {}".format(", ".join(unknown_packs))
        )

    def mutate(payload: Dict[str, Any]):
        ids = {str(entry.get("id")) for entry in config.load_sources(include_disabled=True)}
        ids.update(str(entry.get("id")) for entry in payload["sources"] if isinstance(entry, dict))
        if validated["id"] in ids:
            raise ProfileValidationError("Source already exists: {}".format(validated["id"]))
        _ensure_custom_pack(payload)
        payload["sources"].append(validated)
        return payload, {"action": "added", "source": validated}

    return _write_registry(mutate, dry_run=dry_run)


def set_source_enabled(source_id: str, enabled: bool, dry_run: bool = False) -> Dict[str, Any]:
    source_id = str(source_id or "").strip()
    if not SOURCE_ID_RE.fullmatch(source_id):
        raise ProfileValidationError("Invalid source id")

    def mutate(payload: Dict[str, Any]):
        known = {str(source["id"]) for source in config.load_sources(include_disabled=True)}
        if source_id not in known:
            raise ProfileValidationError("Unknown source: {}".format(source_id))
        for entry in payload["sources"]:
            if isinstance(entry, dict) and str(entry.get("id")) == source_id:
                entry["enabled"] = bool(enabled)
                break
        else:
            payload["sources"].append({"id": source_id, "enabled": bool(enabled)})
        return payload, {
            "action": "enabled" if enabled else "disabled",
            "source_id": source_id,
        }

    return _write_registry(mutate, dry_run=dry_run)


def remove_source(source_id: str, dry_run: bool = False) -> Dict[str, Any]:
    source_id = str(source_id or "").strip()
    if source_id in public_source_ids():
        raise ProfileValidationError("Built-in sources can be disabled but not removed")

    def mutate(payload: Dict[str, Any]):
        before = len(payload["sources"])
        payload["sources"] = [
            entry
            for entry in payload["sources"]
            if not isinstance(entry, dict) or str(entry.get("id")) != source_id
        ]
        if len(payload["sources"]) == before:
            raise ProfileValidationError("Unknown custom source: {}".format(source_id))
        return payload, {"action": "removed", "source_id": source_id}

    return _write_registry(mutate, dry_run=dry_run)


def source_summary(source_id: str) -> Dict[str, Any]:
    for source in config.load_sources(include_disabled=True):
        if str(source.get("id")) == source_id:
            result = deepcopy(source)
            result["custom"] = source_id not in public_source_ids()
            return result
    raise ProfileValidationError("Unknown source: {}".format(source_id))
