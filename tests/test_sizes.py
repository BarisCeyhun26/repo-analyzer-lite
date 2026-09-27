"""Task 03: opt-in extension byte summary (``--sizes`` / ``sizes``).

Expected values below come from the Task 03 contract, not from the implementation.
"""

import builtins
import itertools
import json
import os
import socket
import stat
import subprocess
import sys
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest import mock

from repo_analyzer_lite.scanner import ScanError, scan
from tests.test_scanner import PROJECT_ROOT, TempDirTestCase, _FakeEntry, _FakeScandir
from tests.test_structure import (
    CONTRACT_EXCLUDES,
    ORACLE_DEFAULT_STDOUT,
    ORACLE_STRUCTURE_STDOUT,
    full_snapshot,
    run_cli,
)

# Task 03 exact independent fixture: path -> exact byte count.
SIZE_FILES = {
    "README.md": 3,
    "LICENSE": 0,
    "archive.tar.gz": 5,
    "src/main.PY": 7,
    "src/util.py": 11,
    "src/deep/data.json": 13,
    "docs/guide.md": 17,
    "docs/Makefile": 19,
    ".github/workflows/tests.yml": 23,
}
COUNTS = {".gz": 1, ".json": 1, ".md": 2, ".py": 2, ".yml": 1, "[no_extension]": 2}
SIZES = {
    "by_extension": {".gz": 5, ".json": 13, ".md": 20, ".py": 18, ".yml": 23, "[no_extension]": 19},
    "total_bytes": 98,
}
BY_DIRECTORY = {".": 3, ".github": 1, "docs": 2, "src": 3}
SIZES_ORACLE = {"by_extension": COUNTS, "sizes": SIZES, "total_files": 9}
COMBINED_ORACLE = {"by_directory": BY_DIRECTORY, "by_extension": COUNTS, "sizes": SIZES, "total_files": 9}

SIZES_STDOUT = """{
  "by_extension": {
    ".gz": 1,
    ".json": 1,
    ".md": 2,
    ".py": 2,
    ".yml": 1,
    "[no_extension]": 2
  },
  "sizes": {
    "by_extension": {
      ".gz": 5,
      ".json": 13,
      ".md": 20,
      ".py": 18,
      ".yml": 23,
      "[no_extension]": 19
    },
    "total_bytes": 98
  },
  "total_files": 9
}
"""
COMBINED_STDOUT = """{
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
  "sizes": {
    "by_extension": {
      ".gz": 5,
      ".json": 13,
      ".md": 20,
      ".py": 18,
      ".yml": 23,
      "[no_extension]": 19
    },
    "total_bytes": 98
  },
  "total_files": 9
}
"""

# Flag set -> exact stdout for the fixture (Task 01/02 modes keep their accepted bytes).
MODE_STDOUT = {
    (): ORACLE_DEFAULT_STDOUT,
    ("--structure",): ORACLE_STRUCTURE_STDOUT,
    ("--sizes",): SIZES_STDOUT,
    ("--structure", "--sizes"): COMBINED_STDOUT,
}
MODES = list(MODE_STDOUT)


def make_sized(root, files):
    """Create each file with exactly ``size`` bytes of ASCII ``x``."""
    for rel, size in files.items():
        p = Path(root, rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * size)


def mode_key(args):
    return tuple(flag for flag in ("--structure", "--sizes") if flag in args)


def assert_valid_sizes(test, data):
    """Schema and invariants for any output that contains ``sizes``."""
    sizes = data["sizes"]
    test.assertEqual(list(sizes), ["by_extension", "total_bytes"])
    for value in [sizes["total_bytes"], *sizes["by_extension"].values()]:
        test.assertIs(type(value), int)
        test.assertGreaterEqual(value, 0)
    test.assertEqual(set(sizes["by_extension"]), set(data["by_extension"]))
    test.assertEqual(sum(sizes["by_extension"].values()), sizes["total_bytes"])
    test.assertEqual(sum(data["by_extension"].values()), data["total_files"])
    if "by_directory" in data:
        test.assertEqual(sum(data["by_directory"].values()), data["total_files"])


class _StatRecord:
    """Synthetic lstat result carrying type and size in one record."""

    def __init__(self, mode, size, blocks=0):
        self.st_mode = mode
        self.st_size = size
        self.st_blocks = blocks


class _SyntheticEntry:
    """Directory entry double whose stat() returns a fixed record and counts calls."""

    def __init__(self, root, name, record):
        self.name = name
        self.path = os.path.join(root, name)
        self._record = record
        self.stat_calls = []

    def stat(self, follow_symlinks=True):
        self.stat_calls.append(follow_symlinks)
        return self._record


def regular(size, blocks=0):
    return _StatRecord(stat.S_IFREG | 0o644, size, blocks)


class SizesCliTests(TempDirTestCase):
    """RAL-T03-AC-01."""

    def setUp(self):
        super().setUp()
        make_sized(self.root, SIZE_FILES)

    def test_every_path_and_flag_order_gives_the_mode_output(self):
        for flags in MODES:
            for args in itertools.permutations([str(self.root), *flags]):
                with self.subTest(args=args):
                    self.assertEqual(run_cli(*args), (0, MODE_STDOUT[flags], ""))

    def test_repeated_flags_are_accepted_like_other_boolean_flags(self):
        args = [str(self.root), "--sizes", "--structure", "--sizes"]
        self.assertEqual(run_cli(*args), (0, COMBINED_STDOUT, ""))

    def test_module_entry_point(self):
        for args in ([str(self.root), "--sizes"], ["--sizes", str(self.root), "--structure"]):
            with self.subTest(args=args):
                proc = subprocess.run(
                    [sys.executable, "-B", "-m", "repo_analyzer_lite", *args],
                    cwd=PROJECT_ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (0, MODE_STDOUT[mode_key(args)], ""))

    def test_prefixes_of_both_flags_are_unknown(self):
        prefixes = ["--s", "--si", "--siz", "--size", "--st", "--str", "--stru", "--struct", "--structur"]
        for prefix in prefixes:
            for args in ([str(self.root), prefix], [prefix, str(self.root)], [str(self.root), "--sizes", prefix]):
                with self.subTest(args=args):
                    code, out, err = run_cli(*args)
                    self.assertEqual((code, out), (2, ""))
                    self.assertTrue(err.endswith(f"error: unrecognized arguments: {prefix}\n"), err)

    def test_value_variants_and_unknown_flags_exit_2(self):
        for flag in ("--sizes=1", "--sizes=", "--sizes=true", "--structure=1", "--si=1", "--size=yes",
                     "-s", "-S", "--sizess", "--sizes-x", "--top", "--format", "--tree", "--bytes"):
            for args in ([str(self.root), flag], [flag, str(self.root)]):
                with self.subTest(args=args):
                    code, out, err = run_cli(*args)
                    self.assertEqual((code, out), (2, ""))
                    self.assertIn("usage:", err)

    def test_missing_or_extra_positional_exit_2(self):
        other = self.tmp / "other"
        other.mkdir()
        for args in ([], ["--sizes"], ["--sizes", "--structure"], [str(self.root), str(other), "--sizes"]):
            with self.subTest(args=args):
                code, out, err = run_cli(*args)
                self.assertEqual((code, out), (2, ""))
                self.assertNotIn('"error"', err)

    def test_help_prefixes_show_both_flags(self):
        for flag in ("-h", "--h", "--he", "--hel", "--help"):
            with self.subTest(flag=flag):
                code, out, err = run_cli(flag)
                self.assertEqual((code, err), (0, ""))
                self.assertTrue(out.startswith("usage: repo-analyzer-lite"))
                self.assertIn("--sizes", out)
                self.assertIn("--structure", out)

    def test_help_takes_precedence_over_unknown_flags(self):
        for args in ([str(self.root), "--si", "--he"], ["--size", "-h"], ["--sizes", "--s", "--help"],
                     ["--help", "--sizes=1"]):
            with self.subTest(args=args):
                code, out, _ = run_cli(*args)
                self.assertEqual(code, 0)
                self.assertTrue(out.startswith("usage: repo-analyzer-lite"))

    def test_double_dash_terminates_options(self):
        self.assertEqual(run_cli("--sizes", "--", str(self.root)), (0, SIZES_STDOUT, ""))
        self.assertEqual(
            run_cli("--sizes", "--", "--structure"),
            (1, "", '{"error": "path does not exist"}\n'),
        )


class SizesCompatibilityTests(TempDirTestCase):
    """RAL-T03-AC-02: modes without --sizes are unchanged."""

    def setUp(self):
        super().setUp()
        make_sized(self.root, SIZE_FILES)

    def test_default_and_structure_bytes_match_task02_oracle(self):
        self.assertEqual(run_cli(str(self.root)), (0, ORACLE_DEFAULT_STDOUT, ""))
        self.assertEqual(run_cli(str(self.root), "--structure"), (0, ORACLE_STRUCTURE_STDOUT, ""))

    def test_scan_api_shapes_and_key_order(self):
        cases = [
            ({}, ["total_files", "by_extension"]),
            ({"sizes": False}, ["total_files", "by_extension"]),
            ({"structure": True}, ["total_files", "by_extension", "by_directory"]),
            ({"structure": True, "sizes": False}, ["total_files", "by_extension", "by_directory"]),
            ({"sizes": True}, ["total_files", "by_extension", "sizes"]),
            ({"structure": True, "sizes": True}, ["total_files", "by_extension", "by_directory", "sizes"]),
        ]
        for kwargs, keys in cases:
            with self.subTest(kwargs=kwargs):
                self.assertEqual(list(scan(self.root, **kwargs)), keys)
        self.assertEqual(scan(self.root, sizes=False), scan(self.root))
        self.assertEqual(scan(self.root, structure=True, sizes=False), scan(self.root, structure=True))

    def test_keywords_are_independent_and_keyword_only(self):
        base = scan(self.root)
        with_sizes = scan(self.root, sizes=True)
        self.assertEqual({k: with_sizes[k] for k in base}, base)
        combined = scan(self.root, structure=True, sizes=True)
        self.assertEqual(combined["by_directory"], scan(self.root, structure=True)["by_directory"])
        self.assertEqual(combined["sizes"], with_sizes["sizes"])
        with self.assertRaises(TypeError):
            scan(self.root, False, True)

    def test_errors_unchanged_without_sizes(self):
        self.assertEqual(run_cli(str(self.tmp / "missing")), (1, "", '{"error": "path does not exist"}\n'))
        self.assertEqual(
            run_cli(str(self.root / "README.md")), (1, "", '{"error": "path is not a directory"}\n')
        )


class SizesSchemaOracleTests(TempDirTestCase):
    """RAL-T03-AC-03."""

    def setUp(self):
        super().setUp()
        make_sized(self.root, SIZE_FILES)

    def test_fixture_has_exact_byte_counts(self):
        for rel, size in SIZE_FILES.items():
            self.assertEqual(os.lstat(self.root / rel).st_size, size)

    def test_sizes_exact_value_and_bytes(self):
        self.assertEqual(scan(self.root, sizes=True), SIZES_ORACLE)
        code, out, err = run_cli(str(self.root), "--sizes")
        self.assertEqual((code, out, err), (0, SIZES_STDOUT, ""))
        self.assertEqual(json.loads(out), SIZES_ORACLE)
        self.assertEqual(out, json.dumps(SIZES_ORACLE, indent=2, sort_keys=True, ensure_ascii=True) + "\n")

    def test_combined_exact_value_and_bytes(self):
        self.assertEqual(scan(self.root, structure=True, sizes=True), COMBINED_ORACLE)
        code, out, err = run_cli(str(self.root), "--structure", "--sizes")
        self.assertEqual((code, out, err), (0, COMBINED_STDOUT, ""))

    def test_four_schemas_are_exact(self):
        expected_keys = {
            (): ["by_extension", "total_files"],
            ("--structure",): ["by_directory", "by_extension", "total_files"],
            ("--sizes",): ["by_extension", "sizes", "total_files"],
            ("--structure", "--sizes"): ["by_directory", "by_extension", "sizes", "total_files"],
        }
        for flags, keys in expected_keys.items():
            with self.subTest(flags=flags):
                data = json.loads(run_cli(str(self.root), *flags)[1])
                self.assertEqual(list(data), keys)
                self.assertEqual(data["by_extension"], COUNTS)
                self.assertEqual(data["total_files"], 9)
                if "by_directory" in data:
                    self.assertEqual(data["by_directory"], BY_DIRECTORY)
                if "sizes" in data:
                    assert_valid_sizes(self, data)
                    self.assertEqual(data["sizes"], SIZES)


class ByteSemanticsTests(TempDirTestCase):
    """RAL-T03-AC-04."""

    def test_empty_root(self):
        self.assertEqual(
            scan(self.root, sizes=True),
            {"total_files": 0, "by_extension": {}, "sizes": {"total_bytes": 0, "by_extension": {}}},
        )
        self.assertEqual(
            json.loads(run_cli(str(self.root), "--sizes", "--structure")[1]),
            {
                "by_directory": {".": 0},
                "by_extension": {},
                "sizes": {"by_extension": {}, "total_bytes": 0},
                "total_files": 0,
            },
        )

    def test_all_zero_byte_files_keep_their_keys(self):
        make_sized(self.root, {"a.py": 0, "b.md": 0, "LICENSE": 0, "src/c.py": 0})
        result = scan(self.root, sizes=True)
        self.assertEqual(result["sizes"], {"total_bytes": 0, "by_extension": {".md": 0, ".py": 0, "[no_extension]": 0}})
        self.assertEqual(result["by_extension"], {".md": 1, ".py": 2, "[no_extension]": 1})

    def test_mixed_zero_and_nonzero_same_extension(self):
        make_sized(self.root, {"a.py": 0, "b.py": 5, "src/c.py": 0, "d.txt": 0})
        self.assertEqual(
            scan(self.root, sizes=True)["sizes"],
            {"total_bytes": 5, "by_extension": {".py": 5, ".txt": 0}},
        )

    def test_extension_name_rules(self):
        make_sized(
            self.root,
            {
                ".env": 2,
                ".gitignore": 3,
                "trailing.": 4,
                "UPPER.TXT": 5,
                "lower.txt": 6,
                "x.tar.gz": 7,
                "Makefile": 8,
                ".eslintrc.json": 9,
            },
        )
        result = scan(self.root, sizes=True)
        self.assertEqual(result["by_extension"], {".gz": 1, ".json": 1, ".txt": 2, "[no_extension]": 4})
        self.assertEqual(
            result["sizes"],
            {"total_bytes": 44, "by_extension": {".gz": 7, ".json": 9, ".txt": 11, "[no_extension]": 17}},
        )
        # The API result is sorted too, not just the CLI serialization.
        self.assertEqual(list(result["sizes"]["by_extension"]), [".gz", ".json", ".txt", "[no_extension]"])

    @unittest.skipUnless(hasattr(os, "link"), "requires hard link support")
    def test_hardlinks_count_each_path_separately(self):
        make_sized(self.root, {"a.bin": 10})
        try:
            os.link(self.root / "a.bin", self.root / "b.bin")
        except OSError as exc:
            self.skipTest(f"hard links unavailable: {exc}")
        result = scan(self.root, sizes=True)
        self.assertEqual(result["total_files"], 2)
        self.assertEqual(result["sizes"], {"total_bytes": 20, "by_extension": {".bin": 20}})

    def test_sparse_file_uses_logical_size(self):
        with open(self.root / "sparse.img", "wb") as f:
            f.truncate(5_000_000)
        self.assertEqual(scan(self.root, sizes=True)["sizes"]["total_bytes"], 5_000_000)

    def test_logical_size_not_allocated_blocks_metadata_double(self):
        entries = [_SyntheticEntry(str(self.root), "huge.img", regular(4_000_000_000, blocks=0))]
        with mock.patch("repo_analyzer_lite.scanner.os.scandir", return_value=_FakeScandir(entries)):
            result = scan(self.root, sizes=True)
        self.assertEqual(result["sizes"], {"total_bytes": 4_000_000_000, "by_extension": {".img": 4_000_000_000}})

    def test_large_sizes_keep_integer_precision(self):
        big1, big2 = 2**53 + 1, 2**60 + 3
        entries = [
            _SyntheticEntry(str(self.root), "a.bin", regular(big1)),
            _SyntheticEntry(str(self.root), "b.bin", regular(big2)),
            _SyntheticEntry(str(self.root), "c.dat", regular(1)),
        ]
        with mock.patch("repo_analyzer_lite.scanner.os.scandir", return_value=_FakeScandir(entries)):
            code, out, _ = run_cli(str(self.root), "--sizes")
        self.assertEqual(code, 0)
        self.assertIn('"total_bytes": 1161928703861587973\n', out)
        self.assertIn('".bin": 1161928703861587972,\n', out)
        data = json.loads(out)
        self.assertEqual(data["sizes"]["total_bytes"], 1161928703861587973)
        self.assertIs(type(data["sizes"]["total_bytes"]), int)
        self.assertNotEqual(float(big1), big1)  # would lose precision as a float

    def test_type_and_size_come_from_one_non_following_stat(self):
        entries = [
            _SyntheticEntry(str(self.root), "a.py", regular(3)),
            _SyntheticEntry(str(self.root), "link.py", _StatRecord(stat.S_IFLNK | 0o777, 999)),
            _SyntheticEntry(str(self.root), "pipe", _StatRecord(stat.S_IFIFO | 0o644, 777)),
        ]
        with mock.patch("repo_analyzer_lite.scanner.os.scandir", return_value=_FakeScandir(entries)):
            result = scan(self.root, sizes=True)
        self.assertEqual(result["sizes"], {"total_bytes": 3, "by_extension": {".py": 3}})
        for entry in entries:
            self.assertEqual(entry.stat_calls, [False])

    def test_unicode_case_and_control_extensions_round_trip(self):
        files = {"a.ÉTÉ": 3, "b.été": 4, "c.b\x01": 5, "d.Ω": 6}
        make_sized(self.root, files)
        if set(os.listdir(self.root)) != set(files):
            self.skipTest("file system does not preserve these names exactly")
        code, out, _ = run_cli(str(self.root), "--sizes")
        self.assertEqual(code, 0)
        self.assertTrue(out.isascii())
        data = json.loads(out)
        self.assertEqual(data["by_extension"], {".b\x01": 1, ".été": 2, ".ω": 1})
        self.assertEqual(data["sizes"]["by_extension"], {".b\x01": 5, ".été": 7, ".ω": 6})


class SizesExclusionAndSymlinkTests(TempDirTestCase):
    """RAL-T03-AC-05."""

    def setUp(self):
        super().setUp()
        make_sized(self.root, SIZE_FILES)
        self.outside = self.tmp / "outside"
        make_sized(self.outside, {"big.txt": 500, "sub/more.md": 700})

    def assert_all_modes_match_oracle(self):
        self.assertEqual(scan(self.root, sizes=True), SIZES_ORACLE)
        for flags in MODES:
            with self.subTest(flags=flags):
                self.assertEqual(run_cli(str(self.root), *flags), (0, MODE_STDOUT[flags], ""))

    def test_excluded_directories_at_every_depth_add_no_bytes(self):
        for name in sorted(CONTRACT_EXCLUDES):
            make_sized(self.root, {f"{name}/a.md": 1000, f"src/{name}/b.py": 1000, f"docs/x/{name}/c.gz": 1000})
        self.assert_all_modes_match_oracle()

    def test_regular_files_named_like_excluded_dirs_count_bytes(self):
        make_sized(self.root, {"build": 4, "docs/dist": 6})
        result = scan(self.root, structure=True, sizes=True)
        self.assertEqual(result["sizes"]["by_extension"]["[no_extension]"], 19 + 10)
        self.assertEqual(result["sizes"]["total_bytes"], 98 + 10)
        self.assertEqual(result["by_directory"], {".": 4, ".github": 1, "docs": 3, "src": 3})

    def test_symlinks_are_not_followed_or_sized(self):
        os.symlink(self.outside, self.root / "link_out")
        os.symlink(self.outside / "big.txt", self.root / "big_link.txt")
        os.symlink(self.root / "src", self.root / "docs" / "src_alias")
        os.symlink(self.root / "README.md", self.root / "readme_alias.md")
        os.symlink(self.tmp / "nowhere", self.root / "broken")
        os.symlink(self.root, self.root / "src" / "loop")
        self.assert_all_modes_match_oracle()

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires FIFO support")
    def test_special_files_add_no_bytes(self):
        os.mkfifo(self.root / "docs" / "pipe.md")
        os.mkfifo(self.root / "fifo")
        self.assert_all_modes_match_oracle()

    def test_root_symlink_resolved_once(self):
        link = self.tmp / "root_link"
        os.symlink(self.root, link)
        for flags in MODES:
            with self.subTest(flags=flags):
                self.assertEqual(run_cli(str(link), *flags), (0, MODE_STDOUT[flags], ""))


class SizesPrivacyAndReadOnlyTests(TempDirTestCase):
    """RAL-T03-AC-06."""

    SENTINEL = b"SENTINEL-CONTENT-4f1c"

    def setUp(self):
        super().setUp()
        make_sized(self.root, SIZE_FILES)
        (self.root / "src" / "secret.txt").write_bytes(self.SENTINEL * 3)
        (self.root / "run.sh").write_bytes(b"#!/bin/sh\necho " + self.SENTINEL + b"\n")
        (self.root / "run.sh").chmod(0o755)
        make_sized(self.tmp / "sibling", {"keep.txt": 4})
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

    def test_output_has_no_contents_names_or_paths(self):
        for flags in (("--sizes",), ("--structure", "--sizes")):
            with self.subTest(flags=flags):
                code, out, err = run_cli(str(self.root), *flags)
                self.assertEqual((code, err), (0, ""))
                for leaked in (
                    self.SENTINEL.decode(),
                    "SENTINEL",
                    "echo",
                    str(self.tmp),
                    "repo",
                    "sibling",
                    "keep",
                    "link_out",
                    os.path.expanduser("~"),
                    "/",
                    "secret",
                    "run.sh",
                    "README",
                    "deep",
                    "workflows",
                    "Makefile",
                ):
                    self.assertNotIn(leaked, out)
                data = json.loads(out)
                self.assertEqual(data["sizes"]["by_extension"][".txt"], len(self.SENTINEL) * 3)

    def test_sizes_only_discloses_no_directory_names(self):
        out = run_cli(str(self.root), "--sizes")[1]
        for name in ("src", "docs", ".github", "by_directory"):
            self.assertNotIn(name, out)

    def test_successful_scan_is_metadata_only_and_changes_nothing(self):
        before = full_snapshot(self.tmp)
        with self.forbid_side_effects():
            code, out, _ = run_cli(str(self.root), "--structure", "--sizes")
            result = scan(self.root, sizes=True)
        self.assertEqual(code, 0)
        self.assertEqual(result["sizes"]["total_bytes"], 98 + len(self.SENTINEL) * 4 + 16)
        self.assertEqual(full_snapshot(self.tmp), before)

    def test_failed_scan_changes_nothing(self):
        real_scandir = os.scandir
        locked = str(self.root / "src" / "deep")

        def fake_scandir(path):
            if os.fspath(path) == locked:
                raise PermissionError(13, "Permission denied", locked)
            return real_scandir(path)

        before = full_snapshot(self.tmp)
        with self.forbid_side_effects(), mock.patch("repo_analyzer_lite.scanner.os.scandir", fake_scandir):
            code, out, _ = run_cli(str(self.root), "--sizes")
        self.assertEqual((code, out), (1, ""))
        self.assertEqual(full_snapshot(self.tmp), before)


class SizesFailureTests(TempDirTestCase):
    """RAL-T03-AC-07: all four modes fail identically; no partial success."""

    def assert_same_failure_in_all_modes(self, target, expected_substring):
        default = run_cli(str(target))
        self.assertEqual(default[0], 1)
        self.assertEqual(default[1], "")
        self.assertEqual(default[2].count("\n"), 1)
        self.assertTrue(default[2].endswith("\n"))
        self.assertEqual(list(json.loads(default[2])), ["error"])
        self.assertIn(expected_substring, json.loads(default[2])["error"])
        self.assertNotIn(str(self.tmp), default[2])
        for flags in MODES[1:]:
            for args in ([str(target), *flags], [*flags, str(target)]):
                with self.subTest(args=args):
                    self.assertEqual(run_cli(*args), default)

    def test_missing_path(self):
        self.assert_same_failure_in_all_modes(self.tmp / "missing", "does not exist")

    def test_file_as_root(self):
        make_sized(self.root, {"f.txt": 5})
        self.assert_same_failure_in_all_modes(self.root / "f.txt", "not a directory")

    def test_broken_root_symlink(self):
        os.symlink(self.tmp / "nowhere", self.tmp / "dangling")
        # Accepted behavior: the link exists but does not resolve to a directory.
        self.assert_same_failure_in_all_modes(self.tmp / "dangling", "not a directory")

    def test_unreadable_root_test_double(self):
        with mock.patch(
            "repo_analyzer_lite.scanner.os.scandir",
            side_effect=PermissionError(13, "Permission denied", str(self.root)),
        ):
            with self.assertRaises(ScanError):
                scan(self.root, sizes=True)
            self.assert_same_failure_in_all_modes(self.root, "Permission denied")

    def test_unreadable_child_test_double(self):
        make_sized(self.root, {"ok.txt": 1, "src/a.py": 2, "src/locked/hidden.txt": 3})
        real_scandir = os.scandir
        locked = str(self.root / "src" / "locked")

        def fake_scandir(path):
            if os.fspath(path) == locked:
                raise PermissionError(13, "Permission denied", locked)
            return real_scandir(path)

        with mock.patch("repo_analyzer_lite.scanner.os.scandir", fake_scandir):
            with self.assertRaises(ScanError):
                scan(self.root, structure=True, sizes=True)
            self.assert_same_failure_in_all_modes(self.root, "Permission denied")

    def test_entry_metadata_error_test_double(self):
        with mock.patch(
            "repo_analyzer_lite.scanner.os.scandir",
            return_value=_FakeScandir([_FakeEntry("unstatable.txt")]),
        ):
            with self.assertRaises(ScanError):
                scan(self.root, sizes=True)
            self.assert_same_failure_in_all_modes(self.root, "Permission denied")

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root ignores permissions")
    def test_real_unreadable_child_directory(self):
        make_sized(self.root, {"ok.txt": 1, "src/a.py": 2, "src/locked/hidden.txt": 3})
        locked = self.root / "src" / "locked"
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o755)
        self.assert_same_failure_in_all_modes(self.root, "cannot read")

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root ignores permissions")
    def test_real_unreadable_root(self):
        self.root.chmod(0)
        self.addCleanup(self.root.chmod, 0o755)
        self.assert_same_failure_in_all_modes(self.root, "cannot read")

    def test_content_unreadable_file_is_still_sized(self):
        make_sized(self.root, {"a.py": 2, "locked.bin": 12})
        (self.root / "locked.bin").chmod(0)
        self.addCleanup((self.root / "locked.bin").chmod, 0o644)
        code, out, err = run_cli(str(self.root), "--sizes")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            json.loads(out)["sizes"],
            {"by_extension": {".bin": 12, ".py": 2}, "total_bytes": 14},
        )


class _CountingEntry:
    """Proxy for a real DirEntry that counts stat() calls."""

    def __init__(self, entry, counter):
        self._entry = entry
        self._counter = counter
        self.name = entry.name
        self.path = entry.path

    def stat(self, follow_symlinks=True):
        self._counter["entry_stat"] += 1
        self._counter["follow_symlinks"].add(follow_symlinks)
        return self._entry.stat(follow_symlinks=follow_symlinks)


class SizesDeterminismAndCostTests(TempDirTestCase):
    """RAL-T03-AC-08."""

    def setUp(self):
        super().setUp()
        make_sized(self.root, SIZE_FILES)

    def test_repeated_runs_are_byte_identical(self):
        for flags in MODES:
            with self.subTest(flags=flags):
                self.assertEqual(run_cli(str(self.root), *flags), run_cli(str(self.root), *flags))

    def test_creation_order_and_root_location_do_not_matter(self):
        other = self.tmp / "another place ü" / "renamed"
        make_sized(other, dict(reversed(list(SIZE_FILES.items()))))
        for flags in MODES:
            with self.subTest(flags=flags):
                self.assertEqual(run_cli(str(other), *flags), (0, MODE_STDOUT[flags], ""))

    def test_traversal_order_does_not_matter(self):
        real_scandir = os.scandir

        class ReversedScandir:
            def __init__(self, path):
                self._ctx = real_scandir(path)

            def __enter__(self):
                return iter(list(reversed(list(self._ctx.__enter__()))))

            def __exit__(self, *exc):
                return self._ctx.__exit__(*exc)

        with mock.patch("repo_analyzer_lite.scanner.os.scandir", ReversedScandir):
            for flags in MODES:
                with self.subTest(flags=flags):
                    self.assertEqual(run_cli(str(self.root), *flags), (0, MODE_STDOUT[flags], ""))

    def count_metadata_work(self, **kwargs):
        counter = {"scandir": 0, "entry_stat": 0, "os_stat": 0, "os_lstat": 0, "follow_symlinks": set()}
        real_scandir, real_stat, real_lstat = os.scandir, os.stat, os.lstat

        class CountingScandir:
            def __init__(self, path):
                counter["scandir"] += 1
                self._ctx = real_scandir(path)

            def __enter__(self):
                return (_CountingEntry(e, counter) for e in self._ctx.__enter__())

            def __exit__(self, *exc):
                return self._ctx.__exit__(*exc)

        def counting_stat(*args, **kw):
            counter["os_stat"] += 1
            return real_stat(*args, **kw)

        def counting_lstat(*args, **kw):
            counter["os_lstat"] += 1
            return real_lstat(*args, **kw)

        def forbidden(*args, **kw):
            raise AssertionError("scanner must not open files")

        with mock.patch("repo_analyzer_lite.scanner.os.scandir", CountingScandir), mock.patch.object(
            os, "stat", counting_stat
        ), mock.patch.object(os, "lstat", counting_lstat), mock.patch.object(
            builtins, "open", forbidden
        ), mock.patch.object(os, "open", forbidden):
            result = scan(self.root, **kwargs)
        return result, counter

    def test_sizes_adds_no_listing_stat_or_read_work(self):
        _, baseline = self.count_metadata_work()
        # 6 directories listed; one non-following stat per entry (9 files + 5 subdirectories).
        self.assertEqual(baseline["scandir"], 6)
        self.assertEqual(baseline["entry_stat"], 14)
        self.assertEqual(baseline["follow_symlinks"], {False})
        for kwargs in ({"sizes": True}, {"structure": True}, {"structure": True, "sizes": True}):
            with self.subTest(kwargs=kwargs):
                result, counter = self.count_metadata_work(**kwargs)
                self.assertEqual(counter, baseline)
        self.assertEqual(self.count_metadata_work(sizes=True)[0], SIZES_ORACLE)


if __name__ == "__main__":
    unittest.main()
