# CI Test Reduction — Living Document

**A shared, append-only working document for every agent in this programme.**
Read it before you start. Append your own entry when you finish a step.

Orchestrator: the Command Code session holding Run `run_6e0bc978dd0e`.

---

## 1. The task

Reduce the number of test cases and test scenarios that run in CI, and make the
suite faster, **without compromising test integrity or coverage**. The target is
95% coverage of the existing code. Only test code changes. **Do not change
application source.**

---

## 2. Measured baseline (measured, not estimated)

Measured on `main` at `bc999cc`, on 2026-10-02, with
`.venv/Scripts/python -m pytest`.

| Metric | Backend | Frontend |
|---|---|---|
| Tests collected | **11,129** | 260 |
| Passed | 11,127 (+2 xfail) | 260 |
| Runtime | **319.5 s** | 27.0 s |
| Test files | 75 | 9 |
| CI job | `backend` — `python -m pytest` | `frontend` — `npm run test` |

### Where the 319 seconds actually go

Measured with `pytest --durations=0` and summed by phase:

| Phase | Seconds | Share |
|---|---|---|
| **setup** | **152.5 s** | **49%** |
| call (the test body) | 118.5 s | 38% |
| teardown | 38.1 s | 12% |

**Setup and teardown together are 61% of the runtime. The tests themselves are
not the slow part.**

Setup cost distribution (6,010 setup entries, mean 25.4 ms):

| Setup cost | Entries | Seconds | Share of setup time |
|---|---|---|---|
| >= 5 ms | 6,010 | 152.5 | 100% |
| >= 20 ms | 899 | 101.4 | 66.5% |
| >= 40 ms | 254 | 88.1 | 57.8% |
| >= 100 ms | 105 | 80.0 | 52.4% |

The cost is heavy-tailed. A few hundred expensive setups carry most of the cost.

### Coverage baseline

`pytest --cov=dsr` on `main`: **94.91%** — 47,499 of 50,045 statements covered,
2,546 missed. **This is the number every change must defend.**

> **CORRECTED 2026-10-02 by the orchestrator. Read this before trusting 2,546.**
>
> **The floor is 2,553 missed / 94.89859%, not 2,546 / 94.91%.**
>
> The 2,546 figure above is wrong. It was measured once, on a machine with other
> work running, printed to two decimals, and never re-measured. Four agents were
> then told to defend it. Two of them measured the *same commit* independently
> and both got 2,553. I diffed the two coverage JSON files, per file and line.
>
> The entire difference is **7 statements in one file**:
>
>     dsr/api.py lines 299, 300, 301, 303, 304, 306, 307
>
> Those lines are **import-time** code, guarded by `if FRONTEND_DIST.is_dir():`.
> Whether they count as covered depends on whether a built frontend exists on
> disk when `dsr.api` is first imported.
>
>     main repo      frontend/dist/index.html   EXISTS  -> covered
>     agent clone    frontend/dist/index.html   ABSENT  -> not covered
>     harness tree   frontend/dist/index.html   ABSENT  -> not covered
>
> `frontend/dist` is a build artefact and is gitignored. A clone never has it.
> So the same commit measures 2,546 or 2,553 depending on whether someone ran
> `npm run build` in that working copy first.
>
> **Defend 2,553.** It is the number that reproduces on a fresh clone, which is
> what CI has. A branch measured in a tree where `frontend/dist` happens to exist
> will read 7 statements high for a reason that has nothing to do with the change.
>
> **The real finding, and it is bigger than the number:** **CI does not run
> coverage at all.** All six checks were green on a branch that was below this
> floor, because nothing measures it. A floor with no enforcement behind it is a
> sentence in a document, not a gate. `core` found this and I am recording it
> rather than burying it. Adding coverage to CI is a real decision for a human:
> it costs a provider, a threshold, and a slower backend job.

To reproduce:

```
cd backend
../.venv/Scripts/python -m pytest --cov=dsr --cov-report=json:cov.json -q
```

---

## 3. The four costs, measured

Benchmarked directly against this repository. Machine: 8 cores.

| Operation | Cost |
|---|---|
| `AuditedDatabase(path_on_disk)` — schema applied | **7.0 ms** |
| `AuditedDatabase(":memory:")` | **0.4 ms** |
| `from dsr.api import app` (once per process) | 1,314 ms |
| `TestClient(app)` enter (runs the lifespan) | 46 ms |
| `TestClient(app)` exit | 1 ms |

Fixture usage across the 75 backend test files:

| Pattern | Files |
|---|---|
| Uses `TestClient` | **58** |
| `AuditedDatabase(tmp_path / ...)` — file-backed | **44** |
| `AuditedDatabase(":memory:")` — in-memory | 13 |

### The keystone benchmark

The `http` / `client` fixture in each test file builds a `TemporaryDirectory`,
sets `DSR_DB_PATH`, and enters a `TestClient` — **per test**. Four alternatives,
measured at 25 iterations each:

| Strategy | ms per test | Speedup |
|---|---|---|
| **A** current: file-backed DB + full lifespan per test | **15.55** | 1.0x |
| **B** in-memory DB + full lifespan per test | 4.14 | 3.8x |
| **C** one `TestClient` for the module, swap `app.state.db` per test | **1.25** | **12.5x** |
| **D** shared-cache memory URI + `app.state` swap | 1.26 | 12.4x |

**Strategy C is the single biggest lever in the suite.** `dsr/deps.py` resolves
the database path at call time, and the lifespan in `dsr/api.py` only assigns
`app.state.db` and `app.state.store`. A test therefore needs a *fresh database*,
not a *fresh application*. One module-scoped `TestClient` plus a per-test
`app.state` swap gives each test the same isolation at about 1/12 the cost.

There is **no `backend/tests/conftest.py` today**. Creating one is an
architectural change and belongs to exactly one agent (see section 5).

---

## 4. Rules that apply to every agent

1. **Never change application source.** The files `backend/dsr/**` and
   `frontend/src/**` are out of scope. Test code only. The only exception is
   `backend/pyproject.toml` (pytest configuration), and only for the pytest
   `addopts`.
2. **Never delete a test that covers a distinct behaviour.** Merging two tests
   that prove different things into one test reduces the count and destroys
   coverage. A reduction that deletes a behaviour is a failure, not a saving.
3. **Do not weaken an assertion** to make a test pass.
4. **Prove the coverage number after every change.** Run the coverage command in
   section 2 and record the number. Coverage below 94.91% is not acceptable.
5. **`ruff check` and `ruff format --check` must pass**, with the config flag:

   ```
   ruff check backend tools orchestration --config backend/pyproject.toml
   ruff format --check backend tools orchestration --config backend/pyproject.toml
   ```

6. **Report every 10 minutes.** Send a heartbeat to the orchestrator. Use the
   `heartbeat` command in your task spec. Report: what you did, what you
   measured, what is blocked, what you do next.
7. **Use Simplified Technical English (ASD-STE100) in every report.** The skill is
   installed at `C:/Users/Dilip/.agents/skills/ste100/SKILL.md`. Read it before
   your first report. Short sentences. Active voice. One instruction per
   sentence. No semicolons. No hedging.
8. **Do not step on other agents.** Each agent owns a disjoint set of test
   files, except the two agents who share `conftest.py` — they coordinate through
   this document and through the orchestrator, not by editing each other's work.

---

## 5. Work split

| Agent | Branch | Owns |
|---|---|---|
| **A — keystone** | `perf/test-harness` | `backend/tests/conftest.py` (new), `backend/pyproject.toml` pytest section |
| **B — core** | `perf/tests-core` | 17 core test files (not `test_wf*`) |
| **C — features A** | `perf/tests-features-a` | `test_wf001` .. `test_wf030` |
| **D — features B** | `perf/tests-features-b` | `test_wf032` .. `test_wf079`, and `frontend/**` |

**Agent A is the dependency.** B, C, and D all benefit from the shared
`conftest.py`. A must land first. B, C, and D should design their changes so they
do not *require* A to have merged — they may use `conftest.py` if it exists in
their worktree, and must not fail if it does not.

---

## 6. Environment facts every agent needs

These three facts cost real time to establish. Do not rediscover them.

**1. Use the space-free Python path.**

    C:\Users\Dilip\dsrvenv\Scripts\python.exe

That is a junction to the real virtualenv. The real folder is called
`dummy repo`, which contains a space. **cmd.exe splits a path on a space**, so
the real path does not survive a cmd prompt. One agent reported the interpreter
as missing and refused to start; the interpreter was there the whole time.

**2. Heartbeats must be one line, and must not use backticks.**

    orca orchestration send --subject "HEARTBEAT" --to run:run_6e0bc978dd0e --type heartbeat --body "AGENT: core | ELAPSED: 31 | DONE: x | WORKING: y | BLOCKED: NONE | NEXT: z"

Use the pipe character to separate fields. Newlines and backticks are rewritten
or executed before the agent sees them.

**3. Your terminal handle is the one in your live preamble.** Do not search for
it. `orca orchestration status` is not a command.

---

## 7. Progress log

Append here. Newest last. Write for an agent who has never seen this work.

- 2026-10-02 — Orchestrator. Baseline measured and recorded. Four agents
  dispatched.

- 2026-10-02 — Orchestrator. Environment defects found and fixed. The venv path
  carried a space. The heartbeat command used flags that do not exist. The
  heartbeat monitor matched subjects exactly, so it reported a live agent as
  silent. Two crashed agents from the first launch were still alive in abandoned
  tabs and sending heartbeats under the same identity as a live one. All four
  fixed. See section 6.

- 2026-10-02 — Orchestrator. **A pre-existing flaky test, measured not guessed.**

  `tests/test_wf069.py::TestOneTimeCodeHashing::test_codes_do_not_repeat_in_a_small_sample`
  failed once on the `perf-tests-features-b` branch. It mints 200 six-digit codes
  and asserts all 200 differ. The code space is 1,000,000, so the birthday
  probability of at least one repeat is **1.97 percent**.

  Measured over 2,000 trials of 200 real `mint_code()` calls:

      collisions   35 (1.75%)
      theory       1.97%
      worst trial  1 repeat

  The test is inherently flaky at about 2 percent. It is a defect in the TEST, not
  in the product and not in any fixture change. Re-running the file gives
  298 passed.

  **Two measurement mistakes of my own, both worth recording.**

  1. My first check ran 30 trials on the agent's branch (1 failure) and 30 on a
     pristine copy of the base commit (0 failures), and printed DIFFERENT. With a
     2 percent event, 0 or 1 in 30 is the *expected* result: a clean 30 is more
     likely than not. Reporting a 1-in-30 observation as a difference is how you
     chase a ghost.
  2. The follow-up script printed `birthday probability expected: -97.03%`.
     Operator precedence: `1 - pow(x, n) * 100` multiplies the wrong term. The
     real value is `(1 - pow(x, n)) * 100` = 1.97 percent. A negative
     probability is obviously wrong on sight, and I read the verdict line anyway.

  **No agent should "fix" this test by deleting it or by widening the sample.** It
  proves codes do not repeat, which is a real property. It proves it in a way that
  fails 2 percent of the time. The honest fix is to seed the random source, not to
  remove the assertion.

- 2026-10-02 — Orchestrator. **Recovery after a lost stash.** The `core` agent ran
  `git stash push`, git reported success, and the stash did not persist. Seven
  changed files were gone. They were recovered from the unreachable stash commit
  `1708bd5556294665df054d8bd52a8885abeab4fa` with `git checkout`, and verified
  green before the agent was told. Recorded in `WORKTREE-SAFETY.md`.

- 2026-10-02 — **PR #95 MERGED. Commit `d7f385f`. First landing.**

  Eight files, all under `backend/tests`, no shared file:

      test_access_api.py  test_analytics.py  test_api.py
      test_audited.py     test_features.py   test_roles.py
      test_roles_api.py   test_seed.py

  All six CI checks green on an isolated runner. Mergeable CLEAN.

  What landed, and what it is worth:

  * `test_seed.py` ran the seeder three times for two assertions, because one
    test wanted a fresh nested path and one wanted to seed the same path twice.
    A module fixture now seeds once. The file was 28.4 s, the most expensive in
    the suite for two tests.
  * Five files entered a `TestClient` per test, which runs the FastAPI lifespan
    and opens a database each time. One module-scoped client per module, with a
    fresh database swapped into `app.state` per test.
  * `test_audited.py` split into two fixtures: `db` in memory, and `mirror_db`
    file-backed for exactly the three tests that glob for `audit-*.jsonl`. Its
    slowest setup fell from 1.22 s to 0.20 s.

  **The agent corrected its own headline numbers twice, unprompted.** It first
  claimed a halving, then reported that it had measured its own *converted* tree
  and called it the baseline. Its final claim is 27 to 35 percent on the six
  converted files, measured against a clean clone at `bc999cc` in a temp folder.
  That is the number to trust, and it is smaller than the one it started with.

  **A real defect found and fixed:** `app` is a module-level singleton shared by
  every test file in the process. The first version of the fixture closed its
  database on the way out without restoring `app.state`, so a file that read
  `app.state.store` without entering its own client would have hit a closed
  database. That is the exact "passes alone, fails together" mode. Both fixtures
  now save and restore `app.state.db` and `app.state.store`.

- 2026-10-02 — **The measurement lesson of this programme, stated once.**

  On this machine, with four agent suites competing for eight cores, **a single
  timing sample is not evidence.** The spread between a fast and a slow run of
  identical code was 47 s (151.93 s against 198.85 s), and one `AuditedDatabase`
  round hit 987 ms where the median was 5 ms.

  I published a number from one contaminated sample. I measured a file database
  with a mirror at 657 ms and one without at 13 ms, and wrote that the mirror was
  the cost. Re-measured as the minimum of six interleaved rounds:

      file + mirror    min 3.41ms
      file, no mirror  min 4.03ms
      in-memory        min 0.48ms

  The mirror costs nothing. My first number was an artifact of running first on a
  cold, contended disk. The rule every agent now follows: **interleave the
  variants, take the minimum of several rounds, and say the machine was busy.**

  One more consequence, which is the useful part: **CI is a better measurement
  than any local run here.** It runs on an isolated runner with no competing load.
  When CI disagrees with a local number, CI is right.

- 2026-10-03 — **Agent `harness`. PR #96. The keystone fixtures have landed on a
  branch. Read this before converting any fixture.**

  Two files. No `test_*.py`, no application source, no shared file.

      backend/tests/conftest.py      new
      backend/pyproject.toml         pytest-xdist and pytest-cov in dev, -n auto in addopts

  ### The fixtures, and why they are shaped this way

  Entering a `TestClient` runs the application lifespan. The lifespan in
  `dsr/api.py` does two things: it opens the database, and it assigns
  `app.state.db` and `app.state.store`. A test needs the first. It does not need
  the second re-run, because `get_store` reads `request.app.state.store` fresh
  on every request. **A test needs a fresh database, not a fresh application.**

  `module_client` enters one client per module. `client` reuses it and swaps
  `app.state` per test, then restores the state it found. Names match what the
  suite already calls things, so adopting one is a deletion and not a rename:

      client   http   db   db_path   store   memory_db   module_client

  **A fixture defined in a module always beats one in `conftest.py`,** so any
  module that has not been converted keeps its own fixtures and behaves exactly
  as before. Converting a module is therefore safe at any time and in any order.
  No module depends on another having converted first.

  Two autouse guards sit under all of it:

  1. `DSR_DB_PATH` and `DSR_AUDIT_DIR` point at the test's own `tmp_path` when a
     test has not set them. Every current module sets both, so this changes no
     existing test. It stops the next module from quietly reading and writing the
     real `data/dsr.db`, which would let the suite pass while proving nothing.
  2. `app.dependency_overrides` is emptied before each test. It is the documented
     seam for replacing a service and it is process-wide. Clearing before each
     test can only remove state and never add it, so it cannot manufacture a
     pass.

  ### Measured fixture cost

  200 iterations per strategy, minimum of four interleaved rounds, with a
  no-op-fixture run subtracted as the floor:

  | Strategy | per test |
  |---|---|
  | file db + full lifespan per test (what the suite did) | 27.50 ms |
  | in-memory db + full lifespan per test | 8.15 ms |
  | module client + `app.state` swap, file db | 19.55 ms |
  | session client + `app.state` swap, file db | 23.75 ms |
  | module client + `app.state` swap, in-memory db | 1.35 ms |

  **Module scope beats session scope structurally, not only on timing.** A
  session-scoped client outlives every test in it, so one test can leave
  `app.state` pointing at a closed database for every test after it. That is the
  same defect PR #95 found and fixed inside a single file.

  ### The whole suite

  `pytest-xdist` is now a dev dependency and `addopts` carries `-n auto`. The
  machine was running three other suites throughout, so read these as ratios and
  remember the rule above: CI is the better measurement.

  | Configuration | Runtime | Result |
  |---|---|---|
  | serial, unmodified tree | 708.3 s | 11127 passed, 2 xfailed |
  | `-n 8 --dist load` | 237.1 s | 11127 passed, 2 xfailed |
  | `-n 8 --dist loadscope` | 269.4 s | 11127 passed, 2 xfailed |
  | `-n 8 --dist loadfile` | 393.8 s | 11127 passed, 2 xfailed |

  After rebasing onto `d7f385f`, three runs of plain `python -m pytest`, which is
  what CI runs: **226.40 s, 247.61 s, 268.86 s**, all 11127 passed and 2 xfailed,
  all exit 0. `load` is the default and it was fastest with no failures, so it is
  left alone. The 9 warnings are the one pre-existing `StarletteDeprecationWarning`
  once per worker process, not nine new problems.

  ### Coverage, agreeing with the corrected floor

  This branch measures **2553 missed, 94.89859%**, which is the corrected floor
  and exactly what a clone without a built `frontend/dist` reports. Five runs
  agree, and three of them cannot be blamed on this change:

  | Run | Missed |
  |---|---|
  | pristine tree, serial (`-n 0`), no conftest | 2553 |
  | pristine tree, `-n auto`, no conftest | 2553 |
  | this branch, `--dist load` | 2553 |
  | this branch, `--dist loadfile` | 2553 |
  | this branch, autouse guards removed entirely | 2553 |

  Same 279 files with gaps, same per-file counts. This change costs zero
  coverage. **Coverage is invariant to the distribution mode**, which answers
  "does xdist hide order-dependent tests" by measurement rather than assumption.

  Note for whoever maintains section 4: the gate there still reads 94.91%, which
  was the `frontend/dist` artefact. The number to defend is 2553 missed.

  ### Which tests must stay file-backed

  * `test_persistence_contract.py` and `test_wf001.py` reopen the **same path** in
    a second `AuditedDatabase` and read the first one's rows back. The data has to
    outlive the connection, and `":memory:"` cannot do that at all.
  * `test_seed.py` points `DSR_DB_PATH` at a nested path whose parent directory
    does not exist, then runs `backend/seed.py` in a subprocess.
  * `test_audited.py`, `test_wf002.py`, `test_wf009.py`, `test_wf033.py` open
    file-backed databases directly in their own fixtures.

  **The mirror is not one of them, and this is the correction worth carrying
  forward.** The JSONL audit mirror is written to `mirror_dir`, a directory,
  independently of where the database lives. A test that only reads
  `audit-*.jsonl` needs a real `mirror_dir` and does **not** need a file-backed
  database. `AuditedDatabase(":memory:", mirror_dir=tmp_path / "audit")` covers
  it. That is a larger set than it looks, and it is exactly what PR #95 did in
  `test_audited.py` when it split `db` in memory from `mirror_db` file-backed.

  Three things `":memory:"` genuinely cannot do:

  1. Reopen the path in a second connection.
  2. Use WAL. `_connect` skips the `journal_mode` pragma for `":memory:"`.
  3. Outlive the process. No test currently asserts on `journal_mode`, so this is
     a mechanism to know about, not a test to preserve.

  ### What is left

  * **No module has adopted the fixtures yet.** That is the biggest remaining win:
    58 modules still build a `TestClient` per test at about 27.50 ms each.
  * **Order dependence was not hunted down.** Nothing failed under any
    distribution mode and coverage did not move, so nothing broke. That is
    evidence, not proof: a test that passes both ways while covering different
    lines would show up in neither number.
- 2026-10-03 — **Agent D (features-b), branch `perf-tests-features-b`.** Cut the
  setup cost of `test_wf032`..`test_wf079` and the 9 frontend test files. Test
  code only. No application source, no assertion weakened, no test deleted.

  **Scope correction: 26 files, not 28, and 6,220 tests, not 5,922.** The task
  brief lists 26 filenames and says 28. The count that matters is what pytest
  collects: 6,220 tests across the 26 files I own.

  **Where the time actually was.** `pytest --durations=0` over my 26 files,
  before any edit:

  | Phase | Seconds | Share |
  |---|---|---|
  | setup | 167.1 | 56.5% |
  | call | 71.1 | 24.0% |
  | teardown | 57.4 | 19.4% |

  Setup plus teardown was **75.9%** of measured time. The test bodies were a
  quarter of it. The fixtures were the whole problem, which confirms the section
  3 diagnosis and sharpens it: for the wf032..wf079 files the fixture share is
  worse than the 49% recorded for the suite as a whole.

  **Three changes.**

  1. *In-memory databases, 22 files.* `AuditedDatabase(path)` is 7.0 ms,
     `AuditedDatabase(":memory:")` is 0.4 ms. I verified by reading the tests
     that **no test in these 26 files reads the audit mirror off the
     filesystem.** The one test that does is in `test_wf001.py`, which belongs to
     agent C. So the `mirror_dir` argument bought nothing here.

  2. *One `TestClient` per module, 21 files.* Strategy C from section 3, exactly
     as documented. `dsr/api.py` assigns only `app.state.db` and
     `app.state.store`, and `dsr/deps.py` reads `app.state.store` per request.

     **One exception, found by reading rather than by guessing.**
     `test_wf033.py` keeps a file-backed database. Three of its audit tests open
     a *second* `AuditedDatabase` on the same path and read the audit log back
     from it. That proves the audit row was committed to the file, which is a
     different claim from reading it over HTTP. Converting it to `:memory:` broke
     two of those tests, which is how I found the coupling.

  3. *One seeded database for the read-only seed tests in `test_wf063.py`,
     16 tests.* Fifteen of them assert different things about the same demo
     dataset, and each used to run the whole seeder over its own database. The
     no-rooms case and the test that drives the engine keep their own.

  **Left file-backed on purpose**, per the brief's safety rules:
  `test_wf034.py`, `test_wf038.py`, `test_wf038_http.py` (real transport layer),
  and `test_wf045_perf.py` (performance measurement; its timings must not move).

  **Result, same machine, same interpreter.**

  | | Before | After |
  |---|---|---|
  | 26 files, tests | 6,220 | 6,220 |
  | wall clock | 343.46 s | 151.93 s |
  | setup (reported) | 167.1 s | 45.6 s (-73%) |
  | teardown (reported) | 57.4 s | 8.0 s (-86%) |

  **Read the headline with care.** Two runs of the same code gave 151.93 s and
  198.85 s, because up to four suites from four agents were running on eight
  cores at once. So the honest saving on my files is **between 42% and 56%**, not
  a single figure. The phase split is the robust evidence; the wall clock is
  load noise. The orchestrator's own measurement of my 22 changed files, on a
  quieter machine, was 5,766 passed in 81.19 s. CI on an isolated runner is
  better still and should override both.

  **Gates, on the rebased tree.**

  | Gate | Baseline | Mine |
  |---|---|---|
  | collected | 11,129 | 11,129 |
  | passed | 11,127 + 2 xfailed | 11,127 + 2 xfailed |
  | coverage missed statements | 2,546 measured on this machine | 2,546 measured on this machine |
  | `ruff check` | pass | pass |
  | `ruff format --check` | pass | pass (520 files) |
  | `eslint` | pass | 0 errors, 1 pre-existing warning in `ui.jsx` |
  | `prettier` | pass | pass |
  | `vitest run` | 260 passed | 260 passed, 9 files |
  | `vite build` | pass | pass, 293 modules |

  **On the coverage floor, defer to the corrected number above.** I measured 2,546
  missed statements out of 50,045 both before and after my change, so my change
  moved coverage by nothing at all — that is the claim I can support. I am not
  restating 2,546 as the floor the next agent must defend, because two agents
  have since shown that figure is not reproducible on this machine. The floor
  recorded earlier in this document is the one to hold.

  **The frontend was measured, not changed — and I could not improve it safely.**
  All 9 files render through `@testing-library/react`, so the jsdom environment
  every file pays for is not avoidable; the section 5 suggestion to move a pure
  logic file to a lighter environment has no candidate here. Only 2 test titles
  repeat across the 9 files, and both are per-feature descriptor assertions that
  check their own feature, so there is no duplication to remove. The `waitFor`
  and `findBy` calls are not slow by accident: every page fetches on mount, so
  the assertion genuinely has to wait. I could only have made this faster by
  swapping `userEvent` for `fireEvent`, which would stop testing real key
  events. I did not do that. I added no coverage gate, as instructed.

  **I did not reduce the test count, on purpose.** Once the fixtures were fixed
  the remaining cost is in the test bodies (`call` 46.7 s of 100.2 s reported).
  Cutting tests to save that would trade covered behaviour for a small gain,
  which section 4 rule 2 forbids. So this branch makes the suite much faster and
  keeps every test. If the programme also wants a smaller *count*, that is a
  separate decision and it needs a human, because it means choosing which
  behaviours to stop asserting.

  **Two things I got wrong, recorded so nobody copies them.**

  * I ran `git stash` in a worktree. All four worktrees share one `.git`, so
    `git stash pop` took a **peer agent's** stash and wrote 7 core test files
    into my worktree. I reverted them and popped my own stash by name, and no
    work was lost, but this is exactly the failure `WORKTREE-SAFETY.md` rule 1
    describes. The correct way to get a baseline is rule 3: copy the repo and
    check out the base commit in the copy.
  * The harness temp folder is shared between agents. A peer agent overwrote one
    of my analysis scripts with a different file of the same name, and it failed
    in a way that looked like my bug. My scripts now live in a private
    subfolder. Anyone measuring on this machine should assume the temp folder is
    not private.

  **One measurement trap.** `backend/pyproject.toml` sets `addopts = "-q"`.
  Adding `-q` on the command line makes it `-qq`, which **suppresses the final
  count line entirely**. A full-suite run then exits 0, prints progress to 100%,
  and reports no counts. Do not add a second `-q`; `addopts` already has one.

---

## 8. Results so far

### Merged to main

| PR | Commit | What | Effect |
|---|---|---|---|
| #95 | `d7f385f` | 8 core test files | seeds once, module client, in-memory stores |
| #96 | `698d460` | shared `conftest.py` + `pytest-xdist` | one `TestClient` per module; `-n auto` |
| #98 | `0ae89a7` | 16 WF-001..WF-030 test files | in-memory store fixtures, one line each |

### The number that counts: the Backend tests job, on a GitHub runner

Local timings on this host are not comparable to CI timings and never were. Four
suites share eight cores here. The only like-for-like measurement is the same
runner image, so that is what this reports. Every row below is the
**Backend tests** job duration read from GitHub Actions.

| Run | Branch | Job time | Conclusion |
|---|---|---|---|
| `37009181528` | `chore/refresh-dashboards` | 187 s | success |
| `37048347981` | main, **serial**, before PR 95/96 | **217 s** | success |
| `37045145975` | `perf-tests-core` | 197 s | success |
| `37052796804` | `perf-test-harness` | 107 s | success |
| `37053160255` | main, after PR 96 (`-n auto`) | **118 s** | success |
| `37054804435` | `perf-tests-features-a` | 181 s | success |

The two runs on **main** bracket the work: **217 s serial, 118 s with the
fixtures and `-n auto`.** That is **46 percent**, same runner image, and every
run passed.

The feature-branch runs are slower than the main run because they ran while three
agent suites were competing with the runner's own work, and because the shared
`-n auto` had not yet landed on their base. They are not a like-for-like series
and should not be read as one. The 181 s for `perf-tests-features-a` against
118 s on main is that gap, not a regression.

**All of it: 11,129 tests, unchanged, on every run.** No test was deleted to buy
any of this.

### What each piece contributed

| Change | Agent | Mechanism |
|---|---|---|
| seed once, not three times | core | `test_seed.py` ran the seeder per assertion; 28.4 s for 2 tests |
| one `TestClient` per module | core, features-b | a test needs a fresh *database*, not a fresh *application* |
| in-memory stores | core, features-a, features-b | 3.4 ms to 4.0 ms on disk, 0.5 ms in memory |
| shared `conftest.py` | harness | one place for the fixtures, adopted without editing 75 files |
| `pytest-xdist` with `-n auto` | harness | the suite is setup-bound, so it parallelises cleanly |

### The four things this programme got wrong, and what each cost

1. **A coverage floor nobody could reproduce.** I measured 2,546 missed once, on
   a loaded host, and told four agents to defend it. Two measured the same commit
   and both got 2,553. The gap is 7 statements in `dsr/api.py` behind an
   `if FRONTEND_DIST.is_dir()` guard, and `frontend/dist` is a gitignored build
   artefact. See section 2.
2. **A single timing sample treated as evidence.** Identical code varied 47 s
   between runs. One `AuditedDatabase` round hit 987 ms against a 5 ms median. My
   own first measurement of the mirror cost, 657 ms, was a cold-disk artifact.
3. **A command that printed success and was not.** `git stash push` reported
   "Saved working directory and index state", exited 0, and left nothing. Seven
   files were gone until they were recovered from an unreachable commit.
4. **Treating the working tree as storage.** A file moved out of a worktree with
   no commit behind it is unrecoverable, because git never hashed it.

The common thread is not carelessness. It is treating one observation, or one
success message, as a fact.

Append here. Newest last. Write for an agent who has never seen this work.

- 2026-10-02 — Orchestrator. Baseline measured and recorded. Four agents
  dispatched.

- 2026-10-02 — Orchestrator. Environment defects found and fixed. The venv path
  carried a space. The heartbeat command used flags that do not exist. The
  heartbeat monitor matched subjects exactly, so it reported a live agent as
  silent. Two crashed agents from the first launch were still alive in abandoned
  tabs and sending heartbeats under the same identity as a live one. All four
  fixed. See section 6.

- 2026-10-02 — Orchestrator. **A pre-existing flaky test, measured not guessed.**

  `tests/test_wf069.py::TestOneTimeCodeHashing::test_codes_do_not_repeat_in_a_small_sample`
  failed once on the `perf-tests-features-b` branch. It mints 200 six-digit codes
  and asserts all 200 differ. The code space is 1,000,000, so the birthday
  probability of at least one repeat is **1.97 percent**.

  Measured over 2,000 trials of 200 real `mint_code()` calls:

      collisions   35 (1.75%)
      theory       1.97%
      worst trial  1 repeat

  The test is inherently flaky at about 2 percent. It is a defect in the TEST, not
  in the product and not in any fixture change. Re-running the file gives
  298 passed.

  **Two measurement mistakes of my own, both worth recording.**

  1. My first check ran 30 trials on the agent's branch (1 failure) and 30 on a
     pristine copy of the base commit (0 failures), and printed DIFFERENT. With a
     2 percent event, 0 or 1 in 30 is the *expected* result: a clean 30 is more
     likely than not. Reporting a 1-in-30 observation as a difference is how you
     chase a ghost.
  2. The follow-up script printed `birthday probability expected: -97.03%`.
     Operator precedence: `1 - pow(x, n) * 100` multiplies the wrong term. The
     real value is `(1 - pow(x, n)) * 100` = 1.97 percent. A negative
     probability is obviously wrong on sight, and I read the verdict line anyway.

  **No agent should "fix" this test by deleting it or by widening the sample.** It
  proves codes do not repeat, which is a real property. It proves it in a way that
  fails 2 percent of the time. The honest fix is to seed the random source, not to
  remove the assertion.

- 2026-10-02 — Orchestrator. **Recovery after a lost stash.** The `core` agent ran
  `git stash push`, git reported success, and the stash did not persist. Seven
  changed files were gone. They were recovered from the unreachable stash commit
  `1708bd5556294665df054d8bd52a8885abeab4fa` with `git checkout`, and verified
  green before the agent was told. Recorded in `WORKTREE-SAFETY.md`.

- 2026-10-02 — **PR #95 MERGED. Commit `d7f385f`. First landing.**

  Eight files, all under `backend/tests`, no shared file:

      test_access_api.py  test_analytics.py  test_api.py
      test_audited.py     test_features.py   test_roles.py
      test_roles_api.py   test_seed.py

  All six CI checks green on an isolated runner. Mergeable CLEAN.

  What landed, and what it is worth:

  * `test_seed.py` ran the seeder three times for two assertions, because one
    test wanted a fresh nested path and one wanted to seed the same path twice.
    A module fixture now seeds once. The file was 28.4 s, the most expensive in
    the suite for two tests.
  * Five files entered a `TestClient` per test, which runs the FastAPI lifespan
    and opens a database each time. One module-scoped client per module, with a
    fresh database swapped into `app.state` per test.
  * `test_audited.py` split into two fixtures: `db` in memory, and `mirror_db`
    file-backed for exactly the three tests that glob for `audit-*.jsonl`. Its
    slowest setup fell from 1.22 s to 0.20 s.

  **The agent corrected its own headline numbers twice, unprompted.** It first
  claimed a halving, then reported that it had measured its own *converted* tree
  and called it the baseline. Its final claim is 27 to 35 percent on the six
  converted files, measured against a clean clone at `bc999cc` in a temp folder.
  That is the number to trust, and it is smaller than the one it started with.

  **A real defect found and fixed:** `app` is a module-level singleton shared by
  every test file in the process. The first version of the fixture closed its
  database on the way out without restoring `app.state`, so a file that read
  `app.state.store` without entering its own client would have hit a closed
  database. That is the exact "passes alone, fails together" mode. Both fixtures
  now save and restore `app.state.db` and `app.state.store`.

- 2026-10-02 — **The measurement lesson of this programme, stated once.**

  On this machine, with four agent suites competing for eight cores, **a single
  timing sample is not evidence.** The spread between a fast and a slow run of
  identical code was 47 s (151.93 s against 198.85 s), and one `AuditedDatabase`
  round hit 987 ms where the median was 5 ms.

  I published a number from one contaminated sample. I measured a file database
  with a mirror at 657 ms and one without at 13 ms, and wrote that the mirror was
  the cost. Re-measured as the minimum of six interleaved rounds:

      file + mirror    min 3.41ms
      file, no mirror  min 4.03ms
      in-memory        min 0.48ms

  The mirror costs nothing. My first number was an artifact of running first on a
  cold, contended disk. The rule every agent now follows: **interleave the
  variants, take the minimum of several rounds, and say the machine was busy.**

  One more consequence, which is the useful part: **CI is a better measurement
  than any local run here.** It runs on an isolated runner with no competing load.
  When CI disagrees with a local number, CI is right.

---

## 9. Agent `coverage` — the gate the floor never had

Branch `ci-coverage`. Files: `tools/coverage_gate.py`,
`tools/coverage_comment.py`, `tools/coverage_gate_selftest.py`, and a marked
region of `.github/workflows/ci.yml`. No test file, no application source, no
`backend/pyproject.toml`. `pytest-cov` was already in the `dev` extras, so
nothing had to be added to install coverage.

### The gate

`tools/coverage_gate.py` reads `backend/coverage.json`. It recomputes the
percentage from the per-file `covered_lines` and `num_statements` counts in
that file. It never reads a printed line of pytest output. It cross-checks the
report's own `totals` block against the sum of the per-file rows and refuses to
report a number when the two disagree.

| Exit | Meaning |
|---|---|
| 0 | the measured total is at or above the floor |
| 1 | the measured total is below the floor |
| 2 | the report is missing, unreadable, or contradicts itself |

`FLOOR_PERCENT = 90.0` is a module constant. There is no flag and no workflow
input. `coverage_gate_selftest.py` proves that passing `--floor 10` is
rejected by argparse, so no pull request can move the threshold.

### Measured, on commit 0b57667, in this worktree

Six runs, two interleaved rounds, same machine, same interpreter.

| Round | Configuration | Wall clock | percent | missed | exit |
|---|---|---|---|---|---|
| 1 | `-n auto --cov=dsr` | 231.84 s | 94.88660 | 2559 | 0 |
| 1 | `-n 0 --cov=dsr` | 420.45 s | 94.88660 | 2559 | 0 |
| 1 | `-n auto`, no coverage | 230.65 s | | | 0 |
| 2 | `-n auto --cov=dsr` | 245.38 s | 94.88660 | 2559 | 0 |
| 2 | `-n 0 --cov=dsr` | 636.55 s | 94.88660 | 2559 | 0 |
| 2 | `-n auto`, no coverage | 118.19 s | | | 0 |

Every run reports **11127 passed and 2 xfailed**, which is the 11129 collected on
`main`. Every coverage run reports the same number:

    2559 missed of 50045 statements
    94.88660 percent
    401 files measured, 279 of them with a gap

**This host cannot measure what coverage costs, and I will not pretend it can.**
The two `-n auto` runs without coverage took 230.65 s and 118.19 s. That is a
112 second spread on identical code. The two `-n auto` runs with coverage took
231.84 s and 245.38 s. The coverage overhead is smaller than the spread between
two runs of the same code, so any figure I published from this host would be
measuring the other agents' load and calling it coverage.

The runner figure is the one to believe, and section 7 says why. It is in the
table at the end of this section.

### The xdist question, answered by measurement rather than by argument

The task brief warned that coverage under `-n auto` can report only the subset
one worker saw. Four full runs of the same commit, then a comparison of all four
JSON reports file by file and uncovered line set by uncovered line set:

| Run | Command | percent | missed |
|---|---|---|---|
| 1 | `pytest -n auto --cov=dsr` | 94.88660 | 2559 |
| 2 | `pytest -n 0 --cov=dsr` | 94.88660 | 2559 |
| 3 | `pytest -n auto --cov=dsr` with `COV_CORE_SOURCE=dsr` | 94.88660 | 2559 |
| 4 | round two of `-n auto --cov=dsr` | 94.88660 | 2559 |

    files compared                          401
    files with a count difference            0
    files with a different uncovered set     0
    totals blocks identical                 yes, all four

Every run reports 11127 passed and 2 xfailed and exits 0. pytest-cov is already
combining the xdist workers correctly. `COV_CORE_SOURCE` changes nothing, which
is expected: every `dsr` module is imported inside a test rather than at
interpreter start-up, so there is no early statement for it to catch.

The gated number is the `-n auto` number. It is the same number a serial run
produces, at a fraction of the wall clock.

### A per-file floor is not adoptable today, and the total is sensitive enough

The distribution, measured, not estimated. 399 files carry statements.

| Floor | Files that fail today |
|---|---|
| 60 percent | 0 |
| 70 percent | 8 |
| 80 percent | 13 |
| 90 percent | 45 |
| 95 percent | 131 |
| 100 percent | 279 |

No file is below 60 percent. A floor at 60 passes with an empty allowlist and
catches almost nothing. Any floor worth having needs an allowlist of 13 to 131
files that already exist.

Meanwhile the total already reacts. Removing every covered statement from
`dsr/db/audited.py`, the largest file with a gap at 395 statements, moves the
total from 94.88660 to 94.12329 percent. That is 0.76 points against 4.89
points of headroom, so roughly six abandoned large files would fire the gate.

### What Jev decided

Every decision is in `orchestration/decisions/jev-audit.jsonl`.

| Question | Verdict | Selected | Confidence | Audit id |
|---|---|---|---|---|
| Fail the job, or warn? | pass | `fail_the_job` | 0.98 | `jev-20261003T035005-26256-05268` |
| Total, per-file, or both? | **uncertain**, then pass | `total_floor_with_touched_file_report` | 0.29, then 0.98 | `jev-20261003T035944-7856-84791`, `jev-20261003T040119-3756-79213` |
| Whole package, or touched files? | **uncertain**, then pass | `whole_package_gate_with_touched_file_report` | 0.64, then 0.99 | `jev-20261003T040135-26636-95267`, `jev-20261003T040747-3204-67531` |
| How to combine under xdist? | **uncertain**, then pass | `pytest_cov_dist_no_core_source` | 0.52, then 0.98 | `jev-20261003T040904-27596-44047`, `jev-20261003T041354-22944-34922` |

**Three verdicts came back `uncertain` and I did not override any of them.** All
three were resolved by gathering more evidence, not by rephrasing the question
until it agreed with me.

The gate-shape question returned `uncertain` at confidence 0.29 with a margin
of 0.21 over the runner-up. What it had not been given was the cost of a
per-file floor. I measured that a floor at 80 fails 13 files today, that a
floor at 95 fails 131, that one abandoned large file costs 0.76 points of the
total, and that bringing every sub-90 file to 100 percent would lift the total
only to 96.34. With that evidence it returned `pass` at 0.98.

The measurement-scope question returned `uncertain` at 0.64. What it had not
been given was what a scoped run actually reports. I measured it:

    pytest tests/test_wf001.py tests/test_wf002.py tests/test_wf003.py -n 0 --cov=dsr
    168 tests of 11127
    29.78 percent

**A run of 1.5 percent of the suite reports 29.78 percent, not 94.89.** A gate
that ran only the touched tests could not produce the agreed number at all. It
would have to change the measured target per pull request, and its number would
not be comparable to the floor or to any earlier run. With that, it returned
`pass` at 0.99.

The combining question returned `uncertain` at 0.52, and the near-tie was
between adding `COV_CORE_SOURCE=dsr` and not adding it. That one is settleable
by measurement, so I ran the suite a third time with `COV_CORE_SOURCE=dsr` set.
All three reports came back identical. With that, it returned `pass` at 0.98
for the simplest option: one pytest command, no environment variable, no extra
combine step, and no serial pass of the suite.

The fail-or-warn question was answered with the rule, not with taste:

    gh api repos/n-dilipkumar/client-theater/branches/main/protection
    {"message":"Branch not protected","status":"404"}

The main ruleset lists zero required status checks, so a failed job does not
block a merge today. Jev still chose `fail_the_job` at 0.98, and the reason is
in the state I gave it: the same workflow file records the house position that
a check which reports and passes is worse than no check. A warning would have
been the fourth defect in section 8 wearing a new hat.

### The pull request comment

`tools/coverage_comment.py` renders the body from the report and a changed-file
list. It runs from the repository root with no arguments beyond two paths, so a
human reads the exact body before it is merged. `actions/github-script@v7`
posts it. That action ships with the runner, so it adds no dependency and no
lockfile.

The body carries the marker `<!-- ci-coverage-gate -->`. The posting step finds
a comment with that marker and updates it in place, so forty pushes produce one
comment. The step refuses to post a body without the marker, because a format
change would otherwise silently turn the sticky comment into one new comment
per push.

When the total is below the floor the first line says so:

    ### Coverage is below the floor. Total 40.00 percent against a 90.0 percent floor.

A pull request from a fork gets a read-only token, so the comment cannot be
written. The step catches that and states it in plain words on the run page and
in the job summary, naming the fork and the GitHub error. It does not fail the
step, because the gate is the job above and a fork is a known limit of the
`pull_request` trigger rather than a coverage result.

### Two things I got wrong, and one I got wrong twice

**I corrupted a measurement by running two coverage runs at once, and then I
wrote down the wrong conclusion about it.** While the serial coverage run was
in flight I started a scoped coverage run in the same working tree. Both use
`backend/.coverage`. The scoped run reported `no such table: line_bits` and
`Failed to generate report`.

At the moment I wrote this section down I believed round two of the timing
series was lost to that collision, and I wrote "round two is worthless and I
discarded it". **That was wrong.** Round two finished later, after the collision
had passed, and all three of its runs are valid. All six runs agree on the
coverage number. I discarded nothing.

The serial run had already written its report, so the decisive comparison
survived regardless, and I copied every report out of the working tree before
anything could overwrite it.

The rule this adds: **a second coverage run in the same working tree needs its
own `COVERAGE_FILE`.** The next measurement I started did exactly that and lost
nothing. Point `COVERAGE_FILE` at a different path, or do not run two coverage
runs in one tree.

This is section 8 defect 3 wearing different clothes. `git stash push` printed
success and left nothing. `pytest --cov` printed a warning and produced no
report. Both printed something and did not do the thing.

**I published a coverage cost I could not measure, and I withdrew it.** I first
wrote that coverage tracing cost 1.2 seconds, by comparing one covered run at
231.84 s against one uncovered run at 230.65 s. Round two then produced an
uncovered run at 118.19 s, which is faster than either covered run. The honest
statement is that the run-to-run spread on this host is 112 seconds and the
coverage cost is smaller than that, so this host cannot resolve it at all. The
number is in the runner table at the end of this section instead.

**The documented floor of 2553 does not reproduce, and I did not explain it.**
I measure 2559 on commit `0b57667` in this worktree across six runs, all six
reports byte-identical. The runner measures 2560 on the same commit under
CPython 3.12.14 on Linux. The document says 2553. That is a spread of 7
statements across three environments.

I ruled out what I could and I am not going to guess past that. Ruled out: the
`frontend/dist` question in section 2, because `frontend/dist` is absent here,
which is the 2553 case and not mine. Ruled out: xdist, because the serial and
parallel reports are identical. Ruled out: `COV_CORE_SOURCE`, because the report
is identical with and without it.

What I did establish is that **the number is platform and version dependent**,
which section 2 does not say. Two environments I control measure 2559 and 2560.
So "the floor" is not one number until someone fixes the interpreter and the
operating system in the same sentence as the percentage.

The number to defend from here is **2560 on the runner**, because the runner is
what the gate runs on, and 2559 in a Windows worktree. Both are 4.88 points
above the floor, so the discrepancy cannot move the gate either way. The
orchestrator should correct section 2 and record which environment 2553 came
from, or record that it cannot be reproduced.

### One more instance of the section 7 flake, now on a runner

Run `37096412514` on this branch went red on the backend job. The same code had
been green on run `37096073587`. The only difference between the two commits is
this document, which coverage does not measure and no test reads.

    tests/test_wf069.py::TestOneTimeCodeHashing::test_codes_do_not_repeat_in_a_small_sample
    assert len(minted) == 200
    E   AssertionError: assert 199 == 200

**This is the flake section 7 already measured, not a new defect.** The proof is
in the source, not in a guess. `mint_code` is
`str(secrets.randbelow(10**digits)).zfill(digits)`, so the code space is
1,000,000. The test takes 200 samples and asserts all 200 differ. The birthday
probability of at least one collision is about 1.97 percent, and section 7
measured it at 1.75 percent over 2,000 real calls. This run drew exactly one
collision.

**The coverage gate passed in the same job.** The log shows
`TOTAL 50045 2560 95%` and the gate reported 2560 missed, identical to the
previous run. The job went red because a test failed, not because coverage fell.

I did not touch `backend/tests/test_wf069.py`. That path is another agent's
territory, and section 7 says the honest fix is to seed the random source rather
than to widen the sample or delete the assertion. I re-ran CI instead, which is
the correct response to an event that happens about twice in every hundred runs.

### The job time, before and after

**Merged note, 2026-10-03.** The speed agent landed PR 100 while this branch was
open, and it edited `.github/workflows/ci.yml` and this file. The comment
markers did what they were put there for: the conflict was textual and the
resolution was mechanical. Two things changed and both are deliberate.

1. The speed agent's `Run the suite` step is **absorbed, not deleted**. Two suite
   runs would double this job to measure one number, and the second run would
   report a different coverage figure from the one the gate has just judged. Its
   measurement comment is kept verbatim inside `# >>> SPEED AGENT NOTE` so the
   reasoning survives.
2. Both agents had written a section 9 here. The speed agent's is now section 10,
   and the two cross references in `ci.yml` were repointed at it. Nothing was
   dropped.

Read from GitHub Actions. Same runner image, same 11,129 tests, all passing.

| Run | Branch | Backend tests job | What changed |
|---|---|---|---|
| `37053160255` | `main` | **118 s** | `python -m pytest`, no coverage |
| `37096073587` | `ci-coverage` | **221 s** | same suite, plus `--cov=dsr`, plus the gate |

**Coverage costs about 103 seconds on the backend job, an 87 percent increase.**
The local host cannot see this at all, and its run-to-run spread on identical
code is 112 seconds. This is the clearest argument in the document for the rule
section 7 states: a local number and a runner number are not the same
measurement, and only the runner is reproducible.

The pip cache does restore. The log shows
`Cache restored from key: setup-python-Linux-x64-24.04-Ubuntu-python-3.12.14-pip-...`,
so `cache-dependency-path: backend/pyproject.toml` is the right key and it is
being honoured. Part of the 103 seconds is the cache miss on the first run that
introduced it, and part is coverage tracing. I did not separate the two, because
separating them needs a second runner run with the cache already warm and the
speed agent owns CI timing.

### What the runner actually measured

The gate ran on the runner and read the runner's own report:

    measured total   : 94.88460 percent
    agreed floor     : 90.0 percent
    covered          : 47485 of 50045 statements
    missed           : 2560 statements
    files measured   : 399
    files below floor: 45
    files this change touched: 0 measured of 6 changed
    PASS. Coverage 94.88460 percent is at or above the 90.0 percent floor.

    11127 passed, 2 xfailed, 5 warnings in 193.75s

**The number is platform and version dependent, and now I have both ends of that
measured rather than assuming it.**

| Where | Interpreter | missed | percent |
|---|---|---|---|
| this worktree, Windows | CPython 3.13.15 | 2559 | 94.88660 |
| the runner, Linux | CPython 3.12.14 | 2560 | 94.88460 |
| `orchestration/TEST-REFACTOR.md` section 2 | unrecorded | 2553 | 94.89859 |

One statement is platform dependent. Both measured numbers clear the 90 percent
floor by about 4.88 points, so the gate behaves the same on both.

### What I could not do

* I did not reproduce 2553. The spread across three environments is 7
  statements. I established that the number is platform and version dependent
  and I stopped there.
* I did not separate the cost of coverage tracing from the cost of the first
  cold pip cache on the runner. Both landed in the same 103 seconds.
* I did not add a coverage gate to the frontend. `@vitest/coverage-v8` is not
  installed and the existing comment in `ci.yml` records that as a decision for
  a human. This gate is the backend package only.
* I did not add `backend/.coverage` or `backend/*.json` to `.gitignore`. That
  file belongs to whoever owns it, and the report artefacts are written only on
  a runner. They are left out of every commit here by explicit path rather than
  by ignore rule.
* I did not add a required status check. That is a repository setting, not a
  file, and until `main` is protected a red coverage job does not stop a merge.
  **The gate is only as strong as the ruleset, and the ruleset is still empty.**
  That is the one thing left to do, and it needs a human with admin rights.

---

## 10. Agent SPEED: what the CI workflow actually costs

I wrote this section for the next agent who gets the same task. Read the numbers
before forming an opinion. Five of the six candidates in the speed brief are
settled below. The brief expected four of them to pay. None of them does.

Every number comes from GitHub Actions step timings, never from this host. Runs
`37009181528` through `37060049338`, twelve runs of `.github/workflows/ci.yml`,
runner image `ubuntu-24.04 20260927.320.1`. This host has three other agent suites
on eight cores, so a local duration is not a comparable number.

### The jobs, as measured

Median of twelve runs, whole seconds as GitHub reports them.

| Job | median | min | max |
|---|---|---|---|
| Backend tests | 143 | 107 | 217 |
| Local host app (headless) | 44 | 42 | 74 |
| Lint (ruff, eslint, prettier) | 44 | 25 | 53 |
| Frontend build | 30 | 19 | 40 |
| Feature contract (no shared files) | 8 | 4 | 9 |
| Design floor | 7 | 5 | 8 |

That is 276 job-seconds per run. **The Backend tests job is the slowest job in all
twelve runs, so it alone sets the wall clock.** The other five run in parallel with
it, and they cost runner minutes only.

Its 143 seconds break down as: checkout 3 s, setup-python 0 s, install backend
10 s, `python -m pytest` 125 s. **One second off pytest is one second off the wall
clock. One second off any other job is not.**

### The six candidates, settled

**1. Share one `npm ci` across the three jobs. Rejected.**
`npm ci` takes **4 seconds**. Median of 35 step samples across the three jobs,
range 2 s to 6 s. The brief guessed 40 s. Consolidating saves 8 runner-seconds and
0 wall-clock seconds. The repository is public, so GitHub-hosted Linux minutes cost
nothing and the saving has no price. Every mechanism for sharing the tree costs
more than 8 s: `actions/cache` cannot save from three parallel jobs that all miss
it, upload-artifact plus download-artifact costs about 7 s for a 400 MB
`node_modules`, and combining the jobs lengthens the critical path.

**2. `setup-node` with `cache: npm`. Already working. No change.**
All 35 attempts logged `Cache hit for: node-cache-Linux-x64-npm-c3b3f664...`.
Cache size about 33 MB. The key hits, so there is nothing to correct here.

**3. Add `cache: pip` to the guard, backend and end-to-end jobs. Rejected.**
This runs against the brief's expectation. Only the lint job has the cache today,
and the cache does not pay for itself.

| Step | n | min | median | max |
|---|---|---|---|---|
| Lint, `Install Python linters`, has cache | 12 | 7 | 9 | 11 |
| Backend tests, `Install backend`, no cache | 12 | 6 | 10 | 13 |
| End-to-end, `Install backend`, no cache | 12 | 8 | 10 | 28 |
| `setup-python` step, has cache | 12 | 1 | 2 | 4 |
| `setup-python` step, no cache | 12 | 0 | 0 | 1 |

The cached install is 1 s faster at the median, and the two distributions overlap,
so that 1 s is inside the noise. `setup-python` with a cache costs 2 s median
against 0 s without one. **Net effect about minus 1 second per Python job.** The
install resolves 34 packages in about 10 s, so the download is a small part of it
and the cache restore costs more than the download it replaces.

**4. Combine jobs to cut machine allocations. Rejected.**
Lint takes 44 s and Frontend build takes 30 s today, in parallel, so 44 s of wall
clock. One combined job would take about 63 s, which is the sum of the steps the
combined job keeps. **That trades 19 seconds of wall clock for 11 free
runner-seconds.** The lint job's own comment in `ci.yml` argues for the separate
name, and the measurement supports the comment.
Guard and Design floor could fold for 9 runner-seconds. That merges two named
checks to save something that costs nothing.

**5. Path filters. Rejected, and not because of the fraction.**
The fraction is large. **37 of the 60 pull requests in this repository, 62 percent,
touch no file under `frontend/`.** The Frontend build job is therefore unneeded
most of the time. It still saves **0 wall-clock seconds**, because that job takes
30 s and the critical path takes 143 s. Two further reasons, both worse than the
saving:

* A job that a path filter skips reports as **skipped**, and a skipped required
  status check blocks the merge instead of passing it.
* `paths:` filters on `pull_request` do not apply to `push: main`, so the two runs
  would stop checking the same things. The pull request run would check less than
  the push run. That is a smaller gate wearing a speed label.

**6. Concurrency. Verified correct. No change.**
`github.ref` reads `refs/pull/N/merge` for a pull request and `refs/heads/main` for
a push, so one group never mixes the two events. `cancel-in-progress: true` is
right for both, for the reason the existing comment gives.

### Where the 40 seconds actually are

This is the part that matters for whoever takes the work next.

In the Backend tests job the pytest step starts at t+13.1 s, and pytest-xdist
prints `bringing up nodes...` at t+52.8 s. **That gap is 39.7 s. It is 32 percent
of the 125 second job.** It covers collection and worker startup, and it lives in
`backend/`. A workflow file cannot reach it.

pytest-xdist collects the whole suite once per worker and once on the controller.
On a 4 vCPU runner that is five collections before a single test body runs. That is
the price of `-n auto`, and it is why a larger worker count is not free.

### The worker count, measured twice

Someone measured `-n auto` in `backend/pyproject.toml` on an 8-core workstation
only. A GitHub runner has 4 vCPU, so the count deserved a check on the machine
that actually runs it.

My first attempt was one CI run with `-n 6`. The suite step came back at 93.9 s
against a baseline median of 112.9 s, which reads as a 19-second win. **I did not
believe it and I did not report it.** Eleven single-run baselines spread from
90.0 s to 176.5 s, and 93.9 s is the fourth lowest of twelve samples. Section 8
already records identical code varying by 47 s. One sample cannot separate a
20-second effect from an 86-second spread.

So I ran both arms inside one job, on one runner, alternating, three rounds each.
Run `37095600890`. pytest's own reported seconds:

| round | arm | pytest s | start plus collect s |
|---|---|---|---|
| 1 | auto | 99.91 | cold first run on a fresh runner |
| 2 | 6 | 77.50 | 15.5 |
| 3 | auto | 73.56 | 10.9 |
| 4 | 6 | 78.34 | 16.1 |
| 5 | auto | 73.84 | 11.0 |
| 6 | 6 | 78.48 | 15.5 |

Dropping round 1, which paid the cold-cache premium:

| arm | warm minimum | median start plus collect |
|---|---|---|
| auto | 73.56 s | 11.0 s |
| 6 | 77.50 s | 15.5 s |

**`-n auto` won by 3.9 seconds at the warm minimum, and the two arms did not
overlap once warm.** Rounds 3 to 6 are two clean pairs: 73.56 and 73.84 for
auto, 78.34 and 78.48 for 6.

The mechanism is in the last column. Collection scales with the worker count,
because pytest-xdist collects the whole suite once per worker and once on the
controller. Six workers cost 4.5 s more of collection than four workers did, and
they buy about the same amount back in the test phase. So the extra workers trade
one cost for an equal cost, which is not a win.

All six rounds reported `11127 passed, 2 xfailed`, so 11,129 collected every time.
There was no flakiness at six workers either.

**`-n auto` stays. The decision in `backend/pyproject.toml` transfers to a 4 vCPU
runner, and I measured it there.**

### The 26 seconds nobody can spend

Round 1 took 99.91 s. The warm rounds took 73.6 s. **About 26 seconds of every CI
run is a cold-cache premium**: the runner has just written 34 packages to disk and
has not read them once. A single suite run per job can never be warm, so this cost
is structural rather than fixable inside one run.

That also explains most of the bimodal baseline. The eleven single-run baselines
spread 90.0 s to 176.5 s. The warm floor is 73.6 s. So roughly 16 s of that spread
is cold start, and the rest is runner variance.

For whoever optimises this suite next: the number to beat is **73.6 s warm**, not
the 125 s the CI job reports. And the fixture work has more headroom left than the
CI number suggests, because the CI number carries a 26-second constant in it.

### The variance that stopped me trusting one number

The `Run the suite` step over eleven baseline runs:

    94  98  99  101  111  119  125  134  161  172  181

Spread 87 seconds. The code did not change across most of those runs. My first
experiment set `-n 6` and the suite step came back at 98 s, which reads as a
21-second win against the median. **It is not evidence.** 98 s is the third lowest
of twelve samples. Section 8 already records identical code varying by 47 s, and
here it varies by 87 s. One sample cannot separate a 20-second effect from that.

So the real measurement runs both arms inside one job, on one runner, alternating,
three rounds each, and takes the minimum of each arm. The minimum is the least
contaminated sample, and the alternation keeps any drift in runner load shared
between the arms.

### Jev

Four decisions. All four are in `orchestration/decisions/jev-audit.jsonl`. None
returned `uncertain`.

| Decision | Audit id | Verdict | Confidence |
|---|---|---|---|
| Consolidate the three `npm ci` runs | `jev-20261003T035808-23636-88981` | reject | 1.00 |
| Combine jobs | `jev-20261003T035809-23636-89267` | reject | 0.90 |
| Add path filters | `jev-20261003T035809-23636-89578` | reject | 1.00 |
| Best single change of the six | `jev-20261003T035809-23636-89907` | change no job, record the measurement | 0.84 |

On the job-combination decision Jev returned 0.41 for "can this be checked against
a number that already exists". That answer is right. The 63 second combined figure
is arithmetic on step timings, not a measured job. It is the weakest number in this
section, and this section labels it an estimate.

### Two facts about this repository that change the question

* **`n-dilipkumar/client-theater` is public.** GitHub-hosted Linux runner minutes
  cost 0. So "duplicate work costs money and runner minutes" does not hold here.
  The only currency is wall clock, and the Backend tests job sets the wall clock.
* **Every merge runs the whole workflow twice**, once for the pull request and
  once for the push to main. That is about 276 job-seconds per merge, and it is
  deliberate: the lint job's comment explains that main must get checked, and the
  guard already skips on main. It is also larger than every candidate in the brief
  put together.

### The environment fact that cost me time

The heartbeat command in the speed brief does not run in this agent shell. The
shell rewrites every double quote in a command as a backslash-quote, so

    orca orchestration send --subject "HEARTBEAT" --to run:run_a753c94f0894 ...

loses its opening quote, and the `|` characters turn into shell pipes.
`gh pr create --title "a b c"` fails the same way, with `unknown arguments`. The
workaround is to pass the argument vector from a file, so no shell quoting takes
part. I sent every heartbeat in this section that way, and Orca delivered them.

I did not change any shared tooling for this. Section 6 of this file already warns
that the heartbeat command is fragile. This is the same warning with the exact
failure mode attached.
