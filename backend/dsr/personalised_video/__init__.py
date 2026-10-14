"""WF-126: asynchronous personalised video inside the room.

Module map, in dependency order:

``vocabulary``
    The researched terms: the two recording kinds, the capture sources, the
    transcode states, the hero surface, the four playback events, the
    completion and low-completion thresholds, the automation list, every sourced
    quote and every gap the research left. It also re-exports the WF-002 page
    flag and fragment key and the WF-004 recipient collections, so this workflow
    reuses their vocabulary instead of re-declaring it.
``errors``
    One error per distinct answer, each with a stable code.
``rules``
    The pure rules: timestamp reading, payload normalisation, the playback
    roll-up, the completion rule, the low-completion rule and the record views.
    No store, no framework.
``engine``
    :class:`~dsr.personalised_video.engine.VideoEngine` - the only module that
    reads or writes. Everything goes through the audited store.

Nothing here imports ``dsr.api``. The store seam is
:class:`~dsr.store.RecordStore`.
"""

from __future__ import annotations

from dsr.personalised_video import errors, rules, vocabulary
from dsr.personalised_video.engine import VideoEngine
from dsr.personalised_video.errors import (
    VideoConflict,
    VideoError,
    VideoNotFound,
    VideoValidationError,
)
from dsr.personalised_video.rules import (
    apply_event,
    blank_engagement,
    engagement_state,
    engagement_view,
    normalise_emails,
    parse_timestamp,
    personalisation_message,
    placement_view,
    recording_view,
    should_alert,
    to_iso,
    validate_playback,
    validate_recording,
)
from dsr.personalised_video.vocabulary import (
    ALERT_COLLECTION,
    ALERT_OPEN,
    AUTOMATIONS,
    CAPTURE_SOURCES,
    COLLECTIONS,
    COMPLETE_PERCENT,
    ENGAGEMENT_COLLECTION,
    ENGAGEMENT_STATES,
    EVENT_COMPLETE,
    EVENT_PROGRESS,
    EVENT_REPLAY,
    EVENT_START,
    EVENTS,
    FEATURE_ID,
    IMPLEMENTED_AUTOMATIONS,
    INFERENCES,
    KIND_PERSONAL_INTRO,
    KIND_WALKTHROUGH,
    KINDS,
    LOW_COMPLETION_PERCENT,
    NAME,
    NOTIFICATION_COLLECTION,
    PLACEMENT_COLLECTION,
    PLACEMENT_LIVE,
    PLAYBACK_COLLECTION,
    PREFIX,
    RECORDING_COLLECTION,
    RECORDING_PLACED,
    RECORDING_RECORDED,
    SOURCED_GAPS,
    SOURCED_QUOTES,
    SOURCES,
    STATE_COMPLETED,
    STATE_NOT_STARTED,
    STATE_STARTED,
    SURFACE_HERO,
    TICKET,
    TRANSCODE_READY,
    UNIMPLEMENTED_AUTOMATIONS,
    USER_FLOW,
    published_vocabulary,
)

__all__ = [
    "ALERT_COLLECTION",
    "ALERT_OPEN",
    "AUTOMATIONS",
    "CAPTURE_SOURCES",
    "COLLECTIONS",
    "COMPLETE_PERCENT",
    "ENGAGEMENT_COLLECTION",
    "ENGAGEMENT_STATES",
    "EVENTS",
    "EVENT_COMPLETE",
    "EVENT_PROGRESS",
    "EVENT_REPLAY",
    "EVENT_START",
    "FEATURE_ID",
    "IMPLEMENTED_AUTOMATIONS",
    "INFERENCES",
    "KINDS",
    "KIND_PERSONAL_INTRO",
    "KIND_WALKTHROUGH",
    "LOW_COMPLETION_PERCENT",
    "NAME",
    "NOTIFICATION_COLLECTION",
    "PLACEMENT_COLLECTION",
    "PLACEMENT_LIVE",
    "PLAYBACK_COLLECTION",
    "PREFIX",
    "RECORDING_COLLECTION",
    "RECORDING_PLACED",
    "RECORDING_RECORDED",
    "SOURCES",
    "SOURCED_GAPS",
    "SOURCED_QUOTES",
    "STATE_COMPLETED",
    "STATE_NOT_STARTED",
    "STATE_STARTED",
    "SURFACE_HERO",
    "TICKET",
    "TRANSCODE_READY",
    "UNIMPLEMENTED_AUTOMATIONS",
    "USER_FLOW",
    "VideoConflict",
    "VideoEngine",
    "VideoError",
    "VideoNotFound",
    "VideoValidationError",
    "apply_event",
    "blank_engagement",
    "engagement_state",
    "engagement_view",
    "errors",
    "normalise_emails",
    "parse_timestamp",
    "personalisation_message",
    "placement_view",
    "published_vocabulary",
    "recording_view",
    "rules",
    "should_alert",
    "to_iso",
    "validate_playback",
    "validate_recording",
    "vocabulary",
]
