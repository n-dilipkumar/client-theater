"""The reads: a viewers list, an aggregate, one view's breakdown, a link's views.

The engine is the only module in this package that touches the store. It holds the
store and a clock and nothing else, and the HTTP layer builds it per request for
exactly that reason: both seams stay overridable in a test without hanging a
long-lived object off ``app.state``, which is a shared file this feature may not
edit.

This is a read path and it is honest about it
---------------------------------------------

The specification's data flow says every view writes a ``View`` row and updates a
``Visitor`` row. That producer is not this ticket. This workflow presents those rows,
so every method below is a read, none of them takes a ``source``, and none of them
writes an audit row. A route that recorded a row on every read would fill this
product's own guarantee - the audit log - with entries describing no change having
been made.

Two methods do write, and both are the write path this workflow does own. See
:meth:`EngagementEngine.ingest_view` and :meth:`EngagementEngine.ingest_visitor`.

The cache, and why it is here
----------------------------

The specification says analytics are "cheap (cached aggregates)" and "Cache the
response if you're polling", and the documented integration path is polling: "Webhooks
are not part of the public API yet... Until then: Poll the analytics endpoints." So an
aggregate is cached by a poller rather than recomputed per request. Every cached
response says so in :data:`~dsr.security_governance.engagement.CACHED_FIELD` and names
the instant it was computed, because a poller cannot poll honestly against a response
that does not say whether it is reading fresh numbers.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from dsr.security_governance import engagement as vocab, engagement_rules as rules
from dsr.store import RecordStore

#: The process-level aggregate cache. See :class:`EngagementEngine` for why it is not
#: an instance attribute: the engine is built per request, so a per-instance cache
#: would be discarded between two polls and would never serve the second one.
#:
#: Values are ``(monotonic_at_computation, payload)``. The store's identity is part of
#: every key, so two stores never read each other's numbers and a test's temporary
#: database is never answered from another test's rows.
_CACHE: dict[tuple[int, str], tuple[float, dict[str, Any]]] = {}


class EngagementEngine:
    """Every read this workflow performs, over one audited store.

    ``now`` is a callable rather than a value so a test can move the clock by hand,
    and ``monotonic`` is injected for the same reason: the cache decides staleness by
    elapsed seconds, and a test that cannot choose the elapsed time cannot test the
    boundary the specification cares about, which is that a poll inside the window
    reads the cache.

    The aggregate cache is **process-level**, not per instance, and that is the whole
    point of it. ``dsr.deps.get_engine`` builds one of these per request, so a cache
    living on the instance would be thrown away between two polls and would never be
    read. The specification's requirement is that a caller can "Cache the response if
    you're polling", which is a claim about two requests in a row: the first one
    populates the cache and the second one reads it. Holding the cache at module
    scope is what makes that true across requests, and it is the same shape the
    vendor's "cheap (cached aggregates)" describes.

    The key carries the store's own identity, so two stores never read each other's
    numbers. :meth:`clear_cache` exists for a test that needs to start from nothing,
    and every write calls it rather than trying to invalidate one key.
    """

    def __init__(
        self,
        store: RecordStore,
        now: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self.store = store
        self._now = now or rules.utcnow
        self._monotonic = monotonic or time.monotonic
        self._store_key = id(store)

    @classmethod
    def clear_cache(cls) -> None:
        """Drop every cached aggregate.

        A classmethod rather than an instance method, because the cache is process
        level and the only callers that need this are a write - which has an engine but
        no reason to depend on it - and a test - which has none.
        """

        _CACHE.clear()

    # -- the write path this workflow owns ---------------------------------- #

    def ingest_visitor(
        self,
        email: str,
        *,
        dataroom_id: str | None = None,
        verified: Any = None,
        invited_at: Any = None,
        total_views: Any = None,
        last_viewed_at: Any = None,
        room_id: str | None = None,
    ) -> dict[str, Any]:
        """Record one persistent visitor row.

        This workflow is a read path over view events, but a viewers list with nothing
        in it cannot be reviewed, and the specification's own demo surface is "one row
        per buyer email". So the row is written through the engine rather than straight
        into the store, which means it lands in the audit log the way every other write
        in this product does.

        ``verified`` is optional on purpose. A row written without it reads as
        ``unknown`` through :func:`rules.verification_state`, and that is a distinct
        and truthful answer from ``unverified``: it says no proof was recorded rather
        than that proof was refused.

        The stored ``invited_at`` and ``last_viewed_at`` are Unix milliseconds, because
        the specification puts the boundary in Unix ms and the implementer notes ask
        for the inside-record unit to be confirmed and stated once. It is stated in
        :mod:`dsr.security_governance.engagement`.
        """

        address = str(email or "").strip()
        if not address:
            raise rules.EngagementError(
                "A visitor needs an email address.", {"email": "An email address is required."}
            )

        data: dict[str, Any] = {
            vocab.EMAIL: address,
            vocab.DATAROOM_ID: dataroom_id or room_id or "",
            vocab.INVITED_AT: rules.coerce_ms(invited_at, vocab.INVITED_AT),
            vocab.LAST_VIEWED_AT: rules.coerce_ms(last_viewed_at, vocab.LAST_VIEWED_AT),
            "total_views": max(int(total_views or 0), 0),
            "recorded_at": rules.stamp(self._now()),
        }
        if verified is not None:
            data[vocab.VERIFIED] = rules.verification_state({"verified": verified}) == (
                vocab.VERIFIED_TRUE
            )

        record = self.store.create(
            vocab.VISITOR_COLLECTION,
            data,
            room_id=room_id or (dataroom_id or None),
            actor="reps",
            source="wf-075 ingest",
        )
        self._invalidate()
        return self.project_visitor(record)

    def ingest_view(
        self,
        link_id: str,
        *,
        viewer_email: str | None = None,
        view_type: Any = None,
        viewed_at: Any = None,
        downloaded_at: Any = None,
        download_type: Any = None,
        page_durations: Any = None,
        location: Any = None,
        client: Any = None,
        document_id: str | None = None,
        room_id: str | None = None,
    ) -> dict[str, Any]:
        """Record one view event against one link.

        Every field of the ``View`` row the specification names is accepted, and every
        field of the ``ViewAnalytics`` shape is stored verbatim, because the drill-down
        is the surface that reads them back and a projection that invented a shape
        would make the two disagree.

        ``viewer_email`` is optional and that is the specification's rule, not a
        convenience: views "never tied to a ``Visitor`` record are still reachable
        per-link via ``GET /v1/links/{id}/views``". An anonymous view is written with
        no email and stays in every per-link count.

        The write lands in the audit log with ``source="wf-075 ingest"`` rather than a
        route string, because no route served it. Claiming a route here would be the
        lie the brief's audit-source rule exists to prevent.
        """

        kind = str(view_type or vocab.VIEW_TYPE_LINK)
        payload: dict[str, Any] = {
            "link_id": link_id,
            "document_id": document_id,
            # The room is stored in the payload as well as on the envelope, because the
            # aggregate filters on the payload key. `room_id` is an envelope field, and
            # AuditedDatabase strips it out of `data`, so a view that relied on the
            # envelope alone would be invisible to a filtered board - a room filter that
            # silently returns nothing is the exact defect the envelope-stripping
            # behaviour causes elsewhere in this product.
            vocab.DATAROOM_ID: room_id or "",
            vocab.VIEWER_EMAIL: str(viewer_email or "").strip() or None,
            vocab.VIEW_TYPE: kind,
            vocab.VIEWED_AT: rules.coerce_ms(viewed_at, vocab.VIEWED_AT) or self._epoch_ms(),
            vocab.DOWNLOADED_AT: rules.coerce_ms(downloaded_at, vocab.DOWNLOADED_AT),
            vocab.DOWNLOAD_TYPE: str(download_type) if download_type else None,
            vocab.PAGE_DURATIONS: list(page_durations or []),
            vocab.LOCATION: dict(location) if isinstance(location, Mapping) else {},
            vocab.CLIENT: dict(client) if isinstance(client, Mapping) else {},
            "recorded_at": rules.stamp(self._now()),
        }
        payload[vocab.TOTAL_DURATION_SECONDS] = rules.total_duration(payload)

        record = self.store.create(
            vocab.VIEW_COLLECTION,
            payload,
            room_id=room_id,
            actor="reps",
            source="wf-075 ingest",
        )
        self._invalidate()
        return self.project_view(record)

    def _epoch_ms(self) -> int:
        """The current instant as Unix milliseconds.

        Used when a caller records a view without saying when. The alternative is a
        ``None`` ``viewed_at``, which would put the row last in every reverse
        chronological list and exclude it from every bounded window - so a view that
        nobody timestamped would quietly vanish from the analytics the specification
        says are the point of the workflow.
        """

        return int(self._now().timestamp() * 1000)

    # -- the viewers list ---------------------------------------------------- #

    def visitors(
        self, email: str | None = None, dataroom_id: str | None = None
    ) -> list[dict[str, Any]]:
        """The persistent visitors, one row per email, with first and last seen.

        The specification's first user-flow step: "one row per buyer email (``First
        Seen``, ``Last Seen``), or filters to a single address."

        ``first_seen`` is not a stored field and is not invented from the view rows
        either. It is the earliest ``invited_at`` this workflow holds, falling back to
        the earliest view for that address when the visitor was never given an
        invitation stamp. Both are read from rows that exist, and a visitor with
        neither reports ``None`` rather than a zero that would read as the epoch.
        """

        records = self.store.list(vocab.VISITOR_COLLECTION, limit=500, order_by="created_at")
        rows = [self.project_visitor(record) for record in records]

        if email:
            wanted = str(email).strip().lower()
            rows = [row for row in rows if str(row.get(vocab.EMAIL) or "").lower() == wanted]
        if dataroom_id:
            rows = [row for row in rows if row.get(vocab.DATAROOM_ID) == dataroom_id]

        for row in rows:
            address = str(row.get(vocab.EMAIL) or "").lower()
            row["first_seen"] = self._first_seen(row, address)
            row["last_seen"] = self._last_seen(row, address)
        return rows

    def _first_seen(self, row: Mapping[str, Any], address: str) -> int | None:
        invited = rules.coerce_ms(row.get(vocab.INVITED_AT), vocab.INVITED_AT)
        viewed = self._earliest_view(address)
        candidates = [value for value in (invited, viewed) if value is not None]
        return min(candidates) if candidates else None

    def _last_seen(self, row: Mapping[str, Any], address: str) -> int | None:
        recorded = rules.coerce_ms(row.get(vocab.LAST_VIEWED_AT), vocab.LAST_VIEWED_AT)
        viewed = self._latest_view(address)
        candidates = [value for value in (recorded, viewed) if value is not None]
        return max(candidates) if candidates else None

    def _views_for(self, address: str) -> list[dict[str, Any]]:
        if not address:
            return []
        rows = []
        for record in self.store.list(vocab.VIEW_COLLECTION, limit=1000, order_by="created_at"):
            data = record.get("data") or {}
            if str(data.get(vocab.VIEWER_EMAIL) or "").strip().lower() == address:
                rows.append(dict(data))
        return rows

    def _earliest_view(self, address: str) -> int | None:
        stamps = [
            rules.coerce_ms(row.get(vocab.VIEWED_AT), vocab.VIEWED_AT)
            for row in self._views_for(address)
        ]
        present = [value for value in stamps if value is not None]
        return min(present) if present else None

    def _latest_view(self, address: str) -> int | None:
        stamps = [
            rules.coerce_ms(row.get(vocab.VIEWED_AT), vocab.VIEWED_AT)
            for row in self._views_for(address)
        ]
        present = [value for value in stamps if value is not None]
        return max(present) if present else None

    def read_visitor(self, visitor_id: str) -> dict[str, Any]:
        """One visitor, with the proof state the specification says to read.

        Or :class:`~dsr.security_governance.engagement_rules.VisitorNotFound`. A row
        that exists but is not one of this workflow's visitors is a 404 rather than a
        500, because a feature may not read across into another workflow's collection.
        """

        record = self.store.get(visitor_id)
        if record is None or record.get("collection") != vocab.VISITOR_COLLECTION:
            raise rules.VisitorNotFound(visitor_id)
        row = self.project_visitor(record)
        address = str(row.get(vocab.EMAIL) or "").lower()
        row["first_seen"] = self._first_seen(row, address)
        row["last_seen"] = self._last_seen(row, address)
        row["views"] = [self._project_view_data(data) for data in self._views_for(address)]
        return row

    def visitor_views(self, visitor_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        """One visitor's view history, newest first. ``GET /v1/visitors/{id}/views``."""
        visitor = self.read_visitor(visitor_id)
        address = str(visitor.get(vocab.EMAIL) or "").lower()
        views = self._views_for(address)
        return [
            self._project_view_data(row)
            for row in rules.reverse_chronological(views, rules.page_size(limit))
        ]

    # -- the aggregate ------------------------------------------------------- #

    def stats(
        self,
        dataroom_id: str | None = None,
        *,
        since: Any = None,
        until: Any = None,
        use_cache: bool = True,
    ) -> dict[str, Any]:
        """The cached aggregate for one room. ``GET /v1/analytics/datarooms/{id}``.

        The specification's second user-flow step, with the ``--since`` / ``--until``
        bounds in Unix ms. The cache is what "cheap (cached aggregates)" and "Cache the
        response if you're polling" require, and it is keyed on the room and both
        bounds, so two callers with different windows never read each other's numbers.

        The two counts are read from their own rows and neither is derived from the
        other. ``total_views`` counts view events and ``unique_visitors`` counts the
        distinct addresses behind those events, which is the one case where the
        specification puts both in the same sentence.
        """

        bounds = rules.window(since, until)
        key = (self._store_key, f"stats:{dataroom_id or ''}:{bounds['since']}:{bounds['until']}")
        if use_cache:
            hit = _CACHE.get(key)
            if hit is not None and (self._monotonic() - hit[0]) < vocab.DEFAULT_CACHE_SECONDS:
                return {**hit[1], vocab.CACHED_FIELD: True}

        views = self._view_rows(dataroom_id)
        in_window = [view for view in views if rules.in_window(view.get(vocab.VIEWED_AT), bounds)]
        addresses = rules.unique_visitors(in_window)

        per_page = self._per_page_engagement(in_window)
        dwell = sum(rules.total_duration(view) for view in in_window)

        payload: dict[str, Any] = {
            "dataroom_id": dataroom_id,
            vocab.TOTAL_VIEWS_FIELD: len(in_window),
            vocab.UNIQUE_VISITORS_FIELD: len(addresses),
            vocab.TIME_SPENT_SECONDS_FIELD: dwell,
            vocab.PER_PAGE_FIELD: per_page,
            "viewers": len([row for row in self.visitors(dataroom_id=dataroom_id)]),
            "since": bounds["since"],
            "until": bounds["until"],
            vocab.COMPUTED_AT: self._now_ms(),
            vocab.TIME_UNIT_FIELD: vocab.TIME_UNIT_VALUE,
            vocab.CACHED_FIELD: False,
            "rate_limit_note": (
                "Analytics carry a tighter per-minute rate limit than the rest of the "
                "surface. Poll this endpoint rather than recomputing it per viewer."
            ),
        }
        if use_cache:
            _CACHE[key] = (self._monotonic(), payload)
        return payload

    def per_page(self, views: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """Per-page engagement across a set of views.

        Public because the per-link aggregate needs the same arithmetic as the room
        aggregate, and computing it twice with two different definitions would let the
        two boards disagree about how long a page held attention.
        """

        return self._per_page_engagement(views)

    def computed_at_ms(self) -> int:
        """The current instant as Unix milliseconds, for an aggregate's ``computed_at``."""
        return self._now_ms()

    def _view_rows(self, dataroom_id: str | None = None) -> list[dict[str, Any]]:
        records = self.store.list(vocab.VIEW_COLLECTION, limit=1000, order_by="created_at")
        rows = [dict(record.get("data") or {}) for record in records]
        if dataroom_id:
            rows = [row for row in rows if row.get(vocab.DATAROOM_ID) == dataroom_id]
        return rows

    def _per_page_engagement(self, views: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """Per-page engagement: how long each page number held attention.

        Aggregated across the views rather than reported per view, because "per-page
        engagement" is a room-level word in the specification and the per-view
        breakdown is a separate surface.

        ``viewers`` on each row is how many distinct addresses read that page, and
        ``total_views`` is how many view events did. They are reported as separate
        numbers because they are separate facts: one buyer reading a page four times
        is one engaged reader and four view events.
        """

        totals: dict[int, int] = {}
        readers: dict[int, set[str]] = {}
        events: dict[int, int] = {}
        for view in views:
            address = str(view.get(vocab.VIEWER_EMAIL) or "")
            for page in rules.page_durations(view):
                number = page["page_number"]
                totals[number] = totals.get(number, 0) + page["duration_seconds"]
                events[number] = events.get(number, 0) + 1
                if address:
                    readers.setdefault(number, set()).add(address.lower())

        return [
            {
                "page_number": number,
                "total_duration_seconds": totals[number],
                "viewers": len(readers.get(number, set())),
                "total_views": events.get(number, 0),
            }
            for number in sorted(totals)
        ]

    # -- the drill-down ------------------------------------------------------ #

    def read_view(self, view_id: str) -> dict[str, Any]:
        """One view's full breakdown. ``GET /v1/analytics/views/{id}``.

        This is the specification's third user-flow step: page dwell times plus the
        viewer's country and city and browser, OS and device.

        It reads rather than writes, so it takes no ``source`` and records no audit
        row. Resolving one stored view into the shape the drill-down renders is a read,
        and a route that wrote a row on every drill-down would fill the audit log with
        entries describing no change.
        """

        record = self.store.get(view_id)
        if record is None or record.get("collection") != vocab.VIEW_COLLECTION:
            raise rules.ViewNotFound(view_id)
        return self.project_view(record)

    # -- the per-link view list ---------------------------------------------- #

    def link_views(
        self, link_id: str, limit: Any = None, *, since: Any = None, until: Any = None
    ) -> list[dict[str, Any]]:
        """Every view of one link, newest first. ``GET /v1/links/{id}/views``.

        The specification's fourth user-flow step, and the sentence that keeps
        anonymous views reachable: they are "still reachable per-link via
        ``GET /v1/links/{id}/views``". No filter on viewer email is applied here, so a
        view with no email is in this list and in the count beside it.

        ``limit`` is the researched page size, not a convenience. The endpoint is
        "cursor-paginated" and a caller asking for the whole table would defeat the
        paging the vendor's API actually has.
        """

        bounds = rules.window(since, until)
        rows = [row for row in self._view_rows() if row.get("link_id") == link_id]
        kept = [row for row in rows if rules.in_window(row.get(vocab.VIEWED_AT), bounds)]
        ordered = rules.reverse_chronological(kept, rules.page_size(limit))
        return [self._project_view_data(row) for row in ordered]

    # -- the board ----------------------------------------------------------- #

    def summary(self, dataroom_id: str | None = None) -> dict[str, Any]:
        """The page's headline numbers, read back from the store.

        Counts are read rather than accumulated across calls, so the board cannot
        describe a state the store does not hold.
        """

        views = self._view_rows(dataroom_id)
        visitors = self.visitors(dataroom_id=dataroom_id)
        addresses = rules.unique_visitors(views)
        downloaded = [row for row in views if rules.download_of(row)["downloaded"]]

        return {
            "visitors": len(visitors),
            "verified": sum(
                1 for row in visitors if row.get(vocab.VERIFICATION_FIELD) == vocab.VERIFIED_TRUE
            ),
            "unverified": sum(
                1 for row in visitors if row.get(vocab.VERIFICATION_FIELD) == vocab.VERIFIED_FALSE
            ),
            "unknown_verification": sum(
                1 for row in visitors if row.get(vocab.VERIFICATION_FIELD) == vocab.VERIFIED_UNKNOWN
            ),
            "views": len(views),
            "unique_viewers": len(addresses),
            "anonymous_views": sum(
                1 for row in views if not str(row.get(vocab.VIEWER_EMAIL) or "").strip()
            ),
            "downloads": len(downloaded),
            "time_spent_seconds": sum(rules.total_duration(row) for row in views),
            "by_view_type": rules.tally(views, vocab.VIEW_TYPE),
            "by_country": rules.tally([rules.location_of(row) for row in views], "country"),
            "by_device": rules.tally([rules.client_of(row) for row in views], "device"),
            vocab.COLLECTIONS_FIELD: list(vocab.ALL_COLLECTIONS),
            vocab.TIME_UNIT_FIELD: vocab.TIME_UNIT_VALUE,
        }

    # -- projections --------------------------------------------------------- #

    def project_visitor(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """A stored visitor row as the API returns it.

        The id is read from the envelope rather than from ``data``, because that is
        where the store keeps it, and reading it out of the payload returns ``None``
        and leaves the caller with no way to address the row.

        The projection carries ``verification`` as one of three words rather than a
        bare boolean, because the specification asks the page to "confirm the identity
        was actually proven (not merely typed in)" and a boolean cannot answer that for
        a row where no proof was ever recorded.
        """

        data = dict(record.get("data") or {})
        return {
            "id": record.get("id"),
            "room_id": record.get("room_id"),
            vocab.EMAIL: data.get(vocab.EMAIL),
            vocab.DATAROOM_ID: data.get(vocab.DATAROOM_ID),
            vocab.INVITED_AT: rules.coerce_ms(data.get(vocab.INVITED_AT), vocab.INVITED_AT),
            vocab.LAST_VIEWED_AT: rules.coerce_ms(
                data.get(vocab.LAST_VIEWED_AT), vocab.LAST_VIEWED_AT
            ),
            vocab.TOTAL_VIEWS: int(data.get(vocab.TOTAL_VIEWS) or 0),
            "verified_field": bool(data[vocab.VERIFIED]) if vocab.VERIFIED in data else None,
            vocab.VERIFICATION_FIELD: rules.verification_state(data),
        }

    def project_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """A stored view row as the API returns it, given the whole record."""
        payload = self._project_view_data(dict(record.get("data") or {}))
        payload["id"] = record.get("id")
        payload["room_id"] = record.get("room_id")
        return payload

    def _project_view_data(self, data: Mapping[str, Any]) -> dict[str, Any]:
        """The ``View`` row and the ``ViewAnalytics`` shape, projected together.

        Both quoted shapes are returned side by side rather than merged. A drill-down
        shows the analytics fields and the event fields, and merging them would lose
        the distinction the specification draws between a recorded view and the
        analytics rolled up from it.
        """

        return {
            "link_id": data.get("link_id"),
            "document_id": data.get("document_id"),
            vocab.DATAROOM_ID: data.get(vocab.DATAROOM_ID),
            vocab.VIEWER_EMAIL: data.get(vocab.VIEWER_EMAIL),
            vocab.VIEW_TYPE: data.get(vocab.VIEW_TYPE),
            vocab.VIEWED_AT: rules.coerce_ms(data.get(vocab.VIEWED_AT), vocab.VIEWED_AT),
            vocab.DOWNLOADED_AT: rules.coerce_ms(
                data.get(vocab.DOWNLOADED_AT), vocab.DOWNLOADED_AT
            ),
            vocab.DOWNLOAD_TYPE: data.get(vocab.DOWNLOAD_TYPE),
            "anonymous": not str(data.get(vocab.VIEWER_EMAIL) or "").strip(),
            "download": rules.download_of(data),
            vocab.PAGE_DURATIONS: rules.page_durations(data),
            vocab.TOTAL_DURATION_SECONDS: rules.total_duration(data),
            vocab.LOCATION: rules.location_of(data),
            vocab.CLIENT: rules.client_of(data),
            vocab.TIME_UNIT_FIELD: vocab.TIME_UNIT_VALUE,
        }

    # -- clock and cache ----------------------------------------------------- #

    def _now_ms(self) -> int:
        return int(self._now().timestamp() * 1000)

    def _invalidate(self) -> None:
        """Drop the cache after a write.

        A cached aggregate that outlived the write that changed it would be the one
        defect that matters here, because the poller is the documented integration
        path: a poller that reads a stale board for a minute after a view lands is
        exactly the behaviour the cache was introduced to allow.

        The whole cache is dropped rather than one key. A write can change the unique
        viewer count of every room, not only the room it named, and computing which
        keys are affected would be a second place to get the dependency wrong.
        """

        self.clear_cache()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
