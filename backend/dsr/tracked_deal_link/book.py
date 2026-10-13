"""The store-facing half of WF-121: write the links, record the views, serve the
dwell, and hand the engagement record to the CRM write-back.

:class:`TrackedLinkBook` holds a :class:`~dsr.store.RecordStore` and nothing
else that talks to persistence. Every write goes through the store, so every
write is audited in the same transaction as the change. This module never opens
the database and never bypasses the audit log.

Four decisions a reviewer should be able to check without reading the whole
file:

**The per-page rules are WF-018's.** ``group_revisits``, ``page_dwell`` and
``drop_off_per_page`` live in :mod:`.rules` and call
:mod:`dsr.pdf_analytics.metrics` directly. This module reads timing rows it
wrote in WF-018's own field names (``page``, ``seconds``, ``occurredAt``,
``viewer``, ``sessionId``), so the shared functions take them unchanged.

**The asset is WF-018's.** A link references one content asset and copies its
name and page count onto the link at creation, so a link keeps a readable
summary even if the library row changes. No document bytes are stored: the
library row is the document.

**The CRM write-back is WF-037's.** ``record_view`` builds the canonical
engagement record in :mod:`.rules` and hands it to
:class:`~dsr.crm_engagement.EngagementSync.record_event`, which writes the room's
own row first and enqueues the vendor create. ``fire_queue`` defaults to false,
so recording a view never opens a socket; a deployment drains the queue on the
route that asks for it.

**``source`` is required on every write.** A hardcoded source string drifts from
the route that serves the request, and an audit row naming a path the app no
longer serves is worse than no audit row.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any

from dsr.crm_engagement import EngagementSync
from dsr.pdf_analytics import AnalyticsBook, AnalyticsError
from dsr.store import RecordStore

from . import inferences as inference_register, rules, vocabulary as vocab
from .errors import (
    DistributionRefused,
    DwellRefused,
    LinkNotFound,
    LinkValidationError,
    ViewerRequired,
)

#: A ceiling on rows read for one answer. A deck read by two hundred buyers for
#: forty pages is eight thousand rows, and one request must not scan the whole
#: collection because a document was pathological.
MAX_ROWS = 50_000


class TrackedLinkBook:
    """Tracked links, the page views they record, and the engagement they raise."""

    def __init__(
        self,
        store: RecordStore,
        *,
        now: Callable[[], datetime] | None = None,
        engagement: Callable[[], EngagementSync] | None = None,
    ) -> None:
        self.store = store
        self.library = AnalyticsBook(store)
        self._now = now or rules.now_utc
        self.engagement_factory: Callable[[], EngagementSync] = engagement or (
            lambda: EngagementSync(self.store)
        )

    # -- helpers ------------------------------------------------------------ #

    def _stamp(self) -> str:
        return self._now().isoformat(timespec="milliseconds")

    @staticmethod
    def _data(record: Mapping[str, Any]) -> dict[str, Any]:
        return dict(record.get("data") or {})

    def require_room(self, room_id: Any) -> dict[str, Any]:
        try:
            return self.library.require_room(str(room_id))
        except AnalyticsError as exc:
            raise LinkNotFound(f"room {room_id!r} not found", room_id=str(room_id or "")) from exc

    def _link(self, link_id: Any, *, include_deleted: bool = False) -> dict[str, Any]:
        record = None
        if isinstance(link_id, str) and link_id:
            try:
                record = self.store.db.get(link_id, include_deleted=include_deleted)
            except Exception:  # the store's own miss, or a malformed id
                record = None
        if record is None or record.get("collection") != vocab.LINK_COLLECTION:
            raise LinkNotFound(f"tracked link {link_id!r} not found", link_id=str(link_id or ""))
        return record

    def resolve_link(self, reference: Any) -> dict[str, Any]:
        """Resolve a link by record id, then by the share URL a buyer holds."""
        key = str(reference or "").strip()
        if not key:
            raise LinkNotFound("a link reference is required", link_id="")
        record = self.store.get(key)
        if record is not None and record.get("collection") == vocab.LINK_COLLECTION:
            return record
        matches = self.store.find(vocab.LINK_COLLECTION, {"share_url": key}, limit=2)
        if matches:
            return matches[0]
        raise LinkNotFound(f"tracked link {key!r} not found", link_id=key)

    def _asset_summary(self, asset_ref: str) -> dict[str, Any]:
        """The WF-018 asset a link distributes, reduced to what a link needs."""
        try:
            asset = self.library.require_asset(asset_ref)
        except AnalyticsError as exc:
            raise LinkValidationError(
                f"asset {asset_ref!r} is not in the content library",
                field="asset_id",
                asset_id=asset_ref,
            ) from exc
        data = self._data(asset)
        return {
            "asset_name": data.get("name"),
            "asset_type": data.get("type"),
            "page_count": data.get("pageCount"),
            "asset_external_id": data.get("externalId"),
        }

    def _link_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = self._data(record)
        state = rules.link_state(record, now=self._now())
        return {
            "id": record.get("id"),
            "room_id": data.get(vocab.ROOM_REF) or record.get("room_id"),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            "title": data.get("title"),
            "asset_id": data.get("asset_id"),
            "asset_name": data.get("asset_name"),
            "asset_type": data.get("asset_type"),
            "asset_external_id": data.get("asset_external_id"),
            "page_count": data.get("page_count"),
            "recipients": list(data.get("recipients") or []),
            "expires_at": state["expires_at"],
            "expires_in_seconds": state["expires_in_seconds"],
            "lifetime_days": data.get("lifetime_days"),
            "alert_on_page": data.get("alert_on_page"),
            "notify_on_return": bool(data.get("notify_on_return", True)),
            "tracking_enabled": bool(data.get("tracking_enabled", True)),
            "require_viewer": bool(data.get("require_viewer", True)),
            "crm_system": data.get("crm_system"),
            "crm_deal_id": data.get("crm_deal_id"),
            "share_url": data.get("share_url"),
            "state": state["state"],
            "revoked": state["revoked"],
            "expired": state["expired"],
            "expiry_reason": state["expiry_reason"],
            "closed": state["closed"],
        }

    # -- the link ----------------------------------------------------------- #

    def create_link(
        self,
        room_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Create one tracked link for one room.

        The link references a WF-018 content asset and copies its name and page
        count, so the link carries the readable summary a rep needs without
        reading the library on every request.
        """
        self.require_room(room_id)
        data = rules.normalise_link(payload or {}, now=self._now())
        data.update(self._asset_summary(data["asset_id"]))
        data[vocab.ROOM_REF] = str(room_id)
        if not data.get("share_url"):
            data["share_url"] = None
        record = self.store.create(
            vocab.LINK_COLLECTION, data, room_id=str(room_id), actor=actor, source=source
        )
        return self._link_view(record)

    def attach_share_url(
        self, link_id: str, share_url: str, *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Name the one link a rep shares, once the store has minted its id.

        The URL is the route's business - it is built from the mounted prefix, so
        a link can never be handed out pointing at a path the host stopped
        serving - and it is written back here rather than guessed in the domain.
        """
        record = self._link(link_id)
        updated = self.store.update(
            record["id"], {"share_url": str(share_url)}, actor=actor, source=source
        )
        return self._link_view(updated)

    def update_link(
        self,
        link_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Replace the mutable link fields, then re-validate the whole link.

        A shallow merge is not enough here because ``expires_at`` is derived from
        ``lifetime_days`` when it is absent: merging a new lifetime onto a stored
        expiry would leave both, and the expiry wins. So the patch is applied to
        the stored payload and the result re-normalised as one link.
        """
        record = self._link(link_id, include_deleted=True)
        if record.get("deleted_at") is not None:
            raise LinkValidationError("a revoked link cannot be edited", link_id=link_id)
        current = self._data(record)
        merged = {**current, **dict(payload or {})}
        normalised = rules.normalise_link(merged, now=self._now())
        normalised.update(self._asset_summary(normalised["asset_id"]))
        normalised[vocab.ROOM_REF] = current.get(vocab.ROOM_REF) or record.get("room_id")
        updated = self.store.update(record["id"], normalised, actor=actor, source=source)
        return self._link_view(updated)

    def revoke_link(self, link_id: str, *, actor: str | None = None, source: str) -> dict[str, Any]:
        """Revoke a link. A soft delete, so the row and its history survive."""
        record = self._link(link_id)
        deleted = self.store.delete(record["id"], actor=actor, source=source)
        return {
            **self._link_view(deleted if "data" in deleted else record),
            "revoked": True,
            "state": "revoked",
            "closed": True,
            "revoked_at": deleted.get("updated_at") or self._stamp(),
        }

    def list_links(self, room_id: str | None = None) -> list[dict[str, Any]]:
        if room_id:
            rows = self.store.find(vocab.LINK_COLLECTION, {vocab.ROOM_REF: room_id}, limit=MAX_ROWS)
        else:
            rows = self.store.list(vocab.LINK_COLLECTION, limit=MAX_ROWS)
        views = [self._link_view(row) for row in rows]
        views.sort(
            key=lambda link: (str(link.get("created_at") or ""), str(link["id"])), reverse=True
        )
        return views

    def list_revoked_links(self, room_id: str | None = None) -> list[dict[str, Any]]:
        rows = self.store.list(vocab.LINK_COLLECTION, limit=MAX_ROWS, include_deleted=True)
        revoked = [row for row in rows if row.get("deleted_at") is not None]
        views = [self._link_view(row) for row in revoked]
        if room_id:
            views = [link for link in views if link["room_id"] == room_id]
        return views

    def read_link(self, link_id: str) -> dict[str, Any]:
        return self._link_view(self._link(link_id, include_deleted=True))

    # -- distribution ------------------------------------------------------- #

    def distribute(
        self,
        link_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Record which recipients the one link was shared with.

        The researched flow is "rep shares a single link" and "a buyer (often
        several people behind one link)". One row per recipient keeps the link
        count at one and still answers "who was it sent to", which the delivery
        row - not the link - is the right home for.
        """
        record = self._link(link_id, include_deleted=True)
        rules.require_open(record, now=self._now())
        body = dict(payload or {})
        data = self._data(record)

        raw = body.get("recipients")
        if raw is None:
            raw = data.get("recipients") or []
        if isinstance(raw, str):
            raw = [part.strip() for part in raw.split(",") if part.strip()]
        if not isinstance(raw, (list, tuple)) or not raw:
            raise DistributionRefused(
                "a distribution needs at least one recipient", link_id=link_id
            )

        share_url = str(body.get("share_url") or data.get("share_url") or "").strip() or None
        if share_url and not data.get("share_url"):
            record = self.store.update(
                record["id"], {"share_url": share_url}, actor=actor, source=source
            )
            data = self._data(record)

        room_ref = data.get(vocab.ROOM_REF) or record.get("room_id")
        stamp = self._stamp()
        deliveries: list[dict[str, Any]] = []
        seen: set[str] = set()
        for entry in raw:
            try:
                recipient = vocab.require_viewer(entry)
            except Exception as exc:
                raise DistributionRefused(
                    f"{entry!r} is not an email address", link_id=link_id
                ) from exc
            if recipient in seen:
                continue
            seen.add(recipient)
            deliveries.append(
                {
                    "link_id": record["id"],
                    vocab.ROOM_REF: room_ref,
                    "recipient": recipient,
                    "channel": str(body.get("channel") or "email"),
                    "share_url": share_url,
                    "delivered_at": stamp,
                }
            )

        created = self.store.bulk_create(
            vocab.DELIVERY_COLLECTION, deliveries, room_id=room_ref, actor=actor, source=source
        )
        return {
            "link_id": record["id"],
            "share_url": share_url,
            "count": len(created),
            "recipients": [row["data"]["recipient"] for row in created],
            "deliveries": [{"id": row["id"], **row["data"]} for row in created],
        }

    def list_deliveries(self, link_id: str) -> list[dict[str, Any]]:
        rows = self.store.find(vocab.DELIVERY_COLLECTION, {"link_id": link_id}, limit=MAX_ROWS)
        return [{"id": row["id"], **self._data(row)} for row in rows]

    # -- recording a view --------------------------------------------------- #

    def _rows_for(self, link_id: str, viewer: str | None = None) -> list[dict[str, Any]]:
        rows = self.store.find(vocab.VIEW_COLLECTION, {"linkId": link_id}, limit=MAX_ROWS)
        data = [self._data(row) for row in rows]
        if viewer is not None:
            data = [row for row in data if row.get("viewer") == viewer]
        return data

    def record_view(
        self,
        link_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        actor: str | None = None,
        source: str,
        write_back: bool = True,
        fire_queue: bool = False,
    ) -> dict[str, Any]:
        """Record one viewer's open of one link, page by page.

        The order is the researched data flow: the viewer emits per-page
        telemetry, the analytics store keeps it, the rep is alerted, and the
        engagement record is written back to the CRM opportunity. The telemetry
        is written before the alert and before the CRM hand-off, so a CRM that
        never answers still leaves the room knowing what the buyer did.
        """
        record = self._link(link_id, include_deleted=True)
        rules.require_open(record, now=self._now())
        data = self._data(record)
        if not data.get("tracking_enabled", True):
            raise DwellRefused(
                f"tracked link {record['id']} has tracking_enabled=false, so its views are refused",
                link_id=record["id"],
            )

        view = rules.normalise_view(payload or {})
        if not view["viewer"] and data.get("require_viewer", True):
            raise ViewerRequired(
                "this tracked link records who viewed, so a view needs a viewer identity",
                link_id=record["id"],
            )

        viewer = view["viewer"]
        # Filtered by this viewer's own rows. ``None`` is a legitimate identity
        # here - an anonymously viewed link - so the comparison is explicit
        # rather than a truthiness test, or one anonymous reader would inherit
        # another viewer's revisits and alerts.
        prior = [row for row in self._rows_for(record["id"]) if row.get("viewer") == viewer]
        room_ref = data.get(vocab.ROOM_REF) or record.get("room_id")
        rows = rules.timing_rows(record, view, asset_name=data.get("asset_name"), room_ref=room_ref)
        revisit = rules.revisit_index(prior, rows)
        summary = rules.summarise_view(rows)
        summary["rows"] = rows

        created = self.store.bulk_create(
            vocab.VIEW_COLLECTION, rows, room_id=room_ref, actor=actor, source=source
        )

        alerts = rules.alert_decisions(
            record, view, summary, prior_views=len(prior), revisit=revisit
        )
        alert_rows: list[dict[str, Any]] = []
        for decision in alerts:
            alert_rows.append(
                self.store.create(
                    vocab.ALERT_COLLECTION,
                    {
                        "link_id": record["id"],
                        "link_title": data.get("title"),
                        vocab.ROOM_REF: room_ref,
                        "viewer": viewer,
                        "revisit": revisit,
                        "trigger": decision["trigger"],
                        "detail": decision["detail"],
                        "deepest_page": summary["deepest_page"],
                        "dwell_seconds": summary["total_seconds"],
                        "notified_at": self._stamp(),
                    },
                    room_id=room_ref,
                    actor=actor,
                    source=source,
                )
            )

        engagement: dict[str, Any] | None = None
        # An internal preview is recorded and raises nothing: no alert and no CRM
        # write. A rep opening their own link is not a buyer signal, and a CRM
        # row from it would be a buyer activity that never happened.
        if write_back and viewer and view.get("audience") == "external":
            engagement = self._write_back(
                record,
                view,
                summary,
                revisit=revisit,
                actor=actor,
                source=source,
                fire_queue=fire_queue,
            )

        return {
            "link_id": record["id"],
            "room_id": room_ref,
            "viewer": viewer,
            "session_id": view.get("session_id"),
            "audience": view.get("audience"),
            "revisit": revisit,
            "page_count": len(summary["pages_viewed"]),
            "pages_viewed": summary["pages_viewed"],
            "deepest_page": summary["deepest_page"],
            "dwell_seconds": summary["total_seconds"],
            "started_at": summary["started_at"],
            "ended_at": summary["ended_at"],
            "readings": len(created),
            "view_ids": [row["id"] for row in created],
            "alerts": [{"id": row["id"], **self._data(row)} for row in alert_rows],
            "engagement": engagement,
        }

    def _write_back(
        self,
        link: Mapping[str, Any],
        view: Mapping[str, Any],
        summary: Mapping[str, Any],
        *,
        revisit: int,
        actor: str | None,
        source: str,
        fire_queue: bool,
    ) -> dict[str, Any]:
        """Hand the view to WF-037's engagement sync, as one canonical event."""
        data = self._data(link)
        room_ref = data.get(vocab.ROOM_REF) or link.get("room_id")
        payload = rules.engagement_record(
            link,
            view,
            summary,
            revisit=revisit,
            buyer_crm_id=view.get("buyer_crm_id"),
        )
        sync = self.engagement_factory()
        result = sync.record_event(
            str(room_ref), payload, actor=actor, source=source, fire_queue=fire_queue
        )
        queue = result.get("queue") or {}
        engagement = result.get("engagement") or {}
        return {
            "event_type": payload["type"],
            "engagement_id": engagement.get("id"),
            "queue_id": queue.get("id"),
            "queue_state": self._data(queue).get("state") if queue else None,
            "fired": bool(result.get("fired")),
            "result": result.get("result"),
            "payload": payload,
        }

    # -- reads -------------------------------------------------------------- #

    def link_analytics(self, link_id: str) -> dict[str, Any]:
        """One link's whole analytics block: page dwell, drop-off and viewers.

        Buyers only, which is WF-018's sourced rule for every metric it computes:
        "engagement from external users", and a share is the one internal
        exception. A rep previewing their own link is not a buyer signal.
        """
        record = self._link(link_id, include_deleted=True)
        data = self._data(record)
        rows = [row for row in self._rows_for(record["id"]) if row.get("audience") == "external"]
        revisits = rules.group_revisits(rows)
        pages = [int(row.get("page") or 0) for row in rows]
        page_count = int(data.get("page_count") or 0) or (max(pages) if pages else 0)
        dwell = rules.page_dwell(rows, page_count=page_count or None)
        drop_off = rules.drop_off_per_page(revisits, page_count=max(1, page_count))
        return {
            "link": self._link_view(record),
            "audience": "external",
            "session_gap_seconds": vocab.REVISIT_GAP_SECONDS,
            "page_count": page_count,
            "readings": len(rows),
            "revisits": revisits,
            "viewers": self._viewer_rows(rows),
            "time_spent_per_page": dwell,
            "drop_off_per_page": drop_off["curve"],
            "drop_off": drop_off,
            "pages_never_read": [entry["page"] for entry in dwell if entry["reads"] == 0],
        }

    def _viewer_rows(self, rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[str, list[Mapping[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(str(row.get("viewer") or "anonymous"), []).append(row)
        viewers: list[dict[str, Any]] = []
        for viewer, group in grouped.items():
            summary = rules.summarise_view(group)
            viewers.append(
                {
                    "viewer": viewer,
                    "revisits": len(rules.group_revisits(group)),
                    "readings": summary["readings"],
                    "pages_viewed": summary["pages_viewed"],
                    "deepest_page": summary["deepest_page"],
                    "total_seconds": summary["total_seconds"],
                    "first_seen_at": summary["started_at"],
                    "last_seen_at": summary["ended_at"],
                }
            )
        viewers.sort(key=lambda row: (-int(row["deepest_page"]), row["viewer"]))
        return viewers

    def list_viewers(self, link_id: str) -> dict[str, Any]:
        record = self._link(link_id, include_deleted=True)
        rows = [row for row in self._rows_for(record["id"]) if row.get("audience") == "external"]
        viewers = self._viewer_rows(rows)
        return {"link_id": record["id"], "count": len(viewers), "viewers": viewers}

    def list_records_for_viewer(self, link_id: str, viewer: str) -> dict[str, Any]:
        """Every page view one viewer produced, newest page first, with revisits."""
        record = self._link(link_id, include_deleted=True)
        try:
            address = vocab.require_viewer(viewer)
        except Exception as exc:
            raise LinkNotFound(f"{viewer!r} is not a usable viewer identity") from exc
        rows = self._rows_for(record["id"], address)
        revisits = rules.group_revisits(rows)
        return {
            "link_id": record["id"],
            "viewer": address,
            "readings": len(rows),
            "revisits": revisits,
            "views": sorted(
                rows,
                key=lambda row: (str(row.get("occurredAt") or ""), int(row.get("page") or 0)),
            ),
        }

    def alerts(self, room_id: str | None = None, *, limit: int = 100) -> list[dict[str, Any]]:
        where = {vocab.ROOM_REF: room_id} if room_id else {}
        rows = self.store.find(vocab.ALERT_COLLECTION, where, limit=limit)
        alerts = [self._data(row) for row in rows]
        alerts.sort(key=lambda row: str(row.get("notified_at") or ""), reverse=True)
        return alerts

    def push(
        self, room_id: str, *, actor: str | None = None, source: str, limit: int = 100
    ) -> dict[str, Any]:
        """Fire the queued CRM writes for one room, through WF-037's worker."""
        self.require_room(room_id)
        sync = self.engagement_factory()
        return sync.drain(str(room_id), actor=actor, source=source, limit=limit)

    def summary(self, room_id: str | None = None) -> dict[str, Any]:
        links = self.list_links(room_id)
        revoked = self.list_revoked_links(room_id)
        where = {vocab.ROOM_REF: room_id} if room_id else {}
        open_states = [link for link in links if not link["closed"]]
        return {
            "room_id": room_id,
            "links": len(links),
            "open": len(open_states),
            "expired": sum(1 for link in links if link["state"] == "expired"),
            "revoked": len(revoked),
            "tracking_enabled": sum(1 for link in links if link["tracking_enabled"]),
            "views": self.store.count_where(vocab.VIEW_COLLECTION, where),
            "viewers": len(
                {
                    str(row["data"].get("viewer"))
                    for row in self.store.find(vocab.VIEW_COLLECTION, where, limit=MAX_ROWS)
                    if row["data"].get("viewer")
                }
            ),
            "deliveries": self.store.count_where(vocab.DELIVERY_COLLECTION, where),
            "alerts": self.store.count_where(vocab.ALERT_COLLECTION, where),
            "generated_at": self._stamp(),
        }

    # -- the published contract --------------------------------------------- #

    @staticmethod
    def vocabulary() -> dict[str, Any]:
        return vocab.describe()

    @staticmethod
    def inferences() -> dict[str, Any]:
        return inference_register.describe()


__all__ = ["MAX_ROWS", "TrackedLinkBook"]
