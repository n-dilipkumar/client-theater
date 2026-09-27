"""Merge the verified ports one at a time, testing after each.

Three ports passed review independently. Merging them simultaneously would prove
nothing: if the result is red, there is no way to tell which port caused it. One at
a time, with the suite and the host check run after each merge, means the first
break is unambiguous.

This is the actual test of the plugin host. Three features, each written by a
different agent in a different worktree with no knowledge of each other, merging
with zero shared-file conflicts. The previous run of this project built twelve such
branches and merged none, because all twelve edited the same three files.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MAIN = Path(r"C:\Users\Dilip\orca\projects\client-theater\client-theater")
WORKSPACES = Path(r"C:\Users\Dilip\orca\workspaces\client-theater")
PY = MAIN / ".venv" / "Scripts" / "python.exe"

# Merge order: smallest surface first, so if something breaks the blast radius is
# smallest and the cause is easiest to read.
PORTS = [
    ("WF-012", "dsr-wf-012-room-generation", "n-dilipkumar/dsr-wf-012-room-generation"),
    ("WF-002", "dsr-wf-002-buyer-pages", "n-dilipkumar/dsr-wf-002-buyer-pages"),
    ("WF-003", "dsr-wf-003-doc-library", "n-dilipkumar/dsr-wf-003-doc-library"),
]


def run(args, cwd=MAIN, timeout=900, env=None):
    p = subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout, env=env)
    return p.returncode, (p.stdout + p.stderr)


def git(*args, **kw):
    return run(["git", *args], **kw)


def suite_and_host(label):
    """Run the suite and the registry check against the current main checkout."""
    tmp = tempfile.mkdtemp(prefix="dsr-merge-")
    env = dict(os.environ)
    env["DSR_DB_PATH"] = str(Path(tmp) / "merge.db")
    env["DSR_AUDIT_DIR"] = str(Path(tmp) / "audit")

    code, out = run([str(PY), "-m", "pytest", "-p", "no:cacheprovider", "--tb=line",
                     "-o", "addopts=", "-q"], MAIN / "backend", env=env)
    passed, failed = 0, 0
    for line in reversed(out.splitlines()):
        if " passed" in line or " failed" in line:
            m = re.search(r"(\d+) passed", line)
            if m:
                passed = int(m.group(1))
            m = re.search(r"(\d+) failed", line)
            if m:
                failed = int(m.group(1))
            break
    if failed:
        for line in out.splitlines():
            if line.startswith(("FAILED", "ERROR")):
                print(f"        {line}")

    probe = MAIN / "backend" / "_merge_probe.py"
    probe.write_text(
        "import json, sys\n"
        "sys.path.insert(0, '.')\n"
        "from fastapi.testclient import TestClient\n"
        "from dsr.api import app\n"
        "from dsr.features import load_features\n"
        "load_features(app)\n"
        "with TestClient(app) as c:\n"
        "    d = c.get('/api/features').json()\n"
        "print(json.dumps({'loaded': [(f['id'], f['prefix'], len(f['routes'])) for f in d['features']],\n"
        "                  'failed': [(f['id'], f['error']) for f in d['failed']]}))\n",
        encoding="utf-8")
    _, out2 = run([str(PY), str(probe)], MAIN / "backend", env=env)
    probe.unlink(missing_ok=True)
    shutil.rmtree(tmp, ignore_errors=True)

    reg = None
    for line in out2.splitlines():
        line = line.strip()
        if line.startswith("{") and '"loaded"' in line:
            reg = json.loads(line)
    return passed, failed, reg


def main():
    # Merge into whatever branch is checked out. This script used to do
    # `git checkout main` first, which silently moved work authored on a PR branch
    # onto main and left the branch empty - so the PR had nothing to contain. It
    # took three attempts to spot, because the merges kept succeeding and only the
    # push said "up-to-date".
    code, out = git("rev-parse", "--abbrev-ref", "HEAD")
    target = out.strip()
    if target in ("", "HEAD"):
        print("  detached HEAD; refusing to guess a merge target")
        return 1
    code, out = git("fetch", "origin")
    print(f"=== merging into: {target} ===")
    p, f, reg = suite_and_host("baseline")
    print(f"  suite : {p} passed, {f} failed")
    if reg:
        for fid, prefix, n in reg["loaded"]:
            print(f"    {fid:34} {prefix or '-':18} {n} routes")
    base_features = {fid for fid, _, _ in reg["loaded"]} if reg else set()

    for ticket, folder, branch in PORTS:
        print()
        print("=" * 78)
        print(f"  MERGE {ticket}  ({branch})")
        print("=" * 78)

        # The agents could not push, so their commits exist only in their worktree.
        # Fetch from that path into a namespaced staging ref, NOT into the branch
        # name: git refuses to fetch into a branch checked out in another
        # worktree, which is exactly where these agents are still running.
        staging = f"refs/dsr-staging/{ticket}"
        code, out = git("fetch", str(WORKSPACES / folder), f"{branch}:{staging}")
        if code != 0:
            print(f"  fetch FAILED: {out.strip()[:300]}")
            return 1
        code, out = git("log", "--oneline", f"origin/main..{staging}")
        print(f"  incoming commits:")
        for line in out.splitlines():
            print(f"      {line}")

        code, out = git("merge", "--no-ff", staging, "-m",
                        f"Merge {ticket} ported onto the plugin host\n\n"
                        f"Reviewed independently before merge: no shared file touched, full suite\n"
                        f"passes with the feature mounted, and the host reports it loaded with\n"
                        f"zero failed features. Merged one port at a time with the suite and the\n"
                        f"registry check re-run after each, so a red result would name its cause.\n\n"
                        f"Co-authored-by: CommandCodeBot noreply@commandcode.ai")
        if code != 0:
            print(f"  MERGE CONFLICT:\n{out[:1500]}")
            return 1
        print("  merged cleanly")

        p, f, reg = suite_and_host(ticket)
        print(f"  suite after merge : {p} passed, {f} failed")
        new = set()
        if reg:
            for fid, prefix, n in reg["loaded"]:
                mark = ""
                if fid not in base_features:
                    new.add(fid)
                    mark = "   <-- NEW"
                print(f"    {fid:34} {prefix or '-':18} {n} routes{mark}")
            if reg["failed"]:
                print("    FAILED FEATURES:")
                for fid, err in reg["failed"]:
                    print(f"      {fid}: {err}")
        base_features |= new

        if f or not reg or reg["failed"]:
            print(f"  VERDICT: BROKEN after merging {ticket}")
            return 1
        print(f"  VERDICT: clean after {ticket}")

    print()
    print("=" * 78)
    print("  FINAL")
    print("=" * 78)
    p, f, reg = suite_and_host("final")
    print(f"  suite : {p} passed, {f} failed")
    for fid, prefix, n in (reg["loaded"] if reg else []):
        print(f"    {fid:34} {prefix or '-':18} {n} routes")
    code, out = git("log", "--oneline", "-8")
    print("\n  history:")
    for line in out.splitlines():
        print(f"    {line}")
    return 0 if f == 0 and reg and not reg["failed"] else 1


if __name__ == "__main__":
    sys.exit(main())
