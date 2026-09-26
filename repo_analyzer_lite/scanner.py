"""Read-only directory scanner.

The scanner only lists directories and inspects entry types (lstat). It never
opens, executes, or writes files, and it never follows symbolic links.
"""

import os
import stat

# Directory names skipped at any depth: VCS metadata, dependency caches,
# virtualenvs, and common build output.
EXCLUDED_DIR_NAMES = frozenset(
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

NO_EXTENSION = "[no_extension]"


class ScanError(Exception):
    """Raised when the tree cannot be scanned completely.

    Messages never include file system paths.
    """


def extension_of(name):
    """Return the lowercased last suffix of a file name, or NO_EXTENSION.

    Dotfiles such as ``.gitignore`` have no extension.
    """
    stem, ext = os.path.splitext(name)
    if len(ext) <= 1 or not stem.strip("."):
        return NO_EXTENSION
    return ext.lower()


def scan(path):
    """Scan ``path`` recursively and return a JSON-serializable summary.

    Any access error while scanning aborts the scan with ScanError; a partial
    result is never returned.
    """
    if not os.path.lexists(path):
        raise ScanError("path does not exist")
    # An explicitly given root may itself be a symlink; resolve it once.
    # Symlinks found *inside* the tree are never followed.
    root = os.path.realpath(path)
    if not os.path.isdir(root):
        raise ScanError("path is not a directory")

    total_files = 0
    by_extension = {}

    stack = [root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError as exc:
            where = "the given directory" if current == root else "a directory inside the tree"
            raise ScanError(f"cannot read {where}: {exc.strerror or 'access error'}") from None

        for entry in entries:
            try:
                mode = entry.stat(follow_symlinks=False).st_mode
            except OSError as exc:
                raise ScanError(
                    f"cannot read file metadata inside the tree: {exc.strerror or 'access error'}"
                ) from None
            if stat.S_ISLNK(mode):
                continue
            if stat.S_ISDIR(mode):
                if entry.name not in EXCLUDED_DIR_NAMES:
                    stack.append(entry.path)
            elif stat.S_ISREG(mode):
                total_files += 1
                ext = extension_of(entry.name)
                by_extension[ext] = by_extension.get(ext, 0) + 1
            # Sockets, FIFOs, and device files are ignored.

    return {
        "total_files": total_files,
        "by_extension": dict(sorted(by_extension.items())),
    }
