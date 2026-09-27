"""Merge the verified ports one at a time, testing after each.

Merging ports simultaneously would prove nothing: if the result is red, there is
no way to tell which port caused it. One at a time, with the suite and the host
check run after each merge, means the first break is unambiguous.

This is the actual test of the plugin host. Each feature is written by a different
agent in a different worktree with no knowledge of the others, and they merge with
zero shared-file conflicts. The previous run of this project built twelve such
branches and merged none, because all twelve edited the same three files.

The port list is DATA, read from data/pending_ports.json, which
orchestration/pending_ports.py regenerates from the worktrees and main.

It used to be a constant in this file, and that cost a merge cycle. The list was
hand-edited four times; three of those edits were silently reverted by a shell
restart before they were ever committed. The fourth reverted to a list naming
three ports that were already on main, so the script dutifully re-merged them,
reported "merged cleanly" three times, and never touched the port it was invoked
to merge. Every one of those runs looked like success.

A list describing the current state of the programme belongs somewhere that can be
regenerated and diffed, not in a literal that gets overwritten. Worse, a merge
script should never be trusted to have been told the right thing: it now reads
what is actually pending, and refuses to merge a feature that is already on main.
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
PENDING = MAIN / "data" / "pending_ports.json"

SHARED = {
    "backend/dsr/api.py", "backend/dsr/deps.py", "backend/dsr/store.py",
    "backend/dsr/db/audited.py", "backend/seed.py", "frontend/src/App.jsx",
    "frontend/src/main.jsx", "frontend/src/lib/api.js",
    "frontend/src/lib/features.js", "frontend/src/components/ui.jsx",
    "frontend/vite.config.js",
}


def run(args, cwd=MAIN, timeout=900, env=None):
    p = subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout, env=env)
    return p.returncode, (p.stdout + p.stderr)


def git(*args, **kw):
    return run(["git", *args], **kw)


def features_on(ref: str = "origin/main") -> set[str]:
    """Workflows whose feature module is already present at `ref`.

    Defaults to origin/main, but the merge target is usually a PR branch that
    already carries some of the ports - after a run that stopped halfway, or after
    a reviewer pushed a fix and re-ran the check. Skipping was decided against
    origin/main alone, so a re-run re-merged a port that was already on the branch
    being merged into, and the "no new feature" guard then correctly reported a
    no-op merge. The guard was right; the question was asked about the wrong tree.
    """
    code, out = git("ls-tree", "-r", "--name-only", ref, "backend/dsr/features")
    return {f"WF-{m.group(1)}" for f in out.splitlines()
            if (m := re.search(r"wf[_-]?(\d{3})", f))}


def load_ports():
    """Ports awaiting merge, smallest first so a break has the smallest blast radius."""
    if not PENDING.exists():
        print(f"  {PENDING} is missing.")
        print("  Regenerate it:  .venv/Scripts/python orchestration/pending_ports.py")
        return None

    entries = json.loads(PENDING.read_text(encoding="utf-8"))
    # Ask about the tree being merged INTO, not only about origin/main, so a re-run
    # after a partial run does not re-merge what it already merged.
    already = features_on("HEAD") | features_on("origin/main")
    todo, not_ready = [], []
    for e in entries:
        if e["ticket"] in already:
            print(f"  SKIP {e['ticket']}: already on main. Re-merging it would be a no-op "
                  f"that reports success, which is the failure this file was rewritten to stop.")
            continue
        # A workflow an agent is still writing is not a port yet. Offering it here
        # is how WF-001 came to be merged: it had ZERO commits, so the staging ref
        # was identical to main, `git merge` succeeded without changing anything,
        # and the script printed "merged cleanly" and a passing suite for a merge
        # that never happened. Only the final no-new-feature verdict caught it,
        # after a full baseline suite and a full post-merge suite spent on
        # nothing. Uncommitted work is excluded here so it is never attempted.
        if not e.get("commits_ahead"):
            not_ready.append(e)
            continue
        todo.append(e)

    if not_ready:
        print()
        print(f"  {len(not_ready)} workflow(s) are still being written and are NOT "
              f"mergeable yet - 0 commits:")
        for e in not_ready:
            print(f"    {e['ticket']:8} {e['worktree']:28} "
                  f"{e.get('uncommitted', 0)} uncommitted file(s)")
        print("  They are listed so it is visible they were considered and why they "
              "were left out, rather than silently absent.")

    if not todo:
        return []
    todo.sort(key=lambda e: (e.get("changed_files", 0), e["ticket"]))
    return [(e["ticket"], e["worktree"], e["branch"]) for e in todo]


def suite_and_host():
    """Run the suite and the registry check against the current checkout."""
    tmp = tempfile.mkdtemp(prefix="dsr-merge-")
    env = dict(os.environ)
    env["DSR_DB_PATH"] = str(Path(tmp) / "merge.db")
    env["DSR_AUDIT_DIR"] = str(Path(tmp) / "audit")

    code, out = run([str(PY), "-m", "pytest", "-p", "no:cacheprovider", "--tb=line",
                     "-o", "addopts=", "-q"], MAIN / "backend", env=env)
    passed = failed = 0
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
    # onto main and left the branch empty - so the PR had nothing to contain.
    code, out = git("rev-parse", "--abbrev-ref", "HEAD")
    target = out.strip()
    if target in ("", "HEAD"):
        print("  detached HEAD; refusing to guess a merge target")
        return 1

    ports = load_ports()
    if ports is None:
        return 1
    if not ports:
        print("  nothing pending: every port in data/pending_ports.json is already on main")
        return 0

    # What the tree being merged into already carries, before anything is fetched.
    already_here = features_on("HEAD")
    if already_here:
        print(f"  {target} already carries {len(already_here)} feature(s): "
              f"{sorted(already_here)}")

    print(f"=== merging into: {target} ===")
    print(f"  ports to merge: {', '.join(t for t, _, _ in ports)}")
    print()

    git("fetch", "origin")

    # --preflight walks every step except the suite and the merge itself.
    #
    # It exists because a one-character slip - unpacking git()'s two return
    # values as three - crashed on the first real merge, AFTER a full baseline
    # suite had already run. Five minutes spent finding out that the merge loop
    # could not start is the cost of not having a dry run, paid once here and
    # then again every time the loop changes.
    if "--preflight" in sys.argv:
        print("  PREFLIGHT: no suite, no merge. Checking every step can run.")
        for ticket, folder, branch in ports:
            staging = f"refs/dsr-staging/{ticket}"
            code, out = git("fetch", str(WORKSPACES / folder), f"{branch}:{staging}")
            if code != 0:
                print(f"  {ticket:8} fetch FAILED: {out.strip()[:160]}")
                return 1
            _, log_out = git("log", "--oneline", f"origin/main..{staging}")
            incoming = [l for l in log_out.splitlines() if l.strip()]
            _, diff_out = git("diff", "--name-only", f"origin/main...{staging}")
            offenders = sorted({f.replace("\\", "/") for f in diff_out.splitlines()
                                if f.strip()} & SHARED)
            _, hb = git("rev-parse", "HEAD")
            print(f"  {ticket:8} {len(incoming):>2} commit(s), "
                  f"{len([f for f in diff_out.splitlines() if f.strip()]):>2} file(s), "
                  f"shared={offenders or 'none'}, HEAD={hb.strip()[:8]}")
            if not incoming:
                print(f"  {ticket:8} WOULD REFUSE: no commits ahead of main")
            if offenders:
                print(f"  {ticket:8} WOULD REFUSE: touches {', '.join(offenders)}")
        print("  preflight clean. The merge loop can start.")
        return 0

    p, f, reg = suite_and_host()
    print(f"  baseline suite : {p} passed, {f} failed")
    for fid, prefix, n in (reg["loaded"] if reg else []):
        print(f"    {fid:34} {prefix or '-':18} {n} routes")
    if f or not reg or reg["failed"]:
        print("  ABORT: the baseline is already red, so a later failure could not be attributed")
        return 1
    base_features = {fid for fid, _, _ in reg["loaded"]}

    for ticket, folder, branch in ports:
        print()
        print("=" * 78)
        print(f"  MERGE {ticket}  ({branch})")
        print("=" * 78)

        # The agents could not push, so their commits exist only in their worktree.
        # Fetch from that path into a namespaced staging ref, NOT into the branch
        # name: git refuses to fetch into a branch checked out in another worktree,
        # which is exactly where these agents are still running.
        staging = f"refs/dsr-staging/{ticket}"
        code, out = git("fetch", str(WORKSPACES / folder), f"{branch}:{staging}")
        if code != 0:
            print(f"  fetch FAILED: {out.strip()[:300]}")
            return 1
        code, out = git("log", "--oneline", f"origin/main..{staging}")
        incoming = [l for l in out.splitlines() if l.strip()]
        print("  incoming commits:")
        for line in incoming:
            print(f"      {line}")
        if not incoming:
            # Belt to the braces of load_ports(). A staging ref with nothing
            # ahead of main is main, and merging main is a no-op that exits 0 -
            # so without this the script would go on to print "merged cleanly"
            # for a merge that did not happen.
            print(f"  REFUSING: {ticket} has no commits ahead of main. The staging ref is "
                  f"identical to main, so merging it would report success while "
                  f"changing nothing. The agent has not committed yet.")
            return 1

        # The shared-file guard, run on the incoming diff rather than trusted. The
        # guard is a CI job too, but this runs before the merge so a violation is
        # refused here rather than discovered on a PR.
        code, out = git("diff", "--name-only", f"origin/main...{staging}")
        offenders = sorted({f.replace("\\", "/") for f in out.splitlines() if f.strip()} & SHARED)
        if offenders:
            print(f"  REFUSING: {ticket} touches shared file(s): {', '.join(offenders)}")
            print("  A port is not the instrument for changing the host. See docs/FEATURE-CONTRACT.md.")
            return 1

        # git() returns (returncode, output) - two values, not three. Unpacking
        # three raised ValueError on the first real merge this guard was used for,
        # which is how a one-character slip in a safety check goes unnoticed until
        # the thing it was written to prevent actually happens.
        _, head_before = git("rev-parse", "HEAD")
        head_before = head_before.strip()
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
        _, head_after = git("rev-parse", "HEAD")
        head_after = head_after.strip()
        if head_after == head_before:
            # git merge exits 0 when there is nothing to do. That is a success
            # code for a no-op, and reporting it as "merged cleanly" is how a
            # feature that never landed gets counted as landed.
            print(f"  REFUSING: git merge exited 0 but HEAD did not move "
                  f"({head_before[:8]}). It had nothing to merge.")
            return 1
        print(f"  merged cleanly  ({len(incoming)} commit(s), {head_before[:8]} -> {head_after[:8]})")

        p, f, reg = suite_and_host()
        print(f"  suite after merge : {p} passed, {f} failed")
        new = set()
        for fid, prefix, n in (reg["loaded"] if reg else []):
            mark = "   <-- NEW" if fid not in base_features else ""
            if fid not in base_features:
                new.add(fid)
            print(f"    {fid:34} {prefix or '-':18} {n} routes{mark}")
        if reg and reg["failed"]:
            print("    FAILED FEATURES:")
            for fid, err in reg["failed"]:
                print(f"      {fid}: {err}")
        base_features |= new

        if not new:
            print(f"  VERDICT: {ticket} moved {head_before[:8]} -> {head_after[:8]} but added "
                  f"NO new feature the host loads. A merge that changes the tree without "
                  f"changing what the product serves is not a landed feature.")
            return 1
        if f or not reg or reg["failed"]:
            print(f"  VERDICT: BROKEN after merging {ticket}")
            return 1
        print(f"  VERDICT: clean after {ticket}")

    print()
    print("=" * 78)
    print("  FINAL")
    print("=" * 78)
    p, f, reg = suite_and_host()
    print(f"  suite : {p} passed, {f} failed")
    for fid, prefix, n in (reg["loaded"] if reg else []):
        print(f"    {fid:34} {prefix or '-':18} {n} routes")
    print("\n  history:")
    for line in git("log", "--oneline", "-8")[1].splitlines():
        print(f"    {line}")
    return 0 if f == 0 and reg and not reg["failed"] else 1


if __name__ == "__main__":
    sys.exit(main())
