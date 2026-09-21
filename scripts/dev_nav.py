#!/usr/bin/env python3
"""Bounded, read-only Python navigation without importing application code."""

import argparse
import ast
import itertools
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = ("monitor", "scripts", "tests", "extras/macos-app")
MAX_SOURCE_BYTES = 1024 * 1024
MAX_OUTPUT_CHARS = 16000


def source_paths(root):
    for directory in SOURCE_DIRS:
        parent = root / directory
        if parent.resolve() != parent:
            continue
        for path in sorted(parent.glob("*.py")):
            if not path.is_symlink() and path.is_file():
                yield path


def read_source(path):
    if path.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError(
            "Source exceeds the 1 MiB navigation limit: {}".format(path.name)
        )
    source = path.read_text(encoding="utf-8")
    return source, ast.parse(source, filename=path.name)


def symbols(tree):
    found = []

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.scope = []

        def definition(self, node):
            self.scope.append(node.name)
            found.append((".".join(self.scope), node))
            self.generic_visit(node)
            self.scope.pop()

        visit_ClassDef = definition
        visit_FunctionDef = definition
        visit_AsyncFunctionDef = definition

    Visitor().visit(tree)
    return found


def signature(node):
    if isinstance(node, ast.ClassDef):
        return "class {}".format(node.name)
    arguments = ast.unparse(node.args)
    result = "{}({})".format(node.name, arguments)
    if node.returns is not None:
        result += " -> " + ast.unparse(node.returns)
    return result[:240]


def map_rows(root, paths):
    for path in paths:
        source, tree = read_source(path)
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imports.add("." * node.level + (node.module or ""))
            elif isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
        local = sorted(name for name in imports if name.startswith(("monitor", ".")))
        yield "{}: {} lines, {} symbols; local imports: {}".format(
            path.relative_to(root).as_posix(),
            len(source.splitlines()),
            len(symbols(tree)),
            ", ".join(local) or "-",
        )


def symbol_rows(root, paths, query):
    for path in paths:
        _, tree = read_source(path)
        for name, node in symbols(tree):
            if query.casefold() in name.casefold():
                yield "{}:{}-{} {} | {}".format(
                    path.relative_to(root).as_posix(),
                    node.lineno,
                    node.end_lineno,
                    name,
                    signature(node),
                )


def show_rows(root, path, query):
    source, tree = read_source(path)
    matches = [(name, node) for name, node in symbols(tree) if name == query]
    if len(matches) != 1:
        raise ValueError(
            "Expected one exact qualified symbol; use symbols to locate it"
        )
    _, node = matches[0]
    start = min([node.lineno] + [item.lineno for item in node.decorator_list])
    lines = source.splitlines()
    for index in range(start - 1, node.end_lineno):
        yield "{}:{}: {}".format(
            path.relative_to(root).as_posix(), index + 1, lines[index]
        )


def reference_rows(root, paths, query):
    """Find identifier spellings, including attributes/imports, not resolved callers."""
    for path in paths:
        source, tree = read_source(path)
        matches = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == query:
                matches.add(node.lineno)
            elif isinstance(node, ast.Attribute) and node.attr == query:
                matches.add(node.lineno)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                if any(query in (alias.name, alias.asname) for alias in node.names):
                    matches.add(node.lineno)
        lines = source.splitlines()
        for line in sorted(matches):
            yield "{}:{}: {}".format(
                path.relative_to(root).as_posix(), line, lines[line - 1].strip()
            )


def bounded_output(rows, limit, offset):
    used = 0
    for index, row in enumerate(itertools.islice(rows, offset, None)):
        if index >= limit or used + len(row) + 1 > MAX_OUTPUT_CHARS:
            print("[output limited; narrow --path/--query or increase --offset]")
            return
        print(row)
        used += len(row) + 1


def main(argv=None, root=ROOT):
    root = root.resolve()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("map", "symbols", "show", "refs"))
    parser.add_argument("--path", help="Exact repository-relative Python source path")
    parser.add_argument(
        "--query", default="", help="Symbol name or identifier spelling"
    )
    parser.add_argument("--limit", type=int, default=60, help="Output lines, 1-200")
    parser.add_argument(
        "--offset", type=int, default=0, help="Skip this many result lines"
    )
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 200 or args.offset < 0:
        parser.error("--limit must be 1-200 and --offset must be nonnegative")
    if args.command in ("show", "refs") and not args.query:
        parser.error("show and refs require --query")
    if args.command == "refs" and not args.query.isidentifier():
        parser.error("refs requires a single identifier spelling")
    if args.command == "show" and not args.path:
        parser.error("show requires --path")
    paths = list(source_paths(root))
    if args.path:
        paths = [
            path for path in paths if path.relative_to(root).as_posix() == args.path
        ]
        if not paths:
            parser.error(
                "--path must name a regular Python file in a public source directory"
            )
    try:
        if args.command == "map":
            rows = map_rows(root, paths)
        elif args.command == "symbols":
            rows = symbol_rows(root, paths, args.query)
        elif args.command == "show":
            rows = show_rows(root, paths[0], args.query)
        else:
            rows = reference_rows(root, paths, args.query)
        bounded_output(rows, args.limit, args.offset)
    except (OSError, UnicodeError, SyntaxError, ValueError) as error:
        parser.exit(1, "Navigation failed: {}\n".format(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
