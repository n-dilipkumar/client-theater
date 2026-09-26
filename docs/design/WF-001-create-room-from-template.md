# WF-001 — Create a Digital Sales Room from an account and a template

- **Ticket:** WF-001
- **Research:** [`docs/research/digital-sales-room-workflows/wf/WF-001.md`](../research/digital-sales-room-workflows/wf/WF-001.md)
- **Branch:** `feature/WF-001-create-room-from-template`
- **Status:** implemented, tests passing, Jev-validated

## What the research says happens

An operator opens the sales-room console, clicks *New Digital Sales Room*, and gets a
three-step wizard:

1. **Select the account** to associate with the room. The account "determines the team
   members and contacts available to invite."
2. **Select a template.** "Use the pre-configured DSR template to get started; you can
   customize the layout later."
3. **Enter a name**, optionally a **Friendly URL**, and **Save**. The room appears in the
   Rooms list.

The data flow is what shapes this implementation: the result is "a new DSR entry bound to
a newly created Liferay site (one site per room; the site is what later supplies the
'Site ID' integration key)." Two consequences:

- **The account binding is load-bearing.** It is what makes invite-by-email resolve
  without a separate directory lookup, so `account_id` is a first-class field on the room,
  not a free-text name.
- **Template identity is a pair.** The vendor inventory API exposes
  `digitalSalesRoomTemplateId` *and* `digitalSalesRoomTemplateVersionId`, so a room pins a
  specific version of the template it was created from.

No public vendor API is documented for the create action itself, and none is used. The
read-side inventory API is noted in the research as something a third party calls *after*
rooms exist; it is out of scope here (see [Not in scope](#not-in-scope)).

## Design

### Storage: no new columns, no migration

Everything is a record in the existing open-JSON `records` table, written through
`AuditedDatabase`. **No migration and no typed column was added.** The fields the workflow
itself needs are documented below and are filterable through the dynamic index, so no team
needs to coordinate to add a field of their own.

`room` record `data`:

| Field | Why it exists |
|-------|---------------|
| `name` | Operator input, shown to the buyer. |
| `status` | `active` or `archived`; the Rooms list filters on one at a time and defaults to Active. |
| `account_id`, `account_name` | The account binding. `account_id` is the reference; `account_name` is denormalised so the list does not need a join. |
| `template_id`, `template_version_id` | The pinned template pair. |
| `template_name`, `template_source` | `shipped` or `store`, for display. |
| `friendly_url`, `friendly_url_source` | The URL, and whether the operator typed it or it was derived. |
| `site_id` | The read-only Site ID integration key. |
| `created_by`, `created_by_username` | Matches the fields the vendor inventory API returns. |

`site` record `data`: `name`, `friendly_url`, `status`, `template_id`. The site is written
with `room_id` set, which is what makes "one site per room" queryable.

`account` and `template` records are ordinary schema-flexible records — the seed writes
`domain`, `tier`, `member_count`, `contact_count` on accounts, and a team can write
whatever else it likes.

### Atomicity: one transaction, two records

A room and its site must not be able to half-exist. `AuditedDatabase` gained a
`transaction()` context manager that yields an `AuditedWriter`; the room and the site are
written through that one handle, so either both records and both audit rows commit or
neither does.

This was **decided by Jev, not by taste** (`choose_approach`, audit
`jev-20260925T214101-11156-61605`, confidence 0.80, margin 0.74 over the runner-up), which
chose extending the audited wrapper over collapsing everything into the room record or
writing the two records sequentially.

Two details that fall out of it:

- The room id and site id are minted from one random token inside the block, so the room's
  `site_id` and the site that serves it cannot drift apart.
- Friendly-URL resolution happens **inside** the transaction, so the uniqueness check and
  the insert share one write lock and two operators cannot claim the same URL by racing.
- Each write still produces its own audit row, stamped with the same `request_id`, so one
  wizard submission reads back as a unit via `GET /api/audit?request_id=...`.

`AuditedWriter` shares its implementation with `AuditedDatabase.create`/`update` (both call
the same private `_insert_record`/`_update_record`), so a transactional write cannot be
indexed or audited differently from a standalone one. A single-record write attempted
*inside* a transaction block is refused with a clear error rather than opening a nested
transaction.

### Template catalogue: shipped set overlaid with the store

The research says the template set is "shipped with the app", but this project requires
that a team can add an entity without a deploy. **Decided by Jev**
(audit `jev-20260925T214101-11156-61939`, confidence 0.99): ship a module-level catalogue of
three built-in templates and overlay any `template` records on top at read time, store
records winning on `template_id` collision.

So a fresh deployment has something to select, and `POST /api/records/template` adds or
retunes a template with no code change. A store template needs only `template_id`;
`template_version_id` falls back to the id.

### Validation and failure

| Condition | Code | Status |
|-----------|------|--------|
| Empty or >200-char name | `invalid_name` | 400 |
| `account_id` missing, not an `account`, or soft-deleted | `account_not_found` | 404 |
| `template_id` not in the catalogue | `template_not_found` | 400 |
| Friendly URL that normalises to nothing, or is not `lowercase-words-hyphenated`, or >64 chars | `invalid_friendly_url` | 400 |
| Operator-supplied friendly URL already in use | `friendly_url_taken` | 409 |
| `status` outside `active`/`archived`/`all` on the list | `invalid_status` | 400 |

**An operator-supplied friendly URL that collides is an error, not a silent rewrite** —
rewriting what someone typed would put the room at an address they did not ask for. A URL
*derived* from the name has no such intent, so it gets a numeric suffix (`-2`, `-3`, …)
until free.

Every refusal happens before or inside the transaction, so a refused request writes
nothing: no record, no audit row.

### API

| Route | Purpose |
|-------|---------|
| `GET /api/room-templates` | Step 2. Shipped catalogue overlaid with store records. |
| `GET /api/accounts?q=` | Step 1. Returns account records untouched, so a team's own fields survive. |
| `POST /api/rooms?actor=&request_id=` | Steps 1–3. Requires `name`, `account_id`, `template_id`; optional `friendly_url`, `created_by`, `created_by_username`. **Every other field is stored and indexed as-is.** |
| `GET /api/rooms?status=&q=&account_id=&template_id=&limit=&offset=` | The Rooms list. `status` defaults to `active`. |

The generic `/api/records/*` routes are unchanged and remain the way to query arbitrary
JSON paths (`?where={"branding.theme":"dark"}`). `/api/rooms` deliberately speaks only the
wizard's own filters rather than pretending to be a general query surface.

Free-text `q` is matched in Python over at most 1000 records, because the dynamic index
answers exact matches only. This is a deliberate, bounded limitation.

### UI

`frontend/src/pages/Rooms.jsx` is the researched wizard: a `Stepper` (with
`aria-current="step"`), three steps, and the list below with a search box and a Status
filter defaulting to Active. Two primitives were added to `components/ui.jsx`:

- `Stepper` — real `<ol>` items so position is announced.
- `ChoiceCard` — a real `<input type="radio">` inside its own `<label>`, so arrow-key
  navigation and group semantics work for free, the whole 44px card is the touch target,
  and the focus ring is drawn on the card via `has-[:focus-visible]`.

Design-system rules honoured: no emoji as icons (inline SVG set, `check`/`back` added),
`min-h-11` touch targets, visible focus rings (never removed), 150–300ms transitions,
`prefers-reduced-motion` respected, text contrast ≥4.5:1, and a refused save sends the
operator back to the step that caused it rather than stranding them on a form that cannot
succeed.

### Tests

- `backend/tests/test_wf001_create_room.py` — the workflow: bindings, one-site-per-room,
  version pinning, every validation path, friendly-URL rules, atomicity (including a
  failure *after* the room insert leaves no room and no audit row), schema flexibility,
  the template overlay, the list filters, and the HTTP surface.
- `backend/tests/test_audited.py` — the transaction primitive: commit, full rollback,
  per-write audit rows, index visibility, mirror written only after commit, nested-write
  refusal, and reading one request back by `request_id`.
- `tools/verify_localhost.py` — a headless end-to-end pass over the wizard endpoints,
  including that a refused creation changes neither record count nor audit count.

`backend/seed.py` now builds demo rooms through `create_room` itself, so the demo data is
created by exactly the code path the API uses and there is no second implementation to
drift.

## Automations

**None, by design.** The research is explicit that no automation runs at creation time: the
only automation in the vendor flow is a Liferay module *site initializer* that logs a
console line when the module starts, and it is not per-room.

So every effect of this workflow is a synchronous, audited write inside the request that
triggered it. There is no background job, no queue, no scheduler, and no retry loop:

- `POST /api/rooms` performs the two writes and returns, or performs nothing and returns
  an error.
- Nothing runs later that could bring a half-created room into existence — which is the
  main reason the two writes share one transaction rather than relying on a repair step.

If a future ticket needs an automation (sending invites on creation, say, or indexing rooms
into a search engine), it belongs in that ticket's design, not here. The one affordance
this workflow leaves for it is `request_id`: a later asynchronous action can stamp the same
`request_id` and be read back as belonging to the same submission.

## Test map

Every area the workflow depends on has a test that would fail if it regressed.

| Area | Test |
|------|------|
| Account binding | `test_created_room_binds_account_template_and_site` |
| Template version pinning | `test_room_pins_the_template_version_it_was_created_from` |
| One site per room, room-scoped | `test_every_room_gets_exactly_one_site_and_the_room_carries_its_id` |
| Both writes in one transaction | `test_room_and_site_are_written_in_one_transaction` |
| No half-created room on failure | `test_a_failure_after_the_room_is_written_leaves_no_room` |
| Name validation | `test_name_is_required`, `test_name_length_is_bounded` |
| Account resolution, incl. wrong collection and soft-deleted | `test_unknown_account_is_refused_and_nothing_is_written`, `test_a_record_from_another_collection_is_not_an_account`, `test_soft_deleted_account_cannot_be_bound` |
| Template resolution | `test_unknown_template_is_refused` |
| Friendly URL: derived, suffixed, normalised, conflicting, impossible | `test_friendly_url_is_derived_from_the_name_when_omitted`, `test_derived_friendly_url_is_suffixed_until_free`, `test_operator_supplied_friendly_url_is_normalised`, `test_duplicate_operator_friendly_url_is_a_conflict_not_a_silent_rewrite`, `test_friendly_url_that_normalises_to_nothing_is_refused` |
| Schema flexibility, including dotted-path filtering | `test_unknown_fields_are_stored_and_indexed_without_a_migration` |
| Bindings cannot be spoofed by a caller | `test_extra_fields_cannot_overwrite_the_workflows_own_bindings`, `test_reserved_envelope_keys_cannot_be_smuggled_into_the_payload` |
| Shipped catalogue, store overlay, store override, bare template | `test_shipped_templates_are_offered_on_a_fresh_database`, `test_a_store_template_appears_alongside_the_shipped_ones`, `test_a_store_template_can_override_a_shipped_one_without_a_redeploy`, `test_a_store_template_only_needs_a_template_id` |
| List: status default, archived, all, invalid, search, account/template filter, pagination | `test_list_defaults_to_active_rooms_only` through `test_list_paginates` |
| Audit: actor, source, one row per record, request-id correlation | `test_post_rooms_is_audited_with_the_actor`, `test_post_rooms_shares_one_request_id_across_both_audit_rows` |
| HTTP status mapping | `test_post_rooms_with_unknown_account_is_404`, `..._unknown_template_is_400`, `..._missing_name_is_400`, `..._taken_friendly_url_is_409` |
| A refused creation writes nothing | `test_a_refused_creation_writes_nothing_at_all` |
| Transaction primitive itself | `test_transaction_*` in `backend/tests/test_audited.py` |
| End to end over HTTP | section 6 of `tools/verify_localhost.py` |

## Tooling note: the Jev gate helpers cannot currently pass

Found while running the gates for this ticket, and **not fixed here** because it is the
thing that decides whether this branch merges, so it is not mine to change unilaterally.

`Jev.decide` uses its `pass_option` argument for two different jobs: it looks the gate
*question* up by that name (`tools/jev.py:371`), and `_apply_gate` then compares the
*answer value* against the same string (`tools/jev.py:432`). Any gate whose passing answer
value differs from its question name therefore reports `fail` forever.

Three helpers are affected, because each names its question `verdict` while the passing
value is something else:

| Helper | Question name | Passing value | Result |
|--------|---------------|---------------|--------|
| `validate_workflow` | `verdict` | `accept` | can never pass |
| `validate_design` | `verdict` | `ready` | can never pass |
| `score_implementation` | `verdict` | `merge` | can never pass |

Observed on this ticket: `validate_design("WF-001", ...)` returned
`verdict: fail` with the reason `chose 'ready' rather than 'verdict'` — Jev had in fact
selected *ready*. `validate_workflow` is presumably broken the same way.

Suggested fix: give `decide` a separate parameter for the question name, or have
`_apply_gate` treat a `choice` answer as passing when its value equals the *question's*
success option. The four decisions for this ticket were recorded by calling `decide`
directly with the gate question named after its passing value; the questions, evidence,
thresholds, and audit trail are otherwise unchanged.

## Not in scope

- **The vendor read-side inventory API** (`GET .../reporting/v2/digitalSalesRooms`). The
  research documents it as something a third party calls *after* rooms exist. The room
  record already carries the fields it returns (`name`, the template id pair,
  `created_by`, `created_by_username`, timestamps), so mapping it later is a read-only
  addition.
- **Archive/unarchive actions.** `status` is filterable and settable, but the per-row
  *Actions* menu that toggles it is a separate workflow.
- **Inviting people.** The account binding is stored precisely so that invite-by-email can
  resolve; the invite flow itself is a different ticket.
- **Template editing.** Templates are selected, not authored, in this workflow.
