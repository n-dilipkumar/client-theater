"""The pure rules of WF-121: the link, the view, the revisit and the alert.

Nothing here touches the store. Every function takes plain dicts - the ``data``
payloads a record holds - and returns plain data, so each rule can be tested
against a literal example rather than through a database, and
:mod:`.book` stays a thin read/write shell.

The two rules that are **not** this workflow's own, and are therefore imported
rather than re-declared:

* the revisit split is WF-018's
  :func:`~dsr.pdf_analytics.metrics.group_sessions`, driven by
  :data:`~.vocabulary.REVISIT_GAP_SECONDS`, which is WF-018's
  ``SESSION_GAP_SECONDS``;
* expiry is WF-069's :func:`~dsr.link_gating.rules.expiry_state`, whose
  boundary is ``now > expires_at`` - at the instant named the link is still
  open.

The two rules that **are** this build's, because the research names the
capability without a value, are the link lifetime and the "n-th page view"
alert. Both are recorded in :mod:`.inferences`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from dsr.link_gating import rules as gate_rules
from dsr.pdf_analytics import metrics as pdf_metrics
from dsr.pdf_analytics.vocabulary import audience_of

from . import vocabulary as vocab
from .errors import DwellRefused, LinkClosed, LinkValidationError

#: A link lifetime must be a whole number of days inside this range. The bounds
#: are a guard rail, not a researched fact: the exact lifetime is an inference.
MIN_LIFETIME_DAYS = 1
MAX_LIFETIME_DAYS = 3650


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _as_bool(value: Any, field: str, *, default: bool = False) -> bool:
    """Coerce a JSON boolean, refusing ``bool("false") is True``.

    The string forms WF-069's CLI documents are accepted here too, because a
    query string arrives as text and this workflow's own demo writes booleans.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "yes", "on", "1"):
            return True
        if lowered in ("false", "no", "off", "0"):
            return False
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    raise LinkValidationError(f"{field} must be a boolean, got {value!r}", field=field)


def _as_int(value: Any, field: str, *, default: int, minimum: int, maximum: int) -> int:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        raise LinkValidationError(f"{field} must be a whole number, got {value!r}", field=field)
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise LinkValidationError(
            f"{field} must be a whole number, got {value!r}", field=field
        ) from exc
    if parsed < minimum or parsed > maximum:
        raise LinkValidationError(
            f"{field} must be between {minimum} and {maximum}, got {parsed}", field=field
        )
    return parsed


def _text(value: Any, field: str, *, required: bool = False) -> str | None:
    text = "" if value is None else str(value).strip()
    if not text:
        if required:
            raise LinkValidationError(f"{field} is required", field=field)
        return None
    return text


def _aliased(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {vocab.alias_for(key): value for key, value in dict(payload or {}).items()}


# --------------------------------------------------------------------------- #
# The privacy boundary
# --------------------------------------------------------------------------- #


def refuse_document_bodies(payload: Mapping[str, Any]) -> None:
    """Refuse a view that carries a document body or raw form value.

    The documented open-source analogue for this workflow, DeckLens, "explicitly
    does not transmit document bodies or raw form values". The research calls
    that a privacy boundary, not an accident, so it is enforced by name here
    rather than promised in a docstring. The per-page telemetry this workflow
    stores is page numbers and durations and nothing else.
    """
    fields = _aliased(payload)
    for key in fields:
        if key in vocab.REFUSED_PAYLOAD_KEYS:
            raise LinkValidationError(
                f"a tracked view may not carry {key!r}; this workflow records page "
                "numbers and durations only and never a document body",
                field=key,
                refused=list(vocab.REFUSED_PAYLOAD_KEYS),
            )


# --------------------------------------------------------------------------- #
# The link
# --------------------------------------------------------------------------- #


def normalise_link(payload: Mapping[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Validate and normalise the link a rep is sharing.

    An explicit ``expires_at`` wins; otherwise the lifetime is the inferred
    default. The bound is a plain range check, and ``expires_at`` is parsed by
    WF-069's own rule so a bare date means the end of that day in both
    workflows.
    """
    moment = now or now_utc()
    fields = _aliased(payload)

    asset_id = _text(fields.get("asset_id"), "asset_id", required=True)
    title = _text(fields.get("title"), "title") or "Tracked deal link"

    if "expires_at" in fields and fields.get("expires_at") not in (None, ""):
        try:
            expires = gate_rules.parse_moment(fields["expires_at"])
        except gate_rules.GateError as exc:
            raise LinkValidationError(
                "expires_at must be an ISO date or timestamp, or null for the default lifetime",
                field="expires_at",
            ) from exc
        expires_at = expires.isoformat() if expires else None
    else:
        lifetime = _as_int(
            fields.get("lifetime_days"),
            "lifetime_days",
            default=vocab.DEFAULT_LINK_LIFETIME_DAYS,
            minimum=MIN_LIFETIME_DAYS,
            maximum=MAX_LIFETIME_DAYS,
        )
        expires_at = (moment + timedelta(days=lifetime)).isoformat()

    recipients_raw = fields.get("recipients") or []
    if isinstance(recipients_raw, str):
        recipients_raw = [part.strip() for part in recipients_raw.split(",") if part.strip()]
    if not isinstance(recipients_raw, (list, tuple)):
        raise LinkValidationError(
            "recipients must be a list of email addresses", field="recipients"
        )
    recipients: list[str] = []
    for entry in recipients_raw:
        try:
            address = vocab.require_viewer(entry)
        except gate_rules.GateError as exc:
            raise LinkValidationError(
                f"recipients must be email addresses; {entry!r} is not usable",
                field="recipients",
            ) from exc
        if address not in recipients:
            recipients.append(address)

    return {
        "title": title,
        "asset_id": asset_id,
        "recipients": recipients,
        "expires_at": expires_at,
        "lifetime_days": _as_int(
            fields.get("lifetime_days"),
            "lifetime_days",
            default=vocab.DEFAULT_LINK_LIFETIME_DAYS,
            minimum=MIN_LIFETIME_DAYS,
            maximum=MAX_LIFETIME_DAYS,
        ),
        "alert_on_page": _as_int(
            fields.get("alert_on_page"),
            "alert_on_page",
            default=vocab.DEFAULT_ALERT_PAGE_THRESHOLD,
            minimum=1,
            maximum=1000,
        ),
        "notify_on_return": _as_bool(
            fields.get("notify_on_return"), "notify_on_return", default=True
        ),
        "tracking_enabled": _as_bool(
            fields.get("tracking_enabled"), "tracking_enabled", default=True
        ),
        "require_viewer": _as_bool(fields.get("require_viewer"), "require_viewer", default=True),
        "crm_system": vocab.require_crm_system(fields.get("crm_system")),
        "crm_deal_id": _text(fields.get("crm_deal_id"), "crm_deal_id"),
        "share_url": _text(fields.get("share_url"), "share_url"),
    }


def link_state(record: Mapping[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """The link's state, and the reason, on this request.

    Expiry is evaluated here rather than trusted from creation, which is the
    researched automation: "Expiry is evaluated on every viewer request." A
    revoked link and an expired one return the same ``state`` wording a buyer
    sees, which is WF-069's rule kept intact.
    """
    moment = now or now_utc()
    data = dict(record.get("data") or {})
    revoked = record.get("deleted_at") is not None
    expired, reason, expiry = gate_rules.expiry_state(data.get("expires_at"), moment)
    state = "revoked" if revoked else ("expired" if expired else "active")
    remaining = None
    if expiry is not None and not revoked:
        remaining = max(0, int((expiry - moment).total_seconds()))
    return {
        "state": state,
        "revoked": revoked,
        "expired": expired,
        "expiry_reason": reason,
        "expires_at": data.get("expires_at"),
        "expires_in_seconds": remaining,
        "closed": state != "active",
    }


def require_open(record: Mapping[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """The link's payload, or :class:`~.errors.LinkClosed`."""
    status = link_state(record, now=now)
    if status["closed"]:
        raise LinkClosed(
            "This link has expired. Ask the sender for a new one.",
            reason=status["state"],
            link_id=record.get("id"),
            expires_at=status["expires_at"],
        )
    return status


# --------------------------------------------------------------------------- #
# The view and its per-page telemetry
# --------------------------------------------------------------------------- #


def normalise_view(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one viewer's open of one link, and its per-page timings."""
    refuse_document_bodies(payload)
    fields = _aliased(payload)

    viewer = None
    if fields.get("viewer") not in (None, ""):
        try:
            viewer = vocab.require_viewer(fields.get("viewer"))
        except gate_rules.GateError as exc:
            raise LinkValidationError(
                "viewer must be an email address, so a tracked view has an owner",
                field="viewer",
            ) from exc

    session_id = _text(fields.get("session_id"), "session_id")
    is_internal = _as_bool(fields.get("is_internal"), "is_internal", default=False)
    occurred_at = pdf_metrics.parse_ts(fields.get("occurred_at"))

    raw_timings = fields.get("timings")
    if not isinstance(raw_timings, Sequence) or isinstance(raw_timings, (str, bytes)):
        raise DwellRefused("timings must be a list of {page, seconds} objects", field="timings")
    if not raw_timings:
        raise DwellRefused(
            "timings must not be empty; a view with no page is not a view", field="timings"
        )

    timings: list[dict[str, Any]] = []
    for position, item in enumerate(raw_timings):
        if not isinstance(item, Mapping):
            raise DwellRefused(
                f"timings[{position}] must be an object, got {item!r}", field=f"timings[{position}]"
            )
        entry = _aliased(item)
        try:
            page = vocab.as_int(entry.get("page"), f"timings[{position}].page", minimum=1)
            seconds = vocab.as_seconds(entry.get("seconds"), f"timings[{position}].seconds")
        except Exception as exc:  # WF-018's ValidationError, re-voiced as this workflow's
            raise DwellRefused(str(exc), field=f"timings[{position}]") from exc
        timings.append(
            {
                "page": page,
                "seconds": seconds,
                "occurred_at": pdf_metrics.parse_ts(entry.get("occurred_at")),
            }
        )

    return {
        "viewer": viewer,
        "session_id": session_id,
        "is_internal": is_internal,
        "audience": audience_of({"isInternal": is_internal}),
        "occurred_at": occurred_at,
        "timings": timings,
        "crm_deal_id": _text(fields.get("crm_deal_id"), "crm_deal_id"),
        "crm_system": vocab.require_crm_system(fields.get("crm_system")),
        # The other half of the researched identity source: "email gate or
        # CRM-matched user". A view that arrived already matched to a CRM record
        # carries the id, and it travels onto the engagement record so WF-037's
        # field map can resolve the buyer without a second lookup.
        "buyer_crm_id": _text(
            fields.get("buyer_crm_id") or fields.get("crm_contact_id"), "buyer_crm_id"
        ),
    }


def timing_rows(
    link: Mapping[str, Any],
    view: Mapping[str, Any],
    *,
    asset_name: str | None = None,
    room_ref: str | None = None,
) -> list[dict[str, Any]]:
    """The rows one view writes, in the shape WF-018's rules read.

    ``page``, ``seconds``, ``occurredAt``, ``viewer`` and ``sessionId`` are
    WF-018's own field names on a timing row, so the shared metric functions
    take these rows unchanged. ``linkId``, ``assetName`` and the room reference
    are this workflow's additions, stored inside ``records.data`` with no
    migration. The room reference is a *payload* key rather than only the record
    envelope, because the dynamic index does not cover the envelope and a
    room-scoped count would otherwise silently return nothing.
    """
    base = view.get("occurred_at") or now_utc()
    rows: list[dict[str, Any]] = []
    for timing in view["timings"]:
        stamp = timing.get("occurred_at") or base
        rows.append(
            {
                "linkId": link["id"],
                "assetId": link["data"].get("asset_id"),
                "assetName": asset_name,
                vocab.ROOM_REF: room_ref,
                "viewer": view.get("viewer"),
                "sessionId": view.get("session_id"),
                "audience": view.get("audience"),
                "isInternal": bool(view.get("is_internal")),
                "page": timing["page"],
                "seconds": timing["seconds"],
                "occurredAt": stamp.isoformat(),
            }
        )
    return rows


def group_revisits(
    rows: Sequence[Mapping[str, Any]], *, gap_seconds: int = vocab.REVISIT_GAP_SECONDS
) -> list[dict[str, Any]]:
    """A viewer's page rows, grouped into the readings they belong to.

    WF-018's :func:`~dsr.pdf_analytics.metrics.group_sessions` prefers an
    explicit ``sessionId`` over the time gap, and a revisit is *defined* by the
    gap: a viewer who comes back the next day is a second revisit even though
    the viewer knows it is a second session. So the session id is dropped from
    the copy handed to the shared rule, and the gap decides. The stored rows keep
    their session id, which is what a rep reads.
    """
    stripped = [{**dict(row), "sessionId": None} for row in rows]
    return pdf_metrics.group_sessions(stripped, gap_seconds=gap_seconds)


def page_dwell(
    rows: Sequence[Mapping[str, Any]], *, page_count: int | None = None
) -> list[dict[str, Any]]:
    """Time spent per page, straight from WF-018's own arithmetic."""
    return pdf_metrics.dwell_per_page(rows, page_count=page_count)


def drop_off_per_page(revisits: Sequence[Mapping[str, Any]], *, page_count: int) -> dict[str, Any]:
    """Drop off per page, straight from WF-018's own arithmetic."""
    return pdf_metrics.drop_off_per_page(revisits, page_count=page_count)


def summarise_view(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """One view's headline facts: how long, how deep, which pages."""
    pages = [int(row.get("page") or 0) for row in rows if int(row.get("page") or 0) > 0]
    stamps = [pdf_metrics.parse_ts(row.get("occurredAt")) for row in rows]
    stamps = [stamp for stamp in stamps if stamp is not None]
    seconds = round(sum(float(row.get("seconds") or 0.0) for row in rows), 3)
    return {
        "readings": len(rows),
        "pages_viewed": sorted(set(pages)),
        "deepest_page": max(pages) if pages else 0,
        "total_seconds": seconds,
        "started_at": min(stamps).isoformat() if stamps else None,
        "ended_at": max(stamps).isoformat() if stamps else None,
    }


def revisit_index(
    prior_rows: Sequence[Mapping[str, Any]],
    new_rows: Sequence[Mapping[str, Any]],
    *,
    gap_seconds: int = vocab.REVISIT_GAP_SECONDS,
) -> int:
    """Which revisit this view is, 1-based, for one viewer.

    Both lists must already be one viewer's rows. The count only changes when the
    new rows start a reading of their own, so a viewer who reloads mid-deck
    keeps one revisit and a viewer who returns tomorrow gets the next one.
    """
    before = len(group_revisits(prior_rows, gap_seconds=gap_seconds))
    combined = len(group_revisits([*prior_rows, *new_rows], gap_seconds=gap_seconds))
    if combined > before:
        return combined
    return before or 1


# --------------------------------------------------------------------------- #
# The engagement record and the alert
# --------------------------------------------------------------------------- #


def engagement_record(
    link: Mapping[str, Any],
    view: Mapping[str, Any],
    summary: Mapping[str, Any],
    *,
    revisit: int,
    buyer_crm_id: str | None = None,
) -> dict[str, Any]:
    """The engagement record this view writes back to the room's CRM opportunity.

    The six canonical fields are WF-037's names, in WF-037's spelling, so a field
    map that already routes a room event to the CRM routes this one with no new
    row. Everything else is link-shaped detail stored inside ``records.data``:
    the revisit number, the deepest page, and the per-page dwell, which are what
    a rep reads and what WF-071 will build on.
    """
    data = dict(link.get("data") or {})
    occurred = view.get("occurred_at") or now_utc()
    record: dict[str, Any] = {
        "type": vocab.ENGAGEMENT_EVENT_TYPE,
        "occurred_at": occurred.isoformat() if isinstance(occurred, datetime) else str(occurred),
        "asset": data.get("asset_id"),
        "dwell_seconds": summary.get("total_seconds", 0.0),
        "buyer_email": view.get("viewer"),
        # An explicit argument wins; otherwise the view's own id is used, so a
        # CRM-matched viewer needs no extra call at the site.
        "buyer_crm_id": (buyer_crm_id if buyer_crm_id is not None else view.get("buyer_crm_id")),
        "link_id": link.get("id"),
        "revisit": revisit,
        "page_count": len(summary.get("pages_viewed") or []),
        "pages_viewed": list(summary.get("pages_viewed") or []),
        "deepest_page": summary.get("deepest_page"),
        "page_dwell": [
            {"page": row.get("page"), "seconds": row.get("seconds")}
            for row in sorted(
                (dict(row) for row in summary.get("rows") or []),
                key=lambda row: (int(row.get("page") or 0), str(row.get("occurredAt") or "")),
            )
        ],
        "crm_object": vocab.ENGAGEMENT_CRM_OBJECT,
        "crm_system": view.get("crm_system") or data.get("crm_system"),
        "deal_id": view.get("crm_deal_id") or data.get("crm_deal_id"),
        "share_url": data.get("share_url"),
    }
    if view.get("session_id"):
        record["session_id"] = view["session_id"]
    return record


def alert_decisions(
    link: Mapping[str, Any],
    view: Mapping[str, Any],
    summary: Mapping[str, Any],
    *,
    prior_views: int,
    revisit: int,
) -> list[dict[str, Any]]:
    """Every rep alert this view raises, most specific first.

    The researched automation is "auto-notify rep on N-th page view or on return
    visit". So:

    * a viewer's **first** view raises ``first_view``, because the user flow says
      "the rep is alerted" on the share;
    * a viewer who **returns** raises ``return_visit`` when the link asks to be
      notified on a return;
    * a view that reaches the link's **page threshold** raises
      ``page_threshold`` - the "n-th page view" rule, with ``n`` the link's own
      ``alert_on_page``;
    * a view that dwells long enough raises ``dwell_threshold``.

    The elapsed-time trigger and its default number are this build's, recorded
    in :mod:`.inferences`. Internal viewers raise nothing: a rep previewing
    their own link is not a buyer signal, the rule WF-018 states for every
    metric it computes.
    """
    if view.get("audience") == "internal":
        return []
    data = dict(link.get("data") or {})
    decisions: list[dict[str, Any]] = []

    if prior_views == 0:
        decisions.append(
            {
                "trigger": "first_view",
                "detail": "The first view of this tracked link.",
                "revisit": revisit,
            }
        )
    elif _as_bool(data.get("notify_on_return", True), "notify_on_return", default=True):
        decisions.append(
            {
                "trigger": "return_visit",
                "detail": f"Viewer returned for revisit {revisit}.",
                "revisit": revisit,
            }
        )

    threshold = int(data.get("alert_on_page") or vocab.DEFAULT_ALERT_PAGE_THRESHOLD)
    if int(summary.get("deepest_page") or 0) >= threshold:
        decisions.append(
            {
                "trigger": "page_threshold",
                "detail": f"Viewer reached page {summary.get('deepest_page')} (threshold {threshold}).",
                "revisit": revisit,
            }
        )

    if float(summary.get("total_seconds") or 0.0) >= vocab.DEFAULT_DWELL_ALERT_SECONDS:
        decisions.append(
            {
                "trigger": "dwell_threshold",
                "detail": (
                    f"Viewer spent {round(float(summary.get('total_seconds') or 0.0), 1)}s "
                    f"(threshold {vocab.DEFAULT_DWELL_ALERT_SECONDS}s)."
                ),
                "revisit": revisit,
            }
        )
    return decisions


__all__ = [
    "MAX_LIFETIME_DAYS",
    "MIN_LIFETIME_DAYS",
    "alert_decisions",
    "drop_off_per_page",
    "engagement_record",
    "group_revisits",
    "link_state",
    "normalise_link",
    "normalise_view",
    "now_utc",
    "page_dwell",
    "refuse_document_bodies",
    "require_open",
    "revisit_index",
    "summarise_view",
    "timing_rows",
]
