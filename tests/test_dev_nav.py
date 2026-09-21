import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class DeveloperNavigationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / "scripts").mkdir()
        (self.root / "monitor").mkdir()
        source = Path(__file__).resolve().parents[1] / "scripts" / "dev_nav.py"
        self.script = self.root / "scripts" / "dev_nav.py"
        shutil.copyfile(source, self.script)
        (self.root / "monitor" / "sample.py").write_text(
            "from monitor.other import target as alias\n"
            "raise RuntimeError('navigation must never execute this module')\n"
            "# target in a comment\n"
            "label = 'target in a string'\n"
            "@decorate\n"
            "def target(value: int = 2) -> int:\n"
            "    return value\n"
            "class Example:\n"
            "    async def run(self):\n"
            "        return target(3) + self.target\n",
            encoding="utf-8",
        )

    def run_nav(self, *args, success=True):
        result = subprocess.run(
            [sys.executable, str(self.script), *args],
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
        return result

    def test_map_reports_size_symbols_and_local_imports(self):
        output = self.run_nav("map", "--path", "monitor/sample.py").stdout
        self.assertIn("10 lines, 3 symbols", output)
        self.assertIn("monitor.other", output)
        self.assertNotIn("navigation must never execute", output)

    def test_symbols_find_qualified_async_methods_and_signatures(self):
        output = self.run_nav("symbols", "--query", "Example.run").stdout
        self.assertIn("monitor/sample.py:9-10 Example.run | run(self)", output)
        output = self.run_nav("symbols", "--query", "target").stdout
        self.assertIn("target(value: int=2) -> int", output)

    def test_show_includes_decorators_and_only_requested_implementation(self):
        output = self.run_nav(
            "show", "--path", "monitor/sample.py", "--query", "target"
        ).stdout
        self.assertIn("sample.py:5: @decorate", output)
        self.assertIn("sample.py:7:     return value", output)
        self.assertNotIn("class Example", output)
        self.assertNotIn("raise RuntimeError", output)

    def test_refs_excludes_strings_comments_and_reports_imports_and_attributes(self):
        output = self.run_nav("refs", "--query", "target").stdout
        self.assertIn("sample.py:1:", output)
        self.assertIn("sample.py:10:", output)
        self.assertEqual(len(output.splitlines()), 2)
        self.assertNotIn("comment", output)
        self.assertNotIn("label =", output)

    def test_output_is_bounded_and_can_be_paged(self):
        output = self.run_nav(
            "show", "--path", "monitor/sample.py", "--query", "target", "--limit", "1"
        ).stdout
        self.assertEqual(len(output.splitlines()), 2)
        self.assertIn("[output limited", output)
        output = self.run_nav(
            "show",
            "--path",
            "monitor/sample.py",
            "--query",
            "target",
            "--offset",
            "1",
        ).stdout
        self.assertNotIn("@decorate", output)
        self.assertIn("sample.py:6:", output)

    def test_private_paths_and_symlinks_are_not_read(self):
        (self.root / "data").mkdir()
        private = self.root / "data" / "private.py"
        private.write_text("def PRIVATE_SENTINEL(): pass\n", encoding="utf-8")
        (self.root / "monitor" / "linked.py").symlink_to(private)
        (self.root / "tests").symlink_to(self.root / "data", target_is_directory=True)
        output = self.run_nav("symbols").stdout
        self.assertNotIn("PRIVATE_SENTINEL", output)
        for path in (
            "data/private.py",
            "monitor/linked.py",
            "../private.py",
            str(private),
        ):
            with self.subTest(path=path):
                self.run_nav("symbols", "--path", path, success=False)

    def test_invalid_requests_and_unparseable_code_fail_visibly(self):
        for args in (
            ("map", "--limit", "201"),
            ("map", "--limit", "0"),
            ("map", "--offset", "-1"),
            ("show", "--query", "target"),
            ("refs", "--query", "target.call"),
            ("show", "--path", "monitor/sample.py", "--query", "missing"),
        ):
            with self.subTest(args=args):
                self.run_nav(*args, success=False)
        (self.root / "monitor" / "broken.py").write_text("def (", encoding="utf-8")
        result = self.run_nav("symbols", "--path", "monitor/broken.py", success=False)
        self.assertIn("Navigation failed", result.stderr)

    def test_oversized_source_and_single_line_output_are_bounded(self):
        path = self.root / "monitor" / "large.py"
        path.write_text("#" * (1024 * 1024 + 1), encoding="utf-8")
        result = self.run_nav("map", "--path", "monitor/large.py", success=False)
        self.assertIn("1 MiB", result.stderr)
        path.write_text(
            "def large():\n    return '" + "x" * 20000 + "'\n", encoding="utf-8"
        )
        output = self.run_nav(
            "show", "--path", "monitor/large.py", "--query", "large"
        ).stdout
        self.assertLess(len(output), 16100)
        self.assertIn("[output limited", output)


if __name__ == "__main__":
    unittest.main()
