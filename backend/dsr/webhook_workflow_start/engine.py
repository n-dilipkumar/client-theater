"""WF-114: the store-facing half - mint, map, deliver, start, record.

This is the only module in the package that reads or writes. It builds on
:mod:`dsr.webhook_workflow_start.rules`, which is pure, so the interesting
decisions are testable without a database.

The shape of it
---------------
A team creates a workflow, adds an **Inbound Webhook** trigger, and this package
mints a unique webhook URL for that trigger. The team copies the URL and the
one-time signing value into an external service, declares the mapping that binds
payload paths to workflow variables, saves the trigger, and publishes the
workflow. From then on the external service sends a signed POST, GET or PUT to the
URL; the payload is parsed, de-duplicated and bound, and a run starts with the
workflow's declared downstream actions as its plan.

What one delivery does, in order
--------------------------------
1. Resolve the URL's token to a trigger. A token this build did not issue is a 404.
2. Refuse a method the trigger does not serve.
3. Refuse a GET that carries a body, because HTTP defines no body for GET.
4. Parse the payload. Any JSON shape parses; a GET's payload is its query string.
5. Verify the signature. This build fails closed, so the check is not optional.
6. A **live** delivery needs a saved trigger and a published workflow. A **test**
   delivery does not, because step 4 of the flow sends it before step 6 saves.
7. Run the mapping. A required row that did not resolve is refused with 422. A
   payload that binds nothing is recorded as ``unmapped`` and starts nothing.
8. De-duplicate a live delivery on the payload's fingerprint. A repeat is recorded
   as a duplicate and starts nothing.
9. Start a run, or record the validation a test delivery asked for.

``source`` is a required keyword on every writing method, so the audit row names
the route that actually served the write. A URL string hardcoded inside a domain
method is a defect: a feature's audit log has named a path the app stopped
serving in this codebase before.

A refusal that cannot name a room is not recorded
-------------------------------------------------
An unknown token names no trigger, no workflow and no room, so there is no row for
it to be attached to and it is answered with 404. Every refusal that can be
attributed - a bad method, a missing signature, an unsigned delivery, an unsaved
trigger, an unresolved required mapping - leaves a recorded ``rejected`` row
naming the reason. That is the set the issue means by *"Record the trigger
decisions the issue lists, including refusals."*
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from dsr.store import RecordStore
from dsr.webhook_workflow_start import inferences as infer, rules, signing, vocabulary as vocab
from dsr.webhook_workflow_start.errors import (
    BodyNotAllowedOnGet,
    InvalidTrigger,
    InvalidVariable,
    InvalidWorkflow,
    MalformedPayload,
    MethodNotAllowed,
    RequiredMappingUnresolved,
    SignatureMismatch,
    SignatureMissing,
    TriggerNotSaved,
    UnknownEndpoint,
    UnknownInvocation,
    UnknownRun,
    UnknownTrigger,
    UnknownVariable,
    UnknownWorkflow,
    WebhookWorkflowError,
    WorkflowNotPublished,
)

__all__ = ["WebhookWorkflowStart"]

_PAGE = 500


def _now() -> datetime:
    return datetime.now(timezone.utc)


class WebhookWorkflowStart:
    """The reads and writes, over an audited store.

    Built per request by the HTTP layer rather than hung off ``app.state``, so a
    test can substitute a clock without editing a shared file. ``now`` is a
    callable, not a value, because a module-level constant is captured on first
    import and a test that sets the clock afterwards would keep the old one.
    """

    def __init__(self, store: RecordStore, *, now: Callable[[], datetime] | None = None) -> None:
        self.store = store
        self.now = now or _now

    # -- clock --------------------------------------------------------------- #

    def clock(self) -> datetime:
        value = self.now()
        return value if getattr(value, "tzinfo", None) else value.replace(tzinfo=timezone.utc)

    def _stamp(self) -> str:
        return self.clock().isoformat(timespec="milliseconds")

    # -- workflows ----------------------------------------------------------- #

    def create_workflow(
        self,
        room_ref: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """Register a workflow in a room. It lands a draft.

        The researched flow reaches *"add downstream actions and publish"* only
        after *"Click Save Trigger"*, so a workflow is a draft until publishing is
        a deliberate second act.
        """
        data = dict(payload or {})
        name = str(data.get("name") or "").strip()
        if not name:
            raise InvalidWorkflow("a workflow needs a name.", name="a name is required.")

        actions = data.get("actions") or []
        if not isinstance(actions, (list, tuple)):
            raise InvalidWorkflow(
                "downstream actions must be a list of labels.",
                actions="must be a list",
            )

        created = self.store.create(
            vocab.COLLECTION_WORKFLOW,
            {
                "name": name,
                "description": str(data.get("description") or "").strip(),
                "status": vocab.STATUS_DRAFT,
                "actions": [str(action) for action in actions],
                "action_count": len(actions),
            },
            room_id=str(room_ref),
            actor=actor,
            source=source,
        )
        return self._workflow_summary(created)

    def workflows(self, room_ref: str) -> list[dict[str, Any]]:
        rows = self.store.list(vocab.COLLECTION_WORKFLOW, room_id=str(room_ref), limit=_PAGE)
        return [self._workflow_summary(row) for row in rows]

    def workflow(self, workflow_id: str) -> dict[str, Any]:
        row = self.store.get(workflow_id)
        if row is None or row["collection"] != vocab.COLLECTION_WORKFLOW:
            raise UnknownWorkflow(f"no workflow with id {workflow_id}.")
        return self._workflow_summary(row)

    def patch_workflow(
        self,
        workflow_id: str,
        patch: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """Change a workflow's name, description, actions or status."""
        current = self.workflow(workflow_id)
        updates: dict[str, Any] = {}

        if patch.get("name") is not None:
            name = str(patch["name"]).strip()
            if not name:
                raise InvalidWorkflow("a workflow needs a name.", name="a name is required.")
            updates["name"] = name

        if patch.get("description") is not None:
            updates["description"] = str(patch["description"]).strip()

        if "actions" in patch and patch["actions"] is not None:
            actions = patch["actions"]
            if not isinstance(actions, (list, tuple)):
                raise InvalidWorkflow(
                    "downstream actions must be a list of labels.",
                    actions="must be a list",
                )
            updates["actions"] = [str(action) for action in actions]
            updates["action_count"] = len(actions)

        if patch.get("status") is not None:
            status = str(patch["status"]).strip().lower()
            if status not in vocab.STATUSES:
                raise InvalidWorkflow(
                    f"status {status!r} is not one of {list(vocab.STATUSES)}.",
                    status=f"must be one of {list(vocab.STATUSES)}",
                )
            updates["status"] = status

        if updates:
            self.store.update(workflow_id, updates, actor=actor, source=source)
        summary = self.workflow(workflow_id)
        summary["previous_status"] = current["status"]
        return summary

    def publish_workflow(
        self, workflow_id: str, *, actor: str | None = None, source: str | None = None
    ) -> dict[str, Any]:
        """The last clause of step 6: *"add downstream actions and publish"*."""
        summary = self.patch_workflow(
            workflow_id, {"status": vocab.STATUS_PUBLISHED}, actor=actor, source=source
        )
        published_at = self._stamp()
        self.store.update(workflow_id, {"published_at": published_at}, actor=actor, source=source)
        summary["published_at"] = published_at
        return summary

    # -- the workflow variable store ----------------------------------------- #

    def declare_variable(
        self,
        workflow_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """Declare one variable a mapping may bind. Step 5 names these."""
        workflow = self.workflow(workflow_id)
        data = dict(payload or {})
        name = str(data.get("name") or "").strip()
        if not name:
            raise InvalidVariable("a variable needs a name.", name="a name is required.")
        variable_type = str(data.get("type") or vocab.VARIABLE_TEXT).strip().lower()
        if not vocab.is_known_variable_type(variable_type):
            raise InvalidVariable(
                f"variable type {variable_type!r} is not one of {list(vocab.VARIABLE_TYPE_IDS)}.",
                type=f"must be one of {list(vocab.VARIABLE_TYPE_IDS)}",
            )
        row = self.store.create(
            vocab.COLLECTION_VARIABLE,
            {
                "workflow_ref": workflow["id"],
                "name": name,
                "type": variable_type,
                "description": str(data.get("description") or "").strip(),
                "sample": data.get("sample"),
            },
            room_id=str(workflow.get("room_ref") or ""),
            actor=actor,
            source=source,
        )
        return self._variable_summary(row)

    def variables(self, workflow_id: str) -> list[dict[str, Any]]:
        self.workflow(workflow_id)
        rows = self.store.find(
            vocab.COLLECTION_VARIABLE, {"workflow_ref": workflow_id}, limit=_PAGE
        )
        return [self._variable_summary(row) for row in rows]

    def variable(self, variable_id: str) -> dict[str, Any]:
        row = self.store.get(variable_id)
        if row is None or row["collection"] != vocab.COLLECTION_VARIABLE:
            raise UnknownVariable(f"no variable with id {variable_id}.")
        return self._variable_summary(row)

    # -- triggers and their generated URLs ----------------------------------- #

    def create_trigger(
        self,
        workflow_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """Steps 1 and 2: select the trigger, then copy its generated URL.

        The signing value is served exactly once, in this response, because the
        fail-closed rule means the external service has to be told it. Every read
        afterwards carries ``has_signing_value`` instead.
        """
        workflow = self.workflow(workflow_id)
        data = dict(payload or {})
        name = str(data.get("name") or "").strip() or "Inbound Webhook"

        methods = self._normalise_methods(data.get("methods"))
        mappings = rules.normalise_mappings(data.get("mappings"))

        signing_value = str(data.get("signing_value") or "").strip() or signing.mint_signing_value()
        token = secrets.token_urlsafe(24)

        created = self.store.create(
            vocab.COLLECTION_TRIGGER,
            {
                "workflow_ref": workflow["id"],
                "name": name,
                "kind": vocab.TRIGGER_KIND,
                "label": vocab.TRIGGER_LABEL,
                "token": token,
                "signing_value": signing_value,
                "has_signing_value": True,
                "saved": False,
                "methods": methods,
                "mappings": mappings,
                "mapping_count": len(mappings),
                "premium_trigger": vocab.PREMIUM_TRIGGER,
            },
            room_id=str(workflow.get("room_ref") or ""),
            actor=actor,
            source=source,
        )
        summary = self._trigger_summary(created)
        summary["signing_value"] = signing_value
        summary["signing_value_shown_once"] = True
        summary["signature_header"] = vocab.SIGNATURE_HEADER
        summary["timestamp_header"] = vocab.TIMESTAMP_HEADER
        return summary

    def _normalise_methods(self, methods: Any) -> list[str]:
        rows = methods if isinstance(methods, (list, tuple, set)) else list(vocab.METHODS)
        out: list[str] = []
        for method in rows:
            name = str(method or "").strip().upper()
            if not vocab.is_served_method(name):
                raise InvalidTrigger(
                    f"method {name!r} is not one of {list(vocab.METHODS)}.",
                    method=f"must be one of {list(vocab.METHODS)}",
                )
            if name not in out:
                out.append(name)
        if not out:
            raise InvalidTrigger(
                "a trigger must serve at least one method.",
                methods=f"must name one of {list(vocab.METHODS)}",
            )
        return out

    def triggers(self, room_ref: str) -> list[dict[str, Any]]:
        rows = self.store.list(vocab.COLLECTION_TRIGGER, room_id=str(room_ref), limit=_PAGE)
        return [self._trigger_summary(row) for row in rows]

    def trigger(self, trigger_id: str) -> dict[str, Any]:
        return self._trigger_summary(self._require(trigger_id, vocab.COLLECTION_TRIGGER))

    def patch_trigger(
        self,
        trigger_id: str,
        patch: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """Step 5: bind payload paths to workflow variables."""
        current = self.trigger(trigger_id)
        updates: dict[str, Any] = {}

        if patch.get("name") is not None:
            name = str(patch["name"]).strip()
            if not name:
                raise InvalidTrigger("a trigger needs a name.", name="a name is required.")
            updates["name"] = name

        if patch.get("methods") is not None:
            updates["methods"] = self._normalise_methods(patch["methods"])

        if "mappings" in patch and patch["mappings"] is not None:
            mappings = rules.normalise_mappings(patch["mappings"])
            updates["mappings"] = mappings
            updates["mapping_count"] = len(mappings)

        if patch.get("signing_value") is not None:
            value = str(patch["signing_value"]).strip()
            updates["signing_value"] = value or signing.mint_signing_value()
            updates["has_signing_value"] = True

        if updates:
            self.store.update(trigger_id, updates, actor=actor, source=source)
        summary = self.trigger(trigger_id)
        summary["previous_saved"] = current["saved"]
        return summary

    def save_trigger(
        self, trigger_id: str, *, actor: str | None = None, source: str | None = None
    ) -> dict[str, Any]:
        """Step 6: *"Click Save Trigger"*. The URL works from here on."""
        current = self.trigger(trigger_id)
        if not current["saved"]:
            self.store.update(
                trigger_id, {"saved": True, "saved_at": self._stamp()}, actor=actor, source=source
            )
        summary = self.trigger(trigger_id)
        summary["previous_saved"] = current["saved"]
        return summary

    # -- the inbound entry point --------------------------------------------- #

    def deliver(
        self,
        token: str,
        *,
        method: str,
        payload: Any = None,
        raw_body: Any = None,
        body_present: bool = False,
        query: Mapping[str, Any] | None = None,
        signature: str | None = None,
        timestamp: str | None = None,
        mode: str | None = None,
        actor: str = "provider",
        source: str | None = None,
    ) -> dict[str, Any]:
        """One inbound delivery: resolve, check, parse, bind, dedupe, start.

        Returns a report rather than raising for a duplicate or for a payload that
        bound nothing, because both are outcomes of a successful call. It raises
        only for a delivery this build will not act on at all, and each of those
        that can be attributed leaves a recorded row first.
        """
        entry = self._trigger_by_token(token)
        room_ref = str(entry.get("room_id") or "")
        workflow = self.store.get(self._data(entry).get("workflow_ref") or "")
        if workflow is None or workflow["collection"] != vocab.COLLECTION_WORKFLOW:
            raise UnknownWorkflow(
                "the workflow behind this webhook URL no longer exists, so the URL is dead."
            )
        data = self._data(entry)
        delivery_mode = self._mode(mode, query)

        served = [str(name).upper() for name in (data.get("methods") or vocab.METHODS)]
        if str(method).upper() not in served:
            self._record(
                room_ref,
                entry,
                workflow,
                state=vocab.STATE_REJECTED,
                reason="method_not_served",
                mode=delivery_mode,
                detail={"method": str(method).upper(), "served": served},
                actor=actor,
                source=source,
            )
            raise MethodNotAllowed(
                f"this trigger serves {served}, not {str(method).upper()}.",
                method=f"must be one of {served}",
            )

        source_row = vocab.method_payload_source(method) or {}
        payload_source = str(source_row.get("source") or vocab.METHOD_PAYLOAD_SOURCE_BODY)
        if source_row.get("source") == vocab.METHOD_PAYLOAD_SOURCE_QUERY and body_present:
            self._record(
                room_ref,
                entry,
                workflow,
                state=vocab.STATE_REJECTED,
                reason=vocab.REASON_BODY_NOT_ALLOWED_ON_GET,
                mode=delivery_mode,
                actor=actor,
                source=source,
            )
            raise BodyNotAllowedOnGet(
                "a GET carries its payload in the query string. Move the values there, or "
                "send the delivery as a POST.",
                method="GET",
            )

        if source_row.get("source") == vocab.METHOD_PAYLOAD_SOURCE_QUERY:
            payload_final: Any = dict(query or {})
        else:
            payload_final = payload

        raw_text = self._raw_text(raw_body, payload_final)

        # Fail closed: every delivery is signed, so this check is not conditional.
        signing_value = str(data.get("signing_value") or "")
        ok, reason = signing.verify(
            signing_value,
            raw_text,
            signature=signature,
            timestamp=timestamp,
            now=self.clock(),
        )
        if not ok:
            self._record(
                room_ref,
                entry,
                workflow,
                state=vocab.STATE_REJECTED,
                reason=reason,
                mode=delivery_mode,
                detail={"signature_header": vocab.SIGNATURE_HEADER},
                actor=actor,
                source=source,
            )
            if reason in ("signature_missing", "timestamp_missing"):
                raise SignatureMissing(
                    "every generated URL is signed. Send the "
                    f"{vocab.SIGNATURE_HEADER} and {vocab.TIMESTAMP_HEADER} headers.",
                    reason=reason,
                )
            raise SignatureMismatch(
                "the signature did not verify against the trigger's signing value, or the "
                "timestamp is outside the window.",
                reason=reason,
                window_seconds=vocab.MAX_AGE_SECONDS,
            )

        if delivery_mode == vocab.MODE_LIVE:
            if not data.get("saved"):
                self._record(
                    room_ref,
                    entry,
                    workflow,
                    state=vocab.STATE_REJECTED,
                    reason=vocab.REASON_TRIGGER_NOT_SAVED,
                    mode=delivery_mode,
                    actor=actor,
                    source=source,
                )
                raise TriggerNotSaved(
                    "this trigger is not saved. Open the workflow, choose Save Trigger, and "
                    "send the delivery again. A test request is accepted before that.",
                    trigger_ref=entry["id"],
                )
            if self._data(workflow).get("status") != vocab.STATUS_PUBLISHED:
                self._record(
                    room_ref,
                    entry,
                    workflow,
                    state=vocab.STATE_REJECTED,
                    reason=vocab.REASON_WORKFLOW_NOT_PUBLISHED,
                    mode=delivery_mode,
                    actor=actor,
                    source=source,
                )
                raise WorkflowNotPublished(
                    "this workflow is still a draft. Publish it, then send the delivery again.",
                    workflow_ref=workflow["id"],
                    status=self._data(workflow).get("status"),
                )

        plan = rules.plan_bindings(data.get("mappings") or [], payload_final)

        if plan["required_unresolved"]:
            self._record(
                room_ref,
                entry,
                workflow,
                state=vocab.STATE_REJECTED,
                reason=vocab.REASON_REQUIRED_MAPPING_UNRESOLVED,
                mode=delivery_mode,
                bindings=plan["bindings"],
                declined=plan["declined"],
                payload=payload_final,
                detail={"unresolved": plan["required_unresolved"]},
                actor=actor,
                source=source,
            )
            names = ", ".join(row["variable"] for row in plan["required_unresolved"])
            raise RequiredMappingUnresolved(
                f"the mapping requires {names}, and the payload did not carry "
                f"{'it' if len(plan['required_unresolved']) == 1 else 'them'}.",
                unresolved=plan["required_unresolved"],
            )

        if plan["planned"] and plan["matched"] == 0:
            row = self._record(
                room_ref,
                entry,
                workflow,
                state=vocab.STATE_UNMAPPED,
                reason=vocab.REASON_NO_MAPPING_MATCHED,
                mode=delivery_mode,
                bindings=plan["bindings"],
                declined=plan["declined"],
                payload=payload_final,
                actor=actor,
                source=source,
            )
            return self._report(
                vocab.STATE_UNMAPPED,
                entry,
                workflow,
                row,
                plan,
                payload_final,
                delivery_mode,
                started=False,
                payload_source=payload_source,
                message=(
                    "The delivery reached a saved trigger and no mapping matched it, so the "
                    "workflow did not start. No variable was bound."
                ),
            )

        if delivery_mode == vocab.MODE_TEST:
            row = self._record(
                room_ref,
                entry,
                workflow,
                state=vocab.STATE_ACCEPTED,
                reason=None,
                mode=delivery_mode,
                bindings=plan["bindings"],
                declined=plan["declined"],
                payload=payload_final,
                actor=actor,
                source=source,
            )
            return self._report(
                vocab.STATE_ACCEPTED,
                entry,
                workflow,
                row,
                plan,
                payload_final,
                delivery_mode,
                started=False,
                payload_source=payload_source,
                message=(
                    "Test request validated. The mapping bound these variables and the "
                    "workflow did not start."
                ),
            )

        seen = self.store.find(
            vocab.COLLECTION_DEDUPE,
            {"trigger_ref": entry["id"], "fingerprint": rules.fingerprint(payload_final)},
            limit=1,
        )
        if seen:
            row = self._record(
                room_ref,
                entry,
                workflow,
                state=vocab.STATE_DUPLICATE,
                reason=None,
                mode=delivery_mode,
                bindings=plan["bindings"],
                declined=plan["declined"],
                payload=payload_final,
                duplicate_of=seen[0]["id"],
                actor=actor,
                source=source,
            )
            return self._report(
                vocab.STATE_DUPLICATE,
                entry,
                workflow,
                row,
                plan,
                payload_final,
                delivery_mode,
                started=False,
                payload_source=payload_source,
                message=(
                    "This payload was sent before, so it was recorded as a duplicate and the "
                    "workflow did not start again."
                ),
            )

        row = self._record(
            room_ref,
            entry,
            workflow,
            state=vocab.STATE_ACCEPTED,
            reason=None,
            mode=delivery_mode,
            bindings=plan["bindings"],
            declined=plan["declined"],
            payload=payload_final,
            actor=actor,
            source=source,
        )
        self.store.create(
            vocab.COLLECTION_DEDUPE,
            {
                "trigger_ref": entry["id"],
                "fingerprint": rules.fingerprint(payload_final),
                "invocation_ref": row["id"],
                "first_seen_at": self._stamp(),
            },
            room_id=room_ref,
            actor=actor,
            source=source,
        )
        run = self._start_run(
            room_ref, entry, workflow, row, plan, payload_final, actor=actor, source=source
        )
        return self._report(
            vocab.STATE_ACCEPTED,
            entry,
            workflow,
            row,
            plan,
            payload_final,
            delivery_mode,
            started=True,
            payload_source=payload_source,
            run=run,
            message=("Delivery accepted. The workflow started with these variables bound."),
        )

    def _mode(self, mode: str | None, query: Mapping[str, Any] | None) -> str:
        if mode:
            wanted = str(mode).strip().lower()
            return wanted if wanted in vocab.MODES else vocab.MODE_LIVE
        if rules.test_mode_requested((query or {}).get(vocab.TEST_MODE_PARAM)):
            return vocab.MODE_TEST
        return vocab.MODE_LIVE

    def _trigger_by_token(self, token: str) -> dict[str, Any]:
        rows = self.store.find(vocab.COLLECTION_TRIGGER, {"token": str(token)}, limit=1)
        if not rows:
            raise UnknownEndpoint(
                "this URL is not a webhook URL this deployment issued. Copy the generated "
                "URL from the trigger, or create a trigger.",
                hint="the token is generated per trigger and shown beside the trigger",
            )
        return rows[0]

    def _start_run(
        self,
        room_ref: str,
        entry: Mapping[str, Any],
        workflow: Mapping[str, Any],
        invocation: Mapping[str, Any],
        plan: Mapping[str, Any],
        payload: Any,
        *,
        actor: str,
        source: str | None,
    ) -> dict[str, Any]:
        """Start one run, recording what the workflow planned to do.

        The repository ships no pausable engine for this workflow's downstream
        actions, so the run records the declared actions as its plan and its
        status is ``started``. See the decision register entry
        ``a-run-records-its-plan-not-its-effects``: claiming to have executed a
        contact write or a Google Sheets post would be a claim this build cannot
        support.
        """
        declared = self._data(workflow).get("actions") or []
        created = self.store.create(
            vocab.COLLECTION_RUN,
            {
                "workflow_ref": workflow["id"],
                "trigger_ref": entry["id"],
                "invocation_ref": invocation["id"],
                "status": vocab.RUN_STARTED,
                "mode": vocab.MODE_LIVE,
                "bindings": plan["bindings"],
                "declined": plan["declined"],
                "planned_actions": list(declared),
                "payload_shape": rules.shape_of(payload),
                "started_at": self._stamp(),
            },
            room_id=room_ref,
            actor=actor,
            source=source,
        )
        return self._run_summary(created)

    # -- reads --------------------------------------------------------------- #

    def invocations(
        self,
        room_ref: str,
        *,
        state: str | None = None,
        mode: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        rows = self.store.list(
            vocab.COLLECTION_INVOCATION,
            room_id=str(room_ref),
            limit=max(1, min(int(limit), _PAGE)),
        )
        out = [self._invocation_summary(row) for row in rows]
        if state:
            out = [row for row in out if row["state"] == state]
        if mode:
            out = [row for row in out if row["mode"] == mode]
        return {
            "room_ref": str(room_ref),
            "count": len(out),
            "by_state": _tally(out, "state"),
            "invocations": out,
        }

    def invocation(self, invocation_id: str) -> dict[str, Any]:
        return self._invocation_summary(self._require(invocation_id, vocab.COLLECTION_INVOCATION))

    def runs(self, room_ref: str, *, limit: int = 100) -> dict[str, Any]:
        rows = self.store.list(
            vocab.COLLECTION_RUN, room_id=str(room_ref), limit=max(1, min(int(limit), _PAGE))
        )
        out = [self._run_summary(row) for row in rows]
        return {"room_ref": str(room_ref), "count": len(out), "runs": out}

    def run(self, run_id: str) -> dict[str, Any]:
        return self._run_summary(self._require(run_id, vocab.COLLECTION_RUN))

    def summary(self, room_ref: str | None = None) -> dict[str, Any]:
        """Headline numbers for one room, or for the whole workspace.

        Reads only, so it writes no audit row. A route that wrote on every read
        would fill the guarantee's own log with rows describing nothing happening.
        """
        where_room = str(room_ref) if room_ref else None
        workflows = self.store.list(vocab.COLLECTION_WORKFLOW, room_id=where_room, limit=_PAGE)
        triggers = self.store.list(vocab.COLLECTION_TRIGGER, room_id=where_room, limit=_PAGE)
        invocations = [
            self._invocation_summary(row)
            for row in self.store.list(vocab.COLLECTION_INVOCATION, room_id=where_room, limit=_PAGE)
        ]
        runs = self.store.list(vocab.COLLECTION_RUN, room_id=where_room, limit=_PAGE)
        return {
            "ticket": vocab.TICKET,
            "room_ref": room_ref,
            "workflows": len(workflows),
            "published": sum(
                1
                for row in workflows
                if (row.get("data") or {}).get("status") == vocab.STATUS_PUBLISHED
            ),
            "triggers": len(triggers),
            "saved_triggers": sum(1 for row in triggers if (row.get("data") or {}).get("saved")),
            "invocations": len(invocations),
            "by_state": _tally(invocations, "state"),
            "accepted": sum(1 for row in invocations if row["state"] == vocab.STATE_ACCEPTED),
            "duplicates": sum(1 for row in invocations if row["state"] == vocab.STATE_DUPLICATE),
            "unmapped": sum(1 for row in invocations if row["state"] == vocab.STATE_UNMAPPED),
            "rejected": sum(1 for row in invocations if row["state"] == vocab.STATE_REJECTED),
            "test_deliveries": sum(1 for row in invocations if row["mode"] == vocab.MODE_TEST),
            "runs": len(runs),
            "notes": [
                "The webhook URL is unique for each trigger, and the trigger is the unit of uniqueness.",
                "The payload shape is unvalidated. Nothing is refused for a missing key unless a mapping requires it.",
                "Every delivery is signed. A minted URL that anyone can call is a published write endpoint.",
                "A delivery that matches no mapping is recorded as unmapped and starts nothing.",
            ],
            "rate_limits_enforced": False,
            "premium_trigger_is_a_gate": False,
        }

    def vocabulary(self) -> dict[str, Any]:
        """Every researched term, plus this workflow's signing contract."""
        return {
            "ticket": vocab.TICKET,
            "vocabulary": vocab.describe(),
            "signing": {
                "signature_header": signing.SIGNATURE_HEADER,
                "timestamp_header": signing.TIMESTAMP_HEADER,
                "rule": vocab.SIGNING_RULE,
                "encoding": "hex",
                "timestamp_unit": "unix seconds",
                "signed_input": vocab.SIGNED_INPUT_NOTE,
                "window_seconds": signing.MAX_AGE_SECONDS,
                "required": "always",
                "required_when": "every delivery, because this build fails closed",
                "reused_from": "WF-113 dsr.webhook_ingest.signing",
                "fail_closed": vocab.FAIL_CLOSED,
            },
            "inferences": infer.count(),
        }

    def inferences(self) -> dict[str, Any]:
        return {"ticket": vocab.TICKET, "count": infer.count(), "decisions": infer.describe()}

    def inference(self, decision_id: str) -> dict[str, Any] | None:
        return infer.describe_one(decision_id)

    def known_inference_ids(self) -> list[str]:
        return infer.known_ids()

    # -- internals ----------------------------------------------------------- #

    def _data(self, row: Mapping[str, Any]) -> dict[str, Any]:
        return dict(row.get("data") or {})

    def _require(self, record_id: str, collection: str) -> dict[str, Any]:
        row = self.store.get(record_id)
        if row is None or row["collection"] != collection:
            error: type[WebhookWorkflowError] = {
                vocab.COLLECTION_TRIGGER: UnknownTrigger,
                vocab.COLLECTION_RUN: UnknownRun,
                vocab.COLLECTION_VARIABLE: UnknownVariable,
                vocab.COLLECTION_INVOCATION: UnknownInvocation,
            }.get(collection, UnknownTrigger)
            raise error(f"no {collection} record with id {record_id}.")
        return row

    def _raw_text(self, raw_body: Any, payload: Any) -> str:
        if raw_body is None:
            return rules.canonical_json(payload)
        if isinstance(raw_body, bytes):
            return raw_body.decode("utf-8", "replace")
        return str(raw_body)

    def _record(
        self,
        room_ref: str,
        entry: Mapping[str, Any],
        workflow: Mapping[str, Any],
        *,
        state: str,
        reason: str | None,
        mode: str,
        actor: str,
        source: str | None,
        bindings: Any = None,
        declined: Any = None,
        payload: Any = None,
        detail: Mapping[str, Any] | None = None,
        duplicate_of: str | None = None,
    ) -> dict[str, Any]:
        return self.store.create(
            vocab.COLLECTION_INVOCATION,
            {
                "trigger_ref": entry["id"],
                "workflow_ref": workflow["id"],
                "state": state,
                "reason": reason,
                "mode": mode,
                "bindings": list(bindings or []),
                "declined": list(declined or []),
                "payload": payload,
                "payload_fingerprint": rules.fingerprint(payload) if payload is not None else None,
                "detail": dict(detail or {}),
                "duplicate_of": duplicate_of,
                "received_at": self._stamp(),
            },
            room_id=room_ref,
            actor=actor,
            source=source,
        )

    def _report(
        self,
        state: str,
        entry: Mapping[str, Any],
        workflow: Mapping[str, Any],
        row: Mapping[str, Any],
        plan: Mapping[str, Any],
        payload: Any,
        mode: str,
        *,
        started: bool,
        payload_source: str,
        run: Mapping[str, Any] | None = None,
        message: str = "",
    ) -> dict[str, Any]:
        return {
            "state": state,
            "mode": mode,
            "started": started,
            "message": message,
            "trigger_ref": entry["id"],
            "workflow_ref": workflow["id"],
            "invocation": self._invocation_summary(row),
            "run": dict(run) if run else None,
            "bindings": list(plan.get("bindings") or []),
            "declined": list(plan.get("declined") or []),
            "matched": plan.get("matched", 0),
            "planned": plan.get("planned", 0),
            "payload": rules.payload_facts(payload, source=payload_source),
        }

    def _workflow_summary(self, row: Mapping[str, Any]) -> dict[str, Any]:
        data = self._data(row)
        return {
            "id": row["id"],
            "room_ref": row.get("room_id"),
            "name": data.get("name"),
            "description": data.get("description"),
            "status": data.get("status"),
            "actions": data.get("actions") or [],
            "action_count": data.get("action_count", len(data.get("actions") or [])),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }

    def _variable_summary(self, row: Mapping[str, Any]) -> dict[str, Any]:
        data = self._data(row)
        return {
            "id": row["id"],
            "workflow_ref": data.get("workflow_ref"),
            "name": data.get("name"),
            "type": data.get("type"),
            "description": data.get("description"),
            "sample": data.get("sample"),
            "created_at": row.get("created_at"),
        }

    def _trigger_summary(self, row: Mapping[str, Any]) -> dict[str, Any]:
        data = self._data(row)
        token = data.get("token")
        return {
            "id": row["id"],
            "room_ref": row.get("room_id"),
            "workflow_ref": data.get("workflow_ref"),
            "name": data.get("name"),
            "kind": data.get("kind"),
            "label": data.get("label"),
            "saved": bool(data.get("saved")),
            "saved_at": data.get("saved_at"),
            "methods": data.get("methods") or list(vocab.METHODS),
            "mappings": data.get("mappings") or [],
            "mapping_count": data.get("mapping_count", len(data.get("mappings") or [])),
            # The signing value is served only in the creation response.
            "has_signing_value": bool(data.get("signing_value")),
            "premium_trigger": bool(data.get("premium_trigger")),
            "token": token,
            "url": vocab.ENDPOINT_PATH_TEMPLATE.format(token=token) if token else None,
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }

    def _invocation_summary(self, row: Mapping[str, Any]) -> dict[str, Any]:
        data = self._data(row)
        return {
            "id": row["id"],
            "room_ref": row.get("room_id"),
            "trigger_ref": data.get("trigger_ref"),
            "workflow_ref": data.get("workflow_ref"),
            "state": data.get("state"),
            "reason": data.get("reason"),
            "mode": data.get("mode"),
            "bindings": data.get("bindings") or [],
            "declined": data.get("declined") or [],
            "detail": data.get("detail") or {},
            "duplicate_of": data.get("duplicate_of"),
            "fingerprint": data.get("payload_fingerprint"),
            "payload_shape": rules.shape_of(data.get("payload")),
            "received_at": data.get("received_at"),
            "recorded_at": row.get("created_at"),
        }

    def _run_summary(self, row: Mapping[str, Any]) -> dict[str, Any]:
        data = self._data(row)
        return {
            "id": row["id"],
            "room_ref": row.get("room_id"),
            "workflow_ref": data.get("workflow_ref"),
            "trigger_ref": data.get("trigger_ref"),
            "invocation_ref": data.get("invocation_ref"),
            "status": data.get("status"),
            "mode": data.get("mode"),
            "bindings": data.get("bindings") or [],
            "declined": data.get("declined") or [],
            "planned_actions": data.get("planned_actions") or [],
            "payload_shape": data.get("payload_shape"),
            "started_at": data.get("started_at"),
            "recorded_at": row.get("created_at"),
        }


def _tally(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        value = row.get(key)
        if value is None:
            continue
        out[str(value)] = out.get(str(value), 0) + 1
    return out


#: The error types the HTTP layer maps to a status. Exported so the feature module
#: and its tests agree on one list.
ERRORS: tuple[type[WebhookWorkflowError], ...] = (
    BodyNotAllowedOnGet,
    InvalidTrigger,
    InvalidVariable,
    InvalidWorkflow,
    MalformedPayload,
    MethodNotAllowed,
    RequiredMappingUnresolved,
    SignatureMismatch,
    SignatureMissing,
    TriggerNotSaved,
    UnknownInvocation,
    UnknownRun,
    UnknownTrigger,
    UnknownVariable,
    UnknownWorkflow,
    WorkflowNotPublished,
)
