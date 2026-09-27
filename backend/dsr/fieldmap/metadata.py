"""The CRM's property and type metadata, normalised into one shape.

The research's data flow ends here: "target CRM property names validated against
live CRM metadata (``/crm/properties/…``, ``EntityDefinitions``, Salesforce field
names)". Three vendors, three document shapes, and the validator needs one shape.

A :class:`Property` is that shape. It carries the provider's own ``value_type``
(the HubSpot ``type``, the Dataverse attribute metadata class name, the Salesforce
field type) and, separately, the presentation (``field_type``, HubSpot's
``fieldType``). Keeping the two apart is the research's own distinction - "The
``type`` value determines the type of the property... The ``fieldType`` property
determines how the property will appear" - so a type check never reads the wrong
one.

Enumeration options are carried as :class:`Option`, whose ``value`` is the
**internal name** and whose ``label`` is what a human sees. That split is the
whole basis of the ``unsupported_option`` finding: "you must use internal names to
set values. The internal name stays the same even if you've changed a default
value's label." A mapping that sends a label is therefore detectable *even when
the label and the internal name happen to be spelled the same way*, because the
one the admin typed is recorded as a label and the one the CRM would accept is
recorded as a value.

What this module does **not** do is fetch. The bearer token and the authenticated
request executor belong to WF-034, and nothing in this workflow opens a socket. A
connector reads the endpoints in
:data:`~dsr.fieldmap.vocabulary.METADATA_ENDPOINTS` and hands the document here;
:mod:`dsr.fieldmap.mappings` records it, and every read of this module's
descriptors therefore names the document it came from - so a reviewer can tell
HubSpot metadata from a hand-written fixture without asking.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import get_close_matches
from typing import Any, Iterable, Mapping, Sequence

from dsr.fieldmap.errors import InvalidMapping
from dsr.fieldmap.vocabulary import (
    DATAV_ATTRIBUTE_TYPES,
    DATAV_ELIGIBLE_KEY_TYPES,
    DATAV_OPTION_SETS,
    HUBSPOT_FIELD_TYPES_BY_TYPE,
    UNIQUE_KEY_LIMIT,
    attribute_type_of,
    normalise_provider,
)


@dataclass(frozen=True)
class Option:
    """One enumeration option: the internal name, and the label beside it."""

    value: str
    label: str
    hidden: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"value": self.value, "label": self.label, "hidden": self.hidden}


@dataclass(frozen=True)
class Property:
    """One CRM property, normalised across the three documented vendors."""

    name: str
    label: str
    #: The provider's data type. HubSpot ``type``; the Dataverse attribute metadata
    #: class name; the Salesforce field type.
    value_type: str
    #: The provider's presentation type - HubSpot ``fieldType`` only. Empty
    #: elsewhere, because only HubSpot has the second axis.
    field_type: str = ""
    #: HubSpot ``groupName``, or the group an attribute belongs to. Empty elsewhere.
    group: str = ""
    #: The provider already enforces uniqueness on this property.
    unique: bool = False
    #: The option set, for an enumeration or picklist. Internal names in ``value``.
    options: tuple[Option, ...] = ()
    #: Dataverse: this attribute is already in an alternate key on the table.
    keyed: bool = False
    #: The table's alternate key that claims it, for a keyed attribute.
    key_name: str = ""

    @property
    def is_enumeration(self) -> bool:
        """Whether the property carries an option set, by shape or by name.

        By shape first, because that is what the metadata says; by name second,
        because Dataverse ``State``/``Status`` attributes do not always carry their
        option set in the query the research cites. A name-based guess that
        disagreed with a carried option set would be worse than useless - it would
        refuse a value the CRM accepts.
        """
        if self.options:
            return True
        return self.value_type in ("enumeration", "PicklistAttributeMetadata", "Picklist", "MultiPicklist")

    @property
    def internal_values(self) -> tuple[str, ...]:
        """The internal option values, which are the only ones a write may carry."""
        return tuple(option.value for option in self.options)

    def option_for_label(self, label: str) -> Option | None:
        """The option whose *label* is this, ignoring case and surrounding space.

        Only used to explain a refusal. Finding it is how a finding can say "you
        wrote the label `Won`, the internal name is `1`" instead of "unknown value".
        """
        needle = str(label or "").strip().casefold()
        for option in self.options:
            if option.label.strip().casefold() == needle:
                return option
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "value_type": self.value_type,
            "field_type": self.field_type,
            "group": self.group,
            "unique": self.unique,
            "options": [option.to_dict() for option in self.options],
            "keyed": self.keyed,
            "key_name": self.key_name,
        }


@dataclass(frozen=True)
class Metadata:
    """One connection's read of one CRM object's property metadata."""

    provider: str
    crm_object: str
    properties: tuple[Property, ...]
    #: Dataverse only: the table's alternate keys, each a tuple of logical names,
    #: as ``Keys($select=KeyAttributes)`` returns them.
    keys: tuple[tuple[str, ...], ...] = ()
    #: The document this was read from, and the endpoint that produced it. A
    #: recorded read always carries both; a fixture a test writes carries
    #: ``"fixture"`` and says so.
    document: str = ""
    fetched_at: str = ""
    #: Free-form notes from the normaliser: what it defaulted, what it could not
    #: read, and which parts are unsourced.
    notes: tuple[str, ...] = ()

    # -- lookups ------------------------------------------------------------ #

    def lookup(self, name: str) -> Property | None:
        """The property with this exact name, or ``None``.

        Exact on purpose. A case-insensitive match would let a mapping written
        against ``Dsr_Row_Id`` validate against ``dsr_row_id`` and then fail in the
        CRM, and the whole point of the research's step 5 is that the mismatch is
        caught *here*. :meth:`suggest` gives the near miss instead of adopting it.

        Bound to ``Metadata.property`` after the class body: a method called
        ``property`` shadows the builtin decorator for every member declared after
        it, so it cannot carry that name inside the class. See the note at the foot
        of this module.
        """
        needle = str(name or "")
        for prop in self.properties:
            if prop.name == needle:
                return prop
        return None

    def suggest(self, name: str, limit: int = 3) -> tuple[str, ...]:
        """Property names close to a misspelling, for an ``unknown_property`` finding."""
        return tuple(get_close_matches(str(name or ""), [p.name for p in self.properties], n=limit, cutoff=0.6))

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(prop.name for prop in self.properties)

    @property
    def groups(self) -> tuple[str, ...]:
        """Every property group the metadata names, sorted.

        HubSpot files every property under a ``groupName``, so the set of groups
        the object actually has is derivable from its properties - which is what
        lets a create request be refused for naming a group that does not exist.
        """
        return tuple(sorted({prop.group for prop in self.properties if prop.group}))

    @property
    def unique_property_count(self) -> int:
        """How many properties the CRM already enforces uniqueness on.

        HubSpot's ``hasUniqueValue`` per property. Dataverse has no per-attribute
        flag - a column is keyed, or it is not - so a column already in a key counts
        too, and a column in a key is not double-counted when it is also flagged.
        """
        return sum(1 for prop in self.properties if prop.unique or prop.keyed)

    @property
    def key_count(self) -> int:
        """How many alternate keys the table already has. Dataverse only."""
        return len(self.keys)

    @property
    def unique_key_usage(self) -> int:
        """The number the ten-key ceiling is measured against, per provider.

        HubSpot counts unique properties; Dataverse counts alternate key table
        definitions. The research states both ceilings as ten, and they are not the
        same number for Dataverse - a ten-column key is one definition - so the
        counter follows each vendor's own unit rather than pretending they agree.
        """
        return self.key_count if self.provider == "dataverse" else self.unique_property_count

    def keyed_properties(self) -> tuple[str, ...]:
        """Every property already in a key, from either the flags or the key list."""
        found = {prop.name for prop in self.properties if prop.keyed}
        for key in self.keys:
            found.update(key)
        return tuple(sorted(found))

    def type_is_key_eligible(self, value_type: str) -> bool:
        """Whether a Dataverse attribute of this type may be in an alternate key.

        The researched list, and only the researched list: "Only include columns of
        the following types in alternate key table definitions: DecimalAttributeMetadata
        … StringAttributeMetadata … DateTimeAttributeMetadata … LookupAttributeMetadata …
        PicklistAttributeMetadata". A ``BooleanAttributeMetadata`` or a
        ``UniqueIdentifierAttributeMetadata`` is refused even though both exist on a
        real table, because the research does not put them in the list.

        False for every other provider, and that is the honest answer rather than a
        guard: this is a rule about Dataverse attribute metadata classes, so a
        HubSpot or Salesforce property is not eligible for a *Dataverse* key under
        any spelling. The callers check the provider first regardless.
        """
        if self.provider != "dataverse":
            return False
        return attribute_type_of(self.provider, value_type) in DATAV_ELIGIBLE_KEY_TYPES

    def field_type_fits(self, prop: Property) -> bool:
        """Whether a HubSpot property's ``fieldType`` agrees with its ``type``.

        The cited rule is that the two mean different things. This is where that
        turns into a check, and it is what a HubSpot 400 on create is made of: a
        ``fieldType`` that is not coherent with the ``type`` it is filed under.
        """
        if self.provider != "hubspot" or not prop.field_type:
            return True
        allowed = HUBSPOT_FIELD_TYPES_BY_TYPE.get(prop.value_type)
        if allowed is None:
            return True
        return prop.field_type in allowed

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "crm_object": self.crm_object,
            "document": self.document,
            "fetched_at": self.fetched_at,
            "notes": list(self.notes),
            "property_count": len(self.properties),
            "properties": [prop.to_dict() for prop in self.properties],
            "groups": list(self.groups),
            "keys": [list(key) for key in self.keys],
            "key_count": self.key_count,
            "unique_property_count": self.unique_property_count,
            "unique_key_usage": self.unique_key_usage,
            "unique_key_limit": UNIQUE_KEY_LIMIT,
            "keyed_properties": list(self.keyed_properties()),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Metadata":
        """Rebuild from the stored record, so a read never re-normalises."""
        return cls(
            provider=str(payload.get("provider") or ""),
            crm_object=str(payload.get("crm_object") or ""),
            properties=tuple(
                Property(
                    name=str(prop.get("name") or ""),
                    label=str(prop.get("label") or ""),
                    value_type=str(prop.get("value_type") or ""),
                    field_type=str(prop.get("field_type") or ""),
                    group=str(prop.get("group") or ""),
                    unique=bool(prop.get("unique")),
                    options=tuple(
                        Option(
                            value=str(option.get("value") or ""),
                            label=str(option.get("label") or option.get("value") or ""),
                            hidden=bool(option.get("hidden")),
                        )
                        for option in (prop.get("options") or [])
                        if isinstance(option, Mapping)
                    ),
                    keyed=bool(prop.get("keyed")),
                    key_name=str(prop.get("key_name") or ""),
                )
                for prop in (payload.get("properties") or [])
                if isinstance(prop, Mapping)
            ),
            keys=tuple(tuple(str(name) for name in key) for key in (payload.get("keys") or [])),
            document=str(payload.get("document") or ""),
            fetched_at=str(payload.get("fetched_at") or ""),
            notes=tuple(str(note) for note in (payload.get("notes") or [])),
        )


#: ``metadata.property("dsr_row_id")`` is how the domain reads best - a metadata
#: object holds *properties*, and asking it for one is not a property access. It
#: cannot be a method named ``property`` inside the class, because that name is the
#: builtin decorator from the point it is bound onwards, and every ``@property``
#: declared after it would become a bound method instead of a descriptor. Binding
#: it here keeps the call sites honest and leaves the class body working.
Metadata.property = Metadata.lookup  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- #
# Normalisers
# --------------------------------------------------------------------------- #


def _options_from(raw: Any) -> tuple[Option, ...]:
    """Options from any of the three shapes, with the label defaulting to the value.

    Defaulting the label to the internal name matters: a property whose options
    carry no labels is still a property whose *values* must be internal names, and
    the finding that says so has to be able to quote something.
    """
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return ()
    options: list[Option] = []
    for entry in raw:
        if isinstance(entry, Mapping):
            value = entry.get("value")
            if value in (None, ""):
                # A Dataverse option set is often published as a bare list of
                # labels, in which case the label *is* the internal value.
                value = entry.get("label")
            if value in (None, ""):
                continue
            options.append(
                Option(
                    value=str(value),
                    label=str(entry.get("label") if entry.get("label") not in (None, "") else value),
                    hidden=bool(entry.get("hidden")),
                )
            )
        elif entry not in (None, ""):
            options.append(Option(value=str(entry), label=str(entry)))
    return tuple(options)


def _normalise_hubspot(document: Mapping[str, Any], crm_object: str) -> Metadata:
    """``GET /crm/properties/2026-09/{object}``: ``{"results": [...]}``.

    ``hasUniqueValue`` is the researched unique-ID flag - "In your request body, for
    the ``hasUniqueValue`` field, set the value to ``true``" - so the field read
    here is the same field the create request sets.
    """
    results = document.get("results")
    if not isinstance(results, Sequence) or isinstance(results, (str, bytes)):
        raise InvalidMapping(
            "a HubSpot property read has a `results` array; "
            "GET /crm/properties/2026-09/{object} is the document this expects"
        )
    properties = tuple(
        Property(
            name=str(entry.get("name") or ""),
            label=str(entry.get("label") or entry.get("name") or ""),
            value_type=str(entry.get("type") or ""),
            field_type=str(entry.get("fieldType") or ""),
            group=str(entry.get("groupName") or ""),
            unique=bool(entry.get("hasUniqueValue")),
            options=_options_from(entry.get("options")),
        )
        for entry in results
        if isinstance(entry, Mapping) and entry.get("name")
    )
    notes = [
        "HubSpot read: `type` is the data type and `fieldType` the presentation, so a "
        "mapping is type-checked against `type` and never against `fieldType`.",
        "Enumerations are matched on the option's internal `value`; `label` is what a human reads.",
    ]
    return Metadata(
        provider="hubspot",
        crm_object=crm_object,
        properties=properties,
        document="GET /crm/properties/2026-09/{object}",
        notes=tuple(notes),
    )


def _normalise_dataverse(document: Mapping[str, Any], crm_object: str) -> Metadata:
    """``EntityDefinitions?$select=SchemaName&$expand=Keys($select=KeyAttributes)``.

    Three shapes are accepted, because three tools return this table's metadata in
    three ways and a deployment should not have to reshape a connector's payload
    before handing it over:

    * the Web API envelope - ``{"value": [{"SchemaName": ..., "Attributes": [...],
      "Keys": [{"KeyAttributes": [...]}]}]}``;
    * the same rows without the envelope - a bare list;
    * a CSDL-flavoured mapping - ``{"attributes": [...], "keys": [[...]]}``.

    ``AttributeType`` arrives as the CSDL short name (``String``), and the researched
    rule is written in the metadata class name (``StringAttributeMetadata``), so the
    short name is expanded through
    :data:`~dsr.fieldmap.vocabulary.DATAV_ATTRIBUTE_TYPES` and everything downstream
    speaks one spelling.
    """
    if isinstance(document, Mapping) and isinstance(document.get("attributes"), Sequence):
        attributes: Sequence[Any] = document.get("attributes") or []
        keys: tuple[tuple[str, ...], ...] = tuple(
            tuple(str(name) for name in key) for key in (document.get("keys") or [])
        )
        document_name = "CSDL $metadata"
    else:
        rows: Any
        if isinstance(document, Sequence) and not isinstance(document, (str, bytes, Mapping)):
            rows = document
        elif isinstance(document.get("value"), Sequence):
            rows = document["value"]
        else:
            raise InvalidMapping(
                "a Dataverse table-definition read has a `value` array of EntityDefinitions "
                "rows, each with `Attributes` and optionally `Keys`; "
                "GET /api/data/v9.2/EntityDefinitions?$select=SchemaName"
                "&$expand=Keys($select=KeyAttributes) is the document this expects"
            )
        chosen = next(
            (
                row
                for row in rows
                if isinstance(row, Mapping) and not (crm_object and str(row.get("SchemaName") or "") != crm_object)
            ),
            None,
        )
        if chosen is None:
            raise InvalidMapping(
                f"no EntityDefinitions row for {crm_object!r} in the document; "
                "a $select on SchemaName that does not include this table is a no-op, not a match"
            )
        attributes = chosen.get("Attributes") or chosen.get("attributes") or []
        keys = tuple(
            tuple(str(name) for name in (key.get("KeyAttributes") or key.get("key_attributes") or []))
            for key in (chosen.get("Keys") or chosen.get("keys") or [])
            if isinstance(key, Mapping)
        )
        document_name = "GET /api/data/v9.2/EntityDefinitions?$select=SchemaName&$expand=Keys($select=KeyAttributes)"

    keyed: dict[str, str] = {}
    for position, key in enumerate(keys):
        for name in key:
            keyed.setdefault(name, f"alt_key_{position + 1}")

    properties: list[Property] = []
    for entry in attributes:
        if not isinstance(entry, Mapping):
            continue
        name = str(entry.get("LogicalName") or entry.get("Name") or entry.get("SchemaName") or "")
        if not name:
            continue
        raw_type = str(entry.get("AttributeType") or entry.get("attribute_type") or "")
        value_type = attribute_type_of("dataverse", raw_type)
        options = _options_from(entry.get("OptionSet") or entry.get("options"))
        if not options and value_type in DATAV_OPTION_SETS:
            options = tuple(
                Option(value=option_value, label=option_label)
                for option_value, option_label in DATAV_OPTION_SETS[value_type]
            )
        properties.append(
            Property(
                name=name,
                label=str(entry.get("DisplayName") or entry.get("SchemaName") or name),
                value_type=value_type,
                group=str(entry.get("AttributeGroupName") or ""),
                unique=bool(entry.get("IsUnique") or entry.get("IsPrimaryId")),
                options=options,
                keyed=name in keyed,
                key_name=keyed.get(name, ""),
            )
        )
    notes = [
        "Dataverse read: `AttributeType` is expanded to its attribute metadata class name, which "
        "is how the research writes the alternate-key rule.",
        "Alternate keys come from Keys($select=KeyAttributes); a column already in a key is a key, "
        "whether or not the table also flags it unique.",
    ]
    return Metadata(
        provider="dataverse",
        crm_object=crm_object,
        properties=tuple(properties),
        keys=keys,
        document=document_name,
        notes=tuple(notes),
    )


def _normalise_salesforce(document: Mapping[str, Any], crm_object: str) -> Metadata:
    """Salesforce object metadata: ``{"fields": [...]}``.

    **Unsourced.** The research is explicit that "Salesforce's *Object Reference*
    and *Metadata API* field pages are client-rendered and unreadable via the read
    path", so this shape is this build's reading of a well-known payload and the
    note says so. The consequence is deliberately limited: a Salesforce connection
    can be mapped, validated and previewed, but nothing here creates an external-ID
    field, and :func:`~dsr.fieldmap.sync_key.build_create_request` refuses to
    pretend otherwise.
    """
    fields = document.get("fields")
    if not isinstance(fields, Sequence) or isinstance(fields, (str, bytes)):
        raise InvalidMapping(
            "a Salesforce object read has a `fields` array; the Object Reference field list is "
            "the document this expects"
        )
    properties = tuple(
        Property(
            name=str(entry.get("name") or ""),
            label=str(entry.get("label") or entry.get("name") or ""),
            value_type=str(entry.get("type") or ""),
            unique=bool(entry.get("unique")),
            options=_options_from(entry.get("picklistValues") or entry.get("options")),
        )
        for entry in fields
        if isinstance(entry, Mapping) and entry.get("name")
    )
    return Metadata(
        provider="salesforce",
        crm_object=crm_object,
        properties=properties,
        document="Salesforce object field list (unsourced: client-rendered in the cited docs)",
        notes=(
            "Salesforce metadata is read but not created: the research records that the Object "
            "Reference and Metadata API field pages could not be read, so no external-ID field "
            "request is emitted for this provider.",
        ),
    )


_NORMALISERS = {
    "hubspot": _normalise_hubspot,
    "dataverse": _normalise_dataverse,
    "salesforce": _normalise_salesforce,
}


def normalise(provider: Any, document: Mapping[str, Any], crm_object: str = "") -> Metadata:
    """Read a vendor's document into a :class:`Metadata`.

    Raises :class:`~dsr.fieldmap.errors.InvalidMapping` for an unknown provider or
    a document in no shape this build recognises. The refusal is deliberate and
    names the expected shape: recording metadata that was not really read would let
    validation pass against a fixture and then fail in the CRM, which is the exact
    failure the research's step 5 exists to prevent.
    """
    name = normalise_provider(provider)
    normaliser = _NORMALISERS.get(name)
    if normaliser is None:
        raise InvalidMapping(
            f"{provider!r} is not a documented CRM provider; "
            f"choose one of {', '.join(sorted(_NORMALISERS))}"
        )
    # Dataverse is the one provider whose read can arrive unwrapped: a tool that
    # queries `EntityDefinitions` and hands over the array it got back should not
    # have to reshape it into the Web API envelope first.
    a_bare_list_is_allowed = name == "dataverse" and isinstance(document, Sequence) and not isinstance(
        document, (str, bytes)
    )
    if not isinstance(document, Mapping) and not a_bare_list_is_allowed:
        raise InvalidMapping("a metadata document must be a JSON object")
    return normaliser(document, str(crm_object or ""))


def fixture(
    provider: str,
    crm_object: str,
    properties: Iterable[Mapping[str, Any]],
    keys: Iterable[Sequence[str]] = (),
) -> Metadata:
    """Build a :class:`Metadata` directly, for a seeder or a test.

    Marks itself ``document: "fixture"`` so a record built this way is visibly not
    a CRM read, which is the one thing the audit log is for.
    """
    return Metadata(
        provider=normalise_provider(provider),
        crm_object=str(crm_object),
        properties=tuple(
            Property(
                name=str(entry.get("name") or ""),
                label=str(entry.get("label") or entry.get("name") or ""),
                value_type=str(entry.get("value_type") or ""),
                field_type=str(entry.get("field_type") or ""),
                group=str(entry.get("group") or ""),
                unique=bool(entry.get("unique")),
                options=_options_from(entry.get("options")),
                keyed=bool(entry.get("keyed")),
                key_name=str(entry.get("key_name") or ""),
            )
            for entry in properties
            if entry.get("name")
        ),
        keys=tuple(tuple(str(name) for name in key) for key in keys),
        document="fixture",
        notes=("Not a CRM read: this metadata was written by a seeder or a test, not by a connector.",),
    )
