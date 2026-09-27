"""Switching a Play on and off: the one moment with a human in the loop.

"This workflow *is* the automation - signal to task with no human in the loop
until the seller acts." The corollary the research states outright is that the
automation is not live on arrival:

    "After registration, the registered Play must be enabled in the Salesloft UI.
    You can do so by going to Settings -> Workflow -> Plays -> Edit Play."

That sentence is the reason this module exists separately from
:mod:`dsr.plays.framework`. Registration writes ``enabled: false`` and nothing
else can set it, and the only two ways to change it are :func:`enable` and
:func:`disable`, which the feature module reaches through their own routes. A Play
that fires on registration puts a task in a seller's queue that nobody asked for,
and the only defence is that there is exactly one door and it is not the register
route.

Enabling twice is not an error. A seller's toggle is not idempotent in the UI - it
is a checkbox, and a double click is two requests - so the second one answers with
what is already true and writes nothing, rather than adding an audit row for a
change that did not happen.
"""

from __future__ import annotations

from typing import Any, Mapping

from dsr.plays.errors import ActivationError
from dsr.plays.vocabulary import ACTIVATION_PATH, DISABLED_NOTE

#: The two states a Play can be in, and the one it is born in.
REGISTERED = "registered"
ENABLED = "enabled"
DISABLED = "disabled"


def _moment(now: Any) -> str:
    if callable(now):
        return str(now())
    return str(now)


def state(record: Mapping[str, Any]) -> dict[str, Any]:
    """The activation state of a stored Play, with the research's note beside it.

    Deliberately derived rather than stored, so a row can never say ``enabled: true``
    while its ``enabled_at`` is null, and so the note that explains the difference
    travels with every read of a disabled Play rather than living in a page.
    """
    enabled = record.get("enabled") is True
    return {
        "state": ENABLED if enabled else DISABLED,
        "enabled": enabled,
        "enabled_at": record.get("enabled_at"),
        "enabled_by": record.get("enabled_by"),
        "activation_path": ACTIVATION_PATH,
        "note": None if enabled else DISABLED_NOTE,
        # Whether a signal arriving right now would do anything. Said separately
        # because it is the question a seller actually has, and it is not the same
        # question as whether the Play is enabled - a Play whose trigger no signal
        # carries is enabled and still does nothing.
        "will_create_tasks": enabled,
    }


def enable(record: Mapping[str, Any], *, actor: str | None, now: Any) -> dict[str, Any]:
    """The patch that switches a Play on, or the refusal to.

    A destroyed Play is refused: it is a soft-deleted row, and enabling a retired
    template would resurrect an automation nobody reviewed.
    """
    if record.get("deleted_at") is not None:
        raise ActivationError(
            f"this Play was destroyed and cannot be enabled. A destroyed row is a "
            "retired template; register a new one rather than reviving it."
        )
    if record.get("enabled") is True:
        return {
            "outcome": "already_enabled",
            "patch": {},
            "state": state(record),
            "detail": (
                "This Play was already enabled, so nothing was written. Enabling is not an "
                "error: a seller's toggle is a checkbox and a double click is two requests."
            ),
        }
    at = _moment(now)
    return {
        "outcome": "enabled",
        "patch": {"enabled": True, "enabled_at": at, "enabled_by": actor or "unknown"},
        "state": {
            "state": ENABLED,
            "enabled": True,
            "enabled_at": at,
            "enabled_by": actor or "unknown",
            "activation_path": ACTIVATION_PATH,
            "note": None,
            "will_create_tasks": True,
        },
        "detail": (
            f"Enabled. From the next matching signal this Play creates a task with no human "
            f"in the loop, which is the same switch the vendor exposes at {ACTIVATION_PATH}."
        ),
    }


def disable(record: Mapping[str, Any], *, actor: str | None, now: Any) -> dict[str, Any]:
    """The patch that switches a Play off, or the refusal to.

    Disabling is how a Play is retired without destroying it. The tasks it already
    created stay exactly as they are and keep naming it: they are history, and a
    seller who acted on one of them still acted on one of them.
    """
    if record.get("deleted_at") is not None:
        raise ActivationError("this Play was destroyed and is already off")
    if record.get("enabled") is not True:
        return {
            "outcome": "already_disabled",
            "patch": {},
            "state": state(record),
            "detail": "This Play was already disabled, so nothing was written.",
        }
    at = _moment(now)
    return {
        "outcome": "disabled",
        "patch": {"enabled": False, "enabled_at": None, "enabled_by": None},
        "state": {
            "state": DISABLED,
            "enabled": False,
            "enabled_at": None,
            "enabled_by": None,
            "activation_path": ACTIVATION_PATH,
            "note": DISABLED_NOTE,
            "will_create_tasks": False,
        },
        "detail": (
            "Disabled. It creates nothing from the next matching signal. The tasks it "
            "already created are untouched and still name this Play."
        ),
    }
