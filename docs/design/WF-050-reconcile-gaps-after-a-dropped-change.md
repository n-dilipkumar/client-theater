# WF-050 - Reconcile gaps after a dropped change stream

**Status:** implemented on the plugin host
**Workflow source:** `docs/research/digital-sales-room-workflows/wf/WF-050.md`
**Raw evidence:** `docs/research/raw/crm-integration.md` section 18
**Branch:** `p2-ticket-1`
**Feature id:** `wf-050-reconcile-gaps-after-a-dropped-change-st` - **Prefix:** `/api/wf-050`
**Backend module:** `backend/dsr/features/wf050_reconcile_gaps_after_a_dropped_change_st.py`
**Frontend folder:** `frontend/src/features/wf-050-reconcile-gaps-after-a-dropped-change-st/`

## 1. What the research actually establishes

Numbered, with the sentence that fixes each one.

1. **The gap event types are a closed enumeration of four.** "The `changeType` field in the gap event header identifies the gap event and the associated operation, and can take one of these values: `GAP_CREATE`, `GAP_UPDATE`, `GAP_DELETE`, `GAP_UNDELETE`."
2. **An overflow is a fifth value and it is not a gap.** "The `changeType` field header value is `GAP_OVERFLOW`."
3. **A gap event names a record. An overflow event does not.** "Gap events don't contain record data, but they contain the record ID, which enables you to retrieve the record from Salesforce." / "Overflow events include header fields but no record data and no record ID."
4. **The overflow threshold is 100,000 changes in one transaction.** "Overflow events are generated when a single transaction involves more than 100,000 changes. The first 100,000 changes generate change events."
5. **The gap repair marks the record dirty as of the gap event's date.** "For the gap event, mark the corresponding record as dirty locally as of the date of the gap event."
6. **A dirty record stops accepting incremental change events.** "If you receive change events for new changes for the same record before the data has been reconciled, don't process them."
7. **The ordering test is two timestamp comparisons.** "To ensure that the change is after the gap event, compare the `commitTimestamp` fields of both events. To ensure that the change occurred before the data is reconciled, compare the `LastModifiedDate` fields on the change event and the record retrieved in the next step."
8. **The repair is a full re-read, then clear the flag.** "Reconcile the data for record C. Make a Salesforce API call, such as a REST API call, to retrieve the full data for record C, and save it in your system. Then clear the dirty flag on that record."
9. **The overflow procedure has five steps, in order.** "1. After you receive an overflow event in your subscriber, unsubscribe from the channel, and stop processing further events. ... 2. Store the Replay ID of the overflow event. This ID is the starting point for the data reconciliation. 3. Reconcile the data for new, updated, and undeleted records. ... 4. Reconcile the data for deleted records by performing one of the following steps: a. Get the non-deleted records from Salesforce, and synchronize. ... b. Or get the deleted records from Salesforce, and synchronize. ... Query all records for the entity with `isDeleted=true`."
10. **The room resubscribes and writes a reconciliation event to the sync log.** "Room resubscribes and records a reconciliation event in the sync log for audit."
11. **Dataverse carries deletes inside the same cursor.** `"@odata.context":".../accounts/$deletedEntity", "id": "2e451703-...", "reason": "deleted"`.
12. **A Dataverse token dies after seven days.** "Changes are returned if the last token is within a default value of seven days. ... If unprocessed changes are older than the configured value, the system throws an exception."

### Sourced vs. inferred

| Inference | Why it is needed | Recorded in |
|---|---|---|
| A dirty record is keyed by `(room, entity, record_id)` | The research says "the corresponding record" and "mark the corresponding record as dirty locally". It never says the key. A room holding two buyers cannot key a dirty marker on the record id alone. | `gap_vocabulary.DIRTY_KEY` |
| The gap path and the overflow path share one engine | The research's extensibility note says the same code serves "a single gap, an overflow, and a full reconnect" because both are "re-read + set-difference". | `reconcile_engine.ReconcileEngine` |
| The two resumable positions are two named cursors, not one | The research's own gaps section says the Salesforce Replay ID and the Dataverse delta link "were not reconciled in a single source" and are "a different shape". | `gap_vocabulary.CURSOR_KINDS` |
| The overflow threshold is a named constant, not a literal | The research states 100,000 as a fact about the vendor, not as a setting a deployment chooses. | `gap_vocabulary.OVERFLOW_CHANGE_THRESHOLD` |
| An expired Dataverse delta link falls back to a full re-read | The research calls the seven-day window "the hard deadline after which recovery must fall back to a full re-read". It does not say what the room does at the deadline, only that the vendor throws. | `reconcile.expired_cursor_verdict` |
| HubSpot has no gap path and the API says so | The research's gaps section: "HubSpot has no documented gap/overflow analogue (it uses webhook redelivery instead)". | `gap_errors.UnsupportedVendor` |
| The room holds the vendor's tables rather than calling them | No workflow in this research opens a socket, and a feature with a network call in a repair path cannot be tested. The wire shapes stay the researched ones. | `gap_sources.SimulatedCrm` |
| A run's log lines carry a gapless 1-based `seq` | Not in the research. Required by `test_ordering_determinism.py`, because SQLite orders ties by insertion and a log ordered by insertion order fails intermittently under `pytest-xdist`. | `reconcile_engine.ReconcileEngine.log` |

## 2. Constraints from AGENTS.md

- Every write goes through `AuditedDatabase`. No new tables, no new columns, no migration.
- The domain package imports the store and nothing else. No `fastapi`, no `dsr.api`, no `dsr.deps`, no `sqlite3`.
- Payloads are arbitrary JSON in `records.data`. No typed column for a team's field.
- Every audit row names a route the app actually serves, built from `router.prefix`.
- `seed(db, context)` returns a string every character of which `cp1252` can encode.

## 3. The domain package

`backend/dsr/crm_integration/` already exists. It is WF-042's package and it is the
package for this research domain (`crm-integration`, section 9). **This change extends
it and adds no second package for the same domain.**

Six new modules. None of them edits a file WF-042 owns, so the two features share a
directory without sharing a line.

| Module | What it owns |
|---|---|
| `gap_vocabulary.py` | The five change types, the three vendor facts, the two cursors, the numbers, the states. `describe()` is served at `/vocabulary`. |
| `gap_errors.py` | `GapReconcileError` and its subclasses. A **new base class**, because `CrmIntegrationError` is already claimed by WF-042's handler and two handlers for one type is a collision the host refuses. |
| `gap_inferences.py` | Every judgement call above, named and served at `/inferences`. |
| `gap_sources.py` | The read seam: a `CrmReader` protocol, a `SimulatedCrm`, and `default_reader()`. |
| `reconcile.py` | The pure rules. Classification, the two timestamp comparisons, the dirty-drop rule, the deleted-set difference, the cursor verdicts. |
| `reconcile_engine.py` | The facade the HTTP layer calls. Owns the six collections. |

## 4. The six collections

All prefixed `crm_gap_`. No other feature owns any of them, and this feature writes
into no collection another feature owns. The read seam is section 6.

| Collection | One row is | Why it exists |
|---|---|---|
| `crm_gap_event` | one gap or overflow event the subscriber reported | The research's first step. Header fields only, plus the classification. |
| `crm_gap_dirty` | one record marked dirty awaiting reconciliation | "Mark the corresponding record as dirty locally as of the date of the gap event." |
| `crm_gap_cursor` | one resumable position: a Replay ID for one entity, or one delta link for one org | "Store the Replay ID of the overflow event. This ID is the starting point for the data reconciliation." |
| `crm_gap_run` | one reconciliation: a per-record repair, or a whole-entity repair | The audit unit. Carries the counts and the outcome. |
| `crm_gap_log` | one line of a run's sync log, numbered `seq` | "Room ... records a reconciliation event in the sync log for audit." |
| `crm_gap_replica` | one reconciled record row, with `deleted` set on the tombstones | The research's "replica overwrite + delete diff". Namespaced because WF-043 and WF-045 each own a replica of their own. |
| `crm_gap_source_row` | one row of the room's copy of the vendor's entity tables | The research names "CRM entity tables (incl. Recycle Bin / soft-deleted)" as a data source *separate* from "room replica". Reading the replica to repair the replica would make the repair agree with itself. |

### Recorded decision: this feature repairs a replica it does not own

The issue asks for the decision to be recorded. **WF-050 writes only into
`crm_gap_replica` and never into another feature's replica collection.**

Considered options.

1. *Overwrite the streaming replica in place.* This is what "reconciles its replica"
   most literally says. Rejected: `backend/tests/test_wf043.py` has an enforced test
   named `test_no_other_feature_writes_into_a_collection_this_one_owns`, which fails
   any feature module that so much as quotes WF-043's collection name in its source.
   Two features writing one collection is invisible to the route-collision check and
   is exactly the defect that test exists to prevent.
2. *Own a second copy of the same rows.* That is what this change does, and the
   consequence is stated rather than hidden: a room that wants one merged replica
   needs a surface that reads both `crm_gap_replica` and the streaming replica and
   picks the newer row. That merge is a third party's job, and the extensibility note
   in the research invites exactly that ("A third party can add a clock skew
   detector ... the same primitive the docs use to decide whether a new change is
   older than the reconciliation").
3. *Depend on WF-043's engine and call its replica writer.* Rejected: the host mounts
   features alphabetically and three other agents are landing CRM work at the same
   time. A cross-feature import is a merge conflict waiting for a name to change.

## 5. Recorded decisions the issue asks for

**Vendor scope.** Salesforce and Dataverse only. `GAP_OVERFLOW` on a HubSpot stream
is refused with `UnsupportedVendor`, and the refusal quotes the research: "HubSpot
has no documented gap/overflow analogue (it uses webhook redelivery instead)". No
HubSpot gap path is built.

**Two cursors, not one.** `replay_id` is per `(room, entity)`, because Salesforce
issues "one overflow event for each entity type included in that set" and each carries
its own Replay ID. `delta_link` is per `(room, org)`, because Dataverse hands back one
opaque `@odata.deltaLink` for the whole response and it carries no per-entity
position. A room may hold both at once, and a single `cursor` field would have to
lie about one of them.

**The overflow threshold.** `OVERFLOW_CHANGE_THRESHOLD = 100_000`, quoted where it is
declared. Used to classify an event whose header reports a change count, and served
at `/vocabulary` so a client can render the rule.

**The Dataverse deadline.** Seven days. Past it the delta link is not resumable, the
vendor "throws an exception", so the room does not ask. `expired_cursor_verdict`
returns `full_reread` and the run records that it fell back, rather than resuming and
discovering the exception mid-transaction.

**Deleted set, two ways.** Both of the research's options are implemented and the run
records which one it used. `difference` (option a): read the non-deleted records and
delete whatever the room held that the read did not return. `recycle_bin` (option b):
query the entity with `isDeleted=true` and delete exactly what comes back. Dataverse
needs neither, because its delta response carries the deletions inline as
`$deletedEntity` / `reason: "deleted"`, so `dataverse_delta` is the third source and
it reads from the same cursor.

## 6. The read seam

Reconciliation is a **full re-read**, so the engine needs to read the vendor. It does
not open a socket. The module `gap_sources` declares a reader protocol, and the room
supplies one. A reader answers five questions, and every answer is plain JSON.

| Method | Question it answers | Research sentence |
|---|---|---|
| `entities()` | Which entity types does this vendor expose? | "one overflow event for each entity type included in that set" |
| `record(entity, record_id)` | What does one record hold right now, or `None` if it is gone? | "Make a Salesforce API call, such as a REST API call, to retrieve the full data for record C" |
| `live_records(entity)` | Every record of the entity that is not deleted. | "Get the non-deleted records from Salesforce, and synchronize." |
| `recycle_bin(entity)` | Every soft-deleted record of the entity. | "Query all records for the entity with `isDeleted=true`." |
| `delta(entity, delta_link)` | Dataverse's change page: the live rows, the deleted ids, and the next link. | the `$deletedEntity` / `reason: "deleted"` response |

- `record` answers the per-record repair: "Make a Salesforce API call, such as a REST
  API call, to retrieve the full data for record C". It returns `None` when the record
  is gone, which is what turns a `GAP_DELETE` into a tombstone rather than an error.
- `live_records` answers option (a) of the overflow procedure: "Get the non-deleted
  records from Salesforce, and synchronize."
- `recycle_bin` answers option (b): "Query all records for the entity with
  `isDeleted=true`. You get all the soft-deleted records for that entity that are in
  the Recycle Bin."
- `delta` answers Dataverse: it returns `{"live": [...], "deleted": [...],
  "delta_link": "..."}`, where the deletes arrived inline as `$deletedEntity` /
  `reason: "deleted"`.

`SimulatedCrm` is the room's own copy of those tables, built by `put()` and `delete()`.
A room pointed at a live vendor changes `gap_sources` and nothing else. Every method
returns plain JSON dicts, because a record payload is arbitrary JSON and a team adding
a CRM field must need no coordination.

## 7. The two comparisons, with worked values

Both come from one sentence of the research, and both are implemented as pure
functions in `reconcile` so a test can pin them without a database.

**Comparison A: is this change after the gap?** "To ensure that the change is after the
gap event, compare the `commitTimestamp` fields of both events."

`change_is_after_the_gap(gap_commit_ts, change_commit_ts)` is true when
`change_commit_ts > gap_commit_ts`. Both are parsed to UTC before the comparison; an
unparseable timestamp is a refusal, never a silent false.

**Comparison B: did this change already land in the re-read?** "To ensure that the
change occurred before the data is reconciled, compare the `LastModifiedDate` fields on
the change event and the record retrieved in the next step."

`change_is_already_in_the_re_read(change_last_modified, record_last_modified)` is true
when `record_last_modified >= change_last_modified`. The record comes from the vendor
now, so a record at least as new as the change means the re-read already carries it.

Worked values, all UTC.

| gap commit | change commit | A: after the gap | Verdict |
|---|---|---|---|
| `2026-09-27T10:00:00+00:00` | `2026-09-27T10:00:00+00:00` | false | Same instant. Dropped. |
| `2026-09-27T10:00:00+00:00` | `2026-09-27T09:59:59+00:00` | false | Earlier. Dropped. |
| `2026-09-27T10:00:00+00:00` | `2026-09-27T10:00:01+00:00` | true | Later. Held, pending B. |

| change LastModifiedDate | record LastModifiedDate | B: already in the read | Verdict |
|---|---|---|---|
| `2026-09-27T10:05:00+00:00` | `2026-09-27T10:06:00+00:00` | true | The read covers it. Applied. |
| `2026-09-27T10:05:00+00:00` | `2026-09-27T10:04:00+00:00` | false | The read is older. Still dropped. |

**The drop rule.** `POST /rooms/{room_id}/change-events` returns
`{"applied": bool, "reason": str}` and never raises for a drop, because a drop is the
research's prescribed behaviour and not a caller error. The four reasons, in the order
they are checked:

1. `no_dirty_marker` - the record is clean, so the change is applied.
2. `older_than_the_gap` - comparison A is false. Dropped.
3. `covered_by_the_re_read` - comparison A is true and B is true. Applied, because the
   dirty repair already wrote the newer row.
4. `dirty_and_newer_than_the_read` - A is true and B is false. Dropped, and the dirty
   marker stays open, because the next re-read must see this change too.

Reason 4 is the one the research's sentence exists for. A change newer than the record
the vendor returned has **not** reached the room, and applying it on top of a stale
read would produce a row that was never true.

## 8. What a reconciliation run does

`POST /rooms/{room_id}/reconcile` takes `{"event_id": ..., "record_id": ...}` for the
gap path, or `{"event_id": ..., "entity": ..., "deleted_source": ...}` for the overflow
path. It runs these steps and writes one row per step into `crm_gap_log`, numbered
`seq` from 1.

Gap path.

1. `run_opened`. The run row is created with the event id and the entity.
2. `record_read`. One `reader.record(entity, record_id)`.
3. `replica_overwritten` when the read returned a row, or `replica_deleted` when it
   returned `None`. A `GAP_UNDELETE` therefore writes a live row even though the
   record had been in the Recycle Bin.
4. `dirty_flag_cleared`. "Then clear the dirty flag on that record." The marker row is
   kept, not deleted, with `state = "reconciled"` and the run id on it, so the data
   health view can answer "was this record repaired, and by which run".
5. `run_complete`.

Overflow path.

1. `run_opened`.
2. `unsubscribed`, then `replay_id_stored`. Both happen when the overflow event is
   reported, not here; the log records them against the run so the order the research
   fixes is visible in one place.
3. `entity_read`. `reader.live_records(entity)`, `reader.recycle_bin(entity)`, or
   `reader.delta(entity, delta_link)`, chosen by `deleted_source`.
4. `rows_written`, one line per reconciled row, then `rows_deleted`, one line per
   tombstone. Option `difference` derives the tombstone set as
   `held_record_ids - {ids the read returned}`; option `recycle_bin` takes the ids the
   Recycle Bin query returned; option `dataverse_delta` takes the `reason: "deleted"`
   entries out of the delta response.
5. `run_complete`.

Run states, and what each means.

| State | Meaning |
|---|---|
| `open` | Created, no read issued yet. |
| `complete` | The re-read happened and the diff was applied. |
| `expired_cursor` | The Dataverse delta link was past seven days, so no re-read was attempted and the room fell back to a full re-read. |
| `refused` | The request named something the research does not describe. Nothing was written to the replica. |

The Dataverse deadline, concretely. `GET /rooms/{room_id}/cursors` returns, per cursor,
`{"kind", "position", "held_since", "age_days", "resumable", "fallback"}`. When
`held_since` is more than seven days old and the kind is `delta_link`, `resumable` is
false and `fallback` is `full_reread`. A reconcile against such a cursor produces a run
in `expired_cursor` and a log line that says the vendor would have thrown, so nobody
resumes from a token that is already dead.

## 9. The HTTP surface

Prefix `/api/wf-050`. Fifteen routes, four of which write.

| Method | Path | What it serves |
|---|---|---|
| GET | `/vocabulary` | The enumeration, the cursors, the numbers, the states. |
| GET | `/inferences` | The register above, served. |
| POST | `/rooms/{room_id}/gap-events` | Step 1. The subscriber reports a gap or an overflow. |
| GET | `/rooms/{room_id}/gap-events` | The event ledger. |
| GET | `/rooms/{room_id}/gap-events/{event_id}` | One event. |
| POST | `/rooms/{room_id}/change-events` | Step 2. An incremental change arrives. Applied, or dropped with the reason. |
| GET | `/rooms/{room_id}/dirty` | The data-health view of dirty records. |
| POST | `/rooms/{room_id}/reconcile` | Steps 3 and 5. The full re-read, the overwrite, the delete diff, the flag clear. |
| GET | `/rooms/{room_id}/runs` | The runs. |
| GET | `/rooms/{room_id}/runs/{run_id}` | One run. |
| GET | `/rooms/{room_id}/runs/{run_id}/log` | The sync log, in `seq` order. |
| GET | `/rooms/{room_id}/cursors` | Both cursors, with their verdicts. |
| GET | `/rooms/{room_id}/replica` | The reconciled rows this workflow owns, namespaced. |
| POST | `/rooms/{room_id}/subscribe` | Step 6. Resubscribe after an overflow. |
| GET | `/rooms/{room_id}/health` | Dirty count, open runs, cursor verdicts, in one payload the page's stat cards read. |

Every path parameter name is already in `tools/verify_all_routes.py`'s placeholder
table (`room_id`, `run_id`, `event_id`), so that tool substitutes real values and a
`{}` body reaches a handler that answers 4xx with a sentence.

The frontend folder is `frontend/src/features/wf-050-reconcile-gaps-after-a-dropped-change-st/`
and its default export is `{ id, label, icon, iconPath, order, Component }`, with `id`
byte-identical to `FEATURE["id"]`. The page reads `/vocabulary`, `/cursors`, `/dirty`,
`/runs` and `/health`, and it renders every state as text beside its badge, so no state
is carried by colour alone.

## 10. The test plan

One row per acceptance test, mapped to the research sentence it pins. "Backend" means
`backend/tests/test050.py`, "HTTP" means `backend/tests/test050_http.py`, "Frontend"
means the colocated `GapReconcile.test.jsx`.

The "Pins" column names the numbered sourced item in section 1 that the test quotes.

| # | Test | Pins |
|---|---|---|
| 1 | Each of the four gap types classifies as a gap and names its entity and record | 1 |
| 2 | The overflow type classifies as an overflow and carries no record id | 2, 3 |
| 3 | An overflow reporting more than 100,000 changes is accepted, and the threshold is the named constant rather than a literal in a condition | 4 |
| 4 | A gap marks the record dirty as of the gap event's commit timestamp | 5 |
| 5 | A change event for a dirty record is dropped, and the replica row is unchanged afterwards | 6 |
| 6 | Comparison A answers each of the three worked rows in the table above | 7 |
| 7 | Comparison B answers both of the worked rows in the table above | 7 |
| 8 | A change newer than the vendor's record is dropped and the dirty marker stays open | 7 |
| 9 | A reconcile re-reads the record, overwrites the replica row, and clears the dirty flag | 8 |
| 10 | A gap delete whose record reads as absent writes a tombstone and clears the flag | 8, 9 |
| 11 | An overflow unsubscribes, stores the Replay ID, and the stored cursor is keyed by entity type | 9 |
| 12 | The difference source deletes exactly the held ids minus the returned ids | 9 |
| 13 | The Recycle Bin source deletes exactly the ids that query returned | 9 |
| 14 | The Dataverse delta source reads its deletes out of the delta response | 11 |
| 15 | A delta link older than seven days reports itself unresumable with a full re-read as the fallback | 12 |
| 16 | A reconcile against an expired delta link ends in the expired-cursor state and touches no replica row | 12 |
| 17 | Resubscribing records the resubscription and closes the open overflow run | 10 |
| 18 | Every log line of a run carries a sequence number, and the numbers run from one to the line count with no gaps | Ordering determinism |
| 19 | An overflow reported on a HubSpot stream is refused, and the message quotes the research | Vendor scope decision |
| 20 | The router is mounted by discovery, the prefix is the one the ticket names, no shared file is edited, and neither the feature module nor the domain package reaches sqlite or the shared app | Acceptance criteria |
| 21 | Every audit row this feature writes names a route the registry reports as mounted | Acceptance criteria |
| 22 | Every route answers without a server error to an empty body, and every read route answers on a room that has read nothing | The route verification tool |
| 23 | The seeder's return string encodes as cp1252 and prints | Acceptance criteria |
| 24 | The frontend descriptor's id is byte-identical to the backend feature id | Acceptance criteria |
| 25 | Each gap type renders with its type in text, the dirty list names each record, and a dropped change is reported as dropped | Product surfaces |
| 26 | The descriptor exports the id, the label, an icon path, and the component, and the id matches the ticket pattern | The feature contract |

Rows 1 to 19 live in the two backend test files. Row 18 is the one that keeps this
feature from failing intermittently under parallel runs, so it is stated as an equality
on the whole sequence rather than as a sort check.

## 11. What this change does not do

- It does not apply an incremental change event to a dirty record. The research says
  not to, and there is a test that pins the dropped event as unapplied.
- It does not open a subscription or a socket. `subscribe` records the room's
  intention and the reconciliation that follows it.
- It does not merge its reconciled rows into another feature's replica. Section 4.
- It does not build a HubSpot gap path. Section 5.