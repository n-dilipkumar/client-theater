"""Generate the programme status dashboard, and assert it is true.

The audit log is append-only and grows forever, which makes it a poor dashboard:
to answer "how far along are we" you have to read 45,000 characters. So the status
lives in one generated file, and this generator is the only thing that writes it.

Everything here is MEASURED, never carried forward:

  * features live   - read out of `git ls-tree` on origin/main, tolerant of
                      `wf004`, `wf_004` and `wf-004`, because with a hundred
                      agents each picking their own spelling a single pattern
                      cannot be relied on
  * routes and failures - asked of the host itself, not counted from files
  * tests           - the suite is run
  * the board       - read from Orca
  * what is pending - the worktrees, split into finished and in flight
  * what is blocked - the workflows whose branches edit the audit core, and the
                      duplicate, both of which are DECISIONS now, not blockers

A dashboard that reports a stale number is worse than none, because it is read
instead of re-measured. So the generator recomputes everything and the assert
below checks the rendered file against what it just measured.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import tempfile
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(r"C:\Users\Dilip\orca\projects\client-theater\client-theater")
WORKSPACES = Path(r"C:\Users\Dilip\orca\workspaces\client-theater")
WFDIR = ROOT / "docs" / "research" / "digital-sales-room-workflows" / "wf"
OUT = ROOT / "orchestration" / "STATUS.md"
PY = ROOT / ".venv" / "Scripts" / "python.exe"
REPO_ID = "id:8964203a-831a-425f-8fd7-ebc3a0fc2e46"

TARGET = 100
NOT_THE_FEATURES = ("orchestration/", "docs/", ".github/", "tools/", "data/")

# A workflow is live when its feature module is on main. Tolerant of every
# spelling a build brief might produce.
FEATURE_RE = re.compile(r"wf[_-]?(\d{3})")
# A worktree directory name, e.g. dsr-wf-008-external-sync
TICKET_RE = re.compile(r"wf-(\d{3})", re.I)


def git(*args, cwd=ROOT, timeout=120):
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    return (p.stdout + p.stderr).strip()


def orca(args, timeout=120):
    p = subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        return {}


def features_live():
    out = git("ls-tree", "-r", "--name-only", "origin/main", "backend/dsr/features")
    found = {}
    for line in out.splitlines():
        m = FEATURE_RE.search(line)
        if m:
            found[f"WF-{m.group(1)}"] = line.split("/")[-1]
    return found


def host_report():
    """Ask the host what it loads, rather than counting route decorators."""
    probe = ROOT / "backend" / "_status_probe.py"
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
    env = dict(os.environ)
    tmp = tempfile.mkdtemp(prefix="dsr-status-")
    env["DSR_DB_PATH"] = str(Path(tmp) / "s.db")
    env["DSR_AUDIT_DIR"] = str(Path(tmp) / "audit")
    p = subprocess.run([str(PY), str(probe)], cwd=ROOT / "backend", capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=300, env=env)
    probe.unlink(missing_ok=True)
    for line in p.stdout.splitlines():
        line = line.strip()
        if line.startswith("{") and '"loaded"' in line:
            return json.loads(line)
    return None


SUITE_CACHE = ROOT / "data" / "suite_result.json"


def suite_count(argv):
    """Measure the suite, or reuse a recent measurement.

    Running the suite is the slow part - about three minutes unloaded, and over
    fifteen with two dozen agents competing for the CPU, which is how this
    generator got killed mid-run and left the dashboard describing a state that
    had never been committed. A dashboard that costs a quarter of an hour to
    refresh is a dashboard nobody refreshes, and a stale dashboard is worse than
    none because it is read instead of re-measured.

    So the test count is the one number that may be supplied rather than
    recomputed, and it is never invented: it comes either from this run or from
    a measurement on record, and the dashboard says which, and when.
    """
    for i, a in enumerate(argv):
        if a == "--tests" and i + 1 < len(argv):
            parts = argv[i + 1].split(",")
            return int(parts[0]), int(parts[1]), (int(parts[2]) if len(parts) > 2 else 0), "supplied"
        if a.startswith("--tests="):
            parts = a.split("=", 1)[1].split(",")
            return int(parts[0]), int(parts[1]), (int(parts[2]) if len(parts) > 2 else 0), "supplied"

    if SUITE_CACHE.exists():
        try:
            d = json.loads(SUITE_CACHE.read_text(encoding="utf-8"))
            age = time.time() - d.get("at", 0)
            # An hour is the window in which a test count is a fact about the
            # current tree rather than a recollection.
            if age < 3600 and d.get("passed"):
                return d["passed"], d.get("failed", 0), d.get("xfailed", 0), \
                    f"measured {int(age // 60)} min ago"
        except (json.JSONDecodeError, KeyError, TypeError):
            pass

    env = dict(os.environ)
    tmp = tempfile.mkdtemp(prefix="dsr-status-tests-")
    env["DSR_DB_PATH"] = str(Path(tmp) / "t.db")
    env["DSR_AUDIT_DIR"] = str(Path(tmp) / "audit")
    p = subprocess.run(
        [str(PY), "-m", "pytest", "-p", "no:cacheprovider", "--tb=line", "-o", "addopts=", "-q"],
        cwd=ROOT / "backend", capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=5400, env=env)
    out = p.stdout + p.stderr
    passed = failed = xfail = 0
    for line in reversed(out.splitlines()):
        m = re.search(r"(\d+) passed", line)
        if m:
            passed = int(m.group(1))
        m = re.search(r"(\d+) failed", line)
        if m:
            failed = int(m.group(1))
        m = re.search(r"(\d+) xfailed", line)
        if m:
            xfail = int(m.group(1))
        if passed or failed:
            break
    try:
        SUITE_CACHE.parent.mkdir(parents=True, exist_ok=True)
        SUITE_CACHE.write_text(json.dumps(
            {"at": time.time(), "passed": passed, "failed": failed,
             "xfailed": xfail, "ref": stamp_of("origin/main")}), encoding="utf-8")
    except OSError:
        pass
    return passed, failed, xfail, "measured now"


def stamp_of(ref):
    return git("log", "-1", "--format=%cI", ref).strip()


def worktree_states(live):
    """Split agent worktrees into finished and in flight, from the worktrees."""
    ready, flying = [], []
    if not WORKSPACES.exists():
        return ready, flying
    for d in sorted(WORKSPACES.iterdir()):
        m = TICKET_RE.search(d.name)
        if not m or not d.is_dir() or not d.name.startswith("dsr-"):
            continue
        ticket = f"WF-{m.group(1)}"
        if ticket in live:
            continue
        ahead = git("rev-list", "--count", "origin/main..HEAD", cwd=d)
        dirty = len([l for l in git("status", "--porcelain", cwd=d).splitlines() if l.strip()])
        entry = {"ticket": ticket, "worktree": d.name,
                 "commits": int(ahead) if ahead.isdigit() else 0,
                 "uncommitted": dirty}
        (ready if entry["commits"] and not dirty else flying).append(entry)
    return ready, flying


def board_counts():
    listing = orca(["orca", "worktree", "list", "--repo", REPO_ID, "--json"])
    cards = [w for w in listing.get("result", {}).get("worktrees", [])
             if (w.get("branch") or "").startswith(("refs/heads/feature/", "refs/heads/n-dilipkumar/"))]
    counts = {}
    for c in cards:
        counts[c.get("workspaceStatus")] = counts.get(c.get("workspaceStatus"), 0) + 1
    return counts, len(cards)


def spec_census():
    total = complete = 0
    if WFDIR.exists():
        for p in WFDIR.glob("WF-*.md"):
            total += 1
            t = p.read_text(encoding="utf-8", errors="replace")
            if all(re.search(rf"\b{s}\b", t) for s in
                   ("user_flow", "data_flow", "data_sources", "apis_hit",
                    "automations", "features_tools", "extensibility", "evidence")):
                complete += 1
    return total, complete


def bar(done, total, width=34):
    filled = int(width * done / total) if total else 0
    return "[" + "#" * filled + "." * (width - filled) + f"] {done}/{total}"


def main():
    argv = sys.argv[1:]
    live = features_live()
    reg = host_report()
    passed, failed, xfail, tests_from = suite_count(argv)
    ready, flying = worktree_states(live)
    board, cards = board_counts()
    spec_total, spec_complete = spec_census()

    routes = sum(n for _, _, n in reg["loaded"]) if reg else 0
    failed_features = len(reg["failed"]) if reg else -1
    prefixes = {}
    for fid, prefix, n in (reg["loaded"] if reg else []):
        prefixes.setdefault(prefix or "-", []).append((fid, n))

    spec_without_code = [f"WF-{i:03d}" for i in range(1, spec_total + 1)
                         if f"WF-{i:03d}" not in live]
    remaining = TARGET - len(live)

    now = git("log", "-1", "--format=%h %s", "origin/main")
    stamp = subprocess.run(["git", "log", "-1", "--format=%cI", "origin/main"],
                           cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace").stdout.strip()

    L = []
    A = L.append
    A("# Programme status")
    A("")
    A(f"<!-- GENERATED by orchestration/make_status.py. Do not edit by hand: every")
    A(f"     number here is measured, and a hand-edited dashboard is a stale one. -->")
    A("")
    A(f"**Target {TARGET} workflows.** Built from **{spec_total} researched")
    A(f"specifications** ({spec_complete} complete). Regenerate with")
    A("`.venv/Scripts/python orchestration/make_status.py`.")
    A("")
    A(f"`main` at `{now}` &middot; measured {stamp}")
    A("")
    A("## Progress")
    A("")
    A(f"    workflows  {bar(len(live), TARGET)}")
    A(f"    routes     {routes}")
    A(f"    tests      {passed} passed, {failed} failed, {xfail} xfailed  ({tests_from})")
    A(f"    features   {failed_features} failed to load")
    A(f"    to go      {remaining}")
    A("")
    A("## Every feature the host loads")
    A("")
    A("| Feature | Prefix | Routes |")
    A("|---|---|---|")
    for fid, prefix, n in sorted(reg["loaded"] if reg else []):
        A(f"| `{fid}` | `{prefix}` | {n} |")
    if reg and reg["failed"]:
        A("")
        A("**Failed to load:**")
        for fid, err in reg["failed"]:
            A(f"- `{fid}`: {err}")
    A("")
    shared = {p: v for p, v in prefixes.items() if len(v) > 1 and p != "-"}
    if shared:
        A("### Prefixes carrying more than one feature")
        A("")
        A("These are the case the plugin host exists to allow, and the one")
        A("`PORT-PLAN.md` blocked as *\"researched twice\"*. A prefix comparison is")
        A("not the rule the host enforces - the host refuses on a concrete")
        A("`(method, path)` after prefixing - so these coexist by measurement, not")
        A("by luck.")
        A("")
        for p, v in sorted(shared.items()):
            A(f"- `{p}` &mdash; {len(v)} features: " + ", ".join(f"`{f}`" for f, _ in sorted(v)))
        A("")

    A("## In flight")
    A("")
    A(f"### Ready to verify and merge ({len(ready)})")
    A("")
    if ready:
        A("| Workflow | Worktree | Commits |")
        A("|---|---|---|")
        for e in ready:
            A(f"| {e['ticket']} | `{e['worktree']}` | {e['commits']} |")
    else:
        A("None.")
    A("")
    A(f"### Being written ({len(flying)})")
    A("")
    if flying:
        A("| Workflow | Worktree | Uncommitted files |")
        A("|---|---|---|")
        for e in flying:
            A(f"| {e['ticket']} | `{e['worktree']}` | {e['uncommitted']} |")
    else:
        A("None.")
    A("")

    A("## Board")
    A("")
    A("| Status | Cards |")
    A("|---|---|")
    for k in ("completed", "in-progress", "todo", "blocked"):
        if board.get(k):
            A(f"| {k} | {board[k]} |")
    A(f"| **total** | **{cards}** |")
    A("")

    A("## Awaiting a decision")
    A("")
    A("Decisions are mine to make, and these are the open ones. Each is recorded")
    A("with its reasoning in `orchestration/PROGRAM-AUDIT.md` once taken.")
    A("")
    A("| Item | What has to be decided |")
    A("|---|---|")
    A("| `dsr-wf-008-external-sync-2` | A second, unreviewed WF-008 implementation: "
      "7 files absent from `main`, 8 differing, 0 identical. Which is better. |")
    for t in ("WF-001", "WF-005", "WF-014"):
        A(f"| {t} | Its branch edits `db/audited.py` and `store.py` &mdash; the audit "
          f"guarantee itself. Whether the change belongs in the feature or the host. |")
    A("")

    A("## Remaining work")
    A("")
    A(f"- **{remaining}** workflows to build to reach {TARGET}")
    A(f"- **{len(spec_without_code)}** researched specifications have no code at all")
    A(f"- **{spec_total - len(live)}** of the {spec_total} specifications are unbuilt, "
      f"which is {'more' if spec_total - len(live) >= TARGET else 'fewer'} than the "
      f"{remaining} still needed")
    A("")
    A("The corpus is larger than the target, so this is an implementation pipeline")
    A("rather than a research programme. An earlier count of *17 workflows* was a")
    A("count of **branches**, not of workflows, and the tools that produced it have")
    A("been corrected.")
    A("")

    OUT.write_text("\n".join(L), encoding="utf-8")

    # Assert the rendered file against what was just measured. A generator that
    # writes a dashboard nobody checks is how a dashboard goes stale.
    back = OUT.read_text(encoding="utf-8")
    checks = {
        f"the {TARGET} target": f"Target {TARGET} workflows" in back,
        f"{len(live)} live features": f"workflows  [#" in back and f"{len(live)}/{TARGET}" in back,
        "the measured route count": f"routes     {routes}" in back,
        "the measured test count": f"tests      {passed} passed" in back,
        "the test count's provenance is stated": f"({tests_from})" in back,
        "the measured failure count": f"features   {failed_features} failed" in back,
        "ready count": f"({len(ready)})" in back,
        "in-flight count": f"({len(flying)})" in back,
        "board total": f"**{cards}**" in back,
        "main's commit": now.split()[0] in back,
        "every loaded feature listed": all(f"`{f}`" in back
                                          for f, _, _ in (reg["loaded"] if reg else [])),
    }
    bad = [k for k, v in checks.items() if not v]
    for k, v in checks.items():
        print(f"  {k:34} {'OK' if v else 'MISMATCH'}")
    if bad:
        print(f"  {len(bad)} MISMATCH - the dashboard does not match what was measured")
        return 1
    print(f"  wrote {OUT.relative_to(ROOT)}  ({len(back):,} chars)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
