# Program audit

A running record of key steps for the Digital Sales Room build. Append-only in
spirit: entries are added, not rewritten, so the trail shows how the project's
state was actually arrived at rather than a tidy after-the-fact summary.

Scope note: this file records *program* decisions and milestones. Two other
records exist and are not duplicated here:

* `orchestration/decisions/jev-audit.jsonl` Ã¢â‚¬â€ the append-only JSONL log of every
  typed Jev judgment. Written by `tools/jev.py`. Never hand-edited.
* `git log` Ã¢â‚¬â€ what changed in the code, and why, in the commit message.

---

## 2026-09-26 Ã¢â‚¬â€ Program start: infrastructure before scale

**Starting state, measured rather than assumed:**

| Fact | Value | How established |
|---|---|---|
| Researched workflows | 17 (WF-001Ã¢â‚¬Â¦WF-017) | `docs/research/digital-sales-room-workflows/wf/` |
| Raw research documents | 9 | `docs/research/raw/` |
| Features ported and merged | 1 (WF-006) | `git log` Ã¢â‚¬â€ `f854a79` |
| Backend tests | 145 passing (was 81) | `pytest -q` |
| Headless localhost checks | 22 passing | `tools/verify_localhost.py` |
| Orca worktrees | 22, **all** claiming `in-progress` | `orca worktree list` |
| Worktrees with a live agent | **0** | `orca worktree ps` Ã¢â‚¬â€ every one `live:0 pty:no` |
| Branches with unported code | 11 | `git rev-list --count origin/main..<branch>` |
| Branches empty (never built) | 6 | same, `ahead=0 files=0` |
| `n-dilipkumar/*` branches | 21, all empty and already merged | same |
| CI / `.github/` | **did not exist** | `dir .github` Ã¢â€ â€™ not found |
| Open PRs | 0 | `gh pr list` |

**Scope decision.** The brief asks for 10 sets of 10 workflows (100). Only 17 are
researched, and the project's own standard in `AGENTS.md` separates a *sourced*
workflow from a *hypothesis*. Resolved with the user: **port the 16 researched
workflows first, then research sets 2Ã¢â‚¬â€œ10 as new batches** with the same
primary-source discipline. Generating 83 unsourced workflows up front would have
been faster and would have violated the corpus's own provenance rule.

**Branching inconsistency found and fixed.** The Orca repo base ref was
normalised to `origin/main`. Worktrees from the failed run were split between base
`main` and `refs/remotes/origin/main`, so a new agent could branch from a local
`main` that had not been pushed and silently start nine commits behind Ã¢â‚¬â€ the
exact class of drift that makes a batch look broken.

---

## 2026-09-26 Ã¢â‚¬â€ CI: the feature contract is now enforced, not remembered

The plugin host lets N features merge without conflict, but only while no feature
edits a shared file. Until now that was a rule in a markdown file, checked by
whoever remembered to run `tools/check_feature_diff.py`. WF-006 proved the rule is
followable; nothing proved it would be *followed*. At 100 workflows, an unenforced
convention is a hope.

**PR #1 Ã¢â‚¬â€ `420ba54` Ã¢â‚¬â€ merged.** Adds `.github/workflows/ci.yml` (4 jobs) and
`.github/PULL_REQUEST_TEMPLATE.md`.

| Job | Runs on | Catches |
|---|---|---|
| `guard` | pull_request only | A feature branch editing a shared file |
| `backend` | all | The full pytest suite |
| `frontend` | all | A feature page that does not compile |
| `end-to-end` | all | A plugin that failed to import |

Two asymmetries, both deliberate:

* **The guard skips `main`.** Platform work legitimately edits shared files Ã¢â‚¬â€
  that is how a new extension point gets added (`44555cd`, the seed hook). A guard
  that must be overridden on every legitimate platform PR trains people to
  override it on illegitimate ones. Tests have no such escape hatch, because the
  audit guarantee is the product.
* **`guard` uses `fetch-depth: 0`.** The guard diffs against `origin/main`; a
  shallow clone of the PR branch alone would make it **silently pass**. A guard
  that passes when it cannot see is worse than no guard.

**PR #2 Ã¢â‚¬â€ probe, closed and deleted.** A guard that has only ever passed is
untested. A one-line edit to `backend/dsr/api.py` was pushed deliberately.

| Job | Result |
|---|---|
| `Feature contract (no shared files)` | **fail** (7s) |
| `Backend tests` | pass |
| `Frontend build` | pass |
| `Local host app (headless)` | pass |

This is the discrimination that matters. A shared-file edit compiles, passes every
test, and builds Ã¢â‚¬â€ so nothing except the guard would have caught it. CI log
confirmed the right reason: `FAIL: 1 shared file(s) edited Ã¢â‚¬Â¦ - backend/dsr/api.py`,
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

## 2026-09-26 Ã¢â‚¬â€ Five workflows were never empty

Jev was asked for the board-status policy and returned **`uncertain`** (0.52
confidence, 0.46 margin between options). `AGENTS.md` says an `uncertain` verdict
must not be overridden Ã¢â‚¬â€ gather more evidence or escalate. It was not overridden.

The evidence Jev was missing: whether removing a "dead" worktree would actually
destroy anything. A branch can be empty *at the ref level* while its *working
tree* holds real work, and `git rev-list origin/main..<branch>` Ã¢â‚¬â€ the only triage
that had been done Ã¢â‚¬â€ cannot see that.

It checked, and the answer reversed the plan:

**`orchestration/PORT-PLAN.md` records WF-001, WF-004, WF-012, WF-015 and WF-017 as
"no commits Ã¢â‚¬â€ re-run from scratch".** Their refs are empty. Their directories are
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

They remain **unported and untrusted**. Every one edits shared files Ã¢â‚¬â€ `api.py`,
`seed.py`, `ui.jsx`, `lib/api.js`, `App.jsx`, and in WF-001's case
`db/audited.py` Ã¢â‚¬â€ which is the exact pattern that stalled the previous run. WF-004
also modified `tools/jev.py`, the shared validator every decision in this project
goes through; that needs reading before any of it is trusted.

**The generalisable defect:** a ref-level metric was used to conclude a working
directory was empty. For a branch-based triage that is a reasonable proxy. For
uncommitted work it is blind, and the failure is silent. Any future triage of this
project must check `git status --porcelain` in the worktree, not only
`git rev-list`.

---

## 2026-09-26 Ã¢â‚¬â€ Dispatch set 1: three agents, three environment bugs

The mechanics were established by probing rather than assumed, and two of the
three probes found something that changed the design.

**`orca worktree create --agent opencode` does not work.** It fails with *"Selected
agent is disabled"* Ã¢â‚¬â€ only `pi` is enabled in Orca's settings, and that is a
desktop-app toggle with no CLI surface. The working path is
`orca terminal create --worktree <id> --command opencode`, which launches the real
TUI in a real tab; `terminal show` then reports `agentIdentity: opencode` and the
status bar reads **Build Ã‚Â· Space Bunny Free**. So the agent is a genuine OpenCode
agent on the model `AGENTS.md` requires, in a tab, with no settings change needed.
Verified end to end before relying on it: a probe agent was sent a prompt and
answered `TAB-AGENT-OK`.

**`terminal read --json` returns the screen under `result.terminal.tail`** as an
array of lines, not `result.text`. Reading the wrong key returns empty and looks
like a dead agent Ã¢â‚¬â€ which briefly read as a broken dispatch.

**A server restart interrupted the dispatch mid-run**, after the worktrees and
agent tabs were created but before the run finished. Reading each screen showed
WF-003 and WF-012 had received their briefs and were working, while WF-002 sat at
an empty prompt. Resuming meant sending WF-002's brief, not re-dispatching Ã¢â‚¬â€
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
   a venv Ã¢â‚¬â€ the negative instruction matters as much as the positive one, because
   the wandering is what raised the prompt.
2. **`opencode.json` used V1 syntax in a V2 config.** It had `permission` with a
   `bash` sub-object; V2 wants a `permissions` array of `{action, resource,
   effect}` with actions like `shell`, and explicitly warns that V1's
   `permission`/`bash`/`task` are not the V2 names. The intended policy Ã¢â‚¬â€ allow
   `git push` but ask, ask on `gh pr merge` Ã¢â‚¬â€ **was not in force at all**.
   Rewritten as V2 rules plus the missing piece: an `external_directory` allow for
   this repo's two directories. That action is what OpenCode checks before any read
   outside the active worktree, and it defaults to `ask`, which is exactly why
   every agent that strayed got a modal prompt nobody could answer for it.

Limitation recorded honestly: the config fix only reaches sessions started after
it merged, so the three running agents were redirected by hand.

---

## 2026-09-26 Ã¢â‚¬â€ The four "collisions" were not collisions

`PORT-PLAN.md` blocked WF-007, WF-010, WF-009 and WF-011 on the claim that
*"WF-007 and WF-010 genuinely both own /api/library, and WF-009 and WF-011 both
define a publishing router Ã¢â‚¬â€ the host will refuse the second regardless."*

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

The claim came from comparing **prefixes** Ã¢â‚¬â€ the exact check `fd544e2` replaced
with route-level comparison, because prefix comparison both wrongly blocked
WF-003/WF-005 and missed genuine dead-code shadowing of core routes. Judging these
pairs by prefix repeated the mistake that fix exists to stop repeating.

Jev was asked whether they were duplicate workflows and answered
**`port_all_four_as_is` at 0.79**. Their shared filename
`backend/dsr/publishing.py` also stops being a conflict: each becomes its own module
under `backend/dsr/features/`. Both features keep the `/api/library` and
`/api/publishing` prefixes, which is safe precisely *because* their concrete paths
differ Ã¢â‚¬â€ the case the host was built for.

**Still held back: WF-005**, whose branch edits `db/audited.py` and `store.py`, the
audit guarantee itself. That needs a human read before anything carries it across,
and an agent port is the wrong instrument for it.

---

## 2026-09-27 Ã¢â‚¬â€ Three agent-dispatched ports merged: the host works

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
conflicted on almost every merge Ã¢â‚¬â€ the exact class of problem the plugin host
exists to remove, reappearing in a file nobody had thought of as contended. The
guard missed it because no feature is *supposed* to edit that file by hand; the
tool writes it as a side effect of doing its job.

Fixed with `merge=union` in `.gitattributes` (`27ebd24`). An append-only log has
no notion of a conflicting line, only of rows that were never merged. `union`
concatenates both sides, loses no row, invents none, and each row carries its own
`audit_id` and `decided_at` so the merged file stays a complete record whatever
order they land in.

### Two tooling defects worth recording

**The agent monitor reported a stopped agent as working** Ã¢â‚¬â€ twice, for an agent
that had been idle holding a finished port. The cause was ordering: an ended
OpenCode turn leaves `Ã¢â‚¬Â¦ Ã‚Â· interrupted` in the scrollback while the status bar
*underneath* still shows the spinner strip, and the spinner test ran first. The
spinner glyphs are the status bar, not evidence of activity. Found because the tool
disagreed with a direct screen read, which is the argument for having both a
summary tool and the ability to read the raw thing it summarises.

**`merge_ports.py` began with `git checkout main`**, so work authored on a PR
branch was silently moved onto `main` and the branch was left empty. The merges
kept *succeeding* Ã¢â‚¬â€ three clean merges, no conflicts Ã¢â‚¬â€ and only the push reported
`Everything up-to-date` while local was seven commits ahead. A reflog showed the
branch had been created once and never moved. Three attempts to fix it with the
edit tool failed silently because the shell restarted and reverted the working
copy; each attempt presented identically, so nothing distinguished "not yet tried"
from "tried and lost". The repair is now written by a script that reads the file
back and refuses to claim success unless the line is actually gone Ã¢â‚¬â€ the artifact
is the evidence, not the intention.

### What the agents got right

Each worked from its committed brief and each produced a feature module, a feature
folder, and a test file. No exceptions, and none touched a shared file. Notably
each threaded `source=` through its own domain layer so audit rows name the route
that actually served the write Ã¢â‚¬â€ the defect the WF-006 rename exposed, which would
otherwise have been replicated three more times.

### Verification, before merge rather than after

Each port was checked by running the checks, not by reading the agent's claim Ã¢â‚¬â€ an
agent reporting "246 passed" is asserting a number. Suite in each agent's own
worktree with the feature mounted (202 / 246 / 217), the host registry queried
through `/api/features` to confirm the feature *loads* rather than being silently
skipped, and the guard run per branch.

Merged as **PR #10 Ã¢â€ â€™ `8920d2f`**, all four CI jobs green, 375 tests, 57 frontend
modules, all 22 headless checks passing with four features mounted.

---

## 2026-09-27 Ã¢â‚¬â€ Set 2 dispatched; a backtick in a prompt ate two briefs

WF-007, WF-009, WF-010 and WF-011 dispatched Ã¢â‚¬â€ the four held back as collisions
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
the identical hazard and happened to survive Ã¢â‚¬â€ nothing about the two cases differed
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
dispatch where two of the four deliberately share an API prefix Ã¢â‚¬â€ WF-007 and
WF-010 under `/api/library`, WF-009 and WF-011 under `/api/publishing`. Safe
precisely because their concrete paths differ: the case the host exists to allow,
and the case `PORT-PLAN.md` wrongly flagged as a collision.

Board corrected to match: 4 `in-progress` with live agents, 8 `completed`, 16
`todo`, zero stale cards.

---

## 2026-09-27 Ã¢â‚¬â€ Set 2 merged; a brief of mine broke a feature

**`fb30903`. Seven features, 81 routes, 604 tests, 0 failed.**

| Workflow | Prefix | Routes | Suite in its worktree |
|---|---|---|---|
| WF-007 | `/api/library` | 12 | 437 passed |
| WF-009 | `/api/publishing` | 13 | 445 passed |
| WF-011 | `/api/publishing` | 10 | 472 passed |

All three merged cleanly, one at a time, with the suite and registry re-checked
after each. **WF-009 and WF-011 both mount `/api/publishing` and both load
together** Ã¢â‚¬â€ the case `PORT-PLAN.md` blocked as "researched twice" on a *prefix*
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
to parse a file upload Ã¢â‚¬â€ which is precisely what document ingest is.

WF-007's **original branch** had already declared it, commented *"Required by
FastAPI to parse the multipart ingest request (WF-007)."* The port reversed a
decision the original author had made correctly, because I told it to. Fixed in
`580dca6`, and the brief template corrected to say that a dependency a feature
cannot work without is that feature's own requirement, not a coordination
problem.

**Blast radius worth knowing:** while WF-007 was unloaded,
`test_the_feature_did_not_collide_with_anything` in **WF-003's** suite also
failed Ã¢â‚¬â€ a different feature with nothing to do with uploads. It passed again once
WF-007 loaded. So *"the suite is green"* is a statement about which features
loaded at least as much as about the code, and a **silently skipped feature is
not a green run**.

### A false positive worth recording

The set-3 dispatch reported `mangled=True` for WF-016. Reading the screen showed
the agent had itself piped pytest into `| tail -n 20` Ã¢â‚¬â€ a Unix idiom unavailable in
cmd Ã¢â‚¬â€ and had recovered unaided. The detector matched any *"not recognized as an
internal or external command"*, which fires on an agent's own typo as well as on a
mangled prompt. Three tools narrowed to match only the word that starts the
prompt. A check that cries wolf is worse than no check, because it trains you to
ignore it.

### A mistake of my own

Closing the finished set-2 tabs, I read the handle map for the wrong batch and
closed the **set-3** agents instead. No work was lost Ã¢â‚¬â€ their worktrees were
intact, WF-016 still holding its 10 uncommitted files Ã¢â‚¬â€ but three live agents lost
their terminals. Relaunched with a prompt that says so explicitly, so a resumed
agent does not read the new tab as a rejection of its work.

---

## 2026-09-27 Ã¢â‚¬â€ The guard was a lamp, not a barrier

The seeder fix (`a13b1bb`) edits `backend/seed.py`, a shared file. The guard
fired with exactly the right message Ã¢â‚¬â€

    FAIL: 1 shared file(s) edited.
      - backend/seed.py

Ã¢â‚¬â€ **and the PR merged anyway.**

That PR was legitimate platform work and *should* have been allowed. The problem
is not that it went through. The problem is that **nothing decided that.** The
guard reported accurately, a merge happened regardless, and the judgement between
a feature and platform work fell to whoever typed `gh pr merge` Ã¢â‚¬â€ which is exactly
the judgement the CI job exists to make.

Measured rather than guessed: `gh pr merge` does not consult CI status, and the
repository had **no branch protection and no rulesets at all**.

### Fixed, and proven

Branch protection on `main`: all four checks required, `strict: true`,
`enforce_admins: true` (so an admin cannot bypass it Ã¢â‚¬â€ which is how the seeder PR
got through), force-push disabled.

The exemption is a **label**, not a CI flag, so the decision is recorded where a
reviewer sees it rather than typed into an invocation nobody reads.

Both halves verified with real PRs, because the earlier probe had only shown the
job going red and the actual failure was a red job that stopped nothing:

| Probe | Expected | Result |
|---|---|---|
| Shared-file edit, no label | merge **refused** | `the base branch policy prohibits the merge` |
| Shared-file edit, `platform-change` label | guard Ã¢â€ â€™ NOTICE, check passes | passed; log shows the labelled path |

The second probe's end-to-end job independently reported **`OK: 8 feature(s)
loaded, 0 failed`**.

### Also fixed: the seeder could not seed into a new directory

`sqlite3.connect` does not create intermediate directories, so pointing
`DSR_DB_PATH` at a path under a directory that did not exist died with *"unable to
open database file"* Ã¢â‚¬â€ naming neither the file nor the directory. It bit this
project during a routine check, and the verification script **filtered the
seeder's own output**, so the traceback was discarded and only the symptom Ã¢â‚¬â€ an
empty demo dataset Ã¢â‚¬â€ was left to interpret. A script that filters the output of
the thing it is verifying will hide exactly the failure it exists to catch.

Fixed with two regression tests, the first confirmed to fail with the fix
reverted. That test asserts the seeder **wrote rooms**, not merely that it exited
0 Ã¢â‚¬â€ an exit-code-only test would pass on a seeder that wrote nothing, which is
precisely the failure that was nearly mistaken for a broken app.

---

## 2026-09-27 Ã¢â‚¬â€ Nine features live, 108 routes, 895 tests

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
context)` hook actually works rather than merely existing Ã¢â‚¬â€ nine independent
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

Two prefixes are now shared by two features each Ã¢â‚¬â€ `/api/library` (WF-007 with
WF-008 pending) and `/api/publishing` (WF-009, WF-011) Ã¢â‚¬â€ and both pairs load
together. That was `PORT-PLAN.md`'s stated blocker, resolved by measuring the
rule the host actually enforces rather than the prefix it was checking.

Note the seeding of *interesting* states rather than only happy paths: a room
archived, a generated room declined, an approval workflow left pending, a webhook
delivery retried and one failed, a run unresolved. Demo data that only contains
success teaches a reviewer nothing about the feature.

**WF-008 remains in progress** and is deliberately unmerged.

---

## 2026-09-27 Ã¢â‚¬â€ Ten features, and the prefix claim fully retired

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
prefixes Ã¢â‚¬â€ the check `fd544e2` replaced precisely because it both wrongly
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
fast-forwarded, so its own commits stay reachable and `ahead` never reaches 0 Ã¢â‚¬â€
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

- **WF-005 and WF-014** edit `db/audited.py` **and** `store.py` Ã¢â‚¬â€ the audit
  guarantee itself. Held for a human read of what they changed to the core.
- **WF-001, WF-004, WF-015, WF-017** were rescued from uncommitted working trees,
  so have never been executed. WF-004 also modified `tools/jev.py`.
- **WF-010** has a completed port in its worktree, verified, awaiting merge.
- **The duplicate WF-008 port** needs a decision about which implementation wins.

**Beyond the 17:** the brief asks for 100 workflows across 10 sets. Sets 1Ã¢â‚¬â€œ3
covered 10 of the 17 researched. The remaining 90 do not exist yet and would
need new research to the standard the corpus holds Ã¢â‚¬â€ an explicitly-labelled
hypothesis is permitted by `AGENTS.md`, but that is a scope decision, not an
implementation detail.

---

## 2026-09-27 Ã¢â‚¬â€ Set 4: the collision decided before dispatch, and a fourth wrong board test

**`77fe14f` Ã¢â‚¬â€ 11 features, 116 routes, 1211 tests, 0 failed features.**

WF-010 merged, and `/api/library` now carries **three** features at once Ã¢â‚¬â€ WF-007
(12 routes), WF-008 (8), WF-010 (8) Ã¢â‚¬â€ all loading together. That is the furthest
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
feature** Ã¢â‚¬â€ because that is what a no-op merge looks like from the outside.

### A collision found by measuring, then decided by Jev

WF-004 and WF-015 both add `backend/dsr/access.py` and both serve
`GET /api/rooms/{room_id}/access`.

Jev's first ask returned **`uncertain`** Ã¢â‚¬â€ 0.68 against 0.75, `is_confident` 0.43,
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
`roles.py` and WF-015 keeps `access.py` Ã¢â‚¬â€ and both briefs say so explicitly, in
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
| `origin/main --contains <tip>` | demoted **all 11** Ã¢â‚¬â€ see below |
| **are this worktree's files on main?** | correct |

The third is the instructive one. PRs here are **squash**-merged, so a port's own
commit SHA is never an ancestor of `main` Ã¢â‚¬â€ verified: the WF-007 tip `9b931c49` is
contained in *no* remote branch. Commit identity cannot answer this in either
direction, and the same class of bug bit `merge_ports.py` too.

The content test separates the two WF-008 worktrees exactly, which nothing else
could: both write `wf008_external_sync.py` at the same path, so path existence and
commit reachability are **identical** for them. The shipped one contributes 0
files absent from `main`; the duplicate contributes **7** Ã¢â‚¬â€ its own
`backend/dsr/library/`, where `main` has `external_library/`. It is now `todo`
with a comment saying a human has to choose, rather than `completed` claiming work
shipped that never did.

### WF-017 was blocked on a permission dialog, and the boundary held

It used `%TEMP%` as a scratch directory and hit *Access external directory*.

`orca terminal send` has **no key option** Ã¢â‚¬â€ only `--text` and `--enter` Ã¢â‚¬â€ and a
tab sent as text is not the bytes a TUI reads, so the first attempt did nothing.
Sending the escape sequences a TUI actually reads (right arrow Ãƒâ€”2) reached
`Reject`.

`Always allow` was one keystroke away and would have granted a **standing**
permission to `%TEMP%\*` for this project, to save one agent one keystroke. The
screen shows no highlight, so which option was taken cannot be read from it Ã¢â‚¬â€
`opencode.json` can, and Temp was verified still absent afterwards. **The screen
cannot tell you what a dialog did; the config can.**

### Two agents, two different interventions

WF-017 was **blocked**, so it was unblocked and told where scratch files belong.

WF-004 was **mid-port with correct content and a wrong filename**, so it was
nudged, not restarted Ã¢â‚¬â€ restarting resets an agent's context and loses work where a
note costs one round trip. It did the rename within a minute.

---

## 2026-09-27 Ã¢â‚¬â€ The corpus was always 138. I was counting branches.

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

WF-001Ã¢â‚¬â€œWF-017 were built as branches. **WF-018 onward have a finished
specification and no code at all** Ã¢â‚¬â€ no branch, no feature module, no tests.

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
`- **user_flow**:` Ã¢â‚¬â€ colon outside the bold Ã¢â‚¬â€ so a pattern for
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
  cannot tick.** `Verified in localhost browser` Ã¢â‚¬â€ no browser is attached to this
  session Ã¢â‚¬â€ is reported as not verified rather than claimed.

### Seven prompts that went nowhere

The first build batch was ten agents, and **seven never received their brief.**
`send_ok=True` on all ten; the text was typed at a TUI still drawing its splash, so
it went nowhere. Those seven sat on the startup screen with an empty `Ask anythingÃ¢â‚¬Â¦`
box for several minutes.

The cause is a wait that was long enough for three agents and not for ten: the
dispatch creates each terminal, sleeps 9s, waits for `tui-idle`, and sends. Three
agents give the TUI time to start. Ten, created while the machine is busy running
the earlier ones, do not Ã¢â‚¬â€ and **`tui-idle` is satisfied by a splash screen that
has not started**, because a screen that has not begun is not busy.

The repair waits for the prompt box to actually be drawn rather than for a TUI
condition, confirms the brief is absent before sending so a second run cannot
duplicate a prompt that did land, and then re-reads the screens because
`accepted` comes back empty on a successful send in this Orca build and proves
nothing. All ten are working.

**The first read-back said `active=False` for seven of them and I did not trust
it** Ã¢â‚¬â€ not because the check was clever but because seven simultaneous dead agents
is a much less likely explanation than seven reads that returned nothing, which is
exactly what has happened in this session before when the wrong JSON key was read.
Opening two of them showed the splash screen immediately.

---

## 2026-09-27 Ã¢â‚¬â€ Twenty-four features, and three defects the tooling was built to find

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
documented guarantee requires* Ã¢â‚¬â€ not domain concepts reaching in from a feature:

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
the host deliberately as a labelled `platform-change` Ã¢â‚¬â€ **PR #41, merged, all
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
  earlier two capabilities were silently dropped** Ã¢â‚¬â€ with plausible line counts
  (787, 830, 962), because each result was a superset of `main` alone. The merge is
  cumulative now, and every capability is **asserted present in the file
  afterwards** rather than inferred from a clean exit code. *A clean merge that
  drops a capability is still a clean merge.*

The 23 tests travelled with the code rather than being retyped, because retyping
is a way to end up testing something subtly different from what shipped.

### The two collisions that were waiting behind the hold

**`rooms.py` Ã¢â‚¬â€ WF-001 and WF-005 both add it.** Neither exists on `main`, so this
was never a merge, only a naming decision. Measured: WF-001's is 438 lines with
**82** mentions of templates and **2** of lifecycle; WF-005's is 640 lines with
**84** of lifecycle and **0** of templates. Neither is a superset, so the
more-general-takes-the-neutral-name rule did not decide it.

**WF-005 keeps `rooms.py`.** Its module defines the room *state machine* Ã¢â‚¬â€
`RoomWorkflow`, `status_of`, `available_actions`, `capabilities_for`, `Principal`
Ã¢â‚¬â€ which is what the neutral name means, and WF-001's `create_room` needs somewhere
to put a room's status. WF-001 becomes `room_templates.py`, which is exactly what
it is. A module named `rooms.py` that can only create rooms from templates
misleads the next reader, and **the archive path is the one that has to be
findable when a room comes back.** Jev: **1.00**.

**`access.py` Ã¢â‚¬â€ WF-014 adds it; `main` already has it from WF-015.** **WF-015
keeps it.** Renaming shipped, tested code to accommodate an unbuilt workflow is
backwards. WF-014 becomes `access_controls.py` Ã¢â‚¬â€ which is the name its own design
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
  `library/__init__.py` instead of `main`'s 931-line `library.py` Ã¢â‚¬â€ which WF-007
  imports Ã¢â‚¬â€ **and the import succeeds.** Nothing reports it. A directory and a
  module of the same name cannot coexist in one package, and the failure mode is
  not an error but the wrong code quietly running.
- **`main` carries more tests**: 71 in `test_wf008.py` plus 45 in
  `test_wf008_http.py`, against the branch's 50.
- **The one file `main` lacks is redundant.** The branch's `test_library_sync.py`
  has 58 tests of the sync engine directly. Run against `main`'s implementation
  in a scratch copy, **all 58 pass.** `main` already satisfies them.

So: keep `main`'s, salvage nothing, and **delete the worktree last** Ã¢â‚¬â€ it is the
one irreversible step, and until the tests and frontend were assessed on their own
merits there was a real chance the branch held something worth keeping.

### A misreading caught in the same pass

An earlier comparison in this same session reported the branch's tests and
frontend as *"absent from `main`"*. **That was wrong.** In `git diff
origin/main...HEAD`, the `A` status means added in the HEAD *relative to a
47-commit-old merge base* Ã¢â‚¬â€ not absent from `main`, which has since landed its own
versions of both. A 3-dot diff reports the base's state, and reading it as
`main`'s produces a confident, wrong inventory. It nearly sent a decision the
wrong way.

## Two tooling defects, both of which hid work rather than causing it

**`dispatch_build_batch.py` overwrote the agent handle log.** It wrote the current
batch only, so every dispatch erased the record of the agents already running.
`agent_watch` consequently reported 13 dispatched when 20 were in flight Ã¢â‚¬â€ which is
why a batch of ten looked as though it had never started. It merges by ticket now.
It earned its place immediately: a server restart killed the shell mid-dispatch of
WF-048..055, and **all eight were still on record** even though the output was
lost.

**`brief_title()` matched only `Build brief:`.** The three newly-lifted briefs say
`Port brief:`, so all three were **skipped silently** Ã¢â‚¬â€ a dispatcher reporting
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
duplicate adjudicated and closed out. **21 agents in flight** Ã¢â‚¬â€ WF-001, WF-005,
WF-014 (the lifted ports) plus WF-038..047 and WF-048..055 (builds).

`orchestration/STATUS.md` is generated and self-asserting: every number in it is
measured, and the generator checks the rendered file against what it just
measured, because a dashboard nobody checks is how a dashboard goes stale.

---

## An order that was never a promise: two defects behind one CI failure

**PR #44, WF-030 and WF-032. Suite 4,565 â†’ 4,568.**

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
in the same second *tie* â€” and the order among tied rows is whatever SQLite's
query plan produces. Locally the plan returned insertion order. On the runner it
did not.

Ties are not an edge case. **Any burst of writes ties**, and this suite creates
records in tight loops by design. Every caller that reverses a result, or
documents an order it does not itself impose, inherits a coin flip â€” and several
do, because *"list() is newest first"* reads like a promise and is not one.

Both now order by `<key> <dir>, id <dir>`. `id` is unique, so the order is total.
Rows that do not tie keep exactly the order they always had; rows that do are now
deterministic instead of arbitrary. **This is a change to `db/audited.py`, so it
carries the `platform-change` label** â€” a deliberate change to a shared host file,
recorded where a reviewer sees it.

### The inner defect: reversing is not sorting

`crm_workflows.engine.activity()` documented *"the order they happened"*, then
implemented it as `rows.reverse()` over `store.list()`, which orders by
`updated_at` â€” when the row was **written**, not when the event **happened**. The
two agree only when events arrive chronologically, and events arrive in whatever
order they happen: a backfill, a replay, a slow webhook. Its docstring also
promised the match count *"would not depend on the sort"* while depending on
exactly that.

It now sorts by `occurred_at`, with `id` as a stable tie-break.

### The same mistake, made twice

This is the second time this exact error has been made in this repository. The
first was `deliveries()` asserting *"newest first"* over a `find()` that **takes
no ordering argument at all** â€” recorded earlier in this log as a test that was
"passing for the wrong reason". Both surface identically: a test that passes
locally and fails on CI. Both are one mistake â€” **treating an order you did not
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
  **newest first** â€” the case a `.reverse()` implementation still gets wrong once
  the tie-break is fixed.

## A merge that reported success without merging

`merge_ports.py` merged **WF-001, which had zero commits** â€” the agent was still
writing. Its staging ref was therefore identical to `main`, `git merge` exited `0`
without changing anything, and the script printed:

    merged cleanly
    suite after merge : 4012 passed, 0 failed

**Identical to the baseline.** A no-op reported as a clean merge, at the cost of a
full baseline suite and a full post-merge suite spent on nothing.

The file's own docstring says it was rewritten to stop a no-op reporting success.
It had stopped *one* no-op â€” re-merging a feature already on `main` â€” and left the
other wide open: merging something that was never there. Three guards now:

1. `load_ports()` excludes zero-commit workflows and **lists them with their
   uncommitted file counts**, so being left out is visible rather than silent.
2. An empty staging ref is refused *before* the merge, naming the reason.
3. `HEAD` is compared before and after. A merge that exits `0` without moving
   `HEAD` is refused, and `merged cleanly` now states the commit count and both
   short SHAs.

**And the new guard was itself broken on first use** â€” it unpacked `git()`'s return
as three values when it returns two, so the merge loop raised `ValueError` *after*
the baseline suite had run. Five minutes to learn the loop could not start.
`--preflight` now walks every step except the suite and the merge in about a
minute, and would have caught it immediately. **A safety check that has never been
exercised is not a safety check**, and the cheapest way to exercise one is a mode
that skips the expensive part.

The log reader had the mirror-image fault: it filtered tracebacks out, so that
crash printed as a run in which nothing had happened. **A reader that discards the
most important line in the file is a reader that reports success during a
failure** â€” the same defect one layer up.

## Out of memory, and what it was actually caused by

The merge run stopped on `fatal: Out of memory, calloc failed` during a
`git fetch`, with **0.58 GB free of 31.6 GB**.

The obvious hypothesis was twenty-four agents. **It was wrong.** Measurement
found seven `opencode` processes, of which **one held 14.5 GB resident and 53 GB
of private commit** â€” a leak, not load, and it accounted for 85% of all agent
memory. One runaway process, not the agent count, starved the merge.

It was not killed. Every candidate's parent is `cmd.exe` â€” including this
session's own â€” so the evidence did not distinguish an agent from the session
doing the work, and **the cost of guessing wrong is ending the session while the
cost of leaving it is only slowness.** Measured, then declined to act on an
ambiguous reading: which is the correct order of operations when one option is
reversible and the other is not.
