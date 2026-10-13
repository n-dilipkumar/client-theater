"""WF-105: re-enter a recurring nurture series and tag the contact on completion.

The researched workflow, in the order the research states it: a contact matches
the starting rule block's filters again when a recurring event fires; if
**Re-enter Series** is enabled the contact is re-enrolled into the live series;
the contact traverses the path; the tag block writes an existing workspace Tag
onto the Contact; and a sticky exit rule removes a contact for good.

Module map, in dependency order:

``vocabulary``
    The researched terms, each with the sentence it came from, plus this build's
    own storage keys and collection names.
``errors``
    The one error type the host registers, and the subclasses that draw the line
    between "you typed it wrong" and "the state refuses it".
``rules``
    The pure rules: filters, the entry decision, the sticky exit, the Goal tab,
    the workspace tag vocabulary, and the poll cadence.
``engine``
    The flow end to end, over the audited store.
``inferences``
    Every judgement call, named and served over HTTP so a reviewer can disagree
    with one by name.
"""

from __future__ import annotations

from dsr.automation_engagement.series.engine import SeriesEngine
from dsr.automation_engagement.series.errors import (
    ERROR_TYPES,
    SeriesConflict,
    SeriesError,
    SeriesNotFound,
    SeriesRefused,
    TagNotInWorkspace,
)
from dsr.automation_engagement.series.inferences import (
    INFERENCE_IDS,
    INFERENCES,
    describe,
)
from dsr.automation_engagement.series.rules import (
    due_for_poll,
    entry_decision,
    exit_matches,
    goal_met,
    matches,
    normalise_filters,
    normalise_goal,
    parse_instant,
    select_tag,
    suggest_exit_reason,
    tag_names,
    to_iso,
    validate_path,
    validate_series,
    validate_tag,
)
from dsr.automation_engagement.series.vocabulary import (
    BLOCK_KINDS,
    BLOCK_TAG,
    ENROLLED,
    ENROLLING_OUTCOMES,
    ENROLLMENT_COLLECTION,
    ENROLLMENT_STATES,
    ENTRY_OUTCOMES,
    EVALUATION_COLLECTION,
    EXITED,
    POLL_INTERVAL_MINUTES,
    SERIES_COLLECTION,
    SERIES_STATES,
    SOURCES,
    TAG_APPLICATION_COLLECTION,
    TAG_COLLECTION,
    TAG_TOPICS,
    published_vocabulary,
)

__all__ = [
    "BLOCK_KINDS",
    "BLOCK_TAG",
    "ENROLLED",
    "ENROLLING_OUTCOMES",
    "ENTRY_OUTCOMES",
    "ENROLLMENT_COLLECTION",
    "ENROLLMENT_STATES",
    "ERROR_TYPES",
    "EVALUATION_COLLECTION",
    "EXITED",
    "INFERENCES",
    "INFERENCE_IDS",
    "POLL_INTERVAL_MINUTES",
    "SERIES_COLLECTION",
    "SERIES_STATES",
    "SOURCES",
    "TAG_APPLICATION_COLLECTION",
    "TAG_COLLECTION",
    "TAG_TOPICS",
    "SeriesConflict",
    "SeriesEngine",
    "SeriesError",
    "SeriesNotFound",
    "SeriesRefused",
    "TagNotInWorkspace",
    "describe",
    "due_for_poll",
    "entry_decision",
    "exit_matches",
    "goal_met",
    "matches",
    "normalise_filters",
    "normalise_goal",
    "parse_instant",
    "published_vocabulary",
    "select_tag",
    "suggest_exit_reason",
    "tag_names",
    "to_iso",
    "validate_path",
    "validate_series",
    "validate_tag",
]
