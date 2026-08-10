#!/usr/bin/env python3
"""Create a transactionally consistent SQLite copy."""

import argparse
import os
import sqlite3
from pathlib import Path
from urllib.parse import quote


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    source_uri = "file:{}?mode=ro".format(quote(str(args.source.resolve()), safe="/"))
    source = sqlite3.connect(source_uri, uri=True)
    destination = sqlite3.connect(str(args.destination))
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    os.chmod(args.destination, 0o600)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
