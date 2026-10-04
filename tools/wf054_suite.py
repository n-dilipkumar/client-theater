"""Run the backend suite and print one summary line.

pytest's own summary line is easy to lose through a PowerShell pipeline that
wraps stderr, so this reads the report it wrote instead of scraping stdout.

    C:\\Users\\Dilip\\dsrvenv\\Scripts\\python.exe tools\\wf054_suite.py [--cov]
"""

from __future__ import annotations

import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
PYTHON = Path(sys.executable)
REPORT = Path(__file__).resolve().parent / "wf054-report.xml"


def run(*extra: str) -> None:
    """Run the suite and print the totals it produced."""
    command = [
        str(PYTHON),
        "-m",
        "pytest",
        "-p",
        "no:cacheprovider",
        "--tb=line",
        "-q",
        f"--junitxml={REPORT}",
        *extra,
    ]
    completed = subprocess.run(
        command,
        cwd=BACKEND,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    tail = (completed.stdout or "").strip().splitlines()
    for line in tail[-6:]:
        print(line)
    if not REPORT.exists():
        print("no report was written", file=sys.stderr)
        raise SystemExit(1)
    root = ET.parse(REPORT).getroot()
    # junit-xml wraps the run in <testsuites>, and some pytest versions leave the
    # attributes on the inner <testsuite> instead. Read whichever carries them
    # rather than assuming one shape, or the summary prints "None" and looks like
    # a measurement.
    if root.tag == "testsuites" and root.get("tests"):
        suites = root
    else:
        suites = next(iter(root.iter("testsuite")), root)
    print(
        "WF054 SUITE: total={tests} failures={failures} errors={errors} skipped={skipped}".format(
            tests=suites.get("tests"),
            failures=suites.get("failures"),
            errors=suites.get("errors"),
            skipped=suites.get("skipped"),
        )
    )
    raise SystemExit(0 if completed.returncode == 0 else completed.returncode)


if __name__ == "__main__":
    run(*sys.argv[1:])
