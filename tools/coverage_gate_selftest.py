"""Watch the coverage gate fail. A gate nobody has seen fail is not a gate.

Run from the repository root::

    python tools/coverage_gate_selftest.py

Every case below builds a synthetic coverage report in a temporary directory,
so this runs in under a second and needs no test suite, no interpreter state
and no network. It is safe to wire into CI.

The cases are the four exit codes the gate can return:

    0   at or above the floor
    1   below the floor
    2   the report is missing, unreadable, or contradicts itself

A fifth case checks the comment body, because a comment format nobody has read
is a comment format that is wrong.
"""

import contextlib
import io
import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tools.coverage_gate import (  # noqa: E402
    COMMENT_MARKER,
    FLOOR_PERCENT,
    CoverageReportError,
    changed_rows,
    load_report,
    main,
    measure,
    render_comment,
)

FAILURES: list[str] = []


def build_report(rows: list[tuple[str, int, int]]) -> dict:
    """Build a coverage report from (path, covered, statements) triples.

    The ``totals`` block is computed from the rows so the report is internally
    consistent. A doctored report whose totals disagree is a separate case.
    """
    files = {}
    for path, covered, statements in rows:
        files[path] = {
            "summary": {
                "covered_lines": covered,
                "num_statements": statements,
                "missing_lines": statements - covered,
                "percent_covered": round(100.0 * covered / statements, 2) if statements else 0.0,
            }
        }
    covered = sum(row[1] for row in rows)
    statements = sum(row[2] for row in rows)
    return {
        "files": files,
        "totals": {
            "covered_lines": covered,
            "num_statements": statements,
            "missing_lines": statements - covered,
            "percent_covered": round(100.0 * covered / statements, 2) if statements else 0.0,
        },
    }


def check(name: str, condition: bool, detail: str = "", output: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}" + (f" -- {detail}" if detail else ""))
    if not condition and output:
        for line in output.strip().splitlines():
            print(f"        {line}")
    if not condition:
        FAILURES.append(name)


def write(path: pathlib.Path, report: dict) -> pathlib.Path:
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


def run_gate(argv: list[str]) -> tuple[int, str]:
    """Run the gate and capture what it printed.

    The gate is chatty by design: a CI log should show the measured numbers. In
    a self-test that interleaving hides which case produced which line, so the
    output is captured and only shown when a case fails.
    """
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
        try:
            code = main(argv)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 2
    return code, buffer.getvalue()


def main_() -> int:
    with tempfile.TemporaryDirectory() as raw:
        tmp = pathlib.Path(raw)

        # ---- 1. above the floor passes -----------------------------------
        healthy = build_report(
            [
                ("dsr\\api.py", 108, 110),
                ("dsr\\store.py", 78, 78),
                ("dsr\\features\\wf001_rooms.py", 75, 75),
            ]
        )
        healthy_path = write(tmp / "healthy.json", healthy)
        code, out = run_gate(["--report", str(healthy_path)])
        check("above the floor exits 0", code == 0, f"exit={code}", out)

        # ---- 2. below the floor fails ------------------------------------
        # 80 of 100 statements covered, so 80 percent against a 90 percent floor.
        sick = build_report([("dsr\\api.py", 80, 100)])
        sick_path = write(tmp / "sick.json", sick)
        code, out = run_gate(["--report", str(sick_path)])
        check("below the floor exits 1", code == 1, f"exit={code}", out)
        check(
            "the failure names the number, the floor and the file count",
            "80.00000 percent" in out
            and "90.0 percent floor" in out
            and "1 of 1 measured files are below the floor" in out,
        )

        # ---- 3. exactly on the floor passes ------------------------------
        exact = build_report([("dsr\\api.py", int(FLOOR_PERCENT), 100)])
        exact_path = write(tmp / "exact.json", exact)
        code, out = run_gate(["--report", str(exact_path)])
        check("exactly on the floor exits 0", code == 0, f"exit={code}", out)

        # ---- 4. a report that contradicts itself is an error -------------
        broken = build_report([("dsr\\api.py", 80, 100)])
        broken["totals"]["covered_lines"] = 99
        broken_path = write(tmp / "broken.json", broken)
        code, out = run_gate(["--report", str(broken_path)])
        check("a self-contradictory report exits 2", code == 2, f"exit={code}", out)

        # ---- 5. a missing report is an error, not a pass ------------------
        code, out = run_gate(["--report", str(tmp / "absent.json")])
        check("a missing report exits 2", code == 2, f"exit={code}", out)

        # ---- 6. the floor is a constant, not an argument -----------------
        check(
            "the floor is 90.0 and no flag changes it",
            FLOOR_PERCENT == 90.0,
            f"FLOOR_PERCENT={FLOOR_PERCENT}",
        )
        lowered = build_report([("dsr\\api.py", 80, 100)])
        lowered_path = write(tmp / "lowered.json", lowered)
        # argparse rejects the unknown flag and exits 2. That is the proof:
        # there is no argument through which a pull request can lower the floor.
        code, out = run_gate(["--report", str(lowered_path), "--floor", "10"])
        check(
            "a --floor flag cannot lower the floor",
            code == 2,
            f"exit={code}, and 2 is argparse rejecting an unknown flag",
            out,
        )

        # ---- 7. the measured total is recomputed, not trusted -------------
        doctored = build_report([("dsr\\api.py", 80, 100)])
        doctored["files"]["dsr\\api.py"]["summary"]["percent_covered"] = 99.9
        doctored_path = write(tmp / "lying.json", doctored)
        measured = measure(load_report(doctored_path))
        check(
            "a file that lies about its own percentage is recomputed",
            abs(measured["percent"] - 80.0) < 1e-9,
            f"measured={measured['percent']}",
        )

        # ---- 8. a report with no files is an error -----------------------
        empty = {"files": {}, "totals": {}}
        empty_path = write(tmp / "empty.json", empty)
        code, out = run_gate(["--report", str(empty_path)])
        check("a report with no files exits 2", code == 2, f"exit={code}", out)

        # ---- 9. the comment body ------------------------------------------
        body = render_comment(
            measure(load_report(sick_path)),
            ["backend/dsr/api.py", "frontend/src/App.jsx"],
        )
        check("the comment carries the sticky marker", COMMENT_MARKER in body)
        check(
            "the first line states the failure",
            body.splitlines()[1].startswith("### Coverage is below the floor"),
            body.splitlines()[1],
        )
        check(
            "the failing comment names the number and the floor",
            "80.00 percent" in body and "90.0 percent" in body,
        )
        check(
            "the comment table names the touched file",
            "`dsr/api.py`" in body,
        )
        check(
            "the comment says what it does not measure",
            "`frontend/src/App.jsx`" in body,
        )

        ok_body = render_comment(measure(load_report(healthy_path)), [])
        check(
            "a passing comment does not claim a failure",
            "below the floor. Total" not in ok_body,
        )

        # ---- 10. an unreadable report is an error -------------------------
        junk = tmp / "junk.json"
        junk.write_text("{not json", encoding="utf-8")
        code, out = run_gate(["--report", str(junk)])
        check("an unparsable report exits 2", code == 2, f"exit={code}", out)

        # ---- 11. a report that measured nothing is an error ---------------
        empty_trace = {
            "files": {"dsr\\api.py": {"summary": {"covered_lines": 0, "num_statements": 0}}},
            "totals": {},
        }
        try:
            measure(empty_trace)
        except CoverageReportError:
            check("a report that traced nothing is refused", True)
        else:
            check("a report that traced nothing is refused", False)

        # ---- 12. a changed path is found whichever separator it uses -------
        cross = build_report([("backend/dsr/api.py", 80, 100)])
        cross_path = write(tmp / "cross.json", cross)
        rows = changed_rows(measure(load_report(cross_path)), ["backend/dsr/api.py"])
        check(
            "a repository-relative path matches a report key",
            len(rows) == 1 and rows[0]["percent"] == 80.0,
            f"rows={len(rows)}",
        )

    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} case(s): {', '.join(FAILURES)}")
        return 1
    print("All coverage gate self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_())
