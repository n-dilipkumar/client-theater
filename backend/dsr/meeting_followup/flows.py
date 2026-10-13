"""The batch assignment split, and the ordered to-do queue.

The assignment endpoint is the researched programmatic seam:

    /v2/flows/prospects/assign - scope api:flows:write; body
    AssignFlowRequestV2{crmProspectsIds[], flowId, flowInstanceOwnerEmail}; up to
    200 prospects per request; response splits prospectsAssigned[]
    (flowInstanceId, flowInstanceStatus in Running|Pending|Paused, exclusive,
    workspaceId) vs prospectsNotAssigned[] (errorCode in
    InvalidArgument|InvalidState|UnexpectedError).

The sentence that matters most is: "Failures are returned per-prospect with an
error code rather than failing the batch." :func:`assign` therefore never raises
for a prospect. One malformed id in a batch of two hundred must not stop the other
one hundred and ninety-nine, because the caller's next action is to fix the one
and retry it, not to rebuild the batch.

The to-do queue is quoted as: "To-dos are grouped into a queue that shows your
position as you move through them. For example, 1/5 means you're working on the
first of five to-dos in that queue." :func:`queue_view` renders that position as
the researched ``"1/5"`` string rather than as two numbers, because the string is
what the product shows.
"""

from __future__ import annotations

import hashlib
from typing import Any, Iterable, Mapping

from dsr.meeting_followup import crm, instances, vocabulary as vocab
from dsr.meeting_followup.errors import (
    AssignBatchTooLarge,
    ContactWithoutParentAccount,
    FlowInvalid,
)

#: Derived: the workspace an instance belongs to when no workspace is recorded.
#: The response shape carries ``workspaceId``, and the research does not say where
#: its value comes from, so it is derived from the room.
DEFAULT_WORKSPACE_PREFIX = "ws"


def workspace_id(room_id: str) -> str:
    """A stable workspace id derived from a room id.

    Derived: the assignment response carries ``workspaceId`` and the evidence
    does not say how it is formed. A deterministic derivation keeps the same
    room on the same value between runs, which a random one would not.
    """
    digest = hashlib.sha256(str(room_id or "").encode("utf-8")).hexdigest()[:12]
    return f"{DEFAULT_WORKSPACE_PREFIX}_{digest}"


def new_flow_instance_id(flow_id: str, crm_id: str) -> str:
    """A stable flow instance id for one prospect in one flow.

    Derived: the response carries ``flowInstanceId`` and the evidence does not say
    how it is formed. Derived from the two inputs so that re-assigning the same
    prospect to the same flow yields the same id, which is what "exclusive" needs
    in order to mean anything.
    """
    digest = hashlib.sha256(f"{flow_id}:{crm_id}".encode()).hexdigest()[:16]
    return f"fi_{digest}"


def normalise_request(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one assignment request into a normalised dict.

    The 200-prospect cap is refused before anything is read, because a request
    over the cap is not a partial failure: it is a request the vendor does not
    accept at all.
    """
    raw_ids = payload.get("crmProspectsIds")
    if raw_ids is None:
        raw_ids = payload.get("crm_prospect_ids")
    if not isinstance(raw_ids, list):
        raise FlowInvalid(
            "crmProspectsIds must be a list",
            {"crmProspectsIds": "a list of CRM prospect ids"},
        )
    if not raw_ids:
        raise FlowInvalid(
            "crmProspectsIds must not be empty",
            {"crmProspectsIds": "at least one prospect"},
        )
    if len(raw_ids) > vocab.MAX_PROSPECTS_PER_REQUEST:
        raise AssignBatchTooLarge(
            f"{len(raw_ids)} prospects were sent; up to "
            f"{vocab.MAX_PROSPECTS_PER_REQUEST} can be assigned in a single request",
            {"crmProspectsIds": f"at most {vocab.MAX_PROSPECTS_PER_REQUEST}"},
        )

    flow_id = str(payload.get("flowId") or payload.get("flow_id") or "").strip()
    if not flow_id:
        raise FlowInvalid("flowId is required", {"flowId": "required"})

    owner_email = str(
        payload.get("flowInstanceOwnerEmail") or payload.get("owner_email") or ""
    ).strip()
    if not owner_email:
        raise FlowInvalid(
            "flowInstanceOwnerEmail is required: the assignee owns the flow to-dos "
            "and is the sender for any emails in the flow",
            {"flowInstanceOwnerEmail": "required"},
        )

    return {
        "crmProspectsIds": [str(item) for item in raw_ids],
        "flowId": flow_id,
        "flowInstanceOwnerEmail": owner_email,
    }


def assign(
    request: Mapping[str, Any],
    *,
    flow: Mapping[str, Any],
    owner: Mapping[str, Any] | None,
    prospects: Iterable[Mapping[str, Any]],
    room_id: str,
) -> dict[str, Any]:
    """Assign a batch, splitting the answer exactly as the vendor documents.

    ``prospects`` is every CRM record the caller can see. A requested id that is
    not among them becomes ``InvalidArgument`` for that prospect and nothing else,
    which is the researched per-prospect failure.

    Three researched gates are applied here, per prospect, and their outcomes map
    onto the researched error codes:

    * the assignee has no Engage licence -> the whole batch is ``InvalidState``,
      because the assignee is one property of the request and a request with no
      licensed owner enrols nobody;
    * a contact with no parent account -> that prospect alone is
      ``InvalidArgument``;
    * a personal flow -> the whole batch is ``InvalidState``.

    The distinction is deliberate. An assignee fault and a flow fault are
    properties of the request; a missing parent account is a property of one
    prospect, and refusing the other 199 because of it would be the batch failure
    the vendor's own documentation says does not happen.
    """
    if not isinstance(request.get("crmProspectsIds"), list):
        raise FlowInvalid("crmProspectsIds must be a list")

    wanted = [str(item) for item in request["crmProspectsIds"]]
    by_id = {str(item.get("crm_id")): item for item in prospects}
    space = workspace_id(room_id)

    # Request-level gates first. Both refuse the whole batch, and both do so
    # before any record is read.
    crm.assert_engaged(owner)
    crm.assert_not_personal(flow)

    assigned: list[dict[str, Any]] = []
    not_assigned: list[dict[str, Any]] = []

    for crm_id in wanted:
        record = by_id.get(crm_id)
        if record is None:
            not_assigned.append(
                {
                    "crmProspectId": crm_id,
                    "errorCode": vocab.ERROR_INVALID_ARGUMENT,
                    "detail": "no CRM record with that id",
                }
            )
            continue
        try:
            crm.assert_parent_account(record)
        except ContactWithoutParentAccount as exc:
            # The one researched per-prospect refusal. Caught by type rather than
            # by Exception, so a defect in the gate surfaces instead of being
            # reported to the seller as a bad CRM id.
            not_assigned.append(
                {
                    "crmProspectId": crm_id,
                    "errorCode": vocab.ERROR_INVALID_ARGUMENT,
                    "detail": str(exc),
                }
            )
            continue

        assigned.append(
            {
                "crmProspectId": crm_id,
                "flowInstanceId": new_flow_instance_id(str(flow.get("flow_id") or ""), crm_id),
                "flowInstanceStatus": instances.initial_status(),
                "exclusive": bool(flow.get("exclusive", True)),
                "workspaceId": space,
            }
        )

    return {
        "prospectsAssigned": assigned,
        "prospectsNotAssigned": not_assigned,
        "flowId": str(flow.get("flow_id") or ""),
        "flowInstanceOwnerEmail": str(request.get("flowInstanceOwnerEmail") or ""),
        "requested": len(wanted),
        "assigned": len(assigned),
        "failed": len(not_assigned),
        "endpoint": vocab.ASSIGN_ENDPOINT,
        "scope": vocab.ASSIGN_SCOPE,
        "errorCodes": list(vocab.ASSIGN_ERROR_CODES),
        "statuses": list(vocab.FLOW_INSTANCE_STATUSES),
    }


# --------------------------------------------------------------------------- #
# The ordered to-do queue
# --------------------------------------------------------------------------- #


def build_touches(flow: Mapping[str, Any], *, starts_on: str) -> list[dict[str, Any]]:
    """The ordered touches one flow instance gets.

    Derived: the research says "multi-touch follow-up sequence" and gives no step
    shape. The order is the sequence's offset order, and each touch carries the
    flow reference the research requires to appear with the activity.

    Sorted here as well as at the flow, because the position a touch gets is its
    index in this list. A caller that skipped the sort at the flow would otherwise
    produce a queue numbered in the order the steps happened to arrive rather than
    in the order they are due.
    """
    steps = sorted(flow.get("steps") or [], key=lambda step: int(step.get("offset_days") or 0))
    out: list[dict[str, Any]] = []
    for index, step in enumerate(steps, start=1):
        out.append(
            {
                "position": index,
                "kind": str(step.get("kind") or vocab.TODO_KINDS[0]),
                "subject": str(step.get("subject") or ""),
                "offset_days": int(step.get("offset_days") or 0),
                "due_on": _add_days(starts_on, int(step.get("offset_days") or 0)),
                "flow_reference": str(flow.get("flow_id") or ""),
                "flow_name": str(flow.get("name") or ""),
                "delivered_at": None,
            }
        )
    return out


def _add_days(starts_on: str, days: int) -> str:
    """A date offset in days, read from an ISO date.

    Written out rather than imported because a bad stored date must not stop a
    queue from rendering: an unreadable start produces an empty due date and the
    row still shows its position.
    """
    try:
        from datetime import date, timedelta

        return (date.fromisoformat(str(starts_on)[:10]) + timedelta(days=days)).isoformat()
    except (TypeError, ValueError):
        return ""


def queue_position(todo: Mapping[str, Any], queue: list[Mapping[str, Any]]) -> str:
    """One to-do's position in its queue, as the researched string.

    Quoted: "1/5 means you're working on the first of five to-dos in that queue."
    """
    if not queue:
        return "0/0"
    ids = [str(item.get("todo_id") or item.get("id")) for item in queue]
    target = str(todo.get("todo_id") or todo.get("id"))
    if target not in ids:
        return f"0/{len(queue)}"
    return f"{ids.index(target) + 1}/{len(queue)}"


def queue_view(
    owner_email: str,
    todos: list[Mapping[str, Any]],
    *,
    flow_reference: str = "",
) -> dict[str, Any]:
    """One owner's to-do queue, with the position each row carries.

    Grouped by queue, which the research requires: "To-dos are grouped into a
    queue". A flow with five to-dos for one owner is one queue of five, not five
    ungrouped rows.
    """
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for todo in todos:
        if str(todo.get("owner_email") or "") != str(owner_email or ""):
            continue
        key = str(todo.get("queue_id") or todo.get("flow_reference") or "ungrouped")
        grouped.setdefault(key, []).append(todo)

    queues = []
    for key in sorted(grouped):
        rows = sorted(
            grouped[key],
            key=lambda item: (int(item.get("position") or 0), str(item.get("due_on") or "")),
        )
        queues.append(
            {
                "queue_id": key,
                "flow_reference": flow_reference or str(rows[0].get("flow_reference") or key),
                "count": len(rows),
                "completed": sum(1 for row in rows if row.get("completed_at")),
                "todos": [
                    {
                        **dict(row),
                        "position_label": queue_position(row, rows),
                        "flow_reference_visible": True,
                    }
                    for row in rows
                ],
            }
        )
    return {
        "owner_email": owner_email,
        "queue_count": len(queues),
        "queues": queues,
        "note": vocab.TODO_QUEUE_QUOTE,
    }
