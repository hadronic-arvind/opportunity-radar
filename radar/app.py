"""Operations shared by the desktop and terminal."""

import json
import math
import os
import stat
from pathlib import Path

from .collection import collect
from .locking import exclusive_work
from .store import safe_url

SAMPLE_SOURCE = {
    "id": "sample",
    "name": "Illustrative catalog",
    "kind": "sample",
    "url": "",
}
SAMPLE = [
    (
        "numerics",
        "Scientific Software Fellow",
        "Example Coastal Observatory",
        "research fellowship",
        "Build scientific software and numerical models for coastal experiments. Includes a stipend.",
    ),
    (
        "mapping",
        "Community Mapping Apprenticeship",
        "Example Civic Lab",
        "apprenticeship training",
        "Create data dashboards with local planners and learn data visualization.",
    ),
    (
        "bench",
        "Lab Research Placement",
        "Example Materials Institute",
        "research internship",
        "Run laboratory experiments, document samples, and train alongside a mentor.",
    ),
    (
        "teaching",
        "Museum Education Assistant",
        "Example City Museum",
        "job training",
        "Develop lesson plans, deliver instruction, and support an art exhibition.",
    ),
    (
        "funding",
        "Open Methods Seed Award",
        "Example Research Foundation",
        "grant research",
        "Apply for funding to develop reproducible scientific computing methods. Funding depends on panel approval.",
    ),
    (
        "challenge",
        "Accessible Transit Design Challenge",
        "Example Design Collective",
        "competition",
        "Prototype an accessible wayfinding experience. Finalists may receive a prize.",
    ),
    (
        "accounting",
        "Partner Accountant",
        "Example Rail Services",
        "job",
        "Prepare financial statements and assist with audits.",
    ),
    (
        "hybrid",
        "Community Health Scholar",
        "Example University",
        "research fellowship training",
        "Conduct interviews, analyze public health data, and take graduate-level courses.",
    ),
]


def seed(store):
    rows = [
        dict(
            external_id=id,
            title=title,
            organization=organization,
            kinds=kinds.split(),
            description=description,
            evidence={"origin": "fictional sample"},
        )
        for id, title, organization, kinds, description in SAMPLE
    ]
    with store.transaction() as db:
        store.add_source(SAMPLE_SOURCE, conn=db)
        return store.ingest(
            "sample",
            rows,
            "imported",
            "Fictional examples for offline exploration",
            conn=db,
        )


def read_file(path, max_bytes):
    descriptor = os.open(Path(path).expanduser(), os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("Choose a regular local file")
        content = stream.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise ValueError(f"Input exceeds {max_bytes:,} bytes")
    return content


def read_json(path, max_bytes=8 * 1024 * 1024):
    return json.loads(read_file(path, max_bytes))


def import_file(store, path, source_id="manual"):
    return import_rows(store, read_json(path), source_id)


def import_rows(store, rows, source_id="manual"):
    if (
        not isinstance(rows, list)
        or len(rows) > 5000
        or not all(isinstance(row, dict) for row in rows)
    ):
        raise ValueError("Listing import needs an array of up to 5,000 objects")
    allowed = {
        "external_id",
        "title",
        "organization",
        "location",
        "description",
        "requirements",
        "url",
        "kinds",
        "posted_at",
        "deadline_at",
        "cycle",
    }
    observations = [
        {key: value for key, value in row.items() if key in allowed} for row in rows
    ]
    for row in observations:
        safe_url(row.get("url"))
        row["evidence"] = {"origin": "user import"}
    with store.transaction() as db:
        store.add_source(
            {"id": source_id, "name": source_id, "kind": "manual", "url": ""}, conn=db
        )
        return store.ingest(
            source_id, observations, "imported", "Local listing import", conn=db
        )


def refresh(store, source_id, seconds=15, cancel=None):
    if not math.isfinite(seconds) or not 0.1 <= seconds <= 120:
        raise ValueError("Collection budget must be between 0.1 and 120 seconds")
    with exclusive_work(store):
        config = next(
            (source for source in store.sources() if source["id"] == source_id), None
        )
        if config is None or config["kind"] in {"manual", "sample", "legacy"}:
            raise ValueError("Select a configured public source")
        config.pop("last_receipt", None)
        rows, state, message = collect(config, seconds, cancel)
        return store.ingest(source_id, rows, state, message)


def compare(store, ids, goal=None):
    unique = list(dict.fromkeys(ids))
    if not 2 <= len(unique) <= 6:
        raise ValueError("Select two to six distinct opportunities to compare")
    return store.inspect_many(unique, goal)
