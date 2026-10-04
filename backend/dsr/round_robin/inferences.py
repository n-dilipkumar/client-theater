"""Every judgement call WF-054 makes, named and served so a reviewer can disagree.

The research for WF-054 is precise about the two modes, the license gate, the
credit ledger's two directions, and the fact that the Distribution is a reusable
asset. It is silent about the calendar operation, the tie-breaks, the credit
amount, and several smaller things. Those gaps are product behaviour rather than
comments, so they are collected here and served at ``GET /api/wf054/inferences``
instead of being buried in the module that implements them.

Each entry carries ``decision``, ``why`` and ``change_if``. A reviewer who
disagrees has a named thing to disagree with and a stated consequence, rather
than having to reverse-engineer a diff.
"""

from __future__ import annotations

from typing import Any

from dsr.round_robin.availability import DERIVATION_ID


def _entry(
    entry_id: str,
    topic: str,
    question: str,
    decision: str,
    why: str,
    change_if: str,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "id": entry_id,
        "topic": topic,
        "question": question,
        "decision": decision,
        "why": why,
        "change_if": change_if,
        **extra,
    }


INFERENCES: tuple[dict[str, Any], ...] = (
    _entry(
        DERIVATION_ID,
        "calendar combination",
        "The data flow reads 'union/intersection of member calendars' without saying which "
        "operation applies to which mode. Which does this build use?",
        "A union, for both modes. An instant is on offer when at least one licensed, "
        "calendar-connected member is free for the whole slot, and the slot is annotated with "
        "those member ids. Booking then re-checks that the chosen member is free at that instant.",
        "Four things fix it. The research promises one combined window and an intersection of a "
        "real team's free/busy is empty as soon as any two members hold a busy block at once. "
        "'flexible (weighted by availability)' measures a volume, and under an intersection every "
        "member's availability is identical so every weight would be equal. The license gate "
        "excludes a member from assignment, so their calendar must not narrow the window either. "
        "And reassignment reopens the same Distribution against a changed calendar, which a "
        "union survives and an intersection does not.",
        "Narrow the union to members free at the instant, which is what the re-check already "
        "does, or return no slots when the re-check finds nobody. The re-check is the price of "
        "the union and is cheaper than an empty window.",
        jev_audit_id="jev-20261004T045227-22564-47815",
        jev_verdict="pass",
        jev_confidence=1.0,
        jev_first_ask="jev-20261004T045140-22932-00253 returned uncertain at 0.44 with three "
        "options; the narrowed two-option re-ask is the one enforced",
    ),
    _entry(
        "inference_strict_ignores_weights",
        "Strict selection",
        "Strict rotates by equal turns. Does the per-member weight affect it?",
        "No. Strict sorts on credits consumed, then turns taken, then team member order. The "
        "weight is reported on the selection record but does not change the choice.",
        "A weight is a statement about relative capacity. 'Equal turns' is a statement that "
        "capacity is not the point, and applying both would make Strict a Flexible with a "
        "different name.",
        "Read the weight in the Strict branch of select_member.",
    ),
    _entry(
        "inference_tie_break_order",
        "tie-breaks",
        "Two members tie on the selection key. Which is chosen?",
        "Fewest credits consumed, then fewest turns taken, then position in the team member list.",
        "Team order is the last key, so the choice is deterministic and the rotation completes. "
        "Without a final key two members who each took one booking in a four-member team could "
        "be chosen repeatedly while the two who took none were skipped, and 'equal turns' would "
        "not hold.",
        "Drop the team-order key from the sort in selection.select_member.",
    ),
    _entry(
        "inference_cursor_advances_on_booking",
        "distribution state",
        "The research says 'the distribution state advances on each booking'. What advances?",
        "A cursor into the eligible member list and a cycle counter. Both advance after every "
        "booking, whether or not the booking was against the member the cursor pointed at.",
        "Advancing on every booking is what the sentence says. Tying the advance to a particular "
        "member would mean the rotation stalls whenever the next member in the list was not the "
        "one chosen, which is most of the time under the union.",
        "Advance the cursor only when the booking matches the cursor, in selection.advance.",
    ),
    _entry(
        "inference_credit_back_does_not_rewind_cursor",
        "no-show credit-back",
        "A no-show returns a credit. Does it move the distribution's cursor?",
        "No. The credit goes back to the member whose booking it was and the cursor stays where "
        "it advanced to.",
        "The research says state advances on each booking, and a no-show corrects one booking's "
        "effect rather than taking a new turn. Rewinding would let a member take a second turn "
        "for the same prospect's meeting.",
        "Rewind in credits.apply_return, or in the engine after it calls apply_return.",
    ),
    _entry(
        "inference_credit_amount",
        "credit quantity",
        "How much does a booking consume?",
        "Exactly one credit per booking.",
        "The research describes a per-booking credit with no quantity, and a second knob would be "
        "an invention rather than a derivation.",
        "Make CREDIT_PER_BOOKING configurable and read it in credits.apply_consumption.",
    ),
    _entry(
        "inference_absent_flags_default_permissive",
        "absent member flags",
        "A team member row with no 'licensed' or 'calendar_connected' key. Is that member eligible?",
        "Yes. Absent means licensed and connected; an explicit false is respected.",
        "A team row written by an importer that predates these flags should not have every "
        "member excluded, which would make an upgrade look like a licensing failure.",
        "Read the flags with a false default in teams.is_licensed and teams.calendar_connected.",
    ),
    _entry(
        "inference_unavailable_member_is_not_a_refusal",
        "no availability",
        "Every licensed member is booked out for the whole window. What happens?",
        "The evaluation succeeds and reports outcome 'eligible' with no slots, rather than "
        "refusing. It is a legitimate answer: the researched Not Scheduled path.",
        "The refusal is reserved for a team with nobody assignable, which is a configuration "
        "problem the admin has to fix. A full calendar is the normal state of a busy week and "
        "must not read as an error.",
        "Raise NoEligibleMember instead when the slot list is empty.",
    ),
    _entry(
        "inference_slot_grid_alignment",
        "slot grid",
        "Which instants can a booking start at?",
        "Every duration_minutes step from the interval's start, for slots that fit entirely "
        "inside it. The grid is aligned to the interval's own start, not to midnight.",
        "Aligning to midnight would silently drop the first part of any interval that does not "
        "begin on the hour, and a prospect would be told the team is unavailable at a time the "
        "team is free.",
        "Floor each slot to the hour in timeutil.grid.",
    ),
    _entry(
        "inference_naive_timestamps_are_utc",
        "timestamps",
        "A member busy block carries a timestamp with no offset. Which zone is it in?",
        "UTC, the same as every other timestamp in this package.",
        "A distribution declared in one zone and read in another must produce the same instants. "
        "Guessing the host's local zone would make the same distribution offer different slots on "
        "two machines.",
        "Read naive timestamps as local time in timeutil.parse.",
    ),
    _entry(
        "inference_distribution_owns_weights",
        "weight ownership",
        "Do the per-member weights live on the team or on the distribution?",
        "On the distribution, with the member's own weight as a fallback default.",
        "The research says the distribution consists of the team, the mode, and 'Team member's "
        "weights and credits'. It also calls distributions 'reusable assets independent of the "
        "router', so one team may sit under several distributions with different weights.",
        "Read the weight only off the member row in teams.weight_of.",
    ),
    _entry(
        "inference_credits_persist_on_distribution",
        "credit storage",
        "Where does a consumed credit live?",
        "In the distribution's own credits map, keyed by member id, alongside the cursor and "
        "cycle. The booking names the distribution rather than copying the ledger.",
        "The research requires that the same Distribution context be reused for reassignment, "
        "so the ledger has to be the distribution's state. Burying weights and credits inside the "
        "booking record is what the issue forbids.",
        "Store the ledger on the booking in engine.book.",
    ),
)

INFERENCE_IDS: tuple[str, ...] = tuple(str(entry["id"]) for entry in INFERENCES)


def describe() -> dict[str, Any]:
    """The whole registry, as served."""
    return {
        "count": len(INFERENCES),
        "inferences": [dict(entry) for entry in INFERENCES],
        "ids": list(INFERENCE_IDS),
        "note": (
            "Each entry names a judgement call this build made rather than a behaviour the "
            "research stated. A reviewer who disagrees with one has the change to make named."
        ),
    }


def by_id(entry_id: str) -> dict[str, Any] | None:
    """One entry, or ``None``."""
    for entry in INFERENCES:
        if entry["id"] == entry_id:
            return dict(entry)
    return None
