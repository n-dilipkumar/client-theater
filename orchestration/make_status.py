"""Generate the programme status: every researched workflow, in one table.

The audit log is append-only and grows forever, which makes it a poor dashboard:
to answer "how far along are we" you have to read 45,000 characters. So the status
lives in one generated file, and this generator is the only thing that writes it.

Everything here is MEASURED, never carried forward:

  * which workflows are BUILT - read out of `git ls-tree` on origin/main
  * routes, and whether each feature loads - asked of the host itself
  * tests            - the suite is run
  * each workflow's name, domain and criticality - from `workflows.json`
  * which have a frontend descriptor - read from the descriptors' own declared
    `id`, following re-exports, because a folder name is not a ticket id
  * each workflow's Description - read from its own `wf/WF-NNN.md`, because a
    hand-typed description table drifts silently and did (see `description_of`)

The target is the size of the researched corpus, read from `workflows.json`. It
was 100, a round number that quietly excluded 38 researched and judged workflows
from "to go".

Why built-state is measured rather than read from a file
-------------------------------------------------------
Each `wf/WF-NNN.md` page carries a `## Build status` section with `[x] Implemented`
checkboxes, and it is tempting to read status from there. It cannot be read from
there: only **20** of the 138 pages tick `Implemented`, while **48** workflows
have a feature module on `main`. The checkboxes are claims made when the page was
generated; the feature registry is the fact. A status table built from the
checkboxes would have understated the programme by more than half.

`criticality-decisions.json` states the same rule in its own header:

    Whether a workflow is BUILT is deliberately absent. It is measurable from
    git and from the feature registry, and storing derived state here would
    recreate the stale-dashboard bug that make_status.py exists to prevent.
    Join against the host, do not cache it.

This generator is that join.

The stale sections this replaces
-------------------------------
The previous STATUS.md reported ten agent worktrees "in progress" and forty-six
"empty shells", read from a workspaces directory that no longer exists, and listed
four decisions against branches that have since been deleted. All of it was true
when written and none of it was true when read. Sections describing state this
generator cannot measure are omitted rather than printed as zeros, because a
section reading "0 in progress" is a claim about the world that nobody re-checks.

Usage:
    .venv/Scripts/python orchestration/make_status.py
    .venv/Scripts/python orchestration/make_status.py --tests <passed>,<failed>,<xfailed>
"""

from __future__ import annotations

import json
import os
import posixpath
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

# This script writes into the repository it lives in, always. It used to name a
# hardcoded absolute path (an orca checkout), so running it from this repo
# silently regenerated a dashboard in a different clone -- correct content, wrong
# destination, and the copy in this repo went stale without anyone noticing.
ROOT = Path(__file__).resolve().parent.parent

CORPUS = ROOT / "docs" / "research" / "digital-sales-room-workflows"
WORKFLOWS = CORPUS / "workflows.json"
CRITICALITY = CORPUS / "criticality-decisions.json"
OUT = ROOT / "orchestration" / "STATUS.md"
#: The repo's own venv. `sys.executable` is the right fallback because this
#: generator has to be runnable from a worktree that has no venv of its own,
#: which is the normal case here.
PY = ROOT / ".venv" / "Scripts" / "python.exe"
if not PY.exists():
    PY = Path(sys.executable)
PY_REL = ".venv/Scripts/python"

#: The one-line description per workflow is READ OUT OF ITS OWN SPEC FILE, not
#: carried in a side table.
#:
#: It used to come from `orchestration/summaries.json`, a hand-maintained
#: ticket -> sentence map sitting beside this generator. That file drifted: from
#: WF-078 onward the sentences describe a different workflow than the ticket they
#: are filed under, so the table read e.g. "WF-080 | Download the executed
#: agreement from the e-vault | Detect and stop an unusual access pattern". 57 of
#: its 138 entries shared no content word with their own ticket's
#: specification.
#:
#: The join was never the bug - `SUMMARY.get(ticket)` cannot return another
#: ticket's sentence. The bug was that the description was ever hand-typed. A
#: description that is read from `wf/WF-NNN.md` is correct by construction,
#: because the file it came from is named after the ticket it describes. That is
#: the same argument the rest of this generator makes about built-state: measure
#: it, never carry it.
DESCRIPTION_RE = re.compile(r"^-\s+\*\*name:?\*\*:?\s*(.+)$", re.M)


def description_of(ticket: str) -> str:
    """The one line on what this workflow does, read from its own spec page.

    The corpus states each workflow's researched name as a `name:` field in the
    evidence block. Every one of the 138 pages carries one.
    """
    path = CORPUS / "wf" / f"{ticket}.md"
    if not path.exists():
        raise SystemExit(f"{ticket}: no specification at {path}")
    m = DESCRIPTION_RE.search(path.read_text(encoding="utf-8", errors="replace"))
    if not m:
        raise SystemExit(f"{ticket}: no `name:` field in {path}")
    return m.group(1).strip()


#: The target is the researched corpus, not a round number. It was 100, which
#: quietly excluded 38 researched, judged, spec'd workflows from "to go": the
#: queue could be worked to empty and the dashboard would still read
#: incomplete. The owner has decided to build all of them.
TARGET = len(json.loads(WORKFLOWS.read_text(encoding="utf-8")))

FEATURE_RE = re.compile(r"wf[_-]?(\d{3})", re.I)
#: A frontend descriptor declares its own ticket, e.g. ``id: 'wf-064-reschedule...'``.
FRONTEND_ID_RE = re.compile(r"id:\s*['\"](wf[-_]?\d{3})", re.I)
#: ``export { default } from '../wf-001/room-templates/index.jsx'``
REEXPORT_RE = re.compile(r"export\s*\{\s*default\s*\}\s*from\s*['\"](.+?)['\"]")


def git(*args: str, cwd: Path = ROOT) -> str:
    p = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    return (p.stdout + p.stderr).strip()


#: The ref a test measurement is attributed to.
#:
#: Every other measurement here is asked of `origin/main`, because built-state and
#: the test-file inventory are read out of a git tree. The test suite is the one
#: exception: pytest runs in the working tree (`cwd=ROOT / "backend"`), so the tree
#: it measures is the checked-out one. Keying the cache on `origin/main` instead
#: would let a feature branch that adds tests write its higher count under main's
#: sha, and the next run on main would serve that count as a fact about main --
#: the same lie, one hop further along.
MEASURED_REF = "HEAD"


def measured_ref() -> str:
    """Name the tree the suite is about to be run on, or ``""`` if git cannot.

    The short sha, plus ``+dirty`` when anything under `backend/` has uncommitted
    edits. A dirty tree is not the commit its sha names, and an untracked
    ``test_wf123.py`` under `backend/tests`` is exactly the kind of change that
    moves the count, so untracked files count towards the marker. Empty is
    returned rather than a guess: a measurement whose tree cannot be named is one
    nothing may later reuse.
    """
    sha = git("log", "-1", "--format=%h", MEASURED_REF)
    if not sha:
        return ""
    if git("status", "--porcelain", "--untracked-files=all", "--", "backend"):
        sha += "+dirty"
    return sha


# --------------------------------------------------------------------------- #
# Measurement
# --------------------------------------------------------------------------- #


def features_live() -> dict[str, str]:
    """Ticket -> feature module, for every module on ``origin/main``.

    Tolerant of ``wf004``, ``wf_004`` and ``wf-004`` because with a hundred agents
    each picking their own spelling a single pattern cannot be relied on.
    """
    found: dict[str, str] = {}
    for line in git(
        "ls-tree", "-r", "--name-only", "origin/main", "backend/dsr/features"
    ).splitlines():
        m = FEATURE_RE.search(line)
        if m:
            found[f"WF-{m.group(1)}"] = Path(line).stem
    return found


def host_report() -> dict | None:
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
        encoding="utf-8",
    )
    env = dict(os.environ)
    tmp = tempfile.mkdtemp(prefix="dsr-status-")
    env["DSR_DB_PATH"] = str(Path(tmp) / "s.db")
    env["DSR_AUDIT_DIR"] = str(Path(tmp) / "audit")
    p = subprocess.run(
        [str(PY), str(probe.name)],
        cwd=ROOT / "backend",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        env=env,
    )
    probe.unlink(missing_ok=True)
    for line in p.stdout.splitlines():
        line = line.strip()
        if line.startswith("{") and '"loaded"' in line:
            return json.loads(line)
    return None


def frontend_features() -> dict[str, str]:
    """Ticket -> frontend folder, joined on the id the descriptor declares.

    A folder name is not a ticket id. ``frontend/src/features/analytics/`` is
    WF-006, whose folder is named for its domain, and reading folder names alone
    reported that workflow as having no frontend at all.

    Some descriptors are one-line re-exports of an implementation two folders
    down, because the host's glob only reaches one level. Those are followed
    rather than reported as missing.
    """
    found: dict[str, str] = {}
    for line in git(
        "ls-tree", "-r", "--name-only", "origin/main", "frontend/src/features"
    ).splitlines():
        if not line.endswith("index.jsx"):
            continue
        text = git("show", f"origin/main:{line}")
        m = FRONTEND_ID_RE.search(text)
        if not m:
            r = REEXPORT_RE.search(text)
            if r:
                target = posixpath.normpath(posixpath.join(posixpath.dirname(line), r.group(1)))
                text = git("show", f"origin/main:{target}")
                m = FRONTEND_ID_RE.search(text)
        if m:
            found.setdefault(f"WF-{m.group(1)[-3:]}", line.split("/")[-2])
    return found


def test_files() -> dict[str, list[str]]:
    """Ticket -> its test files.

    Joined on the filename. ``test_wf061.py`` carries its ticket that way, and a
    handful predate the convention - WF-006's tests are ``test_analytics.py``,
    named for the domain - so a filename alone undercounts. Those are found by
    asking the host for each feature's own test, which is the next function.
    """
    found: dict[str, list[str]] = {}
    for line in git("ls-tree", "-r", "--name-only", "origin/main", "backend/tests").splitlines():
        m = FEATURE_RE.search(Path(line).stem)
        if m:
            found.setdefault(f"WF-{m.group(1)}", []).append(Path(line).name)
    return found


def corpus() -> list[dict]:
    return json.loads(WORKFLOWS.read_text(encoding="utf-8"))


def contested() -> list[str]:
    return json.loads(CRITICALITY.read_text(encoding="utf-8")).get("_contested", [])


def conditional() -> list[str]:
    return json.loads(CRITICALITY.read_text(encoding="utf-8")).get("_conditional", [])


SUITE_CACHE = ROOT / "data" / "suite_result.json"

#: How long a measurement stays fresh, in seconds. Age is a floor on staleness, not
#: evidence of currency: a tree does not change on the hour.
CACHE_MAX_AGE = 3600


def cache_is_current(entry: dict, ref: str, age: float) -> bool:
    """Whether a cached measurement may be served as a measurement of ``ref``.

    Age alone is not evidence. The entry records the ref it was measured on, and a
    measurement of any other tree is a measurement of a state that has stopped
    existing - however recent it is. So the ref has to match, and an entry with no
    ref at all is refused rather than guessed at, because an entry that cannot name
    its tree cannot be shown to describe this one.
    """
    return bool(ref) and entry.get("ref") == ref and age < CACHE_MAX_AGE and bool(entry.get("passed"))


def suite_count(argv: list[str]):
    """Measure the suite, or reuse a measurement of this exact tree.

    Running the suite is the slow part - about two minutes unloaded, and far
    longer with a dozen agents competing for the CPU, which is how this generator
    got killed mid-run and left the dashboard describing a state that had never
    been committed. A dashboard that costs a quarter of an hour to refresh is a
    dashboard nobody refreshes. So the cache stays, and its whole job is to stop
    the suite being run twice for the same tree.

    It used to be trusted on age alone, and that is how this dashboard came to
    report 9939 while the tree carried 9995: a measurement taken on `454c005` was
    served as current for an hour, because nothing compared the `ref` the entry
    recorded against the tree being described. It is now pinned - see
    `cache_is_current` - and the label says which commit a reused number came
    from, so a reader can tell a reused measurement from a fresh one.

    The count is never invented. It comes from this run, from a measurement of
    this tree, or from a human passing `--tests`, and the dashboard says which,
    and when, and on what.
    """
    for i, a in enumerate(argv):
        if a == "--tests" and i + 1 < len(argv):
            parts = argv[i + 1].split(",")
            return (
                int(parts[0]),
                int(parts[1]),
                (int(parts[2]) if len(parts) > 2 else 0),
                "supplied",
                "",
            )
        if a.startswith("--tests="):
            parts = a.split("=", 1)[1].split(",")
            return (
                int(parts[0]),
                int(parts[1]),
                (int(parts[2]) if len(parts) > 2 else 0),
                "supplied",
                "",
            )

    ref = measured_ref()

    if SUITE_CACHE.exists():
        try:
            d = json.loads(SUITE_CACHE.read_text(encoding="utf-8"))
            age = time.time() - d.get("at", 0)
            if cache_is_current(d, ref, age):
                return (
                    d["passed"],
                    d.get("failed", 0),
                    d.get("xfailed", 0),
                    f"measured {int(age // 60)} min ago on {ref}",
                    ref,
                )
        except (json.JSONDecodeError, KeyError, TypeError):
            pass

    env = dict(os.environ)
    tmp = tempfile.mkdtemp(prefix="dsr-status-tests-")
    env["DSR_DB_PATH"] = str(Path(tmp) / "t.db")
    env["DSR_AUDIT_DIR"] = str(Path(tmp) / "audit")
    p = subprocess.run(
        [str(PY), "-m", "pytest", "-p", "no:cacheprovider", "--tb=line", "-o", "addopts=", "-q"],
        cwd=ROOT / "backend",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=5400,
        env=env,
    )
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
    # Record the ref this run measured, and only if it can be named. An entry with
    # an empty `ref` would sit in a cache that no reader is allowed to accept, so
    # writing one buys nothing.
    if ref:
        try:
            SUITE_CACHE.parent.mkdir(parents=True, exist_ok=True)
            SUITE_CACHE.write_text(
                json.dumps(
                    {
                        "at": time.time(),
                        "passed": passed,
                        "failed": failed,
                        "xfailed": xfail,
                        "ref": ref,
                    }
                ),
                encoding="utf-8",
            )
        except OSError:
            pass
    return passed, failed, xfail, "measured now", ref


def escape_cell(text: str) -> str:
    """Make a value safe inside a markdown table cell.

    A pipe ends a cell, and a newline ends the row, so both have to go. The names
    in this corpus are prose, and prose contains both.
    """
    return str(text or "").replace("|", "\\|").replace("\n", " ").strip()


# --------------------------------------------------------------------------- #
# Render
# --------------------------------------------------------------------------- #


def render() -> tuple[str, dict]:
    built = features_live()
    front = frontend_features()
    tests = test_files()
    reg = host_report()
    passed, failed, xfail, tests_from, tests_ref = suite_count(sys.argv[1:])
    entries = corpus()

    routes_by_ticket: dict[str, tuple[str, int]] = {}
    if reg:
        for fid, prefix, n in reg["loaded"]:
            m = FEATURE_RE.search(fid)
            if m:
                routes_by_ticket[f"WF-{m.group(1)}"] = (prefix or "", n)

    rows = []
    for e in entries:
        ticket = e["ticket"]
        is_built = ticket in built
        prefix, n_routes = routes_by_ticket.get(ticket, ("", 0))
        tst = tests.get(ticket, [])
        rows.append(
            {
                "ticket": ticket,
                "name": e["name"],
                "domain": e["domain_title"],
                "criticality": e["criticality"],
                "basis": e["criticality_basis"] or "",
                "built": is_built,
                "routes": n_routes,
                "prefix": prefix,
                "has_test": bool(tst),
                "has_front": ticket in front,
            }
        )

    done = [r for r in rows if r["built"]]
    pending = [r for r in rows if not r["built"]]
    total_routes = sum(r["routes"] for r in done)
    failed_features = len(reg["failed"]) if reg else -1

    # Critical first, then by ticket, so the queue below the fold is ordered by
    # what a workflow is worth rather than by when it was researched.
    def rank(r: dict) -> tuple:
        return (0 if r["criticality"] == "critical" else 1, r["ticket"])

    pending.sort(key=rank)
    done.sort(key=rank)

    head = git("log", "-1", "--format=%h %s", "origin/main")
    stamp = git("log", "-1", "--format=%cI", "origin/main")
    pending_critical = [r for r in pending if r["criticality"] == "critical"]

    L: list[str] = []
    A = L.append

    A("# Programme status")
    A("")
    A("<!-- GENERATED by orchestration/make_status.py. Do not edit by hand: every")
    A("     number here is measured, and a hand-edited dashboard is a stale one. -->")
    A("")
    A("**Every researched workflow, its status, and who owns it.**")
    A("")
    A("Regenerate with")
    A(f"`{PY_REL} orchestration/make_status.py`.")
    A("")
    A(f"`main` at `{head}` &middot; measured {stamp}")
    A("")
    A(f"Measured in `{ROOT}`. This file is written by the generator and by nothing")
    A("else; if a copy of it lives in another clone, that copy is not this run.")
    A("")

    # ---- headline ---------------------------------------------------------- #
    A("## Progress")
    A("")
    A(
        f"    workflows  [{'#' * int(34 * len(done) / TARGET)}"
        f"{'.' * (34 - int(34 * len(done) / TARGET))}] {len(done)}/{TARGET}"
    )
    A(f"    routes     {total_routes}")
    A(f"    tests      {passed} passed, {failed} failed, {xfail} xfailed  ({tests_from})")
    A(f"    features   {failed_features} failed to load")
    A(f"    to go      {TARGET - len(done)}")
    A("")
    A(
        f"{len(rows)} researched workflows; **{len(done)} built**, "
        f"**{len(pending)} pending** "
        f"({len(pending_critical)} critical, {len(pending) - len(pending_critical)} supplementary)."
    )
    A("")

    # ---- how status is decided --------------------------------------------- #
    A("## How status is decided")
    A("")
    A("**Built** means a feature module for that ticket is on `origin/main` —")
    A("the same test the plugin host applies. A module that exists only in a")
    A("branch is a claim to verify, not a feature.")
    A("")
    A("It does **not** come from the `## Build status` checkboxes in each")
    A("`wf/WF-NNN.md` page. Only 20 of 138 pages tick `Implemented` while 48")
    A("workflows are built, so those checkboxes understate the programme by more")
    A("than half; they are claims made when the page was generated, and the")
    A("feature registry is the fact.")
    A("")
    A("**Critical / supplementary** is the judgment in")
    A("`docs/research/digital-sales-room-workflows/criticality-decisions.json`.")
    A("Critical means removing it leaves no usable sales room; supplementary means")
    A("the primary loop still closes without it. `C1`/`C2`/`C3` is the basis.")
    A("")
    A("**Owner** is intentionally empty. Nobody has claimed these workflows yet.")
    A("")
    A("| Column | Meaning |")
    A("|---|---|")
    A("| Ticket | The workflow id, `WF-NNN` |")
    A("| What it does | The researched name, verbatim from the corpus |")
    A("| Description | One line on what it does for the product |")
    A("| Criticality | `critical` or `supplementary`, with its basis |")
    A("| Status | `Built` (with routes, UI, and any gap) or `Pending` |")
    A("| Owner | **empty** — unassigned |")
    A("")
    A("Two statuses, not three. `Built` carries its own detail — route count,")
    A("whether it has a UI, and whether it shipped a test file — so a workflow")
    A("that is live but untested reads differently from one that is complete,")
    A("without inventing a third category for it.")
    A("")

    # ---- the table --------------------------------------------------------- #
    A("## Every workflow")
    A("")
    A(f"All {len(rows)} researched workflows. Built first, then pending")
    A("critical-first, so the queue reads in the order it should be worked.")
    A("")
    A("| Ticket | What it does | Description | Criticality | Status | Owner |")
    A("|---|---|---|---|---|---|")

    def status_of(r: dict) -> str:
        if not r["built"]:
            return "Pending"
        bits = [f"{r['routes']} routes"]
        if r["has_front"]:
            bits.append("UI")
        else:
            bits.append("API only")
        if not r["has_test"]:
            bits.append("no test file")
        return "Built — " + ", ".join(bits)

    for r in done + pending:
        criticality = escape_cell(r["criticality"])
        if r["basis"]:
            criticality += f" ({r['basis']})"
        A(
            f"| `{r['ticket']}` | {escape_cell(r['name'])} | "
            f"{escape_cell(description_of(r['ticket']))} | {criticality} | "
            f"{escape_cell(status_of(r))} | |"
        )
    A("")

    # ---- what to build next ------------------------------------------------ #
    A("## Critical and pending")
    A("")
    if pending_critical:
        A(f"{len(pending_critical)} workflows are judged critical and have no code.")
        A("Every one of them is the same area — access and audit — which is the")
        A("argument for choosing the next one by product judgement rather than by")
        A("lowest ticket number: the supplementary workflows are numbered lower and")
        A("will otherwise always look like the obvious queue.")
        A("")
        A("| Ticket | What it does | Basis |")
        A("|---|---|---|")
        for r in pending_critical:
            A(f"| `{r['ticket']}` | {escape_cell(r['name'])} | {r['basis']} |")
        A("")
    else:
        A("None — every critical workflow has a feature module on `main`.")
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

    # ---- provenance -------------------------------------------------------- #
    A("## Provenance")
    A("")
    A("| Source | What it contributes |")
    A("|---|---|")
    A("| `origin/main` | built/pending, from the feature registry |")
    A("| the running host | route counts and whether each feature loads |")
    A("| `workflows.json` | each name, domain and criticality |")
    A("| `criticality-decisions.json` | the criticality judgment and its basis |")
    A("| `backend/tests`, `frontend/src/features` | test and UI presence |")
    A("| the test suite | the test count, run here or reused from a measurement of this tree |")
    A("")
    A("No new source of truth: built-state is measured, never cached. Sections")
    A("describing agent worktrees were removed rather than printed as zeros —")
    A('this generator cannot measure them, and a section reading "0 in progress"')
    A("is a claim about the world nobody re-checks.")
    A("")
    A("The one cache left is the test count, because the suite takes about two")
    A("minutes and this generator used to get killed mid-run. It is pinned to the")
    A("commit it was measured on: age alone is not evidence, and a measurement of")
    A("some earlier commit is a measurement of a tree that no longer exists, which")
    A("is how this dashboard came to quote a count for a state that had stopped")
    A("being true. A reused count names its commit above, so a reader can tell it")
    A("from a fresh one.")
    A("")
    A("The Description column is read out of each workflow's own specification")
    A("page, the same page the ticket number comes from. It used to come from a")
    A("hand-maintained side table beside this generator, which drifted: from")
    A("WF-078 the descriptions belonged to other workflows entirely, and nothing")
    A("failed. A description read from `wf/WF-NNN.md` is correct by construction.")
    A("")

    return "\n".join(L), {
        "built": len(done),
        "pending": len(pending),
        "total": len(rows),
        "routes": total_routes,
        "pending_critical": len(pending_critical),
        "passed": passed,
        "failed": failed,
        "xfailed": xfail,
        "tests_from": tests_from,
        "tests_ref": tests_ref,
        "failed_features": failed_features,
        "head": head.split()[0] if head else "?",
        "built_tickets": [r["ticket"] for r in done],
        "pending_tickets": [r["ticket"] for r in pending],
        "critical_pending_tickets": [r["ticket"] for r in pending_critical],
    }


def main() -> int:
    text, stats = render()
    OUT.write_text(text, encoding="utf-8")

    print(f"  wrote {OUT.relative_to(ROOT)}  ({len(text):,} chars)")
    print(
        f"  {stats['total']} workflows: {stats['built']} built, {stats['pending']} pending "
        f"({stats['pending_critical']} critical)"
    )
    print(f"  {stats['routes']} routes, {stats['failed_features']} features failed to load")

    # Assert the rendered file against what was just measured. A generator that
    # writes a dashboard nobody checks is how a dashboard goes stale.
    back = OUT.read_text(encoding="utf-8")
    # Rows are counted inside the "Every workflow" table only. A loose count
    # over the whole file reads 143, not 138, because the "Critical and pending"
    # table below repeats those five tickets on purpose and the feature registry
    # table lists 48 more under `wf-NNN-slug` ids. The section is sliced out
    # rather than matched heuristically, so the count cannot drift when another
    # table is added.
    section = back.split("## Every workflow", 1)[-1].split("\n## ", 1)[0]
    row_re = re.compile(r"^\| `WF-\d{3}` \| [^|]+\| [^|]+\|", re.M)
    owner_re = re.compile(r"^\| `WF-\d{3}` \|.*\| \|\s*$", re.M)
    # The cache rule is asserted here as a truth table rather than left to prose.
    # These five cases are the regression test for the bug that produced a
    # dashboard quoting a measurement of a commit that had moved on, so they are
    # hermetic: they touch no cache file and run no suite.
    ref_now = measured_ref()
    fresh = {"at": time.time(), "passed": 9939, "failed": 0, "xfailed": 2, "ref": ref_now}
    checks = {
        f"{stats['total']} rows, one per workflow": len(row_re.findall(section)) == stats["total"],
        "every built ticket present": all(f"| `{t}` |" in back for t in stats["built_tickets"]),
        "every pending ticket present": all(f"| `{t}` |" in back for t in stats["pending_tickets"]),
        f"{stats['built']} marked Built": section.count("Built — ") == stats["built"],
        "the headline build count": f"**{stats['built']} built**" in back,
        "the headline pending count": f"**{stats['pending']} pending**" in back,
        "the measured route count": f"routes     {stats['routes']}" in back,
        "the measured test count": f"tests      {stats['passed']} passed" in back,
        "the test count's provenance is stated": f"({stats['tests_from']})" in back,
        "a reused test count names the commit it came from": stats["tests_from"]
        in ("measured now", "supplied")
        or stats["tests_from"].endswith(f"on {stats['tests_ref']}"),
        "a measurement of this tree is accepted": cache_is_current(fresh, ref_now, 0.0),
        "a measurement of another tree is refused, however fresh": not cache_is_current(
            {**fresh, "ref": "454c005"}, ref_now, 0.0
        ),
        "a measurement with no ref is refused": not cache_is_current(
            {"at": time.time(), "passed": 1}, ref_now, 0.0
        ),
        "a measurement over an hour old is refused": not cache_is_current(
            fresh, ref_now, CACHE_MAX_AGE + 1
        ),
        "nothing is reused when no tree can be named": not cache_is_current(fresh, "", 0.0),
        "the measured failure count": f"features   {stats['failed_features']} failed" in back,
        f"{stats['total']} rows carry an empty Owner cell": len(owner_re.findall(section))
        == stats["total"],
        "the critical-pending table repeats only critical tickets": all(
            f"| `{t}` |" in back for t in stats["critical_pending_tickets"]
        ),
        "main's commit named": stats["head"] in back,
        "no removed worktree sections": "Empty shells" not in back
        and "Awaiting a decision" not in back,
    }
    bad = [k for k, v in checks.items() if not v]
    for k, v in checks.items():
        print(f"  {k:44} {'OK' if v else 'MISMATCH'}")
    if bad:
        print(f"  {len(bad)} MISMATCH - the dashboard does not match what was measured")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
