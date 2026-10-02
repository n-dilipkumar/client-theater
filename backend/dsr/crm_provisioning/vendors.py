"""The two vendor adapters: the researched request bodies, one per CRM.

The research names the endpoints it read and writes them down verbatim, so this
module is where those paths live. It exists because a manifest is vendor-neutral
("a sales-room object descriptor (name, labels, field types, option sets)") while
the two APIs are not: HubSpot creates a property with a `POST` whose path carries
a numeric object type and whose body must carry `groupName`, `name`, `label`,
`type` and `fieldType`; Dataverse creates a column as a POST onto an
`EntityDefinitions` attribute collection and creates the sync key through
`CreateEntityKey` with an `EntityKeyMetadata` body.

Two things are deliberately *not* here, because the research does not source
them:

* **A Salesforce adapter.** The research says so in its own gaps paragraph. An
  absent adapter and a refused vendor are the same thing, and
  :class:`~dsr.crm_provisioning.errors.UnsupportedVendor` is how a caller finds
  out which one it hit.
* **A HubSpot alternate key.** `CreateEntityKey` is Dataverse's. The sibling
  workflow in the same research document creates a unique property with
  `hasUniqueValue`, but that is *its* sourced claim, not this workflow's, and
  borrowing it here would be putting an unsourced rule in a build whose whole
  point is that it is not. The adapter reports ``supports_alternate_key: False``
  and the diff says ``unsupported``.

A missing type mapping is a per-property skip, never a guess. Inventing a
``fieldType`` the cited page does not list would put a value over the wire that
nobody sourced, and a wrong ``fieldType`` on a CRM property is a property no one
can edit in the vendor's own UI.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dsr.crm_provisioning.errors import UnsupportedVendor
from dsr.crm_provisioning.vocabulary import PROPERTY_TYPES, UNSUPPORTED_VENDORS, VENDORS


@dataclass(frozen=True)
class VendorAdapter:
    """One CRM's provisioning surface: the paths, and the required body fields."""

    vendor: str
    label: str

    #: The reads step two of the researched flow makes to list what already
    #: exists: "read ``GET /`` (service document) / ``GET /crm/schemas/...`` /
    #: ``EntityDefinitions`` to list what already exists."
    schema_read: tuple[str, ...]

    #: Where the object itself is created.
    object_create: str

    #: Where one property of an object is created. ``{object_type}`` is the id the
    #: object-create returned, which is a number for HubSpot and a schema name
    #: for Dataverse - see ``object_id_style``.
    property_create: str

    #: Numeric for HubSpot (a generated object type), a schema name for Dataverse.
    object_id_style: str

    #: The body fields this vendor refuses to create a property without.
    required_property_fields: tuple[str, ...]

    #: Vendor-neutral type -> the body pair this vendor expects.
    type_map: dict[str, dict[str, str]]

    #: The body fields the adapter fills in from the manifest's own neutral
    #: descriptor, so a manifest never has to be told them twice.
    #:
    #: For HubSpot the research names all five: ``groupName``, ``name``,
    #: ``label``, ``type``, ``fieldType``. Four of the five are derivable -
    #: the property's name, its label, and the pair the neutral type splits into.
    #: ``groupName`` is not: the research lists it as required and names no
    #: default, so a manifest that omits it has its property skipped on HubSpot
    #: and installed on Dataverse. That asymmetry is deliberate and reported.
    derivable_fields: frozenset[str] = frozenset()

    #: Where the alternate key is created, and reactivated. ``None`` when this
    #: research does not source one for that vendor.
    key_create: str | None = None
    key_reactivate: str | None = None
    background_index: bool = False

    #: Whether the object-create body needs ``label`` and ``description``, which
    #: HubSpot's schema create does and Dataverse's table definition does not.
    object_label_required: bool = True

    #: The body key carrying a property's display label, per vendor.
    label_field: str = "label"

    #: The body key carrying a property's internal name, per vendor. HubSpot calls
    #: it ``name``; a Dataverse column is a schema attribute and the API calls it
    #: ``schemaName``. Putting the neutral name under the wrong key would put a
    #: field on the wire the vendor does not read.
    name_field: str = "name"

    def supports(self, property_type: str) -> bool:
        return property_type in self.type_map

    def typed(self, property_type: str) -> dict[str, str]:
        return dict(self.type_map[property_type])

    @property
    def supports_alternate_key(self) -> bool:
        """Whether this vendor has a sourced alternate-key call in this build.

        A property rather than a field, because it is derived: there is exactly one
        way to answer it and two places that ask.
        """
        return self.key_create is not None

    def property_path(self, object_type: str) -> str:
        return self.property_create.replace("{object_type}", str(object_type))

    def check(self) -> None:
        """Refuse a vendor this workflow has no evidence for."""
        if self.vendor in VENDORS:
            return
        gap = UNSUPPORTED_VENDORS.get(
            self.vendor,
            "No researched provisioning behaviour for this vendor is recorded in "
            "docs/research/digital-sales-room-workflows/wf/WF-036.md.",
        )
        raise UnsupportedVendor(
            f"{self.vendor!r} is not a vendor this workflow can provision: {gap}"
        )


#: HubSpot. The property-create path and the five required body fields are
#: quoted by the research: "HubSpot property creation required fields:
#: ``groupName`` ... ``name``: the internal name of the property ... ``label`` ...
#: ``type``: the type of property. ``fieldType``: the field type." The three
#: fieldTypes the same document's evidence names in prose are ``text``
#: ("a plain text field"), ``select`` ("a dropdown menu") and ``date``
#: ("a date picker"); the other two are recorded as inferred in
#: :mod:`dsr.crm_provisioning.inferences`.
HUBSPOT = VendorAdapter(
    vendor="hubspot",
    label="HubSpot",
    schema_read=(
        "/",
        "/crm-object-schemas/2026-09/schemas",
        "/crm/v3/schemas/...",
    ),
    object_create="/crm-object-schemas/2026-09/schemas",
    property_create="/crm/properties/2026-09/{object_type}",
    object_id_style="numeric",
    required_property_fields=("groupName", "name", "label", "type", "fieldType"),
    derivable_fields=frozenset({"name", "label", "type", "fieldType"}),
    type_map={
        "string": {"type": "string", "fieldType": "text"},
        "number": {"type": "number", "fieldType": "number"},
        "bool": {"type": "enumeration", "fieldType": "booleancheckbox"},
        "datetime": {"type": "datetime", "fieldType": "date"},
        "enumeration": {"type": "enumeration", "fieldType": "select"},
    },
    key_create=None,
    key_reactivate=None,
    background_index=False,
    object_label_required=True,
    label_field="label",
    name_field="name",
)

#: Dataverse. The reads are the researched metadata surfaces ("``GET
#: /api/data/v9.2/$metadata`` and ``GET /api/data/v9.2/EntityDefinitions...``
#: for the diff; table and column creation via the Web API table-definition
#: surface"). The attribute metadata type names are the ones the cited
#: alternate-keys page enumerates, and the key create is
#: ``CreateEntityKey`` carrying an ``EntityKeyMetadata`` body.
DATAVERSE = VendorAdapter(
    vendor="dataverse",
    label="Dataverse",
    schema_read=(
        "/api/data/v9.2/$metadata",
        "/api/data/v9.2/EntityDefinitions",
    ),
    object_create="/api/data/v9.2/EntityDefinitions",
    property_create="/api/data/v9.2/EntityDefinitions({object_type})/Attributes",
    object_id_style="schema_name",
    required_property_fields=("schemaName", "AttributeType", "AttributeDisplayName"),
    derivable_fields=frozenset(
        {"schemaName", "AttributeDisplayName", "AttributeType", "AttributeFormat"}
    ),
    type_map={
        "string": {"AttributeType": "StringAttributeMetadata", "AttributeFormat": "String"},
        "number": {"AttributeType": "DecimalAttributeMetadata"},
        "bool": {"AttributeType": "BooleanAttributeMetadata"},
        "datetime": {"AttributeType": "DateTimeAttributeMetadata"},
        "enumeration": {"AttributeType": "PicklistAttributeMetadata"},
    },
    key_create="/api/data/v9.2/CreateEntityKey",
    key_reactivate="/api/data/v9.2/ReactivateEntityKey",
    background_index=True,
    object_label_required=False,
    label_field="AttributeDisplayName",
    name_field="schemaName",
)

ADAPTERS: dict[str, VendorAdapter] = {HUBSPOT.vendor: HUBSPOT, DATAVERSE.vendor: DATAVERSE}

#: The names this product uses for a vendor-neutral type, carried into the
#: request body under each vendor's own key. Declared once, in the vocabulary,
#: and every adapter is expected to cover all of it: a type with no mapping for a
#: vendor is a per-property skip, never a guess.
NEUTRAL_TYPES: tuple[str, ...] = PROPERTY_TYPES


def adapter_for(vendor: str) -> VendorAdapter:
    """The adapter for a vendor, or a refusal naming the research's gap."""
    try:
        found = ADAPTERS[str(vendor or "").strip().lower()]
    except KeyError:
        found = None
    if found is None:
        raise UnsupportedVendor(
            f"{vendor!r} is not a vendor this workflow can provision: "
            f"{UNSUPPORTED_VENDORS.get(vendor, 'No researched provisioning behaviour is recorded for it.')} "
            "See docs/research/digital-sales-room-workflows/wf/WF-036.md — a vendor with no "
            "cited provisioning page has no adapter, on purpose."
        )
    found.check()
    return found


def describe_adapter(adapter: VendorAdapter) -> dict[str, Any]:
    """One vendor's whole surface, for the vocabulary endpoint."""
    return {
        "vendor": adapter.vendor,
        "label": adapter.label,
        "schema_read": list(adapter.schema_read),
        "object_create": adapter.object_create,
        "property_create": adapter.property_create,
        "object_id_style": adapter.object_id_style,
        "object_label_required": adapter.object_label_required,
        "label_field": adapter.label_field,
        "name_field": adapter.name_field,
        "required_property_fields": list(adapter.required_property_fields),
        "derivable_fields": sorted(adapter.derivable_fields),
        "type_map": {key: dict(value) for key, value in sorted(adapter.type_map.items())},
        "unmapped_types": sorted(set(NEUTRAL_TYPES) - set(adapter.type_map)),
        "supports_alternate_key": adapter.key_create is not None,
        "key_create": adapter.key_create,
        "key_reactivate": adapter.key_reactivate,
        "background_index": adapter.background_index,
    }
