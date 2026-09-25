# Agent contract

Read this before writing any code in this repository. It encodes the rules the
Orchestrator and every sub-agent must follow.

## Models

| Role | Model | Notes |
|------|-------|-------|
| Orchestrator and all sub-agents | `opencode-go/opencode-go/space-bunny-free` | The only model permitted for implementation work. |
| Validation and decisions | `jev-latest` via `pi` / the Jev bridge | Never an LLM opinion. See "Validation" below. |

**Jev is not an OpenCode model.** It is a TypeSafe System One model reached over
HTTP. It returns typed `choice` / `score` / `noul` answers with calibrated
probabilities and cannot be spawned as a chat agent. This is confirmed by the
vendor documentation and by the OpenCode model catalogue, which contains no Jev
entry. Do not attempt to add it to `model:` in a sub-agent call.

## Validation

Every gate is a typed Jev judgment with a recorded probability. Use the client
in `tools/jev.py`; do not hand-roll HTTP calls or ask a language model whether
something is good enough.

```python
import sys; sys.path.insert(0, "tools")
from jev import Jev

client = Jev()
record = client.validate_design("WF-014", document_text)
print(record.summary())
if not record.passed:
    ...  # act on record.reason
```

Rules:

* Format every request as **decision -> evidence/state -> questions -> verification**.
* Use `gate="option"` for approval gates (only the named option passes).
* Use `gate="confidence"` when any offered option is legitimate, such as picking
  an approach from a solution pool. The selection is recorded in `selected`.
* A `verdict` of `uncertain` means Jev found the evidence too close to decide.
  Do not override it. Gather more evidence or escalate to a human.
* Every decision appends to `orchestration/decisions/jev-audit.jsonl`. Do not
  edit that file; it is the audit trail of the project's own decisions.
* Check the transport with `python tools/jev.py doctor`.

## Storage

**All SQLite access goes through `AuditedDatabase`.** Do not open a connection
to the database file directly, and do not add code paths that bypass the audit
log. The audit row is written in the same transaction as the change, which is
the guarantee the product is built on.

To decide an architecture question, ask Jev rather than guessing:

```python
decision = client.choose_approach(
    problem="...",
    options={"option_a": "...", "option_b": "..."},
    context={...},
)
print(decision.verdict, decision.selected, decision.reason)
```

## Schema flexibility is a hard requirement

Record payloads are arbitrary JSON stored in `records.data`. **Do not add a
migration or a typed column to store a team's field.** A team adding a field
must not need coordination with anyone. Filter with `find()` /
`?where=...`, which resolves dotted JSON paths through the dynamic index.

The only fixed vocabulary is the envelope: `id`, `collection`, `room_id`,
`revision`, `created_at`, `updated_at`, `deleted_at`.

## Stack

| Layer | Choice |
|-------|--------|
| Frontend | React + Tailwind CSS, built with Vite |
| Backend | Python + FastAPI |
| Database | SQLite, only via the audited wrapper |
| Docs | Docusaurus |

Design decisions live in `design-system/digital-sales-room/MASTER.md`, generated
by the `ui-ux-pro-max` skill. Follow it. In particular: no emoji as icons, 44px
minimum touch targets, visible focus rings, 4.5:1 text contrast, and respect
`prefers-reduced-motion`.

## Workflow for a change

1. Work in your own git worktree on a branch named `feature/<ticket>-<slug>`.
   Do not commit to `main` directly.
2. Write the code and **tests**. A feature without tests is not finished.
3. Run the backend suite: `cd backend && ../.venv/Scripts/python -m pytest`.
4. Have the change validated by Jev before merge.
5. Merge only when tests pass and the Jev gate returns `pass`.

## Commands

```sh
# backend tests
cd backend && ../.venv/Scripts/python -m pytest

# run the app (serves API and the built frontend together on :8000)
./.venv/Scripts/python -m uvicorn dsr.api:app --app-dir backend --host 127.0.0.1 --port 8000

# rebuild the frontend
cd frontend && npm run build

# verify what a browser would load, headlessly
./.venv/Scripts/python tools/verify_localhost.py

# check the Jev validator
./.venv/Scripts/python tools/jev.py doctor

# seed demo data into a fresh database
./.venv/Scripts/python backend/seed.py
```

## Research corpus

`docs/research/raw/` holds per-domain research. Every workflow there cites
primary sources and records what could **not** be sourced. When drawing on it,
keep that distinction: a workflow marked as unsourced is a hypothesis, not a
specification.
