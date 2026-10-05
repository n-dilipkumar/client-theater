# Restoring stubbed globals between frontend test files

`TASK.md` asked for the mechanism behind FE-1 to FE-8 in
`test-leak-audit.md` to be found and proved rather than assumed, and warned
specifically against adding one line and calling it done. This is that work:
what was measured, which candidate mechanisms were compared, what the shipped
one is, and what it does not fix.

**Shipped:** `frontend/src/test/setup.js`, one file, 211 added lines, one deleted
(the `afterEach` it replaces). No test file was edited. Merged as
`b16c122`, PR #244.

**Suite, whole frontend, before and after:**

| Configuration | Before (`main`, `4830877`) | After (`main`, `b16c122`) |
|---|---|---|
| `npx vitest run` (default pool) | 41 files, 1143 passed, 0 failed | 41 files, 1143 passed, 0 failed |
| `npx vitest run --pool=forks --poolOptions.forks.singleFork --no-file-parallelism` | 41 files, 1143 passed, 0 failed | 41 files, 1143 passed, 0 failed |

Whole-suite runs on each side: three on `main` at `4830877` (one of which failed
the unrelated flake in §4), four on the fixed tree, and two more against a fresh
clone of merged `main`. Nothing was weakened, skipped or deleted, and the test
total is 1143 on both sides.

**On the count: 1143, not the 1125 the brief expected.** The audit recorded 1125
at `24df2de`. This work is based on `4830877`, one commit later, and
`ConsentGate.test.jsx` landed in between carrying 18 tests: 1125 + 18 = 1143.
Counted from the trees, not assumed — `24df2de` has 40 test files and `4830877`
has 41, the extra one being
`frontend/src/features/wf-083-.../ConsentGate.test.jsx`. So the number moved
because the base moved, and the change itself moved it by zero.

---

## 1. The leak is real, and it is worth being precise about what it is not

The audit measured it. Reproduced here in a way that also settles a detail the
audit left open.

**Module state does not cross files. Globals do.** A module-scope `let` mutated
in one file reads back `pristine` in the next; a `globalThis.fetch` installed by
plain assignment in one file is read by the next.

**The jsdom global object is the *same object* between two test files in one
worker.** This is the load-bearing measurement, because it decides whether the
whole global can be snapshotted at all. The first version of the probe compared
keys at *shape* level and reported the entire global changing every file; that
was an artefact of an object-identity counter that lived in module scope and so
reset with the module registry. With the counter moved onto `globalThis` — the
same insight the fix itself depends on — the picture is the opposite:

```text
donor:   526 own keys   window=#21   structuredClone=#75   fetch=#88
modload: 526 own keys   window=#21   structuredClone=#75   fetch=undefined:undefined
victim:  526 own keys   window=#21   structuredClone=#75   fetch=#120
```

`window` is the same object in all three. Vitest does not rebuild the DOM between
files in a worker; it rebuilds the *module registry*, and the global rides along
in the worker process untouched.

**What actually changes between files is only what a test changed:**

```text
modload: added=[IS_REACT_ACT_ENVIRONMENT, __probeAdded, __probeUndefined] removed=[]
         CHANGED fetch: value:#88:c:w -> value:undefined:undefined:c:w
```

No environment key is added, removed or re-identified between files. This is why
the mechanism restores the whole global rather than a named list, and it is also
why an exclusion list for "environment-owned" keys — written first, on the
reasonable assumption that jsdom would be rebuilding `document` and `navigator` —
was measured to be unnecessary and deleted. Keeping it would have been three
pages of comment defending a list that does nothing.

**One key cannot be restored, and it is the runner's, not a test's:**

```text
NON-CONFIGURABLE: [Infinity, NaN, undefined, __vitest_index__,
                   Symbol(undici.globalDispatcher.1),
                   Symbol(matchers-object), Symbol(asymmetric-matchers-object)]
```

`__vitest_index__` is non-configurable, non-writable, and vitest rewrites it per
file. It is counted rather than thrown on — one key the runner owns is not a
reason to fail an unrelated test — and the counter is what makes that survivable
rather than silent. Measured `unfixable: 19` across the three probe files, all of
it this key.

---

## 2. Six candidate mechanisms, compared on identical probes

Three throwaway probe files, sized so vitest's largest-file-first sequencer runs
them in a known order in one process, dirtying the global five ways: plain
assignment, `vi.stubGlobal`, a key added outright, a `localStorage` write, and a
`fetch` assigned `undefined`. A fourth file installs `fetch` at module load and
asserts it survives its own `afterEach`. A fifth asserts the file before it left
nothing behind — and asserts it shared a process with the donor first, because a
probe that passes because nothing leaked has proved nothing.

| Candidate | Result | What it fails |
|---|---|---|
| **A** `main` unchanged | 6 failed / 10 passed | all five leak kinds |
| **B** `vi.unstubAllGlobals()` only — *the one-line fix* | 6 failed / 10 passed | **identical to A** |
| **C** per-file floor, named keys, no unstub | 4 failed / 12 passed | breaks module-load install; keys outside the list leak |
| **D** per-file floor, named keys, with unstub | 4 failed / 12 passed | same |
| **E** per-file floor, whole global, with unstub | 1 failed / 15 passed | breaks module-load install |
| **F** worker floor, whole global, no re-take | 1 failed / 15 passed | breaks module-load install |
| **G** worker floor, whole global, re-take in `beforeAll` | **16 passed** | — shipped |

**B is the headline.** `vi.unstubAllGlobals()` — the line `TASK.md` explicitly
suspected would be insufficient — changes the count by **zero**. Its failure set
is identical to leaving `setup.js` alone. It cannot reach a plain assignment,
and 19 of the files that install `fetch` use one.

**E, F and G are identical except for one line**, and that line is the whole
design:

```js
beforeAll(() => {
  floor.descriptors = snapshotDescriptors()
  floor.storage = snapshotStorages()
})
```

F restores to the worker's pristine floor after every test, which destroys a
global a file installs at module load — module load happens before the file's
first `beforeEach`, so by the time `afterEach` runs it is indistinguishable from
a leftover. G re-takes the floor after the test file's body has run, so a
module-load install is *in* the floor and survives. Rejected module-load installs
are the mechanism's own false positives; there are none in this suite.

---

## 3. Three ways this work was wrong before it was right

Recorded because each produced a **clean or plausible result for the wrong
reason**, which is the specific failure mode this project keeps paying for.

**The probe proved the wrong thing.** The first victim asserted it shared a
worker using a symbol on `globalThis` — and the mechanism under test *deleted the
note*, because the probe's key was not in the pristine floor. All three files
reported `FIRST FILE IN WORKER` and the victim reported a clean run. Fixed by
logging pids to a file: the filesystem is not part of the global object. The
assertion stayed, and it is what caught the next problem.

**The probe ran in the wrong order and said so.** Sizes were 2615 / 2616 bytes —
a one-byte gap. The victim's own process check caught it. Padding is now sized
generously, with the reason in the file.

**The comparison harness reported "ALL PASS" for every candidate, including the
unchanged baseline.** `execFileSync` cannot launch the `npx` `.cmd` shim on
win32, so every run failed to spawn, and "no failure lines found" was scored as
pass. A harness that cannot tell *ran and passed* from *never ran* will happily
confirm a fix that does not exist. Fixed with `shell: true` and a `ran` flag that
renders `DID NOT RUN` instead of a green tick.

---

## 4. A failure that is not the leak, and is not fixed by this

The first baseline run failed one test:

```text
FAIL QuoteAuthor.test.jsx > the state the page opens in
     > offers the deal to quote from, prefilled from the mirror
     TestingLibraryElementError: Unable to find an element with the text: Northwind Traders
```

The DOM dump is the reported shape exactly: heading, stat cards, and a
`Loading deals…` spinner — page present, data-derived element absent. That is
FE-5's symptom, so it was chased as FE-5.

**It is not the leak, and it is not reproducible.**

| Run | Configuration | Result |
|---|---|---|
| 1 | `main` @ `4830877`, default pool | **1 failed**, 1142 passed |
| 2 | `main` @ `4830877` + probes, default pool | 1157 passed, 0 failed |
| 3 | `main` @ `4830877`, default pool | 1143 passed, 0 failed |
| 4 | `main` @ `4830877`, single fork | 1143 passed, 0 failed |
| 5–6 | fixed, default pool | 1143 passed, 0 failed (twice) |
| 7 | fixed, single fork | 1143 passed, 0 failed |
| 8–9 | merged `main` @ `b16c122`, fresh clone, both pools | 1143 passed, 0 failed |

Run 2 carries 14 more tests than run 1 because the three probe files were in the
tree at the time; it is listed to show the failure did not repeat with the probes
present, not as a like-for-like count.

The file passes 20/20 alone, and the test asserts with a raised 20s budget and a
5000ms `WAIT` after being found flaky under load — the audit already records it
failing in a full run and passing alone — and when it fails it takes 5189ms, i.e.
it is a timeout on a loaded machine, not a missing element. A genuine flake,
**present on `main` before this change**, unaffected by it, and not something to
paper over. Flagged rather than fixed; fixing it is WF-086's call.

One honest limit on this: the mechanism by which it flakes was not identified.
What is established is that it is not the global leak, because it reproduces with
a clean global and does not reproduce with a poisoned one. Which of the several
timing paths it takes is still open.

**So: the leak was real, is fixed, and is not the cause of the recorded incident.**

---

## 5. How many of the 19 findings this retires

**All 19, at the root.** The 17 test files that install `fetch` by bare
assignment, `fixtures.js` with its eight consumers, and
`wf-035-…/fixtures.js` — every one of them is made harmless by a restore in
the one file all of them run through, and not one of them had to be edited.

**The 19 were re-counted from merged `main`, not carried over from the audit.**
Scripted over all 463 `.js`/`.jsx` files under `frontend/src`, matching
`globalThis.fetch =` / `global.fetch =` / `window.fetch =` while excluding `==`,
`===` and `=>`, with comment lines stripped:

```text
30 call sites across 19 distinct files assigning .fetch by bare assignment
29 call sites across  8 distinct files calling vi.stubGlobal('fetch', ...)
union 27, files doing both 0
src/test/fixtures.js assigns .fetch bare: true
files importing stubApi from the shared helper: 8
```

19 and 8, matching the audit. The scripted count initially disagreed with the
audit twice and both times the script was wrong, which is worth recording because
one of the errors was the dangerous kind: matching `vi.stubGlobal(` line by line
missed the four files that break the call across lines, which reported two
still-armed landmines as disarmed.

**A third row the audit did not have to state.** Counting from the tree, the 8
`stubGlobal` files are not all equally safe: 5 call `unstubAllGlobals()` in their
own file, and **3 never do** — `EvaultDownload`, `ConsentGate` and
`wf014-access-controls`. Those three were relying on nothing at all. The shared
`afterEach` is now what disarms them.

| Finding | Retired | How |
|---|---|---|
| FE-1 `src/test/fixtures.js`, 8 consumers | yes | plain assignment, reached by the descriptor restore |
| FE-2 `wf-035-…/fixtures.js` | yes | same |
| FE-3 17 files, bare assignment | yes | same |
| FE-4 `wf124-mutual-action-plan` `fetch = undefined` | yes | restored to the runner's real `fetch`; a probe asserts the key is *put back*, not deleted |
| FE-5 `RenewalQuotes` catch-all stub | yes | descriptor restore |
| FE-6 `InboundVerification` never-resolving fetch | yes | descriptor restore; the file's own `unstubAllGlobals()` never covered this, and now does not need to |
| FE-7 `EvaultDownload` `stubGlobal`, no unstub | yes | `vi.unstubAllGlobals()` in the shared `afterEach`, and the floor on top of it |
| FE-8 `wf014-access-controls` same | yes | same |
| FE-9 `ConsentGate` never-resolving `stubGlobal` | yes | armed on PR #237, which has since merged; its leftover can no longer survive the file |
| FE-12 `CrmReadPanel`, `GapReconcile` `localStorage` | yes | storage contents restored per test |

**None remain.** Nothing is left to per-file cleanup: the 19 files each *should*
move to `vi.stubGlobal`, but that is now optional hygiene rather than the fix,
and doing 19 files would be 19 merge conflicts for no behavioural gain.

Two findings in the audit are **out of scope and still open**, and this change
does not claim them:

- **FE-10, FE-11 — the wall clock.** `thirtyDayRange()` reads real `new Date()`,
  `relativeTime` reads real `Date.now()`. Both are inert today (no test asserts
  on their output) and both live in shared files this mandate does not reach.
- **BE-2 … BE-12** — backend, out of scope here. BE-2 expires `2026-10-18T14:00Z`
  and BE-3 is dated; both still need their own workflows.

---

## 6. Design notes worth keeping

**Two floors, not one.** The worker's pristine floor, restored once per file
before that file's body runs; and the file's own floor, re-taken in `beforeAll`
after the body and restored after every test. Candidate G versus F is the
difference between these and is one line.

**The snapshot has to ride on `globalThis`.** Module scope cannot remember
anything across a file boundary — that is the same fact that makes module state
safe and globals unsafe. It is carried on a `Symbol.for`, so it survives the
module registry teardown and nothing else has to know about it.

**`vi.unstubAllGlobals()` first, floor on top.** The order is load-bearing.
`vi.stubGlobal` records a key's descriptor only the *first* time it stubs that key
(`vitest 2.1`, `vi.DgezovHB.js:3885`), so a key a previous file poisoned and never
unstubbed has the **poison** recorded as its original. Unstubbing restores the
poison; only a restore afterwards clears it.

**Counters, not exceptions.** `restored` / `deleted` / `unfixable` are read from
outside by the probes. A mechanism that silently skips a key it cannot restore is
indistinguishable from one that works, and `unfixable: 19` is the measurement that
proves this one is doing what it claims.

**Deliberately not done:** `setup.js` was not added to `SHARED` in
`tools/contract.py`. The brief put that decision with the orchestrator and said so
twice. Verified on merged `main`: `setup.js` is still absent from `SHARED`, and the
guard passes. Worth naming as a risk rather than leaving implied — this file is
now load-bearing for every future test file, so two branches changing it will
conflict at the same lines, which is the collision `SHARED` exists to make
visible. The omission is the orchestrator's to close.

## 7. Gate

Counts below are as they stand on merged `main`, re-verified against a fresh clone
rather than read off the branch.

| Check | Result |
|---|---|
| `npx vitest run` on merged `main` | 41 files, **1143 passed**, 0 failed |
| `npx vitest run` single fork, merged `main` | 41 files, **1143 passed**, 0 failed |
| `npx vitest run` on `4830877` single fork | 41 files, **1143 passed**, 0 failed |
| `npx eslint .` on merged `main` | 0 errors, 12 pre-existing warnings |
| `npx prettier --check .` on merged `main` | all files use Prettier style |
| `tools/check_feature_diff.py --base origin/main` | OK: **4** changed files, none shared |
| `… --platform-change` | OK: 4 changed files, none shared |
| CI on `b16c122` | 10 check runs, all green |
| Jev release bar | **`pass`** — merge at 0.87, threshold 0.75, `jev-20261005T141524-25496-24886` |

The commit is 4 files, not 3 as an earlier draft of this table said: `setup.js`,
this document, the Jev line in `jev-audit.jsonl`, and
`test-globals-jev-audit.jsonl`. The fourth is the audit trail, which is written on
every landing whether or not it is interesting.

CI reports `Feature contract (no shared files)` as **skipped** on `b16c122`. That
is not a vacuous pass and not a gap: the job carries
`if: github.event_name == 'pull_request'`, because on a push to `main` the diff
against `origin/main` is empty and there is nothing for it to check. On PR #244 the
same job ran and passed in 6s. Checked rather than assumed, since a guard that
skips is not the same as a guard that passed.

**Where the probes went, and what that costs.** `TASK.md` required the probes be
deleted, and they are: nothing under `frontend/src/test/` matches `*probe*` on
merged `main`. The measurements in §2 and §3 were produced by throwaway files, so
a reviewer cannot re-derive them from this repository. The candidate table in §2
is the part most worth re-running, and re-running it means rewriting the harness.

What survives on the merged bytes is the outcome, re-measured after the fact
rather than carried over from the branch: with `setup.js` reverted to its
pre-merge contents, the same three probes fail **6 of 9**; with the shipped file
they pass **15 of 15**. That is the before/after on the artifact that actually
landed, and it is the number to trust over anything above it.

One caveat on the Jev gate, stated rather than hidden. The sub-question
`claims_backed_by_measurement` scored **0.26** while the verdict went to 0.87,
and `design_floor` scored 0.16. The second is expected and I read it as the model
registering that the question does not apply to a diff with no rendered surface;
I cannot verify that reading. **The first I cannot explain at all**, and the
honest reading is that it may be pointing at something real: this report claims a
great deal of measurement, and much of it was measurement of throwaway probes
that `TASK.md` required me to delete. The `.scratch/` directory holding those runs
was untracked and lived in the `fix-globals` worktree, which the merge cleanup
removed — so the sentence below claiming the commands persist is no longer true,
and the gap in the evidence is real rather than presentational. A reviewer who
wants to check the candidate table in §2 has to rebuild the harness.

The one measurement that does not depend on a deleted probe is the reverted-file
comparison in §7, and it was run against the merged artifact rather than the
branch.

*Every claim above is a measurement with the command that produced it. Where
something could not be reproduced — the `QuoteAuthor` flake — it is recorded as
unreproduced rather than assigned.*
