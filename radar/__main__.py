"""Terminal entry point for the local workspace."""

import argparse
import csv
import json
import os
import signal
import sqlite3
import sys
import unicodedata
from pathlib import Path
from threading import Event
from time import monotonic

from . import __version__
from .store import STATES, Store


def _parser():
    parser = argparse.ArgumentParser(
        prog="python3 -m radar", description="Local opportunity decision workspace"
    )
    parser.add_argument(
        "--workspace",
        default=os.environ.get(
            "RADAR_WORKSPACE",
            str(Path.home() / ".opportunity-radar" / "workspace.sqlite3"),
        ),
        help="Owner-only SQLite workspace path",
    )
    parser.add_argument("--json", action="store_true", help="Print structured output")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("init", "Create an empty private workspace"),
        ("gui", "Open the shared desktop workspace"),
        ("sample", "Add an illustrative offline catalog"),
        ("catalog", "List available public sources"),
        ("sources", "Show configured sources and latest receipts"),
        ("goals", "List saved goals"),
        ("doctor", "Check local database and search support"),
    ):
        sub.add_parser(name, help=help_text)
    src = sub.add_parser(
        "add-source",
        help="Opt in to a catalog source or add a supported source JSON file",
    )
    choices = src.add_mutually_exclusive_group(required=True)
    choices.add_argument("--catalog-id")
    choices.add_argument("--file")
    work = sub.add_parser("collect", help="Collect a bounded public batch and exit")
    work.add_argument("source_id", nargs="?", help="Omit to check enabled sources")
    work.add_argument(
        "--seconds",
        type=float,
        default=15,
        help="Overall network budget in seconds (0.1 to 120)",
    )
    work.add_argument("--max-sources", type=int, default=3)
    receipts = sub.add_parser("receipts", help="Inspect recent source outcomes")
    receipts.add_argument("--limit", type=int, default=30)
    ingest = sub.add_parser(
        "import", help="Add public listing text from a local JSON array"
    )
    ingest.add_argument("file")
    ingest.add_argument("--source-id", default="manual")
    for name in ("search", "export"):
        search = sub.add_parser(
            name,
            help="Explore the local catalog"
            if name == "search"
            else "Export a result page as CSV",
        )
        search.add_argument("query", nargs="?", default="")
        search.add_argument("--goal")
        search.add_argument(
            "--scope", choices=("all", "recommended", "tracked"), default="all"
        )
        search.add_argument("--kind")
        search.add_argument("--limit", type=int, default=50)
        search.add_argument("--offset", type=int, default=0)
    from .feed import PAGE_SIZE, VIEWS

    dashboard = sub.add_parser(
        "feed", help="Read the same feed as the desktop dashboard"
    )
    dashboard.add_argument("query", nargs="?", default="")
    dashboard.add_argument("--view", choices=VIEWS, default="for-you")
    dashboard.add_argument("--goal", help="Choose a goal for this feed request only")
    dashboard.add_argument("--kind")
    dashboard.add_argument("--limit", type=int, default=PAGE_SIZE)
    dashboard.add_argument("--offset", type=int, default=0)
    preferences = sub.add_parser(
        "feed-preferences", help="Choose the default dashboard feed"
    )
    choice = preferences.add_mutually_exclusive_group(required=True)
    choice.add_argument("--goal", help="Personalize with a saved goal")
    choice.add_argument("--recent", action="store_true", help="Use the recent feed")
    hide = sub.add_parser("hide", help="Hide a listing from the feed")
    hide.add_argument("id")
    unhide = sub.add_parser("unhide", help="Restore a hidden listing")
    unhide.add_argument("id")
    unhide.add_argument(
        "--status",
        choices=tuple(state for state in STATES if state != "skip"),
    )
    unhide.add_argument("--token", help="Validate the undo token returned by hide")
    inspect = sub.add_parser("inspect", help="Read listing evidence and history")
    inspect.add_argument("id")
    inspect.add_argument("--goal")
    comparison = sub.add_parser(
        "compare",
        help="Inspect two to six listings side by side in the desktop, or together here",
    )
    comparison.add_argument("ids", nargs="+")
    comparison.add_argument("--goal")
    goal = sub.add_parser(
        "set-goal", help="Create or replace a named goal using local JSON"
    )
    goal.add_argument("name")
    goal.add_argument("file")
    undo = sub.add_parser("undo-goal", help="Undo the latest edit to a goal")
    undo.add_argument("name")
    track = sub.add_parser("track", help="Save, dismiss, or update an application")
    track.add_argument("id")
    track.add_argument("--status", choices=STATES)
    flag = track.add_mutually_exclusive_group()
    flag.add_argument("--save", action="store_true")
    flag.add_argument("--unsave", action="store_true")
    track.add_argument("--note-file", help="Owner-controlled text file, or - for stdin")
    migrate = sub.add_parser(
        "migrate", help="Import a read-only legacy database snapshot"
    )
    migrate.add_argument("legacy_db")
    migrate.add_argument("--config-dir")
    for name in ("backup", "restore"):
        recovery = sub.add_parser(
            name,
            help="Save an offline snapshot"
            if name == "backup"
            else "Restore a validated snapshot",
        )
        recovery.add_argument("file")
    return parser


def _lines(value):
    if (
        isinstance(value, list)
        and value
        and all("title" in item and "observations" not in item for item in value)
    ):
        for item in value:
            extra = f" | fit {item['match']['score']}" if item["match"] else ""
            line = f"{item['id']}  {item['title']} | {item['organization']} | {item['status']}{extra}"
            print(
                "".join(
                    char if unicodedata.category(char) not in {"Cc", "Cf"} else " "
                    for char in line
                )
            )
    elif value == []:
        print("No results.")
    else:
        print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _csv_cell(value):
    text = str(value or "")
    if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(
        ("\t", "\r", "\n")
    ):
        return "'" + text
    return text


def _collect(store, args):
    import math

    from .app import refresh

    if (
        not math.isfinite(args.seconds)
        or not 0.1 <= args.seconds <= 120
        or not 1 <= args.max_sources <= 20
    ):
        raise ValueError("Use 0.1 to 120 seconds and 1 to 20 sources")
    if args.source_id:
        ids = [args.source_id]
    else:
        sources = [
            source
            for source in store.sources()
            if source.get("enabled")
            and source["kind"] not in {"manual", "legacy", "sample"}
        ]
        sources.sort(
            key=lambda source: (
                (source["last_receipt"] or {}).get("finished_at", ""),
                source["id"],
            )
        )
        ids = [source["id"] for source in sources[: args.max_sources]]
    cancel = Event()
    previous = {
        number: signal.signal(number, lambda *_: cancel.set())
        for number in (signal.SIGINT, signal.SIGTERM)
    }
    deadline = monotonic() + args.seconds
    receipts = []
    try:
        for index, source_id in enumerate(ids):
            remaining = deadline - monotonic()
            if cancel.is_set() or (remaining < 0.1 and index > 0):
                break
            # Share the burst across sources rather than letting the first consume it.
            budget = max(0.1, remaining / (len(ids) - index))
            if not args.json:
                print(
                    f"Checking {source_id} ({index + 1}/{len(ids)}, {budget:.1f}s budget)",
                    file=sys.stderr,
                    flush=True,
                )
            receipts.append(refresh(store, source_id, budget, cancel))
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
    failed = {"failed", "timed_out", "cancelled"}
    code = (
        130
        if cancel.is_set()
        else int(bool(receipts) and all(row["state"] in failed for row in receipts))
    )
    return {"receipts": receipts, "deferred": ids[len(receipts) :]}, code


def main(argv=None):
    parser = _parser()
    args = parser.parse_args(argv)
    store = Store(args.workspace)
    try:
        command = args.command
        code = 0
        if command in {"init", "doctor"}:
            with store.transaction() as db:
                fts = db.execute("SELECT count(*) FROM inventory").fetchone()[0]
                check = (
                    db.execute("PRAGMA quick_check").fetchone()[0]
                    if command == "doctor"
                    else "ready"
                )
            result = {"workspace": str(store.path), "check": check, "indexed": fts}
            code = int(check not in {"ok", "ready"})
        elif command == "gui":
            from .gui import main as gui

            return gui(store) or 0
        elif command in {"sample", "import", "compare"}:
            from .app import compare, import_file, seed

            if command == "sample":
                result = seed(store)
            elif command == "import":
                result = import_file(store, args.file, args.source_id)
            else:
                result = compare(store, args.ids, args.goal)
        elif command in {"catalog", "add-source"}:
            from .app import read_json
            from .collection import load_catalog, validate_source

            if command == "catalog":
                result = load_catalog()
            else:
                config = (
                    next(
                        (
                            source
                            for source in load_catalog()
                            if source["id"] == args.catalog_id
                        ),
                        None,
                    )
                    if args.catalog_id
                    else read_json(args.file, 64000)
                )
                if config is None:
                    raise ValueError("Unknown catalog source")
                config = validate_source(config)
                if args.catalog_id:
                    config["enabled"] = True
                result = store.add_source(config)
        elif command == "sources":
            result = store.sources()
        elif command == "receipts":
            result = store.receipts(args.limit)
        elif command == "collect":
            result, code = _collect(store, args)
        elif command in {"search", "export"}:
            result = store.search(
                args.query, args.goal, args.scope, args.kind, args.limit, args.offset
            )
            if command == "export":
                fields = (
                    "id",
                    "title",
                    "organization",
                    "location",
                    "deadline_at",
                    "availability",
                    "status",
                )
                writer = csv.writer(sys.stdout)
                writer.writerow(fields)
                writer.writerows(
                    [_csv_cell(item[field]) for field in fields] for item in result
                )
                return 0
        elif command in {"feed", "feed-preferences", "hide", "unhide"}:
            from . import feed

            if command == "feed":
                result = feed.get_feed(
                    store,
                    args.view,
                    args.query,
                    args.goal,
                    args.kind,
                    args.offset,
                    args.limit,
                )
            elif command == "feed-preferences":
                result = feed.set_preferences(store, args.goal)
            elif command == "hide":
                result = feed.hide(store, args.id)
            else:
                result = feed.undo_hide(store, args.id, args.status, args.token)
        elif command == "inspect":
            result = store.inspect(args.id, args.goal)
        elif command == "goals":
            result = store.goals()
        elif command == "set-goal":
            from .app import read_json

            store.put_goal(args.name, read_json(args.file, 32000))
            result = {"goal": args.name, "updated": True}
        elif command == "undo-goal":
            store.undo_goal(args.name)
            result = {"goal": args.name, "undone": True}
        elif command == "track":
            note = None
            if args.note_file == "-":
                note = sys.stdin.read(8001)
            elif args.note_file:
                from .app import read_file

                note = read_file(args.note_file, 32000).decode("utf-8")
            store.track(
                args.id,
                args.status,
                True if args.save else False if args.unsave else None,
                note,
            )
            result = store.inspect(args.id)
        else:
            from .recovery import backup, migrate, restore

            if command == "migrate":
                result = migrate(store, args.legacy_db, args.config_dir)
            elif command == "backup":
                result = backup(store, args.file)
            else:
                result = restore(store, args.file)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, default=str))
        else:
            _lines(result)
        return code
    except BrokenPipeError:
        return 0
    except (ImportError, OSError, ValueError, sqlite3.Error, RuntimeError) as exc:
        print(f"Radar: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
