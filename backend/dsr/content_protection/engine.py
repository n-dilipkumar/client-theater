"""Every read and write WF-131 makes, in one place.

The store is the only thing this module touches. There is no SQLite handle and no
HTTP object here, so the whole matrix is exercised in tests without a server.

Three collections, and no more
------------------------------

``wf131_recipient_policy`` holds one row per (link, recipient): the switches
below, plus the allow-list. ``wf131_protection_action`` holds one row per logged
action. Nothing else in the product is written here, and the four protections
this workflow bundles keep their own rows untouched - the gate stays WF-069's
link, the acceptance stays WF-070's, the watermark config stays WF-071's and the
download rules stay WF-072's. This workflow *reads* those by their published
field and collection names; it never writes another workflow's collection.

Why the policy is its own row and not fields on the link
--------------------------------------------------------

The same reason WF-071 keeps its config in its own collection, and it is worth
repeating because it is the decision a reviewer should check: a link row is
re-normalised by the workflow that owns it, and a re-normaliser drops a key it
does not know. A per-recipient matrix written onto the link would be the first
thing to disappear the next time a seller edited the link, and it would disappear
silently. Its own row survives that edit, and ``find()`` resolves the link
reference because it is a payload key rather than an envelope one.

Revocation is a value, not a deletion
-------------------------------------

:meth:`ProtectionBook.revoke_policy` sets ``revoked`` on the row and keeps it.
The alternative - a soft delete - would make "revoked" indistinguishable from
"never existed", which is exactly the distinction WF-069's gate takes care to
preserve. The row is still readable, still appears on the board with its
``revoked_at``, and still answers the evaluation from the very next request.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any, Callable

from dsr.store import RecordStore
from dsr.view_only_access import vocabulary as download_vocab

from . import rules, vocabulary as vocab
from .errors import ContentProtectionError, PolicyNotFound, ProtectionDenied

#: ``(message) -> None`` is not needed here; the clock is the only seam.
Clock = Callable[[], datetime]


class ProtectionBook:
    """Every read and write this workflow makes, in one place."""

    def __init__(self, store: RecordStore, *, now: Clock | None = None) -> None:
        self.store = store
        self._now = now or rules.now_utc

    # -- helpers ------------------------------------------------------------ #

    def _stamp(self) -> str:
        return self._now().isoformat(timespec="milliseconds")

    @staticmethod
    def _data(record: Mapping[str, Any]) -> dict[str, Any]:
        return dict(record.get("data") or {})

    def _record_or_none(self, record_id: Any, collection: str) -> dict[str, Any] | None:
        """Fetch a record by id, including a soft-deleted one, or ``None``.

        Read through ``store.db`` rather than ``store.get`` for one reason WF-069
        and WF-070 both hit: ``RecordStore.get`` does not forward
        ``include_deleted``, so a revoked link came back as ``None`` and looked
        like an id that never existed. A revocation has to be distinguishable
        from a missing link, so the difference is needed here too. ``store.db`` is
        the same audited wrapper underneath and this is a read.
        """
        if not isinstance(record_id, str) or not record_id:
            return None
        try:
            record = self.store.db.get(record_id, include_deleted=True)
        except Exception:  # the store's own miss, or a malformed id
            return None
        if record is None or record.get("collection") != collection:
            return None
        return record

    def _link(self, link_id: Any, collection: str) -> dict[str, Any] | None:
        return self._record_or_none(link_id, collection)

    def _agreement(self, agreement_id: Any) -> dict[str, Any] | None:
        return self._record_or_none(agreement_id, vocab.AGREEMENT_COLLECTION)

    def _policy(self, policy_id: Any) -> dict[str, Any]:
        record = self._record_or_none(policy_id, vocab.POLICY_COLLECTION)
        if record is None:
            raise PolicyNotFound(str(policy_id))
        return record

    def _policy_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = self._data(record)
        return {
            "id": record["id"],
            "room_id": data.get(vocab.ROOM_REF) or record.get("room_id"),
            vocab.LINK_REF: data.get(vocab.LINK_REF),
            vocab.LINK_COLLECTION_FIELD: data.get(
                vocab.LINK_COLLECTION_FIELD, vocab.DEFAULT_LINK_COLLECTION
            ),
            vocab.RECIPIENT: data.get(vocab.RECIPIENT),
            "policy": {field: data.get(field) for field in vocab.POLICY_FIELDS},
            "revoked": bool(data.get("revoked")),
            "revoked_at": data.get("revoked_at"),
            "revoked_by": data.get("revoked_by"),
            "updated_at": record.get("updated_at"),
            "created_at": record.get("created_at"),
        }

    # -- the policy matrix -------------------------------------------------- #

    def create_policy(
        self,
        room_id: str,
        payload: Mapping[str, Any],
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Bind a per-recipient policy to a link.

        ``source`` is required and comes from the route. It is the field the build
        brief names as the recurring defect: a domain function that hardcodes a
        URL as the source of a write leaves the audit log naming a route the app
        stopped serving.
        """
        payload = dict(payload or {})
        link_id = str(payload.get(vocab.LINK_REF) or "").strip()
        if not link_id:
            raise ContentProtectionError(
                "a policy is bound to a link", {vocab.LINK_REF: "Choose a link."}
            )
        collection = str(
            payload.get(vocab.LINK_COLLECTION_FIELD) or vocab.DEFAULT_LINK_COLLECTION
        ).strip()
        recipient = rules.normalize_recipient(payload.get(vocab.RECIPIENT))
        settings = rules.normalize_policy(payload)

        link = self._link(link_id, collection)
        if link is None:
            # Fail closed: a policy for a link that is not there would look like a
            # control and gate nothing.
            raise PolicyNotFound(f"no link {link_id!r} in {collection}")

        link_data = self._data(link)
        if settings[vocab.REQUIRE_PASSCODE] and not (
            link_data.get("password_hash") or link_data.get("password_set")
        ):
            raise ContentProtectionError(
                "this link has no passcode to require",
                {
                    vocab.REQUIRE_PASSCODE: (
                        "Set the link's passcode in WF-069 first, or turn this off."
                    )
                },
            )
        if (
            settings[vocab.FIELD_ENABLE_AGREEMENT]
            and self._agreement(settings[vocab.FIELD_AGREEMENT_ID]) is None
        ):
            raise ContentProtectionError(
                "the agreement does not exist",
                {vocab.FIELD_AGREEMENT_ID: "Choose an agreement that exists."},
            )

        effective_room = room_id or link_data.get(vocab.ROOM_REF) or link.get("room_id")
        record = self.store.create(
            vocab.POLICY_COLLECTION,
            {
                **settings,
                vocab.LINK_REF: link_id,
                vocab.LINK_COLLECTION_FIELD: collection,
                vocab.RECIPIENT: recipient,
                vocab.ROOM_REF: effective_room,
                "revoked": False,
                "created_at": self._stamp(),
            },
            room_id=effective_room,
            actor=actor,
            source=source,
        )
        return self._policy_view(record)

    def list_policies(
        self,
        link_id: str | None = None,
        room_id: str | None = None,
        *,
        include_revoked: bool = True,
    ) -> list[dict[str, Any]]:
        """Every policy in scope, newest first.

        Revoked rows are returned by default: a revoke that leaves no visible
        trace is a revoke nobody can audit, which is the workflow's own thesis.
        """
        where: dict[str, Any] = {}
        if link_id:
            where[vocab.LINK_REF] = link_id
        if room_id:
            where[vocab.ROOM_REF] = room_id
        rows = self.store.find(vocab.POLICY_COLLECTION, where, limit=500)
        views = [self._policy_view(row) for row in rows]
        if not include_revoked:
            views = [view for view in views if not view["revoked"]]
        return views

    def read_policy(self, policy_id: str) -> dict[str, Any]:
        return self._policy_view(self._policy(policy_id))

    def update_policy(
        self,
        policy_id: str,
        changes: Mapping[str, Any],
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Edit a policy. Only the matrix fields may move."""
        record = self._policy(policy_id)
        data = self._data(record)
        settings = rules.normalize_policy(changes, base=data)

        if (
            settings[vocab.FIELD_ENABLE_AGREEMENT]
            and self._agreement(settings[vocab.FIELD_AGREEMENT_ID]) is None
        ):
            raise ContentProtectionError(
                "the agreement does not exist",
                {vocab.FIELD_AGREEMENT_ID: "Choose an agreement that exists."},
            )

        if settings[vocab.REQUIRE_PASSCODE]:
            link = self._link(
                data.get(vocab.LINK_REF),
                data.get(vocab.LINK_COLLECTION_FIELD, vocab.DEFAULT_LINK_COLLECTION),
            )
            link_data = self._data(link) if link else {}
            if not (link_data.get("password_hash") or link_data.get("password_set")):
                raise ContentProtectionError(
                    "this link has no passcode to require",
                    {
                        vocab.REQUIRE_PASSCODE: (
                            "Set the link's passcode in WF-069 first, or turn this off."
                        )
                    },
                )

        patch = {field: settings[field] for field in vocab.POLICY_FIELDS}
        # Revoking is not changed by an edit of the matrix. A policy that was
        # revoked comes back only through `restore_policy`, so a stray PATCH
        # cannot quietly re-open a link the seller cut off.
        patch["updated_at"] = self._stamp()
        updated = self.store.update(record["id"], patch, actor=actor, source=source)
        return self._policy_view(updated)

    def revoke_policy(
        self, policy_id: str, *, source: str, actor: str | None = None
    ) -> dict[str, Any]:
        """Revoke a recipient. "Works immediately" - the next request is refused.

        A value rather than a delete, so the row and its history survive and a
        seller can see when they cut the recipient off. The action is logged, so
        the revocation is itself an audit event: the spec's fifth verb, and the
        one the research says no open-source project implements end to end.
        """
        record = self._policy(policy_id)
        data = self._data(record)
        stamp = self._stamp()
        if data.get("revoked"):
            return self._policy_view(record)
        updated = self.store.update(
            record["id"],
            {"revoked": True, "revoked_at": stamp, "revoked_by": actor, "updated_at": stamp},
            actor=actor,
            source=source,
        )
        self.record_action(
            data.get(vocab.LINK_REF) or "",
            data.get(vocab.RECIPIENT),
            action=vocab.ACTION_REVOKE,
            allowed=True,
            policy_id=record["id"],
            room_id=data.get(vocab.ROOM_REF) or record.get("room_id"),
            detail="The recipient's access was revoked; the link is closed from this request.",
            source=source,
            actor=actor,
        )
        return self._policy_view(updated)

    def restore_policy(
        self, policy_id: str, *, source: str, actor: str | None = None
    ) -> dict[str, Any]:
        """Undo a revocation. The only write that clears ``revoked``."""
        record = self._policy(policy_id)
        updated = self.store.update(
            record["id"],
            {"revoked": False, "revoked_at": None, "revoked_by": None, "updated_at": self._stamp()},
            actor=actor,
            source=source,
        )
        return self._policy_view(updated)

    # -- the evaluation ----------------------------------------------------- #

    def evaluate(self, policy_id: str, context: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Answer the matrix without writing anything. A read, for a preview."""
        record = self._policy(policy_id)
        data = self._data(record)
        link = self._link(
            data.get(vocab.LINK_REF),
            data.get(vocab.LINK_COLLECTION_FIELD, vocab.DEFAULT_LINK_COLLECTION),
        )
        decision = rules.evaluate(
            self._data(link) if link else {},
            data,
            context,
            now=self._now(),
        )
        decision["policy_id"] = record["id"]
        return decision

    def admit(
        self,
        policy_id: str,
        context: Mapping[str, Any] | None = None,
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Evaluate, log the attempt, and refuse if the matrix denied it.

        The attempt is logged whether or not it succeeded, because WF-079's
        research is explicit that "both success and failure produce audit
        actions, so a rejected attempt is as visible as a successful one". Then
        the denial is raised, so the route answers 403 with the decision that
        named the protection.
        """
        decision = self.evaluate(policy_id, context)
        record = self._policy(policy_id)
        data = self._data(record)
        self.record_action(
            data.get(vocab.LINK_REF) or "",
            data.get(vocab.RECIPIENT),
            action=vocab.ACTION_VIEW,
            allowed=bool(decision["allowed"]),
            policy_id=policy_id,
            room_id=data.get(vocab.ROOM_REF) or record.get("room_id"),
            detail=decision["message"],
            denied_by=decision["denied_by"],
            source=source,
            actor=actor,
        )
        if not decision["allowed"]:
            raise ProtectionDenied(decision["reasons"][0], decision)
        return decision

    # -- the action log ----------------------------------------------------- #

    def record_action(
        self,
        link_id: str,
        recipient: Any,
        *,
        action: str,
        source: str,
        actor: str | None = None,
        allowed: bool = True,
        policy_id: str | None = None,
        room_id: str | None = None,
        detail: str | None = None,
        denied_by: Any = None,
    ) -> dict[str, Any]:
        """Write one action, carrying WF-079's integer code as ``event_code``.

        The code travels on the row rather than being looked up at export time,
        which is the extension point WF-079's research asks for: a compliance
        review queries by integer, and a workflow that logs its own events adds a
        row carrying the integer and needs no change to the export.
        """
        token, address = rules.validate_action(action, recipient)
        code = rules.action_code_checked(token)
        stamp = self._stamp()
        payload: dict[str, Any] = {
            vocab.LINK_REF: str(link_id),
            "policy_id": policy_id,
            vocab.RECIPIENT: address,
            "action": token,
            vocab.EVENT_CODE_FIELD: code,
            "allowed": bool(allowed),
            "occurred_at": stamp,
        }
        if detail:
            payload["detail"] = str(detail)
        if denied_by:
            payload["denied_by"] = list(denied_by)
        record = self.store.create(
            vocab.ACTION_COLLECTION,
            payload,
            room_id=room_id,
            actor=actor,
            source=source,
        )
        return {"id": record["id"], **payload}

    def list_actions(
        self,
        link_id: str | None = None,
        recipient: str | None = None,
        *,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        where: dict[str, Any] = {}
        if link_id:
            where[vocab.LINK_REF] = link_id
        if recipient:
            where[vocab.RECIPIENT] = rules.normalize_recipient(recipient)
        rows = self.store.find(vocab.ACTION_COLLECTION, where, limit=limit)
        return [{"id": row["id"], **self._data(row)} for row in rows]

    def bulk_alert(
        self,
        link_id: str | None = None,
        *,
        window_minutes: int | None = None,
        threshold: int | None = None,
    ) -> dict[str, Any]:
        """The bulk-download pattern for a scope. An alert, never a block."""
        actions = self.list_actions(link_id, limit=1000) if link_id else self._all_actions()
        return rules.bulk_download_alert(
            actions,
            now=self._now(),
            threshold=int(threshold or vocab.DEFAULT_BULK_ALERT_THRESHOLD),
            window=timedelta(
                minutes=int(window_minutes or vocab.DEFAULT_BULK_ALERT_WINDOW_MINUTES)
            ),
        )

    def _all_actions(self, limit: int = 1000) -> list[dict[str, Any]]:
        rows = self.store.list(vocab.ACTION_COLLECTION, limit=limit)
        return [{"id": row["id"], **self._data(row)} for row in rows]

    # -- composition and the board ------------------------------------------ #

    def compose_link(self, link_id: str, collection: str | None = None) -> dict[str, Any]:
        """Join the four protections and this workflow's matrix onto one link.

        Read-only, and best-effort: a protection that is not configured is
        reported as absent rather than treated as an error, because a link with a
        gate and no watermark is a legitimate link. Nothing here writes.
        """
        collection = collection or vocab.DEFAULT_LINK_COLLECTION
        link = self._link(link_id, collection)
        if link is None:
            raise PolicyNotFound(f"no link {link_id!r} in {collection}")
        link_data = self._data(link)

        agreement = None
        if link_data.get(vocab.FIELD_ENABLE_AGREEMENT):
            agreement = self._agreement(link_data.get(vocab.FIELD_AGREEMENT_ID))

        watermark = None
        for row in self.store.find("wf071_watermark", {"link_id": link_id}, limit=1):
            watermark = self._data(row)

        download_policy = None
        for row in self.store.find("wf072_link", {"link_ref": link_id}, limit=1):
            download_policy = self._data(row)

        policies = self.list_policies(link_id)
        actions = self.list_actions(link_id)
        return {
            vocab.LINK_REF: link_id,
            vocab.LINK_COLLECTION_FIELD: collection,
            "link": {
                "id": link["id"],
                "room_id": link_data.get(vocab.ROOM_REF) or link.get("room_id"),
                "title": link_data.get("title"),
                "email_protected": bool(link_data.get(vocab.FIELD_EMAIL_PROTECTED)),
                "email_authenticated": bool(link_data.get(vocab.FIELD_EMAIL_AUTHENTICATED)),
                "passcode_set": bool(
                    link_data.get("password_hash") or link_data.get("password_set")
                ),
                "expires_at": link_data.get(vocab.FIELD_EXPIRES_AT),
                "revoked": link.get("deleted_at") is not None,
                "closed": link.get("deleted_at") is not None,
                "agreement_id": link_data.get(vocab.FIELD_AGREEMENT_ID),
                "enable_agreement": bool(link_data.get(vocab.FIELD_ENABLE_AGREEMENT)),
            },
            "agreement": ({"id": agreement["id"], **self._data(agreement)} if agreement else None),
            "watermark": watermark,
            "download_policy": download_policy,
            "recipient_policies": policies,
            "recipients": len(policies),
            "revoked_recipients": sum(1 for policy in policies if policy["revoked"]),
            "actions": len(actions),
            **vocab.honesty(),
        }

    def summary(self, room_id: str | None = None) -> dict[str, Any]:
        """The board's headline numbers. Reads only."""
        where = {vocab.ROOM_REF: room_id} if room_id else {}
        policies = (
            self.list_policies(room_id=room_id)
            if room_id
            else [
                self._policy_view(row)
                for row in self.store.list(vocab.POLICY_COLLECTION, limit=500)
            ]
        )
        actions = (
            self.list_actions(limit=1000)
            if not room_id
            else [
                {"id": row["id"], **self._data(row)}
                for row in self.store.find(vocab.ACTION_COLLECTION, where, limit=1000)
            ]
        )
        by_action: dict[str, int] = {action: 0 for action in vocab.ACTIONS}
        denied = 0
        for entry in actions:
            name = str(entry.get("action") or "")
            if name in by_action:
                by_action[name] += 1
            if entry.get("allowed") is False:
                denied += 1
        return {
            "policies": len(policies),
            "revoked": sum(1 for policy in policies if policy["revoked"]),
            "recipients": len({policy[vocab.RECIPIENT] for policy in policies}),
            "allowlisted": sum(
                1 for policy in policies if policy["policy"][vocab.FIELD_IP_ALLOWLIST]
            ),
            "downloading": sum(
                1 for policy in policies if policy["policy"][vocab.FIELD_ALLOW_DOWNLOAD]
            ),
            "watermarked": sum(
                1 for policy in policies if policy["policy"][vocab.FIELD_ENABLE_WATERMARK]
            ),
            "actions": len(actions),
            "denied": denied,
            "by_action": by_action,
            "bulk_alert": self.bulk_alert() if not room_id else None,
            "generated_at": self._stamp(),
        }

    # -- the published contract --------------------------------------------- #

    def vocabulary(self) -> dict[str, Any]:
        return vocab.describe()

    def honesty(self) -> dict[str, Any]:
        return vocab.honesty()


__all__ = ["ProtectionBook"]


#: Referenced so a rename in WF-072's vocabulary fails loudly here too: the
#: download AND this workflow composes reads these three names.
_DOWNLOAD_FIELDS = (
    download_vocab.BULK_DOWNLOAD,
    download_vocab.ALLOW_DOWNLOAD,
    download_vocab.CAN_DOWNLOAD,
)
