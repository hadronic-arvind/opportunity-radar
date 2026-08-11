#!/usr/bin/env python3
"""Fail an installer safely when the managed runtime is still being scanned."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from monitor.config import project_path, resolve_private_state_path
from monitor.pipeline import exclusive_lock


def main() -> int:
    database_path = resolve_private_state_path(
        project_path("data", "opportunities.sqlite3"),
        "data",
        "opportunities.sqlite3",
    )
    with exclusive_lock(database_path.with_name("scan.lock")):
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
