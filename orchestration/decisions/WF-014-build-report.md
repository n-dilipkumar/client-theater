# WF-014 — Expire or cap access to a room

Branch `feature/wf014-access`, one local commit, not pushed. No PR opened, no
merge, nothing ticked in the spec's "Verified in localhost browser" box (no
browser was attached, so there was nothing behind it to tick).

## What this is

A room's link is bounded two independent ways: a number of days counted from the
moment the page goes live, and a number of times the page may be opened. The
rule the feature exists to get right is that **a status is not a claim that a link
works.**

The research is explicit that the two come apart:

> "Once your client reaches the view limit, you'll see the status on your
> dashboard change to View Limit. The Page will retain a Live status, although
> it's been disabled by the view limit."

So every answer here is derived on read, from the stored window plus a fresh
count. Nothing reads a status field that was written once and left to rot.

Deriving on read is also the only correct choice against the vendor's own webhook
enum, which has no expiry signal at all — `pageDeclined` is not published, so
expiry-driven decline is a poll-only signal. A build that waited for an event the
vendor does not send would leave an expired page open forever, which is the
failure this whole workflow exists to prevent.

## Files added (11, none shared)

| File | What it is |
|---|---|
| `backend/dsr/access_controls.py` | The domain module. Pure rules, no FastAPI. |
| `backend/dsr/features/wf014_access_controls.py` | The plugin: 13 routes under `/api/wf-014`, its own error handler, a seed. |
| `backend/tests/test_wf014.py` | 81 tests. Rules, at chosen instants, no HTTP. |
| `backend/tests/test_wf014_http.py` | 45 tests. The contract other teams build against. |
| `frontend/src/features/wf-014-access-controls/` | 4 files: descriptor, page, editor, local primitives. |
| `frontend/src/test/wf014-access-controls.test.jsx` | 21 tests. |
| `orchestration/decisions/WF-014-build-report.md` | This report. |

`tools/check_feature_diff.py --base origin/main` → `OK: 11 changed file(s), none shared`.

## The module name, and why it is not negotiable

`backend/dsr/access.py` exists on `main` and belongs to **WF-015**, which is live
and merged. Two features cannot own one module path — that is the exact collision
the plugin host exists to end, and the same one that made WF-004 become
`roles.py`. This branch adds `access_controls.py`.

The brief cited `docs/design/WF-014-access-controls.md` as the source of that
name. **That file is not in the repository** — `docs/design/` holds only
`WF-015-identity-and-domain-access.md`. The brief's own decision stands on its own,
and `backend/tests/test_wf014.py` pins it so a later rename cannot undo it quietly.

## The frontend folder, which the brief got wrong

The brief says `frontend/src/features/wf-014/access-controls/index.jsx`. That is
two path segments deep, and `lib/features.js` globs:

```js
const modules = import.meta.glob('../features/*/index.jsx', { eager: true })
```

One segment. A two-deep folder compiles and is then **silently undiscovered** —
the worst kind of failure to debug, and `lib/features.js` is a shared file this
branch may not edit. So the folder is `wf-014-access-controls/`, one deep. The
`id` matches the backend's `FEATURE["id"]`, which is what lets the two halves be
found by one name.

## What the research left ambiguous, and the reading taken

Every one of these is served over `GET /api/wf-014/vocabulary` with the quote it
rests on, so a reviewer checks the claim against the code rather than against
this document. Jev's rule gate returned **`uncertain`** on the first run
(`lands` 0.49 vs `partial` 0.40, confidence 0.24) and AGENTS.md forbids
overriding that, so the gaps are enumerated here rather than smoothed over.

1. **Expiry closes at the end of the expiry date in UTC.** Qwilr's "stops working
   after the number of days that you set" reads as elapsed time and would cut a
   page published at 09:00 off at 09:00 on the last day. Liferay's "Access ends at
   the end of the expiration date in UTC" is the only sourced statement that pins
   the boundary to an instant, so it wins. A page published 09:00 UTC on 2 October
   with `days=30` expires at 23:59:59.999999 UTC on 1 November, and is open for
   every instant up to and including that one.

2. **The boundary comparison is strict.** It follows from (1): 23:59:59.999999 is
   the last instant the window covers, so "expired" means `now > expires_at`.

3. **Expiry outranks the cap when a page is both.** The research never says which
   badge wins. Expiry is the only constraint that *moves* the status — the page
   "will automatically switch to a Declined status" — while the cap only masks
   one, so a page that did both declined on a date. `closed_by` reports **both**
   reasons, because a seller told only one will remove the wrong constraint.

4. **Set Live restarts the expiry clock.** "The count of days starts when you
   publish the page", and Set Live publishes again. Without a restart, "You can
   always set the page Live once again if needed" would be false: the page would
   re-decline against a date still in the past and revival would be a no-op.

5. **A draft never expires, and a cap is a lifetime total.** "A draft page keeps
   the setting until you publish it" leaves the count unstarted, so there is no
   instant to compare. The cap describes a limit on views *of the page* and its
   switch is changeable at any time, so it totals the page's lifetime rather than
   restarting — which is why raising a cap above today's count re-opens a closed
   link without clearing anything.

## The rule most likely to be "fixed" by someone who has not read the note

**Set Live does not clear the view limit.** The vendor is explicit:

> "If your page reaches the view limit and then you manually set it as Declined,
> you can still manually set it Live later. If you do that, the view limit setting
> will still in place, so you'll want to use the steps above to remove it."

So reviving clears the hand-declined flag and nothing else. A page can come back
from `set-live` **still closed**, and removing the cap is its own explicit action.
`test_set_live_does_not_clear_the_view_limit` pins it over the engine and
`test_set_live_does_not_clear_the_view_limit` again over HTTP.

## Deliberately not implemented, and why

Reported on `/vocabulary` under `not_implemented` rather than quietly dropped.

- **Password protection.** Listed as an optional fourth step of the Share flow,
  but it is neither an expiry nor a cap, has no documented semantics beyond "on or
  off", and the research itself records that Qwilr's security settings are
  Share-popup only with no API. Building a credential check nobody researched
  would be inventing the rule rather than landing it. It belongs with WF-015.
- **The `pending` / `accepted` / `blueprint` statuses.** Part of the vendor's
  dashboard vocabulary, but they describe an Accept-block workflow this product's
  page model does not have — `dsr.pages` knows `draft` and `published` only.
  Reporting a status no record can hold would be a badge that lies.

## Not stored, on purpose

Only `page.data.access_window` is written, plus `page_view` rows. This feature
**never writes the page's own `status`**, which belongs to `dsr.pages` and the
editor.

The consequence, stated so nobody has to find it in the diff: a seller reading
`status` off a page record still sees `published` for a page that expired an hour
ago. They read `access_status` here instead, and `GET /api/wf-014/pages?status=`
filters on it the way the vendor's `GET /v1/pages?status=` filters on the
dashboard badge.

## Real defects found while building

1. **A `match` statement with bare names.** `case BUYER_EXPIRED:` in a `match` is
   a *capture* pattern, not a value pattern — so all five branches captured one
   name and the later ones were unreachable. Ruff caught it; rewritten as an
   explicit chain. This would have shipped a buyer view whose message was always
   the expiry one.

2. **The seeder depended on feature load order.** The seed wrote as a
   `room_collaborator`, and an archived room is read-only for a collaborator.
   Earlier features seed first and some archive a room to demonstrate their own
   workflow, so the demo dataset broke whenever this feature landed on an
   archived room. Found by `test_wf019.py::test_the_seeder_runs_wf019_without_failing`,
   which asserts no feature reports `seed FAILED`. Fixed by seeding as an
   instance administrator — the seeder is not a collaborator. Filtering the rooms
   instead would have made the demo rows depend on which rooms survived, which is
   worse.

3. **`at` was documented but never wired.** Every read route took an `at` query
   parameter that the handler ignored, so seven boundary tests failed. The domain
   logic was right; the HTTP surface was not.

## Things a reviewer should not have to discover

- **`record_view` is not transactional.** The cap check and the write are two
  operations, so two buyers arriving together at the last view could both be
  admitted and leave the count one over. `RecordStore` does not expose
  `transaction()`, and reaching past the facade for a connection the product keeps
  private was judged worse than a page that closes one view late. The cap is
  re-evaluated on every read, so the link never stays open by mistake.
- **Lost updates are handled, not left to chance.** `_write_window` is a
  read-modify-write, so it carries `expected_revision`; if another writer moved
  the page in between, the write is refused and the caller told to re-read.
- **`days_remaining` rounds up**, so a link with four hours left reads "1 day", not
  "0 days". At exactly the boundary it is 0 and the copy says "expires today",
  because "expires in 0 days" is not a sentence a buyer should read.
- **A page view collection is `page_view`, not `access_session`.** `access_session`
  is WF-015's collection for verified buyer sessions; sharing a name would make
  two live features read each other's rows.
- **`Modal`, `Notice`, `Toggle` and `Checkbox` do not exist in
  `components/ui.jsx`**, though `FEATURE-CONTRACT.md` lists them as shared
  primitives. The switch and the buyer banner are built in the feature's own
  `primitives.jsx`, in the design system's tokens and at the same accessibility
  floor, with the reason in that file's docstring. If a second feature needs the
  same switch it belongs in `ui.jsx` as platform work.

## Evidence

Every number below was measured, not asserted. The full backend suite takes
roughly 25 minutes on this host, so it was run to completion in the background
while the rest of the work proceeded.

| Check | Result |
|---|---|
| `pytest tests/test_wf014.py tests/test_wf014_http.py` | **126 passed** (81 + 45 collected) |
| Full backend suite | **10123 tests, 10121 passed, 2 pre-existing xfail, 0 failed**, exit 0 |
| `npm run test` (vitest) | **138 passed** across 5 files; 21 are WF-014 |
| `npm run build` | 274 modules transformed, built in 7.95s |
| `npm run lint` | 0 errors. The one warning is pre-existing in `components/ui.jsx`, which this branch does not touch. |
| `npm run format:check` | All matched files use Prettier code style! |
| `ruff check backend tools orchestration --config backend/pyproject.toml` | All checks passed! |
| `ruff format --check ...` | 489 files already formatted |
| `check_feature_diff.py --base origin/main` | `OK: 11 changed file(s), none shared` |
| Seeder (`backend/seed.py`) | exit 0; `wf014_access_controls -> 5 pages across the access states: live (open), expiring_soon (expiring_soon), declined (expired), view_limit (view_limit), draft (unpublished)` |
| Jev `release_bar` | **pass**, confidence **0.99** — contract 0.95, audit integrity 0.94, design floor 0.90 |

Jev was run twice and disagreed once, which is worth recording. An earlier
invocation returned `fix` at 0.21 — because the measurement strings in the driver
were typed by hand and had drifted from what the runs actually printed (it claimed
82/44 test split and "design floor: not run" long after the static check existed).
Rewriting the driver to read each measurement out of the file the tool that
produced it wrote moved the verdict to `merge` at 0.99. The lesson is the one
`nev.py` already documents about declared limits: a stale claim in an evidence
block is worse than no evidence, because to a reader it is indistinguishable from
a true one.

`claims_backed_by_measurement` (0.47) and `coverage_of_the_change` (0.46) stay low
in both runs. That is Jev discounting the two declared gaps below — no browser, so
no rendered-page evidence and no scripted design-floor pass — not a defect it
found. The declared gaps are the honest disclosure, and the gate passes anyway.

Over HTTP against a running server, `GET /api/features` reported
**`failed_count` 0** and listed `wf-014-access-controls` with prefix `/api/wf-014`,
13 routes, handler `AccessWindowRefusal`, every route under its own prefix. Then,
end to end: a cap of 2 admitted view 1 (open), admitted view 2 (capped, not
accessible), refused view 3 with 409; badge `view_limit` with stored status
`published`; buyer view `state=view_limit`, `show_content=false`, placement
`replaces-content`; `GET /api/wf-014/pages?status=view_limit` returned it; expiry of
30 days resolved to `2026-11-01T23:59:59.999999+00:00` and the buyer view at +40
days reported `state=expired`; decline-then-set-live left the cap enabled and the
link closed.

The UTC boundary is tested one microsecond either side of `expires_at`, both as
unit tests and over HTTP. `count_where` is pinned against `find` for the same
conditions including numbers and booleans, and against soft-deleted rows.

### Not verified

No browser was attached, so `tools/design_floor_browser.py` did not run and the
spec's localhost box is unticked. The floor was checked statically instead — no
raw hex, no `rgb()`/`hsl()`, no Tailwind 200-400 text step, no drop shadow, no pill
radius, no emoji, `rounded-sm`/`rounded-xs`, `min-h-11` on every control, and
loading/error/empty states present — and asserted in vitest. One real finding came
out of that check: a progress track was using `rounded-full`, now `rounded-xs`.
The page has not been seen rendered by a person.
