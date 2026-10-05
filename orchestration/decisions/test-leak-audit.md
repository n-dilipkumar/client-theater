# Cross-test leakage audit: the frontend suite and the backend suite

Mandate: Jev audit `jev-20261005T122541-24400-41264` chose **audit the whole
pattern** (0.83) over fixing one file (0.09) or something else (0.08). This is
that audit.

A records note before anything else: that `audit_id` appears nowhere in
`orchestration/decisions/jev-audit.jsonl`. The only record of the decision that
started this work is `TASK.md`. The reasoning that chose the whole-pattern
option is not in the audit trail, so it cannot be replayed or compared against
the gates that came after it. That is a gap in the project's own records, and it
is worth closing independently of the code.

---

## 1. What was measured, and what that is worth

Every claim below is one of three kinds, and the difference is kept explicit
throughout:

- **Measured** — I ran it and quote the output.
- **Traced** — I read every line between the fixture and the behaviour.
- **Inferred** — a judgement from the two above. Called out where it appears.

Baseline, on `main` at `24df2de`, before any change:

| Suite | Result |
|---|---|
| `frontend`, `npx vitest run` | 40 files, **1125 passed**, 0 failed |
| `backend`, `pytest -n auto` | **17856 passed**, 3 skipped, 1 xfailed, 0 failed |

`MeetingWebhookFanout.test.jsx` alone: **24 of 24 passing**. In the full suite on
`main`: **24 of 24 passing**.

**So `main` is green, and the thing this audit was told about does not reproduce
on `main`.** That is the single most important sentence in this report, and
everything else is either an explanation of why the suite *can* be fragile, or a
statement about what is about to happen.

### Probes

Four throwaway probe files were placed under `frontend/src/test/`, run, and
deleted. They are the reason this report can distinguish a real mechanism from a
plausible story.

**Probe 1 — does a global installed by one file reach the next file?**
Two files, run with `--pool=forks --poolOptions.forks.singleFork
--no-file-parallelism` so they share one process in a known order. File A does
`globalThis.fetch = ...`; file B reports what it sees.

```
PROBE_A installed at 3632
PROBE_B observed globalThis.fetch.__probeName = "A"
PROBE_B typeof fetch = function | toString head = async () => ({ ok: true, status: 200, json: async () => ({ p
```

**Yes. A bare global assignment crosses test-file boundaries.** The first run of
this probe let vitest pick the order, it ran B before A, and it appeared to show
no leak. That was wrong, and it is worth recording: the probe had to be padded
past its neighbour's size before vitest's default sequencer (largest file first)
would run it first. **A negative result from a probe that ran in the wrong order
is not a negative result.**

**Probe 2 — does module-scope state cross file boundaries?** Same two files, same
process, same order. File C mutates a module-scope `let` inside an imported
module *and* installs a global. File D reads both.

```
PROBE_D MODULE_SLOT = "pristine"
PROBE_D GLOBAL_FETCH = "C"
PROBE_D moduleRegistrationsSame = false
PROBE_D globalSurvived = true
```

**Module-scope state does not cross files. Globals do.** `isolate` defaults to
`true`, so the module registry is rebuilt per file while the jsdom global is not.

---

## 2. The brief's premise is wrong, and that changes where to look

`TASK.md` asks for "module-level mutable state. A `let` or object at module scope
that a test mutates. Under vitest each file often shares a module registry
within a worker, so one file can observe another's leftovers."

Measured: **each file does not share a module registry.** The module half of
that finding cannot produce a cross-file failure in this suite, and no amount of
auditing it will find one.

The global half is real, and it is the whole mechanism:

> **`fetch` is a property of the jsdom global. Vitest rebuilds the module
> registry per file; it does not rebuild the global. A test file that installs
> `fetch` — or `localStorage` — by plain assignment is writing to an object the
> next file in the same worker will read.**

The consequence that makes this produce "one test file fails depending on which
other files ran before it", rather than a uniform failure: **which files share a
worker changes as the suite grows.** Adding the 41st test file reorders the
distribution. A file that has been safe since it was written can become unsafe
because somebody else added a file, and the blame lands on the file that moved.

### How `fetch` is installed, across the frontend suite

Counted by script over every `.js`/`.jsx` under `frontend/src`, not by eye.

| Mechanism | Files | Restorable? |
|---|---|---|
| `global.fetch = ` / `globalThis.fetch = ` | **19** | **No.** Nothing can undo a plain assignment. |
| `vi.stubGlobal('fetch', …)` | 7 | Yes, *if* the file calls `vi.unstubAllGlobals()` |

26 files install `fetch` at all, and the two rows are disjoint. Of the 7 that use
the restorable mechanism:

- 5 pair it correctly (`CrmReadPanel`, `GapReconcile`, `ConfidentialView`,
  `RecipientVerification`, `QuoteAuthor`).
- **2 never unstub**: `EvaultDownload.test.jsx` (`stubGlobal` at 260, 745, 754;
  no `unstubAllGlobals` anywhere) and `wf014-access-controls.test.jsx`
  (`stubGlobal` at 111, 332; no `unstubAllGlobals` anywhere).

And one file that belongs in neither row cleanly:

- **`InboundVerification.test.jsx` calls `vi.unstubAllGlobals()` at 273 but
  never calls `vi.stubGlobal` at all** — it installs its fetches by plain
  assignment at 259 and 591. So it is counted in the 19 above, and its safety
  net is present, does not apply to its own assignments, and reads as though it
  does. This is the single most misleading file in the suite: a reader scanning
  for `unstubAllGlobals` concludes it is clean.

### The 19 files that cannot be undone

Two of them are **shared helper modules**, which makes them the most dangerous
items in this list, because one line there poisons seven files:

**`frontend/src/test/fixtures.js:126` — `stubApi()`.** Installed by bare
assignment. Imported by **seven** test files (`white-label`, `wf060`,
`wf067`, `wf069`, `wf070`, `wf076`, `wf124`). Its matcher **throws** on any path
not in that file's own route table:

```python
if not key: throw new Error(`unstubbed request: ${path}`)
```

**`frontend/src/features/wf-035-…/fixtures.js:335`** — same pattern, one consumer.

The other 17 install `fetch` by bare assignment in their own file:
`ConciergeRouterPage.test.jsx:115`, `HandoffScheduler.test.jsx:242`,
`MeetingWebhookFanout.test.jsx:233`, `RolesAndScopes.test.jsx:102`,
`InboundVerification.test.jsx:259,591`, `wf094.test.jsx:101`,
`AcceptancePage.test.jsx:164`, `RenewalQuotes.test.jsx:219,402,408`,
`IntentAlerts.test.jsx:192,262,452`, `wf001-room-templates.test.jsx:144,202`,
`wf031-identified-companies.test.jsx:230,475,707`, `wf046-quota.test.jsx:169`,
`wf048-sandbox-validation.test.jsx:145,177`, `wf053-ownership-routing.test.jsx:372,425`,
`wf059-meeting-links.test.jsx:298`, `wf079-audit-trail.test.jsx:190,220`,
`wf124-mutual-action-plan.test.jsx:158`.

### The three that would actually break a neighbour

Not all 19 are equally dangerous. What matters is what the *leftover* does to
whichever file renders next.

1. **`wf124-mutual-action-plan.test.jsx:158` — `globalThis.fetch = undefined`,**
   in `beforeEach`, never restored. The next file in the worker gets no `fetch`
   at all: `TypeError: fetch is not a function`. Note the file also imports
   `stubApi` from the shared fixture, so the same file both consumes and
   contradicts the shared contract.
2. **`RenewalQuotes.test.jsx:402` — a stub that returns `{}` for every path.**
   Leaked, every page in the next file renders its shell and headings and *no
   data*. This is the exact failure shape reported for WF-066: page present,
   data-derived element absent, and the `renderPage`-style gates — which wait on
   static text — passing first.
3. **`InboundVerification.test.jsx:591` — `vi.fn(() => new Promise(() => {}))`,**
   a fetch that never resolves. Leaked, every subsequent page render in that
   worker never completes its data load, so every assertion on data-derived text
   times out as "unable to find an element".

**Status: potentially fragile, not currently failing.** The full suite passes
today. I did not force any of these three to detonate, because doing so would
mean choosing a victim, and picking a victim is the mistake that caused the
incidents this audit exists to correct.

---

## 3. WF-066: the mechanism is real, and this file is not its victim

`TASK.md` reports that `MeetingWebhookFanout.test.jsx` fails on WF-083's branch
in a full-suite run, cannot find `not limited by the number of webhooks you have`,
and passes 24/24 alone on clean `main`.

**What I could establish.** PR #237 (WF-083) is open on `q-ticket-18`, and that
branch is fetched locally, so I built a worktree at it and ran the real thing:

| Commit | Result |
|---|---|
| `015f65a` (branch tip, "Fix the WF-083 consent gate page and its nine red tests") | 41 files, **1143 passed**, 0 failed |
| `94ef9de` (the commit before) | **9 failed, 1134 passed** — all 9 inside `ConsentGate.test.jsx`; `MeetingWebhookFanout.test.jsx` passed |

**So at the last commit where anything was red, the 9 failures were WF-083's own
tests and WF-066 was green.** I could not reproduce the reported WF-066 failure
at either commit, on this machine, in a full-suite run.

**And I can show WF-066 is structurally immune to the mechanism above.** I planted
a probe file — larger than WF-066's, so vitest's sequencer ran it first — that
installs a never-resolving `fetch` and never unstubs it, then ran it and
`MeetingWebhookFanout.test.jsx` in one process, in that order:

```
✓ src/test/zz-poison.test.js (1 test)
✓ src/features/wf-066-…/MeetingWebhookFanout.test.jsx (24 tests)
Test Files  2 passed (2)
```

24 of 24, with the worst possible leftover sitting on the global in front of it.
**Traced:** `MeetingWebhookFanout.test.jsx:233` reassigns `global.fetch` inside
`renderPage()`, which every rendering test calls before `render()`. A leaked
`fetch` cannot reach this file.

**Where the blame actually belongs, on the evidence available.** Not WF-066, and
not, as far as I can measure, WF-083 either. `ConsentGate.test.jsx` does leave a
never-resolving `fetch` installed — line **389**, `vi.stubGlobal('fetch',
vi.fn(() => new Promise(() => {})))` — and the file contains **no
`afterEach` and no `vi.unstubAllGlobals()` anywhere**, so nothing undoes it. That
is a real, still-armed landmine on an open PR, and it is the shape of the
incident. But with WF-066 immune, I cannot show it *caused* this failure, and I
am not going to assert a cause I could not reproduce. **The honest finding is
that the WF-066 report is unresolved, and the two candidate mechanisms that
remain are a leaked global in a file I have not identified, and something not on
the branch at all.**

**Recommendation:** the report of a WF-066 failure should have carried its
vitest run output — file order, worker assignment, and the full error. It did
not, and without those three things the incident is not diagnosable. That is a
process fix, and it is cheaper than any of the code fixes above.

---

## 4. Findings — frontend

| # | File:line | Finding | Verdict |
|---|---|---|---|
| FE-1 | `test/fixtures.js:126` | `stubApi` installs `fetch` by bare assignment; 7 consumers; throws on any unlisted path. One line, seven files at risk. | Potentially fragile |
| FE-2 | `wf-035-…/fixtures.js:335` | Same pattern, one consumer. | Potentially fragile |
| FE-3 | 17 test files (listed §2) | Install `fetch` by bare assignment with no restore mechanism. | Potentially fragile |
| FE-4 | `wf124-mutual-action-plan.test.jsx:158` | `globalThis.fetch = undefined`, never restored. Breaks the next file with `fetch is not a function`. | Potentially fragile |
| FE-5 | `RenewalQuotes.test.jsx:402` | Stub returns `{}` for every path. Leaked, it reproduces the exact reported symptom shape. | Potentially fragile |
| FE-6 | `InboundVerification.test.jsx:591` | Never-resolving fetch; the `unstubAllGlobals()` at 273 cannot cover a plain assignment. | Potentially fragile |
| FE-7 | `EvaultDownload.test.jsx:260,745,754` | `vi.stubGlobal` with no `unstubAllGlobals` in the file. | Potentially fragile |
| FE-8 | `wf014-access-controls.test.jsx:111,332` | `vi.stubGlobal` with no `unstubAllGlobals` in the file. | Potentially fragile |
| FE-9 | `ConsentGate.test.jsx:389` (on `q-ticket-18`, PR #237) | Never-resolving `vi.stubGlobal`, no unstub anywhere in the file. Landmine still armed on an open PR. | Potentially fragile |
| FE-10 | `wf-079-…/api.js:45-49` | `thirtyDayRange()` reads real `new Date()`; `AuditTrailExport.jsx:454` uses it as the form's default. No test asserts the default today, so it is inert — but the first test that does will be on a wall clock. | Potentially fragile |
| FE-11 | `lib/api.js:87-101` | `relativeTime` reads real `Date.now()`. Used by ~40 feature components. **No test asserts on its output** — the only relative-time assertions (`CrmReadPanel.test.jsx:142-144,190`) are on `cacheAge`, a pure function of a supplied number. **Shared file: not edited, see §7.** | Potentially fragile |
| FE-12 | `CrmReadPanel.test.jsx:47`, `GapReconcile.test.jsx:50` | `localStorage.clear()` in `beforeEach` but never after, so `wf-042:last-room` / `wf-050-…:last-room` persist into later files in the worker. Keys are feature-namespaced, so no correctness impact today. | Potentially fragile, low |
| FE-13 | 10 files, module-scope `let` | All 10 reset in `beforeEach`. Zero unreset instances — and by probe 2 they could not leak across files anyway. | **No finding** |
| FE-14 | all `*.test.{js,jsx}` | No `new Date()`, no `Date.now()`, no `vi.useFakeTimers`, no `vi.setSystemTime` anywhere in the frontend test surface. | **No finding** |
| FE-15 | all `*.test.{js,jsx}` | Fixed identities (`room_1`, `room-1`, `m-0001`, `mem_1`) are per-file, and every stub is keyed on its own routes, so no file can consume another's identity. | **No finding** |

---

## 5. Findings — backend

The backend runs `pytest -n auto`, so leakage happens only *within* a worker, in
file order.

### Real time

| # | File:line | Finding | Verdict |
|---|---|---|---|
| **BE-1** | `test_wf056.py` — 14 sites | `"interval": {"startsAt": "2026-10-05T08:00:00Z", "duration": 720}` | **CURRENTLY FAILING — see §6. Fixed.** |
| BE-2 | `test_wf061.py:2626-2627` | `"start": "2026-10-19T14:00:00+00:00"` over a real-clock HTTP route; asserts `status == "scheduled"`, and the reminder fires at `start - 24h` = `2026-10-18T14:00Z`. ~13 days of headroom. | Potentially fragile |
| BE-3 | `test_wf057.py:3469` | `MONDAY = "2026-10-05"` (line 146), books at `16:30` **today**, over a real-clock route. Inside 24 hours of expiry. | Potentially fragile |
| BE-4 | `test_wf053.py:2335` | Fixed instant into a real-clock route. Inert *only* because a past start happens to return the same `409 ownership_slot_not_offered` as an unoffered one. | Potentially fragile |

Roughly 70 further modules define a module-level `NOW` that is now in the past.
**All are inert**, because each passes it in explicitly (`now=`, `clock=lambda:`,
`as_of=`). I verified the injection site in each. They are listed here only so the
next auditor does not re-tread them: `test042.py:80`, `test_analytics.py:28`,
`test_roles.py:47`, `test_audited.py:134`, and one `NOW` each in `test_wf004`,
`008`, `011`, `012`, `013`, `015`, `016`, `018`–`038`, `040`, `041`, `043`,
`045`–`055`, `056`, `058`, `060`, `062`–`067`, `069`, `070`, `073`–`075`,
`077`, `078`, `080`–`082`, `084`–`086`, `089`, `091`, `093`, `095`, `098`,
`100`, `124`, `133`.

Also inert, and worth naming because they *look* like incident (a):
`test_wf052_http.py:510` and `test_wf052.py:510` (the route refuses any body
carrying an interval, asserted `400`), `test_wf053.py:2349` (asserted `404`),
`test_wf056.py:3739` (asserted `404`), `test_wf056.py:3312,3369,3608` (busy
blocks, no future claim), `test_wf056.py:3684` (asserted `400`),
`test_wf051.py:575-640` (passes `now=NOW` explicitly throughout),
`test_wf065.py:140,402,3158` (assertions are about counts, never the date).

**The pattern is already solved in this repo, seven times.** `test_wf064.py:194-229`
defines `wall()` / `wall_wd()` from `datetime.now(timezone.utc)`, with a docstring
naming this exact incident: *"a stamp measured from a constant silently becomes a
past date once real time moves past it. That is not hypothetical — it is how this
file's reschedule tests began failing on 2026-10-01."* Also
`test_wf063.py:2576-2581`, `test_wf086_http.py:39-47`, `test_wf054_http.py:38-40`,
`test_wf055_http.py:41-43`, `test_wf081_http.py:14-16`, `test_wf098_http.py:46-53`.
**The distinguishing mechanism: seven modules pin the clock across the HTTP
boundary** via `dependency_overrides` on the feature's engine getter —
`test_wf016.py:199`, `test_wf025.py:274`, `test_wf026.py:292`,
`test_wf032.py:307`, `test_wf034.py:340`, `test_wf037.py:2504`,
`test_wf045.py:280`. **BE-1 through BE-4 are exactly the modules that swap
`app.state` but never override the engine's clock.**

### Module-level mutable state

**No findings.** Grepped three ways across all 118 test modules: module-scope
containers (91 hits, all constants), in-place mutation of any module-scope name
(zero hits), module-scope lowercase containers (zero hits). Every use I traced
copies before mutating.

### Ordering assumptions and shared resources

| # | File:line | Finding | Verdict |
|---|---|---|---|
| BE-5 | `dsr/crm_upsert/transport.py:273` | `SimulatedTransport._known` is a **process-wide class-level set**, mutated in `__init__` (285). Its own docstring says it is order-dependent. `test_wf038.py:1945` and `test_wf048.py:1664` construct it and **never call `reset()`**; `test_wf038_http.py:43` calls it only on setup, in a non-autouse fixture, never on teardown. | Potentially fragile |
| BE-6 | `tests/conftest.py:101-128` | The autouse environment guard covers **only** `DSR_DB_PATH` and `DSR_AUDIT_DIR`. The variables the suite actually varies — `DSR_PUBLIC_URL`, `DSR_CNAME_TARGET`, `DSR_CRM_CURRENCY`, `DSR_SEARCH_TOKEN_SECRET`, `DSR_ACCESS_DELIVER` and others — are not swept. Safe today because every writer restores. | Potentially fragile |
| BE-7 | `test_wf001.py:50` | `tempfile.TemporaryDirectory(dir=Path(__file__).parent)` — a temp dir created **inside `backend/tests/`**, bypassing `tmp_path`, so it is not namespaced by worker or test id. | Potentially fragile |
| BE-8 | `test_wf022.py:2378-2379`, `2558` | Opens a **second** `AuditedDatabase` on `os.environ["DSR_DB_PATH"]` — the file the conftest `client` fixture already holds open — reading the path from the ambient environment. | Potentially fragile |
| BE-9 | `test_wf075.py:42-53` | The aggregate-cache clear lives in a **non-autouse** `engine` fixture, on setup only. Five tests (497, 509, 519, 532, 544) build the engine directly and inherit whatever the previous test cached. | Potentially fragile |
| BE-10 | `test_wf063.py:3017-3036` | Module-scoped shared in-memory database used by 15 tests. Safe because all 15 are read-only — an invariant nothing enforces. | Potentially fragile, latent |
| BE-11 | — | Cross-file data dependencies: **none found.** `conftest.py` has no session-scoped fixture at all, and `module_client` is module-scoped deliberately (`conftest.py:212-216`). | **No finding** |
| BE-12 | — | Absolute-count assertions against a shared collection: **none found.** Every one is a per-test database or a before/after delta. | **No finding** |

### Fixed identities

Values appearing in more than one test file: `006A000001`
(`test_wf050.py:546,652`, `test_wf050_http.py:380,391,436,480`), `005-nadia` /
`005-desk` / `005-priya` (`test_wf052.py:72-74`, `test_wf052_http.py`),
`room-1` (`test042.py:78`, `test042_http.py:33`, `test_wf054.py:1532`,
`test_wf051.py:49`), `room_a` (`test_wf069.py:139,147`, `test_wf091.py:79`,
`test_wf093_http.py:54`).

**All latent, none currently consumable**, because no database is shared. They
would collide the moment any module-scoped database fixture is introduced.
Emails recur across 30+ files but are never uniqueness keys. Secrets and tokens
are per-file literals feeding pure functions; no value is shared between files.

---

## 6. The one thing that was genuinely broken

**`backend/tests/test_wf056.py` had 14 tests whose window was the literal
`2026-10-05T08:00:00Z`, and the engine behind them reads the wall clock.**

Traced end to end:

- `dsr/features/wf056_…py:130` — `get_headless` returns `HeadlessBooking(store)`,
  **no clock**.
- `dsr/headless_booking/engine.py:114` — `self._clock = clock or _utcnow`, and
  `_utcnow` is `datetime.now(timezone.utc)`.
- `engine.py:454` — `now = self.now()`.
- `dsr/headless_booking/sessions.py:482` — `now=now` into `offer_slots`.
- `dsr/headless_booking/availability.py:246-251` — `reference = now`,
  `earliest = now + lead_minutes`, and `if start_from >= window_end: return []`.

The 14 tests assert `status_code == 201` and
`assert body["schedulingData"][0]["startTimes"]` — a **non-empty** offer. With a
12-hour window from `08:00Z`, the offer empties once real time passes `19:30Z`.

I did not assert that; I measured it, by sweeping `now` through
`availability.slots` — the product's own function — one minute at a time:

```
real now                 : 2026-10-05T12:55:39.455554+00:00
window                   : 2026-10-05T08:00:00+00:00 .. 2026-10-05T20:00:00+00:00
window is in the past    : True
  slots offered at real now : 7
  first / last              : 2026-10-05T13:30:00Z .. 2026-10-05T16:30:00Z
  offer empties at          : 2026-10-05T16:00:39.455554+00:00
  that is                  : 3:05:00 from now
```

**Fourteen tests on `main`, green at the time of writing, deterministically red
from 16:00:39Z — about three hours out — for a reason that has nothing to do with
whatever change triggered the run.** This is the same workflow, the same literal,
and the same failure mode as incident (a) in `TASK.md`, which is recorded there
as *"Repaired in PR #240"*. The feature was repaired. **The 14 fixtures were not.**

A second rule surfaced while I was testing the fix: a window *behind* the clock
is refused outright with a `400`, before any slot is computed. Both halves of the
rule were previously satisfied only by the calendar happening to line up.

### The fix

In `backend/tests/test_wf056.py` only — a file WF-056 owns, not a shared file.

1. The `http` fixture now pins the engine's clock, via the same
   `dependency_overrides` seam seven other modules already use:
   ```python
   _shared_client.app.dependency_overrides[get_headless] = lambda: HeadlessBooking(
       _shared_client.app.state.store, clock=lambda: NOW
   )
   ```
2. The 14 literals became one derived constant, so the relationship between the
   clock and the window is stated once instead of 14 times:
   ```python
   SESSION_INTERVAL = {
       "startsAt": format_slot(NOW + timedelta(days=7, hours=-1)),
       "duration": 720,
   }
   ```
   Seven days on from `NOW` puts the start at `08:00` and the 12-hour window ends
   at `20:00`, straddling the published `09:00-17:00` working hours on a weekday.
3. **One new test**, `test_the_http_half_runs_on_the_pinned_clock_not_the_wall_clock`,
   asserts the override is in force and the window is ahead of it. A fixture that
   quietly stopped applying would leave every test beneath it looking fine and
   expiring again — which is exactly how this survived a repair.

**No test was weakened, skipped or deleted.** Every assertion is byte-identical;
the change is that "now" is a constant instead of a fact about the day. No fixed
date was introduced: the window is measured from the file's existing `NOW`
constant, which is the clock these tests now run on.

`345 passed` in `test_wf056.py`. Ruff clean (with `--config backend/pyproject.toml`).

### One thing I did not expect, found by running the suite three times

| Run | Code | Result |
|---|---|---|
| 1 | before the change | 17856 passed, **3 skipped**, 1 xfailed |
| 2 | after the change | 17857 passed, **3 skipped**, 1 xfailed |
| 3 | after the change, identical | 17859 passed, **1 skipped**, 1 xfailed |

17860 collected every time. **Two tests moved from skipped to passed between run
2 and run 3, on byte-identical code.** A conditional skip is a decision made from
something outside the test's own body — the platform, the clock, an import
ordering, a network probe. Two of them are not, today, a correctness problem.
They are a fourth kind of "depends on the world being exactly as it was when the
file was written", and nobody has looked at them, because a skip is green. I did
not identify which tests they are; that needs a `pytest -rs` run and a bisect
over the three runs, and it is a follow-up, not a finding of this audit.

---

## 6b. Gate and CI

| Check | Result |
|---|---|
| `tools/check_feature_diff.py --base origin/main` | `OK: 3 changed file(s), none shared` |
| `… --platform-change` | `OK: 3 changed file(s), none shared` |
| `ruff check backend tools orchestration --config backend/pyproject.toml` | `All checks passed!` |
| `ruff format --check` (same paths) | `886 files already formatted` |
| Jev release bar, first gate | **uncertain** — merge at 0.50, threshold 0.75 |
| Jev release bar, second gate | **pass** — merge at 0.94, `jev-20261005T130438-28500-78349` |

The first gate is worth keeping. It did not say the work was unsound; it said two
measurements were missing. One was `check_feature_diff.py`, which cannot run
against an uncommitted diff, so it could not have been run before committing.
The other was the post-change whole-suite re-run. With both supplied the gate
cleared at 0.94. Both records are in `test-leak-jev-audit.jsonl`, in sequence,
because the sequence is the honest record.

One honest caveat on the second gate: the sub-question scores stayed low
(`claims_backed_by_measurement` 0.32, `coverage_of_the_change` 0.43,
`design_floor` 0.08) even as the verdict went to 0.94. I read `design_floor` 0.08
as the model registering that the question does not apply to a diff with no
frontend source in it, which is what I declared — but I cannot verify that
reading, and a low score on "claims are backed by measurement" is worth a second
pair of eyes on the evidence half of this report rather than a shrug.

---

## 7. What I deliberately did not fix

The highest-leverage fix in this whole report is one line long, and it is not
mine to make.

**`frontend/src/test/setup.js` is the shared setup** (`setupFiles` in
`vitest.config.js:33`). Its `afterEach` calls `cleanup()` and
`vi.restoreAllMocks()`. `vi.restoreAllMocks()` does not undo a plain
`global.fetch = …`. **The shared setup is the root cause**: it is the one place
that could make the 19 files harmless at once, and it cannot be edited under this
mandate. So:

> **Decision needed, and it is not mine.** Add to the shared `afterEach` in
> `frontend/src/test/setup.js` a restore of the globals a test may have replaced —
> `vi.unstubAllGlobals()`, plus capturing and restoring `globalThis.fetch` and
> `localStorage` around each test. That single change retires FE-1 through FE-8
> at the root rather than one file at a time, and it is the only fix that scales
> to the hundredth feature. It touches a shared file, so per `TASK.md` it is
> reported here rather than done.

Also not touched, and why:

- **`lib/api.js:87-101` (`relativeTime`)** — shared, and reads real `Date.now()`.
  No test asserts on its output today (FE-11), so this is not load-bearing yet.
  The durable fix is to let it take an optional `now`, which is a shared-file
  change.
- **`ConsentGate.test.jsx` on `q-ticket-18`** — a one-line fix (add
  `afterEach(() => vi.unstubAllGlobals())`) in a file on someone else's open PR.
  It is not on this branch and not mine to land. **It should be fixed on PR
  #237 before it merges.**
- **BE-2, BE-3** — real and dated, in `test_wf061.py` and `test_wf057.py`. They
  are green, they are not the 3-hour fuse, and fixing them is WF-061's and
  WF-057's work. Flagged with their expiry dates so nobody rediscovers them by
  being the workflow that gets blamed.
- **The 19 files that install `fetch` by bare assignment** — each should move to
  `vi.stubGlobal`. But if the shared-setup change above is made, that becomes
  optional cleanup rather than the fix, and doing 19 files first would be 19
  merge conflicts for no behavioural gain.

---

## 8. Recommendations, in order

1. **Merge the WF-056 fix.** It is 14 tests with a three-hour fuse, and it is the
   only currently-broken thing in either suite.
2. **Add the global restore to the shared frontend `afterEach`** (§7). Retires
   FE-1…FE-8 at the root. Needs a human decision because the file is shared.
3. **Add `afterEach(() => vi.unstubAllGlobals())` to `ConsentGate.test.jsx` on
   PR #237** before it merges. One line, and the landmine at line 389 is
   disarmed.
4. **Fix BE-2 and BE-3 before their dates**, not after: `test_wf061.py` by
   `2026-10-18T14:00Z`, `test_wf057.py` today.
5. **Require the vitest run output in any cross-test failure report** — file
   order, worker assignment, full error. Without it, incident three was
   undiagnosable, and that is why a workflow was blamed twice for nothing.
6. **Extend `conftest.py`'s autouse environment guard** to the variables the suite
   actually varies (BE-6), and make the reset of `SimulatedTransport._known`
   autouse in both consuming modules (BE-5).
7. **Stop recording decisions in `TASK.md`.** The audit that started this work
   has no entry in `jev-audit.jsonl`, so its 0.83 cannot be replayed or compared.

---

## 9. Adjudicating the three incidents

| Incident | Reported | What the evidence says |
|---|---|---|
| WF-056, backend | 14 tests failed on a fixed date now in the past; "repaired in PR #240" | **Half right, and still live.** The feature was repaired; 14 fixtures in `test_wf056.py` still carried `2026-10-05T08:00:00Z` and were 3 hours from going red. Fixed here. |
| WF-086, frontend | `MeetingWebhookFanout.test.jsx` failed on PR 238; the agent fixed its own code and it went green | **Unresolved.** 24/24 on `main` and in the full suite. No mechanism found that reaches this file, and none that explains the failure. The fix may have been coincidental. |
| WF-066, frontend | Same file fails again on WF-083's branch, full-suite only, cannot find the element | **Unresolved, and the blame is unsupported.** `015f65a` is fully green; at `94ef9de` the 9 failures were WF-083's own; and the file is provably immune to a leaked global `fetch`. `ConsentGate.test.jsx:389` is a real armed landmine of the right shape, but I could not show it caused this. |

Three incidents, one pattern, and the pattern is real — but in two of the three
the named workflow was not the owner of the fault, and in one of those the
"repair" was in the wrong file. That is the finding underneath the findings: this
project has been treating a class of bug as a sequence of individual incidents,
and each time the cheapest available explanation was a workflow that had just
landed code.

---

*Branch `audit-testleak`. Findings are measured where marked measured and traced
where marked traced. Where a cause could not be reproduced, it is recorded as
unresolved rather than assigned.*
