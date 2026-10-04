"""The engine: the store-facing half of WF-067, and nothing above it.

Every write goes through :class:`~dsr.store.RecordStore`, which is
:class:`~dsr.db.audited.AuditedDatabase` behind a schema-flexible facade, so every
write writes its audit row in the same transaction. This module never opens a
connection and never imports the app.

``source`` is a **required keyword** on every writing method. A URL string typed
into a domain method is a defect rather than a convenience: this codebase has
already shipped a feature whose audit log kept naming a route the app had
stopped serving, and the only defence is to make omitting it a ``TypeError`` at
the call site rather than an untraceable row three months later.

Every record is ordinary JSON in ``data``. There is no migration and no typed
column, so a team adding a field needs no coordination with anyone.
"""

from __future__ import annotations

import secrets
import string
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from dsr.scheduling_meetings.envelopes import (
    EXTERNAL_ID_RANDOM_CHARS,
    approver_block,
    build_recipients,
    external_id_for,
    read_external_id,
    require_external_id,
    secret_matches,
    signing_unlocked,
)
from dsr.scheduling_meetings.errors import (
    DuplicateEvent,
    EventBeforeDistribution,
    MalformedEvent,
    NoSuchPlan,
    NoSuchRecipient,
    NoSuchTemplate,
    PlanStateConflict,
    SignersBlocked,
    UnauthenticatedEvent,
    UnresolvedPlan,
)
from dsr.scheduling_meetings.events import (
    OUTCOME_APPLIED,
    OUTCOME_DUPLICATE,
    OUTCOME_NOTED,
    RECIPIENT_OPENED,
    RECIPIENT_UNOPENED,
    apply_event,
    event_fingerprint,
    require_known_event,
)
from dsr.scheduling_meetings.inferences import inferences
from dsr.scheduling_meetings.vocabulary import (
    COLLECTION_EVENT,
    COLLECTION_NOTICE,
    COLLECTION_PLAN,
    COLLECTION_RECIPIENT,
    COLLECTION_TEMPLATE,
    DIRECT_EMBED_URL,
    DIRECT_LINK_URL,
    EVENT_DOCUMENT_COMPLETED,
    EVENT_SOURCE_DIRECT_LINK,
    INVITE_PATH_EMAIL,
    INVITE_PATH_EMBED,
    INVITE_PATH_REDIRECT,
    MILESTONE_APPROVED,
    MILESTONE_AWAITING_SIGNATURE,
    MILESTONE_CANCELLED,
    MILESTONE_DRAFT,
    MILESTONE_REFUSED_BY_APPROVER,
    MILESTONE_REFUSED_BY_SIGNER,
    SIGNER,
    SIGNING_ORDERS,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_DRAFT,
    STATUS_PENDING,
    STATUS_REJECTED,
    describe,
)

#: Milestones nobody can move a plan out of.
TERMINAL_MILESTONES: tuple[str, ...] = (
    MILESTONE_APPROVED,
    MILESTONE_REFUSED_BY_SIGNER,
    MILESTONE_REFUSED_BY_APPROVER,
    MILESTONE_CANCELLED,
)

_TOKEN_ALPHABET = string.ascii_lowercase + string.digits


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime | None = None) -> str:
    return (moment or _now()).isoformat(timespec="seconds")


class MapEngine:
    """A mutual action plan, its recipients, and the events that move it."""

    def __init__(self, store: Any, clock: Any = None) -> None:
        self.store = store
        self._clock = clock or _now

    # ------------------------------------------------------------------ #
    # Vocabulary
    # ------------------------------------------------------------------ #

    def vocabulary(self) -> dict[str, Any]:
        """Every published value, served as data."""
        return describe()

    def inferences(self) -> dict[str, Any]:
        """Every judgement call this workflow rests on."""
        return inferences()

    # ------------------------------------------------------------------ #
    # Templates
    # ------------------------------------------------------------------ #

    def create_template(
        self,
        room_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Step 1: the seller-built template, with its recipient roles and fields.

        "Seller builds the MAP/agreement **Template** (a PDF) with recipient roles
        and fields." What the room stores is the field geometry and the roles, not
        the bytes: the research makes the PDF the seller's to upload, and what the
        room has to check is the shape of what will be placed on it.
        """
        name = str(payload.get("name") or "").strip()
        if not name:
            raise MalformedEvent("a template needs a name")

        roles = payload.get("roles") or [SIGNER]
        if not isinstance(roles, list):
            raise MalformedEvent("template roles must be a list")

        fields = payload.get("fields") or []
        if not isinstance(fields, list):
            raise MalformedEvent("template fields must be a list")

        # Validated through the same path a plan's fields take, so a template that
        # would produce an unusable field is refused before any plan uses it.
        from dsr.scheduling_meetings.envelopes import validate_field

        row = self.store.create(
            COLLECTION_TEMPLATE,
            {
                "name": name,
                "document_name": str(payload.get("document_name") or f"{name}.pdf"),
                "roles": [str(role).strip().upper() for role in roles],
                "fields": [validate_field(field, index) for index, field in enumerate(fields)],
                "redirect_url": str(payload.get("redirect_url") or ""),
                "invite_path": str(payload.get("invite_path") or INVITE_PATH_EMBED),
                "notes": str(payload.get("notes") or ""),
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
        return self._template_view(row)

    def template(self, room_id: str, template_id: str) -> dict[str, Any]:
        """One template, by this room's record id."""
        row = self.store.get(template_id)
        if row is None or row.get("deleted_at") or row.get("room_id") != room_id:
            raise NoSuchTemplate(f"no template {template_id} in room {room_id}")
        if row.get("collection") != COLLECTION_TEMPLATE:
            raise NoSuchTemplate(f"record {template_id} is not a template")
        return self._template_view(row)

    def templates(self, room_id: str) -> list[dict[str, Any]]:
        """Every live template in a room."""
        return [
            self._template_view(row)
            for row in self.store.list(COLLECTION_TEMPLATE, room_id=room_id, limit=200)
        ]

    # ------------------------------------------------------------------ #
    # Plans
    # ------------------------------------------------------------------ #

    def create_plan(
        self,
        room_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Step 2: the envelope, its recipients and its fields, in one call.

        "Seller creates the envelope via the API in one call ... supplying
        ``recipients[]`` each with ``email``, ``name``, ``role`` and ``fields[]``."
        The research makes this one request, so this is one room action: one plan
        and every recipient on it.

        The plan is created ``DRAFT``. "After distribution, recipients receive an
        email with a link to sign the document. The document status changes from
        ``DRAFT`` to ``PENDING``", so a plan that exists has not gone anywhere
        yet, and nothing it says can be believed until it has.
        """
        subject = str(payload.get("subject") or "").strip()
        if not subject:
            raise MalformedEvent("a plan needs a subject")

        order = str(payload.get("signing_order") or "PARALLEL").strip().upper()
        if order not in SIGNING_ORDERS:
            raise MalformedEvent(
                f"signing_order {order!r} is not one of {', '.join(SIGNING_ORDERS)}"
            )

        invite_path = str(payload.get("invite_path") or INVITE_PATH_EMBED).strip().lower()
        if invite_path not in (INVITE_PATH_EMBED, INVITE_PATH_REDIRECT, INVITE_PATH_EMAIL):
            raise MalformedEvent(
                f"invite_path {invite_path!r} is not one of embed, redirect, email"
            )

        recipients = build_recipients(payload.get("recipients") or [])

        template_id = str(payload.get("template_id") or "").strip()
        if template_id:
            self.template(room_id, template_id)

        # The record id is generated here rather than left to the store, because
        # the join key is derived from it and the key has to be written in the
        # same insert as the plan.
        draft_id = f"plan_{secrets.token_hex(8)}"
        supplied = str(payload.get("external_id") or "").strip()
        external_id = (
            require_external_id(supplied)
            if supplied
            else external_id_for(
                room_id,
                draft_id,
                # Not a secret: an unguessable suffix, so two plans in one room can
                # never collide and an event cannot name one it was not sent for.
                "".join(secrets.choice(_TOKEN_ALPHABET) for _ in range(EXTERNAL_ID_RANDOM_CHARS)),  # noqa: S311
            )
        )

        plan = self.store.create(
            COLLECTION_PLAN,
            {
                "subject": subject,
                "message": str(payload.get("message") or ""),
                "template_id": template_id,
                "external_id": external_id,
                "signing_order": order,
                "invite_path": invite_path,
                "distribution_method": str(payload.get("distribution_method") or ""),
                "redirect_url": str(payload.get("redirect_url") or ""),
                "webhook_secret": str(payload.get("webhook_secret") or ""),
                "milestone": MILESTONE_DRAFT,
                "status": STATUS_DRAFT,
                "distributed_at": "",
                "completed_at": "",
                "owner": str(payload.get("owner") or actor or ""),
                "notes": str(payload.get("notes") or ""),
            },
            room_id=room_id,
            record_id=draft_id,
            actor=actor,
            source=source,
        )

        # One recipient row per party, each naming its plan. Written as one
        # transaction so a plan and its people cannot end up half-created.
        self.store.bulk_create(
            COLLECTION_RECIPIENT,
            [
                {
                    "plan_id": plan["id"],
                    "external_id": external_id,
                    "email": entry["email"],
                    "name": entry["name"],
                    "role": entry["role"],
                    "party": entry["party"],
                    "signing_order": entry["signing_order"],
                    "fields": entry["fields"],
                    "status": RECIPIENT_UNOPENED,
                    "reminder_count": 0,
                }
                for entry in recipients
            ],
            room_id=room_id,
            actor=actor,
            source=source,
        )
        return self.plan_view(room_id, plan["id"])

    def distribute(
        self,
        room_id: str,
        plan_id: str,
        *,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Step 3: distribute. The plan leaves ``DRAFT`` for ``PENDING``.

        "Seller distributes: ``POST /api/v2/envelope/distribute`` with
        ``{envelopeId}`` -> status ``DRAFT`` to ``PENDING``, recipients get a
        signing link."

        This is also where the approver gate becomes real: before distribution,
        :meth:`can_sign` refuses because nobody has a link, and after it, it
        refuses because the approver has not approved.
        """
        plan = self._require_plan(room_id, plan_id)
        if plan.get("milestone") in TERMINAL_MILESTONES:
            raise PlanStateConflict(
                f"plan {plan_id} is {plan['milestone']} and the research has no "
                "transition out of it"
            )
        if plan.get("distributed_at"):
            from dsr.scheduling_meetings.errors import AlreadyDistributed

            raise AlreadyDistributed(f"plan {plan_id} was already distributed")

        recipients = self.recipients(room_id, plan_id)
        stamp = _iso(self._clock())
        updated = self.store.update(
            plan["id"],
            {
                "milestone": MILESTONE_AWAITING_SIGNATURE,
                "status": STATUS_PENDING,
                "distributed_at": stamp,
            },
            actor=actor,
            source=source,
        )
        self._notify(
            room_id,
            updated["id"],
            "plan_distributed",
            f"The plan went out to {len(recipients)} recipient(s).",
            actor=actor,
            source=source,
        )
        return self.plan_view(room_id, updated["id"])

    def cancel_plan(
        self,
        room_id: str,
        plan_id: str,
        *,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """The seller pulls the plan. A terminal milestone, like the research's cancel.

        "``POST /api/v2/envelope/cancel`` (used by ``DOCUMENT_CANCELLED``)". The
        vendor event is what actually stops the signing links; this records the
        room-side decision that asked for it.
        """
        plan = self._require_plan(room_id, plan_id)
        if plan.get("milestone") in TERMINAL_MILESTONES:
            raise PlanStateConflict(f"plan {plan_id} is already {plan['milestone']}")
        updated = self.store.update(
            plan["id"],
            {"milestone": MILESTONE_CANCELLED, "status": STATUS_CANCELLED},
            actor=actor,
            source=source,
        )
        self._notify(
            room_id,
            plan_id,
            "plan_cancelled",
            "The plan was cancelled.",
            actor=actor,
            source=source,
        )
        return self.plan_view(room_id, updated["id"])

    # ------------------------------------------------------------------ #
    # Reading
    # ------------------------------------------------------------------ #

    def _require_plan(self, room_id: str, plan_id: str) -> dict[str, Any]:
        """A plan in this room, or a refusal.

        Soft-deleted plans still resolve by ``external_id`` for the events that
        named them, so a plan that was removed does not leave its own history
        unresolvable. Deleted plans are refused here and read through
        :meth:`resolve_plan` instead.
        """
        row = self.store.get(plan_id)
        if row is None or row.get("room_id") != room_id or row.get("collection") != COLLECTION_PLAN:
            raise NoSuchPlan(f"no plan {plan_id} in room {room_id}")
        if row.get("deleted_at"):
            raise NoSuchPlan(f"plan {plan_id} was deleted")
        # The record's own fields, flattened together with its id, so a caller
        # reading ``plan["milestone"]`` reads this plan's milestone and not one of
        # the envelope's own keys, which is the mistake this shape invites.
        return dict(row["data"], id=row["id"], revision=row.get("revision"))

    def recipients(self, room_id: str, plan_id: str) -> list[dict[str, Any]]:
        """Everyone on a plan, in signing order then by role."""
        rows = self.store.find(
            COLLECTION_RECIPIENT, {"plan_id": plan_id}, limit=200, include_deleted=True
        )
        order = {SIGNER: 0, "APPROVER": 0, "ASSISTANT": 1, "CC": 2, "VIEWER": 3}
        return sorted(
            (dict(row["data"], id=row["id"], deleted_at=row.get("deleted_at")) for row in rows),
            key=lambda entry: (
                order.get(str(entry.get("role")), 9),
                int(entry.get("signing_order") or 1),
                str(entry.get("email")),
            ),
        )

    def plan_view(self, room_id: str, plan_id: str) -> dict[str, Any]:
        """A plan with its recipients, its gate, and its links."""
        plan = self._require_plan(room_id, plan_id)
        recipients = self.recipients(room_id, plan_id)
        unlocked, blockers = signing_unlocked(recipients)
        return {
            "id": plan["id"],
            "room_id": room_id,
            "revision": plan.get("revision"),
            "milestone": plan.get("milestone"),
            "status": plan.get("status"),
            "signing_unlocked": unlocked,
            "blocking_approvers": blockers,
            "approver_pending": approver_block(recipients),
            "links": self.links_for(plan, recipients),
            "plan": dict(plan),
            "recipients": recipients,
        }

    def links_for(
        self, plan: Mapping[str, Any], recipients: Iterable[Mapping[str, Any]]
    ) -> dict[str, Any]:
        """The signing and embedding URLs, built from the research's two patterns.

        "URL ``https://app.documenso.com/d/{token}``, embed
        ``https://app.documenso.com/embed/direct/{token}``". The token is this
        room's own, derived with the external id, so a URL is reproducible from a
        plan record rather than stored twice.
        """
        external = str(plan.get("external_id") or "")
        # The vendor's token is a single path segment, so a dotted join key cannot
        # be used as one. The random tail of a derived key is the closest thing
        # to it the room holds, and a caller who supplied their own external id
        # has a token of their own to put here.
        token = external.rsplit(".", 1)[-1] if "." in external else external
        path = str(plan.get("invite_path") or INVITE_PATH_EMBED)
        return {
            "invite_path": path,
            "signing_url": f"{DIRECT_LINK_URL.format(token=token)}?externalId={external}",
            "embed_url": f"{DIRECT_EMBED_URL.format(token=token)}?externalId={external}",
            "redirect_url": str(plan.get("redirect_url") or ""),
            "source": EVENT_SOURCE_DIRECT_LINK,
        }

    def can_sign(self, room_id: str, plan_id: str, email: str) -> dict[str, Any]:
        """May this person sign right now? The approver gate, read per person.

        "APPROVER | Must approve before signers can sign." A signer who arrives
        early is not forbidden - the plan is not ready - so this reports the
        decision rather than raising, and the caller turns it into 409.
        """
        plan = self._require_plan(room_id, plan_id)
        recipients = self.recipients(room_id, plan_id)
        unlocked, blockers = signing_unlocked(recipients)
        target = next(
            (entry for entry in recipients if str(entry.get("email")).lower() == email.lower()),
            None,
        )
        if target is None:
            raise NoSuchRecipient(f"{email} is not on plan {plan_id}")

        if not plan.get("distributed_at"):
            return {
                "can_sign": False,
                "reason": "not_distributed",
                "detail": "The plan has not been distributed yet, so there is nothing to sign.",
            }
        if not unlocked and str(target.get("role")) != "APPROVER":
            return {
                "can_sign": False,
                "reason": "awaiting_approver",
                "detail": " ".join(blockers),
                "blocking_approvers": blockers,
            }
        return {"can_sign": True, "reason": "ready", "detail": ""}

    # ------------------------------------------------------------------ #
    # Events
    # ------------------------------------------------------------------ #

    def resolve_plan(self, room_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        """The plan a webhook names, by the research's join key.

        "``externalId`` is the join key back to the deal room." An event with no
        usable key in a room with more than one plan cannot be resolved, and that
        is a fact about the event worth keeping, so it is refused with
        :class:`~dsr.scheduling_meetings.errors.UnresolvedPlan` rather than
        dropped.
        """
        external = read_external_id(payload)
        if not external:
            raise UnresolvedPlan(
                "the event carried no externalId, and the research names it as the "
                "join key back to the deal room"
            )
        rows = self.store.find(
            COLLECTION_PLAN, {"external_id": external}, limit=5, include_deleted=True
        )
        exact = [row for row in rows if row.get("room_id") == room_id]
        if len(exact) == 1:
            return dict(exact[0]["data"], id=exact[0]["id"])
        if not exact:
            raise UnresolvedPlan(f"externalId {external!r} names no plan in room {room_id}")
        raise UnresolvedPlan(f"externalId {external!r} names {len(exact)} plans in room {room_id}")

    def receive_event(
        self,
        room_id: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        *,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Steps 4 to 6: one event, authenticated, deduplicated, applied.

        The researched data flow, in order:

        1. **Verify.** "Check the ``X-Documenso-Secret`` header matches your
           configured secret." A failure raises
           :class:`~dsr.scheduling_meetings.errors.UnauthenticatedEvent` and
           **writes nothing at all**.
        2. **Resolve.** The ``externalId`` join key names the plan.
        3. **Deduplicate.** "Webhooks may be retried, so handle duplicate events."
           A repeat is answered as a duplicate and applies nothing twice.
        4. **Apply.** The event moves a recipient's status or the plan's
           milestone.
        5. **React.** "The sales room consumes ``DOCUMENT_COMPLETED`` ... to flip
           the MAP milestone to *Approved*, advance the plan, and notify the
           owner."
        """
        plan = self.resolve_plan(room_id, payload)
        configured = str(plan.get("webhook_secret") or "")

        if not configured:
            # Checked before anything is written, and said plainly rather than
            # accepting any secret.
            raise UnauthenticatedEvent(
                f"plan {plan['id']} has no webhook_secret, so no event can be "
                "authenticated for it. Set one when the plan is created."
            )
        presented = _header(headers, "X-Documenso-Secret")
        if not secret_matches(presented, configured):
            raise UnauthenticatedEvent(
                "the X-Documenso-Secret header did not match the secret configured for this plan"
            )

        event = require_known_event(payload.get("event") or payload.get("type"))
        body = payload.get("payload") if isinstance(payload.get("payload"), Mapping) else payload
        fingerprint = event_fingerprint(event, payload)

        recorded = self.store.find(
            COLLECTION_EVENT, {"fingerprint": fingerprint}, limit=1, include_deleted=True
        )
        if recorded:
            existing = recorded[0]
            attempts = int(existing["data"].get("attempts") or 1)
            self.store.update(
                existing["id"],
                {"attempts": attempts + 1, "last_seen_at": _iso(self._clock())},
                actor=actor or "vendor",
                source=source,
            )
            raise DuplicateEvent(
                f"event {fingerprint!r} was already taken; this delivery was delivery "
                f"number {attempts + 1} and changed nothing"
            )

        event_row = self.store.create(
            COLLECTION_EVENT,
            {
                "fingerprint": fingerprint,
                "event": event,
                "plan_id": plan["id"],
                "external_id": read_external_id(payload),
                "payload": dict(body),
                "attempts": 1,
                "received_at": _iso(self._clock()),
                "last_seen_at": _iso(self._clock()),
            },
            room_id=room_id,
            actor=actor or "vendor",
            source=source,
        )

        recipients = self.recipients(room_id, plan["id"])
        if event in (
            "DOCUMENT_OPENED",
            "DOCUMENT_SIGNED",
            "DOCUMENT_RECIPIENT_COMPLETED",
            "DOCUMENT_REJECTED",
            "RECIPIENT_EXPIRED",
            "DOCUMENT_REMINDER_SENT",
        ):
            if not plan.get("distributed_at"):
                raise EventBeforeDistribution(
                    f"plan {plan['id']} has not been distributed, so {event} arrived early "
                    "and was recorded but applied to nobody"
                )

        decision = apply_event(event, plan, recipients, body)

        if decision["recipient_patch"] and decision.get("recipient_email"):
            wanted = str(decision["recipient_email"]).lower()
            target = next(
                (entry for entry in recipients if str(entry.get("email") or "").lower() == wanted),
                None,
            )
            if target is not None:
                patch = dict(decision["recipient_patch"])
                if "reminder_count" in patch:
                    patch["reminder_count"] = int(target.get("reminder_count") or 0) + 1
                self.store.update(target["id"], patch, actor=actor or "vendor", source=source)

        milestone = decision["milestone"]
        if milestone:
            self.store.update(
                plan["id"],
                {"milestone": milestone, **self._status_for(milestone)},
                actor=actor or "vendor",
                source=source,
            )
            recipients = self.recipients(room_id, plan["id"])

        if decision["notice_reason"]:
            self._notify(
                room_id,
                plan["id"],
                decision["notice_reason"],
                " ".join(decision["notes"]) or str(decision["notice_reason"]),
                actor=actor,
                source=source,
            )

        # "advance the plan": completion is also recorded on the event row, so the
        # vendor's own completion timestamp survives.
        if milestone == MILESTONE_APPROVED:
            self.store.update(
                event_row["id"],
                {"completed_plan": plan["id"]},
                actor=actor or "vendor",
                source=source,
            )

        return {
            "outcome": decision["outcome"],
            "event": event,
            "event_id": event_row["id"],
            "plan_id": plan["id"],
            "milestone": plan.get("milestone"),
            "new_milestone": milestone,
            "notes": decision["notes"],
            "recipients": self.recipients(room_id, plan["id"]),
        }

    def _status_for(self, milestone: str) -> dict[str, str]:
        """The envelope status that goes with a milestone."""
        if milestone == MILESTONE_APPROVED:
            return {"status": STATUS_COMPLETED, "completed_at": _iso(self._clock())}
        if milestone in (MILESTONE_REFUSED_BY_SIGNER, MILESTONE_REFUSED_BY_APPROVER):
            return {"status": STATUS_REJECTED}
        if milestone == MILESTONE_CANCELLED:
            return {"status": STATUS_CANCELLED}
        return {"status": STATUS_PENDING}

    # ------------------------------------------------------------------ #
    # Reading the log
    # ------------------------------------------------------------------ #

    def plans(self, room_id: str, milestone: str | None = None) -> list[dict[str, Any]]:
        """Every live plan in a room, newest first."""
        rows = self.store.list(COLLECTION_PLAN, room_id=room_id, limit=200)
        return [
            dict(row["data"], id=row["id"], room_id=row.get("room_id"))
            for row in rows
            if milestone is None or row["data"].get("milestone") == milestone
        ]

    def events(
        self, room_id: str, plan_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        """The event log, newest first."""
        where = {"plan_id": plan_id} if plan_id else {}
        rows = self.store.find(COLLECTION_EVENT, where, limit=limit)
        return [dict(row["data"], id=row["id"]) for row in rows]

    def notices(
        self, room_id: str, unread_only: bool = False, limit: int = 100
    ) -> list[dict[str, Any]]:
        """What an event told the owner, newest first."""
        rows = self.store.list(COLLECTION_NOTICE, room_id=room_id, limit=limit)
        result = [dict(row["data"], id=row["id"]) for row in rows]
        if unread_only:
            result = [row for row in result if not row.get("read")]
        return result

    def acknowledge(
        self,
        room_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Mark notices read, by id or all of them."""
        ids = payload.get("notice_ids")
        rows = (
            self.store.list(COLLECTION_NOTICE, room_id=room_id, limit=500)
            if not ids
            else [self.store.get(str(one)) for one in ids]
        )
        touched = 0
        for row in rows:
            if row is None or row.get("room_id") != room_id:
                continue
            if row["data"].get("read"):
                continue
            self.store.update(row["id"], {"read": True}, actor=actor, source=source)
            touched += 1
        return {"room_id": room_id, "acknowledged": touched}

    def summary(self, room_id: str) -> dict[str, Any]:
        """Counts for a room's header, and the invariants beside them.

        Counted over this room's own rows, so a header says what happened in that
        room. The invariants are here because they are the two things a reader
        looks for and does not find on a page: this product never signs for a
        recipient, and the signed document is not retrievable before every
        recipient has finished.
        """
        plans = self.plans(room_id)
        by_milestone: dict[str, int] = {}
        for plan in plans:
            key = str(plan.get("milestone") or "unknown")
            by_milestone[key] = by_milestone.get(key, 0) + 1
        events = self.events(room_id, limit=1000)
        by_event: dict[str, int] = {}
        for event in events:
            key = str(event.get("event"))
            by_event[key] = by_event.get(key, 0) + 1
        notices = self.notices(room_id, limit=500)
        return {
            "room_id": room_id,
            "plans": len(plans),
            "by_milestone": by_milestone,
            "approved": by_milestone.get(MILESTONE_APPROVED, 0),
            "awaiting_signature": by_milestone.get(MILESTONE_AWAITING_SIGNATURE, 0),
            "events": len(events),
            "by_event": by_event,
            "notices": len(notices),
            "unread_notices": sum(1 for notice in notices if not notice.get("read")),
            "templates": len(self.templates(room_id)),
            "invariants": {
                "never_sign_for_a_recipient": True,
                "signed_pdf_before_completion": False,
            },
        }

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _notify(
        self,
        room_id: str,
        plan_id: str,
        reason: str,
        detail: str,
        *,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        return self.store.create(
            COLLECTION_NOTICE,
            {
                "plan_id": plan_id,
                "reason": reason,
                "detail": detail,
                "read": False,
                "at": _iso(self._clock()),
            },
            room_id=room_id,
            actor=actor or "vendor",
            source=source,
        )

    def _template_view(self, row: Mapping[str, Any]) -> dict[str, Any]:
        return dict(row["data"], id=row["id"], room_id=row.get("room_id"))


def _header(headers: Mapping[str, str], name: str) -> str | None:
    """A header by name, however the caller cased it.

    HTTP header names are case-insensitive, and a vendor sending
    ``x-documenso-secret`` must authenticate exactly as one sending
    ``X-Documenso-Secret``.
    """
    wanted = name.lower()
    for key, value in headers.items():
        if str(key).lower() == wanted:
            return str(value)
    return None


__all__ = [
    "EVENT_DOCUMENT_COMPLETED",
    "MapEngine",
    "MILESTONE_AWAITING_SIGNATURE",
    "MILESTONE_DRAFT",
    "OUTCOME_APPLIED",
    "OUTCOME_DUPLICATE",
    "OUTCOME_NOTED",
    "RECIPIENT_OPENED",
    "TERMINAL_MILESTONES",
    "SignersBlocked",
]
