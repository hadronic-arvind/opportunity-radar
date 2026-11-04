#!/usr/bin/env python3
"""Build the macOS desktop app and optionally retarget an owned Desktop shortcut."""

import argparse
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[2]
APP_NAME = "Opportunity Radar.app"
BUNDLE_ID = "io.github.opportunity-radar.feed"
LEGACY_ID = "io.github.opportunity-radar.dashboard"
DEFAULT_APP = Path.home() / "Applications" / "Opportunity Radar Dashboard.app"
DEFAULT_LEGACY = Path.home() / "Applications" / APP_NAME
DEFAULT_SHORTCUT = Path.home() / "Desktop" / APP_NAME
ICON_NAME = "OpportunityRadar.icns"
SWIFT_SOURCE = Path(__file__).with_name("RadarDesktop.swift")
INTERFACE = PROJECT / "radar" / "desktop" / "index.html"
BRIDGE = PROJECT / "radar" / "desktop_bridge.py"


def bundle_id(app):
    try:
        with (app / "Contents" / "Info.plist").open("rb") as file:
            return plistlib.load(file).get("CFBundleIdentifier")
    except (
        OSError,
        plistlib.InvalidFileException,
        ValueError,
        TypeError,
        AttributeError,
    ):
        return None


def verify_bundle(app):
    subprocess.run(
        ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)], check=True
    )


def write_icon(output, legacy_app):
    existing_icon = legacy_app / "Contents" / "Resources" / ICON_NAME
    if bundle_id(legacy_app) == LEGACY_ID and existing_icon.is_file():
        # Copy bytes only. The new bundle receives its own signature and metadata.
        shutil.copyfile(existing_icon, output)
        return
    source = PROJECT / "assets" / "opportunity-radar-icon-v2.png"
    with tempfile.TemporaryDirectory(prefix=".radar-icon-", dir=output.parent) as name:
        iconset = Path(name) / "OpportunityRadar.iconset"
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                pixels = size * scale
                suffix = "@2x" if scale == 2 else ""
                image = iconset / f"icon_{size}x{size}{suffix}.png"
                subprocess.run(
                    [
                        "/usr/bin/sips",
                        "-z",
                        str(pixels),
                        str(pixels),
                        str(source),
                        "--out",
                        str(image),
                    ],
                    check=True,
                    stdout=subprocess.DEVNULL,
                )
        subprocess.run(
            ["/usr/bin/iconutil", "-c", "icns", str(iconset), "-o", str(output)],
            check=True,
        )


def build(app, python, legacy_app):
    if sys.platform != "darwin":
        raise RuntimeError("The native desktop app requires macOS.")
    if not INTERFACE.is_file() or not BRIDGE.is_file():
        raise RuntimeError("The Radar desktop interface is missing from this checkout.")
    if app.suffix != ".app" or app.is_symlink():
        raise RuntimeError("Choose an app bundle path with a .app extension.")
    if app.exists() and bundle_id(app) != BUNDLE_ID:
        raise RuntimeError("The destination already contains another application.")
    python = python.expanduser().resolve(strict=True)
    subprocess.run(
        [
            str(python),
            "-I",
            "-c",
            "import sqlite3, sys; assert sys.version_info >= (3, 9); "
            "c = sqlite3.connect(':memory:'); "
            "c.execute('CREATE VIRTUAL TABLE search USING fts5(text)'); "
            "c.execute(\"SELECT json('{}')\").fetchone()",
        ],
        check=True,
    )

    app.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".radar-desktop-", dir=app.parent) as name:
        stage = Path(name)
        staged_app = stage / APP_NAME
        contents = staged_app / "Contents"
        resources = contents / "Resources"
        macos = contents / "MacOS"
        resources.mkdir(parents=True)
        macos.mkdir()
        plist = {
            "CFBundleDevelopmentRegion": "en",
            "CFBundleDisplayName": "Opportunity Radar",
            "CFBundleExecutable": "opportunity-radar",
            "CFBundleIconFile": ICON_NAME,
            "CFBundleIdentifier": BUNDLE_ID,
            "CFBundleName": "Opportunity Radar",
            "CFBundlePackageType": "APPL",
            "CFBundleShortVersionString": "1.1.0",
            "CFBundleVersion": "1",
            "LSApplicationCategoryType": "public.app-category.productivity",
            "LSMinimumSystemVersion": "11.0",
            "NSHighResolutionCapable": True,
            "NSPrincipalClass": "NSApplication",
            "NSQuitAlwaysKeepsWindows": False,
        }
        with (contents / "Info.plist").open("wb") as file:
            plistlib.dump(plist, file)
        with (resources / "desktop.json").open("w", encoding="utf-8") as file:
            json.dump({"projectRoot": str(PROJECT), "python": str(python)}, file)
        write_icon(resources / ICON_NAME, legacy_app)
        subprocess.run(
            [
                "/usr/bin/swiftc",
                "-module-cache-path",
                str(stage / "swift-cache"),
                "-O",
                "-framework",
                "AppKit",
                "-framework",
                "WebKit",
                "-framework",
                "UniformTypeIdentifiers",
                str(SWIFT_SOURCE),
                "-o",
                str(macos / "opportunity-radar"),
            ],
            check=True,
            env={**os.environ, "MACOSX_DEPLOYMENT_TARGET": "11.0"},
        )
        subprocess.run(["/usr/bin/xattr", "-cr", str(staged_app)], check=True)
        subprocess.run(
            [
                "/usr/bin/codesign",
                "--force",
                "--sign",
                "-",
                "--timestamp=none",
                str(staged_app),
            ],
            check=True,
        )
        verify_bundle(staged_app)
        previous = stage / "previous.app"
        if app.exists():
            if bundle_id(app) != BUNDLE_ID:
                raise RuntimeError("The app destination changed during the build.")
            os.rename(app, previous)
        try:
            os.replace(staged_app, app)
        except OSError:
            if previous.exists():
                os.rename(previous, app)
            raise
    return app


def retarget(shortcut, app):
    app = app.resolve(strict=True)
    if bundle_id(app) not in {BUNDLE_ID, LEGACY_ID}:
        raise RuntimeError("The requested target is not an Opportunity Radar app.")
    verify_bundle(app)

    def current_target():
        if not shortcut.is_symlink():
            if shortcut.exists():
                raise RuntimeError(
                    "The Desktop path contains an item other than a shortcut."
                )
            return None
        current = shortcut.resolve()
        if bundle_id(current) not in {BUNDLE_ID, LEGACY_ID}:
            raise RuntimeError("The shortcut points to another application.")
        return os.readlink(shortcut)

    original = current_target()
    if original is not None and shortcut.resolve() == app:
        return
    with tempfile.TemporaryDirectory(
        prefix=".radar-shortcut-", dir=shortcut.parent
    ) as name:
        staged = Path(name) / shortcut.name
        staged.symlink_to(app, target_is_directory=True)
        if current_target() != original:
            raise RuntimeError(
                "The Desktop shortcut changed before it could be updated."
            )
        os.replace(staged, shortcut)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    package = sub.add_parser("build", help="Build and verify a local app bundle")
    package.add_argument("--app", type=Path, default=DEFAULT_APP)
    package.add_argument("--python", type=Path, default=Path(sys.executable))
    package.add_argument("--icon-from", type=Path, default=DEFAULT_LEGACY)
    shortcut = sub.add_parser(
        "link", help="Point a Finder shortcut at a verified Radar app"
    )
    shortcut.add_argument("--app", type=Path, default=DEFAULT_APP)
    shortcut.add_argument("--shortcut", type=Path, default=DEFAULT_SHORTCUT)
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        if args.operation == "build":
            print(
                build(
                    args.app.expanduser().absolute(),
                    args.python,
                    args.icon_from.expanduser(),
                )
            )
        else:
            retarget(args.shortcut.expanduser().absolute(), args.app.expanduser())
            print(args.shortcut.expanduser())
        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Radar desktop: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
