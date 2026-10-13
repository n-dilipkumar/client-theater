# Worker briefs are dispatched, not read from this file

This file used to hold a full per-ticket brief for one agent (WF-106, GitHub
issue 137). That ticket merged, and the brief went stale while this file stayed
on `main`, so it described a deleted worktree and an already-merged ticket.

Current practice: each worker receives its brief from the Orca orchestrator as
part of `worker-start`. The brief names the worktree, the issue, the owned
paths, the commands, the Jev gate, and the reporting cadence. Nothing in the
repository is read as a task assignment.

If you are an agent and this file was cited as your brief, stop and re-read the
preamble you were dispatched with: it is authoritative. The programme board is
`orchestration/WORKFLOW-BOARD.md`; live state is `/api/features`.
