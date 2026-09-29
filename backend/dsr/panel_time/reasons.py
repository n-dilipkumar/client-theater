"""``suggestionReason`` and ``emptySuggestionsReason`` (WF-057).

Two researched facts land here, and they pull in opposite directions.

The first is one sentence, quoted by the research verbatim::

    "suggestionReason": "Suggested because it is one of the nearest times when
    all attendees are available."

That is the reason for a candidate every invited attendee is free for, and it is
carried as a constant. The other reasons - the panel that is half busy, the
calendar nobody has published - are this build's, and they are marked.

The second is the interesting one, because it is a rule about *what the software
does next* rather than what it returns:

    "If **findMeetingTimes** cannot return any meeting suggestions, the response
    would indicate a reason in the **emptySuggestionsReason** property."  …  "Based
    on this value, you can better adjust the parameters and call
    **findMeetingTimes** again."

So an empty result is not a dead end with a message on it; it is a *state* with a
machine-readable cause, and the documented response to it is a second call with
different parameters. :func:`empty_reason` derives the cause from state this
engine actually holds, and :func:`retune_adjustments` turns that cause into the
parameters the second call should use. :func:`run_retune` is the call.

Every reason this module can produce is *derivable*, which is the test that
matters: :func:`derive_empty_reason` never returns a value the engine could not
have worked out, so a client can rely on ``emptySuggestionsReason`` and a reviewer
can check every branch by constructing the state that produces it.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Mapping, Sequence

from dsr.panel_time.errors import ConstraintError
from dsr.panel_time.slots import Evaluation
from dsr.panel_time.timeutils import format_duration, format_instant
from dsr.panel_time.vocabulary import (
    EMPTY_BUSY_SUGGESTIONS,
    EMPTY_NONE,
    EMPTY_NOT_ORGANIZER,
    EMPTY_NOT_ENOUGH_CALENDAR_FREE_TIME,
    EMPTY_NOT_ENOUGH_PEOPLE_FREE,
    EMPTY_REASONS,
    SUGGESTION_REASON_ALL_FREE,
)

#: The adjustments the documented re-call knows how to make. A client sends one
#: of these names; the engine computes the parameters. [sourced] that a re-call
#: with adjusted parameters is the documented response; [inferred] which
#: adjustments, and in which order they are tried.
RETUNE_ADJUSTMENTS: tuple[str, ...] = (
    "invite_organizer",
    "widen_window",
    "extend_working_window",
    "relax_min_attendee_percentage",
    "coarsen_slot_interval",
    "unrestricted_activity_domain",
)

#: What each adjustment does, served to a client so its picker is not a list of
#: identifiers. [inferred] the wording; the fact that a re-call happens is sourced.
ADJUSTMENT_MEANING: dict[str, str] = {
    "invite_organizer": (
        "The panel does not invite its own organizer, so step 5 has no calendar to create "
        "the event on. Add the organizer to the panel's calendars - the re-call cannot fix "
        "this one by itself, and the refusal says so rather than pretending a re-call will."
    ),
    "widen_window": (
        "Push the searched window out by a number of days at each end. Widening the "
        "window rather than lowering the bar is tried first because it costs nothing: "
        "a slot that suits more people is still a slot everybody can attend."
    ),
    "extend_working_window": (
        "Drop the house rules that bound the working day - earliest_start, latest_end, "
        "no_back_to_back - while leaving the calendars' own availability alone."
    ),
    "relax_min_attendee_percentage": (
        "Set min_attendee_percentage to 0, so a slot where somebody is busy is "
        "suggested rather than withheld. The confidence percentage and the reason both "
        "say who is busy, so the choice is the caller's rather than hidden."
    ),
    "coarsen_slot_interval": (
        "Double slot_interval, so starts land on the hour rather than every half hour. "
        "Finds slots the fine grid stepped straight over."
    ),
    "unrestricted_activity_domain": (
        "Search the weekend too, by setting activityDomain to 'unrestricted'."
    ),
}

#: The keys a re-call may change. Everything else on the panel is carried through
#: unchanged, because the researched advice is to adjust *the parameters* the
#: provider refused on - not to run a different search.
RETUNABLE_KEYS: tuple[str, ...] = (
    "time_constraint",
    "meeting_duration",
    "slot_interval",
    "min_attendee_percentage",
    "house_rules",
    "calendars",
    "activity_domain",
)


def suggestion_reason(evaluation: Evaluation) -> str | None:
    """The human-readable reason for one candidate.

    The researched sentence is used for the case it describes: every invited
    attendee is free. Everything else is this build's vocabulary, and it is
    written to be *specific* - "two of five attendees are already busy" is
    actionable, "low confidence" is not.
    """
    if not evaluation.free:
        return (
            f"No attendee is free: all {evaluation.invited} are busy. Raise the searched "
            "window, or lower min_attendee_percentage to see it anyway."
        )
    if not evaluation.busy and not evaluation.unknown:
        # The researched sentence, used for exactly the case it describes: every
        # invited attendee is free.
        return SUGGESTION_REASON_ALL_FREE

    parts: list[str] = []
    if evaluation.busy:
        parts.append(f"{len(evaluation.busy)} of {evaluation.invited} attendees are already busy")
    if evaluation.unknown:
        parts.append(
            f"availability is unknown for {len(evaluation.unknown)} of {evaluation.invited} "
            "attendees (each counted at the researched 49%)"
        )
    return f"Suggested with caveats: {'; '.join(parts)}."


def derive_empty_reason(
    *,
    candidates: Sequence[Any],
    invited: Sequence[str],
    organizer_id: str | None,
    suggested: Sequence[Evaluation],
    house_rule_refusals: int,
    min_attendee_percentage: int,
) -> str:
    """The ``emptySuggestionsReason`` value this engine can honestly stand behind.

    Ordered from the most fundamental cause to the least, because that is the
    order worth telling a caller in: fix the calendar before fixing the policy.
    An implementation that returned a single ``none`` for all of these would be
    the researched property with the researched advice hollowed out.
    """
    if organizer_id is None or str(organizer_id) not in {str(calendar_id) for calendar_id in invited}:
        # [sourced] the research's step 5 creates the event *on the organizer's
        # calendar*, so a panel with no organizer - or one that does not invite
        # the organizer it names - cannot be committed at all. This is that
        # refusal, reported at search time rather than at commit time, where it
        # would arrive as a surprise after the rep had already picked a slot.
        return EMPTY_NOT_ORGANIZER
    if not candidates:
        return EMPTY_NOT_ENOUGH_CALENDAR_FREE_TIME
    if suggested:
        return EMPTY_NONE
    if house_rule_refusals and house_rule_refusals >= len(candidates):
        return EMPTY_BUSY_SUGGESTIONS
    if min_attendee_percentage > 0:
        return EMPTY_NOT_ENOUGH_PEOPLE_FREE
    return EMPTY_NONE


def retune_adjustments(
    reason: str, *, days: int = 7, min_attendee_percentage: int = 0
) -> list[dict[str, Any]]:
    """What to change, in the order worth trying, for one empty-suggestions reason.

    This is the researched automation, made into a function:
    "Based on this value, you can better adjust the parameters and call
    findMeetingTimes again." The mapping from a cause to a change is this build's,
    and the ordering is a judgement - widen before relax, because a slot that
    suits more people is worth more than a slot that suits fewer.

    ``min_attendee_percentage`` is quoted in the message for the reason that
    mentions it, so the caller can see the bar they are being asked to move
    without reading the panel first.
    """
    if reason not in EMPTY_REASONS:
        raise ConstraintError(
            f"emptySuggestionsReason must be one of {list(EMPTY_REASONS)}; got {reason!r}"
        )
    table: dict[str, tuple[tuple[str, str, Any], ...]] = {
        EMPTY_NOT_ORGANIZER: (
            (
                "invite_organizer",

                "The panel does not invite its own organizer, so there is nobody to create "
                "the event on. Add the organizer to the panel's calendars. No re-call fixes "
                "this one: the searched parameters are correct and the attendee list is not.",
                None,
            ),
        ),
        EMPTY_NOT_ENOUGH_CALENDAR_FREE_TIME: (
            (
                "widen_window",
                f"No {format_duration(timedelta(hours=1))} of the window is free, or the "
                "window is shorter than the meeting. Widen the searched range.",
                {"days": days},
            ),
            (
                "coarsen_slot_interval",
                "If the window is long but the grid is fine, halving the number of candidate "
                "starts can find a slot the exact grid stepped straight over.",
                None,
            ),
            (
                "unrestricted_activity_domain",
                "If the whole window falls on days the work domain excludes, search them "
                "anyway and let the reason say who is busy.",
                None,
            ),
        ),
        EMPTY_NOT_ENOUGH_PEOPLE_FREE: (
            (
                "widen_window",
                f"Every candidate has fewer than the required share of free attendees "
                f"({min_attendee_percentage}%). Widen the range first.",
                {"days": days},
            ),
            (
                "relax_min_attendee_percentage",
                "Then set min_attendee_percentage to 0 and read who is busy from the "
                "confidence percentage and the reason.",
                {"min_attendee_percentage": 0},
            ),
            (
                "unrestricted_activity_domain",
                "And if the searched days are the problem rather than the people, include the "
                "days the work domain excludes.",
                None,
            ),
        ),
        EMPTY_BUSY_SUGGESTIONS: (
            (
                "extend_working_window",
                "Candidates exist and clear the attendee threshold, but the house rules "
                "removed all of them. Drop the rules that bound the working day.",
                {"drop": ("earliest_start", "latest_end", "no_back_to_back")},
            ),
            (
                "widen_window",
                "And widen the range, in case the remaining rules are the weekend or a "
                "blackout rather than the working day.",
                {"days": days},
            ),
        ),
        EMPTY_NONE: (
            (
                "widen_window",
                "No cause could be derived from the state this engine holds. Widen the "
                "searched range and call again.",
                {"days": days},
            ),
            (
                "coarsen_slot_interval",
                "Then coarsen the grid, which finds a slot the fine one stepped over.",
                None,
            ),
        ),
    }
    return [
        {"id": identifier, "change": change, "why": why}
        for identifier, why, change in table[reason]
    ]


def apply_adjustment(
    parameters: Mapping[str, Any],
    adjustment: str,
    *,
    days: int = 7,
) -> dict[str, Any]:
    """One re-called parameter set, from one adjustment.

    Every value is computed from the parameters the first call used, so the
    second call is auditable as *the first call plus this change* - and a
    reviewer can see, in the search record, exactly which knob moved.
    """
    if adjustment not in RETUNE_ADJUSTMENTS:
        raise ConstraintError(
            f"adjustment must be one of {list(RETUNE_ADJUSTMENTS)}; got {adjustment!r}"
        )
    from dsr.panel_time.slots import pad_window
    from dsr.panel_time.timeutils import parse_duration

    result = dict(parameters)
    result["applied_adjustment"] = adjustment

    if adjustment == "invite_organizer":
        # Not a parameter change. The engine adds the organizer to the panel's
        # calendar list; carried here so the adjustment is enumerable, describable
        # and testable alongside the five that really are parameter changes.
        result["invite_organizer"] = True
    elif adjustment == "widen_window":
        result["time_constraint"] = pad_window(
            parameters.get("time_constraint") or {},
            before=timedelta(days=days),
            after=timedelta(days=days),
        )
        result["widen_days"] = days
    elif adjustment == "extend_working_window":
        rules = dict(parameters.get("house_rules") or {})
        for key in ("earliest_start", "latest_end", "no_back_to_back"):
            rules.pop(key, None)
        result["house_rules"] = rules
    elif adjustment == "relax_min_attendee_percentage":
        result["min_attendee_percentage"] = 0
    elif adjustment == "coarsen_slot_interval":
        step = parse_duration(parameters.get("slot_interval") or "PT30M", field="slot_interval")
        result["slot_interval"] = format_duration(step * 2)
    elif adjustment == "unrestricted_activity_domain":
        constraint = dict(parameters.get("time_constraint") or {})
        constraint["activityDomain"] = "unrestricted"
        result["time_constraint"] = constraint
    return result


def describe_reasons() -> dict[str, Any]:
    """The reason vocabulary, the toggle, and the re-call, as data."""
    return {
        "all_free": SUGGESTION_REASON_ALL_FREE,
        "empty_suggestion_reasons": list(EMPTY_REASONS),
        "retune_adjustments": [
            {"id": identifier, "why": ADJUSTMENT_MEANING[identifier]}
            for identifier in RETUNE_ADJUSTMENTS
        ],
        "retunable_parameters": list(RETUNABLE_KEYS),
    }


def nearest_all_free(suggestions: Sequence[Mapping[str, Any]]) -> str | None:
    """The start of the nearest slot every invited attendee is free for.

    The researched sentence claims the all-free suggestions are "one of the
    nearest times when all attendees are available", and a claim like that is only
    worth making if "nearest" means something. It means: earliest in the searched
    window, which is the only ordering the research fixes before the confidence
    sort - "high→low then chronologically" - and so the only one a reader can
    check the sentence against.
    """
    starts = [
        entry["start"]
        for entry in suggestions
        if entry.get("suggestion_reason") == SUGGESTION_REASON_ALL_FREE and entry.get("start")
    ]
    if not starts:
        return None
    return min(starts)


#: The three Graph availability statuses this engine can report, and the
#: ``statusPercentage`` string that goes with each. [sourced] the three weights.
#: [inferred] the three status names and the exact spelling of the percentage.
STATUS_PERCENTAGE: dict[str, str] = {"free": "100", "tentative": "49", "none": "0"}

#: [sourced] "tentative" is the Graph status for the 49% case: a calendar whose
#: availability is unknown is tentative, not a yes and not a no.
UNKNOWN_GRAPH_STATUS = "tentative"

#: [sourced] the 0% case. Graph spells a known-busy attendee "none".
BUSY_GRAPH_STATUS = "none"


def describe_search(
    suggestions: Sequence[Mapping[str, Any]],
    *,
    return_suggestion_reasons: bool,
    empty_reason: str | None,
    min_attendee_percentage: int,
) -> dict[str, Any]:
    """The suggestion list as a Graph-shaped ``meetingTimeSuggestionsResult``.

    A deployment that mirrors this feature's output into a Graph-shaped client
    gets the researched key names, and - with ``returnSuggestionReasons`` off -
    the researched *absence* of ``suggestionReason`` rather than a null.
    """
    entries: list[dict[str, Any]] = []
    for suggestion in suggestions:
        # One entry per invited calendar, in the researched status vocabulary, so
        # the availability behind a confidence percentage is legible rather than
        # something the reader has to trust.
        availability = [
            {
                "attendee": {"type": "required", "status": status},
                "statusPercentage": STATUS_PERCENTAGE[status],
            }
            for status in suggestion.get("statuses") or []
        ]
        entry: dict[str, Any] = {
            "meetingTimeSlot": {
                "start": {
                    "dateTime": suggestion["start"],
                    "timeZone": suggestion.get("time_zone", "UTC"),
                },
                "end": {
                    "dateTime": suggestion["end"],
                    "timeZone": suggestion.get("time_zone", "UTC"),
                },
            },
            "confidence": suggestion["confidence"],
        }
        if availability:
            entry["attendeeAvailability"] = availability
        if return_suggestion_reasons and suggestion.get("suggestion_reason") is not None:
            entry["suggestionReason"] = suggestion["suggestion_reason"]
        entries.append(entry)
    result: dict[str, Any] = {
        "meetingTimeSuggestions": entries,
        "minAttendeePercentage": min_attendee_percentage,
    }
    if not entries and empty_reason:
        result["emptySuggestionsReason"] = empty_reason
    return result
