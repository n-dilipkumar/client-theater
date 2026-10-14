"""The store-facing half of WF-071: write the config, read it back with the link,
and stamp one view with the values bound to it.

:class:`WatermarkBook` holds a :class:`~dsr.store.RecordStore` and nothing else
that talks to persistence. Every write goes through the store, so every write is
audited in the same transaction as the change. This module never opens the
database and never bypasses the audit log.

Four decisions a reviewer should be able to check without reading the whole file:

**The link is WF-121's.** :meth:`~dsr.tracked_deal_link.TrackedLinkBook.resolve_link`
resolves the reference, so a config is keyed by a real tracked link and a request
that names one that does not exist is refused. The link's own ``page_count`` is
what says how many pages the overlay reaches.

**The viewer is WF-121's rule.** A stamp carries the viewer through WF-121's
``require_viewer`` - WF-069's ``normalize_email`` - and a link that records who
viewed refuses a stamp with no viewer with WF-121's own ``ViewerRequired`` 428.
Re-voicing either would let the watermark name a viewer the analytics do not.

**The config lives in its own collection.** :meth:`read`, the enable path and the
patch path all join it onto the link, so the response always carries
``watermark_config`` or ``null`` exactly as the research describes - without
writing onto the link row, which WF-121's own ``update_link`` would re-normalise
and strip. The reason is recorded in :mod:`.inferences`.

**``source`` is required on every write.** A hardcoded source string drifts from
the route that serves the request, and an audit row naming a path the app no
longer serves is worse than no audit row.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from dsr.store import RecordStore
from dsr.tracked_deal_link import LINK_COLLECTION, TrackedLinkBook, rules as link_rules
from dsr.tracked_deal_link.errors import LinkNotFound as TrackedLinkNotFound, ViewerRequired

from . import inferences as inference_register, rules, vocabulary as vocab
from .errors import WatermarkLinkNotFound

#: A ceiling on rows read for one answer, for the same reason WF-121 has one: one
#: request must not scan a pathological collection.
MAX_ROWS = 20_000


class WatermarkBook:
    """The watermark config on a tracked link, and the stamp a view produces."""

    def __init__(
        self,
        store: RecordStore,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.links = TrackedLinkBook(store, now=now)
        self._now = now or rules.now_utc

    # -- helpers ------------------------------------------------------------ #

    def _stamp(self) -> str:
        return self._now().isoformat(timespec="milliseconds")

    @staticmethod
    def _data(record: Mapping[str, Any]) -> dict[str, Any]:
        return dict(record.get("data") or {})

    def _link(self, reference: Any) -> dict[str, Any]:
        """The WF-121 link behind a reference, or :class:`WatermarkLinkNotFound`."""
        try:
            return self.links.resolve_link(reference)
        except TrackedLinkNotFound as exc:
            raise WatermarkLinkNotFound(
                f"tracked link {reference!r} not found", link_id=str(reference or "")
            ) from exc

    def _config_row(self, link_id: Any) -> dict[str, Any] | None:
        rows = self.store.find(vocab.COLLECTION, {vocab.LINK_REF: str(link_id)}, limit=2)
        return rows[0] if rows else None

    def _link_summary(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = self._data(record)
        status = link_rules.link_state(record, now=self._now())
        return {
            "id": record.get("id"),
            "room_id": data.get(vocab.ROOM_REF) or record.get("room_id"),
            "title": data.get("title"),
            "asset_id": data.get("asset_id"),
            "asset_name": data.get("asset_name"),
            "page_count": data.get("page_count"),
            "share_url": data.get("share_url"),
            "require_viewer": bool(data.get("require_viewer", True)),
            "state": status["state"],
            "closed": status["closed"],
        }

    def _config_view(self, link_id: Any) -> dict[str, Any]:
        row = self._config_row(link_id)
        if row is None:
            return {
                "config_id": None,
                "enable_watermark": False,
                "watermark_config": None,
                "updated_at": None,
            }
        data = self._data(row)
        return {
            "config_id": row.get("id"),
            "enable_watermark": bool(data.get(vocab.ENABLE_FIELD)),
            "watermark_config": data.get(vocab.CONFIG_FIELD),
            "updated_at": row.get("updated_at"),
        }

    def _write_config(
        self,
        link_id: Any,
        *,
        room_ref: Any,
        enable: bool,
        config: Mapping[str, Any] | None,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        row = self._config_row(link_id)
        payload = {
            vocab.LINK_REF: str(link_id),
            vocab.ROOM_REF: room_ref,
            vocab.ENABLE_FIELD: bool(enable),
            vocab.CONFIG_FIELD: dict(config) if config is not None else None,
        }
        if row is None:
            created = self.store.create(
                vocab.COLLECTION, payload, room_id=room_ref, actor=actor, source=source
            )
            return self._config_view(link_id) | {"created": True, "revision": created["revision"]}
        updated = self.store.update(row["id"], payload, actor=actor, source=source)
        return self._config_view(link_id) | {"created": False, "revision": updated["revision"]}

    # -- reads -------------------------------------------------------------- #

    def read(self, link_id: Any) -> dict[str, Any]:
        """The link and its watermark settings, whether or not one is set.

        The researched ``GET /v1/links/{id}`` "always includes ``watermark_config``
        (or ``null``) so you can render the current settings back to the user", so
        ``watermark_config`` is always a key here and never absent.
        """
        record = self._link(link_id)
        return {"link": self._link_summary(record), **self._config_view(record["id"])}

    def list_links(self, room_id: str | None = None) -> list[dict[str, Any]]:
        """Every tracked link in scope, each with its watermark settings joined."""
        if room_id:
            rows = self.store.find(LINK_COLLECTION, {vocab.ROOM_REF: room_id}, limit=MAX_ROWS)
        else:
            rows = self.store.list(LINK_COLLECTION, limit=MAX_ROWS)
        rows.sort(key=lambda row: (str(row.get("created_at") or ""), str(row["id"])), reverse=True)
        return [{"link": self._link_summary(row), **self._config_view(row["id"])} for row in rows]

    def stamps(self, link_id: Any, *, limit: int = 100) -> list[dict[str, Any]]:
        record = self._link(link_id)
        rows = self.store.find(vocab.STAMP_COLLECTION, {vocab.LINK_REF: record["id"]}, limit=limit)
        stamps = [{"id": row["id"], **self._data(row)} for row in rows]
        stamps.sort(key=lambda row: str(row.get("stamped_at") or ""), reverse=True)
        return stamps

    # -- authoring ---------------------------------------------------------- #

    def enable(
        self,
        link_id: Any,
        payload: Mapping[str, Any] | None = None,
        *,
        actor: str | None = None,
        source: str,
        plan: str | None = None,
    ) -> dict[str, Any]:
        """Turn the watermark on and set its config.

        The plan gate is checked only when this request flips the toggle from off
        to on, which is the researched rule: "existing links keep working after a
        downgrade and only re-trigger the gate when a request flips the toggle from
        off to on."
        """
        record = self._link(link_id)
        current = self._config_view(record["id"])
        if not current["enable_watermark"]:
            rules.require_plan(plan)
        body = dict(payload or {})
        if vocab.CONFIG_FIELD in body and isinstance(body[vocab.CONFIG_FIELD], Mapping):
            raw_config: Mapping[str, Any] | None = body[vocab.CONFIG_FIELD]
        else:
            raw_config = {key: value for key, value in body.items() if key != vocab.ENABLE_FIELD}
        if not raw_config and current["watermark_config"] is not None:
            # Turning the watermark back on with no config restores the one that was
            # kept when it was turned off, rather than resetting it to the documented
            # defaults and silently discarding the rep's text.
            config = dict(current["watermark_config"])
        else:
            config = rules.normalise_config(raw_config or {})
        room_ref = self._data(record).get(vocab.ROOM_REF) or record.get("room_id")
        return {
            "link": self._link_summary(record),
            **self._write_config(
                record["id"],
                room_ref=room_ref,
                enable=True,
                config=config,
                actor=actor,
                source=source,
            ),
        }

    def patch(
        self,
        link_id: Any,
        payload: Mapping[str, Any] | None = None,
        *,
        actor: str | None = None,
        source: str,
        plan: str | None = None,
    ) -> dict[str, Any]:
        """Replace the config, or clear it without flipping the toggle.

        ``{"watermark_config": null}`` clears the config and leaves the toggle on,
        which is the researched PATCH: "replace the config, or send
        ``{"watermark_config": null}`` to clear it without flipping the toggle." A
        patch that arrives before any config exists is an enable, so it goes
        through the same plan gate.
        """
        record = self._link(link_id)
        current = self._config_view(record["id"])
        if current["config_id"] is None:
            return self.enable(link_id, payload, actor=actor, source=source, plan=plan)

        body = dict(payload or {})
        clearing = vocab.CONFIG_FIELD in body and body[vocab.CONFIG_FIELD] is None
        if clearing:
            config: Mapping[str, Any] | None = None
        elif vocab.CONFIG_FIELD in body and isinstance(body[vocab.CONFIG_FIELD], Mapping):
            config = rules.normalise_config(body[vocab.CONFIG_FIELD])
        else:
            incoming = {key: value for key, value in body.items() if key != vocab.ENABLE_FIELD}
            config = rules.normalise_config({**(current["watermark_config"] or {}), **incoming})

        room_ref = self._data(record).get(vocab.ROOM_REF) or record.get("room_id")
        return {
            "link": self._link_summary(record),
            **self._write_config(
                record["id"],
                room_ref=room_ref,
                enable=True,
                config=config,
                actor=actor,
                source=source,
            ),
            "cleared": clearing,
        }

    def disable(self, link_id: Any, *, actor: str | None = None, source: str) -> dict[str, Any]:
        """Turn the watermark off. The config is kept, so re-enabling restores it."""
        record = self._link(link_id)
        row = self._config_row(record["id"])
        if row is None:
            return {"link": self._link_summary(record), **self._config_view(record["id"])}
        room_ref = self._data(record).get(vocab.ROOM_REF) or record.get("room_id")
        return {
            "link": self._link_summary(record),
            **self._write_config(
                record["id"],
                room_ref=room_ref,
                enable=False,
                config=self._config_view(record["id"])["watermark_config"],
                actor=actor,
                source=source,
            ),
        }

    # -- the per-view render ------------------------------------------------ #

    def stamp(
        self,
        link_id: Any,
        payload: Mapping[str, Any] | None = None,
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Composite one view's watermark onto every page, and record the view.

        The order is the researched data flow: the viewer opens the link, the
        tokens are interpolated *for that view*, the overlay is composited onto the
        rasterised pages, and - this build's addition, so the trace is auditable -
        the rendered overlay is recorded as one stamp row. Nothing is written into
        a document, and the link's own ``page_count`` says how far the overlay
        reaches.
        """
        record = self._link(link_id)
        link_rules.require_open(record, now=self._now())
        settings = self._config_view(record["id"])
        if not settings["enable_watermark"] or settings["watermark_config"] is None:
            return {
                "link_id": record["id"],
                "room_id": self._data(record).get(vocab.ROOM_REF) or record.get("room_id"),
                "enable_watermark": False,
                "stamped": False,
                "rendered_text": None,
                "overlay": None,
                "pages": [],
                "page_count": 0,
                "unresolved_tokens": [],
                "stamp_id": None,
            }

        data = self._data(record)
        # WF-121's own privacy boundary, reused: a stamp is page geometry and an
        # identity, never a document body. A payload carrying one is refused by
        # name here exactly as WF-121 refuses it on a view.
        link_rules.refuse_document_bodies(payload or {})
        view = rules.normalise_view(payload, now=self._now())
        if not view["viewer"] and data.get("require_viewer", True):
            raise ViewerRequired(
                "this tracked link records who viewed, so a watermark stamp needs a viewer",
                link_id=record["id"],
            )
        view["share_url"] = view["share_url"] or data.get("share_url")
        page_count = view["page_count"] or int(data.get("page_count") or 0)
        rendered = rules.render_pages(settings["watermark_config"], view, page_count=page_count)
        room_ref = data.get(vocab.ROOM_REF) or record.get("room_id")
        stamp_row = self.store.create(
            vocab.STAMP_COLLECTION,
            {
                vocab.LINK_REF: record["id"],
                vocab.ROOM_REF: room_ref,
                "link_title": data.get("title"),
                "viewer": view["viewer"],
                "ip_address": view["ip_address"],
                "rendered_text": rendered["rendered_text"],
                "unresolved_tokens": rendered["unresolved_tokens"],
                "page_count": rendered["page_count"],
                "is_tiled": bool(settings["watermark_config"].get("is_tiled")),
                "position": settings["watermark_config"].get("position"),
                "rotation": settings["watermark_config"].get("rotation"),
                "stamped_at": view["occurred_at"].isoformat(),
            },
            room_id=room_ref,
            actor=actor,
            source=source,
        )
        return {
            "link_id": record["id"],
            "room_id": room_ref,
            "enable_watermark": True,
            "stamped": True,
            "stamp_id": stamp_row["id"],
            **rendered,
        }

    def preview(
        self, payload: Mapping[str, Any] | None = None, *, now: datetime | None = None
    ) -> dict[str, Any]:
        """Render a sample overlay from a config with no write.

        The panel uses this so a rep sees the substituted text - with the tokens a
        real view would fill - before saving, and the same pure rules render the
        real view, so the preview cannot disagree with delivery.
        """
        body = dict(payload or {})
        raw_config = (
            body.get(vocab.CONFIG_FIELD)
            if isinstance(body.get(vocab.CONFIG_FIELD), Mapping)
            else body
        )
        config = rules.normalise_config(raw_config or {})
        view = rules.normalise_view(
            {
                k: v
                for k, v in body.items()
                if k in ("viewer", "email", "ip_address", "ip", "share_url", "page_count")
            },
            now=now or self._now(),
        )
        page_count = view["page_count"] or 1
        return {"config": config, **rules.render_pages(config, view, page_count=page_count)}

    # -- the published contract --------------------------------------------- #

    def summary(self, room_id: str | None = None) -> dict[str, Any]:
        where = {vocab.ROOM_REF: room_id} if room_id else {}
        links = self.list_links(room_id)
        configs = self.store.find(vocab.COLLECTION, where, limit=MAX_ROWS)
        stamps = self.store.find(vocab.STAMP_COLLECTION, where, limit=MAX_ROWS)
        enabled = [row for row in configs if row["data"].get(vocab.ENABLE_FIELD)]
        return {
            "room_id": room_id,
            "links": len(links),
            "watermarked": sum(1 for link in links if link["enable_watermark"]),
            "tiled": sum(
                1
                for link in links
                if link["enable_watermark"] and (link["watermark_config"] or {}).get("is_tiled")
            ),
            "cleared": sum(1 for row in enabled if row["data"].get(vocab.CONFIG_FIELD) is None),
            "stamps": len(stamps),
            "viewers_stamped": len(
                {str(row["data"].get("viewer")) for row in stamps if row["data"].get("viewer")}
            ),
            "generated_at": self._stamp(),
        }

    @staticmethod
    def vocabulary() -> dict[str, Any]:
        return vocab.describe()

    @staticmethod
    def inferences() -> dict[str, Any]:
        return inference_register.describe()


__all__ = ["MAX_ROWS", "WatermarkBook"]
