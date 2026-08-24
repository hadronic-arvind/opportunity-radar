"""Layered configuration loading with no third-party dependencies."""

import json
import os
import re
import stat
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import __version__
from .coverage import (
    AUTOMATIC_PRESET,
    MANUAL_PRESET,
    effective_source_packs,
    validate_coverage_preset,
)
from .database import SCHEMA_VERSION


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRIVATE_RUNTIME_MARKERS = (
    Path("monitor/__main__.py"),
    Path("config/profile.json"),
    Path("dashboard/template.html"),
    Path("dashboard/styles.css"),
    Path("dashboard/app.js"),
)
RUNTIME_INSTALL_OWNER = "installer"
RUNTIME_LIFECYCLE_OWNER_ENV = "OPPORTUNITY_RADAR_LIFECYCLE_OWNER"
MAX_RUNTIME_CONTRACT_BYTES = 512 * 1024
PROFILE_STORE_SCHEMA_VERSION = 1
MAX_PROFILE_STORE_BYTES = 16 * 1024 * 1024
MAX_PROFILE_ENTRY_CONFIG_BYTES = 2 * 1024 * 1024
MAX_SAVED_PROFILES = 32
PROFILE_ID_PATTERN = re.compile(r"^(?:legacy|p_[a-f0-9]{32})$")
RUNTIME_VERSION_PATTERN = re.compile(
    r'^__version__\s*=\s*["\']([^"\']+)["\']\s*$',
    re.MULTILINE,
)
RUNTIME_SCHEMA_PATTERN = re.compile(
    r"^SCHEMA_VERSION\s*=\s*(\d+)\s*$",
    re.MULTILINE,
)


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _environment_path(names: Iterable[str]) -> Optional[Path]:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return Path(value).expanduser()
    return None


def local_configuration_root() -> Path:
    """Return the canonical root for ignored user configuration.

    A scheduler installation keeps the live database in its recognized private
    runtime and links the repository database path to it.  Use that same
    validated runtime for local profile and source settings so the clone CLI,
    native app, and scheduled process do not drift onto separate copies.
    """
    database = PROJECT_ROOT / "data" / "opportunities.sqlite3"
    if not database.is_symlink():
        return PROJECT_ROOT
    resolved = resolve_private_state_path(
        database,
        "data",
        "opportunities.sqlite3",
        require_compatible=False,
    )
    return resolved.parent.parent


def local_profile_path() -> Path:
    return local_configuration_root() / "config" / "profile.local.json"


def local_sources_path() -> Path:
    return local_configuration_root() / "config" / "sources.local.json"


def local_profile_store_path() -> Path:
    """Return the canonical owner-only named-profile store."""
    return local_configuration_root() / "config" / "profiles.local.json"


def _local_layer(active: Path, repository: Path) -> Optional[Path]:
    """Prefer canonical runtime state, with one-way legacy migration fallback."""
    if active.exists() or active.is_symlink():
        return active
    if active != repository and (repository.exists() or repository.is_symlink()):
        return repository
    return None


def _profile_store_read_path() -> Optional[Path]:
    repository = PROJECT_ROOT / "config" / "profiles.local.json"
    return _local_layer(local_profile_store_path(), repository)


def _profile_name(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("Saved profile name must be text")
    cleaned = " ".join(value.strip().split())
    if not cleaned or len(cleaned) > 80:
        raise ValueError("Saved profile name must contain 1 to 80 characters")
    if any(ord(character) < 32 or ord(character) == 127 for character in cleaned):
        raise ValueError("Saved profile name contains control characters")
    if cleaned != value:
        raise ValueError("Saved profile name is not normalized")
    return cleaned


def validate_profile_store(payload: Any) -> Dict[str, Any]:
    """Validate the bounded on-disk named-profile envelope."""
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "active_profile_id",
        "profiles",
    }:
        raise ValueError("Saved profile store has an unsupported structure")
    version = payload["schema_version"]
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or version != PROFILE_STORE_SCHEMA_VERSION
    ):
        raise ValueError("Saved profile store has an unsupported version")
    active_id = payload["active_profile_id"]
    if not isinstance(active_id, str) or not PROFILE_ID_PATTERN.fullmatch(active_id):
        raise ValueError("Saved profile store has an invalid active profile id")
    entries = payload["profiles"]
    if (
        not isinstance(entries, list)
        or not entries
        or len(entries) > MAX_SAVED_PROFILES
    ):
        raise ValueError("Saved profile store must contain a bounded profile list")
    identifiers = set()
    names = set()
    normalized = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {
            "id",
            "name",
            "profile",
            "sources",
        }:
            raise ValueError("Saved profile entry has an unsupported structure")
        profile_id = entry["id"]
        if not isinstance(profile_id, str) or not PROFILE_ID_PATTERN.fullmatch(profile_id):
            raise ValueError("Saved profile entry has an invalid id")
        name = _profile_name(entry["name"])
        if profile_id in identifiers:
            raise ValueError("Saved profile store contains a duplicate id")
        name_key = name.casefold()
        if name_key in names:
            raise ValueError("Saved profile names must be unique")
        if not isinstance(entry["profile"], dict) or not isinstance(entry["sources"], dict):
            raise ValueError("Saved profile configuration must use JSON objects")
        try:
            object_sizes = [
                len(
                    json.dumps(
                        entry[key],
                        ensure_ascii=False,
                        sort_keys=True,
                        allow_nan=False,
                    ).encode("utf-8")
                )
                for key in ("profile", "sources")
            ]
        except (TypeError, ValueError) as error:
            raise ValueError("Saved profile configuration is not valid JSON") from error
        if any(size > MAX_PROFILE_ENTRY_CONFIG_BYTES for size in object_sizes):
            raise ValueError("Saved profile configuration is too large")
        identifiers.add(profile_id)
        names.add(name_key)
        normalized.append(
            {
                "id": profile_id,
                "name": name,
                "profile": entry["profile"],
                "sources": entry["sources"],
            }
        )
    if active_id not in identifiers:
        raise ValueError("Saved profile store does not contain its active profile")
    return {
        "schema_version": PROFILE_STORE_SCHEMA_VERSION,
        "active_profile_id": active_id,
        "profiles": normalized,
    }


def load_profile_store() -> Optional[Dict[str, Any]]:
    """Load the canonical named-profile store, or None for legacy installs."""
    path = _profile_store_read_path()
    if path is None:
        return None
    _require_private_owned(path, "Saved profile store")
    try:
        if path.stat().st_size > MAX_PROFILE_STORE_BYTES:
            raise ValueError("Saved profile store is too large")
        payload = _load_json(path)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Saved profile store is not valid JSON") from error
    return validate_profile_store(payload)


def active_profile_entry(
    store: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Return the selected stored entry, or None while using legacy files."""
    selected_store = load_profile_store() if store is None else validate_profile_store(store)
    if selected_store is None:
        return None
    active_id = selected_store["active_profile_id"]
    return next(
        entry for entry in selected_store["profiles"] if entry["id"] == active_id
    )


def active_profile_id() -> str:
    store = load_profile_store()
    return str(store["active_profile_id"]) if store is not None else "legacy"


def _legacy_profile_layer() -> Optional[Path]:
    return _local_layer(
        local_profile_path(),
        PROJECT_ROOT / "config" / "profile.local.json",
    )


def _legacy_source_layer() -> Optional[Path]:
    return _local_layer(
        local_sources_path(),
        PROJECT_ROOT / "config" / "sources.local.json",
    )


def active_local_payloads() -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Return copies of the active raw local profile and source overlays."""
    entry = active_profile_entry()
    if entry is not None:
        # A JSON round trip is unnecessary because every store load already
        # returns fresh objects. Copy the top-level objects so callers cannot
        # accidentally replace the entry's mappings in place.
        return dict(entry["profile"]), dict(entry["sources"])
    payloads: List[Dict[str, Any]] = []
    for path, label in (
        (_legacy_profile_layer(), "Profile"),
        (_legacy_source_layer(), "Source"),
    ):
        if path is None:
            payloads.append({})
            continue
        if not path.is_file():
            raise FileNotFoundError("{} configuration not found: {}".format(label, path))
        payload = _load_json(path)
        if not isinstance(payload, dict):
            raise ValueError("{} configuration must be a JSON object: {}".format(label, path))
        payloads.append(payload)
    return payloads[0], payloads[1]


def profile_files() -> List[Path]:
    """Return profile layers in increasing precedence order."""
    files = [PROJECT_ROOT / "config" / "profile.json"]
    local = _profile_store_read_path() or _legacy_profile_layer()
    if local is not None:
        files.append(local)
    override = _environment_path(("OPPORTUNITY_RADAR_PROFILE", "OPPORTUNITY_MONITOR_PROFILE"))
    if override:
        files.append(override)
    return files


def load_profile() -> Dict[str, Any]:
    profile: Dict[str, Any] = {}
    store_path = _profile_store_read_path()
    for path in profile_files():
        if store_path is not None and path == store_path:
            entry = active_profile_entry()
            if entry is None:
                raise ValueError("Saved profile store does not have an active profile")
            profile = _deep_merge(profile, entry["profile"])
            continue
        if not path.is_file():
            raise FileNotFoundError("Profile configuration not found: {}".format(path))
        payload = _load_json(path)
        if not isinstance(payload, dict):
            raise ValueError("Profile configuration must be a JSON object: {}".format(path))
        profile = _deep_merge(profile, payload)
    curated_override = ""
    for name in ("OPPORTUNITY_RADAR_CURATED_PATH", "OPPORTUNITY_MONITOR_CURATED_PATH"):
        curated_override = os.environ.get(name, "").strip()
        if curated_override:
            break
    if curated_override:
        profile["curated_pipeline_path"] = curated_override
    return profile


def _merge_sources(payloads: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ordered: List[str] = []
    sources: Dict[str, Dict[str, Any]] = {}
    for layer_index, payload in enumerate(payloads):
        entries = payload.get("sources", [])
        if not isinstance(entries, list):
            raise ValueError("Source configuration must contain a 'sources' list")
        for source in entries:
            if not isinstance(source, dict) or not str(source.get("id", "")).strip():
                raise ValueError("Every source must be an object with a non-empty id")
            source_id = str(source["id"])
            if source_id not in sources:
                # Local registries commonly contain small per-source toggles.
                # If a later public catalog removes one of those sources, the
                # orphaned toggle is not a complete private source and must not
                # survive registration as a malformed listing.
                if layer_index and not all(
                    str(source.get(key, "")).strip() for key in ("name", "kind")
                ):
                    continue
                ordered.append(source_id)
                sources[source_id] = {}
            sources[source_id] = _deep_merge(sources[source_id], source)
    return [sources[source_id] for source_id in ordered]


def _merge_packs(payloads: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ordered: List[str] = []
    packs: Dict[str, Dict[str, Any]] = {}
    for payload in payloads:
        entries = payload.get("packs", [])
        if not isinstance(entries, list):
            raise ValueError("Source configuration 'packs' must be a list")
        for pack in entries:
            if not isinstance(pack, dict) or not str(pack.get("id", "")).strip():
                raise ValueError("Every source pack must be an object with a non-empty id")
            pack_id = str(pack["id"])
            if pack_id not in packs:
                ordered.append(pack_id)
                packs[pack_id] = {}
            packs[pack_id] = _deep_merge(packs[pack_id], pack)
    return [packs[pack_id] for pack_id in ordered]


def source_files() -> List[Path]:
    """Return source-registry layers in increasing precedence order."""
    files = [PROJECT_ROOT / "config" / "sources.json"]
    local = _profile_store_read_path() or _legacy_source_layer()
    if local is not None:
        files.append(local)
    override = _environment_path(("OPPORTUNITY_RADAR_SOURCES", "OPPORTUNITY_MONITOR_SOURCES"))
    if override:
        files.append(override)
    return files


def load_sources(include_disabled: bool = False) -> List[Dict[str, Any]]:
    files = source_files()
    payloads = _source_payloads()
    sources = _merge_sources(payloads)
    selection_index = None
    selected_packs: List[str] = []
    coverage_preset = MANUAL_PRESET
    store_path = _profile_store_read_path()
    named_profile_layer = next(
        (
            index
            for index, path in enumerate(files)
            if store_path is not None and path == store_path
        ),
        None,
    )
    for index, payload in enumerate(payloads):
        if "coverage_preset" in payload:
            coverage_preset = validate_coverage_preset(payload["coverage_preset"])
        if "selected_packs" not in payload:
            continue
        if "coverage_preset" not in payload:
            coverage_preset = (
                AUTOMATIC_PRESET if index == named_profile_layer else MANUAL_PRESET
            )
        raw_selection = payload["selected_packs"]
        if not isinstance(raw_selection, list):
            raise ValueError("Source configuration 'selected_packs' must be a list")
        selected_packs = [str(value) for value in raw_selection if str(value).strip()]
        selection_index = index
    if selection_index is not None:
        known_packs = {str(pack["id"]) for pack in _merge_packs(payloads)}
        unknown = sorted(set(selected_packs) - known_packs)
        if unknown:
            raise ValueError("Unknown selected source pack: {}".format(", ".join(unknown)))
        explicit_enabled: Dict[str, Any] = {}
        # Pack lists replace one another by layer, while source objects merge by
        # id. Preserve every user-layer enabled override even when a higher
        # layer changes only the selected pack list.
        for payload in payloads[1:]:
            for entry in payload.get("sources", []):
                if isinstance(entry, dict) and "enabled" in entry and entry.get("id"):
                    # Preserve the configured type so `monitor doctor` can reject
                    # values such as the string "false" instead of enabling a
                    # source through Python truthiness.
                    explicit_enabled[str(entry["id"])] = entry["enabled"]
        chosen = set(
            effective_source_packs(
                load_profile(),
                selected_packs,
                coverage_preset,
            )
        )
        for source in sources:
            packs = {str(value) for value in source.get("packs", [])}
            if packs:
                source["enabled"] = bool(chosen.intersection(packs)) and bool(
                    source.get("auto_enable", True)
                )
            if str(source["id"]) in explicit_enabled:
                source["enabled"] = explicit_enabled[str(source["id"])]
    return sources if include_disabled else [source for source in sources if source.get("enabled", True)]


def source_payloads() -> List[Dict[str, Any]]:
    """Return public, active local, and environment source layers."""
    store_path = _profile_store_read_path()
    payloads: List[Dict[str, Any]] = []
    for path in source_files():
        if store_path is not None and path == store_path:
            entry = active_profile_entry()
            if entry is None:
                raise ValueError("Saved profile store does not have an active profile")
            payloads.append(entry["sources"])
            continue
        if not path.is_file():
            raise FileNotFoundError("Source configuration not found: {}".format(path))
        payload = _load_json(path)
        if not isinstance(payload, dict):
            raise ValueError("Source configuration must be a JSON object: {}".format(path))
        payloads.append(payload)
    return payloads


def _source_payloads() -> List[Dict[str, Any]]:
    """Backward-compatible private alias for older integrations."""
    return source_payloads()


def load_source_packs() -> List[Dict[str, Any]]:
    """Return named source packs after applying local registry layers."""
    return _merge_packs(_source_payloads())


def resolve_project_value(value: str) -> Path:
    """Resolve a user path relative to the repository when it is not absolute."""
    path = Path(value).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def project_path(*parts: str) -> Path:
    return PROJECT_ROOT.joinpath(*parts)


def _require_private_owned(path: Path, label: str, directory: bool = False) -> None:
    if path.is_symlink():
        raise ValueError("{} must not be a symbolic link".format(label))
    try:
        details = path.stat()
    except OSError as error:
        raise ValueError("{} is unavailable".format(label)) from error
    expected = stat.S_ISDIR(details.st_mode) if directory else stat.S_ISREG(details.st_mode)
    if not expected:
        raise ValueError("{} has an unexpected file type".format(label))
    if details.st_uid != os.getuid() or details.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ValueError("{} is not private to the current user".format(label))


def _runtime_contract_value(path: Path, pattern: re.Pattern, label: str) -> str:
    _require_private_owned(path, label)
    try:
        if path.stat().st_size > MAX_RUNTIME_CONTRACT_BYTES:
            raise ValueError("{} is too large".format(label))
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ValueError("{} is unavailable".format(label)) from error
    match = pattern.search(content)
    if match is None:
        raise ValueError("{} does not declare its compatibility version".format(label))
    return match.group(1)


def _require_compatible_private_runtime(runtime_root: Path) -> None:
    """Refuse to let one checkout upgrade state behind an older runtime."""
    if os.environ.get(RUNTIME_LIFECYCLE_OWNER_ENV) == RUNTIME_INSTALL_OWNER:
        return
    installed_version = _runtime_contract_value(
        runtime_root / "monitor" / "__init__.py",
        RUNTIME_VERSION_PATTERN,
        "Private runtime version marker",
    )
    installed_schema = int(
        _runtime_contract_value(
            runtime_root / "monitor" / "database.py",
            RUNTIME_SCHEMA_PATTERN,
            "Private runtime database marker",
        )
    )
    if installed_version == __version__ and installed_schema == SCHEMA_VERSION:
        return
    raise RuntimeError(
        "The installed private runtime does not match this checkout "
        "(installed version {}, schema {}; checkout version {}, schema {}). "
        "Update this checkout, then run ./scripts/install_launch_agent.sh before using it.".format(
            installed_version,
            installed_schema,
            __version__,
            SCHEMA_VERSION,
        )
    )


def resolve_private_state_path(
    configured: Path,
    *relative_parts: str,
    require_compatible: bool = True,
) -> Path:
    """Follow only an installer-owned state link into a recognized private runtime."""
    if not configured.is_symlink():
        return configured
    link_details = configured.lstat()
    if link_details.st_uid != os.getuid():
        raise ValueError("Generated state link is not owned by the current user")
    target = configured.resolve(strict=False)
    expected_suffix = Path(*relative_parts)
    if len(target.parts) < len(expected_suffix.parts) or target.parts[-len(expected_suffix.parts):] != expected_suffix.parts:
        raise ValueError("Generated state link has an unexpected target")
    runtime_root = target
    for _part in relative_parts:
        runtime_root = runtime_root.parent
    _require_private_owned(runtime_root, "Private runtime", directory=True)
    for marker in PRIVATE_RUNTIME_MARKERS:
        _require_private_owned(runtime_root / marker, "Private runtime marker")
    if require_compatible:
        _require_compatible_private_runtime(runtime_root)
    _require_private_owned(target.parent, "Private runtime state directory", directory=True)
    if target.exists() or target.is_symlink():
        _require_private_owned(target, "Private runtime state file")
    return target
