"""Vocabulary for pushing room events out to a CRM.

Everything in this module is either quoted straight from the research for
WF-016 or is a pure function over the schema-flexible payloads the audited store
holds. No database, no network, no clock: the whole module is cheap to test and
safe to import from anywhere.

Sourced vocabulary
------------------
``EVENTS``
    The published webhook event enum: ``pageAccepted``, ``pagePartiallyAccepted``,
    ``pagePreviewAccepted``, ``pageViewed``, ``pageFirstViewed``, ``pageSetLive``,
    ``pageRevivedLive``.

``PAGE_STATUSES``
    "Here is a list of Page Statuses Qwilr will send to your CRM: draft; live;
    partially accepted; accepted; declined". These strings are reproduced
    verbatim, spaces and all, because they are a wire contract.

``PRESETS``
    The three Recommended Automations named in the research, restated against
    this product's facts.

Nothing here names an HTTP path
-------------------------------
Every message this module produces is text a rep reads, so none of them may name
a route. The branch's lint warning told the user to "Register it under
``/api/crm/fields``"; after the port to the plugin host that path does not
exist, and a warning that sends someone to a 404 is worse than no warning. The
wording names the registry, not the URL, so it cannot go stale.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from dsr.crm.errors import CrmError

# The complete published event enum. A subscription must name one of these.
EVENTS: tuple[str, ...] = (
    "pageViewed",
    "pageFirstViewed",
    "pageAccepted",
    "pagePartiallyAccepted",
    "pagePreviewAccepted",
    "pageSetLive",
    "pageRevivedLive",
)

# The fixed set of status values sent to a CRM. Reproduced exactly as published.
PAGE_STATUSES: tuple[str, ...] = (
    "draft",
    "live",
    "partially accepted",
    "accepted",
    "declined",
)

# Field types a CRM can declare for a mapped field. A team registers these in
# the `crm_field` collection; nothing here is a migration or a typed column.
FIELD_TYPES: tuple[str, ...] = (
    "text",
    "number",
    "currency",
    "url",
    "date",
    "percent",
    "boolean",
)

# Default status per event. The five published statuses are sourced; which one
# an event implies is an inference, and the only debatable entry is
# `pagePreviewAccepted` (a preview sign-off is not full acceptance, so it is
# reported as a partial acceptance). Callers may override it per event.
EVENT_STATUS: dict[str, str] = {
    "pageViewed": "live",
    "pageFirstViewed": "live",
    "pageAccepted": "accepted",
    "pagePartiallyAccepted": "partially accepted",
    "pagePreviewAccepted": "partially accepted",
    "pageSetLive": "live",
    "pageRevivedLive": "live",
}

# The fact names :func:`room_facts` projects, published so a client can offer
# them in a picker instead of hard-coding them. A mapping may name any other
# dotted path too; these are the ones that resolve for every room shape.
FACT_PATHS: tuple[str, ...] = (
    "name",
    "account",
    "owner",
    "stage",
    "status",
    "template_id",
    "view_count",
    "live_url",
    "collaborator_url",
    "value",
    "currency",
    "close_date",
    "payment.status",
    "payment.reference",
    "payment.amount",
    "metadata",
)

# Facts whose value is a link. Mapping one of these onto a plain text field is
# the documented "URL clickability trap".
URL_FACTS = frozenset({"live_url", "collaborator_url"})

# Facts that carry a currency *code* as text. CRM currency fields want a number,
# which is the documented currency field-type trap.
CURRENCY_FACTS = frozenset({"currency"})

# Target field names that a CRM derives rather than accepts. For products the
# amount is auto-calculated from the line items and cannot be written, which is
# the documented "Amount is derived, not writable" constraint.
DERIVED_AMOUNT_FIELDS = frozenset({"amount"})

# The Recommended Automations, restated against this product's room facts. The
# names are the research's; the field maps are this product's.
PRESETS: tuple[dict[str, Any], ...] = (
    {
        "id": "sync-page-urls",
        "name": "When a page is accepted by the client, sync page URLs",
        "description": (
            "On acceptance, copy the live and collaborator links onto the CRM "
            "record so the rep can open the room from the deal."
        ),
        "crm": "salesforce",
        "trigger": {"event": "pageAccepted"},
        "actions": [
            {
                "kind": "update_fields",
                "fields": {
                    "page_live_url": "live_url",
                    "page_collaborator_url": "collaborator_url",
                },
            }
        ],
    },
    {
        "id": "sync-payment-details",
        "name": "When a page is accepted by the client, sync payment details",
        "description": (
            "On acceptance, copy the payment status, reference and amount onto "
            "the CRM record. The analogue of the source product's pay-details sync."
        ),
        "crm": "salesforce",
        "trigger": {"event": "pageAccepted"},
        "actions": [
            {
                "kind": "update_fields",
                "fields": {
                    "payment_status": "payment.status",
                    "payment_reference": "payment.reference",
                    "payment_amount": "payment.amount",
                },
            }
        ],
    },
    {
        "id": "sync-view-count",
        "name": "When a page is viewed, sync and update view count",
        "description": (
            "On every view, write the room's view count back to the CRM so "
            "engagement stays visible without a rep refreshing the page."
        ),
        "crm": "salesforce",
        "trigger": {"event": "pageViewed"},
        "actions": [{"kind": "update_fields", "fields": {"room_view_count": "view_count"}}],
    },
)


class VocabularyError(CrmError):
    """Raised when a payload names something outside the published vocabulary."""


def is_event(name: Any) -> bool:
    return name in EVENTS


def is_page_status(name: Any) -> bool:
    return name in PAGE_STATUSES


def is_field_type(name: Any) -> bool:
    return name in FIELD_TYPES


def require_event(name: Any) -> str:
    if not is_event(name):
        raise VocabularyError(f"unknown event {name!r}; expected one of {', '.join(EVENTS)}")
    return str(name)


def require_page_status(name: Any) -> str:
    if not is_page_status(name):
        raise VocabularyError(
            f"unknown page status {name!r}; expected one of {', '.join(PAGE_STATUSES)}"
        )
    return str(name)


def status_for(event: str) -> str:
    """The status an event implies when the caller does not override it."""
    return EVENT_STATUS.get(event, "live")


def dig(payload: Any, path: str, default: Any = None) -> Any:
    """Read a dotted path out of an arbitrary JSON payload.

    Room payloads are whatever a team put in ``data``, so every read has to
    tolerate absence. Returns ``default`` at the first missing segment. A
    numeric segment indexes into a list, which is how the dynamic index stores
    array elements, so ``line_items.0.amount`` reads back what was written.
    """
    current = payload
    for part in str(path).split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        elif isinstance(current, (list, tuple)) and part.lstrip("-").isdigit():
            index = int(part)
            if -len(current) <= index < len(current):
                current = current[index]
            else:
                return default
        else:
            return default
    return current


def room_facts(data: Mapping[str, Any] | None, *, room_id: str | None = None) -> dict[str, Any]:
    """Project a room record's open payload onto the facts a CRM can receive.

    This is the whole of the contract between an arbitrary room record and the
    sync layer: a fixed set of well-known fact names, each read from a few
    plausible paths, all of which may be absent. A team adding a field does not
    get it synced until they map it, and mapping a field the projection does
    not know about is reported as unresolved rather than silently dropped.
    """
    data = data or {}
    return {
        "room_id": room_id,
        "name": dig(data, "name"),
        "account": dig(data, "account"),
        "owner": dig(data, "owner"),
        "stage": dig(data, "stage"),
        "status": dig(data, "status"),
        "template_id": dig(data, "template_id"),
        "view_count": dig(data, "view_count", dig(data, "views", 0)),
        "live_url": dig(data, "links.live", dig(data, "live_url")),
        "collaborator_url": dig(data, "links.collaborator", dig(data, "collaborator_url")),
        "value": dig(data, "value", dig(data, "page_value")),
        "currency": dig(data, "currency"),
        "close_date": dig(data, "close_date"),
        "payment": dig(data, "payment"),
        "metadata": dig(data, "metadata", {}),
    }


def resolve_mapping(fields: Mapping[str, Any], facts: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve ``{crm_field: fact_path}`` into ``{crm_field: value}``.

    A fact path that is absent from the facts resolves to ``None`` rather than
    raising: a room that simply has not been paid yet should not crash the run,
    and the caller decides whether ``None`` is a problem.
    """
    return {target: dig(facts, source) for target, source in fields.items()}


def lint_mapping(
    fields: Mapping[str, Any],
    field_types: Mapping[str, str],
    facts: Mapping[str, Any] | None = None,
    *,
    hidden_fields: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    """Warn about the field-type traps the research documents.

    The product this workflow was researched from has five hard CRM
    compatibility constraints, and all five are things a user can only discover
    by watching an automation fail. Surfacing them as a lint on the mapping
    turns five runtime failures into five things the editor can explain.

    ``field_types`` maps a CRM field name to its declared type; the registry
    behind it is optional, so a name that is absent is reported at ``info``
    rather than guessed at. ``hidden_fields`` names fields the connected
    integration user cannot see, which is a permissions problem in the CRM
    rather than a mapping problem, and is reported as such.

    The type checks describe the *mapping*, not one run of it, so they do not
    need a room: the editor can show them before any room exists. Only
    ``unresolved_fact`` needs a room, so it is skipped when ``facts`` is
    ``None`` and fires when a room genuinely lacks the fact.

    Warnings are advisory. Returns ``{code, severity, target, field, message}``.
    """
    room_is_known = facts is not None
    facts = facts or {}
    warnings: list[dict[str, Any]] = []

    def add(code: str, severity: str, message: str, target: str, field: Any = None) -> None:
        warnings.append(
            {"code": code, "severity": severity, "target": target, "field": field, "message": message}
        )

    for target, source in fields.items():
        target_name = str(target)
        declared = field_types.get(target_name)
        source_path = str(source)

        if declared is None:
            add(
                "unknown_target_field",
                "info",
                f"No CRM field is registered as {target_name!r}, so its type cannot be "
                "checked. Register it in the CRM field registry to get type warnings.",
                target_name,
                source_path,
            )
        elif declared == "currency" and source_path in CURRENCY_FACTS:
            add(
                "currency_type",
                "warning",
                f"{source_path!r} is a text currency code here, but {target_name!r} is a "
                "currency field and expects a number. Map value to the currency field.",
                target_name,
                source_path,
            )
        elif declared == "text" and source_path in URL_FACTS:
            add(
                "url_type",
                "warning",
                f"{source_path!r} is a link, but {target_name!r} is a text field, so it "
                "will not be clickable in the CRM. Map it to a URL-typed field.",
                target_name,
                source_path,
            )
        elif declared not in FIELD_TYPES:
            add(
                "unknown_field_type",
                "warning",
                f"{target_name!r} declares unknown type {declared!r}.",
                target_name,
                source_path,
            )

        if target_name in hidden_fields:
            add(
                "field_not_visible",
                "warning",
                f"The connected CRM user cannot see {target_name!r}. Custom fields that do "
                "not appear are almost always a permissions problem in the CRM, not a "
                "mapping problem.",
                target_name,
                source_path,
            )

        if target_name.lower() in DERIVED_AMOUNT_FIELDS and source_path not in ("value",):
            add(
                "amount_derived",
                "warning",
                "Amount on a record with products is auto-calculated from the line items "
                "and cannot be written. Sync value to a custom field instead.",
                target_name,
                source_path,
            )

        if room_is_known and dig(facts, source_path, None) is None:
            add(
                "unresolved_fact",
                "warning",
                f"{source_path!r} is not present on this room, so {target_name!r} would be "
                "written empty. The run may need manual updating.",
                target_name,
                source_path,
            )

    return warnings


def describe() -> dict[str, Any]:
    """Machine-readable vocabulary for the UI and for other integrations.

    Published as an endpoint so a client can render the event picker and the
    status list without this file being compiled into it, and so a future event
    added here reaches every client at once.
    """
    return {
        "events": list(EVENTS),
        "page_statuses": list(PAGE_STATUSES),
        "field_types": list(FIELD_TYPES),
        "fact_paths": list(FACT_PATHS),
        "presets": [dict(preset) for preset in PRESETS],
        "event_status": dict(EVENT_STATUS),
    }


def merge_presets(presets: Iterable[Mapping[str, Any]] = PRESETS) -> list[dict[str, Any]]:
    """Copy presets into a shape safe to store as a record payload."""
    return [
        {
            "id": preset["id"],
            "name": preset["name"],
            "description": preset["description"],
            "crm": preset["crm"],
            "trigger": dict(preset["trigger"]),
            "actions": [dict(action) for action in preset["actions"]],
        }
        for preset in presets
    ]
