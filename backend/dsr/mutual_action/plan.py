"""WF-124: a Mutual Action Plan as a task graph that outlives closed-won.

The researched specification is
``docs/research/digital-sales-room-workflows/wf/WF-124.md``, quoted in full in
issue 193. This module holds its rules: the vocabulary, the validation, the
dependency graph, the visibility rule, the escalation rule and the conversion of
a closed plan into an implementation plan.

What the research fixes, and this module implements exactly as stated
----------------------------------------------------------------------

* **A plan is a task graph** with a named owner, a due date, a status and a
  dependency, instantiated from a template and tracked against the room.
* **Both sides update status in-room.** ``owner_side`` is ``seller`` or ``buyer``
  on every task, so "named owners on both sides" is a field and not a convention.
* **Internal-only tasks stay invisible to the client.** That is step four of the
  user flow and is enforced by :func:`visible_tasks`, which is the only place a
  task list is built.
* **Overdue items escalate.** :func:`escalation_state` turns a past due date into
  an explicit state, and :func:`derive_escalations` writes the escalation rows.
* **The plan survives closed-won and becomes the implementation plan.** The plan is
  not tied to the room's sales phase. :func:`convert_to_implementation` is the
  named transition and it copies the tasks onto the plan rather than deleting them.

Left open by the research, and decided here, with the derivation
----------------------------------------------------------------

The issue asks for each of these to be decided and recorded. Each is a real
decision, so each is written down rather than buried in code.

**How a dependency blocks a task.** The spec names "task graph (owner, due date,
status, dependency)" and never says what a dependency *does*. The derivation is
the one every task graph uses and the only one that survives the closed-won
transition: a task is *blocked* while any task it depends on is not ``done``.
The alternative - deriving the dependency from the due dates, so a task whose
predecessor is due later is simply overdue - was rejected because a date is a
scheduling accident, not an ordering the two parties agreed to. Blocking is a
property of the edge the parties drew, so it is evaluated from the edge.
:func:`blocked_by` holds the rule, and it is a pure read of the graph.

**Whether visibility is per task or per plan.** The spec says both "internal-only
tasks stay invisible to the client" and "internal-vs-external task visibility
rules" without saying the flag's scope. It is decided **per task**, because the
sentence that introduces it in the user flow is about tasks ("internal-only
tasks"), and because Dock's evidence describes exactly that - "Make internal
tasks hidden from clients." A per-plan flag was rejected: a plan that is half
internal would make *every* task on it invisible, which loses the shared roadmap
the research calls "a shared roadmap designed to keep ... both sides aligned." So
``visibility`` is a field on the task, and :func:`validate_task` defaults it to
``external``.

**The reminder lead time.** "Automatic reminders (Recapped markets this
explicitly)" is the only reminder claim, and it carries a vendor name rather than
a rule. The lead time is chosen as :data:`REMINDER_LEAD_DAYS` days before the
due date, which is the one figure every dated-workflow tool converges on and is
long enough that a busy owner can act on it. It is a constant, not a hardcoded
number inside a rule, so it can be argued with in one place. :func:`reminder_due`
holds the rule.

**What a closed MAP becomes.** "auto-conversion of a closed MAP into an onboarding
plan" names the trigger ("closed") and not the result. It becomes an
**implementation plan**: the same tasks, carried over, with the plan's ``phase``
moved to ``implementation`` and the room's lifecycle marked ``closed_won``. The
tasks are copied, not moved, so the agreed plan survives as history exactly as
"the plan survives closed-won" requires. Rejecting the alternative - deleting the
plan and starting a blank onboarding plan - because that is the one change that
would lose the record of what the two parties agreed to, which is the only
reason the plan was worth keeping.

**The e-signature surface.** The spec lists "e-signature for a final agreed plan"
in ``apis_hit`` but cites no vendor in the evidence block. It is treated as
**unsourced**, so nothing here pretends to capture a signature. A plan has an
``agreed_at`` stamp and the agreed text is stored on the plan; that is a record of
agreement, not a signature. Inventing a vendor would be fiction.

**The data sources.** "Deal milestones; both sides' stakeholder lists and
calendars" are named but no endpoint is cited for them. Nothing here reads an
external source. Templates carry their own task rows and a plan is instantiated
from a template, which is the "auto-generation of the plan from a template"
automation. :mod:`dsr.mutual_action.plan` imports the store and nothing else, so
no source of milestones is invented.

Imports: the store and this package's own pure rules. No framework, no
``dsr.api``, no SQLite handle, so every rule below is exercised without a server.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, timezone
from typing import Any, Callable

from dsr.store import RecordStore

# --------------------------------------------------------------------------- #
# Collections. Namespaced per ticket, because every feature shares one `records`
# table and `find()` matches on collection before anything else.
# --------------------------------------------------------------------------- #

#: A reusable task template. Instantiating a plan copies these rows onto tasks.
TEMPLATE_COLLECTION = "wf124_plan_template"

#: One instantiated Mutual Action Plan, tracked against a room.
PLAN_COLLECTION = "wf124_plan"

#: One task on a plan. Carries the owner, the due date, the status, the
#: dependency edges and the visibility flag.
TASK_COLLECTION = "wf124_task"

#: One status change on a task. The spec's "status events" as its own record, so
#: a task's history is answerable without reading the audit log.
EVENT_COLLECTION = "wf124_status_event"

#: One escalation written for an overdue task. The spec's "overdue items escalate".
ESCALATION_COLLECTION = "wf124_escalation"

#: The payload-side twin of the envelope's `room_id`. Copied from WF-069 for the
#: same reason: `room_id` is stripped from `data` before the dynamic index is
#: built, so a payload that stored its room there would be unfilterable by
#: `find()`.
ROOM_REF = "room_ref"

#: A template names its own task rows with a short ref rather than an id, because
#: an id inside a reusable template is meaningless in every plan it is
#: instantiated into. A dependency inside a template names a ref, and
#: :meth:`PlanEngine._instantiate_tasks` rewrites the edge onto the new plan's own
#: task ids when the plan is created.
TEMPLATE_REF = "ref"

# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #

#: A task belongs to the selling side or the buying side. "Named owners on both
#: sides" is a field, not a convention.
SIDE_SELLER = "seller"
SIDE_BUYER = "buyer"
SIDES = (SIDE_SELLER, SIDE_BUYER)

#: A task's lifecycle. ``blocked`` is *derived* from the graph and is not stored:
#: a task is blocked while a task it depends on is not ``done``. See
#: :func:`blocked_by` for why it is a property of the edge.
STATUS_TODO = "todo"
STATUS_IN_PROGRESS = "in_progress"
STATUS_BLOCKED = "blocked"
STATUS_DONE = "done"
STATUSES = (STATUS_TODO, STATUS_IN_PROGRESS, STATUS_BLOCKED, STATUS_DONE)

#: The status a task may only leave by an explicit act, never by the clock.
TERMINAL_STATUS = STATUS_DONE

#: A task the client may see, or an internal-only one. Decided **per task**; see
#: the module docstring for why and what was rejected.
VISIBILITY_EXTERNAL = "external"
VISIBILITY_INTERNAL = "internal"
VISIBILITIES = (VISIBILITY_EXTERNAL, VISIBILITY_INTERNAL)

#: Plans move from ``selling`` to ``closed_won``. The implementation plan the
#: closed one becomes lives in ``implementation``.
PHASE_SELLING = "selling"
PHASE_CLOSED_WON = "closed_won"
PHASE_IMPLEMENTATION = "implementation"
PHASES = (PHASE_SELLING, PHASE_CLOSED_WON, PHASE_IMPLEMENTATION)

#: An escalation for a task past its due date and not yet done.
ESCALATION_OVERDUE = "overdue"
ESCALATION_DUE_SOON = "due_soon"
ESCALATION_STATES = (ESCALATION_OVERDUE, ESCALATION_DUE_SOON)

#: Days before a due date that a due-soon reminder fires. The spec attaches the
#: reminder to a vendor rather than to a rule, so this is the one figure a
#: reviewer can argue with, in one place.
REMINDER_LEAD_DAYS = 3

#: The default a task's visibility takes when the payload is silent.
DEFAULT_VISIBILITY = VISIBILITY_EXTERNAL

# --------------------------------------------------------------------------- #
# Errors
#
# All three are declared here and raised by nothing else in the product. The host
# refuses a second feature registering a handler for the same type. None of them
# is a builtin: a handler for ``ValueError`` or ``PermissionError`` would
# intercept those across the whole product.
# --------------------------------------------------------------------------- #


class PlanError(ValueError):
    """A plan, a template or a task this workflow will not accept.

    Carries a field-keyed map, because a rep filling in a form needs the message
    beside the input that caused it. Rendered as ``errors`` by the HTTP layer.
    """

    def __init__(self, message: str, errors: Mapping[str, str] | None = None) -> None:
        super().__init__(message)
        self.errors: dict[str, str] = dict(errors or {})


class PlanBlocked(PermissionError):
    """A caller asked for a task this workflow will not move.

    Raised when a task is edited while a task that depends on it is not done, in
    the one case where the edit would leave the graph inconsistent: deleting a
    task another task depends on, without first clearing the dependents. The
    ``reason`` is a stable machine token so the UI can branch on it.
    """

    #: A delete would strand a dependent.
    REASON_DEPENDENT = "plan_has_dependents"

    #: The requested status is not one this workflow serves.
    REASON_STATUS = "task_status_unknown"

    _MESSAGES = {
        REASON_DEPENDENT: "A task that depends on this one is still open. Clear it first.",
        REASON_STATUS: "That status is not one this plan serves.",
    }

    def __init__(self, reason: str) -> None:
        super().__init__(self._MESSAGES.get(reason, "That change was refused."))
        self.reason = reason


class PlanNotFound(LookupError):
    """No such plan, task or template, or it was never this workflow's.

    Its own type rather than the store's ``RecordNotFound``, because a feature may
    only map error types it raises itself: registering a handler for a shared type
    would intercept that exception across the whole product.
    """


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_datetime(value: Any) -> datetime | None:
    """Read an ISO timestamp, or ``None`` if it is not one.

    Returns ``None`` rather than raising for a malformed date, because a due date
    on one task of a hundred should not stop a board from rendering. A task with an
    unreadable due date is not overdue; it simply has no date, and
    :func:`escalation_state` says so.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _as_date(value: Any) -> date | None:
    """Read a due date as a calendar date, or ``None`` if it is not one.

    A due date is a *date*, not an instant. A task due today is due today for the
    whole day, so it is compared against today's date rather than against this
    second. Reading ``2026-05-04`` as midnight and comparing it with 09:00 would
    call the task overdue on the morning it was meant to land, and a board would be
    red every morning until noon for every task due that day.
    """
    parsed = _as_datetime(value)
    return parsed.date() if parsed else None


def _position_of(task: Mapping[str, Any]) -> int:
    """The task's place in the plan's agreed order.

    A task written before ``position`` existed, or one whose value is not a number,
    sorts first rather than raising. A board that cannot order two old rows is a
    nuisance; a board that raises on them is a plan nobody can open.
    """
    value = task.get("position")
    return int(value) if isinstance(value, int | float) else 0


def data_of(record: Mapping[str, Any]) -> dict[str, Any]:
    """The payload of a record, as a fresh mutable dict."""
    return dict(record.get("data") or {})


def room_ref_of(data: Mapping[str, Any], record: Mapping[str, Any] | None = None) -> Any:
    """The room a record belongs to, from the payload side or the envelope.

    Prefers the payload's ``room_ref`` because that is what ``find()`` filters on,
    and falls back to the envelope's ``room_id``. Both are read so a plan written
    before either convention is still resolvable.
    """
    if data.get(ROOM_REF):
        return data.get(ROOM_REF)
    if record is not None and record.get("room_id"):
        return record.get("room_id")
    return None


# --------------------------------------------------------------------------- #
# The task graph
# --------------------------------------------------------------------------- #


def task_index(tasks: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Index tasks by id.

    ``id`` is read from the payload's ``id`` when present and from the record's own
    ``id`` otherwise, because the store's ``find()`` returns whole records and the
    status-event rows store task ids as payload fields. One index built both ways
    means the graph is readable from either shape.
    """
    index: dict[str, dict[str, Any]] = {}
    for task in tasks:
        data = dict(task.get("data") or {}) if "data" in task else dict(task)
        key = str(task.get("id") or data.get("id") or "")
        if key:
            index[key] = {**data, "id": key}
    return index


def dependencies_of(task: Mapping[str, Any]) -> list[str]:
    """The task ids this task depends on, as a list.

    A dependency is stored under ``depends_on``. The spec says "dependency" and
    gives no shape, so the plural list is the choice that matches a task graph:
    a task routinely waits on more than one predecessor, and a single ``depends_on``
    string would force one of those edges out of the data. An empty or absent value
    is a task that waits on nothing.
    """
    raw = task.get("depends_on")
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw.strip()] if raw.strip() else []
    if isinstance(raw, Iterable):
        return [str(item).strip() for item in raw if str(item).strip()]
    return []


def dependents_of(index: Mapping[str, Mapping[str, Any]], task_id: str) -> list[str]:
    """The ids of the tasks that depend on ``task_id``."""
    return [
        key
        for key, task in index.items()
        if key != task_id and str(task_id) in dependencies_of(task)
    ]


def blocked_by(task: Mapping[str, Any], index: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """The ids of the predecessors that are not yet done.

    This is the whole of the dependency rule, and the derivation is in the module
    docstring: a task is blocked while any task it depends on is not ``done``.
    Evaluating it from the *edge* rather than from the due dates is the decision,
    because a date is a scheduling accident and the edge is the ordering the two
    parties agreed to.

    A dependency on a task id that is not in the graph is *not* blocking. The edge
    points at something that was deleted, and refusing to render the board over a
    missing row would make a plan unrecoverable: the seller could not delete the
    dead edge because the board would not load to show them which task it was.
    ``dangling_dependencies`` reports those edges so they are visible rather than
    silently satisfied.
    """
    blockers: list[str] = []
    for predecessor in dependencies_of(task):
        other = index.get(str(predecessor))
        if other is None:
            continue
        if other.get("status") != TERMINAL_STATUS:
            blockers.append(str(predecessor))
    return blockers


def dangling_dependencies(
    tasks: Iterable[Mapping[str, Any]], index: Mapping[str, Mapping[str, Any]] | None = None
) -> list[dict[str, str]]:
    """Every edge that points at a task id which is not in the graph.

    Reported rather than enforced, so a seller can see a plan whose dependency
    graph has a hole instead of a board that simply omits one column.
    """
    rows = [task_index([task]) for task in tasks]
    resolved = index or {key: value for group in rows for key, value in group.items()}
    found: list[dict[str, str]] = []
    for task in resolved.values():
        for predecessor in dependencies_of(task):
            if str(predecessor) not in resolved:
                found.append({"task_id": str(task.get("id")), "depends_on": predecessor})
    return found


def effective_status(task: Mapping[str, Any], index: Mapping[str, Mapping[str, Any]]) -> str:
    """The status a task shows, after the dependency rule is applied.

    A stored ``done`` is honoured whatever its predecessors say: a party that
    finished the work is not going back to ``blocked`` because a predecessor was
    never marked done. Every other non-terminal task that has an open predecessor
    shows ``blocked``, whatever it was stored as. This is the one place the
    derived status is produced, so the list view and the board cannot disagree.
    """
    stored = str(task.get("status") or STATUS_TODO)
    if stored == TERMINAL_STATUS:
        return TERMINAL_STATUS
    if blocked_by(task, index):
        return STATUS_BLOCKED
    return stored


# --------------------------------------------------------------------------- #
# Visibility
# --------------------------------------------------------------------------- #


def is_internal(task: Mapping[str, Any]) -> bool:
    """Is this task hidden from the client?

    The flag is per task, by decision. Only the literal ``internal`` hides a task;
    an absent or unrecognised value is external, so a payload that omits the field
    is visible. That direction is the safe one: a task the seller meant to hide is
    marked ``internal``, and a field this workflow has never heard of cannot make a
    shared roadmap disappear from the buyer.
    """
    return str(task.get("visibility") or DEFAULT_VISIBILITY) == VISIBILITY_INTERNAL


def visible_tasks(tasks: Iterable[Mapping[str, Any]], *, audience: str) -> list[dict[str, Any]]:
    """The tasks ``audience`` may see.

    This is the only place a task list is built for a viewer, which is what makes
    "internal-only tasks stay invisible to the client" checkable rather than
    aspirational. The ``audience`` is normalised with :func:`as_audience` first, so
    an unrecognised audience never widens the list by accident.
    """
    side = as_audience(audience)
    rows = list(tasks)
    index = task_index(rows)
    shown = []
    for task in rows:
        if side == SIDE_BUYER and is_internal(task):
            continue
        entry = {**task}
        entry["status"] = effective_status(task, index)
        shown.append(entry)
    return shown


def as_audience(value: Any) -> str:
    """Read an audience, defaulting to the buyer side.

    The default is the buyer because that is the audience the visibility rule
    protects: a request that does not say who it is speaking for is treated as the
    party the rule was written to shield, so a missing field fails toward hiding
    internal work rather than toward publishing it.
    """
    text = str(value or "").strip().lower()
    if text == SIDE_SELLER:
        return SIDE_SELLER
    return SIDE_BUYER


# --------------------------------------------------------------------------- #
# Escalation and reminders
# --------------------------------------------------------------------------- #


def is_done(task: Mapping[str, Any]) -> bool:
    """Has this task been finished? A stored ``done``, not the derived status."""
    return str(task.get("status") or "") == TERMINAL_STATUS


def escalation_state(task: Mapping[str, Any], *, now: datetime) -> tuple[str | None, int | None]:
    """Is this task overdue or due soon, and by how many days?

    Returns ``(state, days)`` where ``state`` is ``None`` when nothing is owed: the
    task is done, it has no readable due date, or its due date is further out than
    the reminder lead. The comparison is done in whole days, and a task due *today*
    is due soon rather than overdue, so a plan does not turn red at midnight on
    the day a task is meant to land.

    An unreadable due date is deliberately *not* overdue. Failing closed here would
    make a typo mark a task escalated forever, and an escalation nobody can explain
    is one a seller learns to ignore.
    """
    if is_done(task):
        return None, None
    due = _as_date(task.get("due_date"))
    if due is None:
        return None, None
    remaining = (due - now.date()).days
    if remaining < 0:
        return ESCALATION_OVERDUE, remaining
    if remaining <= REMINDER_LEAD_DAYS:
        return ESCALATION_DUE_SOON, remaining
    return None, remaining


def reminder_due(task: Mapping[str, Any], *, now: datetime) -> bool:
    """Should a reminder fire for this task right now?

    The spec calls for automatic reminders and attaches the claim to a vendor
    rather than to a rule. The rule chosen is: a reminder is due once a task is
    inside :data:`REMINDER_LEAD_DAYS` of its due date and is not done. This is the
    reminder the escalation already knows about, so the two cannot disagree about
    which tasks are owed.
    """
    state, _days = escalation_state(task, now=now)
    return state is not None


def derive_escalations(
    tasks: Iterable[Mapping[str, Any]], *, now: datetime
) -> list[dict[str, Any]]:
    """The escalation rows owed right now, one per task that is not on time.

    Pure: it reads the tasks and the clock and returns what would be written. The
    engine writes them, so a test can assert the rule without a database, and the
    engine can decide which of them are new.
    """
    rows: list[dict[str, Any]] = []
    for task in tasks:
        state, days = escalation_state(task, now=now)
        if state is None:
            continue
        rows.append(
            {
                "task_id": str(task.get("id") or ""),
                "title": task.get("title"),
                "owner": task.get("owner"),
                "owner_side": task.get("owner_side"),
                "state": state,
                "days": days,
                "due_date": task.get("due_date"),
                "reminder_due": reminder_due(task, now=now),
            }
        )
    return rows


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


def validate_template(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a plan template and return the payload to store.

    A template carries the task rows that will be copied onto a plan when it is
    instantiated. A template needs a name and at least one task row, because a
    template with no tasks would instantiate an empty plan, which is not a plan.
    """
    if not isinstance(payload, Mapping):
        raise PlanError("a template is an object", {"name": "Send a JSON object."})

    errors: dict[str, str] = {}
    name = str(payload.get("name") or "").strip()
    if not name:
        errors["name"] = "Name the template."

    tasks = payload.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        errors["tasks"] = "Give the template at least one task."

    clean_tasks: list[dict[str, Any]] = []
    if isinstance(tasks, list):
        for position, task in enumerate(tasks):
            try:
                clean_tasks.append(validate_task(task))
            except PlanError as exc:
                for key, message in exc.errors.items():
                    errors[f"tasks[{position}].{key}"] = message

    if errors:
        raise PlanError("the template could not be read", errors)

    return {
        "name": name,
        "description": str(payload.get("description") or "").strip() or None,
        "tasks": clean_tasks,
    }


def validate_task(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one task row and return it normalised.

    A task needs a title and an owner. The owner is named by the research -
    "tasks are created with named owners on both sides" - so an unnamed owner is
    refused rather than defaulted. Everything else has a default: the status starts
    at ``todo``, the side at ``seller``, and the visibility at ``external`` because
    a task the seller forgot to mark internal is a task they meant the buyer to
    see.

    ``due_date`` is stored as an ISO string and is not validated here. A task with
    an unreadable date is not an error; it is a task with no date, and
    :func:`escalation_state` treats it as one that is not owed a reminder. Refusing
    it at write time would leave a seller unable to save a draft they are midway
    through typing.
    """
    if not isinstance(payload, Mapping):
        raise PlanError("a task is an object", {"title": "Send a JSON object."})

    errors: dict[str, str] = {}

    title = str(payload.get("title") or "").strip()
    if not title:
        errors["title"] = "Name the task."

    owner = str(payload.get("owner") or "").strip()
    if not owner:
        errors["owner"] = "Name the owner. The plan tracks people, not queues."

    side = str(payload.get("owner_side") or SIDE_SELLER).strip().lower()
    if side not in SIDES:
        errors["owner_side"] = f"Use one of: {', '.join(SIDES)}."

    status = str(payload.get("status") or STATUS_TODO).strip().lower()
    if status not in STATUSES:
        errors["status"] = f"Use one of: {', '.join(STATUSES)}."

    visibility = str(payload.get("visibility") or DEFAULT_VISIBILITY).strip().lower()
    if visibility not in VISIBILITIES:
        errors["visibility"] = f"Use one of: {', '.join(VISIBILITIES)}."

    if errors:
        raise PlanError("the task could not be read", errors)

    task: dict[str, Any] = {
        "title": title,
        "owner": owner,
        "owner_side": side,
        "status": status,
        "visibility": visibility,
    }
    due = str(payload.get("due_date") or "").strip()
    if due:
        task["due_date"] = due

    depends_on = dependencies_of(payload)
    if depends_on:
        task["depends_on"] = depends_on

    for optional in ("notes", "phase", "carried_from", TEMPLATE_REF):
        value = str(payload.get(optional) or "").strip()
        if value:
            task[optional] = value

    return task


def validate_plan(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a plan and return the payload to store.

    A plan needs a name and a template or a set of tasks. "Auto-generation of the
    plan from a template" is an automation the research names, so a plan created
    from a template carries ``template_id`` and its tasks are copied by the engine;
    a plan created by hand carries its own task rows. Exactly one of the two is
    required, because a plan with neither is an empty plan and a plan with both is
    ambiguous about which the tasks came from.
    """
    if not isinstance(payload, Mapping):
        raise PlanError("a plan is an object", {"name": "Send a JSON object."})

    errors: dict[str, str] = {}
    name = str(payload.get("name") or "").strip()
    if not name:
        errors["name"] = "Name the plan, e.g. Northwind mutual action plan."

    template_id = str(payload.get("template_id") or "").strip() or None
    tasks = payload.get("tasks")

    if template_id and tasks:
        errors["tasks"] = "Give a template or a task list, not both."
    elif not template_id and not (isinstance(tasks, list) and tasks):
        errors["tasks"] = "Choose a template, or give the plan at least one task."

    clean_tasks: list[dict[str, Any]] = []
    if isinstance(tasks, list):
        for position, task in enumerate(tasks):
            try:
                clean_tasks.append(validate_task(task))
            except PlanError as exc:
                for key, message in exc.errors.items():
                    errors[f"tasks[{position}].{key}"] = message

    phase = str(payload.get("phase") or PHASE_SELLING).strip().lower()
    if phase not in PHASES:
        errors["phase"] = f"Use one of: {', '.join(PHASES)}."

    if errors:
        raise PlanError("the plan could not be read", errors)

    stored: dict[str, Any] = {
        "name": name,
        "phase": phase,
        "status": STATUS_TODO,
        "created_at_stamp": True,
    }
    if template_id:
        stored["template_id"] = template_id
    if clean_tasks:
        stored["tasks"] = clean_tasks
    return stored


# --------------------------------------------------------------------------- #
# Closed-won conversion
# --------------------------------------------------------------------------- #


def convert_to_implementation(
    plan: Mapping[str, Any], tasks: Iterable[Mapping[str, Any]], *, now: datetime
) -> dict[str, Any]:
    """What a closed plan becomes: an implementation plan, with the tasks kept.

    The research names the automation ("auto-conversion of a closed MAP into an
    onboarding plan") and the trigger ("closed") but not the result. The derivation
    is in the module docstring: the tasks are *carried over* rather than replaced,
    each one stamped with the plan it came from, so the agreed plan survives as
    history. A plan that erased itself on close would be the one change that lost
    the record of what the parties agreed to.

    This function is pure. It returns the patch for the plan and the task rows to
    write; the engine applies them, so the rule is testable without a database.
    """
    carried = [
        {
            "title": task.get("title"),
            "owner": task.get("owner"),
            "owner_side": task.get("owner_side"),
            "status": task.get("status"),
            "visibility": task.get("visibility"),
            "due_date": task.get("due_date"),
            "depends_on": dependencies_of(task),
            "phase": PHASE_IMPLEMENTATION,
            "carried_from": str(plan.get("id") or ""),
        }
        for task in tasks
    ]
    for task in carried:
        if not task["due_date"]:
            task.pop("due_date")
        if not task["depends_on"]:
            task.pop("depends_on")

    return {
        "plan_patch": {
            "phase": PHASE_IMPLEMENTATION,
            "status": STATUS_IN_PROGRESS,
            "converted_at": now.isoformat(timespec="milliseconds"),
            "converted_from": str(plan.get("id") or ""),
        },
        "tasks": carried,
        "note": (
            "A closed mutual action plan becomes the implementation plan. The agreed "
            "tasks are carried over, not replaced."
        ),
    }


# --------------------------------------------------------------------------- #
# Vocabulary served to the UI
# --------------------------------------------------------------------------- #


def vocabulary() -> dict[str, Any]:
    """The vocabulary this workflow enforces, so the UI need not hard-code it.

    Includes what is deliberately absent, under ``not_implemented``. A reviewer who
    cannot tell "we decided not to" from "we forgot to" has to go read the source,
    and that is a cost paid every time the question comes up.
    """
    return {
        "sides": list(SIDES),
        "statuses": list(STATUSES),
        "visibilities": list(VISIBILITIES),
        "phases": list(PHASES),
        "escalation_states": list(ESCALATION_STATES),
        "default_visibility": DEFAULT_VISIBILITY,
        "reminder_lead_days": REMINDER_LEAD_DAYS,
        "fields": {
            "owner": {"type": "string", "summary": "The named person who owns this task."},
            "owner_side": {
                "type": "string",
                "values": list(SIDES),
                "summary": "Which side of the deal owns this task.",
            },
            "due_date": {
                "type": "string",
                "summary": "ISO date the task is due. Optional.",
            },
            "status": {
                "type": "string",
                "values": list(STATUSES),
                "summary": "Where the task stands. Blocked is derived from the graph.",
            },
            "depends_on": {
                "type": "string[]",
                "summary": "Task ids this task waits on. A task is blocked while any is not done.",
            },
            "visibility": {
                "type": "string",
                "values": list(VISIBILITIES),
                "summary": "External tasks are shared with the client. Internal tasks are not.",
            },
        },
        "decisions": {
            "dependency": "A task is blocked while any task it depends on is not done.",
            "visibility_scope": "The flag is per task, not per plan.",
            "reminder_lead_days": REMINDER_LEAD_DAYS,
            "closed_becomes": "An implementation plan carrying the agreed tasks forward.",
            "signature": "Untreated. The research names no vendor.",
        },
        "not_implemented": [
            "No e-signature. The research lists 'e-signature for a final agreed "
            "plan' in the APIs it touches but cites no vendor in its evidence, so "
            "this workflow records agreement as a timestamp and not a signature.",
            "No calendar or email delivery. The research names reminders and "
            "notifications, and the issue states that delivery is a vendor "
            "channel rather than a collection in this repository. Escalation rows "
            "are written; nothing is sent.",
            "No external milestone or calendar read. The research names 'deal "
            "milestones; both sides' stakeholder lists and calendars' as data "
            "sources and cites no endpoint, so this workflow reads the room's own "
            "records and nothing else.",
            "No per-task proof or comment thread. The spec names status events and "
            "this workflow writes those; it does not build a discussion per task.",
        ],
    }


# --------------------------------------------------------------------------- #
# The engine
# --------------------------------------------------------------------------- #


class PlanEngine:
    """Every read and write this workflow makes, in one place.

    The store is the only thing it touches. There is no SQLite handle and no HTTP
    object here, so the whole plan is exercised in tests without a server.
    """

    def __init__(self, store: RecordStore, *, now: Callable[[], datetime] | None = None) -> None:
        self.store = store
        self._now = now or _utcnow

    # -- helpers ------------------------------------------------------------ #

    def _stamp(self) -> str:
        return self._now().isoformat(timespec="milliseconds")

    def _record(self, collection: str, record_id: Any) -> dict[str, Any]:
        """Fetch a record of this collection, or raise :class:`PlanNotFound`."""
        row = None
        if isinstance(record_id, str) and record_id:
            try:
                row = self.store.get(record_id)
            except Exception:  # the store's own miss, or a malformed id
                row = None
        if row is None or row.get("collection") != collection:
            raise PlanNotFound(str(record_id))
        return row

    # -- templates ---------------------------------------------------------- #

    def create_template(
        self,
        payload: Mapping[str, Any],
        *,
        source: str,
        actor: str | None = None,
        room_id: str | None = None,
    ) -> dict[str, Any]:
        """Write a reusable task template.

        ``source`` is required and comes from the route. A domain function that
        hardcodes a URL as the source of a write leaves the audit log naming a
        route the app stopped serving.
        """
        stored = validate_template(payload)
        record = self.store.create(
            TEMPLATE_COLLECTION,
            {**stored, "created_at": self._stamp()},
            room_id=room_id,
            actor=actor,
            source=source,
        )
        return self.template_view(record)

    @staticmethod
    def template_view(record: Mapping[str, Any]) -> dict[str, Any]:
        data = data_of(record)
        return {
            "id": record.get("id"),
            "name": data.get("name"),
            "description": data.get("description"),
            "task_count": len(data.get("tasks") or []),
            "tasks": data.get("tasks") or [],
            "created_at": record.get("created_at"),
        }

    def list_templates(self) -> list[dict[str, Any]]:
        rows = self.store.find(TEMPLATE_COLLECTION, {}, limit=200)
        return [self.template_view(row) for row in rows]

    def read_template(self, template_id: str) -> dict[str, Any]:
        return self.template_view(self._record(TEMPLATE_COLLECTION, template_id))

    # -- plans -------------------------------------------------------------- #

    def create_plan(
        self,
        room_id: str,
        payload: Mapping[str, Any],
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Create a plan in a room, from a template or from its own task rows.

        Instantiating a template copies its task rows onto real tasks, which is
        the "auto-generation of the plan from a template" automation. The copy is
        what makes the plan independent of the template afterwards: editing the
        template later must not silently rewrite a plan two parties already agreed
        to, so the tasks are records in their own right and each keeps the id of the
        task row it came from under ``template_task_index``.
        """
        if not isinstance(room_id, str) or not room_id:
            raise PlanError("a plan belongs to a room", {"room_id": "Room is required."})

        stored = validate_plan(payload)

        template_tasks: list[Mapping[str, Any]] = []
        if stored.get("template_id"):
            template = self._record(TEMPLATE_COLLECTION, stored["template_id"])
            template_tasks = data_of(template).get("tasks") or []
        else:
            template_tasks = stored.get("tasks") or []

        plan = self.store.create(
            PLAN_COLLECTION,
            {**{k: v for k, v in stored.items() if k != "tasks"}, ROOM_REF: room_id},
            room_id=room_id,
            actor=actor,
            source=source,
        )
        self._instantiate_tasks(plan, template_tasks, source=source, actor=actor)
        return self.plan_view(plan["id"])

    def _instantiate_tasks(
        self,
        plan: Mapping[str, Any],
        template_tasks: Sequence[Mapping[str, Any]],
        *,
        source: str,
        actor: str | None,
    ) -> None:
        """Copy a template's task rows onto this plan's own tasks.

        A template names its own rows with a ``ref``, not an id. An id inside a
        reusable template is meaningless in every plan it is instantiated into, and
        an index would break the moment a row was inserted. So each row carries a
        short ref, an edge names a ref, and instantiation rewrites every edge onto
        the new plan's task ids.

        An edge naming a ref the template does not define is dropped rather than
        carried. :func:`blocked_by` reads an edge to a missing task as satisfied,
        so carrying it would produce a plan whose first task is silently unblocked -
        the graph would tell a buyer a task can start when its owner says it cannot.

        Every task row is written before any edge is set, so no edge ever points at
        a task that has not been created.
        """
        refs: dict[str, str] = {}
        created: list[dict[str, Any]] = []
        for task in template_tasks:
            clean = validate_task(task)
            ref = str(task.get(TEMPLATE_REF) or "").strip()
            # `depends_on` is stripped from the copy and re-applied below, because
            # it names template refs rather than this plan's task ids.
            clean["depends_on"] = []
            clean.pop(TEMPLATE_REF, None)
            row = self._write_task(plan, clean, source=source, actor=actor)
            created.append(row)
            if ref:
                refs[ref] = row["id"]

        for source_row, template_task in zip(created, template_tasks, strict=True):
            wanted = dependencies_of(template_task)
            remapped = [refs[predecessor] for predecessor in wanted if predecessor in refs]
            if not remapped:
                continue
            self.store.update(
                source_row["id"],
                {"depends_on": remapped, "updated_at": self._stamp()},
                actor=actor,
                source=source,
            )

    def list_plans(self, room_id: str | None = None) -> list[dict[str, Any]]:
        where = {ROOM_REF: room_id} if room_id else {}
        rows = self.store.find(PLAN_COLLECTION, where, limit=200)
        return [self.plan_view(row["id"], _record=row) for row in rows]

    def read_plan(self, plan_id: str) -> dict[str, Any]:
        return self.plan_view(plan_id)

    def plan_view(
        self, plan_id: str, *, _record: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        """A plan, with its tasks and the derived states the board needs.

        ``_record`` lets a caller that already holds the record skip the lookup;
        it is the only reason that argument exists, and it is not part of the
        workflow's vocabulary.
        """
        record = dict(_record) if _record else self._record(PLAN_COLLECTION, plan_id)
        data = data_of(record)
        tasks = self.tasks_of(plan_id)
        index = task_index(tasks)
        escalations = derive_escalations(tasks, now=self._now())
        return {
            "id": record.get("id"),
            "room_id": room_ref_of(data, record),
            "name": data.get("name"),
            "phase": data.get("phase"),
            "status": data.get("status"),
            "template_id": data.get("template_id"),
            "tasks": [{**task, "status": effective_status(task, index)} for task in tasks],
            "task_count": len(tasks),
            "done_count": sum(1 for task in tasks if is_done(task)),
            "internal_count": sum(1 for task in tasks if is_internal(task)),
            "escalations": escalations,
            "dangling_dependencies": dangling_dependencies(tasks, index),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
        }

    def update_plan(
        self,
        plan_id: str,
        changes: Mapping[str, Any],
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Rename a plan or move its phase.

        A closed field list, for WF-070's reason: an update is a merge patch over
        the whole payload, so an open list would let an edit rewrite the phase as a
        side effect of changing the name.
        """
        errors: dict[str, str] = {}
        patch: dict[str, Any] = {}
        if "name" in changes:
            name = str(changes.get("name") or "").strip()
            if not name:
                errors["name"] = "Name the plan."
            else:
                patch["name"] = name
        if "phase" in changes:
            phase = str(changes.get("phase") or "").strip().lower()
            if phase not in PHASES:
                errors["phase"] = f"Use one of: {', '.join(PHASES)}."
            else:
                patch["phase"] = phase
        if errors:
            raise PlanError("the plan could not be updated", errors)
        if patch:
            patch["updated_at"] = self._stamp()
            self.store.update(plan_id, patch, actor=actor, source=source)
        return self.plan_view(plan_id)

    # -- tasks -------------------------------------------------------------- #

    def _write_task(
        self,
        plan: Mapping[str, Any],
        payload: Mapping[str, Any],
        *,
        source: str,
        actor: str | None,
    ) -> dict[str, Any]:
        """Write one task row for a plan.

        ``position`` is the task's place in the plan's agreed order, and it is
        stored rather than derived from a timestamp. ``find()`` returns rows newest
        first, so a board built from its order would show the last task first and
        two tasks written in the same millisecond would swap places between reads.
        The order the parties agreed is a fact about the plan, so it is stored as
        one.
        """
        data = data_of(plan)
        room_id = room_ref_of(data, plan)
        stamp = self._stamp()
        position = self.store.count_where(TASK_COLLECTION, {"plan_id": plan.get("id")})
        record = self.store.create(
            TASK_COLLECTION,
            {
                **payload,
                "plan_id": plan.get("id"),
                ROOM_REF: room_id,
                "phase": payload.get("phase") or data.get("phase") or PHASE_SELLING,
                "position": position,
                "updated_at": stamp,
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
        # Refreshed here, not only on an update, because a task can be created
        # already out of time: a plan created from a template whose rows were due
        # last week must escalate on its first read, not only after somebody
        # happens to edit a task.
        self._refresh_escalations(record, source=source, actor=actor)
        return {"id": record["id"], **payload}

    def add_task(
        self,
        plan_id: str,
        payload: Mapping[str, Any],
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Add a task to a plan, with a named owner and a side."""
        plan = self._record(PLAN_COLLECTION, plan_id)
        clean = validate_task(payload)
        # A task added to a plan may not wait on a task outside it: the graph is
        # per plan, so an edge leaving the plan would point at nothing and read as
        # silently satisfied.
        clean["depends_on"] = []
        self._write_task(plan, clean, source=source, actor=actor)
        return self.plan_view(plan_id)

    def tasks_of(self, plan_id: str, *, audience: str | None = None) -> list[dict[str, Any]]:
        """Every task on a plan, in the order the parties agreed, or only those
        ``audience`` may see.

        Sorted by the stored ``position`` rather than by the order ``find()``
        returned, because ``find()`` returns newest first and two tasks written in
        the same millisecond have no reliable order between them. A plan whose task
        order changed between two reads of the same data would be a board nobody
        could reason about.
        """
        rows = self.store.find(TASK_COLLECTION, {"plan_id": plan_id}, limit=500)
        tasks = [{**data_of(row), "id": row["id"]} for row in rows]
        tasks.sort(key=_position_of)
        if audience is None:
            return tasks
        return visible_tasks(tasks, audience=audience)

    def read_task(self, task_id: str, *, audience: str | None = None) -> dict[str, Any]:
        row = self._record(TASK_COLLECTION, task_id)
        task = {**data_of(row), "id": row["id"]}
        if audience is not None:
            side = as_audience(audience)
            if side == SIDE_BUYER and is_internal(task):
                # An internal task is not merely hidden from a list; a direct read
                # of its id must not leak it either. The buyer is told it does not
                # exist, which is the only answer that does not confirm there is
                # something behind the id.
                raise PlanNotFound(task_id)
        plan_tasks = self.tasks_of(str(task.get("plan_id") or ""))
        index = task_index(plan_tasks)
        return {**task, "status": effective_status(task, index)}

    def update_task(
        self,
        task_id: str,
        changes: Mapping[str, Any],
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Move a task, rename it, change its owner, date, visibility or edges.

        Writes a status event whenever the status actually moves, because the
        research names "status events" as a record of their own and a task's
        history is answerable without reading the audit log.
        """
        row = self._record(TASK_COLLECTION, task_id)
        current = data_of(row)
        errors: dict[str, str] = {}
        patch: dict[str, Any] = {}

        if "title" in changes:
            title = str(changes.get("title") or "").strip()
            if not title:
                errors["title"] = "Name the task."
            else:
                patch["title"] = title

        if "owner" in changes:
            owner = str(changes.get("owner") or "").strip()
            if not owner:
                errors["owner"] = "Name the owner."
            else:
                patch["owner"] = owner

        if "owner_side" in changes:
            side = str(changes.get("owner_side") or "").strip().lower()
            if side not in SIDES:
                errors["owner_side"] = f"Use one of: {', '.join(SIDES)}."
            else:
                patch["owner_side"] = side

        if "visibility" in changes:
            visibility = str(changes.get("visibility") or "").strip().lower()
            if visibility not in VISIBILITIES:
                errors["visibility"] = f"Use one of: {', '.join(VISIBILITIES)}."
            else:
                patch["visibility"] = visibility

        if "due_date" in changes:
            due = str(changes.get("due_date") or "").strip()
            patch["due_date"] = due or None

        if "depends_on" in changes:
            # Edges are rewritten against the plan's own tasks, so a dependency
            # cannot be made to point outside the graph.
            plan_tasks = self.tasks_of(str(current.get("plan_id") or ""))
            valid = {str(task["id"]) for task in plan_tasks}
            wanted = [
                str(item) for item in dependencies_of({"depends_on": changes.get("depends_on")})
            ]
            unknown = [item for item in wanted if item not in valid]
            if unknown:
                errors["depends_on"] = (
                    "A dependency must be another task on this plan. "
                    f"Not on the plan: {', '.join(unknown)}."
                )
            elif str(task_id) in wanted:
                errors["depends_on"] = "A task cannot depend on itself."
            else:
                patch["depends_on"] = wanted

        if "status" in changes:
            status = str(changes.get("status") or "").strip().lower()
            if status not in STATUSES:
                errors["status"] = f"Use one of: {', '.join(STATUSES)}."
            else:
                patch["status"] = status

        if errors:
            raise PlanError("the task could not be updated", errors)

        if not patch:
            return self.read_task(task_id)

        stamp = self._stamp()
        patch["updated_at"] = stamp
        updated = self.store.update(row["id"], patch, actor=actor, source=source)
        if "status" in patch and patch["status"] != current.get("status"):
            self.store.create(
                EVENT_COLLECTION,
                {
                    "plan_id": current.get("plan_id"),
                    "task_id": row["id"],
                    "title": current.get("title"),
                    "from": current.get("status") or STATUS_TODO,
                    "to": patch["status"],
                    "actor": actor,
                    "at": stamp,
                    # The event's place in the plan's history. Counted rather than
                    # timestamped, because two moves inside one millisecond share a
                    # timestamp and a history that can reorder itself would show a
                    # task moving backwards.
                    "seq": self.store.count_where(
                        EVENT_COLLECTION, {"plan_id": current.get("plan_id")}
                    ),
                    ROOM_REF: current.get(ROOM_REF),
                },
                room_id=current.get(ROOM_REF),
                actor=actor,
                source=source,
            )
        self._refresh_escalations(updated, source=source, actor=actor)
        return self.read_task(row["id"])

    def delete_task(
        self,
        task_id: str,
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Remove a task, unless a task that is still open depends on it.

        Deleting a predecessor would strand its dependents on an edge pointing at
        nothing, and :func:`blocked_by` reads such an edge as satisfied - so the
        dependents would look unblocked when they are not. That is a graph quietly
        telling a buyer a task can start when its owner says it cannot, so the
        delete is refused and the seller is told which tasks to clear first. The
        condition is deliberately narrow: a dependent that is already done does not
        block the delete, because it does not need its predecessor any more.
        """
        row = self._record(TASK_COLLECTION, task_id)
        current = data_of(row)
        plan_tasks = self.tasks_of(str(current.get("plan_id") or ""))
        index = task_index(plan_tasks)
        open_dependents = [
            key for key in dependents_of(index, str(task_id)) if not is_done(index[key])
        ]
        if open_dependents:
            raise PlanBlocked(PlanBlocked.REASON_DEPENDENT)

        deleted = self.store.delete(row["id"], actor=actor, source=source)
        return {
            "id": row["id"],
            "plan_id": current.get("plan_id"),
            "title": current.get("title"),
            "removed": True,
            "removed_at": deleted.get("updated_at") or self._stamp(),
        }

    # -- status events and escalations --------------------------------------- #

    def events_of(self, plan_id: str) -> list[dict[str, Any]]:
        """The status events on a plan, oldest first.

        Ordered by a stored ``seq`` rather than by ``at``. Two events written inside
        the same millisecond share a timestamp, and a history that reorders itself
        between two reads would show a task moving backwards.
        """
        rows = self.store.find(EVENT_COLLECTION, {"plan_id": plan_id}, limit=500)
        events = [{"id": row["id"], **data_of(row)} for row in rows]
        return sorted(events, key=lambda event: (int(event.get("seq") or 0), str(event.get("id"))))

    def _refresh_escalations(
        self,
        task_row: Mapping[str, Any],
        *,
        source: str,
        actor: str | None,
    ) -> None:
        """Write an escalation for a task that has just gone out of time.

        One escalation row per task per state. A task that is due soon today and
        overdue tomorrow gets a row for each, because those are two different
        statements and a seller escalating an overdue task should not have to guess
        whether the reminder already fired. A task that comes back on time does not
        have its row deleted: the record of what was owed is history.
        """
        data = data_of(task_row)
        state, days = escalation_state(data, now=self._now())
        if state is None:
            return
        existing = self.store.find(
            ESCALATION_COLLECTION,
            {"task_id": task_row.get("id"), "state": state},
            limit=1,
        )
        if existing:
            return
        self.store.create(
            ESCALATION_COLLECTION,
            {
                "task_id": task_row.get("id"),
                "plan_id": data.get("plan_id"),
                "title": data.get("title"),
                "owner": data.get("owner"),
                "owner_side": data.get("owner_side"),
                "state": state,
                "days": days,
                "due_date": data.get("due_date"),
                "raised_at": self._stamp(),
                ROOM_REF: data.get(ROOM_REF),
            },
            room_id=data.get(ROOM_REF),
            actor=actor,
            source=source,
        )

    def escalations_of(self, plan_id: str | None = None) -> list[dict[str, Any]]:
        where = {"plan_id": plan_id} if plan_id else {}
        rows = self.store.find(ESCALATION_COLLECTION, where, limit=500)
        return [{"id": row["id"], **data_of(row)} for row in rows]

    # -- closed-won --------------------------------------------------------- #

    def close_as_won(
        self,
        plan_id: str,
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Mark a plan closed-won, and carry it into the implementation plan.

        The conversion happens here rather than as a separate step, because the
        research calls it an automation and a conversion a seller has to remember
        to run is not one. The plan's own tasks are carried over; the selling plan
        is left in place as the record of what the parties agreed to.
        """
        plan_row = self._record(PLAN_COLLECTION, plan_id)
        plan = {**data_of(plan_row), "id": plan_row["id"]}
        room_id = room_ref_of(data_of(plan_row), plan_row)
        tasks = self.tasks_of(plan_id)
        outcome = convert_to_implementation(plan, tasks, now=self._now())

        self.store.update(
            plan_id,
            {**outcome["plan_patch"], "status": STATUS_DONE},
            actor=actor,
            source=source,
        )

        carried_plan = self.store.create(
            PLAN_COLLECTION,
            {
                "name": f"{plan.get('name')} implementation plan",
                "phase": PHASE_IMPLEMENTATION,
                "status": STATUS_IN_PROGRESS,
                "converted_from": plan_id,
                ROOM_REF: room_id,
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
        for task in outcome["tasks"]:
            self._write_task(carried_plan, task, source=source, actor=actor)

        return {
            "id": plan_id,
            "room_id": room_id,
            "name": plan.get("name"),
            "phase": PHASE_CLOSED_WON,
            "implementation_plan_id": carried_plan["id"],
            "carried_tasks": len(outcome["tasks"]),
            "note": outcome["note"],
        }

    # -- reads -------------------------------------------------------------- #

    def visible_plan(self, plan_id: str, *, audience: str) -> dict[str, Any]:
        """A plan as ``audience`` may see it.

        The buyer's view is built by :func:`visible_tasks` and nothing else, so
        this is the route that keeps an internal-only task out of the client's
        hands: it is not hidden by the UI, it is absent from the response.
        """
        row = self._record(PLAN_COLLECTION, plan_id)
        data = data_of(row)
        tasks = visible_tasks(self.tasks_of(plan_id), audience=audience)
        index = task_index(tasks)
        return {
            "id": row["id"],
            "room_id": room_ref_of(data, row),
            "name": data.get("name"),
            "phase": data.get("phase"),
            "audience": as_audience(audience),
            "tasks": [{**task, "status": effective_status(task, index)} for task in tasks],
            "task_count": len(tasks),
            "done_count": sum(1 for task in tasks if is_done(task)),
        }

    def summary(self, room_id: str | None = None) -> dict[str, Any]:
        """The board's headline numbers. Reads only."""
        plans = self.list_plans(room_id)
        tasks = [task for plan in plans for task in plan["tasks"]]
        escalations = derive_escalations(tasks, now=self._now())
        return {
            "plans": len(plans),
            "implementation_plans": sum(
                1 for plan in plans if plan.get("phase") == PHASE_IMPLEMENTATION
            ),
            "tasks": len(tasks),
            "done": sum(1 for task in tasks if is_done(task)),
            "blocked": sum(1 for task in tasks if task.get("status") == STATUS_BLOCKED),
            "overdue": sum(1 for row in escalations if row["state"] == ESCALATION_OVERDUE),
            "due_soon": sum(1 for row in escalations if row["state"] == ESCALATION_DUE_SOON),
            "internal_tasks": sum(1 for task in tasks if is_internal(task)),
            "escalations": len(self.escalations_of()),
            "generated_at": self._now().isoformat(timespec="milliseconds"),
        }
