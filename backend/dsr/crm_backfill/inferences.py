"""Every judgement call in this package, in one inspectable place.

The research for WF-045 is specific about the parts that decide how a backfill
behaves and silent about everything around them. It gives a volume threshold
(2,000), a page size (5,000), a change-tracking window (seven days), a scope
grant (``crm.export``), a rule for naming a custom object, a statement that the
daily limit resets at local midnight, and - most unusually - a negative, that
Salesforce "doesn't guarantee a service level agreement". It also says what the
cursor record looks like and why it is an interface.

What it does not do is say what a room does when two of those rules disagree, what
"no duplicates, no gaps" means for a row it cannot key, or how much history
"full history" covers. Those edges are where a build has to decide something,
and a judgement call left as a comment in a function body is one nobody re-reads
and a wrong one becomes product behaviour without anyone noticing.

Each entry is:

* **named**, so it can be argued with by name;
* **traceable** - ``basis`` says what the research does and does not say;
* **bounded** - ``value`` is what this build chose, and ``change_it`` says how to
  change it without editing a function body;
* **visible** - :func:`describe` is served at ``GET /api/wf-045/inferences``, so a
  reviewer reads the list instead of reconstructing it from a diff.

Nothing here is a migration, a typed column, or a new required field. It is a list
of ordinary JSON, exactly like everything else this product stores, and it is a
*record* of a judgement rather than a mechanism that enforces one.
"""

from __future__ import annotations

from typing import Any

from dsr.crm_backfill import plan, vendors
from dsr.crm_backfill.vocabulary import NUMBERS, OBJECT_ADDRESSING_QUOTE, REQUIRED_SCOPE

#: The sentence that carries the most weight in this workflow. It is why a
#: cursor is a stored record rather than a value on a run, and why nothing in
#: this package restarts a backfill by reading the range again.
SOURCED_QUOTE = (
    "If the job or the room crashes mid-run, the room restarts from the stored cursor - no "
    "duplicates, no gaps."
)

INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "cursor-advance-after-write",
        "topic": "the order of the page write and the cursor write",
        "basis": (
            "The research states the promise ('no duplicates, no gaps') and the mechanism it names "
            "is the stored cursor. It does not say when in the page cycle the cursor moves."
        ),
        "value": {
            "order": ["write the page", "then advance the cursor"],
            "replay_is_absorbed_by": [
                "the replica is upserted on the vendor's key, so a replayed row merges",
                "a row whose mapped payload is unchanged is not written at all",
            ],
        },
        "why": (
            "Advancing first would make a crash lose the page entirely - a gap. Advancing last makes "
            "a crash replay the page, and the replay is a no-op against a key. The research's two "
            "failure modes are 'duplicates' and 'gaps', and only this order refuses both. The "
            "second mechanism is what keeps a replay from filling the audit log with five thousand "
            "identical writes."
        ),
        "change_it": "_land and _advance in dsr/crm_backfill/engine.py, and merge in transform.py.",
        "blast_radius": "Every page cycle, in both directions.",
    },
    {
        "id": "the-volume-threshold-applies-to-every-vendor",
        "topic": "whether Salesforce's 2,000-record rule decides for Dataverse and HubSpot too",
        "basis": (
            "The number is Salesforce's own wording about Salesforce: 'Any data operation that "
            "includes more than 2,000 records is a good candidate for Bulk API 2.0 ... Jobs with "
            "fewer than 2,000 records should involve bulkified synchronous calls'. The user flow's "
            "own two-way branch - 'an asynchronous extract job (large volumes) or a delta/paged read "
            "(moderate volumes)' - does not name a number."
        ),
        "value": {
            "threshold": plan.bulk_threshold(),
            "rule": "more than the threshold asks for an async job; at or below it, a paged read",
            "applies_to": "every vendor, because the flow's branch is vendor-neutral",
            "vendor_override": "an adapter that does not implement the wanted strategy falls through to its own preference, and says so",
        },
        "why": (
            "The flow states a two-way choice about volume and gives no vendor-specific number for "
            "the other two, so borrowing the one sourced number is the only reading that decides "
            "anything at all. It is a single constant rather than a per-vendor setting, so a "
            "deployment that disagrees changes one value. Where a vendor cannot obey it - HubSpot has "
            "no synchronous export at all - the fall-through is recorded on the run rather than "
            "quietly applied, because a rule that cannot be obeyed should be visible as such."
        ),
        "change_it": "bulk_threshold and choose_strategy in dsr/crm_backfill/plan.py.",
        "blast_radius": "Which strategy every run opens with.",
    },
    {
        "id": "unknown-volume-reconsiders-once",
        "topic": "what a run does when it was opened without a volume",
        "basis": (
            "The research gives the volume rule and does not say where the number comes from. The "
            "room can know it - a previous run counted it - or not, on a first ever backfill."
        ),
        "value": {
            "opens_with": "the adapter's preferred strategy",
            "reconsiders": "once, against the vendor's own reported total",
            "only_while": "nothing has been written",
            "replan_cap": 1,
            "logged_as": ["strategy_reconsidered", "run_replanned"],
        },
        "why": (
            "A run that never checked the rule it claims to be applying is a run whose strategy "
            "record says something unverified. Reopening the job is cheap - no rows have landed - "
            "and it is the only moment the correction is free. The cap is one because the decision "
            "is made from the vendor's own record count: a second disagreement would mean the count "
            "is moving under the run, and reopening forever is a loop."
        ),
        "change_it": "_open_job and _reconsider in dsr/crm_backfill/engine.py, MAX_REPLANS.",
        "blast_radius": "Runs opened with no estimated_records.",
    },
    {
        "id": "cursor-adopted-unless-from-scratch",
        "topic": "what a new backfill does with the cursor already stored for a connection",
        "basis": (
            "'If the job or the room crashes mid-run, the room restarts from the stored cursor.' The "
            "research does not say what a *second* backfill against the same connection does, and "
            "that is not a crash."
        ),
        "value": {
            "default": "adopt the stored cursor and continue",
            "opt_out": "from_scratch: true, which requires the full_history scope",
            "cancelled_runs": "keep their cursor, so a later backfill resumes",
            "data_token": "honoured as a starting position, because a change-stream version number is not a job handle",
            "bulk_job_id_and_export_id": "a new run opens a new job; the adoption decides the strategy, not the position",
        },
        "why": (
            "The cursor is described as an interface for resumability, and a cursor that a new run "
            "discards is a cursor that only ever works for one run - which would make the "
            "scheduled-backfill case the research opens with ('Backfill is a scheduled job in the "
            "room') impossible. Discarding it deliberately is therefore an explicit act, and it is "
            "paired with full_history because re-reading a range from the start over rows the cursor "
            "said were already read is exactly the duplication the workflow promises not to produce. "
            "The two cursor kinds behave differently on purpose: a DataToken is a position in a "
            "change stream, so a new read continues from it, whereas a Bulk job id and an export id "
            "name a job that is over - asking a new run to continue one would be asking it to finish "
            "somebody else's work."
        ),
        "change_it": "start in dsr/crm_backfill/engine.py, and cancel.",
        "blast_radius": "Which range every new run actually reads.",
    },
    {
        "id": "expired-cursor-stalls-rather-than-restarts",
        "topic": "what happens when a stored DataToken is older than the vendor will answer for",
        "basis": (
            "Dataverse: 'Changes are returned if the last token is within a default value of seven "
            "days ... If unprocessed changes are older than the configured value, the system throws "
            "an exception.' The vendor throws. It does not say what the calling system should do."
        ),
        "value": {
            "run_state": "stalled",
            "auto_restart": False,
            "refusal": "CursorExpired, 409",
            "remedy": "a new full-history backfill",
            "expiry_is_kind_aware": "only a data_token ages out; a Bulk job id, an export id and a paging cookie do not",
        },
        "why": (
            "The tempting behaviour - notice the token is old, read the whole range again, carry on "
            "under the same run id - is the one thing that makes the research's promise untrue. Every "
            "row would be written twice and the run's own totals would report the duplication as "
            "work done. Stalling says the truth, and the error message names the backfill to open, so "
            "the operator is not left with a stop and no next step. Kind-awareness matters for the "
            "same reason: a finished Salesforce job id resolves or it does not, and Dataverse's "
            "seven-day rule says nothing about it."
        ),
        "change_it": "require_resumable in dsr/crm_backfill/cursors.py; the engine's _stall path.",
        "blast_radius": "Every resume, and every poll of a run holding a data token.",
    },
    {
        "id": "progress-is-indeterminate-without-a-total",
        "topic": "the percentage shown when the vendor has not reported how much there is",
        "basis": (
            "The flow says 'Progress is shown as a percentage in Sync -> Backfill with a per-run "
            "log', and Salesforce says 'Because both Bulk APIs are asynchronous, Salesforce doesn't "
            "guarantee a service level agreement.' The flow asks for a percentage; the vendor "
            "declines to promise when it will be reached."
        ),
        "value": {
            "with_total": "rows seen over the vendor's reported total, as a percentage",
            "without_total": "percent: null, indeterminate: true, and the reason",
            "eta": "never computed, on any state",
            "complete": "100, because the range is covered",
        },
        "why": (
            "A percentage needs a denominator. Inventing one - pages read over pages expected, or a "
            "guess at the total - produces a number that looks authoritative and moves backwards the "
            "moment the guess is wrong, which is worse than no number for a thing an operator is "
            "waiting on. The negative in the research is the reason no ETA exists at all: an "
            "asynchronous vendor has told us in writing that it will not be held to one."
        ),
        "change_it": "progress in dsr/crm_backfill/engine.py.",
        "blast_radius": "The percentage on every run.",
    },
    {
        "id": "unkeyable-rows-are-rejected-not-stalled",
        "topic": "what a run does with a row that carries no value for the replica's key",
        "basis": (
            "The research promises 'no duplicates, no gaps' and names the field map and the upsert. "
            "It does not discuss a row that cannot be keyed at all."
        ),
        "value": {
            "stored": False,
            "counted": "rows_rejected, per run",
            "logged": "one rows_rejected line naming the reason and a short rendering of the row",
            "page_continues": True,
        },
        "why": (
            "Storing it would break the upsert: a row with no key cannot be found again, so the next "
            "read of the same page would create a second one, and the duplication the workflow "
            "promises not to produce would be its own doing. But failing the whole run on one bad row "
            "means one malformed record blocks a range indefinitely, and the operator has to find it "
            "by hand. Rejecting, counting and naming it makes the gap visible and keeps the rest of "
            "the range moving. The short rendering is deliberate: a rejected row goes into a log "
            "somebody reads, and a log is the wrong place for a record that may hold personal data."
        ),
        "change_it": "external_id in dsr/crm_backfill/transform.py; the rejection branch in _land.",
        "blast_radius": "rows_rejected, and the log line beside it.",
    },
    {
        "id": "unchanged-rows-are-not-written",
        "topic": "whether re-reading a page that has not changed counts as work",
        "basis": (
            "The research says the room writes rows in pages and restarts from a cursor. It does not "
            "say what a page write does when the rows are identical to what is already there."
        ),
        "value": {
            "comparison": "the mapped payload, excluding this product's own bookkeeping",
            "unchanged": "counted in rows_unchanged, not written, no audit row, no revision bump",
            "last_seen_at": "means when the row's content last changed, not when it was last read",
        },
        "why": (
            "A retry after a crash is expected to re-read a page. Writing five thousand identical "
            "rows would put five thousand audit rows against one page, and an audit log that cannot "
            "be read is the thing the whole product guarantee is built on. Measuring the payload "
            "rather than the envelope is what makes the count honest: a row that genuinely changed is "
            "written, and its last_seen_at moves, and a row that did not keeps the moment its "
            "content last moved - which is a more useful fact than the last time somebody polled."
        ),
        "change_it": "payload_of and merge in dsr/crm_backfill/transform.py.",
        "blast_radius": "rows_unchanged, the audit volume of a retried page, and last_seen_at.",
    },
    {
        "id": "quota-is-sized-before-a-job-is-opened",
        "topic": "what happens when a backfill is bigger than the rest of today's allowance",
        "basis": (
            "HubSpot: 'The daily limit resets at midnight based on your time zone setting', and 'You "
            "can also check the number of calls used during the current day using this endpoint.' The "
            "automation note calls this 'relevant when sizing a backfill against remaining quota'."
        ),
        "value": {
            "sized_from": "one call to create the job, one to find it ready, one per page",
            "at_start": "a run whose estimate exceeds the remaining calls is refused with 409",
            "mid_run": "a run that reaches the wall waits for the reset rather than failing",
            "unsized": "reported as unverifiable, not refused",
            "window": "half-open [local midnight, next local midnight), per connection",
        },
        "why": (
            "Sizing is the stated requirement, so a start that cannot fit is refused before the job "
            "exists - a job cut off mid-page would leave a cursor parked against a window that is "
            "about to close, which is the worst of the three outcomes. A run that reaches the wall "
            "mid-flight is a different case and gets a different answer: the vendor's limit resets, so "
            "waiting is correct and failing would not be. A plan with no size at all is not refused: "
            "the requirement is to size against the quota, and a plan with no size has nothing to "
            "compare - reported as unverifiable so the check is visibly absent rather than silently "
            "passed."
        ),
        "change_it": "check in dsr/crm_backfill/quota.py; the two refusals in engine.start and _spend.",
        "blast_radius": "Which runs can be started, and what a run does at the wall.",
    },
    {
        "id": "quota-window-is-a-fixed-offset-not-a-named-zone",
        "topic": "how 'your time zone setting' is represented",
        "basis": (
            "The research quotes the rule and does not say how a connection states its zone. Python's "
            "named-zone support needs an IANA database that is not guaranteed to be present."
        ),
        "value": {
            "represented_as": "quota_timezone_offset_hours, a whole-hour offset from UTC",
            "default": 0,
            "not_supported": "named zones, and any zone that observes daylight saving within a window",
        },
        "why": (
            "The rule is about local midnight, and a whole-hour offset expresses that for every zone "
            "that does not shift its midnight. A deployment needing a DST-observing zone can still get "
            "the behaviour right by declaring the offset that applies to the window in question, and "
            "the simplification is stated here rather than discovered when a tenant's window resets an "
            "hour early."
        ),
        "change_it": "offset_for and window_start in dsr/crm_backfill/quota.py.",
        "blast_radius": "Every window boundary, and the resets_at on every quota report.",
    },
    {
        "id": "standard-objects-are-declared-per-connection",
        "topic": "how the room knows which object names HubSpot will accept",
        "basis": (
            "'For standard objects, you can use the object's name (e.g., CONTACT), but for custom "
            "objects, you must use the objectTypeId value.' The research names one standard object and "
            "publishes no list."
        ),
        "value": {
            "declared_by": "the connection's standard_objects",
            "when_undeclared": "any name is accepted, because the room has nothing to check against",
            "when_declared": "a name outside the list asks for an object_type_id instead",
        },
        "why": (
            "Which objects exist is a property of the account, not of this product, and a hard-coded "
            "list of HubSpot's standard objects would be a claim that goes stale the day HubSpot adds "
            "one. Declaring them per connection keeps the rule checkable without pretending to know "
            "the account. When a connection declares nothing the check declines rather than guessing, "
            "because a wrong refusal here would stop a backfill that would have worked."
        ),
        "change_it": "check_object_addressing in dsr/crm_backfill/vendors.py.",
        "blast_radius": "Which HubSpot starts are refused before the job is created.",
    },
    {
        "id": "scope-refusal-stops-the-job-not-the-run",
        "topic": "what a missing crm.export grant does to a run that has already been opened",
        "basis": (
            "'When using an OAuth access token to authenticate requests to the exports API, the user "
            "installing the app must be a Super Admin to grant the crm.export scope.' The research "
            "states the prerequisite and nothing about a partially configured connection."
        ),
        "value": {
            "preflight_refusals": "recorded on the run and returned in the response",
            "run_state": "created, not running",
            "job_created": False,
        },
        "why": (
            "The run record exists so the findings are somewhere to be read, and it is left in "
            "'created' rather than driven forward: an export started without the grant fails at the "
            "vendor after the fact, which is the state this rule exists to prevent. Refusing the "
            "whole start instead would lose the run's other decisions - the scope, the strategy, the "
            "quota verdict - and an operator would have to answer the wizard again to see them."
        ),
        "change_it": "preflight in dsr/crm_backfill/vendors.py; the findings branch in engine.start.",
        "blast_radius": "Every HubSpot start against a connection without the grant.",
    },
    {
        "id": "push-needs-a-third-method",
        "topic": "the researched interface is two methods, and the reverse direction needs three",
        "basis": (
            "The extensibility note says a third party 'can add a new vendor by implementing only "
            "'create job' and 'read page''. The data flow ends 'or push into CRM for a reverse "
            "backfill'."
        ),
        "value": {
            "pull": ["start", "read_page"],
            "push": ["start", "read_page", "submit_page"],
            "base_class_raises": "DirectionNotSupported, so a read-only adapter says so rather than silently doing nothing",
        },
        "why": (
            "The researched pair is exactly right for sourcing history, and it is complete for that "
            "direction. A push needs somewhere to put a page, and a method that does not exist cannot "
            "receive one. Admitting the third method is smaller than pretending a two-method vendor can "
            "receive, and an adapter that does not implement it inherits a refusal naming the method - "
            "so 'this vendor cannot receive a push' is a row in the catalog rather than a discovery at "
            "run time."
        ),
        "change_it": "VendorAdapter.submit_page in dsr/crm_backfill/vendors.py; _push_page in engine.py.",
        "blast_radius": "Which vendors can receive a reverse backfill, and the catalog's supports_push.",
    },
    {
        "id": "poll-is-routed-not-scheduled",
        "topic": "how the fixed-interval poller exists in a process with no background threads",
        "basis": (
            "'Dataverse's change-tracking token and Salesforce's job queue both require polling, so "
            "the room runs a poller on a fixed interval.' The research does not say what runs the "
            "interval."
        ),
        "value": {
            "mechanism": "a poll route a scheduler calls",
            "interval": "poll_interval_seconds per run, default 300",
            "before_due": "reported as not_due, and nothing is asked of the vendor",
            "force": "resume skips the interval, because a room that restarted has not been respecting it",
        },
        "why": (
            "A thread that fires on a timer inside a web process is not a poller, it is a source of "
            "double-writes the moment two workers exist. Making the poll a route means the scheduler "
            "decides, the interval is a number an operator can read, and a run that was not asked is "
            "not charged a vendor call against a limit the research documents as daily. The force flag "
            "exists because the researched recovery story is a room that restarted, and a room that "
            "restarted has not been respecting anybody's interval."
        ),
        "change_it": "poll and normalise_poll_interval, in engine.py and plan.py.",
        "blast_radius": "Every poll, and every vendor call this room makes.",
    },
    {
        "id": "range-bounds-are-half-open",
        "topic": "whether a range includes the row on its upper bound",
        "basis": "The flow says 'picks a date range'. It does not define the bounds.",
        "value": {
            "from": "inclusive",
            "to": "exclusive",
            "why": "two adjacent ranges neither skip nor double-count the row on their shared boundary",
        },
        "why": (
            "This workflow runs on a schedule, so consecutive ranges share a boundary by "
            "construction. A closed upper bound would write the boundary row twice across two runs, "
            "which the research's own promise rules out; an open lower bound would skip it entirely. "
            "The convention is also this product's existing one for windowed reads, so a reader does "
            "not have to learn it twice."
        ),
        "change_it": "normalise_scope and SimulatedHistory.scoped in dsr/crm_backfill/.",
        "blast_radius": "Every range, and every source that applies it.",
    },
    {
        "id": "full-history-has-a-floor",
        "topic": "how far back 'full history' reaches when no start is given",
        "basis": "The wizard's option is named. The research does not say what it covers.",
        "value": {
            "floor_days": plan.FULL_HISTORY_FLOOR_DAYS,
            "default": "ten years before now",
            "explicit_unbounded": "from: null is *not* how you ask for unbounded; omit the scope key",
        },
        "why": (
            "The call estimate is computed against a bounded range, and an unbounded one has no page "
            "count and therefore no quota verdict - so a plan built on it could not be checked against "
            "the limit the research says sizing matters for. Ten years is a floor rather than a ceiling: "
            "it is a bound the estimate can use, and a caller with older history says so with an "
            "explicit `from`."
        ),
        "change_it": "FULL_HISTORY_FLOOR_DAYS and normalise_scope in dsr/crm_backfill/plan.py.",
        "blast_radius": "The default range, and the quota estimate built on it.",
    },
    {
        "id": "vendors-ship-without-credentials",
        "topic": "what the adapters actually talk to",
        "basis": (
            "The research names three vendors and their mechanisms in detail. This product ships "
            "without any of their credentials, and says so nowhere."
        ),
        "value": {
            "adapters_read_from": "a HistorySource, supplied to the registry",
            "default_source": "SimulatedHistory, in memory",
            "swapping": "one constructor argument to default_registry",
        },
        "why": (
            "The same arrangement the CRM webhook feature uses for delivery. It has two useful "
            "consequences rather than one: the whole workflow is exercisable in a test without a "
            "network, and the demo database is reproducible rather than dependent on what a remote "
            "system answers today. The seam is the point, and it is where a real transport goes - one "
            "argument, in one place, rather than a socket call inside a business rule."
        ),
        "change_it": "default_registry and HistorySource in dsr/crm_backfill/vendors.py.",
        "blast_radius": "Nothing in the workflow's behaviour; it is where a transport plugs in.",
    },
    {
        "id": "hubspot-export-status-vocabulary",
        "topic": "the values a HubSpot export is reported in",
        "basis": "'read export status and the download URL'. The research names the endpoint, not the states.",
        "value": {"states": list(vendors.HUBSPOT_EXPORT_STATUSES)},
        "why": (
            "A room has to render something, and the only honest place to publish the values it "
            "renders is next to the adapter that produces them, so a client picks from data rather "
            "than from a list compiled into a page. They are this build's names, not the vendor's, and "
            "saying so is the difference between a documented boundary and an implied citation."
        ),
        "change_it": "HUBSPOT_EXPORT_STATUSES in dsr/crm_backfill/vendors.py.",
        "blast_radius": "The state on a HubSpot job, and the states the run is rendered in.",
    },
    {
        "id": "room-scope-is-a-filter",
        "topic": "what a room-scoped backfill route guarantees",
        "basis": "The research describes a room, and says nothing about access control.",
        "value": {
            "room_scoped_paths": "/rooms/{room_id}/...",
            "guarantee": "a filter over the room_id column, not an authorisation check",
        },
        "why": (
            "This product's rooms carry no ACL. A query that looked like it enforced one would be a "
            "claim the store cannot back up, so the scope is described as what it is."
        ),
        "change_it": "Every room-scoped route in backend/dsr/features/wf045_backfill_*.py.",
        "blast_radius": "Every room-scoped read and write.",
    },
    {
        "id": "the-cursor-store-is-per-room-and-connection",
        "topic": "what a stored cursor is keyed on",
        "basis": (
            "The research's record is '{vendor, connectionId, cursor, updatedAt}'. It does not say "
            "what a cursor belongs to when one room reads from two connections."
        ),
        "value": {
            "keyed_on": "room and connection",
            "one_live_cursor_per": "room/connection pair",
            "carries": "the researched four fields, plus kind and two counters",
        },
        "why": (
            "A cursor is a position inside one vendor's job on one account. Keying it on the vendor "
            "alone would make two rooms sharing a connection overwrite each other's resume point, and "
            "a cursor keyed on nothing would have no way to be found. The two counters ride alongside "
            "the researched four rather than inside them, so a third party reading the documented "
            "fields still finds exactly the four."
        ),
        "change_it": "cursors.build, and CURSORS_STORE in dsr/crm_backfill/engine.py.",
        "blast_radius": "Which run resumes from where.",
    },
    {
        "id": "a-cancelled-run-keeps-its-cursor",
        "topic": "what cancelling a backfill does to the resume point",
        "basis": "The research describes restarts. It does not describe cancellation at all.",
        "value": {
            "run": "cancelled, terminal",
            "rows_landed": "kept",
            "cursor": "kept, so the next backfill against the connection continues",
        },
        "why": (
            "The rows are real and are the reason the run existed; deleting them would throw away work "
            "the CRM has already been asked for. Keeping the cursor is the other half: a cancellation "
            "an operator performs to stop a run is nearly always followed by a corrected one against "
            "the same connection, and a cancellation that reset the resume point would make that "
            "corrected run re-read everything before it."
        ),
        "change_it": "cancel in dsr/crm_backfill/engine.py.",
        "blast_radius": "What the run after a cancellation reads.",
    },
    {
        "id": "a-page-is-one-transaction-and-one-audit-row",
        "topic": "how many audit rows a page of rows produces",
        "basis": (
            "The research says the room 'writes rows in pages' and says nothing about the audit "
            "volume that implies. The store's own bulk_create already argues the case for a workflow "
            "that imports in bulk, and this is the largest such thing in the product."
        ),
        "value": {
            "page_of_new_rows": "one transaction, one audit row",
            "page_of_changed_rows": "one audited write per changed row",
            "page_of_unchanged_rows": "no write at all (see unchanged-rows-are-not-written)",
            "limit": "a page, not a whole run: a five-thousand-record run is one audit row, a "
            "million-record run is two hundred",
        },
        "why": (
            "A log with five thousand identical-shaped entries in it says nothing a log with one does "
            "not, and the audit log is the thing the whole product guarantee is built on - a guarantee "
            "that degrades into noise is not a guarantee. A changed row is a fact about the row and is "
            "audited per row; an unchanged page is a fact about nothing and is not written at all. "
            "Stating the limit matters as much as the rule: an implementation that batched several "
            "pages into one transaction would produce a cheaper log and a coarser one, and this entry "
            "is where that trade would have to be argued."
        ),
        "change_it": "The bulk_create call and the update loop in _land in dsr/crm_backfill/engine.py.",
        "blast_radius": "The audit volume of a backfill, and how readable it stays.",
    },
    {
        "id": "a-page-looks-up-its-rows-in-one-scan",
        "topic": "how the page decides which of its rows already exist",
        "basis": (
            "Nothing in the research: this is an implementation consequence of the store it runs on. "
            "Recorded because it was a measured 58x slowdown, not because it is interesting."
        ),
        "value": {
            "per_row_find": "about 77ms a row, because AuditedDatabase.find resolves a condition as a "
            "correlated EXISTS over record_index and there is no index on (path, value_text)",
            "one_scan_per_page": "one ordered list() walk per thousand rows, stopping when every key "
            "is found",
            "measured": "the seed went from 190s to 3.3s for the same 2,610 rows",
        },
        "why": (
            "The upsert needs the stored row for each key in the page, and the obvious way to get it "
            "is one find per key, which is what the first version did. It is a trap because it is "
            "correct, readable, and quadratic in the size of the room's replica. The fix is in this "
            "feature rather than in the store because the store is shared: the index that would make "
            "find fast is a change to a file every other feature depends on, and a per-page scan is "
            "within this feature's own reach. backend/tests/test_wf045_perf.py holds the query count, "
            "because a fix nobody can see has been applied is a fix somebody will undo."
        ),
        "change_it": "_replica_index in dsr/crm_backfill/engine.py; the bound in backend/tests/test_wf045_perf.py.",
        "blast_radius": "Every page cycle, and every room whose replica is large.",
    },
)


def by_id(inference_id: str) -> dict[str, Any] | None:
    for entry in INFERENCES:
        if entry["id"] == inference_id:
            return dict(entry)
    return None


def describe() -> dict[str, Any]:
    """The whole registry, alongside the half of the workflow that is sourced.

    Both halves in one payload on purpose. The point of this endpoint is that a
    reader can see where the line falls, and showing the researched numbers and
    rules next to the inferred behaviour is what lets them.
    """
    return {
        "count": len(INFERENCES),
        "sourced_quote": SOURCED_QUOTE,
        "sourced": {
            "numbers": {name: entry["value"] for name, entry in NUMBERS.items()},
            "bulk_threshold_quote": NUMBERS["bulk_threshold_records"]["quote"],
            "page_size_quote": NUMBERS["dataverse_page_size"]["quote"],
            "change_tracking_quote": NUMBERS["change_tracking_expiry_days"]["quote"],
            "required_scope": dict(REQUIRED_SCOPE),
            "object_addressing_quote": OBJECT_ADDRESSING_QUOTE,
            "cursor_shape": list(_CURSOR_SHAPE),
            "interface": "a third party can add a new vendor by implementing only 'create job' and 'read page'",
        },
        "inferences": [dict(entry) for entry in INFERENCES],
    }


#: The research's own four field names, quoted so the endpoint can show them
#: without importing the module that mints them.
_CURSOR_SHAPE: tuple[str, ...] = ("vendor", "connectionId", "cursor", "updatedAt")
