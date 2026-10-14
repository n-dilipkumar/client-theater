"""Every judgement call WF-136 makes, named and served so a reviewer can
disagree with one by name.

This is the pattern WF-055, WF-058, WF-059 and WF-124 all use, and the reason is
the same in each: a judgement call buried in a function body can only be argued
with by reading the function. A judgement call with a name can be argued with by
naming it.

Two of the entries carry a Jev audit id. One of them was cleared. One of them
was not, and that one says so in its own ``status`` field - ``cleared`` or
``unresolved`` - because a reviewer who cannot tell "we decided not to" from
"Jev could not decide" has to read the source to find out, and that is a cost
paid every time the question comes up.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- #
# The gate decisions
# --------------------------------------------------------------------------- #

#: Does the buyer's acceptance book the meeting, or only propose one?
#:
#: Cleared by Jev, audit ``jev-20261014T094215-7988-35585``, at confidence 0.91
#: against a 0.75 threshold. The probabilities were commit 0.94,
#: commit_with_confirmation_step 0.05, propose 0.01.
#:
#: The alternative rejected is the one the issue names as the risk: "Auto-book on
#: acceptance is a real risk and the spec does not guard it." The gate chosen
#: here guards it three ways rather than removing the commit - the proposal
#: carries an expiry, the slot must still be free at the moment of the click,
#: and WF-058's own cancel route un-books the meeting afterwards. A buyer who
#: mis-clicks has a way back; a buyer whose click produced nothing has to go and
#: find the seller.
ACCEPT_IS_A_COMMIT = {
    "name": "accept_is_a_commit",
    "status": "cleared",
    "audit_id": "jev-20261014T094215-7988-35585",
    "confidence": 0.91,
    "threshold": 0.75,
    "selected": "commit",
    "probabilities": {
        "commit": 0.94,
        "commit_with_confirmation_step": 0.05,
        "propose": 0.01,
    },
    "decision": "The buyer's one-click accept books the meeting immediately.",
    "rejected": [
        {
            "option": "propose",
            "why": "It would put a second actor and a second round trip between "
            "the buyer's pick and the calendar event, which the researched user "
            "flow does not describe.",
        },
        {
            "option": "commit_with_confirmation_step",
            "why": "The words one-click accept do not describe a second click, "
            "and the guard the issue asks for is already carried by the "
            "proposal expiry, the live slot check and WF-058's cancel route.",
        },
    ],
    "evidence": (
        "features_tools: 'one-click accept'. user_flow: 'the buyer picks a slot "
        "from the seller's live availability, inside the room -> the meeting is "
        "created, invites go out'."
    ),
}

#: How long a room must be idle before the automation proposes a slot?
#:
#: **Not cleared.** Audit ``jev-20261014T094324-5156-04570`` returned
#: ``uncertain``: confidence 0.59 against a 0.75 threshold, with fifteen_minutes
#: at 0.73, sixty_minutes at 0.27 and twenty_four_hours at 0.00. The standing
#: ruling on an uncertain gate is that it is inconclusive and is never
#: overridden, so this entry ships the front-runner and records the uncertainty
#: where a reviewer reads it.
IDLE_MINUTES = {
    "name": "idle_minutes",
    "status": "unresolved",
    "audit_id": "jev-20261014T094324-5156-04570",
    "confidence": 0.59,
    "threshold": 0.75,
    "selected": "fifteen_minutes",
    "probabilities": {
        "fifteen_minutes": 0.73,
        "sixty_minutes": 0.27,
        "twenty_four_hours": 0.00,
    },
    "decision": (
        "Fifteen minutes without buyer activity reads as the end of a room "
        "session, which is the moment the user flow describes."
    ),
    "rejected": [
        {
            "option": "sixty_minutes",
            "why": "Fewer proposals, so less chance of proposing into a room "
            "somebody is still reading. A pause over an hour is more likely to "
            "be a genuine stop.",
        },
        {
            "option": "twenty_four_hours",
            "why": "Proposes at most once a day per room, which is close to "
            "never interrupting - and a buyer who returns the next morning "
            "finds nothing waiting.",
        },
    ],
    "evidence": (
        "automations: 'Auto-propose a slot when a room is idle'. The issue: "
        "'The booking trigger is a timeout, and the spec does not define it. "
        "No idle threshold is given. Pick one and record it.'"
    ),
}

# --------------------------------------------------------------------------- #
# The build decisions
# --------------------------------------------------------------------------- #

#: How long a proposal stays answerable. Seven days.
#:
#: The specification gives no expiry for a proposal. Seven days is chosen so a
#: step proposed on Friday and answered on Monday is still answerable, and so
#: "the proposal lapsed" means something a buyer did not do over a whole week
#: rather than over an afternoon. The number is published, so a workflow that
#: wants a different one can argue with the name rather than with the function.
PROPOSAL_TTL_MINUTES = {
    "name": "proposal_ttl_minutes",
    "status": "build_decision",
    "decision": "A proposal stays answerable for seven days, then lapses on its own.",
    "rejected": [
        {
            "option": "no_expiry",
            "why": "A proposal that never lapses is a proposal the buyer can "
            "answer six months later, booking a slot the seller no longer "
            "offers. The live slot check would refuse it, which turns a stale "
            "proposal into a confusing refusal.",
        },
        {
            "option": "one_hour",
            "why": "Matches WF-058's five-minute hold only in spirit; a next "
            "step is not a slot hold. A buyer who is asked to decide in an hour "
            "is being rushed for no reason the specification states.",
        },
    ],
}

#: Which calendar provider this workflow integrates.
#:
#: Neither. The specification names two - "Calendar availability (Google /
#: Microsoft)" - and specifies neither, while WF-058 already models three
#: connected calendars (google, outlook, apple) per room. A workflow that
#: picked one would refuse to read a room connected to the other, for no reason
#: except a choice the research did not make.
CALENDAR_PROVIDER_IS_NOT_OWNED_HERE = {
    "name": "calendar_provider_is_not_owned_here",
    "status": "build_decision",
    "decision": (
        "This workflow reads whichever calendar the room's WF-058 embed "
        "connected and refuses the room when none is live. It does not pick a "
        "provider."
    ),
    "rejected": [
        {
            "option": "google_calendar_only",
            "why": "The research names Google Calendar and Microsoft Graph and "
            "specifies neither. Picking one would make a room connected to the "
            "other unbookable for a reason the specification does not contain.",
        }
    ],
    "evidence": (
        "data_flow: 'Calendar availability (Google/Microsoft) + routing rules -> "
        "slot list rendered in-room'. WF-058's published set is google, outlook "
        "and apple."
    ),
}

#: What happens to a meeting whose conferencing link cannot be provisioned.
#:
#: WF-059's own rule, adopted rather than re-decided: "A booking whose Location
#: cannot produce a conference is still a booking." So the meeting is written,
#: the link is absent, and the meeting row says ``unlinked`` with the reason.
CONFERENCE_FAILURE_DOES_NOT_FAIL_THE_MEETING = {
    "name": "conference_failure_does_not_fail_the_meeting",
    "status": "build_decision",
    "decision": (
        "A meeting whose conferencing provider refuses is written with state "
        "unlinked and the provider's reason. The booking, the MAP task and the "
        "timeline all stand."
    ),
    "rejected": [
        {
            "option": "refuse_the_acceptance",
            "why": "The buyer has chosen a time, the time was free, and the "
            "seller's provider is the thing that failed. Refusing would throw "
            "away a decision the buyer made because of an outage on the "
            "seller's side, and the slot would be gone by the time they "
            "retried.",
        }
    ],
    "evidence": (
        "WF-059: 'A booking whose Location cannot produce a conference is still a booking.'"
    ),
}

#: How a recording reaches the room.
#:
#: The specification's automation is "auto-record and attach" and its surface is
#: "recorded demo auto-attached (Watch recording)". Recording is WF-060's work
#: ("Auto-join and record with consent"). This workflow therefore exposes an
#: attach route that stores the URL and writes the timeline row, and it does not
#: claim to capture anything.
RECORDING_IS_ATTACHED_NOT_PRODUCED = {
    "name": "recording_is_attached_not_produced",
    "status": "build_decision",
    "decision": (
        "A recording is attached by URL. This workflow never claims to produce "
        "one, and the timeline row says Watch recording rather than describing "
        "a capture."
    ),
    "rejected": [
        {
            "option": "capture_the_meeting_here",
            "why": "That is WF-060's workflow and duplicating it inside a "
            "booking feature would put two consent gates in one product.",
        }
    ],
    "evidence": (
        "features_tools: 'recorded demo auto-attached (Watch recording)'. "
        "automations: 'auto-record and attach'."
    ),
}

#: What the room's next-steps panel reads.
#:
#: The specification says "the room's 'next steps' panel updates" and names "the
#: MAP's open tasks" and "the room's 'next steps' panel" as two separate data
#: sources. So the panel merges this workflow's own proposals with WF-124's open
#: tasks, rather than being a second copy of either.
NEXT_STEPS_PANEL_MERGES_PROPOSALS_AND_MAP_TASKS = {
    "name": "next_steps_panel_merges_proposals_and_map_tasks",
    "status": "build_decision",
    "decision": (
        "The panel is built fresh on every read from this workflow's proposals "
        "and WF-124's open tasks. Nothing is stored as a panel row."
    ),
    "rejected": [
        {
            "option": "store_a_panel_row",
            "why": "A stored panel is a cached answer to a question whose "
            "inputs change underneath it. A MAP task completed through WF-124's "
            "own route would leave a stored panel stale, which is exactly the "
            "failure the specification names ('the room's next steps panel "
            "updates').",
        }
    ],
    "evidence": ("data_sources: \"the MAP's open tasks; the room's 'next steps' panel.\""),
}


#: Every judgement call, in one list, so the route serves them all and a
#: reviewer can count them.
ALL: tuple[dict[str, Any], ...] = (
    ACCEPT_IS_A_COMMIT,
    IDLE_MINUTES,
    PROPOSAL_TTL_MINUTES,
    CALENDAR_PROVIDER_IS_NOT_OWNED_HERE,
    CONFERENCE_FAILURE_DOES_NOT_FAIL_THE_MEETING,
    RECORDING_IS_ATTACHED_NOT_PRODUCED,
    NEXT_STEPS_PANEL_MERGES_PROPOSALS_AND_MAP_TASKS,
)


def describe() -> dict[str, Any]:
    """Every judgement call, as one response.

    ``unresolved`` counts the entries Jev did not clear. It is the first field a
    reviewer reads, because it is the number that says how much of this build
    rests on a decision nobody verified.
    """
    unresolved = [entry["name"] for entry in ALL if entry.get("status") == "unresolved"]
    return {
        "count": len(ALL),
        "unresolved": unresolved,
        "unresolved_count": len(unresolved),
        "inferences": [dict(entry) for entry in ALL],
    }
