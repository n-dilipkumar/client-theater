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
