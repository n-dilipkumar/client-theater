"""The published vocabulary WF-035 validates and renders against.

Served as data at ``/vocabulary`` so a client renders its pickers from the same
source the validator enforces against, and so a type or direction added here
reaches every client at once.

What the research fixes, and what this module therefore hard-codes
----------------------------------------------------------------

* **Direction is ``in``, ``out`` or ``both``** - "its direction (in / out /
  both)", step 3 of the user flow.
* **HubSpot property creation needs both ``type`` and ``fieldType``**, and they
  mean different things: "The ``type`` value determines the type of the property,
  i.e. a string or a number. The ``fieldType`` property determines how the
  property will appear in HubSpot or on a form, i.e. as a plain text field, a
  dropdown menu, or a date picker." So this module carries both vocabularies and
  the pairing between them, and the type check in :mod:`dsr.fieldmap.validate`
  reads ``type`` and never ``fieldType``.
* **Enumeration values are internal names**, not labels: "you must use internal
  names to set values. The internal name stays the same even if you've changed a
  default value's label." So :data:`ENUMERATION_HINT` is what every
  ``unsupported_option`` finding quotes.
* **Ten unique keys, for both documented vendors**: "You can have up to ten unique
  ID properties per object" and "A table in a Dataverse instance can have up to ten
  alternate key table definitions."
* **Only five Dataverse attribute types may be in an alternate key**:
  ``DecimalAttributeMetadata``, ``StringAttributeMetadata``,
  ``DateTimeAttributeMetadata``, ``LookupAttributeMetadata``,
  ``PicklistAttributeMetadata``.
* **The four APIs this workflow reads**, verbatim from the research's
  ``apis_hit``, with the document each comes from.

What this module had to decide, and says so
-------------------------------------------

:data:`HUBSPOT_FIELD_TYPES_BY_TYPE` and the room-type compatibility tables are
**curated, not cited**. The research establishes that ``type`` and ``fieldType``
are different axes and that both are required; it does not publish the pairing.
A deployment whose CRM disagrees with a row in this table changes the table, and
:mod:`dsr.fieldmap.inferences` carries the entry saying so. Likewise the
sales-room field dictionary (:data:`ROOM_FIELD_TYPES`) is this product's own, and
a team adding a field adds a row here or overrides the type on the mapping row -
no migration either way, because the type lives in the record's ``data``.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

#: The three researched directions. Anything else is refused at write time.
DIRECTIONS: tuple[str, ...] = ("in", "out", "both")

#: The documented vendors. Salesforce is listed because the research names it in
#: the data flow ("Salesforce field names") and records a sourcing gap for the
#: external-ID half; see :mod:`dsr.fieldmap.inferences`.
PROVIDERS: tuple[str, ...] = ("hubspot", "dataverse", "salesforce")

#: The sales-room field dictionary - the types a sales-room column may carry.
#: This product's own vocabulary, and the left-hand side of every type check.
ROOM_FIELD_TYPES: tuple[str, ...] = (
    "text",
    "email",
    "url",
    "id",
    "number",
    "date",
    "datetime",
    "boolean",
    "enumeration",
)

# --------------------------------------------------------------------------- #
# HubSpot
# --------------------------------------------------------------------------- #

#: HubSpot property ``type`` values - the data type. "The ``type`` value
#: determines the type of the property, i.e. a string or a number."
HUBSPOT_TYPES: tuple[str, ...] = (
    "string",
    "number",
    "date",
    "datetime",
    "enumeration",
    "bool",
    "phone_number",
)

#: HubSpot property ``fieldType`` values - how the property is presented. "The
#: ``fieldType`` property determines how the property will appear in HubSpot or on
#: a form, i.e. as a plain text field, a dropdown menu, or a date picker."
HUBSPOT_FIELD_TYPES: tuple[str, ...] = (
    "text",
    "textarea",
    "date",
    "number",
    "select",
    "multiselect",
    "checkbox",
    "booleanselect",
    "phonenumber",
)

#: Which ``fieldType`` values are coherent with which ``type``.
#:
#: **Curated, not cited** - see this module's docstring. What *is* cited is that
#: the two are different axes, which is why a pair that disagrees is a finding
#: rather than a harmless choice.
HUBSPOT_FIELD_TYPES_BY_TYPE: dict[str, tuple[str, ...]] = {
    "string": ("text", "textarea"),
    "number": ("number",),
    "date": ("date",),
    "datetime": ("date",),
    "enumeration": ("select", "multiselect"),
    "bool": ("checkbox", "booleanselect"),
    "phone_number": ("phonenumber",),
}

#: A sales-room field type mapped onto the HubSpot ``type`` values it may target.
#: Checked against ``type``, per the quoted evidence, never against ``fieldType``.
ROOM_TYPE_TO_HUBSPOT_TYPES: dict[str, tuple[str, ...]] = {
    "text": ("string",),
    "email": ("string",),
    "url": ("string",),
    "id": ("string",),
    "number": ("number",),
    "date": ("date",),
    "datetime": ("datetime", "date"),
    "boolean": ("bool",),
    "enumeration": ("enumeration",),
}

#: The HubSpot property group a created property is filed under. Required in the
#: create body, so the sync-key request builder refuses to build a body without
#: one rather than inventing it: the cited requirement is that it is there.
HUBSPOT_DEFAULT_GROUP = "contactinformation"

# --------------------------------------------------------------------------- #
# Dataverse
# --------------------------------------------------------------------------- #

#: Dataverse ``AttributeType`` (the CSDL short name) to the attribute metadata
#: class name (``$metadata`` / ``EntityDefinitions``). The research names the five
#: metadata class names, so they are the canonical spelling throughout this
#: package and the short names only appear as input aliases.
DATAV_ATTRIBUTE_TYPES: dict[str, str] = {
    "String": "StringAttributeMetadata",
    "Memo": "MemoAttributeMetadata",
    "Email": "EmailAddressAttributeMetadata",
    "Phone": "PhoneNumberAttributeMetadata",
    "Url": "UrlAttributeMetadata",
    "BigInt": "BigIntAttributeMetadata",
    "Integer": "IntegerAttributeMetadata",
    "SmallInt": "SmallIntAttributeMetadata",
    "Decimal": "DecimalAttributeMetadata",
    "Double": "DoubleAttributeMetadata",
    "Float": "FloatAttributeMetadata",
    "Currency": "CurrencyAttributeMetadata",
    "Date": "DateAttributeMetadata",
    "DateTime": "DateTimeAttributeMetadata",
    "Boolean": "BooleanAttributeMetadata",
    "TwoOption": "TwoOptionAttributeMetadata",
    "Picklist": "PicklistAttributeMetadata",
    "MultiSelectPicklist": "MultiSelectPicklistAttributeMetadata",
    "State": "StateAttributeMetadata",
    "Status": "StatusAttributeMetadata",
    "Lookup": "LookupAttributeMetadata",
    "Customer": "CustomerAttributeMetadata",
    "UniqueIdentifier": "UniqueIdentifierAttributeMetadata",
    "Virtual": "VirtualAttributeMetadata",
    "Composite": "CompositeAttributeMetadata",
}

#: The five attribute types an alternate key may be built from, quoted exactly as
#: the research quotes them: "Only include columns of the following types in
#: alternate key table definitions: DecimalAttributeMetadata …
#: StringAttributeMetadata … DateTimeAttributeMetadata … LookupAttributeMetadata …
#: PicklistAttributeMetadata".
DATAV_ELIGIBLE_KEY_TYPES: tuple[str, ...] = (
    "DecimalAttributeMetadata",
    "StringAttributeMetadata",
    "DateTimeAttributeMetadata",
    "LookupAttributeMetadata",
    "PicklistAttributeMetadata",
)

#: A sales-room field type mapped onto the Dataverse attribute metadata classes
#: it may target.
ROOM_TYPE_TO_DATAV_ATTRIBUTE_TYPES: dict[str, tuple[str, ...]] = {
    "text": ("StringAttributeMetadata", "MemoAttributeMetadata"),
    "email": ("EmailAddressAttributeMetadata", "StringAttributeMetadata"),
    "url": ("UrlAttributeMetadata", "StringAttributeMetadata"),
    "id": ("StringAttributeMetadata",),
    "number": (
        "DecimalAttributeMetadata",
        "IntegerAttributeMetadata",
        "BigIntAttributeMetadata",
        "DoubleAttributeMetadata",
        "FloatAttributeMetadata",
        "CurrencyAttributeMetadata",
    ),
    "date": ("DateAttributeMetadata", "DateTimeAttributeMetadata"),
    "datetime": ("DateTimeAttributeMetadata",),
    "boolean": ("BooleanAttributeMetadata", "TwoOptionAttributeMetadata"),
    "enumeration": (
        "PicklistAttributeMetadata",
        "StateAttributeMetadata",
        "StatusAttributeMetadata",
        "MultiSelectPicklistAttributeMetadata",
    ),
}

#: What a Dataverse table's ``Keys($select=KeyAttributes)`` gives, and the
#: default label set per attribute metadata class. Curated: the research cites the
#: $expand shape and the eligible types, not the per-type option sets.
DATAV_OPTION_SETS: dict[str, tuple[tuple[str, str], ...]] = {
    "StateAttributeMetadata": (("0", "Active"), ("1", "Inactive")),
    "StatusAttributeMetadata": (("0", "Draft"), ("1", "Submitted"), ("2", "Approved")),
    "TwoOptionAttributeMetadata": (("0", "No"), ("1", "Yes")),
    "BooleanAttributeMetadata": (("0", "No"), ("1", "Yes")),
    "MultiSelectPicklistAttributeMetadata": (("0", "First"), ("1", "Second")),
}

# --------------------------------------------------------------------------- #
# Salesforce
# --------------------------------------------------------------------------- #

#: Salesforce field ``type`` values, as the Object Reference spells them.
#: **Unsourced**: the research records that "Salesforce's *Object Reference* and
#: *Metadata API* field pages are client-rendered and unreadable via the read
#: path". Present so a Salesforce connection can be mapped and validated; absent
#: is any claim that these are current.
SALESFORCE_TYPES: tuple[str, ...] = (
    "Text",
    "LongTextArea",
    "Email",
    "Url",
    "Id",
    "Reference",
    "Picklist",
    "MultiPicklist",
    "Number",
    "Currency",
    "Percent",
    "Date",
    "DateTime",
    "Boolean",
    "EncryptedString",
)

ROOM_TYPE_TO_SALESFORCE_TYPES: dict[str, tuple[str, ...]] = {
    "text": ("Text", "LongTextArea"),
    "email": ("Email", "Text"),
    "url": ("Url", "Text"),
    "id": ("Text",),
    "number": ("Number", "Currency", "Percent"),
    "date": ("Date",),
    "datetime": ("DateTime",),
    "boolean": ("Boolean",),
    "enumeration": ("Picklist", "MultiPicklist"),
}

# --------------------------------------------------------------------------- #
# The researched ceiling
# --------------------------------------------------------------------------- #

#: "You can have up to ten unique ID properties per object." / "A table in a
#: Dataverse instance can have up to ten alternate key table definitions."
UNIQUE_KEY_LIMIT = 10

#: Which endpoints carry the ceiling, so a 409 can say why rather than just that.
UNIQUE_KEY_LIMIT_QUOTES: dict[str, str] = {
    "hubspot": (
        "HubSpot: you can have up to ten unique ID properties per object "
        "(developers.hubspot.com, CRM properties guide)."
    ),
    "dataverse": (
        "Dataverse: a table in a Dataverse instance can have up to ten alternate key "
        "table definitions (learn.microsoft.com, define alternate keys)."
    ),
}

# --------------------------------------------------------------------------- #
# The APIs this workflow reads
# --------------------------------------------------------------------------- #

#: The researched ``apis_hit`` for WF-035, verbatim, with the document each is
#: cited from. Served at ``/vocabulary`` so a connector knows what to call and a
#: reader can check it against the research without leaving the app.
#:
#: ``sourced: false`` marks a call this workflow does not make. The two Dataverse
#: reads and the HubSpot property create *are* in this workflow's evidence. The
#: Dataverse key-create surface is not: it is WF-036's, and the plan emitted for it
#: says so.
METADATA_ENDPOINTS: dict[str, tuple[Mapping[str, Any], ...]] = {
    "hubspot": (
        {
            "method": "GET",
            "url": "/crm/properties/2026-09/{object}",
            "purpose": "Discover target properties, types and option sets.",
            "document": "HubSpot CRM properties guide",
            "sourced": True,
        },
        {
            "method": "GET",
            "url": "/crm/properties/2026-09/{object}/{property}",
            "purpose": "One property's own metadata, including its option set.",
            "document": "HubSpot CRM properties guide",
            "sourced": True,
        },
        {
            "method": "POST",
            "url": "/crm/properties/2026-09/{object}",
            "purpose": "Create the sync-key property with hasUniqueValue set to true.",
            "document": "HubSpot CRM properties guide",
            "sourced": True,
        },
    ),
    "dataverse": (
        {
            "method": "GET",
            "url": "/api/data/v9.2/EntityDefinitions"
            "?$select=DisplayName,IsKnowledgeManagementEnabled,EntitySetName",
            "purpose": "List the tables an app may address.",
            "document": "Dataverse query metadata Web API",
            "sourced": True,
        },
        {
            "method": "GET",
            "url": "/api/data/v9.2/EntityDefinitions?$select=SchemaName"
            "&$expand=Keys($select=KeyAttributes)",
            "purpose": "The alternate keys, and therefore the key columns, per table.",
            "document": "Dataverse query metadata Web API",
            "sourced": True,
        },
        {
            "method": "GET",
            "url": "/api/data/v9.2/$metadata",
            "purpose": "The CSDL schema document: attribute metadata class names.",
            "document": "Dataverse Web API service documents",
            "sourced": True,
        },
    ),
    "salesforce": (),
}

#: The rule behind the ``unsupported_option`` finding, in the words the research
#: uses. Quoted in every such finding so the admin reading it is told the rule
#: rather than only that their value was refused.
ENUMERATION_HINT = (
    "Enumeration values must be internal names, not labels. HubSpot: \"you must use "
    "internal names to set values. The internal name stays the same even if you've changed "
    "a default value's label.\""
)

#: The rule behind the HubSpot create body's required fields, quoted.
HUBSPOT_CREATE_HINT = (
    "When creating or updating properties, both `type` and `fieldType` values are required. "
    "`type` determines the type of the property and `fieldType` how it is presented."
)

# --------------------------------------------------------------------------- #
# Vendor default mappings
# --------------------------------------------------------------------------- #

#: A default mapping per vendor, because "mapping is stored per connection (not
#: per deployment), a self-hosted room can ship a *default* mapping per vendor and
#: let tenants override individual fields."
#:
#: The capability is cited; **the contents are this build's**, and
#: :mod:`dsr.fieldmap.inferences` says so. They are applied when a connection is
#: created with ``apply_defaults``, and a tenant overriding one field edits that one
#: row - the rest of the default is untouched, which is the whole point of shipping
#: one.
DEFAULT_MAPPINGS: dict[str, tuple[Mapping[str, Any], ...]] = {
    "hubspot": (
        {
            "source_field": "id",
            "source_type": "id",
            "target_property": "dsr_row_id",
            "direction": "out",
            "transform": "identity",
            "transform_config": {},
        },
        {
            "source_field": "account_name",
            "source_type": "text",
            "target_property": "company",
            "direction": "out",
            "transform": "text.trim",
            "transform_config": {},
        },
        {
            "source_field": "primary_contact_email",
            "source_type": "email",
            "target_property": "email",
            "direction": "out",
            "transform": "email.normalize",
            "transform_config": {},
        },
        {
            "source_field": "buyer_stage",
            "source_type": "enumeration",
            "target_property": "lifecyclestage",
            "direction": "out",
            "transform": "picklist.map",
            "transform_config": {
                "map": {
                    "Discovery": "lead",
                    "Evaluation": "opportunity",
                    "Negotiation": "customer",
                }
            },
        },
        {
            "source_field": "seats",
            "source_type": "number",
            "target_property": "numberofemployees",
            "direction": "out",
            "transform": "number.coerce",
            "transform_config": {},
        },
    ),
    "dataverse": (
        {
            "source_field": "id",
            "source_type": "id",
            "target_property": "dsr_row_id",
            "direction": "out",
            "transform": "identity",
            "transform_config": {},
        },
        {
            "source_field": "account_name",
            "source_type": "text",
            "target_property": "name",
            "direction": "out",
            "transform": "text.trim",
            "transform_config": {},
        },
        {
            "source_field": "primary_contact_email",
            "source_type": "email",
            "target_property": "emailaddress1",
            "direction": "out",
            "transform": "email.normalize",
            "transform_config": {},
        },
        {
            "source_field": "renewal_date",
            "source_type": "date",
            "target_property": "effectiveto",
            "direction": "out",
            "transform": "date.iso8601",
            "transform_config": {},
        },
        {
            "source_field": "state",
            "source_type": "enumeration",
            "target_property": "statecode",
            "direction": "out",
            "transform": "picklist.map",
            "transform_config": {"map": {"Open": "0", "Won": "1"}},
        },
    ),
    "salesforce": (
        {
            "source_field": "id",
            "source_type": "id",
            "target_property": "DSR_Row_Id__c",
            "direction": "out",
            "transform": "identity",
            "transform_config": {},
        },
        {
            "source_field": "primary_contact_email",
            "source_type": "email",
            "target_property": "Email",
            "direction": "out",
            "transform": "email.normalize",
            "transform_config": {},
        },
    ),
}

#: The CRM object a vendor's default mapping is written for. The research names
#: the choice as the admin's - "picks the CRM object the room writes to (e.g.
#: Contact, custom Engagement object, lead)" - so a default is only applied when
#: the connection names the object it was written for.
DEFAULT_MAPPING_OBJECTS: dict[str, str] = {
    "hubspot": "contacts",
    "dataverse": "account",
    "salesforce": "Contact",
}


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #


def normalise_provider(value: Any) -> str:
    """The canonical provider spelling, or the empty string when unrecognised."""
    text = str(value or "").strip().lower()
    return text if text in PROVIDERS else ""


def direction_of(value: Any) -> str:
    """The canonical direction spelling, or the empty string when unrecognised."""
    text = str(value or "").strip().lower()
    return text if text in DIRECTIONS else ""


def sends_out(direction: str) -> bool:
    """Whether a row in this direction produces CRM values.

    ``both`` sends both ways, and the outbound half of ``both`` is the one that has
    to satisfy the internal-name rule, so the two directions are tested separately
    everywhere rather than as "out or both".
    """
    return direction in ("out", "both")


def sends_in(direction: str) -> bool:
    """Whether a row in this direction produces sales-room values."""
    return direction in ("in", "both")


def types_for(provider: str, room_type: str) -> tuple[str, ...]:
    """The provider value types a sales-room field type may target.

    Empty for an unknown provider or room type, and the caller treats empty as
    "cannot check" rather than "mismatch" - a type this build does not know about
    is an inference, not a defect in somebody's mapping.
    """
    tables = {
        "hubspot": ROOM_TYPE_TO_HUBSPOT_TYPES,
        "dataverse": ROOM_TYPE_TO_DATAV_ATTRIBUTE_TYPES,
        "salesforce": ROOM_TYPE_TO_SALESFORCE_TYPES,
    }
    table = tables.get(provider)
    if table is None:
        return ()
    return table.get(str(room_type or "").strip().lower(), ())


def attribute_type_of(provider: str, value: str) -> str:
    """The canonical attribute metadata class name for a Dataverse short type."""
    if provider != "dataverse":
        return str(value or "")
    return DATAV_ATTRIBUTE_TYPES.get(str(value or ""), str(value or ""))


def default_mapping(provider: str) -> tuple[Mapping[str, Any], ...]:
    """The shipped default rows for a provider, copied so a caller cannot mutate it."""
    return tuple(dict(row) for row in DEFAULT_MAPPINGS.get(provider, ()))


def default_mapping_object(provider: str) -> str:
    """The object each provider's default rows were written for."""
    return DEFAULT_MAPPING_OBJECTS.get(provider, "")


def describe() -> dict[str, Any]:
    """The whole published vocabulary, as one document."""
    return {
        "directions": list(DIRECTIONS),
        "providers": list(PROVIDERS),
        "room_field_types": list(ROOM_FIELD_TYPES),
        "types_by_provider": {
            "hubspot": list(HUBSPOT_TYPES),
            "dataverse": sorted(set(DATAV_ATTRIBUTE_TYPES.values())),
            "salesforce": list(SALESFORCE_TYPES),
        },
        "field_types": {
            "hubspot": list(HUBSPOT_FIELD_TYPES),
            "hubspot_by_type": {k: list(v) for k, v in HUBSPOT_FIELD_TYPES_BY_TYPE.items()},
        },
        "compatible_targets": {
            provider: {room: list(values) for room, values in table.items()}
            for provider, table in (
                ("hubspot", ROOM_TYPE_TO_HUBSPOT_TYPES),
                ("dataverse", ROOM_TYPE_TO_DATAV_ATTRIBUTE_TYPES),
                ("salesforce", ROOM_TYPE_TO_SALESFORCE_TYPES),
            )
        },
        "enumeration_rule": ENUMERATION_HINT,
        "hubspot_create_rule": HUBSPOT_CREATE_HINT,
        "unique_key_limit": UNIQUE_KEY_LIMIT,
        "unique_key_limit_quotes": dict(UNIQUE_KEY_LIMIT_QUOTES),
        "dataverse_key_eligible_types": list(DATAV_ELIGIBLE_KEY_TYPES),
        "hubspot_default_group": HUBSPOT_DEFAULT_GROUP,
        "metadata_endpoints": {
            provider: [dict(entry) for entry in entries]
            for provider, entries in METADATA_ENDPOINTS.items()
        },
        "default_mappings": {
            provider: [dict(row) for row in rows] for provider, rows in DEFAULT_MAPPINGS.items()
        },
        "default_mapping_objects": dict(DEFAULT_MAPPING_OBJECTS),
    }


def counts_by(rows: Sequence[Mapping[str, Any]], key: str) -> dict[str, int]:
    """A ``{key: count}`` tally, for a summary that does not recount in the client."""
    tally: dict[str, int] = {}
    for row in rows:
        name = str(row.get(key) or "")
        tally[name] = tally.get(name, 0) + 1
    return tally
