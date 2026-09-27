"""Every decision this workflow makes that the research does not make.

The research for WF-035 is unusually specific about what it knows: it names the
three validation findings, quotes the ten-key ceiling for both vendors, lists the
five attribute types an alternate key may use, and records two gaps. Everything
else this build had to decide, and a decision a reviewer cannot see in a diff is a
decision a reviewer cannot disagree with.

Each entry says four things: the question, the reading taken, why, and how to
change it. Served at ``/inferences`` and rendered on the page, because the
difference between "we inferred this" and "we inferred this, here it is, here is
what would change it" is the difference between a judgement call and a hidden
assumption.
"""

from __future__ import annotations

from typing import Any

INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "hubspot_type_fieldtype_pairing",
        "question": (
            "Which HubSpot fieldType may be filed under which type? The research establishes "
            "that both are required and that they mean different things; it does not publish the "
            "pairing."
        ),
        "decision": (
            "A curated table, HUBSPOT_FIELD_TYPES_BY_TYPE. A create body whose pair disagrees with "
            "the table is refused, and a recorded property whose own pair disagrees is reported as "
            "a field_type_disagrees warning."
        ),
        "why": (
            "Without a pairing rule, the cited distinction between type and fieldType has nothing "
            "to act on, and a mismatch would first surface as a CRM 400 at create time. The table "
            "is the smallest thing that makes the distinction load-bearing."
        ),
        "change_it": "Edit HUBSPOT_FIELD_TYPES_BY_TYPE in dsr.fieldmap.vocabulary. Nothing else reads it.",
    },
    {
        "id": "room_field_dictionary",
        "question": "What are the sales-room field types a mapping row may declare?",
        "decision": (
            "ROOM_FIELD_TYPES: text, email, url, id, number, date, datetime, boolean, enumeration. A "
            "row may carry any string; a type this build does not know is accepted and simply is "
            "not type-checked."
        ),
        "why": (
            "The research names the four transforms, which implies a small type set, but never "
            "publishes one. Accepting an unknown type rather than refusing it keeps the "
            "schema-flexibility promise: a team adding a field must not need a code change."
        ),
        "change_it": "Extend the tables in dsr.fieldmap.vocabulary, or send a source_type per row.",
    },
    {
        "id": "exact_property_name_match",
        "question": "Should a target property name match the CRM's case-insensitively?",
        "decision": (
            "Exact, case included. A near miss is reported as unknown_property with difflib "
            "suggestions rather than normalised onto the name the metadata has."
        ),
        "why": (
            "Adopting a near match would let a mapping validate here and fail in the CRM, which is "
            "the precise failure the research's step 5 exists to prevent. A suggestion gives the "
            "admin the same convenience without the false assurance."
        ),
        "change_it": "Change Metadata.property in dsr.fieldmap.metadata.",
    },
    {
        "id": "mapping_invariant_findings",
        "question": "The research names three findings. What else does a validation report carry?",
        "decision": (
            "Four more, grouped as MAPPING_FLAGS and visible without a CRM read: no_target "
            "(warning), duplicate_target, transform_unavailable and transform_mismatch (warning). "
            "The three researched findings stay in METADATA_FLAGS, unchanged."
        ),
        "why": (
            "The research's three are exactly the ones produced by comparing a mapping against live "
            "CRM metadata. Two rows targeting one property is a mapping fault that no metadata read "
            "would catch, and a grid that cannot show it will send the same column twice."
        ),
        "change_it": "SEVERITY and MAPPING_FLAGS in dsr.fieldmap.validate.",
    },
    {
        "id": "no_target_is_a_warning",
        "question": "Is a sales-room field with no CRM property an error?",
        "decision": "A warning. It does not block activation, and it does not count as a mapped row.",
        "why": (
            "The grid lists the sales-room field dictionary, most of which an admin will not map. A "
            "grid that cannot be saved until every field is mapped would be a grid nobody completes."
        ),
        "change_it": "SEVERITY['no_target'] in dsr.fieldmap.validate.",
    },
    {
        "id": "dataverse_key_creation_unsourced",
        "question": (
            "WF-035 cites the two EntityDefinitions reads and the alternate-key type rule, but not "
            "the call that creates a key. What request should this workflow emit?"
        ),
        "decision": (
            "A plan, marked sourced: false, with the EntityKeyMetadata step and the CreateEntityKey "
            "step named and the second URL left unset. HubSpot's plan is sourced: true."
        ),
        "why": (
            "The research's own gap discipline is the point: emitting a URL this workflow cannot "
            "source would be a guess wearing the costume of a citation. The create surface is "
            "WF-036's, which does cite it."
        ),
        "change_it": "DATAVERSE_GAP and _dataverse_plan in dsr.fieldmap.sync_key.",
    },
    {
        "id": "salesforce_external_id_unsourced",
        "question": (
            "The research records that Salesforce's Object Reference and Metadata API field pages "
            "are client-rendered and unreadable, so 'create the external-ID field' is not cited. "
            "What happens on a Salesforce connection?"
        ),
        "decision": (
            "Salesforce metadata is normalised, mappings are validated, previews run, and a sync key "
            "can be pinned to a field the admin has already marked unique. No create request is "
            "emitted: the builder raises with the gap quoted."
        ),
        "why": (
            "Half the flow is still useful, and the half that is not sourced is refused loudly "
            "instead of being guessed at."
        ),
        "change_it": "SALESFORCE_GAP and build_create_request in dsr.fieldmap.sync_key.",
    },
    {
        "id": "salesforce_metadata_shape",
        "question": "Salesforce metadata is normalised, but the shape is not cited. Is that safe?",
        "decision": (
            "Yes, with the payload marked: the Salesforce document name says 'unsourced' and the "
            "read carries a note that no request is emitted for this provider."
        ),
        "why": (
            "Reading a payload is not the same as making a claim about a vendor's API, and the note "
            "is where a reader finds out. The alternative, refusing Salesforce outright, would "
            "drop a provider the research names in its data flow."
        ),
        "change_it": "_normalise_salesforce in dsr.fieldmap.metadata.",
    },
    {
        "id": "metadata_is_recorded_not_fetched",
        "question": (
            "Step 5 says the sales room reads the CRM's property metadata. This workflow does not "
            "fetch. Why not?"
        ),
        "decision": (
            "The bearer token and the authenticated request executor are WF-034's. A connector reads "
            "the endpoints served at /vocabulary and posts the document to this feature's properties "
            "route, which normalises and records it. Validating with nothing recorded is a 409."
        ),
        "why": (
            "Fetching would mean a token vault and a request executor in a workflow whose research "
            "never mentions either, and a socket call in a seeder. The recorded read is also what "
            "makes a validation auditable: the report names the document and the time."
        ),
        "change_it": "Swap MetadataReader in behind the properties routes; the record layer is unchanged.",
    },
    {
        "id": "metadata_staleness",
        "question": "When is a recorded metadata read too old to validate against?",
        "decision": (
            "Never automatically. The read reports its age and whether it is older than a configured "
            "max age; the decision to re-read is the reader's."
        ),
        "why": (
            "The research says 'Dataverse can flag a schema change so a cached mapping is "
            "auto-invalidated (see W17)'. W17 is section 17 of the same document and is a different "
            "workflow. Building the invalidation here would take a decision the research assigns "
            "elsewhere."
        ),
        "change_it": "metadata_view in dsr.fieldmap.mappings, plus whatever W17 lands.",
    },
    {
        "id": "activation_gate",
        "question": (
            "The research says validation happens 'before any data is written', and lists no "
            "activate step. What stops a mapping going live unvalidated?"
        ),
        "decision": (
            "A mapping is draft or active, and activation is refused unless the latest validation "
            "has no error finding. Any change to the grid, the sync key or the object clears the "
            "last validation, because a mapping whose rows changed has not been validated in its "
            "current shape."
        ),
        "why": (
            "'Before any data is written' is a statement about ordering, and an ordering rule needs "
            "something to enforce it. Clearing the stamp on change is what stops an activation "
            "riding a validation of a grid that no longer exists."
        ),
        "change_it": "FieldMapping.activate and MappingBook.clear_validation.",
    },
    {
        "id": "sync_key_required_for_activation",
        "question": "Must an active mapping have a pinned sync key?",
        "decision": "Yes. A mapping with no key cannot be activated.",
        "why": (
            "The sync key is step 4 of a five-step flow and is what makes a write idempotent: it is "
            "the property that carries the sales room's own row id so the CRM rejects a collision. "
            "An active mapping without one would double-write on every sync cycle."
        ),
        "change_it": "build_report's can_activate in dsr.fieldmap.validate.",
    },
    {
        "id": "declared_transforms_are_not_executable",
        "question": (
            "The research's extension point is a transform registered in the connector's code. "
            "What does this feature offer over HTTP?"
        ),
        "decision": (
            "A transform can be declared as data, which records the name, version and intent. A "
            "declared transform is not executable: a row naming one gets a transform_unavailable "
            "error until the code is registered in dsr.fieldmap.transforms."
        ),
        "why": (
            "Executable code arriving as JSON would be a remote code execution surface. The "
            "declaration is still worth recording - it is where an admin writes down the transform "
            "a deployment is adding - and the badge says precisely what is missing."
        ),
        "change_it": "MappingBook.declare_transform and the registry in dsr.fieldmap.transforms.",
    },
    {
        "id": "vendor_default_contents",
        "question": "The research says a deployment can ship a default mapping per vendor. Which rows?",
        "decision": (
            "DEFAULT_MAPPINGS: five rows for HubSpot, five for Dataverse, two for Salesforce, each "
            "written for one object. They apply only when a mapping is for that object."
        ),
        "why": (
            "The capability is cited and the contents are not. Shipping them under the right object "
            "means a new tenant gets a working mapping, and the object guard means a custom "
            "engagement object does not inherit five rows pointing at properties it lacks."
        ),
        "change_it": "DEFAULT_MAPPINGS in dsr.fieldmap.vocabulary.",
    },
    {
        "id": "room_scoping",
        "question": "Is a mapping per room or per connection?",
        "decision": (
            "Per connection, as the research says ('mapping is stored per connection (not per "
            "deployment)'). A connection may itself be room-scoped, and the room routes read through "
            "that scoping rather than storing a second copy."
        ),
        "why": (
            "Two copies of one mapping is a configuration that disagrees with itself. Scoping at the "
            "connection keeps one source of truth and still lets a room page show only its own."
        ),
        "change_it": "MappingBook.connections and the room routes in the feature module.",
    },
)


def describe() -> dict[str, Any]:
    """The whole list, with the count, for the page's panel and the tests."""
    return {"count": len(INFERENCES), "inferences": [dict(entry) for entry in INFERENCES]}


def by_id(inference_id: str) -> dict[str, Any] | None:
    """One entry, so a client can link straight to the decision a finding names."""
    for entry in INFERENCES:
        if entry["id"] == inference_id:
            return dict(entry)
    return None
