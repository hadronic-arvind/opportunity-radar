#!/usr/bin/env python3
"""Fail when the exact Git publication set contains private local data."""

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from monitor.config import resolve_private_state_path


MAX_PRIVATE_PROFILE_BYTES = 2 * 1024 * 1024
DISALLOWED_PATHS = {
    ".agents/project-memory.md",
    "config/profile.local.json",
    "config/sources.local.json",
    "dashboard/index.html",
    "public/dashboard/index.html",
}
DISALLOWED_PREFIXES = (
    ".agents/",
    ".runtime/",
    "data/",
    "logs/",
    "public/dashboard/",
    "reports/daily/",
    "seed/",
)
DISALLOWED_SUFFIXES = (
    ".db",
    ".dSYM",
    ".key",
    ".p12",
    ".pem",
    ".pfx",
    ".sqlite3",
)
GENERIC_PUBLIC_LABELS = {
    "application track",
    "excluded work",
    "general",
    "research cv",
    "research software",
    "software",
}
FORBIDDEN_PUBLIC_SOURCE_KEYS = {
    "acceptance_chance",
    "acceptance_odds",
    "acceptance_rate",
    "base_score",
    "cycle",
    "high_chance",
    "item_exclude",
    "item_include",
    "recommended_resume",
    "target_season",
    "tier",
}
PUBLIC_BINARY_BLOBS = {
    "assets/opportunity-radar-icon-v2.png":
        "0d0866faeb538da1a0fc2327338e5f47584ee6bee12b734411c940acf3e13fde",
}


def publication_paths() -> List[Path]:
    """Return publication paths for compatibility with existing integrations."""
    if (PROJECT_ROOT / ".git").exists():
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=PROJECT_ROOT,
            check=True,
            stdout=subprocess.PIPE,
        )
        return [PROJECT_ROOT / os.fsdecode(value) for value in result.stdout.split(b"\0") if value]
    return [path for path in PROJECT_ROOT.rglob("*") if not fallback_ignored(path)]


def publication_items() -> List[Tuple[str, int, bytes]]:
    """Read publishable bytes from both the Git index and working tree."""
    if not (PROJECT_ROOT / ".git").exists():
        items = []
        for path in publication_paths():
            relative = path.relative_to(PROJECT_ROOT).as_posix()
            if path.is_symlink():
                items.append((relative, 0o120000, os.fsencode(os.readlink(path))))
            else:
                try:
                    items.append((relative, path.stat().st_mode, path.read_bytes()))
                except OSError:
                    continue
        return items

    staged = subprocess.run(
        ["git", "ls-files", "--cached", "--stage", "-z"],
        cwd=PROJECT_ROOT,
        check=True,
        stdout=subprocess.PIPE,
    ).stdout
    items: List[Tuple[str, int, bytes]] = []
    indexed_items = {}
    for record in staged.split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        raw_mode, object_id, raw_stage = metadata.split(b" ", 2)
        if raw_stage != b"0":
            raise RuntimeError("Privacy check cannot inspect an unmerged Git index")
        content = subprocess.run(
            ["git", "cat-file", "blob", object_id.decode("ascii")],
            cwd=PROJECT_ROOT,
            check=True,
            stdout=subprocess.PIPE,
        ).stdout
        relative = os.fsdecode(raw_path)
        mode = int(raw_mode, 8)
        items.append((relative, mode, content))
        indexed_items[relative] = (mode, content)

    # Inspect unstaged edits too. This keeps the gate safe when a developer runs
    # it before a later `git commit -a`, while the index remains authoritative
    # for staged content that differs from the working tree.
    for relative, indexed in indexed_items.items():
        path = PROJECT_ROOT / relative
        if path.is_symlink():
            working = (0o120000, os.fsencode(os.readlink(path)))
        elif path.is_file():
            try:
                details = path.stat()
                working_mode = 0o100755 if details.st_mode & 0o111 else 0o100644
                working = (working_mode, path.read_bytes())
            except OSError:
                continue
        else:
            continue
        if working != indexed:
            items.append((relative, working[0], working[1]))

    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=PROJECT_ROOT,
        check=True,
        stdout=subprocess.PIPE,
    ).stdout
    for raw_path in untracked.split(b"\0"):
        if not raw_path:
            continue
        relative = os.fsdecode(raw_path)
        path = PROJECT_ROOT / relative
        if path.is_symlink():
            items.append((relative, 0o120000, os.fsencode(os.readlink(path))))
        else:
            try:
                items.append((relative, path.stat().st_mode, path.read_bytes()))
            except OSError:
                continue
    return items


def fallback_ignored(path: Path) -> bool:
    relative = path.relative_to(PROJECT_ROOT).as_posix()
    name = path.name
    if path.is_dir():
        return True
    if relative in {"config/profile.local.json", "config/sources.local.json", "dashboard/index.html"}:
        return True
    if relative.startswith(
        (
            ".agents/",
            ".git/",
            ".runtime/",
            ".next/",
            ".vinext/",
            ".wrangler/",
            "data/",
            "logs/",
            "node_modules/",
            "public/dashboard/",
            "reports/daily/",
        )
    ):
        return True
    return name.startswith(".env") or name == ".DS_Store" or name.endswith((".pyc", ".pem", ".key", ".p12", ".pfx"))


def _runtime_profile_path() -> Optional[Path]:
    """Return a private runtime profile only through the managed database link."""
    database = PROJECT_ROOT / "data" / "opportunities.sqlite3"
    if not database.is_symlink():
        return None
    try:
        database_target = resolve_private_state_path(
            database,
            "data",
            "opportunities.sqlite3",
        )
        profile = database_target.parent.parent / "config" / "profile.local.json"
        details = profile.lstat()
    except (OSError, ValueError):
        return None
    if (
        not stat.S_ISREG(details.st_mode)
        or details.st_uid != os.getuid()
        or details.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
        or details.st_size > MAX_PRIVATE_PROFILE_BYTES
    ):
        return None
    return profile


def _private_profile() -> Dict[str, object]:
    path = _runtime_profile_path() or PROJECT_ROOT / "config" / "profile.local.json"
    try:
        if not path.is_file() or path.stat().st_size > MAX_PRIVATE_PROFILE_BYTES:
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def private_values() -> List[str]:
    payload = _private_profile()
    candidate = payload.get("candidate", {})
    candidate = candidate if isinstance(candidate, dict) else {}
    values = [
        candidate.get("name"),
        candidate.get("program"),
        candidate.get("expected_graduation"),
        candidate.get("target_season"),
    ]
    dashboard = payload.get("dashboard", {})
    if isinstance(dashboard, dict):
        values.append(dashboard.get("target_season"))
    curated = str(payload.get("curated_pipeline_path", ""))
    if curated:
        parts = Path(curated).parts
        if len(parts) > 2 and parts[1] == "Users":
            values.append(parts[2])
    return [str(value) for value in values if value and len(str(value)) >= 5]


def _nested_strings(value: object) -> Iterable[str]:
    if isinstance(value, dict):
        for child in value.values():
            yield from _nested_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _nested_strings(child)
    elif isinstance(value, str) and value.strip():
        yield value.strip()


def _public_config_strings() -> set[str]:
    values = set()
    for name in ("profile.json", "sources.json"):
        path = PROJECT_ROOT / "config" / name
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        values.update(value.casefold() for value in _nested_strings(payload))
    return values


def private_labels() -> List[str]:
    """Return custom local labels that should never appear verbatim in public text."""
    public_path = PROJECT_ROOT / "config" / "profile.json"
    local = _private_profile()
    if not local:
        return []
    try:
        public = (
            json.loads(public_path.read_text(encoding="utf-8"))
            if public_path.is_file()
            else {}
        )
    except (OSError, UnicodeError, ValueError):
        return []

    values = []
    documents = local.get("documents", {})
    if isinstance(documents, dict):
        values.append(documents.get("default"))
        routes = documents.get("routes", [])
        for route in routes if isinstance(routes, list) else []:
            if isinstance(route, dict):
                values.append(route.get("label"))
    matching = local.get("matching", {})
    if isinstance(matching, dict):
        rules = matching.get("rules", [])
        for rule in rules if isinstance(rules, list) else []:
            if isinstance(rule, dict):
                values.append(rule.get("label"))
    dashboard = local.get("dashboard", {})
    if isinstance(dashboard, dict):
        values.extend(
            dashboard.get(key)
            for key in ("title", "subtitle", "document_label", "default_reason")
        )

    public_values = _public_config_strings()
    output = []
    for candidate in values:
        value = str(candidate or "").strip()
        if (
            len(value) >= 5
            and value.casefold() not in public_values
            and value.casefold() not in GENERIC_PUBLIC_LABELS
            and value not in output
        ):
            output.append(value)
    return output


def private_preference_groups() -> List[List[str]]:
    """Return multi-term local rules whose verbatim copy is tailored configuration."""
    payload = _private_profile()
    if not payload:
        return []
    matching = payload.get("matching", {}) if isinstance(payload, dict) else {}
    if not isinstance(matching, dict):
        return []
    public_values = _public_config_strings()
    groups = []

    def add_group(candidates: object, minimum_length: int = 8) -> None:
        terms = []
        for candidate in candidates if isinstance(candidates, list) else []:
            value = str(candidate or "").strip()
            if (
                len(value) >= minimum_length
                and value.casefold() not in public_values
                and value not in terms
            ):
                terms.append(value)
        if len(terms) >= 2:
            groups.append(terms)
        elif terms and len(terms[0]) >= 24:
            groups.append(terms)

    add_group(payload.get("priority_organizations", []), minimum_length=5)
    rules = matching.get("rules", [])
    for rule in rules if isinstance(rules, list) else []:
        if not isinstance(rule, dict):
            continue
        add_group(rule.get("terms", []))
    documents = payload.get("documents", {})
    routes = documents.get("routes", []) if isinstance(documents, dict) else []
    for route in routes if isinstance(routes, list) else []:
        if isinstance(route, dict):
            add_group(route.get("terms", []))
    return groups


def _quoted_value_present(content: str, value: str) -> bool:
    folded = content.casefold()
    return (
        json.dumps(value, ensure_ascii=False).casefold() in folded
        or "`{}`".format(value).casefold() in folded
    )


def _intentional_legal_identity(relative: str, content: str, value: str) -> bool:
    """Allow an explicit copyright-holder name only on LICENSE's copyright line."""
    if relative != "LICENSE":
        return False
    pattern = r"^Copyright \(c\) \d{{4}} {}\.?$".format(re.escape(value.strip()))
    return bool(re.search(pattern, content, flags=re.MULTILINE))


def content_patterns() -> Iterable[Tuple[str, re.Pattern[str]]]:
    return [
        ("absolute macOS home path", re.compile("/" + "Users" + r"/[A-Za-z0-9._-]+/")),
        ("absolute Linux home path", re.compile("/" + "home" + r"/[A-Za-z0-9._-]+/")),
        ("private workspace path", re.compile("Desktop" + r"/Work", re.IGNORECASE)),
        ("private key material", re.compile("BEGIN [A-Z ]*PRIVATE KEY")),
        (
            "signed webhook URL",
            re.compile(r"https://[^\s\"']+(?:logic\.azure\.com|webhook)[^\s\"']*(?:sig=|signature=)", re.IGNORECASE),
        ),
        (
            "common access token",
            re.compile(r"(?:ghp|github_pat|sk_live|sk_test)_[A-Za-z0-9_-]{16,}"),
        ),
    ]


def _profile_is_tailored(payload: object) -> bool:
    if not isinstance(payload, dict):
        return True
    matching = payload.get("matching", {})
    documents = payload.get("documents", {})
    dashboard = payload.get("dashboard", {})
    return bool(
        payload.get("candidate")
        or payload.get("timeframes")
        or payload.get("targets")
        or payload.get("priority_organizations")
        or payload.get("positive_rules")
        or payload.get("negative_rules")
        or payload.get("resume_routing")
        or payload.get("exclusions")
        or payload.get("curated_pipeline_path")
        or (isinstance(matching, dict) and matching.get("rules"))
        or (isinstance(documents, dict) and documents.get("routes"))
        or (isinstance(dashboard, dict) and dashboard.get("target_season"))
        or (isinstance(dashboard, dict) and dashboard.get("timeframes"))
    )


def _nested_keys(value: object) -> Iterable[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key)
            yield from _nested_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _nested_keys(child)


def history_failures() -> List[str]:
    if not (PROJECT_ROOT / ".git").exists():
        return []
    revisions = subprocess.run(
        ["git", "rev-list", "--all"],
        cwd=PROJECT_ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.splitlines()
    failures = []
    for revision in revisions:
        short = revision[:12]
        for relative, kind in (
            ("config/profile.json", "profile"),
            ("config/sources.json", "sources"),
        ):
            result = subprocess.run(
                ["git", "show", "{}:{}".format(revision, relative)],
                cwd=PROJECT_ROOT,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            if result.returncode:
                continue
            try:
                payload = json.loads(result.stdout.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                failures.append("invalid historical {} at {}".format(kind, short))
                continue
            if kind == "profile" and _profile_is_tailored(payload):
                failures.append("tailored public profile remains in Git history at {}".format(short))
            if kind == "sources" and FORBIDDEN_PUBLIC_SOURCE_KEYS.intersection(_nested_keys(payload)):
                failures.append("private ranking metadata remains in Git history at {}".format(short))
    return failures


def scan(include_history: bool = False) -> List[str]:
    failures = []
    dynamic_values = private_values()
    local_labels = private_labels()
    preference_groups = private_preference_groups()
    for relative, mode, raw_content in publication_items():
        name = Path(relative).name
        if (
            relative in DISALLOWED_PATHS
            or relative.startswith(DISALLOWED_PREFIXES)
            or name.startswith(".env")
            or relative.endswith(DISALLOWED_SUFFIXES)
            or ".app/" in relative
            or ".dSYM/" in relative
        ):
            failures.append("private path is publishable: {}".format(relative))
            continue
        if mode == 0o120000:
            failures.append("symbolic link is publishable: {}".format(relative))
            continue
        if b"\0" in raw_content:
            expected_digest = PUBLIC_BINARY_BLOBS.get(relative)
            actual_digest = hashlib.sha256(raw_content).hexdigest()
            if not expected_digest or expected_digest != actual_digest:
                failures.append("binary blob requires an explicit public allowlist: {}".format(relative))
        if relative in {"config/profile.json", "config/sources.json"}:
            kind = "profile" if relative.endswith("profile.json") else "sources"
            try:
                public_payload = json.loads(raw_content.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                failures.append("invalid public {} is publishable".format(kind))
            else:
                if kind == "profile" and _profile_is_tailored(public_payload):
                    failures.append("tailored public profile is publishable")
                if (
                    kind == "sources"
                    and FORBIDDEN_PUBLIC_SOURCE_KEYS.intersection(
                        _nested_keys(public_payload)
                    )
                ):
                    failures.append("private ranking metadata is publishable")
        content = raw_content.decode("utf-8", errors="ignore")
        for label, pattern in content_patterns():
            if pattern.search(content):
                failures.append("{} in {}".format(label, relative))
        for value in dynamic_values:
            if (
                value.casefold() in content.casefold()
                and not _intentional_legal_identity(relative, content, value)
            ):
                failures.append("local profile value in {}".format(relative))
                break
        if any(_quoted_value_present(content, value) for value in local_labels):
            failures.append("local application label in {}".format(relative))
        if any(
            all(_quoted_value_present(content, value) for value in group)
            for group in preference_groups
        ):
            failures.append("local matching rule copied into {}".format(relative))
    if include_history:
        failures.extend(history_failures())
    return sorted(set(failures))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate the complete Git publication boundary."
    )
    parser.add_argument(
        "--history",
        action="store_true",
        help="Explicitly request the default reachable-history check.",
    )
    parser.parse_args()
    failures = scan(include_history=True)
    if failures:
        for failure in failures:
            print(failure, file=sys.stderr)
        return 1
    print("Privacy check passed for the Git publication set.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
