"""WF-126: the researched vocabulary for asynchronous personalised video.

The workflow is "Asynchronous personalised video inside the room". The research
is ``docs/research/digital-sales-room-workflows/wf/WF-126.md``; this module is
the closed set of words a page renders and a validator enforces, each declared
beside the sentence it came from.

Three kinds of term live here:

* a **researched** term carries a quote: the research states it.
* an **inferred** term carries nothing here and is recorded in the research
  gaps below, because the research left the choice open.
* a **collection name** is this build's own storage key and carries neither.

Reuse, not re-declaration
-------------------------

The workflow places the video on the room's hero section and personalises it per
buyer, so it depends on two merged workflows rather than inventing their shapes:

* WF-002 (``dsr.pages`` / ``dsr.fragments``) owns room pages, their blocks and
  the Video Block. :data:`HERO_FRAGMENT` is that Video Block's key and
  :data:`PLAYER_FIELDS` are its documented configuration fields, so the player
  this feature records is the player WF-002 already renders. The hero surface is
  the room's home page (:data:`HERO_PAGE_FLAG`), which is the flag WF-002 sets.
* WF-004 (``dsr.roles``) owns the per-room recipient list. :data:`ACCESS_GRANTS`
  and :data:`INVITATIONS` are that workflow's collection names, read by name and
  not re-declared, and the buyer identity used for personalisation is the
  principal email those rows carry.

Nothing here imports the framework. See :mod:`dsr.personalised_video.engine` for
the only module that reads or writes.
"""

from __future__ import annotations

from typing import Any

from dsr import roles as room_roles

# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #

TICKET = "WF-126"
FEATURE_ID = "wf-126-personalised-video"
NAME = "Asynchronous personalised video inside the room"
PREFIX = "/api/wf-126"

# --------------------------------------------------------------------------- #
# Collections this workflow owns
#
# All of them are ordinary JSON in ``records.data``: no migration, no typed
# column. A team adding a field needs no coordination with anyone.
# --------------------------------------------------------------------------- #

RECORDING_COLLECTION = "wf126_recording"
PLACEMENT_COLLECTION = "wf126_placement"
PLAYBACK_COLLECTION = "wf126_playback_event"
ENGAGEMENT_COLLECTION = "wf126_engagement"
NOTIFICATION_COLLECTION = "wf126_notification"
ALERT_COLLECTION = "wf126_alert"

COLLECTIONS: dict[str, str] = {
    "recording": RECORDING_COLLECTION,
    "placement": PLACEMENT_COLLECTION,
    "playback_event": PLAYBACK_COLLECTION,
    "engagement": ENGAGEMENT_COLLECTION,
    "notification": NOTIFICATION_COLLECTION,
    "alert": ALERT_COLLECTION,
}

# --------------------------------------------------------------------------- #
# Reuse: WF-002 (pages and blocks) and WF-004 (roles and access grants)
# --------------------------------------------------------------------------- #

#: WF-002's collection of room pages. The hero section is one of its pages.
PAGE_COLLECTION = "page"

#: The field WF-002 sets on a room's home page. The user flow places the video
#: on the room's hero section, so the surface this feature resolves is the page
#: carrying this flag.
HERO_PAGE_FLAG = "is_home"

#: WF-002's Video Block key. "Under Video Options, URL points to the video and
#: Width and Height set the player's dimensions. Autoplay is off by default."
HERO_FRAGMENT = "video"

#: The Video Block's documented configuration fields, in the order WF-002
#: declares them. A placement records its player with exactly these keys, so the
#: configuration this feature stores is the configuration WF-002 renders.
PLAYER_FIELDS: tuple[str, ...] = ("url", "width", "height", "autoplay")

PLAYER_DEFAULT_WIDTH = 640
PLAYER_DEFAULT_HEIGHT = 360
PLAYER_DEFAULT_AUTOPLAY = False

#: WF-004's per-room recipient list. A grant row carries the buyer's email as
#: ``principal``; an invitation carries it as ``email``. Both are read by the
#: principal helpers below, so the buyer this feature personalises for is the
#: buyer WF-004 invited.
ACCESS_GRANTS = room_roles.ACCESS
INVITATIONS = room_roles.INVITATIONS

#: WF-004's role identifiers, reused so a placement can say which recipients are
#: viewers as opposed to editors. Nothing here grants or changes a role.
ROLE_IDS: tuple[str, ...] = tuple(room_roles.ROLE_IDS)
DEFAULT_ROLE = room_roles.DEFAULT_ROLE

#: The room payload field WF-002 reads for a view-only audience. A room that
#: declares no grants, no invitations and no viewers is unconfigured, and this
#: feature accepts any buyer rather than pretending a gate exists.
ROOM_VIEWERS_FIELD = "viewers"

# --------------------------------------------------------------------------- #
# The recording
# --------------------------------------------------------------------------- #

#: "A rep records a short personal intro (or a product team records a
#: walkthrough)". The two kinds the user flow names, and no other.
KIND_PERSONAL_INTRO = "personal_intro"
KIND_WALKTHROUGH = "walkthrough"

KINDS: tuple[str, ...] = (KIND_PERSONAL_INTRO, KIND_WALKTHROUGH)

KIND_LABELS: dict[str, str] = {
    KIND_PERSONAL_INTRO: "Personal intro",
    KIND_WALKTHROUGH: "Product walkthrough",
}

DEFAULT_KIND = KIND_PERSONAL_INTRO

#: "Screen/camera capture". The data sources are "Rep's camera/mic; room content
#: (case study, demo) being walked through". The capture itself is not performed
#: by this build; only the source a rep declares is recorded.
SOURCE_CAMERA = "camera"
SOURCE_SCREEN = "screen"
SOURCE_CAMERA_AND_SCREEN = "camera_and_screen"

CAPTURE_SOURCES: tuple[str, ...] = (SOURCE_CAMERA, SOURCE_SCREEN, SOURCE_CAMERA_AND_SCREEN)

SOURCE_LABELS: dict[str, str] = {
    SOURCE_CAMERA: "Camera and microphone",
    SOURCE_SCREEN: "Screen share",
    SOURCE_CAMERA_AND_SCREEN: "Camera and screen",
}

DEFAULT_SOURCE = SOURCE_CAMERA_AND_SCREEN

#: The data flow is "capture -> transcode -> adaptive stream (HLS)". No object
#: store and no transcoder exist in this build, so the states are recorded and
#: the media reference is opaque. See :data:`SOURCED_GAPS`.
TRANSCODE_PENDING = "pending"
TRANSCODE_READY = "ready"

TRANSCODES: tuple[str, ...] = (TRANSCODE_PENDING, TRANSCODE_READY)

#: A recorded asset is recorded, then placed on a hero, then may be withdrawn.
RECORDING_RECORDED = "recorded"
RECORDING_PLACED = "placed"
RECORDING_WITHDRAWN = "withdrawn"

RECORDING_STATUSES: tuple[str, ...] = (
    RECORDING_RECORDED,
    RECORDING_PLACED,
    RECORDING_WITHDRAWN,
)

# --------------------------------------------------------------------------- #
# The placement
# --------------------------------------------------------------------------- #

#: "the video is placed on the room's hero section". One surface, and no other.
SURFACE_HERO = "hero"

SURFACES: tuple[str, ...] = (SURFACE_HERO,)

#: A room's hero shows one video. Placing a second supersedes the first rather
#: than deleting it, because the buyer's history points at what they watched.
PLACEMENT_LIVE = "live"
PLACEMENT_SUPERSEDED = "superseded"
PLACEMENT_WITHDRAWN = "withdrawn"

PLACEMENT_STATUSES: tuple[str, ...] = (
    PLACEMENT_LIVE,
    PLACEMENT_SUPERSEDED,
    PLACEMENT_WITHDRAWN,
)

PLACEMENT_STATUS_LABELS: dict[str, str] = {
    PLACEMENT_LIVE: "Live on the hero",
    PLACEMENT_SUPERSEDED: "Replaced by a newer video",
    PLACEMENT_WITHDRAWN: "Withdrawn",
}

# --------------------------------------------------------------------------- #
# Per-viewer playback events
# --------------------------------------------------------------------------- #

#: The data flow names the events exactly: "(start, % watched, completes,
#: replays)". The four below are that list and no more.
EVENT_START = "start"
EVENT_PROGRESS = "progress"
EVENT_COMPLETE = "complete"
EVENT_REPLAY = "replay"

EVENTS: tuple[str, ...] = (EVENT_START, EVENT_PROGRESS, EVENT_COMPLETE, EVENT_REPLAY)

EVENT_LABELS: dict[str, str] = {
    EVENT_START: "Started",
    EVENT_PROGRESS: "Progress",
    EVENT_COMPLETE: "Completed",
    EVENT_REPLAY: "Replayed",
}

#: A ``progress`` or ``complete`` event carries the percentage watched. A
#: ``start`` or ``replay`` event carries a position in seconds.
PERCENT_EVENTS: tuple[str, ...] = (EVENT_PROGRESS, EVENT_COMPLETE)
POSITION_EVENTS: tuple[str, ...] = (EVENT_START, EVENT_PROGRESS, EVENT_COMPLETE, EVENT_REPLAY)

# --------------------------------------------------------------------------- #
# Engagement: the roll-up, the completion rule and the low-completion alert
# --------------------------------------------------------------------------- #

#: A viewer's derived state. There are three, and no other.
STATE_NOT_STARTED = "not_started"
STATE_STARTED = "started"
STATE_COMPLETED = "completed"

ENGAGEMENT_STATES: tuple[str, ...] = (STATE_NOT_STARTED, STATE_STARTED, STATE_COMPLETED)

STATE_LABELS: dict[str, str] = {
    STATE_NOT_STARTED: "Not started",
    STATE_STARTED: "Started, not finished",
    STATE_COMPLETED: "Completed",
}

#: The percentage at which a viewer counts as having completed the video even if
#: no explicit ``complete`` event arrived. The research names a ``% watched``
#: signal and does not fix a threshold, so this is an inference.
COMPLETE_PERCENT = 95

#: The completion below which the rep is alerted. "Alert the rep on low
#: completion" fixes no number, so the threshold is a per-workflow default and
#: an inference.
LOW_COMPLETION_PERCENT = 50

#: The alert's two states. An alert is opened on a low-completion signal and
#: cleared when the same viewer later completes.
ALERT_OPEN = "open"
ALERT_CLEARED = "cleared"

ALERT_STATUSES: tuple[str, ...] = (ALERT_OPEN, ALERT_CLEARED)

#: Why an alert fired. A page branches on the reason and never on the message.
REASON_LOW_COMPLETION = "low_completion"
REASON_COMPLETED_LATE = "completed_late"

# --------------------------------------------------------------------------- #
# Automations
#
# "Auto-transcode and publish on save; auto-embed into new rooms from a
# template; notify the buyer when a new video lands; alert the rep on low
# completion." This build owns the first, third and fourth; the embed-from-
# template path depends on a room template surface this workflow does not own.
# --------------------------------------------------------------------------- #

AUTOMATION_TRANSCODE_ON_SAVE = "Auto-transcode and publish on save"
AUTOMATION_EMBED_FROM_TEMPLATE = "Auto-embed into new rooms from a template"
AUTOMATION_NOTIFY_BUYER = "Notify the buyer when a new video lands"
AUTOMATION_ALERT_REP = "Alert the rep on low completion"

AUTOMATIONS: tuple[str, ...] = (
    AUTOMATION_TRANSCODE_ON_SAVE,
    AUTOMATION_EMBED_FROM_TEMPLATE,
    AUTOMATION_NOTIFY_BUYER,
    AUTOMATION_ALERT_REP,
)

IMPLEMENTED_AUTOMATIONS: tuple[str, ...] = (
    AUTOMATION_TRANSCODE_ON_SAVE,
    AUTOMATION_NOTIFY_BUYER,
    AUTOMATION_ALERT_REP,
)

UNIMPLEMENTED_AUTOMATIONS: tuple[str, ...] = (AUTOMATION_EMBED_FROM_TEMPLATE,)

# --------------------------------------------------------------------------- #
# Capture stacks the research names and does not choose
# --------------------------------------------------------------------------- #

#: "LiveKit ... or Jitsi Meet ... or BigBlueButton ...; media storage; CRM
#: write-back." None is chosen, and none is implemented.
CAPTURE_STACKS: tuple[str, ...] = (
    "LiveKit (SFU + egress for compositing/recording/transcoding to HLS)",
    "Jitsi Meet",
    "BigBlueButton",
    "Browser recorder (MediaRecorder API)",
)

# --------------------------------------------------------------------------- #
# Sourced quotes
# --------------------------------------------------------------------------- #

SOURCED_QUOTES: dict[str, str] = {
    "user_flow": (
        "[sourced] A rep records a short personal intro from inside the room (or a "
        "product team records a walkthrough) -> the video is placed on the room's hero "
        "section and personalised per buyer -> the buyer watches, loops back, and the "
        "seller's message lands before a call."
    ),
    "data_flow": (
        "[sourced] Screen/camera capture -> transcode -> adaptive stream (HLS) -> "
        "per-viewer playback events (start, % watched, completes, replays) -> engagement "
        "record on the deal."
    ),
    "data_sources": (
        "[sourced] Rep's camera/mic; room content (case study, demo) being walked "
        "through; buyer identity for personalisation."
    ),
    "apis": (
        "[sourced] LiveKit (Apache-2.0, SFU + egress for compositing/recording/"
        "transcoding to HLS) or Jitsi Meet (Apache-2.0) or BigBlueButton (LGPL-3.0); "
        "media storage; CRM write-back."
    ),
    "automations": (
        "[sourced] Auto-transcode and publish on save; auto-embed into new rooms from a "
        "template; notify the buyer when a new video lands; alert the rep on low "
        "completion."
    ),
    "product_surfaces": (
        "[sourced] In-room recorder; drag-and-drop placement; per-buyer personalisation; "
        "viewer-visible 'why I'm reaching out'; playback analytics."
    ),
    "extensibility": (
        "[sourced] Open-source: LiveKit / Jitsi / mediasoup for capture and egress; a "
        "browser recorder (MediaRecorder API) is sufficient for the intro use case. "
        "Commercial: Seismic, Consensus, Allego."
    ),
    "seismic": (
        '[sourced] "Personalize - Enable personalized content delivery with an '
        "easy-to-use, drag-and-drop design tool. Stand out from the crowd and win "
        "buyers' attention with video intros that add a personal touch, easily recorded "
        'right within the Digital Sales Room."'
    ),
    "consensus": (
        '[sourced] Consensus markets "On-Demand Video Demos - Easily shareable and '
        'always available."'
    ),
    "mindtickle": (
        '[sourced] Mindtickle describes Allego as "a unified platform combining sales '
        "learning and content sharing ... create and inject personalized video "
        "recordings into buyer portals, enabling buyers to see and hear the seller "
        'directly in the portal."'
    ),
}

#: What this build could not source or could not build. A reader must treat each
#: line as a hypothesis and not as a specification.
SOURCED_GAPS: tuple[str, ...] = (
    "The research marks native video hosting as unsourced, so this build hosts no "
    "media. It records a media reference and an HLS-style stream URL as opaque strings. "
    "Upload, transcode and delivery are stand-ins, not a working pipeline.",
    "The APIs section names three interchangeable capture stacks and does not choose "
    "one. This build implements no capture: a rep declares the source (camera, screen, "
    "or both) and the recorder is a stand-in.",
    "The data flow ends at 'engagement record on the deal' and names no workflow that "
    "provisions that surface. This build records the engagement roll-up locally and "
    "does not write to a CRM, so the write-back is not implemented.",
    "The automations list names four automations without saying which one this plugin "
    "owns. This build owns transcode-on-save, notify-the-buyer and alert-the-rep, and "
    "declares auto-embed-from-a-template as not implemented because it depends on the "
    "room template surface, which is another workflow's.",
    "The percentage at which a viewer counts as complete, and the percentage below "
    "which the rep is alerted, are both unnamed. Both are per-workflow defaults "
    "recorded as inferences.",
)

SOURCES: tuple[str, ...] = (
    "https://seismic.com/platform/digital-sales-rooms/",
    "https://goconsensus.com/",
    "https://www.allego.com/",
    "https://www.mindtickle.com/blog/best-digital-sales-room-platforms-comparison-and-review/",
    "https://github.com/livekit/livekit",
    "https://github.com/jitsi/jitsi-meet",
    "https://github.com/versatica/mediasoup",
)

#: The researched user flow, numbered, as the page renders it.
USER_FLOW: tuple[str, ...] = (
    "A rep records a short personal intro from inside the room, or a product team "
    "records a walkthrough.",
    "The video is placed on the room's hero section and personalised per buyer.",
    "The buyer watches, loops back, and the seller's message lands before a call.",
)

#: The four open decisions the research left, and the choice this build makes.
INFERENCES: dict[str, str] = {
    "completion_threshold": (
        f"A viewer counts as complete when a complete event arrives or the percentage "
        f"watched reaches {COMPLETE_PERCENT}. The research names '% watched' and no "
        f"threshold."
    ),
    "low_completion_threshold": (
        f"The rep is alerted when a viewer who started stays below "
        f"{LOW_COMPLETION_PERCENT} percent watched. The research names the automation "
        f"and no number."
    ),
    "one_video_per_hero": (
        "A room's hero shows one video. Placing a second supersedes the first rather "
        "than deleting it, so a buyer's playback history still resolves."
    ),
    "recording_belongs_to_its_room": (
        "A recording may only be placed on the hero of the room it was recorded for. "
        "The user flow is 'from inside the room', so a cross-room placement is refused."
    ),
    "personalisation_requires_a_known_buyer": (
        "When a room declares recipients (grants, invitations or viewers), a placement "
        "may only personalise for one of them. An unconfigured room accepts any buyer, "
        "which is the same shape WF-002 uses for page editing."
    ),
}


def describe_user_flow() -> list[dict[str, str]]:
    return [{"step": str(index), "text": text} for index, text in enumerate(USER_FLOW, start=1)]


def published_vocabulary() -> dict[str, Any]:
    """Every closed word and every sourced rule, in one payload.

    The page reads this instead of repeating the words, so a panel cannot drift
    from the rules the engine enforces.
    """

    return {
        "ticket": TICKET,
        "feature_id": FEATURE_ID,
        "name": NAME,
        "prefix": PREFIX,
        "kinds": list(KINDS),
        "kind_labels": dict(KIND_LABELS),
        "default_kind": DEFAULT_KIND,
        "capture_sources": list(CAPTURE_SOURCES),
        "source_labels": dict(SOURCE_LABELS),
        "default_source": DEFAULT_SOURCE,
        "transcodes": list(TRANSCODES),
        "recording_statuses": list(RECORDING_STATUSES),
        "surfaces": list(SURFACES),
        "placement_statuses": list(PLACEMENT_STATUSES),
        "placement_status_labels": dict(PLACEMENT_STATUS_LABELS),
        "events": list(EVENTS),
        "event_labels": dict(EVENT_LABELS),
        "states": list(ENGAGEMENT_STATES),
        "state_labels": dict(STATE_LABELS),
        "alert_statuses": list(ALERT_STATUSES),
        "complete_percent": COMPLETE_PERCENT,
        "low_completion_percent": LOW_COMPLETION_PERCENT,
        "automations": list(AUTOMATIONS),
        "implemented_automations": list(IMPLEMENTED_AUTOMATIONS),
        "unimplemented_automations": list(UNIMPLEMENTED_AUTOMATIONS),
        "capture_stacks": list(CAPTURE_STACKS),
        "hero_fragment": HERO_FRAGMENT,
        "hero_page_flag": HERO_PAGE_FLAG,
        "player_fields": list(PLAYER_FIELDS),
        "player_default_width": PLAYER_DEFAULT_WIDTH,
        "player_default_height": PLAYER_DEFAULT_HEIGHT,
        "player_default_autoplay": PLAYER_DEFAULT_AUTOPLAY,
        "access_grants_collection": ACCESS_GRANTS,
        "invitations_collection": INVITATIONS,
        "collections": dict(COLLECTIONS),
        "user_flow": describe_user_flow(),
        "inferences": dict(INFERENCES),
        "sourced_quotes": dict(SOURCED_QUOTES),
        "sourced_gaps": list(SOURCED_GAPS),
        "sources": list(SOURCES),
    }


__all__ = [
    "ACCESS_GRANTS",
    "ALERT_CLEARED",
    "ALERT_COLLECTION",
    "ALERT_OPEN",
    "ALERT_STATUSES",
    "AUTOMATIONS",
    "AUTOMATION_ALERT_REP",
    "AUTOMATION_EMBED_FROM_TEMPLATE",
    "AUTOMATION_NOTIFY_BUYER",
    "AUTOMATION_TRANSCODE_ON_SAVE",
    "CAPTURE_STACKS",
    "CAPTURE_SOURCES",
    "COLLECTIONS",
    "COMPLETE_PERCENT",
    "DEFAULT_KIND",
    "DEFAULT_ROLE",
    "DEFAULT_SOURCE",
    "ENGAGEMENT_COLLECTION",
    "ENGAGEMENT_STATES",
    "EVENT_COMPLETE",
    "EVENT_LABELS",
    "EVENT_PROGRESS",
    "EVENT_REPLAY",
    "EVENTS",
    "EVENT_START",
    "FEATURE_ID",
    "HERO_FRAGMENT",
    "HERO_PAGE_FLAG",
    "IMPLEMENTED_AUTOMATIONS",
    "INFERENCES",
    "INVITATIONS",
    "KINDS",
    "KIND_LABELS",
    "KIND_PERSONAL_INTRO",
    "KIND_WALKTHROUGH",
    "LOW_COMPLETION_PERCENT",
    "NAME",
    "NOTIFICATION_COLLECTION",
    "PAGE_COLLECTION",
    "PERCENT_EVENTS",
    "PLACEMENT_COLLECTION",
    "PLACEMENT_LIVE",
    "PLACEMENT_STATUSES",
    "PLACEMENT_STATUS_LABELS",
    "PLACEMENT_SUPERSEDED",
    "PLACEMENT_WITHDRAWN",
    "PLAYBACK_COLLECTION",
    "PLAYER_DEFAULT_AUTOPLAY",
    "PLAYER_DEFAULT_HEIGHT",
    "PLAYER_DEFAULT_WIDTH",
    "PLAYER_FIELDS",
    "PREFIX",
    "REASON_COMPLETED_LATE",
    "REASON_LOW_COMPLETION",
    "RECORDING_COLLECTION",
    "RECORDING_PLACED",
    "RECORDING_RECORDED",
    "RECORDING_STATUSES",
    "RECORDING_WITHDRAWN",
    "ROLE_IDS",
    "ROOM_VIEWERS_FIELD",
    "SOURCED_GAPS",
    "SOURCED_QUOTES",
    "SOURCES",
    "SOURCE_CAMERA",
    "SOURCE_CAMERA_AND_SCREEN",
    "SOURCE_LABELS",
    "SOURCE_SCREEN",
    "STATE_COMPLETED",
    "STATE_LABELS",
    "STATE_NOT_STARTED",
    "STATE_STARTED",
    "SURFACE_HERO",
    "SURFACES",
    "TICKET",
    "TRANSCODE_PENDING",
    "TRANSCODE_READY",
    "TRANSCODES",
    "UNIMPLEMENTED_AUTOMATIONS",
    "USER_FLOW",
    "describe_user_flow",
    "published_vocabulary",
]
