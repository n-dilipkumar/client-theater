"""Every judgement call WF-105 makes, named and served so a reviewer can disagree.

The research is precise about re-entry, the sticky exit, the tag block, and the
Goal tab. It is silent about how the 30-minute poll runs, how the tag vocabulary
is stored, how re-entry is recorded, and several smaller things. Those gaps are
product behaviour rather than comments, so they are collected here and served at
``GET /api/wf105/inferences`` instead of being buried in the module that
implements them.

Each entry carries ``decision``, ``why`` and ``change_if``. A reviewer who
disagrees has a named thing to disagree with and a stated consequence.
"""

from __future__ import annotations

from typing import Any

#: The three open decisions the issue itself names, plus this build's own.
DERIVED_POLL_IS_ON_DEMAND = "inference_poll_is_on_demand"
DERIVED_DOMAIN_IS_A_SUBPACKAGE = "inference_domain_is_a_subpackage"
DERIVED_GOAL_IS_A_SECOND_FILTER = "inference_goal_is_a_second_filter"
DERIVED_EXIT_IS_ANY_MATCH = "inference_exit_is_any_match"
DERIVED_REENTRY_REUSES_ONE_ROW = "inference_reentry_reuses_one_enrollment_row"
DERIVED_TAG_NAME_IS_CASE_INSENSITIVE = "inference_tag_name_is_case_insensitive"
DERIVED_TAG_TOPIC_IS_RECORDED_NOT_SENT = "inference_tag_topic_is_recorded_not_sent"


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
        DERIVED_POLL_IS_ON_DEMAND,
        "the poll",
        "The research says 'Series is polled every 30 minutes to evaluate entry and "
        "progression'. Is the poll a background job or an evaluation on demand?",
        "An evaluation on demand, with the cadence owned by the domain. POST "
        "/api/wf105/series/{id}/poll runs one poll over the events the caller supplies and returns "
        "what it decided. GET /api/wf105/due and GET /api/wf105/series/{id}/due report which live "
        "series have passed the researched 30-minute interval, from each series' last_polled_at, so "
        "any scheduler drives the cadence without a timer inside the process.",
        "An in-process background thread would hold the store open for the life of the process, put "
        "a second writer beside the single-writer audited store, and make every test wait on a "
        "timer. Serving the cadence as data keeps the poll synchronous and auditable while still "
        "answering 'when is this series next due', which is the only thing the researched interval "
        "was there to decide.",
        "Add a lifespan task in a platform module that reads GET /api/wf105/due on a timer and "
        "posts the poll route. The routes and last_polled_at already exist.",
    ),
    _entry(
        DERIVED_DOMAIN_IS_A_SUBPACKAGE,
        "package layout",
        "The brief names backend/dsr/automation_engagement/ as this workflow's domain module, "
        "but that package already exists on main and is owned by WF-111.",
        "WF-105 adds a NEW subpackage, backend/dsr/automation_engagement/series/, and edits no "
        "WF-111 file (errors.py, rules.py, vocabulary.py).",
        "The feature contract's first rule is that a workflow adds files and does not edit "
        "another workflow's files. Sharing the package while keeping the modules disjoint "
        "satisfies the brief's named path and the parallel-authorship guarantee at once. A "
        "future workflow that also belongs to the automation-engagement domain can add its own "
        "subpackage beside this one with no conflict.",
        "Move backend/dsr/automation_engagement/series/ to its own top-level package and update "
        "the three imports in the feature module, its tests, and nothing else.",
    ),
    _entry(
        DERIVED_GOAL_IS_A_SECOND_FILTER,
        "the Goal tab",
        "The user flow's step four opens a Goal tab with an outcome filter and a match window, "
        "separate from the starting rule block of step one. Are they one filter set?",
        "No. The starting rule block lives in start_filters and decides entry. The goal lives "
        "in goal as {outcome_filters, match_window_days} and decides whether a completed "
        "contact satisfied the outcome within the window measured from its own entered_at.",
        "The issue states the two are distinct and must stay distinct in the data model. Merging "
        "them would make the entry rule and the success criterion one thing, so a contact could "
        "only ever enter when it had already achieved the outcome - which is the opposite of a "
        "nurture series.",
        "Read goal.outcome_filters as an additional clause of the start filter in "
        "rules.entry_decision.",
    ),
    _entry(
        DERIVED_EXIT_IS_ANY_MATCH,
        "exit rules",
        "The exit-rules tab holds several exclusion filters. Does a contact exit on any of them "
        "or on all of them?",
        "On any of them: the contact exits when a single exit filter matches its data. An empty "
        "exit-filter list matches nothing and therefore never exits a contact.",
        "The researched example lists 'Tag is ExitUpgradeSeries' and 'Marked email as spam is "
        "true' as two separate exclusion rules a seller adds to the tab. Reading the list as "
        "'all' would require a contact to satisfy every exclusion at once, and the exit is "
        "sticky, so a rule that never fired would be invisible rather than harmless.",
        "Change rules.exit_matches to require every filter to match.",
    ),
    _entry(
        DERIVED_REENTRY_REUSES_ONE_ROW,
        "re-entry storage",
        "A contact may re-enter a series several times. Is that several enrollment rows or one?",
        "One row per (series, contact). A re-entry updates the same row: state back to enrolled, "
        "position back to zero, entered_at reset, reentry_count incremented. An exit marks that "
        "one row exited, and entry_decision refuses it before reading the starting rule.",
        "One row is what makes the sticky exit a property of the row rather than a search across "
        "a history: a completed row can be re-entered, and an exited row cannot be, with no rule "
        "outside the row to enforce it. It also keeps 'is this contact active right now' a "
        "single lookup.",
        "Append a new enrollment row per entry and read the sticky exit from the newest exited "
        "row in the engine's _enrollment.",
    ),
    _entry(
        DERIVED_TAG_NAME_IS_CASE_INSENSITIVE,
        "tag selection",
        "Two workspace tags differ only in case. Does a tag block matching one match the other?",
        "Yes. A tag block's name is resolved case-insensitively and the workspace's canonical "
        "spelling is returned, so a block cannot create a second tag that differs only in case.",
        "The research forbids creating a tag from the tag block. Treating case as significant "
        "would let the tag block introduce 'upgraded after series' beside 'Upgraded after "
        "Series', which is a new tag by another route.",
        "Compare with == in rules.select_tag.",
    ),
    _entry(
        DERIVED_TAG_TOPIC_IS_RECORDED_NOT_SENT,
        "tag webhooks",
        "The data flow names contact.user.tag.created as the webhook other systems react to. "
        "Does this build deliver it?",
        "No. A tag application records the topic it would publish on, so a reader can see the "
        "contract, but no HTTP request leaves this build. Delivering a webhook is a platform "
        "concern with retries and signing, and a fake delivery would be worse than an honest "
        "recording.",
        "The research names the topic as extensibility rather than as behaviour of this "
        "workflow. The issue's extensibility note says to add a Webhook block after the tag "
        "block to push the state out, which this build does not implement.",
        "Add a webhook block kind and a delivery module; the tag application already carries "
        "the topic and the contact id it would send.",
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
