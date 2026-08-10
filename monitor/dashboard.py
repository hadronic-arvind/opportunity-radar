"""Render a private, self-contained dashboard that works directly from file://."""

import json
import os
import secrets
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from .config import load_profile, project_path, resolve_private_state_path


STYLE_MARKER = "/*__OPPORTUNITY_STYLES__*/"
DATA_MARKER = "/*__OPPORTUNITY_DATA__*/"
APP_MARKER = "/*__OPPORTUNITY_APP__*/"
NONCE_MARKER = "__OPPORTUNITY_NONCE__"
# Backward-compatible export for integrations which imported the old marker.
MARKER = DATA_MARKER


def _dashboard_settings(profile: Dict[str, Any]) -> Dict[str, str]:
    dashboard = profile.get("dashboard", {})
    return {
        "title": str(dashboard.get("title", "Opportunity Radar")),
        "subtitle": str(
            dashboard.get(
                "subtitle",
                "Find and track opportunities from the sources you choose.",
            )
        ),
        "target_season": str(dashboard.get("target_season", "")),
        "default_reason": str(
            dashboard.get("default_reason", "Matched by your configured preferences.")
        ),
        "document_label": str(dashboard.get("document_label", "Application track")),
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
    rendered_payload["settings"] = _dashboard_settings(profile or load_profile())
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
