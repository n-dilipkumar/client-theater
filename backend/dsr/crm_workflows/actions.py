"""Resolving a workflow's actions against a contact and a room.

The research names four actions and one write side: "send emails, send Slack
notifications, update HubSpot fields, change stages", performed by "HubSpot's CRM
API (PATCH /crm/v3/objects/contacts/{contactId}, POST
/crm/v3/objects/contacts/batch/upsert) and HubSpot workflow APIs".

This build **records** what would be written rather than writing it, and says so on
every resolved action. That is the same choice
:mod:`dsr.crm.automations` made for the sibling CRM workflow research, and it is
the honest one: the research names the endpoints but no credentials, no tenant and
no request body, so an "executor" here would be a function that opens a socket and
sends nothing. The action plan is the part a team with a real HubSpot client needs
and the part that is actually verifiable in a test.

Three outcomes, not two
-----------------------

``planned``, ``refused``, ``unresolved``:

* **planned** - the action is one of the four, and what it would write resolved.
* **refused** - it is one of the four, but it cannot apply this time. The one
  researched case is the forward-only lifecycle stage: "When you include the
  ``lifecyclestage`` property, you can only set the value *forward* in the stage
  order." A ``change_stage`` naming a stage at or behind the contact's current one
  is refused **for that action**, and the workflow's other actions still apply.
  Refusing the whole enrollment would be wrong: "Change stages in HubSpot based on
  onboarding or mutual action plan tasks" is one action in a workflow that may also
  send an email, and a contact who completed a task out of order has still
  completed it.
* **unresolved** - the kind is outside the four. Stored, never dropped. See
  :func:`dsr.crm_workflows.definition.normalise_actions`.

A deal/pipeline stage is deliberately *not* subject to the forward-only rule, which
is why :func:`resolve_action` requires ``stage_kind``. "Change stages in HubSpot
based on onboarding or mutual action plan tasks" is about a pipeline, and a
pipeline moves backwards all the time. The lifecycle stage is a different property
with a different, sourced, one-way constraint.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from dsr.crm_workflows.vocabulary import (
    ACTION_KINDS,
    CRM_WRITE_ENDPOINTS,
    EXECUTION_NOTE,
    LIFECYCLE_STAGES,
    dig,
    first_present,
    lifecycle_rank,
)

PLANNED = "planned"
REFUSED = "refused"
UNRESOLVED = "unresolved"


def _text(value: Any, default: str = "") -> str:
    return value.strip() if isinstance(value, str) and value.strip() else default


def _contact_value(contact: Any) -> str:
    """The buyer's address, from whatever the caller passed as the contact.

    Accepts a bare string, or a mapping under any of the keys the vocabulary's
    alias table already uses for a buyer. The engine passes a mapping, and a test
    passes a string, and neither should have to know which shape the other picked -
    a function that returned ``None`` for one of them would make the email it plans
    to send address nobody, which is the kind of thing that only shows up in a
    reviewer's inbox.
    """
    if isinstance(contact, str):
        return contact.strip()
    if not isinstance(contact, Mapping):
        return ""
    found = first_present(contact, "contact")
    return found.strip() if isinstance(found, str) else ""


def _resolve_value(spec: Any, *, room: Mapping[str, Any] | None, contact: Mapping[str, Any] | None) -> Any:
    """Resolve an action's value, which may be a literal or a path out of the room.

    A literal is used as-is. A ``{"from": "dotted.path"}`` object is read out of the
    room (falling back to the contact), and a path that resolves to nothing yields
    ``None`` with the caller reporting it - the same choice
    :func:`dsr.crm.vocabulary.resolve_mapping` makes, and for the same reason: a
    room that simply lacks a field should not crash a run.
    """
    if isinstance(spec, Mapping) and "from" in spec:
        path = str(spec.get("from") or "").strip()
        if not path:
            return None
        for source in (room, contact):
            if source is None:
                continue
            found = dig(source, path)
            if found is not None:
                return found
        return None
    return spec


def resolve_action(
    action: Mapping[str, Any],
    *,
    contact: Mapping[str, Any] | None = None,
    room: Mapping[str, Any] | None = None,
    lifecycle_stage: str | None = None,
) -> dict[str, Any]:
    """One action, resolved against the contact and the room.

    Always returns a row. The row is what an enrollment carries, so a reader can
    see which actions the workflow would run, which it would not, and why.
    """
    kind = _text(action.get("kind"), "unknown")
    base: dict[str, Any] = {
        "index": action.get("index"),
        "kind": kind,
        "api": CRM_WRITE_ENDPOINTS.get(kind),
        "executed": False,
    }

    if kind not in ACTION_KINDS:
        return {
            **base,
            "status": UNRESOLVED,
            "resolved": False,
            "reason": str(
                action.get("reason")
                or (
                    f"{kind!r} is not one of the four action kinds the research names "
                    f"({', '.join(ACTION_KINDS)}). It is kept on the enrollment and "
                    f"reported unresolved, because the source says 'and more!'."
                )
            ),
            "write": None,
        }

    buyer = _contact_value(contact)

    if kind == "send_email":
        to = _text(action.get("to")) or buyer
        return {
            **base,
            "status": PLANNED,
            "resolved": True,
            "write": {
                "kind": "send_email",
                "to": to or None,
                "template": _text(action.get("template")) or None,
                "subject": _text(action.get("subject")) or None,
            },
            "note": (
                "Delivered by the CRM's own workflow action, not by this product. The "
                "research lists email as a HubSpot workflow action."
            ),
        }

    if kind == "slack_notification":
        return {
            **base,
            "status": PLANNED,
            "resolved": True,
            "write": {
                "kind": "slack_notification",
                "channel": _text(action.get("channel")) or None,
                "text": _text(action.get("text")) or None,
                "contact": buyer or None,
                "room": _text((room or {}).get("id")) or None,
            },
            "note": (
                "Slack is reached through the HubSpot workflow action ('send emails, "
                "slack notifications, update fields, change stages'), so the write "
                "recorded here is the workflow action rather than a Slack call."
            ),
        }

    if kind == "update_field":
        field = _text(action.get("field"))
        value = _resolve_value(action.get("value"), room=room, contact=contact)
        return {
            **base,
            "status": PLANNED,
            "resolved": True,
            "write": {"kind": "update_field", "field": field, "value": value},
            "note": (
                "Researched: 'Update HubSpot fields based on Dock activity.' The write "
                f"side is {CRM_WRITE_ENDPOINTS['update_field']}."
            ),
        }

    # change_stage
    stage = _text(action.get("stage"))
    stage_kind = _text(action.get("stage_kind"), "deal_stage")
    row: dict[str, Any] = {
        **base,
        "status": PLANNED,
        "resolved": True,
        "write": {"kind": "change_stage", "stage": stage, "stage_kind": stage_kind},
    }

    if stage_kind == "lifecyclestage":
        target_rank = lifecycle_rank(stage)
        current_rank = lifecycle_rank(lifecycle_stage)
        if target_rank is None:
            return {
                **row,
                "status": REFUSED,
                "reason": (
                    f"{stage!r} is not one of the documented lifecycle stages "
                    f"({', '.join(LIFECYCLE_STAGES)})."
                ),
            }
        if current_rank is not None and target_rank <= current_rank:
            return {
                **row,
                "status": REFUSED,
                "reason": (
                    f"the lifecycle stage can only move forward in stage order, and "
                    f"{stage!r} is at or behind the contact's current stage "
                    f"{lifecycle_stage!r}. The source states the constraint: 'you can "
                    f"only set the value *forward* in the stage order.'"
                ),
            }
        row["reason"] = (
            f"moving the lifecycle stage from {lifecycle_stage or 'unset'} to {stage!r}, "
            f"which is forward in stage order."
        )
    else:
        row["reason"] = (
            f"moving the deal stage to {stage!r}. A deal stage is a pipeline position "
            f"and is not subject to the forward-only lifecycle-stage rule."
        )
    return row


def resolve_actions(
    actions: Sequence[Mapping[str, Any]],
    *,
    contact: Mapping[str, Any] | None = None,
    room: Mapping[str, Any] | None = None,
    lifecycle_stage: str | None = None,
) -> list[dict[str, Any]]:
    """Every action of a workflow, resolved, in the order the workflow lists them."""
    return [
        resolve_action(
            action, contact=contact, room=room, lifecycle_stage=lifecycle_stage
        )
        for action in actions
    ]


def summarise(plan: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Counts an enrollment carries so a list does not have to walk the plan."""
    return {
        "actions": len(plan),
        "planned": sum(1 for row in plan if row["status"] == PLANNED),
        "refused": sum(1 for row in plan if row["status"] == REFUSED),
        "unresolved": sum(1 for row in plan if row["status"] == UNRESOLVED),
        "executed": 0,
        "execution": EXECUTION_NOTE,
    }
