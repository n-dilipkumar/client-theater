"""Every inference in this package, in one inspectable place.

The research for WF-038 is unusually specific, and it also names its own limits.
That combination is why this module is short and why each entry is narrow: most
of what this package does is *quoted*, and the entries below are the parts that
are not.

What the research fixes, and this package therefore does not have to guess
----------------------------------------------------------------------

* The per-request caps: "The list can contain up to 200 objects" and "Batch
  operations are limited to 100 records at a time".
* The request shapes, including "Each object in the request body must contain an
  attributes map. The map must contain a value for ``type``", "**no ``id``
  field**, external-ID field only", HubSpot's ``idProperty``, and Dataverse's
  ``Targets`` with ``@odata.type`` and ``@odata.id``.
* The ordering guarantee: "Objects are created or updated in the order they're
  listed in the request body. The ``UpsertResult`` objects are returned in the
  same order."
* ``allOrNone``, and ``updateOnly`` as the way to prevent a create.
* The 300 for a duplicate external key, and that no records are created or
  updated.
* That Dataverse's ``UpsertMultiple`` "returns ``204 NoContent``".
* The fallback: "it auto-falls back from ``UpsertMultiple`` to per-row ``PATCH``
  for tables that don't support bulk upsert".

What follows is the remainder. Each entry is **named**, so it can be argued with
by name; **traceable**, because ``basis`` says what the research does and does
not say; **bounded**, because ``value`` is what this build chose and
``change_it`` says how to change it without editing a function body; and
**visible**, because :func:`describe` is served at ``/api/wf-038/inferences``.

Nothing here is a migration, a typed column, or a new required field. It is a
list of ordinary JSON, and it is a *record* of a judgement rather than a
mechanism that enforces one.
"""

from __future__ import annotations

from typing import Any

#: The researched sentence that makes the "no real HTTP client" choice honest.
#: It is quoted because it is the reason a row sent to Dataverse can be recorded
#: as ``submitted`` and never as ``synced``.
DATAVAULT_204_QUOTE = "The `UpsertMultiple` action returns `204 NoContent`."

INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "unconfirmed-is-a-state",
        "topic": "what a row means when the CRM confirms nothing",
        "basis": (
            'The research quotes Dataverse: "The `UpsertMultiple` action returns '
            '`204 NoContent`", and the data flow says the room updates `synced_at` / '
            "`crm_record_id` per row. A 204 with no body therefore carries no per-item "
            "success flag, and the research says nothing about how to record a row it "
            "cannot confirm."
        ),
        "value": {
            "outcome": "submitted",
            "sync_status": "unconfirmed",
            "sets_sent_at": True,
            "sets_synced_at": False,
            "sets_crm_record_id": False,
            "leaves_the_queue": True,
        },
        "why": (
            "The three available answers are all wrong. Calling it synced writes a "
            "confirmation the connector never received. Calling it failed re-sends it on "
            "every run forever, because a Dataverse upsert that worked will look exactly "
            "the same next time. A third state, which leaves the queue and is counted "
            "separately, is the only one that neither lies nor loops."
        ),
        "change_it": (
            "The branch is dsr/crm_upsert/runs.py::_write_back and "
            "dsr/crm_upsert/connections.py::QUEUE_STATUSES. A capability whose "
            "returns_per_item_results is True never reaches this state."
        ),
        "blast_radius": (
            "Every Dataverse connection. The queue view counts unconfirmed rows "
            "separately from synced ones so a rep can see they need checking in the CRM."
        ),
    },
    {
        "id": "positional-result-matching",
        "topic": "how a per-item result is tied back to a room row",
        "basis": (
            'The research states the guarantee twice: "Objects are created or updated in '
            "the order they're listed in the request body. The `UpsertResult` objects are "
            'returned in the same order." It does not say what to do when a response '
            "arrives with the wrong number of results."
        ),
        "value": {
            "match_by": "position",
            "length_mismatch": "fail the whole chunk",
            "never": "truncate with zip, or match on the external key",
        },
        "why": (
            "Matching on the key would look correct and would misattribute every outcome "
            "the first time the same external key appears twice in one batch, which is "
            "exactly what re-running a queue does. Silently truncating a longer response "
            "would leave the tail pending and the queue would never drain while the run "
            "reported success."
        ),
        "change_it": (
            "dsr/crm_upsert/runs.py::interpret_items. The mismatch text is RESULT_COUNT_MISMATCH."
        ),
        "blast_radius": "Every run that sends a chunk.",
    },
    {
        "id": "dataverse-batch-size",
        "topic": "how many rows one Dataverse UpsertMultiple request may carry",
        "basis": (
            'The research quotes the caps for Salesforce ("up to 200 objects") and '
            'HubSpot ("Batch operations are limited to 100 records at a time") and is '
            "silent about Dataverse's. This build uses 1000."
        ),
        "value": {"max_batch_size": 1000},
        "why": (
            "Dataverse's documented default is 1000, which is a different document from "
            "the six this research cites. It is chosen because the field is required and "
            "an invented small number would throttle Dataverse for no sourced reason - "
            "but it is a judgement call and is labelled as one: the capability carries "
            "`sourced: False` for this figure and a stored capability record overrides it."
        ),
        "change_it": (
            "Register a capability record for dataverse, or edit DATAVAULT in "
            "dsr/crm_upsert/capabilities.py. The lint endpoint reports which source a run "
            "used as `capability_source`."
        ),
        "blast_radius": "Dataverse connections only. Salesforce and HubSpot use sourced caps.",
    },
    {
        "id": "hubspot-response-shape",
        "topic": "the envelope HubSpot's batch upsert answers with",
        "basis": (
            'The research quotes the endpoint and the parameter - "include the '
            '`idProperty` parameter" - and the data flow\'s "per-item `success` flag + '
            "`errors` array in the response\". It does not quote HubSpot's own response "
            "envelope, nor its per-item `new` flag."
        ),
        "value": {
            "body": {"status": "COMPLETE", "results": [{"id": "...", "new": True}]},
            "accepts_a_bare_success_flag": True,
        },
        "why": (
            "A response shape has to be chosen or the HubSpot path cannot be tested at "
            "all. The interpreter is written to accept either the envelope or a bare "
            "per-item `success` flag, so a response in the data flow's own words is "
            "understood without a change here."
        ),
        "change_it": (
            "dsr/crm_upsert/transport.py::hubspot_upsert_results and "
            "dsr/crm_upsert/runs.py::_extract_results, which also accepts `records` and "
            "`UpsertResult`."
        ),
        "blast_radius": "HubSpot connections, and any future vendor on the same envelope.",
    },
    {
        "id": "hubspot-single-row-path",
        "topic": "HubSpot's single-row upsert, used by the researched fallback",
        "basis": (
            'The fallback rule is sourced - "it auto-falls back from `UpsertMultiple` to '
            "per-row `PATCH` for tables that don't support bulk upsert\" - but the research "
            "documents only HubSpot's batch endpoint. The per-row path below is inferred "
            "from the same API version."
        ),
        "value": {"single_path": "/crm/v3/objects/{object}/{key_field}/{key_value}"},
        "why": (
            "The fallback has to land somewhere for a vendor that cannot take a batch, and "
            "refusing to guess would mean the researched fallback silently did not work for "
            "HubSpot. The guess is confined to a path template a connection can override, "
            "and the run log shows the exact path that was used."
        ),
        "change_it": (
            "The `single_path` on the hubspot capability in "
            "dsr/crm_upsert/capabilities.py, or a stored capability record."
        ),
        "blast_radius": "Only reached when a HubSpot table declares no bulk upsert.",
    },
    {
        "id": "opportunistic-threshold",
        "topic": 'the N in "when the queue exceeds N rows"',
        "basis": (
            'The rule is quoted - "the room may also upsert opportunistically when the '
            'queue exceeds N rows" - and N is not given.'
        ),
        "value": {"opportunistic_threshold": 25},
        "why": (
            "An eighth of the 200-row researched queue target: frequent enough that a busy "
            "room does not wait for the nightly run, rare enough that a trickle of activity "
            "does not send a request per row. Any other number is defensible; the rule it "
            "feeds is the sourced part."
        ),
        "change_it": "queue.opportunistic_threshold in the config, or POST /config.",
        "blast_radius": "The backlog decision only. A run triggered by hand ignores it.",
    },
    {
        "id": "queue-target-is-the-vendor-cap",
        "topic": 'what "up to 200 pending engagement rows" is a number of',
        "basis": (
            "\"The room's queue accumulates up to 200 pending engagement rows (or the "
            'nightly backlog)", and separately "The list can contain up to 200 objects" '
            "for a Salesforce request. The two 200s are in different sentences about "
            "different things."
        ),
        "value": {"queue.target": 200, "read_as": "the Salesforce collections cap"},
        "why": (
            "A queue that grows past its target does not fail: the run chunks at the "
            "connection's batch size and sends as many requests as it needs, which is the "
            "researched behaviour. The target is published so a rep can see the queue is "
            "over its mark, not used to truncate work."
        ),
        "change_it": "queue.target in the config. It never limits a run; only the advisory changes.",
        "blast_radius": "Advisory reporting in the queue view.",
    },
    {
        "id": "rejected-and-failed-rows-stay-queued",
        "topic": "what happens to a row this connector refuses, or that the CRM refuses",
        "basis": (
            'The research says "Per-row outcomes are written back to the room; failures '
            "appear in the sync log with the row's error text\", and it names no retry, no "
            "dead-letter queue, and no give-up rule."
        ),
        "value": {
            "rejected_status": "pending",
            "failed_status": "failed",
            "both_stay_in_the_queue": True,
            "reason_stored_on_the_row": True,
        },
        "why": (
            "A row refused here - no key value, a record-id key, a property set HubSpot will "
            "not accept partially - will be refused identically next run, so a dead-letter "
            "queue would be a fourth state the research never asked for. A row the *CRM* "
            "refused is different: the operator can fix that in the CRM, and the next run "
            "is the mechanism. So the two are kept apart - `pending` for a row this "
            "connector will not send, `failed` for one the CRM turned down - and the queue "
            "selects both, because neither has landed. Conflating them either way is a bug: "
            "renaming both to `pending` loses the fact that one had already been tried, and "
            "dropping `failed` from the queue drops the row permanently. Delivery retries "
            "with backoff are WF-016's subject, not this workflow's."
        ),
        "change_it": (
            "dsr/crm_upsert/runs.py::_status_for for the status a row gets, and "
            "dsr/crm_upsert/connections.py::UNSENT_STATUSES for which of them the queue "
            "picks up. The refusal reasons are REJECTION_REASONS in "
            "dsr/crm_upsert/payloads.py."
        ),
        "blast_radius": "Any connection whose rows are not all sendable or all accepted.",
    },
    {
        "id": "no-network-transport-ships",
        "topic": "why there is no HTTP client in this package",
        "basis": (
            "The research names the four endpoints precisely, and this build implements "
            "every one of them as a request object. It cites no credentials, no "
            "authentication scheme, and no base URL for any of the four."
        ),
        "value": {
            "ships": [
                "OutboundRequest",
                "OutboundResponse",
                "Transport protocol",
                "ScriptedTransport",
            ],
            "does_not_ship": "an HTTP client",
        },
        "why": (
            "An unexercisable network path is a liability: no test could run it, the "
            "seeder must never open a socket, and a rule that only lives behind a socket "
            "is a rule nobody tests. The researched content is observable through "
            "`GET /preview`, which returns the exact request a run would send, and through "
            "the sync log, which stores it."
        ),
        "change_it": (
            "Implement Transport.send in your own module and pass it to run_upsert. No "
            "other file in this package changes."
        ),
        "blast_radius": "None of the researched rules. A live deployment adds a transport.",
    },
    {
        "id": "mixed-object-types-refused",
        "topic": "what a chunk carrying two object types should do",
        "basis": (
            '"The list can contain objects only of the type indicated in the request '
            'URI", and the data flow says "single object type". Whether to split the '
            "chunk or refuse the run is not stated."
        ),
        "value": {"action": "refuse the run", "split": False},
        "why": (
            "A connection is bound to one object, so rows asking for a different one are a "
            "configuration mistake rather than a routing problem. Splitting would silently "
            "relabel them into the connection's object, which is how an engagement row ends "
            "up written to the wrong CRM table."
        ),
        "change_it": "dsr/crm_upsert/payloads.py::_assert_single_type.",
        "blast_radius": "Only a connection whose rows disagree with its own object.",
    },
)


def describe() -> dict[str, Any]:
    """Every inference, as served at ``/api/wf-038/inferences``."""
    return {
        "count": len(INFERENCES),
        "inferences": [dict(entry) for entry in INFERENCES],
    }
