"""Task 02: opt-in top-level directory summary (``--structure`` / ``by_directory``).

Expected values below come from the Task 02 contract, not from the implementation.
"""

import builtins
import hashlib
import io
import json
import os
import socket
import subprocess
import sys
import unicodedata
import unittest
from contextlib import ExitStack, contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from repo_analyzer_lite.cli import main
from repo_analyzer_lite.scanner import EXCLUDED_DIR_NAMES, ScanError, scan
from tests.test_scanner import PROJECT_ROOT, TempDirTestCase, _FakeEntry, _FakeScandir, make_files

# Task 02 exact fixture / regression oracle.
ORACLE_FILES = [
    "README.md",
    "LICENSE",
    "archive.tar.gz",
    "src/main.PY",
    "src/util.py",
    "src/deep/data.json",
    "docs/guide.md",
    "docs/Makefile",
    ".github/workflows/tests.yml",
]
ORACLE_STRUCTURE = {
    "by_directory": {".": 3, ".github": 1, "docs": 2, "src": 3},
    "by_extension": {".gz": 1, ".json": 1, ".md": 2, ".py": 2, ".yml": 1, "[no_extension]": 2},
    "total_files": 9,
}
ORACLE_STRUCTURE_STDOUT = """{
  "by_directory": {
    ".": 3,
    ".github": 1,
    "docs": 2,
    "src": 3
  },
  "by_extension": {
    ".gz": 1,
    ".json": 1,
    ".md": 2,
    ".py": 2,
    ".yml": 1,
    "[no_extension]": 2
  },
  "total_files": 9
}
"""
ORACLE_DEFAULT_STDOUT = """{
  "by_extension": {
    ".gz": 1,
    ".json": 1,
    ".md": 2,
    ".py": 2,
    ".yml": 1,
    "[no_extension]": 2
  },
  "total_files": 9
}
"""

# Task 01 five-file fixture and its accepted default output.
TASK01_FILES = ["README.md", "src/main.PY", "src/util.py", "LICENSE", "archive.tar.gz"]
TASK01_DEFAULT_STDOUT = """{
  "by_extension": {
    ".gz": 1,
    ".md": 1,
    ".py": 2,
    "[no_extension]": 1
  },
  "total_files": 5
}
"""

CONTRACT_EXCLUDES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        "target",
    }
)


def run_cli(*args):
    """Run the CLI in-process; usage errors are reported as their SystemExit code."""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = main(list(args))
        except SystemExit as exc:
            code = exc.code
    return code, out.getvalue(), err.getvalue()


def full_snapshot(root):
    """Map every path under root to lstat info, plus content hash or link target."""
    result = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in dirnames + filenames:
            p = os.path.join(dirpath, name)
            st = os.lstat(p)
            if os.path.islink(p):
                extra = os.readlink(p)
            elif os.path.isfile(p):
                with open(p, "rb") as f:
                    extra = hashlib.sha256(f.read()).hexdigest()
            else:
                extra = None
            result[os.path.relpath(p, root)] = (st.st_mode, st.st_size, st.st_mtime_ns, extra)
    return result


def assert_valid_structure(test, data):
    """Check the three-key schema and both aggregate invariants."""
    test.assertEqual(set(data), {"total_files", "by_extension", "by_directory"})
    test.assertIn(".", data["by_directory"])
    for value in list(data["by_directory"].values()) + [data["total_files"]]:
        test.assertIs(type(value), int)
        test.assertGreaterEqual(value, 0)
    for key, value in data["by_directory"].items():
        if key != ".":
            test.assertGreater(value, 0)
    test.assertEqual(sum(data["by_directory"].values()), data["total_files"])
    test.assertEqual(sum(data["by_extension"].values()), data["total_files"])


class StructureCliTests(TempDirTestCase):
    """RAL-T02-AC-01."""

    def test_flag_position_does_not_matter(self):
        make_files(self.root, ORACLE_FILES)
        after = run_cli(str(self.root), "--structure")
        before = run_cli("--structure", str(self.root))
        self.assertEqual(after, (0, ORACLE_STRUCTURE_STDOUT, ""))
        self.assertEqual(before, after)

    def test_flag_position_does_not_matter_on_error(self):
        missing = str(self.tmp / "missing")
        after = run_cli(missing, "--structure")
        self.assertEqual(after[0], 1)
        self.assertEqual(run_cli("--structure", missing), after)

    def test_module_entry_point_matches_in_process(self):
        make_files(self.root, ORACLE_FILES)
        for args in ([str(self.root), "--structure"], ["--structure", str(self.root)]):
            proc = subprocess.run(
                [sys.executable, "-m", "repo_analyzer_lite", *args],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout, ORACLE_STRUCTURE_STDOUT)
            self.assertEqual(proc.stderr, "")

    def test_usage_errors_exit_2_with_empty_stdout(self):
        make_files(self.root, ["a.py"])
        other = self.tmp / "other"
        other.mkdir()
        cases = [
            [],
            ["--structure"],
            [str(self.root), str(other)],
            [str(self.root), str(other), "--structure"],
            [str(self.root), "--tree"],
            [str(self.root), "--depth", "1"],
            [str(self.root), "--format", "json"],
            [str(self.root), "--exclude", "src"],
            [str(self.root), "--structure=yes"],
        ]
        for args in cases:
            with self.subTest(args=args):
                code, out, err = run_cli(*args)
                self.assertEqual(code, 2)
                self.assertEqual(out, "")
                self.assertIn("usage:", err)

    def test_help_lists_structure_flag(self):
        for flag in ("-h", "--help"):
            with self.subTest(flag=flag):
                code, out, err = run_cli(flag)
                self.assertEqual(code, 0)
                self.assertEqual(err, "")
                self.assertIn("--structure", out)
                self.assertIn("path", out)


class StructureFlagPrefixTests(TempDirTestCase):
    """RAL-T02-AC-01/02: only the exact --structure flag is accepted.

    The accepted baseline rejected --s/--st/--struct as unknown flags (exit 2)
    and accepted --he/--hel as --help; both must stay that way.
    """

    PREFIXES = ["--s", "--st", "--struct", "--stru", "--structur"]

    def setUp(self):
        super().setUp()
        make_files(self.root, ORACLE_FILES)

    def test_exact_flag_works_before_and_after_path(self):
        self.assertEqual(run_cli(str(self.root), "--structure"), (0, ORACLE_STRUCTURE_STDOUT, ""))
        self.assertEqual(run_cli("--structure", str(self.root)), (0, ORACLE_STRUCTURE_STDOUT, ""))

    def test_structure_prefixes_are_unknown_flags(self):
        for prefix in self.PREFIXES + ["--st=1"]:
            for args in ([str(self.root), prefix], [prefix, str(self.root)]):
                with self.subTest(args=args):
                    code, out, err = run_cli(*args)
                    self.assertEqual(code, 2)
                    self.assertEqual(out, "")
                    self.assertIn("usage:", err)
                    self.assertNotIn("by_directory", err)

    def test_prefix_error_names_the_unrecognized_argument(self):
        for prefix in self.PREFIXES + ["--st=1"]:
            with self.subTest(prefix=prefix):
                err = run_cli(str(self.root), prefix)[2]
                self.assertIn(f"error: unrecognized arguments: {prefix}\n", err)

    def test_multiple_unknown_flags_are_all_reported(self):
        err = run_cli(str(self.root), "--st", "--x", "--struct")[2]
        self.assertIn("error: unrecognized arguments: --st --x --struct\n", err)

    def test_prefix_alongside_exact_flag_is_still_rejected(self):
        code, out, _ = run_cli(str(self.root), "--structure", "--st")
        self.assertEqual((code, out), (2, ""))

    def test_prefixes_rejected_by_module_entry_point(self):
        for prefix in ("--s", "--st", "--struct"):
            with self.subTest(prefix=prefix):
                proc = subprocess.run(
                    [sys.executable, "-B", "-m", "repo_analyzer_lite", str(self.root), prefix],
                    cwd=PROJECT_ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(proc.returncode, 2)
                self.assertEqual(proc.stdout, "")
                self.assertIn(f"unrecognized arguments: {prefix}", proc.stderr)

    def test_unrelated_unknown_flags_still_exit_2(self):
        for flag in ("--tree", "--x", "--structures", "--structure-x", "--helps", "-s"):
            with self.subTest(flag=flag):
                code, out, _ = run_cli(str(self.root), flag)
                self.assertEqual((code, out), (2, ""))

    def test_help_prefixes_still_show_help(self):
        for flag in ("-h", "--h", "--he", "--hel", "--help"):
            with self.subTest(flag=flag):
                code, out, err = run_cli(flag)
                self.assertEqual((code, err), (0, ""))
                self.assertTrue(out.startswith("usage: repo-analyzer-lite"))
                self.assertIn("--structure", out)

    def test_help_takes_precedence_over_structure_prefix(self):
        # As in the baseline, argparse handles help before reporting unknown flags.
        for args in ([str(self.root), "--st", "--he"], ["--he", "--st"], ["--st=1", "--help"], ["--s", "-h"]):
            with self.subTest(args=args):
                code, out, _ = run_cli(*args)
                self.assertEqual(code, 0)
                self.assertTrue(out.startswith("usage: repo-analyzer-lite"))

    def test_prefix_after_double_dash_is_a_path_not_a_flag(self):
        self.assertEqual(
            run_cli("--", str(self.root)),
            (0, ORACLE_DEFAULT_STDOUT, ""),
        )
        code, out, err = run_cli("--structure", "--", "--st")
        self.assertEqual((code, out), (1, ""))
        self.assertEqual(json.loads(err), {"error": "path does not exist"})

    def test_default_and_structure_output_unchanged(self):
        self.assertEqual(run_cli(str(self.root)), (0, ORACLE_DEFAULT_STDOUT, ""))
        self.assertEqual(run_cli(str(self.root), "--structure"), (0, ORACLE_STRUCTURE_STDOUT, ""))
        task01 = self.tmp / "task01"
        make_files(task01, TASK01_FILES)
        self.assertEqual(run_cli(str(task01)), (0, TASK01_DEFAULT_STDOUT, ""))


class DefaultCompatibilityTests(TempDirTestCase):
    """RAL-T02-AC-02: without --structure nothing changes."""

    def test_task01_default_stdout_is_byte_identical(self):
        make_files(self.root, TASK01_FILES)
        self.assertEqual(run_cli(str(self.root)), (0, TASK01_DEFAULT_STDOUT, ""))

    def test_oracle_default_stdout_has_two_keys_and_no_directory_names(self):
        make_files(self.root, ORACLE_FILES)
        code, out, err = run_cli(str(self.root))
        self.assertEqual((code, out, err), (0, ORACLE_DEFAULT_STDOUT, ""))
        self.assertEqual(list(json.loads(out)), ["by_extension", "total_files"])
        for name in ("by_directory", "src", "docs", ".github", "deep", "workflows"):
            self.assertNotIn(name, out)

    def test_scan_default_return_shape_unchanged(self):
        make_files(self.root, ORACLE_FILES)
        expected = {
            "total_files": ORACLE_STRUCTURE["total_files"],
            "by_extension": ORACLE_STRUCTURE["by_extension"],
        }
        for result in (scan(self.root), scan(self.root, structure=False)):
            self.assertEqual(result, expected)
            self.assertEqual(list(result), ["total_files", "by_extension"])

    def test_scan_empty_default_unchanged(self):
        self.assertEqual(scan(self.root), {"total_files": 0, "by_extension": {}})

    def test_default_error_output_unchanged(self):
        self.assertEqual(
            run_cli(str(self.tmp / "missing")),
            (1, "", '{"error": "path does not exist"}\n'),
        )
        make_files(self.root, ["f.txt"])
        self.assertEqual(
            run_cli(str(self.root / "f.txt")),
            (1, "", '{"error": "path is not a directory"}\n'),
        )

    def test_existing_fields_equal_default_output(self):
        make_files(self.root, ORACLE_FILES + ["node_modules/x.js"])
        os.symlink(self.root / "src", self.root / "src_link")
        default = json.loads(run_cli(str(self.root))[1])
        structured = json.loads(run_cli(str(self.root), "--structure")[1])
        del structured["by_directory"]
        self.assertEqual(structured, default)


class PartitionTests(TempDirTestCase):
    """RAL-T02-AC-03."""

    def test_root_files_only(self):
        make_files(self.root, ["a.py", "b.md", ".env"])
        result = scan(self.root, structure=True)
        self.assertEqual(result["by_directory"], {".": 3})
        assert_valid_structure(self, result)

    def test_deep_subtree_aggregates_into_first_level_directory(self):
        make_files(self.root, ["pkg/a.py", "pkg/x/b.py", "pkg/x/y/z/c.py", "pkg/x/y/z/w/d.txt"])
        result = scan(self.root, structure=True)
        self.assertEqual(result["by_directory"], {".": 0, "pkg": 4})
        assert_valid_structure(self, result)

    def test_multiple_first_level_branches(self):
        make_files(
            self.root,
            ["README.md", "src/a.py", "src/nested/b.py", "tests/test_a.py", "docs/x/y/z.md", "lib/l.c"],
        )
        result = scan(self.root, structure=True)
        self.assertEqual(result["by_directory"], {".": 1, "docs": 1, "lib": 1, "src": 2, "tests": 1})
        assert_valid_structure(self, result)

    def test_nested_directory_named_like_first_level_does_not_split_bucket(self):
        make_files(self.root, ["docs/a.md", "src/docs/b.md", "src/src/c.py"])
        self.assertEqual(scan(self.root, structure=True)["by_directory"], {".": 0, "docs": 1, "src": 2})

    def test_cli_output_is_exactly_three_keys(self):
        make_files(self.root, ORACLE_FILES)
        code, out, err = run_cli(str(self.root), "--structure")
        self.assertEqual((code, err), (0, ""))
        data = json.loads(out)
        self.assertEqual(list(data), ["by_directory", "by_extension", "total_files"])
        assert_valid_structure(self, data)


class OracleAndEmptyTests(TempDirTestCase):
    """RAL-T02-AC-04."""

    def test_exact_oracle_value(self):
        make_files(self.root, ORACLE_FILES)
        self.assertEqual(scan(self.root, structure=True), ORACLE_STRUCTURE)

    def test_exact_oracle_serialization(self):
        make_files(self.root, ORACLE_FILES)
        code, out, err = run_cli(str(self.root), "--structure")
        self.assertEqual((code, out, err), (0, ORACLE_STRUCTURE_STDOUT, ""))
        self.assertEqual(json.loads(out), ORACLE_STRUCTURE)

    def test_task01_fixture(self):
        make_files(self.root, TASK01_FILES)
        result = scan(self.root, structure=True)
        self.assertEqual(result["by_directory"], {".": 3, "src": 2})
        self.assertEqual(run_cli(str(self.root))[1], TASK01_DEFAULT_STDOUT)

    def test_empty_root(self):
        self.assertEqual(
            scan(self.root, structure=True),
            {"total_files": 0, "by_extension": {}, "by_directory": {".": 0}},
        )
        code, out, _ = run_cli(str(self.root), "--structure")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"by_directory": {".": 0}, "by_extension": {}, "total_files": 0})

    def test_root_bucket_present_when_zero(self):
        make_files(self.root, ["src/a.py"])
        self.assertEqual(scan(self.root, structure=True)["by_directory"], {".": 0, "src": 1})

    def test_directories_without_counted_files_get_no_bucket(self):
        make_files(self.root, ["a.py", "pkg/node_modules/x.js", "gen/sub/dist/out.js"])
        (self.root / "empty").mkdir()
        (self.root / "nested_empty" / "a" / "b").mkdir(parents=True)
        (self.root / "links_only").mkdir()
        os.symlink(self.root / "a.py", self.root / "links_only" / "alias.py")
        os.symlink(self.tmp / "nowhere", self.root / "links_only" / "broken")
        self.assertEqual(
            scan(self.root, structure=True),
            {"total_files": 1, "by_extension": {".py": 1}, "by_directory": {".": 1}},
        )

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires FIFO support")
    def test_directory_with_only_special_files_gets_no_bucket(self):
        make_files(self.root, ["a.py"])
        (self.root / "pipes").mkdir()
        os.mkfifo(self.root / "pipes" / "fifo")
        os.mkfifo(self.root / "root_fifo")
        self.assertEqual(scan(self.root, structure=True)["by_directory"], {".": 1})


class StructureExclusionTests(TempDirTestCase):
    """RAL-T02-AC-05."""

    def test_exclusion_list_is_exactly_the_contract_set(self):
        self.assertEqual(EXCLUDED_DIR_NAMES, CONTRACT_EXCLUDES)

    def test_every_excluded_directory_changes_nothing_at_any_depth(self):
        make_files(self.root, ORACLE_FILES)
        for name in sorted(CONTRACT_EXCLUDES):
            make_files(self.root, [f"{name}/f.txt", f"src/{name}/g.txt", f"docs/x/{name}/deep/h.txt"])
        self.assertEqual(scan(self.root, structure=True), ORACLE_STRUCTURE)
        code, out, _ = run_cli(str(self.root), "--structure")
        self.assertEqual((code, out), (0, ORACLE_STRUCTURE_STDOUT))
        self.assertEqual(run_cli(str(self.root))[1], ORACLE_DEFAULT_STDOUT)

    def test_excluded_directory_names_not_disclosed(self):
        make_files(self.root, ["a.py"])
        for name in sorted(CONTRACT_EXCLUDES):
            make_files(self.root, [f"{name}/f.txt"])
        out = run_cli(str(self.root), "--structure")[1]
        self.assertEqual(json.loads(out)["by_directory"], {".": 1})
        for name in CONTRACT_EXCLUDES:
            self.assertNotIn(f'"{name}"', out)

    def test_regular_files_named_like_excluded_dirs_are_counted(self):
        make_files(self.root, ["build", "dist", "docs/target", "docs/node_modules"])
        result = scan(self.root, structure=True)
        self.assertEqual(result["by_directory"], {".": 2, "docs": 2})
        self.assertEqual(result["total_files"], 4)


class StructureSymlinkAndPathTests(TempDirTestCase):
    """RAL-T02-AC-06."""

    def setUp(self):
        super().setUp()
        self.outside = self.tmp / "outside"
        make_files(self.outside, ["secret1.txt", "sub/secret2.txt"])
        make_files(self.root, ORACLE_FILES)

    def test_symlinks_inside_tree_are_not_followed_or_bucketed(self):
        os.symlink(self.outside, self.root / "link_out")
        os.symlink(self.outside / "secret1.txt", self.root / "linked.txt")
        os.symlink(self.root / "src", self.root / "src_alias")
        os.symlink(self.root / "src", self.root / "docs" / "src_alias")
        os.symlink(self.tmp / "nowhere", self.root / "broken")
        os.symlink(self.root, self.root / "src" / "loop")
        os.symlink("self_ref", self.root / "self_ref")
        self.assertEqual(scan(self.root, structure=True), ORACLE_STRUCTURE)
        out = run_cli(str(self.root), "--structure")[1]
        self.assertEqual(out, ORACLE_STRUCTURE_STDOUT)
        for leaked in ("link_out", "src_alias", "broken", "loop", "self_ref", "secret", "outside"):
            self.assertNotIn(leaked, out)

    def test_root_symlink_resolved_once_gives_same_output(self):
        link = self.tmp / "root_link"
        os.symlink(self.root, link)
        self.assertEqual(run_cli(str(link), "--structure"), run_cli(str(self.root), "--structure"))
        self.assertEqual(run_cli(str(link), "--structure")[1], ORACLE_STRUCTURE_STDOUT)

    def test_no_absolute_root_or_deep_paths_or_file_names_in_output(self):
        link = self.tmp / "root_link"
        os.symlink(self.root, link)
        for target in (self.root, link):
            code, out, err = run_cli(str(target), "--structure")
            self.assertEqual((code, err), (0, ""))
            for leaked in (
                str(self.tmp),
                str(target),
                "repo",
                "root_link",
                os.path.expanduser("~"),
                "/",
                "deep",
                "workflows",
                "README",
                "LICENSE",
                "main",
                "util",
                "guide",
                "Makefile",
                "archive",
                "data",
                "tests.yml",
            ):
                self.assertNotIn(leaked, out)


class StructureDeterminismTests(TempDirTestCase):
    """RAL-T02-AC-07."""

    def test_repeated_runs_are_byte_identical(self):
        make_files(self.root, ORACLE_FILES)
        first = run_cli(str(self.root), "--structure")
        self.assertEqual(run_cli(str(self.root), "--structure"), first)

    def test_creation_order_and_root_name_do_not_matter(self):
        make_files(self.root, ORACLE_FILES)
        other = self.tmp / "different location ü" / "another-name"
        other.mkdir(parents=True)
        make_files(other, list(reversed(ORACLE_FILES)))
        self.assertEqual(run_cli(str(other), "--structure"), run_cli(str(self.root), "--structure"))

    def test_traversal_order_does_not_matter(self):
        make_files(self.root, ORACLE_FILES + ["zeta/z.py", "alpha/a.py", "Mid/m.py"])
        real_scandir = os.scandir

        class ReversedScandir:
            def __init__(self, path):
                self._ctx = real_scandir(path)

            def __enter__(self):
                return iter(list(reversed(list(self._ctx.__enter__()))))

            def __exit__(self, *exc):
                return self._ctx.__exit__(*exc)

        expected = run_cli(str(self.root), "--structure")
        with mock.patch("repo_analyzer_lite.scanner.os.scandir", ReversedScandir):
            self.assertEqual(run_cli(str(self.root), "--structure"), expected)

    def test_keys_use_plain_python_string_ordering(self):
        names = ["alpha", "Zeta", "!bang", " lead space", "é", "tab\there"]
        make_files(self.root, [f"{name}/f.txt" for name in names] + ["root.txt"])
        if set(os.listdir(self.root)) != set(names) | {"root.txt"}:
            self.skipTest("file system does not preserve these names exactly")
        code, out, _ = run_cli(str(self.root), "--structure")
        self.assertEqual(code, 0)
        expected = [" lead space", "!bang", ".", "Zeta", "alpha", "tab\there", "é"]
        self.assertEqual(list(json.loads(out)["by_directory"]), expected)
        self.assertEqual(list(scan(self.root, structure=True)["by_directory"]), expected)

    def test_unicode_case_space_and_control_names_round_trip(self):
        names = ["café", "目录", "📁 files", "Docs", "with space", "new\nline", "bell\x07"]
        nfd = unicodedata.normalize("NFD", "résumé")
        make_files(self.root, [f"{name}/sub/f.txt" for name in names] + [f"{nfd}/f.txt"])
        on_disk = set(os.listdir(self.root))
        if on_disk != set(names) | {nfd}:
            self.skipTest("file system does not preserve these names exactly")
        code, out, _ = run_cli(str(self.root), "--structure")
        self.assertEqual(code, 0)
        self.assertTrue(out.isascii())  # ensure_ascii JSON escaping
        by_directory = json.loads(out)["by_directory"]
        self.assertEqual(by_directory, {".": 0, **{name: 1 for name in names}, nfd: 1})
        # Names are not normalized, case-folded, or lowercased.
        self.assertIn(nfd, by_directory)
        self.assertNotIn(unicodedata.normalize("NFC", nfd), by_directory)
        self.assertNotIn("docs", by_directory)

    def test_case_distinct_directories_are_separate_buckets(self):
        make_files(self.root, ["Src/a.py"])
        if (self.root / "src").exists():
            self.skipTest("case-insensitive file system")
        make_files(self.root, ["src/b.py", "src/c.py"])
        self.assertEqual(scan(self.root, structure=True)["by_directory"], {".": 0, "Src": 1, "src": 2})


class StructureFailureTests(TempDirTestCase):
    """RAL-T02-AC-08: both modes fail the same way; no partial success."""

    def assert_same_failure_in_both_modes(self, target, expected_substring):
        default = run_cli(str(target))
        for args in ([str(target), "--structure"], ["--structure", str(target)]):
            code, out, err = run_cli(*args)
            self.assertEqual((code, out, err), default)
            self.assertEqual(code, 1)
            self.assertEqual(out, "")
            self.assertEqual(err.count("\n"), 1)
            self.assertEqual(list(json.loads(err)), ["error"])
            self.assertIn(expected_substring, json.loads(err)["error"])
            self.assertNotIn(str(self.tmp), err)

    def test_missing_path(self):
        self.assert_same_failure_in_both_modes(self.tmp / "missing", "does not exist")

    def test_file_as_root(self):
        make_files(self.root, ["f.txt"])
        self.assert_same_failure_in_both_modes(self.root / "f.txt", "not a directory")

    def test_unreadable_root_test_double(self):
        with mock.patch(
            "repo_analyzer_lite.scanner.os.scandir",
            side_effect=PermissionError(13, "Permission denied", str(self.root)),
        ):
            with self.assertRaises(ScanError):
                scan(self.root, structure=True)
            self.assert_same_failure_in_both_modes(self.root, "Permission denied")

    def test_unreadable_child_test_double(self):
        make_files(self.root, ["ok.txt", "src/a.py", "src/locked/hidden.txt"])
        real_scandir = os.scandir
        locked = str(self.root / "src" / "locked")

        def fake_scandir(path):
            if os.fspath(path) == locked:
                raise PermissionError(13, "Permission denied", locked)
            return real_scandir(path)

        with mock.patch("repo_analyzer_lite.scanner.os.scandir", fake_scandir):
            with self.assertRaises(ScanError):
                scan(self.root, structure=True)
            self.assert_same_failure_in_both_modes(self.root, "Permission denied")

    def test_entry_metadata_error_test_double(self):
        with mock.patch(
            "repo_analyzer_lite.scanner.os.scandir",
            return_value=_FakeScandir([_FakeEntry("unstatable.txt")]),
        ):
            self.assert_same_failure_in_both_modes(self.root, "Permission denied")

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root ignores permissions")
    def test_real_unreadable_child_directory(self):
        make_files(self.root, ["ok.txt", "src/a.py", "src/locked/hidden.txt"])
        locked = self.root / "src" / "locked"
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o755)
        with self.assertRaises(ScanError):
            scan(self.root, structure=True)
        self.assert_same_failure_in_both_modes(self.root, "cannot read")

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root ignores permissions")
    def test_real_unreadable_root(self):
        self.root.chmod(0)
        self.addCleanup(self.root.chmod, 0o755)
        self.assert_same_failure_in_both_modes(self.root, "cannot read")

    def test_missing_path_argument_is_usage_error_not_scan_error(self):
        for args in ([], ["--structure"]):
            code, out, err = run_cli(*args)
            self.assertEqual(code, 2)
            self.assertEqual(out, "")
            self.assertNotIn('"error"', err)


class StructureReadOnlyTests(TempDirTestCase):
    """RAL-T02-AC-09."""

    def setUp(self):
        super().setUp()
        make_files(self.root, ORACLE_FILES)
        make_files(self.tmp / "sibling", ["keep.txt"])
        (self.root / "src" / "run.sh").write_text("#!/bin/sh\necho no\n")
        (self.root / "src" / "run.sh").chmod(0o755)
        os.symlink(self.tmp / "sibling", self.root / "link_out")

    @contextmanager
    def forbid_side_effects(self):
        def forbidden(*args, **kwargs):
            raise AssertionError("scanner must not open files, run processes, or use the network")

        with ExitStack() as stack:
            for target, name in [
                (builtins, "open"),
                (os, "open"),
                (subprocess, "Popen"),
                (os, "system"),
                (socket, "socket"),
                (socket, "create_connection"),
            ]:
                stack.enter_context(mock.patch.object(target, name, forbidden))
            yield

    def test_successful_structure_scan_does_not_modify_anything(self):
        before = full_snapshot(self.tmp)
        with self.forbid_side_effects():
            code, out, err = run_cli(str(self.root), "--structure")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out)["by_directory"], {".": 3, ".github": 1, "docs": 2, "src": 4})
        self.assertEqual(full_snapshot(self.tmp), before)

    def test_failed_structure_scan_does_not_modify_anything(self):
        real_scandir = os.scandir
        locked = str(self.root / "src" / "deep")

        def fake_scandir(path):
            if os.fspath(path) == locked:
                raise PermissionError(13, "Permission denied", locked)
            return real_scandir(path)

        before = full_snapshot(self.tmp)
        with self.forbid_side_effects(), mock.patch("repo_analyzer_lite.scanner.os.scandir", fake_scandir):
            code, out, _ = run_cli(str(self.root), "--structure")
        self.assertEqual((code, out), (1, ""))
        self.assertEqual(full_snapshot(self.tmp), before)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires FIFO support")
    def test_special_files_are_not_opened_or_counted(self):
        os.mkfifo(self.root / "docs" / "pipe")
        before = full_snapshot(self.tmp)
        with self.forbid_side_effects():
            result = scan(self.root, structure=True)
        self.assertEqual(result["by_directory"]["docs"], 2)
        self.assertEqual(full_snapshot(self.tmp), before)


if __name__ == "__main__":
    unittest.main()
