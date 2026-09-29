"""Every judgement call WF-057 rests on, named and served over HTTP.

The research is specific about the wire and silent about almost everything around
it. The 100/49/0 weights, the 50-calendar cap, the sort, the
``returnSuggestionReasons`` toggle, ``emptySuggestionsReason``'s existence and its
documented use - those are sourced, and they live in
:mod:`dsr.panel_time.vocabulary`.

What is *not* sourced is everything a deployment has to make a decision about
before the first search runs: what a panel record is called, how coarse a
candidate grid is, whether a working day includes Saturday, what a group that
cannot be expanded means, whether a 49% slot outranks a 100% slot three days
later, and what the engine does with a search from last week.

Each entry below carries its **basis**, and the basis is one of:

``[sourced]``
    Quoted from the research. Nothing to disagree with.
``[partly sourced]``
    One part quoted, the rest this build's reading. The quoted part is named.
``[not sourced]``
    This build's own. A deployment may replace it without contradicting any
    source.

The point of serving these is that a reviewer who disagrees with one of them can
say so precisely, and change one line, rather than reading a diff and guessing
which part was load-bearing.
"""

from __future__ import annotations

from typing import Any

#: Every inference, in the order a deployment meets them.
INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "panel-record",
        "topic": "What a 'panel' is in this product",
        "basis": "[not sourced]",
        "value": (
            "A panel is a saved, room-scoped search: a name, an organizer, a list of "
            "calendar addresses, a time constraint, a location constraint, a duration, "
            "a minAttendeePercentage, and optional house rules. It is named rather than "
            "typed, because the researched flow starts by 'picks a set of participants + "
            "a date range' and the same set is asked for again next week."
        ),
        "why": (
            "The research describes a surface, not a record. A persisted panel is what "
            "makes step 1 of the flow ('a date range') worth having, and what lets step 5 "
            "create the event on the same organizer's calendar rather than a re-prompted "
            "one."
        ),
        "change_it": "The panel payload is arbitrary JSON; no column holds any of it.",
        "blast_radius": "Panels list and filter through the dynamic index, so a new key "
        "is queryable the day it is written.",
    },
    {
        "id": "calendar-registry",
        "topic": "Where the attendees' calendars come from",
        "basis": "[not sourced]",
        "value": (
            "A room-independent registry of calendar records, each an email address, a "
            "kind (person, room, or group/distribution list), and either its busy blocks or "
            "an explicit unreadable reason. The research's step 2 says the app 'collects "
            "the attendees' email addresses'; it does not say from where."
        ),
        "why": (
            "The research's step 1 places the surface 'in a sales room, a CRM record, or a "
            "scheduling page'. Calendars are therefore an installation concern, not a room "
            "concern, and one room's panel can invite a calendar another room's panel also "
            "invites."
        ),
        "change_it": "Add a key to a calendar payload; nothing in the engine reads it.",
        "blast_radius": "Only the local provider reads calendar payloads, and it reads two "
        "optional keys: `busy` and an unreadable reason.",
    },
    {
        "id": "slot-grid",
        "topic": "Candidate slot granularity",
        "basis": "[not sourced]",
        "value": (
            "Starts are laid on a `slot_interval` grid, default PT30M, and a candidate "
            "exists only where the whole meeting fits inside a declared time slot."
        ),
        "why": (
            "The research says 'Ranked candidate slots are returned' and gives no "
            "granularity and no `slotInterval` parameter. 30 minutes is what both providers' "
            "own defaults imply, and a grid is what makes the ranking reproducible: without "
            "one, 'the nearest time' is a continuum rather than a list."
        ),
        "change_it": "`slot_interval` on the panel, or `PT1H` for a coarser grid.",
        "blast_radius": "A finer grid is more candidates and a longer search. The grid is "
        "refused if it is not shorter than `meeting_duration`.",
    },
    {
        "id": "activity-domain-work",
        "topic": "What activityDomain: 'work' excludes",
        "basis": "[partly sourced] the research names `activityDomain` and quotes neither "
        "its values nor their meaning.",
        "value": "`work` drops Saturday and Sunday. `unrestricted` searches all seven days.",
        "why": (
            "The two domain names are Graph's documented values. Excluding weekends from "
            "`work` is this build's reading of what distinguishes it from `unrestricted`: a "
            "domain that changed nothing would be a field with no effect, and a panel that "
            "searches a working-hours meeting on a Saturday is not what 'work' means."
        ),
        "change_it": "Set `activityDomain` to `unrestricted` on the panel.",
        "blast_radius": "Only candidate *enumeration* is affected. A slot on a Saturday is "
        "never generated under `work`, so it cannot be ranked or booked.",
    },
    {
        "id": "unknown-stays-in-the-denominator",
        "topic": "Whether an unreadable calendar counts toward the average",
        "basis": "[sourced] the weights; [not sourced] this consequence of them.",
        "value": (
            "The average is over every invited calendar, so an unreadable one contributes "
            "49% and lowers the slot's confidence."
        ),
        "why": (
            "The research gives one number for an unknown status - 49% - and describes the "
            "aggregate as an average of per-attendee availability. Averaging only over the "
            "calendars that answered would give 49% no effect at all when every calendar "
            "was unreadable: the score would be 100% for a panel nobody can schedule."
        ),
        "change_it": "The score is computed in one place; a deployment that wants the other "
        "reading changes that denominator.",
        "blast_radius": "Confidence, ranking, and every threshold decision derived from it.",
    },
    {
        "id": "min-attendee-percentage-basis",
        "topic": "What minAttendeePercentage is measured against",
        "basis": "[partly sourced] the research names the parameter in the data flow and "
        "never defines it.",
        "value": (
            "The share of invited calendars that are **free**, compared inclusively "
            "(`free_share >= min_attendee_percentage`). The default is 0, so a panel with "
            "no bar suggests every candidate."
        ),
        "why": (
            "'minAttendeePercentage' reads as a floor on attendance, and attendance is "
            "what the 100% free weight measures. A calendar that is *unknown* is not known "
            "to attend, so it does not count toward the share - which is exactly why a "
            "panel can raise the bar to force everyone to publish their calendar."
        ),
        "change_it": "`min_attendee_percentage` on the panel, 0-100.",
        "blast_radius": "Which candidates survive. The confidence percentage is reported "
        "either way, so raising the bar hides candidates rather than downgrading them.",
    },
    {
        "id": "half-open-busy-blocks",
        "topic": "Whether a busy block's end instant is inside the block",
        "basis": "[not sourced] both providers document start and end and neither documents "
        "the boundary.",
        "value": (
            "Busy blocks and candidates are half-open: a 09:00-10:00 block does not conflict "
            "with a 10:00-11:00 meeting."
        ),
        "why": (
            "This is the reading that makes an ordinary diary work. Treating the ends as "
            "inclusive would refuse to book the entire back half of every working day, and a "
            "find-a-time surface that reports no slots in the afternoon is a find-a-time "
            "surface nobody uses. The researched house rule 'no back-to-back' is the "
            "explicit way to reject a touching slot, which only makes sense if touching is "
            "not already a conflict."
        ),
        "change_it": "One predicate, `dsr.panel_time.timeutils.overlaps`.",
        "blast_radius": "Every availability decision in the engine.",
    },
    {
        "id": "empty-reason-vocabulary",
        "topic": "The values emptySuggestionsReason can take",
        "basis": "[sourced] the property and its documented use; [not sourced] the values.",
        "value": (
            "Five values, each derived from state the engine holds: "
            "`notOrganizedAsAttendee` (the panel does not invite its own organizer), "
            "`notEnoughCalendarFreeTime` (no candidate fits the window), "
            "`notEnoughPeopleFree` (candidates exist, none clear minAttendeePercentage), "
            "`busySuggestions` (candidates clear the threshold but the house rules removed "
            "every one), and `none` (nothing more specific could be derived)."
        ),
        "why": (
            "The research quotes the property and says 'Based on this value, you can better "
            "adjust the parameters and call findMeetingTimes again' - so the value has to "
            "*determine* an adjustment, which means it cannot be one undifferentiated "
            "sentinel. Each of these five is checkable: a test constructs the state and "
            "asserts the value, so a reader can verify the mapping is not decorative."
        ),
        "change_it": "Add a value in `derive_empty_reason` and a row in "
        "`retune_adjustments`; the two are read together.",
        "blast_radius": "The retune endpoint's suggestions, and the copy a client renders "
        "for an empty search.",
    },
    {
        "id": "retune-as-a-new-call",
        "topic": "Whether a re-call is a new search record or an update of the old one",
        "basis": "[sourced] the advice is to 'call findMeetingTimes again'; [not sourced] "
        "that the second call is a separate record.",
        "value": "A retune writes a new search row, linked to the one that came back empty.",
        "why": (
            "It is a second call, and this product's guarantee is that every write is "
            "audited in the same transaction as the change. Collapsing two calls into one "
            "row would make the audit log describe a search that never happened, and the "
            "parameters that produced the empty result would be overwritten by the ones "
            "that fixed it."
        ),
        "change_it": "One write; the alternative is an update on the original search.",
        "blast_radius": "The search log. A panel's history shows the empty result and the "
        "retry side by side.",
    },
    {
        "id": "recheck-at-commit",
        "topic": "Whether a booking re-reads availability",
        "basis": "[partly sourced] the research notes Google's suggestions are 'fine-tuned "
        "from time to time' so 'test environments may drift'; [not sourced] that this engine "
        "re-reads at commit time.",
        "value": (
            "Booking re-reads the busy blocks for the chosen slot and refuses if the slot no "
            "longer clears the panel's threshold, or if the organizer is now busy."
        ),
        "why": (
            "The research's own gap list says availability is a *pull* read, with no push "
            "invalidation - and the research quotes the drift note. A search from last week "
            "is a snapshot of a fact that moves, and creating a calendar event from a stale "
            "snapshot is a double-booking the user will discover from a customer."
        ),
        "change_it": "The check is in `PanelScheduler.book`, after the search is loaded.",
        "blast_radius": "Only the commit. A search is still cheap and re-runnable.",
    },
    {
        "id": "organizer-must-be-invited",
        "topic": "Whether the organizer has to be in the invited set",
        "basis": "[partly sourced] the research's step 5 creates the event 'on the "
        "organizer's calendar'.",
        "value": (
            "A panel must invite its own organizer. A panel that does not is refused at "
            "search time with `notOrganizedAsAttendee`, not at commit time."
        ),
        "why": (
            "There is no calendar to create the event on, so step 5 cannot complete. "
            "Refusing at search time is the researched automation applied to this package's "
            "own input: the same 'adjust the parameters and call again' advice, where the "
            "parameter to adjust is the attendee list."
        ),
        "change_it": "One check in `evaluate_search`.",
        "blast_radius": "A panel that forgot the organizer. The refusal names the fix.",
    },
    {
        "id": "graph-suggestions-not-merged",
        "topic": "Whether the Graph provider's own ranked suggestions are merged in",
        "basis": "[sourced] the researched data flow runs over a free/busy read; [not "
        "sourced] that a provider's own ranking is discarded.",
        "value": (
            "The Graph dialect renders the researched `findMeetingTimes` request exactly - "
            "path, `Prefer: outlook.timezone`, `Calendars.Read.Shared`, the researched body "
            "fields - and then runs this engine's researched pipeline over its own "
            "availability. The provider's `meetingTimeSuggestions` are not merged in."
        ),
        "why": (
            "The research's data flow is one pipeline and it starts at a free/busy read. "
            "Graph's endpoint returns suggestions the provider has already ranked, and no "
            "cited source describes combining a provider's ranking with an average of "
            "per-attendee availability. Silently preferring one would be a decision nobody "
            "made, so the response says which ranking produced the shortlist."
        ),
        "change_it": "Implement a second provider adapter with a `send()` that returns a "
        "BusyMap, and a merge policy.",
        "blast_radius": "The Graph dialect only. The Google dialect is unaffected.",
    },
    {
        "id": "confidence-rounding",
        "topic": "Whether confidence is an integer",
        "basis": "[not sourced] the research says 'averaged confidence score' and calls it a "
        "'confidence percentage'.",
        "value": (
            "A whole number, rounded half away from zero. The exact mean is reported beside "
            "it as `score` for the alternative ranker."
        ),
        "why": (
            "The weights are integers and the denominator is the invited count, so a five-"
            "person panel can land on 69.8%. A confidence badge wants an integer, and a "
            "rounding rule nobody chose produces a 70% that differs from 69.8% for reasons "
            "no reader can see."
        ),
        "change_it": "`round_percentage` in `dsr.panel_time.slots`.",
        "blast_radius": "The displayed number, and rank ties - which the chronological "
        "tie-break then resolves deterministically.",
    },
    {
        "id": "room-booking-not-implemented",
        "topic": "Booking into a room resource",
        "basis": "[sourced] 'book into a room resource' is named as an extensibility claim "
        "and the Graph resource/room directory is named as a data source; [not sourced] any "
        "endpoint for it.",
        "value": (
            "A room is a third kind of calendar whose busy blocks are honoured in the search, "
            "and a `locationConstraint` of type `room` names it on the commit. No room is "
            "booked, and no room directory is queried."
        ),
        "why": (
            "The research cites a directory as a data source and books into a room as a "
            "claim, but sources no endpoint for either. Inventing one would put an "
            "un-sourced call in the commit path, and an un-sourced call in the commit path "
            "is the kind of thing a deployment finds out in production."
        ),
        "change_it": "Add a provider method and a request renderer; the search is unaffected.",
        "blast_radius": "The commit. The search already treats a room as busy-blocked.",
    },
    {
        "id": "timezone-fallback",
        "topic": "The working window's time zone",
        "basis": "[not sourced] the research never names a zone, and this interpreter ships "
        "no IANA database.",
        "value": (
            "A named zone is used when the platform can resolve it and UTC when it cannot, "
            "and the response says which happened, with the note explaining why."
        ),
        "why": (
            "`zoneinfo.available_timezones()` is empty here and no `tzdata` package is "
            "installed, so a deployment on this image would silently get UTC hours for a "
            "panel that asked for Europe/London. Returning the offset and the exactness "
            "flag turns a silent wrong answer into a visible one, and installing `tzdata` "
            "fixes it with no code change."
        ),
        "change_it": "Install `tzdata`, or replace `dsr.panel_time.timeutils.Clock`.",
        "blast_radius": "House rules and `activityDomain: 'work'`, both of which are local-"
        "time questions. The searched window itself is always UTC instants.",
    },
    {
        "id": "house-rule-extra-keys",
        "topic": "Whether an unrecognised house rule is refused or carried",
        "basis": "[sourced] 'an integrator can layer their own ... house rules'; [not sourced] "
        "what happens to a key this build has never seen.",
        "value": "Carried through untouched under `extra`, and not enforced here.",
        "why": (
            "The extensibility sentence is the point of the feature. Refusing a key this "
            "build has not heard of would make the seam a wall, and the research's own "
            "examples - 'no Friday afternoons' - are not a closed list."
        ),
        "change_it": "Add the key to `HOUSE_RULE_KEYS` and to `apply_house_rules`.",
        "blast_radius": "Nothing, until a key is recognised. The value is visible in the "
        "search record so a reviewer can see it was passed and not silently dropped.",
    },
    {
        "id": "confidence-badge-colour",
        "topic": "What the frontend does with a confidence number",
        "basis": "[not sourced]",
        "value": (
            "Four bands - 100, 75-99, 50-74, below 50 - each with a text label, never colour "
            "alone."
        ),
        "why": (
            "The design system requires 4.5:1 contrast and a text label beside every "
            "indicator, so a coloured pill with no number would fail it. The bands are in the "
            "page, not in a shared component, because a feature that needs a different scale "
            "must not change anyone else's."
        ),
        "change_it": "The `CONFIDENCE_BANDS` table in the feature's own JSX.",
        "blast_radius": "One page.",
    },
)


def describe() -> dict[str, Any]:
    """The inference register, served next to the sourced facts it is measured against."""
    return {
        "count": len(INFERENCES),
        "inferences": [dict(entry) for entry in INFERENCES],
        "basis_vocabulary": {
            "[sourced]": "Quoted from the research; nothing to disagree with.",
            "[partly sourced]": "One part quoted, the rest this build's reading.",
            "[not sourced]": "This build's own; a deployment may replace it freely.",
        },
    }


def by_id(inference_id: str) -> dict[str, Any] | None:
    for entry in INFERENCES:
        if entry["id"] == inference_id:
            return dict(entry)
    return None
