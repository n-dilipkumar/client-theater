"""The store-facing half of WF-127: build the demo, record the event stream,
discover the stakeholders, and serve the roll-up.

:class:`DemoAnalyticsBook` holds a :class:`~dsr.store.RecordStore` and nothing
else that talks to persistence. Every write goes through the store, so every
write is audited in the same transaction as the change. This module never opens
the database and never bypasses the audit log.

Three decisions a reviewer should be able to check without reading the whole
file:

**The rules are pure.** The click-path tree, the heatmap, the six-level roll-up,
the DQL score and the discovery attribution live in :mod:`.rules`, which takes
plain dicts. This module is the join between those rules and the store.

**A stakeholder row is identity, not aggregate.** The row holds who the viewer
is, the role they earned, who attributed them and when they were first and last
seen. Everything engagement-shaped - views, sections, downloads, DQL - is
computed from the event stream at read time, so no stored counter can drift from
the events it summarises.

**``source`` is required on every write.** A hardcoded source string drifts from
the route that serves the request, and an audit row naming a path the app no
longer serves is worse than no audit row.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from dsr.store import RecordStore

from . import inferences as inference_register, rules, vocabulary as vocab
from .errors import DemoArchived, DemoNotFound, DemoValidationError
from .vocabulary import LEVELS

#: A ceiling on rows read for one answer. A demo watched by a thousand buyers is
#: a large collection, and one request must not scan it without bound.
MAX_ROWS = 50_000


class DemoAnalyticsBook:
    """Interactive demos, their event stream, and the stakeholders it discovers."""

    def __init__(
        self,
        store: RecordStore,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self._now = now or rules.now_utc

    # -- helpers ------------------------------------------------------------ #

    def _stamp(self) -> str:
        return self._now().isoformat(timespec="milliseconds")

    @staticmethod
    def _data(record: Mapping[str, Any]) -> dict[str, Any]:
        return dict(record.get("data") or {})

    def require_room(self, room_id: Any) -> dict[str, Any]:
        record = None
        if isinstance(room_id, str) and room_id:
            record = self.store.get(room_id)
        if record is None or record.get("collection") != "room":
            raise DemoNotFound(f"room {room_id!r} not found", room_id=str(room_id or ""))
        return record

    def _demo_record(self, demo_id: Any) -> dict[str, Any]:
        record = None
        if isinstance(demo_id, str) and demo_id:
            record = self.store.get(demo_id)
        if record is None or record.get("collection") != vocab.DEMO_COLLECTION:
            raise DemoNotFound(f"demo {demo_id!r} not found", demo_id=str(demo_id or ""))
        return record

    def _demo_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = self._data(record)
        nodes = list(data.get("nodes") or [])
        return {
            "id": record.get("id"),
            "room_id": data.get(vocab.ROOM_REF) or record.get("room_id"),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            "title": data.get("title"),
            "demo_type": data.get("demo_type"),
            "state": data.get("state"),
            "recording_ref": data.get("recording_ref"),
            "team": data.get("team"),
            "owner": data.get("owner"),
            "entry": data.get("entry"),
            "nodes": nodes,
            "node_count": len(nodes),
            "branch_count": sum(len(node.get("next") or []) for node in nodes),
            "sections": list(data.get("sections") or []),
            "published_at": data.get("published_at"),
            "archived_at": data.get("archived_at"),
            "archived": data.get("state") == "archived",
        }

    def _events(
        self, demo_id: str | None = None, room_id: str | None = None
    ) -> list[dict[str, Any]]:
        where: dict[str, Any] = {}
        if demo_id:
            where["demo_id"] = demo_id
        elif room_id:
            where[vocab.ROOM_REF] = room_id
        rows = self.store.find(vocab.EVENT_COLLECTION, where, limit=MAX_ROWS)
        return [self._data(row) for row in rows]

    def _stakeholder_rows(
        self, demo_id: str | None = None, room_id: str | None = None
    ) -> list[dict[str, Any]]:
        where: dict[str, Any] = {}
        if demo_id:
            where["demo_id"] = demo_id
        elif room_id:
            where[vocab.ROOM_REF] = room_id
        return self.store.find(vocab.STAKEHOLDER_COLLECTION, where, limit=MAX_ROWS)

    def _shares(
        self, demo_id: str | None = None, room_id: str | None = None
    ) -> list[dict[str, Any]]:
        where: dict[str, Any] = {}
        if demo_id:
            where["demo_id"] = demo_id
        elif room_id:
            where[vocab.ROOM_REF] = room_id
        rows = self.store.find(vocab.SHARE_COLLECTION, where, limit=MAX_ROWS)
        return [self._data(row) for row in rows]

    # -- the demo ----------------------------------------------------------- #

    def create_demo(
        self,
        room_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Build one interactive demo for one room.

        The researched flow is a recording converted into a branching demo. A
        payload with no click path is built from the default branching template;
        a payload with a tree is validated node by node.
        """
        self.require_room(room_id)
        data = rules.normalise_demo(payload or {}, now=self._now())
        data[vocab.ROOM_REF] = str(room_id)
        record = self.store.create(
            vocab.DEMO_COLLECTION, data, room_id=str(room_id), actor=actor, source=source
        )
        return self._demo_view(record)

    def update_demo(
        self,
        demo_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Replace the mutable demo fields, then re-validate the whole demo."""
        record = self._demo_record(demo_id)
        if record.get("data", {}).get("state") == "archived":
            raise DemoArchived("an archived demo cannot be edited", demo_id=demo_id)
        merged = {**self._data(record), **dict(payload or {})}
        normalised = rules.normalise_demo(merged, now=self._now())
        normalised[vocab.ROOM_REF] = self._data(record).get(vocab.ROOM_REF) or record.get("room_id")
        updated = self.store.update(record["id"], normalised, actor=actor, source=source)
        return self._demo_view(updated)

    def publish_demo(
        self, demo_id: str, *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Publish a demo so it can be shared and watched."""
        record = self._demo_record(demo_id)
        if record.get("data", {}).get("state") == "archived":
            raise DemoArchived("an archived demo cannot be republished", demo_id=demo_id)
        patch = {"state": "published", "published_at": self._stamp()}
        updated = self.store.update(record["id"], patch, actor=actor, source=source)
        return self._demo_view(updated)

    def archive_demo(
        self, demo_id: str, *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Withdraw a demo. It keeps its recorded activity and accepts no more."""
        record = self._demo_record(demo_id)
        patch = {"state": "archived", "archived_at": self._stamp()}
        updated = self.store.update(record["id"], patch, actor=actor, source=source)
        return self._demo_view(updated)

    def read_demo(self, demo_id: str) -> dict[str, Any]:
        return self._demo_view(self._demo_record(demo_id))

    def list_demos(self, room_id: str | None = None) -> list[dict[str, Any]]:
        if room_id:
            rows = self.store.find(vocab.DEMO_COLLECTION, {vocab.ROOM_REF: room_id}, limit=MAX_ROWS)
        else:
            rows = self.store.list(vocab.DEMO_COLLECTION, limit=MAX_ROWS)
        views = [self._demo_view(row) for row in rows]
        views.sort(
            key=lambda demo: (str(demo.get("created_at") or ""), str(demo["id"])), reverse=True
        )
        return views

    # -- distribution ------------------------------------------------------- #

    def _ensure_stakeholder(
        self,
        demo_id: str,
        viewer: str,
        *,
        role: str,
        invited_by: str | None,
        room_ref: str | None,
        stamp: str,
        actor: str | None,
        source: str,
    ) -> tuple[dict[str, Any], bool]:
        """The stakeholder row for a viewer, created once and then updated."""
        existing = self.store.find(
            vocab.STAKEHOLDER_COLLECTION, {"demo_id": demo_id, "viewer": viewer}, limit=1
        )
        if existing:
            updated = self.store.update(
                existing[0]["id"], {"last_seen_at": stamp}, actor=actor, source=source
            )
            return updated, False
        payload = {
            "demo_id": demo_id,
            "viewer": viewer,
            "role": role,
            "discovered_via": "intra_viral" if role == "discovered" else None,
            "invited_by": invited_by,
            "first_seen_at": stamp,
            "last_seen_at": stamp,
        }
        if room_ref:
            payload[vocab.ROOM_REF] = room_ref
        created = self.store.create(
            vocab.STAKEHOLDER_COLLECTION, payload, room_id=room_ref, actor=actor, source=source
        )
        return created, True

    def record_share(
        self,
        demo_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Record who a demo was sent to, and who a buyer forwarded it to.

        An initial send mints each recipient as an ``initial_recipient``. A
        forward re-share mints each recipient as *discovered*, attributed to the
        buyer who sent it - which is the researched intra-viral discovery made a
        stored fact at share time as well as inferred at view time.
        """
        record = self._demo_record(demo_id)
        if record.get("data", {}).get("state") == "archived":
            raise DemoArchived("an archived demo cannot be shared", demo_id=demo_id)
        share = rules.normalise_share(payload or {})
        room_ref = self._data(record).get(vocab.ROOM_REF) or record.get("room_id")
        stamp = self._stamp()
        known = {str(row["data"].get("viewer")) for row in self._stakeholder_rows(demo_id)}

        rows = [
            {
                "demo_id": record["id"],
                "kind": share["kind"],
                "sender": share["sender"],
                "recipient": recipient,
                "channel": share["channel"],
                "shared_at": stamp,
                **({vocab.ROOM_REF: room_ref} if room_ref else {}),
            }
            for recipient in share["recipients"]
        ]
        created = self.store.bulk_create(
            vocab.SHARE_COLLECTION, rows, room_id=room_ref, actor=actor, source=source
        )

        discovered = 0
        for recipient in share["recipients"]:
            if share["kind"] == "initial":
                role = "initial_recipient"
            elif share["sender"] and share["sender"] in known:
                role = "discovered"
            else:
                role = "direct"
            _, was_created = self._ensure_stakeholder(
                record["id"],
                recipient,
                role=role,
                invited_by=share["sender"],
                room_ref=room_ref,
                stamp=stamp,
                actor=actor,
                source=source,
            )
            if was_created and role == "discovered":
                discovered += 1
        return {
            "demo_id": record["id"],
            "kind": share["kind"],
            "count": len(created),
            "recipients": list(share["recipients"]),
            "sender": share["sender"],
            "stakeholders_discovered": discovered,
        }

    # -- the event stream --------------------------------------------------- #

    def record_event(
        self,
        demo_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Record one viewer event, and discover the stakeholder behind it.

        The order is the researched data flow: the per-viewer event is written,
        the stakeholder is created or touched, and the DQL score is recomputed
        from the viewer's own events. The event is written first, so a viewer
        who cannot be attributed still leaves the activity recorded.
        """
        record = self._demo_record(demo_id)
        if record.get("data", {}).get("state") == "archived":
            raise DemoArchived("an archived demo accepts no new events", demo_id=demo_id)
        event = rules.normalise_event(payload or {}, now=self._now())
        demo = self._demo_view(record)
        if event["event"] == "section_selected" and event["section"] not in (
            demo.get("sections") or []
        ):
            raise DemoValidationError(
                f"{event['section']!r} is not a section of this demo",
                field="section",
                demo_id=record["id"],
            )
        room_ref = self._data(record).get(vocab.ROOM_REF) or record.get("room_id")
        stamp = self._stamp()
        known = {str(row["data"].get("viewer")) for row in self._stakeholder_rows(demo_id)}
        role = rules.stakeholder_role(
            event["viewer"], known=known, shared_from=event["shared_from"]
        )

        row = {
            "demo_id": record["id"],
            "viewer": event["viewer"],
            "event": event["event"],
            "section": event["section"],
            "shared_from": event["shared_from"],
            "is_internal": event["is_internal"],
            "occurred_at": event["occurred_at"],
            **({vocab.ROOM_REF: room_ref} if room_ref else {}),
        }
        self.store.create(vocab.EVENT_COLLECTION, row, room_id=room_ref, actor=actor, source=source)

        stakeholder, created = self._ensure_stakeholder(
            record["id"],
            event["viewer"],
            role=role if role != "known" else "direct",
            invited_by=event["shared_from"],
            room_ref=room_ref,
            stamp=stamp,
            actor=actor,
            source=source,
        )
        discovered = bool(created and role == "discovered")
        viewer_events = [
            item for item in self._events(record["id"]) if item.get("viewer") == event["viewer"]
        ]
        return {
            "demo_id": record["id"],
            "room_id": room_ref,
            "viewer": event["viewer"],
            "event": event["event"],
            "section": event["section"],
            "shared_from": event["shared_from"],
            "is_internal": event["is_internal"],
            "occurred_at": event["occurred_at"],
            "stakeholder": {
                "viewer": stakeholder["data"].get("viewer"),
                "role": stakeholder["data"].get("role"),
                "invited_by": stakeholder["data"].get("invited_by"),
            },
            "discovered": discovered,
            "dql": rules.dql_score(viewer_events),
        }

    # -- reads -------------------------------------------------------------- #

    def _buyer_stakeholders(
        self, demo_id: str, events: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """The stakeholder rows that are buyers, internal previews excluded."""
        hidden = rules.internal_only_viewers(events)
        return [
            self._data(row)
            for row in self._stakeholder_rows(demo_id)
            if str(row["data"].get("viewer")) not in hidden
        ]

    def stakeholder_views(self, demo_id: str) -> list[dict[str, Any]]:
        """One row per buyer, with the engagement the event stream implies."""
        self._demo_record(demo_id)
        events = self._events(demo_id)
        hidden = rules.internal_only_viewers(events)
        rows: list[dict[str, Any]] = []
        for record in self._stakeholder_rows(demo_id):
            data = self._data(record)
            viewer = str(data.get("viewer"))
            if viewer in hidden:
                continue
            mine = [event for event in events if str(event.get("viewer")) == viewer]
            sections = sorted(
                {
                    str(event.get("section"))
                    for event in mine
                    if event.get("event") == "section_selected" and not event.get("is_internal")
                }
            )
            rows.append(
                {
                    "viewer": viewer,
                    "role": data.get("role"),
                    "discovered_via": data.get("discovered_via"),
                    "invited_by": data.get("invited_by"),
                    "first_seen_at": data.get("first_seen_at"),
                    "last_seen_at": data.get("last_seen_at"),
                    "views": sum(
                        1
                        for event in mine
                        if event.get("event") == "view" and not event.get("is_internal")
                    ),
                    "sections": sections,
                    "downloads": sum(
                        1
                        for event in mine
                        if event.get("event") == "doc_downloaded" and not event.get("is_internal")
                    ),
                    "dql": rules.dql_score(mine),
                }
            )
        rows.sort(key=lambda row: (-row["dql"]["score"], row["viewer"]))
        return rows

    def analytics(self, demo_id: str) -> dict[str, Any]:
        """One demo's whole analytics block: metrics, heatmap, sections, people."""
        demo = self.read_demo(demo_id)
        events = self._events(demo_id)
        buyers = self._buyer_stakeholders(demo_id, events)
        shares = self._shares(demo_id)
        return {
            "demo": demo,
            "metrics": rules.metrics(events, buyers, shares),
            "heatmap": rules.heatmap(events),
            "sections": rules.section_counts(demo, events),
            "reach": rules.reach_counts(demo, events),
            "stakeholders": self.stakeholder_views(demo_id),
            "levels": list(LEVELS),
        }

    def heatmap(self, demo_id: str) -> dict[str, Any]:
        self._demo_record(demo_id)
        return rules.heatmap(self._events(demo_id))

    def sections(self, demo_id: str) -> dict[str, Any]:
        return rules.section_counts(self.read_demo(demo_id), self._events(demo_id))

    def rollup(
        self, level: str, demo_id: str | None = None, room_id: str | None = None
    ) -> dict[str, Any]:
        """One of the six drill-down levels over one demo, or over a room."""
        if demo_id:
            demos = [self.read_demo(demo_id)]
        else:
            demos = self.list_demos(room_id)
        events = self._events(demo_id, room_id)
        hidden = rules.internal_only_viewers(events)
        stakeholders = [
            row
            for row in self._stakeholder_rows_plain(demo_id, room_id)
            if str(row.get("viewer")) not in hidden
        ]
        return rules.rollup(level, demos, events, stakeholders, self._shares(demo_id, room_id))

    def _stakeholder_rows_plain(
        self, demo_id: str | None, room_id: str | None
    ) -> list[dict[str, Any]]:
        return [self._data(row) for row in self._stakeholder_rows(demo_id, room_id)]

    def list_stakeholders(
        self, demo_id: str | None = None, room_id: str | None = None
    ) -> list[dict[str, Any]]:
        if demo_id:
            return self.stakeholder_views(demo_id)
        events = self._events(None, room_id)
        hidden = rules.internal_only_viewers(events)
        rows = [
            row
            for row in self._stakeholder_rows_plain(None, room_id)
            if str(row.get("viewer")) not in hidden
        ]
        rows.sort(
            key=lambda row: (str(row.get("first_seen_at") or ""), str(row.get("viewer"))),
            reverse=True,
        )
        return [
            {
                "demo_id": row.get("demo_id"),
                "viewer": row.get("viewer"),
                "role": row.get("role"),
                "discovered_via": row.get("discovered_via"),
                "invited_by": row.get("invited_by"),
                "first_seen_at": row.get("first_seen_at"),
                "last_seen_at": row.get("last_seen_at"),
            }
            for row in rows
        ]

    def summary(self, room_id: str | None = None) -> dict[str, Any]:
        demos = self.list_demos(room_id)
        events = self._events(None, room_id)
        hidden = rules.internal_only_viewers(events)
        buyers = [
            row
            for row in self._stakeholder_rows_plain(None, room_id)
            if str(row.get("viewer")) not in hidden
        ]
        shares = self._shares(None, room_id)
        block = rules.metrics(events, buyers, shares)
        return {
            "room_id": room_id,
            "demos": len(demos),
            "published": sum(1 for demo in demos if demo["state"] == "published"),
            "draft": sum(1 for demo in demos if demo["state"] == "draft"),
            "archived": sum(1 for demo in demos if demo["state"] == "archived"),
            "metrics": block,
            "demos_sent": block["demos_sent"],
            "views": block["views"],
            "stakeholders": block["stakeholders"],
            "stakeholders_discovered": block["stakeholders_discovered"],
            "discovery_rate": block["discovery_rate"],
            "internal_previews": block["internal_previews"],
            "generated_at": self._stamp(),
        }

    # -- the published contract --------------------------------------------- #

    @staticmethod
    def vocabulary() -> dict[str, Any]:
        return vocab.describe()

    @staticmethod
    def inferences() -> dict[str, Any]:
        return inference_register.describe()


__all__ = ["MAX_ROWS", "DemoAnalyticsBook"]
