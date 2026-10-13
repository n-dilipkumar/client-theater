"""The flow instance state machine: exactly the three researched statuses.

Quoted: "flowInstanceStatus ... enum: Running, Pending, Paused". The issue says
"Model those three and no others", so :data:`~dsr.meeting_followup.vocabulary.FLOW_INSTANCE_STATUSES`
has length three and nothing in this module can produce a fourth value.

Three consequences follow, and each is derived rather than sourced:

**There is no completion step.** A finished sequence leaves whatever status it was
in. The research names no status for a completed flow, and inventing a fourth value
would break the one rule the issue states exactly.

**A new instance is Pending, not Running.** "Pending" is the only one of the three
that describes a sequence which has not begun. Starting a five-touch sequence as
Running on the day it was enrolled would claim the first touch had already been
sent.

**A Pending instance refuses to become Running while a touch is undelivered.** That
is what makes the first two consistent: the transition is the moment the first
touch goes out, so permitting it earlier would produce a Running instance with
nothing sent.
"""

from __future__ import annotations

from typing import Any, Mapping

from dsr.meeting_followup import vocabulary as vocab
from dsr.meeting_followup.errors import FlowInstanceTransitionRefused


def initial_status() -> str:
    """The status a new instance starts in: ``Pending``.

    Derived. The research names the three statuses and no starting one.
    ``Pending`` is used because it is the only one of the three that describes a
    sequence whose first touch has not been delivered, which is exactly the state
    a prospect is in the moment they are enrolled.
    """
    return vocab.FLOW_INSTANCE_INITIAL_STATUS


def valid_status(raw: Any) -> bool:
    """Whether a value is one of the three researched statuses."""
    return str(raw or "").strip() in vocab.FLOW_INSTANCE_STATUSES


def allowed_transitions(status: Any) -> tuple[str, ...]:
    """The statuses reachable from one status."""
    return vocab.FLOW_INSTANCE_TRANSITIONS.get(str(status or "").strip(), ())


def can_transition(current: Any, requested: Any) -> bool:
    """Whether one status change is permitted."""
    return str(requested or "").strip() in allowed_transitions(current)


def advance(
    current: Any,
    requested: Any,
    *,
    steps: list[Mapping[str, Any]] | None = None,
) -> str:
    """Move an instance to a new status, or refuse.

    Raises :class:`~dsr.meeting_followup.errors.FlowInstanceTransitionRefused`
    with the current status, the requested one and the alternatives attached.

    ``Pending`` refuses to advance while the sequence still has an undelivered
    touch. A new instance is Pending precisely because its first touch has not
    gone out, so permitting the transition early would produce a Running instance
    with nothing sent.
    """
    current_status = str(current or "").strip()
    target = str(requested or "").strip()

    if not valid_status(current_status):
        raise FlowInstanceTransitionRefused(
            f"{current_status!r} is not one of the researched statuses",
            current=current_status,
            requested=target,
            allowed=vocab.FLOW_INSTANCE_STATUSES,
        )
    if target == current_status:
        return current_status
    if not valid_status(target):
        raise FlowInstanceTransitionRefused(
            f"{target!r} is not one of the researched statuses; the enum has "
            f"exactly {len(vocab.FLOW_INSTANCE_STATUSES)} values",
            current=current_status,
            requested=target,
            allowed=allowed_transitions(current_status),
        )
    if target not in allowed_transitions(current_status):
        raise FlowInstanceTransitionRefused(
            f"a {current_status} instance cannot become {target}",
            current=current_status,
            requested=target,
            allowed=allowed_transitions(current_status),
        )
    if current_status == vocab.STATUS_PENDING and target == vocab.STATUS_RUNNING:
        outstanding = [step for step in (steps or []) if not step.get("delivered_at")]
        if outstanding:
            raise FlowInstanceTransitionRefused(
                f"{len(outstanding)} touch(es) have not been delivered, so the "
                "instance stays Pending",
                current=current_status,
                requested=target,
                allowed=allowed_transitions(current_status),
            )
    return target


def status_view(instance: Mapping[str, Any], steps: list[Mapping[str, Any]] | None = None) -> dict:
    """One instance with its status, its alternatives, and its progress.

    The alternatives travel with the read, so a page renders the three researched
    statuses as a control without a second call.
    """
    status = str(instance.get("status") or initial_status())
    steps = list(steps if steps is not None else instance.get("steps") or [])
    delivered = sum(1 for step in steps if step.get("delivered_at"))
    return {
        "flow_instance_id": instance.get("flow_instance_id") or instance.get("id"),
        "status": status,
        "allowed_transitions": list(allowed_transitions(status)),
        "statuses": list(vocab.FLOW_INSTANCE_STATUSES),
        "owner_email": instance.get("owner_email"),
        "exclusive": bool(instance.get("exclusive", True)),
        "workspace_id": instance.get("workspace_id"),
        "touches": len(steps),
        "touches_delivered": delivered,
        "terminal": False,
        "note": (
            "The researched enum has no terminal value, so a finished sequence stays Running."
        ),
    }
