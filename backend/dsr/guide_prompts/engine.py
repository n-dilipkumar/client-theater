"""WF-118's service over the schema-flexible record store.

The only module in ``dsr.guide_prompts`` that touches a store. It reads and
writes through :class:`dsr.store.RecordStore`, so every write lands in
:class:`dsr.db.audited.AuditedDatabase` and gets its audit row in the same
transaction. The caller passes the audit ``source``, which the HTTP layer builds
from the route that actually served the request, so the audit row always names a
route the app serves.

The rules themselves live in :mod:`dsr.guide_prompts.rules` and take plain data
and an explicit moment. This module is the part that persists rows and freezes
nothing: a caller that wants a deterministic answer passes ``at``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from dsr.store import RecordStore

from . import rules, vocabulary as vocab
from .errors import (
    GuideInvalid,
    GuideNotFound,
    KeyImmutable,
    KeyInvalid,
    KeyTaken,
    OrderInvalid,
)

GUIDE_IMMUTABLE = ("key",)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _moment(value: Any) -> datetime:
    parsed = rules.parse_time(value)
    return parsed if parsed is not None else _now()


class GuidePrompts:
    """The ordering, throttling and repeat workflow over one record store."""

    def __init__(self, store: RecordStore) -> None:
        self.store = store

    # ------------------------------------------------------------------ #
    # Rows and views
    # ------------------------------------------------------------------ #

    def _guide_record(self, key: str, *, include_deleted: bool = False) -> dict[str, Any] | None:
        rows = self.store.find(vocab.GUIDES, {"key": key}, limit=1, include_deleted=include_deleted)
        return rows[0] if rows else None

    def guide_view(self, record: dict[str, Any]) -> dict[str, Any]:
        """A guide record plus every field derived from its activation."""
        data = dict(record.get("data") or {})
        activation = rules.normalise_activation(data.get("activation"))
        view = dict(data)
        view.update(
            {
                "id": record.get("id"),
                "collection": record.get("collection", vocab.GUIDES),
                "revision": record.get("revision"),
                "created_at": record.get("created_at"),
                "updated_at": record.get("updated_at"),
                "deleted_at": record.get("deleted_at"),
                "deleted": record.get("deleted_at") is not None,
                "status": data.get("status") or vocab.STATUS_STAGING,
                "activation": activation,
                "stop_after_label": rules.stop_after_label(activation),
                "issues": rules.guide_errors(data),
            }
        )
        return view

    def require_guide(self, key: str, *, include_deleted: bool = False) -> dict[str, Any]:
        record = self._guide_record(key, include_deleted=include_deleted)
        if record is None or record.get("collection") != vocab.GUIDES:
            raise GuideNotFound(f"no guide with key {key!r}")
        return record

    def require_live_guide(self, key: str) -> dict[str, Any]:
        """A guide that is not soft-deleted, for the order list and events."""
        rows = self.store.find(vocab.GUIDES, {"key": key}, limit=1)
        if not rows:
            raise GuideNotFound(f"no live guide with key {key!r}")
        return rows[0]

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #

    def guides(
        self,
        *,
        status: str | None = None,
        q: str | None = None,
        product_area: str | None = None,
        category: str | None = None,
        page_location: str | None = None,
        include_deleted: bool = False,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Every guide, newest first, filtered in memory over the small set."""
        records = self.store.list(vocab.GUIDES, limit=1000, include_deleted=include_deleted)
        views = [self.guide_view(record) for record in records]
        needle = (q or "").strip().lower()
        wanted_status = (status or "").strip() or None
        selected = []
        for view in views:
            if wanted_status and view["status"] != wanted_status:
                continue
            if product_area and view.get("product_area") != product_area:
                continue
            if category and view.get("category") != category:
                continue
            if page_location and view.get("page_location") != page_location:
                continue
            if needle and needle not in f"{view.get('name', '')} {view['key']}".lower():
                continue
            selected.append(view)
        return selected[:limit]

    def summary(self) -> dict[str, Any]:
        """Counts the ordering page leads with."""
        records = self.store.list(vocab.GUIDES, limit=1000)
        views = [self.guide_view(record) for record in records]
        by_status: dict[str, int] = {}
        for view in views:
            by_status[view["status"]] = by_status.get(view["status"], 0) + 1
        orders = self.store.list(vocab.ORDERS, limit=1000)
        sessions = self.store.list(vocab.SESSIONS, limit=1000)
        events = self.store.list(vocab.EVENTS, limit=1000)
        return {
            "total": len(views),
            "unthrottled": sum(1 for v in views if v["activation"]["ignore_throttling"]),
            "repeating": sum(1 for v in views if v["activation"]["repeat"]["enabled"]),
            "by_status": by_status,
            "applications": len(orders),
            "sessions": len(sessions),
            "events": len(events),
            "deleted": len(self.store.list(vocab.GUIDES, limit=1000, include_deleted=True))
            - len(views),
        }

    # ------------------------------------------------------------------ #
    # Authoring
    # ------------------------------------------------------------------ #

    def create(
        self,
        *,
        key: Any,
        name: Any,
        actor: str,
        source: str,
        fields: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Author a guide in the staging state."""
        key_issue = rules.key_problem(key)
        if key_issue:
            raise KeyInvalid(key_issue)
        if self._guide_record(str(key), include_deleted=True):
            raise KeyTaken(f"the key {key!r} is already in use by a guide")
        data: dict[str, Any] = {"key": key, "name": name}
        data.update(fields or {})
        data.setdefault("status", vocab.STATUS_STAGING)
        problems = rules.guide_errors(data)
        if problems:
            raise GuideInvalid(problems)
        record = self.store.create(vocab.GUIDES, data, actor=actor, source=source)
        return self.guide_view(record)

    def update(self, key: str, patch: dict[str, Any], *, actor: str, source: str) -> dict[str, Any]:
        """Merge an edit into a guide. The key is immutable."""
        record = self.require_guide(key)
        data = dict(record.get("data") or {})
        for field in GUIDE_IMMUTABLE:
            if field in patch and patch[field] != data.get(field):
                raise KeyImmutable(f"the {field} is immutable and cannot be changed")
        candidate = {**data, **patch}
        problems = rules.guide_errors(candidate)
        if problems:
            raise GuideInvalid(problems)
        updated = self.store.update(str(record["id"]), candidate, actor=actor, source=source)
        return self.guide_view(updated)

    def delete(self, key: str, *, actor: str, source: str) -> dict[str, Any]:
        record = self.require_guide(key)
        self.store.delete(str(record["id"]), actor=actor, source=source)
        return self.guide_view(self.require_guide(key, include_deleted=True))

    def restore(self, key: str, *, actor: str, source: str) -> dict[str, Any]:
        record = self.require_guide(key, include_deleted=True)
        if record.get("deleted_at") is None:
            raise GuideInvalid([f"guide {key!r} is not deleted"])
        updated = self.store.restore(str(record["id"]), actor=actor, source=source)
        return self.guide_view(updated)

    # ------------------------------------------------------------------ #
    # Ordering and throttling
    # ------------------------------------------------------------------ #

    def _order_record(self, app_id: str) -> dict[str, Any] | None:
        rows = self.store.find(vocab.ORDERS, {"app_id": app_id}, limit=1)
        return rows[0] if rows else None

    def order_view(self, app_id: str) -> dict[str, Any]:
        """The ordering document for an application, with defaults when absent."""
        record = self._order_record(app_id)
        if record is None:
            return {
                "id": None,
                "app_id": app_id,
                "guide_keys": [],
                "throttling": {"enabled": False, "count": 1, "unit": "day"},
                "configured": False,
                "revision": None,
            }
        data = dict(record.get("data") or {})
        throttle = dict(data.get("throttling") or {})
        throttle.setdefault("enabled", False)
        throttle.setdefault("count", 1)
        throttle.setdefault("unit", "day")
        return {
            "id": record.get("id"),
            "app_id": app_id,
            "guide_keys": list(data.get("guide_keys") or []),
            "throttling": throttle,
            "configured": True,
            "revision": record.get("revision"),
            "updated_at": record.get("updated_at"),
        }

    def set_order(
        self,
        app_id: str,
        *,
        guide_keys: Any,
        throttling: dict[str, Any] | None,
        actor: str,
        source: str,
    ) -> dict[str, Any]:
        """Replace an application's order list and throttle in one write."""
        known = [str(g["data"]["key"]) for g in self.store.list(vocab.GUIDES, limit=1000)]
        problems = rules.order_errors(guide_keys, throttling, known)
        if problems:
            raise OrderInvalid(problems)
        throttle = dict(throttling or {})
        throttle.setdefault("enabled", False)
        throttle.setdefault("count", 1)
        throttle.setdefault("unit", "day")
        payload = {"app_id": app_id, "guide_keys": list(guide_keys), "throttling": throttle}
        record = self._order_record(app_id)
        if record is None:
            self.store.create(vocab.ORDERS, payload, actor=actor, source=source)
            return self.order_view(app_id)
        self.store.update(str(record["id"]), payload, actor=actor, source=source)
        return self.order_view(app_id)

    def move_guide(
        self, app_id: str, key: str, direction: str, *, actor: str, source: str
    ) -> dict[str, Any]:
        """Apply one row arrow to the order list."""
        view = self.order_view(app_id)
        keys = rules.move(view["guide_keys"], key, direction)
        return self.set_order(
            app_id,
            guide_keys=keys,
            throttling=view["throttling"],
            actor=actor,
            source=source,
        )

    # ------------------------------------------------------------------ #
    # Sessions and events
    # ------------------------------------------------------------------ #

    def _session_record(self, app_id: str, user_id: str) -> dict[str, Any] | None:
        rows = self.store.find(vocab.SESSIONS, {"app_id": app_id, "user_id": user_id}, limit=1)
        return rows[0] if rows else None

    def session_view(self, app_id: str, user_id: str) -> dict[str, Any]:
        record = self._session_record(app_id, user_id)
        if record is None:
            return {
                "app_id": app_id,
                "user_id": user_id,
                "configured": False,
                **rules.default_session(app_id, user_id, None),
            }
        return {"id": record.get("id"), "configured": True, **dict(record.get("data") or {})}

    def _write_session(
        self, app_id: str, user_id: str, state: dict[str, Any], *, actor: str, source: str
    ) -> dict[str, Any]:
        record = self._session_record(app_id, user_id)
        if record is None:
            created = self.store.create(vocab.SESSIONS, state, actor=actor, source=source)
            return dict(created.get("data") or {})
        updated = self.store.update(str(record["id"]), state, actor=actor, source=source)
        return dict(updated.get("data") or {})

    def record_event(
        self,
        app_id: str,
        user_id: str,
        *,
        event: Any,
        key: Any,
        at: Any,
        cause: str | None = None,
        programmatic: bool = False,
        actor: str,
        source: str,
    ) -> dict[str, Any]:
        """Record one interaction and advance the clocks it restarts.

        A ``shown`` event starts nothing. A dismissal, a step advance or a
        completion restarts the per-guide repeat clock and the app-level throttle
        clock, and clears the active guide so the queue can move on.
        """
        event_name = rules.validate_event(event)
        guide = self.require_live_guide(str(key))
        moment = _moment(at)
        session = self.session_view(app_id, user_id)
        session.pop("configured", None)
        session.pop("id", None)
        if event_name == "shown":
            state = rules.apply_shown(session, str(key), moment, programmatic=programmatic)
        else:
            state = rules.apply_dismissal(session, str(key), event_name, moment)
        persisted = self._write_session(app_id, user_id, state, actor=actor, source=source)
        self.store.create(
            vocab.EVENTS,
            {
                "app_id": app_id,
                "user_id": user_id,
                "key": str(key),
                "event": event_name,
                "cause": cause,
                "programmatic": bool(programmatic),
                "at": rules.to_iso(moment),
            },
            actor=actor,
            source=source,
        )
        return {
            "event": event_name,
            "guide": guide["data"].get("name") or str(key),
            "key": str(key),
            "at": rules.to_iso(moment),
            "programmatic": bool(programmatic),
            "restarted_clocks": rules.is_dismissal(event_name),
            "session": persisted,
        }

    def ranking(self, app_id: str, user_id: str, at: Any) -> dict[str, Any]:
        """The two-tier order list with each guide's eligibility at ``at``."""
        guides = [record["data"] for record in self.store.list(vocab.GUIDES, limit=1000)]
        order = self.order_view(app_id)
        session = self.session_view(app_id, user_id)
        views = rules.ranking(guides, order, session, _moment(at))
        return {
            "app_id": app_id,
            "user_id": user_id,
            "order": order,
            "ranking": views,
            "at": rules.to_iso(_moment(at)),
        }

    def next_guide(self, app_id: str, user_id: str, at: Any) -> dict[str, Any]:
        """The next automatic guide to launch, or why none can."""
        guides = [record["data"] for record in self.store.list(vocab.GUIDES, limit=1000)]
        order = self.order_view(app_id)
        session = self.session_view(app_id, user_id)
        return rules.select_next(guides, order, session, _moment(at))


def seed(db: Any, context: dict[str, Any]) -> str:
    """Demo data for WF-118: the states the ordering page has to show.

    Seeded states: a published guide that repeats every day and stops after three
    dismissals; a guide that ignores throttling and so sorts first; a public guide
    with repeat off; a staging draft; an application order list whose throttle is
    one guide every day; and a visitor who has already dismissed one guide, so the
    throttle countdown is visibly running.

    Returns a plain ASCII summary, because a non-encodable character here once
    broke the whole Windows seeder.
    """
    engine = GuidePrompts(RecordStore(db))
    if engine.guides(limit=1):
        return "already seeded"

    def author(key, name, status, activation, **extra):
        fields = {
            "status": status,
            "activation": activation,
            "product_area": extra.pop("product_area", "Sales room"),
            "category": extra.pop("category", "Onboarding"),
            "page_location": extra.pop("page_location", "Home"),
        }
        fields.update(extra)
        return engine.create(key=key, name=name, actor="seed", source="seed", fields=fields)

    author(
        "welcome_tour",
        "Welcome tour",
        vocab.STATUS_PUBLIC,
        {
            "repeat": {"enabled": True, "every": 1, "unit": "day"},
            "stop_after": {"mode": "count", "count": 3},
            "ignore_throttling": False,
            "delay_seconds": 5,
        },
    )
    author(
        "critical_security_notice",
        "Critical security notice",
        vocab.STATUS_PUBLIC,
        {
            "repeat": {"enabled": True, "every": 6, "unit": "hour"},
            "stop_after": {"mode": "never", "count": 2},
            "ignore_throttling": True,
            "delay_seconds": 0,
        },
        category="Security",
    )
    author(
        "pricing_nudge",
        "Pricing nudge",
        vocab.STATUS_PUBLIC,
        {
            "repeat": {"enabled": False, "every": 1, "unit": "day"},
            "stop_after": {"mode": "count", "count": 2},
            "ignore_throttling": False,
            "delay_seconds": 45,
        },
        page_location="Pricing",
    )
    author(
        "draft_survey",
        "Draft survey prompt",
        vocab.STATUS_STAGING,
        {
            "repeat": {"enabled": False, "every": 1, "unit": "hour"},
            "stop_after": {"mode": "never", "count": 2},
            "ignore_throttling": False,
            "delay_seconds": 0,
        },
        category="Feedback",
    )

    order = engine.set_order(
        "demo-app",
        guide_keys=[
            "welcome_tour",
            "critical_security_notice",
            "pricing_nudge",
            "draft_survey",
        ],
        throttling={"enabled": True, "count": 1, "unit": "day"},
        actor="seed",
        source="seed",
    )
    del order

    now = context.get("now") or _now()
    engine.record_event(
        "demo-app",
        "dana",
        event="shown",
        key="welcome_tour",
        at=now,
        actor="seed",
        source="seed",
    )
    engine.record_event(
        "demo-app",
        "dana",
        event="dismissed",
        key="welcome_tour",
        at=now,
        cause="close",
        actor="seed",
        source="seed",
    )
    return "4 guides, 1 order list, 1 visitor with a running throttle"


__all__ = ["GuidePrompts", "seed"]
