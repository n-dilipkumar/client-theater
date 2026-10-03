#!/usr/bin/env python3
"""Fail CI when backend test coverage falls below the agreed floor.

Why this exists
---------------
The floor was agreed in ``orchestration/TEST-REFACTOR.md`` section 2 and nothing
enforced it. Every CI run was green on a branch that sat below the floor,
because no job measured coverage. A floor with no enforcement is a sentence in
a document, not a gate.

What it reads
-------------
A coverage JSON report. The percentage is computed here from the per-file
summaries in that file. A printed line of pytest output is not read, because a
printed line is a claim and a JSON file is evidence. The ``totals`` block in the
same file is used only as a cross-check: if it disagrees with the sum of the
per-file rows, that is an error and this script exits non-zero rather than
reporting a number nobody can reproduce.

How it fails
------------
Exit code 1 when the measured total is below ``FLOOR_PERCENT``. The message
names the measured percentage, the floor, the missed statement count, and how
many measured files sit below the floor.

The floor is a module constant and there is no flag to change it. A threshold a
pull request can raise or lower is not a threshold.

Usage
-----
::

    python tools/coverage_gate.py --report backend/coverage.json
    python tools/coverage_gate.py --report backend/coverage.json --changed-from ../changed.txt

Run it from the repository root. ``--changed-from`` names a text file of
newline-separated repository-relative paths. Those files are the ones a
reviewer acts on, so they are named in the output and in the comment body.

Run this locally against a doctored report to see it fail::

    python tools/coverage_gate.py --report backend/coverage-doctored.json
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

#: The agreed floor, in percent. Hardcoded on purpose. See the module docstring.
FLOOR_PERCENT = 90.0

#: Marker that identifies this gate's own comment on a pull request. The comment
#: workflow searches for a comment carrying this exact string and updates it in
#: place, so forty pushes produce one comment and not forty.
COMMENT_MARKER = "<!-- ci-coverage-gate -->"

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


class CoverageReportError(RuntimeError):
    """The coverage report is missing, unreadable, or self-contradictory."""


def normalise_path(raw: str) -> str:
    """Reduce a report or git path to one comparable form.

    Coverage writes the keys relative to whatever directory it ran in, so the
    same file appears as ``dsr/api.py`` or ``backend/dsr/api.py`` depending on
    the caller. Windows git output uses backslashes. Both are folded to a
    forward-slash repository-relative path here so a lookup either matches or
    is honestly absent.
    """
    path = raw.replace("\\", "/").lstrip("./")
    if path.startswith("backend/"):
        path = path[len("backend/") :]
    return path


def load_report(path: pathlib.Path) -> dict[str, Any]:
    """Read the coverage JSON report, or raise with the reason."""
    if not path.is_file():
        raise CoverageReportError(f"No coverage report at {path}. The test step must write one.")
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CoverageReportError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(report.get("files"), dict) or not report["files"]:
        raise CoverageReportError(f"{path} has no 'files' block. It is not a coverage report.")
    return report


def measure(report: dict[str, Any]) -> dict[str, Any]:
    """Sum the per-file summaries into one measured total.

    The percentage is computed here from ``covered_lines`` and ``num_statements``
    rather than read from ``percent_covered``, because ``percent_covered`` is
    rounded by the producer. Recomputing from the integer counts also means the
    gate cannot be moved by a report whose percentage field disagrees with its
    own rows.
    """
    covered = 0
    statements = 0
    below_floor: list[tuple[str, float]] = []
    rows: list[dict[str, Any]] = []
    for raw_name, entry in report["files"].items():
        summary = entry.get("summary") or {}
        file_covered = int(summary.get("covered_lines", 0))
        file_statements = int(summary.get("num_statements", 0))
        if file_statements <= 0:
            continue
        percent = 100.0 * file_covered / file_statements
        covered += file_covered
        statements += file_statements
        rows.append(
            {
                "path": normalise_path(raw_name),
                "percent": percent,
                "covered": file_covered,
                "statements": file_statements,
                "missing": int(summary.get("missing_lines", file_statements - file_covered)),
            }
        )
        if percent < FLOOR_PERCENT:
            below_floor.append((normalise_path(raw_name), percent))
    if statements <= 0:
        raise CoverageReportError("The report measured 0 statements. Coverage traced nothing.")
    percent = 100.0 * covered / statements

    # Cross-check. A producer bug that fills 'totals' wrongly must not be able to
    # pass silently, so a disagreement is an error and not a warning.
    totals = report.get("totals") or {}
    if totals:
        reported_statements = int(totals.get("num_statements", statements))
        reported_covered = int(totals.get("covered_lines", covered))
        if (reported_statements, reported_covered) != (statements, covered):
            raise CoverageReportError(
                "The coverage report contradicts itself. "
                f"Per-file rows sum to {covered}/{statements} statements. "
                f"The totals block says {reported_covered}/{reported_statements}. "
                "Refusing to report a number that cannot be reproduced."
            )

    rows.sort(key=lambda row: (row["percent"], row["path"]))
    return {
        "percent": percent,
        "covered": covered,
        "statements": statements,
        "missing": statements - covered,
        "files_measured": len(rows),
        "files_below_floor": sorted(below_floor, key=lambda pair: pair[1]),
        "rows": rows,
    }


def read_changed(path: pathlib.Path | None) -> list[str]:
    """Read the repository-relative paths a pull request touched."""
    if path is None or not path.is_file():
        return []
    names = []
    for line in path.read_text(encoding="utf-8").splitlines():
        name = normalise_path(line.strip())
        if name:
            names.append(name)
    return names


def changed_rows(measured: dict[str, Any], changed: list[str]) -> list[dict[str, Any]]:
    """The per-file rows for the files the pull request touched.

    Both sides are normalised before the lookup. ``read_changed`` already
    normalises, and ``normalise_path`` is idempotent, so a caller that already
    normalised gets the same answer and a caller that did not gets a correct one
    instead of an empty table.
    """
    by_path = {row["path"]: row for row in measured["rows"]}
    rows = []
    for name in changed:
        row = by_path.get(normalise_path(name))
        if row is not None:
            rows.append(row)
    return rows


def unmeasured_names(measured: dict[str, Any], changed: list[str]) -> list[str]:
    """The changed paths the report says nothing about.

    These are files with no Python statements in the measured package: frontend
    sources, Markdown, and shell or JSON files. They are listed in one line
    rather than as table rows, because a row per unmeasured file buries the
    numbers a reviewer acts on.
    """
    measured_paths = {row["path"] for row in measured["rows"]}
    return [name for name in changed if normalise_path(name) not in measured_paths]


def render_report(measured: dict[str, Any], changed: list[str]) -> str:
    """The gate's own output, for the log and for a human reading it."""
    percent = measured["percent"]
    below = measured["files_below_floor"]
    lines = [
        "",
        "=" * 72,
        "CI coverage gate",
        "=" * 72,
        f"measured total   : {percent:.5f} percent",
        f"agreed floor     : {FLOOR_PERCENT:.1f} percent",
        f"covered          : {measured['covered']} of {measured['statements']} statements",
        f"missed           : {measured['missing']} statements",
        f"files measured   : {measured['files_measured']}",
        f"files below floor: {len(below)}",
    ]
    if changed:
        rows = changed_rows(measured, changed)
        unmeasured = unmeasured_names(measured, changed)
        lines.append(f"files this change touched: {len(rows)} measured of {len(changed)} changed")
        for row in rows:
            flag = "BELOW FLOOR" if row["percent"] < FLOOR_PERCENT else "ok"
            lines.append(
                f"  {row['percent']:7.2f} percent  {row['missing']:5d} missed  {row['path']}  [{flag}]"
            )
        if unmeasured:
            shown = ", ".join(unmeasured[:12])
            more = f", and {len(unmeasured) - 12} more" if len(unmeasured) > 12 else ""
            lines.append(f"  not measured by this report: {shown}{more}")
    lines.append("")
    return "\n".join(lines)


def render_comment(measured: dict[str, Any], changed: list[str]) -> str:
    """The sticky pull request comment body.

    When the total is below the floor the first line says so. A reviewer reads
    the first line, so the failure cannot live in a footnote.
    """
    percent = measured["percent"]
    below = measured["files_below_floor"]
    rows = changed_rows(measured, changed)
    unmeasured = unmeasured_names(measured, changed)

    if percent < FLOOR_PERCENT:
        headline = (
            f"Coverage is below the floor. Total {percent:.2f} percent against a "
            f"{FLOOR_PERCENT:.1f} percent floor."
        )
    else:
        headline = f"Coverage is {percent:.2f} percent against a {FLOOR_PERCENT:.1f} percent floor."

    lines = [
        COMMENT_MARKER,
        f"### {headline}",
        "",
        "| Measure | Value |",
        "|---|---|",
        f"| Total coverage | {percent:.5f} percent |",
        f"| Agreed floor | {FLOOR_PERCENT:.1f} percent |",
        f"| Covered statements | {measured['covered']} of {measured['statements']} |",
        f"| Missed statements | {measured['missing']} |",
        f"| Files measured | {measured['files_measured']} |",
        f"| Files below the floor | {len(below)} |",
        "",
    ]

    if changed:
        lines += ["#### Files this pull request touched", ""]
        if rows:
            lines += ["| File | Coverage | Missed | Statements |", "|---|---|---|---|"]
            for row in rows:
                mark = " **below floor**" if row["percent"] < FLOOR_PERCENT else ""
                lines.append(
                    f"| `{row['path']}` | {row['percent']:.2f} percent{mark} | "
                    f"{row['missing']} | {row['statements']} |"
                )
            lines.append("")
        else:
            lines += [
                "No file this pull request touched holds Python statements in the measured package.",
                "",
            ]
        if unmeasured:
            shown = ", ".join(f"`{name}`" for name in unmeasured[:12])
            more = f", and {len(unmeasured) - 12} more" if len(unmeasured) > 12 else ""
            lines += [f"Also changed and not measured by this report: {shown}{more}.", ""]
    else:
        lines += [
            "This pull request touches no file that coverage measures.",
            "",
        ]

    if below:
        worst = ", ".join(f"`{name}` at {pct:.2f} percent" for name, pct in below[:10])
        more = f" and {len(below) - 10} more" if len(below) > 10 else ""
        lines += [f"The files furthest below the floor are {worst}{more}.", ""]

    lines += [
        "The gate reads `backend/coverage.json` and computes this number from the "
        "per-file counts in that file. The threshold is the constant `FLOOR_PERCENT` "
        "in `tools/coverage_gate.py`. A pull request cannot change it.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail CI below the agreed coverage floor.")
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
        "--comment-out",
        type=pathlib.Path,
        default=None,
        help="write the pull request comment body to this path",
    )
    args = parser.parse_args(argv)

    try:
        report = load_report(args.report)
        measured = measure(report)
    except CoverageReportError as exc:
        print(f"Coverage gate error. {exc}", file=sys.stderr)
        return 2

    changed = read_changed(args.changed_from)
    print(render_report(measured, changed))

    body = render_comment(measured, changed)
    if args.comment_out:
        args.comment_out.parent.mkdir(parents=True, exist_ok=True)
        args.comment_out.write_text(body, encoding="utf-8")
        print(f"Comment body written to {args.comment_out}")

    if measured["percent"] < FLOOR_PERCENT:
        print(
            f"FAIL. Coverage {measured['percent']:.5f} percent is below the "
            f"{FLOOR_PERCENT:.1f} percent floor. "
            f"{measured['missing']} statements are missed. "
            f"{len(measured['files_below_floor'])} of {measured['files_measured']} "
            "measured files are below the floor.",
            file=sys.stderr,
        )
        return 1

    print(
        f"PASS. Coverage {measured['percent']:.5f} percent is at or above the "
        f"{FLOOR_PERCENT:.1f} percent floor."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
