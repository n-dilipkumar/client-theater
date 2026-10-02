"""The store-facing half of WF-018: read the library, write the telemetry, serve
the three researched analytics blocks.

:class:`AnalyticsBook` holds a :class:`~dsr.store.RecordStore` and nothing
else. Every write goes through the store, so every write is audited in the same
transaction as the change, which is the guarantee this product is built on;
this module never opens the database and never bypasses the audit log.

Three design decisions a reviewer should be able to check without reading the
whole file:

**``source`` is a required keyword on every write.** A hardcoded string in a
domain function can drift away from the route that serves the request, and an
audit row naming a path the app does not serve is worse than no audit row.
Every method here takes it from the route, built from ``router.prefix``.

**No migration, no typed column, no new required field.** Four open collections
(:data:`ASSET_COLLECTION`, :data:`TIMING_COLLECTION`, :data:`WATCH_COLLECTION`,
:data:`EVENT_COLLECTION`) of arbitrary JSON. Filtering is ``find()`` over
dotted JSON paths, so a team that adds ``pricingTier`` to an asset can filter on
it the same day.

**Reads never write.** The research is explicit that "Analytics are computed
continuously; the *action* on them is manual", so there is no cache to
invalidate and no aggregation row to keep in step. Every read here is a pure
read of the telemetry, and the suite asserts it.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from dsr.store import RecordStore

from . import metrics
from .errors import (
    NotFound,
    PdfAnalyticsUnavailable,
    TrackingDisabled,
    ValidationError,
    VideoAnalyticsUnavailable,
)
from .vocabulary import (
    MIN_PAGES_FOR_PDF_ANALYTICS,
    SESSION_GAP_SECONDS,
    as_int,
    as_seconds,
    audience_of,
    normalise_snapshot,
    require_asset_type,
    require_event_type,
    require_grain,
)

#: The four collections this workflow owns. All open, all payload-shaped; the
#: envelope (``id``, ``room_id``, ``revision``, timestamps) is the only fixed
#: vocabulary, and it belongs to the store rather than to this feature.
ASSET_COLLECTION = "contentAsset"
TIMING_COLLECTION = "pageTiming"
WATCH_COLLECTION = "watchTiming"
EVENT_COLLECTION = "assetEvent"
ROOM_COLLECTION = "room"

#: A hard ceiling on rows read per analytics block. A PDF read by 200 buyers for
#: 40 pages is 8,000 rows; the cap exists so a pathological asset cannot make
#: one request scan the whole collection. It is above any real document's worth
#: of telemetry, so it does not silently truncate a curve.
MAX_TELEMETRY_ROWS = 50_000


class AnalyticsBook:
    """Library assets, their per-page telemetry, and the three analytics."""

    def __init__(self, store: RecordStore) -> None:
        self.store = store

    # ---------------------------------------------------------------- rooms -- #

    def require_room(self, room_id: str) -> dict[str, Any]:
        record = self.store.get(str(room_id or ""))
        if record is None or record["collection"] != ROOM_COLLECTION:
            raise NotFound(f"room {room_id!r} not found", room_id=str(room_id or ""))
        return record

    def _check_room(self, room_id: str | None) -> str | None:
        """Validate a room scope once, at the edge, so reads stay pure."""
        if room_id is None or str(room_id).strip() == "":
            return None
        clean = str(room_id).strip()
        self.require_room(clean)
        return clean

    # --------------------------------------------------------------- assets -- #

    def resolve_asset(self, reference: str) -> dict[str, Any] | None:
        """Resolve an asset by record id, then by the source's own asset id.

        Two references because there are two ways in. A client holding a record
        id from our own API has the first. A webhook receiver has the second:
        the researched payload identifies the asset by *its* id, and
        correlating our rows with a real ``asset.viewed`` body must not require
        a lookup table nobody was told to build.
        """
        key = str(reference or "").strip()
        if not key:
            return None
        record = self.store.get(key)
        if record is not None and record["collection"] == ASSET_COLLECTION:
            return record
        matches = self.store.find(ASSET_COLLECTION, {"externalId": key}, limit=2)
        return matches[0] if matches else None

    def require_asset(self, reference: str) -> dict[str, Any]:
        record = self.resolve_asset(reference)
        if record is None:
            raise NotFound(f"asset {reference!r} not found", asset=str(reference or ""))
        return record

    def register_asset(
        self,
        payload: Mapping[str, Any] | None = None,
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Register a library asset from the researched snapshot, or update one.

        Upsert keyed on ``externalId``, because the same asset arriving twice
        from ``GET /v1/assets`` and from an ``asset.viewed`` payload is the same
        asset, and two rows would split its telemetry in half.
        """
        fields = normalise_snapshot(payload)
        scope = self._check_room(room_id)
        external_id = fields.get("externalId")
        existing = None
        if external_id:
            existing = self.resolve_asset(str(external_id))

        data = self._decorate(fields)
        if existing is not None:
            return self.store.update(existing["id"], data, actor=actor, source=source)
        return self.store.create(ASSET_COLLECTION, data, room_id=scope, actor=actor, source=source)

    def _decorate(self, fields: Mapping[str, Any]) -> dict[str, Any]:
        """Add the derived availability flags a client renders from.

        Written onto the row rather than computed per read, because they are
        pure functions of fields the row already holds, and a client that has
        to re-derive "is this a multi-page PDF" to decide which panel to draw
        will eventually get it wrong. The source inputs are still there, so
        recomputing is always possible.
        """
        data = dict(fields)
        data["pdfAnalyticsAvailable"] = self._pdf_reason(data) is None
        data["videoAnalyticsAvailable"] = self._video_reason(data) is None
        return data

    def list_assets(
        self,
        *,
        type: str | None = None,
        room_id: str | None = None,
        q: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """The Library list: every asset, optionally scoped and filtered.

        ``type`` resolves through the dynamic index. ``room_id`` is the record
        *envelope*, which the index does not cover, and ``q`` is a
        case-insensitive substring the index cannot answer at all, so both are
        applied here - the same asymmetry :meth:`_rows` has, for the same
        reason.
        """
        if type:
            records = self.store.find(
                ASSET_COLLECTION, {"type": require_asset_type(type)}, limit=MAX_TELEMETRY_ROWS
            )
        else:
            records = self.store.list(ASSET_COLLECTION, limit=MAX_TELEMETRY_ROWS)
        if room_id is not None:
            records = [r for r in records if r["room_id"] == room_id]
        needle = str(q or "").strip().lower()
        if needle:
            records = [r for r in records if needle in str(r["data"].get("name") or "").lower()]
        return sorted(records, key=lambda r: (str(r["data"].get("name") or "").lower(), r["id"]))[
            :limit
        ]

    # --------------------------------------------------------------- events -- #

    def record_event(
        self,
        asset_ref: str,
        payload: Mapping[str, Any] | None = None,
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Record one ``asset.viewed`` / ``asset.downloaded`` / ``asset.shared``.

        The payload's embedded asset snapshot is what the researched webhook
        carries, and it is stored verbatim on the event row. That is the
        researched extensibility path made concrete: a third party consumes
        ``asset.viewed`` rows and joins them to its own viewer telemetry, and
        the snapshot is the join key.

        An event for an asset this product has never seen registers the asset
        from that snapshot, because the payload embeds it precisely so the
        receiver does not have to make a second call. A known asset is left
        alone: overwriting a library record from a webhook would let any event
        rewrite an asset's name.
        """
        body = dict(payload or {})
        name = require_event_type(body.get("event"))
        scope = self._check_room(body.get("room_id") or room_id)

        record = self.resolve_asset(asset_ref)
        snapshot = body.get("asset") if isinstance(body.get("asset"), Mapping) else None
        if record is None and snapshot:
            record = self.register_asset(snapshot, room_id=scope, actor=actor, source=source)
        if record is None:
            raise NotFound(f"asset {asset_ref!r} not found", asset=str(asset_ref or ""))

        stamp = metrics.parse_ts(body.get("occurred_at") or body.get("occurredAt"))
        data: dict[str, Any] = {
            "event": name,
            "assetId": record["id"],
            "assetExternalId": record["data"].get("externalId"),
            "viewer": str(body.get("viewer") or "").strip(),
            "isInternal": bool(body.get("isInternal")),
            "audience": audience_of(body),
            "occurredAt": stamp.isoformat() if stamp else metrics.utcnow(),
        }
        if snapshot:
            # Verbatim, including fields this workflow knows nothing about: the
            # snapshot is somebody else's contract, and rewriting it into our
            # vocabulary would destroy the thing being joined on.
            data["assetSnapshot"] = dict(snapshot)
        if body.get("metadata") is not None:
            data["metadata"] = body["metadata"]

        return self.store.create(EVENT_COLLECTION, data, room_id=scope, actor=actor, source=source)

    # ------------------------------------------------------------ telemetry -- #

    def record_timings(
        self,
        asset_ref: str,
        payload: Mapping[str, Any] | None = None,
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Record one viewer's per-page timing for one open of one PDF.

        The envelope is per request and the rows are just pages, because the
        researched data flow is exactly that: "Dock's viewer emits per-page
        timing" while a buyer has one document open. That also makes the batch
        a single audited unit, which matters because the rows are meaningless
        apart - half a session is not a session.

        Refused, not dropped, when the asset is untracked or is not a PDF. See
        the ``untracked-timings-are-refused`` inference.
        """
        record = self.require_asset(asset_ref)
        body = dict(payload or {})

        if record["data"].get("type") != "pdf":
            raise ValidationError(
                "per-page timing is only recorded for PDF assets; "
                f"asset {record['id']} is type={record['data'].get('type')!r}",
                field="asset",
                asset_type=record["data"].get("type"),
            )
        if not record["data"].get("trackingEnabled", True):
            raise TrackingDisabled(
                f"asset {record['id']!r} has trackingEnabled=false, so its page timings are refused",
                asset=record["id"],
                assetName=record["data"].get("name"),
            )

        items = body.get("timings") or body.get("pages") or []
        if not isinstance(items, Sequence) or isinstance(items, (str, bytes)) or not items:
            raise ValidationError(
                "timings must be a non-empty list of {page, seconds} objects", field="timings"
            )

        scope = self._check_room(body.get("room_id") or room_id)
        audience = audience_of(body)
        viewer = str(body.get("viewer") or "").strip()
        session_id = str(body.get("session_id") or body.get("sessionId") or "").strip() or None
        base = metrics.parse_ts(body.get("occurred_at") or body.get("occurredAt"))

        rows: list[dict[str, Any]] = []
        for position, item in enumerate(items):
            if not isinstance(item, Mapping):
                raise ValidationError(
                    f"timings[{position}] must be an object, got {item!r}",
                    field=f"timings[{position}]",
                )
            stamp = metrics.parse_ts(item.get("occurred_at") or item.get("occurredAt")) or base
            rows.append(
                {
                    "assetId": record["id"],
                    "assetExternalId": record["data"].get("externalId"),
                    "page": as_int(item.get("page"), f"timings[{position}].page", minimum=1),
                    "seconds": as_seconds(item.get("seconds"), f"timings[{position}].seconds"),
                    "viewer": viewer,
                    "sessionId": session_id,
                    "audience": audience,
                    "isInternal": audience == "internal",
                    "occurredAt": stamp.isoformat() if stamp else metrics.utcnow(),
                }
            )

        created = self.store.bulk_create(
            TIMING_COLLECTION, rows, room_id=scope, actor=actor, source=source
        )
        return {
            "asset_id": record["id"],
            "room_id": scope,
            "viewer": viewer,
            "session_id": session_id,
            "audience": audience,
            "count": len(created),
            "timings": [
                {
                    "id": row["id"],
                    "page": row["data"]["page"],
                    "seconds": row["data"]["seconds"],
                    "occurredAt": row["data"]["occurredAt"],
                }
                for row in created
            ],
        }

    def record_watch(
        self,
        asset_ref: str,
        payload: Mapping[str, Any] | None = None,
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Record one completed watch of a self-hosted video.

        Watch time is the researched video metric, and it is scoped the same way
        PDF Analytics is: "For self-hosted videos, we're able to show the average
        watch time". A watch row for a video we do not host is refused, because
        no such viewer would ever emit one and accepting it would put a number
        on the page that no reader of ours produced.
        """
        record = self.require_asset(asset_ref)
        body = dict(payload or {})
        if record["data"].get("type") != "video":
            raise ValidationError(
                "watch time is only recorded for video assets; "
                f"asset {record['id']} is type={record['data'].get('type')!r}",
                field="asset",
                asset_type=record["data"].get("type"),
            )
        if not record["data"].get("selfHosted", True):
            raise VideoAnalyticsUnavailable(
                f"asset {record['id']!r} is not self-hosted, so no watch time exists for it",
                reason="not_self_hosted",
                asset=record["id"],
            )

        scope = self._check_room(body.get("room_id") or room_id)
        audience = audience_of(body)
        stamp = metrics.parse_ts(body.get("occurred_at") or body.get("occurredAt"))
        data = {
            "assetId": record["id"],
            "assetExternalId": record["data"].get("externalId"),
            "seconds": as_seconds(body.get("seconds")),
            "viewer": str(body.get("viewer") or "").strip(),
            "audience": audience,
            "isInternal": audience == "internal",
            "occurredAt": stamp.isoformat() if stamp else metrics.utcnow(),
        }
        if body.get("completed") is not None:
            data["completed"] = bool(body["completed"])
        return self.store.create(WATCH_COLLECTION, data, room_id=scope, actor=actor, source=source)

    # ----------------------------------------------------------------- reads -- #

    def _rows(
        self,
        collection: str,
        asset: Mapping[str, Any],
        *,
        room_id: str | None = None,
        audience: str | None = None,
    ) -> list[dict[str, Any]]:
        """Read one collection for one asset, honouring the scope filters.

        ``assetId`` and ``audience`` are dotted JSON paths in the row's own
        payload, so they resolve through the dynamic index and no private
        index is kept. ``room_id`` is the record *envelope*, which the dynamic
        index does not cover, so it is filtered here - the same asymmetry the
        rest of this product has, and the reason an envelope column can never
        be confused with a team field.
        """
        where: dict[str, Any] = {"assetId": asset["id"]}
        if audience is not None:
            where["audience"] = audience
        records = self.store.find(collection, where, limit=MAX_TELEMETRY_ROWS)
        if room_id is not None:
            records = [r for r in records if r["room_id"] == room_id]
        return [r["data"] for r in records]

    def _external_timings(
        self, asset: Mapping[str, Any], *, room_id: str | None
    ) -> list[dict[str, Any]]:
        """Page timings from buyers only.

        Sourced, and the single place it is applied for the PDF metrics:
        "Dock's analytics only show engagement from external users (i.e. buyers
        and customers)." A rep previewing their own deck is not a buyer signal,
        and letting the team read its own PDF inflate the curve is the failure
        the rule exists to prevent.
        """
        return self._rows(TIMING_COLLECTION, asset, room_id=room_id, audience="external")

    # -- availability ------------------------------------------------------ #

    @staticmethod
    def _pdf_reason(data: Mapping[str, Any]) -> str | None:
        """Why PDF Analytics does not apply to this asset, or ``None``."""
        if data.get("type") != "pdf":
            return "unknown_asset_type"
        if int(data.get("pageCount") or 0) < MIN_PAGES_FOR_PDF_ANALYTICS:
            return "single_page"
        return None

    @staticmethod
    def _video_reason(data: Mapping[str, Any]) -> str | None:
        """Why Video Analytics does not apply to this asset, or ``None``."""
        if data.get("type") != "video":
            return "unknown_asset_type"
        if not data.get("selfHosted", True):
            return "not_self_hosted"
        return None

    # -- the three researched blocks ---------------------------------------- #

    def pdf_analytics(self, asset_ref: str, *, room_id: str | None = None) -> dict[str, Any]:
        """**Advanced Analytics -> PDF Analytics**: time spent, and drop off.

        Time spent per page: the average amount of time that's spent per page.
        Drop off per page: understand when someone stops looking at your content.

        Both only exist for a multi-page PDF, and both only count buyers. A
        single-page PDF or a video is a 422 with a named reason rather than an
        empty curve, which would read as "nobody read it".
        """
        asset = self.require_asset(asset_ref)
        reason = self._pdf_reason(asset["data"])
        if reason is not None:
            raise PdfAnalyticsUnavailable(
                self._unavailable_message("PDF Analytics", asset, reason),
                reason=reason,
                asset=asset["id"],
                assetType=asset["data"].get("type"),
                pageCount=asset["data"].get("pageCount"),
            )

        scope = self._check_room(room_id)
        pages = int(asset["data"].get("pageCount") or MIN_PAGES_FOR_PDF_ANALYTICS)
        rows = self._external_timings(asset, room_id=scope)
        sessions = metrics.group_sessions(rows)
        drop_off = metrics.drop_off_per_page(sessions, page_count=pages)
        dwell = metrics.dwell_per_page(rows, page_count=pages)

        return {
            "asset_id": asset["id"],
            "asset_name": asset["data"].get("name"),
            "room_id": scope,
            "page_count": pages,
            "audience": "external",
            "session_gap_seconds": SESSION_GAP_SECONDS,
            "time_spent_per_page": dwell,
            "drop_off_per_page": drop_off["curve"],
            "drop_off": drop_off,
            "sessions": sessions,
            "readings": len(rows),
            "pages_never_read": [entry["page"] for entry in dwell if entry["reads"] == 0],
        }

    def video_analytics(self, asset_ref: str, *, room_id: str | None = None) -> dict[str, Any]:
        """**Advanced Analytics -> Video Analytics**: the average watch time.

        "For self-hosted videos, we're able to show the average watch time of
        the video." Buyers only, like every other metric here.
        """
        asset = self.require_asset(asset_ref)
        reason = self._video_reason(asset["data"])
        if reason is not None:
            raise VideoAnalyticsUnavailable(
                self._unavailable_message("Video Analytics", asset, reason),
                reason=reason,
                asset=asset["id"],
                assetType=asset["data"].get("type"),
                selfHosted=asset["data"].get("selfHosted"),
            )
        scope = self._check_room(room_id)
        rows = self._rows(WATCH_COLLECTION, asset, room_id=scope, audience="external")
        return {
            "asset_id": asset["id"],
            "asset_name": asset["data"].get("name"),
            "room_id": scope,
            "audience": "external",
            **metrics.average_watch_time(rows),
        }

    def core_analytics(
        self, asset_ref: str, *, grain: str = "day", room_id: str | None = None
    ) -> dict[str, Any]:
        """**Core Analytics**: the bar charts, and the flat counts under them.

        Views and downloads count buyers. Shares counts the internal team, which
        is the one exception the research states. The audience rule lives in
        :func:`dsr.pdf_analytics.metrics._counts_for` rather than in a filter
        here, because a share is internal *by definition* and filtering it out
        upstream would make the exception unrepresentable.
        """
        asset = self.require_asset(asset_ref)
        scope = self._check_room(room_id)
        bucket = require_grain(grain)
        rows = self._rows(EVENT_COLLECTION, asset, room_id=scope)
        return {
            "asset_id": asset["id"],
            "asset_name": asset["data"].get("name"),
            "room_id": scope,
            "grain": bucket,
            "counts": metrics.core_counts(rows),
            "series": metrics.bucket_series(rows, grain=bucket),
        }

    @staticmethod
    def _unavailable_message(block: str, asset: Mapping[str, Any], reason: str) -> str:
        if reason == "unknown_asset_type":
            return (
                f"{block} is available for {block.split()[0].lower()} assets; "
                f"asset {asset['id']!r} is type={asset['data'].get('type')!r}"
            )
        if reason == "single_page":
            return (
                f"{block} is available for multi-page PDFs; asset {asset['id']!r} has "
                f"{asset['data'].get('pageCount') or 0} page(s)"
            )
        return (
            f"{block} is available for self-hosted videos; asset {asset['id']!r} "
            "is hosted elsewhere"
        )

    def asset_detail(
        self, asset_ref: str, *, grain: str = "day", room_id: str | None = None
    ) -> dict[str, Any]:
        """The asset detail page's **Advanced Analytics** block, whole.

        The user flow is: open the asset, scroll to Advanced Analytics, read the
        PDF or Video panel. So one call answers the whole page, and an analytics
        block that does not apply is reported as unavailable with its reason
        rather than raised - on the detail page "not applicable" is information,
        while on the dedicated route it is a wrong question.
        """
        asset = self.require_asset(asset_ref)
        scope = self._check_room(room_id)
        bucket = require_grain(grain)

        core = self.core_analytics(asset["id"], grain=bucket, room_id=scope)
        panels: dict[str, Any] = {"core": core}
        pdf_reason = self._pdf_reason(asset["data"])
        panels["pdf"] = (
            self.pdf_analytics(asset["id"], room_id=scope)
            if pdf_reason is None
            else {"available": False, "reason": pdf_reason, "block": "PDF Analytics"}
        )
        video_reason = self._video_reason(asset["data"])
        panels["video"] = (
            self.video_analytics(asset["id"], room_id=scope)
            if video_reason is None
            else {"available": False, "reason": video_reason, "block": "Video Analytics"}
        )
        for panel in panels.values():
            if panel.get("available") is not False:
                panel["available"] = True

        return {
            "id": asset["id"],
            "room_id": asset["room_id"],
            "created_at": asset["created_at"],
            "updated_at": asset["updated_at"],
            "asset": asset["data"],
            "advanced_analytics": panels,
        }

    def room_assets(self, room_id: str, *, grain: str = "day") -> dict[str, Any]:
        """The library assets shared into one room, with their engagement.

        Room-scoped because that is the question a seller working a deal asks:
        not "how does this deck perform" but "how did *this* buyer read it". The
        numbers are the same researched metrics over that room's telemetry only,
        so a room view and a library view can never be mistaken for one another.
        """
        room = self.require_room(room_id)
        bucket = require_grain(grain)
        assets = self.store.list(ASSET_COLLECTION, room_id=room["id"], limit=500)
        return {
            "room_id": room["id"],
            "room_name": room["data"].get("name"),
            "grain": bucket,
            "count": len(assets),
            "assets": [self._summary(asset, room_id=room["id"], grain=bucket) for asset in assets],
        }

    def _summary(self, asset: Mapping[str, Any], *, room_id: str, grain: str) -> dict[str, Any]:
        """One row of the room's library list: the asset and its headline numbers.

        Deliberately a fixed set of the researched metrics rather than the full
        blocks. A room list renders a dozen assets; a drop-off curve per asset
        is not what anyone reads there, and the detail route is one call away.
        """
        data = asset["data"]
        summary: dict[str, Any] = {
            "id": asset["id"],
            "name": data.get("name"),
            "type": data.get("type"),
            "pageCount": data.get("pageCount"),
            "selfHosted": data.get("selfHosted"),
            "isInternal": data.get("isInternal"),
            "trackingEnabled": data.get("trackingEnabled"),
            "downloadEnabled": data.get("downloadEnabled"),
            "shareUrl": data.get("shareUrl"),
            "tags": data.get("tags") or [],
            "pdfAnalyticsAvailable": self._pdf_reason(data) is None,
            "videoAnalyticsAvailable": self._video_reason(data) is None,
            "counts": metrics.core_counts(self._rows(EVENT_COLLECTION, asset, room_id=room_id)),
        }
        if summary["pdfAnalyticsAvailable"]:
            rows = self._external_timings(asset, room_id=room_id)
            sessions = metrics.group_sessions(rows)
            pages = int(data.get("pageCount") or 0)
            drop_off = metrics.drop_off_per_page(sessions, page_count=pages)
            dwell = metrics.dwell_per_page(rows, page_count=pages)
            with_dwell = [entry for entry in dwell if entry["reads"]]
            top = max(with_dwell, key=lambda e: e["average_seconds"] or 0.0) if with_dwell else None
            summary["pdf"] = {
                "page_count": pages,
                "readings": len(rows),
                "sessions": drop_off["sessions"],
                "completed_sessions": drop_off["completed_sessions"],
                "last_page_reached": max((e["page"] for e in with_dwell), default=0),
                "longest_average_page": top["page"] if top else None,
                "longest_average_seconds": top["average_seconds"] if top else None,
            }
        if summary["videoAnalyticsAvailable"]:
            watches = self._rows(WATCH_COLLECTION, asset, room_id=room_id, audience="external")
            summary["video"] = metrics.average_watch_time(watches)
        return summary


__all__ = [
    "ASSET_COLLECTION",
    "AnalyticsBook",
    "EVENT_COLLECTION",
    "MAX_TELEMETRY_ROWS",
    "TIMING_COLLECTION",
    "WATCH_COLLECTION",
]
