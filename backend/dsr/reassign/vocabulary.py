"""The researched vocabulary of WF-063, and the only normalisation it needs.

Every term here is one the research names, with the sentence it comes from kept
next to it. :func:`published_vocabulary` serves the lot at
``/api/wf-063/vocabulary`` so the pickers on the page are rendered from the same
source the validator enforces against, and so a term added in one place reaches
every client at once.

The four sources the research cites, and what each contributes:

* Chili Piper's *Reassigning Meetings* article - the three-step shape (pick a
  host and Reassign, or Edit Meeting to reopen the scheduler), the editable
  axis, and the two locked fields.
* Chili Piper's Events History article - what a reassignment row must display,
  and the two sources it names.
* Cal.com's automation/webhooks guide - ``BOOKING_REASSIGNED`` and the exact
  payload keys that differ from a plain booking payload.
* Cal.com's v2 API reference - the two reassign endpoints and the
  round-robin-only limit on the automatic one.
"""

from __future__ import annotations

from typing import Any

from dsr.reassign.errors import ReassignError

# --------------------------------------------------------------------------- #
# Quoted evidence
# --------------------------------------------------------------------------- #

#: "When Reassigning a meeting with Chili Piper, you are booking a new meeting
#: for another user. Reassignment will take into account your Handoff/ChiliCal
#: User controls and the Distribution settings of the meeting booked."
REASSIGNMENT_IS_A_NEW_BOOKING = (
    "When Reassigning a meeting with Chili Piper, you are booking a new meeting for another user. "
    "Reassignment will take into account your Handoff/ChiliCal User controls and the Distribution "
    "settings of the meeting booked."
)

#: "You can change the **Distribution, Team,** or **Individual**. **You cannot
#: change the Meeting Type or Workspace.**"
EDITABLE_AND_LOCKED = (
    "You can change the Distribution, Team, or Individual. You cannot change the Meeting Type or "
    "Workspace."
)

#: "Chili Piper should update the invite accordingly with the new assignee's
#: name, links, and other details that possibly changed from one assignee to
#: another."
INVITE_UPDATES_WITH_THE_ASSIGNEE = (
    "Chili Piper should update the invite accordingly with the new assignee's name, links, and other "
    "details that possibly changed from one assignee to another."
)

#: "Note: Reassignment does **not** take into account the minimum scheduling
#: notice or the maximum availability range."
BOUNDS_ARE_IGNORED = (
    "Note: Reassignment does not take into account the minimum scheduling notice or the maximum "
    "availability range."
)

#: "This option is available in Google Calendar if the meeting was booked with
#: Chili Piper. You must have ChiliCal's extension installed and be logged in
#: there."
EXTENSION_MUST_BE_INSTALLED_AND_LOGGED_IN = (
    "This option is available in Google Calendar if the meeting was booked with Chili Piper. You must "
    "have ChiliCal's extension installed and be logged in there."
)

#: "`addedHostUserIds` lists the user IDs of the host(s) newly assigned to the
#: booking and `removedHostUserIds` lists the user IDs of the host(s) removed.
#: `organizer` reflects the new host."
REASSIGNED_PAYLOAD_KEYS = (
    "`addedHostUserIds` lists the user IDs of the host(s) newly assigned to the booking and "
    "`removedHostUserIds` lists the user IDs of the host(s) removed. `organizer` reflects the new "
    "host."
)

#: "**Reassignments** Whenever a meeting is reassigned, we will display who
#: reassigned it, to whom, when, and the reassignment source (Meetings Activity
#: or ChiliCal Home)."
EVENTS_HISTORY_ROW = (
    "Reassignments. Whenever a meeting is reassigned, we will display who reassigned it, to whom, "
    "when, and the reassignment source (Meetings Activity or ChiliCal Home)."
)

#: "Fires when a round-robin booking's host is reassigned (automatic or
#: manual)." - the sentence that scopes ``BOOKING_REASSIGNED`` to round-robin
#: bookings and not to every reassignment.
BOOKING_REASSIGNED_SCOPE = (
    "Fires when a round-robin booking's host is reassigned (automatic or manual)."
)

#: "Currently only supports reassigning host for round robin bookings" - the
#: limit on Cal's ``POST /v2/bookings/{uid}/reassign/auto``.
AUTO_IS_ROUND_ROBIN_ONLY = "Currently only supports reassigning host for round robin bookings"

#: "Reassign a booking to a specific host" - the other Cal endpoint,
#: ``POST /v2/bookings/{uid}/reassign/{userId}``.
SPECIFIC_HOST = "Reassign a booking to a specific host"

#: "Triggered whenever a meeting is updated, for example if it's reassigned or
#: rescheduled" - Chili Piper's ``For Meeting Update`` custom webhook.
MEETING_UPDATE_WEBHOOK = (
    "Triggered whenever a meeting is updated, for example if it's reassigned or rescheduled"
)


# --------------------------------------------------------------------------- #
# Surfaces: the four documented entry points
# --------------------------------------------------------------------------- #

#: The four entry points ``extensibility`` names, plus the two APIs.
#:
#: One vocabulary, used twice: a meeting records the ``product_source`` it was
#: booked through (the "product source" filter on the Meetings Activity list),
#: and a reassignment records the ``surface`` it was made from (the Events
#: History "reassignment source"). One list, so the two can never disagree about
#: what a surface is called.
SURFACES: tuple[str, ...] = (
    "meetings_activity",
    "myapp",
    "chilical_home",
    "crm_event_button",
    "api",
)

#: The surfaces as the research names them, for the Events History dropdown.
SURFACE_LABELS: dict[str, str] = {
    "meetings_activity": "Meetings Activity",
    "myapp": "MyApp schedule",
    "chilical_home": "ChiliCal Home",
    "crm_event_button": "CRM Event button",
    "api": "API",
}

#: The only surface with a sourced precondition. "You must have ChiliCal's
#: extension installed and be logged in there."
SURFACES_REQUIRING_ADDON: frozenset[str] = frozenset({"chilical_home"})

DEFAULT_SURFACE = "api"


def require_surface(value: Any, *, field: str = "surface") -> str:
    """Normalise and validate a surface name.

    ``product_source`` on a meeting is normalised through the same function, so
    a booking through "ChiliCal Home" and a reassignment from it cannot end up
    spelled two ways.
    """
    if value is None or str(value).strip() == "":
        return DEFAULT_SURFACE
    text = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    if text not in SURFACES:
        raise ReassignError(f"unknown {field} {value!r}; it must be one of {', '.join(SURFACES)}")
    return text


# --------------------------------------------------------------------------- #
# The assignment axis: Distribution, Team, or Individual
# --------------------------------------------------------------------------- #

#: "You can change the **Distribution, Team,** or **Individual**." The three
#: granularities the reopened scheduler offers.
ASSIGNMENT_KINDS: tuple[str, ...] = ("individual", "team", "distribution")

#: ``individual`` names one host and is the researched "Reassign a booking to a
#: specific host" path. ``team`` and ``distribution`` name a group and leave the
#: host to be selected, which is the researched "auto-selected host" path and is
#: therefore the one the round-robin limit applies to.
SPECIFIC_MODE = "specific"
AUTO_MODE = "auto"
REASSIGN_MODES: tuple[str, ...] = (SPECIFIC_MODE, AUTO_MODE)

ASSIGNMENT_MODES: dict[str, str] = {
    "individual": SPECIFIC_MODE,
    "team": AUTO_MODE,
    "distribution": AUTO_MODE,
}

#: A meeting only accepts an assignment at a group granularity if it was a
#: round-robin booking, because the automatic path the group kinds map onto is
#: the researched ``reassign/auto`` and that is "Currently only supports
#: reassigning host for round robin bookings".
GROUP_ASSIGNMENT_KINDS: frozenset[str] = frozenset({"team", "distribution"})


def require_assignment_kind(value: Any) -> str:
    """Normalise and validate the assignment granularity."""
    text = str(value or "").strip().lower().replace("-", "_")
    if text not in ASSIGNMENT_KINDS:
        raise ReassignError(
            f"unknown assign_to kind {value!r}; the scheduler offers {', '.join(ASSIGNMENT_KINDS)}"
        )
    return text


def mode_for_kind(kind: str) -> str:
    """The Cal endpoint an assignment granularity maps onto."""
    return ASSIGNMENT_MODES[require_assignment_kind(kind)]


# --------------------------------------------------------------------------- #
# Locked and editable fields
# --------------------------------------------------------------------------- #

#: "You cannot change the Meeting Type or Workspace."
LOCKED_FIELDS: tuple[str, ...] = ("meeting_type", "workspace")

#: The editable side of the same sentence, plus the slot: step 4 of the flow is
#: "change the Distribution, Team, or Individual, pick a new slot, and book".
EDITABLE_FIELDS: tuple[str, ...] = ("distribution", "team", "individual", "starts_at")


# --------------------------------------------------------------------------- #
# Meeting status
# --------------------------------------------------------------------------- #

#: The status a Meetings Activity list filters on (step 1: "filters by Meeting
#: Type / Assignee / Booker / Status / product source"), plus the state
#: ``Mark as No-Show`` moves a meeting into.
MEETING_STATUSES: tuple[str, ...] = ("scheduled", "completed", "no_show", "cancelled")

#: The only status a meeting can be reassigned from. ``completed`` and
#: ``cancelled`` meetings are history, and ``no_show`` is reached only after the
#: meeting happened - which is exactly the state the researched no-show
#: credit-back interacts with, so reassigning from it is allowed and moves no
#: credit. See :func:`reassignable_statuses`.
DEFAULT_STATUS = "scheduled"

#: Statuses whose meetings can still be handed to a different host.
REASSIGNABLE_STATUSES: frozenset[str] = frozenset({"scheduled", "no_show"})

#: The one outcome that is not a refusal, named here so the decision vocabulary
#: has a single anchor every other module quotes.
ASSIGNED = "reassigned"


def require_status(value: Any, *, field: str = "status") -> str:
    text = str(value or DEFAULT_STATUS).strip().lower().replace("-", "_").replace(" ", "_")
    if text not in MEETING_STATUSES:
        raise ReassignError(
            f"unknown {field} {value!r}; it must be one of {', '.join(MEETING_STATUSES)}"
        )
    return text


# --------------------------------------------------------------------------- #
# The Events History tabs
# --------------------------------------------------------------------------- #

#: Step 1: the Meetings Activity list has an **Upcoming** (or **Past**) tab.
ACTIVITY_TABS: tuple[str, ...] = ("upcoming", "past", "all")

DEFAULT_TAB = "all"


def require_tab(value: Any) -> str:
    text = str(value or DEFAULT_TAB).strip().lower()
    if text not in ACTIVITY_TABS:
        raise ReassignError(
            f"unknown tab {value!r}; Meetings Activity has {', '.join(ACTIVITY_TABS)}"
        )
    return text


# --------------------------------------------------------------------------- #
# Field normalisation
# --------------------------------------------------------------------------- #


def normalise_key(value: Any) -> str:
    """A lowercase, dash-separated form used for meeting types and workspaces.

    "Demo" and "demo " are one meeting type, so a lock check cannot be walked
    past by changing the case of a locked field.
    """
    text = str(value or "").strip().lower()
    text = "_".join(text.split())
    return text


def require_locked(value: Any, *, field: str) -> str:
    """A Meeting Type or Workspace name. Required, because a lock has to match."""
    text = normalise_key(value)
    if not text:
        raise ReassignError(
            f"{field} is required; it cannot be changed but it is what the lock is checked against"
        )
    return text


# --------------------------------------------------------------------------- #
# The invite
# --------------------------------------------------------------------------- #

#: The host-dependent fields of an invite. "Chili Piper should update the invite
#: accordingly with the new assignee's name, links, and other details that
#: possibly changed from one assignee to another" - so every field here is one
#: that changes when the assignee changes, which is exactly the set worth
#: recording before and after.
INVITE_FIELDS: tuple[str, ...] = (
    "organizer",
    "organizer_email",
    "conference_link",
    "dial_in",
    "location",
)

#: The "Meeting conference-link icon" the research lists among the tools: one
#: clickable field rather than a free-text location.
CONFERENCE_LINK_FIELD = "conference_link"


def invite_for(host: dict[str, Any]) -> dict[str, Any]:
    """Build an invite from a host's own details.

    Every field is read from the host rather than carried over from the old
    invite, because the researched behaviour is that the invite takes the *new
    assignee's* name and links. A field the new host has not set is ``None``
    rather than the old host's value: carrying it over would leave one host's
    dial-in on another host's meeting, which is the failure the sentence exists
    to prevent.
    """
    return {
        "organizer": host.get("name") or None,
        "organizer_email": host.get("email") or None,
        "conference_link": host.get(CONFERENCE_LINK_FIELD) or None,
        "dial_in": host.get("dial_in") or None,
        "location": host.get("location") or None,
    }


def changed_invite_fields(before: dict[str, Any] | None, after: dict[str, Any] | None) -> list[str]:
    """Which invite fields the reassignment actually changed, in a stable order.

    Reported on the reassignment so a reader can see that the invite really did
    pick up the new assignee's links, rather than having to diff two blobs. An
    empty list is a legitimate and useful answer: two hosts can share a room's
    conference link.
    """
    before = before or {}
    after = after or {}
    return [field for field in INVITE_FIELDS if before.get(field) != after.get(field)]


# --------------------------------------------------------------------------- #
# The whole published vocabulary
# --------------------------------------------------------------------------- #


def published_vocabulary() -> dict[str, Any]:
    """Every researched term, with the sentence it comes from.

    Served as data, not as prose in the page, so the two cannot drift: the
    dropdowns render from this and the validator raises from the same tuples.
    """
    return {
        "surfaces": list(SURFACES),
        "surface_labels": dict(SURFACE_LABELS),
        "surfaces_requiring_addon": sorted(SURFACES_REQUIRING_ADDON),
        "assignment_kinds": list(ASSIGNMENT_KINDS),
        "assignment_modes": dict(ASSIGNMENT_MODES),
        "reassign_modes": list(REASSIGN_MODES),
        "group_assignment_kinds": sorted(GROUP_ASSIGNMENT_KINDS),
        "locked_fields": list(LOCKED_FIELDS),
        "editable_fields": list(EDITABLE_FIELDS),
        "meeting_statuses": list(MEETING_STATUSES),
        "reassignable_statuses": sorted(REASSIGNABLE_STATUSES),
        "activity_tabs": list(ACTIVITY_TABS),
        "invite_fields": list(INVITE_FIELDS),
        "defaults": {
            "surface": DEFAULT_SURFACE,
            "status": DEFAULT_STATUS,
            "tab": DEFAULT_TAB,
        },
        "evidence": {
            "editable_and_locked": EDITABLE_AND_LOCKED,
            "reassignment_is_a_new_booking": REASSIGNMENT_IS_A_NEW_BOOKING,
            "invite_updates_with_the_assignee": INVITE_UPDATES_WITH_THE_ASSIGNEE,
            "bounds_are_ignored": BOUNDS_ARE_IGNORED,
            "extension_must_be_installed_and_logged_in": EXTENSION_MUST_BE_INSTALLED_AND_LOGGED_IN,
            "reassigned_payload_keys": REASSIGNED_PAYLOAD_KEYS,
            "events_history_row": EVENTS_HISTORY_ROW,
            "booking_reassigned_scope": BOOKING_REASSIGNED_SCOPE,
            "auto_is_round_robin_only": AUTO_IS_ROUND_ROBIN_ONLY,
            "specific_host": SPECIFIC_HOST,
            "meeting_update_webhook": MEETING_UPDATE_WEBHOOK,
        },
    }
