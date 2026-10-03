# Test refactor: agent core findings

Findings from the agent that owned the 17 non-`test_wf*` backend test files.
Its work landed in PR 95 (`d7f385f`). This document is the record that came out
of it, kept separate from the change itself because it is mostly about what the
measurements did *not* show.

Written for an agent who has not seen this work.

## The result, stated honestly

| | |
|---|---|
| Test count | 11,129 collected, **unchanged** |
| Coverage | 2,553 missed of 50,045, **zero lines lost** against a clean clone of the base commit |
| Runtime | 23.0 s better in `test_seed.py`; **not measurable** at the CI job level |
| Tests deleted | **none** |
| Assertions weakened | **none** |

## What changed, and why

`test_seed.py` ran `backend/seed.py` as a subprocess three times across two
tests. One test wanted a nested `DSR_DB_PATH` whose parent directory did not
exist to succeed. The other wanted the same path seeded twice and refused. Those
are two *answers from one run*, so a module fixture now seeds once and the
refusal test starts from a copy of the file that leaves behind. It is a module
fixture rather than shared module state, so either test still passes when it is
selected on its own.

Five files moved from a `TestClient` entered per test to one module-scoped
`TestClient`, with a fresh in-memory database swapped into `app.state.store` for
each test. `dsr.deps.get_store` reads that attribute on every request, so a fresh
database per test costs nothing extra. `test_roles.py` went in-memory.
`test_audited.py` was split: `db` is in memory, and `mirror_db` is file-backed
for exactly the three tests that glob for `audit-*.jsonl`.

`test_audited.py`, `test_persistence_contract.py` and
`test_ordering_determinism.py` kept their files on disk. The first two read the
audit mirror and reopen the database. The third pins a real ordering bug fix
where `list()` and `find()` ordered by `updated_at` with no tie-break, so rows
written in the same millisecond were ordered by a random uuid4.

## One bug found in my own work

`app` is a module-level singleton shared by every test file in one pytest
process. The first version of the converted fixture closed its database on the
way out without restoring `app.state`, so between the end of one module and the
start of the next, `app.state.store` pointed at a **closed** database. Any file
that read `app.state.store` without entering its own `TestClient` would have hit
it. That is the "passes alone, fails together" failure mode. The fixture now
saves and restores both attributes.

## The two measurements that matter

**Isolated, interleaved, three rounds, against a clean clone of the base
commit:**

| | samples | best |
|---|---|---|
| `test_seed.py` baseline | 73.3 s, 185.6 s, 126.9 s | 73.3 s |
| `test_seed.py` this branch | 55.6 s, 50.3 s, 66.4 s | 50.3 s |

**At the CI job level, the change is not measurable.** Backend tests was 194 s on
`main` at `bc999cc` and 197 s on this branch. That job is `pip install` plus all
75 test files, of which 58 belong to other agents, so a saving inside 17 files
is diluted below runner variance.

The honest summary is that the structural change is real and the job-level
benefit is not measurable yet. The pattern is worth considerably more once a
shared `conftest.py` applies it to all 75 files than it is worth inside 17.

**Machine disclosure.** Every wall clock above came from a host running four
agent suites at once. One unchanged seeder run measured 17 s, 27 s, 30 s, 32 s
and 38 s across five runs. One unchanged file varied by 113 s across three runs.
Do not read any single local wall clock on a loaded host as a clean measurement.

Coverage numbers are **not** affected by that. Coverage is deterministic, and
both trees were measured the same way.

## Coverage: the floor was wrong, and here is why

Both trees, same host, same interpreter, full suite, compared from coverage's
own JSON report:

| | covered | missed | coverage |
|---|---|---|---|
| baseline `main` at `bc999cc` | 47,492 | 2,553 | 94.89859 % |
| this branch | 47,492 | 2,553 | 94.89859 % |

Net new missed statements: **0**. Lines lost across all 401 files: **0**.

A floor of 94.91 % (2,546 missed of 50,045) was recorded in the shared document.
**That figure is not reproducible on a fresh clone.** The entire difference is
seven statements in `dsr/api.py`, at lines 299, 300, 301, 303, 304, 306 and 307.
Those sit inside a module-level guard:

```python
if FRONTEND_DIST.is_dir():
```

They mount the static assets route and define the SPA catch-all. Whether they
count as covered depends entirely on whether a built frontend exists on disk
when `dsr.api` is first imported. `frontend/dist` is a gitignored build
artefact, so a fresh clone never has it:

| tree | `frontend/dist/index.html` | those 7 lines |
|---|---|---|
| a tree where `npm run build` ran | present | covered |
| a fresh clone | absent | missed |

**Defend 2,553.** A branch that measures 2,546 is a tree where a frontend was
built, not a better result.

## The finding that outlives this change

**CI does not run coverage.** All six checks were green on this branch, and they
would have been green on a branch with a real coverage regression. The floor has
no enforcement behind it whatever its value.

Adding coverage to CI is a decision for a human: it costs a provider, a threshold
nobody has agreed, and a slower backend job. It should not be slipped into a
performance pull request, and it should not be left unwritten either.

## Four numbers I published and then retracted

1. A 47.32 s result presented as a baseline, then compared against a 110.45 s
   "baseline" taken under load, inverting the conclusion.
2. A 1.38 s module-scoped client enter. The real figure is 0.03 s.
3. A coverage run reported as dead when it had completed, because
   `tasklist | findstr python` returned nothing from a shell quoting bug.
4. A 7-statement coverage regression that did not exist, because I compared my
   branch against a figure in a document rather than against a measured baseline.

The first three share one cause: reading process and console state instead of
reading the output file. The fourth is the same instinct in a worse place,
comparing against a remembered number instead of a measured one. On this host the
shell also mangles quoted arguments, so `findstr` and `grep` with a quoted
pattern silently match nothing, which is what produced items 1 to 3.

## Not done, and why

**Test count is unchanged.** I looked for the duplication between
`test_access.py` / `test_access_api.py` and between `test_roles.py` /
`test_roles_api.py`, and did not find tests that assert the same thing. The
domain files call the domain objects. The API files assert status codes, error
bodies and response shapes. Removing any of them drops HTTP coverage. I would
rather report no count reduction than delete coverage to look productive.

**`parametrize` was not applied for its own sake.** A parametrized test with
three cases still collects three node ids, so it does not reduce the count. In
the places I checked, the tests that could be parametrized each assert a
different error key and carry their own rationale, so folding them together
would remove the reason a reader can tell them apart.

## Reproducing the comparison

Never reset a worktree to get a baseline. Copy the repo, check the base commit
out in the copy, and measure there:

```
git clone --no-hardlinks --shared <path-to-repo> <temp>/baseline-repo
cd <temp>/baseline-repo && git checkout bc999cc
```

Then run the same command in both trees, interleaved, more than once. Interleave
rather than run all of A then all of B, because on a loaded host the two trees
see different machine conditions and the ordering alone will mislead you.

For coverage, read the number out of coverage's own JSON report rather than the
console summary:

```
python -m pytest --cov=dsr --cov-report=json:cov.json -q
```

On this host the console summary line was dropped by the shell redirect twice,
and a redirected run cannot be told from a killed one by looking at the process
table. Read the file.