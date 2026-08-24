"""Render a private, self-contained dashboard that works directly from file://."""

import json
import os
import secrets
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from .config import (
    load_profile,
    load_source_packs,
    load_sources,
    project_path,
    resolve_private_state_path,
)
from .profile import profile_catalog_payload, profile_editor_payload


STYLE_MARKER = "/*__OPPORTUNITY_STYLES__*/"
DATA_MARKER = "/*__OPPORTUNITY_DATA__*/"
APP_MARKER = "/*__OPPORTUNITY_APP__*/"
NONCE_MARKER = "__OPPORTUNITY_NONCE__"
# Backward-compatible export for integrations which imported the old marker.
MARKER = DATA_MARKER
MAX_SOURCE_RESOURCES = 500


def _bounded_setting(value: Any, fallback: str, limit: int) -> str:
    text = " ".join(str(value or fallback).split())
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 3)].rstrip() + "..."


def _bounded_string_list(value: Any, maximum_items: int = 24) -> list:
    if not isinstance(value, list):
        return []
    return [
        _bounded_setting(entry, "", 80)
        for entry in value[:maximum_items]
        if str(entry).strip()
    ]


def _source_resource_directory() -> list:
    resources = []
    for source in load_sources(include_disabled=True)[:MAX_SOURCE_RESOURCES]:
        source_id = _bounded_setting(source.get("id"), "", 100)
        name = _bounded_setting(source.get("name"), source_id, 160)
        if not source_id or not name:
            continue
        resources.append(
            {
                "id": source_id,
                "name": name,
                "url": safe_external_url(source.get("url")),
                "packs": _bounded_string_list(source.get("packs")),
                "domains": _bounded_string_list(source.get("domains")),
                "source_type": _bounded_setting(
                    source.get("source_type"), source.get("kind", "resource"), 80
                ),
                "support_level": _bounded_setting(
                    source.get("support_level"), "manual", 40
                ),
                "enabled": bool(source.get("enabled", True)),
                "auto_enable": bool(source.get("auto_enable", True)),
            }
        )
    return resources


def _dashboard_settings(profile: Dict[str, Any]) -> Dict[str, Any]:
    dashboard = profile.get("dashboard", {})
    configured_timeframes = profile.get("timeframes", dashboard.get("timeframes", []))
    if not isinstance(configured_timeframes, list):
        configured_timeframes = []
    timeframes = [
        str(value).strip()
        for value in configured_timeframes
        if str(value).strip()
    ][:12]
    legacy_target = str(dashboard.get("target_season", "")).strip()
    if not timeframes and legacy_target:
        timeframes = [legacy_target]
    packs = [
        {
            "id": str(pack.get("id", "")),
            "name": str(pack.get("name", pack.get("id", ""))),
            "description": str(pack.get("description", ""))[:240],
            "default": bool(pack.get("default", False)),
        }
        for pack in load_source_packs()
        if str(pack.get("id", "")).strip()
    ]
    return {
        "title": _bounded_setting(
            dashboard.get("title"),
            "Opportunity Radar",
            80,
        ),
        "subtitle": _bounded_setting(
            dashboard.get(
                "subtitle",
                "Review matches and track applications from the sources you follow.",
            ),
            "Review matches and track applications from the sources you follow.",
            240,
        ),
        "timeframes": timeframes,
        "target_season": legacy_target,
        "default_reason": _bounded_setting(
            dashboard.get("default_reason"),
            "Matched by your configured preferences.",
            240,
        ),
        "document_label": _bounded_setting(
            dashboard.get("document_label"),
            "Application track",
            80,
        ),
        "profile_catalog": profile_catalog_payload(),
        "profile_editor": profile_editor_payload(profile),
        "source_packs": packs,
        "source_resources": _source_resource_directory(),
    }


def safe_external_url(value: Any) -> str:
    candidate = str(value or "").strip()
    parsed = urlparse(candidate)
    return candidate if parsed.scheme.lower() in {"https", "http"} and parsed.netloc else ""


def _safe_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    rendered = dict(payload)
    rendered["opportunities"] = [
        dict(item, url=safe_external_url(item.get("url")))
        for item in payload.get("opportunities", [])
    ]
    rendered["sources"] = [
        dict(source, url=safe_external_url(source.get("url")))
        for source in payload.get("sources", [])
    ]
    rendered["events"] = [
        dict(event, url=safe_external_url(event.get("url")))
        for event in payload.get("events", [])
    ]
    return rendered


def _read_asset(name: str) -> str:
    return project_path("dashboard", name).read_text(encoding="utf-8")


def render_dashboard(payload: Dict[str, Any], profile: Optional[Dict[str, Any]] = None) -> Path:
    configured_output = project_path("dashboard", "index.html")
    output_path = resolve_private_state_path(
        configured_output,
        "dashboard",
        "index.html",
    )
    template = _read_asset("template.html")
    required = (STYLE_MARKER, DATA_MARKER, APP_MARKER)
    if any(template.count(marker) != 1 for marker in required):
        raise ValueError("Dashboard template must contain each asset marker exactly once")
    if template.count(NONCE_MARKER) < 3:
        raise ValueError("Dashboard template is missing CSP nonce markers")

    rendered_payload = _safe_payload(payload)
    rendered_payload["settings"] = _dashboard_settings(
        load_profile() if profile is None else profile
    )
    # Escaping '<' prevents an embedded closing script tag from ending the JSON block.
    data = json.dumps(rendered_payload, ensure_ascii=False, separators=(",", ":")).replace(
        "<", "\\u003c"
    )
    nonce = secrets.token_urlsafe(24)
    rendered = template.replace(NONCE_MARKER, nonce)
    rendered = rendered.replace(STYLE_MARKER, _read_asset("styles.css"))
    rendered = rendered.replace(DATA_MARKER, data)
    rendered = rendered.replace(APP_MARKER, _read_asset("app.js"))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=str(output_path.parent), prefix=".dashboard-", suffix=".html"
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, output_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
    return output_path
