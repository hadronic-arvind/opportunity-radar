#!/usr/bin/env python3
"""Remove one superseded runtime archive after an installer commit."""

import argparse
import os
import re
import shutil
import stat
from pathlib import Path
from typing import Optional


SAFE_BACKUP_STAMP = re.compile(r"\d{8}-\d{6}\Z")
RUNTIME_MARKERS = (
    Path("monitor/__main__.py"),
    Path("config/profile.json"),
    Path("dashboard/template.html"),
    Path("scripts/run_monitor.sh"),
)


def _reject_control_characters(value: str, label: str) -> None:
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("{} contains control characters".format(label))


def _require_private_owned_directory(path: Path, label: str, uid: int) -> None:
    if path.is_symlink():
        raise ValueError("{} must not be a symbolic link".format(label))
    details = path.lstat()
    if not stat.S_ISDIR(details.st_mode):
        raise ValueError("{} must be a directory".format(label))
    if details.st_uid != uid:
        raise ValueError("{} is not owned by the current user".format(label))
    if details.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise ValueError("{} is writable by another user".format(label))


def _require_runtime(path: Path, label: str, uid: int) -> None:
    _require_private_owned_directory(path, label, uid)
    for marker in RUNTIME_MARKERS:
        marker_path = path / marker
        details = marker_path.lstat()
        if not stat.S_ISREG(details.st_mode) or details.st_uid != uid:
            raise ValueError("{} is not an Opportunity Radar runtime".format(label))


def remove_superseded_runtime(
    runtime: Path,
    stamp: str,
    uid: Optional[int] = None,
) -> Path:
    """Delete only the exact archive created by the current successful upgrade."""

    runtime = Path(runtime)
    _reject_control_characters(str(runtime), "runtime path")
    if not runtime.is_absolute():
        raise ValueError("runtime path must be absolute")
    if not SAFE_BACKUP_STAMP.fullmatch(stamp):
        raise ValueError("backup stamp must use YYYYMMDD-HHMMSS")

    current_uid = os.getuid() if uid is None else uid
    parent = runtime.parent.resolve(strict=True)
    runtime = parent / runtime.name
    previous = runtime.with_name(runtime.name + ".previous")
    archive = previous.with_name(previous.name + "-" + stamp)

    _require_private_owned_directory(parent, "runtime parent", current_uid)
    _require_runtime(runtime, "current runtime", current_uid)
    _require_runtime(previous, "previous runtime", current_uid)
    _require_runtime(archive, "superseded runtime archive", current_uid)
    if not shutil.rmtree.avoids_symlink_attacks:
        raise RuntimeError("secure recursive removal is unavailable")

    shutil.rmtree(archive)
    if archive.exists() or archive.is_symlink():
        raise RuntimeError("superseded runtime archive could not be removed")
    return archive


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--stamp", required=True)
    args = parser.parse_args()
    try:
        remove_superseded_runtime(args.runtime, args.stamp)
    except (OSError, RuntimeError, ValueError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
