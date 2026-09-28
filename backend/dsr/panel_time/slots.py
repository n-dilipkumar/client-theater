"""Candidate slots, the researched confidence score, and the researched sort (WF-057).

This is the arithmetic the research specifies exactly, and nothing in this module
knows what a calendar *is*:

    "attendee emails + ``timeConstraint{activityDomain, timeSlots}`` +
    ``locationConstraint`` + ``meetingDuration`` + ``minAttendeePercentage`` →
    provider free/busy read → per-attendee availability (free=100%,
    unknown=49%, busy=0%) → averaged **confidence** score, sorted high→low then
    chronologically → ``meetingTimeSuggestions[]`` with ``suggestionReason``."

:func:`enumerate_candidates` lays the grid out of ``timeSlots`` and
``meetingDuration``. :func:`evaluate` intersects it with a
:class:`~dsr.panel_time.calendar.BusyMap` and produces the researched confidence
number. :func:`rank` applies the researched order, and the alternative order the
extensibility claim invites.

Nothing here needs a store, a provider, a room, or an HTTP layer. That is
deliberate: it is the seam the research's own extensibility sentence describes,
and a test that exercises the whole ranking needs nothing but a busy map and a
window.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence

from dsr.panel_time.calendar import BusyMap
from dsr.panel_time.errors import ConstraintError, LimitExceeded
from dsr.panel_time.timeutils import (
    Clock,
    format_duration,
    format_instant,
    overlaps,
    parse_clock_time,
    parse_duration,
    parse_instant,
)
from dsr.panel_time.vocabulary import (
    ACTIVITY_DOMAINS,
    ACTIVITY_UNRESTRICTED,
    ATTENDANCE_WEIGHTS,
    DEFAULT_MEETING_DURATION,
    DEFAULT_SLOT_INTERVAL,
    HOUSE_RULE_KEYS,
    MAX_CANDIDATES_LIMIT,
    RANK_CONFIDENCE,
    RANK_WEIGHTED,
    RANKERS,
    STATUS_BUSY,
    STATUS_FREE,
    STATUS_UNKNOWN,
    WORK_WEEKDAYS,
)

#: The researched aggregate: round half away from zero to a whole percentage, so
#: the confidence badge is an integer a person can be shown. [inferred] The
#: research says "averaged confidence score" and never says whether it is an
#: integer; see the ``confidence-rounding`` inference.
def round_percentage(value: float) -> int:
    return int(math.floor(value + 0.5)) if value >= 0 else -int(math.floor(-value + 0.5))


@dataclass(frozen=True)
class Candidate:
    """One grid position: a possible start, and the end the duration implies."""

    start: datetime
    end: datetime
    slot_index: int

    def describe(self) -> dict[str, Any]:
        return {"start": format_instant(self.start), "end": format_instant(self.end)}


@dataclass(frozen=True)
class Evaluation:
    """One candidate scored against one availability read."""

    candidate: Candidate
    confidence: int
    free: tuple[str, ...]
    busy: tuple[str, ...]
    unknown: tuple[str, ...]
    meets_threshold: bool
    score: int
    penalty: int = 0

    @property
    def invited(self) -> int:
        return len(self.free) + len(self.busy) + len(self.unknown)

    @property
    def free_percentage(self) -> int:
        if not self.invited:
            return 0
        return round_percentage(len(self.free) * 100 / self.invited)

    def describe(self, *, return_suggestion_reasons: bool = True, reason: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            **self.candidate.describe(),
            "confidence": self.confidence,
            "score": self.score,
            "free_percentage": self.free_percentage,
            "invited": self.invited,
            "free": list(self.free),
            "busy": list(self.busy),
            "unknown": list(self.unknown),
        }
        if return_suggestion_reasons and reason is not None:
            payload["suggestion_reason"] = reason
        return payload


# --------------------------------------------------------------------------- #
# Time constraint
# --------------------------------------------------------------------------- #


def normalise_time_constraint(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    """``timeConstraint{activityDomain, timeSlots}`` as this engine reads it.

    [sourced] the field names. The Graph shape each slot is written in -
    ``{"start": {"dateTime": ..., "timeZone": ...}, "end": {...}}`` - is read
    verbatim, and the flat ``{"start": "…", "end": "…"}`` is accepted too,
    because a panel is arbitrary JSON and an operator who writes it by hand
    writes it flat.
    """
    if raw is None:
        raise ConstraintError("time_constraint is required; it carries the timeSlots to search")
    if not isinstance(raw, Mapping):
        raise ConstraintError(f"time_constraint must be an object; got {type(raw).__name__}")

    domain = str(raw.get("activityDomain") or raw.get("activity_domain") or "work")
    if domain not in ACTIVITY_DOMAINS:
        raise ConstraintError(
            f"activityDomain must be one of {list(ACTIVITY_DOMAINS)}; got {domain!r}"
        )

    slots_raw = raw.get("timeSlots")
    if slots_raw is None:
        slots_raw = raw.get("time_slots")
    if not isinstance(slots_raw, (list, tuple)) or not slots_raw:
        raise ConstraintError("time_constraint.timeSlots must be a non-empty list of intervals")

    slots: list[dict[str, Any]] = []
    for index, entry in enumerate(slots_raw):
        if not isinstance(entry, Mapping):
            raise ConstraintError(f"timeSlots[{index}] must be an object; got {type(entry).__name__}")
        begin = _instant_of(entry.get("start"), f"timeSlots[{index}].start")
        finish = _instant_of(entry.get("end"), f"timeSlots[{index}].end")
        if finish <= begin:
            raise ConstraintError(
                f"timeSlots[{index}] ends at or before it starts ({format_instant(begin)} to "
                f"{format_instant(finish)}); a searched window must be a real interval"
            )
        zone = str(entry.get("timeZone") or entry.get("time_zone") or raw.get("timeZone") or "UTC")
        slots.append(
            {
                "start": {"dateTime": format_instant(begin), "timeZone": zone},
                "end": {"dateTime": format_instant(finish), "timeZone": zone},
            }
        )
    slots.sort(key=lambda slot: (slot["start"]["dateTime"], slot["end"]["dateTime"]))
    return {"activityDomain": domain, "timeSlots": slots}


def _instant_of(value: object, label: str) -> datetime:
    if isinstance(value, Mapping):
        value = value.get("dateTime") or value.get("date_time")
    if value is None:
        raise ConstraintError(f"{label} is required and must be an RFC 3339 instant")
    return parse_instant(value, field=label)


def enumerate_candidates(
    time_constraint: Mapping[str, Any],
    *,
    meeting_duration: str = DEFAULT_MEETING_DURATION,
    slot_interval: str = DEFAULT_SLOT_INTERVAL,
    clock: Clock | None = None,
) -> list[Candidate]:
    """The grid: every start in every time slot that fits the whole meeting.

    Three researched inputs, three rules:

    * A slot only yields starts where ``start + meetingDuration`` is still inside
      the slot. A time slot is the window the caller said the meeting could
      happen in, and a meeting that hangs out of it is not a candidate.
    * The step is ``slot_interval``. [inferred] the research names neither the
      granularity nor a ``slotInterval`` parameter, and 30 minutes is what both
      providers' own defaults imply.
    * ``activityDomain: "work"`` drops Saturday and Sunday. [inferred] the
      research names ``activityDomain`` and says nothing about what ``work``
      excludes; see the ``activity-domain-work`` inference.
    """
    duration = parse_duration(meeting_duration, field="meeting_duration")
    step = parse_duration(slot_interval, field="slot_interval")
    if step > duration:
        # Equal is fine and is the common case: an hourly meeting searched on the
        # hour is PT1H duration on a PT1H grid, and that enumerates one candidate
        # per hour in the window. Only a step *coarser* than the meeting is a
        # problem, because it steps clean over every slot the meeting fits in and
        # the search silently returns nothing for a window that is full of room.
        raise ConstraintError(
            f"slot_interval ({format_duration(step)}) is coarser than meeting_duration "
            f"({format_duration(duration)}), so it steps over slots the meeting fits in and "
            "the search would return nothing for a window that has room. Use an interval "
            "no longer than the meeting."
        )
    zone = clock or Clock.resolve(str(time_constraint.get("timeZone") or "UTC"))
    domain = str(time_constraint.get("activityDomain") or "work")
    weekdays = None if domain == ACTIVITY_UNRESTRICTED else set(WORK_WEEKDAYS)

    candidates: list[Candidate] = []
    for slot_index, slot in enumerate(time_constraint.get("timeSlots") or []):
        begin = _instant_of(slot.get("start"), "timeSlots[].start")
        finish = _instant_of(slot.get("end"), "timeSlots[].end")
        cursor = begin
        while cursor + duration <= finish:
            if weekdays is None or zone.local(cursor).weekday() in weekdays:
                candidates.append(Candidate(cursor, cursor + duration, slot_index))
                if len(candidates) > MAX_CANDIDATES_LIMIT:
                    raise LimitExceeded(
                        f"this time window enumerates more than {MAX_CANDIDATES_LIMIT} candidate "
                        f"slots at a {format_duration(step)} grid. Narrow the date range, or "
                        "coarsen slot_interval."
                    )
            cursor += step
    return candidates


# --------------------------------------------------------------------------- #
# The researched confidence score
# --------------------------------------------------------------------------- #


def evaluate(
    candidates: Sequence[Candidate],
    availability: BusyMap,
    invited: Sequence[str],
    *,
    min_attendee_percentage: int = 0,
) -> list[Evaluation]:
    """Per-attendee availability, then the researched average.

    The arithmetic is the research's sentence, exactly:

    * a candidate is ``free`` for a calendar with no busy block overlapping it,
      ``busy`` for one with, and ``unknown`` for one the read could not answer;
    * the weights are 100, 0 and 49 respectively - and the *unknown* weight sits
      between them, which is the whole point: a calendar nobody has published is
      better than a calendar that is busy, and worse than one that is free;
    * the confidence is the **mean over the invited set**, not the mean over the
      calendars that happened to answer. Dropping the unanswerable ones from the
      denominator would turn "we could not read three of these calendars" into
      "100% confident", which is the opposite of what it means.
    * ``min_attendee_percentage`` is compared against the share of invited
      calendars that are **free**, and the comparison is inclusive.
    """
    if not isinstance(min_attendee_percentage, int) or isinstance(min_attendee_percentage, bool):
        raise ConstraintError("min_attendee_percentage must be an integer")
    if not 0 <= min_attendee_percentage <= 100:
        raise ConstraintError(
            f"min_attendee_percentage must be between 0 and 100; got {min_attendee_percentage}"
        )

    roster = [str(calendar_id) for calendar_id in invited]
    if not roster:
        raise ConstraintError("a panel must invite at least one calendar; the average is over it")

    results: list[Evaluation] = []
    for candidate in candidates:
        free: list[str] = []
        busy: list[str] = []
        unknown: list[str] = []
        for calendar_id in roster:
            if availability.is_unreadable(calendar_id):
                unknown.append(calendar_id)
            elif any(
                overlaps(candidate.start, candidate.end, block_start, block_end)
                for block_start, block_end in availability.blocks(calendar_id)
            ):
                busy.append(calendar_id)
            else:
                free.append(calendar_id)

        confidence = round_percentage(
            (
                ATTENDANCE_WEIGHTS[STATUS_FREE] * len(free)
                + ATTENDANCE_WEIGHTS[STATUS_UNKNOWN] * len(unknown)
                + ATTENDANCE_WEIGHTS[STATUS_BUSY] * len(busy)
            )
            / len(roster)
        )
        free_share = round_percentage(len(free) * 100 / len(roster))
        results.append(
            Evaluation(
                candidate=candidate,
                confidence=confidence,
                free=tuple(free),
                busy=tuple(busy),
                unknown=tuple(unknown),
                meets_threshold=free_share >= min_attendee_percentage,
                score=confidence,
            )
        )
    return results


# --------------------------------------------------------------------------- #
# House rules - the extensibility claim, made into a filter
# --------------------------------------------------------------------------- #


def normalise_house_rules(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    """The two house rules the research names, plus the obvious neighbours.

    [sourced] "house rules (no Friday afternoons, no back-to-back)" are named as
    the example; [inferred] what they mean and the three keys shipped beside
    them. Every key is optional and unvalidated beyond its own type, so a
    deployment can add a key of its own without this module knowing.
    """
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ConstraintError(f"house_rules must be an object; got {type(raw).__name__}")
    rules: dict[str, Any] = {}
    for key in ("earliest_start", "latest_end", "no_friday_after"):
        if raw.get(key) is not None:
            parse_clock_time(raw[key], field=f"house_rules.{key}")
            rules[key] = str(raw[key])
    if raw.get("no_back_to_back") is not None:
        # A real boolean, not a truthiness test. ``"false"`` is truthy, so
        # reading it as a flag would silently *enable* the rule for a payload that
        # turned it off - the same class of bug as reading a researched toggle
        # with ``bool()``.
        value = raw["no_back_to_back"]
        if not isinstance(value, bool):
            raise ConstraintError(
                f"house_rules.no_back_to_back must be true or false; got {value!r}"
            )
        rules["no_back_to_back"] = value
    if raw.get("weekdays") is not None:
        days = raw["weekdays"]
        if not isinstance(days, (list, tuple)) or not all(
            isinstance(day, int) and 0 <= day <= 6 for day in days
        ):
            raise ConstraintError("house_rules.weekdays must be a list of weekday numbers, 0=Monday")
        rules["weekdays"] = [int(day) for day in days]
    blackouts = raw.get("blackouts")
    if blackouts is not None:
        if not isinstance(blackouts, (list, tuple)):
            raise ConstraintError("house_rules.blackouts must be a list of {from, to} intervals")
        rules["blackouts"] = [
            {
                "from": format_instant(parse_instant(entry.get("from"), field="blackouts[].from")),
                "to": format_instant(parse_instant(entry.get("to"), field="blackouts[].to")),
            }
            for entry in blackouts
        ]
    unknown = sorted(set(raw) - set(HOUSE_RULE_KEYS) - set(rules))
    if unknown:
        # Carried through untouched rather than refused. The research's claim is
        # that an integrator layers *their* rules; a key this build has never
        # heard of is that integrator, not an error.
        rules["extra"] = {key: raw[key] for key in unknown}
    return rules


def apply_house_rules(
    candidates: Sequence[Candidate],
    rules: Mapping[str, Any],
    availability: BusyMap,
    invited: Sequence[str],
    *,
    clock: Clock | None = None,
) -> tuple[list[Candidate], list[dict[str, Any]]]:
    """Split candidates into the ones the house allows and the ones it refuses.

    Each refusal names the rule that refused it and the candidate it refused, so
    a rep who cannot find a Friday afternoon slot is *told* it is a house rule
    rather than left to conclude the calendars were busy.
    """
    if not rules:
        return list(candidates), []
    zone = clock or Clock.resolve("UTC")
    allowed: list[Candidate] = []
    refused: list[dict[str, Any]] = []

    earliest = parse_clock_time(rules["earliest_start"], field="house_rules.earliest_start") if rules.get("earliest_start") else None
    latest = parse_clock_time(rules["latest_end"], field="house_rules.latest_end") if rules.get("latest_end") else None
    friday_after = parse_clock_time(rules["no_friday_after"], field="house_rules.no_friday_after") if rules.get("no_friday_after") else None
    weekdays = set(rules.get("weekdays") or ())
    blackouts = [
        (parse_instant(entry["from"]), parse_instant(entry["to"]))
        for entry in rules.get("blackouts") or ()
    ]
    back_to_back = bool(rules.get("no_back_to_back"))

    for candidate in candidates:
        local_start = zone.local(candidate.start)
        local_end = zone.local(candidate.end)
        rule, detail = None, ""

        if weekdays and local_start.weekday() not in weekdays:
            rule, detail = "weekdays", f"{local_start:%A} is not one of the allowed weekdays"
        elif earliest and (local_start.hour, local_start.minute) < earliest:
            rule, detail = "earliest_start", f"starts at {local_start:%H:%M}, before {rules['earliest_start']}"
        elif latest and (local_end.hour, local_end.minute) > latest:
            rule, detail = "latest_end", f"ends at {local_end:%H:%M}, after {rules['latest_end']}"
        elif friday_after and local_start.weekday() == 4 and (local_start.hour, local_start.minute) >= friday_after:
            rule, detail = "no_friday_after", f"a Friday starting at {local_start:%H:%M}"
        elif any(
            overlaps(candidate.start, candidate.end, black_start, black_end)
            for black_start, black_end in blackouts
        ):
            rule, detail = "blackouts", "inside a blacked-out interval"
        elif back_to_back and _butts(candidate, availability, invited):
            rule, detail = "no_back_to_back", "touches another meeting with no gap either side"

        if rule is None:
            allowed.append(candidate)
        else:
            refused.append({**candidate.describe(), "rule": rule, "detail": detail})

    return allowed, refused


def _butts(candidate: Candidate, availability: BusyMap, invited: Sequence[str]) -> bool:
    """Whether a candidate touches a busy block at exactly one edge.

    A block ending exactly at the candidate's start is not an overlap - the
    half-open reading in :func:`~dsr.panel_time.timeutils.overlaps` says so - but
    it *is* a back-to-back booking, which is precisely what the researched house
    rule is about. Treating it as a conflict would break every working day; the
    rule exists for the other case.
    """
    for calendar_id in invited:
        for block_start, block_end in availability.blocks(calendar_id):
            if block_end == candidate.start or block_start == candidate.end:
                return True
    return False


# --------------------------------------------------------------------------- #
# The researched sort, and the one the extensibility claim invites
# --------------------------------------------------------------------------- #


def rank(
    evaluations: Sequence[Evaluation],
    *,
    ranker: str = RANK_CONFIDENCE,
    unknown_penalty: int = 0,
) -> list[Evaluation]:
    """The researched order: **high→low, then chronological**.

    [sourced] "averaged **confidence** score, sorted high→low then chronologically".

    The tie-break is part of the sentence and not a detail of it. Two slots at
    the same confidence are not interchangeable - the earlier one is the one a rep
    would otherwise have to think about - so the order is total, and a
    deployment that wants a different one names it rather than getting a
    different answer for the same input.

    :data:`~dsr.panel_time.vocabulary.RANK_WEIGHTED` is the researched
    extensibility claim made into a second ranker: confidence minus a penalty per
    calendar whose availability was unknown, so "we could not read three of these
    calendars" ranks below a genuinely free slot even when the arithmetic ties.
    """
    if ranker not in RANKERS:
        raise ConstraintError(f"ranker must be one of {list(RANKERS)}; got {ranker!r}")
    if not isinstance(unknown_penalty, int) or isinstance(unknown_penalty, bool):
        raise ConstraintError("unknown_penalty must be an integer")
    if unknown_penalty < 0:
        raise ConstraintError("unknown_penalty must not be negative; it is subtracted from confidence")

    scored: list[tuple[int, datetime, Evaluation]] = []
    for evaluation in evaluations:
        score = evaluation.confidence
        if ranker == RANK_WEIGHTED:
            score -= unknown_penalty * len(evaluation.unknown)
        scored.append((-score, evaluation.candidate.start, evaluation))
    scored.sort(key=lambda row: (row[0], row[1]))

    ordered: list[Evaluation] = []
    for score, _start, evaluation in scored:
        ordered.append(
            Evaluation(
                candidate=evaluation.candidate,
                confidence=evaluation.confidence,
                free=evaluation.free,
                busy=evaluation.busy,
                unknown=evaluation.unknown,
                meets_threshold=evaluation.meets_threshold,
                score=-score,
                penalty=evaluation.confidence - (-score),
            )
        )
    return ordered


def window(time_constraint: Mapping[str, Any]) -> tuple[datetime, datetime]:
    """The read's ``timeMin`` and ``timeMax``: the span of the searched slots."""
    slots = time_constraint.get("timeSlots") or []
    starts = [_instant_of(slot.get("start"), "timeSlots[].start") for slot in slots]
    ends = [_instant_of(slot.get("end"), "timeSlots[].end") for slot in slots]
    if not starts:
        raise ConstraintError("time_constraint.timeSlots must be a non-empty list of intervals")
    return min(starts), max(ends)


def pad_window(
    time_constraint: Mapping[str, Any], *, before: timedelta = timedelta(0), after: timedelta = timedelta(0)
) -> dict[str, Any]:
    """The same window, pushed outwards. The one adjustment that always helps.

    This is what the researched re-call does when
    ``emptySuggestionsReason`` says the calendars are too tight: widen the search
    rather than lower the bar. Widening the window and lowering the bar are
    different decisions with different costs, and the one that costs nothing is
    tried first.
    """
    padded: list[dict[str, Any]] = []
    for slot in time_constraint.get("timeSlots") or []:
        begin = _instant_of(slot.get("start"), "timeSlots[].start") - before
        finish = _instant_of(slot.get("end"), "timeSlots[].end") + after
        zone = str(slot.get("timeZone") or "UTC")
        padded.append(
            {
                "start": {"dateTime": format_instant(begin), "timeZone": zone},
                "end": {"dateTime": format_instant(finish), "timeZone": zone},
            }
        )
    padded.sort(key=lambda slot: (slot["start"]["dateTime"], slot["end"]["dateTime"]))
    return {
        "activityDomain": str(time_constraint.get("activityDomain") or "work"),
        "timeSlots": padded,
    }
