#!/usr/bin/env python3
"""Render the coverage comment body for a pull request, locally, from real data.

The comment itself is posted by the built-in ``actions/github-script`` step,
which needs no extra dependency. The formatting lives here in Python so that a
human can read the exact body before it is merged, instead of discovering the
format on a pull request.

    python tools/coverage_comment.py
    python tools/coverage_comment.py --report backend/coverage.json
    python tools/coverage_comment.py --report backend/coverage.json \\
        --changed-from ../changed.txt --out ../comment.md

Both inputs are plain files. The report is the coverage JSON. The changed-file
list is newline-separated repository-relative paths, which is exactly what
``git diff --name-only`` produces.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.coverage_gate import (  # noqa: E402
    CoverageReportError,
    load_report,
    measure,
    read_changed,
    render_comment,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render the pull request coverage comment body.")
    parser.add_argument(
        "--report",
        type=pathlib.Path,
        default=pathlib.Path("backend/coverage.json"),
        help="coverage JSON report (default: backend/coverage.json)",
    )
    parser.add_argument(
        "--changed-from",
        type=pathlib.Path,
        default=None,
        help="text file of newline-separated repository-relative paths",
    )
    parser.add_argument(
        "--out", type=pathlib.Path, default=None, help="write the body to this path"
    )
    args = parser.parse_args(argv)

    try:
        measured = measure(load_report(args.report))
    except CoverageReportError as exc:
        print(f"Coverage comment error. {exc}", file=sys.stderr)
        return 2

    body = render_comment(measured, read_changed(args.changed_from))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(body, encoding="utf-8")
        print(f"Comment body written to {args.out}")
    else:
        print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
