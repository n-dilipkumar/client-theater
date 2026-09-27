"""Generate build-from-spec briefs for researched-but-unbuilt workflows.

The corpus is far larger than the branches suggested. `docs/research/.../wf/`
holds **WF-001 through WF-138**, and 138 of the 139 documents are complete specs
with a median of **five cited primary sources** each. Only WF-013 is different -
it is a design document with a companion research document beside it.

WF-001..WF-017 were built as branches. **WF-018 onward have a finished spec and no
code at all** - no branch, no feature module, no tests. So of the 89 workflows
still needed to reach 100, none needs new research. The research is done; the work
is implementation.

That makes a second kind of brief necessary. Every brief so far has been a PORT:
take a branch that already has an implementation and reshape it onto the plugin
host. There is nothing to port here, so the instruction has to change from "carry
this across" to "build this, and the spec is the specification".

Three things this generator has to get right, and each is a way the previous
briefs could have gone wrong:

1. **The research doc is the spec, and the agent must not redesign it.** The
   ported workflows all carried researched decisions - role delegation rules,
   UTC expiry, bot detection - and a port has no business altering them. The same
   applies here, and it is said explicitly rather than assumed.

2. **The prefix is ticket-derived.** A spec does not declare routes, so a
   collision cannot be predicted the way it could for a port with a known route
   table. `/api/wf-0NN` cannot collide with a feature-shaped prefix by
   construction, and the host's loader refuses a colliding (method, path) anyway
   and reports it, so the failure mode is a reported refusal rather than a
   silent shadow.

3. **Every build status checkbox in the spec is the agent's to tick honestly**,
   including the ones it cannot honestly tick. `Verified in localhost browser` is
   the interesting one: no browser is attached to this session, so the agent must
   report that as not-verified rather than claiming it.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(r"C:\Users\Dilip\orca\projects\client-theater\client-theater")
OUT = ROOT / "orchestration" / "ports"
WFDIR = ROOT / "docs" / "research" / "digital-sales-room-workflows" / "wf"
TEMPLATE = OUT / "WF-013.md"

# The nine sections a spec must carry before an agent can build from it without
# inventing the workflow. Matched on the label, not on `**label:**` - the corpus
# puts the colon outside the bold, and a pattern that requires it inside reports
# every spec as having none of the nine.
REQUIRED = ("user_flow", "data_flow", "data_sources", "apis_hit",
            "automations", "features_tools", "extensibility", "evidence")
URL = re.compile(r"https?://[^\s\)\]\>`\"']+")


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=90).stdout


def live_features():
    live = set()
    for f in git("ls-tree", "-r", "--name-only", "origin/main",
                 "backend/dsr/features").splitlines():
        m = re.search(r"wf(\d{3})", f)
        if m:
            live.add(int(m.group(1)))
    return live


def existing_branches():
    """Tickets that already have a research branch, and so are ports not builds."""
    out = git("branch", "-a", "--list", "feature/WF-*")
    return {int(m.group(1)) for line in out.splitlines()
            if (m := re.search(r"feature/WF-(\d+)", line))}


def spec_of(n):
    p = WFDIR / f"WF-{n:03d}.md"
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else None


def usable(n):
    """A spec is buildable if it carries every section and cites real sources."""
    text = spec_of(n)
    if text is None:
        return False, "no spec file"
    missing = [s for s in REQUIRED if not re.search(rf"\b{s}\b", text)]
    if missing:
        return False, f"missing sections: {', '.join(missing)}"
    if len(set(URL.findall(text))) < 2:
        return False, "fewer than two cited sources"
    return True, ""


def title_of(text):
    m = re.match(r"#\s*(WF-\d+)\s*-\s*(.+)", text)
    return (m.group(1), m.group(2).strip()) if m else (None, None)


def domain_of(text):
    m = re.search(r"\*\*Domain:\*\*\s*(.+)", text)
    return m.group(1).strip().strip("`") if m else "?"


NOTES = """1. **This is a BUILD, not a port.** There is no source branch: this workflow
   has a finished research document and no code at all. The research document IS
   the specification. Implement what it says. Do not treat it as a starting point
   to be improved on.

2. **The researched decisions are the product.** The eleven ports in this programme
   all carried researched behaviour across unchanged - role delegation rules, UTC
   expiry boundaries, bot detection from request headers, the exact vocabulary of
   a policy tier. A build has more licence to be creative than a port does, and
   that is exactly why this note exists: if the research says a form submission
   triggers a routing rule that must end in a catch-all, build that, because a
   rule that does not fall through is a bug someone will hit in production. If
   the research is ambiguous, implement the reading it supports best and say in
   your report which reading you took and why. Do not invent a requirement it does
   not mention, and do not quietly drop one it does.

3. **Your prefix is `/api/wf-{ticket}`**, and your feature id is
   `wf-{ticket}-<slug>` where the slug names what the workflow does. A spec does
   not declare its routes, so a prefix collision cannot be predicted the way it
   could for a port with a known route table; a ticket-derived prefix cannot
   collide with a feature-shaped one by construction. The host's loader refuses a
   colliding (method, path) and REPORTS it rather than shadowing, so a mistake here
   shows up as a reported failure rather than a quietly broken route. Keep
   room-scoped paths room-scoped: `GET /api/wf-{ticket}/rooms/<room_id>/...`.

4. **Your module is `backend/dsr/features/wfNNN_<slug>.py`** exporting
   `FEATURE`, `router`, and `EXCEPTION_HANDLERS` if the workflow has domain errors.
   The frontend is `frontend/src/features/wf-{ticket}-<slug>/index.jsx`, a
   default-export descriptor `{{ id, label, icon, Component }}`. Read
   `docs/FEATURE-CONTRACT.md` and follow its checklist at the end.

5. **Every read and write goes through `StoreDep` / `RecordStore`**, never a
   SQLite connection. The audit row is written in the same transaction as the
   change, and that is the guarantee the product is built on. **Pass `source=`
   from the HTTP layer** for every write, built from `router.prefix`. The branch
   history is full of features whose audit log named a route the app had stopped
   serving, and the contract names that defect by name.

6. **No migration and no typed column.** Record payloads are arbitrary JSON in
   `records.data`. A team adding a field must not need coordination. Filter with
   `find()` / `?where=...`, which resolves dotted JSON paths through the dynamic
   index. The only fixed vocabulary is the envelope.

7. **Demo data belongs in `seed(db, context)` in your feature module**, not in
   `backend/seed.py`, which is shared. **Seed the interesting states, not just the
   happy path** - the features already on main seed a declined room, an archived
   document, a pending approval, a retried delivery and a failed one, because demo
   data containing only success teaches a reviewer nothing.

8. **Tick the spec's build-status checkboxes honestly.** In particular
   `Verified in localhost browser` - **no browser is attached to this session**,
   so you cannot tick it. Verify over HTTP against a running server, and report
   what you did. A checkbox ticked without the work behind it is worse than an
   unticked one.

9. **Tests are not optional.** A feature without tests is not finished. Cover the
   domain rules the research specifies, the HTTP surface through your own router,
   and the audit-source rule. Aim for the density of the ports already merged -
   they range from 57 to 200 tests each, and the largest was the one with the most
   researched rules."""


def build_brief(n):
    text = spec_of(n)
    ticket, title = title_of(text)
    domain = domain_of(text)
    sources = sorted(set(URL.findall(text)))
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:38]
    body = f"""# Build brief: {ticket} - {title}

<!--
Generated by orchestration/make_build_briefs.py from
docs/research/digital-sales-room-workflows/wf/{ticket}.md.

Committed on purpose: this is the exact brief the agent works from, so a reviewer
can compare what was asked against what was delivered, rather than reconstructing
the instruction from a chat message.
-->

## The specification

    docs/research/digital-sales-room-workflows/wf/{ticket}.md

**Domain:** {domain}

**Read it first, in full, before writing anything.** It carries the user flow,
the data flow, the data sources, the APIs hit, the automations, the features and
tools in play, the extensibility, and quoted evidence for each. Those are the
workflow's decisions, made and sourced. Your job is to land them, not to revisit
them.

**The {len(sources)} sources it cites:**

{chr(10).join(f'    {s}' for s in sources)}

---

## Notes for this specific workflow

{NOTES.replace("{ticket}", ticket[3:])}

### What to produce

| | |
|---|---|
| Feature module | `backend/dsr/features/wf{ticket[3:]}_{slug.replace('-', '_')}.py` |
| Router prefix | `/api/wf-{ticket[3:]}` |
| Feature id | `wf-{ticket[3:]}-{slug}` |
| Frontend folder | `frontend/src/features/wf-{ticket[3:]}-{slug}/` |
| Research spec | `docs/research/digital-sales-room-workflows/wf/{ticket}.md` |

---

"""
    return body + TAIL


TAIL = """You are building one researched workflow of the Digital Sales Room. Read these
two files before writing anything, in this order:

  AGENTS.md                    the repo-wide agent contract
  docs/FEATURE-CONTRACT.md     the feature contract, and the checklist at the end

The short version, so you know why the rules exist:

This product's workflows were originally built as 12 separate git branches, and
every one of them appended routes to the single FastAPI `app` in
`backend/dsr/api.py`, appended an entry to the hard-coded `ROUTES` array in
`frontend/src/App.jsx`, and appended methods to the shared api object. All 12
edited the same three files, so all 12 conflicted and none ever merged. The
plugin host now exists to make that impossible: a feature is a new backend module
plus a new frontend folder, discovered automatically, and it registers by adding
files and never by editing shared files.

Eleven features already work this way, with **116 routes and 1211 tests between
them, merging with zero shared-file conflicts** - including three features
sharing the `/api/library` prefix and two sharing `/api/publishing`, all loading
together. You are the twelfth.

## Your environment, precisely

You are in a **fresh git worktree**. Two things follow, and getting them wrong
will cost you the whole task:

1. **There is no `.venv` in your worktree.** `.venv` is gitignored, so a new
   checkout does not have one. Do not go looking for one, and do not read a
   sibling worktree to find it - that raises a permission prompt and, if it is
   declined, your turn is lost. Use the main checkout's interpreter by absolute
   path:

       C:/Users/Dilip/orca/projects/client-theater/client-theater/.venv/Scripts/python.exe

   So the commands are, from your worktree root:

       cd backend && "C:/Users/Dilip/orca/projects/client-theater/client-theater/.venv/Scripts/python.exe" -m pytest -q
       "C:/Users/Dilip/orca/projects/client-theater/client-theater/.venv/Scripts/python.exe" tools/check_feature_diff.py --base origin/main

2. **Stay inside your worktree.** Never read another worktree, the parent
   `workspaces/` directory, or a drive root. **Do not use the system temp folder as
   a scratch directory** - an agent that did that this session was blocked on a
   permission dialog for %TEMP%, and answering it was the only way to unblock it.
   Put scratch files inside your worktree, under a path like `.scratch/`, and read
   a tracked file with `git show <ref>:<path>` from your own worktree. If a
   permission dialog appears, reject it and carry on.

## Hard rules

1. **Edit no shared file.** These are the files that stall every other branch:
   - `backend/dsr/api.py`
   - `backend/dsr/deps.py`
   - `backend/dsr/store.py`
   - `backend/dsr/db/audited.py`
   - `backend/seed.py`
   - `frontend/src/App.jsx`
   - `frontend/src/main.jsx`
   - `frontend/src/lib/api.js`
   - `frontend/src/lib/features.js`
   - `frontend/src/components/ui.jsx`
   - `frontend/vite.config.js`
   CI **fails** any pull request that touches one of them. Run the guard yourself
   before you commit: it must print `OK: N changed file(s), none shared`.

2. **Import dependencies from `dsr.deps`, never from `dsr.api`.** A test on main
   asserts that no feature module imports the app, and it will catch you.

3. **All reads and writes go through `StoreDep` / `RecordStore`.** Never open the
   SQLite file. The audit row is written in the same transaction as the change,
   and that is the guarantee the whole product is built on.

4. **Pass `source=` from the HTTP layer.** If a domain function hardcodes a URL
   string as the `source` of a write, that is a defect. The audit row must name
   the route that actually served the write. This exact bug has shipped before: a
   feature's audit log kept recording a path the app had stopped serving. Add a
   `source` parameter and pass `f"{router.prefix}/..."` from the route, and add a
   test that every source you record matches a route the host actually mounted.

5. **Schema-flexible.** No migration, no typed column, no new required field.

6. **Demo data in `seed(db, context)`** in your feature module.

## Steps

1. Read the specification document in full, then `AGENTS.md`, then
   `docs/FEATURE-CONTRACT.md`.
2. Write the domain logic. Keep it in your own modules under `backend/dsr/`, named
   for this workflow, so no two features claim one path.
3. Write the router, with `EXCEPTION_HANDLERS` for your own domain error types.
4. Write the tests. Cover the researched rules, the HTTP surface through your own
   router, and the audit-source rule.
5. Write `seed(db, context)`, seeding the states the research says matter.
6. Write the frontend page and its `index.jsx` descriptor.
7. Run the suite: `cd backend && <absolute python> -m pytest -q`. All green.
8. Run the guard: `<absolute python> tools/check_feature_diff.py --base origin/main`.
9. Build the frontend: `cd frontend && npm run build`.
10. Seed and run the app, then check your feature over HTTP:
    `<absolute python> -m uvicorn dsr.api:app --app-dir backend --host 127.0.0.1 --port 8000`
    and confirm `/api/features` lists your feature with its routes and that
    `failed_count` is 0.
11. Tick the build-status checkboxes in the spec honestly. You cannot tick
    `Verified in localhost browser` - no browser is attached to this session.
12. Commit locally. **Do not push, do not open a pull request, do not merge.** A
    human reviews your diff, pushes, and opens the PR. If a permission dialog
    appears, reject it and carry on.

## Your report

End your turn with a report a reviewer can act on without re-reading everything:

- **What you built**, and which researched rules it implements.
- **Measured results**: the suite count before and after, the guard's verbatim
  output, the frontend build line, and what `/api/features` reported.
- **Decisions the research left open**, which reading you took, and why.
  Be specific - this is the part a reviewer most needs and least often gets.
- **What you deliberately did not build**, and why.
- **Build status**, per checkbox, with `Verified in localhost browser` reported
  honestly as not verified.
- **Anything you would not want a reviewer to discover by reading the diff.**
"""


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    live = live_features()
    branches = existing_branches()
    print(f"  live features : {len(live)}  {sorted(f'WF-{n:03d}' for n in live)}")
    print(f"  have branches : {len(branches)}  {sorted(f'WF-{n:03d}' for n in branches)}")
    print()

    buildable, blocked = [], []
    for n in range(1, 139):
        if n in live:
            continue
        if n in branches:
            continue  # a port, not a build
        ok, why = usable(n)
        (buildable if ok else blocked).append((n, why))

    print(f"  BUILD from spec (no branch, complete spec) : {len(buildable)}")
    print(f"  blocked                                        : {len(blocked)}")
    for n, why in blocked:
        print(f"    WF-{n:03d}  {why}")

    written = []
    for n, _ in buildable:
        ticket, title = title_of(spec_of(n))
        path = OUT / f"{ticket}.md"
        path.write_text(build_brief(n), encoding="utf-8")
        written.append(ticket)
        print(f"  wrote orchestration\\ports\\{ticket}.md")

    manifest = {
        "generated": "by orchestration/make_build_briefs.py",
        "live_before_generation": sorted(f"WF-{n:03d}" for n in live),
        "ports_not_generated_here": sorted(f"WF-{n:03d}" for n in branches),
        "buildable_count": len(buildable),
        "blocked": {f"WF-{n:03d}": why for n, why in blocked},
        "written": written,
    }
    (OUT / "BUILD-BRIEFS.json").write_text(json.dumps(manifest, indent=2),
                                           encoding="utf-8")
    print()
    print(f"  {len(written)} build briefs written, manifest in BUILD-BRIEFS.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
