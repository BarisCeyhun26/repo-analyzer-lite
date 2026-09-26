import builtins
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from repo_analyzer_lite.cli import main
from repo_analyzer_lite.scanner import NO_EXTENSION, ScanError, extension_of, scan

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def make_files(root, relpaths):
    for rel in relpaths:
        p = Path(root, rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")


def snapshot(root):
    """Map of every path under root (without following links) to its lstat info."""
    result = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in dirnames + filenames:
            p = os.path.join(dirpath, name)
            st = os.lstat(p)
            result[os.path.relpath(p, root)] = (st.st_mode, st.st_size, st.st_mtime_ns)
    return result


class TempDirTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        self.root = self.tmp / "repo"
        self.root.mkdir()


class CountTests(TempDirTestCase):
    def test_contract_fixture(self):
        make_files(self.root, ["README.md", "src/main.PY", "src/util.py", "LICENSE", "archive.tar.gz"])
        result = scan(self.root)
        self.assertEqual(
            result,
            {
                "total_files": 5,
                "by_extension": {".gz": 1, ".md": 1, ".py": 2, "[no_extension]": 1},
            },
        )

    def test_contract_fixture_unchanged_by_excluded_dir_and_symlinks(self):
        make_files(self.root, ["README.md", "src/main.PY", "src/util.py", "LICENSE", "archive.tar.gz"])
        make_files(self.root, ["node_modules/pkg/index.js", "src/__pycache__/m.pyc"])
        os.symlink(self.root / "README.md", self.root / "link.md")
        os.symlink(self.root / "src", self.root / "src_link")
        self.assertEqual(
            scan(self.root)["by_extension"],
            {".gz": 1, ".md": 1, ".py": 2, "[no_extension]": 1},
        )

    def test_extensionless_files_use_no_extension_label(self):
        make_files(self.root, ["README", "LICENSE", "Makefile", ".env", ".gitignore", "a.py"])
        result = scan(self.root)
        self.assertEqual(NO_EXTENSION, "[no_extension]")
        self.assertEqual(result["by_extension"], {".py": 1, "[no_extension]": 5})
        self.assertNotIn("(none)", result["by_extension"])

    def test_counts_and_extensions(self):
        make_files(
            self.root,
            [
                "README.md",
                "setup.py",
                "src/app.py",
                "src/util.PY",
                "src/deep/nested/data.json",
                "docs/guide.md",
                "Makefile",
                ".gitignore",
                "archive.tar.gz",
            ],
        )
        result = scan(self.root)
        self.assertEqual(result["total_files"], 9)
        self.assertEqual(
            result["by_extension"],
            {".gz": 1, ".json": 1, ".md": 2, ".py": 3, "[no_extension]": 2},
        )
        self.assertEqual(sum(result["by_extension"].values()), result["total_files"])

    def test_extension_of(self):
        self.assertEqual(extension_of("a.py"), ".py")
        self.assertEqual(extension_of("A.TXT"), ".txt")
        self.assertEqual(extension_of("main.PY"), ".py")
        self.assertEqual(extension_of("archive.tar.gz"), ".gz")
        self.assertEqual(extension_of("Dockerfile"), "[no_extension]")
        self.assertEqual(extension_of("README"), "[no_extension]")
        self.assertEqual(extension_of("LICENSE"), "[no_extension]")
        self.assertEqual(extension_of("Makefile"), "[no_extension]")
        self.assertEqual(extension_of(".env"), "[no_extension]")
        self.assertEqual(extension_of(".eslintrc.json"), ".json")
        self.assertEqual(extension_of("trailing."), "[no_extension]")

    def test_empty_directory(self):
        self.assertEqual(scan(self.root), {"total_files": 0, "by_extension": {}})

    def test_directories_only_counts_no_files(self):
        (self.root / "a" / "b" / "c").mkdir(parents=True)
        self.assertEqual(scan(self.root)["total_files"], 0)


class ExclusionTests(TempDirTestCase):
    def test_ignored_directories_at_any_depth(self):
        make_files(
            self.root,
            [
                "index.js",
                ".git/HEAD",
                ".git/objects/ab/cdef",
                "node_modules/lib/index.js",
                "packages/web/node_modules/x/y.js",
                "src/__pycache__/mod.cpython-314.pyc",
                ".venv/bin/python",
                "dist/bundle.js",
                "build/out.o",
            ],
        )
        result = scan(self.root)
        self.assertEqual(result["total_files"], 1)
        self.assertEqual(result["by_extension"], {".js": 1})

    def test_file_named_like_excluded_dir_is_counted(self):
        make_files(self.root, ["build", "dist"])
        self.assertEqual(scan(self.root)["total_files"], 2)


class InvalidPathTests(TempDirTestCase):
    def test_nonexistent_path(self):
        with self.assertRaises(ScanError):
            scan(self.tmp / "missing")

    def test_file_instead_of_directory(self):
        make_files(self.root, ["f.txt"])
        with self.assertRaises(ScanError):
            scan(self.root / "f.txt")

    def test_broken_symlink_as_root(self):
        os.symlink(self.tmp / "missing", self.tmp / "dangling")
        with self.assertRaises(ScanError):
            scan(self.tmp / "dangling")


class SymlinkTests(TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.outside = self.tmp / "outside"
        make_files(self.outside, ["secret1.txt", "secret2.txt", "sub/secret3.txt"])
        make_files(self.root, ["real.py"])

    def test_directory_symlink_outside_root_not_followed(self):
        os.symlink(self.outside, self.root / "link_out")
        result = scan(self.root)
        self.assertEqual(result["total_files"], 1)
        self.assertNotIn(".txt", result["by_extension"])

    def test_file_symlinks_not_counted(self):
        os.symlink(self.outside / "secret1.txt", self.root / "linked.txt")
        os.symlink(self.root / "real.py", self.root / "alias.py")
        result = scan(self.root)
        self.assertEqual(result["total_files"], 1)
        self.assertEqual(result["by_extension"], {".py": 1})

    def test_symlink_loop_terminates(self):
        (self.root / "sub").mkdir()
        os.symlink(self.root, self.root / "sub" / "loop")
        os.symlink("self_ref", self.root / "self_ref")
        result = scan(self.root)
        self.assertEqual(result["total_files"], 1)

    def test_broken_symlink_inside_tree(self):
        os.symlink(self.tmp / "nowhere", self.root / "broken")
        result = scan(self.root)
        self.assertEqual(result["total_files"], 1)

    def test_root_symlink_is_resolved_once(self):
        link = self.tmp / "root_link"
        os.symlink(self.root, link)
        self.assertEqual(scan(link), {"total_files": 1, "by_extension": {".py": 1}})


class _FakeEntry:
    """Directory entry whose lstat fails, standing in for a metadata access error."""

    def __init__(self, name):
        self.name = name

    def stat(self, follow_symlinks=True):
        raise PermissionError(13, "Permission denied")


class _FakeScandir:
    def __init__(self, entries):
        self._entries = entries

    def __enter__(self):
        return iter(self._entries)

    def __exit__(self, *exc):
        return False


class AccessErrorTests(TempDirTestCase):
    """A partial scan must never be reported as success."""

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(list(args))
        return code, out.getvalue(), err.getvalue()

    def assert_access_failure(self, code, out, err):
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        message = json.loads(err)["error"]
        self.assertIn("Permission denied", message)
        self.assertNotIn(str(self.tmp), err)

    def test_unreadable_subdirectory_test_double(self):
        make_files(self.root, ["ok.txt", "locked/hidden.txt"])
        real_scandir = os.scandir
        locked = str(self.root / "locked")

        def fake_scandir(path):
            if os.fspath(path) == locked:
                raise PermissionError(13, "Permission denied", locked)
            return real_scandir(path)

        with mock.patch("repo_analyzer_lite.scanner.os.scandir", fake_scandir):
            with self.assertRaises(ScanError):
                scan(self.root)
            self.assert_access_failure(*self.run_cli(str(self.root)))

    def test_unreadable_root_test_double(self):
        with mock.patch(
            "repo_analyzer_lite.scanner.os.scandir",
            side_effect=PermissionError(13, "Permission denied", str(self.root)),
        ):
            self.assert_access_failure(*self.run_cli(str(self.root)))

    def test_entry_metadata_error_test_double(self):
        with mock.patch(
            "repo_analyzer_lite.scanner.os.scandir",
            return_value=_FakeScandir([_FakeEntry("unstatable.txt")]),
        ):
            self.assert_access_failure(*self.run_cli(str(self.root)))


@unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root ignores permissions")
class RealPermissionTests(TempDirTestCase):
    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_unreadable_subdirectory_fails(self):
        make_files(self.root, ["ok.txt", "locked/hidden.txt"])
        locked = self.root / "locked"
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o755)
        with self.assertRaises(ScanError):
            scan(self.root)
        code, out, err = self.run_cli(str(self.root))
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("error", json.loads(err))
        self.assertNotIn(str(self.tmp), err)

    def test_unreadable_root_fails(self):
        self.root.chmod(0)
        self.addCleanup(self.root.chmod, 0o755)
        code, out, err = self.run_cli(str(self.root))
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("error", json.loads(err))


class SafetyAndDeterminismTests(TempDirTestCase):
    def setUp(self):
        super().setUp()
        make_files(
            self.root,
            ["package.json", "setup.py", "Makefile", "run.sh", "b/z.rs", "a/y.go", "node_modules/x.js"],
        )
        (self.root / "run.sh").chmod(0o755)

    def test_scan_does_not_modify_tree(self):
        before = snapshot(self.root)
        scan(self.root)
        self.assertEqual(snapshot(self.root), before)

    def test_scan_never_opens_files_or_spawns_processes(self):
        def forbidden(*args, **kwargs):
            raise AssertionError("scanner must not open files or run processes")

        with mock.patch.object(builtins, "open", forbidden), mock.patch.object(
            subprocess, "Popen", forbidden
        ), mock.patch.object(os, "system", forbidden):
            result = scan(self.root)
        self.assertEqual(result["total_files"], 6)

    def test_output_is_deterministic(self):
        out1, out2 = io.StringIO(), io.StringIO()
        with redirect_stdout(out1):
            main([str(self.root)])
        with redirect_stdout(out2):
            main([str(self.root)])
        self.assertEqual(out1.getvalue(), out2.getvalue())
        keys = list(json.loads(out1.getvalue())["by_extension"])
        self.assertEqual(keys, sorted(keys))


class CliTests(TempDirTestCase):
    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_valid_directory(self):
        make_files(self.root, ["a.py", "b.py", "c.md"])
        code, out, err = self.run_cli(str(self.root))
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        data = json.loads(out)
        self.assertEqual(data, {"by_extension": {".md": 1, ".py": 2}, "total_files": 3})

    def test_empty_directory(self):
        code, out, _ = self.run_cli(str(self.root))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"by_extension": {}, "total_files": 0})

    def test_output_has_only_contract_fields_and_no_path(self):
        make_files(self.root, ["a.py", "LICENSE"])
        link = self.tmp / "root_link"
        os.symlink(self.root, link)
        for target in (self.root, link):
            code, out, err = self.run_cli(str(target))
            self.assertEqual(code, 0)
            self.assertEqual(set(json.loads(out)), {"total_files", "by_extension"})
            for leaked in (str(self.root), str(link), str(self.tmp), "repo", "root_link"):
                self.assertNotIn(leaked, out)
            self.assertEqual(err, "")

    def test_invalid_path(self):
        code, out, err = self.run_cli(str(self.tmp / "missing"))
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("does not exist", json.loads(err)["error"])
        self.assertNotIn(str(self.tmp), err)

    def test_file_instead_of_directory(self):
        make_files(self.root, ["f.txt"])
        code, out, err = self.run_cli(str(self.root / "f.txt"))
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("not a directory", json.loads(err)["error"])
        self.assertNotIn(str(self.tmp), err)

    def test_module_entry_point(self):
        make_files(self.root, ["a.py"])
        proc = subprocess.run(
            [sys.executable, "-m", "repo_analyzer_lite", str(self.root)],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), {"by_extension": {".py": 1}, "total_files": 1})


if __name__ == "__main__":
    unittest.main()
