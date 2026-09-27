"""Every published name in WF-040, and the researched text behind it.

This is a build, so the researched behaviour is the specification. That makes it
worth keeping the *source* of each name next to the name, in code, rather than
only in the research document: a connector descriptor here carries the request
that produces per-record outcomes, the status the vendor answers with, the key
that correlates a result back to a row, and the quotation it rests on. A reviewer
can then disagree with one descriptor by name.

Three ideas hold the vocabulary together, and all three are quoted in the
research:

**Per-record outcomes instead of a whole-batch failure.** The user flow says the
connector "asks for **per-record outcomes** (multi-status / continue-on-error)"
rather than failing the batch. Each connector spells that differently, and the
spellings are in :data:`CONNECTORS` below:

* HubSpot - ``207 Multi-Status`` on the object API **batch create** endpoints,
  with a unique ``objectWriteTraceId`` on every input and the matching result
  carrying ``context.objectWriteTraceId``.
* Dataverse - ``Prefer: odata.continue-on-error`` on ``$batch``, which answers
  ``200 OK`` and puts the individual errors in the body. A 200 from a Dataverse
  batch is therefore *not* a success, and that is the single most consequential
  fact in this module.
* Salesforce - sObject Collections with ``allOrNone: false``, answering ``200``
  with a per-record result array whose failures carry an ``errors`` array of
  ``errorCode`` and ``message``.

**The error model is the extension point.** The extensibility note is explicit:
"a connector maps vendor codes into ``{retryable, field, code, message,
docLink}``". Those five keys are the room's error model and nothing else is
required of a connector - see :class:`~dsr.partial_failures.normalise.NormalisedError`.

**A doc link where the vendor offers one.** Only Dataverse is documented as
producing one: ``Prefer: odata.include-annotations="*"`` returns annotations
"that contain more details about errors and a URL that might direct you to
specific guidance", including ``@Microsoft.PowerApps.CDS.HelpLink``. So ``docLink``
is a real field that is usually empty, and this module never invents one.

The gap the research states about itself is carried here too: HubSpot's
per-object multi-status detail "is only documented for **batch create** endpoints
in the page read", so the HubSpot descriptor says ``batch_create`` and not
``batch_upsert``.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- #
# Connectors
# --------------------------------------------------------------------------- #

#: The three connectors the research names, and the only three this workflow
#: normalises. Keyed by the name every surface in the product uses.
CONNECTORS: tuple[str, ...] = ("hubspot", "dataverse", "salesforce")

CONNECTOR_LABELS: dict[str, str] = {
    "hubspot": "HubSpot",
    "dataverse": "Dataverse",
    "salesforce": "Salesforce",
}

#: What each connector must be asked for, to get per-record outcomes rather than
#: one verdict for the whole batch. This is the researched user flow's step two,
#: made concrete per vendor.
PER_RECORD_REQUEST: dict[str, dict[str, Any]] = {
    "hubspot": {
        "endpoint_scope": "batch_create",
        "request_headers": {},
        "request_notes": [
            "Enable multi-status error handling on the object API batch create endpoints.",
            "Send a unique objectWriteTraceId on every input; the matching result carries it back "
            "in context.objectWriteTraceId, and that is how a result is keyed to a row.",
        ],
        "gap": (
            "The research records that HubSpot's per-object multi-status detail is only documented "
            "for batch create in the page it read, and that whether batch/upsert and batch/update "
            "accept objectWriteTraceId was not confirmed. This descriptor therefore claims "
            "batch_create only; a connector using it on another endpoint is relying on something "
            "the source set does not say."
        ),
    },
    "dataverse": {
        "endpoint_scope": "$batch",
        "request_headers": {
            "Prefer": "odata.continue-on-error, odata.include-annotations=\"*\"",
        },
        "request_notes": [
            "odata.continue-on-error: the server keeps processing requests after an error, the batch "
            "answers 200 OK, and the individual errors arrive in the body.",
            "odata.include-annotations=\"*\": the response carries the annotations that hold more "
            "detail about errors and a URL pointing at specific guidance.",
        ],
        "gap": (
            "The research quotes the header, the 200 OK, the error body and the HelpLink annotation, "
            "and does not name how a per-item result is correlated to the request that caused it. "
            "This build matches them in request order; see the dataverse-correlation inference."
        ),
    },
    "salesforce": {
        "endpoint_scope": "sObject.Collections",
        "request_headers": {},
        "request_notes": [
            "Send allOrNone: false. The researched flow requires per-record outcomes instead of "
            "failing the whole batch, and allOrNone is the flag that decides between the two.",
        ],
        "gap": (
            "The research quotes the 400 for a malformed body, the 403 with errorCode "
            "REQUEST_LIMIT_EXCEEDED, and the per-item errors array. It does not name the per-record "
            "result array's own key, so this build accepts a results array under results or results."
        ),
    },
}

#: The status each connector answers a partially-failed batch with. Recorded because
#: "200 means success" is the assumption that makes all three connectors lie.
PER_RECORD_STATUS: dict[str, dict[str, Any]] = {
    "hubspot": {
        "partial": 207,
        "all_succeeded": 200,
        "malformed": 400,
        "note": (
            "207 Multi-Status is returned when there are different statuses - errors and successes "
            "together - which occurs once multi-status error handling is enabled on the object API "
            "batch create endpoints."
        ),
    },
    "dataverse": {
        "partial": 200,
        "all_succeeded": 200,
        "malformed": 400,
        "note": (
            "The batch answers 200 OK whether or not any request inside it failed, and the failures "
            "are in the body. A caller that reads 200 as success reports a clean sync for a batch "
            "where nothing was written."
        ),
    },
    "salesforce": {
        "partial": 200,
        "all_succeeded": 200,
        "malformed": 400,
        "note": (
            "sObject Collections answers 200 with a per-record result array; a 400 means the request "
            "could not be understood at all, usually a bad JSON or XML body, and a 403 with "
            "errorCode REQUEST_LIMIT_EXCEEDED means the org's API request limits were exceeded."
        ),
    },
}

#: How a per-record result is tied back to the input row. Only HubSpot's key is
#: in the source set; the other two are this build's reading and are published as
#: inferences rather than here.
CORRELATION: dict[str, dict[str, Any]] = {
    "hubspot": {
        "key": "objectWriteTraceId",
        "where": "context.objectWriteTraceId on the failing result",
        "sourced": True,
        "note": "A list; the room takes the first entry and keeps the whole list in the raw payload.",
    },
    "dataverse": {
        "key": "request order",
        "where": "position of the item in the $batch response, matched to the position it was sent",
        "sourced": False,
        "note": (
            "Not named by the source set. A row may still carry its own trace_id, and the room "
            "prefers a matching id over the position when one is present."
        ),
    },
    "salesforce": {
        "key": "results order",
        "where": "position of the item in the per-record result array, matched to the position sent",
        "sourced": False,
        "note": (
            "Not named by the source set. Salesforce's sObject Collections result entries do carry "
            "the created or updated record id, which is a stronger key when the connector captured it."
        ),
    },
}

#: Where a doc link comes from, per connector. Only Dataverse is documented to
#: produce one, and inventing one for the other two would be a fabricated URL.
DOC_LINK_SOURCE: dict[str, dict[str, Any]] = {
    "hubspot": {
        "annotation": None,
        "sourced": False,
        "note": "The researched page gives error messages and codes, and no per-error documentation URL.",
    },
    "dataverse": {
        "annotation": "@Microsoft.PowerApps.CDS.HelpLink",
        "sourced": True,
        "note": (
            "Returned under Prefer: odata.include-annotations=\"*\": the annotations contain more "
            "detail about errors and a URL that might direct you to specific guidance."
        ),
    },
    "salesforce": {
        "annotation": None,
        "sourced": False,
        "note": "The researched page quotes errorCode and message, and no per-error documentation URL.",
    },
}

#: Where the offending property is read from, per connector, in order of
#: preference. Salesforce is the only one the source set names, and it names the
#: key ``fields`` on the per-item error.
FIELD_SOURCE: dict[str, dict[str, Any]] = {
    "hubspot": {
        "keys": ["field", "in", "name"],
        "sourced": False,
        "note": (
            "The research says a HubSpot per-item error object carries message, code and context and "
            "does not name the property key, so this build reads the three keys above and, if none "
            "matches, reports the property from the room's own validation metadata and says so."
        ),
    },
    "dataverse": {
        "keys": [],
        "sourced": False,
        "note": (
            "Dataverse names no property key; it names the property inside the message. The room "
            "reads the quoted attribute out of the message, which the researched example makes "
            "possible: \"The length of the 'subject' attribute of the 'task' entity exceeded the "
            "maximum allowed length of '200'.\""
        ),
    },
    "salesforce": {
        "keys": ["fields", "field"],
        "sourced": True,
        "note": "The per-item error array carries the offending fields alongside errorCode and message.",
    },
}

#: The three sourced sentences each connector descriptor rests on, quoted from the
#: research's own evidence block so the page can show them next to the claim.
CONNECTOR_QUOTES: dict[str, str] = {
    "hubspot": (
        "\"207 Multi-Status | Returned when there are different statuses (e.g., errors and "
        "successes), which occurs when you've enabled multi-status error handling for the object "
        "API batch create endpoints.\""
    ),
    "dataverse": (
        "\"If you add the `Prefer: odata.continue-on-error` request header, you can specify that the "
        "server processes more requests when errors occur. The batch request returns `200 OK`, and "
        "individual response errors are included in the batch response body.\""
    ),
    "salesforce": (
        "\"400 The request couldn't be understood, usually because the JSON or XML body contains an "
        "error.\" / \"403 ... If the error code is REQUEST_LIMIT_EXCEEDED, you've exceeded API "
        "request limits in your org.\""
    ),
}

#: The researched date on which HubSpot began enforcing admin-configured
#: validation rules on all CRM API write paths, and the API version that carried
#: it. This is a fact about a release, not a setting a team can turn off, which is
#: exactly why the room's own pre-flight validation matters.
HUBSPOT_VALIDATION_ENFORCEMENT = {
    "api_version": "/2026-09/",
    "ga_date": "2026-09-08",
    "quote": (
        "\"Starting with the GA release of API version `/2026-09/` on September 8, 2026, HubSpot will "
        "enforce admin-configured validation rules on all CRM API write paths. You can retrieve or "
        "manage your validation rules using the property validation API, or by reviewing rules via "
        "the property settings page.\""
    ),
    "consequence": (
        "A write the room could have refused locally now costs a CRM call and lands in the Sync log. "
        "That is the half of this workflow's title that happens before the request leaves."
    ),
}

# --------------------------------------------------------------------------- #
# Row vocabulary
# --------------------------------------------------------------------------- #

#: The per-row status chips the Sync log shows. Exactly two, and the count is
#: itself the researched claim: the user flow says the connector asks for
#: per-record outcomes and the log "lists each failed row", and the retry action
#: is "Retry failed rows only - successes are not re-sent". A row is written when
#: a result arrives, so there is no third state to invent: a row that has not been
#: sent has no outcome and therefore no row.
ROW_STATUSES: tuple[str, ...] = ("succeeded", "failed")

ROW_STATUS_LABELS: dict[str, str] = {"succeeded": "Succeeded", "failed": "Failed"}

#: What a failed row is waiting for.
#:
#: The automation is quoted: "Retry queue drains automatically for retryable
#: classes (rate limit, locked) and waits for admin action for validation
#: failures - the room classifies errors into retryable vs terminal." So there are
#: two waiting states, and the classification is what chooses between them.
DISPOSITIONS: tuple[str, ...] = ("queued", "needs_action", "resolved")

DISPOSITION_LABELS: dict[str, str] = {
    "queued": "Queued for retry",
    "needs_action": "Needs a person",
    "resolved": "Resolved",
}

DISPOSITION_MEANING: dict[str, str] = {
    "queued": "The error clears on its own, so the retry queue drains it without anyone asking.",
    "needs_action": "Only an admin can move this: fix the mapping or the data, then retry.",
    "resolved": "An admin retried the row and it was accepted, or acknowledged the failure.",
}

#: The researched automation, quoted, because the two dispositions are not this
#: build's invention.
AUTOMATION_QUOTE = (
    "Retry queue drains automatically for retryable classes (rate limit, locked) and waits for admin "
    "action for validation failures - the room classifies errors into retryable vs terminal."
)

#: The researched extension point, quoted, because it is the exact shape a
#: deployment adds a rule through.
EXTENSIBILITY_QUOTE = (
    "The room's error model is the extension point - a connector maps vendor codes into "
    "{retryable, field, code, message, docLink}. A deployment can add a rule (\"route records "
    "missing `email` to a manual-review queue instead of retrying\") without changing the transport."
)

#: The five keys the room's error model is made of, in the research's order.
ERROR_MODEL_KEYS: tuple[str, ...] = ("retryable", "field", "code", "message", "doc_link")

ERROR_MODEL_SPELLING: dict[str, str] = {
    "retryable": "retryable",
    "field": "field",
    "code": "code",
    "message": "message",
    "doc_link": "docLink",
}

# --------------------------------------------------------------------------- #
# Collections
# --------------------------------------------------------------------------- #

#: The collections this workflow owns. Named after the ticket so a team reading
#: /api/collections can tell which rows are the Sync log and which belong to
#: another workflow.
RUN_COLLECTION = "crm_sync_run"
ROW_COLLECTION = "crm_sync_row"
RULES_COLLECTION = "crm_sync_rules"
RULES_RECORD_ID = "crm_sync_rules"
ROOM_COLLECTION = "room"


def vocabulary() -> dict[str, Any]:
    """Everything this workflow publishes, as data.

    Served at ``/api/wf-040/vocabulary`` so a client renders its pickers, its
    status chips and its error detail from the same source the normaliser
    validates against, rather than from a list compiled in the browser that can
    drift from the connector table.
    """
    connectors = []
    for name in CONNECTORS:
        connectors.append(
            {
                "id": name,
                "label": CONNECTOR_LABELS[name],
                "per_record_request": PER_RECORD_REQUEST[name],
                "status": PER_RECORD_STATUS[name],
                "correlation": CORRELATION[name],
                "doc_link": DOC_LINK_SOURCE[name],
                "field": FIELD_SOURCE[name],
                "quote": CONNECTOR_QUOTES[name],
            }
        )
    return {
        "connectors": connectors,
        "connector_ids": list(CONNECTORS),
        "row_statuses": list(ROW_STATUSES),
        "row_status_labels": dict(ROW_STATUS_LABELS),
        "dispositions": list(DISPOSITIONS),
        "disposition_labels": dict(DISPOSITION_LABELS),
        "disposition_meaning": dict(DISPOSITION_MEANING),
        "error_model_keys": list(ERROR_MODEL_KEYS),
        "error_model_spelling": dict(ERROR_MODEL_SPELLING),
        "automation": AUTOMATION_QUOTE,
        "extensibility": EXTENSIBILITY_QUOTE,
        "hubspot_validation_enforcement": HUBSPOT_VALIDATION_ENFORCEMENT,
        "collections": {
            "run": RUN_COLLECTION,
            "row": ROW_COLLECTION,
            "rules": RULES_COLLECTION,
        },
    }


def require_connector(name: Any) -> str:
    """The connector name, or a refusal naming the three this build normalises."""
    from dsr.partial_failures.errors import UnknownConnector

    text = str(name or "").strip().lower()
    if text not in CONNECTORS:
        raise UnknownConnector(
            f"unknown connector {name!r}; this workflow normalises {', '.join(CONNECTORS)}"
        )
    return text


__all__ = [
    "CONNECTORS",
    "CONNECTOR_LABELS",
    "CONNECTOR_QUOTES",
    "PER_RECORD_REQUEST",
    "PER_RECORD_STATUS",
    "CORRELATION",
    "DOC_LINK_SOURCE",
    "FIELD_SOURCE",
    "HUBSPOT_VALIDATION_ENFORCEMENT",
    "ROW_STATUSES",
    "ROW_STATUS_LABELS",
    "DISPOSITIONS",
    "DISPOSITION_LABELS",
    "DISPOSITION_MEANING",
    "ERROR_MODEL_KEYS",
    "ERROR_MODEL_SPELLING",
    "AUTOMATION_QUOTE",
    "EXTENSIBILITY_QUOTE",
    "RUN_COLLECTION",
    "ROW_COLLECTION",
    "RULES_COLLECTION",
    "RULES_RECORD_ID",
    "ROOM_COLLECTION",
    "vocabulary",
    "require_connector",
]
