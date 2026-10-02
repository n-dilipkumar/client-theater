"""The workflow board: what is built, what is not, and what each one is worth.

One file, `orchestration/WORKFLOW-BOARD.md`, answering three questions a person
actually asks when they are about to open the next branch:

  1. Which workflows are done, and can I trust that?
  2. Which are not, ordered so I know what to build next?
  3. What is each one worth - critical or supplementary?

Why a generator rather than a hand-maintained list
---------------------------------------------------
`orchestration/STATUS.md` already exists and is already stale: it names ten agent
worktrees that no longer exist and reports `main` at a commit from three merged
PRs ago. Its own header says "GENERATED, do not edit by hand", and the numbers
were right when written - which is exactly the problem. A board that has to be
remembered is a board that is wrong.

So built-state is *measured here*, from `git ls-tree` on `origin/main`, and never
copied from any other file. That is also what
`criticality-decisions.json` demands in its own header:

    Whether a workflow is BUILT is deliberately absent. It is measurable from
    git and from the feature registry, and storing derived state here would
    recreate the stale-dashboard bug that make_status.py exists to prevent.
    Join against the host, do not cache it.

This generator is that join. It adds no new source of truth; it reads three that
already exist and joins them:

  * `origin/main`                  - which tickets have a feature module
  * `criticality-decisions.json`   - the criticality judgment (138 tickets)
  * `wf/WF-*.md`                   - the researched specification per ticket

A workflow is BUILT when and only when its feature module is on `main`, because
that is the same test the plugin host applies - a module that exists only in a
branch is a claim, not a feature.

Usage:
    .venv/Scripts/python orchestration/make_workflow_board.py
    .venv/Scripts/python orchestration/make_workflow_board.py --check
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# Derive the repository from this file rather than naming one, so a generator
# inside a repo can never write its output into a different clone.
ROOT = Path(__file__).resolve().parent.parent

CORPUS = ROOT / "docs" / "research" / "digital-sales-room-workflows"
WFDIR = CORPUS / "wf"
CRITICALITY = CORPUS / "criticality-decisions.json"
CATALOGUE = CORPUS / "catalogue.json"
OUT = ROOT / "orchestration" / "WORKFLOW-BOARD.md"

#: The target is the researched corpus, not a round number. It was 100, which
#: meant the 38 workflows researched beyond it were excluded from "to go" - the
#: queue could be worked to empty and the board would still read incomplete.
#: Derived, so a corpus that grows moves the target with it.
TARGET = len(json.loads((CORPUS / "workflows.json").read_text(encoding="utf-8")))

#: How to invoke this generator, used in the messages it prints.
PY_REL = ".venv/Scripts/python"

FEATURE_RE = re.compile(r"wf[_-]?(\d{3})", re.I)

#: The one line whose value changes on every run by construction.
STAMP_RE = re.compile(r"^Verified against .*$", re.M)


def strip_stamp(text: str) -> str:
    """Drop the generation line so two renders can be compared for equality."""
    return STAMP_RE.sub("", text)


def git(*args: str) -> str:
    p = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    return (p.stdout + p.stderr).strip()


# --------------------------------------------------------------------------- #
# Measurement
# --------------------------------------------------------------------------- #


def built_modules() -> dict[str, str]:
    """Ticket -> feature module filename, for every module on ``origin/main``.

    Tolerant of ``wf004``, ``wf_004`` and ``wf-004`` because a hundred agents
    each picking their own spelling is not a hypothetical.
    """
    found: dict[str, str] = {}
    for line in git(
        "ls-tree", "-r", "--name-only", "origin/main", "backend/dsr/features"
    ).splitlines():
        m = FEATURE_RE.search(line)
        if m:
            found[f"WF-{m.group(1)}"] = Path(line).name
    return found


def frontend_dirs() -> dict[str, str]:
    """Ticket -> frontend folder, for the built workflows that have one."""
    found: dict[str, str] = {}
    for line in git(
        "ls-tree", "-r", "--name-only", "origin/main", "frontend/src/features"
    ).splitlines():
        m = FEATURE_RE.search(line)
        if m:
            found.setdefault(f"WF-{m.group(1)}", line.split("/")[-1])
    return found


def test_files() -> dict[str, str]:
    """Ticket -> its test file, when the workflow shipped one."""
    found: dict[str, str] = {}
    for line in git("ls-tree", "-r", "--name-only", "origin/main", "backend/tests").splitlines():
        m = FEATURE_RE.search(Path(line).stem)
        if m:
            found[f"WF-{m.group(1)}"] = Path(line).name
    return found


def title_of(ticket: str) -> str:
    """The workflow's name, read off its specification file's first heading.

    The heading carries the ticket id as a prefix ("WF-001 - Create a room"), and
    the table already has a column for that, so it is stripped here rather than
    printed twice on every row.
    """
    path = WFDIR / f"{ticket}.md"
    if not path.exists():
        return ""
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("# "):
            heading = line[2:].strip()
            return re.sub(rf"^{re.escape(ticket)}\s*[-—:]\s*", "", heading).strip()
    return ""


def spec_is_complete(ticket: str) -> bool:
    """Does the spec carry every section the corpus requires?

    The same test ``make_status.py`` uses, so the board and the dashboard cannot
    disagree about what "researched" means.
    """
    path = WFDIR / f"{ticket}.md"
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8", errors="replace")
    required = (
        "user_flow",
        "data_flow",
        "data_sources",
        "apis_hit",
        "automations",
        "features_tools",
        "extensibility",
        "evidence",
    )
    return all(re.search(rf"\b{s}\b", text) for s in required)


def load_criticality() -> dict[str, dict]:
    data = json.loads(CRITICALITY.read_text(encoding="utf-8"))
    return {entry["ticket"]: entry for entry in data["decisions"]}


def catalogue_size() -> int:
    return len(json.loads(CATALOGUE.read_text(encoding="utf-8")))


# --------------------------------------------------------------------------- #
# Render
# --------------------------------------------------------------------------- #


def render() -> tuple[str, dict]:
    built = built_modules()
    tests = test_files()
    crit = load_criticality()
    deduped = catalogue_size()

    all_tickets = sorted(set(crit) | set(built))
    done = [t for t in all_tickets if t in built]
    pending = [t for t in all_tickets if t not in built]

    def rank(ticket: str) -> tuple:
        entry = crit.get(ticket, {})
        # critical first, then by ticket number so the order is stable and the
        # sequence of work is reviewable rather than incidental.
        return (0 if entry.get("criticality") == "critical" else 1, ticket)

    done.sort(key=rank)
    pending.sort(key=rank)

    def crit_of(ticket: str) -> str:
        return crit.get(ticket, {}).get("criticality", "—")

    def basis_of(ticket: str) -> str:
        return crit.get(ticket, {}).get("basis", "—")

    head = git("log", "-1", "--format=%h", "origin/main")
    stamp = git("log", "-1", "--format=%cI", "origin/main")
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

    pending_critical = [t for t in pending if crit_of(t) == "critical"]
    pending_supp = [t for t in pending if crit_of(t) != "critical"]

    L: list[str] = []
    A = L.append

    A("# Workflow board")
    A("")
    A("<!-- GENERATED by orchestration/make_workflow_board.py. Do not edit by hand:")
    A("     the built/pending split is measured from origin/main, and a board that has")
    A("     to be remembered is a board that is wrong. -->")
    A("")
    A("**What is built, what is not, and what each one is worth.**")
    A("")
    A("Regenerate with `.venv/Scripts/python orchestration/make_workflow_board.py`.")
    A(f"Verified against `origin/main` at `{head}` ({stamp}); generated {now}.")
    A("")
    A("This is the board to open a branch from. For live route counts, test counts")
    A("and in-flight agent worktrees, see `orchestration/STATUS.md` — that file")
    A("measures the running product, this one measures the workflow corpus.")
    A("")

    # ---- summary ---------------------------------------------------------- #
    A("## Summary")
    A("")
    A("| | Count |")
    A("|---|---:|")
    A(f"| **Built and landed** | **{len(done)}** |")
    A(f"| Pending — critical | {len(pending_critical)} |")
    A(f"| Pending — supplementary | {len(pending_supp)} |")
    A(f"| **Pending total** | **{len(pending)}** |")
    A(f"| Corpus researched | {len(all_tickets)} tickets, {deduped} after dedupe |")
    A(f"| Target | {TARGET} |")
    A("")
    A(
        f"**{len(done)} of {TARGET} built ({100 * len(done) // TARGET}%), {TARGET - len(done)} to go.**"
    )
    A("")
    A(f"The target is the whole corpus: {TARGET} researched workflows, all of")
    A("them to be built. There is no cap and no shortlist to be chosen from -")
    A(f"{len(pending)} remain, and criticality is what decides the order they are")
    A("worked in, not which of them are worth doing.")
    A("")

    # ---- how to read it --------------------------------------------------- #
    A("## How status is decided")
    A("")
    A("A workflow is **built** when and only when its feature module is on")
    A("`origin/main`. That is the same test the plugin host applies — a module")
    A("that exists only in a branch is a claim to verify, not a feature. Nothing")
    A("here is copied from another status file, because a derived number cached")
    A("anywhere else is a number that goes stale.")
    A("")
    A("Criticality is the judgment in")
    A("`docs/research/digital-sales-room-workflows/criticality-decisions.json`:")
    A("**critical** means removing it leaves no usable sales room;")
    A("**supplementary** means the primary loop still closes without it.")
    A("")
    A("| Status | Meaning |")
    A("|---|---|")
    A("| ✅ Built | Feature module on `origin/main`, reachable over HTTP |")
    A("| ⬜ Pending — critical | Specified and judged critical; no code yet |")
    A("| ⬜ Pending — supplementary | Specified; primary loop closes without it |")
    A("")

    # ---- built ------------------------------------------------------------ #
    A("## Built")
    A("")
    A(f"All {len(done)} workflows with a feature module on `origin/main`.")
    A("")
    A("| Workflow | Name | Criticality | Basis | Tests |")
    A("|---|---|---|---|---|")
    for t in done:
        name = title_of(t) or "—"
        tst = f"`{tests[t]}`" if t in tests else "—"
        A(f"| ✅ **{t}** | {name} | {crit_of(t)} | {basis_of(t)} | {tst} |")
    A("")

    # ---- pending ---------------------------------------------------------- #
    A("## Pending")
    A("")
    A(f"{len(pending)} researched workflows with no code on `main`. Ordered")
    A("critical-first, then by ticket number, so the queue is reviewable. Each is")
    A("a candidate for a feature branch or an issue.")
    A("")
    if pending_critical:
        A(f"### Critical ({len(pending_critical)})")
        A("")
        A("Removing any of these leaves no usable sales room.")
        A("")
        A("| Workflow | Name | Basis | Spec |")
        A("|---|---|---|---|")
        for t in pending_critical:
            name = title_of(t) or "—"
            A(
                f"| ⬜ **{t}** | {name} | {basis_of(t)} | "
                f"{'complete' if spec_is_complete(t) else '**incomplete**'} |"
            )
        A("")
    if pending_supp:
        A(f"### Supplementary ({len(pending_supp)})")
        A("")
        A("The primary loop closes without these; they add reach, automation,")
        A("polish or adjacent surface.")
        A("")
        A("| Workflow | Name | Spec |")
        A("|---|---|---|")
        for t in pending_supp:
            name = title_of(t) or "—"
            A(f"| ⬜ {t} | {name} | {'complete' if spec_is_complete(t) else '**incomplete**'} |")
        A("")

    # ---- provenance ------------------------------------------------------- #
    A("## Provenance")
    A("")
    A("| Source | What it contributes |")
    A("|---|---|")
    A("| `origin/main` | built/pending, measured from the feature registry |")
    A("| `criticality-decisions.json` | the criticality judgment and its basis |")
    A("| `wf/WF-*.md` | each workflow's name and whether its spec is complete |")
    A("| `catalogue.json` | the deduped count |")
    A("")
    A("No new source of truth is introduced: built-state is never stored in a")
    A("file, only measured. If the host learns a feature this board does not")
    A("list, that is the bug — regenerate and the two agree.")
    A("")
    A("A spec marked **incomplete** is missing one of the sections the corpus")
    A("requires. It is still buildable, but the brief would be working from a")
    A(" thinner document than the rest.")
    A("")

    return "\n".join(L), {
        "built": len(done),
        "pending": len(pending),
        "pending_critical": len(pending_critical),
        "pending_supp": len(pending_supp),
        "head": head,
        "done": done,
        "pending_all": pending,
    }


def main() -> int:
    check_only = "--check" in sys.argv[1:]
    text, stats = render()

    if check_only:
        if not OUT.exists():
            print(f"  MISSING  {OUT.relative_to(ROOT)} does not exist")
            return 1
        current = OUT.read_text(encoding="utf-8")
        # The generation timestamp differs on every run by construction, so a
        # byte comparison would report STALE for a board that is perfectly
        # current - a check that always fails is a check nobody reads. Compare
        # everything except that one line.
        if strip_stamp(current) == strip_stamp(text):
            print(f"  OK       {OUT.relative_to(ROOT)} matches what is measured now")
            print(
                f"           built {stats['built']}, pending {stats['pending']} "
                f"against origin/main {stats['head']}"
            )
            return 0
        print(f"  STALE    {OUT.relative_to(ROOT)} does not match origin/main")
        print(f"           built now: {stats['built']}  pending now: {stats['pending']}")
        print(f"           regenerate: {PY_REL} orchestration/make_workflow_board.py")
        return 1

    OUT.write_text(text, encoding="utf-8")
    print(f"  wrote {OUT.relative_to(ROOT)}")
    print(
        f"  built {stats['built']}, pending {stats['pending']} "
        f"(critical {stats['pending_critical']}, supplementary {stats['pending_supp']})"
    )
    print(f"  against origin/main {stats['head']}")

    # Assert the rendered file against what was measured. A generator that
    # writes a board nobody checks is how a board goes stale.
    back = OUT.read_text(encoding="utf-8")
    checks = {
        f"{stats['built']} built rows": back.count("| ✅ **WF-") == stats["built"],
        "every pending ticket listed": all(
            f"**{t}**" in back or f"| ⬜ {t} |" in back for t in stats["pending_all"]
        ),
        "summary names its own build count": f"| **Built and landed** | **{stats['built']}** |"
        in back,
        "summary names its pending count": f"| **Pending total** | **{stats['pending']}** |"
        in back,
        "summary names its critical split": f"| Pending — critical | {stats['pending_critical']} |"
        in back,
        "names the commit it measured": stats["head"] in back,
        "no stale hand-edit claim": "Do not edit by hand" in back,
    }
    bad = [k for k, v in checks.items() if not v]
    for k, v in checks.items():
        print(f"  {k:36} {'OK' if v else 'MISMATCH'}")
    if bad:
        print(f"  {len(bad)} MISMATCH — the board does not match what was measured")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
