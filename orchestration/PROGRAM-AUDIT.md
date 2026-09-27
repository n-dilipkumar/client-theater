# Program audit

A running record of key steps for the Digital Sales Room build. Append-only in
spirit: entries are added, not rewritten, so the trail shows how the project's
state was actually arrived at rather than a tidy after-the-fact summary.

Scope note: this file records *program* decisions and milestones. Two other
records exist and are not duplicated here:

* `orchestration/decisions/jev-audit.jsonl` — the append-only JSONL log of every
  typed Jev judgment. Written by `tools/jev.py`. Never hand-edited.
* `git log` — what changed in the code, and why, in the commit message.

---

## 2026-09-26 — Program start: infrastructure before scale

**Starting state, measured rather than assumed:**

| Fact | Value | How established |
|---|---|---|
| Researched workflows | 17 (WF-001…WF-017) | `docs/research/digital-sales-room-workflows/wf/` |
| Raw research documents | 9 | `docs/research/raw/` |
| Features ported and merged | 1 (WF-006) | `git log` — `f854a79` |
| Backend tests | 145 passing (was 81) | `pytest -q` |
| Headless localhost checks | 22 passing | `tools/verify_localhost.py` |
| Orca worktrees | 22, **all** claiming `in-progress` | `orca worktree list` |
| Worktrees with a live agent | **0** | `orca worktree ps` — every one `live:0 pty:no` |
| Branches with unported code | 11 | `git rev-list --count origin/main..<branch>` |
| Branches empty (never built) | 6 | same, `ahead=0 files=0` |
| `n-dilipkumar/*` branches | 21, all empty and already merged | same |
| CI / `.github/` | **did not exist** | `dir .github` → not found |
| Open PRs | 0 | `gh pr list` |

**Scope decision.** The brief asks for 10 sets of 10 workflows (100). Only 17 are
researched, and the project's own standard in `AGENTS.md` separates a *sourced*
workflow from a *hypothesis*. Resolved with the user: **port the 16 researched
workflows first, then research sets 2–10 as new batches** with the same
primary-source discipline. Generating 83 unsourced workflows up front would have
been faster and would have violated the corpus's own provenance rule.

**Branching inconsistency found and fixed.** The Orca repo base ref was
normalised to `origin/main`. Worktrees from the failed run were split between base
`main` and `refs/remotes/origin/main`, so a new agent could branch from a local
`main` that had not been pushed and silently start nine commits behind — the
exact class of drift that makes a batch look broken.

---

## 2026-09-26 — CI: the feature contract is now enforced, not remembered

The plugin host lets N features merge without conflict, but only while no feature
edits a shared file. Until now that was a rule in a markdown file, checked by
whoever remembered to run `tools/check_feature_diff.py`. WF-006 proved the rule is
followable; nothing proved it would be *followed*. At 100 workflows, an unenforced
convention is a hope.

**PR #1 — `420ba54` — merged.** Adds `.github/workflows/ci.yml` (4 jobs) and
`.github/PULL_REQUEST_TEMPLATE.md`.

| Job | Runs on | Catches |
|---|---|---|
| `guard` | pull_request only | A feature branch editing a shared file |
| `backend` | all | The full pytest suite |
| `frontend` | all | A feature page that does not compile |
| `end-to-end` | all | A plugin that failed to import |

Two asymmetries, both deliberate:

* **The guard skips `main`.** Platform work legitimately edits shared files —
  that is how a new extension point gets added (`44555cd`, the seed hook). A guard
  that must be overridden on every legitimate platform PR trains people to
  override it on illegitimate ones. Tests have no such escape hatch, because the
  audit guarantee is the product.
* **`guard` uses `fetch-depth: 0`.** The guard diffs against `origin/main`; a
  shallow clone of the PR branch alone would make it **silently pass**. A guard
  that passes when it cannot see is worse than no guard.

**PR #2 — probe, closed and deleted.** A guard that has only ever passed is
untested. A one-line edit to `backend/dsr/api.py` was pushed deliberately.

| Job | Result |
|---|---|
| `Feature contract (no shared files)` | **fail** (7s) |
| `Backend tests` | pass |
| `Frontend build` | pass |
| `Local host app (headless)` | pass |

This is the discrimination that matters. A shared-file edit compiles, passes every
test, and builds — so nothing except the guard would have caught it. CI log
confirmed the right reason: `FAIL: 1 shared file(s) edited … - backend/dsr/api.py`,
with the failure message pointing the author at the two files to add instead.

**Evidence for the CI file itself.** A green run on the PR that introduces it is
not proof it is correct, only that it did not break. So before committing: the YAML
was parsed and all four jobs and their steps resolved; and `pip install -e
'backend[dev]'` was run in a clean venv with `dsr` importing from a neutral working
directory, because that one line gates three of the four jobs.

---

## Board state to correct

22 worktrees, 0 with a live agent, all reporting `in-progress`. A board where every
card claims active work reports nothing. Correction driven by measured branch
state, with Jev consulted on the status policy rather than applying it by feel.

---

## 2026-09-26 — Five workflows were never empty

Jev was asked for the board-status policy and returned **`uncertain`** (0.52
confidence, 0.46 margin between options). `AGENTS.md` says an `uncertain` verdict
must not be overridden — gather more evidence or escalate. It was not overridden.

The evidence Jev was missing: whether removing a "dead" worktree would actually
destroy anything. A branch can be empty *at the ref level* while its *working
tree* holds real work, and `git rev-list origin/main..<branch>` — the only triage
that had been done — cannot see that.

It checked, and the answer reversed the plan:

**`orchestration/PORT-PLAN.md` records WF-001, WF-004, WF-012, WF-015 and WF-017 as
"no commits — re-run from scratch".** Their refs are empty. Their directories are
not:

| Workflow | Modified | Untracked | Ref commits |
|---|---|---|---|
| WF-001 | 9 | 3 | 0 |
| WF-004 | 8 | 11 | 0 |
| WF-012 | 6 | 3 | 0 |
| WF-015 | 7 | 9 | 0 |
| WF-017 | 10 | 7 | 0 |
| **Total** | **40** | **33** | **0** |

Roughly **73 files that had never been committed and had never been pushed**,
existing in exactly one place on one machine. A `git checkout`, a rebase, or a
worktree cleanup would have destroyed all of it silently. One of the options put
to Jev proposed removing worktrees believed to be empty and merged; had that been
adopted on the evidence available at the time, it would have destroyed five
workflows' worth of work.

**Rescued.** Each was committed to its own feature branch with a message stating
what was recovered and that the ref-level triage was misleading, then pushed to
`origin`. They are recoverable off-machine now.

They remain **unported and untrusted**. Every one edits shared files — `api.py`,
`seed.py`, `ui.jsx`, `lib/api.js`, `App.jsx`, and in WF-001's case
`db/audited.py` — which is the exact pattern that stalled the previous run. WF-004
also modified `tools/jev.py`, the shared validator every decision in this project
goes through; that needs reading before any of it is trusted.

**The generalisable defect:** a ref-level metric was used to conclude a working
directory was empty. For a branch-based triage that is a reasonable proxy. For
uncommitted work it is blind, and the failure is silent. Any future triage of this
project must check `git status --porcelain` in the worktree, not only
`git rev-list`.

---

## 2026-09-26 — Dispatch set 1: three agents, three environment bugs

The mechanics were established by probing rather than assumed, and two of the
three probes found something that changed the design.

**`orca worktree create --agent opencode` does not work.** It fails with *"Selected
agent is disabled"* — only `pi` is enabled in Orca's settings, and that is a
desktop-app toggle with no CLI surface. The working path is
`orca terminal create --worktree <id> --command opencode`, which launches the real
TUI in a real tab; `terminal show` then reports `agentIdentity: opencode` and the
status bar reads **Build · Space Bunny Free**. So the agent is a genuine OpenCode
agent on the model `AGENTS.md` requires, in a tab, with no settings change needed.
Verified end to end before relying on it: a probe agent was sent a prompt and
answered `TAB-AGENT-OK`.

**`terminal read --json` returns the screen under `result.terminal.tail`** as an
array of lines, not `result.text`. Reading the wrong key returns empty and looks
like a dead agent — which briefly read as a broken dispatch.

**A server restart interrupted the dispatch mid-run**, after the worktrees and
agent tabs were created but before the run finished. Reading each screen showed
WF-003 and WF-012 had received their briefs and were working, while WF-002 sat at
an empty prompt. Resuming meant sending WF-002's brief, not re-dispatching —
re-dispatching would have left two agents already working on a third that was not
needed. The handle map is now written to `data/dispatched.json` before the sends
rather than after, so an interruption cannot lose it.

### Two of three agents then stalled on the same thing

WF-002 spent **four minutes** hunting for a virtualenv, walked into a *sibling
worktree* to look for one, and raised an external-directory permission prompt.
WF-003 climbed to `D:/` and hit the same wall. Declining the prompt declined the
tool call and **ended the turn**, so both had done nothing.

The cause is structural, not a one-off, and it would have hit roughly one agent in
three across the whole programme:

1. **A worktree has no `.venv`**, because `.venv` is gitignored. `AGENTS.md` and
   the port briefs both said `cd backend && ../.venv/Scripts/python -m pytest`,
   which does not resolve in a worktree. The briefs now carry the absolute
   interpreter path, and say plainly that no sibling worktree may be read to find
   a venv — the negative instruction matters as much as the positive one, because
   the wandering is what raised the prompt.
2. **`opencode.json` used V1 syntax in a V2 config.** It had `permission` with a
   `bash` sub-object; V2 wants a `permissions` array of `{action, resource,
   effect}` with actions like `shell`, and explicitly warns that V1's
   `permission`/`bash`/`task` are not the V2 names. The intended policy — allow
   `git push` but ask, ask on `gh pr merge` — **was not in force at all**.
   Rewritten as V2 rules plus the missing piece: an `external_directory` allow for
   this repo's two directories. That action is what OpenCode checks before any read
   outside the active worktree, and it defaults to `ask`, which is exactly why
   every agent that strayed got a modal prompt nobody could answer for it.

Limitation recorded honestly: the config fix only reaches sessions started after
it merged, so the three running agents were redirected by hand.

---

## 2026-09-26 — The four "collisions" were not collisions

`PORT-PLAN.md` blocked WF-007, WF-010, WF-009 and WF-011 on the claim that
*"WF-007 and WF-010 genuinely both own /api/library, and WF-009 and WF-011 both
define a publishing router — the host will refuse the second regardless."*

Measured against the host's actual rule, which refuses on `(method, path)` **after
prefixing**:

| Pair | Routes each | Identical `(method, path)` | Overlapping paths |
|---|---|---|---|
| WF-007 vs WF-010 | 11 / 0 | **0** | **0** |
| WF-009 vs WF-011 | 13 / 10 | **0** | **0** |

WF-007 owns `/api/library/rooms/{id}/documents` and `/documents/{id}`. WF-010 owns
`/api/library/contract`, `/fields`, `/search`, `/assemble`, `/searches`. WF-009
owns `/api/publishing/processes`, `/submissions`, `/workflows`, `/publish`,
`/publications`. WF-011 owns `/api/publishing/rooms`, `/status`, `/share-link`,
`/access`, `/events`, `/webhooks`.

The claim came from comparing **prefixes** — the exact check `fd544e2` replaced
with route-level comparison, because prefix comparison both wrongly blocked
WF-003/WF-005 and missed genuine dead-code shadowing of core routes. Judging these
pairs by prefix repeated the mistake that fix exists to stop repeating.

Jev was asked whether they were duplicate workflows and answered
**`port_all_four_as_is` at 0.79**. Their shared filename
`backend/dsr/publishing.py` also stops being a conflict: each becomes its own module
under `backend/dsr/features/`. Both features keep the `/api/library` and
`/api/publishing` prefixes, which is safe precisely *because* their concrete paths
differ — the case the host was built for.

**Still held back: WF-005**, whose branch edits `db/audited.py` and `store.py`, the
audit guarantee itself. That needs a human read before anything carries it across,
and an agent port is the wrong instrument for it.

---

## 2026-09-27 — Three agent-dispatched ports merged: the host works

The result the whole plugin host was built to produce, and the first time it has
been demonstrated with features written by independent agents in parallel.

Three workflows, each researched and built on its own branch by its own agent in
its own worktree, **none of which had ever seen the others' code**. They merged
with **zero shared-file conflicts**, one at a time, with the full suite and the
feature registry re-checked after each merge so a red result would name its cause:

| Step | Suite | Features live | Failed |
|---|---|---|---|
| baseline | 145 | 1 (WF-006) | 0 |
| + WF-012 | 217 | 2 | 0 |
| + WF-002 | 274 | 3 | 0 |
| + WF-003 | **375** | **4** | **0** |

44 routes across four features. The previous run of this project built twelve such
branches and merged **none**.

### One real conflict, and the fix that generalises

The only conflict in three merges was not application code. Every agent runs a Jev
gate, and the Jev client appends a row to
`orchestration/decisions/jev-audit.jsonl` as a side effect. Three branches
appending to the same file conflict textually even though no row contradicts
another.

This is not a one-off: **every one of the ~100 workflows runs a Jev gate**, so
every one of them appends there. Left alone, the audit log alone would have
conflicted on almost every merge — the exact class of problem the plugin host
exists to remove, reappearing in a file nobody had thought of as contended. The
guard missed it because no feature is *supposed* to edit that file by hand; the
tool writes it as a side effect of doing its job.

Fixed with `merge=union` in `.gitattributes` (`27ebd24`). An append-only log has
no notion of a conflicting line, only of rows that were never merged. `union`
concatenates both sides, loses no row, invents none, and each row carries its own
`audit_id` and `decided_at` so the merged file stays a complete record whatever
order they land in.

### Two tooling defects worth recording

**The agent monitor reported a stopped agent as working** — twice, for an agent
that had been idle holding a finished port. The cause was ordering: an ended
OpenCode turn leaves `… · interrupted` in the scrollback while the status bar
*underneath* still shows the spinner strip, and the spinner test ran first. The
spinner glyphs are the status bar, not evidence of activity. Found because the tool
disagreed with a direct screen read, which is the argument for having both a
summary tool and the ability to read the raw thing it summarises.

**`merge_ports.py` began with `git checkout main`**, so work authored on a PR
branch was silently moved onto `main` and the branch was left empty. The merges
kept *succeeding* — three clean merges, no conflicts — and only the push reported
`Everything up-to-date` while local was seven commits ahead. A reflog showed the
branch had been created once and never moved. Three attempts to fix it with the
edit tool failed silently because the shell restarted and reverted the working
copy; each attempt presented identically, so nothing distinguished "not yet tried"
from "tried and lost". The repair is now written by a script that reads the file
back and refuses to claim success unless the line is actually gone — the artifact
is the evidence, not the intention.

### What the agents got right

Each worked from its committed brief and each produced a feature module, a feature
folder, and a test file. No exceptions, and none touched a shared file. Notably
each threaded `source=` through its own domain layer so audit rows name the route
that actually served the write — the defect the WF-006 rename exposed, which would
otherwise have been replicated three more times.

### Verification, before merge rather than after

Each port was checked by running the checks, not by reading the agent's claim — an
agent reporting "246 passed" is asserting a number. Suite in each agent's own
worktree with the feature mounted (202 / 246 / 217), the host registry queried
through `/api/features` to confirm the feature *loads* rather than being silently
skipped, and the guard run per branch.

Merged as **PR #10 → `8920d2f`**, all four CI jobs green, 375 tests, 57 frontend
modules, all 22 headless checks passing with four features mounted.

---

## 2026-09-27 — Set 2 dispatched; a backtick in a prompt ate two briefs

WF-007, WF-009, WF-010 and WF-011 dispatched — the four held back as collisions
until measurement showed 0 identical `(method, path)` pairs and Jev answered
`port_all_four_as_is` at 0.79.

### The defect

The pointer prompt read:

    Read `orchestration/ports/WF-007.md` in this repo and carry out ...

An Orca terminal on Windows is a **cmd.exe pty**, and backticks are **command
substitution** there. cmd tried to *execute* the word "Read", and the prompt
arrived mangled. Two agents were affected:

- **WF-010** sat idle at an empty prompt, having never received a brief
- **WF-007** was gone entirely, its pty not having survived

Neither failure is visible from git, from the worktree, or from the dispatch
script's exit code. Only from **reading the agent's screen**. Set 1's pointer had
the identical hazard and happened to survive — nothing about the two cases differed
in a way that had been checked, so that was luck being mistaken for a working
design.

While writing the PR that documents this, the same failure reproduced in my own
tooling: the first attempt to pass the PR body inline had backticks in it and
`gh` rejected the whole argument as unquoted fragments. The `--body-file` form is
the fix, which is a fitting way to ship a note about text a shell rewrites.

`repair_set2.py` re-delivers briefs with no backticks **and no other character cmd
treats specially**, asserts that before sending, and reads the screen back
afterwards. It does not trust the receipt: `accepted` came back **empty on a
successful send**, so it is not evidence of anything on its own.

### State

All four live and working, **none touching a shared file**. This is the first
dispatch where two of the four deliberately share an API prefix — WF-007 and
WF-010 under `/api/library`, WF-009 and WF-011 under `/api/publishing`. Safe
precisely because their concrete paths differ: the case the host exists to allow,
and the case `PORT-PLAN.md` wrongly flagged as a collision.

Board corrected to match: 4 `in-progress` with live agents, 8 `completed`, 16
`todo`, zero stale cards.

---

## 2026-09-27 — Set 2 merged; a brief of mine broke a feature

**`fb30903`. Seven features, 81 routes, 604 tests, 0 failed.**

| Workflow | Prefix | Routes | Suite in its worktree |
|---|---|---|---|
| WF-007 | `/api/library` | 12 | 437 passed |
| WF-009 | `/api/publishing` | 13 | 445 passed |
| WF-011 | `/api/publishing` | 10 | 472 passed |

All three merged cleanly, one at a time, with the suite and registry re-checked
after each. **WF-009 and WF-011 both mount `/api/publishing` and both load
together** — the case `PORT-PLAN.md` blocked as "researched twice" on a *prefix*
comparison. Measured on the rule the host enforces, `(method, path)` after
prefixing, there are **0 colliding pairs**. The claim had come from repeating the
exact mistake `fd544e2` fixed.

### The defect was my brief, not the agent

WF-007 **could not load at all**:

```
RuntimeError: WF-007 needs the 'python-multipart' package to serve its
multipart ingest endpoint
```

51 of its tests failed with it. My WF-007 brief had said: *"Leave
`backend/pyproject.toml` alone... not four agents each adding their own."*
The agent obeyed. But `pyproject.toml` is **not** in the guard's shared list, so
the feature was always allowed to edit it, and FastAPI needs `python-multipart`
to parse a file upload — which is precisely what document ingest is.

WF-007's **original branch** had already declared it, commented *"Required by
FastAPI to parse the multipart ingest request (WF-007)."* The port reversed a
decision the original author had made correctly, because I told it to. Fixed in
`580dca6`, and the brief template corrected to say that a dependency a feature
cannot work without is that feature's own requirement, not a coordination
problem.

**Blast radius worth knowing:** while WF-007 was unloaded,
`test_the_feature_did_not_collide_with_anything` in **WF-003's** suite also
failed — a different feature with nothing to do with uploads. It passed again once
WF-007 loaded. So *"the suite is green"* is a statement about which features
loaded at least as much as about the code, and a **silently skipped feature is
not a green run**.

### A false positive worth recording

The set-3 dispatch reported `mangled=True` for WF-016. Reading the screen showed
the agent had itself piped pytest into `| tail -n 20` — a Unix idiom unavailable in
cmd — and had recovered unaided. The detector matched any *"not recognized as an
internal or external command"*, which fires on an agent's own typo as well as on a
mangled prompt. Three tools narrowed to match only the word that starts the
prompt. A check that cries wolf is worse than no check, because it trains you to
ignore it.

### A mistake of my own

Closing the finished set-2 tabs, I read the handle map for the wrong batch and
closed the **set-3** agents instead. No work was lost — their worktrees were
intact, WF-016 still holding its 10 uncommitted files — but three live agents lost
their terminals. Relaunched with a prompt that says so explicitly, so a resumed
agent does not read the new tab as a rejection of its work.

---

## 2026-09-27 — The guard was a lamp, not a barrier

The seeder fix (`a13b1bb`) edits `backend/seed.py`, a shared file. The guard
fired with exactly the right message —

    FAIL: 1 shared file(s) edited.
      - backend/seed.py

— **and the PR merged anyway.**

That PR was legitimate platform work and *should* have been allowed. The problem
is not that it went through. The problem is that **nothing decided that.** The
guard reported accurately, a merge happened regardless, and the judgement between
a feature and platform work fell to whoever typed `gh pr merge` — which is exactly
the judgement the CI job exists to make.

Measured rather than guessed: `gh pr merge` does not consult CI status, and the
repository had **no branch protection and no rulesets at all**.

### Fixed, and proven

Branch protection on `main`: all four checks required, `strict: true`,
`enforce_admins: true` (so an admin cannot bypass it — which is how the seeder PR
got through), force-push disabled.

The exemption is a **label**, not a CI flag, so the decision is recorded where a
reviewer sees it rather than typed into an invocation nobody reads.

Both halves verified with real PRs, because the earlier probe had only shown the
job going red and the actual failure was a red job that stopped nothing:

| Probe | Expected | Result |
|---|---|---|
| Shared-file edit, no label | merge **refused** | `the base branch policy prohibits the merge` |
| Shared-file edit, `platform-change` label | guard → NOTICE, check passes | passed; log shows the labelled path |

The second probe's end-to-end job independently reported **`OK: 8 feature(s)
loaded, 0 failed`**.

### Also fixed: the seeder could not seed into a new directory

`sqlite3.connect` does not create intermediate directories, so pointing
`DSR_DB_PATH` at a path under a directory that did not exist died with *"unable to
open database file"* — naming neither the file nor the directory. It bit this
project during a routine check, and the verification script **filtered the
seeder's own output**, so the traceback was discarded and only the symptom — an
empty demo dataset — was left to interpret. A script that filters the output of
the thing it is verifying will hide exactly the failure it exists to catch.

Fixed with two regression tests, the first confirmed to fail with the fix
reverted. That test asserts the seeder **wrote rooms**, not merely that it exited
0 — an exit-code-only test would pass on a seeder that wrote nothing, which is
precisely the failure that was nearly mistaken for a broken app.

---

## 2026-09-27 — Nine features live, 108 routes, 895 tests

`96ccc79`. This is the first state where the product is demonstrably a *product*
rather than a set of plugins: every feature contributes its own demo data, the
seeder says what each one added, and the running server serves all of it.

| | |
|---|---|
| Features | **9** (+ the core registry) |
| Routes | **108** |
| Suite | **895 passed, 0 failed** |
| Demo records | 309 live, **351 audit entries** |
| Failed features | **0** |
| Frontend | 85 modules |

**Every feature seeded its own data**, which is the test that the `seed(db,
context)` hook actually works rather than merely existing — nine independent
implementations of the same extension point, none of which edited
`backend/seed.py`:

```
wf002_pages   -> 1 fragment set, 1 fragment, 8 pages, 4 published revisions
wf003_library -> 8 documents given library metadata, 1 room archived, 1 gallery block
wf006_analytics -> analytics_config, 10 timeline notes, 71 activity events
wf007_library -> 3 documents ingested through the real path, 1 folder, 2 thumbnails
wf009_publishing -> 1 approval process, 4 drafts, 2 folders, 2 subscribers, 2 workflows
wf011_publishing -> 1 webhook subscriber, 3 rooms transitioned, 1 template
wf012_generation -> 1 template, 3 generated rooms (draft, published, declined)
wf013_rules   -> 4 variables, 8 blocks, 1 personalisation (3 shown, 1 hidden)
wf016_crm_sync -> 5 CRM fields, 2 automations, 3 subscriptions, 2 events, 5 rows
```

Two prefixes are now shared by two features each — `/api/library` (WF-007 with
WF-008 pending) and `/api/publishing` (WF-009, WF-011) — and both pairs load
together. That was `PORT-PLAN.md`'s stated blocker, resolved by measuring the
rule the host actually enforces rather than the prefix it was checking.

Note the seeding of *interesting* states rather than only happy paths: a room
archived, a generated room declined, an approval workflow left pending, a webhook
delivery retried and one failed, a run unresolved. Demo data that only contains
success teaches a reviewer nothing about the feature.

**WF-008 remains in progress** and is deliberately unmerged.

---

## 2026-09-27 — Ten features, and the prefix claim fully retired

`7d2a5e7` (WF-008) and `cef3cca` (tooling). **10 features, 116 routes, 1011
tests, 0 failed features.**

Both shared prefixes now load two features each, read from the host's own
registry rather than from a file count:

```
/api/library    : wf-007-content-library, wf-008-external-sync
/api/publishing : wf-009-publishing,    wf-011-room-handover
```

`PORT-PLAN.md` called these *"researched twice"* and said the host *"will refuse
the second regardless."* It refused neither. The claim came from comparing
prefixes — the check `fd544e2` replaced precisely because it both wrongly
blocked WF-003/WF-005 and missed genuine dead-code shadowing of core routes.
Repeating that mistake is how four workflows nearly stayed unbuilt for no reason.

### A duplicate that needs a human

There are **two** worktrees for WF-008, `dsr-wf-008-external-sync` and
`dsr-wf-008-external-sync-2`, each an **independent port of the same workflow by
a different agent**, each writing `backend/dsr/features/wf008_external_sync.py`
at the same path. They cannot both land. The verified one is merged; the other
needs a decision about which implementation is better, and that is not a call
to make by whichever branch happened to merge first.

### Two hand-maintained lists, both wrong

`merge_ports.py` held its port list as a constant. Hand-edited three times,
silently reverted twice by shell restarts, each revert costing a merge cycle.
`pending_ports.py` now regenerates it from the worktrees and `main`.

`board_sync.py` asked the wrong question and **demoted nine shipped features to
`todo`** in one pass. A port branch is merged through a *merge* branch rather than
fast-forwarded, so its own commits stay reachable and `ahead` never reaches 0 —
which has nothing to do with whether the workflow shipped. It now asks whether
the feature module is on `main`, which is the measurable question, and the card
comment says so explicitly so the ahead-count is not misread as pending work.

---

## Programme state

| | |
|---|---|
| Features live | **10** of 17 researched |
| Routes | **116** |
| Suite | **1011 passed, 0 failed** |
| Shared prefixes loading together | 2 (`/api/library`, `/api/publishing`) |
| Failed features | **0** |
| PRs this session | 27, all merged or closed-and-deleted |
| Branch protection | all four checks required, `strict`, admins included |

**Remaining of the 17 researched:** WF-001, WF-004, WF-005, WF-010, WF-014,
WF-015, WF-017.

- **WF-005 and WF-014** edit `db/audited.py` **and** `store.py` — the audit
  guarantee itself. Held for a human read of what they changed to the core.
- **WF-001, WF-004, WF-015, WF-017** were rescued from uncommitted working trees,
  so have never been executed. WF-004 also modified `tools/jev.py`.
- **WF-010** has a completed port in its worktree, verified, awaiting merge.
- **The duplicate WF-008 port** needs a decision about which implementation wins.

**Beyond the 17:** the brief asks for 100 workflows across 10 sets. Sets 1–3
covered 10 of the 17 researched. The remaining 90 do not exist yet and would
need new research to the standard the corpus holds — an explicitly-labelled
hypothesis is permitted by `AGENTS.md`, but that is a scope decision, not an
implementation detail.

---

## 2026-09-27 — Set 4: the collision decided before dispatch, and a fourth wrong board test

**`77fe14f` — 11 features, 116 routes, 1211 tests, 0 failed features.**

WF-010 merged, and `/api/library` now carries **three** features at once — WF-007
(12 routes), WF-008 (8), WF-010 (8) — all loading together. That is the furthest
`PORT-PLAN.md` is from being right about shared prefixes, and it was blocked on a
comparison the host never makes.

### The merge script was lying, in the most dangerous way

Invoked to merge WF-010, `merge_ports.py` re-merged **three ports already on
`main`**, printed `merged cleanly` three times, and never touched WF-010. Exit 0.
Every step looked like success.

The list was a hardcoded constant, hand-edited four times; three of those edits
were silently reverted by a shell restart before being committed, and the fourth
reverted to a list naming shipped ports. It now reads
`data/pending_ports.json`, regenerated from the worktrees, and refuses a port
already on `main`, refuses a shared-file edit, and **fails if a merge added no new
feature** — because that is what a no-op merge looks like from the outside.

### A collision found by measuring, then decided by Jev

WF-004 and WF-015 both add `backend/dsr/access.py` and both serve
`GET /api/rooms/{room_id}/access`.

Jev's first ask returned **`uncertain`** — 0.68 against 0.75, `is_confident` 0.43,
*"options too close to decide on this evidence"*. `AGENTS.md` forbids overriding
that, so the missing evidence was gathered rather than the answer guessed.

**What was missing was the content overlap**, which is what the cost of keeping
them separate turns on. The first ask gave the path collision and the route
collision but not that number, and the gap was mine. Measured: **3.2%** in the
domain module, 1.0% in the domain tests, 4.8% in the HTTP tests, **2 shared
symbols of 74**, one of them `__init__`. WF-004's 37 unique symbols are all about
*granting*; WF-015's 35 are all about *verifying*, and "expiry" means different
things in each.

Second ask: **`pass` at confidence 1.00**, margin 1.00, `is_confident` 0.88, for two
self-contained features (`jev-20260927T052837-24152-17484`). So WF-004 renames to
`roles.py` and WF-015 keeps `access.py` — and both briefs say so explicitly, in
opposite directions, because the failure mode is both agents keeping it.

**And that is now tested rather than asserted:** both were dispatched at once, and
WF-004's worktree has `roles.py` with 6/6 granting symbols and 0/8 verifying ones.

### Four wrong answers about whether work landed

`board_sync.py` has put a wrong card on the board four times, each looking like a
considered decision in the output:

| Test | Result |
|---|---|
| ticket on main **and** `ahead == 0` | demoted **9** shipped features to `todo` |
| ticket on main, alone | promoted a **duplicate** port to `completed` |
| `origin/main --contains <tip>` | demoted **all 11** — see below |
| **are this worktree's files on main?** | correct |

The third is the instructive one. PRs here are **squash**-merged, so a port's own
commit SHA is never an ancestor of `main` — verified: the WF-007 tip `9b931c49` is
contained in *no* remote branch. Commit identity cannot answer this in either
direction, and the same class of bug bit `merge_ports.py` too.

The content test separates the two WF-008 worktrees exactly, which nothing else
could: both write `wf008_external_sync.py` at the same path, so path existence and
commit reachability are **identical** for them. The shipped one contributes 0
files absent from `main`; the duplicate contributes **7** — its own
`backend/dsr/library/`, where `main` has `external_library/`. It is now `todo`
with a comment saying a human has to choose, rather than `completed` claiming work
shipped that never did.

### WF-017 was blocked on a permission dialog, and the boundary held

It used `%TEMP%` as a scratch directory and hit *Access external directory*.

`orca terminal send` has **no key option** — only `--text` and `--enter` — and a
tab sent as text is not the bytes a TUI reads, so the first attempt did nothing.
Sending the escape sequences a TUI actually reads (right arrow ×2) reached
`Reject`.

`Always allow` was one keystroke away and would have granted a **standing**
permission to `%TEMP%\*` for this project, to save one agent one keystroke. The
screen shows no highlight, so which option was taken cannot be read from it —
`opencode.json` can, and Temp was verified still absent afterwards. **The screen
cannot tell you what a dialog did; the config can.**

### Two agents, two different interventions

WF-017 was **blocked**, so it was unblocked and told where scratch files belong.

WF-004 was **mid-port with correct content and a wrong filename**, so it was
nudged, not restarted — restarting resets an agent's context and loses work where a
note costs one round trip. It did the rename within a minute.

---

## 2026-09-27 — The corpus was always 138. I was counting branches.

**`e376798`. 11 features, 116 routes, 1211 tests. 135 briefs on main. 13 agents
live.**

### The correction

I had been tracking "17 researched workflows" and treating the other 83 of the
100 as needing new research. `docs/research/digital-sales-room-workflows/wf/`
holds **WF-001 through WF-138**, and:

| | |
|---|---|
| research documents | **139** |
| complete specifications (all nine sections) | **138** |
| blocked | **0** |
| fewer than two cited sources | **0** |
| **median distinct primary-source URLs per spec** | **5** |

WF-001–WF-017 were built as branches. **WF-018 onward have a finished
specification and no code at all** — no branch, no feature module, no tests.

So of the **89 workflows still needed to reach 100, none needs new research.** The
research is done; the work is implementation.

**I was reading the number of branches and calling it the number of workflows.**
Twelve PRs and four port batches all reinforced the wrong number, because every
tool I had written counted branches. Nothing was wrong with the corpus and nothing
was wrong with the agents; the survey was counting the wrong thing.

### Two bugs in the survey, and why they were believable

Both produced plausible numbers, which is the whole difficulty:

**The URL regex matched only the scheme prefix.** `re.compile(r"https?://")` finds
the literal `https://`, so `set(findall(...))` collapses every specification in
the corpus to a single element. The survey reported a median of **one** source per
spec, which reads as a finding about a thin corpus rather than as a bug in a
measuring instrument. Fixed to match the whole URL; the median is **five**.

**The completeness regex required the colon inside the bold.** The corpus writes
`- **user_flow**:` — colon outside the bold — so a pattern for
`**user_flow:**` matches nothing, and **19 complete, well-sourced documents** were
reported as having none of the nine sections. WF-051 was opened and read by hand
and has all nine.

A wrong check does not look wrong. It looks like a finding.

### A second kind of brief

Every brief so far was a **port**: take a branch that has an implementation and
reshape it onto the plugin host. WF-018 onward have nothing to port, so the
instruction has to change to *build this, and the spec is the specification*.

**121 build briefs** are now generated, and each one is asserted individually to
name its own spec document, to say build-not-port, to carry a ticket-derived
prefix, and to carry the temp-directory warning. Then read back **out of git**,
because the working copy is what has been reverting all session.

Three things those briefs have to get right:

- **The research is the spec, and the agent must not redesign it.** A build has
  more licence to be creative than a port does, which is exactly why it needs
  saying. Where the research is ambiguous the brief asks for *which reading you
  took and why*, not a silent choice.
- **The prefix is ticket-derived, `/api/wf-NNN`.** A spec does not declare its
  routes, so a collision cannot be predicted the way it could for a port with a
  known route table. The host refuses a colliding `(method, path)` and **reports**
  it rather than shadowing, so a mistake surfaces as a failure rather than a
  quietly broken route.
- **Build-status checkboxes get ticked honestly, including the one the agent
  cannot tick.** `Verified in localhost browser` — no browser is attached to this
  session — is reported as not verified rather than claimed.

### Seven prompts that went nowhere

The first build batch was ten agents, and **seven never received their brief.**
`send_ok=True` on all ten; the text was typed at a TUI still drawing its splash, so
it went nowhere. Those seven sat on the startup screen with an empty `Ask anything…`
box for several minutes.

The cause is a wait that was long enough for three agents and not for ten: the
dispatch creates each terminal, sleeps 9s, waits for `tui-idle`, and sends. Three
agents give the TUI time to start. Ten, created while the machine is busy running
the earlier ones, do not — and **`tui-idle` is satisfied by a splash screen that
has not started**, because a screen that has not begun is not busy.

The repair waits for the prompt box to actually be drawn rather than for a TUI
condition, confirms the brief is absent before sending so a second run cannot
duplicate a prompt that did land, and then re-reads the screens because
`accepted` comes back empty on a successful send in this Orca build and proves
nothing. All ten are working.

**The first read-back said `active=False` for seven of them and I did not trust
it** — not because the check was clever but because seven simultaneous dead agents
is a much less likely explanation than seven reads that returned nothing, which is
exactly what has happened in this session before when the wrong JSON key was read.
Opening two of them showed the splash screen immediately.
