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
