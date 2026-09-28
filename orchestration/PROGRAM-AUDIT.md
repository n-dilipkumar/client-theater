# Program audit

A running record of key steps for the Digital Sales Room build. Append-only in
spirit: entries are added, not rewritten, so the trail shows how the project's
state was actually arrived at rather than a tidy after-the-fact summary.

Scope note: this file records *program* decisions and milestones. Two other
records exist and are not duplicated here:

* `orchestration/decisions/jev-audit.jsonl` ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â the append-only JSONL log of every
  typed Jev judgment. Written by `tools/jev.py`. Never hand-edited.
* `git log` ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â what changed in the code, and why, in the commit message.

---

## 2026-09-26 ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â Program start: infrastructure before scale

**Starting state, measured rather than assumed:**

| Fact | Value | How established |
|---|---|---|
| Researched workflows | 17 (WF-001ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦WF-017) | `docs/research/digital-sales-room-workflows/wf/` |
| Raw research documents | 9 | `docs/research/raw/` |
| Features ported and merged | 1 (WF-006) | `git log` ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â `f854a79` |
| Backend tests | 145 passing (was 81) | `pytest -q` |
| Headless localhost checks | 22 passing | `tools/verify_localhost.py` |
| Orca worktrees | 22, **all** claiming `in-progress` | `orca worktree list` |
| Worktrees with a live agent | **0** | `orca worktree ps` ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â every one `live:0 pty:no` |
| Branches with unported code | 11 | `git rev-list --count origin/main..<branch>` |
| Branches empty (never built) | 6 | same, `ahead=0 files=0` |
| `n-dilipkumar/*` branches | 21, all empty and already merged | same |
| CI / `.github/` | **did not exist** | `dir .github` ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ not found |
| Open PRs | 0 | `gh pr list` |

**Scope decision.** The brief asks for 10 sets of 10 workflows (100). Only 17 are
researched, and the project's own standard in `AGENTS.md` separates a *sourced*
workflow from a *hypothesis*. Resolved with the user: **port the 16 researched
workflows first, then research sets 2ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Å“10 as new batches** with the same
primary-source discipline. Generating 83 unsourced workflows up front would have
been faster and would have violated the corpus's own provenance rule.

**Branching inconsistency found and fixed.** The Orca repo base ref was
normalised to `origin/main`. Worktrees from the failed run were split between base
`main` and `refs/remotes/origin/main`, so a new agent could branch from a local
`main` that had not been pushed and silently start nine commits behind ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â the
exact class of drift that makes a batch look broken.

---

## 2026-09-26 ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â CI: the feature contract is now enforced, not remembered

The plugin host lets N features merge without conflict, but only while no feature
edits a shared file. Until now that was a rule in a markdown file, checked by
whoever remembered to run `tools/check_feature_diff.py`. WF-006 proved the rule is
followable; nothing proved it would be *followed*. At 100 workflows, an unenforced
convention is a hope.

**PR #1 ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â `420ba54` ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â merged.** Adds `.github/workflows/ci.yml` (4 jobs) and
`.github/PULL_REQUEST_TEMPLATE.md`.

| Job | Runs on | Catches |
|---|---|---|
| `guard` | pull_request only | A feature branch editing a shared file |
| `backend` | all | The full pytest suite |
| `frontend` | all | A feature page that does not compile |
| `end-to-end` | all | A plugin that failed to import |

Two asymmetries, both deliberate:

* **The guard skips `main`.** Platform work legitimately edits shared files ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â
  that is how a new extension point gets added (`44555cd`, the seed hook). A guard
  that must be overridden on every legitimate platform PR trains people to
  override it on illegitimate ones. Tests have no such escape hatch, because the
  audit guarantee is the product.
* **`guard` uses `fetch-depth: 0`.** The guard diffs against `origin/main`; a
  shallow clone of the PR branch alone would make it **silently pass**. A guard
  that passes when it cannot see is worse than no guard.

**PR #2 ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â probe, closed and deleted.** A guard that has only ever passed is
untested. A one-line edit to `backend/dsr/api.py` was pushed deliberately.

| Job | Result |
|---|---|
| `Feature contract (no shared files)` | **fail** (7s) |
| `Backend tests` | pass |
| `Frontend build` | pass |
| `Local host app (headless)` | pass |

This is the discrimination that matters. A shared-file edit compiles, passes every
test, and builds ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â so nothing except the guard would have caught it. CI log
confirmed the right reason: `FAIL: 1 shared file(s) edited ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ - backend/dsr/api.py`,
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

## 2026-09-26 ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â Five workflows were never empty

Jev was asked for the board-status policy and returned **`uncertain`** (0.52
confidence, 0.46 margin between options). `AGENTS.md` says an `uncertain` verdict
must not be overridden ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â gather more evidence or escalate. It was not overridden.

The evidence Jev was missing: whether removing a "dead" worktree would actually
destroy anything. A branch can be empty *at the ref level* while its *working
tree* holds real work, and `git rev-list origin/main..<branch>` ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â the only triage
that had been done ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â cannot see that.

It checked, and the answer reversed the plan:

**`orchestration/PORT-PLAN.md` records WF-001, WF-004, WF-012, WF-015 and WF-017 as
"no commits ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â re-run from scratch".** Their refs are empty. Their directories are
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

They remain **unported and untrusted**. Every one edits shared files ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â `api.py`,
`seed.py`, `ui.jsx`, `lib/api.js`, `App.jsx`, and in WF-001's case
`db/audited.py` ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â which is the exact pattern that stalled the previous run. WF-004
also modified `tools/jev.py`, the shared validator every decision in this project
goes through; that needs reading before any of it is trusted.

**The generalisable defect:** a ref-level metric was used to conclude a working
directory was empty. For a branch-based triage that is a reasonable proxy. For
uncommitted work it is blind, and the failure is silent. Any future triage of this
project must check `git status --porcelain` in the worktree, not only
`git rev-list`.

---

## 2026-09-26 ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â Dispatch set 1: three agents, three environment bugs

The mechanics were established by probing rather than assumed, and two of the
three probes found something that changed the design.

**`orca worktree create --agent opencode` does not work.** It fails with *"Selected
agent is disabled"* ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â only `pi` is enabled in Orca's settings, and that is a
desktop-app toggle with no CLI surface. The working path is
`orca terminal create --worktree <id> --command opencode`, which launches the real
TUI in a real tab; `terminal show` then reports `agentIdentity: opencode` and the
status bar reads **Build Ãƒâ€šÃ‚Â· Space Bunny Free**. So the agent is a genuine OpenCode
agent on the model `AGENTS.md` requires, in a tab, with no settings change needed.
Verified end to end before relying on it: a probe agent was sent a prompt and
answered `TAB-AGENT-OK`.

**`terminal read --json` returns the screen under `result.terminal.tail`** as an
array of lines, not `result.text`. Reading the wrong key returns empty and looks
like a dead agent ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â which briefly read as a broken dispatch.

**A server restart interrupted the dispatch mid-run**, after the worktrees and
agent tabs were created but before the run finished. Reading each screen showed
WF-003 and WF-012 had received their briefs and were working, while WF-002 sat at
an empty prompt. Resuming meant sending WF-002's brief, not re-dispatching ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â
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
   a venv ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â the negative instruction matters as much as the positive one, because
   the wandering is what raised the prompt.
2. **`opencode.json` used V1 syntax in a V2 config.** It had `permission` with a
   `bash` sub-object; V2 wants a `permissions` array of `{action, resource,
   effect}` with actions like `shell`, and explicitly warns that V1's
   `permission`/`bash`/`task` are not the V2 names. The intended policy ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â allow
   `git push` but ask, ask on `gh pr merge` ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â **was not in force at all**.
   Rewritten as V2 rules plus the missing piece: an `external_directory` allow for
   this repo's two directories. That action is what OpenCode checks before any read
   outside the active worktree, and it defaults to `ask`, which is exactly why
   every agent that strayed got a modal prompt nobody could answer for it.

Limitation recorded honestly: the config fix only reaches sessions started after
it merged, so the three running agents were redirected by hand.

---

## 2026-09-26 ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â The four "collisions" were not collisions

`PORT-PLAN.md` blocked WF-007, WF-010, WF-009 and WF-011 on the claim that
*"WF-007 and WF-010 genuinely both own /api/library, and WF-009 and WF-011 both
define a publishing router ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â the host will refuse the second regardless."*

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

The claim came from comparing **prefixes** ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â the exact check `fd544e2` replaced
with route-level comparison, because prefix comparison both wrongly blocked
WF-003/WF-005 and missed genuine dead-code shadowing of core routes. Judging these
pairs by prefix repeated the mistake that fix exists to stop repeating.

Jev was asked whether they were duplicate workflows and answered
**`port_all_four_as_is` at 0.79**. Their shared filename
`backend/dsr/publishing.py` also stops being a conflict: each becomes its own module
under `backend/dsr/features/`. Both features keep the `/api/library` and
`/api/publishing` prefixes, which is safe precisely *because* their concrete paths
differ ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â the case the host was built for.

**Still held back: WF-005**, whose branch edits `db/audited.py` and `store.py`, the
audit guarantee itself. That needs a human read before anything carries it across,
and an agent port is the wrong instrument for it.

---

## 2026-09-27 ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â Three agent-dispatched ports merged: the host works

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
conflicted on almost every merge ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â the exact class of problem the plugin host
exists to remove, reappearing in a file nobody had thought of as contended. The
guard missed it because no feature is *supposed* to edit that file by hand; the
tool writes it as a side effect of doing its job.

Fixed with `merge=union` in `.gitattributes` (`27ebd24`). An append-only log has
no notion of a conflicting line, only of rows that were never merged. `union`
concatenates both sides, loses no row, invents none, and each row carries its own
`audit_id` and `decided_at` so the merged file stays a complete record whatever
order they land in.

### Two tooling defects worth recording

**The agent monitor reported a stopped agent as working** ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â twice, for an agent
that had been idle holding a finished port. The cause was ordering: an ended
OpenCode turn leaves `ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ Ãƒâ€šÃ‚Â· interrupted` in the scrollback while the status bar
*underneath* still shows the spinner strip, and the spinner test ran first. The
spinner glyphs are the status bar, not evidence of activity. Found because the tool
disagreed with a direct screen read, which is the argument for having both a
summary tool and the ability to read the raw thing it summarises.

**`merge_ports.py` began with `git checkout main`**, so work authored on a PR
branch was silently moved onto `main` and the branch was left empty. The merges
kept *succeeding* ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â three clean merges, no conflicts ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â and only the push reported
`Everything up-to-date` while local was seven commits ahead. A reflog showed the
branch had been created once and never moved. Three attempts to fix it with the
edit tool failed silently because the shell restarted and reverted the working
copy; each attempt presented identically, so nothing distinguished "not yet tried"
from "tried and lost". The repair is now written by a script that reads the file
back and refuses to claim success unless the line is actually gone ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â the artifact
is the evidence, not the intention.

### What the agents got right

Each worked from its committed brief and each produced a feature module, a feature
folder, and a test file. No exceptions, and none touched a shared file. Notably
each threaded `source=` through its own domain layer so audit rows name the route
that actually served the write ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â the defect the WF-006 rename exposed, which would
otherwise have been replicated three more times.

### Verification, before merge rather than after

Each port was checked by running the checks, not by reading the agent's claim ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â an
agent reporting "246 passed" is asserting a number. Suite in each agent's own
worktree with the feature mounted (202 / 246 / 217), the host registry queried
through `/api/features` to confirm the feature *loads* rather than being silently
skipped, and the guard run per branch.

Merged as **PR #10 ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ `8920d2f`**, all four CI jobs green, 375 tests, 57 frontend
modules, all 22 headless checks passing with four features mounted.

---

## 2026-09-27 ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â Set 2 dispatched; a backtick in a prompt ate two briefs

WF-007, WF-009, WF-010 and WF-011 dispatched ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â the four held back as collisions
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
the identical hazard and happened to survive ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â nothing about the two cases differed
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
dispatch where two of the four deliberately share an API prefix ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â WF-007 and
WF-010 under `/api/library`, WF-009 and WF-011 under `/api/publishing`. Safe
precisely because their concrete paths differ: the case the host exists to allow,
and the case `PORT-PLAN.md` wrongly flagged as a collision.

Board corrected to match: 4 `in-progress` with live agents, 8 `completed`, 16
`todo`, zero stale cards.

---

## 2026-09-27 ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â Set 2 merged; a brief of mine broke a feature

**`fb30903`. Seven features, 81 routes, 604 tests, 0 failed.**

| Workflow | Prefix | Routes | Suite in its worktree |
|---|---|---|---|
| WF-007 | `/api/library` | 12 | 437 passed |
| WF-009 | `/api/publishing` | 13 | 445 passed |
| WF-011 | `/api/publishing` | 10 | 472 passed |

All three merged cleanly, one at a time, with the suite and registry re-checked
after each. **WF-009 and WF-011 both mount `/api/publishing` and both load
together** ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â the case `PORT-PLAN.md` blocked as "researched twice" on a *prefix*
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
to parse a file upload ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â which is precisely what document ingest is.

WF-007's **original branch** had already declared it, commented *"Required by
FastAPI to parse the multipart ingest request (WF-007)."* The port reversed a
decision the original author had made correctly, because I told it to. Fixed in
`580dca6`, and the brief template corrected to say that a dependency a feature
cannot work without is that feature's own requirement, not a coordination
problem.

**Blast radius worth knowing:** while WF-007 was unloaded,
`test_the_feature_did_not_collide_with_anything` in **WF-003's** suite also
failed ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â a different feature with nothing to do with uploads. It passed again once
WF-007 loaded. So *"the suite is green"* is a statement about which features
loaded at least as much as about the code, and a **silently skipped feature is
not a green run**.

### A false positive worth recording

The set-3 dispatch reported `mangled=True` for WF-016. Reading the screen showed
the agent had itself piped pytest into `| tail -n 20` ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â a Unix idiom unavailable in
cmd ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â and had recovered unaided. The detector matched any *"not recognized as an
internal or external command"*, which fires on an agent's own typo as well as on a
mangled prompt. Three tools narrowed to match only the word that starts the
prompt. A check that cries wolf is worse than no check, because it trains you to
ignore it.

### A mistake of my own

Closing the finished set-2 tabs, I read the handle map for the wrong batch and
closed the **set-3** agents instead. No work was lost ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â their worktrees were
intact, WF-016 still holding its 10 uncommitted files ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â but three live agents lost
their terminals. Relaunched with a prompt that says so explicitly, so a resumed
agent does not read the new tab as a rejection of its work.

---

## 2026-09-27 ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â The guard was a lamp, not a barrier

The seeder fix (`a13b1bb`) edits `backend/seed.py`, a shared file. The guard
fired with exactly the right message ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â

    FAIL: 1 shared file(s) edited.
      - backend/seed.py

ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â **and the PR merged anyway.**

That PR was legitimate platform work and *should* have been allowed. The problem
is not that it went through. The problem is that **nothing decided that.** The
guard reported accurately, a merge happened regardless, and the judgement between
a feature and platform work fell to whoever typed `gh pr merge` ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â which is exactly
the judgement the CI job exists to make.

Measured rather than guessed: `gh pr merge` does not consult CI status, and the
repository had **no branch protection and no rulesets at all**.

### Fixed, and proven

Branch protection on `main`: all four checks required, `strict: true`,
`enforce_admins: true` (so an admin cannot bypass it ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â which is how the seeder PR
got through), force-push disabled.

The exemption is a **label**, not a CI flag, so the decision is recorded where a
reviewer sees it rather than typed into an invocation nobody reads.

Both halves verified with real PRs, because the earlier probe had only shown the
job going red and the actual failure was a red job that stopped nothing:

| Probe | Expected | Result |
|---|---|---|
| Shared-file edit, no label | merge **refused** | `the base branch policy prohibits the merge` |
| Shared-file edit, `platform-change` label | guard ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ NOTICE, check passes | passed; log shows the labelled path |

The second probe's end-to-end job independently reported **`OK: 8 feature(s)
loaded, 0 failed`**.

### Also fixed: the seeder could not seed into a new directory

`sqlite3.connect` does not create intermediate directories, so pointing
`DSR_DB_PATH` at a path under a directory that did not exist died with *"unable to
open database file"* ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â naming neither the file nor the directory. It bit this
project during a routine check, and the verification script **filtered the
seeder's own output**, so the traceback was discarded and only the symptom ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â an
empty demo dataset ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â was left to interpret. A script that filters the output of
the thing it is verifying will hide exactly the failure it exists to catch.

Fixed with two regression tests, the first confirmed to fail with the fix
reverted. That test asserts the seeder **wrote rooms**, not merely that it exited
0 ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â an exit-code-only test would pass on a seeder that wrote nothing, which is
precisely the failure that was nearly mistaken for a broken app.

---

## 2026-09-27 ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â Nine features live, 108 routes, 895 tests

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
context)` hook actually works rather than merely existing ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â nine independent
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

Two prefixes are now shared by two features each ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â `/api/library` (WF-007 with
WF-008 pending) and `/api/publishing` (WF-009, WF-011) ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â and both pairs load
together. That was `PORT-PLAN.md`'s stated blocker, resolved by measuring the
rule the host actually enforces rather than the prefix it was checking.

Note the seeding of *interesting* states rather than only happy paths: a room
archived, a generated room declined, an approval workflow left pending, a webhook
delivery retried and one failed, a run unresolved. Demo data that only contains
success teaches a reviewer nothing about the feature.

**WF-008 remains in progress** and is deliberately unmerged.

---

## 2026-09-27 ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â Ten features, and the prefix claim fully retired

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
prefixes ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â the check `fd544e2` replaced precisely because it both wrongly
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
fast-forwarded, so its own commits stay reachable and `ahead` never reaches 0 ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â
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

- **WF-005 and WF-014** edit `db/audited.py` **and** `store.py` ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â the audit
  guarantee itself. Held for a human read of what they changed to the core.
- **WF-001, WF-004, WF-015, WF-017** were rescued from uncommitted working trees,
  so have never been executed. WF-004 also modified `tools/jev.py`.
- **WF-010** has a completed port in its worktree, verified, awaiting merge.
- **The duplicate WF-008 port** needs a decision about which implementation wins.

**Beyond the 17:** the brief asks for 100 workflows across 10 sets. Sets 1ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Å“3
covered 10 of the 17 researched. The remaining 90 do not exist yet and would
need new research to the standard the corpus holds ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â an explicitly-labelled
hypothesis is permitted by `AGENTS.md`, but that is a scope decision, not an
implementation detail.

---

## 2026-09-27 ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â Set 4: the collision decided before dispatch, and a fourth wrong board test

**`77fe14f` ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â 11 features, 116 routes, 1211 tests, 0 failed features.**

WF-010 merged, and `/api/library` now carries **three** features at once ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â WF-007
(12 routes), WF-008 (8), WF-010 (8) ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â all loading together. That is the furthest
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
feature** ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â because that is what a no-op merge looks like from the outside.

### A collision found by measuring, then decided by Jev

WF-004 and WF-015 both add `backend/dsr/access.py` and both serve
`GET /api/rooms/{room_id}/access`.

Jev's first ask returned **`uncertain`** ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â 0.68 against 0.75, `is_confident` 0.43,
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
`roles.py` and WF-015 keeps `access.py` ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â and both briefs say so explicitly, in
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
| `origin/main --contains <tip>` | demoted **all 11** ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â see below |
| **are this worktree's files on main?** | correct |

The third is the instructive one. PRs here are **squash**-merged, so a port's own
commit SHA is never an ancestor of `main` ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â verified: the WF-007 tip `9b931c49` is
contained in *no* remote branch. Commit identity cannot answer this in either
direction, and the same class of bug bit `merge_ports.py` too.

The content test separates the two WF-008 worktrees exactly, which nothing else
could: both write `wf008_external_sync.py` at the same path, so path existence and
commit reachability are **identical** for them. The shipped one contributes 0
files absent from `main`; the duplicate contributes **7** ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â its own
`backend/dsr/library/`, where `main` has `external_library/`. It is now `todo`
with a comment saying a human has to choose, rather than `completed` claiming work
shipped that never did.

### WF-017 was blocked on a permission dialog, and the boundary held

It used `%TEMP%` as a scratch directory and hit *Access external directory*.

`orca terminal send` has **no key option** ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â only `--text` and `--enter` ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â and a
tab sent as text is not the bytes a TUI reads, so the first attempt did nothing.
Sending the escape sequences a TUI actually reads (right arrow ÃƒÆ’Ã¢â‚¬â€2) reached
`Reject`.

`Always allow` was one keystroke away and would have granted a **standing**
permission to `%TEMP%\*` for this project, to save one agent one keystroke. The
screen shows no highlight, so which option was taken cannot be read from it ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â
`opencode.json` can, and Temp was verified still absent afterwards. **The screen
cannot tell you what a dialog did; the config can.**

### Two agents, two different interventions

WF-017 was **blocked**, so it was unblocked and told where scratch files belong.

WF-004 was **mid-port with correct content and a wrong filename**, so it was
nudged, not restarted ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â restarting resets an agent's context and loses work where a
note costs one round trip. It did the rename within a minute.

---

## 2026-09-27 ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â The corpus was always 138. I was counting branches.

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

WF-001ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Å“WF-017 were built as branches. **WF-018 onward have a finished
specification and no code at all** ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â no branch, no feature module, no tests.

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
`- **user_flow**:` ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â colon outside the bold ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â so a pattern for
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
  cannot tick.** `Verified in localhost browser` ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â no browser is attached to this
  session ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â is reported as not verified rather than claimed.

### Seven prompts that went nowhere

The first build batch was ten agents, and **seven never received their brief.**
`send_ok=True` on all ten; the text was typed at a TUI still drawing its splash, so
it went nowhere. Those seven sat on the startup screen with an empty `Ask anythingÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦`
box for several minutes.

The cause is a wait that was long enough for three agents and not for ten: the
dispatch creates each terminal, sleeps 9s, waits for `tui-idle`, and sends. Three
agents give the TUI time to start. Ten, created while the machine is busy running
the earlier ones, do not ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â and **`tui-idle` is satisfied by a splash screen that
has not started**, because a screen that has not begun is not busy.

The repair waits for the prompt box to actually be drawn rather than for a TUI
condition, confirms the brief is absent before sending so a second run cannot
duplicate a prompt that did land, and then re-reads the screens because
`accepted` comes back empty on a successful send in this Orca build and proves
nothing. All ten are working.

**The first read-back said `active=False` for seven of them and I did not trust
it** ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â not because the check was clever but because seven simultaneous dead agents
is a much less likely explanation than seven reads that returned nothing, which is
exactly what has happened in this session before when the wrong JSON key was read.
Opening two of them showed the splash screen immediately.

---

## 2026-09-27 ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â Twenty-four features, and three defects the tooling was built to find

**`4fe7a64`. 25 features (24 + the registry), 317 routes, 3,989 tests, 0 failed
features.** 38 PRs. Set 2 of the build programme dispatched: WF-028..WF-037.

| | before this stretch | now |
|---|---|---|
| features | 11 | **24** |
| routes | 116 | **317** |
| tests | 1,211 | **3,989** |
| demo records | 309 | **819** |
| audit entries | 351 | **798** |
| failed features | 0 | **0** |

The verified local host app, on merged `main`: 152 frontend modules, a 665 KB
bundle carrying **22 of 22** product feature ids, 819 live records across 60
collections, 798 audit entries. Every one of the 24 features contributed its own
seed data, and the states they chose are the interesting ones - a hot / warm /
cooling / cold engagement spread, a section rule that hides an order form until it
completes, a deal closed before it was created, an account with no client
activity, **60 delivered / 40 failed-needing-a-human / 18 skipped** deliveries,
one signal rendered in French and one through locale fallback, one emission
refused for failing its own bound, a domain awaiting DNS. Demo data containing only
success teaches a reviewer nothing about the feature.

Both shared prefixes still hold: **`/api/library` carries three features**,
`/api/publishing` carries two.

### Defect one: a retry never cleared the row it was retrying

CI went red with one failure and **branch protection refused the merge** - the
barrier added in #20 doing the job it was installed for, on the first real
violation it saw.

    tests/test_wf026.py::test_a_manual_retry_clears_a_skipped_row_once_the_blocker_is_gone
    AssertionError: assert 'skipped' == 'delivered'

Locally the suite passed 3,659 twice, and the test passed alone and in its own
file. It looked like CI-only flakiness. **It was a real defect and the test had
been passing for the wrong reason.**

A delivery's `event_key` includes the prospect id, so a row skipped for want of a
prospect is keyed with an **empty** one. `retry_delivery` recomputed the key - and
by then a prospect *had* been linked, so the recomputed key missed the row it was
asked to clear and the retry wrote a **second** one. The original stayed `skipped`
forever while a delivered duplicate appeared beside it, and because the two carry
different keys a later publish no longer saw the event as a duplicate. The
activity feed shows the event twice and the first card still claims it was never
sent.

The test had asserted `feed.deliveries()[0]` - index 0 of a query implemented as
`store.find(...)`, **which takes no ordering argument**, though `deliveries()`
documents itself "newest first". The retry's duplicate was the row at index 0, and
it was delivered, so the assertion was reading the *duplicate* and passing on it.
Addressing the row by its own id removed the ambiguity and **reproduced the defect
locally, immediately**, which is how it was confirmed real rather than
environmental. The fix: a named row wins over the key lookup. Reverting the fix
makes the test fail with the module and line named, so the guard has teeth.

### Defect two: a feature's test asserted a claim about the whole repository

WF-004's suite carried `assert not (package / "access.py").exists()`. It reads like
the right guard - and it is a much stronger claim: true only while WF-015 is absent,
and WF-015 legitimately ships `access.py`, because keeping that name is the entire
point of the rename. It went red the moment the sibling landed, in a suite
unrelated to the failure.

It is now a claim about what **this** feature owns: that `dsr.roles` exists and
nothing in WF-004's files reaches for `dsr.access`. A companion test asserts WF-004
neither creates nor deletes the module, because **a test that insists a file does
not exist invites someone to "fix" a red suite by deleting it** - which would take
WF-015's domain with it, the opposite of the collision prevention it was written
to provide. Both proven by reintroducing the collision.

### Defect three: the merge script could only be run once

It decided what to skip against `origin/main`, while the merge target is a PR
branch that may already carry ports. A re-run after a partial run re-merged a port
already there, and the no-op guard correctly reported it. The guard was right and
the question was about the wrong tree. Runs stop halfway *because* that guard
exists, so the script has to be able to recover. It now asks about `HEAD` as well
and prints what the target branch already carries.

### What the one-at-a-time merge bought

WF-015 passed alone - 1,324 tests, 0 failed - and broke one test in the merged
tree. The script prints `VERDICT` after **each** merge, so a red result names the
port that caused it. Ten features merging simultaneously would have produced one
red build and no idea which of the ten was responsible, which is precisely the
failure mode of the previous run of this project: twelve branches built, none
merged, because all twelve edited the same three files.

---

## The three held workflows, and a hold that turned out to be wrong

**PR #41, commit `182f56e`. Suite 3,989 to 4,012. PR #42 lifts the hold and
carries the briefs.**

WF-001, WF-005 and WF-014 had been parked on a claim, repeated often enough to
have become a blocker: their branches edit `db/audited.py` and `store.py`, and a
port is a mechanical transformation, which is the wrong instrument for deciding
what a change to the audit core means. The reasoning was sound. **The conclusion
was wrong, and so was the fact underneath it.**

Read the diffs and all three add *missing host capabilities that the core's own
documented guarantee requires* ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â not domain concepts reaching in from a feature:

| Workflow | Adds | The core's own words |
|---|---|---|
| WF-001 | `transaction()`, `AuditedWriter`, `_tx_depth` | N audited writes in ONE all-or-nothing transaction, each still audited, and it **refuses** single-record writes while open so the guarantee cannot be bypassed. *"A workflow that must produce more than one record cannot half-succeed through this handle."* |
| WF-005 | `bulk_delete()` | Many records, ONE transaction, ONE audit row. *"Deleting a room and the documents it owns one call at a time would let the audit log describe work that only partly happened, which is the exact failure mode the guarantee exists to prevent."* |
| WF-014 | `count_where()` | The counting twin of `find()` over the dynamic index, *"so a caller that needs an aggregate over schema-flexible data does not have to fetch rows to measure them, and does not have to reach around this wrapper to write the SQL itself."* |

WF-014's gap was **already known and already recorded in the repository**.
WF-009, merged, says so in its own commit message: *"The other, count, has no
honest equivalent on the facade ... a capped `len(list(...))` would quietly
downgrade it."* A shipped feature had documented a missing primitive and shipped
a workaround for it. That is the strongest possible evidence the primitive
belongs in the host: a feature already had to route around its absence.

So dropping these changes would drop the guarantee, and they were promoted into
the host deliberately as a labelled `platform-change` ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â **PR #41, merged, all
four checks green.** Jev: `promote_deliberately` at **0.99**, margin 1.00.

**The lesson is about the hold, not the merge.** A blocker that is never examined
is a story, and this one had been carried through a dozen status reports as
though it were a fact about the code. It was a fact about a summary, written once
and inherited thereafter. The same reasoning that demanded a human read is what
should have demanded the diff be read first.

### Three approaches that fail, and the one that works

- **A patch does not apply.** All three branches are **47 commits behind `main`**,
  so a 3-dot diff's context is a merge base that no longer exists in the tree.
  The refusal reads like a conflict and is actually a stale base.
- **A whole-file merge of `test_audited.py` conflicts from line 1.** Twenty-four
  features have landed and every one appended to that file, so a file-level merge
  cannot tell an append from an edit. The tests therefore move at the granularity
  of a **test function**, which is the unit that is actually independent.
- **The first merge loop was wrong and looked right.** Using pristine `main` as
  *ours* and overwriting the result each time meant the **last branch won and the
  earlier two capabilities were silently dropped** ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â with plausible line counts
  (787, 830, 962), because each result was a superset of `main` alone. The merge is
  cumulative now, and every capability is **asserted present in the file
  afterwards** rather than inferred from a clean exit code. *A clean merge that
  drops a capability is still a clean merge.*

The 23 tests travelled with the code rather than being retyped, because retyping
is a way to end up testing something subtly different from what shipped.

### The two collisions that were waiting behind the hold

**`rooms.py` ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â WF-001 and WF-005 both add it.** Neither exists on `main`, so this
was never a merge, only a naming decision. Measured: WF-001's is 438 lines with
**82** mentions of templates and **2** of lifecycle; WF-005's is 640 lines with
**84** of lifecycle and **0** of templates. Neither is a superset, so the
more-general-takes-the-neutral-name rule did not decide it.

**WF-005 keeps `rooms.py`.** Its module defines the room *state machine* ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â
`RoomWorkflow`, `status_of`, `available_actions`, `capabilities_for`, `Principal`
ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â which is what the neutral name means, and WF-001's `create_room` needs somewhere
to put a room's status. WF-001 becomes `room_templates.py`, which is exactly what
it is. A module named `rooms.py` that can only create rooms from templates
misleads the next reader, and **the archive path is the one that has to be
findable when a room comes back.** Jev: **1.00**.

**`access.py` ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â WF-014 adds it; `main` already has it from WF-015.** **WF-015
keeps it.** Renaming shipped, tested code to accommodate an unbuilt workflow is
backwards. WF-014 becomes `access_controls.py` ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â which is the name its own design
document already uses. Same collision as WF-004 versus WF-015, where WF-004 became
`roles.py` for exactly this reason. Jev: **1.00**.

## The duplicate WF-008: superseded, not a second design

`dsr-wf-008-external-sync-2` held a second, unreviewed WF-008. Decided on
measurement:

- **Both branches are the same port.** The worktree's commit reads *"Port WF-008
  to the plugin host so it can merge at all"*; `main`'s merged commit reads
  *"Port WF-008 onto the plugin host so it can merge without a conflict (#26)"*.
  One landed, one did not. 1 of 5 domain files is byte-identical to `main`'s and
  the other 4 differ within a few lines.
- **The collision is silent, and that is what made it worth measuring.** With the
  branch's `dsr/library/` package in place, `import dsr.library` resolves to
  `library/__init__.py` instead of `main`'s 931-line `library.py` ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â which WF-007
  imports ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â **and the import succeeds.** Nothing reports it. A directory and a
  module of the same name cannot coexist in one package, and the failure mode is
  not an error but the wrong code quietly running.
- **`main` carries more tests**: 71 in `test_wf008.py` plus 45 in
  `test_wf008_http.py`, against the branch's 50.
- **The one file `main` lacks is redundant.** The branch's `test_library_sync.py`
  has 58 tests of the sync engine directly. Run against `main`'s implementation
  in a scratch copy, **all 58 pass.** `main` already satisfies them.

So: keep `main`'s, salvage nothing, and **delete the worktree last** ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â it is the
one irreversible step, and until the tests and frontend were assessed on their own
merits there was a real chance the branch held something worth keeping.

### A misreading caught in the same pass

An earlier comparison in this same session reported the branch's tests and
frontend as *"absent from `main`"*. **That was wrong.** In `git diff
origin/main...HEAD`, the `A` status means added in the HEAD *relative to a
47-commit-old merge base* ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â not absent from `main`, which has since landed its own
versions of both. A 3-dot diff reports the base's state, and reading it as
`main`'s produces a confident, wrong inventory. It nearly sent a decision the
wrong way.

## Two tooling defects, both of which hid work rather than causing it

**`dispatch_build_batch.py` overwrote the agent handle log.** It wrote the current
batch only, so every dispatch erased the record of the agents already running.
`agent_watch` consequently reported 13 dispatched when 20 were in flight ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â which is
why a batch of ten looked as though it had never started. It merges by ticket now.
It earned its place immediately: a server restart killed the shell mid-dispatch of
WF-048..055, and **all eight were still on record** even though the output was
lost.

**`brief_title()` matched only `Build brief:`.** The three newly-lifted briefs say
`Port brief:`, so all three were **skipped silently** ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â a dispatcher reporting
nothing to do while three briefs sat on disk. The match is now tolerant of the
brief *kind* as well as the ticket spelling, which is the same lesson already
learned once: WF-004's `wf_004-invite-buyer.py` filename led to every `wf(\d{3})`
pattern in the tooling being widened to `wf[_-]?(\d{3})`, so one odd name could not
hide a feature. **The same class of bug, found by the same class of check, in
tooling that had already been fixed once.**

Two verification bugs were also caught in this stretch, both recorded because both
reported a problem that was not there: a regex `3,989 to (\d+)` that matched `4`
and stopped at the comma in `4,012`; and `@()` around `ConvertFrom-Json` output,
which wrapped a 13-element array as one object so `.Count` read 1 and the handle
log looked destroyed when it had merged correctly. **A check with a bug in it is
worse than no check**, because it invites a second, worse fix.

## Where this leaves the programme

**24 features live**, 317 routes, **4,012 tests**, 0 failed. Two platform
capabilities added to the host. Three workflows unblocked and dispatched. One
duplicate adjudicated and closed out. **21 agents in flight** ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â WF-001, WF-005,
WF-014 (the lifted ports) plus WF-038..047 and WF-048..055 (builds).

`orchestration/STATUS.md` is generated and self-asserting: every number in it is
measured, and the generator checks the rendered file against what it just
measured, because a dashboard nobody checks is how a dashboard goes stale.

---

## An order that was never a promise: two defects behind one CI failure

**PR #44, WF-030 and WF-032. Suite 4,565 Ã¢â€ â€™ 4,568.**

The local suite passed 4,565 with 0 failures. CI failed with one:

    tests/test_wf030.py::test_activity_is_listed_oldest_first
    assert stamps == sorted(stamps)
    AssertionError: assert ['2026-09-27T...:40:00+00:00'] == ['2026-09-27T...:50:00+00:00']

Same commit, same data, different answer on a different machine. **That is not
flakiness. It is an order that was never actually guaranteed**, and a test that
agrees with the local machine is not evidence about ordering at all.

### The outer defect: `updated_at` is not a total order

`AuditedDatabase.list()` and `.find()` both did `ORDER BY updated_at DESC` with
**no tie-break**. `updated_at` is a second-granularity timestamp, so rows written
in the same second *tie* Ã¢â‚¬â€ and the order among tied rows is whatever SQLite's
query plan produces. Locally the plan returned insertion order. On the runner it
did not.

Ties are not an edge case. **Any burst of writes ties**, and this suite creates
records in tight loops by design. Every caller that reverses a result, or
documents an order it does not itself impose, inherits a coin flip Ã¢â‚¬â€ and several
do, because *"list() is newest first"* reads like a promise and is not one.

Both now order by `<key> <dir>, id <dir>`. `id` is unique, so the order is total.
Rows that do not tie keep exactly the order they always had; rows that do are now
deterministic instead of arbitrary. **This is a change to `db/audited.py`, so it
carries the `platform-change` label** Ã¢â‚¬â€ a deliberate change to a shared host file,
recorded where a reviewer sees it.

### The inner defect: reversing is not sorting

`crm_workflows.engine.activity()` documented *"the order they happened"*, then
implemented it as `rows.reverse()` over `store.list()`, which orders by
`updated_at` Ã¢â‚¬â€ when the row was **written**, not when the event **happened**. The
two agree only when events arrive chronologically, and events arrive in whatever
order they happen: a backfill, a replay, a slow webhook. Its docstring also
promised the match count *"would not depend on the sort"* while depending on
exactly that.

It now sorts by `occurred_at`, with `id` as a stable tie-break.

### The same mistake, made twice

This is the second time this exact error has been made in this repository. The
first was `deliveries()` asserting *"newest first"* over a `find()` that **takes
no ordering argument at all** Ã¢â‚¬â€ recorded earlier in this log as a test that was
"passing for the wrong reason". Both surface identically: a test that passes
locally and fails on CI. Both are one mistake Ã¢â‚¬â€ **treating an order you did not
impose as though you had.**

A repository accumulates this class of defect faster than it accumulates the
obvious ones, because the mistake is locally invisible. The only defence that
works is a test that asserts stability across *repeat* reads, not one that
asserts a single call came out right.

### Tests that pin it

`backend/tests/test_ordering_determinism.py`:

* `list()` returns the same order across five repeat reads when timestamps tie,
  and each tie group is internally ordered;
* `find()` likewise;
* activity is listed in the order events happened when they are recorded
  **newest first** Ã¢â‚¬â€ the case a `.reverse()` implementation still gets wrong once
  the tie-break is fixed.

## A merge that reported success without merging

`merge_ports.py` merged **WF-001, which had zero commits** Ã¢â‚¬â€ the agent was still
writing. Its staging ref was therefore identical to `main`, `git merge` exited `0`
without changing anything, and the script printed:

    merged cleanly
    suite after merge : 4012 passed, 0 failed

**Identical to the baseline.** A no-op reported as a clean merge, at the cost of a
full baseline suite and a full post-merge suite spent on nothing.

The file's own docstring says it was rewritten to stop a no-op reporting success.
It had stopped *one* no-op Ã¢â‚¬â€ re-merging a feature already on `main` Ã¢â‚¬â€ and left the
other wide open: merging something that was never there. Three guards now:

1. `load_ports()` excludes zero-commit workflows and **lists them with their
   uncommitted file counts**, so being left out is visible rather than silent.
2. An empty staging ref is refused *before* the merge, naming the reason.
3. `HEAD` is compared before and after. A merge that exits `0` without moving
   `HEAD` is refused, and `merged cleanly` now states the commit count and both
   short SHAs.

**And the new guard was itself broken on first use** Ã¢â‚¬â€ it unpacked `git()`'s return
as three values when it returns two, so the merge loop raised `ValueError` *after*
the baseline suite had run. Five minutes to learn the loop could not start.
`--preflight` now walks every step except the suite and the merge in about a
minute, and would have caught it immediately. **A safety check that has never been
exercised is not a safety check**, and the cheapest way to exercise one is a mode
that skips the expensive part.

The log reader had the mirror-image fault: it filtered tracebacks out, so that
crash printed as a run in which nothing had happened. **A reader that discards the
most important line in the file is a reader that reports success during a
failure** Ã¢â‚¬â€ the same defect one layer up.

## Out of memory, and what it was actually caused by

The merge run stopped on `fatal: Out of memory, calloc failed` during a
`git fetch`, with **0.58 GB free of 31.6 GB**.

The obvious hypothesis was twenty-four agents. **It was wrong.** Measurement
found seven `opencode` processes, of which **one held 14.5 GB resident and 53 GB
of private commit** Ã¢â‚¬â€ a leak, not load, and it accounted for 85% of all agent
memory. One runaway process, not the agent count, starved the merge.

It was not killed. Every candidate's parent is `cmd.exe` Ã¢â‚¬â€ including this
session's own Ã¢â‚¬â€ so the evidence did not distinguish an agent from the session
doing the work, and **the cost of guessing wrong is ending the session while the
cost of leaving it is only slowness.** Measured, then declined to act on an
ambiguous reading: which is the correct order of operations when one option is
reversible and the other is not.

---

## Six more features, and a repository that was rewriting its own history

**PR #48. 33 workflows live. Suite 6,724.**

### The guard refused twenty-six features at once, and it was right to refuse none of them

`merge_ports.py --preflight` reported that **all twenty-six** pending ports
touched **all eleven shared files**, across **1,286 to 1,306 files** each. That
is the most convincing-looking violation this project has produced, and it was
completely spurious.

Remote `main`'s history has been rewritten: it shares a root commit with local
`main` and nothing after it. So `git diff origin/main...<branch>` â€” a three-dot
diff â€” has **no merge base**, falls back to comparing whole trees, and reports
every file that differs between an old-main-based branch and today's main. The
entire repository. On every port. At once.

The guard was right and the input was wrong. **No twenty-six agents each
rewrote the host.** Subtracting the branch's own merge-base isolates exactly what
each agent did: **one commit, 17 to 22 files, no shared file, every time.**

A lesson worth more than the fix: *a guard that rejects everything is usually
being fed the wrong question, not being obeyed too little.* Sixteen finished
features were blocked behind a measurement that was never about them.

### Landing by content, because the history is not comparable

Even with the right base, a cherry-pick reported add/add conflicts on fourteen
files â€” `AGENTS.md`, `api.py`, `audited.py`, `store.py`, `seed.py`,
`package.json`, `App.jsx`, `ui.jsx`, `api.js`, `vite.config.js`, `jev.py`,
`test_audited.py`, `pyproject.toml` â€” and every one showed `base: 0 lines`.

The commits **never touched those files.** What each commit actually adds is 16
to 17 files, all status `A`: a domain package, a feature module, a test file,
and the frontend feature directory. That is the feature contract working exactly
as designed.

So the features were landed **by content**: take the files they add, leave
`main`'s version of everything else alone. History is a detail; the content is
the deliverable.

### Two features held back because CI said so, by name

The batch was six. CI rejected two, in ninety-eight seconds:

```
FAILED tests/test_wf038.py::test_all_or_none_rolls_the_whole_chunk_back
       AssertionError: assert 'failed' == 'rolled_back'
FAILED tests/test_wf038.py::test_a_single_row_201_created_and_204_updated
       AssertionError: assert 'updated' == 'created'
FAILED tests/test_wf040.py::test_the_demo_is_deterministic
       AssertionError: assert [...] == [...]     (the same rows, ordered differently)
```

**All three passed locally on that exact commit.** `'created'` versus
`'updated'` is a row that already existed. A "deterministic" listing that is not
deterministic is the same defect. Each test depends on what ran before it, or on
insertion order, rather than on the behaviour it names.

**WF-038 and WF-040 were not merged.** The four CI accepted were. Branch
protection identified precisely which two were broken, and the honest response to
a red gate is to land what is green and fix what is not â€” not to merge all six
and leave a red suite for whoever looked next. That is the failure this project
paid for once already.

### A credential test that was failing on a coin flip

`test_the_seed_never_stores_a_credential_in_plaintext` went red the moment WF-040
landed, and was not WF-040's doing: that feature never mentions the credential
collection, and the test passed alone and in its own file â€” 188 green.

The test searched the whole record body for `at-` and `rt-`, the access-token
and refresh-token prefixes. But `sealed` is **ciphertext** â€”
`v1.<key>.<payload>.<tag>` in base64url, whose alphabet includes `-` â€” so a
random sealed value contains those substrings by chance.

**Measured over 400 independent seeds: `at-` appeared in 3, `rt-` in 4**, the
envelope was `v1` every time, and `secret` never appeared at all. Roughly one
run in fifty-seven, in a suite with nothing to do with credentials.

It now asserts per field: `sealed` is a well-formed v1 envelope, `fields` names
what is sealed without revealing it, and every *other* field is searched for the
token prefixes â€” which is exactly where a plaintext credential would be.

Verified both ways, because a security test that has quietly stopped checking is
worse than a flaky one: **80 consecutive runs, 0 failures**, and a planted
plaintext token in a readable field **is still caught**, with the failure naming
the field.

An earlier attempt to prove the trap added a second test asserting that random
base64url *does* contain those prefixes. **It measured 0 hits in 2,000** â€” the
probability model was wrong, and the test would have failed. It was cut rather
than shipped. A test written to prove a point is still a test, and still has to
be right.

## Attribution: the repository was adding a bot as a contributor

`merge_ports.py` wrote `Co-authored-by: CommandCodeBot noreply@commandcode.ai`
into the message of **every merge it made**. That is the repository adding an
automated identity as a contributor to its own history, from its own tooling.
It was invisible in normal use â€” the trailer appears in `git log --format=%B` but
not in `--oneline`, and a squash collapses the merge message away entirely.

Removed, and the local identity corrected to `ddilipnithyanandam@gmail.com`.
Every commit is now re-authored on landing, because **cherry-picking an agent's
commit preserves the agent as author *and* preserves its co-author trailer** â€”
landing one is not the same as merging one. The subject, which describes the
work, is the agent's and is kept.

**One thing not fully under this repository's control, recorded plainly:** the
squash-merge commits on `main` are attributed by GitHub to
`45650186+n-dilipkumar@users.noreply.github.com`, the account those credentials
belong to, because that is what `gh pr merge --squash` uses. It is the same
person, not a second contributor, and **no bot or agent identity appears
anywhere in the history this session produced** â€” verified by reading
`%(trailers:key=Co-authored-by)` back out of git for every commit.

## The `origin` remote keeps being removed

Twice now, unprompted, and each time it broke the pipeline in a way that pointed
somewhere else: the first time every `origin/main` reference stopped resolving;
the second time `git push` failed with *"'origin' does not appear to be a git
repository"* â€” an error that reads like a credentials problem and is not one, and
which cost real time to diagnose as authentication twice.

**Cause: Orca's worktree management rewrites `.git/config`** to register its
80-odd worktree branches, and the rewrite drops the `[remote "origin"]` section.
The config is full of `autoSetupRemote = true` and eighty `[branch ...] base =
refs/remotes/origin/main` entries that Orca added.

Restoring the remote is one command. The expensive part was not knowing, and
twice assuming it was auth.

## Two dashboards, and the reason they are separate

The requirement is that the status stays current *while development is ongoing*,
which makes speed the design constraint rather than a nicety.

* `orchestration/AGENTS.md` â€” every dispatched agent, what it is doing, its tab,
  and whether its feature is on `main`. **Generated in about three seconds**,
  because it reads only what is on disk and already running. It measures an
  agent's state from its worktree's git state, not from its screen: a screen can
  look busy while nothing has been written, which is exactly the state that is
  hardest to tell from working.
* `orchestration/STATUS.md` â€” features live, routes, tests, the board, what is
  in flight, what remains. Its first version ran the whole suite on every
  invocation â€” three minutes unloaded, **over fifteen with two dozen agents
  competing** â€” and was killed mid-run, leaving a dashboard describing a state
  that had never been committed. *A dashboard costing a quarter of an hour to
  refresh is one nobody refreshes.* The test count is now the one number that may
  be supplied, and the dashboard says which, in the line itself.

Both assert the rendered file against what they just measured, because a
dashboard nobody checks goes stale â€” and a dashboard that reports success while
the thing it watches has failed is worse than none. That is the same defect this
project has now found in three separate tools.

## The author address was wrong in the repository, not in history

The address was corrected to `dilipnithyanandam@gmail.com`. The interesting part is
where the wrong one was living:

```
repo    user.email: ddilipnithyanandam@gmail.com
global  user.email: dilipnithyanandam@gmail.com
```

The global config was already right and the *repository* config was overriding it.
Any commit made from inside the repo got the wrong address and any commit made
outside it got the right one, which is the shape that produces a history where the
author changes halfway through for no visible reason. Fixing the global value alone
would have changed nothing.

**What the published history actually contained.** Asking GitHub rather than the
local clone, because a clone can be rewritten and still describe the old one:

| address | commits |
|---|---|
| `45650186+n-dilipkumar@users.noreply.github.com` | 170 |
| `dilipnithyanandam@gmail.com` | 144 |
| `dilip.nithyanandam@standards.org.au` | 39 |
| `ddilipnithyanandam@gmail.com` | 28 |
| `opencode@local` | 1 |
| `wf-010@local` | 1 |

**`origin/main` had 60 commits and not one of them on the requested address.** 45
were GitHub squash-merge attributions and 15 carried a third address entirely. The
audit had previously reported this as "squash-merge credits main's commits to the
same account the credentials belong to, not a second contributor" â€” which was
reasoning about what the rule *meant* rather than measuring what the repository
*contained*. It was measured this time, and the measurement disagreed.

### 177 commits carried a bot as a co-author

```
Co-authored-by: CommandCodeBot noreply@commandcode.ai
Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Co-Authored-By: OpenCode (Space Bunny Free) <noreply@opencode.ai>
```

A first scan reported **214** by matching the string anywhere in a commit body. That
count was wrong in a way worth recording, because the wrong number was much larger
than the right one and in the opposite direction of dangerous: a scan that treats
prose as a trailer flags commits whose message *describes* removing a bot trailer.

A trailer is a line that **starts** with the key. Under that rule: 177 commits with a
real bot trailer, and 35 with a self-referencing `Co-authored-by` naming the same
person. Both were removed â€” a self-co-author trailer is still a second author line,
and leaving it would have left `ddilipnithyanandam@gmail.com` in the history.

### Three no-op rewrites, and what each one looked like

`git filter-repo` failed three times, and **twice it failed while reporting success**:

1. **The mailmap had a UTF-8 BOM.** Windows PowerShell's `Set-Content -Encoding utf8`
   writes one; filter-repo rejects a mailmap whose first byte is `0xEF` and stops.
2. **The message callback had no `return`.** filter-repo continued and rewrote
   nothing.
3. **The mailmap parsed but did not match.** Every SHA moved, the message callback
   demonstrably ran, and all five identities were left exactly as they were. A
   mailmap only rewrites identities it matches, so a file it declines to apply is a
   no-op and not an error. It is indistinguishable from success unless you read the
   identities back out afterwards.

The fix was to stop matching and start assigning: a `--commit-callback` that sets
author and committer unconditionally, so there is nothing left to match and nothing
left to decline. The message also arrives as **bytes**, not `str` â€” filtering it as
text raises `TypeError` from inside a fast-import, which leaves the refs untouched
and writes a crash report, so it looks like something happened.

A bundle of `main` was taken first, and every branch's tree hash was compared before
and after. **A rewrite changes metadata and not one byte of content**, and that is
checkable rather than assumable:

```
old tree a2138d846b76e8eeacbccb6998cd9d26edde39a3
new tree a2138d846b76e8eeacbccb6998cd9d26edde39a3
```

### 86 agent worktrees were deliberately left alone

There are 87 worktrees, each on a branch, with agents working in them. Moving a
branch that a worktree has checked out leaves that worktree reporting every file as
modified. Their features are landed **by content, never by branch**, so their history
is scratch: 106 refs were rewritten, and the 86 worktree branches were left where
they are. Five of those five `feature/WF-*` branches *were* on the remote and *were*
rewritten, after checking each worktree for uncommitted work first (all five clean)
and re-syncing it to the rewritten tip afterwards.

### A scoped variable, read as a refspec

```powershell
git push --force origin "refs/heads/$b:refs/heads/$b"
```

PowerShell reads `$b:refs` as a **scoped variable** â€” a variable named `b` in the
`refs` scope. The refspec came out as `refs/heads//heads/feature/WF-001-...`. The
braces are not optional. Five branches "failed to push" for a reason that had nothing
to do with git.

### What is published now

Read back from GitHub, walking the whole of `main` rather than its first page:

```
commits walked: 60
distinct author/committer emails: 1
      120  dilipnithyanandam@gmail.com
distinct names: 1
      120  Dilip Nithyanandam
identities off the good one: 0
real trailer lines: 0
```

**120 = 60 commits Ã— author + committer.** Every one of them, on every branch, is the
same identity, with no machine and no second author anywhere. Three stale remote
branches (`features/set-4-clean-four`, `orchestration/status-and-audit`, and a
leftover `origin` self-reference) were deleted; none had an open PR.

## The seven CI failures were one bug: the tie-break was a random number

PR #50 came back with seven red tests across three features. They looked unrelated â€”
`assert 'a02' == 'a01'`, `'failed' != 'rolled_back'`, `'updated' != 'created'`,
`['fabrikam-1'...] == ['fabrikam-1'...]` â€” and the previous note recorded them as
three separate ordering dependencies. **They are one defect, and it is in the host.**

`AuditedDatabase.list()` ordered by the requested key and then broke ties on `id`.
`id` is `uuid4().hex`. Unique, so the order was *total*; random with respect to
insertion, so it was *meaningless*:

```python
def new_id(prefix: str = "") -> str:
    raw = uuid.uuid4().hex
```

`created_at` and `updated_at` come from `utcnow()`, which is
`isoformat(timespec="milliseconds")`. **I assumed for most of this investigation
that they were second-granularity, and that was wrong** â€” a probe printing the
stored values showed six rows a millisecond apart. That correction matters more
than the fix it interrupted, so the rest of this is stated correctly:

* **Ties are rare, and that is exactly why this bug survived.** A loop that
  writes rows usually gives each one a distinct millisecond and never ties, so a
  tie-break looks like dead code â€” and then two rows land in the same millisecond
  on a loaded machine and the order becomes a random number.
* **Ties are not exotic in this system.** Every batch write updates a whole
  chunk's rows inside one transaction, and a transaction that runs longer than a
  millisecond puts several rows in the same millisecond. That is the shape CI hit.
* **A test that merely writes rows in a loop tests nothing here.** My first
  version of these tests did exactly that, and it went green â€” because nothing
  tied. It then failed on a later run, at a different index, for the same reason.

So the four new tests **force the tie**: they write rows through `create` and then
stamp `created_at` and `updated_at` back to one identical value, which is the only
way to test a tie-break rather than hope for one. They assert against *the order the
rows were written in*, not against any order the ids imply, so they cannot pass by
luck. A fifth test asserts the rows really do tie, so a future change to the clock
that made them tautologies would be caught rather than quietly passing.

One of them pages 25 tied rows 10 at a time and asserts no row is returned twice
and none is lost â€” the property that a within-run-stable but arbitrary tie-break
still breaks, because the pages are separate queries.

**And all four were confirmed to fail with the old tie-break in place** before
being accepted. A regression test nobody has watched go red is a test that might
not be testing anything.

`crm_upsert`'s `_page` asks for `order_by="created_at", descending=False` and pages
with `offset`, which is the exact shape that reads the same row twice and skips
another.

This is why the tests *passed locally four times in a row* and failed on CI with
identical data: a two-row test is a coin flip, and four green runs in a row is 1-in-16,
not evidence. A previous fix had already replaced a bare `ORDER BY updated_at` with
`ORDER BY updated_at, id` â€” which converted "whatever the query plan produced" into
"a random but stable number". That is the **worst of both**: the failure stopped
reproducing locally and became a rare, machine-dependent flake.

The fix is `rowid`. `records` is declared `id TEXT PRIMARY KEY` with no
`WITHOUT ROWID`, so SQLite keeps an implicit `rowid` that is the true insertion
sequence â€” monotonic, independent of the query plan, identical on every machine:

```sql
ORDER BY created_at ASC, rowid ASC
```

`find()` had the same defect and got the same fix. There are **40 `list()` call sites
in the tree**, so this was never a three-feature problem; it was a latent flake in
every feature to be written, which is a hundred workflows' worth of it.

This is a `platform-change`: `db/audited.py` is a protected shared file, and the
guarantee the audit core makes about order is not honest without it.
