"""This workflow's own error types.

Every one of them descends from :class:`FieldMapError`, which is a *domain* type
rather than :class:`Exception`. That matters for how they reach the wire: FastAPI
only accepts exception handlers on the app object, so the feature exports
``EXCEPTION_HANDLERS`` and the plugin host attaches them process-wide. A handler
registered for ``Exception`` would therefore intercept every unrelated failure in
the product. A handler registered for a type declared here cannot.

The refusals this package can make, and what each one means to the caller:

``InvalidMapping``
    A mapping row that cannot be recorded as asked for - a direction outside the
    researched ``in`` / ``out`` / ``both`` set, a row with no source field, a
    transform name that is not in the registry, a picklist transform with no
    table, or a metadata document no vendor shape can be read from. The caller's
    fault and fixable from the request alone, so 422.
``UnknownConnection`` / ``UnknownMapping`` / ``UnknownFieldRow``
    An id that does not resolve to a live record. 404, and deliberately three
    types rather than one so the body can name which of the three failed.
``MetadataUnavailable``
    Validation or preview asked for the CRM's property metadata and none has been
    recorded for that connection and object. 409, not 404: the route exists, the
    thing it needs does not, and the remedy is a read rather than a different URL.
``InvalidSyncKey``
    A sync key that cannot be pinned - no properties, a property the CRM metadata
    does not carry, a Dataverse attribute whose metadata type is not one of the
    five the research allows in an alternate key, or more than one property for a
    vendor whose unique-id property is a single column. 422.
``SyncKeyCapacity``
    Pinning this key would exceed the researched ceiling of ten unique ID
    properties per object (HubSpot) or ten alternate key table definitions
    (Dataverse). 409, because the request is well formed and conflicts with
    current state, and the body carries the counts so a reader can see how many
    slots are left.
``MappingNotValid``
    An attempt to activate a mapping that has never been validated, or whose last
    validation carries an error badge. This is the research's "before any data is
    written" made into a gate. 422, and the offending report is carried in the body
    so the client does not have to go and fetch it.
``UnsupportedSyncKeyRequest``
    A vendor request to create the unique property cannot be built from what the
    research cites - Salesforce's external-ID field, where the research records a
    sourcing gap, and any request missing a field the vendor requires. 422, and
    the message names the gap rather than sending the reader to a path that is not
    served.

``RecordNotFound`` is deliberately **not** re-declared here. The core app already
maps it to 404, and two handlers for one type is a collision the feature host
refuses to mount, which would take the whole feature offline.
"""

from __future__ import annotations

from typing import Any, Mapping


class FieldMapError(Exception):
    """Base of every refusal this workflow makes."""


class InvalidMapping(FieldMapError):
    """A mapping row or metadata document that cannot be recorded as asked for."""


class UnknownConnection(FieldMapError):
    """A connection id that does not resolve to a live record."""


class UnknownMapping(FieldMapError):
    """A mapping id that does not resolve to a live record."""


class UnknownFieldRow(FieldMapError):
    """A mapping-row id that does not resolve to a live record."""


class MetadataUnavailable(FieldMapError):
    """No CRM property metadata has been recorded for this connection and object.

    ``connection_id`` and ``crm_object`` are carried so the 409 body can name what
    to read, and ``source_document`` names the endpoint a connector should call -
    the answer is a metadata read, not a different URL.
    """

    def __init__(
        self,
        message: str,
        *,
        connection_id: str = "",
        crm_object: str = "",
        source_document: str = "",
        endpoints: tuple[Mapping[str, Any], ...] = (),
    ) -> None:
        super().__init__(message)
        self.connection_id = connection_id
        self.crm_object = crm_object
        self.source_document = source_document
        self.endpoints = endpoints


class InvalidSyncKey(FieldMapError):
    """A sync key that cannot be pinned on this object."""

    def __init__(self, message: str, *, findings: tuple[Mapping[str, Any], ...] = ()) -> None:
        super().__init__(message)
        self.findings = findings


class SyncKeyCapacity(FieldMapError):
    """Pinning this key would pass the researched ceiling of ten unique keys.

    ``used`` and ``limit`` are carried so a client can render "9 of 10 used"
    without counting anything itself, and ``providers`` records that the ceiling
    is ten for both documented vendors.
    """

    def __init__(self, message: str, *, used: int = 0, limit: int = 10, providers: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.used = used
        self.limit = limit
        self.providers = providers


class MappingNotValid(FieldMapError):
    """Activation attempted on a mapping that is not valid to activate.

    ``report`` is the validation that refused it, or ``None`` when the mapping has
    never been validated at all - the two are different situations and the client
    renders them differently.
    """

    def __init__(self, message: str, *, report: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.report = report


class UnsupportedSyncKeyRequest(FieldMapError):
    """A vendor request to create the unique property cannot be built.

    ``gap`` is the sourcing note that explains why, carried into the 422 body so a
    reader is told the reason rather than left to infer one.
    """

    def __init__(self, message: str, *, gap: str = "", provider: str = "") -> None:
        super().__init__(message)
        self.gap = gap
        self.provider = provider
