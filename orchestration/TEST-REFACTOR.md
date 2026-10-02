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

## 6. Progress log

Append here. Newest last.

- 2026-10-02 — Orchestrator. Baseline measured and recorded. Four agents
  dispatched.

- 2026-10-03 — Agent `harness` (branch `perf-test-harness`, commit "Add shared
  test fixtures and run the suite in parallel"). Two files changed:
  `backend/tests/conftest.py` (new) and the `[tool.pytest.ini_options]` and
  `dev` sections of `backend/pyproject.toml`. No `test_*.py` file was touched.
  No application source was touched.

  ### What the fixtures are for

  58 of the 75 test modules built a `TestClient` inside each test. Entering one
  runs the application lifespan, and the lifespan is the expensive half. A test
  needs a fresh **database**, not a fresh **application**, because `get_store`
  reads `request.app.state.store` on every request. So `module_client` enters
  one client per module and `client` swaps `app.state` per test.

  Fixture names match what the suite already calls things, so adopting one is a
  deletion, not a rename: `client`, `http`, `db`, `db_path`, `store`,
  `memory_db`, `module_client`. A module that keeps its own fixture of the same
  name keeps it, so **no existing test changed behaviour**. As of this commit
  no module has adopted them yet. That is the next agent's work, and it is
  where the remaining speed is.

  Two autouse guards back this up:

  1. `DSR_DB_PATH` and `DSR_AUDIT_DIR` are pointed at the test's own `tmp_path`
     when a test has not set them. Every current module sets both, so this
     changes no existing test. It stops the next module from silently reading
     and writing the real `data/dsr.db`.
  2. `app.dependency_overrides` is emptied before each test. It is the
     documented seam for replacing a service and it is process-wide. Several
     modules clear it in teardown and several do not (`test_wf034.py` installs
     overrides at lines 2065 and 2084 with no teardown). Clearing before each
     test can only remove state, never add it, so it cannot manufacture a pass.

  ### Measured fixture cost

  200 iterations per strategy, minimum of four interleaved rounds, with a
  no-op-fixture run subtracted as the floor. Whole-suite runs, not samples:

  | Strategy | per test |
  |---|---|
  | A  file db + full lifespan per test (today) | 27.50 ms |
  | B  in-memory db + full lifespan per test | 8.15 ms |
  | C  module client + `app.state` swap, file db | 19.55 ms |
  | D  session client + `app.state` swap, file db | 23.75 ms |
  | E  module client + `app.state` swap, in-memory db | 1.35 ms |

  **Module scope beats session scope for a structural reason, not only a
  timing one.** A session-scoped client outlives every test in it, so one test
  can leave `app.state` pointing at a closed database for every test after it.

  Correction to an earlier claim in this document: the audit mirror is not a
  meaningful cost. Re-measured interleaved, a file db **with** a mirror is
  3.41 ms and the same db **without** one is 4.03 ms. The spread between the
  fastest and slowest round on identical code reached 245x, because four suites
  were sharing eight cores. Read the minimum, never the mean.

  ### pytest-xdist

  Added `pytest-xdist` to the dev extras and `-n auto` to `addopts`. The
  machine was busy the whole time with three other suites, so read these as
  ratios, not as absolute CI predictions. CI runs on an isolated runner and
  will do better.

  | Configuration | Runtime | Result |
  |---|---|---|
  | serial, unmodified tree | 708.3 s | 11127 passed, 2 xfailed |
  | `-n 8 --dist load` | 237.1 s | 11127 passed, 2 xfailed |
  | `-n 8 --dist loadscope` | 269.4 s | 11127 passed, 2 xfailed |
  | `-n 8 --dist loadfile` | 393.8 s | 11127 passed, 2 xfailed |

  After rebasing onto the merged core-test PR, three runs of plain
  `python -m pytest`, which is what CI runs: **226.40 s, 247.61 s, 268.86 s**.
  Minimum 226.40 s against a 708.3 s baseline on the same machine.

  `load` is the default and it was fastest with no failures, so it is left
  alone. Every configuration exited 0 with the same test count. The 9 warnings
  are the one pre-existing `StarletteDeprecationWarning`, once per worker
  process, not 9 new problems.

  ### Coverage: the recorded 94.91% does not reproduce

  Section 2 records 94.91%, 2546 missed of 50045. **On this machine at this
  commit the same figure measures 94.8986%, 2553 missed.** Five independent runs
  agree, and three of them cannot be blamed on this change:

  | Run | Missed |
  |---|---|
  | pristine tree, serial (`-n 0`), no conftest | 2553 |
  | pristine tree, `-n auto`, no conftest | 2553 |
  | this branch, `--dist load` | 2553 |
  | this branch, `--dist loadfile` | 2553 |
  | this branch, autouse guards removed entirely | 2553 |

  Same 279 files with gaps, same per-file counts. So the change costs **zero**
  coverage, and the 7-statement gap against section 2 is pre-existing here
  rather than something this branch introduced. Coverage is invariant to the
  distribution mode, which also answers the "does xdist hide order-dependent
  tests" question by measurement: it does not change what is covered.

  `pytest-cov` was missing from the dev extras, so the coverage command in
  section 2 could not run on a clean install. It is now there.

  ### Which tests genuinely need a file on disk

  Kept file-backed, with the reason:

  * `test_persistence_contract.py` and `test_wf001.py` reopen the **same path**
    in a second `AuditedDatabase` and read the first one's rows back. The data
    has to outlive the connection, which `":memory:"` cannot do at all.
  * `test_seed.py` points `DSR_DB_PATH` at a nested path whose parent directory
    does not exist, then runs `backend/seed.py` in a subprocess. It needs a real
    filesystem and a real `sqlite3.connect`.
  * `test_audited.py`, `test_wf002.py`, `test_wf009.py`, `test_wf033.py` open
    file-backed databases directly in their own fixtures.

  **The mirror is not one of them.** The JSONL audit mirror is written to
  `mirror_dir`, which is a directory, independently of where the database lives.
  So a test that only reads `audit-*.jsonl` needs a real `mirror_dir` and does
  **not** need a file-backed database: `AuditedDatabase(":memory:",
  mirror_dir=tmp_path / "audit")` covers it. That is a larger set than it looks,
  and it is why `db` sets both and `store` deliberately sets neither.

  Three things `:memory:` genuinely cannot do, for whoever converts next:

  1. Reopen the path in a second connection.
  2. Use WAL. `_connect` skips the `journal_mode` pragma for `:memory:`.
  3. Outlive the process. No test currently asserts on `journal_mode`, so this
     is a mechanism to be aware of rather than a test to preserve.

  ### What I could not do

  * **No module was converted.** Strategy C is measured and available, and it is
    roughly a 14x cut on the fixture cost of the 58 modules that build a
    `TestClient` per test, but converting them means editing files three other
    agents own. That is the next agent's work and the biggest remaining win.
  * **The 2,546 figure is unexplained.** It is not this branch, not the
    distribution mode, and not the conftest. It is most likely a different
    environment on the machine that recorded it. Anyone who needs a coverage
    gate should re-record the baseline on the runner rather than trust 94.91%.
  * **Order dependence was not hunted down.** No test failed under any
    distribution mode, and coverage did not move, so nothing broke. That is
    evidence, not proof: a test that passes both ways while covering different
    lines would not show up in either number.