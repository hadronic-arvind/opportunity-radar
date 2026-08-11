#!/usr/bin/env python3
"""Recover a validated lifecycle lock whose recorded owner is no longer alive."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from monitor.profile import ProfileValidationError, recover_stale_lifecycle_lock


def main() -> int:
    try:
        recover_stale_lifecycle_lock()
    except (OSError, ProfileValidationError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
