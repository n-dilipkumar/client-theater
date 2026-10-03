# Forensics report: five early ports

Agent: `early`. Worktree: `forensics-early`. Branch: `forensics-early`.

Subjects: `wf-001-2`, `wf-004-2`, `wf-012`, `wf-015`, `wf-017`.

Main is `origin/main` = `c65b77b`. Merge base for all five is `d70d458`.

Nothing was deleted. No pull request was opened. No archive branch was pushed.
No file under `backend/dsr/**` was modified.

## Headline

Three Jev gates were run. The third is the decisive one.

| Audit id | Question | Verdict | Selected | Confidence |
|---|---|---|---|---|
| `jev-20261003T134215-7200-35417` | disposition of the five ports | uncertain | `retain_as_archive_no_pull_request` | 0.57 |
| `jev-20261003T134655-28036-15486` | disposition, repeated with probes | uncertain | `retain_wf017_and_archive_all_five` | 0.47 |
| `jev-20261003T140303-3512-83638` | is the missing behaviour real enough to reimplement later | **pass** | `only_the_security_defect_and_its_tests_are_real` | **0.97** |

The deciding gate, `jev-20261003T140303-3512-83638`, returned `pass` at
confidence **0.97** against a threshold of 0.75. Its probabilities were
`only_the_security_defect_and_its_tests_are_real` 0.98,
`all_gaps_are_real` 0.02, `main_already_covers_all_of_it` 0.00.

Both earlier gates put **0.00 on every option that discards**. Neither was
overridden.

The reading: Jev was never uncertain about whether the gaps matter. It was
uncertain about how to slice an archive, which is an administrative question.
Asked instead whether the residual behaviour is real capability, it answered at
0.97 that almost none of it is, with one exception that is a defect in main
rather than a gap in an orphan.

**The consequence: of everything these five ports hold, exactly one thing is
worth rebuilding later. It is the WF-017 `link_secret` closure and the two HTTP
tests that specify it.**

## One live defect was measured on main

This is the finding that matters most. It is measured, not read.

Three probe tests ran against an extracted copy of `origin/main`. Each probe
asserts the opposite of main's own expectation, so a pass proves the defect.
**Three of three passed.**

1. `PATCH /api/records/room/{id}` with `{"link_secret": null, "name": "Renamed"}`
   clears `link_secret` and still honours `name`. The share-link secret is
   removable by any caller.
2. `PATCH /api/records/room/{id}` with `{"collaborator_token": "..."}` writes the
   reserved `collaborator_token` field.
3. `PATCH /api/records/room/{id}` with `{"branding": {"accent": "url(https://evil.example.net/x)"}}`
   stores a hostile CSS value. A control in the same test confirms the feature's
   own route returns **422** for the identical value, so the generic route really
   does bypass `is_valid_colour`.

The research states the share-link secret is a unique non-removable identifier
for security purposes. On main it is removable.

Main knows. `backend/dsr/features/wf_017_white_label.py` exports
`guard_room_payload()` at line 188. It delegates to `DomainService.strip_reserved`,
which drops `domain`, `link_secret` and `collaborator_token`. **It is called from
nowhere.** The generic route at `backend/dsr/api.py` line 193 passes `payload`
straight to `store.update`.

Main's own test `test_the_generic_record_route_cannot_clear_the_link_secret` is
marked `xfail(strict=True)`. Two prior audit attempts at this gap are recorded and
both are uncertain with neither passing.

The fix is one line in a shared host file. It needs an integrator.

## Per subject

### wf-001-2 — PARTIALLY SUPERSEDED

Branch: `feature/WF-001-create-room-from-template`.

Main carries all product behaviour from `backend/dsr/features/wf001_rooms.py` and
`backend/dsr/room_templates.py` under prefix `/api/wf-001`. All 26 orphan symbols
map. Main fixed a real orphan defect, where a room name whose derived slug passed
64 characters was uncreatable.

What main is missing:

- `GET /api/audit` has no `request_id` query parameter. The database layer has the
  filter. No route exposes it.
- The frontend `request()` error has no `.code` field, only `.status`.
- `Stepper` and `ChoiceCard` are feature-local, not in shared `ui.jsx`. Several
  ports have now duplicated them.
- `tools/verify_localhost.py` has no `DSR_BASE_URL` override. Its `post()` does not
  return a status and body on `HTTPError`. It has no WF-001 smoke section.

Tests: main's `backend/tests/test_wf001.py` is a superset. 21 extra tests.

### wf-004-2 — FULLY SUPERSEDED

Branch: `feature/WF-004-invite-buyers-to-a-room-with-a-role`.

The `access.py` collision is **confirmed as two different workflows**, not one.

- The orphan `access.py` is WF-004. Collections `room_invitation` and
  `room_access`. `AccessService` with 16 methods.
- Main's `access.py` is WF-015. Collections `access_policy`, `access_session`,
  `verification_outbox`. `AccessGate` with 21 methods.

The symbol sets are disjoint apart from a same-named class whose base classes
differ.

The rename is confirmed in code. `roles.py`, `roles_api.py` and
`features/wf004_roles.py` each state in their docstrings that the module is
`roles` not `access`, because the paired branch owns `access` for identity
verification. The docstrings record a 3.2 percent overlap measurement.

What main is missing:

- A relocation only. A per-room Share button and actor selector moved from the
  shared `pages/Rooms.jsx` into the feature's own `InviteBuyer.jsx`, which is a
  superset. Both main files record this as deliberate.

A separate defect on main: `docs/wf-004.md` is byte-identical to the orphan copy
and is now wrong. It cites `access.py` and `test_access.py`, which on main are
WF-015's files, and six routes main does not serve. It never mentions the
collision.

Six audit rows are absent from main's log.

### wf-012 — FULLY SUPERSEDED

Branch: `feature/WF-012-generate-a-personalised-room-programmatically-from-a-template`.

Main carries the same 29 symbols at the same line numbers in `generation.py`. Only
the HTTP layer moved to `features/wf012_generation.py` under `/api/wf-012`. All 9
routes map. Main made four methods require an explicit `source`.

What main is missing: **nothing.**

Tests: the orphan's `test_generation.py` is not on main, but main's `test_wf012.py`
covers all 66 of its behaviours by identical name with identical assertions, plus
6 more.

Two audit rows are absent. Both decisions survive in code and test docstrings.

### wf-015 — PARTIALLY SUPERSEDED

Branch: `feature/WF-015-verify-buyer-identity-and-restrict-by-email-domain`.

Main carries all 12 routes under `/api/wf-015-identity-gate`. Policy logic, modes,
token lifecycle, bot detection and refusal reasons are byte-identical. Main is
stricter. `source` is required, and the emailed link is built from the router
prefix rather than a path the app does not serve.

What main is missing:

- `tools/verify_localhost.py` section 6, an 18-assertion identity gate round trip.
  Also absent is the `BROWSER_UA` header, without which the verifier is itself
  bot-flagged by the gate it tests.
- `error.code`, `error.errors` and `error.body` are not promoted into shared
  `lib/api.js`. The feature works around this locally at full parity.

Tests: 0 of 82 orphan test functions are missing. Main adds `test_wf015.py` with 20.

Seven audit rows are absent, including the uncertain design verdict at
probability 0.78 that the research doc cites the audit log for.

A defect identical in both trees, so discarding loses nothing: `verify()` never
calls `looks_like_bot`. A mail scanner that fetches a verification link can
consume an attempt and grant the session.

### wf-017 — PARTIALLY SUPERSEDED

Branch: `feature/WF-017-white-label-rooms-on-a-custom-domain`.

Main carries all 10 routes under `/api/wf-017-white-label`. Main tightened the
colour grammar against CSS injection and added `oklab`, `lab` and `lch`.

What main is missing:

- **The live defect measured above.** The orphan ran every room patch through
  `strip_reserved`, so its back door does not exist. Main exports the guard and
  never wires it.
- No HTTP test that reserved fields are unoverwritable. Main tests only the
  uncalled pure function.
- `tools/verify_localhost.py` has no WF-017 section. 14 assertions are absent,
  including that releasing a domain does not break an already-shared link.
- `CopyButton` and `StatusPill` are feature-local, flagged as promotion candidates.

Tests: main's `test_wf017.py` covers all 48 orphan tests and adds 15, except the
two generic-route cases above.

Seven audit rows are absent. `backend/dsr/domains.py` cites `secret_is_identity`
at probability 0.99 in its own docstring, and that decision is not in the log.

## What Jev says is worth rebuilding

From `jev-20261003T140303-3512-83638` at 0.97:

- **Worth rebuilding:** the WF-017 `link_secret` closure, and the two HTTP tests
  that specify it.
- **Not worth rebuilding:** the missing `tools/verify_localhost.py` sections, the
  absent `request_id` query parameter, the 22 absent audit rows, the duplicated
  frontend primitives, the `.code` field on frontend errors, the Share-button
  relocation, and the `looks_like_bot` hole in WF-015, which exists in main and
  in the orphan alike.

The `looks_like_bot` case is worth naming. It is a real security weakness in
WF-015, but it is present in main too, so reimplementing the orphan recovers
nothing. It is a defect against main, not a gap in an orphan.

## Ambiguities

- The deciding gate's own `is_confident` noul scored 0.53 while its choice
  confidence scored 0.97. The gate passed on the choice. The split is recorded
  here rather than resolved.
- Main's `WhiteLabel.jsx` comment misquotes the code it replaced. It claims the
  old code declared `cancelled` and never assigned it. No file in the wf-017
  orphan does that. The behavioural claim is right. The code quote is wrong.
- `wf-015` could not confirm whether main's per-request `AccessGate` and the
  orphan's `app.state` singleton differ. `__init__` holds no mutable state, so they
  are judged equivalent. Not executed to confirm.
- `wf-015` read that main downgrades a failed submit from `role="alert"` to
  `role="status"` because `Gate.jsx` hard-codes `tone="info"` and ignores
  `notice.tone`. This is a read of the JSX. It was not rendered.
- The `origin/main` ref was stale at `dec2423` until a fetch advanced it 73
  commits to `c65b77b`. The first merge-base computations used the stale ref. All
  findings here use `c65b77b`.
- No browser or frontend build was run. Frontend findings are from code reading.

## Note on the recovery branch

`origin/recovery/orphaned-features` at `3caceed` holds 80 files, not the 79
stated. It contains 6 feature modules for WF-044, WF-046, WF-048, WF-049, WF-053
and WF-062. Those are a different subject set from these five.

It contains no shared host file, as stated. It does modify `opencode.json` and
`orchestration/check_cleanup_heartbeats.py`, which are neither features nor host
files under the feature contract.

Its content breaks the seeder on Windows. `backend/seed.py` line 172 raises
`UnicodeEncodeError` on a cp1252 console. The cause is one U+2192 character in
the WF-049 seed detail. Evidence was sent to agent DIRTY as `msg_2a6c590c4a09`.
