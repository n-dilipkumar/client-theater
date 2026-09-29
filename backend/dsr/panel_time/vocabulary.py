"""The researched contract for WF-057, as data.

The research for this workflow is specific about the *wire* and silent about
almost everything around it. Two provider endpoints, two scopes, one request
header, two capacity knobs, and the sentence that makes the whole ranking
reproducible - "free=100%, unknown=49%, busy=0%". It says nothing about what a
panel record is called in this product, which granularity a candidate slot is
generated at, or what a rep reads when no slot works.

Everything in this module is therefore one of two things, and the difference is
kept visible:

* **sourced** - carried in a ``[sourced]``-marked constant, quoted from
  ``docs/research/digital-sales-room-workflows/wf/WF-057.md``, and traceable to
  the primary documentation the research cites;
* **designed** - this build's own vocabulary, always named in
  :mod:`dsr.panel_time.inferences` with the reasoning.

The reason for the split is not tidiness. The 100/49/0 weights and the 50-calendar
cap are numbers a deployment will hit in production, and a reader has to be able
to tell "Microsoft documents 49% for an unknown status" from "this build decided
49% is also a good warning threshold for us". :func:`describe` serves both groups
separately so a reviewer can disagree with one without arguing with the other.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- #
# Providers
# --------------------------------------------------------------------------- #

#: [sourced] The Google Calendar free/busy read.
PROVIDER_GOOGLE = "google"

#: [sourced] The Microsoft Graph ``findMeetingTimes`` read.
PROVIDER_GRAPH = "graph"

#: The two researched providers, in the order the research lists them.
PROVIDERS: tuple[str, ...] = (PROVIDER_GOOGLE, PROVIDER_GRAPH)

# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

#: [sourced] ``POST https://www.googleapis.com/calendar/v3/freeBusy`` with the
#: body ``{timeMin, timeMax, timeZone, groupExpansionMax, calendarExpansionMax,
#: items[{id}]}``, returning ``calendars[key].busy[]``.
GOOGLE_FREEBUSY_URL = "https://www.googleapis.com/calendar/v3/freeBusy"

#: [sourced] ``POST https://www.googleapis.com/calendar/v3/calendars/{calendarId}
#: /events`` - the commit of the chosen slot.
GOOGLE_EVENTS_PATH = "/calendar/v3/calendars/{calendarId}/events"

#: [sourced] ``POST /me/findMeetingTimes`` - the organizer-scoped Graph read.
GRAPH_FIND_MEETING_TIMES_PATH = "/me/findMeetingTimes"

#: [sourced] ``POST /users/{id|userPrincipalName}/findMeetingTimes`` - the
#: delegation-scoped Graph read.
GRAPH_FIND_MEETING_TIMES_BY_USER_PATH = "/users/{id|userPrincipalName}/findMeetingTimes"

# --------------------------------------------------------------------------- #
# Permissions and headers
# --------------------------------------------------------------------------- #

#: [sourced] "scope ``https://www.googleapis.com/auth/calendar.events.freebusy``
#: suffices" - the whole free/busy read needs this scope and nothing broader.
GOOGLE_FREEBUSY_SCOPE = "https://www.googleapis.com/auth/calendar.events.freebusy"

#: [sourced] The least-privileged *delegated* permission for the Graph read.
GRAPH_DELEGATED_SCOPE = "Calendars.Read.Shared"

#: [sourced] The Graph timezone header, ``Prefer: outlook.timezone``.
GRAPH_PREFER_HEADER = "outlook.timezone"

# --------------------------------------------------------------------------- #
# Availability weights - the sentence the whole ranking rests on
# --------------------------------------------------------------------------- #

#: [sourced] "For each attendee, a free status for a specified meeting time
#: period corresponds to 100% chance of attendance, unknown status 49%, and busy
#: status 0%."
ATTENDANCE_QUOTE = (
    "For each attendee, a free status for a specified meeting time period "
    "corresponds to 100% chance of attendance, unknown status 49%, and busy status 0%."
)

#: [sourced] The three statuses, and the attendance weight each one carries.
STATUS_FREE = "free"
STATUS_UNKNOWN = "unknown"
STATUS_BUSY = "busy"

#: The exact wording of the three, in the order the research gives them.
ATTENDANCE_STATUSES: tuple[str, ...] = (STATUS_FREE, STATUS_UNKNOWN, STATUS_BUSY)

#: [sourced] free=100, unknown=49, busy=0. Averaged over the invited set.
ATTENDANCE_WEIGHTS: dict[str, int] = {
    STATUS_FREE: 100,
    STATUS_UNKNOWN: 49,
    STATUS_BUSY: 0,
}

# --------------------------------------------------------------------------- #
# Documented limits
# --------------------------------------------------------------------------- #

#: [sourced] "Returns free/busy information for a set of calendars."
FREEBUSY_DESCRIPTION_QUOTE = "Returns free/busy information for a set of calendars."

#: [sourced] "Maximal number of calendars for which FreeBusy information is to be
#: provided. Optional. Maximum value is 50." Quoted by the research verbatim.
CALENDAR_EXPANSION_MAX_QUOTE = (
    "Maximal number of calendars for which FreeBusy information is to be provided. "
    "Optional. Maximum value is 50."
)

#: [sourced] The hard cap on ``calendarExpansionMax``. [inferred] The research
#: quotes the 50 for ``calendarExpansionMax`` and states "``groupExpansionMax``
#: (max 100) and ``calendarExpansionMax`` (max 50) are explicit capacity knobs",
#: so both caps are read as the vendor's, not as this build's.
CALENDAR_EXPANSION_MAX_LIMIT = 50

#: [sourced] The hard cap on ``groupExpansionMax``.
GROUP_EXPANSION_MAX_LIMIT = 100

#: The ceiling for a *search* on top of the vendor caps. [inferred] A panel that
#: invites more than this many calendars is a distribution-list paste, not a
#: panel, and the request this engine renders would be rejected upstream. The
#: vendor's own answer to the same problem is to raise ``calendarExpansionMax``,
#: which is already at its documented maximum.
MAX_SUGGESTIONS_LIMIT = 25

# --------------------------------------------------------------------------- #
# Time constraint
# --------------------------------------------------------------------------- #

#: [sourced] ``timeConstraint{activityDomain, timeSlots}``.
ACTIVITY_WORK = "work"
ACTIVITY_UNRESTRICTED = "unrestricted"

#: The two activity domains. [inferred] The research names the field and quotes
#: neither its values nor their meaning; these are the two Graph documents.
ACTIVITY_DOMAINS: tuple[str, ...] = (ACTIVITY_WORK, ACTIVITY_UNRESTRICTED)

#: [inferred] ``work`` excludes Saturday and Sunday. The research names
#: ``activityDomain`` and stops. See the ``activity-domain-work`` inference.
WORK_WEEKDAYS: tuple[int, ...] = (0, 1, 2, 3, 4)  # Monday=0, matching ``date.weekday()``

#: [sourced] ``meetingDuration`` is a parameter; this is the value a panel gets
#: when it does not declare one. [inferred] the default length.
DEFAULT_MEETING_DURATION = "PT1H"

#: [inferred] The grid candidate starts are laid on. The research says
#: "Ranked candidate slots are returned" without fixing a granularity, and a
#: 30-minute grid is what the two providers' own defaults imply.
DEFAULT_SLOT_INTERVAL = "PT30M"

#: [inferred] The hard cap on candidates a single search enumerates, so a panel
#: with a year-long window cannot make this service enumerate a million slots.
MAX_CANDIDATES_LIMIT = 5_000

# --------------------------------------------------------------------------- #
# Location constraint
# --------------------------------------------------------------------------- #

#: [sourced] the location constraint is a room, or "suggest a location".
LOCATION_ROOM = "room"
LOCATION_SUGGEST = "suggest"

#: The two location constraint types, taken from the research's own words:
#: "(room / "suggest a location")".
LOCATION_TYPES: tuple[str, ...] = (LOCATION_ROOM, LOCATION_SUGGEST)

# --------------------------------------------------------------------------- #
# Suggestion reasons
# --------------------------------------------------------------------------- #

#: [sourced] ``"suggestionReason": "Suggested because it is one of the nearest
#: times when all attendees are available."`` - quoted by the research verbatim,
#: and the reason served for a candidate every invited attendee is free for.
SUGGESTION_REASON_ALL_FREE = (
    "Suggested because it is one of the nearest times when all attendees are available."
)

#: [sourced] The research quotes the reason inside a Graph response body, so it is
#: carried with the JSON key it arrived under.
SUGGESTION_REASON_QUOTE = (
    f'"suggestionReason": "{SUGGESTION_REASON_ALL_FREE}"'
)

#: [sourced] The presence of ``suggestionReason`` is the ``returnSuggestionReasons``
#: toggle's whole effect: with it off, the key is not in the response at all.
RETURN_SUGGESTION_REASONS_DEFAULT = True

# --------------------------------------------------------------------------- #
# emptySuggestionsReason and the documented re-call
# --------------------------------------------------------------------------- #

#: [sourced] "If **findMeetingTimes** cannot return any meeting suggestions, the
#: response would indicate a reason in the **emptySuggestionsReason** property."
EMPTY_SUGGESTIONS_REASON_QUOTE = (
    "If findMeetingTimes cannot return any meeting suggestions, the response would "
    "indicate a reason in the emptySuggestionsReason property."
)

#: [sourced] "Based on this value, you can better adjust the parameters and call
#: **findMeetingTimes** again." - the automation the research calls documented.
RETUNE_QUOTE = (
    "Based on this value, you can better adjust the parameters and call findMeetingTimes again"
)

#: [sourced] "fine-tuned from time to time" - the drift note, and the reason a
#: commit re-reads availability instead of trusting a search from last week.
DRIFT_QUOTE = "fine-tuned from time to time"

#: [inferred] The reason vocabulary. The research names the *property* and its
#: use, and quotes no values; these are the values this engine can actually
#: derive from state it holds. See the ``empty-reason-vocabulary`` inference.
EMPTY_NONE = "none"
EMPTY_NOT_ORGANIZER = "notOrganizedAsAttendee"
EMPTY_NOT_ENOUGH_CALENDAR_FREE_TIME = "notEnoughCalendarFreeTime"
EMPTY_NOT_ENOUGH_PEOPLE_FREE = "notEnoughPeopleFree"
EMPTY_BUSY_SUGGESTIONS = "busySuggestions"

EMPTY_REASONS: tuple[str, ...] = (
    EMPTY_NONE,
    EMPTY_NOT_ORGANIZER,
    EMPTY_NOT_ENOUGH_CALENDAR_FREE_TIME,
    EMPTY_NOT_ENOUGH_PEOPLE_FREE,
    EMPTY_BUSY_SUGGESTIONS,
)

# --------------------------------------------------------------------------- #
# Ranking
# --------------------------------------------------------------------------- #

#: [sourced] "averaged **confidence** score, sorted high→low then chronologically".
#: The tie-break is part of the sentence, not a detail of it.
SORT_QUOTE = (
    "averaged confidence score, sorted high→low then chronologically"
)

#: [sourced] The researched ranking.
RANK_CONFIDENCE = "confidence"

#: [inferred] The researched extensibility claim made into a second ranker: a
#: deployment that layers its own scoring over the researched one names it here.
RANK_WEIGHTED = "weighted"

RANKERS: tuple[str, ...] = (RANK_CONFIDENCE, RANK_WEIGHTED)

# --------------------------------------------------------------------------- #
# Conference creation
# --------------------------------------------------------------------------- #

#: [sourced] step 5's "optionally creating a fresh conference".
CREATE_CONFERENCE_DEFAULT = True

#: [inferred] Google's ``conferenceData.createRequest`` shape, which the research
#: does not quote. See the ``conference-request-body`` inference.
GOOGLE_CONFERENCE_SOLUTION_KEY = {"type": "hangoutsMeet"}

# --------------------------------------------------------------------------- #
# The researched user flow
# --------------------------------------------------------------------------- #

#: [sourced] The five researched steps, carried as the flow this build serves.
USER_FLOW: tuple[str, ...] = (
    "User opens a \"find a time\" surface (in a sales room, a CRM record, or a "
    "scheduling page) and picks a set of participants + a date range.",
    "The app collects the attendees' email addresses and location constraints "
    "(room / \"suggest a location\").",
    "The app calls each attendee's calendar free/busy service (Google) or Graph "
    "findMeetingTimes (Microsoft).",
    "Ranked candidate slots are returned, each with a confidence percentage and a "
    "human-readable reason; the user picks one.",
    "On pick, the app creates the event on the organizer's calendar (optionally "
    "creating a fresh conference).",
)

# --------------------------------------------------------------------------- #
# The extensibility claim
# --------------------------------------------------------------------------- #

#: [sourced] "free/busy is decoupled from slot data - an integrator can layer
#: their own scoring/ranking, house rules (no Friday afternoons, no
#: back-to-back), or book into a room resource."
EXTENSIBILITY_QUOTE = (
    "free/busy is decoupled from slot data - an integrator can layer their own "
    "scoring/ranking, house rules (no Friday afternoons, no back-to-back), or book "
    "into a room resource."
)

#: The two house rules the research names by example, as keys this build
#: recognises. [inferred] the two rules are the research's; their meaning is not
#: stated, so both are implemented from the plainest reading and both are
#: overridable in the panel payload.
HOUSE_RULE_KEYS: tuple[str, ...] = (
    "earliest_start",
    "latest_end",
    "no_friday_after",
    "no_back_to_back",
    "blackouts",
    "weekdays",
)

# --------------------------------------------------------------------------- #
# What this build deliberately does not implement
# --------------------------------------------------------------------------- #

#: Adjacent surfaces the research names and this workflow does not implement,
#: with the reason each is out of scope. A reader who finds one missing should
#: read a decision rather than guess at an oversight.
ADJACENT_SURFACES: tuple[dict[str, str], ...] = (
    {
        "surface": "book into a room resource from the Graph resource/room directory",
        "why_not": (
            "The research lists the Graph resource/room directory as a data "
            "source and 'book into a room resource' as an extensibility claim, "
            "but sources no room-booking endpoint. A room is therefore accepted "
            "as a location constraint and a third kind of calendar, and the "
            "search honours its busy blocks; no room is booked here."
        ),
    },
    {
        "surface": "push-based availability invalidation (Google events.watch, Graph change notifications)",
        "why_not": (
            "The research's own gap list says so: 'I read freebusy.query (pull) "
            "but not Google events.watch / Graph change notifications, so "
            "\"availability sync\" in #7 is documented as a *pull* free/busy read. "
            "I did not verify a push-based availability invalidation flow.' Every "
            "read here is a pull, and a commit re-reads rather than trusting a "
            "stored answer."
        ),
    },
    {
        "surface": "section 8 of the domain research, embed a bookable calendar in the room",
        "why_not": (
            "That is WF-058's workflow. This one answers the *pre-booking* "
            "question - which slot - and hands the chosen slot to the commit."
        ),
    },
    {
        "surface": "section 12 of the domain research, route a requested slot for host approval",
        "why_not": (
            "That is WF-069's workflow. Step 5 of this flow says the event is "
            "created on pick; there is no approval gate in it."
        ),
    },
)

#: The research's own statement of what it could not substantiate, carried here
#: next to the numbers so a reader knows which figures are quoted and which
#: shapes are not.
SOURCED_GAPS: tuple[str, ...] = (
    "The research quotes no value for emptySuggestionsReason, only the property "
    "and its documented use. The reason vocabulary in this module is designed; "
    "every value it can produce is derived from state this engine holds.",
    "The research quotes no body for the Google events.insert commit, and no "
    "conferenceData shape. The rendered request is this build's reading of the "
    "documented API, and it is returned on every booking so it can be checked.",
    "The research names minAttendeePercentage in the data flow but does not say "
    "what it is measured against or whether the comparison is inclusive.",
    "The research gives no candidate-slot granularity, so the 30-minute grid in "
    "DEFAULT_SLOT_INTERVAL is this build's choice.",
    "The research states 'Meeting times and locations ... specified as "
    "parameters' but never defines a time zone for the working window, and no "
    "IANA database ships with this interpreter.",
    "The research lists the Graph resource/room directory as a data source but "
    "sources no endpoint for it, so no room directory is queried here.",
)

#: The evidence the research quotes, carried verbatim.
SOURCED_QUOTES: tuple[str, ...] = (
    "Suggest meeting times and locations based on organizer and attendee "
    "availability, and time or location constraints specified as parameters.",
    ATTENDANCE_QUOTE,
    EMPTY_SUGGESTIONS_REASON_QUOTE,
    SUGGESTION_REASON_QUOTE,
    FREEBUSY_DESCRIPTION_QUOTE,
    CALENDAR_EXPANSION_MAX_QUOTE,
    RETUNE_QUOTE,
    SORT_QUOTE,
    DRIFT_QUOTE,
    EXTENSIBILITY_QUOTE,
)


def _endpoints() -> dict[str, Any]:
    """The researched endpoints, keyed by provider dialect."""
    return {
        PROVIDER_GOOGLE: {
            "free_busy": GOOGLE_FREEBUSY_URL,
            "commit": GOOGLE_EVENTS_PATH,
            "method": "POST",
            "scope": GOOGLE_FREEBUSY_SCOPE,
            "request_body_fields": [
                "timeMin",
                "timeMax",
                "timeZone",
                "groupExpansionMax",
                "calendarExpansionMax",
                "items",
            ],
            "response": "calendars[key].busy[]",
            "per_calendar_failure": "calendars[key].errors[]",
        },
        PROVIDER_GRAPH: {
            "free_busy": GRAPH_FIND_MEETING_TIMES_PATH,
            "free_busy_by_user": GRAPH_FIND_MEETING_TIMES_BY_USER_PATH,
            "method": "POST",
            "scope": GRAPH_DELEGATED_SCOPE,
            "delegated": True,
            "headers": {f"Prefer": GRAPH_PREFER_HEADER},
            "request_body_fields": [
                "attendees",
                "timeConstraint",
                "locationConstraint",
                "meetingDuration",
                "minAttendeePercentage",
                "returnSuggestionReasons",
                "isOnlineMeeting",
            ],
            "response": "meetingTimeSuggestionsResult",
            "empty_reason": "meetingTimeSuggestionsResult.emptySuggestionsReason",
        },
    }


def _limits() -> dict[str, Any]:
    """The documented limits, each with the sentence that establishes it."""
    return {
        "calendarExpansionMax": {
            "max": CALENDAR_EXPANSION_MAX_LIMIT,
            "quote": CALENDAR_EXPANSION_MAX_QUOTE,
        },
        "groupExpansionMax": {
            "max": GROUP_EXPANSION_MAX_LIMIT,
            "quote": "groupExpansionMax (max 100) and calendarExpansionMax (max 50) "
            "are explicit capacity knobs.",
        },
        "max_suggestions": {
            "max": MAX_SUGGESTIONS_LIMIT,
            "quote": "[not sourced] no provider documents a suggestion-count cap; "
            "this is a guard on the enumeration, not a vendor limit.",
        },
        "max_candidates": {
            "max": MAX_CANDIDATES_LIMIT,
            "quote": "[not sourced] no provider enumerates slots; this bounds the "
            "grid this engine generates before it intersects a free/busy read.",
        },
    }


def _availability() -> dict[str, Any]:
    """The sourced status vocabulary, its weights, and the sentence behind them."""
    return {
        "quote": ATTENDANCE_QUOTE,
        "statuses": list(ATTENDANCE_STATUSES),
        "weights": dict(ATTENDANCE_WEIGHTS),
        "aggregation": (
            "[sourced] the data flow says 'averaged confidence score'. Averaged "
            "over the invited calendars, so an unreadable calendar's 49% counts "
            "against the slot exactly as a busy one's 0% does."
        ),
    }


def describe() -> dict[str, Any]:
    """The whole researched contract, served over HTTP by the feature module."""
    return {
        "ticket": "WF-057",
        "providers": list(PROVIDERS),
        "endpoints": _endpoints(),
        "limits": _limits(),
        "availability": _availability(),
        "activity_domains": list(ACTIVITY_DOMAINS),
        "location_types": list(LOCATION_TYPES),
        "rankers": list(RANKERS),
        "empty_suggestion_reasons": list(EMPTY_REASONS),
        "user_flow": [{"step": index + 1, "text": text} for index, text in enumerate(USER_FLOW)],
        "extensibility": {
            "quote": EXTENSIBILITY_QUOTE,
            "house_rule_keys": list(HOUSE_RULE_KEYS),
            "decoupled": (
                "The free/busy read and the slot enumeration are separate "
                "functions with no shared state: a caller can hand "
                "dsr.panel_time.slots.evaluate a busy map and get a ranked, "
                "scored shortlist without a calendar registry, a provider, or a "
                "store. That is the research's claim made into an interface."
            ),
        },
        "suggestion_reasons": {
            "all_free": SUGGESTION_REASON_ALL_FREE,
            "quote": SUGGESTION_REASON_QUOTE,
            "toggle": "returnSuggestionReasons",
            "toggle_default": RETURN_SUGGESTION_REASONS_DEFAULT,
        },
        "empty_suggestions": {
            "property": "emptySuggestionsReason",
            "quote": EMPTY_SUGGESTIONS_REASON_QUOTE,
            "retune_quote": RETUNE_QUOTE,
            "drift_quote": DRIFT_QUOTE,
        },
        "adjacent_surfaces": [dict(entry) for entry in ADJACENT_SURFACES],
        "sourced_gaps": list(SOURCED_GAPS),
        "sourced_quotes": list(SOURCED_QUOTES),
    }
