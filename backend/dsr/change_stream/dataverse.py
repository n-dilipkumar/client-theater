"""Dataverse change tracking: the one-way property and the delta-link poll.

Every rule in this module is quoted. The research carries three Dataverse facts
and each one is a rule rather than a description:

* **"After you enable change tracking for a table, you can't disable it."** The
  only genuinely irreversible thing in this workflow, and enforced as a refusal
  rather than a soft delete. A room that believed it had turned the property off
  would keep polling a delta link it thinks it abandoned.

* **"You can track changes made in tables by using Web API requests that include
  the ``Prefer: odata.track-changes`` header. This header requests that a delta
  link is returned, which you can later use to retrieve table changes."** So a
  poll without the header is a plain read, not a delta poll, and answering one
  with a delta link would invent the guarantee the header exists to ask for.

* **"$filter, $orderby, $expand, and $top aren't supported when you use the
  ``Prefer: odata.track-changes`` header ... you get an error message: The
  "${filter|orderby|expand|top}" query parameter isn't supported when Change
  Tracking is enabled."** Four options refused, with the vendor's own message.
  ``$select`` is deliberately not among them - the research's own example poll
  carries it - and neither is ``$deltatoken``, which is how an incremental poll
  asks for what it missed.

The poll result is shaped like the vendor's response rather than like a room
record, because the delta link and the ``ChangeTracking`` annotation are the two
things a client has to carry forward to poll again.
"""

from __future__ import annotations

from typing import Any, Mapping

from dsr.change_stream import vocabulary
from dsr.change_stream.errors import (
    ChangeTrackingDisabled,
    ChangeTrackingIrreversible,
    MissingTrackChangesPreference,
    UnknownTable,
    UnsupportedDeltaQueryOption,
)

#: The option a caller sends by name, matched against the four the vendor refuses.
#:
#: Keys are what a client writes - ``$filter`` with or without the dollar sign,
#: ``select`` accepted because the research's own example uses it - and values are
#: the vendor's spelling, which is what goes in the error message.
_OPTION_ALIASES: dict[str, str] = {
    "filter": "$filter",
    "$filter": "$filter",
    "orderby": "$orderby",
    "$orderby": "$orderby",
    "expand": "$expand",
    "$expand": "$expand",
    "top": "$top",
    "$top": "$top",
}


def track_changes_annotation() -> str:
    """The annotation a tracked entity set carries, verbatim from the research."""
    return vocabulary.CHANGE_TRACKING_ANNOTATION


def normalise_table(raw: Mapping[str, Any]) -> dict[str, Any]:
    """A Dataverse table record, with change tracking off.

    Off, because the research describes turning it on - "In Power Apps, select
    Data > Tables and the specific table. Under Advanced options, you find the
    Track changes property" - and a table that starts on has skipped a step whose
    irreversibility is the point of the property.
    """
    if not isinstance(raw, Mapping):
        raise ChangeTrackingDisabled("a table must be described as a JSON object")
    logical_name = str(raw.get("logical_name") or raw.get("logicalName") or "").strip()
    if not logical_name:
        raise ChangeTrackingDisabled(
            "a table needs a logical_name; that is what the delta link is scoped to"
        )
    return {
        "logical_name": logical_name,
        "entity_set": str(raw.get("entity_set") or f"{logical_name}s"),
        "track_changes": False,
        "change_tracking_supported": False,
        "delta_link": "",
        "deltatoken": "",
        "change_count": 0,
        "poll_count": 0,
    }


def enable_track_changes(table: Mapping[str, Any]) -> dict[str, Any]:
    """Turn the property on. Idempotent, because turning it on twice is not a change.

    The vendor's own path for this is the Power Apps table property; this is the
    HTTP spelling of the same thing, and a second call is a no-op rather than an
    error because a room that cannot tell "already on" from "just turned on" will
    not call it twice.
    """
    if bool(table.get("track_changes")):
        return {
            "track_changes": True,
            "already_enabled": True,
            "change_tracking_supported": bool(table.get("change_tracking_supported")),
        }
    return {
        "track_changes": True,
        "already_enabled": False,
        "change_tracking_supported": True,
    }


def refuse_disable(table: Mapping[str, Any]) -> None:
    """Refuse to turn change tracking off, always.

    "After you enable change tracking for a table, you can't disable it." No
    reading of that sentence leaves room for a soft delete, an undo, or a
    privileged bypass, so this raises whatever the table's current state is -
    including "it was never on", because a caller reaching for the off switch
    needs the same message either way.
    """
    raise ChangeTrackingIrreversible(
        f"change tracking cannot be disabled on {table.get('logical_name')!r}: "
        '"After you enable change tracking for a table, you can\'t disable it." '
        "A table that no longer needs tracking can be left on; nothing this API does "
        "depends on the property being off."
    )


def find_unsupported_option(options: Mapping[str, Any]) -> tuple[str, str] | None:
    """The first refused query option in ``options``, as ``(key, vendor_name)``.

    ``$select`` and ``$deltatoken`` are not in the refused set, which is the
    research's own example poll: ``GET /api/data/v9.2/accounts?$select=…`` with
    the ``Prefer: odata.track-changes`` header.
    """
    for key in options:
        if key in (None, "", "select", "$select", "deltatoken", "$deltatoken"):
            continue
        vendor_name = _OPTION_ALIASES.get(str(key).strip().lstrip("$"))
        if vendor_name:
            return str(key), vendor_name
    return None


def poll(
    table: Mapping[str, Any],
    *,
    prefer: str | None,
    options: Mapping[str, Any],
    observed_changes: int = 0,
) -> dict[str, Any]:
    """One delta-link poll, in the shape the vendor's response has.

    ``observed_changes`` is how many changes the room's subscriber actually
    applied since the last poll; it is what makes ``$deltatoken`` and
    ``$count`` mean anything here, because a delta link on its own only says
    "there is something new".
    """
    if not prefer or vocabulary.CHANGE_TRACKING_PREFERENCE not in str(prefer):
        raise MissingTrackChangesPreference(
            "a delta poll must carry the Prefer: odata.track-changes header: "
            '"This header requests that a delta link is returned, which you can later use to '
            'retrieve table changes." Without it this is a plain read and returns no delta link.'
        )
    if not bool(table.get("track_changes")):
        raise ChangeTrackingDisabled(
            f"change tracking is not enabled on {table.get('logical_name')!r}, so there is no "
            "delta link to advance. In Power Apps, select Data > Tables and the specific "
            "table; under Advanced options, set the Track changes property."
        )
    refused = find_unsupported_option(options)
    if refused is not None:
        _key, vendor_name = refused
        raise UnsupportedDeltaQueryOption(
            vocabulary.UNSUPPORTED_DELTA_QUERY_OPTION_MESSAGE.format(option=vendor_name)
        )

    logical_name = str(table.get("logical_name"))
    entity_set = str(table.get("entity_set") or f"{logical_name}s")
    base = f"/api/data/{vocabulary.DATAVERSE_API_VERSION}/{entity_set}"
    prior_token = str(table.get("deltatoken") or "")
    requested_token = str(options.get("deltatoken") or options.get("$deltatoken") or "").strip()
    incremental = bool(requested_token)

    select = str(options.get("select") or options.get("$select") or "").strip()
    query = f"?$select={select}" if select else ""
    separator = "&" if query else "?"
    token = requested_token or prior_token or "new"

    return {
        "@odata.context": f"{base}{query}",
        "@odata.deltaLink": f"{base}{query}{separator}$deltatoken={token}",
        "preference": vocabulary.CHANGE_TRACKING_PREFERENCE,
        "entity_set": entity_set,
        "logical_name": logical_name,
        "changeTracking": {
            "Supported": True,
            "Annotation": vocabulary.CHANGE_TRACKING_ANNOTATION,
        },
        "incremental": incremental,
        "previous_deltatoken": prior_token,
        "returned_deltatoken": requested_token or prior_token or f"{logical_name}:0",
        "changes_observed": max(0, int(observed_changes)),
        "count_url": f"{base}/$count?$deltatoken={token}",
        "query_options_accepted": sorted(
            str(key)
            for key in options
            if key
            not in (
                None,
                "",
                "deltatoken",
                "$deltatoken",
                "filter",
                "$filter",
                "orderby",
                "$orderby",
                "expand",
                "$expand",
                "top",
                "$top",
            )
        ),
        "query_options_refused": list(vocabulary.UNSUPPORTED_DELTA_QUERY_OPTIONS.values()),
    }


def change_count(
    table: Mapping[str, Any],
    *,
    deltatoken: str | None,
) -> dict[str, Any]:
    """``GET /accounts/$count?$deltatoken=…`` - how many changes since a token.

    The count is of the changes the room has *applied* since that token, because
    that is the only number this product can state honestly: it is the number of
    change events the buffer committed, and it says nothing about the vendor's
    unconsumed backlog, which the research never describes.
    """
    if not bool(table.get("track_changes")):
        raise ChangeTrackingDisabled(
            f"change tracking is not enabled on {table.get('logical_name')!r}, so there is no "
            "deltatoken to count changes from."
        )
    entity_set = str(table.get("entity_set") or "")
    base = f"/api/data/{vocabulary.DATAVERSE_API_VERSION}/{entity_set}"
    token = str(deltatoken or table.get("deltatoken") or "")
    return {
        "entity_set": entity_set,
        "deltatoken": token,
        "count_url": f"{base}/$count?$deltatoken={token}",
        "count": int(table.get("change_count") or 0),
        "counted": "changes the room has committed since this deltatoken",
        "not_counted": (
            "anything still buffered in an uncommitted transaction, and any unconsumed "
            "backlog behind the vendor's delta link, which this API cannot see"
        ),
    }


__all__ = [
    "UnknownTable",
    "change_count",
    "enable_track_changes",
    "find_unsupported_option",
    "normalise_table",
    "poll",
    "refuse_disable",
    "track_changes_annotation",
]
