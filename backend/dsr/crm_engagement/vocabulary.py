"""The researched contract, as data.

Everything in this module is something the research for WF-037 states. Nothing here is
a preference of this build: where a judgement call had to be made, it went to
:mod:`dsr.crm_engagement.inferences` instead, and this file is the half a reviewer can
check against the source.

The three vendors, and the one researched difference between them that matters most
--------------------------------------------------------------------------------------

The research quotes Dataverse and Salesforce ``POST`` semantics and quotes no status
code for HubSpot at all. That is not a gap in the writing, it is the shape of the
finding, and it produces three genuinely different create policies:

* **Dataverse** - "When you apply this preference to a POST request, a successful
  response has status 201 Created. … Without this preference, both operations return
  status 204 No Content." So a Dataverse create *succeeds with no body at all*, and the
  id arrives in the ``OData-EntityId`` response header. The id is not in the JSON; a
  parser that only reads bodies finds nothing on the most common Dataverse success.
* **Salesforce** - "``201`` - 'Created' success code, for POST requests" and "``204`` -
  'No Content' success code, for DELETE requests and some PATCH requests." So 201 is the
  create success and 204 is explicitly *not* one for a create, even though 204 is a
  success code in general. Reading "2xx means done" would be wrong in a way the status
  reference itself forbids.
* **HubSpot** - the research quotes the endpoint ("To create one contact, make a ``POST``
  request to ``/crm/v3/objects/contacts``") and the data flow names the id ("response
  returns the new record id (HubSpot ``id``)") but no status code. So the success set is
  the whole 2xx range and the id comes from the body.

The same quote is why a create that *succeeds* can still leave this workflow unable to
finish its own step 5. The research says that on success the worker "writes the returned
CRM record id into its local row (``crm_record_id``) and marks the event as synced". If
the response carried no id anywhere, that step cannot complete, and calling the event
synced would be a claim the row cannot support. See ``crm_id_absent`` in
:mod:`dsr.crm_engagement.queue`.
"""

from __future__ import annotations

from typing import Any

#: The five sentences the rest of this module is measured against, quoted from
#: ``docs/research/digital-sales-room-workflows/wf/WF-037.md`` verbatim. Served beside
#: the inferred half at ``GET /api/wf-037/inferences`` so a reader can check one against
#: the other without opening anything else.
SOURCED_QUOTES: dict[str, str] = {
    "dataverse_create": (
        "Send a `POST` request to the Web API entityset resource to create a table row "
        "(entity record) in Microsoft Dataverse."
    ),
    "dataverse_created_uri": (
        "`HTTP/1.1 204 No Content` … `OData-EntityId: "
        "[Organization URI]/api/data/v9.2/accounts(00aa00aa-…)`"
    ),
    "dataverse_entity_set": (
        "In Power Apps, when viewing a list of tables, select Advanced > Tools. Select "
        "**Copy set name** to copy the entity set name for the table. You can also select "
        "**API link to table data** to view the top 10 rows of data in your browser."
    ),
    "return_representation": (
        "`return=representation` — Use this preference to return data on create (POST) or "
        "update (PATCH) operations for entities. When you apply this preference to a POST "
        "request, a successful response has status 201 Created. … Without this "
        "preference, both operations return status 204 No Content."
    ),
    "salesforce_201": "`201` — 'Created' success code, for POST requests and some PATCH requests.",
    "salesforce_204": (
        "`204` — 'No Content' success code, for DELETE requests and some PATCH requests."
    ),
    "hubspot_contacts": "To create one contact, make a `POST` request to `/crm/v3/objects/contacts`.",
    "queue_worker": (
        "Fully user-action-triggered (a room event), but the *write* is asynchronous: the "
        "room's queue worker fires it without further user input, with retry on failure."
    ),
    "extensibility": (
        "New event types are rows in the room's event catalogue mapped by the field map, "
        "so adding \"download\", \"pricing-view\", \"cta-click\" needs a mapping row, not a "
        "code path. Optional `Prefer: return=representation` / `respond-async` style "
        "preferences let a connector opt into returning created data."
    ),
    "success_step": (
        "On success the worker writes the returned CRM record id into its local row "
        "(`crm_record_id`) and marks the event as synced; on failure it retries with "
        "backoff and surfaces the failure in the admin **Sync log** panel."
    ),
    "identity_source": "buyer identity (CRM record id or email from W1/W2)",
}

#: The vendors this build can write a create to. A fourth vendor is a row of
#: configuration, not a code path, once this build has read its documented create
#: semantics - see the ``unmapped_vendor`` inference.
VENDORS: tuple[str, ...] = ("hubspot", "dataverse", "salesforce")

#: The researched create surface for each vendor.
#:
#: ``path_template`` is relative to the connector's ``base_url`` and its ``{...}``
#: placeholders are filled from the connector. ``body_style`` is the researched payload
#: shape: the data flow names "CRM create payload (`properties` object, or SOQL-shaped
#: field body)", and which vendor gets which is settled by the two documented request
#: models rather than chosen here.
CREATE_ENDPOINTS: dict[str, dict[str, Any]] = {
    "hubspot": {
        "label": "HubSpot",
        "path_template": "/crm/v3/objects/{object}",
        "body_style": "properties_object",
        "body_example": {
            "properties": {
                "email": "procurement@northwind.example",
                "dsr_engagement_id": "crm_engagement_a1b2c3",
            }
        },
        "object_field": "object",
        "object_label": "object type",
        "object_example": "contacts",
        "sourced_success": False,
        "success_codes": list(range(200, 300)),
        "success_basis": (
            "The research quotes the endpoint and the id location but no status code for "
            "HubSpot, so any 2xx is a success. Stated rather than guessed: a reviewer who "
            "has the API reference can narrow this list without reading any code."
        ),
        "sources": ["https://developers.hubspot.com/docs/api-reference/legacy/crm/objects/contacts"],
    },
    "dataverse": {
        "label": "Dataverse",
        "path_template": "/api/data/v9.2/{entity_set}",
        "body_style": "field_body",
        "body_example": {
            "dsr_engagement_id": "crm_engagement_a1b2c3",
            "buyeremail": "procurement@northwind.example",
            "engagement@odata.type": "#Microsoft.Dynamics.CRM.dsr_engagement",
        },
        "object_field": "entity_set",
        "object_label": "entity set",
        "object_example": "accounts",
        "object_note": (
            "The entity *set* name, which is what 'Copy set name' copies - not the table's "
            "display name, and not a path."
        ),
        "sourced_success": True,
        "success_codes": [201, 204],
        "success_basis": (
            "`return=representation` gives 201 Created; without it a create returns 204 No "
            "Content. Both are quoted, and the set is exactly those two: the quote states "
            "what a Dataverse create returns, and inventing a wider set would make a 200 "
            "look like a create that never happened."
        ),
        "sources": [
            "https://learn.microsoft.com/en-us/power-apps/developer/data-platform/webapi/create-entity-web-api?view=dataverse-latest"
        ],
    },
    "salesforce": {
        "label": "Salesforce",
        "path_template": "/services/data/vXX.X/sobjects/{object}",
        "body_style": "field_body",
        "body_example": {
            "Name": "Northwind - viewed Enterprise Overview Deck",
            "DSR_Engagement_Id__c": "crm_engagement_a1b2c3",
        },
        "object_field": "object",
        "object_label": "sObject name",
        "object_example": "Room_Engagement__c",
        "sourced_success": True,
        "success_codes": [201],
        "success_basis": (
            "201 is quoted as the Created success code 'for POST requests'. 204 is quoted "
            "as a success code for DELETE and some PATCH, so it is deliberately excluded "
            "here: on this vendor's own reference a 204 to a create is not a create that "
            "succeeded."
        ),
        "sources": ["https://developer.salesforce.com/docs/platform/api-rest/guide/errorcodes.html"],
    },
}

#: Where each vendor returns the id of the row it just created, in the order this build
#: looks. ``header`` entries name a response header; ``body`` entries name a dotted JSON
#: path in the response body. ``sourced`` says whether the research states this location;
#: where it does not, the entry is still usable but the Sync log says so.
RECORD_ID_LOCATIONS: dict[str, tuple[dict[str, Any], ...]] = {
    "hubspot": (
        {
            "where": "body",
            "path": "id",
            "sourced": True,
            "basis": "The data flow: 'response returns the new record id (HubSpot `id`)'.",
        },
    ),
    "dataverse": (
        {
            "where": "header",
            "name": "OData-EntityId",
            "sourced": True,
            "basis": (
                "The quoted create response is '`HTTP/1.1 204 No Content` … "
                "`OData-EntityId: [Organization URI]/api/data/v9.2/accounts(00aa00aa-…)`'. "
                "The id is inside the parentheses of the returned entity URI, not in the "
                "body - which on a 204 there is not."
            ),
        },
        {
            "where": "body",
            "path": "accountid",
            "sourced": False,
            "basis": (
                "Only reachable on the 201 that `return=representation` produces, where the "
                "quote says the response returns data. The research does not name the "
                "field, so this is a documented guess and the Sync log labels it as one."
            ),
        },
    ),
    "salesforce": (
        {
            "where": "body",
            "path": "id",
            "sourced": False,
            "basis": (
                "Not sourced. The research's own gap note says the Salesforce claim 'rests "
                "on the status-code reference plus the general resource model, not a "
                "`sobjects` create page', and its data flow names an id location for "
                "HubSpot and Dataverse only. So this vendor's id is read from the body "
                "when the body carries one and reported absent when it does not."
            ),
        },
    ),
}

#: The `Prefer`-style preferences the research names, and what each one is documented to
#: do. A connector may configure any other token as well: this is an open list, and the
#: two named here are the two this build can explain.
PREFERENCES: dict[str, dict[str, str]] = {
    "return=representation": {
        "vendors": ["dataverse"],
        "effect": (
            "Return the created row in the response body. Changes a successful create "
            "from 204 No Content to 201 Created."
        ),
        "sourced": "True",
        "source": "return_representation",
    },
    "odata.include-annotations": {
        "vendors": ["dataverse"],
        "effect": (
            "Ask for enriched error detail. On a failure the response body carries the "
            "annotations, which the Sync log keeps so a rep sees the vendor's own "
            "explanation rather than only a status code."
        ),
        "sourced": "True",
        "source": "features_tools",
    },
    "respond-async": {
        "vendors": ["salesforce"],
        "effect": (
            "Named by the research as an opt-in that lets a connector return created data. "
            "This build passes the token and records it, and has no completion poll, so a "
            "connector that sets it is understood as sending the header without waiting on "
            "a job."
        ),
        "sourced": "True",
        "source": "extensibility",
    },
}

#: The states a queue row ends in. Every row ends in exactly one of them, and none of
#: them is a silent drop: a row this build cannot send is ``blocked`` with a reason, not
#: absent.
QUEUE_STATES: tuple[str, ...] = ("pending", "synced", "failed", "blocked")

#: Why a row is ``blocked``. Named, because a rule that cannot fall through is a bug
#: someone hits in production, and "it did nothing" with no reason is that bug.
BLOCK_REASONS: dict[str, str] = {
    "no_connector": "No CRM connector is registered for this room.",
    "connector_disabled": "The only connector for this room is switched off.",
    "event_type_unmapped": (
        "No field map covers this event type. Adding one is a row, not a code path - see "
        "the extensibility note in the research."
    ),
    "field_map_empty": "The field map for this event type declares no fields.",
    "ambiguous_connector": (
        "More than one connector is switched on and no field map says which one this event "
        "type belongs to, so the create has no single target."
    ),
    "sync_key_unresolved": (
        "The sync key could not be built from this event, and without it the CRM cannot "
        "reject a duplicate or the row be updated later."
    ),
    "connector_unaddressable": (
        "The connector names a vendor this build cannot shape a create for, or has no "
        "target object to create the row on."
    ),
    "buyer_unresolved": (
        "Neither the buyer's CRM record id nor an email resolved, so step 3 of the "
        "researched flow - resolving the buyer's CRM record - produced nothing to create "
        "against."
    ),
    "event_missing": "The engagement row this queue row points at is gone.",
}

#: Why a write failed. ``crm_id_absent`` is the researched one worth reading twice: a
#: create that the vendor *accepted* and that this build still cannot call synced, because
#: the researched step 5 requires an id to write and none arrived.
FAILURE_REASONS: dict[str, str] = {
    "crm_id_absent": (
        "The CRM accepted the create but returned no record id in any researched location, "
        "so the row cannot be marked synced and cannot be updated later."
    ),
    "transport_error": "The request never completed: the host did not answer.",
    "sync_key_collision": (
        "The CRM rejected the create because the sync key is already there. The uniqueness "
        "is working - it is what stops this write happening twice - and a create workflow "
        "has no answer to it, because turning it into an update is a different one."
    ),
    "crm_refused": "The CRM answered with a status outside its documented create-success codes.",
    "unmapped_status": (
        "The response was not one of this vendor's documented create-success codes, and "
        "did not carry a record id either."
    ),
}

#: How each reason should read. Shipped beside the reason rather than compiled into a page,
#: so a reason this build adds later arrives with its own presentation instead of falling
#: back to whatever the client guessed. The four values are the tones the feature's own
#: ``Notice`` primitive accepts; anything else is read as informational.
BLOCK_TONES: dict[str, str] = {
    "no_connector": "warn",
    "connector_disabled": "warn",
    "event_type_unmapped": "warn",
    "field_map_empty": "warn",
    "ambiguous_connector": "warn",
    "sync_key_unresolved": "warn",
    "connector_unaddressable": "warn",
    "buyer_unresolved": "warn",
    "event_missing": "danger",
    "crm_id_absent": "danger",
    "sync_key_collision": "danger",
    "crm_refused": "danger",
    "transport_error": "warn",
    "unmapped_status": "warn",
}

#: Statuses worth trying again. The research says only that the worker "retries with
#: backoff"; it names no codes. See the ``retry_ladder`` inference.
RETRYABLE_STATUS: frozenset[int] = frozenset({408, 425, 429, 500, 502, 503, 504})

#: The transforms the research names, as "named, versioned functions registered in the
#: connector". ``identity`` and ``number`` are the fourth and fifth: the same document
#: lists "number coercion" among the transforms an admin picks, and a create with no
#: transform at all is the common case.
TRANSFORM_NAMES: tuple[str, ...] = (
    "identity",
    "email.normalize",
    "date.iso8601",
    "picklist.map",
    "number",
)

#: The fields the research's data flow names for a sales-room event: "sales-room event
#: (type, timestamp, asset, dwell time, buyer identity)". These are the canonical source
#: names a field map may use; anything else is a dotted path into the event's own JSON,
#: which is what schema flexibility means in practice.
CANONICAL_FIELDS: dict[str, str] = {
    "type": "The event type - an open, a download, a CTA answer.",
    "occurred_at": "The event timestamp.",
    "asset": "The room asset the buyer acted on: a document, a pricing page, a deck.",
    "dwell_seconds": "Dwell time on the asset, in seconds.",
    "buyer_email": "The buyer's email, as carried by the room's own event.",
    "buyer_crm_id": (
        "The buyer's CRM record id, when W1 or W2 already established one. The research's "
        "data source is 'buyer identity (CRM record id or email from W1/W2)'."
    ),
}

#: Alternative spellings for the canonical fields, so a team whose engagement rows were
#: written by a webhook can map them without editing a field map. A source is resolved
#: by its exact dotted path first, then through this list, and an unresolved source is
#: flagged rather than sent as null.
FIELD_SYNONYMS: dict[str, tuple[str, ...]] = {
    "type": ("event", "event_type", "action", "activity", "name"),
    "occurred_at": ("timestamp", "at", "occurred", "happened_at", "time", "created", "created_at"),
    "asset": ("target", "asset_name", "document", "page", "item", "content"),
    # Unit variants are deliberately absent. "dwell_ms" is not another spelling of
    # "dwell_seconds", it is a different number, and resolving one from the other would
    # send a value off by a factor of a thousand with nothing in the row to say so.
    "dwell_seconds": ("dwell", "dwell_time", "seconds_on_asset", "time_on_asset"),
    "buyer_email": ("email", "person", "viewer_email", "contact_email", "user_email"),
    # ``crm_record_id`` is deliberately NOT a synonym here. The research gives that name
    # to this event's own CRM row - "writes the returned CRM record id into its local row
    # (`crm_record_id`)" - so treating it as another spelling of the buyer's id would let a
    # field map send the engagement row's own id as the buyer's CRM record.
    "buyer_crm_id": ("buyer_contact_id", "crm_contact_id", "contact_id", "buyer_id", "external_id"),
}

#: Direction values a field map row may declare. The research's W2 step 3 names them:
#: "its direction (in / out / both)".
DIRECTIONS: tuple[str, ...] = ("in", "out", "both")

#: The researched surfaces this workflow shows a rep: the room's engagement feed and the
#: Sync log. Named here so the page and the API agree on the words.
FEATURE_SURFACES: tuple[str, ...] = ("engagement_feed", "sync_log")

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF = 0.5
DEFAULT_TIMEOUT = 10.0
BODY_SAMPLE = 2048
USER_AGENT = "digital-sales-room-engagement-sync/0.1"
REDACTED = "Bearer ***redacted***"


def describe() -> dict[str, Any]:
    """The whole researched contract, as one payload.

    Served at ``GET /api/wf-037/vocabulary`` so a client renders its pickers from the
    same source the writer enforces against, and so a reviewer can read the researched
    facts without opening a Python file.
    """
    return {
        "vendors": list(VENDORS),
        "create_endpoints": {vendor: dict(spec) for vendor, spec in CREATE_ENDPOINTS.items()},
        "record_id_locations": {
            vendor: [dict(entry) for entry in entries] for vendor, entries in RECORD_ID_LOCATIONS.items()
        },
        "preferences": {name: dict(spec) for name, spec in PREFERENCES.items()},
        "queue_states": list(QUEUE_STATES),
        "block_reasons": dict(BLOCK_REASONS),
        "block_tones": dict(BLOCK_TONES),
        "failure_reasons": dict(FAILURE_REASONS),
        "failure_tones": {
            reason: BLOCK_TONES.get(reason, "info") for reason in FAILURE_REASONS
        },
        "retryable_status": sorted(RETRYABLE_STATUS),
        "transforms": list(TRANSFORM_NAMES),
        "canonical_fields": dict(CANONICAL_FIELDS),
        "field_synonyms": {key: list(values) for key, values in FIELD_SYNONYMS.items()},
        "directions": list(DIRECTIONS),
        "surfaces": list(FEATURE_SURFACES),
        "sourced_quotes": dict(SOURCED_QUOTES),
        "defaults": {
            "max_attempts": DEFAULT_MAX_ATTEMPTS,
            "backoff_seconds": DEFAULT_BACKOFF,
            "timeout_seconds": DEFAULT_TIMEOUT,
        },
    }
