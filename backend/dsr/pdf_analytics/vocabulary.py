"""The vocabulary WF-018 accepts, and where each part of it came from.

Three sources, cited in
``docs/research/digital-sales-room-workflows/wf/WF-018.md``:

* ``GET https://api.dock.us/v1/assets`` - the asset object shape, which is why
  :data:`ASSET_SNAPSHOT_FIELDS` is exactly the field list the research quotes
  for the ``asset.viewed`` / ``asset.downloaded`` payload.
* ``https://developers.dock.us/webhooks/event-types.md`` - the event names, which
  is why :data:`WEBHOOK_EVENT_TYPES` is validated rather than free text.
* ``https://help.dock.us/en/articles/6989922-content-analytics`` - the three
  analytics blocks and the external-only rule, which live in :mod:`.metrics` and
  :mod:`.book` rather than here.

Two decisions worth stating, because both are visible in the surface:

**The researched spelling is the stored key.** ``shareUrl``, ``isInternal``,
``downloadEnabled`` and ``trackingEnabled`` are stored with the capital letters
the source uses, because the source is the contract and a team correlating our
rows with a real ``asset.viewed`` payload should not have to rename anything.
Snake-case spellings are accepted on input and normalised, so a caller writing
Python does not have to remember which product they are talking to.

**Anything the research does not name is kept verbatim.** :func:`normalise_snapshot`
returns the caller's unknown keys untouched. A team that needs
``pricingTier`` on an asset must not need a migration, a redeploy, or a change
to this file - which is the same rule every other record in this product obeys.
"""

from __future__ import annotations

from typing import Any, Mapping

from .errors import UnknownEventType, ValidationError

#: ``type`` values the research names for the library. A ``pdf`` gets PDF
#: Analytics, a ``video`` gets Video Analytics.
ASSET_TYPES: tuple[str, ...] = ("pdf", "video")

#: The webhook event types. ``asset.viewed`` and ``asset.downloaded`` are named
#: in WF-018's own ``apis_hit`` line.
#:
#: ``asset.shared`` is the third, and the only one not quoted in WF-018. It is
#: here because WF-018's own evidence quotes a metric that cannot be computed
#: without it: "The one exception is 'Shares' which is an internal metric
#: showing how often the internal team shares a specific asset." The *metric* is
#: sourced; the *event name* is inferred, and is recorded as such in
#: :mod:`.inferences`.
WEBHOOK_EVENT_TYPES: tuple[str, ...] = ("asset.viewed", "asset.downloaded", "asset.shared")

#: The asset snapshot fields, in the order the research lists them.
ASSET_SNAPSHOT_FIELDS: tuple[str, ...] = (
    "name",
    "type",
    "shareUrl",
    "isInternal",
    "tags",
    "downloadEnabled",
    "trackingEnabled",
)

#: Who an interaction came from. ``external`` is a buyer or customer;
#: ``internal`` is the selling team.
#:
#: Sourced: "Dock's analytics only show engagement from external users (i.e.
#: buyers and customers). The one exception is 'Shares' which is an internal
#: metric". Every metric in :mod:`.metrics` is filtered on this, and
#: :data:`INTERNAL_ONLY_METRICS` is the one that inverts.
AUDIENCES: tuple[str, ...] = ("external", "internal")

#: The metrics the research says are internal, and therefore the only ones that
#: count ``internal`` engagement. Everything else is external-only.
INTERNAL_ONLY_METRICS: tuple[str, ...] = ("shares",)

#: Bar-chart grains for the Core Analytics series. The research names "Core
#: Analytics bar charts" without an axis vocabulary, so this is an inference -
#: see :mod:`.inferences`.
GRAINS: tuple[str, ...] = ("day", "week", "month")

#: "For multi-page PDFs, we're able to show two additional metrics." A one-page
#: PDF has no per-page curve.
MIN_PAGES_FOR_PDF_ANALYTICS = 2

#: A reader who pauses for longer than this between two page timings is treated
#: as starting a new reading session. Inferred; see :mod:`.inferences`.
SESSION_GAP_SECONDS = 1800

#: Why PDF Analytics is unavailable, as the codes a client can branch on.
PDF_UNAVAILABLE_REASONS: tuple[str, ...] = ("unknown_asset_type", "single_page")

#: Why Video Analytics is unavailable.
VIDEO_UNAVAILABLE_REASONS: tuple[str, ...] = ("unknown_asset_type", "not_self_hosted")

#: ``(input key, stored key)`` pairs for the aliases accepted on input. The
#: stored key is always the researched spelling.
_ALIASES: dict[str, str] = {
    "share_url": "shareUrl",
    "shareurl": "shareUrl",
    "is_internal": "isInternal",
    "download_enabled": "downloadEnabled",
    "downloadenabled": "downloadEnabled",
    "tracking_enabled": "trackingEnabled",
    "trackingenabled": "trackingEnabled",
    "external_id": "externalId",
    "externalid": "externalId",
    "asset_id": "externalId",
    "assetid": "externalId",
    "page_count": "pageCount",
    "pagecount": "pageCount",
    "self_hosted": "selfHosted",
    "selfhosted": "selfHosted",
    "watch_url": "playbackUrl",
    "watchurl": "playbackUrl",
}


def _as_bool(value: Any, field: str) -> bool:
    """Coerce a JSON boolean, refusing anything that is not one.

    ``bool("false")`` is ``True``, so a caller sending the *string* ``"false"``
    would silently mark a trackable asset untrackable. That is a real hazard
    on a field the whole workflow gates on, so the strings are read rather than
    truth-tested.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "yes", "1"):
            return True
        if lowered in ("false", "no", "0"):
            return False
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    raise ValidationError(f"{field} must be a boolean, got {value!r}", field=field)


def _as_int(value: Any, field: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or value is None:
        raise ValidationError(f"{field} must be a whole number, got {value!r}", field=field)
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            f"{field} must be a whole number, got {value!r}", field=field
        ) from exc
    if parsed < minimum:
        raise ValidationError(f"{field} must be at least {minimum}, got {parsed}", field=field)
    return parsed


def _as_seconds(value: Any, field: str) -> float:
    if isinstance(value, bool) or value is None:
        raise ValidationError(f"{field} must be a number of seconds, got {value!r}", field=field)
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            f"{field} must be a number of seconds, got {value!r}", field=field
        ) from exc
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        raise ValidationError(f"{field} must be a finite number of seconds", field=field)
    if parsed <= 0:
        raise ValidationError(f"{field} must be greater than zero, got {parsed}", field=field)
    return parsed


def as_seconds(value: Any, field: str = "seconds") -> float:
    """Public form of the dwell validator, so a route can use the same rule."""
    return _as_seconds(value, field)


def as_int(value: Any, field: str, *, minimum: int = 0) -> int:
    return _as_int(value, field, minimum=minimum)


def require_asset_type(value: Any) -> str:
    kind = str(value or "").strip().lower()
    if kind not in ASSET_TYPES:
        raise ValidationError(
            f"type must be one of {list(ASSET_TYPES)}, got {value!r}",
            field="type",
            allowed=list(ASSET_TYPES),
        )
    return kind


def require_event_type(value: Any) -> str:
    name = str(value or "").strip()
    if name not in WEBHOOK_EVENT_TYPES:
        raise UnknownEventType(
            f"event must be one of {list(WEBHOOK_EVENT_TYPES)}, got {value!r}",
            field="event",
            allowed=list(WEBHOOK_EVENT_TYPES),
        )
    return name


def require_grain(value: Any) -> str:
    grain = str(value or "").strip().lower()
    if grain not in GRAINS:
        raise ValidationError(
            f"grain must be one of {list(GRAINS)}, got {value!r}",
            field="grain",
            allowed=list(GRAINS),
        )
    return grain


def normalise_snapshot(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    """Validate and canonicalise one asset snapshot.

    Keeps every key the caller sent, in the researched spelling where the
    research names one, and passes anything else through untouched. The
    validation is only over the fields this workflow *gates on*; a snapshot
    missing ``shareUrl`` or ``tags`` is perfectly valid, because the research
    lists them as what the payload carries, not as what it requires.
    """
    fields = dict(raw or {})
    clean: dict[str, Any] = {}
    for key, value in fields.items():
        clean[_ALIASES.get(key, _ALIASES.get(str(key).lower(), key))] = value

    name = str(clean.get("name") or "").strip()
    if not name:
        raise ValidationError("asset name is required", field="name")
    clean["name"] = name

    clean["type"] = require_asset_type(clean.get("type"))

    for flag in ("isInternal", "downloadEnabled", "trackingEnabled", "selfHosted"):
        if flag in clean and clean[flag] is not None:
            clean[flag] = _as_bool(clean[flag], flag)

    if clean.get("tags") is not None:
        tags = clean["tags"]
        if isinstance(tags, str):
            tags = [part.strip() for part in tags.split(",") if part.strip()]
        if not isinstance(tags, (list, tuple)):
            raise ValidationError("tags must be a list of strings", field="tags")
        clean["tags"] = [str(tag).strip() for tag in tags if str(tag).strip()]

    if clean.get("pageCount") is not None:
        clean["pageCount"] = _as_int(clean["pageCount"], "pageCount", minimum=1)

    if clean.get("externalId") is not None:
        clean["externalId"] = str(clean["externalId"]).strip() or None
        if clean["externalId"] is None:
            clean.pop("externalId")

    # The researched defaults, written explicitly rather than left implicit, so
    # a reader of a stored row never has to guess what an absent flag meant.
    clean.setdefault("isInternal", False)
    clean.setdefault("downloadEnabled", True)
    clean.setdefault("trackingEnabled", True)
    # A video record in this library is self-hosted unless it says otherwise;
    # the research scopes watch time to "self-hosted videos" but names no field
    # that distinguishes one, which is an inference (see :mod:`.inferences`).
    if clean["type"] == "video":
        clean.setdefault("selfHosted", True)

    # No migration and no typed column: everything stays inside ``records.data``.
    return clean


def audience_of(raw: Mapping[str, Any] | None) -> str:
    """Decide whether an interaction counts as buyer engagement.

    The single rule the research states about who is counted: "Dock's analytics
    only show engagement from external users (i.e. buyers and customers). The
    one exception is 'Shares'".

    ``isInternal`` is the flag the researched payload already carries, so it is
    the one read here. An interaction that does not declare it is external,
    because the documented entry point for anonymous engagement is a trackable
    asset link opened by a buyer, who is by definition not the internal team.
    """
    return "internal" if (raw or {}).get("isInternal") else "external"


def is_internal_metric(metric: str) -> bool:
    """The one metric that counts internal engagement."""
    return metric in INTERNAL_ONLY_METRICS
