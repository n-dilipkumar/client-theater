"""Verify all three completed ports independently, from evidence.

Each agent reported success. Reports are not evidence - an agent that says "246
passed" is an agent claiming a number, and the whole point of the review gate is
that the reviewer checks. This runs the checks itself, in each agent's worktree,
with the same interpreter CI uses.

The three things that actually matter for a port, in order of how badly they fail
the product:

  1. no shared file touched  - otherwise it collides with 99 other branches and
     the entire reason the plugin host exists is defeated
  2. the suite passes with the feature mounted - a feature that breaks core is
     worse than no feature
  3. the host actually loads it - the host *skips* a feature that fails to
     import and reports it, so a broken feature can look like a green build
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

MAIN = Path(__file__).resolve().parent.parent
WORKSPACES = Path(os.environ.get("DSR_WORKSPACES") or MAIN.parent)
PY = MAIN / ".venv" / "Scripts" / "python.exe"
if not PY.exists():
    PY = Path(sys.executable)

# One definition of the shared-file list, in tools/contract.py.
if str(MAIN) not in sys.path:
    sys.path.insert(0, str(MAIN))

from tools.contract import SHARED  # noqa: E402

TARGETS = [
    ("WF-002", "dsr-wf-002-buyer-pages", "n-dilipkumar/dsr-wf-002-buyer-pages"),
    ("WF-003", "dsr-wf-003-doc-library", "n-dilipkumar/dsr-wf-003-doc-library"),
    ("WF-012", "dsr-wf-012-room-generation", "n-dilipkumar/dsr-wf-012-room-generation"),
]


def run(args, cwd, timeout=600, env=None):
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


def main():
    results = {}
    for ticket, folder, _branch in TARGETS:
        wt = WORKSPACES / folder
        print("=" * 78)
        print(f"  {ticket}   {folder}")
        print("=" * 78)
        if not wt.exists():
            print("  MISSING worktree")
            results[ticket] = {"ok": False, "why": "worktree missing"}
            continue

        # --- 1. contract: no shared file
        _, out = run(["git", "diff", "--name-only", "origin/main...HEAD"], wt)
        files = [f for f in out.splitlines() if f.strip()]
        offenders = sorted(set(files) & SHARED)
        print(f"  changed files        : {len(files)}")
        print(f"  shared files touched : {offenders if offenders else 'NONE'}")
        for f in files:
            print(f"      {f}")

        # --- 2. the suite, with the feature mounted
        tmp = tempfile.mkdtemp(prefix=f"dsr-{ticket}-")
        env = dict(os.environ)
        env["DSR_DB_PATH"] = str(Path(tmp) / "verify.db")
        env["DSR_AUDIT_DIR"] = str(Path(tmp) / "audit")
        code, out = run(
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
            if " passed" in line or " failed" in line:
                if " passed" in line:
                    passed = int(line.split(" passed")[0].split()[-1])
                if " failed" in line:
                    try:
                        failed = int(line.split(" failed")[0].split()[-1])
                    except ValueError:
                        failed = -1
                break
        print(f"  backend suite        : {passed} passed, {failed} failed (exit {code})")
        if failed:
            for line in out.splitlines():
                if line.startswith(("FAILED", "ERROR")):
                    print(f"      {line}")

        # --- 3. does the host actually load it?
        # Written as a real file rather than a one-liner: squeezing this into
        # `python -c` is what produced a NameError on load_features, which read
        # as three failed ports when the ports were fine. The check is the thing
        # that decides whether a port is accepted, so it has to be readable.
        probe = wt / "backend" / "_verify_probe.py"
        probe.write_text(
            "import json, sys\n"
            "sys.path.insert(0, '.')\n"
            "from fastapi.testclient import TestClient\n"
            "from dsr.api import app\n"
            "from dsr.features import load_features\n"
            "load_features(app)\n"
            "with TestClient(app) as client:\n"
            "    data = client.get('/api/features').json()\n"
            "print(json.dumps({\n"
            "    'loaded': [(f['id'], f['prefix'], len(f['routes'])) for f in data['features']],\n"
            "    'failed': [(f['id'], f['error']) for f in data['failed']],\n"
            "}))\n",
            encoding="utf-8",
        )
        code3, out3 = run([str(PY), str(probe)], wt / "backend", env=env)
        probe.unlink(missing_ok=True)
        reg = None
        for line in out3.splitlines():
            line = line.strip()
            if line.startswith("{") and '"loaded"' in line:
                reg = json.loads(line)
        if reg:
            print("  host loaded          :")
            for fid, prefix, n in reg["loaded"]:
                mark = "  <-- this port" if ticket.lower().replace("-", "") in fid else ""
                print(f"      {fid:34} {prefix or '-':18} {n} routes{mark}")
            if reg["failed"]:
                print("  host FAILED features :")
                for fid, err in reg["failed"]:
                    print(f"      {fid}: {err}")
            else:
                print("  failed features      : none")
        else:
            print(f"  host check FAILED    : {out3[-400:]}")

        ok = (not offenders) and failed == 0 and reg is not None and not reg["failed"]
        results[ticket] = {
            "ok": ok,
            "passed": passed,
            "failed": failed,
            "offenders": offenders,
            "files": len(files),
        }
        print(f"\n  VERDICT: {'PASS' if ok else 'NEEDS WORK'}")
        print()

    print("=" * 78)
    print("  SUMMARY")
    print("=" * 78)
    for ticket, _, _ in TARGETS:
        r = results.get(ticket, {})
        print(
            f"  {ticket}  {r.get('passed', '?')} passed  {r.get('failed', '?')} failed  "
            f"shared={r.get('offenders', '?')}  -> {'PASS' if r.get('ok') else 'NEEDS WORK'}"
        )

    (MAIN / "data" / "verify_ports.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8"
    )
    return 0 if all(r.get("ok") for r in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
