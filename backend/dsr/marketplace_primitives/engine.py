"""The WF-115 service: the review state machine over the record store.

:class:`Marketplace` is the only object in this package that touches the store.
It reads and writes ordinary JSON records through :class:`dsr.store.RecordStore`,
which delegates every mutation to :class:`dsr.db.audited.AuditedDatabase`, so the
audit row is written in the same transaction as the change. This module imports
``dsr.store`` and nothing else from ``dsr``.

Where more than one record changes at once -- a submission updates the module,
snapshots a version, and records a review event -- the writes go through one
``AuditedDatabase.transaction`` block so the set is all-or-nothing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from dsr.store import RecordStore

from . import rules, vocabulary as vocab
from .errors import (
    DynamicFilterLimitExceeded,
    KeyImmutable,
    KeyInvalid,
    KeyTaken,
    ModuleInvalid,
    ModuleNotFound,
    ModuleNotPublished,
    TransitionRefused,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _check(kind: object, data: dict[str, Any]) -> None:
    """Raise the right refusal for a payload that is not acceptable."""
    if rules.dynamic_filter_count(data.get("filters")) > vocab.DYNAMIC_FILTER_LIMIT:
        raise DynamicFilterLimitExceeded(
            f"at most {vocab.DYNAMIC_FILTER_LIMIT} Dynamic filter is allowed per module"
        )
    problems = rules.module_errors(kind, data)
    if problems:
        raise ModuleInvalid(problems)


class Marketplace:
    """The authoring, review, and publication of marketplace modules."""

    def __init__(self, store: RecordStore) -> None:
        self.store = store

    # ------------------------------------------------------------------ #
    # Rows and views
    # ------------------------------------------------------------------ #

    def _record(self, module_id: str, *, include_deleted: bool = False) -> dict[str, Any] | None:
        record = self.store.get(module_id)
        if record is None and include_deleted:
            record = self.store.db.get(module_id, include_deleted=True)
        return record

    def _live(self, module_id: str) -> dict[str, Any]:
        record = self._record(module_id)
        if record is None or record.get("collection") != vocab.MODULES:
            raise ModuleNotFound(f"no marketplace module with id {module_id!r}")
        return record

    def view(self, record: dict[str, Any]) -> dict[str, Any]:
        """A module record plus every field derived from its state."""
        data = dict(record.get("data") or {})
        state = (
            vocab.STATE_DELETED
            if record.get("deleted_at")
            else data.get("review_state", vocab.STATE_DRAFT)
        )
        kind = data.get("kind")
        filters = data.get("filters")
        view = dict(data)
        view.update(
            {
                "id": record.get("id"),
                "collection": record.get("collection", vocab.MODULES),
                "room_id": record.get("room_id"),
                "revision": record.get("revision"),
                "created_at": record.get("created_at"),
                "updated_at": record.get("updated_at"),
                "deleted_at": record.get("deleted_at"),
                "review_state": state,
                "deleted": record.get("deleted_at") is not None,
                "targets": list(rules.targets_from(state)),
                "available_actions": {
                    "submit": state in vocab.SUBMITTABLE_STATES,
                    "review": state in (vocab.STATE_SUBMITTED, vocab.STATE_IN_REVIEW),
                    "publish": state == vocab.STATE_APPROVED,
                    "withdraw": state in vocab.WITHDRAWABLE_STATES,
                    "delete": state != vocab.STATE_DELETED,
                    "restore": state == vocab.STATE_DELETED,
                    "deliver": state == vocab.STATE_PUBLISHED,
                },
                "dynamic_filter_count": rules.dynamic_filter_count(filters),
                "issues": rules.module_errors(kind, data),
                "warnings": rules.module_warnings(kind, data),
                # Recorded configuration, never run here. See the decisions.
                "runs_custom_code": False,
            }
        )
        return view

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #

    def module(self, module_id: str, *, include_deleted: bool = False) -> dict[str, Any]:
        record = self._record(module_id, include_deleted=include_deleted)
        if record is None or record.get("collection") != vocab.MODULES:
            raise ModuleNotFound(f"no marketplace module with id {module_id!r}")
        return self.view(record)

    def modules(
        self,
        *,
        kind: str | None = None,
        review_state: str | None = None,
        q: str | None = None,
        include_deleted: bool = False,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        records = self.store.list(
            vocab.MODULES,
            limit=limit,
            include_deleted=include_deleted,
            order_by="updated_at",
            descending=True,
        )
        views = [self.view(record) for record in records]
        if kind:
            views = [view for view in views if view.get("kind") == kind]
        if review_state:
            states = {item.strip() for item in review_state.split(",") if item.strip()}
            views = [view for view in views if view.get("review_state") in states]
        if q:
            needle = q.lower()
            views = [
                view
                for view in views
                if needle in str(view.get("name", "")).lower()
                or needle in str(view.get("key", "")).lower()
            ]
        return views

    def by_key(self, key: str, *, include_deleted: bool = False) -> dict[str, Any] | None:
        records = self.store.find(
            vocab.MODULES, {"key": key}, limit=1, include_deleted=include_deleted
        )
        return self.view(records[0]) if records else None

    def versions(self, module_id: str) -> list[dict[str, Any]]:
        rows = self.store.find(vocab.VERSIONS, {"module_id": module_id}, limit=1000)
        return sorted(
            rows, key=lambda row: int((row.get("data") or {}).get("version") or 0), reverse=True
        )

    def reviews(self, module_id: str) -> list[dict[str, Any]]:
        return self.store.find(vocab.REVIEWS, {"module_id": module_id}, limit=1000)

    def deliveries(self, module_id: str) -> list[dict[str, Any]]:
        return self.store.find(vocab.DELIVERIES, {"module_id": module_id}, limit=1000)

    def detail(self, module_id: str, *, include_deleted: bool = False) -> dict[str, Any]:
        """One module with its version, review, and delivery history."""
        view = self.module(module_id, include_deleted=include_deleted)
        view["versions"] = self.versions(module_id)
        view["reviews"] = self.reviews(module_id)
        view["deliveries"] = self.deliveries(module_id)
        return view

    def summary(self) -> dict[str, Any]:
        views = self.modules(limit=1000)
        by_state = {state: 0 for state in vocab.REVIEW_STATES}
        by_kind = {kind: 0 for kind in vocab.KINDS}
        for view in views:
            by_state[view["review_state"]] = by_state.get(view["review_state"], 0) + 1
            by_kind[view.get("kind")] = by_kind.get(view.get("kind"), 0) + 1
        return {
            "total": len(views),
            "by_state": by_state,
            "by_kind": by_kind,
            "published": by_state.get(vocab.STATE_PUBLISHED, 0),
            "awaiting_review": by_state.get(vocab.STATE_SUBMITTED, 0)
            + by_state.get(vocab.STATE_IN_REVIEW, 0),
            "deleted": len(self.store.list(vocab.MODULES, limit=1000, include_deleted=True))
            - len(views),
        }

    # ------------------------------------------------------------------ #
    # Authoring
    # ------------------------------------------------------------------ #

    def create(
        self,
        *,
        kind: str,
        key: str,
        name: str,
        actor: str,
        source: str,
        fields: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Author a new module in the draft state."""
        key_issue = rules.key_problem(key)
        if key_issue:
            raise KeyInvalid(key_issue)
        if self.store.find(vocab.MODULES, {"key": key}, limit=1):
            raise KeyTaken(f"the key {key!r} is already in use by a live module")
        data: dict[str, Any] = {"kind": kind, "key": key, "name": name, "version": 0}
        data.update(fields or {})
        data["review_state"] = vocab.STATE_DRAFT
        _check(kind, data)
        record = self.store.create(vocab.MODULES, data, actor=actor, source=source)
        return self.view(record)

    def update(
        self,
        module_id: str,
        patch: dict[str, Any],
        *,
        actor: str,
        source: str,
    ) -> dict[str, Any]:
        """Merge an edit into a module, refusing an immutable-key change."""
        record = self._live(module_id)
        data = dict(record.get("data") or {})
        for field in vocab.IMMUTABLE_FIELDS:
            if field in patch and patch[field] != data.get(field):
                raise KeyImmutable(f"the {field} is immutable and cannot be changed")
        if "review_state" in patch and patch["review_state"] != data.get("review_state"):
            raise TransitionRefused(
                "review_state changes only through the review, publish, withdraw, "
                "delete and restore routes"
            )
        state = data.get("review_state", vocab.STATE_DRAFT)
        if state == vocab.STATE_PUBLISHED:
            raise TransitionRefused("a published module is immutable; withdraw it before editing")
        merged = {**data, **patch}
        _check(merged.get("kind"), merged)
        updated = self.store.update(module_id, merged, actor=actor, source=source)
        return self.view(updated)

    # ------------------------------------------------------------------ #
    # The review state machine
    # ------------------------------------------------------------------ #

    def _transition(
        self,
        module_id: str,
        target: str,
        *,
        actor: str,
        source: str,
        action: str,
        patch: dict[str, Any] | None = None,
        event_fields: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        record = self._live(module_id)
        data = dict(record.get("data") or {})
        state = data.get("review_state", vocab.STATE_DRAFT)
        if not rules.can_transition(state, target):
            raise TransitionRefused(
                f"a module in state {state!r} cannot move to {target!r}; "
                f"allowed: {', '.join(rules.targets_from(state)) or 'none'}"
            )
        module_patch = {"review_state": target, **(patch or {})}
        event = {
            "module_id": module_id,
            "key": data.get("key"),
            "kind": data.get("kind"),
            "version": data.get("version"),
            "action": action,
            "from_state": state,
            "to_state": target,
            "actor": actor,
            **(event_fields or {}),
        }
        with self.store.db.transaction(actor=actor, source=source) as tx:
            updated = tx.update(module_id, module_patch)
            tx.create(vocab.REVIEWS, event)
        return self.view(updated)

    def submit(self, module_id: str, *, changelog: str, actor: str, source: str) -> dict[str, Any]:
        """Submit a draft (or a returned one) for review, with a changelog."""
        if not isinstance(changelog, str) or not changelog.strip():
            raise ModuleInvalid(["a changelog is required to submit a module for review"])
        record = self._live(module_id)
        data = dict(record.get("data") or {})
        state = data.get("review_state", vocab.STATE_DRAFT)
        if not rules.can_transition(state, vocab.STATE_SUBMITTED):
            raise TransitionRefused(f"a module in state {state!r} cannot be submitted for review")
        _check(data.get("kind"), data)
        version = int(data.get("version") or 0) + 1
        moment = _now()
        with self.store.db.transaction(actor=actor, source=source) as tx:
            updated = tx.update(
                module_id,
                {
                    "review_state": vocab.STATE_SUBMITTED,
                    "version": version,
                    "changelog": changelog,
                    "submitted_at": moment,
                },
            )
            tx.create(
                vocab.VERSIONS,
                {
                    "module_id": module_id,
                    "key": data.get("key"),
                    "kind": data.get("kind"),
                    "version": version,
                    "changelog": changelog,
                    "submitted_at": moment,
                    "snapshot": data,
                },
            )
            tx.create(
                vocab.REVIEWS,
                {
                    "module_id": module_id,
                    "key": data.get("key"),
                    "kind": data.get("kind"),
                    "version": version,
                    "action": "submit",
                    "from_state": data.get("review_state", vocab.STATE_DRAFT),
                    "to_state": vocab.STATE_SUBMITTED,
                    "actor": actor,
                    "note": changelog,
                },
            )
        return self.view(updated)

    def review(
        self,
        module_id: str,
        *,
        decision: str,
        reviewer: str,
        note: str | None,
        actor: str,
        source: str,
    ) -> dict[str, Any]:
        """Take a review decision: start, approve, request changes, or reject."""
        if decision not in vocab.REVIEW_DECISIONS:
            raise ModuleInvalid(["decision must be one of " + ", ".join(vocab.REVIEW_DECISIONS)])
        if decision in vocab.DECISIONS_REQUIRING_NOTE and (
            not isinstance(note, str) or not note.strip()
        ):
            raise ModuleInvalid([f"a note is required for the {decision!r} decision"])
        return self._transition(
            module_id,
            vocab.REVIEW_DECISIONS[decision],
            actor=actor,
            source=source,
            action=decision,
            patch={"reviewer": reviewer} if decision != "start_review" else {},
            event_fields={"reviewer": reviewer, "note": note},
        )

    def publish(self, module_id: str, *, actor: str, source: str) -> dict[str, Any]:
        """Publish an approved module live to every sub-account."""
        return self._transition(
            module_id,
            vocab.STATE_PUBLISHED,
            actor=actor,
            source=source,
            action="publish",
            patch={"published_at": _now()},
        )

    def withdraw(self, module_id: str, *, actor: str, source: str) -> dict[str, Any]:
        """Withdraw a submission, or unpublish a published module."""
        return self._transition(
            module_id,
            vocab.STATE_WITHDRAWN,
            actor=actor,
            source=source,
            action="withdraw",
        )

    def delete(self, module_id: str, *, actor: str, source: str) -> dict[str, Any]:
        """Soft-delete a module. The row and its key survive."""
        self._live(module_id)
        self.store.delete(module_id, actor=actor, source=source)
        restored = self._record(module_id, include_deleted=True)
        return self.view(restored) if restored else {"id": module_id, "deleted": True}

    def restore(self, module_id: str, *, actor: str, source: str) -> dict[str, Any]:
        """Undo a soft delete."""
        record = self.store.restore(module_id, actor=actor, source=source)
        return self.view(record)

    # ------------------------------------------------------------------ #
    # Subscription deliveries
    # ------------------------------------------------------------------ #

    def record_delivery(
        self,
        module_id: str,
        *,
        event_type: str,
        target_url: str,
        filters: list[Any] | None,
        extras: dict[str, Any] | None,
        actor: str,
        source: str,
    ) -> dict[str, Any]:
        """Record and evaluate what the platform POSTs to the Subscription URL.

        The room does not own the Subscription URL and never calls it; it models
        the inbound direction so a builder can see how the platform's delivery
        resolves against the module's own filters and sample payload.
        """
        record = self._live(module_id)
        view = self.view(record)
        if view["review_state"] != vocab.STATE_PUBLISHED:
            raise ModuleNotPublished(
                f"module {module_id!r} is {view['review_state']!r}; only a published module receives deliveries"
            )
        problems = rules.delivery_errors(event_type, target_url, filters)
        if problems:
            raise ModuleInvalid(problems)
        data = record.get("data") or {}
        evaluation = rules.evaluate_filters(data.get("filters"), data.get("sample_payload"))
        created = self.store.create(
            vocab.DELIVERIES,
            {
                "module_id": module_id,
                "key": data.get("key"),
                "kind": data.get("kind"),
                "version": data.get("version"),
                "event_type": event_type,
                "target_url": target_url,
                "filters": filters or [],
                "extras": extras or {},
                "matched": evaluation["matched"],
                "evaluation": evaluation,
            },
            actor=actor,
            source=source,
        )
        return {"delivery": created, "evaluation": evaluation}


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

_TRIGGER_SAMPLE = {
    "contact_id": "con_northwind_1",
    "location_id": "loc_northwind",
    "event": "page_viewed",
    "page": {"url": "/pricing", "seconds": 92},
    "tags": ["pricing", "security"],
}

_ACTION_SAMPLE = {
    "contact_id": "con_northwind_1",
    "workflow_id": "wf_77",
    "message": "Hello Dana",
}


def _author(
    engine: Marketplace,
    *,
    kind: str,
    key: str,
    name: str,
    fields: dict[str, Any],
    actor: str = "seed",
) -> str:
    module = engine.create(kind=kind, key=key, name=name, actor=actor, source="seed", fields=fields)
    return module["id"]


def seed(db: Any, context: dict[str, Any]) -> str | None:
    """Seven modules, one in each review state worth showing.

    ``db`` is an AuditedDatabase and ``context`` carries ``room_ids``, ``now``,
    and a per-feature ``rng``. The states are reached through the real routes --
    a published module is genuinely submitted, approved, and published -- so the
    demo data is a record of the machine working, not a set of hand-written
    state fields. Every character in the returned string is cp1252-encodable.
    """
    store = RecordStore(db)
    engine = Marketplace(store)
    if engine.modules(limit=1):
        return None  # already seeded; do not double up on a re-run

    published_trigger = _author(
        engine,
        kind=vocab.KIND_TRIGGER,
        key="northwind_page_viewed",
        name="Northwind page viewed",
        fields={
            "short_description": "Fires when a buyer opens a tracked page.",
            "summary": "Sends a checkout signal when a contact views a page.",
            "sample_payload": _TRIGGER_SAMPLE,
            "filters": [
                {"field": "page.url", "title": "Page", "field_type": "String", "required": True},
                {
                    "field": "tags",
                    "title": "Tags",
                    "field_type": "Multi-Select",
                    "option_source": "constants",
                    "options": [
                        {"label": "Pricing", "value": "pricing"},
                        {"label": "Security", "value": "security"},
                    ],
                },
            ],
            "custom_variables": [{"name": "Page URL", "key_path": "page.url"}],
            "events": ["CREATED", "UPDATED", "DELETED"],
            "subscription_url": "https://apps.example.com/wf115/subscribe",
        },
    )
    engine.submit(published_trigger, changelog="First release.", actor="seed", source="seed")
    engine.review(
        published_trigger,
        decision="approve",
        reviewer="reviewer@example.com",
        note="Looks good.",
        actor="seed",
        source="seed",
    )
    engine.publish(published_trigger, actor="seed", source="seed")
    engine.record_delivery(
        published_trigger,
        event_type="UPDATED",
        target_url="https://services.leadconnectorhq.com/workflows-marketplace/triggers/execute/abc/def",
        filters=[{"field": "page.url", "required": True}],
        extras={"locationId": "loc_northwind", "workflowId": "wf_77"},
        actor="seed",
        source="seed",
    )

    published_action = _author(
        engine,
        kind=vocab.KIND_ACTION,
        key="northwind_send_hello",
        name="Northwind send hello",
        fields={
            "short_description": "Posts a greeting and returns bindable variables.",
            "summary": "Calls the app API and returns message text.",
            "sample_payload": _ACTION_SAMPLE,
            "execution": {"method": "url", "url": "https://apps.example.com/wf115/execute"},
            "custom_variables": [{"name": "Message", "key_path": "message"}],
        },
    )
    engine.submit(published_action, changelog="First release.", actor="seed", source="seed")
    engine.review(
        published_action,
        decision="approve",
        reviewer="reviewer@example.com",
        note="Approved.",
        actor="seed",
        source="seed",
    )
    engine.publish(published_action, actor="seed", source="seed")

    submitted = _author(
        engine,
        kind=vocab.KIND_TRIGGER,
        key="contoso_form_submitted",
        name="Contoso form submitted",
        fields={
            "short_description": "Fires when a form is submitted.",
            "sample_payload": {"contact_id": "con_1", "form_id": "form_9"},
            "filters": [
                {"field": "form_id", "title": "Form", "field_type": "String", "required": True}
            ],
            "subscription_url": "https://apps.example.com/wf115/forms",
        },
    )
    engine.submit(submitted, changelog="Awaiting review.", actor="seed", source="seed")

    in_review = _author(
        engine,
        kind=vocab.KIND_ACTION,
        key="fabrikam_create_task",
        name="Fabrikam create task",
        fields={
            "short_description": "Creates a follow-up task.",
            "sample_payload": {"contact_id": "con_2", "title": "Call back"},
            "execution": {"method": "custom_code", "code": "return {done: true}"},
        },
    )
    engine.submit(in_review, changelog="Adds a task.", actor="seed", source="seed")
    engine.review(
        in_review,
        decision="start_review",
        reviewer="reviewer@example.com",
        note=None,
        actor="seed",
        source="seed",
    )

    approved = _author(
        engine,
        kind=vocab.KIND_TRIGGER,
        key="adventure_quote_viewed",
        name="Adventure quote viewed",
        fields={
            "short_description": "Fires when a quote is opened.",
            "sample_payload": {"contact_id": "con_3", "quote_id": "q_5"},
            "subscription_url": "https://apps.example.com/wf115/quotes",
        },
    )
    engine.submit(approved, changelog="Quote trigger.", actor="seed", source="seed")
    engine.review(
        approved,
        decision="approve",
        reviewer="reviewer@example.com",
        note="Ready.",
        actor="seed",
        source="seed",
    )

    changes = _author(
        engine,
        kind=vocab.KIND_TRIGGER,
        key="contoso_deal_won",
        name="Contoso deal won",
        fields={
            "short_description": "Fires when an opportunity is won.",
            "sample_payload": {"contact_id": "con_4", "deal_id": "d_2"},
            "subscription_url": "https://apps.example.com/wf115/deals",
        },
    )
    engine.submit(changes, changelog="Deal trigger.", actor="seed", source="seed")
    engine.review(
        changes,
        decision="request_changes",
        reviewer="reviewer@example.com",
        note="Name the filter field.",
        actor="seed",
        source="seed",
    )

    draft = _author(
        engine,
        kind=vocab.KIND_ACTION,
        key="fabrikam_send_summary",
        name="Fabrikam send summary",
        fields={
            "short_description": "Sends a summary email.",
            "sample_payload": {"contact_id": "con_5"},
            "execution": {"method": "url", "url": "https://apps.example.com/wf115/summary"},
        },
    )

    deleted = _author(
        engine,
        kind=vocab.KIND_TRIGGER,
        key="retired_legacy_ping",
        name="Retired legacy ping",
        fields={
            "short_description": "A retired trigger, kept so its key resolves.",
            "sample_payload": {"contact_id": "con_6"},
            "subscription_url": "https://apps.example.com/wf115/legacy",
        },
    )
    engine.delete(deleted, actor="seed", source="seed")

    states = []
    for module_id in (
        published_trigger,
        published_action,
        submitted,
        in_review,
        approved,
        changes,
        draft,
        deleted,
    ):
        states.append(engine.module(module_id, include_deleted=True)["review_state"])
    return (
        f"{len(states)} modules across the review states: "
        + ", ".join(states)
        + " (one published trigger also has a recorded delivery)"
    )


__all__ = ["Marketplace", "seed"]
