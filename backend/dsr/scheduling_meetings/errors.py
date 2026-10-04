"""One error hierarchy for the MAP e-signature package.

Every refusal this package makes is something the caller sent, so the types share
a base and the feature module registers a single handler for the whole hierarchy.
Anything that is *not* a :class:`MapError` is a bug in this package and must
propagate.

``code`` and ``status`` ride on the exception rather than being decided inside the
handler, because a malformed body and an event that conflicts with live state are
both this package's errors, and a handler answering 400 for both would be lying
about the second. FastAPI only accepts exception handlers on the app object, so
the feature module exports this mapping as ``EXCEPTION_HANDLERS``; two features
may not map the same type, which is why the whole hierarchy hangs off one base.

The split that matters most is **refusal** against **report**. An event that
arrived, proved itself, and turned out to name a plan this room cannot place is a
fact about the event, and it is recorded in the log with a reason rather than
thrown away. But an event that failed its secret check writes nothing at all,
because the one guarantee an unauthenticated caller must not have is a way to make
this product write rows.
"""

from __future__ import annotations


class MapError(ValueError):
    """A mutual action plan cannot be configured, sent, or advanced as asked.

    422 rather than 400 for the base: every refusal here is a well-formed request
    that cannot be honoured - a role outside the researched five, a coordinate
    past the edge of the page, an event name this build does not serve. 400 says
    the request was malformed, and telling a seller their request was malformed
    when they simply used a role the research does not list sends them looking in
    the wrong place. The subclasses that genuinely conflict with live state
    override this with 409.
    """

    code = "map_error"
    status = 422


# --------------------------------------------------------------------------- #
# Configuring the template and the recipients
# --------------------------------------------------------------------------- #


class UnknownRecipientRole(MapError):
    """A recipient carries a role the research does not list.

    "Roles include ``SIGNER``, **``APPROVER``**, ``CC``, ``VIEWER``, ``ASSISTANT``."
    Five, and this build serves exactly those. A sixth would be stored looking
    armed, and a page would have nothing to render it as.
    """

    code = "unknown_recipient_role"


class MalformedRecipient(MapError):
    """A recipient the researched create call could not have produced.

    ``recipients[]`` needs ``email``, ``name`` and ``role`` - and this package
    additionally requires the address to look like one, because the research's
    whole invitation path is an email a recipient opens.
    """

    code = "malformed_recipient"


class MissingRecipients(MapError):
    """A plan with nobody to sign it.

    Not this build's opinion: step 2 requires ``recipients[]``, and a mutual action
    plan with no signing role is not mutual. A plan of only CCs and VIEWERs is
    refused for the same reason.
    """

    code = "plan_needs_a_signing_recipient"


class MalformedField(MapError):
    """A field the vendor's create call could not place on a page.

    The coordinates are percentages - "``positionX`` | Horizontal position from
    left edge (0 = left, 100 = right)" - and ``identifier`` names a file index
    "0 for first file, 1 for second, etc.". A field outside those bounds, or with
    no type, is not a field.
    """

    code = "malformed_field"


class UnknownFieldType(MapError):
    """A field type outside the researched set."""

    code = "unknown_field_type"


class InvalidExternalId(MapError):
    """A join key this build cannot derive.

    The research says ``externalId`` is the join key and does not say what format
    it takes here, so the format is derived and recorded in
    :mod:`dsr.scheduling_meetings.inferences`. A caller may override it, and one
    that cannot be read as an identifier is refused rather than stored.
    """

    code = "invalid_external_id"


# --------------------------------------------------------------------------- #
# Prerequisites and state conflicts
# --------------------------------------------------------------------------- #


class NoSuchPlan(MapError):
    """A plan this room has not created, or has deleted.

    Soft-deleted rather than destroyed, so the events that named it still resolve
    - the same reason ``dsr.db.audited`` keeps the row behind a soft delete.
    """

    code = "plan_not_found"
    status = 404


class NoSuchTemplate(MapError):
    """A template this room has not created."""

    code = "template_not_found"
    status = 404


class NoSuchRecipient(MapError):
    """A recipient who is not on the named plan.

    Soft-deleted rather than destroyed, so an event naming them still resolves.
    """

    code = "recipient_not_found"
    status = 404


class PlanStateConflict(MapError):
    """A lifecycle change the plan's current milestone does not allow.

    Distributing a plan that already went out, or cancelling one that is already
    approved. Both are 409: the request was well-formed and conflicts with state
    that already exists.
    """

    code = "plan_state_conflict"
    status = 409


class AlreadyDistributed(PlanStateConflict):
    """Distributing a plan that is not a draft any more."""

    code = "plan_already_distributed"


class TerminalPlan(PlanStateConflict):
    """Changing a plan that has already finished, one way or the other.

    An approved plan is not cancelled, and a cancelled plan is not distributed.
    The research has no transition out of either, so neither is invented.
    """

    code = "plan_already_terminal"


class SignersBlocked(PlanStateConflict):
    """A signer tried to sign while the approver had not approved.

    "APPROVER | Must approve before signers can sign." This is the role's gate, and
    it is 409 rather than 403: nothing about the caller is wrong. The plan simply
    is not ready, and the response says who still has to act.
    """

    code = "signers_blocked_pending_approval"
    status = 409


class NotAnApprover(MapError):
    """An approval attempt by somebody who does not hold the approver role.

    The same gate as :class:`SignersBlocked`, read from the other side: this
    refuses the *approver action* rather than the signer's wait. 403, because here
    the caller's role genuinely is wrong.
    """

    code = "recipient_is_not_an_approver"
    status = 403


# --------------------------------------------------------------------------- #
# Events
# --------------------------------------------------------------------------- #


class UnauthenticatedEvent(MapError):
    """The request did not prove it came from the vendor.

    401. And, unlike every other refusal here, **nothing is written**: not the
    event, not the recipient's status, not the milestone. A request that failed
    authentication is the one input to this product that has not been shown to be
    entitled to anything, and the row it would leave behind is indistinguishable
    from a real one to everyone who reads the log afterwards.
    """

    code = "event_unauthenticated"
    status = 401


class UnknownEventType(MapError):
    """An event name outside the researched list.

    The fourteen events the research enumerates are the fourteen this build
    accepts. An unknown name is refused rather than stored, because storing it
    would let a caller with the right secret write any string into the event log
    and have the page render it as an event.
    """

    code = "unknown_event_type"


class MalformedEvent(MapError):
    """A body that is not an event of the researched contract.

    The event name, and for an event that describes a recipient, which recipient.
    "DOCUMENT_SIGNED | Recipient signs document" names a recipient, so an event
    carrying none is not that event.
    """

    code = "malformed_event"


class DuplicateEvent(MapError):
    """This exact event was already taken.

    "Webhooks may be retried, so handle duplicate events." A retry is not an
    error the vendor did wrong, so it is answered 200 and reported as a duplicate
    rather than refused. The type exists so the engine can tell the two apart
    without the caller catching an exception to learn nothing happened.
    """

    code = "duplicate_event"
    status = 200


class EventBeforeDistribution(MapError):
    """An event arrived for a plan that has not gone out.

    The research's own order is create then distribute then events: "status
    ``DRAFT`` to ``PENDING``" happens at distribution, and "Buyer opens" can only
    follow it. An event claiming a signature on a draft is refused rather than
    believed, because believing it would approve a plan nobody was ever sent.
    """

    code = "event_before_distribution"
    status = 409


class UnresolvedPlan(MapError):
    """The event named a plan this room cannot place.

    "``externalId`` is the join key back to the deal room." An event with no usable
    external id, in a room with more than one plan, cannot be resolved - and that
    is a fact about the event worth keeping, so it is recorded with this reason
    rather than dropped.
    """

    code = "plan_unresolved"
    status = 409
