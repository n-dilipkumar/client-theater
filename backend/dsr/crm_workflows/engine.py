"""The one object this workflow is driven through.

:class:`WorkflowEngine` is the façade the HTTP layer calls. It holds nothing but a
:class:`~dsr.store.RecordStore` handle, which is why the feature module builds one
per request from ``StoreDep`` rather than hanging it on ``app.state``: an
``app.state`` entry would mean editing ``dsr/api.py``, and the whole point of the
feature host is that adding a workflow is adding a file.

Every method that writes takes a required ``source=``. That is not decoration. The
audit row is the product's guarantee, and an audit row that names a string rather
than a route cannot be traced back to the request that caused it - which is a
defect this codebase has already shipped once. A required keyword means the
omission is a ``TypeError`` at the call site rather than a silently untraceable row
in production.

Three collections, all schema-flexible
--------------------------------------

``crm_integration``, ``crm_workflow``, ``crm_workflow_enrollment``. None has a
migration, a typed column, or a required field beyond the researched contract,
because a team adding a field must not need to coordinate with anyone. Every
filter goes through ``find()`` and therefore through the dynamic index, so a new
field is queryable the moment it is written.

The fourth collection is **not** one of ours. Activity is the product's own
``activity`` stream - the same rows ``client_engagement`` and ``analytics`` read -
so recording a DSR event here makes this feature's page agree with every other
analytics page instead of keeping a private copy of the same fact. Writing a
shared *collection* is data, not code: no shared file changes, and a team reading
``/api/records/activity`` sees what this workflow saw.

The one-writer assumption
-------------------------

Two checks here are find-then-write: "is this integration already registered?" and
"is this contact already enrolled?". Both are sound for one reason -
:class:`~dsr.db.audited.AuditedDatabase` is documented as a single writer behind
one lock, so no other request can insert between the find and the write. Under a
second writer both would stop being true, and that is the assumption to revisit
first if the store ever gains one.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from dsr.crm_workflows import actions as actions_module, criteria as criteria_module
from dsr.crm_workflows.activity import normalise_activity, parse_timestamp
from dsr.crm_workflows.definition import (
    DRAFT,
    PUBLISHED,
    WITHDRAWN,
    apply_patch,
    lint_workflow,
    normalise_workflow,
)
from dsr.crm_workflows.errors import (
    AlreadyPublished,
    AlreadyWithdrawn,
    IntegrationDisabled,
    IntegrationInUse,
    MalformedActivity,
    PublishedWorkflowIsImmutable,
    UnknownIntegration,
    WorkflowError,
)
from dsr.crm_workflows.vocabulary import ACTIONABILITY_NOTE, FILTER_FAMILIES
from dsr.store import RecordStore

#: The collections this workflow owns.
INTEGRATIONS = "crm_integration"
WORKFLOWS = "crm_workflow"
ENROLLMENTS = "crm_workflow_enrollment"

#: Not ours: DSR activity is the product's own stream, read by
#: ``client_engagement`` and ``analytics`` as well. Recorded here rather than in a
#: private collection so this feature's page and every other analytics page agree.
ACTIVITY = "activity"

#: How many per-activity reasons an evaluation reports for a workflow that matched
#: nothing. A room can hold a year of activity, and a response that returned a
#: thousand rows of "this one did not match" would bury the one that explains it.
REASON_SAMPLE = 5


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class WorkflowEngine:
    """Register integrations, define workflows, publish them, and evaluate activity."""

    def __init__(self, store: RecordStore, *, now: Any = None) -> None:
        self.store = store
        # Injectable so a test can assert on ordering and timestamps without
        # freezing the whole process clock.
        self._now = now or _now

    # ----------------------------------------------------------------- #
    # Vocabulary
    # ----------------------------------------------------------------- #

    def vocabulary(self) -> dict[str, Any]:
        """Every published vocabulary, served as data.

        A client builds its filter editor, its action editor and its family picker
        from this rather than from a list compiled into the page, so the editor and
        the validator can never disagree about what is legal.
        """
        from dsr.crm_workflows import vocabulary as vocabulary_module

        return {**vocabulary_module.describe(), **criteria_module.describe_vocabulary()}

    def inferences(self) -> dict[str, Any]:
        """Where this workflow stops being sourced. See :mod:`dsr.crm_workflows.inferences`."""
        from dsr.crm_workflows import inferences as inferences_module

        return inferences_module.describe()

    # ----------------------------------------------------------------- #
    # Step 1: the integration and the workspace-to-deal link
    # ----------------------------------------------------------------- #

    def present(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """A record flattened to ``data`` plus the envelope.

        Every read goes through this, so there is exactly one shape for each kind of
        row in this package. Handing the raw envelope to the domain would put every
        field lookup one level too deep, which returns a silent ``None`` rather than
        an error.
        """
        data = dict(record.get("data") or {})
        return {
            "id": record.get("id"),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            "revision": record.get("revision"),
            "room_id": record.get("room_id"),
            **data,
        }

    def integrations(self) -> list[dict[str, Any]]:
        return [self.present(record) for record in self.store.list(INTEGRATIONS, limit=1000)]

    def integration(self, integration_id: str) -> dict[str, Any] | None:
        record = self.store.get(integration_id)
        if record is None or record.get("collection") != INTEGRATIONS:
            return None
        return self.present(record)

    def integration_by_name(self, name: str) -> dict[str, Any] | None:
        found = self.store.find(INTEGRATIONS, {"name": name}, limit=1)
        return self.present(found[0]) if found else None

    def register_integration(
        self, payload: Mapping[str, Any], *, actor: str | None, source: str
    ) -> dict[str, Any]:
        """Register the integration a workflow's trigger filters on.

        Step 1 of the researched flow. The record carries the two things step 1
        checks - ``enabled``, and the ``connections`` map from a room to the
        deal/account its workspace is linked to - because a second workflow naming
        the same integration must be able to rely on them.

        A name that is already registered is refused rather than merged, because a
        second record claiming the same name would give a published workflow two
        possible integrations to depend on and no way to tell which. Amend the one
        that is there.
        """
        if not isinstance(payload, Mapping):
            raise WorkflowError("an integration must be a JSON object")
        name = str(payload.get("name") or "").strip().lower()
        if not name:
            raise WorkflowError("an integration needs a name")

        clash = self.integration_by_name(name)
        if clash is not None:
            raise WorkflowError(
                f"integration {name!r} is already registered ({clash['id']}). Amend it "
                f"rather than registering a second one: a second record claiming the "
                f"name would leave a published workflow with two possible integrations "
                f"to depend on."
            )

        connections = _normalise_connections(payload.get("connections"))
        record = {
            "name": name,
            "label": str(payload.get("label") or name).strip(),
            "vendor": str(payload.get("vendor") or "hubspot").strip().lower(),
            "enabled": bool(payload.get("enabled", True)),
            "connections": connections,
            "workflow_ids": [],
        }
        created = self.store.create(INTEGRATIONS, record, actor=actor, source=source)
        return self.present(created)

    def amend_integration(
        self, integration_id: str, patch: Mapping[str, Any], *, actor: str | None, source: str
    ) -> dict[str, Any]:
        """Turn the integration on or off, or (un)link a room.

        Turning it **off** or unlinking the last connected room is refused while a
        published workflow depends on it, naming the workflows that would stop
        firing. Step 1 is a check a person performs before building a workflow, so
        silently disabling the integration underneath a live rule would make the
        check meaningless and the failure invisible.
        """
        current = self.integration(integration_id)
        if current is None:
            raise UnknownIntegration(f"integration {integration_id} is not registered")

        merged = dict(current)
        if "connections" in patch:
            merged["connections"] = _amend_connections(
                current.get("connections") or {}, patch.get("connections")
            )
        for key in ("label", "vendor"):
            if key in patch:
                merged[key] = str(patch.get(key) or "").strip()
        if "enabled" in patch:
            merged["enabled"] = bool(patch.get("enabled"))

        disabling = current.get("enabled") and not merged.get("enabled")
        dropping_connections = len(merged.get("connections") or {}) < len(
            current.get("connections") or {}
        )
        dependents = self._published_dependents(str(current.get("name")))
        if dependents and (disabling or dropping_connections):
            raise IntegrationInUse(
                f"{len(dependents)} published workflow(s) depend on integration "
                f"{current.get('name')!r}: {', '.join(dependents)}. Turning it off or "
                f"unlinking a connected room would silently stop them firing. Unpublish "
                f"them first."
            )

        updated = self.store.update(integration_id, merged, actor=actor, source=source)
        return self.present(updated)

    def _published_dependents(self, integration_name: str) -> list[str]:
        """Names of published workflows whose trigger names this integration."""
        found = self.store.find(
            WORKFLOWS, {"trigger.integration": integration_name, "status": PUBLISHED}, limit=1000
        )
        return [str((record.get("data") or {}).get("name") or record["id"]) for record in found]

    def _require_integration(self, name: str) -> dict[str, Any]:
        """The integration a trigger names, or refuse it.

        Step 1 is a prerequisite rather than a preference, so both failures here are
        409 rather than 400: the request is well formed and what it conflicts with
        is state that does not exist yet or is switched off.
        """
        found = self.integration_by_name(name)
        if found is None:
            raise UnknownIntegration(
                f"no integration is registered as {name!r}. Step 1 of the researched "
                f"flow is to verify the integration is on; register it first."
            )
        return found

    # ----------------------------------------------------------------- #
    # Steps 2 to 7: the definition
    # ----------------------------------------------------------------- #

    def workflows(
        self,
        *,
        status: str | None = None,
        integration: str | None = None,
        include_withdrawn: bool = False,
    ) -> list[dict[str, Any]]:
        """The workflow library, newest first.

        A withdrawn workflow is a soft-deleted row, so it is out of the default
        listing and reachable with ``include_withdrawn``: the enrollments taken under
        it still name it, and the audit trail must not point at nothing.
        """
        where: dict[str, Any] = {}
        if status:
            where["status"] = str(status).strip().lower()
        if integration:
            where["trigger.integration"] = str(integration).strip().lower()
        if where:
            records = self.store.find(
                WORKFLOWS, where, limit=1000, include_deleted=include_withdrawn
            )
        else:
            records = self.store.list(WORKFLOWS, limit=1000, include_deleted=include_withdrawn)
        if not include_withdrawn:
            records = [record for record in records if record.get("deleted_at") is None]
        return [self.decorate(self.present(record)) for record in records]

    def decorate(self, definition: Mapping[str, Any]) -> dict[str, Any]:
        """A definition with its lint findings and its enrollment count beside it.

        The findings are on every listing, not only on a detail route, because "a
        workflow nobody re-reads is where an unrefined filter goes to live".
        """
        integration_name = str((definition.get("trigger") or {}).get("integration") or "")
        warnings = lint_workflow(definition, self.integration_by_name(integration_name))
        return {
            **definition,
            "warnings": warnings,
            "flagged": sum(1 for warning in warnings if warning.get("severity") == "warning"),
        }

    def workflow(self, workflow_id: str) -> dict[str, Any] | None:
        record = self.store.get(workflow_id)
        if record is None or record.get("collection") != WORKFLOWS:
            return None
        return self.decorate(self.present(record))

    def _live_workflow(self, workflow_id: str) -> dict[str, Any]:
        """The **raw** record for a live workflow, or refuse it naming what is wrong.

        Returns the record, not the presented view, because every caller presents it
        itself: presenting twice is silently wrong rather than loudly wrong, since a
        presented dict has no ``data`` key and so flattens to nothing.

        ``store.get`` filters soft-deleted rows, so a withdrawn workflow reads as
        absent. It is not absent - its enrollments still name it - and "not found"
        would send a caller looking for a workflow that is sitting right there in
        the listing with ``include_withdrawn``. The two cases are told apart here so
        the message can say which one happened.
        """
        record = self.store.get(workflow_id)
        if record is not None and record.get("collection") == WORKFLOWS:
            return record
        if any(
            row.get("id") == workflow_id
            for row in self.store.list(WORKFLOWS, limit=1000, include_deleted=True)
        ):
            raise AlreadyWithdrawn(
                f"workflow {workflow_id} is withdrawn. A withdrawn workflow is not "
                f"amended, republished or un-published in place: the enrollments taken "
                f"under it still name it. Define a new one."
            )
        raise WorkflowError(f"workflow {workflow_id} not found")

    def create(
        self, payload: Mapping[str, Any], *, actor: str | None, source: str
    ) -> dict[str, Any]:
        """Define a workflow. It does not fire until it is published.

        Steps 2 to 7. The definition is validated in the order the flow describes
        it, and the integration is only *resolved* rather than required here - a
        draft may be written before step 1 is finished, and the lint says so.
        Publishing is where the integration becomes a hard prerequisite, which is
        what makes it visible at the moment it matters.
        """
        definition = normalise_workflow(payload)
        created = self.store.create(WORKFLOWS, definition, actor=actor, source=source)
        self._bind(created["id"], definition, actor=actor, source=source)
        return self.decorate(self.present(self.store.get(created["id"]) or created))

    def _bind(
        self, workflow_id: str, definition: Mapping[str, Any], *, actor: str | None, source: str
    ) -> None:
        """Record the workflow on the integration it depends on.

        So "which workflows would stop if I turn this off" is a stored fact rather
        than a scan over every workflow's nested trigger, and so the integration's
        page can list them.
        """
        integration_name = str((definition.get("trigger") or {}).get("integration") or "")
        integration = self.integration_by_name(integration_name)
        if integration is None:
            return
        bound = sorted({*(integration.get("workflow_ids") or []), workflow_id})
        self.store.update(integration["id"], {"workflow_ids": bound}, actor=actor, source=source)

    def amend(
        self, workflow_id: str, patch: Mapping[str, Any], *, actor: str | None, source: str
    ) -> dict[str, Any]:
        """Change a draft. A published workflow must be unpublished first.

        See the ``amend-published-workflow`` inference: a published definition is
        the thing already firing, and contacts may have been enrolled by the version
        being edited.
        """
        record = self._live_workflow(workflow_id)
        current = self.present(record)
        if current.get("status") == PUBLISHED:
            raise PublishedWorkflowIsImmutable(
                f"workflow {workflow_id} is published, so its definition is what is "
                f"already firing and may already have enrolled contacts. Unpublish it, "
                f"amend, then publish again."
            )
        merged = apply_patch(current, patch)
        updated = self.store.update(workflow_id, merged, actor=actor, source=source)
        return self.decorate(self.present(updated))

    def withdraw(self, workflow_id: str, *, actor: str | None, source: str) -> dict[str, Any]:
        """Withdraw a workflow, published or not.

        A soft delete, always. A published workflow's enrollments name it, and
        destroying the row would leave the audit trail and the enrollments pointing
        at something the API no longer serves - the exact defect the audit-source
        rule exists to prevent.
        """
        record = self.store.get(workflow_id)
        if record is None or record.get("collection") != WORKFLOWS:
            # Reuse the same discrimination as the other routes, so withdrawing
            # twice reports "already withdrawn" rather than "not found".
            self._live_workflow(workflow_id)
        definition = self.present(record)
        if definition.get("status") == WITHDRAWN:
            raise AlreadyWithdrawn(f"workflow {workflow_id} is already withdrawn")
        self.store.delete(workflow_id, actor=actor, source=source)
        return {
            "withdrawn": True,
            "id": workflow_id,
            "name": definition.get("name"),
            "status": WITHDRAWN,
            "was_published": definition.get("status") == PUBLISHED,
            "enrollments": int(definition.get("enrollments") or 0),
        }

    def publish(self, workflow_id: str, *, actor: str | None, source: str) -> dict[str, Any]:
        """Step 8: publish, so DSR activity drives the workflow with no further setup.

        The integration must be registered and on. This is the moment step 1's check
        becomes load-bearing, which is why it is enforced here rather than at
        creation: a draft may legitimately be written first.
        """
        record = self._live_workflow(workflow_id)
        current = self.present(record)
        if current.get("status") == PUBLISHED:
            raise AlreadyPublished(f"workflow {workflow_id} is already published")

        definition = normalise_workflow(
            {
                k: v
                for k, v in current.items()
                if k
                not in (
                    "id",
                    "created_at",
                    "updated_at",
                    "revision",
                    "status",
                    "published_at",
                    "enrollments",
                    "last_enrolled_at",
                    "warnings",
                    "flagged",
                )
            }
        )
        integration = self._require_integration(definition["trigger"]["integration"])
        if not integration.get("enabled"):
            raise IntegrationDisabled(
                f"integration {integration.get('name')!r} is registered but switched "
                f"off. Step 1 of the researched flow is to verify it is on, and "
                f"publishing now would create a rule that silently never fires."
            )

        published = {
            **definition,
            "status": PUBLISHED,
            "published_at": self._now(),
            "enrollments": int(current.get("enrollments") or 0),
            "last_enrolled_at": current.get("last_enrolled_at"),
        }
        updated = self.store.update(workflow_id, published, actor=actor, source=source)
        self._bind(workflow_id, published, actor=actor, source=source)
        return self.decorate(self.present(self.store.get(workflow_id) or updated))

    def unpublish(self, workflow_id: str, *, actor: str | None, source: str) -> dict[str, Any]:
        """Stop a published workflow firing, without withdrawing it.

        Unpublishing and withdrawing are different acts on purpose. Unpublishing is
        the off switch: the definition survives, its enrollments survive, and
        republishing resumes exactly where it left off. Withdrawing retires the
        definition.
        """
        record = self._live_workflow(workflow_id)
        current = self.present(record)
        if current.get("status") != PUBLISHED:
            raise WorkflowError(
                f"workflow {workflow_id} is {current.get('status')!r}, not published, so "
                f"there is nothing to unpublish."
            )
        patched = {
            "status": DRAFT,
            "published_at": None,
            "enrollments": int(current.get("enrollments") or 0),
            "last_enrolled_at": current.get("last_enrolled_at"),
        }
        updated = self.store.update(workflow_id, patched, actor=actor, source=source)
        return self.decorate(self.present(updated))

    # ----------------------------------------------------------------- #
    # The source side: DSR activity
    # ----------------------------------------------------------------- #

    def record_activity(
        self, room_id: str, payload: Mapping[str, Any], *, actor: str | None, source: str
    ) -> dict[str, Any]:
        """Record one DSR activity event against the product's own activity stream.

        The research's source side is ``workspace.*`` / ``workspace.plan.task.*``
        webhooks and ``GET /v1/workspace-plan-tasks``; this route is the webhook end
        of that. It writes to the shared ``activity`` collection so this feature and
        every other analytics page read the same rows.

        A repeated ``idempotency_key`` answers with the event that was kept and
        ``outcome: duplicate`` rather than storing a second one. A retried webhook
        would otherwise inflate an enrollment's match count, and the person reading
        the history would see activity that did not happen. The key is optional, so
        a caller that sends none is unaffected - see the ``activity-idempotency-key``
        inference for why it is here when this workflow's own sources do not say so.
        """
        event = normalise_activity(payload, room_id=room_id)

        key = event.get("idempotency_key")
        if isinstance(key, str) and key.strip():
            kept = self.store.find(ACTIVITY, {"idempotency_key": key.strip()}, limit=1)
            if kept:
                winner = kept[0]
                attempts = int((winner.get("data") or {}).get("duplicate_attempts") or 0) + 1
                self.store.update(
                    winner["id"], {"duplicate_attempts": attempts}, actor=actor, source=source
                )
                fresh = self.store.get(winner["id"]) or winner
                return {
                    "outcome": "duplicate",
                    "reason": "duplicate_idempotency_key",
                    "detail": (
                        "An activity event with this idempotency_key was already "
                        "recorded, so this one was not stored again. A retried webhook "
                        "must not inflate a match count."
                    ),
                    "duplicate_attempts": attempts,
                    "activity": self.present_activity(fresh),
                }

        stored = {key_: value for key_, value in payload.items() if key_ != "data"}
        stored.update(
            {
                "action_family": event["action_family"],
                "idempotency_key": key,
                "delivery": event["delivery"],
                "duplicate_attempts": 0,
            }
        )
        if event["action_family"] is None and payload.get("action_family") is None:
            stored.pop("action_family")
        record = self.store.create(ACTIVITY, stored, room_id=room_id, actor=actor, source=source)
        return {
            "outcome": "recorded",
            "reason": None,
            "duplicate_attempts": 0,
            "activity": self.present_activity(record),
        }

    def present_activity(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """An activity row in the shape the matcher evaluates, plus its id.

        Read back through the same normaliser that reads a posted event, so a row
        this feature wrote and a row another feature wrote are the same thing to
        every filter.

        A row this normaliser cannot read is **reported, not raised**. The
        ``activity`` collection is the product's own stream, shared with the
        analytics features, and a row one of them wrote may not carry a contact under
        any of the documented aliases. Raising here would make this feature fail on
        a database that has nothing wrong with it - the exact failure mode the
        feature host exists to prevent - and the refusal belongs on the *write* path
        anyway, where the caller is the one who can fix it. On the read path the row
        is returned with ``action_family: None`` and a warning, which is what makes
        it belong to no filter and match nothing.
        """
        data = dict(record.get("data") or {})
        data.setdefault("idempotency_key", None)
        data["duplicate_attempts"] = int(data.get("duplicate_attempts") or 0)
        try:
            event = normalise_activity(data, room_id=record.get("room_id"), record_id=record["id"])
        except MalformedActivity as exc:
            return {
                "id": record["id"],
                "room_id": record.get("room_id"),
                "contact": None,
                "account": str(data.get("account") or ""),
                "action": str(data.get("action") or data.get("event") or "unknown"),
                "action_family": None,
                "target": str(data.get("target") or ""),
                "link_url": None,
                "file_name": None,
                "activity_text": None,
                "task_name": None,
                "occurred_at": data.get("occurred_at") or data.get("at"),
                "idempotency_key": data.get("idempotency_key"),
                "delivery": str(data.get("delivery") or "webhook"),
                "duplicate_attempts": data["duplicate_attempts"],
                "warnings": [
                    {
                        "code": "unreadable_activity",
                        "severity": "warning",
                        "field": None,
                        "message": (
                            f"{exc} The row is kept and shown, but it belongs to no "
                            f"filter family, so no workflow's filter can match it."
                        ),
                    }
                ],
                "data": data,
            }
        return {**event, "duplicate_attempts": data["duplicate_attempts"]}

    def activity(
        self, *, room_id: str | None, contact: str | None = None, family: str | None = None
    ) -> list[dict[str, Any]]:
        """The room's DSR activity, oldest first, each row classified into its family.

        Oldest first, deliberately: an evaluation reads them in the order they
        happened, and a list that reordered them would make the match count and the
        first-match reasoning depend on the sort.

        Sorted by ``occurred_at``, NOT by reversing ``list()``. Those agree only
        when events happen to be recorded in chronological order, and they are
        recorded in whatever order they arrive - a backfill, a replay, a webhook
        that was slow. ``list()`` orders by ``updated_at``, which is when the row
        was written, not when the event happened; reversing it therefore yields
        write order and calls it event order. That is the same defect as
        ``deliveries()`` asserting "newest first" over a ``find()`` that takes no
        ordering argument, and it surfaced the same way: a test that passed
        locally and failed on CI, because the underlying order was a tie-break
        coin flip rather than a promise.

        ``id`` breaks ties so two events with the same ``occurred_at`` keep a
        stable relative order across runs.
        """
        records = (
            self.store.list(ACTIVITY, room_id=room_id, limit=1000)
            if room_id
            else self.store.list(ACTIVITY, limit=1000)
        )
        rows = [self.present_activity(record) for record in records]
        rows.sort(key=lambda row: (str(row.get("occurred_at") or ""), str(row.get("id") or "")))
        if contact:
            wanted = contact.strip().lower()
            rows = [row for row in rows if str(row.get("contact") or "").lower() == wanted]
        if family:
            wanted_family = family.strip().lower()
            rows = [row for row in rows if row.get("action_family") == wanted_family]
        return rows

    # ----------------------------------------------------------------- #
    # The automation: evaluate a contact's activity
    # ----------------------------------------------------------------- #

    def evaluate(
        self,
        room_id: str,
        contact: str,
        *,
        actor: str | None,
        source: str,
        workflow_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """The researched data flow, start to finish, for one contact in one room.

        "DSR activity ... -> the integration's filter evaluates criteria per contact
        -> workflow enrollment -> emails, slack notifications, field writes, stage
        changes". Three decisions, all reported:

        * **which workflows could not even be considered**, and why - not
          published, integration off, room not connected, or a workflow named
          explicitly that is not live;
        * **which of them the contact's activity did not satisfy**, with the
          per-criteria reasoning, so a workflow that enrolled nobody can say which
          filter fell short rather than returning an empty answer;
        * **which enrolled this contact**, and the resolved action plan for each.

        Nothing is raised for a workflow that did not fire. A buyer's activity not
        matching a filter is a fact about the buyer, and a caller that got a 400 for
        it would eventually retry forever.
        """
        if not isinstance(contact, str) or not contact.strip():
            raise WorkflowError("an evaluation needs a contact")
        contact_key = contact.strip()
        room = self.store.get(room_id)
        events = self.activity(room_id=room_id, contact=contact_key)

        candidates = self._live_workflows(workflow_ids)
        enrolled: list[dict[str, Any]] = []
        not_enrolled: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []

        for definition in candidates:
            name = str(definition.get("name") or definition.get("id"))
            integration_name = str((definition.get("trigger") or {}).get("integration") or "")

            if definition.get("status") != PUBLISHED:
                skipped.append(
                    {
                        "workflow_id": definition.get("id"),
                        "name": name,
                        "reason": "not_published",
                        "detail": (
                            f"the workflow is {definition.get('status')!r}. Only a "
                            f"published workflow is driven by DSR activity."
                        ),
                    }
                )
                continue

            integration = self.integration_by_name(integration_name)
            if integration is None or not integration.get("enabled"):
                skipped.append(
                    {
                        "workflow_id": definition.get("id"),
                        "name": name,
                        "reason": "integration_unavailable",
                        "detail": (
                            f"integration {integration_name!r} is "
                            + ("not registered" if integration is None else "switched off")
                            + ". Step 1 of the researched flow is to verify it is on."
                        ),
                    }
                )
                continue

            connection = (integration.get("connections") or {}).get(room_id)
            if not connection:
                skipped.append(
                    {
                        "workflow_id": definition.get("id"),
                        "name": name,
                        "reason": "room_not_connected",
                        "detail": (
                            f"the research pairs the integration being on with the "
                            f"workspace being connected to a deal/account, and this room "
                            f"has no connection recorded in integration "
                            f"{integration_name!r}. A contact with no deal has no stage "
                            f"to change and no pipeline to notify."
                        ),
                    }
                )
                continue

            considered = self._within_lookback(events, definition)
            criteria_list = (definition.get("trigger") or {}).get("criteria") or []
            verdict = criteria_module.evaluate_contact(criteria_list, considered)

            if not verdict["matched"]:
                not_enrolled.append(
                    {
                        "workflow_id": definition.get("id"),
                        "name": name,
                        "reason": "no_matching_activity",
                        "detail": verdict["detail"],
                        "events_considered": len(considered),
                        "criteria": verdict["criteria"],
                        "sample": _sample_misses(considered, criteria_list),
                    }
                )
                continue

            hits = [
                event
                for event in considered
                if str(event.get("id")) in set(verdict["hit_activity_ids"])
            ] or considered
            record = self._enroll(
                definition, room, room_id, contact_key, hits, actor=actor, source=source
            )
            enrolled.append(record)

        return {
            "room_id": room_id,
            "contact": contact_key,
            "room_name": (room or {}).get("data", {}).get("name") if room else None,
            "events_considered": len(events),
            "workflows_considered": len(candidates),
            "enrolled": len(enrolled),
            "not_enrolled": len(not_enrolled),
            "skipped": skipped,
            "enrollments": enrolled,
            "misses": not_enrolled,
            # The research is explicit: "Nothing happens on the seller's screen."
            "actionable": 0,
            "seller_visible": False,
            "actionability_note": ACTIONABILITY_NOTE,
        }

    def _live_workflows(self, workflow_ids: list[str] | None) -> list[dict[str, Any]]:
        """The workflows this evaluation considers, drafts included.

        Drafts are included on purpose: the response's ``skipped`` list then says
        "this one is a draft" rather than leaving a reader to wonder whether a
        workflow they just wrote is being considered at all.
        """
        if workflow_ids:
            found = []
            for workflow_id in workflow_ids:
                record = self.store.get(workflow_id)
                if record is not None and record.get("collection") == WORKFLOWS:
                    found.append(self.decorate(self.present(record)))
            return found
        return self.workflows()

    def _within_lookback(
        self, events: list[dict[str, Any]], definition: Mapping[str, Any]
    ) -> list[dict[str, Any]]:
        """Apply a definition's own ``lookback_days``, if it set one.

        Measured from **now**, not from the contact's most recent event. Both are
        defensible and the difference matters: measured from the latest event, a
        single old event always satisfies any window, so the setting would be a knob
        that appeared to do nothing exactly when someone was trying to use it to stop
        old activity enrolling a contact. Measured from now, ``lookback_days: 1``
        means the last day, which is what the words say.

        The default is no limit - see the ``lookback-defaults-to-unlimited``
        inference - so this returns the events untouched unless the definition asked
        for a window, which makes the choice visible on the definition itself.

        An event with no readable timestamp is kept rather than dropped: dropping it
        would quietly make an unparseable timestamp into a non-match, and the
        matcher's ``refinement_unverifiable`` reason is the honest place for that
        fact to surface.
        """
        days = definition.get("lookback_days")
        if days is None:
            return events
        try:
            span = timedelta(days=int(days))
        except (TypeError, ValueError):
            return events
        now = parse_timestamp(self._now()) or datetime.now(timezone.utc)
        cutoff = now - span
        return [
            event
            for event in events
            if (at := parse_timestamp(event.get("occurred_at"))) is None or at >= cutoff
        ]

    def _enroll(
        self,
        definition: Mapping[str, Any],
        room: Mapping[str, Any] | None,
        room_id: str,
        contact: str,
        hits: list[dict[str, Any]],
        *,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Enrol a contact, or count the further match on the enrollment already there.

        See the ``first-enrollment-wins`` inference. The existing row is *updated*
        rather than duplicated, and that update is audited against the route that
        served it, so a reader of the audit log can see that a contact was matched
        again without being enrolled twice.
        """
        workflow_id = str(definition.get("id"))
        existing = self.store.find(
            ENROLLMENTS,
            {"workflow_id": workflow_id, "contact": contact},
            limit=1,
        )

        room_data = dict((room or {}).get("data") or {})
        contact_row = {"contact": contact}
        lifecycle_stage = None
        if existing:
            lifecycle_stage = (existing[0].get("data") or {}).get("lifecycle_stage")

        plan = actions_module.resolve_actions(
            definition.get("actions") or [],
            contact=contact_row,
            room=room_data,
            lifecycle_stage=lifecycle_stage,
        )
        via = str(hits[-1].get("delivery") or definition.get("delivery") or "filter")

        if existing:
            kept = existing[0]
            current = dict(kept.get("data") or {})
            patch = {
                "match_count": int(current.get("match_count") or 1) + len(hits),
                "last_match_at": self._now(),
                "matched_activity_ids": _merge_ids(
                    current.get("matched_activity_ids"), [hit.get("id") for hit in hits]
                ),
                "action_plan": plan,
                "action_summary": actions_module.summarise(plan),
            }
            self.store.update(kept["id"], patch, actor=actor, source=source)
            return {
                "outcome": "already_enrolled",
                "reason": "contact_already_enrolled",
                "detail": (
                    "The workflow is contact based and enrolment is a function of the "
                    "(contact, workflow) pair, so this further matching activity is "
                    "counted on the existing enrollment rather than enrolling the contact "
                    "a second time."
                ),
                "enrollment": self.present_enrollment(self.store.get(kept["id"]) or kept),
            }

        str(hits[0].get("contact") or contact)
        data = {
            "workflow_id": workflow_id,
            "workflow_name": str(definition.get("name") or ""),
            "workflow_revision": definition.get("revision"),
            "contact": contact,
            "integration": str((definition.get("trigger") or {}).get("integration") or ""),
            "via": via,
            "delivery": definition.get("delivery"),
            "enrollment_type": definition.get("enrollment_type"),
            "match_count": len(hits),
            "matched_activity_ids": _merge_ids(None, [hit.get("id") for hit in hits]),
            "first_matched_at": self._now(),
            "last_match_at": self._now(),
            "lifecycle_stage": lifecycle_stage,
            "action_plan": plan,
            "action_summary": actions_module.summarise(plan),
            # Repeated on the row, not only in the response, so a caller who reads
            # the record later still sees it.
            "actionable": 0,
            "seller_visible": False,
            "actionability_note": ACTIONABILITY_NOTE,
        }
        record = self.store.create(ENROLLMENTS, data, room_id=room_id, actor=actor, source=source)
        self._count_enrollment(workflow_id, actor=actor, source=source)
        return {
            "outcome": "enrolled",
            "reason": None,
            "detail": (
                f"{len(hits)} event(s) satisfied the workflow's filter and the contact's "
                f"actions were resolved. Nothing happened on the seller's screen."
            ),
            "enrollment": self.present_enrollment(record),
        }

    def _count_enrollment(self, workflow_id: str, *, actor: str | None, source: str) -> None:
        """Move the workflow's own counters, audited against the serving route.

        A counter maintained by arithmetic on the read side would need a scan of
        every enrollment on every listing; a stored counter is a dotted JSON path a
        client can filter on, which is the schema-flexibility rule doing its job.
        """
        record = self.store.get(workflow_id)
        if record is None:
            return
        current = dict(record.get("data") or {})
        self.store.update(
            workflow_id,
            {
                "enrollments": int(current.get("enrollments") or 0) + 1,
                "last_enrolled_at": self._now(),
            },
            actor=actor,
            source=source,
        )

    # ----------------------------------------------------------------- #
    # Reading enrollments
    # ----------------------------------------------------------------- #

    def present_enrollment(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(record.get("data") or {})
        return {
            "id": record.get("id"),
            "room_id": record.get("room_id"),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            "revision": record.get("revision"),
            **data,
        }

    def enrollments(
        self,
        *,
        room_id: str | None,
        workflow_id: str | None = None,
        contact: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Enrollments, newest first.

        Each filter is a JSON path in the enrollment's own payload and is resolved
        by ``find()`` through the dynamic index - including ``workflow_id`` and
        ``contact``, which a team can filter on with no change to this method.
        """
        where: dict[str, Any] = {}
        if workflow_id:
            where["workflow_id"] = workflow_id
        if contact:
            where["contact"] = contact.strip()
        if where:
            found = self.store.find(ENROLLMENTS, where, limit=1000)
            records = [
                record for record in found if room_id is None or record.get("room_id") == room_id
            ]
        elif room_id:
            records = self.store.list(ENROLLMENTS, room_id=room_id, limit=1000)
        else:
            records = self.store.list(ENROLLMENTS, limit=1000)
        return [
            self.present_enrollment(record) for record in records[: max(1, min(int(limit), 1000))]
        ]

    def enrollment(self, room_id: str, enrollment_id: str) -> dict[str, Any] | None:
        record = self.store.get(enrollment_id)
        if record is None or record.get("collection") != ENROLLMENTS:
            return None
        if record.get("room_id") != room_id:
            return None
        row = self.present_enrollment(record)
        workflow = self.workflow(str(row.get("workflow_id") or ""))
        row["workflow"] = (
            {
                "id": workflow.get("id"),
                "name": workflow.get("name"),
                "status": workflow.get("status"),
                "criteria": (workflow.get("trigger") or {}).get("criteria"),
                "published_at": workflow.get("published_at"),
            }
            if workflow
            else None
        )
        row["workflow_missing"] = workflow is None
        return row

    # ----------------------------------------------------------------- #
    # The page header
    # ----------------------------------------------------------------- #

    def summary(self, *, room_id: str | None) -> dict[str, Any]:
        """Counts for the page header, and the actionability note beside them.

        Counted over this room's enrollments rather than the whole collection, so a
        room's header says what happened in that room.
        """
        workflows = self.workflows()
        by_status: dict[str, int] = {DRAFT: 0, PUBLISHED: 0}
        for definition in workflows:
            status = str(definition.get("status"))
            by_status[status] = by_status.get(status, 0) + 1

        rows = self.enrollments(room_id=room_id, limit=1000)
        plans = [row.get("action_plan") or [] for row in rows]
        events = self.activity(room_id=room_id)
        by_family = {name: 0 for name in FILTER_FAMILIES}
        unclassified = 0
        for event in events:
            family = event.get("action_family")
            if family in by_family:
                by_family[str(family)] += 1
            else:
                unclassified += 1

        room_record = self.store.get(room_id) if room_id else None
        room_name = (room_record or {}).get("data", {}).get("name") if room_record else None

        return {
            "room_id": room_id,
            "room_name": room_name,
            "workflows": len(workflows),
            "published": by_status.get(PUBLISHED, 0),
            "drafts": by_status.get(DRAFT, 0),
            "flagged": sum(1 for definition in workflows if definition.get("flagged")),
            "integrations": len(self.integrations()),
            "enrollments": len(rows),
            "contacts": len({str(row.get("contact")) for row in rows}),
            "actions_planned": sum(
                1 for plan in plans for action in plan if action.get("status") == "planned"
            ),
            "actions_refused": sum(
                1 for plan in plans for action in plan if action.get("status") == "refused"
            ),
            "actions_unresolved": sum(
                1 for plan in plans for action in plan if action.get("status") == "unresolved"
            ),
            "activity": len(events),
            "activity_by_family": [
                {"family": name, "count": count} for name, count in by_family.items()
            ],
            "activity_unclassified": unclassified,
            "re_matched": sum(max(0, int(row.get("match_count") or 1) - 1) for row in rows),
            "executed": 0,
            "actionable": 0,
            "seller_visible": False,
            "actionability_note": ACTIONABILITY_NOTE,
            "filter_families": list(FILTER_FAMILIES),
        }


def _amend_connections(current: Mapping[str, Any], patch: Any) -> dict[str, Any]:
    """Merge a connections patch, and unlink a room given ``null``.

    A patch entry of ``null`` removes the room's connection. Expressed this way
    rather than as a separate route, because the whole map is one JSON value in
    ``data`` and "remove this key" is what a merge patch means everywhere else in
    this product. Without it the map could only ever grow, and step 1's check -
    "the workspace is connected to a deal/account" - would have no way to become
    untrue.
    """
    merged = dict(current)
    for room_id, value in _normalise_connections(patch, keep_nulls=True).items():
        if value is None:
            merged.pop(room_id, None)
        else:
            merged[room_id] = value
    return merged


def _normalise_connections(payload: Any, *, keep_nulls: bool = False) -> dict[str, Any]:
    """The room-to-deal map step 1 checks, as ordinary JSON.

    A list of ``{"room_id", "deal_id"}`` rows is accepted and indexed, because that
    is the shape a client builds from a table of rooms. An object keyed by room id is
    accepted as-is. Neither is a typed column: the whole map is one JSON value in
    ``data``, so a team that wants ``opportunity_id`` or ``contact_ids`` on a
    connection adds it without a migration.
    """
    if payload is None:
        return {}
    if isinstance(payload, Mapping):
        result: dict[str, Any] = {}
        for room_id, value in payload.items():
            key = str(room_id)
            if value is None:
                if keep_nulls:
                    result[key] = None
                continue
            if isinstance(value, Mapping):
                result[key] = dict(value)
            else:
                result[key] = {"deal_id": str(value)}
        return result
    if isinstance(payload, (list, tuple)):
        result = {}
        for entry in payload:
            if not isinstance(entry, Mapping):
                continue
            room_id = entry.get("room_id") or entry.get("workspace_id")
            if not room_id:
                continue
            result[str(room_id)] = {
                key: value for key, value in entry.items() if key not in ("room_id", "workspace_id")
            }
        return result
    return {}


def _merge_ids(existing: Any, new: Any) -> list[str]:
    """The matched-activity ids, de-duplicated and order-preserving."""
    out: list[str] = []
    for value in list(existing or []) + list(new or []):
        text = str(value)
        if text and text not in out:
            out.append(text)
    return out


#: How informative each "did not match" reason is, most informative first. Shared
#: with the matcher so a response and a decision cannot rank a reason differently.
#:
#: A sample of five ``family_mismatch`` rows tells a person nothing about their
#: filter, because it only says "this event was a download and you are filtering
#: clicks" - which was never the interesting question. The reasons that reached a
#: refinement and were then declined are what a person is debugging, so they are
#: reported first, and the trivial ones fill the remaining slots.
_MISS_PRIORITY: tuple[str, ...] = criteria_module.MISS_PRIORITY


def _sample_misses(
    events: Sequence[Mapping[str, Any]], criteria_list: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """A few per-event reasons, the informative ones first.

    Evaluated per criteria, because the criteria is what a person is debugging: a
    sample drawn from a workflow's two filters should say something about each of
    them rather than five rows about whichever happened to be first.
    """
    collected: list[dict[str, Any]] = []
    for criteria in criteria_list:
        for event in events:
            verdict = criteria_module.matches(criteria, event)
            if verdict["matched"]:
                continue
            collected.append(
                {
                    "family": str(criteria.get("family") or ""),
                    "activity_id": event.get("id"),
                    "action": event.get("action"),
                    "event_family": event.get("action_family"),
                    "reason": verdict["reason"],
                    "detail": verdict["detail"],
                }
            )
    collected.sort(
        key=lambda row: (
            _MISS_PRIORITY.index(str(row["reason"]))
            if str(row["reason"]) in _MISS_PRIORITY
            else len(_MISS_PRIORITY)
        )
    )
    return collected[:REASON_SAMPLE]


__all__ = [
    "ACTIVITY",
    "ENROLLMENTS",
    "INTEGRATIONS",
    "REASON_SAMPLE",
    "WORKFLOWS",
    "WorkflowEngine",
]
