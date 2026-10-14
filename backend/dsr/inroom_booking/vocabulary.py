"""The researched vocabulary of WF-136, and the limits this build puts on it.

Everything quoted from the specification is marked as a quote and carries the
sentence it came from. Everything else is a constant this build chose, and every
one of those is either published in :mod:`dsr.inroom_booking.inferences` or named
here with the reason it is not a judgement call at all.

The one rule that governs this module: no constant is a silent choice. A number
with no owner is a number nobody can argue with, and the specification leaves
several of them open.
"""

from __future__ import annotations

from typing import Any

from dsr.inroom_booking.errors import InRoomBookingError

# --------------------------------------------------------------------------- #
# The user flow, quoted, because every state below comes from one step of it
# --------------------------------------------------------------------------- #

#: "At the end of a room session the seller proposes the next step -> the buyer
#: picks a slot from the seller's live availability, inside the room -> the
#: meeting is created, invites go out, the recording links back to the room ->
#: the MAP task flips to done and the room's 'next steps' panel updates."
USER_FLOW_QUOTE = (
    "At the end of a room session the seller proposes the next step, the buyer "
    "picks a slot from the seller's live availability inside the room, the "
    "meeting is created, invites go out, the recording links back to the room, "
    "the MAP task flips to done and the room's next-steps panel updates."
)

#: "Auto-propose a slot when a room is idle; auto-book on acceptance;
#: auto-generate the conferencing room; auto-record and attach; auto-update the
#: MAP and CRM."
AUTOMATIONS_QUOTE = (
    "Auto-propose a slot when a room is idle; auto-book on acceptance; "
    "auto-generate the conferencing room; auto-record and attach; auto-update "
    "the MAP and CRM."
)

#: "Embedded availability; one-click accept; recorded demo auto-attached
#: ('Watch recording'); task completion; next-steps panel."
SURFACES_QUOTE = (
    "Embedded availability; one-click accept; recorded demo auto-attached (Watch "
    "recording); task completion; next-steps panel."
)

#: "The recording is attached, not produced." The workflow's own scope limit:
#: "auto-record and attach" is listed under ``automations``, and the surface is
#: "recorded demo auto-attached (Watch recording)". Recording is WF-060's work.
#: This workflow stores and links the artefact.
RECORDING_NOTE = (
    "The recording is attached, not produced. This workflow stores and links the "
    "artefact; capturing the meeting is WF-060's work."
)

#: "the room's task API" is the only ``apis_hit`` entry that names this
#: application rather than a third party. Resolved against
#: ``docs/FEATURE-CONTRACT.md``: the host is a plugin, so the room's task API is
#: the workflow that owns the task graph - WF-124 - and this feature reaches it
#: through ``dsr.mutual_action.plan`` rather than through an invented route.
ROOM_TASK_API_NOTE = (
    "The room's task API is WF-124's mutual action plan. This feature flips a "
    "task to done through dsr.mutual_action.plan.PlanEngine, not through a route "
    "it invented."
)

#: "Calendar availability (Google/Microsoft) + routing rules -> slot list
#: rendered in-room". Both are inputs. The calendar a room uses is whichever one
#: WF-058's embed connected, and the routing rules are WF-058's routing forms.
#: This workflow reads both and owns neither.
INPUTS_NOTE = (
    "Calendar availability and routing rules are inputs this workflow reads and "
    "does not own: the calendar is the one the room's WF-058 embed connected, "
    "and the routing rules are WF-058's routing forms."
)

# --------------------------------------------------------------------------- #
# Collections
# --------------------------------------------------------------------------- #

#: Prefixed with the workflow, so a reviewer reading the collections list can
#: tell this workflow's rows from any other feature's.
PROPOSAL_COLLECTION = "inroom_booking_proposal"
MEETING_COLLECTION = "inroom_booking_meeting"

#: The room's own timeline rows, written into the collection the room already
#: reads. A booking that lands nowhere the buyer looks is a booking that did not
#: land in the room.
TIMELINE_COLLECTION = "timeline"

#: The field on a room row carrying this workflow's convenience copy. The same
#: shape WF-058 and WF-124 use, so a reader of the room row sees one object per
#: workflow rather than one object per feature.
ROOM_FIELD = "inroom_booking"

#: How many meetings the room row's convenience copy keeps. The meeting records
#: are the durable copy; this is what a rep glances at, capped so a room with a
#: hundred meetings does not grow a hundred-element array.
ROOM_MEETING_LIMIT = 20

# --------------------------------------------------------------------------- #
# The states a next-step proposal moves through
# --------------------------------------------------------------------------- #

#: The five states, in the order the user flow reaches them.
PROPOSAL_PROPOSED = "proposed"
PROPOSAL_ACCEPTED = "accepted"
PROPOSAL_BOOKED = "booked"
PROPOSAL_DECLINED = "declined"
PROPOSAL_EXPIRED = "expired"

PROPOSAL_STATES: tuple[str, ...] = (
    PROPOSAL_PROPOSED,
    PROPOSAL_ACCEPTED,
    PROPOSAL_BOOKED,
    PROPOSAL_DECLINED,
    PROPOSAL_EXPIRED,
)

#: A state a buyer can no longer answer from. ``proposed`` is the only live one.
ANSWERABLE_PROPOSAL_STATES: frozenset[str] = frozenset({PROPOSAL_PROPOSED})

#: The states a proposal can reach without anybody acting. ``expired`` is the
#: one: "no user action needed for the hold to expire" is WF-058's sentence
#: about holds, and a proposal nobody answers behaves the same way.
UNATTENDED_PROPOSAL_STATES: frozenset[str] = frozenset({PROPOSAL_EXPIRED})

#: The states that count as a completed next step on the room's panel.
COMPLETED_PROPOSAL_STATES: frozenset[str] = frozenset({PROPOSAL_BOOKED})

# --------------------------------------------------------------------------- #
# The states a booked meeting can be in
# --------------------------------------------------------------------------- #

#: The meeting exists and the conference link is on it.
MEETING_LINKED = "linked"

#: The meeting exists and no link is on it, because the conferencing provider
#: refused. WF-059's own rule: "A booking whose Location cannot produce a
#: conference is still a booking." So the meeting stands and says so.
MEETING_UNLINKED = "unlinked"

MEETING_STATES: tuple[str, ...] = (MEETING_LINKED, MEETING_UNLINKED)

# --------------------------------------------------------------------------- #
# The two numbers the specification leaves open
# --------------------------------------------------------------------------- #

#: How long a room must have had no buyer activity before the auto-propose
#: automation fires. **Jev did not clear this number**: audit
#: jev-20261014T094324-5156-04570 returned ``uncertain`` at confidence 0.59
#: against a 0.75 threshold, with fifteen_minutes at 0.73, sixty_minutes at 0.27
#: and twenty_four_hours at 0.00. It ships as the front-runner and is published
#: as an unresolved inference in :mod:`dsr.inroom_booking.inferences`. See the
#: ``idle_minutes`` entry there, and the pull request body.
IDLE_MINUTES = 15

#: How long a proposal stays answerable. Seven days, because a next step
#: proposed on Friday and answered on Monday is a next step that was proposed
#: and answered, and a proposal that lapses inside a working week asks the buyer
#: to ask again for no reason that anybody can name.
PROPOSAL_TTL_MINUTES = 7 * 24 * 60

#: Whether the buyer's acceptance is a final commit. Cleared by Jev: audit
#: jev-20261014T094215-7988-35585 selected ``commit`` at confidence 0.91. The
#: alternative rejected there was a confirmation step the words "one-click
#: accept" do not describe.
ACCEPT_BOOKS_IMMEDIATELY = True


def require_proposal_state(state: str) -> str:
    """The proposal state called ``state``, or a refusal naming the set."""
    if state not in PROPOSAL_STATES:
        raise InRoomBookingError(
            f"unknown proposal state {state!r}; the published set is " + ", ".join(PROPOSAL_STATES)
        )
    return state


def published_vocabulary() -> dict[str, Any]:
    """Everything a page renders a picker or an explanation from.

    Served as data rather than compiled into the frontend, so a term added here
    reaches every client at once and the frontend ships no second list to keep in
    step with this one.

    The unresolved inference is included rather than hidden. A page that showed
    a number without saying Jev had not cleared it would be asserting a
    confidence the project does not have.
    """
    return {
        "user_flow": USER_FLOW_QUOTE,
        "automations": AUTOMATIONS_QUOTE,
        "surfaces": SURFACES_QUOTE,
        "proposal_states": list(PROPOSAL_STATES),
        "answerable_proposal_states": sorted(ANSWERABLE_PROPOSAL_STATES),
        "unattended_proposal_states": sorted(UNATTENDED_PROPOSAL_STATES),
        "completed_proposal_states": sorted(COMPLETED_PROPOSAL_STATES),
        "meeting_states": list(MEETING_STATES),
        "idle_minutes": IDLE_MINUTES,
        "proposal_ttl_minutes": PROPOSAL_TTL_MINUTES,
        "accept_books_immediately": ACCEPT_BOOKS_IMMEDIATELY,
        "collections": {
            "proposal": PROPOSAL_COLLECTION,
            "meeting": MEETING_COLLECTION,
            "timeline": TIMELINE_COLLECTION,
        },
        "notes": {
            "recording": RECORDING_NOTE,
            "room_task_api": ROOM_TASK_API_NOTE,
            "inputs": INPUTS_NOTE,
        },
    }
