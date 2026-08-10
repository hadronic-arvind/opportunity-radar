#!/usr/bin/env python3
"""Build and install the optional native Opportunity Radar macOS app."""

import argparse
import os
import platform
import plistlib
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SWIFT_SOURCE = Path(__file__).resolve().with_name("OpportunityRadar.swift")
DEFAULT_DESTINATION = Path.home() / "Applications" / "Opportunity Radar.app"
DEFAULT_DESKTOP_SHORTCUT = Path.home() / "Desktop" / "Opportunity Radar.app"
DEFAULT_RUNTIME_ROOT = (
    Path.home() / "Library" / "Application Support" / "OpportunityRadar"
)
EXPECTED_BUNDLE_IDENTIFIER = "io.github.opportunity-radar.dashboard"
ICON_CHUNKS = (
    ("icp4", 16),
    ("icp5", 32),
    ("icp6", 64),
    ("ic07", 128),
    ("ic08", 256),
    ("ic09", 512),
    ("ic10", 1024),
    ("ic11", 32),
    ("ic12", 64),
    ("ic13", 256),
    ("ic14", 512),
)


def bundle_info() -> dict:
    return {
        "CFBundleDevelopmentRegion": "en",
        "CFBundleDisplayName": "Opportunity Radar",
        "CFBundleExecutable": "opportunity-radar",
        "CFBundleIconFile": "OpportunityRadar.icns",
        "CFBundleIdentifier": EXPECTED_BUNDLE_IDENTIFIER,
        "CFBundleName": "Opportunity Radar",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "2.0",
        "CFBundleVersion": "3",
        "LSApplicationCategoryType": "public.app-category.productivity",
        "LSMinimumSystemVersion": "11.0",
        "NSHighResolutionCapable": True,
        "NSPrincipalClass": "NSApplication",
        "NSQuitAlwaysKeepsWindows": False,
    }


def app_config(runtime_root: Path, python_executable: Path) -> dict:
    return {
        "runtimeRoot": str(runtime_root.resolve()),
        "pythonExecutable": str(python_executable.resolve()),
    }


def select_runtime_root(project_root: Path, installed_runtime: Path) -> Path:
    """Prefer the scheduler's private runtime when it is installed and valid."""
    for candidate in (installed_runtime, project_root):
        resolved = candidate.expanduser().resolve()
        if (resolved / "monitor" / "__main__.py").is_file():
            return resolved
    raise FileNotFoundError("The Opportunity Radar runtime is unavailable")


def write_icns(images: Dict[int, Path], output: Path) -> None:
    chunks = []
    for tag, pixels in ICON_CHUNKS:
        data = images[pixels].read_bytes()
        chunks.append(tag.encode("ascii") + struct.pack(">I", len(data) + 8) + data)
    body = b"".join(chunks)
    output.write_bytes(b"icns" + struct.pack(">I", len(body) + 8) + body)


def compile_app(source: Path, executable: Path, swiftc: Path, module_cache: Path) -> None:
    environment = dict(os.environ)
    environment["MACOSX_DEPLOYMENT_TARGET"] = "11.0"
    subprocess.run(
        [
            str(swiftc),
            "-module-cache-path",
            str(module_cache),
            "-O",
            "-framework",
            "AppKit",
            "-framework",
            "WebKit",
            str(source),
            "-o",
            str(executable),
        ],
        check=True,
        env=environment,
    )


def render_icons(executable: Path, directory: Path) -> Dict[int, Path]:
    directory.mkdir(parents=True)
    images = {}
    for pixels in sorted(set(size for _tag, size in ICON_CHUNKS)):
        output = directory / "icon-{}.png".format(pixels)
        subprocess.run(
            [str(executable), "--render-icon", str(output), str(pixels)],
            check=True,
        )
        images[pixels] = output
    return images


def sign_app(app: Path) -> None:
    cleanup = subprocess.run(
        ["/usr/bin/xattr", "-cr", str(app)],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if cleanup.returncode:
        details = cleanup.stderr.strip() or "unknown xattr error"
        raise RuntimeError(
            "Unable to remove generated-file metadata before signing: {}".format(
                details[-1200:]
            )
        )
    run_codesign(
        [
            "/usr/bin/codesign",
            "--force",
            "--deep",
            "--sign",
            "-",
            "--timestamp=none",
            str(app),
        ]
    )
    verify_app(app)


def run_codesign(command) -> None:
    completed = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode:
        details = completed.stderr.strip() or "unknown codesign error"
        raise RuntimeError("macOS code signing failed: {}".format(details[-1200:]))


def verify_app(app: Path) -> None:
    run_codesign(
        ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)]
    )


def install_desktop_shortcut(
    app: Path,
    shortcut: Path = DEFAULT_DESKTOP_SHORTCUT,
) -> Path:
    app = app.expanduser().resolve()
    shortcut = Path(os.path.abspath(str(shortcut.expanduser())))
    if shortcut.is_symlink():
        if shortcut.resolve() == app:
            return shortcut
        raise FileExistsError("A different Desktop shortcut already exists")
    if shortcut.exists():
        raise FileExistsError(
            "The Desktop destination already exists; move it aside and retry"
        )
    shortcut.parent.mkdir(parents=True, exist_ok=True)
    staged = shortcut.with_name(".{}.shortcut-{}".format(shortcut.name, os.getpid()))
    if staged.exists() or staged.is_symlink():
        raise FileExistsError("The temporary Desktop shortcut path already exists")
    try:
        staged.symlink_to(app, target_is_directory=True)
        os.replace(staged, shortcut)
    finally:
        if staged.is_symlink():
            staged.unlink()
    return shortcut


def _require_owned_private_path(path: Path, label: str) -> None:
    details = path.stat()
    if details.st_uid != os.getuid():
        raise ValueError("{} is not owned by the current user".format(label))
    if details.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise ValueError("{} is writable by another user".format(label))


def _existing_bundle_identifier(destination: Path) -> str:
    contents = destination / "Contents"
    info = contents / "Info.plist"
    if contents.is_symlink() or not contents.is_dir():
        raise ValueError("Refusing to replace an unrelated application directory")
    if info.is_symlink() or not info.is_file():
        raise ValueError("Refusing to replace an unrelated application directory")
    _require_owned_private_path(contents, "existing application Contents directory")
    _require_owned_private_path(info, "existing application Info.plist")
    with info.open("rb") as handle:
        payload = plistlib.load(handle)
    identifier = payload.get("CFBundleIdentifier") if isinstance(payload, dict) else None
    if identifier != EXPECTED_BUNDLE_IDENTIFIER:
        raise ValueError("Refusing to replace an unrelated application bundle")
    return str(identifier)


def validate_destination(destination: Path, project_root: Path = PROJECT_ROOT) -> Path:
    if destination.suffix != ".app":
        raise ValueError("The destination must end in .app")
    if destination.is_symlink():
        raise ValueError("Refusing to replace a symbolic-link destination")
    resolved_destination = destination.resolve(strict=False)
    resolved_project = project_root.resolve()
    if (
        resolved_destination == resolved_project
        or resolved_project in resolved_destination.parents
        or resolved_destination in resolved_project.parents
    ):
        raise ValueError("Refusing to install an application inside the repository")
    requested_parent = destination.parent
    if requested_parent.is_symlink():
        raise ValueError("Refusing to install through a symbolic-link parent")
    existing_parent = requested_parent
    while not existing_parent.exists():
        if existing_parent.is_symlink():
            raise ValueError("Refusing to install through a symbolic-link parent")
        if existing_parent.parent == existing_parent:
            raise ValueError("The application destination has no existing parent")
        existing_parent = existing_parent.parent
    if existing_parent.is_symlink():
        raise ValueError("Refusing to install through a symbolic-link parent")
    parent = existing_parent.resolve()
    if not parent.is_dir():
        raise ValueError("The application destination parent must be a directory")
    _require_owned_private_path(parent, "application destination parent")
    if destination.exists() and not destination.is_dir():
        raise ValueError("Refusing to replace a non-application file")
    if destination.exists():
        _require_owned_private_path(destination, "existing application")
        _existing_bundle_identifier(destination)
    return resolved_destination


def build_app(
    destination: Path,
    project_root: Path = PROJECT_ROOT,
    python_executable: Optional[Path] = None,
    swiftc: Optional[Path] = None,
    runtime_root: Optional[Path] = None,
) -> Path:
    destination = Path(os.path.abspath(str(destination.expanduser())))
    project_root = project_root.resolve()
    runtime_root = select_runtime_root(
        project_root,
        runtime_root or DEFAULT_RUNTIME_ROOT,
    )
    python_executable = (python_executable or Path(sys.executable)).resolve()
    swiftc = swiftc or Path(shutil.which("swiftc") or "")
    destination = validate_destination(destination, project_root)
    if not SWIFT_SOURCE.is_file():
        raise FileNotFoundError("The optional macOS app source is unavailable")
    if not swiftc or not swiftc.is_file():
        raise FileNotFoundError(
            "Swift is unavailable. Install Apple's Command Line Tools with xcode-select --install."
        )
    if not (project_root / "monitor" / "__main__.py").is_file():
        raise FileNotFoundError("The Opportunity Radar project is unavailable")

    backup = destination.with_name(".{}.previous-{}".format(destination.name, os.getpid()))
    if backup.exists() or backup.is_symlink():
        raise FileExistsError("Application backup path already exists")
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _require_owned_private_path(destination.parent, "application destination parent")
    stage_root = Path(
        tempfile.mkdtemp(prefix=".opportunity-radar-app-", dir=str(destination.parent))
    )
    staged_app = stage_root / destination.name
    contents = staged_app / "Contents"
    macos = contents / "MacOS"
    resources = contents / "Resources"
    executable = macos / "opportunity-radar"
    compiled_executable = stage_root / "opportunity-radar"
    replaced = False
    installed = False
    try:
        macos.mkdir(parents=True)
        resources.mkdir(parents=True)
        with (contents / "Info.plist").open("wb") as handle:
            plistlib.dump(bundle_info(), handle, sort_keys=False)
        with (resources / "config.plist").open("wb") as handle:
            plistlib.dump(
                app_config(runtime_root, python_executable),
                handle,
                sort_keys=False,
            )
        compile_app(
            SWIFT_SOURCE,
            compiled_executable,
            swiftc,
            stage_root / "module-cache",
        )
        images = render_icons(compiled_executable, stage_root / "icons")
        write_icns(images, resources / "OpportunityRadar.icns")
        os.replace(compiled_executable, executable)

        executable.chmod(0o700)
        (contents / "Info.plist").chmod(0o600)
        (resources / "config.plist").chmod(0o600)
        (resources / "OpportunityRadar.icns").chmod(0o600)
        for directory in (staged_app, contents, macos, resources):
            directory.chmod(0o700)
        sign_app(staged_app)

        if destination.exists():
            if backup.exists() or backup.is_symlink():
                raise FileExistsError("Application backup path already exists")
            os.replace(destination, backup)
            replaced = True
        os.replace(staged_app, destination)
        installed = True
        verify_app(destination)
        if backup.exists():
            shutil.rmtree(backup)
        return destination
    except Exception:
        if installed and destination.exists():
            shutil.rmtree(destination)
        if replaced and backup.exists():
            if not destination.exists():
                os.replace(backup, destination)
        raise
    finally:
        if stage_root.exists():
            shutil.rmtree(stage_root)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the optional native Opportunity Radar app on macOS."
    )
    parser.add_argument(
        "--destination",
        type=Path,
        default=DEFAULT_DESTINATION,
        help="App destination (default: ~/Applications/Opportunity Radar.app)",
    )
    parser.add_argument(
        "--desktop-shortcut",
        action="store_true",
        help="Create an optional Desktop shortcut to the installed app",
    )
    args = parser.parse_args()
    if platform.system() != "Darwin":
        parser.error("the optional native app supports macOS only")
    destination = build_app(args.destination)
    print("Installed {}".format(destination))
    if args.desktop_shortcut:
        shortcut = install_desktop_shortcut(destination)
        print("Created {}".format(shortcut))
    print("The CLI remains available and independent of the optional app.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
