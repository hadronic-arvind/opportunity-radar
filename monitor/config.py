"""Layered configuration loading with no third-party dependencies."""

import json
import os
import stat
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRIVATE_RUNTIME_MARKERS = (
    Path("monitor/__main__.py"),
    Path("config/profile.json"),
    Path("dashboard/template.html"),
    Path("dashboard/styles.css"),
    Path("dashboard/app.js"),
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
    )
    return resolved.parent.parent


def local_profile_path() -> Path:
    return local_configuration_root() / "config" / "profile.local.json"


def local_sources_path() -> Path:
    return local_configuration_root() / "config" / "sources.local.json"


def _local_layer(active: Path, repository: Path) -> Optional[Path]:
    """Prefer canonical runtime state, with one-way legacy migration fallback."""
    if active.exists() or active.is_symlink():
        return active
    if active != repository and (repository.exists() or repository.is_symlink()):
        return repository
    return None


def profile_files() -> List[Path]:
    """Return profile layers in increasing precedence order."""
    files = [PROJECT_ROOT / "config" / "profile.json"]
    repository_local = PROJECT_ROOT / "config" / "profile.local.json"
    local = _local_layer(local_profile_path(), repository_local)
    if local is not None:
        files.append(local)
    override = _environment_path(("OPPORTUNITY_RADAR_PROFILE", "OPPORTUNITY_MONITOR_PROFILE"))
    if override:
        files.append(override)
    return files


def load_profile() -> Dict[str, Any]:
    profile: Dict[str, Any] = {}
    for path in profile_files():
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
    repository_local = PROJECT_ROOT / "config" / "sources.local.json"
    local = _local_layer(local_sources_path(), repository_local)
    if local is not None:
        files.append(local)
    override = _environment_path(("OPPORTUNITY_RADAR_SOURCES", "OPPORTUNITY_MONITOR_SOURCES"))
    if override:
        files.append(override)
    return files


def load_sources(include_disabled: bool = False) -> List[Dict[str, Any]]:
    payloads = _source_payloads()
    sources = _merge_sources(payloads)
    selection_index = None
    selected_packs: List[str] = []
    for index, payload in enumerate(payloads):
        if "selected_packs" not in payload:
            continue
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
        chosen = set(selected_packs)
        for source in sources:
            packs = {str(value) for value in source.get("packs", [])}
            if packs:
                source["enabled"] = bool(chosen.intersection(packs))
            if str(source["id"]) in explicit_enabled:
                source["enabled"] = explicit_enabled[str(source["id"])]
    return sources if include_disabled else [source for source in sources if source.get("enabled", True)]


def _source_payloads() -> List[Dict[str, Any]]:
    payloads: List[Dict[str, Any]] = []
    for path in source_files():
        if not path.is_file():
            raise FileNotFoundError("Source configuration not found: {}".format(path))
        payload = _load_json(path)
        if not isinstance(payload, dict):
            raise ValueError("Source configuration must be a JSON object: {}".format(path))
        payloads.append(payload)
    return payloads


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


def resolve_private_state_path(configured: Path, *relative_parts: str) -> Path:
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
    _require_private_owned(target.parent, "Private runtime state directory", directory=True)
    if target.exists() or target.is_symlink():
        _require_private_owned(target, "Private runtime state file")
    return target
