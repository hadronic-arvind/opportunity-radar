"""One bounded local request for the native desktop host."""

import argparse
import json
import os
import signal
import sqlite3
import sys
from pathlib import Path
from threading import Event, Thread

from . import app, feed
from .collection import load_catalog
from .recovery import backup, migrate, restore
from .store import Store

MAX_REQUEST = 2 * 1024 * 1024
MAX_RESPONSE = 8 * 1024 * 1024


def _ping():
    return {"ready": True}


def _set_goal(store, name, rules):
    store.put_goal(name, rules)
    return {"name": name.strip()}


def _undo_goal(store, name):
    store.undo_goal(name)
    return {"name": name, "undone": True}


def _sources(store):
    return {
        "configured": store.sources(),
        "catalog": load_catalog(),
        "receipts": store.receipts(30),
    }


def _add_source(store, id=None, config=None, enabled=True):
    if type(enabled) is not bool:
        raise ValueError("Enabled must be true or false")
    if config is None and isinstance(id, str):
        config = next((row for row in store.sources() if row["id"] == id), None)
        if config is None:
            config = next((row for row in load_catalog() if row["id"] == id), None)
    if not isinstance(config, dict):
        raise ValueError("Choose a catalog source or supply a source configuration")
    config = {key: value for key, value in config.items() if key != "last_receipt"}
    return store.add_source({**config, "enabled": enabled})


def _collect(store, source_id=None, seconds=15, id=None):
    source = source_id if source_id is not None else id
    if not isinstance(source, str) or not source:
        raise ValueError("Choose a configured source")
    cancel = Event()
    handlers = {
        number: signal.signal(number, lambda *_: cancel.set())
        for number in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        return app.refresh(store, source, seconds, cancel=cancel)
    finally:
        for number, handler in handlers.items():
            signal.signal(number, handler)


def _import(store, path=None, rows=None, source_id="manual"):
    if (path is None) == (rows is None):
        raise ValueError("Choose a local file or provide records")
    return (
        app.import_file(store, path, source_id)
        if path is not None
        else app.import_rows(store, rows, source_id)
    )


def _inspect(store, id, goal=None):
    detail = store.inspect(id, goal or None)
    detail["history"] = detail["history"][:10]
    return detail


def _restore(store, path):
    return restore(store, path)


def _migrate(store, path, config_dir=None):
    return migrate(store, path, config_dir=config_dir)


def _dispatch(store, request):
    if not isinstance(request, dict):
        raise ValueError("A desktop request must be an object")
    op, args = request.get("op"), request.get("args", {})
    if not isinstance(op, str) or not isinstance(args, dict):
        raise ValueError("Invalid desktop request")
    operations = {
        "ping": _ping,
        "feed": lambda **args: feed.get_feed(store, **args),
        "home": lambda **args: feed.get_feed(store, **args),
        "inspect": lambda **args: _inspect(store, **args),
        "track": lambda **args: feed.track(store, **args),
        "hide": lambda **args: feed.hide(store, **args),
        "undo_hide": lambda **args: feed.undo_hide(store, **args),
        "set_preferences": lambda **args: feed.set_preferences(store, **args),
        "goals": store.goals,
        "set_goal": lambda **args: _set_goal(store, **args),
        "undo_goal": lambda **args: _undo_goal(store, **args),
        "sources": lambda: _sources(store),
        "add_source": lambda **args: _add_source(store, **args),
        "addSource": lambda **args: _add_source(store, **args),
        "refresh": lambda **args: _collect(store, **args),
        "collect": lambda **args: _collect(store, **args),
        "sample": lambda: app.seed(store),
        "compare": lambda **args: app.compare(store, **args),
        "import": lambda **args: _import(store, **args),
        "importListings": lambda **args: _import(store, **args),
        "backup": lambda path: backup(store, path),
        "restore": lambda **args: _restore(store, **args),
        "migrate": lambda **args: _migrate(store, **args),
    }
    function = operations.get(op)
    if function is None:
        raise ValueError("Unknown desktop operation")
    return function(**args)


def _watch_owner(fd):
    # Use the OS file descriptor to avoid holding Python's buffered-reader lock
    # during interpreter shutdown.
    if not os.read(fd, 1):
        os._exit(130)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="radar.desktop_bridge")
    parser.add_argument(
        "--workspace",
        default=os.environ.get(
            "RADAR_WORKSPACE",
            str(Path.home() / ".opportunity-radar" / "workspace.sqlite3"),
        ),
    )
    args = parser.parse_args(argv)
    try:
        line = sys.stdin.buffer.readline(MAX_REQUEST + 1)
        if len(line) > MAX_REQUEST:
            raise ValueError("Desktop request is too large")
        if os.environ.get("RADAR_DESKTOP_WATCH_OWNER") == "1":
            Thread(target=_watch_owner, args=(sys.stdin.fileno(),), daemon=True).start()
        result = _dispatch(Store(args.workspace), json.loads(line))
        output = json.dumps({"ok": True, "result": result}, ensure_ascii=False)
        if len(output.encode("utf-8")) > MAX_RESPONSE:
            raise ValueError("Desktop result is too large")
    except TypeError:
        output = json.dumps(
            {"ok": False, "error": "Invalid desktop operation arguments"}
        )
    except (ValueError, OSError, RuntimeError, sqlite3.Error) as exc:
        output = json.dumps({"ok": False, "error": str(exc)})
    print(output, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
