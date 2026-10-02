AGENT HANDLES — CI Test Reduction Programme

Run: run_6e0bc978dd0e
Status: all four agents live, all four reporting heartbeats.

| Agent | Worktree | Branch | Terminal handle | Owns |
|---|---|---|---|---|
| harness | C:/Users/Dilip/orca/workspaces/client-theater/perf-test-harness | perf-test-harness | term_e2755632-1410-479f-9ab6-129bd7693c0c | backend/tests/conftest.py, pytest config |
| core | C:/Users/Dilip/orca/workspaces/client-theater/perf-tests-core | perf-tests-core | term_350fd287-1759-40dc-9cc8-2e21cc9b170d | 17 core test files |
| features-a | C:/Users/Dilip/orca/workspaces/client-theater/perf-tests-features-a | perf-tests-features-a | term_5fbc0ed0-8e93-44a7-9397-8a084be65ad5 | test_wf001..wf030 |
| features-b | C:/Users/Dilip/orca/workspaces/client-theater/perf-tests-features-b | perf-tests-features-b | term_866f33ca-c921-4153-8ee7-d488a7293222 | test_wf032..wf079, frontend |

Coordinator: term_e406831f-d12a-46fe-b7dd-7c13c32a1a51

## Python path. Use this one. It has no spaces.

    C:\Users\Dilip\dsrvenv\Scripts\python.exe

That is a junction to C:\Users\Dilip\Documents\dummy repo\client-theater\.venv.
The real folder name contains a space. cmd.exe splits a path on a space, so the
real path does not survive a cmd prompt. Do not use the real path in a command.

Verify it before reporting the environment blocked:

    C:\Users\Dilip\dsrvenv\Scripts\python.exe -m pytest --version

## Heartbeat command. This exact form works.

    orca orchestration send --subject "HEARTBEAT" --to run:run_6e0bc978dd0e --type heartbeat --body "AGENT: core | ELAPSED: 31 | DONE: x | WORKING: y | BLOCKED: NONE | NEXT: z"

Rules that the agents proved the hard way:

- The body must be ONE line. cmd cannot pass a multi-line body.
- Use the pipe character to separate fields. Do not use newlines.
- Do not use backticks in the body. cmd treats them as command substitution.
- Use your own agent name in the AGENT field.

## Closed terminals

These were from the first launch, which crashed. They are gone. Do not send to them.

    term_9bb68840-8785-4ce7-8297-c48f46518765  (harness, duplicate)
    term_505ae313-877d-4084-a974-ea257b3893f8  (core, duplicate)
    term_e8c33f6c-2ed0-4109-bd01-608b5083544f  (probe)

## Baseline every agent must defend

- 11,129 tests collected, 11,127 passed, 2 xfailed
- 319.5 seconds
- 94.91 percent coverage (47,499 of 50,045 statements)

Task specs, one per worktree root:
  TASK-HARNESS.md, TASK-CORE.md, TASK-FEATURES-A.md, TASK-FEATURES-B.md

Shared living document: orchestration/TEST-REFACTOR.md
Heartbeat monitor: orchestration/check_heartbeats.py