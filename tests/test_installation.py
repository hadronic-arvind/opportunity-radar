import argparse
import importlib.util
import io
import os
import platform
import plistlib
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


def load_script(name, filename):
    project = Path(__file__).resolve().parents[1]
    script = project / "scripts" / filename
    spec = importlib.util.spec_from_file_location(name, script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return project, module


def private_file(path, content=b""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    path.chmod(0o600)


def private_database(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE state(value TEXT)")
    connection.commit()
    connection.close()
    path.chmod(0o600)


def private_executable(path):
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o700)
    return path


def project_artifact_tree(root):
    project = root / "project"
    for name in ("data", "dashboard", "logs"):
        (project / name).mkdir(parents=True, mode=0o700)
    return project


def private_runtime(path, version):
    markers = (
        "monitor/__main__.py",
        "config/profile.json",
        "dashboard/template.html",
        "scripts/run_monitor.sh",
    )
    for marker in markers:
        private_file(path / marker, version.encode("utf-8"))
    private_file(path / "version.txt", version.encode("utf-8"))
    for directory in (path, *path.rglob("*")):
        if directory.is_dir():
            directory.chmod(0o700)
    return path


class InstallationTests(unittest.TestCase):
    def test_rendered_launch_agent_is_low_priority_and_twice_daily(self):
        project = Path(__file__).resolve().parents[1]
        script = project / "scripts" / "render_launch_agent.py"
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            runtime = root / "Runtime & Data"
            output = root / "agent.plist"
            subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "--runtime",
                    str(runtime),
                    "--output",
                    str(output),
                ],
                check=True,
            )
            with output.open("rb") as handle:
                payload = plistlib.load(handle)
            self.assertEqual(payload["Label"], "io.github.opportunity-radar.monitor")
            self.assertEqual(
                payload["StartCalendarInterval"],
                [{"Hour": 7, "Minute": 30}, {"Hour": 16, "Minute": 30}],
            )
            self.assertEqual(payload["ProcessType"], "Background")
            self.assertTrue(payload["LowPriorityIO"])
            self.assertEqual(payload["Nice"], 10)
            self.assertNotIn("KeepAlive", payload)
            self.assertNotIn("StartInterval", payload)
            self.assertEqual(payload["WorkingDirectory"], str(runtime))
            self.assertIn("Runtime & Data", payload["ProgramArguments"][0])
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)

    def test_database_copy_uses_sqlite_backup(self):
        project = Path(__file__).resolve().parents[1]
        script = project / "scripts" / "copy_database.py"
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            source = root / "Source & State.sqlite3"
            destination = root / "Destination.sqlite3"
            connection = sqlite3.connect(source)
            connection.execute("CREATE TABLE state(value TEXT)")
            connection.execute("INSERT INTO state VALUES ('apply')")
            connection.commit()
            connection.close()
            subprocess.run([sys.executable, str(script), str(source), str(destination)], check=True)
            copied = sqlite3.connect(destination)
            try:
                self.assertEqual(copied.execute("SELECT value FROM state").fetchone()[0], "apply")
            finally:
                copied.close()
            self.assertEqual(destination.stat().st_mode & 0o777, 0o600)

    def test_cron_fallback_preserves_unmanaged_entries(self):
        _project, module = load_script("manage_cron", "manage_cron.py")
        existing = "15 3 * * * /usr/bin/example\n"
        runtime = Path("/tmp/Runtime & Data")
        rendered = module.build_crontab(existing, runtime, [(7, 30), (16, 30)])
        self.assertIn(existing.strip(), rendered)
        self.assertEqual(rendered.count(module.BEGIN), 1)
        self.assertEqual(rendered.count(module.END), 1)
        self.assertIn("30 7 * * *", rendered)
        self.assertIn("30 16 * * *", rendered)
        self.assertIn("'" + str(runtime / "scripts" / "run_monitor.sh") + "'", rendered)
        updated = module.build_crontab(rendered, runtime, [(8, 0), (17, 0)])
        self.assertEqual(updated.count(module.BEGIN), 1)
        self.assertNotIn("30 7 * * *", updated)

    def test_cron_fallback_writes_schedule_through_stdin(self):
        _project, module = load_script("manage_cron_write", "manage_cron.py")
        content = "30 7 * * * /usr/bin/example\n"
        with patch.object(module.subprocess, "run") as run:
            module.write_crontab(content)
        run.assert_called_once_with(
            [module.CRONTAB, "-"], input=content, text=True, check=True
        )

    def test_cron_fallback_rejects_malformed_markers_without_returning_content(self):
        _project, module = load_script("manage_cron_malformed", "manage_cron.py")
        unrelated = "17 4 * * * /usr/bin/unrelated"
        malformed = (
            "{}\n{}".format(module.BEGIN, unrelated),
            "{}\n{}".format(module.END, unrelated),
            "{}\n{}\n{}\n{}".format(module.BEGIN, module.BEGIN, module.END, unrelated),
            "{}\n{}\n{}\n{}\n{}".format(
                module.BEGIN, module.END, module.BEGIN, module.END, unrelated
            ),
        )
        for content in malformed:
            with self.subTest(content=content):
                with self.assertRaisesRegex(ValueError, "Malformed"):
                    module.without_managed_block(content)
                self.assertIn(unrelated, content)

    def test_cron_fallback_never_writes_when_markers_are_malformed(self):
        _project, module = load_script("manage_cron_no_write", "manage_cron.py")
        malformed = "{}\n17 4 * * * /usr/bin/unrelated\n".format(module.BEGIN)
        with (
            patch.object(module, "current_crontab", return_value=malformed),
            patch.object(module, "write_crontab") as write,
            patch.object(sys, "argv", ["manage_cron.py", "remove"]),
        ):
            with self.assertRaisesRegex(ValueError, "Malformed"):
                module.main()
        write.assert_not_called()

        with (
            patch.object(module, "current_crontab", return_value=malformed),
            patch.object(sys, "argv", ["manage_cron.py", "status"]),
        ):
            with self.assertRaisesRegex(ValueError, "Malformed"):
                module.main()

    def test_cron_removal_is_a_noop_without_a_managed_block(self):
        _project, module = load_script("manage_cron_remove_noop", "manage_cron.py")
        existing = "17 4 * * * /usr/bin/unrelated\n"
        with (
            patch.object(module, "current_crontab", return_value=existing),
            patch.object(module, "write_crontab") as write,
            patch.object(sys, "argv", ["manage_cron.py", "remove"]),
        ):
            self.assertEqual(module.main(), 0)
        write.assert_not_called()

    def test_cron_snapshot_and_restore_preserve_exact_prior_state(self):
        _project, module = load_script("manage_cron_snapshot", "manage_cron.py")
        original = "MAILTO=\"\"\n17 4 * * * /usr/bin/unrelated\n"
        output = io.StringIO()
        with (
            patch.object(module, "current_crontab", return_value=original),
            patch.object(sys, "stdout", output),
            patch.object(sys, "argv", ["manage_cron.py", "snapshot"]),
        ):
            self.assertEqual(module.main(), 0)
        self.assertEqual(output.getvalue(), original)

        changed = module.build_crontab(original, Path("/tmp/runtime"), [(7, 30), (16, 30)])
        with (
            patch.object(module, "current_crontab", return_value=changed),
            patch.object(module, "write_crontab") as write,
            patch.object(sys, "stdin", io.StringIO(original)),
            patch.object(sys, "argv", ["manage_cron.py", "restore"]),
        ):
            self.assertEqual(module.main(), 0)
        write.assert_called_once_with(original)

        with (
            patch.object(module, "current_crontab", return_value=original),
            patch.object(module, "write_crontab") as write,
            patch.object(sys, "stdin", io.StringIO(original)),
            patch.object(sys, "argv", ["manage_cron.py", "restore"]),
        ):
            self.assertEqual(module.main(), 0)
        write.assert_not_called()

        prior = module.build_crontab(
            "11 2 * * * /usr/bin/prior-unrelated\n",
            Path("/tmp/old-runtime"),
            [(7, 30), (16, 30)],
        )
        concurrent = module.build_crontab(
            "11 2 * * * /usr/bin/prior-unrelated\n22 3 * * * /usr/bin/new-unrelated\n",
            Path("/tmp/new-runtime"),
            [(8, 0), (17, 0)],
        )
        restored = module.restore_managed_state(concurrent, prior)
        self.assertIn("/usr/bin/new-unrelated", restored)
        self.assertIn("/tmp/old-runtime", restored)
        self.assertNotIn("/tmp/new-runtime", restored)

    def test_cron_verify_accepts_only_the_exact_managed_schedule(self):
        _project, module = load_script("manage_cron_verify", "manage_cron.py")
        runtime = Path("/tmp/Opportunity Radar")
        expected = module.build_crontab("", runtime, [(7, 30), (16, 30)])
        arguments = ["manage_cron.py", "verify", "--runtime", str(runtime)]
        with patch.object(module, "current_crontab", return_value=expected), patch.object(
            sys, "argv", arguments
        ):
            self.assertEqual(module.main(), 0)
        changed = module.build_crontab("", runtime, [(8, 0), (17, 0)])
        with patch.object(module, "current_crontab", return_value=changed), patch.object(
            sys, "argv", arguments
        ):
            self.assertEqual(module.main(), 1)

    def test_cron_labels_are_isolated(self):
        _project, module = load_script("manage_cron_labels", "manage_cron.py")
        label_a = "io.github.opportunity-radar.alpha"
        label_b = "io.github.opportunity-radar.beta"
        schedule_a = module.build_crontab(
            "",
            Path("/tmp/runtime-alpha"),
            [(7, 30), (16, 30)],
            label_a,
        )
        both = module.build_crontab(
            schedule_a,
            Path("/tmp/runtime-beta"),
            [(8, 0), (17, 0)],
            label_b,
        )
        self.assertTrue(module.has_managed_block(both, label_a))
        self.assertTrue(module.has_managed_block(both, label_b))
        without_a = module.without_managed_block(both, label_a)
        self.assertFalse(module.has_managed_block(without_a, label_a))
        self.assertTrue(module.has_managed_block(without_a, label_b))
        self.assertIn("/tmp/runtime-beta", without_a)

    def test_cron_runtime_rejects_relative_and_control_character_paths(self):
        _project, module = load_script("manage_cron_runtime", "manage_cron.py")
        with self.assertRaisesRegex(ValueError, "absolute"):
            module.build_crontab("", Path("relative/runtime"), [(7, 30)])
        for character in ("\n", "\r", "\t", "\x00", "\x7f"):
            with self.subTest(character=repr(character)):
                with self.assertRaisesRegex(ValueError, "control characters"):
                    module.build_crontab(
                        "",
                        Path("/tmp/runtime{}injected".format(character)),
                        [(7, 30)],
                    )

    def test_launch_label_is_strict_and_bounded(self):
        _project, module = load_script("render_launch_agent_labels", "render_launch_agent.py")
        self.assertEqual(
            module.launch_label("io.github.opportunity-radar.monitor"),
            "io.github.opportunity-radar.monitor",
        )
        for value in (
            "",
            "../escape",
            "io.github/bad",
            "io..github",
            ".leading",
            "trailing-",
            "white space",
            "a" * 129,
        ):
            with self.subTest(value=value):
                with self.assertRaises(argparse.ArgumentTypeError):
                    module.launch_label(value)

    def test_render_output_rejects_symbolic_links(self):
        _project, module = load_script("render_launch_agent_output", "render_launch_agent.py")
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            outside = root / "outside.plist"
            outside.write_text("unrelated", encoding="utf-8")
            output = root / "agent.plist"
            output.symlink_to(outside)
            with self.assertRaisesRegex(ValueError, "symbolic link"):
                module.validate_output_path(output)
            self.assertEqual(outside.read_text(encoding="utf-8"), "unrelated")

    def test_generated_artifacts_accept_private_files_and_managed_runtime_links(self):
        _project, module = load_script("render_launch_agent_artifacts", "render_launch_agent.py")
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            project = project_artifact_tree(root)
            runtime = root / "runtime"
            dashboard = project / "dashboard" / "index.html"
            database = project / "data" / "opportunities.sqlite3"
            stdout = project / "logs" / "scheduler.out.log"
            stderr = project / "logs" / "scheduler.err.log"
            private_file(
                dashboard,
                b'<!doctype html><meta http-equiv="Content-Security-Policy">'
                b'<script id="opportunity-data"></script>',
            )
            private_database(database)
            private_file(stdout, b"scan complete\n")
            private_file(stderr)
            module.validate_generated_artifacts(project, runtime)

            private_file(
                dashboard,
                b'<!doctype html><meta http-equiv="Content-Security-Policy">'
                b'<title>Opportunity Radar</title>'
                b'<script>window.OPPORTUNITY_DATA = {}</script>',
            )
            module.validate_generated_artifacts(project, runtime)

            for path in (dashboard, database, stdout, stderr):
                path.unlink()
            dashboard.symlink_to(runtime / "dashboard" / "index.html")
            database.symlink_to(runtime / "data" / "opportunities.sqlite3")
            stdout.symlink_to(runtime / "logs" / "cron.out.log")
            stderr.symlink_to(runtime / "logs" / "launchd.err.log")
            module.validate_generated_artifacts(project, runtime)

    def test_generated_artifacts_reject_unsafe_or_unrecognized_files(self):
        _project, module = load_script(
            "render_launch_agent_unsafe_artifacts", "render_launch_agent.py"
        )
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            project = project_artifact_tree(root)
            runtime = root / "runtime"

            dashboard = project / "dashboard" / "index.html"
            private_file(dashboard, b"unrelated private page")
            with self.assertRaisesRegex(ValueError, "generated Opportunity Radar dashboard"):
                module.validate_generated_artifacts(project, runtime)
            dashboard.unlink()

            database = project / "data" / "opportunities.sqlite3"
            private_file(database, b"not sqlite")
            with self.assertRaisesRegex(ValueError, "valid SQLite database"):
                module.validate_generated_artifacts(project, runtime)
            database.unlink()

            stdout = project / "logs" / "scheduler.out.log"
            private_file(stdout, b"public mode")
            stdout.chmod(0o644)
            with self.assertRaisesRegex(ValueError, "private mode 0600"):
                module.validate_generated_artifacts(project, runtime)
            stdout.unlink()

            stdout.mkdir()
            with self.assertRaisesRegex(ValueError, "regular file"):
                module.validate_generated_artifacts(project, runtime)
            stdout.rmdir()

            outside = root / "outside.log"
            stdout.symlink_to(outside)
            with self.assertRaisesRegex(ValueError, "expected runtime artifact"):
                module.validate_generated_artifacts(project, runtime)

    def test_generated_artifact_validation_enforces_current_user_ownership(self):
        _project, module = load_script(
            "render_launch_agent_artifact_owner", "render_launch_agent.py"
        )
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "scheduler.log"
            private_file(path)
            with self.assertRaisesRegex(ValueError, "not owned by the current user"):
                module._validate_private_regular_artifact(
                    path,
                    "scheduler log",
                    "log",
                    os.getuid() + 1,
                )

    def test_every_installer_backup_destination_is_reserved_before_mutation(self):
        _project, module = load_script("render_launch_agent_backups", "render_launch_agent.py")
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            project = project_artifact_tree(root)
            runtime = root / "OpportunityRadar"
            target = root / "LaunchAgents"
            target.mkdir(mode=0o700)
            stamp = "20260810-123456"
            paths = module.installer_backup_paths(
                project,
                runtime,
                target,
                "io.github.opportunity-radar.monitor",
                stamp,
            )
            self.assertEqual(len(paths), 9)
            expected_names = {
                "OpportunityRadar.failed-20260810-123456",
                "OpportunityRadar.previous-20260810-123456",
                "io.github.opportunity-radar.monitor.plist.failed-20260810-123456",
                "previous-launch-agent-20260810-123456.plist",
                "previous-crontab-20260810-123456.txt",
                "index.pre-runtime-20260810-123456.html",
                "opportunities.pre-runtime-20260810-123456.sqlite3",
                "scheduler.pre-runtime-20260810-123456.out.log",
                "scheduler.pre-runtime-20260810-123456.err.log",
            }
            self.assertEqual({path.name for path in paths}, expected_names)
            for path in paths:
                with self.subTest(path=path.name):
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.symlink_to(root / "missing-recovery-target")
                    with self.assertRaisesRegex(ValueError, "reserved installer recovery path"):
                        module.validate_backup_destinations(
                            project,
                            runtime,
                            target,
                            "io.github.opportunity-radar.monitor",
                            stamp,
                        )
                    path.unlink()
            with self.assertRaisesRegex(ValueError, "YYYYMMDD-HHMMSS"):
                module.installer_backup_paths(
                    project,
                    runtime,
                    target,
                    "io.github.opportunity-radar.monitor",
                    "../unsafe",
                )

    def test_successful_upgrades_keep_only_one_previous_runtime(self):
        _project, module = load_script(
            "remove_superseded_runtime",
            "remove_superseded_runtime.py",
        )
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            runtime = private_runtime(root / "OpportunityRadar", "current-1")
            previous = private_runtime(
                root / "OpportunityRadar.previous",
                "previous-0",
            )
            unrelated = private_runtime(
                root / "Unrelated.previous-20260810-010101",
                "unrelated",
            )

            for generation, stamp in enumerate(
                ("20260810-020202", "20260810-030303"),
                start=2,
            ):
                archived = root / "OpportunityRadar.previous-{}".format(stamp)
                previous.rename(archived)
                runtime.rename(previous)
                runtime = private_runtime(
                    root / "OpportunityRadar",
                    "current-{}".format(generation),
                )

                removed = module.remove_superseded_runtime(runtime, stamp)

                self.assertEqual(removed, archived.resolve(strict=False))
                self.assertFalse(archived.exists())
                self.assertTrue(runtime.is_dir())
                self.assertTrue(previous.is_dir())
                self.assertTrue(unrelated.is_dir())
                self.assertEqual(
                    list(root.glob("OpportunityRadar.previous-*")),
                    [],
                )

    def test_runtime_archive_is_retained_until_upgrade_commit(self):
        project = Path(__file__).resolve().parents[1]
        source = (project / "scripts" / "install_launch_agent.sh").read_text(
            encoding="utf-8"
        )
        archive_move = '/bin/mv "$PREVIOUS_RUNTIME" "$ARCHIVED_PREVIOUS_RUNTIME_PATH"'
        rollback_restore = (
            '/bin/mv "$ARCHIVED_PREVIOUS_RUNTIME_PATH" "$PREVIOUS_RUNTIME" || true'
        )
        commit = "trap - ERR HUP INT TERM"
        cleanup = 'remove_superseded_runtime.py"'

        rollback_start = source.index("rollback()")
        rollback_end = source.index("\n}\n\ntrap 'rollback", rollback_start)
        final_commit = source.rindex(commit)
        self.assertLess(source.index(archive_move), final_commit)
        self.assertIn(rollback_restore, source[rollback_start:rollback_end])
        self.assertGreater(source.index(cleanup), source.rindex(commit))

    def test_install_path_validation_rejects_unrelated_runtime_and_symlinks(self):
        project, module = load_script("render_launch_agent_paths", "render_launch_agent.py")
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            target = root / "LaunchAgents"
            target.mkdir(mode=0o700)
            unrelated = root / "Documents"
            unrelated.mkdir(mode=0o700)
            with self.assertRaisesRegex(ValueError, "not an Opportunity Radar runtime"):
                module.validate_install_paths(
                    project,
                    unrelated,
                    target,
                    "io.github.opportunity-radar.monitor",
                    Path(sys.executable),
                )

            actual = root / "actual-runtime"
            actual.mkdir(mode=0o700)
            linked = root / "linked-runtime"
            linked.symlink_to(actual, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symbolic link"):
                module.validate_install_paths(
                    project,
                    linked,
                    target,
                    "io.github.opportunity-radar.monitor",
                    Path(sys.executable),
                )

    def test_install_path_validation_accepts_only_recognized_existing_runtime(self):
        project, module = load_script("render_launch_agent_runtime", "render_launch_agent.py")
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            runtime = root / "OpportunityRadar"
            target = root / "LaunchAgents"
            target.mkdir(mode=0o700)
            python = private_executable(root / "python")
            for marker in module.RUNTIME_MARKERS:
                path = runtime / marker
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("test", encoding="utf-8")
            runtime.chmod(0o700)
            validated_runtime, validated_target, validated_python = module.validate_install_paths(
                project,
                runtime,
                target,
                "io.github.opportunity-radar.monitor",
                python,
            )
            self.assertEqual(validated_runtime, runtime.resolve())
            self.assertEqual(validated_target, target.resolve())
            self.assertEqual(validated_python, python.resolve())

    def test_install_path_validation_accepts_a_recognized_legacy_runtime(self):
        project, module = load_script("render_launch_agent_legacy", "render_launch_agent.py")
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            runtime = root / "OpportunityRadar"
            target = root / "LaunchAgents"
            target.mkdir(mode=0o700)
            python = private_executable(root / "python")
            for marker in module.LEGACY_RUNTIME_MARKERS:
                path = runtime / marker
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("test", encoding="utf-8")
            runtime.chmod(0o700)
            validated_runtime, _target, _python = module.validate_install_paths(
                project,
                runtime,
                target,
                "io.github.opportunity-radar.monitor",
                python,
            )
            self.assertEqual(validated_runtime, runtime.resolve())

    def test_install_path_validation_rejects_repo_runtime_and_symlink_plist(self):
        project, module = load_script("render_launch_agent_boundaries", "render_launch_agent.py")
        with self.assertRaisesRegex(ValueError, "separate from the repository"):
            module.validate_install_paths(
                project,
                project / "runtime",
                project.parent,
                "io.github.opportunity-radar.monitor",
                Path(sys.executable),
            )

        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            target = root / "LaunchAgents"
            target.mkdir(mode=0o700)
            plist_target = root / "outside.plist"
            plist_target.write_text("outside", encoding="utf-8")
            (target / "io.github.opportunity-radar.monitor.plist").symlink_to(plist_target)
            with self.assertRaisesRegex(ValueError, "symbolic link"):
                module.validate_install_paths(
                    project,
                    root / "new-runtime",
                    target,
                    "io.github.opportunity-radar.monitor",
                    Path(sys.executable),
                )

    def test_install_path_validation_rejects_repo_launch_agent_directory(self):
        project, module = load_script("render_launch_agent_target", "render_launch_agent.py")
        with tempfile.TemporaryDirectory() as tempdir:
            with self.assertRaisesRegex(ValueError, "outside the repository"):
                module.validate_install_paths(
                    project,
                    Path(tempdir) / "new-runtime",
                    project / "data",
                    "io.github.opportunity-radar.monitor",
                    Path(sys.executable),
                )

    def test_shell_installer_validates_before_filesystem_mutation(self):
        project = Path(__file__).resolve().parents[1]
        source = (project / "scripts" / "install_launch_agent.sh").read_text(encoding="utf-8")
        validation = source.index("--validate-install")
        reservation = source.index("for reserved_path in")
        first_mutation = source.index('mkdir -p "$PROJECT_DIR/data"')
        self.assertLess(validation, reservation)
        self.assertLess(reservation, first_mutation)
        self.assertIn('--backup-stamp "$STAMP"', source[validation:reservation])
        for variable in (
            "DASHBOARD_BACKUP",
            "DATABASE_BACKUP",
            "CRON_BACKUP",
            "SCHEDULER_OUT_BACKUP",
            "SCHEDULER_ERR_BACKUP",
        ):
            self.assertIn('"${}"'.format(variable), source[reservation:first_mutation])
        self.assertIn(
            '/bin/mv "$DASHBOARD_PATH" "$DASHBOARD_BACKUP"',
            source,
        )
        self.assertIn(
            '/bin/mv "$DATABASE_PATH" "$DATABASE_BACKUP"',
            source,
        )
        for asset in ("template.html", "styles.css", "app.js"):
            self.assertIn(
                'cp "$PROJECT_DIR/dashboard/{}" "$STAGE/dashboard/{}"'.format(
                    asset, asset
                ),
                source,
            )

    def test_upgrade_preserves_canonical_runtime_profile_settings(self):
        project = Path(__file__).resolve().parents[1]
        source = (project / "scripts" / "install_launch_agent.sh").read_text(
            encoding="utf-8"
        )
        runtime_profile = '"$RUNTIME_DIR/config/profile.local.json"'
        repository_profile = '"$PROJECT_DIR/config/profile.local.json"'
        runtime_sources = '"$RUNTIME_DIR/config/sources.local.json"'
        repository_sources = '"$PROJECT_DIR/config/sources.local.json"'
        selection = source.index("PROFILE_LOCAL_SOURCE=")
        promotion = source.index('/bin/mv "$STAGE" "$RUNTIME_DIR"')
        self.assertLess(selection, promotion)
        self.assertLess(source.index(runtime_profile, selection), source.index(repository_profile, selection))
        self.assertLess(source.index(runtime_sources, selection), source.index(repository_sources, selection))
        self.assertIn('/bin/cp "$PROFILE_LOCAL_SOURCE"', source[selection:promotion])
        self.assertIn('/bin/cp "$SOURCES_LOCAL_SOURCE"', source[selection:promotion])
        self.assertIn("8#$mode & 8#77", source)

    def test_shell_installer_snapshots_and_restores_scheduler_transitions(self):
        project = Path(__file__).resolve().parents[1]
        source = (project / "scripts" / "install_launch_agent.sh").read_text(
            encoding="utf-8"
        )
        snapshot = source.index('manage_cron.py" snapshot')
        mutation = source.index("SCHEDULER_MUTATION_STARTED=1")
        self.assertLess(snapshot, mutation)
        self.assertIn('manage_cron.py" restore', source)
        self.assertIn('manage_cron.py" remove', source)
        self.assertIn("CRON_MUTATION_ATTEMPTED=1", source)
        self.assertIn("cannot switch safely to the cron fallback", source.lower())
        self.assertIn('manage_cron.py" verify', source)
        self.assertIn('SCHEDULER_KIND="existing cron fallback"', source)

    def test_scan_idle_helper_imports_from_outside_the_project(self):
        project = Path(__file__).resolve().parents[1]
        scripts = (
            project / "scripts" / "check_scan_idle.py",
            project / "scripts" / "recover_lifecycle_lock.py",
        )
        for script in scripts:
            with self.subTest(script=script.name), tempfile.TemporaryDirectory() as directory:
                result = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        (
                            "import runpy, sys; "
                            "runpy.run_path(sys.argv[1], run_name='helper_import_test')"
                        ),
                        str(script),
                    ],
                    cwd=directory,
                    capture_output=True,
                    text=True,
                    check=False,
                )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_scheduler_lifecycle_has_phase_rollback_locking_and_signal_guards(self):
        project = Path(__file__).resolve().parents[1]
        installer = (project / "scripts" / "install_launch_agent.sh").read_text(
            encoding="utf-8"
        )
        uninstaller = (project / "scripts" / "uninstall_launch_agent.sh").read_text(
            encoding="utf-8"
        )
        lock = 'LOCK_DIR="$LOCK_PARENT/.OpportunityRadar.lifecycle-lock"'
        self.assertIn(lock, installer)
        self.assertIn(lock, uninstaller)
        for source in (installer, uninstaller):
            self.assertIn("trap 'rollback 129' HUP", source)
            self.assertIn("trap 'rollback 130' INT", source)
            self.assertIn("trap 'rollback 143' TERM", source)
            self.assertIn("bootstrapped from a different property list", source)
            self.assertIn("trap - ERR\n  /bin/launchctl print", source)
            self.assertIn('mkdir "$LOCK_DIR"', source)
            self.assertIn('/bin/rmdir "$LOCK_DIR"', source)
            self.assertIn('--label "$LABEL"', source)

        idle_check = '"$PYTHON_BIN" "$PROJECT_DIR/scripts/check_scan_idle.py"'
        recovery = '"$PYTHON_BIN" "$PROJECT_DIR/scripts/recover_lifecycle_lock.py"'
        self.assertIn(idle_check, installer)
        self.assertIn(recovery, installer)
        self.assertIn(recovery, uninstaller)
        self.assertLess(installer.index(recovery), installer.index('mkdir "$LOCK_DIR"'))
        self.assertLess(uninstaller.index(recovery), uninstaller.index('mkdir "$LOCK_DIR"'))
        for source in (installer, uninstaller):
            self.assertIn('LOCK_OWNER="$LOCK_DIR/owner.pid"', source)
            self.assertIn("/bin/rm -f \"$LOCK_OWNER\"", source)
        self.assertLess(installer.index('mkdir "$LOCK_DIR"'), installer.index(idle_check))
        self.assertLess(installer.index(idle_check), installer.index('DATABASE_SOURCE=""'))
        self.assertEqual(installer.count("OPPORTUNITY_RADAR_LIFECYCLE_OWNER=installer"), 2)

        self.assertLess(
            installer.rindex("ARCHIVED_PREVIOUS_RUNTIME=1"),
            installer.index('/bin/mv "$PREVIOUS_RUNTIME" "$ARCHIVED_PREVIOUS_RUNTIME_PATH"'),
        )
        self.assertLess(
            installer.rindex("OLD_RUNTIME_MOVED=1"),
            installer.index('/bin/mv "$RUNTIME_DIR" "$PREVIOUS_RUNTIME"'),
        )
        self.assertLess(
            installer.rindex("OLD_RUNTIME_MOVED=1"),
            installer.index('/bin/mv "$STAGE" "$RUNTIME_DIR"'),
        )
        self.assertLess(
            installer.rindex("STAGE_PROMOTED=1"),
            installer.index('/bin/mv "$STAGE" "$RUNTIME_DIR"'),
        )
        for flag, move in (
            ("HAD_DASHBOARD_PATH=1", '/bin/mv "$DASHBOARD_PATH" "$DASHBOARD_BACKUP"'),
            ("HAD_DATABASE_PATH=1", '/bin/mv "$DATABASE_PATH" "$DATABASE_BACKUP"'),
            (
                "HAD_SCHEDULER_OUT_PATH=1",
                '/bin/mv "$SCHEDULER_OUT_PATH" "$SCHEDULER_OUT_BACKUP"',
            ),
            (
                "HAD_SCHEDULER_ERR_PATH=1",
                '/bin/mv "$SCHEDULER_ERR_PATH" "$SCHEDULER_ERR_BACKUP"',
            ),
        ):
            self.assertLess(installer.rindex(flag), installer.index(move))
        self.assertLess(
            uninstaller.rindex("MOVED_TARGET=1"),
            uninstaller.index('/bin/mv "$TARGET" "$TRASH_TARGET"'),
        )
        self.assertIn('if [[ "$OLD_RUNTIME_MOVED" -eq 1', installer)
        self.assertIn('if [[ "$ARCHIVED_PREVIOUS_RUNTIME" -eq 1', installer)
        self.assertIn("restore_managed_link", installer)
        for path in (
            "$DASHBOARD_PATH",
            "$DATABASE_PATH",
            "$SCHEDULER_OUT_PATH",
            "$SCHEDULER_ERR_PATH",
        ):
            self.assertIn(path, installer)

    def test_shell_uninstaller_validates_before_launchctl_mutation(self):
        project = Path(__file__).resolve().parents[1]
        source = (project / "scripts" / "uninstall_launch_agent.sh").read_text(
            encoding="utf-8"
        )
        validation = source.index('if [[ -L "$TARGET" ]]')
        launchctl = source.index('/bin/launchctl print "$SERVICE"')
        self.assertLess(validation, launchctl)
        self.assertLess(source.index("unsafe home directory"), launchctl)
        self.assertLess(source.index("existing Trash item"), launchctl)
        self.assertNotIn("manage_cron.py\" remove >/dev/null 2>&1 || true", source)

    def test_shell_uninstaller_preflights_cron_and_has_rollback(self):
        project = Path(__file__).resolve().parents[1]
        source = (project / "scripts" / "uninstall_launch_agent.sh").read_text(
            encoding="utf-8"
        )
        self.assertLess(
            source.index('manage_cron.py" snapshot'),
            source.index("SCHEDULER_MUTATION_STARTED=1"),
        )
        self.assertIn('manage_cron.py" restore', source)
        for path, label in (
            ("$HOME_DIR", "home directory"),
            ("$TARGET_DIR", "LaunchAgents directory"),
            ("$TRASH_DIR", "Trash directory"),
        ):
            self.assertIn(
                'reject_writable_by_others "{}" "{}"'.format(path, label),
                source,
            )

    @unittest.skipUnless(platform.system() == "Darwin", "macOS scheduler paths only")
    def test_uninstaller_rejects_writable_home_launchagents_and_trash(self):
        project = Path(__file__).resolve().parents[1]
        script = project / "scripts" / "uninstall_launch_agent.sh"
        cases = ("home", "launchagents", "trash")
        for unsafe in cases:
            with self.subTest(unsafe=unsafe), tempfile.TemporaryDirectory() as tempdir:
                root = Path(tempdir)
                home = root / "home"
                launch_agents = home / "Library" / "LaunchAgents"
                trash = home / ".Trash"
                launch_agents.mkdir(parents=True, mode=0o700)
                trash.mkdir(mode=0o700)
                home.chmod(0o700)
                if unsafe == "home":
                    home.chmod(0o777)
                elif unsafe == "launchagents":
                    launch_agents.chmod(0o777)
                else:
                    trash.chmod(0o777)

                log = root / "fake-python.log"
                fake_python = root / "python"
                fake_python.write_text(
                    "#!/bin/bash\n"
                    'printf "%s\\n" "${2:-missing}" >> "$FAKE_PYTHON_LOG"\n'
                    'if [[ "${2:-}" == "snapshot" ]]; then exit 0; fi\n'
                    'if [[ "${2:-}" == "restore" ]]; then /bin/cat >/dev/null; fi\n'
                    "exit 0\n",
                    encoding="utf-8",
                )
                fake_python.chmod(0o700)
                environment = os.environ.copy()
                environment.update(
                    {
                        "HOME": str(home),
                        "FAKE_PYTHON_LOG": str(log),
                        "OPPORTUNITY_RADAR_LABEL": "io.github.opportunity-radar.permission-test",
                        "OPPORTUNITY_RADAR_LAUNCH_AGENTS_DIR": str(launch_agents),
                        "OPPORTUNITY_RADAR_PYTHON": str(fake_python),
                    }
                )
                completed = subprocess.run(
                    ["/bin/bash", str(script)],
                    env=environment,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn("group/world-writable", completed.stderr)
                self.assertFalse(log.exists(), "scheduler preflight ran after unsafe path")

    @unittest.skipUnless(platform.system() == "Darwin", "macOS scheduler paths only")
    def test_uninstaller_restores_plist_and_cron_snapshot_after_failure(self):
        project = Path(__file__).resolve().parents[1]
        script = project / "scripts" / "uninstall_launch_agent.sh"
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            home = root / "home"
            launch_agents = home / "Library" / "LaunchAgents"
            trash = home / ".Trash"
            launch_agents.mkdir(parents=True, mode=0o700)
            trash.mkdir(mode=0o700)
            home.chmod(0o700)
            label = "io.github.opportunity-radar.rollback-test"
            target = launch_agents / "{}.plist".format(label)
            target.write_text("prior launchd state", encoding="utf-8")
            target.chmod(0o600)

            log = root / "fake-python.log"
            fake_python = root / "python"
            fake_python.write_text(
                "#!/bin/bash\n"
                'if [[ "$1" == *recover_lifecycle_lock.py ]]; then exit 0; fi\n'
                'case "${2:-}" in\n'
                '  snapshot) printf "17 4 * * * /usr/bin/unrelated\\n" ;;\n'
                '  remove) printf "remove\\n" >> "$FAKE_PYTHON_LOG"; exit 1 ;;\n'
                '  restore) /bin/cat >/dev/null; printf "restore\\n" >> "$FAKE_PYTHON_LOG" ;;\n'
                "  *) exit 1 ;;\n"
                "esac\n",
                encoding="utf-8",
            )
            fake_python.chmod(0o700)
            environment = os.environ.copy()
            environment.update(
                {
                    "HOME": str(home),
                    "FAKE_PYTHON_LOG": str(log),
                    "OPPORTUNITY_RADAR_LABEL": label,
                    "OPPORTUNITY_RADAR_LAUNCH_AGENTS_DIR": str(launch_agents),
                    "OPPORTUNITY_RADAR_PYTHON": str(fake_python),
                }
            )
            completed = subprocess.run(
                ["/bin/bash", str(script)],
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertEqual(target.read_text(encoding="utf-8"), "prior launchd state")
            self.assertEqual(
                log.read_text(encoding="utf-8").splitlines(),
                ["remove", "restore"],
                completed.stderr,
            )
            self.assertEqual(list(trash.iterdir()), [])

    @unittest.skipUnless(platform.system() == "Darwin", "macOS scheduler paths only")
    def test_uninstaller_refuses_an_existing_lifecycle_lock_before_scheduler_access(self):
        project = Path(__file__).resolve().parents[1]
        script = project / "scripts" / "uninstall_launch_agent.sh"
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            home = root / "home"
            launch_agents = home / "Library" / "LaunchAgents"
            trash = home / ".Trash"
            lock = home / "Library" / "Application Support" / ".OpportunityRadar.lifecycle-lock"
            launch_agents.mkdir(parents=True, mode=0o700)
            trash.mkdir(mode=0o700)
            lock.mkdir(parents=True, mode=0o700)
            home.chmod(0o700)

            log = root / "fake-python.log"
            fake_python = root / "python"
            fake_python.write_text(
                "#!/bin/bash\n"
                'if [[ "$1" == *recover_lifecycle_lock.py ]]; then exit 0; fi\n'
                'printf "%s\\n" "${2:-missing}" >> "$FAKE_PYTHON_LOG"\n'
                "exit 0\n",
                encoding="utf-8",
            )
            fake_python.chmod(0o700)
            environment = os.environ.copy()
            environment.update(
                {
                    "HOME": str(home),
                    "FAKE_PYTHON_LOG": str(log),
                    "OPPORTUNITY_RADAR_LABEL": "io.github.opportunity-radar.lock-test",
                    "OPPORTUNITY_RADAR_LAUNCH_AGENTS_DIR": str(launch_agents),
                    "OPPORTUNITY_RADAR_PYTHON": str(fake_python),
                }
            )
            completed = subprocess.run(
                ["/bin/bash", str(script)],
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("already running", completed.stderr)
            self.assertTrue(lock.is_dir())
            self.assertFalse(log.exists())


if __name__ == "__main__":
    unittest.main()
