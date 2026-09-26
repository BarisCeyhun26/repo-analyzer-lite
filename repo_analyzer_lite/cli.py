"""Command-line entry point."""

import argparse
import json
import sys

from repo_analyzer_lite.scanner import ScanError, scan


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="repo-analyzer-lite",
        description="Count files by extension in a local directory (read-only).",
    )
    parser.add_argument("path", help="directory to scan")
    args = parser.parse_args(argv)

    try:
        result = scan(args.path)
    except ScanError as exc:
        print(json.dumps({"error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0
