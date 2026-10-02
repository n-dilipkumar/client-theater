"""Verify the set-2 ports that have committed, before merging any of them.

WF-007, WF-009 and WF-011 each have a commit and a clean tree. Reports are not
evidence: an agent that says "246 passed" is asserting a number, and the whole
point of the review gate is that the reviewer runs the check.

The three things that matter for a port, in order of how badly they fail the
product:

  1. no shared file touched - otherwise it collides with every other branch and
     the reason the plugin host exists is defeated
  2. the suite passes with the feature mounted - a feature that breaks core is
     worse than no feature
  3. the host actually LOADS it - the host skips a feature that fails to import
     and reports it, so a broken feature otherwise looks like a green build
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# Derived from this file, not named. This script used to hardcode one machine's
# orca checkout, so from any other clone it verified worktrees against a
# repository they had no relationship to.
ROOT = Path(__file__).resolve().parent.parent
WORKSPACES = Path(os.environ.get("DSR_WORKSPACES") or ROOT.parent)
PY = ROOT / ".venv" / "Scripts" / "python.exe"
if not PY.exists():  # a venv elsewhere on PATH, or POSIX layout
    PY = Path(sys.executable)

# One definition of the shared-file list, in tools/contract.py. This review gate
# used to carry its own copy; see the note there for why the copies could
# disagree without anything failing.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.contract import SHARED  # noqa: E402

TARGETS = [
    ("WF-007", "dsr-wf-007-content-library"),
    ("WF-009", "dsr-wf-009-publishing"),
    ("WF-011", "dsr-wf-011-room-handover"),
    ("WF-008", "dsr-wf-008-external-sync"),
    ("WF-013", "dsr-wf-013-conditional-rules"),
    ("WF-016", "dsr-wf-016-crm-sync"),
]


def run(args, cwd, timeout=900, env=None):
    p = subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
    )
    return p.returncode, (p.stdout + p.stderr)


def host_report(wt, env):
    """Ask the host itself which features it loaded and which it skipped."""
    probe = wt / "backend" / "_probe.py"
    probe.write_text(
        "import json, sys\n"
        "sys.path.insert(0, '.')\n"
        "from fastapi.testclient import TestClient\n"
        "from dsr.api import app\n"
        "from dsr.features import load_features\n"
        "load_features(app)\n"
        "with TestClient(app) as c:\n"
        "    d = c.get('/api/features').json()\n"
        "print(json.dumps({\n"
        "  'loaded': [(f['id'], f['prefix'], len(f['routes'])) for f in d['features']],\n"
        "  'failed': [(f['id'], f['error']) for f in d['failed']]}))\n",
        encoding="utf-8",
    )
    _, out = run([str(PY), str(probe)], wt / "backend", env=env)
    probe.unlink(missing_ok=True)
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("{") and '"loaded"' in line:
            return json.loads(line)
    return None


def main():
    results = {}
    for ticket, folder in TARGETS:
        wt = WORKSPACES / folder
        print("=" * 78)
        print(f"  {ticket}   {folder}")
        print("=" * 78)
        if not wt.exists():
            print("  worktree not created yet")
            continue

        _, out = run(["git", "diff", "--name-only", "origin/main...HEAD"], wt)
        files = [f for f in out.splitlines() if f.strip()]
        _, ahead = run(["git", "rev-list", "--count", "origin/main..HEAD"], wt)
        _, dirty = run(["git", "status", "--porcelain"], wt)
        dirty_n = len([ln for ln in dirty.splitlines() if ln.strip()])

        if not files:
            print(f"  no commits yet ({dirty_n} uncommitted file(s)) - agent still working")
            continue

        offenders = sorted(set(files) & SHARED)
        print(
            f"  commits      : {ahead.strip()} ahead, {len(files)} file(s) changed, {dirty_n} uncommitted"
        )
        print(f"  shared files : {offenders if offenders else 'NONE'}")

        tmp = tempfile.mkdtemp(prefix=f"dsr-{ticket}-")
        env = dict(os.environ)
        env["DSR_DB_PATH"] = str(Path(tmp) / "v.db")
        env["DSR_AUDIT_DIR"] = str(Path(tmp) / "audit")

        _, out = run(
            [
                str(PY),
                "-m",
                "pytest",
                "-p",
                "no:cacheprovider",
                "--tb=line",
                "-o",
                "addopts=",
                "-q",
            ],
            wt / "backend",
            env=env,
        )
        passed = failed = 0
        for line in reversed(out.splitlines()):
            m = re.search(r"(\d+) passed", line)
            if m:
                passed = int(m.group(1))
            m = re.search(r"(\d+) failed", line)
            if m:
                failed = int(m.group(1))
            if passed or failed:
                break
        print(f"  suite        : {passed} passed, {failed} failed")
        if failed:
            for line in out.splitlines():
                if line.startswith(("FAILED", "ERROR")):
                    print(f"      {line[:110]}")

        reg = host_report(wt, env)
        shutil.rmtree(tmp, ignore_errors=True)
        if reg:
            for fid, prefix, n in reg["loaded"]:
                mark = "   <-- this port" if ticket.lower().replace("-", "") in fid else ""
                print(f"  loaded       : {fid:34} {prefix or '-':16} {n:2} routes{mark}")
            if reg["failed"]:
                print("  FAILED       :")
                for fid, err in reg["failed"]:
                    print(f"      {fid}: {err[:110]}")
            else:
                print("  failed       : none")

        ok = not offenders and failed == 0 and reg is not None and not reg["failed"]
        results[ticket] = {
            "ok": ok,
            "passed": passed,
            "failed": failed,
            "offenders": offenders,
            "files": len(files),
        }
        print(f"  VERDICT      : {'PASS' if ok else 'NEEDS WORK'}")
        print()

    print("=" * 78)
    print("  SUMMARY")
    print("=" * 78)
    for ticket, _ in TARGETS:
        r = results.get(ticket)
        if not r:
            print(f"  {ticket}  (still working)")
        else:
            print(
                f"  {ticket}  {r['passed']:>4} passed  {r['failed']} failed  "
                f"shared={r['offenders'] or 'none'}  -> {'PASS' if r['ok'] else 'NEEDS WORK'}"
            )
    (ROOT / "data" / "verify_set2_set3.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
