"""WF-128: the researched vocabulary for an in-room live presentation.

The workflow is "In-room live presentation / co-browsing". The research is
``docs/research/digital-sales-room-workflows/wf/WF-128.md``, quoted in full on
issue #138. This module is the closed set of words a page renders and a
validator enforces, each declared beside the sentence it came from.

Three kinds of term live here:

* a **researched** term carries a quote: the research states it.
* an **inferred** term carries nothing here and is recorded in
  :data:`INFERENCES`, because the research left the choice open.
* a **collection name** is this build's own storage key and carries neither.

The one decision the issue explicitly asks for is the stack. The research names
candidate SFUs (LiveKit Apache-2.0, Jitsi Meet Apache-2.0, BigBlueButton
LGPL-3.0, mediasoup ISC) and candidate deck engines (reveal.js MIT, Slidev MIT,
impress.js MIT) and does not choose one. This build chooses one of each and
records the licence and the reason in :data:`STACK_CHOICE`. The choice is not
implemented as a transport: this product runs no media server, so the session is
modelled as state and events and the live link is a stand-in. See
:data:`SOURCED_GAPS`.

Reuse, not re-declaration
-------------------------

The workflow starts "from the room" and the buyer is an existing recipient, so
it depends on two merged workflows rather than inventing their shapes:

* WF-002 (``dsr.pages``) owns room pages. The agenda this feature plans is the
  room's own pages, in page order, so the "pages" the presenter reveals are the
  pages the room already has.
* WF-004 (``dsr.roles``) owns the per-room recipient list. :data:`ACCESS_GRANTS`
  and :data:`INVITATIONS` are that workflow's collection names, read by name and
  not re-declared, and the invitee identity is the principal email those rows
  carry.

Per-participant link gating is owned by the link-gating and revocation
workflows, which this feature does not edit. This module records a token and a
stand-in URL per participant and says so in :data:`SOURCED_GAPS`.

Nothing here imports the framework. See :mod:`dsr.live_sessions.engine` for the
only module that reads or writes.
"""

from __future__ import annotations

from typing import Any

from dsr import roles as room_roles

# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #

TICKET = "WF-128"
FEATURE_ID = "wf-128-live-presentation"
NAME = "In-room live presentation / co-browsing"
PREFIX = "/api/wf-128"

# --------------------------------------------------------------------------- #
# Collections this workflow owns
#
# All of them are ordinary JSON in ``records.data``: no migration, no typed
# column. A team adding a field needs no coordination with anyone.
# --------------------------------------------------------------------------- #

SESSION_COLLECTION = "wf128_session"
PARTICIPANT_COLLECTION = "wf128_participant"
EVENT_COLLECTION = "wf128_event"
QUESTION_COLLECTION = "wf128_question"
ATTENDANCE_COLLECTION = "wf128_attendance"
RECORDING_COLLECTION = "wf128_recording"
NOTIFICATION_COLLECTION = "wf128_notification"

COLLECTIONS: dict[str, str] = {
    "session": SESSION_COLLECTION,
    "participant": PARTICIPANT_COLLECTION,
    "event": EVENT_COLLECTION,
    "question": QUESTION_COLLECTION,
    "attendance": ATTENDANCE_COLLECTION,
    "recording": RECORDING_COLLECTION,
    "notification": NOTIFICATION_COLLECTION,
}

# --------------------------------------------------------------------------- #
# Reuse: WF-002 (room pages) and WF-004 (roles and recipient rows)
# --------------------------------------------------------------------------- #

#: WF-002's collection of room pages. The agenda is built from these, in order.
PAGE_COLLECTION = "page"

#: WF-004's per-room recipient list. A grant row carries the buyer's email as
#: ``principal``; an invitation carries it as ``email``.
ACCESS_GRANTS = room_roles.ACCESS
INVITATIONS = room_roles.INVITATIONS

#: WF-004's role identifiers, reused so a participant can say which recipients
#: are room editors. Nothing here grants or changes a role.
ROLE_IDS: tuple[str, ...] = tuple(room_roles.ROLE_IDS)
DEFAULT_ROLE = room_roles.DEFAULT_ROLE

#: The room payload field WF-002 reads for a view-only audience. A room that
#: declares no grants, no invitations and no viewers is unconfigured, and this
#: feature then accepts any named participant rather than pretending a gate
#: exists.
ROOM_VIEWERS_FIELD = "viewers"

# --------------------------------------------------------------------------- #
# The session
#
# "A rep starts a live session from the room". A session is created, then
# started, then ended. There are three states and no other.
# --------------------------------------------------------------------------- #

STATE_SCHEDULED = "scheduled"
STATE_LIVE = "live"
STATE_ENDED = "ended"

SESSION_STATES: tuple[str, ...] = (STATE_SCHEDULED, STATE_LIVE, STATE_ENDED)

SESSION_STATE_LABELS: dict[str, str] = {
    STATE_SCHEDULED: "Scheduled, not started",
    STATE_LIVE: "Live",
    STATE_ENDED: "Ended",
}

#: The two session roles the user flow names: a rep drives, and everyone else
#: follows. "the buyer (or a whole buying committee, each with their own link)
#: joins and follows the presenter".
ROLE_PRESENTER = "presenter"
ROLE_FOLLOWER = "follower"

PARTICIPANT_ROLES: tuple[str, ...] = (ROLE_PRESENTER, ROLE_FOLLOWER)

PARTICIPANT_ROLE_LABELS: dict[str, str] = {
    ROLE_PRESENTER: "Presenter",
    ROLE_FOLLOWER: "Follower",
}

#: A session has exactly one presenter. The rule is enforced on invite and on
#: start, so "the presenter" is never ambiguous.
PRESENTER_COUNT = 1

# --------------------------------------------------------------------------- #
# The agenda: the deck or the actual product
#
# "follows the presenter through the deck or the actual product". The deck is
# the room's own pages (WF-002). The actual product is a screen share, which
# this build records as a single step rather than performing.
# --------------------------------------------------------------------------- #

STEP_PAGE = "page"
STEP_SCREEN = "screen"

STEP_KINDS: tuple[str, ...] = (STEP_PAGE, STEP_SCREEN)

STEP_KIND_LABELS: dict[str, str] = {
    STEP_PAGE: "Room page",
    STEP_SCREEN: "Live screen",
}

#: The label a single screen step carries when the room has no pages to present.
SCREEN_STEP_LABEL = "Presenter screen"

#: A session must have at least one step to start. A room with no pages still
#: gets the screen step, so start never fails on an empty agenda.
DEFAULT_AGENDA_LABEL = "Shared screen"

# --------------------------------------------------------------------------- #
# The reveal order
#
# "a reveal order is respected". The presenter reveals the agenda one step at a
# time. A forward move is one step; a backward move is any earlier step. A
# forward jump would reveal a step out of order, so it is refused.
# --------------------------------------------------------------------------- #

#: How far forward the presenter may move in one call.
REVEAL_STEP = 1

#: Why a move was refused. A page branches on the token and never on prose.
REASON_NOT_PRESENTER = "not_the_presenter"
REASON_SKIPPED_REVEAL = "reveal_out_of_order"
REASON_OUT_OF_RANGE = "step_out_of_range"
REASON_SESSION_NOT_LIVE = "session_not_live"
REASON_ALREADY_STARTED = "session_already_started"
REASON_ALREADY_ENDED = "session_already_ended"
REASON_FOLLOWER_CANNOT_CONTROL = "follower_cannot_control"
REASON_SECOND_PRESENTER = "second_presenter_refused"
REASON_QUESTION_ALREADY_ANSWERED = "question_already_answered"
REASON_PARTICIPANT_ALREADY_LEFT = "participant_already_left"

# --------------------------------------------------------------------------- #
# A participant's own link
#
# "a whole buying committee, each with their own link". One guest identity per
# invitee, so one join token per participant. The token is recorded here and
# the access rules that gate it belong to the link-gating workflows.
# --------------------------------------------------------------------------- #

PARTICIPANT_INVITED = "invited"
PARTICIPANT_JOINED = "joined"
PARTICIPANT_LEFT = "left"

PARTICIPANT_STATES: tuple[str, ...] = (
    PARTICIPANT_INVITED,
    PARTICIPANT_JOINED,
    PARTICIPANT_LEFT,
)

PARTICIPANT_STATE_LABELS: dict[str, str] = {
    PARTICIPANT_INVITED: "Invited",
    PARTICIPANT_JOINED: "Following",
    PARTICIPANT_LEFT: "Left",
}

#: The path prefix a stand-in join link carries. No host serves it.
JOIN_PATH = "/join/wf128"

# --------------------------------------------------------------------------- #
# The session event log
#
# Every state change is recorded as an event, because no live transport exists
# to observe it. The log is what makes the session replayable in tests.
# --------------------------------------------------------------------------- #

EVENT_SESSION_STARTED = "session_started"
EVENT_SESSION_ENDED = "session_ended"
EVENT_PARTICIPANT_INVITED = "participant_invited"
EVENT_PARTICIPANT_JOINED = "participant_joined"
EVENT_PARTICIPANT_LEFT = "participant_left"
EVENT_STEP_REVEALED = "step_revealed"
EVENT_STEP_BACK = "step_back"
EVENT_QUESTION_ASKED = "question_asked"
EVENT_QUESTION_ANSWERED = "question_answered"
EVENT_RECORDING_PUBLISHED = "recording_published"
EVENT_ACCESS_LINK_ISSUED = "access_link_issued"

EVENT_KINDS: tuple[str, ...] = (
    EVENT_SESSION_STARTED,
    EVENT_SESSION_ENDED,
    EVENT_PARTICIPANT_INVITED,
    EVENT_PARTICIPANT_JOINED,
    EVENT_PARTICIPANT_LEFT,
    EVENT_STEP_REVEALED,
    EVENT_STEP_BACK,
    EVENT_QUESTION_ASKED,
    EVENT_QUESTION_ANSWERED,
    EVENT_RECORDING_PUBLISHED,
    EVENT_ACCESS_LINK_ISSUED,
)

EVENT_LABELS: dict[str, str] = {
    EVENT_SESSION_STARTED: "Session started",
    EVENT_SESSION_ENDED: "Session ended",
    EVENT_PARTICIPANT_INVITED: "Invitee added",
    EVENT_PARTICIPANT_JOINED: "Participant joined",
    EVENT_PARTICIPANT_LEFT: "Participant left",
    EVENT_STEP_REVEALED: "Step revealed",
    EVENT_STEP_BACK: "Stepped back",
    EVENT_QUESTION_ASKED: "Question asked",
    EVENT_QUESTION_ANSWERED: "Question answered",
    EVENT_RECORDING_PUBLISHED: "Replay published",
    EVENT_ACCESS_LINK_ISSUED: "Access link issued",
}

# --------------------------------------------------------------------------- #
# Questions
#
# "questions asked live are logged against the deal". A question is open until
# the presenter answers it.
# --------------------------------------------------------------------------- #

QUESTION_OPEN = "open"
QUESTION_ANSWERED = "answered"

QUESTION_STATES: tuple[str, ...] = (QUESTION_OPEN, QUESTION_ANSWERED)

QUESTION_STATE_LABELS: dict[str, str] = {
    QUESTION_OPEN: "Open",
    QUESTION_ANSWERED: "Answered",
}

# --------------------------------------------------------------------------- #
# A question's destination, and where attendance is written
#
# The research disagrees with itself: the automation line says "attendance
# written to CRM" and the data flow says "attendance + questions -> deal
# record". This build writes both to the room's own record, because the data
# flow names a surface that exists in this product and no CRM write-back is
# provisioned. The disagreement is recorded in :data:`SOURCED_GAPS` and the
# choice in :data:`INFERENCES`.
# --------------------------------------------------------------------------- #

DESTINATION_ROOM = "room"
DESTINATION_CRM = "crm"

#: The session's ``deal_ref`` is the room it was started from, so "the deal
#: record" and "the room" are one destination in this build.
DEAL_FIELD = "room_id"

# --------------------------------------------------------------------------- #
# The recording
#
# "the session is recorded and attached to the room as a replay". Auto-record
# on start and auto-publish on end. The artefact is binary and the store holds
# JSON, so this build records an opaque reference and a stand-in playback URL.
# --------------------------------------------------------------------------- #

RECORDING_RECORDING = "recording"
RECORDING_PUBLISHED = "published"

RECORDING_STATES: tuple[str, ...] = (RECORDING_RECORDING, RECORDING_PUBLISHED)

RECORDING_STATE_LABELS: dict[str, str] = {
    RECORDING_RECORDING: "Recording now",
    RECORDING_PUBLISHED: "Published as a replay",
}

#: The stand-in playback URL's host. ``.invalid`` is reserved by RFC 2606, so it
#: can never resolve to a real server by accident.
REPLAY_HOST = "https://replay.example.invalid/wf128"

#: Where a real artefact would live. Recorded, not built: this repository has no
#: object store.
ARTEFACT_STORE_NOTE = (
    "object storage (S3-compatible) composed via SFU egress, then transcoded to HLS/MP4"
)

# --------------------------------------------------------------------------- #
# Attendance
# --------------------------------------------------------------------------- #

ATTENDANCE_ATTENDED = "attended"
ATTENDANCE_INVITED_ONLY = "invited_only"

ATTENDANCE_STATES: tuple[str, ...] = (ATTENDANCE_ATTENDED, ATTENDANCE_INVITED_ONLY)

ATTENDANCE_STATE_LABELS: dict[str, str] = {
    ATTENDANCE_ATTENDED: "Attended",
    ATTENDANCE_INVITED_ONLY: "Invited, did not join",
}

# --------------------------------------------------------------------------- #
# The live transport is a stand-in
#
# No SFU, no WebSocket and no realtime service runs in this build, so a
# follower reads the presenter's cursor by polling the state route. The word for
# that is "poll" and the vocabulary publishes the note beside it.
# --------------------------------------------------------------------------- #

TRANSPORT_STAND_IN = "poll"

TRANSPORT_NOTE = (
    "No realtime transport exists in this build. A follower reads the presenter's "
    "cursor by polling the state route, so sync is recorded state and not a live stream."
)

# --------------------------------------------------------------------------- #
# The stack choice, with licence and reason
#
# The issue names the candidates and asks for one of each, with the licence and
# the reason recorded. Nothing here is a dependency: the choice is recorded so
# a reader knows what a real transport would be built on.
# --------------------------------------------------------------------------- #

STACK_SFU = "LiveKit"
STACK_SFU_LICENCE = "Apache-2.0"
STACK_SFU_REASON = (
    "LiveKit is Apache-2.0 and its egress component matches the researched data flow "
    "('recording composed via egress'). The rejected alternatives were BigBlueButton "
    "(LGPL-3.0, purpose-built but copyleft and a heavier deployment) and mediasoup "
    "(ISC, which is an SFU library with no egress, so the recording path would be "
    "custom work)."
)

STACK_DECK = "reveal.js"
STACK_DECK_LICENCE = "MIT"
STACK_DECK_REASON = (
    "reveal.js is MIT and its fragment model is exactly the reveal-per-step order this "
    "workflow enforces, so the rule and the engine agree. Slidev (MIT) needs a Node "
    "build step per deck and impress.js (MIT) is unmaintained. Neither is a dependency "
    "here; the room's own pages stand in for the deck."
)

STACK_CHOICE: dict[str, dict[str, str]] = {
    "sfu": {"name": STACK_SFU, "licence": STACK_SFU_LICENCE, "reason": STACK_SFU_REASON},
    "deck": {"name": STACK_DECK, "licence": STACK_DECK_LICENCE, "reason": STACK_DECK_REASON},
}

# --------------------------------------------------------------------------- #
# Automations
#
# "Auto-record and auto-publish; auto-notify invitees; auto-translate captions;
# auto-clip highlights; attendance written to CRM." This build owns the first
# two and the attendance destination; the rest are declared not implemented
# rather than pretended.
# --------------------------------------------------------------------------- #

AUTOMATION_RECORD_AND_PUBLISH = "Auto-record and auto-publish"
AUTOMATION_NOTIFY_INVITEES = "Auto-notify invitees"
AUTOMATION_TRANSLATE_CAPTIONS = "Auto-translate captions"
AUTOMATION_CLIP_HIGHLIGHTS = "Auto-clip highlights"
AUTOMATION_ATTENDANCE_TO_CRM = "Attendance written to CRM"

AUTOMATIONS: tuple[str, ...] = (
    AUTOMATION_RECORD_AND_PUBLISH,
    AUTOMATION_NOTIFY_INVITEES,
    AUTOMATION_TRANSLATE_CAPTIONS,
    AUTOMATION_CLIP_HIGHLIGHTS,
    AUTOMATION_ATTENDANCE_TO_CRM,
)

IMPLEMENTED_AUTOMATIONS: tuple[str, ...] = (
    AUTOMATION_RECORD_AND_PUBLISH,
    AUTOMATION_NOTIFY_INVITEES,
)

UNIMPLEMENTED_AUTOMATIONS: tuple[str, ...] = (
    AUTOMATION_TRANSLATE_CAPTIONS,
    AUTOMATION_CLIP_HIGHLIGHTS,
    AUTOMATION_ATTENDANCE_TO_CRM,
)

# --------------------------------------------------------------------------- #
# Sourced quotes
# --------------------------------------------------------------------------- #

SOURCED_QUOTES: dict[str, str] = {
    "user_flow": (
        "[sourced] A rep starts a live session from the room -> the buyer (or a whole "
        "buying committee, each with their own link) joins and follows the presenter "
        "through the deck or the actual product -> the session is recorded and attached "
        "to the room as a replay the buyer can revisit asynchronously -> questions asked "
        "live are logged against the deal."
    ),
    "data_flow": (
        "[sourced] Presenter screen/audio -> SFU (selective forwarding) -> per-participant "
        "tiles; recording composed via egress -> HLS/MP4 -> stored with the room; "
        "attendance + questions -> deal record."
    ),
    "data_sources": (
        "[sourced] Presenter's screen; the room's content; participant identities; the "
        "recording artefact."
    ),
    "apis": (
        "[sourced] LiveKit (Apache-2.0) with egress; or Jitsi Meet (Apache-2.0); or "
        "BigBlueButton (LGPL-3.0) which is purpose-built for presentation + recording; "
        "mediasoup (ISC) if building a custom SFU; presentation engine (reveal.js MIT, "
        "Slidev MIT, impress.js MIT)."
    ),
    "automations": (
        "[sourced] Auto-record and auto-publish; auto-notify invitees; auto-translate "
        "captions; auto-clip highlights; attendance written to CRM."
    ),
    "product_surfaces": (
        "[sourced] Multi-party video; screen share; shared cursor/annotation; chat and "
        "Q&A; recording with playback; per-participant attendance; presenter mode."
    ),
    "extensibility": (
        "[sourced] Fully buildable open source - this is the best-covered DSR capability "
        "in FOSS. LiveKit (Go, Apache-2.0) + reveal.js/Slidev (MIT) + Mediasoup (ISC) is a "
        "complete stack. Commercial: Consensus ('Live Demo Platform') and Dock."
    ),
    "consensus": (
        '[sourced] Consensus nav lists "Live Demo Platform - Deliver live demos with '
        'confidence" alongside "Interactive Product Demos - Create on-demand product demos."'
    ),
    "dock": (
        "[sourced] Dock's homepage product walkthrough markup shows a room section "
        'labelled "Technical demo / Watch recording" next to "Discovery call / View '
        'notes," i.e. the live session and its recording are both first-class room objects.'
    ),
}

#: What this build could not source or could not build. A reader must treat each
#: line as a hypothesis and not as a specification.
SOURCED_GAPS: tuple[str, ...] = (
    "No realtime transport exists in this product, so this build runs no SFU and opens "
    "no WebSocket. The session is modelled as state, roles and an event log, and a "
    "follower polls the presenter's cursor. The live link is a stand-in, not a stream.",
    "The research names four interchangeable SFUs and three deck engines and does not "
    "choose. This build records one of each with its licence and reason in the vocabulary, "
    "but installs neither, so the choice is a documented decision and not a dependency.",
    "Per-participant link gating is owned by the existing link-gating and revocation "
    "workflows. This feature records one join token per participant and a stand-in URL "
    "and does not enforce, revoke or expire a link itself.",
    "The recording artefact is binary and the store holds JSON. This build records an "
    "opaque artefact reference and a stand-in playback URL and stores no bytes.",
    "The automation line says 'attendance written to CRM' and the data flow says "
    "'attendance + questions -> deal record'. The two disagree on the destination. This "
    "build writes both to the room's record, because the data flow names a surface that "
    "exists here and no CRM write-back is provisioned.",
    "Auto-translate captions and auto-clip highlights are named and not built: neither "
    "has a surface in this product and both need media processing this build does not do.",
)

SOURCES: tuple[str, ...] = (
    "https://goconsensus.com/",
    "https://www.dock.us/",
    "https://github.com/livekit/livekit",
    "https://github.com/jitsi/jitsi-meet",
    "https://github.com/bigbluebutton/bigbluebutton",
    "https://github.com/hakimel/reveal.js",
    "https://github.com/slidevjs/slidev",
)

#: The researched user flow, numbered, as the page renders it.
USER_FLOW: tuple[str, ...] = (
    "A rep starts a live session from the room.",
    "The buyer, or a whole buying committee each with their own link, joins and follows "
    "the presenter through the deck or the actual product.",
    "The session is recorded and attached to the room as a replay the buyer can revisit "
    "asynchronously.",
    "Questions asked live are logged against the deal.",
)

#: The open decisions the research left, and the choice this build makes.
INFERENCES: dict[str, str] = {
    "transport_is_a_stand_in": (
        "No realtime service is available, so the presenter's cursor is state a follower "
        "polls. The research assumes an SFU and the data flow assumes selective "
        "forwarding; neither exists here."
    ),
    "one_presenter_per_session": (
        f"A session has exactly {PRESENTER_COUNT} presenter. The user flow says the rep "
        "drives and everyone else follows, so a second presenter is refused rather than "
        "making 'the presenter' ambiguous."
    ),
    "reveal_forward_by_one": (
        f"The presenter moves forward by at most {REVEAL_STEP} step per call and may move "
        "back to any earlier step. A forward jump would reveal a step out of order, which "
        "is the reveal order the workflow exists to respect."
    ),
    "agenda_is_the_rooms_pages": (
        "The deck is the room's own pages in page order (WF-002), so the steps a presenter "
        "reveals are the pages the room already has. A room with no pages gets one screen "
        "step, so start never fails on an empty agenda."
    ),
    "attendance_and_questions_go_to_the_room": (
        "The research's automation line says CRM and its data flow says the deal record. "
        "This build writes both to the room record, which is the deal surface that exists "
        "here, and records the CRM write-back as not implemented."
    ),
    "attendance_is_join_based": (
        "A participant counts as attended when they join at all, and the row records the "
        "seconds between join and leave. The research names per-participant attendance and "
        "no threshold, so no minimum-dwell rule is invented."
    ),
    "the_recording_reference_is_opaque": (
        "The artefact is binary and the store holds JSON, so a session records a reference "
        "and a stand-in playback URL. No bytes and no object store exist in this build."
    ),
    "invitees_are_the_rooms_recipients": (
        "When a session is created with no explicit invitees, the invitees are the room's "
        "recipients (WF-004's grants and invitations). A room that declares nobody invites "
        "nobody, which is visible rather than silently empty."
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
        "session_states": list(SESSION_STATES),
        "session_state_labels": dict(SESSION_STATE_LABELS),
        "participant_roles": list(PARTICIPANT_ROLES),
        "participant_role_labels": dict(PARTICIPANT_ROLE_LABELS),
        "presenter_count": PRESENTER_COUNT,
        "step_kinds": list(STEP_KINDS),
        "step_kind_labels": dict(STEP_KIND_LABELS),
        "reveal_step": REVEAL_STEP,
        "participant_states": list(PARTICIPANT_STATES),
        "participant_state_labels": dict(PARTICIPANT_STATE_LABELS),
        "join_path": JOIN_PATH,
        "event_kinds": list(EVENT_KINDS),
        "event_labels": dict(EVENT_LABELS),
        "question_states": list(QUESTION_STATES),
        "question_state_labels": dict(QUESTION_STATE_LABELS),
        "recording_states": list(RECORDING_STATES),
        "recording_state_labels": dict(RECORDING_STATE_LABELS),
        "attendance_states": list(ATTENDANCE_STATES),
        "attendance_state_labels": dict(ATTENDANCE_STATE_LABELS),
        "transport": TRANSPORT_STAND_IN,
        "transport_note": TRANSPORT_NOTE,
        "stack_choice": {key: dict(value) for key, value in STACK_CHOICE.items()},
        "artefact_store_note": ARTEFACT_STORE_NOTE,
        "automations": list(AUTOMATIONS),
        "implemented_automations": list(IMPLEMENTED_AUTOMATIONS),
        "unimplemented_automations": list(UNIMPLEMENTED_AUTOMATIONS),
        "deal_field": DEAL_FIELD,
        "access_grants_collection": ACCESS_GRANTS,
        "invitations_collection": INVITATIONS,
        "page_collection": PAGE_COLLECTION,
        "collections": dict(COLLECTIONS),
        "user_flow": describe_user_flow(),
        "inferences": dict(INFERENCES),
        "sourced_quotes": dict(SOURCED_QUOTES),
        "sourced_gaps": list(SOURCED_GAPS),
        "sources": list(SOURCES),
    }


__all__ = [
    "ACCESS_GRANTS",
    "ARTEFACT_STORE_NOTE",
    "ATTENDANCE_ATTENDED",
    "ATTENDANCE_COLLECTION",
    "ATTENDANCE_INVITED_ONLY",
    "ATTENDANCE_STATES",
    "ATTENDANCE_STATE_LABELS",
    "AUTOMATIONS",
    "AUTOMATION_ATTENDANCE_TO_CRM",
    "AUTOMATION_CLIP_HIGHLIGHTS",
    "AUTOMATION_NOTIFY_INVITEES",
    "AUTOMATION_RECORD_AND_PUBLISH",
    "AUTOMATION_TRANSLATE_CAPTIONS",
    "COLLECTIONS",
    "DEAL_FIELD",
    "DEFAULT_AGENDA_LABEL",
    "DEFAULT_ROLE",
    "EVENT_ACCESS_LINK_ISSUED",
    "EVENT_COLLECTION",
    "EVENT_KINDS",
    "EVENT_LABELS",
    "EVENT_PARTICIPANT_INVITED",
    "EVENT_PARTICIPANT_JOINED",
    "EVENT_PARTICIPANT_LEFT",
    "EVENT_QUESTION_ANSWERED",
    "EVENT_QUESTION_ASKED",
    "EVENT_RECORDING_PUBLISHED",
    "EVENT_SESSION_ENDED",
    "EVENT_SESSION_STARTED",
    "EVENT_STEP_BACK",
    "EVENT_STEP_REVEALED",
    "FEATURE_ID",
    "IMPLEMENTED_AUTOMATIONS",
    "INFERENCES",
    "INVITATIONS",
    "JOIN_PATH",
    "NAME",
    "NOTIFICATION_COLLECTION",
    "PAGE_COLLECTION",
    "PARTICIPANT_COLLECTION",
    "PARTICIPANT_INVITED",
    "PARTICIPANT_JOINED",
    "PARTICIPANT_LEFT",
    "PARTICIPANT_ROLES",
    "PARTICIPANT_ROLE_LABELS",
    "PARTICIPANT_STATES",
    "PARTICIPANT_STATE_LABELS",
    "PREFIX",
    "PRESENTER_COUNT",
    "QUESTION_ANSWERED",
    "QUESTION_COLLECTION",
    "QUESTION_OPEN",
    "QUESTION_STATES",
    "QUESTION_STATE_LABELS",
    "REASON_ALREADY_ENDED",
    "REASON_ALREADY_STARTED",
    "REASON_FOLLOWER_CANNOT_CONTROL",
    "REASON_NOT_PRESENTER",
    "REASON_OUT_OF_RANGE",
    "REASON_PARTICIPANT_ALREADY_LEFT",
    "REASON_QUESTION_ALREADY_ANSWERED",
    "REASON_SECOND_PRESENTER",
    "REASON_SESSION_NOT_LIVE",
    "REASON_SKIPPED_REVEAL",
    "RECORDING_COLLECTION",
    "RECORDING_PUBLISHED",
    "RECORDING_RECORDING",
    "RECORDING_STATES",
    "RECORDING_STATE_LABELS",
    "REPLAY_HOST",
    "REVEAL_STEP",
    "ROLE_FOLLOWER",
    "ROLE_IDS",
    "ROLE_PRESENTER",
    "ROOM_VIEWERS_FIELD",
    "SCREEN_STEP_LABEL",
    "SESSION_COLLECTION",
    "SESSION_STATES",
    "SESSION_STATE_LABELS",
    "SOURCED_GAPS",
    "SOURCED_QUOTES",
    "SOURCES",
    "STACK_CHOICE",
    "STACK_DECK",
    "STACK_DECK_LICENCE",
    "STACK_DECK_REASON",
    "STACK_SFU",
    "STACK_SFU_LICENCE",
    "STACK_SFU_REASON",
    "STATE_ENDED",
    "STATE_LIVE",
    "STATE_SCHEDULED",
    "STEP_KINDS",
    "STEP_KIND_LABELS",
    "STEP_PAGE",
    "STEP_SCREEN",
    "TICKET",
    "TRANSPORT_NOTE",
    "TRANSPORT_STAND_IN",
    "UNIMPLEMENTED_AUTOMATIONS",
    "USER_FLOW",
    "describe_user_flow",
    "published_vocabulary",
]
