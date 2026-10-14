"""The store-facing half of WF-130: generate, review, publish, refresh.

:class:`CollateralBook` holds a :class:`~dsr.store.RecordStore` and nothing else
that talks to persistence. Every write goes through the store, so every write is
audited in the same transaction as the change. This module never opens the
database and never bypasses the audit log.

Three decisions a reviewer should be able to check without reading the whole
file:

**The rules are pure.** The assembly engine, the personalisation and the
readiness rule live in :mod:`.rules`, which takes plain dicts. This module is
the join between those rules and the store.

**An artefact is one record with its content blocks inside it.** The blocks are
ordinary JSON in ``data.sections``, so a generation is atomic and a buyer read
never has to join. A team that adds a field to a block stores it the same day.

**``source`` is required on every write.** A hardcoded source string drifts from
the route that serves the request, and an audit row naming a path the app no
longer serves is worse than no audit row.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from dsr.store import RecordStore

from . import rules, vocabulary as vocab
from .errors import CollateralNotFound, CollateralRefused, CollateralValidationError
from .vocabulary import ROOM_REF

#: A ceiling on rows read for one answer.
MAX_ROWS = 50_000

#: The fields an edit may change on a section. ``key`` addresses the section and
#: is not itself editable.
EDITABLE_SECTION_FIELDS = ("title", "body", "reveal_at_stage", "kind")


class CollateralBook:
    """Generated documents and decks, their source assets and their events."""

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
            raise CollateralNotFound(f"room {room_id!r} not found", room_id=str(room_id or ""))
        return record

    def _collateral_record(self, collateral_id: Any) -> dict[str, Any]:
        record = None
        if isinstance(collateral_id, str) and collateral_id:
            record = self.store.get(collateral_id)
        if record is None or record.get("collection") != vocab.COLLATERAL_COLLECTION:
            raise CollateralNotFound(
                f"collateral {collateral_id!r} not found", collateral_id=str(collateral_id or "")
            )
        return record

    def _view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """The collateral as a caller reads it, with visibility up to date."""
        data = self._data(record)
        deal = dict(data.get("deal") or {})
        stage = str(deal.get("stage") or vocab.DEFAULT_STAGE)
        sections = rules.apply_stage(data.get("sections") or [], stage)
        view = {
            "id": record.get("id"),
            "room_id": data.get(ROOM_REF) or record.get("room_id"),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            "title": data.get("title"),
            "kind": data.get("kind"),
            "state": data.get("state"),
            "engine": data.get("engine"),
            "version": data.get("version", 1),
            "template_key": data.get("template_key"),
            "template_name": data.get("template_name"),
            "deal": deal,
            "stage": stage,
            "brand": dict(data.get("brand") or {}),
            "sections": sections,
            "visible_sections": rules.visible_section_keys(sections),
            "published_at": data.get("published_at"),
            "withdrawn_at": data.get("withdrawn_at"),
            "room_page_id": data.get("room_page_id"),
        }
        view.update(rules.summarise(sections))
        return view

    # -- source assets ------------------------------------------------------ #

    def _asset_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = self._data(record)
        return {
            "id": record.get("id"),
            "key": data.get("key"),
            "kind": data.get("kind"),
            "title": data.get("title"),
            "body": data.get("body"),
            "version": data.get("version", 1),
            "updated_at": record.get("updated_at"),
        }

    def _asset_record(self, asset_id: Any) -> dict[str, Any]:
        record = None
        if isinstance(asset_id, str) and asset_id:
            record = self.store.get(asset_id)
        if record is None or record.get("collection") != vocab.ASSET_COLLECTION:
            raise CollateralNotFound(
                f"source asset {asset_id!r} not found", asset_id=str(asset_id or "")
            )
        return record

    def _asset_map(self) -> dict[str, dict[str, Any]]:
        rows = self.store.find(vocab.ASSET_COLLECTION, {}, limit=MAX_ROWS)
        mapping: dict[str, dict[str, Any]] = {}
        for row in rows:
            data = self._data(row)
            key = str(data.get("key") or "")
            if key:
                mapping[key] = data
        return mapping

    def register_asset(
        self,
        payload: Mapping[str, Any] | None = None,
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Add an entry to the content library: a case study, a deck, pricing."""
        data = dict(payload or {})
        key = str(data.get("key") or "").strip()
        if not key:
            raise CollateralValidationError("a source asset needs a key", field="key")
        if key in self._asset_map():
            raise CollateralRefused(
                f"source asset {key!r} already exists; a new asset needs its own key",
                asset_key=key,
            )
        record = self.store.create(
            vocab.ASSET_COLLECTION,
            {
                "key": key,
                "kind": str(data.get("kind") or "reference").strip() or "reference",
                "title": str(data.get("title") or key).strip() or key,
                "body": str(data.get("body") or ""),
                "version": 1,
            },
            actor=actor,
            source=source,
        )
        return self._asset_view(record)

    def update_asset(
        self,
        asset_id: str,
        patch: Mapping[str, Any] | None = None,
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Change a library asset and bump its version.

        The version is what a synced section records, so a change is visible in
        the artefact that drew from it even before a refresh runs.
        """
        record = self._asset_record(asset_id)
        data = self._data(record)
        update: dict[str, Any] = {}
        for field in ("title", "body", "kind"):
            if field in (patch or {}):
                update[field] = str((patch or {}).get(field) or "")
        update["version"] = int(data.get("version") or 1) + 1
        return self._asset_view(self.store.update(record["id"], update, actor=actor, source=source))

    def list_assets(self) -> list[dict[str, Any]]:
        rows = self.store.find(vocab.ASSET_COLLECTION, {}, limit=MAX_ROWS)
        views = [self._asset_view(row) for row in rows]
        views.sort(key=lambda asset: str(asset.get("key") or ""))
        return views

    # -- events ------------------------------------------------------------- #

    def _record_event(
        self,
        collateral_id: str,
        kind: str,
        detail: Mapping[str, Any],
        *,
        actor: str | None,
        source: str,
        room_ref: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "collateral_id": collateral_id,
            "kind": kind,
            "detail": dict(detail),
            "at": self._stamp(),
        }
        if room_ref:
            payload[ROOM_REF] = room_ref
        return self.store.create(
            vocab.EVENT_COLLECTION, payload, room_id=room_ref, actor=actor, source=source
        )

    def list_events(self, collateral_id: str | None = None) -> list[dict[str, Any]]:
        where: dict[str, Any] = {}
        if collateral_id:
            where["collateral_id"] = collateral_id
        rows = self.store.find(vocab.EVENT_COLLECTION, where, limit=MAX_ROWS)
        events = [self._data(row) for row in rows]
        events.sort(key=lambda event: str(event.get("at") or ""))
        return events

    # -- generation and review --------------------------------------------- #

    def create_collateral(
        self,
        room_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Assemble a personalised artefact for one room from a deal and template.

        The researched flow is a described deal (or a CRM record) assembled into
        a personalised room artefact. There is no CRM read here, so the deal
        arrives in the request; the template is a shipped one or the caller's.
        """
        self.require_room(room_id)
        body = dict(payload or {})
        deal = rules.normalise_deal(self._deal_payload(body))
        template = rules.normalise_template(self._template_payload(body))
        data = rules.assemble(template, deal, self._asset_map(), brand=body.get("brand"))
        if body.get("title"):
            data["title"] = str(body["title"])
        data[ROOM_REF] = str(room_id)
        record = self.store.create(
            vocab.COLLATERAL_COLLECTION, data, room_id=str(room_id), actor=actor, source=source
        )
        self._record_event(
            record["id"],
            vocab.EVENT_GENERATED,
            {"template_key": template["key"], "engine": "rules", "stage": deal["stage"]},
            actor=actor,
            source=source,
            room_ref=str(room_id),
        )
        return self._view(record)

    @staticmethod
    def _deal_payload(body: Mapping[str, Any]) -> dict[str, Any]:
        nested = body.get("deal")
        if isinstance(nested, Mapping):
            return dict(nested)
        keys = ("account", "contact", "product", "stage", "seller", "deal_id")
        return {key: body.get(key) for key in keys if key in body}

    @staticmethod
    def _template_payload(body: Mapping[str, Any]) -> Any:
        if "template" in body:
            return body.get("template")
        if "template_key" in body:
            return body.get("template_key")
        return None

    def update_collateral(
        self,
        collateral_id: str,
        patch: Mapping[str, Any] | None = None,
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Edit a draft artefact: its title, its deal stage, or its sections.

        Moving the deal stage is what reveals a hidden section. The reveal is
        derived from the stored ``reveal_at_stage`` at read time, and this
        method records which sections the move revealed so the event trail shows
        the unhide rather than leaving it implicit.
        """
        record = self._collateral_record(collateral_id)
        data = self._data(record)
        if data.get("state") == "withdrawn":
            raise CollateralRefused(
                "a withdrawn collateral cannot be edited", collateral_id=record["id"]
            )

        update: dict[str, Any] = {}
        if patch and patch.get("title") is not None:
            title = str(patch["title"]).strip()
            if not title:
                raise CollateralValidationError("a title must not be empty", field="title")
            update["title"] = title

        old_deal = dict(data.get("deal") or {})
        reveal_from = str(old_deal.get("stage") or vocab.DEFAULT_STAGE)
        if patch and isinstance(patch.get("deal"), Mapping):
            merged = {**old_deal, **dict(patch["deal"])}
            new_deal = rules.normalise_deal(merged)
            update["deal"] = new_deal
        else:
            new_deal = old_deal
        reveal_to = str(new_deal.get("stage") or vocab.DEFAULT_STAGE)

        sections = [dict(section) for section in data.get("sections") or []]
        if patch and patch.get("sections") is not None:
            sections = self._apply_section_edits(sections, patch.get("sections"))

        if patch and isinstance(patch.get("brand"), Mapping):
            update["brand"] = dict(patch["brand"])

        sections = rules.apply_stage(sections, reveal_to)
        update["sections"] = sections
        update["version"] = int(data.get("version") or 1) + 1
        updated = self.store.update(record["id"], update, actor=actor, source=source)

        revealed = self._newly_revealed(sections, reveal_from, reveal_to)
        self._record_event(
            record["id"],
            vocab.EVENT_EDITED,
            {"stage": reveal_to, "revealed": revealed},
            actor=actor,
            source=source,
            room_ref=str(data.get(ROOM_REF) or record.get("room_id") or "") or None,
        )
        if revealed:
            self._record_event(
                record["id"],
                vocab.EVENT_REVEALED,
                {"from_stage": reveal_from, "to_stage": reveal_to, "sections": revealed},
                actor=actor,
                source=source,
                room_ref=str(data.get(ROOM_REF) or record.get("room_id") or "") or None,
            )
        return self._view(updated)

    @staticmethod
    def _apply_section_edits(sections: list[dict[str, Any]], edits: Any) -> list[dict[str, Any]]:
        if not isinstance(edits, list):
            raise CollateralValidationError("sections must be a list of edits", field="sections")
        by_key = {str(section.get("key")): section for section in sections}
        for edit in edits:
            if not isinstance(edit, Mapping):
                raise CollateralValidationError(
                    "every section edit must be an object", field="sections"
                )
            key = str(edit.get("key") or "").strip()
            target = by_key.get(key)
            if target is None:
                raise CollateralValidationError(
                    f"no section {key!r} on this collateral", field="sections", section=key
                )
            for field in EDITABLE_SECTION_FIELDS:
                if field in edit:
                    target[field] = edit[field]
            if "reveal_at_stage" in edit:
                reveal = edit["reveal_at_stage"]
                if reveal is not None:
                    reveal = str(reveal).strip().lower().replace(" ", "_")
                    if reveal not in vocab.DEAL_STAGES:
                        raise CollateralValidationError(
                            f"unknown deal stage {reveal!r}", field="reveal_at_stage"
                        )
                target["reveal_at_stage"] = reveal
        return sections

    @staticmethod
    def _newly_revealed(
        sections: list[dict[str, Any]], old_stage: str, new_stage: str
    ) -> list[str]:
        revealed: list[str] = []
        for section in sections:
            before = rules.is_revealed(old_stage, section.get("reveal_at_stage"))
            after = rules.is_revealed(new_stage, section.get("reveal_at_stage"))
            if after and not before:
                revealed.append(str(section.get("key")))
        return revealed

    # -- publish, withdraw, refresh ---------------------------------------- #

    def publish(
        self,
        collateral_id: str,
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Make the artefact live in the room.

        An always-visible section with no body refuses the publish, because a
        buyer-facing document with a blank opening is worse than a refusal.
        """
        record = self._collateral_record(collateral_id)
        data = self._data(record)
        if data.get("state") == "withdrawn":
            raise CollateralRefused(
                "a withdrawn collateral cannot be published", collateral_id=record["id"]
            )
        sections = rules.apply_stage(
            data.get("sections") or [], str((data.get("deal") or {}).get("stage"))
        )
        if not sections:
            raise CollateralRefused(
                "the collateral has no sections to publish", collateral_id=record["id"]
            )
        empty = [
            str(section["key"])
            for section in sections
            if section.get("reveal_at_stage") is None and not str(section.get("body") or "").strip()
        ]
        if empty:
            raise CollateralRefused(
                "an always-visible section is empty: " + ", ".join(empty),
                collateral_id=record["id"],
                sections=empty,
            )
        updated = self.store.update(
            record["id"],
            {
                "state": vocab.LIVE_STATE,
                "published_at": self._stamp(),
                "version": int(data.get("version") or 1) + 1,
            },
            actor=actor,
            source=source,
        )
        self._record_event(
            record["id"],
            vocab.EVENT_PUBLISHED,
            {"version": int(data.get("version") or 1) + 1},
            actor=actor,
            source=source,
            room_ref=str(data.get(ROOM_REF) or record.get("room_id") or "") or None,
        )
        return self._view(updated)

    def withdraw(
        self,
        collateral_id: str,
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Take the artefact back out of the room. It accepts no further edit."""
        record = self._collateral_record(collateral_id)
        data = self._data(record)
        updated = self.store.update(
            record["id"],
            {"state": "withdrawn", "withdrawn_at": self._stamp()},
            actor=actor,
            source=source,
        )
        self._record_event(
            record["id"],
            vocab.EVENT_WITHDRAWN,
            {},
            actor=actor,
            source=source,
            room_ref=str(data.get(ROOM_REF) or record.get("room_id") or "") or None,
        )
        return self._view(updated)

    def attach_page(
        self,
        collateral_id: str,
        page_id: str,
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Remember the room page the publishing seam wrote for this artefact."""
        record = self._collateral_record(collateral_id)
        data = self._data(record)
        updated = self.store.update(
            record["id"], {"room_page_id": page_id}, actor=actor, source=source
        )
        self._record_event(
            record["id"],
            vocab.EVENT_PAGE_PUBLISHED,
            {"page_id": page_id},
            actor=actor,
            source=source,
            room_ref=str(data.get(ROOM_REF) or record.get("room_id") or "") or None,
        )
        return self._view(updated)

    def refresh_from_asset(
        self,
        asset_id: str,
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Push a changed source asset across every live artefact that uses it.

        The evidence's own words are "push content changes ... across all active
        deals instantly". Active means published, not withdrawn, so a withdrawn
        artefact is left alone. A synced section re-renders from its template
        body and the asset's current version, so an edit to a *synced* section's
        body is replaced by the asset on refresh - which is the point of a
        synced section.
        """
        asset_record = self._asset_record(asset_id)
        asset = self._data(asset_record)
        asset_key = str(asset.get("key") or "")
        refreshed: list[str] = []

        for record in self.store.find(
            vocab.COLLATERAL_COLLECTION, {"state": vocab.LIVE_STATE}, limit=MAX_ROWS
        ):
            data = self._data(record)
            sections = [dict(section) for section in data.get("sections") or []]
            if not any(
                section.get("synced") and str(section.get("asset_ref")) == asset_key
                for section in sections
            ):
                continue
            deal = dict(data.get("deal") or {})
            rendered: list[dict[str, Any]] = []
            for section in sections:
                if section.get("synced") and str(section.get("asset_ref")) == asset_key:
                    spec = {
                        "key": section.get("key"),
                        "title": section.get("title"),
                        "kind": section.get("kind"),
                        "body": section.get("template_body") or section.get("body"),
                        "reveal_at_stage": section.get("reveal_at_stage"),
                        "asset_ref": asset_key,
                        "synced": True,
                    }
                    section = rules.render_section(spec, deal, asset)
                rendered.append(section)
            rendered = rules.apply_stage(rendered, str(deal.get("stage") or vocab.DEFAULT_STAGE))
            room_ref = str(data.get(ROOM_REF) or record.get("room_id") or "") or None
            self.store.update(
                record["id"],
                {
                    "sections": rendered,
                    "version": int(data.get("version") or 1) + 1,
                    **rules.summarise(rendered),
                },
                actor=actor,
                source=source,
            )
            self._record_event(
                record["id"],
                vocab.EVENT_REFRESHED,
                {"asset_key": asset_key, "asset_version": asset.get("version")},
                actor=actor,
                source=source,
                room_ref=room_ref,
            )
            refreshed.append(str(record["id"]))

        return {
            "asset_id": asset_record["id"],
            "asset_key": asset_key,
            "version": asset.get("version"),
            "refreshed": len(refreshed),
            "collateral_ids": refreshed,
        }

    # -- reads -------------------------------------------------------------- #

    def read(self, collateral_id: str) -> dict[str, Any]:
        return self._view(self._collateral_record(collateral_id))

    def list_collaterals(self, room_id: str | None = None) -> list[dict[str, Any]]:
        if room_id:
            rows = self.store.find(vocab.COLLATERAL_COLLECTION, {ROOM_REF: room_id}, limit=MAX_ROWS)
        else:
            rows = self.store.list(vocab.COLLATERAL_COLLECTION, limit=MAX_ROWS)
        views = [self._view(row) for row in rows]
        views.sort(
            key=lambda item: (str(item.get("created_at") or ""), str(item["id"])), reverse=True
        )
        return views

    def summary(self, room_id: str | None = None) -> dict[str, Any]:
        collaterals = self.list_collaterals(room_id)
        assets = self.list_assets()
        events = self.list_events()
        return {
            "room_id": room_id,
            "collaterals": len(collaterals),
            "by_kind": {
                kind: sum(1 for item in collaterals if item["kind"] == kind) for kind in vocab.KINDS
            },
            "draft": sum(1 for item in collaterals if item["state"] == "draft"),
            "published": sum(1 for item in collaterals if item["state"] == "published"),
            "withdrawn": sum(1 for item in collaterals if item["state"] == "withdrawn"),
            "visible_sections": sum(int(item.get("visible_count") or 0) for item in collaterals),
            "hidden_sections": sum(int(item.get("hidden_count") or 0) for item in collaterals),
            "synced_sections": sum(int(item.get("synced_count") or 0) for item in collaterals),
            "assets": len(assets),
            "events": len(events),
            "generated_at": self._stamp(),
        }

    # -- the published contract --------------------------------------------- #

    @staticmethod
    def vocabulary() -> dict[str, Any]:
        return vocab.describe()

    @staticmethod
    def decisions() -> dict[str, Any]:
        return {
            "ticket": "WF-130",
            "decisions": dict(vocab.DECISIONS),
            "not_implemented": list(vocab.NOT_IMPLEMENTED),
        }


__all__ = ["EDITABLE_SECTION_FIELDS", "MAX_ROWS", "CollateralBook"]
