"""The credit ledger: consumed on booking, returned on a no-show.

The research names both directions in one clause of the data flow:

    "credit consumed on the selected member (or credited back on no-show)"

and it says which of them is a standing rule:

    "no-show credit-back is admin-triggered but the Meeting Type flag (if the
    Distribution associated with the meeting is set to credit back assignees for
    No-Shows) makes it a standing rule."

So a return is not an ad-hoc correction an admin types in. It is the
distribution's configured behaviour, applied when the admin marks the prospect
No-Show. :func:`apply_return` therefore reads the flag and refuses a credit-back
that the distribution has not switched on, rather than returning a credit because
somebody asked.

Every function here is pure: it reads a ledger and returns the ledger to persist.
The engine writes it in the same transaction as the booking or the no-show, so a
ledger that records a credit cannot exist without the booking that consumed it,
and a returned credit cannot exist without the no-show that justified it.

A returned credit does not move the cursor. The rotation advances on bookings
("distribution state advances on each booking"), and a no-show is a correction to
one booking's effect rather than a new turn, so rewinding the cursor would let a
member take a second turn for the same prospect's meeting.
"""

from __future__ import annotations

from typing import Any, Mapping

from dsr.round_robin.errors import RoundRobinError
from dsr.round_robin.vocabulary import CREDIT_CONSUMED, CREDIT_RETURNED

#: A booking consumes exactly one credit. Not a configurable amount: the research
#: describes a per-booking credit with no quantity, and a second knob would be an
#: invention.
CREDIT_PER_BOOKING = 1


def empty_ledger() -> dict[str, Any]:
    """A ledger with no members in it yet."""
    return {}


def _entry(ledger: Mapping[str, Any], member: str) -> dict[str, Any]:
    row = ledger.get(member) or {}
    if not isinstance(row, Mapping):
        raise RoundRobinError(f"credit ledger entry for {member} is not an object: {row!r}")
    return {
        "credits_consumed": int(row.get("credits_consumed") or 0),
        "turns_taken": int(row.get("turns_taken") or 0),
        "bookings": int(row.get("bookings") or 0),
        "credits_returned": int(row.get("credits_returned") or 0),
    }


def apply_consumption(ledger: Mapping[str, Any], member: str, *, booking_id: str) -> dict[str, Any]:
    """The ledger after ``member`` has taken a booking.

    Counts the booking, consumes one credit, and takes the turn. The booking id
    is recorded on the entry so a later credit-back can find the consumption it is
    reversing without the caller having to remember which turn it was.
    """
    if not member:
        raise RoundRobinError("a credit cannot be consumed without the member it was consumed on")
    updated = dict(ledger)
    entry = _entry(updated, member)
    entry["credits_consumed"] += CREDIT_PER_BOOKING
    entry["turns_taken"] += 1
    entry["bookings"] += 1
    entry["last_booking_id"] = booking_id
    updated[member] = entry
    return updated


def can_return(distribution: Mapping[str, Any]) -> bool:
    """Whether this distribution returns a credit when a rep no-shows."""
    return bool(distribution.get("credit_back_on_no_show", False))


def apply_return(
    ledger: Mapping[str, Any],
    member: str,
    *,
    booking_id: str,
    distribution: Mapping[str, Any],
) -> dict[str, Any]:
    """The ledger after ``member`` is credited back for a no-show.

    Refuses when the distribution has not set ``credit_back_on_no_show``, and
    refuses when the member has no consumed credit to return. Both are refusals
    rather than silent no-ops: an admin who marked a no-show expects either a
    credit back or an explanation, and a silent no-op gives neither.
    """
    if not can_return(distribution):
        raise RoundRobinError(
            f"distribution does not set credit_back_on_no_show, so booking {booking_id} cannot "
            "credit a member back. Set the flag on the distribution first"
        )
    updated = dict(ledger)
    entry = _entry(updated, member)
    if entry["credits_consumed"] <= 0:
        raise RoundRobinError(
            f"member {member} has no consumed credit to return for booking {booking_id}"
        )
    entry["credits_consumed"] -= CREDIT_PER_BOOKING
    entry["credits_returned"] += CREDIT_PER_BOOKING
    entry["returned_booking_id"] = booking_id
    updated[member] = entry
    return updated


def movement(direction: str, *, booking_id: str) -> dict[str, Any]:
    """One ledger movement, as the row that records it.

    The ledger itself is state on the distribution. The movement is the record of
    why it changed, and it is a record rather than a field because a rep's credit
    history is what an administrator reads when asking whether the rotation is
    fair.
    """
    return {
        "direction": direction,
        "amount": CREDIT_PER_BOOKING,
        "booking_id": booking_id,
    }


def totals(ledger: Mapping[str, Any]) -> dict[str, int]:
    """Credit totals across the whole ledger, for the page header."""
    rows = [_entry(ledger, member) for member in ledger]
    return {
        "consumed": sum(row["credits_consumed"] for row in rows),
        "returned": sum(row["credits_returned"] for row in rows),
        "bookings": sum(row["bookings"] for row in rows),
        "outstanding": sum(row["credits_consumed"] for row in rows),
    }


def balance(ledger: Mapping[str, Any], member: str) -> int:
    """One member's outstanding credit count."""
    return _entry(ledger, member)["credits_consumed"]


__all__ = [
    "CREDIT_CONSUMED",
    "CREDIT_PER_BOOKING",
    "CREDIT_RETURNED",
    "apply_consumption",
    "apply_return",
    "balance",
    "can_return",
    "empty_ledger",
    "movement",
    "totals",
]
