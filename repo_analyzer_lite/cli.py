"""Command-line entry point."""

import argparse
import json
import sys

from repo_analyzer_lite.scanner import ScanError, scan


# Opt-in flags that are recognized only when spelled exactly.
EXACT_ONLY_FLAGS = frozenset({"--structure", "--sizes"})


class _ArgumentParser(argparse.ArgumentParser):
    """ArgumentParser that recognizes EXACT_ONLY_FLAGS only when spelled exactly.

    argparse accepts unambiguous prefixes of long options, which would make
    ``--st``/``--struct`` select ``--structure`` and ``--si``/``--size`` select
    ``--sizes``. The accepted baseline rejected those as unknown flags (exit 2),
    so they must stay unknown. Prefixes of ``--help`` (``--he``) keep working.
    """

    def _get_option_tuples(self, option_string):
        # Prefix-match lookup; exact option strings never reach this method.
        # Each tuple starts with (action, option_string, ...) on Python 3.9+.
        return [t for t in super()._get_option_tuples(option_string) if t[1] not in EXACT_ONLY_FLAGS]


def main(argv=None):
    parser = _ArgumentParser(
        prog="repo-analyzer-lite",
        description="Count files by extension in a local directory (read-only).",
    )
    parser.add_argument("path", help="directory to scan")
    parser.add_argument(
        "--structure",
        action="store_true",
        help="also count files per top-level directory (by_directory)",
    )
    parser.add_argument(
        "--sizes",
        action="store_true",
        help="also report total file bytes and bytes per extension (sizes)",
    )
    args = parser.parse_args(argv)

    try:
        result = scan(args.path, structure=args.structure, sizes=args.sizes)
    except ScanError as exc:
        print(json.dumps({"error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0
