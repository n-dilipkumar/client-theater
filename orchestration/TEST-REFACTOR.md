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

## 9. Agent SPEED: what the CI workflow actually costs

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

- 2026-10-03 — **Agent: security. Five CI checks measured before adoption, three
  adopted as gates.**

  ## What landed

  `.github/workflows/security.yml`, three jobs. Every number below came from
  running the tool against this repository.

  | Job | Command | Measured | Verdict |
  |---|---|---|---|
  | `secret-scan` | gitleaks 8.30.1, 206 commits | 0 leaks, 2.5 s | gate |
  | `dependency-audit-python` | `pip-audit --local` | 37 packages, 0 vulns, 4.2 s | gate |
  | `dependency-audit-node` | `npm audit --omit=dev` | 0 vulns, exit 0 | gate |

  Plus two edits to `ci.yml`: all 14 `uses:` pinned to commit SHAs, and
  `permissions: contents: read` stated per job instead of inherited.

  ## The gitleaks false positives, all seven read by hand

  gitleaks 8.30.1 (checksum matched the published hash before the binary was
  trusted) reported 8 findings over 206 commits and 7 in the tree. Every one is
  a false positive:

  | Count | What | Why it is not a secret |
  |---|---|---|
  | 4 | `password: "northwind-2026"` | demo seed literal for the WF-069 link-gating demo |
  | 2 | `_SECRET_ALPHABET = "23456789...xyz"` | the alphabet a one-time code is drawn from; entropy 5.83 is why it trips |
  | 2 | `Authorization: Bearer S2S_TOKEN` | a placeholder in two research documents |

  Zero real secrets. `.gitleaks.toml` removes exactly those seven, scoped by
  path and by value, each with its reason in the file. After it: `no leaks
  found` across all 206 commits.

  A directory scan on a dirty machine reports an eighth finding that the history
  scan cannot see: a `__pycache__/vocabulary.cpython-313.pyc`. So the job scans
  git history, not the directory.

  ## The npm audit scope finding, which is the useful half

  Two commands, same lock file, same tree, both correct:

      npm audit              -> exit 1, 5 findings (1 critical, 1 high, 3 moderate)
      npm audit --omit=dev   -> exit 0, 0 findings

  The dependency counts are identical in both runs (prod 6, dev 384, optional
  122, peer 8, total 389). The tree is not the variable. The scope flag is.

  All five findings are devDependencies: vitest, vite, vite-node, esbuild,
  @vitest/mocker. None reaches the shipped bundle. So the gate is the production
  scope, which is green today and is what a buyer would actually run, and the
  dev-tree count is published into the job summary beside it so the flag that
  makes the job green cannot hide the findings it excludes.

  **This is the fifth way an audit number in this project was wrong.** The
  Orchestrator measured 0 vulnerabilities and asserted twice that my 5 was
  wrong. Its own cause, found afterwards: the main repo's `node_modules` had
  drifted from `package-lock.json` (vite 6.4.3 installed against 5.4.21 locked,
  esbuild 0.25.12 against 0.21.5, `@vitest/mocker` absent entirely). A stale
  working copy is the same failure as a single timing sample: one observation,
  treated as the state of the repository.

  **The rule that came out of it.** An audit is a statement about a resolved
  dependency set. Before believing anyone's audit number, name the set it was
  computed from. `npm ci` first, then audit. A `node_modules` that was installed
  at some other time is not the lock file, and the difference is invisible
  unless you look.

  I re-measured three ways to rule out my own cache: plain `npm audit`,
  `npm audit --prefer-online`, and `npm audit --cache <a directory that had
  never existed>`. All three exit 1 with the same five findings.

  ## Jev: four decisions, three of them escalated rather than decided

  AGENTS.md requires a typed judgment for each decision. Two came back
  `uncertain` and a third only cleared after the evidence was corrected. None of
  the three was overridden.

  | # | Decision | Verdict | Confidence | Audit id |
  |---|---|---|---|---|
  | Q1 | which candidate checks to adopt | **uncertain** | 0.47 | `jev-20261003T035709-25144-29024` |
  | Q2 | npm policy, first ask | pass, premise later disproved | 0.78 | `jev-20261003T035709-25144-29351` |
  | Q3 | pin actions to SHAs | **uncertain** | 0.43 | `jev-20261003T035709-25144-29634` |
  | Q4 | gitleaks false-positive handling | **pass** | 0.78 | `jev-20261003T035710-25144-30149` |
  | Q2 | npm policy, re-asked with exit codes | **uncertain** | 0.43 | `jev-20261003T035819-25208-99319` |
  | Q2 | npm policy, re-asked with scope evidence | **pass** | 0.88 | `jev-20261003T041547-26400-47636` |

  **Q2 is the one worth reading twice.** The first ask passed at 0.78 and Jev
  selected an option whose stated premise was that `--audit-level=critical`
  passes today. Measured, every threshold exits 1, because the tree has a
  critical finding. A `pass` verdict is not a licence to skip the measurement
  behind it. The second ask, on the real exit codes, came back `uncertain` at
  0.43. Only the third, which told Jev that all five findings are
  devDependencies, cleared at 0.88.

  So the sequence was: an unmeasured claim in my own option text -> a `pass`
  built on it -> measurement -> `uncertain` -> corrected evidence -> `pass`.
  **Three Jev rows exist for one decision because the state changed twice.** The
  log is append-only and all three stay. That is what it is for.

  Q1 and Q3 remain `uncertain` and are escalated to the Orchestrator, not
  decided by me. Q3 in particular is why this branch pins SHAs but adds no
  Dependabot: adding an updater changes who opens pull requests in this
  repository, and that is a human's decision.

  ## Action SHAs, and why the risk was small here

  Each SHA was resolved by asking the GitHub API which commit a *fixed patch
  tag* points at, not by reading a web page:

  | Action | Version | SHA |
  |---|---|---|
  | `actions/checkout` | v4.4.0 | `11d5960a326750d5838078e36cf38b85af677262` |
  | `actions/setup-python` | v5.6.0 | `a26af69be951a213d495a4c3e4e4022e16d87065` |
  | `actions/setup-node` | v4.4.0 | `49933ea5288caeca8642d1e84afbd3f7d6820020` |

  Every floating major tag resolved to the same commit as the newest patch tag
  in its series, so pinning changed no behaviour on the day it landed. The
  trailing comment names the version, so a wrong comment is visible on sight.

  ## Two things I got wrong, both worth recording

  1. **A passing verdict on a false premise went into a permanent log.** I wrote
     "the critical finding is test-only" into a Jev option, believed it, and
     built a decision on it. It happened to be true. I had not measured it. If it
     had been false, the audit log would now carry a confidently-worded wrong
     claim with a probability attached, and no later reader could tell.
  2. **I read `jq` as available in CI and never checked.** I removed it in
     favour of `node`, which `setup-node` guarantees and which I can then test
     locally. I only found the problem because I extracted the step from the
     YAML and ran it under bash instead of trusting that it looked right. It did
     not look right: it would have crashed the build on a reporting step.

  The second one is the method I would keep. **Extract the step from the file and
  run it.** A shell step that has never been executed is a guess with YAML
  indentation.

  ## State at hand-off

  Both workflow files parse under PyYAML. All 17 `uses:` resolve to 40-character
  SHAs. `ruff check` and `ruff format --check` pass from the worktree root with
  `--config backend/pyproject.toml`. The backend suite is 11,127 passed and 2
  xfailed, which is the 11,129 the baseline records.

- 2026-10-03 — **Agent: security, addendum. A step that passed locally and died
  in CI, and the reason my test did not catch it.**

  The first run of PR #103 was 8 green, 1 red. `Dependency audit (npm)` failed in
  12 s on the reporting step. From the log:

      shell: /usr/bin/bash -e {0}
      ##[error]Process completed with exit code 1.

  GitHub runs every `run:` block as `bash -e {0}`. **Errexit is already in force
  before the first line of the script runs.** My step opened with:

      set -uo pipefail
      npm audit --json > npm-audit-full.json

  That reads like "do not exit on error" and is not. `set -uo pipefail` *adds*
  `-u` and `pipefail`. It does not remove an `-e` the runner turned on before
  the script started, and no `set` line inside the script can pre-empt a flag
  already on the command line. So `npm audit` exited 1 on the second line and
  killed the step, which is precisely the thing the step existed to prevent.

  The fix is `set +e`, which does take effect at runtime.

  **Why my local test passed it.** The harness ran `bash step.sh`. The runner
  runs `bash -e step.sh`. One flag apart, and it is the flag that decides
  whether this script works. A local test that does not reproduce the runner's
  invocation is not a test of the runner's behaviour, and the gap showed up as a
  green local result and a red CI result on the same commit.

  The harness now runs `bash -e step.sh`, and I checked that this makes it
  meaningful rather than assuming it:

      set -uo pipefail; false   under bash -e   -> exit 1, never reaches the next line
      set +e; set -uo pipefail; false  under bash -e   -> exit 0, reaches the next line

  **The rule, and it is the one I would hand to the next agent.** When you
  extract a CI step and run it locally, copy the runner's invocation, not just
  the script. `bash script.sh` and `bash -e script.sh` are different programs.
  And when a local test disagrees with CI, suspect the harness before you
  suspect the code: this repository has now produced "CI is red" twice, and both
  times the red was correct and the local green was the thing that was wrong.

  A step that has never been executed is a guess with YAML indentation. A step
  that has been executed with the wrong shell flags is only slightly better.

<!-- security-agent-addendum-bash-e -->
