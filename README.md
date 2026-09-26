# Repo Analyzer Lite

A small, read-only CLI that scans a local directory and reports file counts as
deterministic JSON.

## What v0 does

- Recursively scans a local directory.
- Reports the total number of regular files and the count per file extension.
- Skips common generated/heavy directories (at any depth): `.git`, `.hg`,
  `.svn`, `node_modules`, `__pycache__`, `.venv`, `venv`, `.tox`,
  `.mypy_cache`, `.pytest_cache`, `.ruff_cache`, `dist`, `build`, `target`.
- Never follows or counts symbolic links.
- Fails (nonzero exit, no stdout) on any access error; a partial scan is
  never reported as success.
- Output contains only counts: no paths, file names, or file contents.
- Produces byte-identical output for an unchanged tree (sorted keys).

Requires Python 3.9+. No third-party dependencies.

## Usage

```sh
python3 -m repo_analyzer_lite /path/to/repo
```

Or install the `repo-analyzer-lite` command:

```sh
pip install -e .
repo-analyzer-lite /path/to/repo
```

Exit codes:

| Code | Meaning                                                             |
| ---- | ------------------------------------------------------------------- |
| 0    | Scan succeeded; JSON result on stdout                               |
| 1    | Path missing, not a directory, or any access error during the scan; JSON error on stderr, nothing on stdout |
| 2    | Invalid command-line usage                                          |

## Example output

For a directory containing `README.md`, `src/main.PY`, `src/util.py`,
`LICENSE`, and `archive.tar.gz`:

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

`total_files` always equals the sum of the `by_extension` counts. An empty
directory gives `{"by_extension": {}, "total_files": 0}`.

Error examples (stderr, exit code 1, stdout empty). Error messages do not
include paths:

```json
{"error": "path does not exist"}
{"error": "cannot read a directory inside the tree: Permission denied"}
```

### Extension rules

- The extension is the last suffix, lowercased: `a.PY` → `.py`,
  `x.tar.gz` → `.gz`.
- Files without an extension (`README`, `LICENSE`, `Makefile`), dotfiles such
  as `.env` or `.gitignore`, and names ending in `.` are counted under
  `"[no_extension]"`.

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

## Development

Run the tests (standard library `unittest`, no extra packages):

```sh
python3 -m unittest discover -s tests -t .
```
