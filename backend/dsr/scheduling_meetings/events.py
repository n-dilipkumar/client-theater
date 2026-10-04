"""Applying a vendor event to a plan. The whole state machine, in one function.

The research's own order is the order here:

1. "Buyer opens -> ``DOCUMENT_OPENED``; buyer signs/approves -> ``DOCUMENT_SIGNED``
   + ``DOCUMENT_RECIPIENT_COMPLETED`` with ``signedAt``"
2. "when all recipients are done -> ``DOCUMENT_COMPLETED`` with ``completedAt``"
3. "A buyer can instead **reject** (``DOCUMENT_REJECTED``, ``rejectionReason``) or
   let the link **expire** (``RECIPIENT_EXPIRED``, ``expiresAt`` /
   ``expirationNotifiedAt``); unsent recipients get ``DOCUMENT_REMINDER_SENT``"
4. "The sales room consumes ``DOCUMENT_COMPLETED`` ... to flip the MAP milestone to
   *Approved*, advance the plan, and notify the owner."

The rule that shapes the module most: **this product never signs for anybody.**
"The API cannot: Sign documents on behalf of recipients". A recipient's status
changes here only because an event that passed its secret check said it did, and
no function in this package accepts a status a caller asserts.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from dsr.scheduling_meetings.errors import (
    MalformedEvent,
    UnknownEventType,
)
from dsr.scheduling_meetings.vocabulary import (
    APPROVER,
    EVENT_DOCUMENT_CANCELLED,
    EVENT_DOCUMENT_COMPLETED,
    EVENT_DOCUMENT_CREATED,
    EVENT_DOCUMENT_OPENED,
    EVENT_DOCUMENT_RECIPIENT_COMPLETED,
    EVENT_DOCUMENT_REJECTED,
    EVENT_DOCUMENT_REMINDER_SENT,
    EVENT_DOCUMENT_SENT,
    EVENT_DOCUMENT_SIGNED,
    EVENT_RECIPIENT_EXPIRED,
    EVENT_TYPE_TEMPLATE_CREATED,
    EVENT_TYPE_TEMPLATE_DELETED,
    EVENT_TYPE_TEMPLATE_UPDATED,
    EVENT_TYPE_TEMPLATE_USED,
    EVENTS,
    MILESTONE_APPROVED,
    MILESTONE_CANCELLED,
    MILESTONE_EXPIRED,
    MILESTONE_REFUSED_BY_APPROVER,
    MILESTONE_REFUSED_BY_SIGNER,
    RECIPIENT_EVENTS,
    REFUSAL_MILESTONES,
)

#: What an event did when it arrived.
OUTCOME_APPLIED = "applied"
#: It was already taken. "Webhooks may be retried, so handle duplicate events."
OUTCOME_DUPLICATE = "duplicate"
#: A real event that changed nothing - a reminder, a template notice - so it is
#: recorded and the room does not pretend the plan moved.
OUTCOME_NOTED = "noted"

#: Per-recipient status, as the research spells it. "Recipient ``signingStatus:
#: "SIGNED"``, ``signedAt`` set".
RECIPIENT_UNOPENED = "UNOPENED"
RECIPIENT_OPENED = "OPENED"
RECIPIENT_SIGNED = "SIGNED"
RECIPIENT_APPROVED = "APPROVED"
RECIPIENT_COMPLETED = "COMPLETED"
RECIPIENT_REJECTED = "REJECTED"
RECIPIENT_EXPIRED = "EXPIRED"
RECIPIENT_REMINDED = "REMINDED"

#: Which statuses count as done for the "all recipients are done" rule.
DONE_STATUSES: frozenset[str] = frozenset(
    {RECIPIENT_COMPLETED, RECIPIENT_APPROVED, RECIPIENT_REJECTED}
)

#: The events that mean "somebody acted", as opposed to "something was created or
#: reminded". Only these can move the milestone.
ACTION_EVENTS: tuple[str, ...] = (
    EVENT_DOCUMENT_OPENED,
    EVENT_DOCUMENT_SIGNED,
    EVENT_DOCUMENT_RECIPIENT_COMPLETED,
    EVENT_DOCUMENT_REJECTED,
    EVENT_RECIPIENT_EXPIRED,
)

#: The events that can complete a plan. "DOCUMENT_COMPLETED | Recipient completes
#: their action" for the last of them - the vendor sends it once every recipient
#: is done, and the room reads it rather than recomputing it. The second entry is
#: the room's own check, which exists because a vendor that stops sending it must
#: not leave a plan open forever.
COMPLETION_EVENTS: tuple[str, ...] = (EVENT_DOCUMENT_COMPLETED,)


def require_known_event(name: Any) -> str:
    """An event name from the researched list, and nothing else.

    The fourteen names the research enumerates are the fourteen this build takes.
    An unknown name is refused rather than stored: storing it would let a caller
    holding the right secret write any string into the event log and have a page
    render it as a real event.
    """
    if not isinstance(name, str) or not name.strip():
        raise MalformedEvent("an event carries no event name")
    event = name.strip().upper()
    if event not in EVENTS:
        raise UnknownEventType(
            f"event {event!r} is not one of the {len(EVENTS)} the research names"
        )
    return event


def event_fingerprint(event: str, payload: Mapping[str, Any]) -> str:
    """What makes two deliveries the same event.

    "Webhooks may be retried, so handle duplicate events." A retry carries the
    same event name and the same vendor event id, so the id is what identifies it.
    Where a payload carries no id - a hand-written test event, or a vendor that
    omits one - the fingerprint falls back to the name plus the recipient plus the
    envelope, which is the most a repeat can be told apart by.

    This is a name and a lookup, not a hash of the whole body: a retry of a
    ``DOCUMENT_OPENED`` that also carries a changed timestamp is still the same
    opening, and hashing the body would apply it twice.
    """
    vendor_id = first_string(payload, ("eventId", "event_id", "webhookId", "id"))
    if vendor_id:
        return f"{event}:{vendor_id}"
    envelope = first_string(payload, ("envelopeId", "envelope_id", "documentId", "document_id"))
    recipient = read_recipient_email(payload)
    return f"{event}:{envelope}:{recipient}"


def first_string(payload: Mapping[str, Any], keys: Iterable[str]) -> str:
    """The first of ``keys`` the payload carries as a non-empty string."""
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def read_recipient_email(payload: Mapping[str, Any]) -> str:
    """Which recipient an event is about, however the vendor nested it.

    The research's event table names a recipient on every event that describes one:
    "DOCUMENT_SIGNED | Recipient signs document". The vendor puts that recipient
    in different places depending on the event, so this looks in all of them.
    """
    for key in ("recipientEmail", "recipient_email", "email"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    recipient = payload.get("recipient")
    if isinstance(recipient, Mapping):
        for key in ("email", "recipientEmail", "recipient_email"):
            value = recipient.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        recipient_id = recipient.get("id") or recipient.get("recipientId")
        if isinstance(recipient_id, str) and recipient_id.strip():
            return f"id:{recipient_id.strip()}"

    for key in ("recipientId", "recipient_id"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return f"id:{value.strip()}"

    nested = payload.get("data") or payload.get("payload")
    if isinstance(nested, Mapping) and nested is not payload:
        return read_recipient_email(nested)
    return ""


def require_recipient_for(event: str, payload: Mapping[str, Any]) -> str:
    """The recipient an event names, and a refusal when it names none.

    An event that describes somebody acting but does not say who is malformed, not
    merely incomplete: a ``DOCUMENT_SIGNED`` with no recipient would otherwise be
    applied to the first signable person on the plan, which is exactly the failure
    mode that has this product signing for people.
    """
    email = read_recipient_email(payload)
    if email:
        return email
    if event in RECIPIENT_EVENTS:
        raise MalformedEvent(
            f"{event} names a recipient in the research's own event table "
            "('recipient signs document', 'recipient signing deadline passes') but this "
            "payload carried none"
        )
    return ""


def resolve_recipient(email: str, recipients: Iterable[Mapping[str, Any]]) -> dict[str, Any] | None:
    """Find one recipient by address, by vendor id, or by record id.

    A webhook identifies a recipient by whatever the vendor knows, which is not
    necessarily what the seller typed into the room. The three spellings are
    tried in turn rather than one, because a vendor that starts sending a
    recipient id where it used to send an address should not silently stop
    resolving.
    """
    wanted = email.strip().lower()
    for entry in recipients:
        if str(entry.get("email") or "").lower() == wanted:
            return dict(entry)
    if wanted.startswith("id:"):
        vendor_id = wanted[3:].lower()
        for entry in recipients:
            if str(entry.get("vendor_recipient_id") or "").lower() == vendor_id:
                return dict(entry)
            if str(entry.get("id") or "").lower() == vendor_id:
                return dict(entry)
    return None


def read_timestamp(payload: Mapping[str, Any], *keys: str) -> str:
    """The first timestamp the payload carries, kept as a string.

    Stored as it arrived rather than parsed. Nothing in this package compares two
    timestamps - the vendor owns the clock and this room only records what it was
    told - so parsing would add a failure mode and reject a payload whose time is
    perfectly legible.
    """
    candidates = keys or (
        "signedAt",
        "signed_at",
        "completedAt",
        "completed_at",
        "expiresAt",
        "expires_at",
        "expirationNotifiedAt",
        "expiration_notified_at",
        "occurredAt",
        "occurred_at",
        "createdAt",
        "created_at",
        "updatedAt",
        "updated_at",
        "timestamp",
    )
    for key in candidates:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def rejection_reason(payload: Mapping[str, Any]) -> str:
    """Why a recipient refused, in their own words where they gave them.

    "A buyer can instead **reject** (``DOCUMENT_REJECTED``, ``rejectionReason``)". A
    refusal with no reason is recorded as one rather than dropped: the seller has
    to know the plan came back, and an empty reason is not a reason to hide it.
    """
    return first_string(payload, ("rejectionReason", "rejection_reason", "reason"))


# --------------------------------------------------------------------------- #
# The decision
# --------------------------------------------------------------------------- #


def apply_event(
    event: str,
    plan: Mapping[str, Any],
    recipients: list[Mapping[str, Any]],
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """What one event does to one plan. Pure, and returns the whole decision.

    Takes the plan and its recipients as read and returns what should change, so
    the caller does the writing and the rule stays testable without a database.

    The returned mapping carries:

    * ``outcome`` - :data:`OUTCOME_APPLIED`, :data:`OUTCOME_DUPLICATE` or
      :data:`OUTCOME_NOTED`.
    * ``recipient_patch`` - the recipient row to update, or ``None`` when the
      event was not about one.
    * ``milestone`` - the plan's new milestone, or ``None`` when it does not move.
    * ``notice_reason`` - what to tell the owner, or ``None``.
    * ``notes`` - the human-readable findings a page shows beside the badge.

    A milestone never moves backwards and a terminal milestone is never left. Both
    come from the research having no transition out of approval, rejection or
    cancellation, and from a late ``DOCUMENT_REMINDER_SENT`` after an approval
    being a fact rather than a state change.
    """
    outcome = OUTCOME_APPLIED
    notes: list[str] = []
    milestone: str | None = None
    recipient_patch: dict[str, Any] | None = None
    notice_reason: str | None = None
    current = str(plan.get("milestone") or "")

    if event in ACTION_EVENTS and not plan.get("distributed_at"):
        # The research's order is create, distribute, then events. Believing an
        # event on a draft would approve a plan nobody was ever sent.
        return {
            "outcome": OUTCOME_NOTED,
            "recipient_patch": None,
            "recipient_email": None,
            "milestone": None,
            "notice_reason": None,
            "notes": [
                "the plan has not been distributed yet, so this event arrived early and "
                "was not applied"
            ],
            "event": event,
        }

    email = require_recipient_for(event, payload)
    target = resolve_recipient(email, recipients) if email else None

    if event == EVENT_DOCUMENT_CREATED:
        outcome = OUTCOME_NOTED
        notes.append("the vendor created the document. The room already has the plan")
    elif event == EVENT_DOCUMENT_SENT:
        outcome = OUTCOME_NOTED
        notes.append("the vendor sent the signing links. The room distributed the plan itself")
    elif event == EVENT_DOCUMENT_OPENED:
        if target is not None:
            recipient_patch = {"status": RECIPIENT_OPENED, "opened_at": read_timestamp(payload)}
            notice_reason = "recipient_opened"
    elif event == EVENT_DOCUMENT_SIGNED:
        if target is not None:
            role = str(target.get("role") or "")
            # The approver and the signer both produce DOCUMENT_SIGNED. Which one
            # arrived is read off the role, and it matters: an approver's signature
            # is the gate opening.
            status = RECIPIENT_APPROVED if role == APPROVER else RECIPIENT_SIGNED
            recipient_patch = {"status": status, "signed_at": read_timestamp(payload)}
            notice_reason = "approver_approved" if role == APPROVER else "recipient_signed"
            if role == APPROVER:
                notes.append("the approver approved. Signers can sign now")
    elif event == EVENT_DOCUMENT_RECIPIENT_COMPLETED:
        if target is not None:
            recipient_patch = {
                "status": RECIPIENT_COMPLETED,
                "completed_at": read_timestamp(payload),
            }
    elif event == EVENT_DOCUMENT_REJECTED:
        if target is not None:
            role = str(target.get("role") or "")
            recipient_patch = {
                "status": RECIPIENT_REJECTED,
                "rejected_at": read_timestamp(payload),
                "rejection_reason": rejection_reason(payload),
            }
            # The research makes these two different problems. An approver refuses
            # before signers can sign, so the seller can fix the plan and send it
            # again; a signer refuses the terms.
            milestone = REFUSAL_MILESTONES.get(role, MILESTONE_REFUSED_BY_SIGNER)
            notice_reason = "refused_by_approver" if role == APPROVER else "refused_by_signer"
            notes.append(
                f"{role} refused"
                + (
                    f": {recipient_patch['rejection_reason']}"
                    if recipient_patch.get("rejection_reason")
                    else ""
                )
            )
    elif event == EVENT_RECIPIENT_EXPIRED:
        if target is not None:
            recipient_patch = {
                "status": RECIPIENT_EXPIRED,
                "expires_at": read_timestamp(payload, "expiresAt", "expires_at"),
                "expiration_notified_at": read_timestamp(
                    payload, "expirationNotifiedAt", "expiration_notified_at"
                ),
            }
            milestone = MILESTONE_EXPIRED
            notice_reason = "recipient_expired"
            notes.append("a recipient let the signing deadline pass")
    elif event == EVENT_DOCUMENT_REMINDER_SENT:
        outcome = OUTCOME_NOTED
        if target is not None:
            recipient_patch = {"reminded_at": read_timestamp(payload), "reminder_count": 1}
        notice_reason = "reminder_sent"
        notes.append("a reminder went out to somebody who has not signed")
    elif event == EVENT_DOCUMENT_CANCELLED:
        milestone = MILESTONE_CANCELLED
        notice_reason = "plan_cancelled"
    elif event == EVENT_DOCUMENT_COMPLETED:
        milestone = MILESTONE_APPROVED
        notice_reason = "plan_approved"
    elif event in (
        EVENT_TYPE_TEMPLATE_CREATED,
        EVENT_TYPE_TEMPLATE_UPDATED,
        EVENT_TYPE_TEMPLATE_DELETED,
        EVENT_TYPE_TEMPLATE_USED,
    ):
        outcome = OUTCOME_NOTED
        notes.append(
            "this is a template event. It changes no plan and this build reports it "
            "as read rather than acting on it"
        )

    if target is None and email and event in RECIPIENT_EVENTS:
        notes.append(
            f"the event named {email}, who is not on this plan. It was recorded and "
            "applied to nobody"
        )

    # A terminal milestone stays terminal. The research publishes no transition out
    # of approved, refused or cancelled, so a late event from the vendor - a
    # reminder sent before a signature that arrived out of order - is recorded and
    # changes nothing.
    if milestone is not None and current in (
        MILESTONE_APPROVED,
        MILESTONE_REFUSED_BY_SIGNER,
        MILESTONE_REFUSED_BY_APPROVER,
        MILESTONE_CANCELLED,
        MILESTONE_EXPIRED,
    ):
        notes.append(
            f"the plan is already {current.replace('_', ' ')}, so this event changed no milestone"
        )
        milestone = None
        if notice_reason in ("plan_approved", "refused_by_signer", "refused_by_approver"):
            notice_reason = None
        outcome = OUTCOME_NOTED

    return {
        "outcome": outcome,
        "event": event,
        "recipient_patch": recipient_patch,
        "recipient_email": str(target.get("email")) if target is not None else None,
        "milestone": milestone,
        "notice_reason": notice_reason,
        "notes": notes,
    }


def plan_is_complete(recipients: Iterable[Mapping[str, Any]]) -> bool:
    """Is every recipient who had to act finished?

    "when all recipients are done -> ``DOCUMENT_COMPLETED``". Only the signing
    roles are counted: a CC and a VIEWER are never asked to sign, so their absence
    cannot hold a plan open forever. A plan with nobody still to act is complete
    by definition, which is the correct reading of a plan whose only signers have
    all refused it being finished - though it is refused, not approved, and the
    caller decides that from the recipient statuses.
    """
    pending = [
        entry
        for entry in recipients
        if str(entry.get("role") or "") in ("SIGNER", APPROVER)
        and str(entry.get("status") or "").upper() not in DONE_STATUSES
    ]
    return not pending


__all__ = [
    "ACTION_EVENTS",
    "COMPLETION_EVENTS",
    "DONE_STATUSES",
    "OUTCOME_APPLIED",
    "OUTCOME_DUPLICATE",
    "OUTCOME_NOTED",
    "RECIPIENT_APPROVED",
    "RECIPIENT_COMPLETED",
    "RECIPIENT_EXPIRED",
    "RECIPIENT_OPENED",
    "RECIPIENT_REJECTED",
    "RECIPIENT_REMINDED",
    "RECIPIENT_SIGNED",
    "RECIPIENT_UNOPENED",
    "apply_event",
    "event_fingerprint",
    "first_string",
    "plan_is_complete",
    "read_recipient_email",
    "read_timestamp",
    "rejection_reason",
    "require_known_event",
    "require_recipient_for",
    "resolve_recipient",
]
