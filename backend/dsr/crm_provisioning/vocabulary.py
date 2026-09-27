"""The values the research for WF-036 fixes by name, served as data.

Every constant here is traceable to a sentence in
``docs/research/digital-sales-room-workflows/wf/WF-036.md``. Where the research
names a value, it is quoted beside it so a reader can check the code against the
evidence without opening the document. Where the research does *not* name one,
the value is not here - it is in :mod:`dsr.crm_provisioning.inferences`, because
a value that looks sourced and is not is the worst kind.

Nothing in this module is a migration, a typed column, or a new required field.
It is a list of ordinary Python values, and :func:`vocabulary` serves them as
JSON so a client renders its pickers from the same source the validator enforces
against.
"""

from __future__ import annotations

from typing import Any

#: The two vendors whose provisioning behaviour this research carries. Its own
#: sentence: "HubSpot and Dataverse carry this workflow in the evidence."
VENDORS: tuple[str, ...] = ("hubspot", "dataverse")

#: The vendor named in the research and *not* carried by it. Kept as a name
#: rather than as an absence so the refusal can point at the gap instead of at
#: nothing. See :class:`dsr.crm_provisioning.errors.UnsupportedVendor`.
UNSUPPORTED_VENDORS: dict[str, str] = {
    "salesforce": (
        "The Salesforce half (Metadata API CustomObject / CustomField deploy) could not be "
        "sourced - the Metadata API guide returns a cookie banner only."
    ),
}

#: The vendor-neutral property types a manifest may declare.
#:
#: The research names "field types, option sets" as part of the descriptor and
#: quotes HubSpot's split between a property's ``type`` ("i.e. a string or a
#: number") and its ``fieldType`` ("how the property will appear in HubSpot or
#: on a form, i.e. as a plain text field, a dropdown menu, or a date picker").
#: So a manifest declares one neutral type and each vendor adapter splits it.
PROPERTY_TYPES: tuple[str, ...] = ("string", "number", "bool", "datetime", "enumeration")

#: What the diff decided to do about one manifest property.
#:
#: ``create``        it is missing, and the request that would create it is attached
#: ``unchanged``     it already exists and matches the manifest exactly
#: ``conflict``      it already exists and differs; reported, never changed
#: ``unmappable``    this vendor cannot create it as declared; skipped, with a reason
#: ``left_in_place`` a property on the CRM object the manifest no longer declares;
#:                   reported, never dropped
PROPERTY_ACTIONS: tuple[str, ...] = (
    "create",
    "unchanged",
    "conflict",
    "unmappable",
    "left_in_place",
)

#: The four index states a background alternate-key build can be in.
#:
#: "``EntityKeyMetadata.EntityKeyIndexStatus`` ... Pending / In Progress / Active /
#: Failed." Verbatim, spaces and capitals included, because these names go over
#: the wire and a client that switches on them needs them exact.
KEY_STATUSES: tuple[str, ...] = ("Pending", "In Progress", "Active", "Failed")

#: "16 columns per key."
KEY_MAX_COLUMNS = 16

#: "900 bytes per key."
KEY_MAX_BYTES = 900

#: The order a simulated background index build moves through.
#:
#: The four names are sourced. How many times a client has to look before the
#: build finishes is this build's judgement, and it is recorded as the
#: ``key-index-advances-on-poll`` inference rather than asserted here as fact.
KEY_STATUS_PROGRESSION: dict[str, str] = {
    "Pending": "In Progress",
    "In Progress": "Active",
    "Failed": "Pending",
    "Active": "Active",
}

#: What one installer run did.
OUTCOMES: tuple[str, ...] = ("created", "unchanged", "dry_run")

#: What the diff decided about the object itself. Closed, and two long, because
#: "the object exists" and "the object is missing" is the whole of it.
OBJECT_ACTIONS: tuple[str, ...] = ("create", "unchanged")

#: What the diff decided about the declared sync key. Closed, and five long: the
#: manifest declares none, this vendor has no sourced call for one, the declared
#: one is not there yet, it is there and its index built, or it is there and its
#: index did not. The last one is reported rather than treated as an error,
#: because the research names a half-provisioned key as something to repair
#: rather than something to refuse.
KEY_ACTIONS: tuple[str, ...] = ("create", "unchanged", "absent", "unsupported", "failed")

#: How bad a manifest finding is.
#:
#: ``blocking``  the whole run is refused with 422; nothing is written
#: ``property``  that one property is skipped and the reason is recorded on the run
#: ``advisory``  the run proceeds and the finding is reported
FINDING_SEVERITIES: tuple[str, ...] = ("blocking", "property", "advisory")

#: The rooms a connection can be bound to, used by the room-scoped reads. The
#: room record's own ``integrations`` field carries vendor names; a connection is
#: the thing a vendor account is reached through, and the binding is explicit so
#: one account can serve several rooms.
CONNECTION_ENVIRONMENTS: tuple[str, ...] = ("production", "sandbox")


def vocabulary() -> dict[str, Any]:
    """Every published value this workflow enforces against."""
    return {
        "vendors": list(VENDORS),
        "unsupported_vendors": dict(UNSUPPORTED_VENDORS),
        "property_types": list(PROPERTY_TYPES),
        "property_actions": list(PROPERTY_ACTIONS),
        "object_actions": list(OBJECT_ACTIONS),
        "key_actions": list(KEY_ACTIONS),
        "key_statuses": list(KEY_STATUSES),
        "key_status_progression": dict(KEY_STATUS_PROGRESSION),
        "key_limits": {"max_columns": KEY_MAX_COLUMNS, "max_bytes": KEY_MAX_BYTES},
        "outcomes": list(OUTCOMES),
        "finding_severities": list(FINDING_SEVERITIES),
        "connection_environments": list(CONNECTION_ENVIRONMENTS),
    }
