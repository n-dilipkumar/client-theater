"""The video engine: the only module in this package that reads or writes.

Every write goes through :class:`~dsr.store.RecordStore` and its
:class:`~dsr.db.audited.AuditedDatabase`, so the audit row is written in the
same transaction as the change. The engine never opens SQLite itself.

The clock is injected. Production passes the wall clock; a test passes a frozen
function, so no test depends on today's date.

What is real and what is a stand-in
-----------------------------------

This build records the *media reference* and the *playback events*. It does not
host, transcode or stream media: no object store exists in this repository. So a
recording carries an opaque ``media_ref`` and an HLS-style ``stream_ref`` that is
generated, not delivered, and its ``transcode`` block says both things plainly.
The capture itself is not performed either: a rep declares the source and the
recorder is a stand-in. See :data:`dsr.personalised_video.vocabulary.SOURCED_GAPS`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from dsr.db.audited import new_id
from dsr.personalised_video import rules, vocabulary as v
from dsr.personalised_video.errors import (
    REASON_CROSS_ROOM,
    REASON_NOT_LIVE,
    VideoConflict,
    VideoNotFound,
    VideoValidationError,
)
from dsr.store import RecordStore

Clock = Callable[[], datetime]


def _wall_clock() -> datetime:
    return datetime.now(timezone.utc)


class VideoEngine:
    """Recordings, their hero placements, and per-viewer playback."""

    def __init__(self, store: RecordStore, *, now: Clock | None = None) -> None:
        self.store = store
        self._now = now or _wall_clock

    # -- clock -------------------------------------------------------------- #

    def now_ms(self) -> int:
        return rules.parse_timestamp(self._now(), "now") or 0

    # -- vocabulary --------------------------------------------------------- #

    @staticmethod
    def vocabulary() -> dict[str, Any]:
        return v.published_vocabulary()

    # -- rooms and recipients (reusing WF-002 and WF-004) ------------------- #

    def require_room(self, room_id: str) -> dict[str, Any]:
        room = self.store.get(room_id)
        if room is None or room.get("collection") != "room":
            raise VideoNotFound(room_id, collection="room")
        return room

    def recipients(self, room_id: str) -> list[dict[str, Any]]:
        """The room's buyers, reusing WF-004's grant and invitation rows.

        A recipient is one principal email with the role WF-004 gave it. Grants
        win over invitations, and invitations over the room's ``viewers`` list,
        because the grant is the live record and the others are the offering.
        A room that declares nobody returns an empty list, which the caller reads
        as "unconfigured" rather than "everybody is refused".
        """

        self.require_room(room_id)
        room = self.store.get(room_id) or {}
        room_data = room.get("data") or {}

        found: dict[str, dict[str, Any]] = {}
        priority = {"grant": 3, "invitation": 2, "viewer": 1}

        def add(email: Any, role: Any, source: str) -> None:
            text = str(email or "").strip().lower()
            if not text:
                return
            existing = found.get(text)
            if existing is not None and priority.get(existing["source"], 0) >= priority[source]:
                # A live grant outranks the invitation it came from, which in
                # turn outranks a plain viewer entry. The first, strongest row
                # for a buyer is the one that wins.
                return
            found[text] = {"email": text, "role": str(role or v.DEFAULT_ROLE), "source": source}

        for grant in self.store.list(v.ACCESS_GRANTS, room_id=room_id, limit=500):
            data = grant.get("data") or {}
            add(data.get("principal"), data.get("role"), "grant")
        for invitation in self.store.list(v.INVITATIONS, room_id=room_id, limit=500):
            data = invitation.get("data") or {}
            add(data.get("email"), data.get("role"), "invitation")
        viewers = room_data.get(v.ROOM_VIEWERS_FIELD)
        for email in rules.normalise_emails(viewers):
            add(email, v.DEFAULT_ROLE, "viewer")
        return list(found.values())

    def hero_page(self, room_id: str) -> dict[str, Any] | None:
        """The room's hero surface: WF-002's home page, if the room has one."""

        for page in self.store.list(v.PAGE_COLLECTION, room_id=room_id, limit=500):
            if bool((page.get("data") or {}).get(v.HERO_PAGE_FLAG)):
                return page
        return None

    # -- recordings: reads -------------------------------------------------- #

    def require_recording(self, recording_id: str) -> dict[str, Any]:
        record = self.store.get(recording_id)
        if record is None or record.get("collection") != v.RECORDING_COLLECTION:
            raise VideoNotFound(recording_id, collection="recording")
        return record

    def get_recording(self, recording_id: str) -> dict[str, Any]:
        return rules.recording_view(self.require_recording(recording_id))

    def recordings(
        self,
        *,
        room_id: str | None = None,
        kind: str | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        records = self.store.list(
            v.RECORDING_COLLECTION,
            room_id=room_id,
            limit=limit,
            order_by="updated_at",
            descending=True,
        )
        views = [rules.recording_view(record) for record in records]
        if kind:
            views = [view for view in views if view["kind"] == kind]
        if status:
            views = [view for view in views if view["status"] == status]
        return views

    # -- recordings: writes ------------------------------------------------- #

    def create_recording(
        self,
        payload: Mapping[str, Any],
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Record a personal intro or a walkthrough, and transcode it on save.

        The transcoder is a stand-in: the state is set to ``ready`` immediately
        and the block says so, which is the researched "auto-transcode and
        publish on save" automation modelled deterministically rather than
        pretended.
        """

        fields = rules.validate_recording(payload)
        room_id = fields.pop("room_id")
        if room_id is not None:
            self.require_room(room_id)

        now_ms = self.now_ms()
        recording_id = new_id("wf126_rec")
        media_ref = fields["media_ref"] or f"media://wf126/{recording_id}"
        stream_ref = (
            fields["stream_ref"] or f"https://media.example.invalid/wf126/{recording_id}.m3u8"
        )

        data = {
            "title": fields["title"],
            "kind": fields["kind"],
            "source": fields["source"],
            "duration_seconds": fields["duration_seconds"],
            "note": fields["note"],
            "recorded_by": fields["recorded_by"],
            "media_ref": media_ref,
            "stream_ref": stream_ref,
            "transcode": {
                "state": v.TRANSCODE_READY,
                "at_ms": now_ms,
                "at": rules.to_iso(now_ms),
                "stand_in": True,
                "note": "No transcoder runs here. The state is recorded as ready on save.",
            },
            "status": v.RECORDING_RECORDED,
            "created_at_ms": now_ms,
            "updated_at_ms": now_ms,
            "published_at_ms": now_ms,
            "placed_at_ms": None,
        }
        record = self.store.create(
            v.RECORDING_COLLECTION,
            data,
            record_id=recording_id,
            room_id=room_id,
            actor=actor,
            source=source,
        )
        return rules.recording_view(record)

    # -- placements --------------------------------------------------------- #

    def live_placement(self, room_id: str) -> dict[str, Any] | None:
        for record in self.store.list(
            v.PLACEMENT_COLLECTION,
            room_id=room_id,
            limit=500,
            order_by="updated_at",
            descending=True,
        ):
            if (record.get("data") or {}).get("status") == v.PLACEMENT_LIVE:
                return record
        return None

    def placements(
        self,
        *,
        room_id: str | None = None,
        recording_id: str | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        records = self.store.list(
            v.PLACEMENT_COLLECTION,
            room_id=room_id,
            limit=limit,
            order_by="updated_at",
            descending=True,
        )
        views = [rules.placement_view(record) for record in records]
        if recording_id:
            views = [view for view in views if view["recording_id"] == recording_id]
        if status:
            views = [view for view in views if view["status"] == status]
        return views

    def place_on_hero(
        self,
        room_id: str,
        recording_id: str,
        payload: Mapping[str, Any],
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Place the video on the room's hero section and personalise it.

        The hero holds one video. A new placement supersedes the room's live one
        rather than deleting it, so the buyer's playback history still resolves.
        Each personalised buyer is notified that a new video landed, which is the
        researched notify-the-buyer automation.
        """

        payload = dict(payload or {})
        room = self.require_room(room_id)
        recording = self.require_recording(recording_id)

        recording_room = recording.get("room_id")
        if recording_room and recording_room != room_id:
            raise VideoConflict(
                REASON_CROSS_ROOM,
                f"recording {recording_id} belongs to room {recording_room}, not {room_id}",
            )

        requested = rules.normalise_emails(
            payload.get("personalised_for") or payload.get("viewers")
        )
        recipients = self.recipients(room_id)
        known = {recipient["email"] for recipient in recipients}
        if known:
            unknown = [email for email in requested if email not in known]
            if unknown:
                raise VideoValidationError(
                    {
                        "personalised_for": (
                            "these buyers are not recipients of this room: " + ", ".join(unknown)
                        )
                    }
                )
            personalised_for = requested or sorted(known)
        else:
            # The room declares nobody. Accept the buyer the caller named rather
            # than pretend a gate exists, the same shape WF-002 uses.
            personalised_for = requested

        surface_errors: dict[str, str] = {}
        surface = rules.coerce_choice(
            payload.get("surface"), v.SURFACES, "surface", surface_errors, default=v.SURFACE_HERO
        )
        if surface_errors:
            raise VideoValidationError(surface_errors)
        note = payload.get("note")
        note = str(note).strip() if note not in (None, "") else None

        now_ms = self.now_ms()
        hero_page = self.hero_page(room_id)
        page_id = str(payload.get("page_id") or "").strip() or (
            str(hero_page["id"]) if hero_page else None
        )

        stream_ref = (recording.get("data") or {}).get("stream_ref")
        player = {
            "url": stream_ref,
            "width": _positive_or_default(payload.get("width"), v.PLAYER_DEFAULT_WIDTH),
            "height": _positive_or_default(payload.get("height"), v.PLAYER_DEFAULT_HEIGHT),
            "autoplay": bool(payload.get("autoplay", v.PLAYER_DEFAULT_AUTOPLAY)),
        }

        # Supersede any live hero placement first, so two placements are never
        # live at once even for a moment.
        superseded: list[str] = []
        for existing in self.store.list(v.PLACEMENT_COLLECTION, room_id=room_id, limit=500):
            if (existing.get("data") or {}).get("status") == v.PLACEMENT_LIVE:
                self.store.update(
                    existing["id"], {"status": v.PLACEMENT_SUPERSEDED}, actor=actor, source=source
                )
                superseded.append(str(existing["id"]))

        placement_id = new_id("wf126_place")
        record = self.store.create(
            v.PLACEMENT_COLLECTION,
            {
                "recording_id": recording_id,
                "surface": surface,
                "page_id": page_id,
                "status": v.PLACEMENT_LIVE,
                "personalised_for": personalised_for,
                "note": note,
                "player": player,
                "placed_by": actor,
                "placed_at_ms": now_ms,
                "placed_at": rules.to_iso(now_ms),
            },
            record_id=placement_id,
            room_id=room_id,
            actor=actor,
            source=source,
        )

        self.store.update(
            recording_id,
            {
                "status": v.RECORDING_PLACED,
                "placed_at_ms": now_ms,
                "updated_at_ms": now_ms,
            },
            actor=actor,
            source=source,
        )

        notifications = self._notify_buyers(
            room_id=room_id,
            recording=recording,
            placement_id=placement_id,
            recipients=personalised_for,
            now_ms=now_ms,
            source=source,
            actor=actor,
        )

        view = rules.placement_view(record)
        return {
            **view,
            "superseded": superseded,
            "notified": [rules.notification_view(row) for row in notifications],
            "room_name": (room.get("data") or {}).get("name"),
        }

    def withdraw_placement(
        self,
        placement_id: str,
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        record = self.store.get(placement_id)
        if record is None or record.get("collection") != v.PLACEMENT_COLLECTION:
            raise VideoNotFound(placement_id, collection="placement")
        if (record.get("data") or {}).get("status") != v.PLACEMENT_LIVE:
            raise VideoConflict(
                REASON_NOT_LIVE,
                f"placement {placement_id} is not live, so it cannot be withdrawn",
            )
        updated = self.store.update(
            placement_id, {"status": v.PLACEMENT_WITHDRAWN}, actor=actor, source=source
        )
        return rules.placement_view(updated)

    def _notify_buyers(
        self,
        *,
        room_id: str,
        recording: Mapping[str, Any],
        placement_id: str,
        recipients: list[str],
        now_ms: int,
        source: str,
        actor: str | None,
    ) -> list[dict[str, Any]]:
        title = (recording.get("data") or {}).get("title") or "A new video"
        rows: list[dict[str, Any]] = []
        for recipient in recipients:
            rows.append(
                self.store.create(
                    v.NOTIFICATION_COLLECTION,
                    {
                        "recording_id": str(recording["id"]),
                        "placement_id": placement_id,
                        "recipient": recipient,
                        "channel": "email",
                        "message": f"{title} is ready for you in the room.",
                        "at_ms": now_ms,
                        "at": rules.to_iso(now_ms),
                    },
                    room_id=room_id,
                    actor=actor,
                    source=source,
                )
            )
        return rows

    # -- playback and engagement ------------------------------------------- #

    def record_playback(
        self,
        recording_id: str,
        payload: Mapping[str, Any],
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Record one per-viewer playback event and fold it into the roll-up.

        The alert is opened here rather than on a schedule: the researched
        automation is "alert the rep on low completion", and the moment the rep
        learns is the moment the event lands.
        """

        payload = dict(payload or {})
        recording = self.require_recording(recording_id)
        fields = rules.validate_playback(payload)
        room_id = recording.get("room_id")
        at_ms = rules.parse_timestamp(payload.get("at"), "at") or self.now_ms()

        event_record = self.store.create(
            v.PLAYBACK_COLLECTION,
            {
                "recording_id": recording_id,
                "placement_id": fields["placement_id"],
                "viewer": fields["viewer"],
                "event": fields["event"],
                "watched_percent": fields["watched_percent"],
                "position_seconds": fields["position_seconds"],
                "at_ms": at_ms,
                "at": rules.to_iso(at_ms),
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )

        threshold = payload.get("threshold")
        threshold = (
            int(threshold)
            if isinstance(threshold, (int, float)) and not isinstance(threshold, bool)
            else v.LOW_COMPLETION_PERCENT
        )

        engagement_record = self._roll_up(
            recording_id=recording_id,
            viewer=str(fields["viewer"]),
            event=fields,
            at_ms=at_ms,
            room_id=room_id,
            source=source,
            actor=actor,
        )
        alert = self._apply_alert(
            recording_id=recording_id,
            placement_id=fields["placement_id"],
            viewer=str(fields["viewer"]),
            engagement=engagement_record,
            at_ms=at_ms,
            threshold=threshold,
            room_id=room_id,
            source=source,
            actor=actor,
        )

        return {
            "event": rules.event_view(event_record),
            "engagement": rules.engagement_view(engagement_record),
            "alert": rules.alert_view(alert) if alert is not None else None,
        }

    def _roll_up(
        self,
        *,
        recording_id: str,
        viewer: str,
        event: Mapping[str, Any],
        at_ms: int,
        room_id: str | None,
        source: str,
        actor: str | None,
    ) -> dict[str, Any]:
        existing = self.store.find(
            v.ENGAGEMENT_COLLECTION, {"recording_id": recording_id, "viewer": viewer}, limit=1
        )
        base = (existing[0].get("data") if existing else None) or rules.blank_engagement(
            recording_id, viewer
        )
        folded = rules.apply_event(base, event, at_ms)
        if existing:
            return self.store.update(existing[0]["id"], folded, actor=actor, source=source)
        return self.store.create(
            v.ENGAGEMENT_COLLECTION, folded, room_id=room_id, actor=actor, source=source
        )

    def _apply_alert(
        self,
        *,
        recording_id: str,
        placement_id: str | None,
        viewer: str,
        engagement: Mapping[str, Any],
        at_ms: int,
        threshold: int,
        room_id: str | None,
        source: str,
        actor: str | None,
    ) -> dict[str, Any] | None:
        data = dict(engagement.get("data") or {})
        open_rows = [
            row
            for row in self.store.find(
                v.ALERT_COLLECTION, {"recording_id": recording_id, "viewer": viewer}, limit=10
            )
            if (row.get("data") or {}).get("status") == v.ALERT_OPEN
        ]

        if rules.should_alert(data, threshold):
            percent = rules.watched_percent(data)
            message = (
                f"{viewer} watched {percent:g} percent of this video, below the "
                f"{threshold} percent threshold."
            )
            payload = {
                "recording_id": recording_id,
                "placement_id": placement_id,
                "viewer": viewer,
                "status": v.ALERT_OPEN,
                "reason": v.REASON_LOW_COMPLETION,
                "max_percent": percent,
                "threshold": threshold,
                "opened_at_ms": at_ms,
                "opened_at": rules.to_iso(at_ms),
                "cleared_at_ms": None,
                "cleared_at": None,
                "message": message,
            }
            if open_rows:
                return self.store.update(open_rows[0]["id"], payload, actor=actor, source=source)
            return self.store.create(
                v.ALERT_COLLECTION, payload, room_id=room_id, actor=actor, source=source
            )

        if data.get("completed") and open_rows:
            return self.store.update(
                open_rows[0]["id"],
                {
                    "status": v.ALERT_CLEARED,
                    "reason": v.REASON_COMPLETED_LATE,
                    "cleared_at_ms": at_ms,
                    "cleared_at": rules.to_iso(at_ms),
                    "max_percent": rules.watched_percent(data),
                },
                actor=actor,
                source=source,
            )
        return None

    # -- reads over the event log ------------------------------------------ #

    def events(
        self,
        *,
        room_id: str | None = None,
        recording_id: str | None = None,
        viewer: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        records = self.store.list(
            v.PLAYBACK_COLLECTION,
            room_id=room_id,
            limit=limit,
            order_by="created_at",
            descending=True,
        )
        views = [rules.event_view(record) for record in records]
        if recording_id:
            views = [view for view in views if view["recording_id"] == recording_id]
        if viewer:
            needle = viewer.strip().lower()
            views = [view for view in views if str(view["viewer"]).lower() == needle]
        return views

    def engagement(
        self,
        *,
        room_id: str | None = None,
        recording_id: str | None = None,
        viewer: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        records = self.store.list(v.ENGAGEMENT_COLLECTION, room_id=room_id, limit=limit)
        views = [rules.engagement_view(record) for record in records]
        if recording_id:
            views = [view for view in views if view["recording_id"] == recording_id]
        if viewer:
            needle = viewer.strip().lower()
            views = [view for view in views if str(view["viewer"]).lower() == needle]
        views.sort(key=lambda view: (str(view["recording_id"]), str(view["viewer"])))
        return views

    def alerts(
        self,
        *,
        room_id: str | None = None,
        recording_id: str | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        records = self.store.list(
            v.ALERT_COLLECTION, room_id=room_id, limit=limit, order_by="updated_at", descending=True
        )
        views = [rules.alert_view(record) for record in records]
        if recording_id:
            views = [view for view in views if view["recording_id"] == recording_id]
        if status:
            views = [view for view in views if view["status"] == status]
        return views

    def notifications(
        self,
        *,
        room_id: str | None = None,
        recording_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        records = self.store.list(
            v.NOTIFICATION_COLLECTION,
            room_id=room_id,
            limit=limit,
            order_by="created_at",
            descending=True,
        )
        views = [rules.notification_view(record) for record in records]
        if recording_id:
            views = [view for view in views if view["recording_id"] == recording_id]
        return views

    # -- the buyer surface -------------------------------------------------- #

    def hero(self, room_id: str) -> dict[str, Any]:
        """The video currently on the room's hero, as a buyer reads it."""

        self.require_room(room_id)
        placement = self.live_placement(room_id)
        if placement is None:
            raise VideoNotFound(f"hero in {room_id}", collection="placement")
        recording_id = (placement.get("data") or {}).get("recording_id")
        recording = self.require_recording(str(recording_id))
        return self._hero_payload(room_id, placement, recording)

    def hero_for(self, room_id: str, viewer: str) -> dict[str, Any]:
        """The hero personalised for one buyer, with that buyer's playback state."""

        payload = self.hero(room_id)
        needle = (viewer or "").strip().lower()
        personalised_for = [email.lower() for email in payload["placement"]["personalised_for"]]
        personalised = not personalised_for or needle in personalised_for
        engagement = [
            rules.engagement_view(record)
            for record in self.store.find(
                v.ENGAGEMENT_COLLECTION,
                {"recording_id": payload["recording"]["id"], "viewer": needle},
                limit=1,
            )
        ]
        return {
            **payload,
            "viewer": needle,
            "personalised": personalised,
            "note": payload["placement"]["note"],
            "engagement": engagement[0] if engagement else None,
        }

    def _hero_payload(
        self,
        room_id: str,
        placement: Mapping[str, Any],
        recording: Mapping[str, Any],
    ) -> dict[str, Any]:
        placement_view = rules.placement_view(placement)
        recording_view = rules.recording_view(recording)
        return {
            "room_id": room_id,
            "placement": placement_view,
            "recording": recording_view,
            "surface": placement_view["surface"],
            "message": rules.personalisation_message(recording, placement),
            "hosted": False,
            "hosting_note": "The player points at a stand-in URL. No media is hosted in this build.",
        }

    # -- the seller's summary ---------------------------------------------- #

    def summary(self, room_id: str | None = None) -> dict[str, Any]:
        recordings = self.recordings(room_id=room_id, limit=500)
        placements = self.placements(room_id=room_id, limit=500)
        engagement = self.engagement(room_id=room_id, limit=500)
        alerts = self.alerts(room_id=room_id, limit=500)
        notifications = self.notifications(room_id=room_id, limit=500)

        viewers = {row["viewer"] for row in engagement}
        started = [row for row in engagement if row["state"] != v.STATE_NOT_STARTED]
        completed = [row for row in engagement if row["completed"]]
        percent = [float(row["max_percent"] or 0) for row in engagement]
        return {
            "recordings": len(recordings),
            "placed": len([row for row in recordings if row["status"] == v.RECORDING_PLACED]),
            "live_placements": len(
                [row for row in placements if row["status"] == v.PLACEMENT_LIVE]
            ),
            "viewers": len(viewers),
            "started": len(started),
            "completed": len(completed),
            "replays": sum(int(row["replays"]) for row in engagement),
            "low_completion_alerts": len([row for row in alerts if row["status"] == v.ALERT_OPEN]),
            "notifications": len(notifications),
            "average_watched_percent": round(sum(percent) / len(percent), 1) if percent else 0.0,
            "generated_at": rules.to_iso(self.now_ms()),
        }


def _positive_or_default(value: Any, default: int) -> int:
    if isinstance(value, bool) or value in (None, ""):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


__all__ = ["VideoEngine"]
