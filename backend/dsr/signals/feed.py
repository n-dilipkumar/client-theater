"""The seller's Live Feed: rendered sentences, ordered by urgency.

"Salesloft hydrates the signal to a Person/Account and publishes the rendered
description to the seller's Live Feed", and the indicators are "rendered as human
sentences". Two sourced rules decide what this feed contains and in what order,
and they are the only two:

* ``broadcast_notification`` "controls Live Feed display". A signal that carries
  it as false is still a delivered signal and still stored - it is simply not
  something this module shows. Excluding it from the feed rather than from the
  store is what makes the flag mean what the research says it means.
* ``urgency`` "drives priority". So the order is urgency, then recency, and never
  recency first: a low-urgency signal from a minute ago does not outrank a
  high-urgency one from an hour ago.

The feed also repeats, on every row, that a signal makes nobody do anything. The
research is explicit that "users may choose to not take action on a signal" and
that "Actionability depends on the end user's governance (Play) configurations
and settings within Salesloft". A feed that implied otherwise would be the single
most misleading thing this product could put in front of a seller, and a seller
who thinks an intent signal is a task will stop acting on the ones that are.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from dsr.signals import icume
from dsr.signals.vocabulary import (
    ACTIONABILITY_NOTE,
    resolve_locale,
    urgency_rank,
)


def arguments_for(signal: Mapping[str, Any]) -> dict[str, Any]:
    """The values a message can refer to, in resolution order.

    The indicator's own metadata first, then the signal's ``data``, then the
    context the registration cannot supply. Indicator metadata comes first
    because that is the evidence the sentence is making a claim about: if a
    signal's ``data`` and an indicator's metadata both carry ``view_count``, the
    sentence is about the indicator's number.
    """
    arguments: dict[str, Any] = {}
    for entry in signal.get("indicators") or []:
        if isinstance(entry, Mapping) and isinstance(entry.get("metadata"), Mapping):
            for key, value in entry["metadata"].items():
                arguments.setdefault(str(key), value)
    data = signal.get("data")
    if isinstance(data, Mapping):
        for key, value in data.items():
            arguments.setdefault(str(key), value)
    for key, value in (
        ("signal_name", signal.get("signal_name")),
        ("type", signal.get("type")),
        ("occurred_at", signal.get("occurred_at")),
        ("account", (signal.get("room") or {}).get("account") if isinstance(signal.get("room"), Mapping) else None),
    ):
        if value is not None:
            arguments.setdefault(key, value)
    return arguments


def render_signal(
    registration: Mapping[str, Any], signal: Mapping[str, Any], locale: str | None = None
) -> dict[str, Any]:
    """Render a signal's description and each of its indicators into sentences.

    The registration's localized description is the signal's own sentence; each
    indicator's localized description is the sentence for that indicator. An
    indicator with no description of its own falls back to the registration's,
    which is weaker but never empty, and the fallback is reported.
    """
    requested = locale or signal.get("locale") or "en"
    templates = registration.get("description") or {}
    resolved, fell_back = resolve_locale(requested, templates)
    arguments = arguments_for(signal)

    warnings: list[str] = []
    if fell_back:
        warnings.append(
            f"no description for locale {requested!r}; rendered {resolved!r} instead. The "
            "registration declares "
            f"{', '.join(sorted(templates))}."
        )

    text = ""
    text_warnings: list[str] = []
    if resolved is None:
        warnings.append("the registration declares no description, so no sentence could be rendered")
    else:
        rendered = icume.render(templates[resolved], arguments)
        text = rendered["text"]
        text_warnings = rendered["warnings"]

    indicator_sentences: list[dict[str, Any]] = []
    for entry in signal.get("indicators") or []:
        if not isinstance(entry, Mapping):
            continue
        key = str(entry.get("key"))
        declared = next(
            (
                candidate
                for candidate in registration.get("indicators") or []
                if str(candidate.get("key")) == key
            ),
            None,
        )
        indicator_templates = (declared or {}).get("description") or {}
        indicator_resolved, indicator_fell_back = resolve_locale(requested, indicator_templates)
        if indicator_resolved is None:
            indicator_sentences.append(
                {
                    "key": key,
                    "text": text,
                    "source_locale": None,
                    "fallback": True,
                    "warnings": [
                        "this indicator has no description of its own, so the signal's "
                        "description is shown in its place"
                    ],
                    "metadata": entry.get("metadata"),
                    "qualification": entry.get("qualification"),
                }
            )
            continue
        rendered = icume.render(indicator_templates[indicator_resolved], arguments)
        indicator_sentences.append(
            {
                "key": key,
                "text": rendered["text"],
                "source_locale": indicator_resolved,
                "fallback": indicator_fell_back,
                "warnings": rendered["warnings"],
                "metadata": entry.get("metadata"),
                "qualification": entry.get("qualification"),
            }
        )

    return {
        "locale_requested": requested,
        "locale_resolved": resolved,
        "locale_fallback": fell_back,
        "locales_available": sorted(templates),
        "text": text,
        "indicators": indicator_sentences,
        "warnings": warnings + text_warnings,
    }


def feed_entry(
    signal: Mapping[str, Any],
    registration: Mapping[str, Any] | None,
    locale: str | None = None,
) -> dict[str, Any]:
    """One row of the Live Feed.

    The rendered sentence is recomputed per read rather than frozen at emit time.
    That is a deliberate trade: a registration is immutable in everything that
    changes a sentence (see :mod:`dsr.signals.registration`), so recomputing gives
    the same answer, and it means an amendment that adds a locale shows up in
    signals that were emitted before it existed.
    """
    rendered = render_signal(registration or {}, signal, locale) if registration else {
        "locale_requested": locale or "en",
        "locale_resolved": None,
        "locale_fallback": False,
        "locales_available": [],
        "text": "",
        "indicators": [],
        "warnings": ["this signal's registration has been withdrawn, so it cannot be rendered"],
    }
    return {
        "id": signal.get("id"),
        "room_id": signal.get("room_id"),
        "registration_id": signal.get("registration_id"),
        "signal_name": signal.get("signal_name"),
        "type": signal.get("type"),
        "integration_id": signal.get("integration_id"),
        "urgency": signal.get("urgency"),
        "occurred_at": signal.get("occurred_at"),
        "broadcast_notification": signal.get("broadcast_notification"),
        "attribution": signal.get("attribution"),
        "receiver": signal.get("receiver"),
        "idempotency_key": signal.get("idempotency_key"),
        "duplicate_attempts": signal.get("duplicate_attempts", 0),
        "indicators": signal.get("indicators") or [],
        "warnings": list(signal.get("warnings") or []) + rendered["warnings"],
        "qualification": [entry.get("qualification") for entry in signal.get("indicators") or []],
        "rendered": rendered,
        # Said on the row, every time, because it is the thing a seller is most
        # likely to misread and the research is most explicit about.
        "actionable": False,
        "actionability_note": ACTIONABILITY_NOTE,
    }


def build(
    signals: Sequence[Mapping[str, Any]],
    registrations: Mapping[str, Mapping[str, Any]],
    *,
    seller: str | None = None,
    locale: str | None = None,
) -> list[dict[str, Any]]:
    """Assemble the feed: broadcast signals only, highest urgency first."""
    rows = []
    for signal in signals:
        if signal.get("broadcast_notification") is not True:
            continue
        if seller is not None:
            receiver = signal.get("receiver") or {}
            if str(receiver.get("seller") or "") != seller:
                continue
        rows.append(feed_entry(signal, registrations.get(str(signal.get("registration_id"))), locale))
    # Most recent first, then highest urgency first. Two passes rather than one
    # composite key because a stable sort preserves the first pass's order inside
    # each urgency band, and an ISO 8601 timestamp cannot be negated the way a
    # number can.
    rows.sort(key=lambda row: str(row.get("occurred_at") or ""), reverse=True)
    rows.sort(key=lambda row: urgency_rank(str(row.get("urgency"))))
    return rows
