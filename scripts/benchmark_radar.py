#!/usr/bin/env python3
"""Exercise a clean offline Radar workspace and report timings in milliseconds."""

import argparse
import json
import platform
import resource
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path
from time import perf_counter

from radar.store import Store


def timings(function, count):
    samples = []
    for _ in range(count):
        start = perf_counter()
        function()
        samples.append(round((perf_counter() - start) * 1000, 2))
    return {
        "median_ms": statistics.median(samples),
        "worst_ms": max(samples),
        "runs": count,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=30000)
    args = parser.parse_args()
    if not 1 <= args.rows <= 100000:
        parser.error("Use 1 to 100,000 records")
    with tempfile.TemporaryDirectory(prefix="radar-benchmark-") as name:
        store = Store(Path(name) / "bench.sqlite3")
        cli = [sys.executable, "-m", "radar", "--workspace", str(store.path), "--json"]
        subprocess.run(
            [*cli, "init"], check=True, stdout=subprocess.DEVNULL, timeout=10
        )
        startup = timings(
            lambda: subprocess.run(
                [*cli, "search", "research"],
                check=True,
                stdout=subprocess.DEVNULL,
                timeout=10,
            ),
            7,
        )
        store.add_source(
            {"id": "bench", "name": "Benchmark", "kind": "sample", "url": ""}
        )
        start = perf_counter()
        for low in range(0, args.rows, 5000):
            rows = [
                {
                    "external_id": str(index),
                    "title": f"Fellowship {index}",
                    "organization": f"Sample institution {index % 100}",
                    "description": (
                        "Build numerical models and report research findings."
                        if index % 20 == 0
                        else "Coordinate public programs and maintain records."
                    ),
                    "kinds": ["fellowship"],
                }
                for index in range(low, min(args.rows, low + 5000))
            ]
            store.ingest("bench", rows, "imported", "Offline benchmark fixture")
        ingest = round((perf_counter() - start) * 1000, 2)
        store.put_goal("Research", {"prefer": ["scientific computing", "research"]})
        searches = {
            "explore": timings(lambda: store.search(), 15),
            "search": timings(lambda: store.search("research"), 15),
            "recommendations": timings(
                lambda: store.search(goal="Research", scope="recommended"), 10
            ),
        }
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        rss_bytes = rss if sys.platform == "darwin" else rss * 1024
        print(
            json.dumps(
                {
                    "platform": platform.platform(),
                    "python": platform.python_version(),
                    "rows": args.rows,
                    "cold_process_startup_empty": startup,
                    "ingest_ms": ingest,
                    "first_page": searches,
                    "workspace_bytes": store.path.stat().st_size,
                    "peak_rss_bytes": rss_bytes,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
