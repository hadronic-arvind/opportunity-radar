#!/usr/bin/env python3
"""Render the launchd property list without shell-escaping path values."""

import argparse
import os
import plistlib
import re
import stat
from pathlib import Path
from typing import Optional, Tuple


SAFE_LABEL = re.compile(r"[A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*\Z")
SAFE_BACKUP_STAMP = re.compile(r"\d{8}-\d{6}\Z")
MAX_LABEL_LENGTH = 128
LEGACY_RUNTIME_MARKERS = (
    Path("monitor/__main__.py"),
    Path("config/profile.json"),
    Path("dashboard/template.html"),
    Path("scripts/run_monitor.sh"),
)
RUNTIME_MARKERS = LEGACY_RUNTIME_MARKERS + (
    Path("dashboard/styles.css"),
    Path("dashboard/app.js"),
)
RUNTIME_GENERATED_ARTIFACTS = (
    (Path("dashboard/index.html"), "dashboard"),
    (Path("data/opportunities.sqlite3"), "sqlite"),
    (Path("logs/cron.out.log"), "log"),
    (Path("logs/cron.err.log"), "log"),
    (Path("logs/launchd.out.log"), "log"),
    (Path("logs/launchd.err.log"), "log"),
)
SQLITE_HEADER = b"SQLite format 3\x00"
DASHBOARD_SIGNATURE_SETS = (
    (
        b"Content-Security-Policy",
        b'id="opportunity-data"',
    ),
    (
        b"Content-Security-Policy",
        b"window.OPPORTUNITY_DATA = {",
        b"<title>Opportunity Radar</title>",
    ),
)


def calendar_value(value: str, minimum: int, maximum: int, label: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("{} must be an integer".format(label)) from error
    if parsed < minimum or parsed > maximum:
        raise argparse.ArgumentTypeError("{} must be between {} and {}".format(label, minimum, maximum))
    return parsed


def launch_label(value: str) -> str:
    if not value or len(value) > MAX_LABEL_LENGTH or not SAFE_LABEL.fullmatch(value):
        raise argparse.ArgumentTypeError(
            "label must use 1-128 ASCII letters, digits, dots, or hyphens without repeated separators"
        )
    return value


def _reject_control_characters(value: str, label: str) -> None:
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("{} contains control characters".format(label))


def _absolute_path(value: Path, label: str) -> Path:
    expanded = value.expanduser()
    _reject_control_characters(str(expanded), label)
    if not expanded.is_absolute():
        raise ValueError("{} must be an absolute path".format(label))
    return expanded


def _reject_terminal_symlink(path: Path, label: str) -> None:
    if path.is_symlink():
        raise ValueError("{} must not be a symbolic link".format(label))


def _nearest_existing(path: Path) -> Path:
    candidate = path
    while not candidate.exists():
        _reject_terminal_symlink(candidate, "path component")
        parent = candidate.parent
        if parent == candidate:
            raise ValueError("path has no existing parent")
        candidate = parent
    return candidate


def _require_owned(path: Path, label: str, uid: int) -> None:
    details = path.stat()
    if details.st_uid != uid:
        raise ValueError("{} is not owned by the current user".format(label))
    if details.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise ValueError("{} is writable by another user".format(label))


def _validate_artifact_signature(path: Path, label: str, artifact_type: str) -> None:
    if artifact_type == "sqlite":
        with path.open("rb") as handle:
            if handle.read(len(SQLITE_HEADER)) != SQLITE_HEADER:
                raise ValueError("{} is not a valid SQLite database".format(label))
    elif artifact_type == "dashboard":
        with path.open("rb") as handle:
            prefix = handle.read(256 * 1024)
        if not any(
            all(signature in prefix for signature in signature_set)
            for signature_set in DASHBOARD_SIGNATURE_SETS
        ):
            raise ValueError("{} is not a generated Opportunity Radar dashboard".format(label))


def _validate_private_regular_artifact(
    path: Path,
    label: str,
    artifact_type: str,
    uid: int,
) -> None:
    details = path.lstat()
    if not stat.S_ISREG(details.st_mode):
        raise ValueError("{} must be a regular file".format(label))
    if details.st_uid != uid:
        raise ValueError("{} is not owned by the current user".format(label))
    if stat.S_IMODE(details.st_mode) != 0o600:
        raise ValueError("{} must have private mode 0600".format(label))
    _validate_artifact_signature(path, label, artifact_type)


def _resolved_link_target(path: Path) -> Path:
    target = Path(os.readlink(path))
    if not target.is_absolute():
        target = path.parent / target
    return target.resolve(strict=False)


def validate_generated_artifacts(
    project: Path,
    runtime: Path,
    uid: Optional[int] = None,
) -> None:
    """Validate project artifacts that will be archived or replaced by runtime links."""

    current_uid = os.getuid() if uid is None else uid
    specifications = (
        (
            project / "dashboard" / "index.html",
            "existing dashboard index",
            "dashboard",
            (runtime / "dashboard" / "index.html",),
        ),
        (
            project / "data" / "opportunities.sqlite3",
            "existing SQLite database",
            "sqlite",
            (runtime / "data" / "opportunities.sqlite3",),
        ),
        (
            project / "logs" / "scheduler.out.log",
            "existing scheduler stdout log",
            "log",
            (
                runtime / "logs" / "cron.out.log",
                runtime / "logs" / "launchd.out.log",
            ),
        ),
        (
            project / "logs" / "scheduler.err.log",
            "existing scheduler stderr log",
            "log",
            (
                runtime / "logs" / "cron.err.log",
                runtime / "logs" / "launchd.err.log",
            ),
        ),
    )
    for path, label, artifact_type, expected_targets in specifications:
        if path.is_symlink():
            if path.lstat().st_uid != current_uid:
                raise ValueError("{} link is not owned by the current user".format(label))
            expected = {target.resolve(strict=False) for target in expected_targets}
            if _resolved_link_target(path) not in expected:
                raise ValueError("{} link does not target the expected runtime artifact".format(label))
            continue
        if path.exists():
            _validate_private_regular_artifact(path, label, artifact_type, current_uid)


def installer_backup_paths(
    project: Path,
    runtime: Path,
    target_directory: Path,
    label: str,
    stamp: str,
) -> Tuple[Path, ...]:
    """Return every timestamped recovery or archive destination used by the installer."""

    validated_label = launch_label(label)
    if not SAFE_BACKUP_STAMP.fullmatch(stamp):
        raise ValueError("backup stamp must use YYYYMMDD-HHMMSS")
    target = target_directory / "{}.plist".format(validated_label)
    previous_runtime = runtime.with_name(runtime.name + ".previous")
    return (
        runtime.with_name(runtime.name + ".failed-" + stamp),
        previous_runtime.with_name(previous_runtime.name + "-" + stamp),
        target.with_name(target.name + ".failed-" + stamp),
        project / "data" / "previous-launch-agent-{}.plist".format(stamp),
        project / "data" / "previous-crontab-{}.txt".format(stamp),
        project / "dashboard" / "index.pre-runtime-{}.html".format(stamp),
        project / "data" / "opportunities.pre-runtime-{}.sqlite3".format(stamp),
        project / "logs" / "scheduler.pre-runtime-{}.out.log".format(stamp),
        project / "logs" / "scheduler.pre-runtime-{}.err.log".format(stamp),
    )


def validate_backup_destinations(
    project: Path,
    runtime: Path,
    target_directory: Path,
    label: str,
    stamp: str,
) -> Tuple[Path, ...]:
    """Fail before mutation if any installer backup destination is already occupied."""

    destinations = installer_backup_paths(project, runtime, target_directory, label, stamp)
    if len(set(destinations)) != len(destinations):
        raise ValueError("installer backup destinations must be distinct")
    for destination in destinations:
        if destination.exists() or destination.is_symlink():
            raise ValueError("reserved installer recovery path already exists: {}".format(destination.name))
    return destinations


def _validate_private_directory(path: Path, label: str, uid: int) -> Path:
    requested = _absolute_path(path, label)
    _reject_terminal_symlink(requested, label)
    existing = _nearest_existing(requested)
    _reject_terminal_symlink(existing, label)
    if not existing.is_dir():
        raise ValueError("{} does not have a directory parent".format(label))
    _require_owned(existing, label, uid)
    if requested.exists():
        if not requested.is_dir():
            raise ValueError("{} must be a directory".format(label))
        _require_owned(requested, label, uid)
    return requested.resolve(strict=False)


def _looks_like_runtime(path: Path) -> bool:
    if not all(
        not (path / marker).is_symlink() and (path / marker).is_file()
        for marker in LEGACY_RUNTIME_MARKERS
    ):
        return False
    # Older private runtimes predate the split dashboard assets and must remain
    # safely upgradeable. If either newer asset exists, it must be a regular file.
    return all(
        not (path / marker).is_symlink()
        and (not (path / marker).exists() or (path / marker).is_file())
        for marker in RUNTIME_MARKERS[len(LEGACY_RUNTIME_MARKERS) :]
    )


def _validate_existing_runtime(path: Path, label: str, uid: int) -> None:
    if not path.exists() and not path.is_symlink():
        return
    _reject_terminal_symlink(path, label)
    if not path.is_dir():
        raise ValueError("{} must be a directory".format(label))
    _require_owned(path, label, uid)
    if not _looks_like_runtime(path):
        raise ValueError("{} is not an Opportunity Radar runtime".format(label))
    for marker in RUNTIME_MARKERS:
        marker_path = path / marker
        if marker_path.exists():
            _require_owned(marker_path, "{} marker".format(label), uid)
    for relative, artifact_type in RUNTIME_GENERATED_ARTIFACTS:
        artifact = path / relative
        if artifact.exists() or artifact.is_symlink():
            _validate_private_regular_artifact(
                artifact,
                "{} {}".format(label, relative),
                artifact_type,
                uid,
            )


def _validate_target_directory(path: Path, label: str, uid: int) -> Path:
    requested = _absolute_path(path, label)
    _reject_terminal_symlink(requested, label)
    existing = _nearest_existing(requested)
    _reject_terminal_symlink(existing, label)
    if not existing.is_dir():
        raise ValueError("{} does not have a directory parent".format(label))
    if requested.exists():
        if not requested.is_dir():
            raise ValueError("{} must be a directory".format(label))
        # A non-writable system-owned LaunchAgents directory is valid because
        # the installer will use its user-crontab fallback without mutating it.
        if os.access(requested, os.W_OK):
            _require_owned(requested, label, uid)
    else:
        _require_owned(existing, label, uid)
    return requested.resolve(strict=False)


def _validate_python(path: Path, uid: int) -> Path:
    requested = _absolute_path(path, "Python executable")
    resolved = requested.resolve(strict=True)
    details = resolved.stat()
    if not stat.S_ISREG(details.st_mode) or not os.access(resolved, os.X_OK):
        raise ValueError("Python executable must be an executable regular file")
    if details.st_uid not in {0, uid}:
        raise ValueError("Python executable has an unexpected owner")
    if details.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise ValueError("Python executable is writable by another user")
    return resolved


def validate_output_path(path: Path, uid: Optional[int] = None) -> Path:
    """Return a canonical, current-user-owned plist output destination."""
    current_uid = os.getuid() if uid is None else uid
    requested = _absolute_path(path, "output")
    _reject_terminal_symlink(requested, "output")
    parent = _validate_private_directory(requested.parent, "output directory", current_uid)
    output = parent / requested.name
    if output.exists():
        if not output.is_file():
            raise ValueError("output must be a regular file")
        _require_owned(output, "output", current_uid)
    return output


def validate_install_paths(
    project: Path,
    runtime: Path,
    target_directory: Path,
    label: str,
    python_executable: Path,
    uid: Optional[int] = None,
    backup_stamp: Optional[str] = None,
) -> Tuple[Path, Path, Path]:
    """Validate and canonicalize every installer-controlled destination."""
    current_uid = os.getuid() if uid is None else uid
    validated_label = launch_label(label)
    project_path = _validate_private_directory(project, "project directory", current_uid)
    for name in ("data", "dashboard", "logs"):
        _validate_private_directory(
            project_path / name,
            "project {} directory".format(name),
            current_uid,
        )
    runtime_path = _validate_private_directory(runtime, "runtime directory", current_uid)
    home = Path.home().resolve()
    if runtime_path in {Path("/"), home, project_path}:
        raise ValueError("runtime directory is an unsafe broad path")
    if runtime_path in project_path.parents or project_path in runtime_path.parents:
        raise ValueError("runtime directory must be separate from the repository")
    _validate_existing_runtime(runtime_path, "existing runtime", current_uid)
    _validate_existing_runtime(
        runtime_path.with_name(runtime_path.name + ".previous"),
        "previous runtime",
        current_uid,
    )
    target_path = _validate_target_directory(
        target_directory, "LaunchAgents directory", current_uid
    )
    if (
        target_path == project_path
        or project_path in target_path.parents
        or target_path in project_path.parents
    ):
        raise ValueError("LaunchAgents directory must be outside the repository")
    if (
        target_path == runtime_path
        or runtime_path in target_path.parents
        or target_path in runtime_path.parents
    ):
        raise ValueError("LaunchAgents directory must be outside the runtime")
    target_file = target_path / "{}.plist".format(validated_label)
    _reject_terminal_symlink(target_file, "launch-agent file")
    if target_file.exists():
        if not target_file.is_file():
            raise ValueError("launch-agent destination must be a regular file")
        _require_owned(target_file, "launch-agent file", current_uid)
    rendered_file = project_path / "data" / "{}.plist".format(validated_label)
    _reject_terminal_symlink(rendered_file, "rendered launch-agent file")
    if rendered_file.exists():
        if not rendered_file.is_file():
            raise ValueError("rendered launch-agent destination must be a regular file")
        _require_owned(rendered_file, "rendered launch-agent file", current_uid)
    if backup_stamp is not None:
        validate_generated_artifacts(project_path, runtime_path, current_uid)
        validate_backup_destinations(
            project_path,
            runtime_path,
            target_path,
            validated_label,
            backup_stamp,
        )
    python_path = _validate_python(python_executable, current_uid)
    return runtime_path, target_path, python_path


def build_plist(runtime: Path, label: str, times: list[tuple[int, int]]) -> dict:
    label = launch_label(label)
    return {
        "Label": label,
        "ProgramArguments": [str(runtime / "scripts" / "run_monitor.sh")],
        "WorkingDirectory": str(runtime),
        "StartCalendarInterval": [
            {"Hour": hour, "Minute": minute} for hour, minute in times
        ],
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "Nice": 10,
        "AbandonProcessGroup": False,
        "StandardOutPath": str(runtime / "logs" / "launchd.out.log"),
        "StandardErrorPath": str(runtime / "logs" / "launchd.err.log"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--label", type=launch_label, default="io.github.opportunity-radar.monitor")
    parser.add_argument("--validate-install", action="store_true")
    parser.add_argument("--project", type=Path)
    parser.add_argument("--target-dir", type=Path)
    parser.add_argument("--python", type=Path)
    parser.add_argument("--backup-stamp")
    parser.add_argument("--morning-hour", default="7")
    parser.add_argument("--morning-minute", default="30")
    parser.add_argument("--afternoon-hour", default="16")
    parser.add_argument("--afternoon-minute", default="30")
    args = parser.parse_args()
    times = [
        (
            calendar_value(args.morning_hour, 0, 23, "morning hour"),
            calendar_value(args.morning_minute, 0, 59, "morning minute"),
        ),
        (
            calendar_value(args.afternoon_hour, 0, 23, "afternoon hour"),
            calendar_value(args.afternoon_minute, 0, 59, "afternoon minute"),
        ),
    ]
    if args.validate_install:
        if (
            args.project is None
            or args.target_dir is None
            or args.python is None
            or args.backup_stamp is None
        ):
            parser.error(
                "--validate-install requires --project, --target-dir, --python, and --backup-stamp"
            )
        try:
            runtime, target, python = validate_install_paths(
                args.project,
                args.runtime,
                args.target_dir,
                args.label,
                args.python,
                backup_stamp=args.backup_stamp,
            )
        except (OSError, ValueError) as error:
            parser.error(str(error))
        print("{}\t{}\t{}".format(runtime, target, python))
        return 0
    if args.output is None:
        parser.error("--output is required unless --validate-install is used")
    try:
        output = validate_output_path(args.output)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as handle:
        plistlib.dump(build_plist(args.runtime, args.label, times), handle, sort_keys=False)
    os.chmod(output, 0o600)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
