import importlib.util
import plistlib
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


def load_installer():
    project = Path(__file__).resolve().parents[1]
    script = project / "extras" / "macos-app" / "install.py"
    spec = importlib.util.spec_from_file_location("install_macos_app", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return project, module


class MacOSAppTests(unittest.TestCase):
    def test_builds_signed_private_native_bundle_and_replaces_cleanly(self):
        project, module = load_installer()

        def fake_compile(source, executable, swiftc, module_cache):
            executable.write_text("native-binary", encoding="utf-8")
            executable.chmod(0o700)

        def fake_render(executable, directory):
            directory.mkdir(parents=True)
            images = {}
            for pixels in sorted(set(size for _tag, size in module.ICON_CHUNKS)):
                output = directory / "icon-{}.png".format(pixels)
                output.write_bytes(b"PNG" + str(pixels).encode("ascii"))
                images[pixels] = output
            return images

        def fake_sign(app):
            (app / "Contents" / "signature-test").write_text("signed", encoding="utf-8")

        with tempfile.TemporaryDirectory() as tempdir:
            destination = Path(tempdir) / "Opportunity Radar.app"
            with (
                patch.object(module, "compile_app", side_effect=fake_compile),
                patch.object(module, "render_icons", side_effect=fake_render),
                patch.object(module, "sign_app", side_effect=fake_sign),
                patch.object(module, "verify_app"),
            ):
                installed = module.build_app(
                    destination,
                    project_root=project,
                    python_executable=Path("/usr/bin/python3"),
                    swiftc=Path("/usr/bin/true"),
                    runtime_root=project,
                )
                self.assertEqual(installed, destination.resolve())
                info_path = destination / "Contents" / "Info.plist"
                with info_path.open("rb") as handle:
                    info = plistlib.load(handle)
                self.assertEqual(
                    info["CFBundleIdentifier"],
                    "io.github.opportunity-radar.dashboard",
                )
                self.assertNotIn("LSUIElement", info)
                config_path = destination / "Contents" / "Resources" / "config.plist"
                with config_path.open("rb") as handle:
                    config = plistlib.load(handle)
                self.assertEqual(
                    config["pythonExecutable"],
                    str(Path("/usr/bin/python3").resolve()),
                )
                self.assertEqual(config["runtimeRoot"], str(project))
                executable = destination / "Contents" / "MacOS" / "opportunity-radar"
                self.assertEqual(executable.stat().st_mode & 0o777, 0o700)
                self.assertEqual(config_path.stat().st_mode & 0o777, 0o600)
                self.assertTrue((destination / "Contents" / "signature-test").is_file())
                icon = destination / "Contents" / "Resources" / "OpportunityRadar.icns"
                self.assertEqual(icon.read_bytes()[:4], b"icns")

                marker = destination / "stale-file"
                marker.write_text("stale", encoding="utf-8")
                module.build_app(
                    destination,
                    project_root=project,
                    python_executable=Path("/usr/bin/python3"),
                    swiftc=Path("/usr/bin/true"),
                    runtime_root=project,
                )
                self.assertFalse(marker.exists())

    def test_desktop_shortcut_is_explicit_and_refuses_existing_content(self):
        _project, module = load_installer()
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            app = root / "Applications" / "Opportunity Radar.app"
            app.mkdir(parents=True)
            shortcut = root / "Desktop" / "Opportunity Radar.app"
            installed = module.install_desktop_shortcut(app, shortcut)
            self.assertEqual(installed, shortcut)
            self.assertTrue(shortcut.is_symlink())
            self.assertEqual(shortcut.resolve(), app.resolve())
            self.assertEqual(module.install_desktop_shortcut(app, shortcut), shortcut)
            shortcut.unlink()
            shortcut.write_text("unrelated", encoding="utf-8")
            with self.assertRaisesRegex(FileExistsError, "already exists"):
                module.install_desktop_shortcut(app, shortcut)

    def test_icns_writer_uses_bounded_declared_chunks(self):
        _project, module = load_installer()
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            images = {}
            for pixels in sorted(set(size for _tag, size in module.ICON_CHUNKS)):
                path = root / "{}.png".format(pixels)
                path.write_bytes("image-{}".format(pixels).encode("ascii"))
                images[pixels] = path
            output = root / "icon.icns"
            module.write_icns(images, output)
            payload = output.read_bytes()
            self.assertEqual(payload[:4], b"icns")
            self.assertEqual(struct.unpack(">I", payload[4:8])[0], len(payload))
            self.assertEqual(payload[8:12], module.ICON_CHUNKS[0][0].encode("ascii"))

    def test_refuses_symbolic_link_destination_before_build(self):
        project, module = load_installer()
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            target = root / "target"
            target.mkdir()
            destination = root / "Opportunity Radar.app"
            destination.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symbolic-link"):
                module.build_app(
                    destination,
                    project_root=project,
                    swiftc=Path("/usr/bin/true"),
                    runtime_root=project,
                )
            self.assertTrue(destination.is_symlink())

    def test_refuses_symbolic_link_destination_parent(self):
        project, module = load_installer()
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            real_parent = root / "real-parent"
            real_parent.mkdir()
            linked_parent = root / "linked-parent"
            linked_parent.symlink_to(real_parent, target_is_directory=True)
            destination = linked_parent / "Opportunity Radar.app"
            with self.assertRaisesRegex(ValueError, "symbolic-link parent"):
                module.validate_destination(destination, project)

    def test_refuses_symbolic_link_in_destination_ancestry(self):
        project, module = load_installer()
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            real_parent = root / "real-parent"
            real_parent.mkdir()
            linked_parent = root / "linked-parent"
            linked_parent.symlink_to(real_parent, target_is_directory=True)
            destination = linked_parent / "nested" / "Opportunity Radar.app"
            with self.assertRaisesRegex(ValueError, "symbolic-link parent"):
                module.validate_destination(destination, project)

    def test_refuses_unrelated_existing_application_or_directory(self):
        project, module = load_installer()
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            unrelated = root / "Unrelated.app"
            contents = unrelated / "Contents"
            contents.mkdir(parents=True)
            with (contents / "Info.plist").open("wb") as handle:
                plistlib.dump({"CFBundleIdentifier": "example.unrelated"}, handle)
            with self.assertRaisesRegex(ValueError, "unrelated application bundle"):
                module.validate_destination(unrelated, project)
            self.assertTrue(unrelated.is_dir())

            plain = root / "Plain.app"
            plain.mkdir()
            with self.assertRaisesRegex(ValueError, "unrelated application directory"):
                module.validate_destination(plain, project)
            self.assertTrue(plain.is_dir())

    def test_refuses_repository_contained_app_destination(self):
        project, module = load_installer()
        destination = project / "local-build" / "Opportunity Radar.app"
        with self.assertRaisesRegex(ValueError, "inside the repository"):
            module.validate_destination(destination, project)

    def test_compile_links_appkit_and_webkit(self):
        _project, module = load_installer()
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            source = root / "App.swift"
            source.write_text("", encoding="utf-8")
            with patch.object(module.subprocess, "run") as run:
                module.compile_app(
                    source,
                    root / "app",
                    Path("/usr/bin/swiftc"),
                    root / "module-cache",
                )
            command = run.call_args.args[0]
            self.assertIn("AppKit", command)
            self.assertIn("WebKit", command)

    def test_reserved_app_backup_fails_before_compilation(self):
        project, module = load_installer()
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            destination = root / "Opportunity Radar.app"
            backup = root / ".Opportunity Radar.app.previous-1234"
            backup.mkdir()
            with (
                patch.object(module.os, "getpid", return_value=1234),
                patch.object(module, "compile_app") as compile_app,
            ):
                with self.assertRaisesRegex(FileExistsError, "backup path"):
                    module.build_app(
                        destination,
                        project_root=project,
                        swiftc=Path("/usr/bin/true"),
                        runtime_root=project,
                    )
            compile_app.assert_not_called()

    def test_prefers_valid_private_runtime_and_falls_back_to_clone(self):
        project, module = load_installer()
        with tempfile.TemporaryDirectory() as tempdir:
            runtime = Path(tempdir) / "runtime"
            (runtime / "monitor").mkdir(parents=True)
            (runtime / "monitor" / "__main__.py").write_text("", encoding="utf-8")
            self.assertEqual(module.select_runtime_root(project, runtime), runtime.resolve())
            (runtime / "monitor" / "__main__.py").unlink()
            self.assertEqual(module.select_runtime_root(project, runtime), project)


if __name__ == "__main__":
    unittest.main()
