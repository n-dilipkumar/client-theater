"""The researched vocabulary for WF-105, and nothing else.

Built from ``docs/research/digital-sales-room-workflows/wf/WF-105.md``, which is
itself built from section 2 of ``docs/research/raw/automation-engagement.md``.
Every constant that a page renders or a validator enforces is declared here beside
the sentence it came from, so a reader can check the value against the research
rather than against the code that reads it.

Three kinds of term live here and they are kept apart on purpose:

* a **researched** term carries ``source`` and ``quote``: the research states it.
* an **inferred** term carries ``inference``: the research leaves it open and this
  build chose. The choice is listed in :mod:`dsr.automation_engagement.series.inferences`
  so a reviewer can disagree with it by name.
* a **collection name** is this build's own storage key and carries neither.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- #
# Collections: ordinary JSON in records.data, no migration and no typed column
# --------------------------------------------------------------------------- #

SERIES_COLLECTION = "wf105_series"
TAG_COLLECTION = "wf105_tag"
ENROLLMENT_COLLECTION = "wf105_enrollment"
TAG_APPLICATION_COLLECTION = "wf105_tag_application"
EVALUATION_COLLECTION = "wf105_series_evaluation"

#: The researched poll cadence. "Series is polled every 30 minutes to evaluate
#: entry and progression."
POLL_INTERVAL_MINUTES = 30

# --------------------------------------------------------------------------- #
# Series state, block kinds and enrollment state
# --------------------------------------------------------------------------- #

STATE_DRAFT = "draft"
STATE_LIVE = "live"
STATE_PAUSED = "paused"
SERIES_STATES = (STATE_DRAFT, STATE_LIVE, STATE_PAUSED)

#: The user flow builds "message -> rule -> tag block".
BLOCK_MESSAGE = "message"
BLOCK_RULE = "rule"
BLOCK_TAG = "tag"
BLOCK_KINDS = (BLOCK_MESSAGE, BLOCK_RULE, BLOCK_TAG)

ENROLLED = "enrolled"
REENTERED = "reentered"
COMPLETED = "completed"
EXITED = "exited"
ENROLLMENT_STATES = (ENROLLED, COMPLETED, EXITED)

#: Entry outcomes. The first two write an enrollment; the rest are honest skips
#: that a poll reports rather than errors, because a contact that does not match
#: is the normal case for a data-driven automation.
OUTCOME_ENROLLED = "enrolled"
OUTCOME_REENTERED = "reentered"
OUTCOME_ALREADY_ACTIVE = "skipped_already_active"
OUTCOME_EXITED_STICKY = "skipped_exited_sticky"
OUTCOME_NO_MATCH = "skipped_no_match"
OUTCOME_REENTER_DISABLED = "skipped_reenter_disabled"
OUTCOME_NOT_LIVE = "skipped_not_live"
ENTRY_OUTCOMES = (
    OUTCOME_ENROLLED,
    OUTCOME_REENTERED,
    OUTCOME_ALREADY_ACTIVE,
    OUTCOME_EXITED_STICKY,
    OUTCOME_NO_MATCH,
    OUTCOME_REENTER_DISABLED,
    OUTCOME_NOT_LIVE,
)

#: The two outcomes that write an enrollment.
ENROLLING_OUTCOMES = (OUTCOME_ENROLLED, OUTCOME_REENTERED)

#: Exit reasons. ``exit_rule`` is the researched sticky exit.
EXIT_RULE = "exit_rule"
EXIT_REASONS = (EXIT_RULE,)

# --------------------------------------------------------------------------- #
# The researched terms, each with the sentence that fixes it
# --------------------------------------------------------------------------- #

SOURCE_SERIES_HELP = "https://www.intercom.com/help/en/articles/4425207-series-explained"
SOURCE_WEBHOOKS = "https://developers.intercom.com/docs/references/webhooks/webhook-models"
SOURCE_TAGS = "https://developers.intercom.com/docs/references/rest-api/api.intercom.io/tags"

SOURCES = (
    {"id": "series_explained", "url": SOURCE_SERIES_HELP},
    {"id": "webhook_models", "url": SOURCE_WEBHOOKS},
    {"id": "tags_api", "url": SOURCE_TAGS},
)

#: The one tag webhook this workflow names as its extensibility seam.
TAG_CREATED_TOPIC = "contact.user.tag.created"
TAG_DELETED_TOPIC = "contact.user.tag.deleted"
TAG_TOPICS = (TAG_CREATED_TOPIC, TAG_DELETED_TOPIC)


def _researched(
    term: str, value: Any, quote: str, source: str = SOURCE_SERIES_HELP
) -> dict[str, Any]:
    return {"term": term, "value": value, "quote": quote, "source": source}


def published_vocabulary() -> dict[str, Any]:
    """Every published term, as data, so a client renders from one source.

    A picker built from this endpoint cannot drift from the validator that
    enforces the same list, which is the whole reason the term is served rather
    than compiled into the page.
    """

    return {
        "poll_interval_minutes": _researched(
            "poll_interval_minutes",
            POLL_INTERVAL_MINUTES,
            "Series is polled every 30 minutes to evaluate entry and progression.",
        ),
        "reenter_series": _researched(
            "reenter_series",
            True,
            "Customers can re-enter the same Series multiple times if you enable "
            "'Re-enter Series' on the starting rules block.",
        ),
        "tag_block": _researched(
            "tag_block",
            True,
            "A tag block lets you automatically apply tags to customers as they complete "
            "their journey through a series.",
        ),
        "existing_tag_only": _researched(
            "existing_tag_only",
            True,
            "You won't be able to create a new tag here, but can use any existing tag from "
            "the workspace.",
        ),
        "sticky_exit_rule": _researched(
            "sticky_exit_rule",
            EXIT_RULE,
            "If a customer's data is updated to match the filters in this exit rule they will "
            "exit the series completely, and cannot re-enter.",
        ),
        "goal_match_window_days": _researched(
            "goal_match_window_days",
            30,
            "Customers have to match within 30 Days",
        ),
        "tags_are_shared": _researched(
            "tags_are_shared",
            True,
            "Tags can then also be used to enter or exit customers from other Series",
        ),
        "tag_topics": _researched(
            "tag_topics",
            list(TAG_TOPICS),
            "Webhook topics: contact.user.tag.created, contact.user.tag.deleted.",
            source=SOURCE_WEBHOOKS,
        ),
        "tag_api_surface": _researched(
            "tag_api_surface",
            ["POST /tags", "POST /tags/{id}/users", "GET /contacts/{id}/tags"],
            "POST /tags and POST /tags/{id}/users are the documented tag surface; "
            "GET /contacts/{id}/tags is listed on the Contacts reference.",
            source=SOURCE_TAGS,
        ),
        "series_states": {"term": "series_states", "value": list(SERIES_STATES)},
        "block_kinds": {"term": "block_kinds", "value": list(BLOCK_KINDS)},
        "enrollment_states": {"term": "enrollment_states", "value": list(ENROLLMENT_STATES)},
        "entry_outcomes": {"term": "entry_outcomes", "value": list(ENTRY_OUTCOMES)},
        "sources": {"term": "sources", "value": list(SOURCES)},
    }
