"""WF-009 - Approve and publish library content, immediately or on schedule.

This is the domain layer for the researched workflow. It owns three behaviours
and nothing else: approval workflows, publication, and the destinations a
published document lands in.

It was ported, essentially unchanged, from ``backend/dsr/publishing.py`` on
``feature/WF-009-approve-and-publish-library-content-immediately-or-on``. It lives
inside the feature folder rather than at ``dsr/publishing.py`` because WF-011's
branch added a *different* ``backend/dsr/publishing.py``, and keeping this file
where two workflows both want it would recreate the exact filename collision the
plugin host exists to remove. See the module docstring of
``dsr.features.wf009_publishing`` for the rest of the port.

Sourced behaviour
-----------------
Every rule below that is not marked ``inference`` is taken from
``docs/research/digital-sales-room-workflows/wf/WF-009.md``:

* an **approval process** is "a named template that defines the ordered steps,
  assigned approvers, and routing rules applied to a class of approval
  workflows";
* an approval workflow carries **ordered steps**, each with a ``status`` of
  ``Pending`` | ``Approved`` | ``Rejected``, plus ``approveButtonLabel``,
  ``rejectButtonLabel``, ``assignedTo``, ``approvers[]`` and ``watchers[]``;
* publishing transitions a document "from Draft to Published" and "always
  publishes the latest version of each document";
* "when ``publishAt`` is omitted, the documents are published immediately" and
  "All times are processed in UTC";
* the batch ceiling is **10 items when publishing immediately** and **50 items
  when ``publishAt`` is supplied**;
* the result is a partial-success envelope of ``totalRequests`` /
  ``totalSucceeded`` / ``totalErrors`` / ``totalWarnings`` with per-item
  ``errors[]`` and ``warnings[]``;
* ``IsSendNotification`` defaults to ``true`` and "notify subscribers when the
  content is published";
* published content lands in profiles through **Dynamic Folders** that
  "automatically detect the content based on the content's metadata and content
  properties", and the endpoint "does not currently support a parameter to
  publish to specific profiles" - so this module deliberately exposes no such
  parameter.

The researched field names are camelCase (``approveButtonLabel``). This codebase
is snake_case throughout, so the mapping is ``approve_button_label`` and so on;
the vocabulary itself is unchanged.

Design inferences
-----------------
These are **not** in the research. They are design decisions, and the reasoning
is recorded inline so a reviewer can disagree with them:

1. **A scheduled publication is applied by an explicit sweep.** The research
   documents ``publishAt`` as an input and says the transition is deferred to a
   UTC instant, but documents no mechanism for that transition actually
   happening. There is no background scheduler in this project, so
   :meth:`PublishingService.run_due` applies publications whose instant has
   passed. The pattern mirrors the one the research does document - "Poll this
   endpoint periodically" - rather than inventing a daemon.
2. **Rejection is terminal.** The research names a ``Rejected`` status but does
   not say whether the author may resubmit into the same workflow. Here a
   rejection ends the workflow; resubmission creates a new one.
3. **Steps advance strictly in order.** "Ordered steps" is sourced; whether they
   may run in parallel is not. A step is actionable only when it is the
   earliest still-``Pending`` step.
4. **Derived state is never stored.** ``current_step_key`` and which step is
   actionable are computed on read and returned under a ``derived`` key, so a
   stored payload can never drift from the step list it describes.
5. **"Already published" is an error; a notification problem is a warning.**
   The research lists both example messages without saying which bucket each
   belongs to. A document that was not published is a failure; a document that
   *was* published but could not be notified is a success with a caveat.
6. **Watcher notification on a decision is inferred.** The research names
   ``watchers[]`` as a field but documents notification only for *subscribers*.
   Here watchers are recorded as notified when a workflow reaches a terminal
   state, and are not notified on intermediate steps.
7. **Dynamic Folder rules are a flat ``{dotted.path: value}`` map.** The
   research describes the behaviour in prose and documents no rule language. The
   map is evaluated through the same dynamic index the rest of the store uses, so
   it needs no new query machinery and cannot drift from it.

Storage
-------
Every write goes through :class:`~dsr.store.RecordStore`, which is a façade over
:class:`~dsr.db.audited.AuditedDatabase`. There is no other write path in this
module, and no migration: workflow steps, folder rules, and subscriber lists are
all arbitrary JSON in ``records.data``, so a team can add a field without
coordinating with anyone.

One read path still reaches through the façade to ``store.db``: ``count``, in
:meth:`PublishingService.list_workflows`, because ``RecordStore`` does not
forward it and ``total_count`` is a documented part of the researched
``GET /approvalWorkflows`` contract, so substituting a capped ``len(list(...))``
would quietly downgrade it. That is a reach-through the port would rather not
have, and it is raised as a finding rather than fixed here: ``store.py`` is a
shared file, and forwarding ``count`` the way the branch did is a platform
decision, not a feature one. It does not weaken the audit guarantee, because the
audit row is written by ``AuditedDatabase``, which is still the thing being
called. A second reach-through, ``query_index`` for Dynamic Folder matching, was
removed by expressing the same query as ``find(CONTENT, {path: value})``, which
is scoped more narrowly and needs no reach-through.

Every method that writes takes a required ``source``. That is the port brief's
hard rule 4: the audit row must name the route that actually served the write,
so the string is built by the HTTP layer from ``router.prefix`` and this module
never hard-codes a path it might stop serving.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from dsr.db.audited import AuditError, utcnow
from dsr.store import RecordStore

# --------------------------------------------------------------------------- #
# Collections
#
# Collection names are ordinary strings, not schema: they are conventions this
# feature uses, and the generic /api/records endpoints accept any of them.
# --------------------------------------------------------------------------- #

CONTENT = "document"
PROCESS = "approval_process"
WORKFLOW = "approval_workflow"
PUBLICATION = "publication"
FOLDER = "dynamic_folder"
SUBSCRIPTION = "subscription"
NOTIFICATION = "notification"

# --------------------------------------------------------------------------- #
# Sourced constants and vocabulary
# --------------------------------------------------------------------------- #

#: "The documented maximum of 10 items per request applies to immediate
#: (unscheduled) publishing; when publishAt is supplied ... up to 50 items."
IMMEDIATE_BATCH_LIMIT = 10
SCHEDULED_BATCH_LIMIT = 50

#: Step status vocabulary, exactly as the researched schema note records it.
PENDING = "Pending"
APPROVED = "Approved"
REJECTED = "Rejected"

#: A workflow is ``Pending`` until its last step is approved, then ``Approved``;
#: a rejected step ends it as ``Rejected``.
WORKFLOW_TERMINAL = (APPROVED, REJECTED)

#: The documented publish pattern. `datetime-local` (what a browser's date input
#: produces) and ISO-8601 are accepted as a superset; a value with no offset is
#: read as UTC, because "All times are processed in UTC".
_PUBLISH_AT_DOCUMENTED = re.compile(r"^\d{4}-\d{2}-\d{2}( \d{1,2}:\d{2} (AM|PM))?$", re.IGNORECASE)


class PublishError(AuditError):
    """The request cannot be attempted at all. Surfaces as HTTP 400."""


class ApprovalConflict(AuditError):
    """The request is well-formed but conflicts with current state. HTTP 409."""


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _is_published(status: Any) -> bool:
    """True when a document is already published.

    Case-insensitive on purpose. The researched vocabulary is ``Draft`` and
    ``Published``, but existing seeded records in this project carry lowercase
    ``published``; treating both as published is what stops a re-publish of
    already-live content.
    """
    return str(status or "").strip().casefold() == "published"


def parse_publish_at(raw: Any) -> datetime:
    """Read a ``publishAt`` value as a UTC instant.

    Accepts the documented ``YYYY-MM-DD`` / ``YYYY-MM-DD hh:mm AM`` form, plus
    ISO-8601 and ``datetime-local`` so the browser can post what its date input
    produces. A value with no offset is interpreted as UTC, per the documented
    "All times are processed in UTC".
    """
    text = str(raw or "").strip()
    if not text:
        raise PublishError("publishAt is required when scheduling a publication")

    if _PUBLISH_AT_DOCUMENTED.match(text):
        fmt = "%Y-%m-%d %I:%M %p" if len(text) > 10 else "%Y-%m-%d"
        try:
            moment = datetime.strptime(text.upper(), fmt)
        except ValueError as exc:
            raise PublishError(f"publishAt is not a valid date: {text!r}") from exc
        return moment.replace(tzinfo=timezone.utc)

    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PublishError(
            f"publishAt is not a recognised date: {text!r}. "
            f"Expected {text!r}-style dates, which are read as UTC."
        ) from exc
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _as_list(value: Any) -> list[Any]:
    """Coerce a scalar-or-list field into a list, dropping blanks."""
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple)):
        return [item for item in value if item not in (None, "")]
    return [value]


def _scalar(value: Any) -> bool:
    """Whether a value can be compared through the dynamic index."""
    return isinstance(value, (str, int, float, bool))


def _dedupe(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered


def _content_entries(content: Any) -> list[dict[str, Any]]:
    """Normalise a ``content`` array to ``[{id, version_id}]``.

    The researched request body is ``content: [{id}, {id}, ...]``, so an object
    per item is the documented shape. A bare string id is also accepted because
    it is what a caller with nothing to say about versions naturally has. Any
    other key on the object is carried through untouched.
    """
    entries: list[dict[str, Any]] = []
    for item in content or []:
        if isinstance(item, Mapping):
            entry = {str(k): v for k, v in dict(item).items()}
            identifier = entry.get("id") or entry.get("content_id")
        else:
            entry = {"id": str(item)}
            identifier = entry.get("id")
        if identifier in (None, ""):
            continue
        entry["id"] = str(identifier)
        entry.setdefault("version_id", entry.pop("content_id", None))
        entries.append(entry)
    return entries


def _step_key(raw: Any, index: int, taken: set[str]) -> str:
    """A stable, URL-safe key for a step.

    Callers may name their steps; when they do not, the label is slugged, and
    failing that the ordinal is used. Keys must be unique because the decision
    endpoint addresses a step by key.
    """
    base = str(raw or "").strip().lower()
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
    if not base:
        base = f"step-{index + 1}"
    candidate = base
    suffix = 2
    while candidate in taken:
        candidate = f"{base}-{suffix}"
        suffix += 1
    taken.add(candidate)
    return candidate


def _sort_steps(steps: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Order steps by their declared ``order``, keeping the original sequence
    as the tie-break so a template that omits ``order`` still behaves."""
    decorated = list(enumerate(steps))
    decorated.sort(key=lambda pair: (_as_int(pair[1].get("order"), pair[0] + 1), pair[0]))
    return [dict(step) for _, step in decorated]


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _envelope(
    *,
    mode: str,
    total_requests: int,
    succeeded: int = 0,
    errors: Sequence[str] | None = None,
    warnings: Sequence[str] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """The partial-success result shape.

    Names are the researched ``totalRequests`` / ``totalSucceeded`` /
    ``totalErrors`` / ``totalWarnings`` and ``errors[]`` / ``warnings[]``,
    snake_cased like the rest of this API.
    """
    error_list = list(errors or [])
    warning_list = list(warnings or [])
    return {
        "mode": mode,
        "total_requests": total_requests,
        "total_succeeded": succeeded,
        "total_errors": len(error_list),
        "total_warnings": len(warning_list),
        "errors": error_list,
        "warnings": warning_list,
        **extra,
    }


# --------------------------------------------------------------------------- #
# Service
# --------------------------------------------------------------------------- #


class PublishingService:
    """Approval workflows and publication for a Digital Sales Room.

    Every write method requires ``source``: the route that served the request,
    passed in by the HTTP layer so the audit row cannot name a path the app has
    stopped serving.
    """

    def __init__(self, store: RecordStore) -> None:
        self.store = store

    # -- helpers ------------------------------------------------------------ #

    def _find(self, collection: str, where: Mapping[str, Any], *, limit: int = 1000) -> list[dict[str, Any]]:
        return self.store.find(collection, dict(where), limit=limit)

    def _document(self, document_id: str) -> dict[str, Any] | None:
        record = self.store.get(document_id)
        if record is None or record["collection"] != CONTENT:
            return None
        return record

    def _latest_version(self, document: Mapping[str, Any]) -> str | None:
        """The version a publish will release.

        The researched publish endpoint "always publishes the latest version of
        each document", so selection is positional rather than by request.
        """
        versions = document.get("data", {}).get("versions")
        if isinstance(versions, list) and versions:
            latest = versions[-1]
            if isinstance(latest, Mapping):
                return str(latest.get("version_id") or latest.get("id") or "") or None
            return str(latest)
        return document.get("data", {}).get("version_id")

    # -- approval processes -------------------------------------------------- #

    def create_process(
        self,
        data: Mapping[str, Any],
        *,
        actor: str | None = None,
        room_id: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Create an approval process template.

        ``steps`` is the only structural requirement. Everything else in
        ``data`` is stored verbatim, so a team can add routing rules or an
        approval SLA without a migration.
        """
        name = str(data.get("name") or "").strip()
        if not name:
            raise PublishError("an approval process needs a name")

        raw_steps = data.get("steps")
        if not isinstance(raw_steps, (list, tuple)) or not raw_steps:
            raise PublishError("an approval process needs at least one step")

        taken: set[str] = set()
        steps: list[dict[str, Any]] = []
        for index, raw in enumerate(raw_steps):
            if not isinstance(raw, Mapping):
                raise PublishError(f"step {index + 1} must be an object")
            key = _step_key(raw.get("key") or raw.get("label"), index, taken)
            # Unrecognised keys pass through untouched: schema flexibility means
            # this layer stores what it is given rather than pruning it.
            step = {k: v for k, v in dict(raw).items() if k != "key"}
            step.update(
                {
                    "key": key,
                    "order": _as_int(raw.get("order"), index + 1),
                    "label": str(raw.get("label") or key),
                    "assigned_to": raw.get("assigned_to"),
                    "approvers": _as_list(raw.get("approvers")),
                    "watchers": _as_list(raw.get("watchers")),
                    "approve_button_label": str(raw.get("approve_button_label") or "Approve"),
                    "reject_button_label": str(raw.get("reject_button_label") or "Reject"),
                }
            )
            steps.append(step)

        payload = {k: v for k, v in dict(data).items() if k not in ("name", "steps")}
        payload.update({"name": name, "steps": _sort_steps(steps)})
        return self.store.create(PROCESS, payload, room_id=room_id, actor=actor, source=source)

    def list_processes(self, *, room_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        return self.store.list(PROCESS, room_id=room_id, limit=limit, order_by="updated_at", descending=False)

    # -- submissions --------------------------------------------------------- #

    def submit(
        self,
        *,
        content: Sequence[str],
        process_id: str,
        actor: str | None = None,
        room_id: str | None = None,
        comment: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Submit library content into an approval process.

        The steps are **copied** from the template onto the instance. A workflow
        that read its steps from the template by reference would change shape
        under a reviewer the moment somebody edited the template, which is
        exactly the sort of silent history rewrite an audit-backed product cannot
        afford.
        """
        process = self.store.get(process_id)
        if process is None or process["collection"] != PROCESS:
            raise PublishError(f"approval process {process_id} not found")

        document_ids = _dedupe([entry["id"] for entry in _content_entries(content)])
        if not document_ids:
            raise PublishError("content must contain at least one item")

        documents: list[dict[str, Any]] = []
        for document_id in document_ids:
            document = self._document(document_id)
            if document is None:
                raise PublishError(f"library content {document_id} not found")
            if _is_published(document["data"].get("status")):
                raise PublishError(f"{document_id} is already published and cannot enter approval")
            if self._open_workflow_for(document_id):
                raise ApprovalConflict(f"{document_id} is already in an open approval workflow")
            documents.append(document)

        # The submitter cannot be the only possible reviewer of their own
        # submission, but that is a policy question rather than a sourced rule,
        # so it is left to the caller to express in `steps[].approvers`.
        taken: set[str] = set()
        steps = [
            {
                **dict(template),
                "key": _step_key(template.get("key"), index, taken),
                "order": _as_int(template.get("order"), index + 1),
                "status": PENDING,
                "decided_by": None,
                "decided_at": None,
                "comment": None,
            }
            for index, template in enumerate(process["data"].get("steps") or [])
        ]

        resolved_room = room_id or next((d["room_id"] for d in documents if d["room_id"]), None)
        payload = {
            "approval_process": {"id": process["id"], "name": process["data"].get("name")},
            "status": PENDING,
            "steps": _sort_steps(steps),
            "content": [
                {
                    "id": document["id"],
                    "title": document["data"].get("title"),
                    "version_id": self._latest_version(document),
                }
                for document in documents
            ],
            "submitted_by": actor,
            "submitted_at": utcnow(),
            "decided_at": None,
            "comment": comment,
        }
        return self.store.create(WORKFLOW, payload, room_id=resolved_room, actor=actor, source=source)

    def _open_workflow_for(self, document_id: str) -> dict[str, Any] | None:
        """The live ``Pending`` workflow a document is stuck in, if any.

        Array members are flattened positionally by the dynamic index, so
        ``content.0.id`` is a real path: this filters on a nested field of an
        array without a schema and without a second query implementation.
        """
        matches = self._find(WORKFLOW, {"status": PENDING})
        for workflow in matches:
            for entry in workflow["data"].get("content") or []:
                if isinstance(entry, Mapping) and str(entry.get("id")) == document_id:
                    return workflow
        return None

    # -- reading the queue --------------------------------------------------- #

    def list_workflows(
        self,
        *,
        room_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
        queue_path: str = "/api/publishing/workflows",
    ) -> dict[str, Any]:
        """The approval queue, paginated.

        Limits and the ``offset``-walk shape follow the documented
        ``GET /approvalWorkflows`` contract: ``limit`` 1-1000 defaulting to 100,
        ``offset`` defaulting to 0, and a ``total_count`` so a consumer can keep
        walking until it is exhausted.

        ``queue_path`` is supplied by the HTTP layer rather than written here,
        for the same reason ``source`` is: the pagination link is a promise about
        a URL the app actually serves.
        """
        limit = max(1, min(int(limit), 1000))
        offset = max(0, int(offset))

        if status:
            candidates = self._find(WORKFLOW, {"status": status}, limit=1000)
            if room_id:
                candidates = [c for c in candidates if c["room_id"] == room_id]
            total = len(candidates)
            page = candidates[offset : offset + limit]
        elif room_id:
            total = self.store.db.count(WORKFLOW, room_id=room_id)
            page = self.store.list(WORKFLOW, room_id=room_id, limit=limit, offset=offset)
        else:
            total = self.store.db.count(WORKFLOW)
            page = self.store.list(WORKFLOW, limit=limit, offset=offset)

        next_offset = offset + limit
        return {
            "entries": [view_workflow(record) for record in page],
            "total_count": total,
            "limit": limit,
            "offset": offset,
            "next_page": (f"{queue_path}?limit={limit}&offset={next_offset}" if next_offset < total else None),
        }

    def get_workflow(self, workflow_id: str) -> dict[str, Any] | None:
        record = self.store.get(workflow_id)
        if record is None or record["collection"] != WORKFLOW:
            return None
        return view_workflow(record)

    # -- decisions ----------------------------------------------------------- #

    def decide(
        self,
        workflow_id: str,
        step_key: str,
        *,
        decision: str,
        actor: str | None = None,
        comment: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Approve or reject one step of a workflow.

        Guards, in order: the workflow must exist and still be open, the step
        must exist, the step must be the one currently awaiting a decision, and
        the actor must be somebody the step is assigned to.
        """
        record = self.store.get(workflow_id)
        if record is None or record["collection"] != WORKFLOW:
            raise PublishError(f"approval workflow {workflow_id} not found")

        data = record["data"]
        status = str(data.get("status") or "")
        if status != PENDING:
            raise ApprovalConflict(
                f"workflow {workflow_id} is {status or 'in an unknown state'} and no longer accepts decisions"
            )

        steps = [dict(step) for step in data.get("steps") or []]
        target = next((step for step in steps if str(step.get("key")) == str(step_key)), None)
        if target is None:
            raise PublishError(f"step {step_key!r} is not part of workflow {workflow_id}")

        current = _current_step(steps)
        if current is None or str(current.get("key")) != str(step_key):
            raise ApprovalConflict(
                f"step {step_key!r} is not the step awaiting a decision"
                + (f" (that is {current.get('key')!r})" if current else "")
            )

        if not _actor_may_act(target, actor):
            raise ApprovalConflict(
                f"{actor or 'anonymous'} is not assigned to step {step_key!r}"
            )

        verdict = str(decision or "").strip().casefold()
        if verdict not in ("approve", "reject"):
            raise PublishError("decision must be 'approve' or 'reject'")

        now = utcnow()
        target["status"] = APPROVED if verdict == "approve" else REJECTED
        target["decided_by"] = actor
        target["decided_at"] = now
        if comment:
            target["comment"] = comment

        patch: dict[str, Any] = {"steps": steps, "decided_at": None}
        if verdict == "reject":
            # Inference 2: a rejection ends the workflow. Resubmission is a new
            # workflow, so the rejected history stays intact.
            patch["status"] = REJECTED
            patch["decided_at"] = now
        elif all(str(step.get("status")) == APPROVED for step in steps):
            patch["status"] = APPROVED
            patch["decided_at"] = now

        updated = self.store.update(workflow_id, patch, actor=actor, source=source)
        if updated["data"].get("status") in WORKFLOW_TERMINAL:
            self._notify_watchers(updated, actor=actor, source=source)
        return view_workflow(updated)

    def _notify_watchers(self, workflow: Mapping[str, Any], *, actor: str | None, source: str) -> None:
        """Record watcher notification when a workflow reaches a terminal state.

        Inference 6: the research documents notification for *subscribers* only.
        Watchers are recorded so the intent is visible, and deliberately not
        notified on intermediate steps.
        """
        status = workflow["data"].get("status")
        watchers = _dedupe(
            [
                str(watcher)
                for step in workflow["data"].get("steps") or []
                for watcher in _as_list(step.get("watchers"))
            ]
        )
        for watcher in watchers:
            self.store.create(
                NOTIFICATION,
                {
                    "kind": f"workflow_{str(status or '').casefold()}",
                    "workflow_id": workflow["id"],
                    "subscriber": watcher,
                    "workflow_status": status,
                    "decided_by": actor,
                    "created_at": utcnow(),
                    "delivery": "recorded",
                },
                room_id=workflow.get("room_id"),
                actor=actor,
                source=source,
            )

    # -- publication --------------------------------------------------------- #

    def publish(
        self,
        *,
        content: Sequence[str],
        publish_at: Any = None,
        is_send_notification: bool = True,
        comment: str | None = None,
        actor: str | None = None,
        subscribers: Sequence[str] | None = None,
        room_id: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Publish library content now, or schedule it for a future UTC instant.

        Omitting ``publish_at`` publishes immediately. Supplying it defers the
        whole transition; the documents are untouched until
        :meth:`run_due` sweeps the instant.

        ``source`` is the route that served this request. A publication applied
        later by the sweep carries the sweep's own source instead, because by
        then *that* is the route that served the write.
        """
        requested = _content_entries(content)
        document_ids = _dedupe([entry["id"] for entry in requested])
        if not document_ids:
            raise PublishError("content must contain at least one item")

        scheduled = publish_at not in (None, "")
        if scheduled:
            instant = parse_publish_at(publish_at)
            limit = SCHEDULED_BATCH_LIMIT
            publish_at_value = instant.isoformat(timespec="seconds")
        else:
            instant = datetime.now(timezone.utc)
            publish_at_value = None
            limit = IMMEDIATE_BATCH_LIMIT

        if len(document_ids) > limit:
            raise PublishError(
                f"a {'scheduled' if scheduled else 'immediate'} publish accepts at most "
                f"{limit} items; {len(document_ids)} were supplied"
            )

        mode = "scheduled" if scheduled else "immediate"

        # Validate up front so a scheduled publish cannot be accepted for content
        # that will be unpublishable by the time its instant arrives.
        errors: list[str] = []
        documents: list[dict[str, Any]] = []
        for document_id in document_ids:
            document = self._document(document_id)
            if document is None:
                errors.append(f"{document_id}: library content not found")
                continue
            if _is_published(document["data"].get("status")):
                errors.append(f"{document_id}: content is already published")
                continue
            documents.append(document)

        if scheduled and errors:
            # Nothing is scheduled when any item is unpublishable: a schedule is a
            # promise about a specific instant, and a partly-honoured one is
            # worse than a refusal the caller can see and fix.
            return _envelope(
                mode=mode,
                total_requests=len(document_ids),
                errors=errors,
                publish_at=publish_at_value,
                publication=None,
                status="rejected",
            )

        publication = self.store.create(
            PUBLICATION,
            {
                "mode": mode,
                "status": "scheduled" if scheduled else "pending",
                "publish_at": publish_at_value,
                "requested_by": actor,
                "content": [
                    {
                        "id": document["id"],
                        "title": document["data"].get("title"),
                        "version_id": self._latest_version(document),
                        "requested_version_id": next(
                            (
                                entry.get("version_id")
                                for entry in requested
                                if entry["id"] == document["id"]
                            ),
                            None,
                        ),
                    }
                    for document in documents
                ],
                "requested_content": document_ids,
                "is_send_notification": bool(is_send_notification),
                "comment": comment,
                "subscribers": _dedupe([str(s) for s in (subscribers or [])]),
                "total_requests": len(document_ids),
                # Items validation already rejected. They are carried on the
                # record rather than discarded, so an immediate publish reports
                # them and the audit trail explains the shortfall.
                "rejected": errors,
            },
            room_id=room_id or next((d["room_id"] for d in documents if d["room_id"]), None),
            actor=actor,
            source=source,
        )

        if scheduled:
            return _envelope(
                mode=mode,
                total_requests=len(document_ids),
                publish_at=publish_at_value,
                publication=publication,
                status="scheduled",
                results=[],
            )

        return self._execute(publication, actor=actor, source=source)

    def _execute(
        self,
        publication: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Apply a publication: release every document it lists.

        Each document is released in its own audited transaction, so a single
        failure cannot undo the documents that succeeded. The publication record
        is then updated once with the totals, and *that* update is what the
        audit log shows as the outcome of the request.

        Every write in here is attributed to ``source``, which is whatever route
        triggered the execution: the publish request for an immediate publish,
        the due sweep for a scheduled one.
        """
        data = publication["data"]
        requester = data.get("requested_by") or actor
        notify = bool(data.get("is_send_notification", True))
        comment = data.get("comment")
        now = utcnow()

        succeeded: list[dict[str, Any]] = []
        # Errors carried from request-time validation, so the caller learns about
        # items that never reached this execution rather than seeing a clean run.
        errors: list[str] = [str(e) for e in (data.get("rejected") or [])]
        warnings: list[str] = []

        for entry in data.get("content") or []:
            if isinstance(entry, Mapping):
                document_id = str(entry.get("id") or "")
                requested_version = entry.get("version_id")
            else:
                document_id = str(entry)
                requested_version = None
            document = self._document(document_id)
            if document is None:
                errors.append(f"{document_id}: library content not found")
                continue
            if _is_published(document["data"].get("status")):
                # Inference 5: an already-published document was not published by
                # this request, so it is an error rather than a warning.
                errors.append(f"{document_id}: content is already published")
                continue

            # The researched endpoint "always publishes the latest version of
            # each document", so the stored version wins over what was requested.
            version_id = self._latest_version(document) or requested_version
            self.store.update(
                document_id,
                {
                    "status": "Published",
                    "content_status": "Published",
                    "published_at": now,
                    "published_by": requester,
                    "published_version_id": version_id,
                    # `addLibraryContentToProfileAt` is a reported field in the
                    # researched reporting API; recording it here keeps the
                    # document's own history self-describing.
                    "add_library_content_to_profile_at": now,
                },
                actor=requester,
                source=source,
            )
            landed = self._land_in_folders(document_id, actor=requester, source=source)
            succeeded.append({"id": document_id, "folders": landed, "version_id": version_id})

            if notify:
                warnings.extend(
                    self._notify_subscribers(
                        document,
                        requester=requester,
                        now=now,
                        requested=_as_list(data.get("subscribers")),
                        source=source,
                    )
                )

        updated = self.store.update(
            publication["id"],
            {
                "status": "published",
                "published_at": now,
                "total_succeeded": len(succeeded),
                "total_errors": len(errors),
                "total_warnings": len(warnings),
                "errors": errors,
                "warnings": warnings,
                "results": succeeded,
            },
            actor=requester,
            source=source,
        )
        return _envelope(
            mode=str(data.get("mode") or "immediate"),
            total_requests=int(data.get("total_requests") or 0),
            succeeded=len(succeeded),
            errors=errors,
            warnings=warnings,
            publication=updated,
            status="published",
            results=succeeded,
        )

    def _notify_subscribers(
        self,
        document: Mapping[str, Any],
        *,
        requester: str | None,
        now: str,
        requested: Sequence[Any] = (),
        source: str,
    ) -> list[str]:
        """Record a publish notification for each resolved subscriber.

        Subscribers named on the request are the audience when any are given;
        otherwise every active subscription on the document's room is. A named
        subscriber that no longer resolves produces the researched warning shape
        - the content *was* published, but notification delivery failed for them.
        """
        resolved = self._resolve_subscribers(document, requested)
        if not resolved:
            return []

        warnings: list[str] = []
        recipients: list[str] = []
        for entry in resolved:
            if entry["resolved"] is None:
                warnings.append(
                    f"{document['id']}: content was published but notification delivery failed "
                    f"for {entry['name']}"
                )
            elif entry["resolved"]:
                recipients.append(entry["name"])

        for subscriber in recipients:
            self.store.create(
                NOTIFICATION,
                {
                    "kind": "publish",
                    "content_id": document["id"],
                    "subscriber": subscriber,
                    "requested_by": requester,
                    "created_at": now,
                    "delivery": "recorded",
                },
                room_id=document.get("room_id"),
                actor=requester,
                source=source,
            )
        return warnings

    def _resolve_subscribers(
        self, document: Mapping[str, Any], requested: Sequence[Any]
    ) -> list[dict[str, Any]]:
        """Pair each audience member with the subscription backing them, if any.

        ``resolved`` is ``None`` for a named subscriber with no live
        subscription, which is the delivery-failure case; ``False`` for a
        deactivated subscription, which is a deliberate opt-out rather than a
        failure and so produces no warning.
        """
        if requested:
            names = [str(item) for item in requested]
        else:
            names = self._room_subscribers(document)

        known = self.store.list(SUBSCRIPTION, limit=1000)
        by_name: dict[str, dict[str, Any]] = {}
        for subscription in known:
            name = subscription["data"].get("subscriber") or subscription["data"].get("subscriber_id")
            if name:
                by_name.setdefault(str(name), subscription)

        resolved: list[dict[str, Any]] = []
        for name in names:
            subscription = by_name.get(name)
            if subscription is None:
                resolved.append({"name": name, "resolved": None})
                continue
            if subscription["data"].get("active") is False:
                resolved.append({"name": name, "resolved": False})
                continue
            if document.get("room_id") and subscription.get("room_id") != document["room_id"]:
                # A subscription on another room is not this document's audience.
                resolved.append({"name": name, "resolved": False})
                continue
            resolved.append({"name": name, "resolved": True})
        return resolved

    def _room_subscribers(self, document: Mapping[str, Any]) -> list[str]:
        """Active subscribers on a document's room.

        A room with no subscription records has nobody to notify, which is not
        an error: ``IsSendNotification`` asks for subscribers to be notified, not
        for a subscription list to exist.
        """
        room_id = document.get("room_id")
        if not room_id:
            return []
        subscriptions = self.store.list(SUBSCRIPTION, room_id=room_id, limit=1000)
        return _dedupe(
            [
                str(subscription["data"].get("subscriber") or subscription["data"].get("subscriber_id"))
                for subscription in subscriptions
                if subscription["data"].get("active") is not False
            ]
        )

    def run_due(
        self,
        *,
        now: datetime | None = None,
        actor: str | None = "system",
        source: str,
    ) -> dict[str, Any]:
        """Apply every scheduled publication whose UTC instant has passed.

        Inference 1: the research documents ``publishAt`` but no mechanism for the
        deferred transition actually firing. This sweep is that mechanism, and it
        is an explicit call rather than a hidden side effect of a read, so that
        "reads never mutate" stays true.

        The writes it performs are attributed to ``source`` - the sweep's own
        route - rather than to the original publish request, because the sweep is
        what served them.
        """
        moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        applied: list[dict[str, Any]] = []
        for publication in self._find(PUBLICATION, {"status": "scheduled"}):
            due = publication["data"].get("publish_at")
            if not due:
                continue
            try:
                instant = parse_publish_at(due)
            except PublishError:
                continue
            if instant > moment:
                continue
            result = self._execute(
                publication,
                actor=publication["data"].get("requested_by") or actor,
                source=source,
            )
            applied.append(
                {
                    "publication_id": publication["id"],
                    "publish_at": due,
                    "total_succeeded": result["total_succeeded"],
                    "total_errors": result["total_errors"],
                }
            )
        return {"ran_at": moment.isoformat(timespec="seconds"), "applied": applied, "count": len(applied)}

    def list_publications(self, *, room_id: str | None = None, limit: int = 100, status: str | None = None) -> dict[str, Any]:
        if status:
            records = self._find(PUBLICATION, {"status": status}, limit=1000)
            if room_id:
                records = [r for r in records if r["room_id"] == room_id]
            records = records[:limit]
        else:
            records = self.store.list(PUBLICATION, room_id=room_id, limit=limit)
        return {"count": len(records), "entries": records}

    # -- dynamic folders ------------------------------------------------------ #

    def create_folder(
        self,
        data: Mapping[str, Any],
        *,
        actor: str | None = None,
        room_id: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Define a Dynamic Folder: a profile destination plus a match rule.

        Inference 7: the research describes Dynamic Folders detecting content
        "based on the content's metadata and content properties" but documents no
        rule language. ``matches`` is a flat ``{dotted.path: value}`` map, which
        is exactly what the store's dynamic index already understands.
        """
        name = str(data.get("name") or "").strip()
        if not name:
            raise PublishError("a dynamic folder needs a name")
        matches = data.get("matches")
        if not isinstance(matches, Mapping) or not matches:
            raise PublishError("a dynamic folder needs at least one match rule")
        for path, expected in matches.items():
            if not str(path).strip() or not _scalar(expected):
                raise PublishError(
                    f"match rule {path!r} must be a dotted path and a string, number, or boolean"
                )

        payload = {k: v for k, v in dict(data).items() if k != "matches"}
        payload.update(
            {
                "name": name,
                "profile": data.get("profile") or "default",
                "matches": dict(matches),
                "content": [],
            }
        )
        return self.store.create(FOLDER, payload, room_id=room_id, actor=actor, source=source)

    def _land_in_folders(self, document_id: str, *, actor: str | None, source: str) -> list[str]:
        """Route a freshly published document into every folder that matches it.

        There is deliberately no "publish to a specific profile" parameter: the
        researched endpoint refuses to offer one, because destinations are
        decided by content metadata rather than by the caller.
        """
        landed: list[str] = []
        for folder in self.store.list(FOLDER, limit=1000):
            matches = folder["data"].get("matches") or {}
            if not isinstance(matches, Mapping) or not matches:
                continue
            if not self._matches(document_id, matches):
                continue
            existing = list(folder["data"].get("content") or [])
            if document_id in existing:
                continue
            existing.append(document_id)
            self.store.update(
                folder["id"],
                {"content": existing, "last_landed_at": utcnow()},
                actor=actor,
                source=source,
            )
            landed.append(folder["id"])
        return landed

    def _matches(self, document_id: str, matches: Mapping[str, Any]) -> bool:
        """Does a document satisfy every rule of a folder?

        Each rule is asked as ``find(CONTENT, {path: value})`` rather than as a
        hand-written path walker, so a folder rule means exactly what ``?where=``
        means everywhere else in the product, nested paths included. The branch
        reached past the store facade for this, calling
        ``store.db.query_index`` directly because ``RecordStore`` does not
        forward it; scoping the same query to the content collection is both
        narrower than a global index lookup and expressible through the facade,
        so the reach-through is not needed. ``count`` has no such equivalent on
        the facade and is raised as a finding rather than worked around.
        """
        for path, expected in matches.items():
            candidates = self._find(CONTENT, {str(path): expected}, limit=1000)
            if document_id not in {record["id"] for record in candidates}:
                return False
        return True

    def list_folders(self, *, room_id: str | None = None) -> dict[str, Any]:
        folders = self.store.list(FOLDER, room_id=room_id, limit=1000, order_by="updated_at", descending=False)
        return {"count": len(folders), "entries": folders}

    # -- subscriptions -------------------------------------------------------- #

    def create_subscription(
        self,
        data: Mapping[str, Any],
        *,
        actor: str | None = None,
        room_id: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Register a subscriber who should hear about publishes in a room."""
        subscriber = str(data.get("subscriber") or "").strip()
        if not subscriber:
            raise PublishError("a subscription needs a subscriber")
        payload = dict(data)
        payload.update({"subscriber": subscriber, "active": data.get("active", True)})
        return self.store.create(SUBSCRIPTION, payload, room_id=room_id, actor=actor, source=source)

    def list_subscriptions(self, *, room_id: str | None = None) -> dict[str, Any]:
        records = self.store.list(SUBSCRIPTION, room_id=room_id, limit=1000, order_by="updated_at", descending=False)
        return {"count": len(records), "entries": records}


# --------------------------------------------------------------------------- #
# Derived views
# --------------------------------------------------------------------------- #


def _current_step(steps: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """The step awaiting a decision: the earliest one still ``Pending``.

    Inference 3: steps are strictly ordered, so "the current step" is a
    derivation rather than a stored flag that could drift.
    """
    for step in _sort_steps(steps):
        if str(step.get("status")) == PENDING:
            return dict(step)
    return None


def _actor_may_act(step: Mapping[str, Any], actor: str | None) -> bool:
    """Is this actor somebody the step is assigned to?

    A step with neither ``assignedTo`` nor ``approvers`` is open, matching the
    research's ``isDefault`` notion of an unassigned default step.
    """
    assigned = step.get("assigned_to")
    approvers = [str(a) for a in _as_list(step.get("approvers"))]
    if assigned in (None, "") and not approvers:
        return True
    if assigned not in (None, "") and str(assigned) == str(actor):
        return True
    return str(actor) in approvers


def view_workflow(record: Mapping[str, Any]) -> dict[str, Any]:
    """Add derived queue state to a workflow without touching what is stored.

    ``data`` is returned exactly as persisted; everything computed lives under
    ``derived`` so a client can never mistake a computation for stored state.
    """
    steps = _sort_steps(record.get("data", {}).get("steps") or [])
    current = _current_step(steps)
    current_key = str(current.get("key")) if current else None
    views = [
        {
            "key": str(step.get("key")),
            "label": step.get("label"),
            "order": step.get("order"),
            "status": step.get("status"),
            "assigned_to": step.get("assigned_to"),
            "approvers": step.get("approvers") or [],
            "watchers": step.get("watchers") or [],
            "approve_button_label": step.get("approve_button_label") or "Approve",
            "reject_button_label": step.get("reject_button_label") or "Reject",
            "decided_by": step.get("decided_by"),
            "decided_at": step.get("decided_at"),
            # Only the step actually awaiting a decision is actionable, so a
            # client can render the rest of the queue as read-only.
            "actionable": current_key is not None and str(step.get("key")) == current_key,
        }
        for step in steps
    ]
    data = record.get("data", {})
    return {
        **record,
        "derived": {
            "current_step_key": current.get("key") if current else None,
            "current_step_label": current.get("label") if current else None,
            "total_steps": len(steps),
            "approved_steps": sum(1 for s in steps if str(s.get("status")) == APPROVED),
            "terminal": str(data.get("status")) in WORKFLOW_TERMINAL,
            "steps": views,
        },
    }
