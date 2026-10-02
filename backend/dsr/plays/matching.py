"""Deciding which Plays a signal fires, and what each decision was.

The researched data flow is one sentence:

    "Registered signal fires -> matches a Play's indicator list -> Salesloft
    generates a one-off task -> assignment resolved by precedence"

So the match is an intersection between the indicators the signal carries and the
``indicators[]`` the Play declared as its triggers. Nothing about urgency, nothing
about the signal's data, nothing about the seller. A Play fires on the indicator
list and on nothing else, and :func:`match_plays` reports every Play with a
reason so a signal that fired nothing can say which door it did not go through.

The second thing that gates a Play is :data:`dsr.plays.activation.REGISTERED` -
"After registration, the registered Play must be enabled in the Salesloft UI." A
disabled Play is not a match, and saying ``enabled: false`` as the reason is more
useful than returning an empty list, because the most common reason a Play appears
to do nothing is that nobody switched it on.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from dsr.plays.vocabulary import AUTOMATION_NOTE

#: Every reason a Play did or did not fire, published so a client renders the
#: sentence this module wrote rather than one of its own.
MATCH_REASONS: dict[str, str] = {
    "matched": ("the signal carries an indicator this Play triggers on, and the Play is enabled"),
    "not_enabled": (
        '"After registration, the registered Play must be enabled in the Salesloft UI." '
        "This one is not, so it creates nothing."
    ),
    "registration_mismatch": (
        "the Play is registered against a different signal registration, so a signal from "
        "this one is not one it can fire on"
    ),
    "no_indicators": (
        "the signal carries no indicators, and a Play triggers on an indicator list, so "
        "there is nothing to match"
    ),
    "no_overlap": ("the signal carries none of the indicators this Play triggers on"),
}


def signal_indicator_keys(signal: Mapping[str, Any]) -> list[str]:
    """The indicator keys a signal carries, deduplicated and ordered.

    The researched signal carries ``indicators[]`` as objects with a ``key`` and
    some ``metadata``; a bare key is accepted as well, because a Play's trigger
    list is bare keys and a person comparing the two should not have to unwrap one.
    """
    raw = signal.get("indicators")
    if raw is None:
        return []
    if isinstance(raw, (str, Mapping)):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return []
    keys: list[str] = []
    for entry in raw:
        if isinstance(entry, Mapping):
            entry = entry.get("key")
        if isinstance(entry, str) and entry.strip() and entry.strip() not in keys:
            keys.append(entry.strip())
    return keys


def match_plays(
    plays: Sequence[Mapping[str, Any]], signal: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """One decision per Play, in the order they were registered.

    Every decision carries ``fired`` and a ``reason`` drawn from
    :data:`MATCH_REASONS`. A caller asking why a signal did nothing gets a per-Play
    answer rather than a count.
    """
    keys = signal_indicator_keys(signal)
    registration = signal.get("registration_id")
    if not isinstance(registration, str) or not registration.strip():
        # A signal that names no registration is matched on its own type, which is
        # the key the registration itself is filed under. See the
        # `signal-matches-on-type` inference.
        registration = signal.get("type")
    registration = registration.strip() if isinstance(registration, str) else None

    decisions: list[dict[str, Any]] = []
    for play in plays:
        triggers = [str(key) for key in (play.get("indicators") or [])]
        overlap = [key for key in keys if key in triggers]
        enabled = play.get("enabled") is True
        play_registration = play.get("signal_registration_id")

        if not enabled:
            reason = "not_enabled"
        elif not keys:
            reason = "no_indicators"
        elif registration and play_registration and play_registration != registration:
            reason = "registration_mismatch"
        elif not overlap:
            reason = "no_overlap"
        else:
            reason = "matched"

        decisions.append(
            {
                "play_id": play.get("id"),
                "label": play.get("label"),
                "task_type": (play.get("attributes") or {}).get("task_type"),
                "enabled": enabled,
                "triggers": triggers,
                "overlap": overlap,
                "fired": reason == "matched",
                "reason": reason,
                "detail": MATCH_REASONS[reason],
            }
        )
    return decisions


def describe() -> dict[str, Any]:
    """The matching rules, published for clients and for the page."""
    return {
        "reasons": MATCH_REASONS,
        "rule": (
            "a Play fires when the signal carries one of the indicators the Play declared "
            "as a trigger, and the Play is enabled. Nothing else about the signal "
            "participates."
        ),
        "automation_note": AUTOMATION_NOTE,
    }


def note_no_plays(enabled_only: Iterable[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """The answer when nothing fired, distinguishing an empty registry from a switch."""
    plays = list(enabled_only)
    if not plays:
        return {
            "reason": "no_plays_registered",
            "detail": (
                "No Play framework is registered against this signal's registration. A Play "
                "is what turns a signal into an action; without one a signal is only ever a "
                "signal."
            ),
        }
    return {
        "reason": "no_play_fired",
        "detail": (
            f"{len(plays)} Play framework(s) are registered against this signal's "
            "registration and none fired. Each decision above names which of the two gates "
            "it stopped at: the enable switch, or the indicator overlap."
        ),
    }
