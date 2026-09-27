# Repo Analyzer Lite

[![Tests](https://github.com/BarisCeyhun26/repo-analyzer-lite/actions/workflows/tests.yml/badge.svg)](https://github.com/BarisCeyhun26/repo-analyzer-lite/actions/workflows/tests.yml)

A read-only local repository analysis CLI that counts regular files and groups
them by extension. It produces deterministic JSON without opening file
contents, executing project code, or modifying the target repository.

## Why it exists

A small repository summary should be easy to inspect, reproduce, and trust.
This utility starts with file counts and extension statistics, with automated
tests covering scan failures, exclusions, determinism, and read-only safety.

## Current capabilities

- Recursively scans a local directory.
- Reports total regular files and counts by extension.
- Skips generated/heavy directories at any depth: `.git`, `.hg`, `.svn`,
  `node_modules`, `__pycache__`, `.venv`, `venv`, `.tox`, `.mypy_cache`,
  `.pytest_cache`, `.ruff_cache`, `dist`, `build`, and `target`.
- Never follows or counts symbolic links inside the scanned tree.
- Fails on access errors rather than reporting a partial scan as successful.
- Returns counts only: no paths, file names, or contents in successful output.
- Produces byte-identical JSON for an unchanged tree.

## Design principles

- **Target repositories are treated as read-only.** Only directory entries and
  file metadata are inspected.
- **Deterministic results.** Extensions and JSON keys are sorted.
- **Explicit failure.** Missing paths and access errors return a nonzero exit.
- **Small runtime footprint.** Python standard library only; no network,
  AI API, paid service, or hosting requirement.

## Quick start

Requires Python 3.9+. From the repository root, no installation is needed:

```sh
python3 -m repo_analyzer_lite .
```

For an installed command, create a virtual environment (POSIX shell):

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/repo-analyzer-lite .
```

The CLI takes one argument: the directory to scan.

```sh
python3 -m repo_analyzer_lite path/to/repository
```

## Reproducible example

Create a small directory and scan it (POSIX shell):

```sh
example_dir=$(mktemp -d)
mkdir -p "$example_dir/src"
touch "$example_dir/README.md" "$example_dir/src/main.PY" \
  "$example_dir/src/util.py" "$example_dir/LICENSE" "$example_dir/archive.tar.gz"
python3 -m repo_analyzer_lite "$example_dir"
```

Output:

```json
{
  "by_extension": {
    ".gz": 1,
    ".md": 1,
    ".py": 2,
    "[no_extension]": 1
  },
  "total_files": 5
}
```

`total_files` equals the sum of `by_extension`. An empty directory has zero
files and an empty extension map.

### Extension rules

- The last suffix is lowercased: `a.PY` → `.py`, `x.tar.gz` → `.gz`.
- Files without extensions, dotfiles such as `.gitignore`, and names ending
  in `.` are counted under `"[no_extension]"`.

### Exit codes

| Code | Meaning |
| ---- | ------- |
| 0 | Complete scan; JSON result on stdout |
| 1 | Missing path, non-directory, or access error; JSON error on stderr, no stdout |
| 2 | Invalid command-line usage |

For example, a missing path returns `{"error": "path does not exist"}` on
stderr. Error messages do not include paths.

## Read-only safety

- The scanner only lists directories and reads entry metadata (`lstat`). It
  never opens, reads, executes, or writes any file in the scanned tree.
- It never runs package scripts, installs anything, or spawns processes.
- Symbolic links inside the tree (to files or directories, broken, or
  looping) are **never followed** and are not counted. This keeps the scan
  inside the given root.
- If the path you pass is itself a symlink, it is resolved once and the
  resolved directory is scanned.
- If any directory in the tree cannot be listed, or any entry's metadata
  cannot be read, the scan aborts with exit code 1 instead of returning a
  partial count.

## Current limitations

- The exclusion list is fixed; `.gitignore` and custom ignore rules are not
  honored.
- Only regular files are counted; symlinks, sockets, FIFOs, and devices are
  not.
- Extension is purely name-based (no content or language detection).
- No language, dependency, framework, or architecture analysis yet.
- Output is JSON only.

## Project structure

```text
repo_analyzer_lite/
  scanner.py   directory traversal, exclusions, and extension counts
  cli.py       argument parsing and JSON output
  __main__.py  module entry point
tests/
  test_scanner.py  scanner, safety, failure, and CLI tests
```

## Testing

The suite uses standard-library `unittest`; no extra packages are required:

```sh
python3 -B -X dev -W error -m unittest discover -s tests -t .
```

GitHub Actions runs the suite on Ubuntu with Python 3.9, 3.12, and 3.14 on
pushes and pull requests. These are representative versions, not a claim
that every Python release or operating system has been tested.

## Status and future work

The current baseline is intentionally limited to file counts and extension
statistics. Possible future directions include configurable exclusions and
richer summaries; these are not implemented or scheduled commitments.
