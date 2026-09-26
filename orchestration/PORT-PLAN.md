# Batch 1 port plan

Derived from `git diff 82deb4c..<branch>` over all 17 batch-1 branches. Nothing
here is guesswork; the counts come from the survey of what each branch actually
changed in the shared files.

## Why batch 1 never landed

Every branch appended routes to the single FastAPI `app` in `dsr/api.py`,
appended an entry to the hard-coded `ROUTES` array in `App.jsx`, and appended
primitives and icon paths to `components/ui.jsx`. Twelve branches, three shared
files, one conflict each. `origin/main` is now at `fd544e2` with the plugin host,
so the port is mechanical: move each feature's code into its own two files.

## Shape of each port

1. Copy the branch's additive files verbatim: `backend/dsr/<feature>*.py`,
   `backend/tests/test_<feature>.py`, `frontend/src/pages/<Page>.jsx` (move the
   page to `frontend/src/features/<id>/<Page>.jsx`).
2. Write `backend/dsr/features/<ticket>_<slug>.py`: `FEATURE`, and either
   re-export the branch's existing `router` or convert its `@app.<verb>` routes
   into an `APIRouter`. Move its `@app.exception_handler` blocks into
   `EXCEPTION_HANDLERS`.
3. Write `frontend/src/features/<id>/index.jsx` with the default-export
   descriptor; point it at the moved page.
4. Move that branch's `ui.jsx` additions into the promoted core set (shared
   work, done once) or into the feature folder if genuinely specific.
5. `tools/check_feature_diff.py --base origin/main` must pass.
6. `score_implementation` gate, then merge.

## Per-ticket facts

| Ticket | Branch state | Registration | Exception handlers | Notes |
|---|---|---|---|---|
| WF-001 | **no commits** | — | — | Re-run from scratch on the contract |
| WF-002 | 16 files | `include_router(pages_router)` | `FragmentError`, `PermissionDenied` | adds `Toggle`, `Note`, 21 icons |
| WF-003 | 15 files | 10 inline `@app` routes | 5 handlers | shares `/api/rooms` with WF-005 — now allowed |
| WF-004 | **no commits** | — | — | Re-run |
| WF-005 | 11 files | 12 inline `@app` routes | `RoomRefusal` | also edited `audited.py` + `store.py` — review before trusting |
| WF-006 | 15 files | 8 inline routes under `/api/analytics` | none | clean; smallest port |
| WF-007 | 19 files | `include_router(library_router)` | `LibraryError` | adds `Toggle`, `Note`, 8 icons |
| WF-008 | 16 files | none in api.py | none | `library/` package; adds `Notice`, `Checkbox` |
| WF-009 | 10 files | `include_router(publishing_router)` | `ApprovalConflict`, `PublishError` | also edited `store.py` — review |
| WF-010 | 18 files | 8 inline routes under `/api/library` | `SearchError` | **genuinely collides with WF-007** on `/api/library` |
| WF-011 | 15 files | `include_router(publishing_router)` | none | **name clash with WF-009's publishing router** |
| WF-012 | **no commits** | — | — | Re-run |
| WF-013 | 13 files | `include_router(rules_router)` | `RuleError` | **tampered with core `store.create`/`update` call sites** — needs a guard hook or a redesign |
| WF-014 | 25 files | 6 inline routes under `/api/access` | `AccessError` | also edited `audited.py`; left `.opencode/wf014-*.py` scratch files on the branch |
| WF-015 | **no commits** | — | — | Re-run |
| WF-016 | 16 files | 15 inline routes under `/api/crm` | `CrmError` | largest inline surface |
| WF-017 | **no commits** | — | — | Re-run |

## Known real collisions, not artifacts

- **WF-007 vs WF-010** both own `/api/library` for the content library. Decide:
  one feature with two routers, or give WF-010 `/api/library-search` and update
  its tests. The host will refuse the second either way, so this cannot be
  deferred.
- **WF-009 vs WF-011** both define a `publishing_router` and overlap on draft →
  live publishing. These are likely one workflow researched twice. Check the
  dedupe decisions before porting both.
- **WF-013** reaching into the core write path to validate rules is the one port
  that does not reduce to moving files. Options: a payload-guard hook in the
  host, or drop the generic-route guard and validate only on the rules routes.
  Put it to Jev before choosing.

## Order

Start with the ones that need no decisions and no shared-UI promotion, so the
app grows visibly early (Jev: `green_localhost_first`):

1. WF-006 analytics — clean, no handlers, one icon
2. WF-016 CRM sync — self-contained, one handler
3. WF-014 access windows — self-contained, one handler
4. WF-003 documents, WF-005 rooms — coexist under `/api/rooms`
5. WF-002 pages, WF-007 library, WF-008 external sync, WF-009 publishing
6. Resolve the WF-007/WF-010 and WF-009/WF-011 overlaps, then WF-010, WF-011
7. WF-013 last, after the guard decision
8. Re-run WF-001, WF-004, WF-012, WF-015, WF-017 as fresh builds on the contract

## Branches that touched the audit core

WF-005, WF-009, WF-010 and WF-014 edited `store.py` or `db/audited.py`. Those
edits are the product's guarantee and were written by twelve different agents;
read them before carrying any across.
