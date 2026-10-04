"""One error hierarchy for the inbound CRM read package.

Every refusal this package makes is a caller's mistake or a conflict with state
that already exists, so the types share a base and the feature module registers
a single handler for it. Anything that is *not* a :class:`CrmIntegrationError`
is a bug and must propagate.

``code`` and ``status`` ride on the exception rather than being decided inside
the handler, for the reason :mod:`dsr.change_stream.errors` does it: asking for
a vendor whose limit the room has not acknowledged and asking to read an object
the field map does not map are both this package's errors, and only one of them
is a malformed request. FastAPI only accepts exception handlers on the app
object, so the feature module exports this mapping as ``EXCEPTION_HANDLERS``; two
features may not map the same type, which is why the whole hierarchy hangs off
one base class.
"""

from __future__ import annotations


class CrmIntegrationError(ValueError):
    """An inbound CRM read cannot be honoured as written.

    A ``ValueError`` because every subclass is caused by something the caller
    sent, or by a conflict between that and a state that already exists.
    Nothing in this package raises for a fault of its own.
    """

    code = "crm_integration_error"
    status = 400


# --------------------------------------------------------------------------- #
# The identity the read is scoped to
# --------------------------------------------------------------------------- #


class IdentityError(CrmIntegrationError):
    """A buyer's CRM identity cannot be described as asked."""

    code = "crm_identity_error"


class UnknownIdentity(IdentityError):
    """No identity record matches that id.

    404 rather than 409: the row is not there at all, which is different from
    being there and refusing the change.
    """

    code = "crm_identity_not_found"
    status = 404


class NoCrmIdentity(IdentityError):
    """The room has no CRM identity for this buyer.

    404, and the one refusal in this package that is not the caller's fault.
    "the room still works when a seller authors it without CRM context" is the
    research's own summary of the workflow, so a buyer the seller never mapped
    is a state the room must carry rather than an error it invented. The panel
    route answers 200 with a reason; this type exists for the paths that cannot
    render a panel without an identity at all.
    """

    code = "no_crm_identity"
    status = 404


class DuplicateIdentity(IdentityError):
    """Two identity records for one buyer in one room.

    The read is scoped by ``(room_id, buyer_email)``, so two records would give
    one panel two answers and the cache two entries that disagree.
    """

    code = "crm_identity_already_registered"
    status = 409


class MissingBuyerEmail(IdentityError):
    """The identity carries no buyer email.

    400 rather than 404, and the distinction is the point: a buyer email is the
    room-scoped key the read is filtered by, so an identity without one is a
    request this API cannot fulfil. Saying "not found" would send the caller
    looking for a row that does not exist instead of for the field they left out.
    """

    code = "buyer_email_required"


class AmbiguousIdentity(IdentityError):
    """The room has several buyers and the caller named none of them.

    409, because the request is well formed and what it conflicts with is the
    room's own state. The message names the count and the route that resolves it,
    because guessing which buyer a pull was for would put one buyer's deal panel
    on another's room - the one mistake this product exists to prevent.
    """

    code = "ambiguous_crm_identity"
    status = 409


class UnknownSystem(IdentityError):
    """No vendor in the researched set answers to that name."""

    code = "unknown_crm_system"


# --------------------------------------------------------------------------- #
# The field map and the read set
# --------------------------------------------------------------------------- #


class FieldMapError(CrmIntegrationError):
    """A field map cannot produce a read set."""

    code = "field_map_error"


class UnknownObject(FieldMapError):
    """No researched object answers to that name.

    404 rather than 400: the object is not a thing this workflow reads, which
    is different from a request that is malformed.
    """

    code = "unknown_crm_object"
    status = 404


class FieldNotMapped(FieldMapError):
    """The panel asked for a field the field map does not carry.

    The extensibility claim is one-directional: a field the deployment *adds* to
    the map reaches the panel with no API change, and a field the panel wants
    that the map does not carry is a gap the operator has to close. The message
    names the field and the object, because that is what makes it actionable.
    """

    code = "field_not_mapped"


class EmptyReadSet(FieldMapError):
    """The field map selects no column at all.

    A read with no columns returns nothing, and returning nothing with a 200 is
    indistinguishable from a room whose CRM has no deal. Refused instead.
    """

    code = "empty_read_set"


# --------------------------------------------------------------------------- #
# The vendor limits
# --------------------------------------------------------------------------- #


class VendorLimitError(CrmIntegrationError):
    """A read asks a vendor for more than that vendor returns.

    Every message here is the vendor's own number or its own wording, so an
    operator who hits one can search the vendor's documentation for it.
    """

    code = "vendor_limit_exceeded"


class BatchTooLarge(VendorLimitError):
    """More ids in one batch read than the vendor accepts.

    "You can retrieve up to 100 contacts in one request."
    """

    code = "batch_read_too_large"


class PageTooLarge(VendorLimitError):
    """More rows in one page than the vendor returns.

    Salesforce: "up to 2,000 records can be returned at a time in a synchronous
    request". Dataverse: "Without this limit, Dataverse returns up to 5,000
    standard table rows and 500 elastic table rows."
    """

    code = "page_size_exceeds_vendor_limit"


class TooManyConditions(VendorLimitError):
    """More filter conditions than the vendor accepts.

    "You can include up to 500 total conditions in a query. Otherwise, you see
    this error message: Number of conditions in query exceeded maximum limit."
    """

    code = "too_many_conditions"


class QueryTooLong(VendorLimitError):
    """The assembled query is longer than the vendor accepts.

    HubSpot: "A query can contain a maximum of 3,000 characters".
    """

    code = "query_too_long"


class UnknownCursor(CrmIntegrationError):
    """The continuation cursor does not belong to this read.

    The three vendors page three different ways, so a cursor is only meaningful
    with the query that produced it. Reusing one across reads would skip rows or
    repeat them.
    """

    code = "unknown_read_cursor"
    status = 409


# --------------------------------------------------------------------------- #
# Display labels
# --------------------------------------------------------------------------- #


class LabelError(CrmIntegrationError):
    """A display label cannot be produced as asked."""

    code = "display_label_error"


class UnknownOptionSet(LabelError):
    """No option-set record covers that field on that object."""

    code = "unknown_option_set"
    status = 404


class DuplicateOptionSet(LabelError):
    """Two option-set records for one field on one object.

    The fallback is consulted when the vendor cannot annotate, so two maps would
    make "Proposal sent" and "Qualification" both correct for one value.
    """

    code = "option_set_already_registered"
    status = 409


__all__ = [
    "BatchTooLarge",
    "CrmIntegrationError",
    "DuplicateIdentity",
    "DuplicateOptionSet",
    "EmptyReadSet",
    "FieldMapError",
    "FieldNotMapped",
    "IdentityError",
    "LabelError",
    "MissingBuyerEmail",
    "NoCrmIdentity",
    "AmbiguousIdentity",
    "PageTooLarge",
    "QueryTooLong",
    "TooManyConditions",
    "UnknownCursor",
    "UnknownIdentity",
    "UnknownObject",
    "UnknownOptionSet",
    "UnknownSystem",
    "VendorLimitError",
]
