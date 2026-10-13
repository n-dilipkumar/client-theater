"""WF-122: the engine that applies the pure rules to stored state.

The engine is the only place the rules in :mod:`dsr.competitive_baseline.rules`
meet the store. It reads and writes ordinary JSON through
:class:`~dsr.store.RecordStore`, so every write goes through the audited
database and its audit row names the route that served it.

Two properties are deliberate and are what the tests pin:

* **A write is verified before it reports success.** Every tier change writes,
  then reads the record back and compares. A write that does not read back
  raises :class:`~dsr.competitive_baseline.errors.DisclosureRefused` with the
  reason ``write_not_visible`` rather than returning the record it hoped for.
  The researched source verifies against the live site before reporting success;
  this is the local form of that rule.

* **The engine names no route.** Every write takes a ``source`` argument from its
  caller, and the feature module builds that string from its own router. A
  literal URL here would leave the audit log naming a route the app stopped
  serving.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from dsr.store import RecordStore

from . import rules
from .errors import DisclosureError, DisclosureNotFound, DisclosureRefused
from .rules import (
    effective_state,
    new_token,
    token_hash,
    token_hint,
    validate_policy,
    validate_recipient,
    validate_section,
)
from .vocabulary import (
    ACTIVE_STATE,
    DEFAULT_BELOW_TIER,
    DEFAULT_TIER_LABELS,
    EVENT_COLLECTION,
    EVENT_LINK_REVOKED,
    EVENT_LINK_ROTATED,
    EVENT_RECIPIENT_CREATED,
    EVENT_SECTION_GRANTED,
    EVENT_SECTION_REQUESTED,
    EVENT_TIER_UNLOCKED,
    EXPIRED_STATE,
    GRANT_COLLECTION,
    POLICY_COLLECTION,
    RECIPIENT_COLLECTION,
    REQUEST_COLLECTION,
    REVOKED_STATE,
    ROOM_REF,
    SECTION_COLLECTION,
)

# --------------------------------------------------------------------------- #
# Record helpers. The store returns whole records; the presenters flatten the
# payload and hide the token hash, which must never leave the server.
# --------------------------------------------------------------------------- #


def data_of(record: Mapping[str, Any]) -> dict[str, Any]:
    """The payload of a record, as a fresh mutable dict."""
    return dict(record.get("data") or {})


def room_of(record: Mapping[str, Any]) -> Any:
    """The room a record belongs to, from the envelope or the payload."""
    return record.get("room_id") or data_of(record).get(ROOM_REF)


def present_section(record: Mapping[str, Any]) -> dict[str, Any]:
    return {"id": record["id"], "room_id": room_of(record), **data_of(record)}


def present_recipient(record: Mapping[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """A recipient without its token hash, with the derived link state."""
    data = data_of(record)
    data.pop("token_hash", None)
    state = effective_state(data, now)
    return {
        "id": record["id"],
        "room_id": room_of(record),
        **data,
        "effective_state": state,
        "live": state == ACTIVE_STATE,
    }


def present_grant(record: Mapping[str, Any]) -> dict[str, Any]:
    return {"id": record["id"], "room_id": room_of(record), **data_of(record)}


def present_request(record: Mapping[str, Any]) -> dict[str, Any]:
    return {"id": record["id"], "room_id": room_of(record), **data_of(record)}


def present_event(record: Mapping[str, Any]) -> dict[str, Any]:
    return {"id": record["id"], "room_id": room_of(record), **data_of(record)}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DisclosureEngine:
    """Reads and writes a room's tier policy, sections, links and events.

    ``now`` is injectable so a test can pin the clock. The production route
    leaves it at the real clock, because a caller that could pass its own ``now``
    could make an expired link look live.
    """

    def __init__(self, store: RecordStore, now: Callable[[], datetime] | None = None) -> None:
        self.store = store
        self._now = now or _utcnow

    # -- reads of one record ------------------------------------------------ #

    def _require(self, collection: str, record_id: str, label: str) -> dict[str, Any]:
        record = self.store.get(record_id)
        if record is None or record.get("collection") != collection:
            raise DisclosureNotFound(f"No such {label}: {record_id}")
        return record

    def _list(self, collection: str, room_id: str | None, *, limit: int = 500) -> list[dict]:
        if room_id:
            return self.store.find(collection, {ROOM_REF: room_id}, limit=limit)
        return self.store.list(collection, limit=limit)

    # -- policy ------------------------------------------------------------- #

    def _policy_record(self, room_id: str) -> dict[str, Any] | None:
        found = self.store.find(POLICY_COLLECTION, {ROOM_REF: room_id}, limit=1)
        return found[0] if found else None

    def read_policy(self, room_id: str) -> dict[str, Any]:
        """The room's tier policy, or the defaults when none was written yet."""
        record = self._policy_record(room_id)
        if record is None:
            return {
                "room_id": room_id,
                "tier_labels": dict(DEFAULT_TIER_LABELS),
                "default_below_tier": DEFAULT_BELOW_TIER,
            }
        data = data_of(record)
        labels = data.get("tier_labels")
        if not isinstance(labels, dict) or not labels:
            labels = DEFAULT_TIER_LABELS
        return {
            "room_id": room_id,
            "tier_labels": {str(key): str(value) for key, value in labels.items()},
            "default_below_tier": str(data.get("default_below_tier") or DEFAULT_BELOW_TIER),
        }

    def set_policy(
        self, room_id: str, payload: Mapping[str, Any] | None, *, source: str, actor: str
    ) -> dict[str, Any]:
        """Write or replace a room's tier policy."""
        if not str(room_id or "").strip():
            raise DisclosureError("A policy belongs to a room.", {"room_id": "Name the room."})
        normalised = validate_policy(payload)
        data = {**normalised, ROOM_REF: room_id}
        record = self._policy_record(room_id)
        if record is None:
            self.store.create(POLICY_COLLECTION, data, room_id=room_id, source=source, actor=actor)
        else:
            self.store.update(record["id"], data, source=source, actor=actor)
        return self.read_policy(room_id)

    # -- sections ----------------------------------------------------------- #

    def _section_records(self, room_id: str | None) -> list[dict[str, Any]]:
        records = self._list(SECTION_COLLECTION, room_id)
        records.sort(
            key=lambda record: (
                rules.read_int(data_of(record).get("order"), 0) or 0,
                str(record.get("created_at") or ""),
            )
        )
        return records

    def list_sections(self, room_id: str) -> list[dict[str, Any]]:
        return [present_section(record) for record in self._section_records(room_id)]

    def read_section(self, section_id: str) -> dict[str, Any]:
        return present_section(self._require(SECTION_COLLECTION, section_id, "section"))

    def create_section(
        self, room_id: str, payload: Mapping[str, Any] | None, *, source: str, actor: str
    ) -> dict[str, Any]:
        policy = self.read_policy(room_id)
        normalised = validate_section(payload, default_below=policy["default_below_tier"])
        data = {**normalised, ROOM_REF: room_id}
        record = self.store.create(
            SECTION_COLLECTION, data, room_id=room_id, source=source, actor=actor
        )
        return present_section(record)

    def update_section(
        self, section_id: str, patch: Mapping[str, Any] | None, *, source: str, actor: str
    ) -> dict[str, Any]:
        record = self._require(SECTION_COLLECTION, section_id, "section")
        policy = self.read_policy(room_of(record))
        merged = {**data_of(record), **dict(patch or {})}
        normalised = validate_section(merged, default_below=policy["default_below_tier"])
        fresh = self._write_verified(
            record["id"],
            normalised,
            expect={
                "name": normalised["name"],
                "min_tier": normalised["min_tier"],
                "below_tier": normalised["below_tier"],
            },
            source=source,
            actor=actor,
        )
        return present_section(fresh)

    def delete_section(self, section_id: str, *, source: str, actor: str) -> dict[str, Any]:
        """Remove a section. Its grants and requests become orphans and are ignored."""
        self._require(SECTION_COLLECTION, section_id, "section")
        self.store.delete(section_id, source=source, actor=actor)
        return {"removed": True, "id": section_id}

    # -- recipients and their links ----------------------------------------- #

    def _recipient_records(self, room_id: str | None) -> list[dict[str, Any]]:
        records = self._list(RECIPIENT_COLLECTION, room_id)
        records.sort(
            key=lambda record: (
                str(data_of(record).get("org") or "").lower(),
                str(record.get("created_at") or ""),
            )
        )
        return records

    def list_recipients(self, room_id: str) -> list[dict[str, Any]]:
        now = self._now()
        return [present_recipient(record, now) for record in self._recipient_records(room_id)]

    def read_recipient(self, recipient_id: str) -> dict[str, Any]:
        return present_recipient(
            self._require(RECIPIENT_COLLECTION, recipient_id, "recipient"), self._now()
        )

    def create_recipient(
        self, room_id: str, payload: Mapping[str, Any] | None, *, source: str, actor: str
    ) -> dict[str, Any]:
        """Issue one access link for one counterparty organisation.

        The raw token is returned exactly once, here. The store keeps only its
        hash and a four-character hint, so a later read of the record cannot
        recover the link.
        """
        normalised = validate_recipient(payload)
        token = new_token()
        data = {
            **normalised,
            ROOM_REF: room_id,
            "state": ACTIVE_STATE,
            "token_hash": token_hash(token),
            "token_hint": token_hint(token),
        }
        record = self.store.create(
            RECIPIENT_COLLECTION, data, room_id=room_id, source=source, actor=actor
        )
        fresh = self.store.get(record["id"]) or record
        if data_of(fresh).get("token_hash") != data["token_hash"]:
            raise DisclosureRefused(
                DisclosureRefused.REASON_WRITE_NOT_VISIBLE, "the access link did not read back."
            )
        self._write_event(
            room_id,
            record["id"],
            EVENT_RECIPIENT_CREATED,
            to_tier=normalised["tier"],
            source=source,
            actor=actor,
        )
        presented = present_recipient(fresh, self._now())
        presented["token"] = token
        presented["access_path"] = f"/api/wf-122/access/{token}"
        return presented

    def update_recipient(
        self, recipient_id: str, patch: Mapping[str, Any] | None, *, source: str, actor: str
    ) -> dict[str, Any]:
        """Edit a link's org, name, tier or expiry.

        The link's state and token are refused here on purpose. They change only
        through the revoke and rotate routes, so a generic patch is not a back
        door that revives a revoked link or forges a token.
        """
        record = self._require(RECIPIENT_COLLECTION, recipient_id, "recipient")
        current = data_of(record)
        cleaned = {
            key: value
            for key, value in dict(patch or {}).items()
            if key not in ("state", "token_hash", "token_hint")
        }
        base = {
            key: value
            for key, value in current.items()
            if key not in ("state", "token_hash", "token_hint")
        }
        normalised = validate_recipient({**base, **cleaned})
        normalised["state"] = str(current.get("state") or ACTIVE_STATE)
        normalised["token_hash"] = current.get("token_hash")
        normalised["token_hint"] = current.get("token_hint")
        fresh = self._write_verified(
            record["id"],
            normalised,
            expect={"org": normalised["org"], "tier": normalised["tier"]},
            source=source,
            actor=actor,
        )
        return present_recipient(fresh, self._now())

    def resolve_token(self, token: str) -> dict[str, Any]:
        """The recipient an access token names, or a not-found error."""
        if not str(token or "").strip():
            raise DisclosureNotFound("No such access link.")
        found = self.store.find(RECIPIENT_COLLECTION, {"token_hash": token_hash(token)}, limit=1)
        if not found:
            raise DisclosureNotFound("No such access link.")
        return present_recipient(found[0], self._now())

    # -- the clear changes -------------------------------------------------- #

    def unlock(self, recipient_id: str, tier: Any, *, source: str, actor: str) -> dict[str, Any]:
        """Raise a link's tier, verify the write, and log the unlock.

        The write is read back before the route reports success. A tier change
        that did not land is a conflict, not a success.
        """
        record = self._require(RECIPIENT_COLLECTION, recipient_id, "recipient")
        current = data_of(record)
        state = effective_state(current, self._now())
        if state != ACTIVE_STATE:
            reason = (
                DisclosureRefused.REASON_REVOKED
                if state == REVOKED_STATE
                else DisclosureRefused.REASON_EXPIRED
            )
            raise DisclosureRefused(reason)
        new_tier = rules.read_int(tier, None)
        if new_tier is None or new_tier < 0:
            raise DisclosureError(
                "The tier could not be read.",
                {"tier": "tier must be a whole number of zero or more."},
            )
        fresh = self._write_verified(
            record["id"], {"tier": new_tier}, expect={"tier": new_tier}, source=source, actor=actor
        )
        self._write_event(
            room_of(record),
            recipient_id,
            EVENT_TIER_UNLOCKED,
            from_tier=rules.read_int(current.get("tier"), 0) or 0,
            to_tier=new_tier,
            source=source,
            actor=actor,
        )
        return present_recipient(fresh, self._now())

    def revoke(self, recipient_id: str, *, source: str, actor: str) -> dict[str, Any]:
        """Revoke a link. Idempotent: a second revoke writes nothing and logs nothing."""
        record = self._require(RECIPIENT_COLLECTION, recipient_id, "recipient")
        current = data_of(record)
        if str(current.get("state") or ACTIVE_STATE) == REVOKED_STATE:
            return present_recipient(record, self._now())
        fresh = self._write_verified(
            record["id"],
            {"state": REVOKED_STATE},
            expect={"state": REVOKED_STATE},
            source=source,
            actor=actor,
        )
        self._write_event(
            room_of(record),
            recipient_id,
            EVENT_LINK_REVOKED,
            from_tier=rules.read_int(current.get("tier"), 0) or 0,
            source=source,
            actor=actor,
        )
        return present_recipient(fresh, self._now())

    def rotate_token(self, recipient_id: str, *, source: str, actor: str) -> dict[str, Any]:
        """Re-issue a link's token, so the old one stops resolving."""
        record = self._require(RECIPIENT_COLLECTION, recipient_id, "recipient")
        current = data_of(record)
        if effective_state(current, self._now()) == REVOKED_STATE:
            raise DisclosureRefused(DisclosureRefused.REASON_REVOKED)
        token = new_token()
        hint = token_hint(token)
        fresh = self._write_verified(
            record["id"],
            {"token_hash": token_hash(token), "token_hint": hint},
            expect={"token_hint": hint},
            source=source,
            actor=actor,
        )
        self._write_event(
            room_of(record),
            recipient_id,
            EVENT_LINK_ROTATED,
            from_tier=rules.read_int(current.get("tier"), 0) or 0,
            source=source,
            actor=actor,
        )
        presented = present_recipient(fresh, self._now())
        presented["token"] = token
        presented["access_path"] = f"/api/wf-122/access/{token}"
        return presented

    # -- section-level access ----------------------------------------------- #

    def grant_section(
        self,
        section_id: str,
        recipient_id: str,
        *,
        note: str | None = None,
        source: str,
        actor: str,
    ) -> dict[str, Any]:
        """Reveal one section to one recipient below their tier.

        Idempotent by (recipient, section): a second grant of the same pair
        returns the existing grant rather than writing a duplicate.
        """
        section = self._require(SECTION_COLLECTION, section_id, "section")
        recipient = self._require(RECIPIENT_COLLECTION, recipient_id, "recipient")
        room_id = room_of(section) or room_of(recipient)
        existing = self.store.find(
            GRANT_COLLECTION, {"recipient_id": recipient_id, "section_id": section_id}, limit=1
        )
        if existing:
            return present_grant(existing[0])
        data: dict[str, Any] = {
            "recipient_id": recipient_id,
            "section_id": section_id,
            ROOM_REF: room_id,
        }
        if note:
            data["note"] = str(note)
        record = self.store.create(
            GRANT_COLLECTION, data, room_id=room_id, source=source, actor=actor
        )
        self._write_event(
            room_id,
            recipient_id,
            EVENT_SECTION_GRANTED,
            section_id=section_id,
            note=note,
            source=source,
            actor=actor,
        )
        return present_grant(record)

    def request_section(
        self,
        section_id: str,
        recipient_id: str,
        *,
        note: str | None = None,
        source: str,
        actor: str,
    ) -> dict[str, Any]:
        """A recipient asks for an on-request section the seller has not opened."""
        section = self._require(SECTION_COLLECTION, section_id, "section")
        recipient = self._require(RECIPIENT_COLLECTION, recipient_id, "recipient")
        room_id = room_of(section) or room_of(recipient)
        data: dict[str, Any] = {
            "recipient_id": recipient_id,
            "section_id": section_id,
            "state": "pending",
            ROOM_REF: room_id,
        }
        if note:
            data["note"] = str(note)
        record = self.store.create(
            REQUEST_COLLECTION, data, room_id=room_id, source=source, actor=actor
        )
        self._write_event(
            room_id,
            recipient_id,
            EVENT_SECTION_REQUESTED,
            section_id=section_id,
            note=note,
            source=source,
            actor=actor,
        )
        return present_request(record)

    def list_requests(self, room_id: str) -> list[dict[str, Any]]:
        records = self._list(REQUEST_COLLECTION, room_id)
        records.sort(key=lambda record: str(record.get("created_at") or ""))
        return [present_request(record) for record in records]

    def list_grants(self, room_id: str) -> list[dict[str, Any]]:
        records = self._list(GRANT_COLLECTION, room_id)
        records.sort(key=lambda record: str(record.get("created_at") or ""))
        return [present_grant(record) for record in records]

    # -- rendering ---------------------------------------------------------- #

    def _grant_ids_for(self, recipient_id: str) -> set[str]:
        found = self.store.find(GRANT_COLLECTION, {"recipient_id": recipient_id}, limit=500)
        return {str(data_of(record).get("section_id")) for record in found}

    def render_room(self, room_id: str, tier: Any) -> dict[str, Any]:
        """The room as a viewer at ``tier`` would see it. A seller's preview."""
        viewer_tier = rules.read_int(tier, 0)
        if viewer_tier is None or viewer_tier < 0:
            viewer_tier = 0
        sections = [present_section(record) for record in self._section_records(room_id)]
        rows = rules.render_room(sections, viewer_tier)
        return {
            "room_id": room_id,
            "viewer_tier": viewer_tier,
            "sections": rows,
            "section_count": len(sections),
            "visible_count": len(rows),
            "hidden_count": len(sections) - len(rows),
        }

    def render_for_recipient(self, recipient_id: str) -> dict[str, Any]:
        """The room as one recipient sees it, with their grants and link state.

        A revoked or expired link is rendered at tier -1 with no grants, so every
        section is below-tier and the response shows nothing.
        """
        record = self._require(RECIPIENT_COLLECTION, recipient_id, "recipient")
        data = data_of(record)
        now = self._now()
        state = effective_state(data, now)
        room_id = room_of(record)
        tier = rules.read_int(data.get("tier"), 0) or 0
        sections = [present_section(row) for row in self._section_records(room_id)]
        if state != ACTIVE_STATE:
            # A revoked or expired link is not a below-tier viewer: it is a closed
            # room. It sees nothing, not a teaser and not an on-request marker, so
            # the section list is empty rather than rendered at a low tier.
            tier = -1
            rows: list[dict[str, Any]] = []
        else:
            grants = self._grant_ids_for(recipient_id)
            rows = rules.render_room(sections, tier, granted_ids=grants)
        return {
            "recipient": present_recipient(record, now),
            "room_id": room_id,
            "state": state,
            "viewer_tier": tier,
            "sections": rows,
            "section_count": len(sections),
            "visible_count": len(rows),
            "hidden_count": len(sections) - len(rows),
        }

    # -- events and summary ------------------------------------------------- #

    def events_of(self, room_id: str) -> list[dict[str, Any]]:
        records = self._list(EVENT_COLLECTION, room_id, limit=1000)
        records.sort(key=lambda record: str(record.get("created_at") or ""))
        return [present_event(record) for record in records]

    def summary(self, room_id: str | None = None) -> dict[str, Any]:
        """The headline numbers, including the states that are not successes."""
        now = self._now()
        sections = self._section_records(room_id)
        recipients = self._recipient_records(room_id)
        states = [effective_state(data_of(record), now) for record in recipients]
        grants = self._list(GRANT_COLLECTION, room_id)
        requests = self._list(REQUEST_COLLECTION, room_id)
        events = self._list(EVENT_COLLECTION, room_id, limit=1000)
        return {
            "room_id": room_id,
            "sections": len(sections),
            "recipients": len(recipients),
            "active": states.count(ACTIVE_STATE),
            "revoked": states.count(REVOKED_STATE),
            "expired": states.count(EXPIRED_STATE),
            "unlocked": sum(
                1
                for record in recipients
                if (rules.read_int(data_of(record).get("tier"), 0) or 0) > 0
            ),
            "grants": len(grants),
            "open_requests": sum(
                1
                for record in requests
                if str(data_of(record).get("state") or "pending") == "pending"
            ),
            "events": len(events),
            "generated_at": now.isoformat(),
        }

    # -- internals ---------------------------------------------------------- #

    def _write_verified(
        self,
        record_id: str,
        patch: Mapping[str, Any],
        *,
        expect: Mapping[str, Any],
        source: str,
        actor: str,
    ) -> dict[str, Any]:
        """Write, read back, and refuse to report success if it did not land."""
        self.store.update(record_id, patch, source=source, actor=actor)
        fresh = self.store.get(record_id)
        if fresh is None:
            raise DisclosureRefused(
                DisclosureRefused.REASON_WRITE_NOT_VISIBLE,
                f"record {record_id} vanished after a write.",
            )
        data = data_of(fresh)
        for key, value in expect.items():
            if data.get(key) != value:
                raise DisclosureRefused(
                    DisclosureRefused.REASON_WRITE_NOT_VISIBLE,
                    f"{key} did not read back as written.",
                )
        return fresh

    def _write_event(
        self,
        room_id: str,
        recipient_id: str,
        kind: str,
        *,
        from_tier: int | None = None,
        to_tier: int | None = None,
        section_id: str | None = None,
        note: str | None = None,
        source: str,
        actor: str,
    ) -> dict[str, Any]:
        data: dict[str, Any] = {
            "kind": kind,
            "recipient_id": recipient_id,
            "at": self._now().isoformat(),
            ROOM_REF: room_id,
        }
        if section_id:
            data["section_id"] = section_id
        if from_tier is not None:
            data["from_tier"] = from_tier
        if to_tier is not None:
            data["to_tier"] = to_tier
        if note:
            data["note"] = str(note)
        return self.store.create(
            EVENT_COLLECTION, data, room_id=room_id, source=source, actor=actor
        )
