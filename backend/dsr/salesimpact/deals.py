"""The CRM deal mirror: the records this workflow reads and the ones it writes.

A **build**, so the design question this module answers is where a CRM opportunity
lives. Jev decided it (``choose_approach``, audit id
``jev-20260927T062748-26392-68603``, ``feature_owned_crm_deal_collection`` at confidence
1.00) and the reason is in ``docs/research/digital-sales-room-workflows/wf/WF-023-design.md``
§2. The short version: the researched close rate is a fraction over closed-won and
closed-lost stages, ``room.stage`` in this product is a lifecycle label written by other
features, and the researched completeness rule needs somewhere to record a CRM deal id at
all. So the deal is a record, attached to a workspace through the record envelope's own
``room_id`` column, which means the join is a column rather than a convention.

``source`` on every write
-------------------------
Every method that writes takes a **required keyword-only** ``source``, and the feature's
routes pass the route that served the request, built from ``router.prefix``. A domain
function that hardcoded a source string would put a path in the audit log that the app
might have stopped serving, and that class of bug has shipped in this codebase before.
Required rather than defaulted: a caller that forgets is a type error, not a wrong audit
row.

``workspace_id`` as a spelling
------------------------------
The research's own vocabulary is ``workspace``, and its API is ``/v1/workspaces``. A
client written against that vocabulary sends ``workspace_id``. It is accepted and stored,
and the attachment itself is still the envelope's ``room_id`` so a single mechanism
governs it.

Schema flexibility
------------------
Nothing here validates a field against a schema, because there is no schema. A deal is
whatever the CRM sent, stored verbatim, and every field this module *reads* is located
by a synonym list that a team can replace in the config record. The only two refusals
are the ones where there is genuinely nothing to store: no CRM id and no name means
nothing to key the row by, and a CRM id that already exists means a second row would
double-count the same deal in every rollup.
"""

from __future__ import annotations

from typing import Any, Iterator, Mapping, Sequence

from dsr.salesimpact.errors import DealConflict, InvalidDeal, UnknownWorkspace
from dsr.salesimpact.vocabulary import (
    FIELD_SYNONYMS,
    as_bool,
    as_number,
    as_text,
    normalise,
    pick,
)
from dsr.store import RecordStore

#: The CRM opportunity/deal mirror. Feature-owned; no other feature writes it.
DEAL_COLLECTION = "crm_deal"

#: The single configuration record. Holds which collections to read, the synonym
#: overrides, the stage-set overrides, and the CRM integration state.
CONFIG_COLLECTION = "sales_impact_config"
CONFIG_KEY = "default"

#: How many records one listing call may return. ``AuditedDatabase`` caps a single
#: ``find``/``list`` at 1000, so anything that has to be complete pages at this size and
#: keeps going rather than stopping at the cap.
_PAGE = 1000

DEFAULT_COLLECTIONS = {"rooms": "room", "engagement": "activity"}

DEFAULT_STAGES = {"won": None, "lost": None}


def _merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """A shallow merge, used to layer a config record over the built-in defaults."""
    merged = dict(base)
    merged.update({key: value for key, value in override.items() if value is not None})
    return merged


class DealBook:
    """Reads and writes the CRM deal mirror, and the one configuration record."""

    def __init__(self, store: RecordStore) -> None:
        self.store = store

    # -- configuration ------------------------------------------------------ #

    def config(self, *, include_deleted: bool = False) -> dict[str, Any]:
        """The effective configuration, defaults layered under the stored record.

        A report that 500s because no config record exists yet is not a report; a report
        that reads the built-in defaults is. So a missing record is the normal case, not
        an error, and ``PATCH /integration`` is what creates the first one.
        """
        stored = self.store.find(
            CONFIG_COLLECTION, {"key": CONFIG_KEY}, limit=1, include_deleted=include_deleted
        )
        data = dict(stored[0]["data"]) if stored else {}
        collections = _merge(DEFAULT_COLLECTIONS, data.get("collections") or {})
        fields = dict(data.get("fields") or {})
        stages = _merge(DEFAULT_STAGES, fields.get("stage") or {})
        return {
            "id": stored[0]["id"] if stored else None,
            "key": CONFIG_KEY,
            "integration": _integration_state(data),
            "collections": {
                "rooms": as_text(collections.get("rooms"), DEFAULT_COLLECTIONS["rooms"]),
                "engagement": as_text(
                    collections.get("engagement"), DEFAULT_COLLECTIONS["engagement"]
                ),
            },
            "fields": {concept: list(keys) for concept, keys in fields.items() if concept != "stage"},
            "stages": {
                "won": [as_text(value) for value in (stages.get("won") or []) if as_text(value)],
                "lost": [as_text(value) for value in (stages.get("lost") or []) if as_text(value)],
            },
            "configured": bool(stored),
        }

    def save_config(
        self, patch: Mapping[str, Any], *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Merge a patch into the configuration record, creating it on first write.

        Creating it here rather than in a separate create route is what lets
        ``PATCH /integration`` be the only endpoint a caller has to know about, and it
        is the create that is audited - so the first call with ``{"connected": true}``
        produces an ``insert`` audit row and later calls produce ``update`` rows.
        """
        current = self.config()
        record_id = current["id"]
        data = self._config_payload(current)
        merged = {**data, **self._config_patch(patch, current)}
        if record_id:
            self.store.update(record_id, merged, actor=actor, source=source)
        else:
            self.store.create(CONFIG_COLLECTION, merged, actor=actor, source=source)
        return self.config()

    def _config_payload(self, current: Mapping[str, Any]) -> dict[str, Any]:
        """The stored shape of the config record, so a patch lands in the right place."""
        return {
            "key": CONFIG_KEY,
            "integration": dict(current.get("integration") or {}),
            "collections": dict(current.get("collections") or {}),
            "fields": {
                "stage": dict(current.get("stages") or {}),
                **{concept: list(keys) for concept, keys in (current.get("fields") or {}).items()},
            },
        }

    @staticmethod
    def _config_patch(patch: Mapping[str, Any], current: Mapping[str, Any]) -> dict[str, Any]:
        """Route a flat patch to its place in the stored shape.

        ``{"connected": true}`` belongs under ``integration``; ``{"engagement": "x"}``
        belongs under ``collections``. Accepting both spellings means a client does not
        have to know the nesting to turn the integration on, which is the one write a
        non-integrator will ever make.
        """
        body = {key: value for key, value in dict(patch).items() if key != "key"}
        integration_keys = {"connected", "provider", "connected_at", "crm"}
        nested = body.get("integration") if isinstance(body.get("integration"), Mapping) else {}
        out: dict[str, Any] = {}

        flat_integration = {key: value for key, value in body.items() if key in integration_keys}
        if flat_integration or nested:
            merged = dict(current.get("integration") or {})
            merged.update(flat_integration)
            merged.update(nested)
            if "connected" in merged:
                merged["connected"] = as_bool(merged["connected"])
            if merged.get("connected") and not merged.get("connected_at"):
                merged["connected_at"] = _now()
            if not merged.get("connected"):
                merged.pop("connected_at", None)
            out["integration"] = merged

        collections_in = body.get("collections") if isinstance(body.get("collections"), Mapping) else {}
        flat_collections = {
            key: value for key, value in body.items() if key in {"rooms", "engagement"}
        }
        collections = dict(current.get("collections") or {})
        collections.update(flat_collections)
        collections.update(collections_in)
        if collections:
            out["collections"] = collections

        if isinstance(body.get("fields"), Mapping):
            out["fields"] = dict(body["fields"])

        passthrough = {
            key: value
            for key, value in body.items()
            if key not in integration_keys
            and key not in {"rooms", "engagement", "integration", "collections", "fields"}
        }
        if passthrough:
            out.update(passthrough)
        return out

    # -- reads -------------------------------------------------------------- #

    def _all(self, collection: str) -> Iterator[dict[str, Any]]:
        """Every live record in a collection, paging past the per-call cap.

        ``AuditedDatabase`` limits a single listing to 1000 and offers no offset on
        ``find``, so completeness here is a loop over ``list`` pages with the offset
        advanced until a page comes back short. Aggregates computed over a truncated
        population would be quietly wrong, which is the one thing a report must not be.
        """
        offset = 0
        while True:
            page = self.store.list(collection, limit=_PAGE, offset=offset, order_by="created_at")
            if not page:
                return
            yield from page
            if len(page) < _PAGE:
                return
            offset += _PAGE

    def deals(self) -> list[dict[str, Any]]:
        """Every live deal record, in a deterministic order."""
        return sorted(self._all(DEAL_COLLECTION), key=lambda record: str(record["id"]))

    def rooms(self, collection: str = "room") -> list[dict[str, Any]]:
        """Every live workspace record, in a deterministic order."""
        return sorted(self._all(collection), key=lambda record: str(record["id"]))

    def events(self, collection: str) -> list[dict[str, Any]]:
        """Every live buyer engagement record, in a deterministic order."""
        return sorted(self._all(collection), key=lambda record: str(record["id"]))

    def deal(self, deal_id: str) -> dict[str, Any] | None:
        """One deal record, or ``None``. A record in another collection is not a deal."""
        record = self.store.get(deal_id)
        if record is None or record.get("collection") != DEAL_COLLECTION:
            return None
        return record

    def require_deal(self, deal_id: str) -> dict[str, Any]:
        """One live deal record, or :class:`UnknownWorkspace`.

        The same 404 the write paths use, so a client handling "no such deal" has one
        shape to handle across a read, a patch and a delete. Raised as a domain type
        rather than the store's ``RecordNotFound`` because the core app already claims
        that one, and two handlers for one type is a collision the host refuses.
        """
        record = self.deal(deal_id)
        if record is None:
            raise UnknownWorkspace(f"deal {deal_id} not found")
        return record

    def by_external_id(self, crm_deal_id: str) -> dict[str, Any] | None:
        """The live deal holding a CRM id, through the dynamic index.

        ``find`` resolves a JSON path in ``data`` without a migration, which is the point
        of storing the id in the payload rather than in a column of our own.
        """
        wanted = as_text(crm_deal_id)
        if not wanted:
            return None
        matches = self.store.find(DEAL_COLLECTION, {"crm_deal_id": wanted}, limit=1)
        if matches:
            return matches[0]
        # A CRM that spells the same id with different separators still means the same
        # deal, so a case-folded sweep catches it rather than letting a second row in.
        for record in self._all(DEAL_COLLECTION):
            data = record.get("data") or {}
            stored = as_text(pick(data, FIELD_SYNONYMS["crm_deal_id"]))
            if stored and normalise(stored) == normalise(wanted):
                return record
        return None

    def require_room(self, room_id: str, *, collection: str = "room") -> dict[str, Any]:
        """A live workspace record, or :class:`UnknownWorkspace`.

        A room id that is not a live ``room`` record is a 404 even when some other
        collection happens to hold that id, because the report's population is defined
        over workspaces and attaching a deal to something else is a mistake worth naming.
        """
        record = self.store.get(room_id)
        if record is None or record.get("collection") != collection or record.get("deleted_at"):
            raise UnknownWorkspace(f"room {room_id} not found")
        return record

    # -- writes ------------------------------------------------------------- #

    def create_deal(
        self,
        payload: Mapping[str, Any],
        *,
        room_id: str | None = None,
        room_collection: str = "room",
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Attach a CRM deal to a workspace.

        The attachment is the envelope's ``room_id``. ``workspace_id`` in the body is the
        researched spelling and is accepted, and stored, but it is never *load-bearing*:
        one mechanism governing the join is worth more than two that can disagree.

        A CRM id that is already attached is a conflict rather than a second row. A
        mistyped id, or a client that re-posts instead of patching, would otherwise add a
        duplicate that counts the same money twice in every tile.
        """
        data = dict(payload or {})
        resolved_room = (
            as_text(room_id)
            or as_text(data.get("room_id"))
            or as_text(data.get("workspace_id"))
            or as_text(data.get("workspaceId"))
        )
        external_id = as_text(pick(data, FIELD_SYNONYMS["crm_deal_id"]))
        name = as_text(pick(data, FIELD_SYNONYMS["name"]))
        if not external_id and not name:
            raise InvalidDeal(
                "a deal needs a crm_deal_id to be recognised by the CRM, or a name to be "
                "keyed by; the payload had neither"
            )

        if external_id:
            existing = self.by_external_id(external_id)
            if existing is not None:
                raise DealConflict(
                    f"crm_deal_id {external_id!r} is already attached to room "
                    f"{existing.get('room_id')!r}; patch that record rather than "
                    f"creating a second row for the same deal",
                    existing_id=str(existing["id"]),
                    existing_room_id=str(existing.get("room_id") or ""),
                )

        if resolved_room:
            self.require_room(resolved_room, collection=room_collection)

        # The researched spelling is kept so a client reading the record back sees what
        # it sent, and the envelope stays the only authority on the attachment.
        stored = {key: value for key, value in data.items() if key not in {"workspaceId"}}
        if resolved_room and "workspace_id" not in stored:
            stored["workspace_id"] = resolved_room
        return self.store.create(
            DEAL_COLLECTION, stored, room_id=resolved_room or None, actor=actor, source=source
        )

    def update_deal(
        self, deal_id: str, patch: Mapping[str, Any], *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """A shallow merge patch over a deal. The researched stage/amount sync.

        The stage, the amount and the close date are the three fields the research says
        change on a sync, and this is the single place they are written, so a CRM push and
        a person correcting a typo take the same path and produce the same audit row
        shape.
        """
        record = self.require_deal(deal_id)
        del record
        return self.store.update(deal_id, dict(patch or {}), actor=actor, source=source)

    def delete_deal(self, deal_id: str, *, actor: str | None = None, source: str) -> dict[str, Any]:
        """Soft-delete a deal, so the detachment and its audit row survive.

        Soft rather than hard, because "this deal was detached on the 3rd" is exactly the
        kind of thing a leadership report about pipeline completeness needs to be able to
        answer, and a hard delete would erase the question along with the answer.
        """
        self.require_deal(deal_id)
        return self.store.delete(deal_id, actor=actor, source=source)


def _integration_state(data: Mapping[str, Any]) -> dict[str, Any]:
    """The integration block, normalised, whether or not a record exists yet."""
    block = data.get("integration") if isinstance(data.get("integration"), Mapping) else {}
    return {
        "provider": as_text(block.get("provider")),
        "connected": as_bool(block.get("connected")),
        "connected_at": block.get("connected_at") or None,
    }


def _now() -> str:
    from dsr.db.audited import utcnow

    return utcnow()


def deal_amount(record: Mapping[str, Any], synonyms: Mapping[str, Sequence[str]] | None = None) -> float | None:
    """A deal's amount, or ``None``. The rollup treats those two differently."""
    return as_number(
        pick((record.get("data") or {}), (), synonyms=synonyms, concept="amount")
    )


def view_deal(
    record: Mapping[str, Any],
    *,
    stage_sets: Mapping[str, Sequence[str]] | None = None,
    room: Mapping[str, Any] | None = None,
    in_scope: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """The read projection of one deal: the researched fields, resolved, plus provenance.

    Everything in here is derived on the way out and is never written back to ``data``.
    A stored ``stage_class`` would be a cache, and the research says the stage changes by
    sync - a cache that goes stale is how a report starts lying.
    """
    from dsr.salesimpact.vocabulary import classify_stage

    data = record.get("data") or {}
    stage_text = as_text(pick(data, (), concept="stage"))
    stage_class = classify_stage(
        stage_text,
        won=set((stage_sets or {}).get("won") or ()) or None,
        lost=set((stage_sets or {}).get("lost") or ()) or None,
    )
    own_owner = as_text(pick(data, (), concept="owner"))
    owner_source = "deal" if own_owner else "room"
    owner = own_owner or (as_text(pick((room or {}).get("data") or {}, (), concept="room_owner")) if room else "")
    if not owner:
        owner_source = "unassigned"
    return {
        "id": record.get("id"),
        "room_id": record.get("room_id"),
        "crm_deal_id": as_text(pick(data, (), concept="crm_deal_id")) or None,
        "name": as_text(pick(data, (), concept="name")),
        "account": as_text(pick(data, (), concept="account")),
        "stage": stage_text,
        "stage_class": stage_class,
        "amount": deal_amount(record),
        "currency": as_text(pick(data, (), concept="currency")) or None,
        "owner": owner,
        "owner_source": owner_source,
        "team": as_text(pick(data, (), concept="team")),
        "created_at": pick(data, (), concept="created_at"),
        "closed_at": pick(data, (), concept="closed_at"),
        "in_scope": in_scope,
        "reason": reason,
    }
