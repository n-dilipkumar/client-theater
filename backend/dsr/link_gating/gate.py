"""The WF-069 gate: create a gated link, and walk a buyer through it.

Everything the research asks for happens here, in the order it asks for it:

1. The rep sets a password, an ``expires_at`` and the email flags, and the link
   is written with a hash and a timestamp rather than the secrets themselves.
2. The buyer opens the URL. Expiry is evaluated **on this request**, and again
   on every request after it.
3. The buyer's email is collected. If the link is authenticated, a one-time code
   goes to that address and is verified server-side.
4. The password is compared.
5. Only then is the document content released, the verified email is persisted as
   a visitor, and the view is stamped and notified.

Two seams are injected rather than hard-wired, both because the research names
the capability but not the implementation:

``now``
    The clock. Every expiry comparison and every stamp goes through it, so the
    boundary rule in :mod:`dsr.link_gating.rules` is testable at the instant it
    is stated about rather than approximately.

``deliver``
    The one-time-code transport. ``data_sources`` says "transactional email
    provider for the one-time code ``[inferred - the docs name the OTP but not
    the delivery vendor]``", so the vendor is genuinely unknown and inventing one
    would be fiction. The default transport does nothing, and the dispatch is
    recorded as a delivery row that names the recipient, the challenge and the
    time but never the code. The cleartext code exists in exactly one place: the
    mapping handed to this callable, in memory, for the moment it takes to call
    it. Tests inject a transport that captures it, which is how the tests below
    complete an authenticated round trip without any code ever reaching a record
    payload, a response body, or a log.

Where the grant happens
-----------------------

The guide's step list is "enter their email, enter the password, see the
document", and it presumes a password exists. When a link asks for an email and
no password, there is nothing left to ask after the email step, so that step is
the grant. :meth:`GateEngine.submit_email` and :meth:`GateEngine.submit_code`
both end in the same place when no password is required. The alternative - telling
the buyer "now enter the password" on a link with no password - is a dead end
with no way out of it except refreshing.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from dsr.link_gating import rules, secrets as link_secrets
from dsr.store import RecordStore

#: ``(message) -> None``. The message carries the cleartext code; see the module
#: docstring for why that is the only place it goes.
Delivery = Callable[[Mapping[str, Any]], None]

#: The gate fields an update may write. Deliberately a closed list: ``update`` is
#: a merge patch over the whole payload, and an open list would let a PATCH rewrite
#: ``title``, ``target`` or a preset's carried fields as a side effect of rotating
#: a password.
_PATCHABLE = (
    "email_protected",
    "email_authenticated",
    "enable_notification",
    "expires_at",
    "password_hash",
    "password_set",
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _noop_transport(message: Mapping[str, Any]) -> None:
    """The default: no vendor is configured, so nothing is sent.

    The dispatch is still recorded by the engine before this is called, so a
    seller can see that a code went out and when. What a deployment adds is this
    one callable - an SMTP relay, SES, Postmark - and nothing else in this
    workflow changes.
    """


class GateEngine:
    """Every read and write this workflow makes, in one place.

    The store is the only thing it touches. There is no SQLite handle here and no
    HTTP object, so the whole gate is exercised in tests without a server.
    """

    def __init__(
        self,
        store: RecordStore,
        *,
        now: Callable[[], datetime] | None = None,
        deliver: Delivery | None = None,
    ) -> None:
        self.store = store
        self._now = now or _utcnow
        self.deliver: Delivery = deliver or _noop_transport

    # -- helpers ------------------------------------------------------------ #

    def _stamp(self) -> str:
        return self._now().isoformat(timespec="milliseconds")

    @staticmethod
    def _data(record: Mapping[str, Any]) -> dict[str, Any]:
        return dict(record.get("data") or {})

    def _link(self, link_id: Any, *, include_deleted: bool = False) -> dict[str, Any]:
        """Fetch a link, or raise :class:`~dsr.link_gating.rules.LinkNotFound`.

        A soft-deleted link is still fetchable on purpose: revocation has to be
        distinguishable from "this id never existed", so that the buyer gets the
        friendly page rather than a 404 that tells them the link was real once.
        """
        record = None
        if isinstance(link_id, str) and link_id:
            try:
                # Read through ``store.db`` rather than ``store.get`` for one
                # reason: ``RecordStore.get`` does not forward ``include_deleted``,
                # and it filters soft-deleted rows out entirely. So a revoked link
                # came back as ``None`` and looked like an id that never existed -
                # which is precisely the distinction this workflow exists to keep.
                # ``store.db`` is the same audited wrapper underneath, and this is a
                # read, so nothing about the audit guarantee changes. A one-line
                # `**kwargs` on ``RecordStore.get`` would remove the need for this;
                # that is platform work in a shared file and is reported, not done
                # here.
                record = self.store.db.get(link_id, include_deleted=include_deleted)
            except Exception:  # the store's own miss, or a malformed id
                record = None
        if record is None or record.get("collection") != rules.LINK_COLLECTION:
            raise rules.LinkNotFound(str(link_id))
        if record.get("deleted_at") is not None and not include_deleted:
            raise rules.LinkNotFound(str(link_id))
        return record

    def _open_link(self, link_id: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        """A link a buyer may still be walking through.

        Expiry and revocation are evaluated here, on this request, not once at
        creation - the automation the research states is "Expiry is evaluated on
        every viewer request". A link that expired two seconds ago refuses the
        password, even though it accepted the email.
        """
        record = self._link(link_id, include_deleted=True)
        data = self._data(record)
        if record.get("deleted_at") is not None:
            raise rules.GateDenied(rules.GateDenied.REASON_REVOKED)
        expired, _reason, _moment = rules.expiry_state(data.get("expires_at"), self._now())
        if expired:
            raise rules.GateDenied(rules.GateDenied.REASON_EXPIRED)
        return record, data

    @staticmethod
    def _step_settings(data: Mapping[str, Any], step: str) -> dict[str, Any]:
        """Refuse a step this link does not ask for, or asks for out of order.

        Without this, a link with no password would accept a password that matched
        nothing, and a buyer could present the code step on a link that never
        authenticated. The gate is only a gate if the order is enforced.
        """
        required = rules.steps_required(data)
        if step in required:
            return dict(data)
        if not required:
            raise rules.GateDenied(rules.GateDenied.REASON_NOT_REQUIRED)
        raise rules.GateDenied(rules.GateDenied.REASON_OUT_OF_ORDER)

    def _status(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """The seller's view of one link: settings, expiry, and what it needs."""
        data = self._data(record)
        now = self._now()
        revoked = record.get("deleted_at") is not None
        expired, reason, _ = rules.expiry_state(data.get("expires_at"), now)
        step = rules.gate_step(data, now=now, expired=expired, revoked=revoked)
        return {
            "id": record["id"],
            "room_id": rules.room_ref_of(data, record),
            "title": data.get("title"),
            "target": data.get("target"),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            "revoked": revoked,
            "revoked_at": record.get("deleted_at"),
            "expired": expired,
            "expiry_reason": reason,
            "expires_at": data.get("expires_at"),
            "expires_in_seconds": _remaining_seconds(data.get("expires_at"), now),
            "settings": rules.gated_fields(data),
            "state": step["step"],
            "next_step": step["step"],
            "message": step["message"],
            "preset_id": data.get("preset_id"),
            "preset_fields": list(data.get("preset_fields") or []),
            "preset_overridden": list(data.get("preset_overridden") or []),
            "carried_fields": sorted(
                f for f in data if f in rules.PRESET_COVERED_FIELDS and f not in rules.GATED_FIELDS
            ),
            "password_rotated_at": data.get("password_rotated_at"),
        }

    # -- creation ----------------------------------------------------------- #

    def create_link(
        self,
        room_id: str,
        payload: Mapping[str, Any],
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Create a gated link. Mirrors ``POST /v1/links``.

        ``source`` is required and comes from the route. It is the field the build
        brief names as the recurring defect: a domain function that hardcodes a
        URL as the source of a write leaves the audit log naming a route the app
        stopped serving.
        """
        payload = dict(payload or {})
        if not isinstance(room_id, str) or not room_id:
            raise rules.GateError("a link belongs to a room", {"room_id": "Room is required."})

        target = self._target(payload)

        preset_record = (
            self._preset(str(payload["preset_id"])) if payload.get("preset_id") else None
        )
        preset_data = self._data(preset_record) if preset_record else None
        defaults, from_preset, overridden = rules.resolve_preset(preset_data, payload)
        carried_fields = dict((preset_data or {}).get("fields") or {})

        settings = rules.normalize_settings(payload, base=defaults)
        data: dict[str, Any] = {
            **settings,
            rules.ROOM_REF: room_id,
            "title": str(payload.get("title") or "Buyer link").strip() or "Buyer link",
            "target": target,
            "created_at": self._stamp(),
        }
        if preset_record is not None:
            data["preset_id"] = preset_record["id"]
            data["preset_fields"] = from_preset
            data["preset_overridden"] = overridden
        # Preset-controlled fields this workflow does not enforce are carried
        # across verbatim. See rules.PRESET_COVERED_FIELDS.
        for field in rules.PRESET_COVERED_FIELDS:
            if field in rules.GATED_FIELDS or field in data or field not in carried_fields:
                continue
            data[field] = carried_fields[field]

        record = self.store.create(
            rules.LINK_COLLECTION,
            data,
            record_id=link_secrets.mint_link_id(),
            room_id=room_id,
            actor=actor,
            source=source,
        )
        return self._status(record)

    @staticmethod
    def _target(payload: Mapping[str, Any]) -> dict[str, Any]:
        """Resolve the ``document_id | dataroom_id`` the OpenAPI request carries.

        Exactly one of the two. The schema writes them with a pipe, which reads as
        one of them, and a link that pointed at nothing would mint a URL that
        grants access to nothing - which is worse than a refusal, because it looks
        like it worked.
        """
        given = [bool(payload.get("document_id")), bool(payload.get("dataroom_id"))]
        if given == [True, True]:
            raise rules.GateError(
                "a link points at a document or a dataroom",
                {"document_id": "Give a document_id or a dataroom_id, not both."},
            )
        if given == [False, False]:
            raise rules.GateError(
                "a link needs something to point at",
                {"document_id": "Give a document_id or a dataroom_id."},
            )
        if payload.get("document_id"):
            return {"kind": "document", "id": str(payload["document_id"])}
        return {"kind": "dataroom", "id": str(payload["dataroom_id"])}

    # -- update and revoke -------------------------------------------------- #

    def update_link(
        self,
        link_id: str,
        changes: Mapping[str, Any],
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Rotate the password or move the expiry. Mirrors ``PATCH /v1/links/{id}``.

        Every field is tri-state: absent leaves it alone, ``on``/``off`` or
        ``true``/``false`` sets it, and an explicit ``null`` clears it. That third
        state is not a nicety - "Pass null to override a preset's expiry with
        none" only works if null and absent stay distinguishable, and a link that
        can be set but never cleared cannot be opened up again after a deal
        closes.
        """
        record = self._link(link_id, include_deleted=True)
        if record.get("deleted_at") is not None:
            raise rules.GateDenied(rules.GateDenied.REASON_REVOKED)

        changes = dict(changes or {})
        settings = rules.normalize_settings(changes, base=self._data(record))
        patch = {field: settings[field] for field in _PATCHABLE if field in settings}
        patch["updated_at"] = self._stamp()
        if "password" in changes:
            patch["password_rotated_at"] = self._stamp()

        updated = self.store.update(record["id"], patch, actor=actor, source=source)
        return self._status(updated)

    def revoke_link(self, link_id: str, *, source: str, actor: str | None = None) -> dict[str, Any]:
        """Revoke. Mirrors ``DELETE /v1/links/{id}``.

        "Works immediately. Anyone with the URL gets the expired page on their
        next request." So this is a soft delete - the row, its history and its view
        events all survive - and the gate treats it as closed from the very next
        request, with the same wording an expired link uses.
        """
        record = self._link(link_id)
        deleted = self.store.delete(record["id"], actor=actor, source=source)
        return {
            "id": record["id"],
            "room_id": rules.room_ref_of(self._data(record), record),
            "revoked": True,
            "revoked_at": deleted.get("updated_at") or self._stamp(),
            "next_step": rules.STEP_EXPIRED,
            "message": rules.GateDenied.MESSAGES[rules.GateDenied.REASON_EXPIRED],
        }

    # -- reads -------------------------------------------------------------- #

    def read_link(self, link_id: str) -> dict[str, Any]:
        return self._status(self._link(link_id, include_deleted=True))

    def list_links(self, room_id: str | None = None) -> list[dict[str, Any]]:
        """Live links for a room."""
        if room_id:
            rows = self.store.find(rules.LINK_COLLECTION, {rules.ROOM_REF: room_id}, limit=200)
        else:
            rows = self.store.list(rules.LINK_COLLECTION, limit=200)
        return [self._status(row) for row in rows]

    def list_revoked_links(self, room_id: str | None = None) -> list[dict[str, Any]]:
        """Revoked links, which ``list_links`` cannot return.

        They are soft-deleted, so they are absent from every live read. A seller
        who wants to see "we closed this link and here is when" needs them back,
        and a revoke that leaves no visible trace is a revoke nobody can audit.

        Filtered in Python rather than with ``find``, because ``deleted_at`` is
        part of the record *envelope* and not of ``data``: ``find`` matches on
        indexed JSON paths and would silently return nothing at all.
        """
        rows = self.store.list(rules.LINK_COLLECTION, limit=200, include_deleted=True)
        revoked = [row for row in rows if row.get("deleted_at") is not None]
        if room_id:
            revoked = [row for row in revoked if rules.room_ref_of(self._data(row), row) == room_id]
        return [self._status(row) for row in revoked]

    def list_views(
        self, room_id: str | None = None, link_id: str | None = None
    ) -> list[dict[str, Any]]:
        where: dict[str, Any] = {}
        if room_id:
            where[rules.ROOM_REF] = room_id
        if link_id:
            where["link_id"] = link_id
        return [self._data(row) for row in self.store.find(rules.VIEW_COLLECTION, where, limit=200)]

    def list_notifications(self, room_id: str | None = None) -> list[dict[str, Any]]:
        where = {rules.ROOM_REF: room_id} if room_id else {}
        return [
            self._data(row)
            for row in self.store.find(rules.NOTIFICATION_COLLECTION, where, limit=200)
        ]

    def read_visitor(self, visitor_id: Any) -> dict[str, Any]:
        """Mirrors ``GET /v1/visitors/{id}``: the persisted, verified identity."""
        record = None
        if isinstance(visitor_id, str) and visitor_id:
            try:
                record = self.store.get(visitor_id)
            except Exception:
                record = None
        if record is None or record.get("collection") != rules.VISITOR_COLLECTION:
            raise rules.LinkNotFound(str(visitor_id))
        data = self._data(record)
        return {"id": record["id"], "created_at": record.get("created_at"), **data}

    def summary(self, room_id: str | None = None) -> dict[str, Any]:
        links = self.list_links(room_id)
        revoked = self.list_revoked_links(room_id)
        where = {rules.ROOM_REF: room_id} if room_id else {}
        closed = [link for link in links if link["state"] == rules.STEP_EXPIRED]
        soon = int(timedelta(days=1).total_seconds())
        return {
            "links": len(links),
            "open": len(links) - len(closed),
            "expired": sum(1 for link in closed if not link["revoked"]),
            "revoked": len(revoked),
            "password_protected": sum(1 for link in links if link["settings"]["password_set"]),
            "email_protected": sum(1 for link in links if link["settings"]["email_required"]),
            "email_authenticated": sum(
                1 for link in links if link["settings"]["email_authenticated"]
            ),
            # `expires_in_seconds is None` means the link never expires, and
            # `(None or 0) <= soon` would count every ungated link in the product as
            # "closing today". A board that cries wolf is a board nobody reads.
            "expiring_within_a_day": sum(
                1
                for link in links
                if link["state"] != rules.STEP_EXPIRED
                and link["expires_in_seconds"] is not None
                and link["expires_in_seconds"] <= soon
            ),
            "verified_visitors": self.store.count_where(rules.VISITOR_COLLECTION, where),
            "views": self.store.count_where(rules.VIEW_COLLECTION, where),
            "notifications": self.store.count_where(rules.NOTIFICATION_COLLECTION, where),
            "pending_codes": self.store.count_where(rules.CODE_COLLECTION, {"consumed": False}),
            "presets": self.store.count_where(rules.PRESET_COLLECTION, {}),
            "generated_at": self._now().isoformat(timespec="milliseconds"),
        }

    # -- presets ------------------------------------------------------------ #

    def create_preset(
        self, payload: Mapping[str, Any], *, source: str, actor: str | None = None
    ) -> dict[str, Any]:
        preset = rules.validate_preset(payload)
        record = self.store.create(
            rules.PRESET_COLLECTION,
            {**preset, "created_at": self._stamp()},
            actor=actor,
            source=source,
        )
        return self._preset_view(record)

    def list_presets(self) -> list[dict[str, Any]]:
        return [
            self._preset_view(row) for row in self.store.list(rules.PRESET_COLLECTION, limit=100)
        ]

    def _preset(self, preset_id: str) -> dict[str, Any]:
        try:
            record = self.store.get(preset_id)
        except Exception:
            record = None
        if record is None or record.get("collection") != rules.PRESET_COLLECTION:
            raise rules.GateError(
                "no such link preset", {"preset_id": f"No preset with id {preset_id!r}."}
            )
        return record

    @staticmethod
    def _preset_view(record: Mapping[str, Any]) -> dict[str, Any]:
        data = GateEngine._data(record)
        fields = dict(data.get("fields") or {})
        return {
            "id": record["id"],
            "name": data.get("name"),
            "created_at": data.get("created_at"),
            "fields": {k: v for k, v in fields.items() if k != "password_hash"},
            "password_set": bool(fields.get("password_hash")),
            "covered_fields": list(rules.PRESET_COVERED_FIELDS),
            "gated_fields": list(rules.GATED_FIELDS),
        }

    # -- the buyer gate ----------------------------------------------------- #

    def gate_state(self, link_id: str) -> dict[str, Any]:
        """What the buyer is asked for next.

        Mirrors the first thing the URL does, and answers with the friendly page
        rather than a 404 for a link that has expired or been revoked.
        """
        record = self._link(link_id, include_deleted=True)
        data = self._data(record)
        now = self._now()
        revoked = record.get("deleted_at") is not None
        expired, reason, _ = rules.expiry_state(data.get("expires_at"), now)
        step = rules.gate_step(data, now=now, expired=expired, revoked=revoked)
        return {
            "link_id": record["id"],
            "room_id": rules.room_ref_of(data, record),
            "title": data.get("title"),
            "target": data.get("target"),
            "step": step["step"],
            "message": step["message"],
            "reason": reason,
            "steps": step["required"],
            "password_set": bool(data.get("password_hash")),
            "email_authenticated": bool(data.get("email_authenticated")),
            "expires_at": data.get("expires_at"),
            "revoked": revoked,
        }

    def submit_email(
        self, link_id: str, email: Any, *, source: str, actor: str | None = None
    ) -> dict[str, Any]:
        """Collect the buyer's address and, when authenticated, dispatch a code.

        Mirrors "On each viewer request the browser-supplied email is collected, a
        one-time code is dispatched to that address". A fresh challenge is minted
        per request, so nothing a buyer proved on an earlier pass carries forward
        into this one - including a link they cleared a minute ago.
        """
        record, data = self._open_link(link_id)
        settings = self._step_settings(data, rules.STEP_EMAIL)
        address = rules.normalize_email(email)

        # Minted once, hashed immediately, and held only in this local. It is
        # never written to a record and never returned in a response; it reaches
        # the buyer through ``_dispatch`` and nowhere else.
        code = link_secrets.mint_code() if rules.code_required(settings) else ""

        challenge_data: dict[str, Any] = {
            "link_id": record["id"],
            rules.ROOM_REF: rules.room_ref_of(data, record),
            "email": address,
            "email_verified": False,
            "consumed": False,
            "issued_at": self._stamp(),
            "requires_code": bool(code),
        }
        if code:
            challenge_data["code_hash"] = link_secrets.hash_code(code)

        challenge = self.store.create(
            rules.CODE_COLLECTION, challenge_data, actor=actor, source=source
        )

        if code:
            self._dispatch(challenge, address, code, source=source, actor=actor)
            return {
                "link_id": record["id"],
                "challenge_id": challenge["id"],
                "email": address,
                "step": rules.STEP_CODE,
                "email_verified": False,
                "code_dispatched": True,
                "message": f"We sent a code to {address}. Enter it to continue.",
            }

        if not rules.password_required(settings):
            # Nothing left to ask, so this step is the grant.
            return self._grant(
                record, data, self._data(challenge), challenge["id"], source=source, actor=actor
            )
        return {
            "link_id": record["id"],
            "challenge_id": challenge["id"],
            "email": address,
            "step": rules.STEP_PASSWORD,
            "email_verified": False,
            "code_dispatched": False,
            "message": f"Continue as {address}.",
        }

    def submit_code(
        self,
        link_id: str,
        challenge_id: Any,
        code: Any,
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Verify the one-time code server-side.

        "the code is verified server-side, then the password is compared" - so a
        correct code never itself opens anything; it moves the buyer to the
        password step. On success the verified email is persisted as a visitor,
        which is the row ``GET /v1/visitors/{id}`` reads back.
        """
        record, data = self._open_link(link_id)
        settings = self._step_settings(data, rules.STEP_CODE)
        challenge = self._challenge(record["id"], challenge_id)
        challenge_data = self._data(challenge)

        if challenge_data.get("consumed"):
            # The hash is still on the row, so without this a buyer who kept the
            # code could present it again and verify twice. "One-time" has to mean
            # the row is spent, not just that the wrong answer burns it.
            raise rules.GateDenied(rules.GateDenied.REASON_CODE)

        if not isinstance(code, str) or not link_secrets.verify_code(
            code.strip(), challenge_data.get("code_hash")
        ):
            # A refused code is consumed anyway. Leaving it live would let one
            # challenge absorb unlimited guesses, and the research specifies no
            # attempt cap to bound that - so the single-use property does the work
            # instead. The buyer re-enters their address and gets a fresh code,
            # which is the round trip the flow already makes on any new visit.
            self.store.update(
                challenge["id"],
                {"consumed": True, "consumed_at": self._stamp(), "outcome": "code_rejected"},
                actor=actor,
                source=source,
            )
            raise rules.GateDenied(rules.GateDenied.REASON_CODE)

        visitor = self._record_visitor(record, challenge_data, source=source, actor=actor)
        self.store.update(
            challenge["id"],
            {
                "consumed": True,
                "consumed_at": self._stamp(),
                "outcome": "code_accepted",
                "email_verified": True,
                "visitor_id": visitor["id"],
            },
            actor=actor,
            source=source,
        )
        verified_challenge = {**challenge_data, "email_verified": True, "visitor_id": visitor["id"]}

        if not rules.password_required(settings):
            return self._grant(
                record, data, verified_challenge, challenge["id"], source=source, actor=actor
            )
        return {
            "link_id": record["id"],
            "challenge_id": challenge["id"],
            "email": challenge_data["email"],
            "email_verified": True,
            "visitor_id": visitor["id"],
            "step": rules.STEP_PASSWORD,
            "message": "Email confirmed. Enter the link password to continue.",
        }

    def submit_password(
        self,
        link_id: str,
        password: Any,
        *,
        challenge_id: Any = None,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Compare the password, and on success release the view.

        The password is the last step, so this is where the gate closes: a granted
        view session is minted, the verified email is stamped onto the view event,
        and the team is notified because ``enable_notification`` defaults on.
        """
        record, data = self._open_link(link_id)
        settings = self._step_settings(data, rules.STEP_PASSWORD)

        challenge = self._preconditions(record["id"], settings, challenge_id)
        challenge_data = self._data(challenge)

        stored = data.get("password_hash")
        candidate = password if isinstance(password, str) else ""
        if not stored or not link_secrets.verify_password(candidate, stored):
            if challenge:
                # Mark the challenge, not the link. The buyer's attempt belongs to
                # the run they are making, and a link has no business recording how
                # many times somebody guessed at it.
                self.store.update(
                    challenge["id"],
                    {"outcome": "password_rejected"},
                    actor=actor,
                    source=source,
                )
            raise rules.GateDenied(rules.GateDenied.REASON_PASSWORD)

        return self._grant(
            record, data, challenge_data, challenge.get("id"), source=source, actor=actor
        )

    def _preconditions(
        self,
        link_id: str,
        settings: Mapping[str, Any],
        challenge_id: Any,
    ) -> dict[str, Any]:
        """The challenge this password submission is attached to, if any.

        Two refusals the step-order check alone cannot make: submitting a password
        with no challenge on a link that asks for an email, and submitting it with
        an *unverified* challenge on a link that authenticates. Returns the record
        rather than its payload so the caller can write back to it by id.
        """
        if not rules.email_required(settings):
            return {}
        if not challenge_id:
            raise rules.GateDenied(rules.GateDenied.REASON_EMAIL_REQUIRED)
        challenge = self._challenge(link_id, challenge_id)
        if rules.code_required(settings) and not self._data(challenge).get("email_verified"):
            raise rules.GateDenied(rules.GateDenied.REASON_EMAIL_UNVERIFIED)
        return challenge

    def _challenge(self, link_id: str, challenge_id: Any) -> dict[str, Any]:
        record = None
        if isinstance(challenge_id, str) and challenge_id:
            try:
                record = self.store.get(challenge_id)
            except Exception:
                record = None
        if (
            record is None
            or record.get("collection") != rules.CODE_COLLECTION
            or self._data(record).get("link_id") != link_id
        ):
            # A challenge is bound to the link that minted it, so one link's code
            # cannot be presented to a different link's gate.
            raise rules.GateDenied(rules.GateDenied.REASON_CODE)
        return record

    def _dispatch(
        self,
        challenge: Mapping[str, Any],
        address: str,
        code: str,
        *,
        source: str,
        actor: str | None,
    ) -> None:
        """Record the dispatch and hand the code to the transport.

        The delivery row carries the recipient, the challenge, the time and the
        transport name - and not the code, not even the last four digits. Six
        digits with four of them recorded is a hundred-candidate secret, and "only
        for support" is how secrets end up in support tickets.
        """
        challenge_data = self._data(challenge)
        transport = getattr(self.deliver, "__name__", type(self.deliver).__name__)
        self.store.create(
            rules.DELIVERY_COLLECTION,
            {
                "delivery_id": link_secrets.mint_delivery_id(),
                "challenge_id": challenge["id"],
                "link_id": challenge_data.get("link_id"),
                "to": address,
                "status": "dispatched",
                "transport": transport,
                "dispatched_at": self._stamp(),
            },
            actor=actor,
            source=source,
        )
        # The only place in the product where a cleartext code exists outside the
        # buyer's inbox, and it is gone the moment this returns.
        self.deliver(
            {
                "to": address,
                "link_id": challenge_data.get("link_id"),
                "challenge_id": challenge["id"],
                "code": code,
            }
        )

    def _grant(
        self,
        link: Mapping[str, Any],
        data: Mapping[str, Any],
        challenge_data: Mapping[str, Any],
        challenge_id: str | None,
        *,
        source: str,
        actor: str | None,
    ) -> dict[str, Any]:
        """Close the gate: mint a view session, stamp the view, notify the team.

        Shared by every path that finishes the walk, because there is exactly one
        definition of "the buyer is through" and three callers should not each be
        able to write their own.
        """
        email = challenge_data.get("email")
        verified = bool(challenge_data.get("email_verified"))
        visitor = None
        if email and verified:
            visitor = self._record_visitor(link, challenge_data, source=source, actor=actor)

        token = link_secrets.mint_view_token()
        room_ref = rules.room_ref_of(data, link)
        session = self.store.create(
            rules.SESSION_COLLECTION,
            {
                "link_id": link["id"],
                rules.ROOM_REF: room_ref,
                "email": email,
                "email_verified": verified,
                "visitor_id": visitor["id"] if visitor else challenge_data.get("visitor_id"),
                "token_hash": link_secrets.hash_token(token),
                # What was actually asked of this buyer, which is the link's own
                # requirement list - not a hand-written list that could drift.
                "granted_via": rules.steps_required(data),
                "issued_at": self._stamp(),
            },
            actor=actor,
            source=source,
        )
        view = self._record_view(
            link, data, email=email, verified=verified, visitor=visitor, source=source, actor=actor
        )
        return {
            "link_id": link["id"],
            "granted": True,
            "step": rules.STEP_OPEN,
            "challenge_id": challenge_id,
            "view_token": token,
            "session_id": session["id"],
            "email": email,
            "email_verified": verified,
            "visitor_id": view["visitor_id"],
            "view_id": view["id"],
            "notified": view["notified"],
            "document_url": f"{prefix()}/links/{link['id']}/document",
        }

    def _record_visitor(
        self,
        link: Mapping[str, Any],
        challenge_data: Mapping[str, Any],
        *,
        source: str,
        actor: str | None,
    ) -> dict[str, Any]:
        """Persist the verified email as a ``Visitor`` row.

        Keyed on link *and* address. Verification on one link is evidence about
        one inbox in front of one deal; carrying it to a different link would turn
        a buyer who proved they were real into a buyer who is trusted everywhere,
        which is not what the research describes and not a property any seller
        would expect.
        """
        address = str(challenge_data.get("email"))
        existing = self.store.find(
            rules.VISITOR_COLLECTION, {"link_id": link["id"], "email": address}, limit=1
        )
        now = self._stamp()
        room_ref = rules.room_ref_of(self._data(link), link)
        if existing:
            # `last_seen_at` only, deliberately. The counter is views, and a view is
            # recorded in `_record_view` - counting a visit here as well would mean
            # a buyer who authenticated and then was refused the password inflated
            # their own view count, and nobody had viewed anything.
            return self.store.update(
                existing[0]["id"],
                {"verified": True, "last_seen_at": now},
                actor=actor,
                source=source,
            )
        return self.store.create(
            rules.VISITOR_COLLECTION,
            {
                "link_id": link["id"],
                rules.ROOM_REF: room_ref,
                "email": address,
                "verified": True,
                "verification_method": "one_time_code",
                "first_seen_at": now,
                "last_seen_at": now,
                "view_count": 0,
            },
            room_id=room_ref,
            actor=actor,
            source=source,
        )

    def _record_view(
        self,
        link: Mapping[str, Any],
        data: Mapping[str, Any],
        *,
        email: str | None,
        verified: bool,
        visitor: Mapping[str, Any] | None,
        source: str,
        actor: str | None,
    ) -> dict[str, Any]:
        """Stamp the view and, because the default is on, notify the team.

        "`enable_notification` defaults to on, so the team is notified on each view
        of the link." The view row is written whether or not notification is on -
        the view happened either way - and it records which, so a seller reading
        the audit log is never left guessing why no notification arrived.
        """
        room_ref = rules.room_ref_of(data, link)
        notified = bool(data.get("enable_notification", rules.DEFAULT_ENABLE_NOTIFICATION))
        stamp = self._stamp()
        view = self.store.create(
            rules.VIEW_COLLECTION,
            {
                "link_id": link["id"],
                rules.ROOM_REF: room_ref,
                "email": email,
                "email_verified": verified,
                "visitor_id": visitor["id"] if visitor else None,
                "viewed_at": stamp,
                "notified": notified,
            },
            room_id=room_ref,
            actor=actor,
            source=source,
        )
        if visitor is not None:
            # The view is what the counter counts, so it is incremented here and
            # nowhere else.
            self.store.update(
                visitor["id"],
                {"view_count": int(self._data(visitor).get("view_count") or 0) + 1},
                actor=actor,
                source=source,
            )
        if notified:
            self.store.create(
                rules.NOTIFICATION_COLLECTION,
                {
                    "link_id": link["id"],
                    rules.ROOM_REF: room_ref,
                    "kind": "link_viewed",
                    "title": data.get("title"),
                    "viewer_email": email,
                    "email_verified": verified,
                    "visitor_id": visitor["id"] if visitor else None,
                    "notified_at": stamp,
                },
                room_id=room_ref,
                actor=actor,
                source=source,
            )
        return {
            "id": view["id"],
            "visitor_id": visitor["id"] if visitor else None,
            "notified": notified,
        }

    def read_document(self, link_id: str, token: Any, *, source: str) -> dict[str, Any]:
        """Release the document: "only then is the document content streamed".

        The gate is re-evaluated here rather than trusted from the session: a
        session granted at 09:00 must not survive a link that expired at 09:30,
        because expiry is evaluated on every viewer request - and this is a viewer
        request.
        """
        record, data = self._open_link(link_id)
        if not isinstance(token, str) or not token:
            raise rules.GateDenied(rules.GateDenied.REASON_SESSION)

        matches = self.store.find(
            rules.SESSION_COLLECTION,
            {"link_id": record["id"], "token_hash": link_secrets.hash_token(token)},
            limit=1,
        )
        if not matches:
            raise rules.GateDenied(rules.GateDenied.REASON_SESSION)
        session = self._data(matches[0])

        target = data.get("target") or {}
        kind = str(target.get("kind") or "dataroom")
        collection = "document" if kind == "document" else "dataroom"
        try:
            resolved = self.store.get(str(target.get("id") or ""))
        except Exception:
            resolved = None
        content = None
        if resolved is not None and resolved.get("collection") == collection:
            content = resolved.get("data")

        return {
            "link_id": record["id"],
            "target": target,
            "resolved": content is not None,
            "content": link_secrets.redact(content),
            "viewer": {
                "email": session.get("email"),
                "email_verified": bool(session.get("email_verified")),
                "visitor_id": session.get("visitor_id"),
                "granted_at": session.get("issued_at"),
            },
            "steps_cleared": session.get("granted_via"),
        }


#: Codes are minted once and hashed immediately: see the local in
#: :meth:`GateEngine.submit_email`. There is no module-level slot holding a
#: cleartext code, because a slot outlives the request that filled it and the one
#: thing this workflow must never do is leave a secret lying around between calls.


def _remaining_seconds(expires_at: Any, now: datetime) -> int | None:
    try:
        window = rules.remaining_window(expires_at, now)
    except rules.GateError:
        return None
    if window is None:
        return None
    return max(0, int(window.total_seconds()))


def prefix() -> str:
    """The mounted prefix, for the URL handed back on a grant.

    Read off the router rather than written as a literal, so the URL a buyer is
    told to follow cannot name a path the host stopped serving. That is the same
    defect as a hardcoded ``source=``, one layer over.
    """
    from dsr.features.wf069_gate_each_buyer_link_with_a_password_a import router

    return router.prefix


def redacted(value: Any) -> Any:
    """Public alias for :func:`dsr.link_gating.secrets.redact`."""
    return link_secrets.redact(value)
