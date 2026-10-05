YOU BUILD ONE WORKFLOW. Ship it end to end, on a merged pull request.

MANDATORY, in this order:
1. Read C:/Users/Dilip/.agents/skills/ste100/SKILL.md. Apply it to every message
   you produce, every commit message and every comment. Short sentences. Active
   voice. One instruction per sentence. No semicolons.
2. Read AGENTS.md in your worktree. It binds you. It is the project contract.
3. Read docs/FEATURE-CONTRACT.md. That is how a feature is shaped here.
4. Read docs/DESIGN-SYSTEM.md before you write any UI. index.css wins over the
   markdown where they disagree.

Your ticket is WF-106. Its GitHub issue is issue 137.

Read the issue in full before anything else. It quotes the research
specification, the flow, the data sources, the APIs, the evidence and the
sources. The issue is designed so you need no context beyond it.

=================================================================
ENVIRONMENT
=================================================================

Worktree:  C:/Users/Dilip/orca/workspaces/client-theater/q-ticket-31
Branch:   q-ticket-31

No .venv here. EVERY python command must use this absolute path:

  C:\Users\Dilip\dsrvenv\Scripts\python.exe

It is a junction. The real folder is named "dummy repo", which contains a SPACE,
and cmd.exe splits a path on a space, so the real path never works.

NEVER run: git stash, git reset, git reset --hard, git checkout --, git restore,
git clean, git checkout <branch>. Those destroyed work in this project twice
today. ALLOWED because it names its target: git checkout <sha> -- <file>

Three other agents are working beside you on other workflows, and they are not
all the same tool. Working in someone else's worktree breaks them. Stay in yours.

=================================================================
WHAT TO BUILD
=================================================================

A feature plugin. It owns these paths and nothing else. These names are exact.
Do not shorten, re-slug or renumber them:

  backend/dsr/<your domain package>/      the domain module: pure rules,
                                           vocabulary, errors. No framework.
  backend/dsr/features/wf106_trigger_outreach_on_high_intent_page_visits.py        exports FEATURE and a router on
                                           prefix /api/wf-106
  backend/tests/test_wf106.py                  domain tests
  backend/tests/test_wf106_http.py            HTTP tests
  frontend/src/features/wf-106-trigger-outreach-on-high-intent-page-visits/     index.jsx exporting the descriptor,
                                           the page, api.js, primitives.jsx

If a package for your domain ALREADY EXISTS under backend/dsr/, extend it. Do
not create a second one for the same domain. These packages already exist on
main and are yours to extend where the subject matches:
crm_integration, throttle, lead_score, visitor_identification,
lead_qualification, round_robin, concierge_router.
Read backend/dsr/features/ first and see what is already there.

YOU MUST NOT EDIT ANY SHARED FILE. These are shared and the CI guard refuses a
branch that touches one:

  backend/dsr/api.py, backend/dsr/deps.py, backend/dsr/store.py,
  backend/dsr/db/audited.py, backend/seed.py,
  frontend/src/App.jsx, frontend/src/main.jsx, frontend/src/lib/api.js,
  frontend/src/lib/features.js, frontend/src/components/ui.jsx,
  frontend/vite.config.js

If your workflow genuinely cannot be built without editing one, STOP and report
it. Do not edit it and do not add a noqa. That is a platform change and it needs
a person.

HARD RULES THAT ENFORCED TESTS CHECK, NOT REVIEW

- The domain module imports nothing but the store. An enforced test greps every
  feature module for dsr.api and for bare sqlite3. Use dsr.deps for
  dependencies, never dsr.api.
- Records are ordinary JSON in records.data. No migration. No typed column.
  A team adding a field must need no coordination with anyone.
- Every write goes through AuditedDatabase and its audit row names a route the
  app actually serves. backend/seed.py prints each feature's return string.
- Every character of that return string must be encodable by cp1252. A single
  U+2192 RIGHTWARDS ARROW in one recovered feature broke the entire seeder on a
  Windows console. Test that by printing it.
- The frontend page uses the shared components from components/ui.jsx. No emoji
  as an icon. Icons come from the shared Icon by name. 44px touch targets. No
  status conveyed by colour alone.
- Tests must pass when their file is run on its own. The suite runs under
  pytest-xdist, so a test that only passes in one order will fail
  intermittently. A test that passes alone and fails in a run is a defect in
  the test, not a reason to switch off xdist.

=================================================================
KEEP A TODO FILE. THE ORCHESTRATOR READS IT.
=================================================================

Create this file first, before any code:

  .scratch/progress.md

with exactly this content:

  done: 0
  total: 11

Pick the real total for your workflow. Eleven is a guide, not a rule.

`.scratch/` is already in .gitignore, so this file never enters a commit.

Rules for keeping it:

- Rewrite the WHOLE FILE every time. Two lines, nothing else. Do not append,
  do not add headings, do not leave notes in it.
- `total` only ever changes if you genuinely rescope. If it does, say why in
  your pull request body, not in this file.
- `done` counts completed steps, not started ones.
- Update it the moment you finish a step, not at the end of a turn. A stale
  count is worse than an old one, because the orchestrator cannot tell them
  apart.

The orchestrator reports progress to a human from these two numbers. A wrong
number here is a lie told on someone's behalf, so if you are unsure, round
`done` DOWN.

=================================================================
TESTING UNDER PARALLELISM. READ THIS. IT WILL SAVE YOU AN HOUR.
=================================================================

Four agents run at once on eight cores. That makes test runs slow and makes some
of them time out for no reason at all.

I measured this. On main, alone, the frontend suite is 11 of 11 files passing.
In an agent worktree with four agents running, white-label.test.jsx fails 9 of
42. Run that same file on its own, it fails 1 of 42.

So:

- A slow test is not a broken test. Do not edit a test you do not own to make a
  suite green. white-label, wf069, wf079, wf059 and wf077 are pre-existing and
  they pass on a clean runner.
- CI is the only trustworthy measurement. Its runner is not shared.
- Run YOUR OWN test files, one at a time, and judge those.
- For the whole-suite run, accept that it will be slow and may time out locally.
  If it fails in a file that is not yours, say so, rebase onto main, and re-run.
  Do not fix their code.

=================================================================
COMMIT DISCIPLINE. THIS IS THE PART AGENTS GET WRONG.
=================================================================

Commit ONLY what you intend to push, and only after you have verified it.

- Stage by path. Never use `git add -A`, `git add .` or `git commit -a`. In a
  worktree that also holds scratch files, a blanket add publishes your working
  directory rather than your change. Three agents have shipped scratch scripts
  and scratch logs by doing this.
- Before every commit run `git status --porcelain` and read every line. Anything
  you did not write must not be staged. If a scratch file of yours is in the
  way, delete it or leave it unstaged. Do not delete anything you did not write.
- Every commit message says WHAT changed, WHY it changed, and HOW. Three
  sentences minimum in that order. "fix bug" is not a commit message.
- One concern per commit. A feature, a test fix and a rename are three commits.
- Never amend a commit that has been pushed.
- If a file you did not write is modified in your worktree, leave it alone and
  report it. Do not revert it and do not commit it.

=================================================================
THE FULL CYCLE. DO NOT STOP AT "IMPLEMENTED".
=================================================================

1. Implement the domain module and the feature module.
2. Write the tests. Run your own files alone. Then attempt the whole suite.
3. Run ruff check and ruff format --check, from the repository root:
     python -m ruff check backend tools orchestration --config backend/pyproject.toml
     python -m ruff format --check backend tools orchestration --config backend/pyproject.toml
   The --config flag is not optional. Without it ruff finds no config and
   reports hundreds of false errors.
4. Check the coverage gate. The gate fails below 90 percent:
     cd backend
     python -m pytest --cov=dsr --cov-report=term:skip-covered -q
   Record the number you measured and say plainly that four agents were running,
   because a number measured under contention understates the truth. CI is the
   authority.
5. Check the frontend:
     cd frontend
     npm ci
     npm run test
     npm run build
     npm run lint
     npm run format:check
6. Verify over real HTTP, not only through TestClient. Use a port no other agent
   is using; 8000 is usually taken, pick something in the 81xx range.
     python backend/seed.py
     python -m uvicorn dsr.api:app --app-dir backend --host 127.0.0.1 --port 8123
     python tools/verify_all_routes.py --base http://127.0.0.1:8000
   Your routes must answer with no 5xx.
7. Put a decision to Jev and record the audit id in the pull request. Ask the
   question your finished change actually raises. A gate on a design document
   answers a different question from a gate on a finished, tested change.
8. Commit by path. Nothing is pushed yet.
9. NOW, IN THIS SAME BREATH, IMMEDIATELY BEFORE THE PUSH, run:
     git fetch origin
     git rebase origin/main
   Then run:
     git diff --stat origin/main..HEAD
   Read every line. EVERY line must be an addition. If any line carries a minus
   sign you have NOT rebased correctly and you must not push. Fix it, then check
   again. Another workflow may have merged while you worked, and without this
   step your push silently reverts it.
10. Re-run your own tests after the rebase, because the rebase can move code.
11. Push, open the pull request, watch every check until green, and merge it.
   Do not merge with a red check.

A rebase done earlier in the cycle is not enough. It goes stale the moment the
next workflow merges, which is exactly what happens when four agents share a
moving main. Rebase once, here, immediately before the push.

=================================================================
JEV. USE IT FOR EVERY BLOCKING DECISION, WITHOUT ASKING ANYONE
=================================================================

  C:\Users\Dilip\dsrvenv\Scripts\python.exe tools\jev.py doctor

You are expected to decide for yourself. Do not stop and wait for a human when
you are unsure. Put the decision to Jev and act on what it says.

- A verdict of "uncertain" must NOT be overridden, and a selection is not a pass.
- If Jev's confidence comes back below its threshold, that is an answer too. Do
  not re-ask the same question hoping for a better number. Gather more evidence
  and re-ask with better options: state what you measured, narrow the
  candidates, and say what each one costs. More data raises confidence; the same
  question twice does not.
- If Jev still comes back low, park the item. Leave the worktree open, write what
  is done and what is left into orchestration/paused/<ticket>.md, move on to the
  next ticket in the queue, and say so in your heartbeat.
- Write your audit record to your own file, never the shared log:
    orchestration/decisions/<your ticket lowercase>-jev-audit.jsonl
  Several agents appending to orchestration/decisions/jev-audit.jsonl collide on
  every merge and one has to resolve it.
- Record the audit id and the verdict in your pull request body.

Questions that are yours to decide, not a human's: which design you pick, whether
a domain package should be extended or created, whether a failing test or the
code is wrong, and whether the change meets the release bar.

=================================================================
THE PULL REQUEST
=================================================================

The body must answer three questions, in this order:

1. WHAT was done. The workflow, in two sentences, and the files that carry it.
2. WHY it is shaped this way. The design decision, the alternative you rejected,
   and what would have gone wrong. Name the Jev audit id and its verdict.
3. HOW it was verified. The measured numbers: tests added, your own files'
   result, the whole-suite result, coverage percentage, routes called with no
   5xx, and the Jev release-bar verdict.

Then state plainly anything you could NOT do. A check you could not perform is
recorded, never claimed.

If CI reports a failure in a file that is not yours, say so, rebase onto main,
and re-run. Do not edit a file you do not own.

=================================================================
REPORT EVERY 10 MINUTES
==================================================================

  orca orchestration send --subject "HEARTBEAT" --to run:run_a753c94f0894 --type heartbeat --body "AGENT: wf-106 | ELAPSED: 10 | DONE: x | WORKING: y | BLOCKED: NONE | NEXT: z"

One line. Pipes between fields. No newlines, no backticks. A pipe inside a
quoted argument has broken shell quoting on this host; if your heartbeat is
refused, use commas instead.

=================================================================
BLOCKERS
==================================================================

Report and keep working. I have full authority.

  "No venv"                    -> C:\Users\Dilip\dsrvenv\Scripts\python.exe
  "ruff reports 400 errors"    -> you dropped --config backend/pyproject.toml
  "brief text sits unsubmitted"  -> read the screen and press Enter again
  "a test fails only in a full run" -> either contention or an order dependency
                                   in your own test. Fix your own. Say which.
  "the suite is red in someone else's file" -> rebase. Do not fix their code.
  "a permission dialog"          -> answer option 1 only. Never choose the
                                   option that stops asking for the rest of
                                   the session.

If you are blocked for more than one heartbeat on the same thing, say clearly
what you tried.
