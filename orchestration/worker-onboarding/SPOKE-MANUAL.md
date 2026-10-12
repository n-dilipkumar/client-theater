# Spoke operating manual

You are running as an autonomous worker on a Windows **spoke** machine
(pc-mini or pc-high), dispatched by the **hub** (pc-max) through Orca ADE.
This file is the contract. Read it before your first command.

## Your environment, in short

| Thing | Where it is on this spoke |
|---|---|
| your worktree | the path Orca gave you; always a **separate git worktree** on its own branch |
| python + deps | `C:\Users\Dilip\orca-envs\venv` (shared, already linked into your worktree as `.venv`) |
| node deps | `C:\Users\Dilip\orca-envs\node_modules` (shared, linked as `frontend\node_modules`) |
| jev bridge | **on the hub**, at `http://100.71.15.24:8777/v1` - you call it, you do not host it |
| jev client | `tools\jev.py` in your worktree |
| your real name | `host` - report it, so the hub knows which spoke answered |

Your worktree already has `.venv` and `frontend\node_modules` as directory
junctions. If you ever create a new worktree, link it first:

```powershell
powershell -File C:\Users\Dilip\orca-envs\bin\link-worktree-env.ps1 -Worktree <path>
```

That takes **under a second**. Do not install dependencies per worktree.

## Commands that just work

These are the AGENTS.md commands, verbatim. They need no setup:

```powershell
cd backend
..\.venv\Scripts\python.exe -m pytest          # the mandated suite
..\.venv\Scripts\python.exe -m ruff check .
cd frontend
npm test
```

Measured on pc-mini: full backend suite = **133 tests, 458 s**, green.

## Jev is your decision gate

Jev is a TypeSafe System One model. It returns typed answers - `choice`,
`score`, `noul` - with calibrated probabilities. It is **not** a chat model
and can never be spawned as one.

Check the transport is alive (do this once per session):

```powershell
..\.venv\Scripts\python.exe ..\tools\jev.py doctor
```

Use it through the client. Do not hand-roll HTTP and do not ask a language
model whether something is good enough:

```python
import sys; sys.path.insert(0, "tools")
from jev import Jev

client = Jev()
record = client.validate_design("WF-014", document_text)
print(record.summary())
if not record.passed:
    ...  # act on record.reason
```

For an architecture question:

```python
decision = client.choose_approach(
    problem="...",
    options={"option_a": "...", "option_b": "..."},
    context={...},
)
print(decision.verdict, decision.selected, decision.reason)
```

Rules that matter:

- Format every request as **decision -> evidence/state -> questions -> verification**.
- `gate="option"` is an approval gate; only the named option passes.
- `gate="confidence"` picks from a pool; the choice is recorded in `selected`.
- A verdict of `uncertain` is **not** permission to proceed. Gather more
  evidence or escalate.
- Every decision appends to `orchestration/decisions/jev-audit.jsonl`.
  **Never edit that file.** It is the project's own audit trail.
- The TypeSafe API key lives only on the hub. You never need it, and you
  must never ask for it.

## Decide alone, escalate rarely

The hub is a bottleneck, not a supervisor to check in with. A round trip
costs the hub ~0.4-0.9 s **and** stops you until it answers. Independence is
the point of having more than one machine.

**You decide, alone, without asking anyone:**

- implementation approach inside your own worktree and branch
- which tests to run, and how to fix a failing one
- reading and reviewing `git status` / diff of your own branch
- the contents of the feature files you are asked to add
- ordinary lint, formatting, and import cleanup
- how to structure your own commits on your own branch
- a Jev `choice` or `score` gate - that is what it is for

**You stop and escalate, once, in a single message, when:**

- worktree or branch isolation is broken (someone else is in your checkout)
- the environment cannot be repaired locally
- the fix would require editing a **shared** file: `dsr/api.py`,
  `dsr/deps.py`, `store.py`, `db/audited.py`, `App.jsx`, `lib/api.js`,
  `components/ui.jsx`, `index.css`, or `orchestration/` board state
- a Jev verdict comes back `uncertain`
- you believe the spec or the contract is wrong
- you would need to reset, clean, or force-overwrite git state

An escalation is a **single bounded report**, not a conversation:

```
SPOKE: <hostname>
TASK: <task id>
BLOCKER: <one sentence>
EVIDENCE: <the command you ran and its output>
WHAT I TRIED: <2-3 bullets>
WHAT I NEED: <the specific decision only you can make>
```

Batch questions. If you have three questions, send one message with three
questions. Never drip-feed one question at a time.

## Rules you never break

- **Never** run `git reset --hard`, `git clean`, `git checkout --`, or any
  destructive history rewrite.
- **Never** edit a shared file. A hundred features land at once; editing a
  shared file is how branches collide and none of them merge.
- **Never** add a migration or a typed column for a team's field. Record
  payloads are arbitrary JSON in `records.data`.
- **Never** commit to `main`. You are on a feature branch; commit there.
- **Never** push unless your task says to.
- **Never** edit `orchestration/decisions/jev-audit.jsonl`.
- **Never** ask for, copy, or store the TypeSafe API key.

## Reporting back

Your final message is what the hub reads. Make it count:

- **Report evidence, not vibes.** Paste the command and its output.
- **State plainly whether tests ran, and their result.** "Tests not run" is
  an acceptable answer; implying otherwise is not.
- **Flag anything you noticed but did not fix**, especially pre-existing
  problems, so the hub can route them. Both spokes independently spotted a
  stale `TASK.md` and a missing venv this way - that is the system working.
- **Scratch files go outside the worktree**, under
  `C:\Users\Dilip\AppData\Local\Temp\opencode\`. Your worktree should end the
  task byte-identical unless you were told to change something.

## Quick self-check before you finish

```powershell
cd <your-worktree>
git status --short --branch     # expect only your intended changes
```

If that shows files you did not intend to touch, revert them before
reporting done.