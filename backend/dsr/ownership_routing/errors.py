"""One error hierarchy for the ownership-routing package.

Every refusal this package makes about a *request* is a caller's mistake, so the
types share a base and the feature module registers a single handler for it.
Anything that is not an :class:`OwnershipError` is a bug and must propagate.

``code`` and ``status`` ride on the exception rather than being decided inside the
handler, because these refusals are not all the same kind of thing. A missing
``guestEmail`` on an Ownership link is a 400. A booking for a slot the route never
offered, or a route that has already been consumed, is a 409: the request was
well formed and what it conflicts with is the state of the system. A handler that
answered 400 for both would be lying about the second.

FastAPI only accepts exception handlers on the app object, so the feature module
exports this mapping as ``EXCEPTION_HANDLERS``; two features may not map the same
type, which is why the whole hierarchy hangs off one base class.
"""

from __future__ import annotations


class OwnershipError(ValueError):
    """An ownership-routing request cannot be honoured as written.

    A ``ValueError`` because every subclass is caused by something the caller
    sent. Nothing in this package raises for a fault of its own.
    """

    code = "ownership_error"
    status = 400


class LinkError(OwnershipError):
    """The scheduling link cannot be used, or cannot be declared as asked."""

    code = "ownership_link_invalid"


class LinkTypeNotSupported(LinkError):
    """The link's type is a researched one, but not the one this workflow routes.

    Chili Pepper publishes five link types and the research names all five. Four of
    them route by something other than the CRM owner - a person, a round robin, a
    group - and WF-053 is the owner-resolution workflow. Declaring a ``Personal``
    link here and quietly resolving it by CRM owner would make the link's type a
    lie, so it is refused and the message names the type that is supported.
    """

    code = "ownership_link_type_not_supported"


class GuestEmailRequired(LinkError):
    """An Ownership link was initialised without a ``guestEmail``.

    "For **Ownership** links, also pass ``guestEmail`` in the init call - it is
    required so Chili Piper can resolve the owner from your CRM." This is the one
    required field in the researched init payload, and it is required *because* the
    whole workflow keys off it: with no guest email there is nothing to look up.
    """

    code = "ownership_guest_email_required"


class IntervalError(LinkError):
    """The availability window is not a window.

    An interval that ends before it starts, a minimum-notice larger than the whole
    interval, a duration of zero minutes. Named separately from the rest because
    the remedy is a different field each time.
    """

    code = "ownership_interval_invalid"


class RoutingRuleError(OwnershipError):
    """The routing rules cannot be declared, or cannot fall through."""

    code = "ownership_rule_invalid"


class RulesDoNotFallThrough(RoutingRuleError):
    """A rule set that can end without naming a host.

    The Concierge flow this research sits in is built from ``Routing Rule`` and
    ``Catch All`` nodes, and a chain that can simply run out is a prospect who
    reaches the end of the scheduler and books nothing. Refusing the declaration is
    the fix; a runtime "no host matched" for a well-formed chain is impossible by
    construction, because the chain cannot exist without a catch-all.
    """

    code = "ownership_rules_no_catch_all"


class ForbiddenNodeOnOwnershipPath(OwnershipError):
    """An ``Update Ownership`` or ``Assign To`` node was put on an Ownership path.

    The research states the guardrail as a warning in a help article rather than as
    a rule it explains: "you should not use this node in **Ownership** paths. If you
    have any Assign To nodes in Ownership-related paths, this could prevent your
    Router from being published." "Could prevent" is the part worth honouring - a
    router that will not publish is a router nobody discovers the failure of until
    they try to ship it, so the refusal happens where the node is added.
    """

    code = "ownership_forbidden_node_on_path"


class PrerequisiteError(OwnershipError):
    """A prerequisite the researched flow assumes is not in place.

    409 rather than 400: the request is well formed, and what it lacks is a
    relationship the rest of the system owns - a CRM record with no owner, an owner
    whose calendar is not connected, a link nobody declared.
    """

    code = "ownership_prerequisite_missing"
    status = 409


class NoOwnerResolved(PrerequisiteError):
    """Nothing owned the guest's CRM record, and there is no catch-all to fall to."""

    code = "ownership_no_owner_resolved"


class OwnerUnknown(NoOwnerResolved):
    """The record names an owner, but no such rep is in the workspace.

    Distinct from :class:`NoOwnerResolved` because the remedy differs: nothing
    owned the record is a CRM fact, whereas an owner id no rep answers to is a
    workspace that has not caught up with its CRM.
    """

    code = "ownership_owner_unknown"


class AmbiguousOwner(PrerequisiteError):
    """One CRM owner id answers to more than one rep in this workspace.

    The researched flow routes to "the owner of the guest's CRM record", singular,
    and the whole workflow is built on that owner being one person with one
    calendar. Two reps answering to one owner id means there is no single calendar
    to read, so the refusal is a conflict with the workspace's state rather than a
    malformed request - 409, and it names the id so the fix is obvious.
    """

    code = "ownership_owner_ambiguous"


class CalendarNotConnected(PrerequisiteError):
    """The resolved owner has no connected calendar to read availability from.

    The researched data flow is "CRM lookup for owner id -> owner's calendar
    availability". The owner is resolved; the calendar is what cannot be read, and
    "Google/Outlook calendar of the resolved owner" is named as a data source
    precisely because a rep without one has no availability at all.
    """

    code = "ownership_calendar_not_connected"


class SessionError(OwnershipError):
    """The routing session named by the second call is not usable.

    409 for the same reason as the rest of the hierarchy: the request is fine, the
    state is what conflicts.
    """

    code = "ownership_session_conflict"
    status = 409


class RouteNotFound(SessionError):
    """No routing session with that id exists."""

    code = "ownership_route_not_found"
    status = 404


class RouteConsumed(SessionError):
    """The routing session has already been booked.

    ``init-simple`` hands back a ``routingId`` "for the second call", and the second
    call books a specific ``startTime``. A second booking on the same session would
    put two prospects in one slot on one owner's calendar, so a consumed session
    refuses rather than re-answering.
    """

    code = "ownership_route_consumed"


class SlotNotOffered(SessionError):
    """The requested ``startTime`` is not one the route offered.

    The init call returns "Available ``startTimes`` plus a ``routingId``", and the
    schedule call names one of them. A time that was never offered is not merely out
    of stock: it is a time the owner's calendar was never shown to have free.
    """

    code = "ownership_slot_not_offered"


class GuestMismatch(SessionError):
    """The ``guestEmail`` on the second call is not the one the session was opened for.

    The researched schedule-simple payload carries ``guestEmail`` again, and it is
    carried so the booking is attributable. A different address on the second call
    is not the prospect who chose the slot.
    """

    code = "ownership_guest_mismatch"


class BookingStateError(OwnershipError):
    """A booking is not in a state where this operation means anything.

    Cancelling a booking that is already cancelled, restoring one that was never
    cancelled. 409 for the same reason as the rest of the hierarchy.
    """

    code = "ownership_booking_state_conflict"
    status = 409
