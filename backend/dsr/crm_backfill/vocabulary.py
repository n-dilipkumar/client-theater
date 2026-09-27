"""The vocabulary the backfill research fixes by name, served as data.

Served at ``GET /api/wf-045/vocabulary`` so a client renders its pickers from
the same source the validator enforces against. A value added in one place
reaches every client at once, and a reviewer can read what this workflow
believes is true without reading a function body.

Each constant carries the sentence from
``docs/research/digital-sales-room-workflows/wf/WF-045.md`` that fixes it. The
quote is not decoration: it is what makes the constant arguable, and the
inference register in :mod:`dsr.crm_backfill.inferences` names the values that
*no* sentence fixes.
"""

from __future__ import annotations

from typing import Any

#: The three vendors the research names, and what each of them is read through.
#:
#: Each entry is what the research states, not a claim about the vendor's whole
#: product. Salesforce is quoted for Bulk API 2.0, Dataverse for change
#: tracking and ``RetrieveEntityChanges``, HubSpot for the exports API and the
#: account-information quota endpoint.
VENDORS: dict[str, dict[str, str]] = {
    "salesforce": {
        "label": "Salesforce",
        "mechanism": "Bulk API 2.0",
        "cursor_kind": "bulk_job_id",
        "quote": (
            "Both Salesforce Bulk APIs are based on REST principles and are optimized for working "
            "with large sets of data. Use them to insert, update, upsert, or delete many records "
            "asynchronously. You submit a request and come back for the results later. Salesforce "
            "processes the request in the background."
        ),
    },
    "dataverse": {
        "label": "Dataverse",
        "mechanism": "RetrieveEntityChanges / odata.track-changes",
        "cursor_kind": "data_token",
        "quote": (
            "The first time you use this message, it returns all records for the table. ... The "
            "message also returns a version number that you send back with the next use of the "
            "RetrieveEntityChanges message so that only data for those changes that occurred "
            "since that version is returned."
        ),
    },
    "hubspot": {
        "label": "HubSpot",
        "mechanism": "POST /crm/exports/2026-09/export/async",
        "cursor_kind": "export_id",
        "quote": (
            "To start an export, make a POST request to /crm/exports/2026-09/export/async. Your "
            "request body should specify information such as the file format, the object and "
            "properties you want to exported, and the type of export you're completing (e.g., "
            "exporting an object view or a list)."
        ),
    },
}

#: The two ways the research says a room asks a CRM for history.
#:
#: "Room asks the CRM for an asynchronous extract job (large volumes) or a
#: delta/paged read (moderate volumes)." ``async_job`` is the first half,
#: ``delta_read`` and ``paged_read`` are the two readings of the second half,
#: split because Dataverse's delta token and a paged scan resume differently and
#: a caller that has to choose between them is choosing how expiry works.
STRATEGIES: tuple[str, ...] = ("async_job", "delta_read", "paged_read")

STRATEGY_MEANING: dict[str, str] = {
    "async_job": (
        "Submit a job and come back for it. Salesforce Bulk API 2.0 is the researched case; "
        "HubSpot's async export is the same shape with a file at the end."
    ),
    "delta_read": (
        "Read what changed since a token the vendor issued. Dataverse's DataToken is the "
        "researched case, and it is the only strategy whose cursor the vendor will expire."
    ),
    "paged_read": (
        "Page through the history in order, carrying a page cookie. The researched fallback for "
        "volumes under the bulk threshold."
    ),
}

#: Where the pages end up.
DIRECTIONS: tuple[str, ...] = ("pull", "push")

DIRECTION_MEANING: dict[str, str] = {
    "pull": "CRM history into this room's replica. The default, and the flow the research narrates.",
    "push": (
        "This room's replica into the CRM - the researched data flow's 'or push into CRM for a "
        "reverse backfill'. The page cycle is the same; the source and the sink are swapped."
    ),
}

#: The two ways an admin says how much history they want.
SCOPES: tuple[str, ...] = ("range", "full_history")

#: Run states. ``stalled`` is the one the research forces into existence: a
#: Dataverse token past ``ExpireChangeTrackingInDays`` cannot be resumed and is
#: not a failure of the run, it is a run that cannot move.
RUN_STATES: tuple[str, ...] = (
    "created",
    "running",
    "complete",
    "failed",
    "cancelled",
    "stalled",
)

#: A run in one of these is finished; a run in one of the others is not.
TERMINAL_STATES: frozenset[str] = frozenset({"complete", "failed", "cancelled", "stalled"})

#: Per-run log event kinds. Every state change and every cursor move writes one,
#: so "a per-run log" (the research's step 6) is a queryable collection rather
#: than a field somebody has to remember to update.
LOG_EVENTS: tuple[str, ...] = (
    "run_created",
    "connection_checked",
    "strategy_selected",
    "strategy_reconsidered",
    "cursor_adopted",
    "cursor_saved",
    "job_created",
    "poll_job_not_ready",
    "page_written",
    "rows_rejected",
    "quota_charged",
    "quota_refused",
    "run_paused",
    "run_resumed",
    "run_replanned",
    "run_cancelled",
    "run_failed",
    "run_stalled",
    "run_complete",
)

#: The vendor-specific cursor each strategy stores. The standard record shape is
#: the research's own: ``{vendor, connectionId, cursor, updatedAt}``. These are
#: the four names that go in it.
CURSOR_KINDS: dict[str, str] = {
    "bulk_job_id": "The Salesforce Bulk API 2.0 job id. The job is the cursor.",
    "odata_delta_link": "An OData delta link, the shape odata.track-changes hands back.",
    "data_token": "The Dataverse DataToken, a version number for the table's changes.",
    "paging_cookie": "The PagingCookie that continues a paged read where it stopped.",
    "export_id": "The HubSpot export id, polled until the file is ready.",
    "none": "A run that has not yet been given anything to resume from.",
}

#: The researched numbers, each with the sentence that fixes it. These are
#: constants rather than settings because the research states them as facts about
#: the vendors, not as defaults a deployment may choose.
NUMBERS: dict[str, dict[str, Any]] = {
    "bulk_threshold_records": {
        "value": 2000,
        "quote": (
            "Any data operation that includes more than 2,000 records is a good candidate for Bulk "
            "API 2.0 to successfully prepare, execute, and manage an asynchronous workflow that "
            "uses the Bulk framework. Jobs with fewer than 2,000 records should involve 'bulkified' "
            "synchronous calls in REST (for example, Composite) or SOAP."
        ),
        "meaning": (
            "Above this, ask for an async job. At or below it, page. The rule is Salesforce's own "
            "wording about Salesforce; see the register for how it is applied to the other vendors."
        ),
    },
    "dataverse_page_size": {
        "value": 5000,
        "quote": (
            "If the new or updated item collection is greater than 5,000, the user can page through "
            "the collection."
        ),
        "meaning": "The Count in Dataverse's PagingInfo. The default page size for every strategy.",
    },
    "change_tracking_expiry_days": {
        "value": 7,
        "quote": (
            "Changes are returned if the last token is within a default value of seven days. The "
            "value of the Organization table ExpireChangeTrackingInDays column controls this "
            "duration and can be changed. If unprocessed changes are older than the configured "
            "value, the system throws an exception."
        ),
        "meaning": (
            "How long a stored Dataverse cursor stays resumable. A connection may declare a "
            "different value, because the column is described as configurable."
        ),
    },
}

#: The one researched statement that is a negative, and is therefore easy to
#: drop. Salesforce: "Because both Bulk APIs are asynchronous, Salesforce doesn't
#: guarantee a service level agreement." It is why this package reports progress
#: and never an ETA.
NO_SLA_QUOTE = (
    "Because both Bulk APIs are asynchronous, Salesforce doesn't guarantee a service level agreement."
)

#: The scope HubSpot's exports API needs before it will produce a file, and the
#: privilege the research names as a prerequisite for granting it.
REQUIRED_SCOPE: dict[str, str] = {
    "vendor": "hubspot",
    "scope": "crm.export",
    "quote": (
        "When using an OAuth access token to authenticate requests to the exports API, the user "
        "installing the app must be a Super Admin to grant the crm.export scope."
    ),
}

#: HubSpot's own rule for addressing an export, quoted, because the two spellings
#: are not interchangeable and picking the wrong one wastes the whole job.
OBJECT_ADDRESSING_QUOTE = (
    "For standard objects, you can use the object's name (e.g., CONTACT), but for custom objects, "
    "you must use the objectTypeId value."
)


def describe() -> dict[str, Any]:
    """The whole vocabulary, as one payload a client can render from."""
    return {
        "vendors": [dict(entry) for entry in VENDORS.values()],
        "vendor_ids": sorted(VENDORS),
        "strategies": [
            {"value": value, "meaning": STRATEGY_MEANING[value]} for value in STRATEGIES
        ],
        "directions": [{"value": value, "meaning": DIRECTION_MEANING[value]} for value in DIRECTIONS],
        "scopes": list(SCOPES),
        "run_states": [
            {"value": value, "terminal": value in TERMINAL_STATES} for value in RUN_STATES
        ],
        "log_events": list(LOG_EVENTS),
        "cursor_kinds": [dict(kind=k, meaning=v) for k, v in CURSOR_KINDS.items()],
        "numbers": {name: dict(entry) for name, entry in NUMBERS.items()},
        "no_sla_quote": NO_SLA_QUOTE,
        "required_scope": dict(REQUIRED_SCOPE),
        "object_addressing_quote": OBJECT_ADDRESSING_QUOTE,
    }
