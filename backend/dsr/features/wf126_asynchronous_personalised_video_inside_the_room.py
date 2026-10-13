"""WF-126: asynchronous personalised video inside the room.

The rules live in :mod:`dsr.personalised_video`, which knows nothing about HTTP.
This module is the four things a feature contributes, and the three things it
must never contribute.

What this module contributes
----------------------------
* The route table, under the prefix this feature owns (``/api/wf-126``).
* The error-to-response mapping, exported as :data:`EXCEPTION_HANDLERS` because
  FastAPI accepts handlers on the app object only.
* The demo rows, as ``seed(db, context)`` rather than an edit to the shared
  ``backend/seed.py``.
* The vocabulary, the inferences and the researched user flow the page renders.

What it must never contribute
-----------------------------
* No shared file: ``dsr/api.py``, ``dsr/deps.py``, ``dsr/store.py``,
  ``dsr/db/audited.py``, ``seed.py``, ``App.jsx``, ``main.jsx``, ``lib/api.js``,
  ``lib/features.js``, ``components/ui.jsx``, ``vite.config.js``.
* No import of ``dsr.api``. Dependencies come from ``dsr.deps``.
* No hand-written ``source=`` string. :func:`_source` builds every one from
  :data:`router`, so the audit row names the route that actually served.

Source: ``docs/research/digital-sales-room-workflows/wf/WF-126.md``; issue 163.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.personalised_video import vocabulary as v
from dsr.personalised_video.engine import VideoEngine
from dsr.personalised_video.errors import (
    VideoConflict,
    VideoNotFound,
    VideoValidationError,
)
from dsr.store import RecordStore

FEATURE = {
    "id": v.FEATURE_ID,
    "ticket": v.TICKET,
    "name": v.NAME,
    "description": (
        "A rep records a personal intro, or a product team records a walkthrough, from "
        "inside the room. The video is placed on the room's hero section and personalised "
        "per buyer, and the buyer's playback events (start, percent watched, completes, "
        "replays) are recorded and rolled up. A placement notifies the buyers; a viewer "
        "who stops well short of the end raises an alert for the rep. No media is hosted: "
        "the reference and the player URL are stand-ins."
    ),
    "nav": [{"id": v.FEATURE_ID, "label": "Personalised video"}],
}

router = APIRouter(prefix=v.PREFIX, tags=["wf-126"])


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


def _validation_error(request: Request, exc: VideoValidationError) -> JSONResponse:
    """422, with one message beside each bad field."""

    return JSONResponse(status_code=422, content={"error": "video_invalid", **exc.as_dict()})


def _not_found(request: Request, exc: VideoNotFound) -> JSONResponse:
    """404, worded so it does not confirm what a caller may not see."""

    return JSONResponse(status_code=404, content={"error": "video_not_found", **exc.as_dict()})


def _conflict(request: Request, exc: VideoConflict) -> JSONResponse:
    """409, with a stable reason token the page branches on."""

    return JSONResponse(status_code=409, content={"error": "video_conflict", **exc.as_dict()})


EXCEPTION_HANDLERS = {
    VideoValidationError: _validation_error,
    VideoNotFound: _not_found,
    VideoConflict: _conflict,
}


# --------------------------------------------------------------------------- #
# Composition
# --------------------------------------------------------------------------- #


def get_engine(store: RecordStore = StoreDep) -> VideoEngine:
    """The video engine on the shared audited store, with the production clock."""

    return VideoEngine(store)


EngineDep = Depends(get_engine)


def _source(verb: str, suffix: str) -> str:
    """The audit ``source`` for a write: the path this router actually serves."""

    return f"{verb} {router.prefix}{suffix}"


# --------------------------------------------------------------------------- #
# Reference data
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def vocabulary() -> dict[str, Any]:
    """Every closed word, sourced rule and collection this workflow enforces."""

    return VideoEngine.vocabulary()


@router.get("/summary")
def summary(
    room_id: str | None = Query(default=None),
    engine: VideoEngine = EngineDep,
) -> dict[str, Any]:
    """The headline counts a seller needs before opening a room's video."""

    return engine.summary(room_id=room_id)


@router.get("/recipients")
def recipients(
    room_id: str = Query(...),
    engine: VideoEngine = EngineDep,
) -> dict[str, Any]:
    """The room's buyers, reused from WF-004's grants, invitations and viewers."""

    entries = engine.recipients(room_id)
    return {"room_id": room_id, "count": len(entries), "entries": entries}


# --------------------------------------------------------------------------- #
# Recordings
# --------------------------------------------------------------------------- #


@router.get("/recordings")
def list_recordings(
    room_id: str | None = Query(default=None),
    kind: str | None = Query(default=None),
    status: str | None = Query(default=None),
    engine: VideoEngine = EngineDep,
) -> dict[str, Any]:
    """Every recording, newest first, scoped to a room when one is given."""

    entries = engine.recordings(room_id=room_id, kind=kind, status=status)
    return {"count": len(entries), "entries": entries}


@router.post("/recordings", status_code=201)
def create_recording(
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: VideoEngine = EngineDep,
) -> dict[str, Any]:
    """Record a personal intro or a walkthrough, and transcode it on save."""

    return engine.create_recording(
        payload, source=_source("POST", "/recordings"), actor=payload.get("actor")
    )


@router.get("/recordings/{recording_id}")
def read_recording(recording_id: str, engine: VideoEngine = EngineDep) -> dict[str, Any]:
    """One recording, with its media reference and its stand-in stream URL."""

    return engine.get_recording(recording_id)


@router.post("/recordings/{recording_id}/playback", status_code=201)
def record_playback(
    recording_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: VideoEngine = EngineDep,
) -> dict[str, Any]:
    """Record one per-viewer playback event and fold it into the roll-up."""

    return engine.record_playback(
        recording_id,
        payload,
        source=_source("POST", "/recordings/{recording_id}/playback"),
        actor=payload.get("actor"),
    )


@router.get("/recordings/{recording_id}/events")
def recording_events(
    recording_id: str,
    viewer: str | None = Query(default=None),
    engine: VideoEngine = EngineDep,
) -> dict[str, Any]:
    """The per-viewer playback log for one recording, newest first."""

    entries = engine.events(recording_id=recording_id, viewer=viewer)
    return {"recording_id": recording_id, "count": len(entries), "entries": entries}


@router.get("/recordings/{recording_id}/engagement")
def recording_engagement(
    recording_id: str,
    engine: VideoEngine = EngineDep,
) -> dict[str, Any]:
    """One roll-up per viewer for this recording."""

    entries = engine.engagement(recording_id=recording_id)
    return {"recording_id": recording_id, "count": len(entries), "entries": entries}


# --------------------------------------------------------------------------- #
# The hero surface
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/hero")
def read_hero(
    room_id: str,
    viewer: str | None = Query(default=None, description="Personalise for this buyer"),
    engine: VideoEngine = EngineDep,
) -> dict[str, Any]:
    """The video on the room's hero. With a viewer, that buyer's personalisation."""

    if viewer:
        return engine.hero_for(room_id, viewer)
    return engine.hero(room_id)


@router.post("/rooms/{room_id}/recordings/{recording_id}/place", status_code=201)
def place_on_hero(
    room_id: str,
    recording_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: VideoEngine = EngineDep,
) -> dict[str, Any]:
    """Place a recording on the room's hero section and personalise it per buyer."""

    return engine.place_on_hero(
        room_id,
        recording_id,
        payload,
        source=_source("POST", "/rooms/{room_id}/recordings/{recording_id}/place"),
        actor=payload.get("actor"),
    )


@router.get("/rooms/{room_id}/placements")
def room_placements(
    room_id: str,
    status: str | None = Query(default=None),
    engine: VideoEngine = EngineDep,
) -> dict[str, Any]:
    """Every placement this room has had, newest first."""

    entries = engine.placements(room_id=room_id, status=status)
    return {"room_id": room_id, "count": len(entries), "entries": entries}


@router.post("/placements/{placement_id}/withdraw")
def withdraw_placement(
    placement_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: VideoEngine = EngineDep,
) -> dict[str, Any]:
    """Take the live video off the hero, keeping the buyer's playback history."""

    return engine.withdraw_placement(
        placement_id,
        source=_source("POST", "/placements/{placement_id}/withdraw"),
        actor=payload.get("actor"),
    )


# --------------------------------------------------------------------------- #
# Analytics: the event log, the roll-up, alerts and notifications
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/events")
def room_events(
    room_id: str,
    recording_id: str | None = Query(default=None),
    viewer: str | None = Query(default=None),
    engine: VideoEngine = EngineDep,
) -> dict[str, Any]:
    """Every playback event recorded in this room, newest first."""

    entries = engine.events(room_id=room_id, recording_id=recording_id, viewer=viewer)
    return {"room_id": room_id, "count": len(entries), "entries": entries}


@router.get("/rooms/{room_id}/engagement")
def room_engagement(
    room_id: str,
    recording_id: str | None = Query(default=None),
    engine: VideoEngine = EngineDep,
) -> dict[str, Any]:
    """The per-viewer roll-ups recorded in this room."""

    entries = engine.engagement(room_id=room_id, recording_id=recording_id)
    return {"room_id": room_id, "count": len(entries), "entries": entries}


@router.get("/alerts")
def list_alerts(
    room_id: str | None = Query(default=None),
    recording_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    engine: VideoEngine = EngineDep,
) -> dict[str, Any]:
    """Low-completion alerts: a viewer who started and stopped well short of the end."""

    entries = engine.alerts(room_id=room_id, recording_id=recording_id, status=status)
    return {"count": len(entries), "entries": entries}


@router.get("/rooms/{room_id}/alerts")
def room_alerts(
    room_id: str,
    status: str | None = Query(default=None),
    engine: VideoEngine = EngineDep,
) -> dict[str, Any]:
    """The low-completion alerts recorded in this room."""

    entries = engine.alerts(room_id=room_id, status=status)
    return {"room_id": room_id, "count": len(entries), "entries": entries}


@router.get("/notifications")
def list_notifications(
    room_id: str | None = Query(default=None),
    recording_id: str | None = Query(default=None),
    engine: VideoEngine = EngineDep,
) -> dict[str, Any]:
    """The buyer notifications written when a new video landed."""

    entries = engine.notifications(room_id=room_id, recording_id=recording_id)
    return {"count": len(entries), "entries": entries}


@router.get("/rooms/{room_id}/notifications")
def room_notifications(
    room_id: str,
    engine: VideoEngine = EngineDep,
) -> dict[str, Any]:
    """The buyer notifications for this room."""

    entries = engine.notifications(room_id=room_id)
    return {"room_id": room_id, "count": len(entries), "entries": entries}


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

#: Two rooms are given a video so the demo shows both researched recording kinds
#: and more than one room. A feature whose page is empty in the demo is a feature
#: nobody can review, and this one has several ways to be empty: no recording, no
#: placement, no playback events, no opened alert.
DEMO: tuple[dict[str, Any], ...] = (
    {
        "kind": v.KIND_PERSONAL_INTRO,
        "title": "A short hello from your account team",
        "source": v.SOURCE_CAMERA,
        "duration_seconds": 95,
        "note": "I put this together so you have the pricing answer before our call on Thursday.",
    },
    {
        "kind": v.KIND_WALKTHROUGH,
        "title": "Security review walkthrough",
        "source": v.SOURCE_SCREEN,
        "duration_seconds": 420,
        "note": "A guided pass over the controls your security team asked about.",
    },
)


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed two videos and the playback states the research names.

    The states are chosen so a reviewer can see the workflow doing its work with
    no interaction:

    * a personal intro on the first room's hero, watched to the end by one buyer
      (which clears an alert it opened on the way) and stopped early by a second
      (which leaves the low-completion alert open);
    * a walkthrough on the second room's hero, watched to the end, so the two
      recording kinds both appear and the analytics are not all one shape.

    Every character in the returned string is cp1252-encodable. The seeder prints
    it on a Windows console, and one arrow character in one recovered feature
    broke the whole seed.
    """

    store = RecordStore(db)
    now = context.get("now")
    engine = VideoEngine(store, now=(lambda: now) if now is not None else None)

    room_ids = context.get("room_ids") or []
    if not room_ids:
        return "no rooms to attach to"

    recordings = 0
    placements = 0
    for index, spec in enumerate(DEMO):
        if index >= len(room_ids):
            break
        room_id = room_ids[index][0]
        recording = engine.create_recording(
            {**spec, "room_id": room_id, "recorded_by": "dana"},
            source="seed",
            actor="dana",
        )
        recordings += 1
        engine.place_on_hero(
            room_id,
            recording["id"],
            {"note": spec["note"]},
            source="seed",
            actor="dana",
        )
        placements += 1

        buyers = [recipient["email"] for recipient in engine.recipients(room_id)]
        if not buyers:
            continue

        first = buyers[0]
        engine.record_playback(
            recording["id"], {"event": v.EVENT_START, "viewer": first}, source="seed", actor="buyer"
        )
        engine.record_playback(
            recording["id"],
            {"event": v.EVENT_PROGRESS, "viewer": first, "watched_percent": 30},
            source="seed",
            actor="buyer",
        )
        engine.record_playback(
            recording["id"],
            {"event": v.EVENT_COMPLETE, "viewer": first},
            source="seed",
            actor="buyer",
        )

        if index == 0 and len(buyers) > 1:
            # A second buyer stops early, so the low-completion alert stays open.
            second = buyers[1]
            engine.record_playback(
                recording["id"],
                {"event": v.EVENT_START, "viewer": second},
                source="seed",
                actor="buyer",
            )
            engine.record_playback(
                recording["id"],
                {"event": v.EVENT_PROGRESS, "viewer": second, "watched_percent": 15},
                source="seed",
                actor="buyer",
            )
            engine.record_playback(
                recording["id"],
                {"event": v.EVENT_REPLAY, "viewer": second},
                source="seed",
                actor="buyer",
            )

    events = len(engine.events(limit=500))
    rollups = len(engine.engagement(limit=500))
    open_alerts = len(engine.alerts(status=v.ALERT_OPEN, limit=500))
    notifications = len(engine.notifications(limit=500))

    return (
        f"WF-126 seeded: {recordings} recordings, {placements} hero placements, "
        f"{events} playback events, {rollups} viewer roll-ups, "
        f"{open_alerts} open low-completion alerts, {notifications} buyer notifications"
    )
