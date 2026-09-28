"""Land finished agents' work, one at a time, with the suite after each.

This is the merge pipeline, rebuilt around what was actually learned:

  * **Land by content, not by history.** Remote main's history has been
    rewritten; it shares a root commit with the agent worktrees and nothing
    after it. A cherry-pick therefore has a zero-line base and reports add/add
    conflicts on fourteen files the commit never touched. So a feature's
    contribution is taken to be the files it ADDS - which is what the feature
    contract says a feature is anyway.

  * **The agent's own commits are the ones after its merge-base with main.**
    Not `origin/main..branch` (returns the whole other lineage) and not
    `local-main..branch` (local main goes stale and returns main's missing PRs).
    The merge-base is the only base that is both current and shared.

  * **The shared-file guard runs on those added files**, so it measures the
    agent's contribution rather than a diff between unrelated trees.

  * **A violation is refused and the run continues.** One bad branch must not
    block the good ones behind it - which is exactly what happened when WF-029's
    refusal stopped sixteen finished features from landing.

  * **The suite runs after each landing**, so a red result names the feature that
    caused it. Six landing at once and going red identifies nothing.

  * **Attribution is set at landing.** Cherry-picking preserves the agent as
    author AND its co-author trailer, so the commit is made here instead, under
    the one permitted identity, with the agent's subject line kept.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(r"C:\Users\Dilip\orca\projects\client-theater\client-theater")
WORKSPACES = Path(r"C:\Users\Dilip\orca\workspaces\client-theater")
PENDING = ROOT / "data" / "pending_ports.json"
PY = ROOT / ".venv" / "Scripts" / "python.exe"
ME = "Dilip Nithyanandam <ddilipnithyanandam@gmail.com>"

SHARED = {
    "backend/dsr/api.py", "backend/dsr/deps.py", "backend/dsr/store.py",
    "backend/dsr/db/audited.py", "backend/seed.py", "frontend/src/App.jsx",
    "frontend/src/main.jsx", "frontend/src/lib/api.js",
    "frontend/src/lib/features.js", "frontend/src/components/ui.jsx",
    "frontend/vite.config.js",
}
TRAILER = re.compile(
    r"^(Co-authored-by|Signed-off-by|Reviewed-by|Generated-with|Thanks-to"
    r"|Report-Message|Mailmap-To)\s*:", re.I)


def git(*args, cwd=ROOT, timeout=300):
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def suite_and_registry():
    """The suite, and the host's own view of what it loaded."""
    env = {"DSR_DB_PATH": str(Path(tempfile.mkdtemp()) / "t.db"),
           "DSR_AUDIT_DIR": str(Path(tempfile.mkdtemp()) / "a"),
           "PATH": r"C:\Windows\System32;C:\Windows",
           "SYSTEMROOT": r"C:\Windows",
           "TEMP": tempfile.gettempdir(), "TMP": tempfile.gettempdir(),
           "USERPROFILE": str(Path.home())}
    p = subprocess.run([str(PY), "-m", "pytest", "-p", "no:cacheprovider",
                        "--tb=line", "-o", "addopts=", "-q"],
                       cwd=ROOT / "backend", capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=7200, env=env)
    out = p.stdout + p.stderr
    passed = failed = 0
    fails = []
    for line in out.splitlines():
        m = re.search(r"(\d+) passed", line)
        if m:
            passed = int(m.group(1))
        m = re.search(r"(\d+) failed", line)
        if m:
            failed = int(m.group(1))
        if line.startswith(("FAILED", "ERROR")):
            fails.append(line.strip())
    return passed, failed, fails


def features_on(ref="HEAD"):
    _, out, _ = git("ls-tree", "-r", "--name-only", ref, "backend/dsr/features")
    return {f"WF-{m.group(1)}" for f in out.splitlines()
            if (m := re.search(r"wf[_-]?(\d{3})", f))}


def clean_message(sha):
    _, raw, _ = git("log", "-1", "--format=%B", sha)
    subject, body, in_t = "", [], False
    for line in raw.splitlines():
        if TRAILER.match(line):
            in_t = True
            continue
        if in_t and not line.strip():
            continue
        in_t = False
        if not subject:
            if line.strip():
                subject = line.strip()
        else:
            body.append(line)
    return subject, "\n".join(body).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--branch", default="features/land-batch")
    ap.add_argument("--limit", type=int, default=0, help="0 = all ready")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not git("remote")[1].strip():
        git("remote", "add", "origin", "https://github.com/n-dilipkumar/client-theater.git")
    git("fetch", "origin")
    git("reset", "--hard")
    git("cherry-pick", "--abort")
    git("checkout", "-B", args.branch, "origin/main")
    git("reset", "--hard")

    already = features_on("HEAD") | features_on("origin/main")
    if not PENDING.exists():
        print("  data/pending_ports.json missing - run pending_ports.py first")
        return 1
    entries = json.loads(PENDING.read_text(encoding="utf-8"))

    print("=" * 78)
    print("  planning")
    print("=" * 78)
    plan, skipped, refused = [], [], []
    for e in entries:
        ticket = e["ticket"]
        if ticket in already:
            skipped.append((ticket, "already on main"))
            continue
        wt = WORKSPACES / e["worktree"]
        if not wt.is_dir():
            skipped.append((ticket, "worktree gone"))
            continue
        _, br, _ = git("rev-parse", "--abbrev-ref", "HEAD", cwd=wt)
        branch = br.strip()
        # Identify the agent's commit by its SUBJECT, not by a commit range.
        #
        # Ranges are unreliable here, and that is what made a guard refuse
        # twenty-two finished features. WF-031's merge-base with main is the
        # INITIAL COMMIT - it shares a root with main and nothing after it - so
        # `main..branch` returned main's entire other lineage, and every shared
        # file came back as ADDED because main's own commits sat in that range.
        # The guard reported twenty-two features for rewriting the host. They had
        # rewritten nothing; the range had swallowed a whole lineage.
        #
        # The four features that landed cleanly were found by subject, which is
        # why they worked. A subject names the workflow it belongs to, so it
        # survives a history rewrite. A range does not.
        _, lg, _ = git("log", "--format=%H %s", branch, cwd=wt)
        want = re.compile(rf"\b{ticket}\b", re.I)
        own = []
        for line in lg.splitlines():
            if not line.strip():
                continue
            sha, subj = line.split()[0], line[41:]
            if want.search(subj):
                own.append(sha)
        own.reverse()
        if not own:
            skipped.append((ticket, "no commit names it"))
            continue
        added = set()
        for sha in own:
            _, ns, _ = git("show", "--name-status", "--format=", sha, cwd=wt)
            for line in ns.splitlines():
                if line.startswith("A\t"):
                    added.add(line.split("\t")[-1].replace("\\", "/").strip())
        offenders = sorted(added & SHARED)
        if offenders:
            refused.append((ticket, offenders))
            continue
        if not added:
            skipped.append((ticket, "adds no files"))
            continue
        plan.append({"ticket": ticket, "worktree": e["worktree"], "branch": branch,
                     "commits": own, "files": sorted(added)})

    plan.sort(key=lambda p: len(p["files"]))
    if args.limit:
        plan = plan[:args.limit]
    print(f"  {len(plan)} to land, smallest first")
    for p in plan:
        print(f"    {p['ticket']}  {len(p['commits'])} commit(s), {len(p['files'])} file(s)")
    if refused:
        print()
        print(f"  {len(refused)} REFUSED for touching a shared file:")
        for t, files in refused:
            print(f"    {t}: {', '.join(files)}")
    if skipped:
        print()
        print(f"  {len(skipped)} not ready:")
        for t, why in skipped:
            print(f"    {t}: {why}")
    if args.dry_run:
        return 0

    landed, broke = [], []
    for p in plan:
        print()
        print("=" * 78)
        print(f"  LANDING {p['ticket']}  ({len(p['commits'])} commit(s), "
              f"{len(p['files'])} file(s))")
        print("=" * 78)
        rc, _, err = git("fetch", str(WORKSPACES / p["worktree"]),
                         f"{p['branch']}:refs/dsr-staging/{p['ticket']}")
        if rc != 0:
            print(f"  fetch FAILED: {err.strip()[:200]}")
            broke.append((p["ticket"], "fetch failed"))
            break
        rc, _, err = git("checkout", p["commits"][-1], "--", *p["files"])
        if rc != 0:
            print(f"  checkout FAILED: {err.strip()[:200]}")
            broke.append((p["ticket"], "checkout failed"))
            break
        git("add", *p["files"])

        # A file the agent added may ALREADY be on main - same content, different
        # history. Then `git add` stages nothing and `git commit` fails with
        # "nothing to commit" and an empty message, which reads as an unexplained
        # failure. It is not a failure: the content is already there.
        _, staged, _ = git("diff", "--cached", "--name-only")
        if not staged.strip():
            _, head_files, _ = git("show", "--name-only", "--format=", p["commits"][-1])
            missing = [f for f in p["files"] if f in head_files]
            print(f"  nothing to land: {len(p['files'])} file(s) are already on main "
                  f"with the same content")
            print("  That is a no-op, not a failure - continuing.")
            skipped.append((p["ticket"], "already on main"))
            continue

        subject, body = clean_message(p["commits"][-1])
        msg = (subject + "\n\n" + body + "\n\n"
               "Landed by content rather than by history. Remote main's history has "
               "been rewritten, so it shares a root commit with the agent worktrees "
               "and nothing after it; a cherry-pick therefore had a zero-line base and "
               "reported add/add conflicts on files this commit never touched. A "
               "feature's contribution is the files it ADDS, and the feature contract "
               "says a feature adds files, so those were taken and main's version of "
               "everything else was left alone.\n\n"
               "Authored and committed solely by ddilipnithyanandam@gmail.com; the "
               "original commit's agent identity and any co-author trailer were removed "
               "on landing. The subject line, which describes the work, is the "
               "agent's and is kept.")
        mf = ROOT / "data" / "_land.txt"
        mf.write_text(msg, encoding="utf-8")
        rc, _, cerr = git("commit", "-q", f"--author={ME}", "-F", str(mf))
        mf.unlink(missing_ok=True)
        if rc != 0:
            print(f"  commit FAILED: {(cerr or '(no message)').strip()[:250]}")
            broke.append((p["ticket"], f"commit failed: {cerr.strip()[:80]}"))
            break
        _, sha, _ = git("rev-parse", "--short", "HEAD")
        print(f"  landed {sha}")

        passed, failed, fails = suite_and_registry()
        new = features_on("HEAD") - already
        print(f"  suite : {passed} passed, {failed} failed")
        if new:
            print(f"  new   : {', '.join(sorted(new))}")
        already |= new
        if failed:
            print("  RED. Stopping here so the next step names the cause:")
            for f in fails[:10]:
                print(f"    {f}")
            broke.append((p["ticket"], f"{failed} test(s) failed"))
            break
        if not new:
            print("  WARNING: the suite is green but the host loaded no new feature.")
            print("  A change that does not change what the product serves is not a")
            print("  landed feature. Stopping rather than counting it as one.")
            broke.append((p["ticket"], "no new feature loaded"))
            break
        landed.append((p["ticket"], sha.strip()))

    print()
    print("=" * 78)
    print("  FINAL")
    print("=" * 78)
    print(f"  {len(landed)} landed: {', '.join(t for t, _ in landed) or 'none'}")
    if broke:
        print(f"  stopped on {broke[0][0]}: {broke[0][1]}")
    if refused:
        print(f"  {len(refused)} refused for a shared file: "
              f"{', '.join(t for t, _ in refused)}")
    _, tip, _ = git("log", "-1", "--format=%h %s")
    print(f"  branch tip: {tip}")
    print(f"  workflows on the branch: {len(features_on('HEAD'))}")
    print(f"  commits ahead of main  : {git('rev-list', '--count', 'origin/main..HEAD')[1].strip()}")
    return 1 if broke else 0


if __name__ == "__main__":
    sys.exit(main())
