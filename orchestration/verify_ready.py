"""Verify a port in its OWN worktree, before merging it.

The agent's report is not evidence. An agent that says "1,043 passed" is asserting
a number, and the whole point of the review gate is that the reviewer runs the
check. This has caught real defects repeatedly: a feature that could not load at
all because a dependency was missing, and a cross-feature blast radius where one
feature's missing dependency failed an unrelated feature's tests.

Three things decide a port, in the order of how badly each one fails the product:

  1. NO SHARED FILE TOUCHED. Otherwise the port collides with every other branch
     and the reason the plugin host exists is defeated.
  2. THE SUITE PASSES WITH THE FEATURE MOUNTED. A feature that breaks core is
     worse than no feature.
  3. THE HOST ACTUALLY LOADS IT. The host skips a feature whose import fails and
     reports it, so a broken feature otherwise looks like a green build - and a
     silently skipped feature is not a green run.

Plus one more, because a port can satisfy all three and still be wrong:

  4. THE FILENAME COLLISION IS RESOLVED. WF-004 and WF-015 both wanted
     `backend/dsr/access.py`; Jev decided WF-004 renames to `roles.py`. A port can
     be otherwise perfect and still ship a file another live feature owns, and no
     other check here would see it.
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

ROOT = Path(r"C:\Users\Dilip\orca\projects\client-theater\client-theater")
WORKSPACES = Path(r"C:\Users\Dilip\orca\workspaces\client-theater")
PY = ROOT / ".venv" / "Scripts" / "python.exe"

SHARED = {
    "backend/dsr/api.py", "backend/dsr/deps.py", "backend/dsr/store.py",
    "backend/dsr/db/audited.py", "backend/seed.py", "frontend/src/App.jsx",
    "frontend/src/main.jsx", "frontend/src/lib/api.js",
    "frontend/src/lib/features.js", "frontend/src/components/ui.jsx",
    "frontend/vite.config.js",
}


def run(args, cwd, timeout=1800, env=None):
    p = subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout, env=env)
    return p.returncode, (p.stdout + p.stderr)


def host_report(wt, env):
    """Ask the host itself which features it loaded and which it skipped."""
    probe = wt / "backend" / "_verify_probe.py"
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
        encoding="utf-8")
    _, out = run([str(PY), str(probe)], wt / "backend", env=env)
    probe.unlink(missing_ok=True)
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("{") and '"loaded"' in line:
            return json.loads(line)
    return None


def main():
    ready = json.loads((ROOT / "data" / "pending_ports.json").read_text(encoding="utf-8"))
    if not ready:
        print("  nothing ready to verify")
        return 0

    results = []
    for entry in ready:
        ticket, folder = entry["ticket"], entry["worktree"]
        wt = WORKSPACES / folder
        print("=" * 80)
        print(f"  {ticket}   {folder}")
        print("=" * 80)
        if not wt.exists():
            print("  worktree gone")
            continue

        _, diff = run(["git", "diff", "--name-only", "origin/main...HEAD"], wt)
        files = [f.strip().replace("\\", "/") for f in diff.splitlines() if f.strip()]
        _, st = run(["git", "status", "--porcelain"], wt)
        dirty = len([l for l in st.splitlines() if l.strip()])

        offenders = sorted(set(files) & SHARED)
        print(f"  commits      : {entry['commits_ahead']} ahead")
        print(f"  files        : {len(files)} changed, {dirty} uncommitted")
        print(f"  shared files : {offenders if offenders else 'NONE'}")

        # 4. the filename collision the Jev decision depends on
        claims = [f for f in files if f.endswith(("access.py", "access_api.py",
                                                 "roles.py", "roles_api.py",
                                                 "test_access.py", "test_access_api.py",
                                                 "test_roles.py", "test_roles_api.py"))]
        if claims:
            print(f"  access/roles : {claims}")
            both = [c for c in claims if "roles" in c]
            anyacc = [c for c in claims if "access" in c]
            if anyacc and ticket == "WF-004":
                print("      !! WF-004 must NOT ship an access.py - WF-015 keeps that name")

        tmp = tempfile.mkdtemp(prefix=f"dsr-verify-{ticket}-")
        env = dict(os.environ)
        env["DSR_DB_PATH"] = str(Path(tmp) / "v.db")
        env["DSR_AUDIT_DIR"] = str(Path(tmp) / "audit")

        _, out = run([str(PY), "-m", "pytest", "-p", "no:cacheprovider", "--tb=line",
                      "-o", "addopts=", "-q"], wt / "backend", env=env)
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
                    print(f"      {line[:120]}")

        reg = host_report(wt, env)
        shutil.rmtree(tmp, ignore_errors=True)
        if reg:
            for fid, prefix, n in reg["loaded"]:
                mark = "   <-- this port" if ticket[3:].replace("-", "") in fid else ""
                print(f"  loaded       : {fid:34} {prefix or '-':16} {n:2} routes{mark}")
            if reg["failed"]:
                print("  FAILED FEATURES:")
                for fid, err in reg["failed"]:
                    print(f"      {fid}: {err[:120]}")
            else:
                print("  failed       : none")

        added = [fid for fid, _, _ in (reg["loaded"] if reg else [])]
        ok = (not offenders and failed == 0 and reg is not None
              and not reg["failed"] and dirty == 0
              and any(ticket[3:].replace("-", "") in fid for fid in added))
        results.append({"ticket": ticket, "ok": ok, "passed": passed,
                        "failed": failed, "offenders": offenders, "dirty": dirty,
                        "files": len(files)})
        print(f"  VERDICT      : {'PASS' if ok else 'NEEDS WORK'}")
        print()

    print("=" * 80)
    print("  SUMMARY")
    print("=" * 80)
    for r in results:
        print(f"  {r['ticket']}  {r['passed']:>5} passed  {r['failed']} failed  "
              f"shared={r['offenders'] or 'none'}  -> {'PASS' if r['ok'] else 'NEEDS WORK'}")
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
