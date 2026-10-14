"""WF-136: in-deal scheduling and next-step booking.

A buyer books the next step from inside the deal room, and the meeting lands in
the room's timeline. The research is blunt about where the custom work is -
"No OSS project does booking *inside* a deal room" - so this package adds that
one layer and borrows everything on either side of it:

* the slot grid and the booking come from WF-058
  (:mod:`dsr.inroom_scheduling`),
* the join link comes from WF-059 (:mod:`dsr.conference_links`),
* the MAP task comes from WF-124 (:mod:`dsr.mutual_action.plan`).

Module map, in dependency order:

``errors``
    One base type and one subclass per refusal a caller can act on, each
    carrying the status it answers with.
``vocabulary``
    The researched states a proposal and a meeting move through, the collections
    this workflow writes, and the two numbers the specification leaves open.
``inferences``
    Every judgement call, named and served over HTTP so a reviewer can disagree
    with one by name, and marking which one Jev did not clear.
``engine``
    The workflow end to end over the audited store, with ``source`` required on
    every write.
"""

from __future__ import annotations

from dsr.inroom_booking import inferences, vocabulary
from dsr.inroom_booking.engine import ACTIVITY_COLLECTION, ROOM_COLLECTION, InRoomBookingEngine
from dsr.inroom_booking.errors import (
    AttendeeRequired,
    BookingRefused,
    InRoomBookingError,
    MeetingNotFound,
    NextStepTaskNotFound,
    ProposalAnswered,
    ProposalExpired,
    ProposalInvalid,
    ProposalNotFound,
    RecordingNotAttached,
    RoomNotBookable,
    SlotNotOffered,
)
from dsr.inroom_booking.vocabulary import (
    ACCEPT_BOOKS_IMMEDIATELY,
    ANSWERABLE_PROPOSAL_STATES,
    COMPLETED_PROPOSAL_STATES,
    IDLE_MINUTES,
    MEETING_COLLECTION,
    MEETING_LINKED,
    MEETING_STATES,
    MEETING_UNLINKED,
    PROPOSAL_ACCEPTED,
    PROPOSAL_BOOKED,
    PROPOSAL_COLLECTION,
    PROPOSAL_DECLINED,
    PROPOSAL_EXPIRED,
    PROPOSAL_PROPOSED,
    PROPOSAL_STATES,
    PROPOSAL_TTL_MINUTES,
    ROOM_FIELD,
    ROOM_MEETING_LIMIT,
    TIMELINE_COLLECTION,
    published_vocabulary,
    require_proposal_state,
)

__all__ = [
    "ACCEPT_BOOKS_IMMEDIATELY",
    "ACTIVITY_COLLECTION",
    "ANSWERABLE_PROPOSAL_STATES",
    "AttendeeRequired",
    "BookingRefused",
    "COMPLETED_PROPOSAL_STATES",
    "IDLE_MINUTES",
    "InRoomBookingEngine",
    "InRoomBookingError",
    "MEETING_COLLECTION",
    "MEETING_LINKED",
    "MEETING_STATES",
    "MEETING_UNLINKED",
    "MeetingNotFound",
    "NextStepTaskNotFound",
    "PROPOSAL_ACCEPTED",
    "PROPOSAL_BOOKED",
    "PROPOSAL_COLLECTION",
    "PROPOSAL_DECLINED",
    "PROPOSAL_EXPIRED",
    "PROPOSAL_PROPOSED",
    "PROPOSAL_STATES",
    "PROPOSAL_TTL_MINUTES",
    "ProposalAnswered",
    "ProposalExpired",
    "ProposalInvalid",
    "ProposalNotFound",
    "ROOM_COLLECTION",
    "ROOM_FIELD",
    "ROOM_MEETING_LIMIT",
    "RecordingNotAttached",
    "RoomNotBookable",
    "SlotNotOffered",
    "TIMELINE_COLLECTION",
    "inferences",
    "published_vocabulary",
    "require_proposal_state",
    "vocabulary",
]
