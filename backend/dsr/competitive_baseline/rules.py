"""WF-122: the pure rules of tiered disclosure.

Every function here is a function of its arguments. None touches the database,
the clock, the network or a framework, so each rule is checkable without a
server and without a fixture. The engine in :mod:`dsr.competitive_baseline.engine`
is the only place these are called with stored state.

The one rule that matters is :func:`disclosure_for`: a viewer at or above a
section's minimum tier sees it, and a viewer below it is shown the section's
``below_tier`` state instead. A grant is the section-level exception to that
rule. Every other function supports it.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from .errors import DisclosureError
from .vocabulary import (
    ACTIVE_STATE,
    BELOW_TIER_STATES,
    DEFAULT_BELOW_TIER,
    DEFAULT_RECIPIENT_TIER,
    EXPIRED_STATE,
    FULL,
    HIDDEN,
    ON_REQUEST,
    RESERVED_KEYS,
    REVOKED_STATE,
    TEASER,
)

# --------------------------------------------------------------------------- #
# Coercion
# --------------------------------------------------------------------------- #


def read_int(value: Any, default: int | None = None) -> int | None:
    """Read a whole number, or ``default`` when the value is not one.

    A JSON body carries tier numbers as numbers, but a policy's tier labels
    arrive with string keys (JSON has no integer keys), so a numeric string is
    read as a number. A bool is refused: ``True`` is not a tier.
    """
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return default
    return default


def as_datetime(value: Any) -> datetime | None:
    """Read an ISO date or timestamp as an aware datetime, or ``None``.

    Returns ``None`` rather than raising for a malformed value, because one
    unreadable expiry on one link must not stop a room from rendering. A link
    with an unreadable expiry simply has no expiry.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def strip_reserved(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """A payload without the keys the store owns.

    The envelope is the only fixed vocabulary the product allows, so a caller
    may not set it through a `data` payload and expect it to stick.
    """
    return {key: value for key, value in dict(payload or {}).items() if key not in RESERVED_KEYS}


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


def validate_section(
    payload: Mapping[str, Any] | None, *, default_below: str = DEFAULT_BELOW_TIER
) -> dict[str, Any]:
    """Normalise a section, or raise with a message for every bad field.

    A field this workflow has never heard of is kept. Teams add fields without a
    migration or a typed column, so validation normalises the fields it knows and
    passes the rest through untouched.
    """
    data = strip_reserved(payload)
    errors: dict[str, str] = {}

    name = str(data.get("name") or "").strip()
    if not name:
        errors["name"] = "Name the section. A room is a tree of named sections."

    min_tier = read_int(data["min_tier"], None) if "min_tier" in data else 0
    if min_tier is None or min_tier < 0:
        errors["min_tier"] = "min_tier must be a whole number of zero or more."
        min_tier = 0

    below = str(data.get("below_tier") or default_below).strip()
    if below not in BELOW_TIER_STATES:
        errors["below_tier"] = "below_tier must be one of " + ", ".join(BELOW_TIER_STATES) + "."
        below = default_below

    order = read_int(data.get("order"), 0)
    documents = data.get("documents")
    if documents is not None and not isinstance(documents, list):
        errors["documents"] = "documents must be a list of names."

    if errors:
        raise DisclosureError("The section could not be read.", errors)

    normalised = dict(data)
    normalised["name"] = name
    normalised["min_tier"] = min_tier
    normalised["below_tier"] = below
    normalised["order"] = order or 0
    if normalised.get("teaser") is not None:
        normalised["teaser"] = str(normalised["teaser"])
    if normalised.get("summary") is not None:
        normalised["summary"] = str(normalised["summary"])
    if documents is not None:
        normalised["documents"] = [str(item) for item in documents]
    return normalised


def validate_recipient(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Normalise a recipient link, or raise with a message for every bad field."""
    data = strip_reserved(payload)
    errors: dict[str, str] = {}

    org = str(data.get("org") or "").strip()
    if not org:
        errors["org"] = "Name the counterparty organisation. One link is issued per organisation."

    tier = read_int(data["tier"], None) if "tier" in data else DEFAULT_RECIPIENT_TIER
    if tier is None or tier < 0:
        errors["tier"] = "tier must be a whole number of zero or more."
        tier = DEFAULT_RECIPIENT_TIER

    expires_at = data.get("expires_at")
    if expires_at not in (None, "") and as_datetime(expires_at) is None:
        errors["expires_at"] = "expires_at must be an ISO 8601 date or timestamp."

    if errors:
        raise DisclosureError("The recipient could not be read.", errors)

    normalised = dict(data)
    normalised["org"] = org
    normalised["tier"] = tier
    for field in ("name", "email"):
        if normalised.get(field) is not None:
            normalised[field] = str(normalised[field])
    if expires_at not in (None, ""):
        normalised["expires_at"] = str(expires_at)
    return normalised


def validate_policy(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Normalise a room's tier policy, or raise with a message per bad field."""
    data = strip_reserved(payload)
    errors: dict[str, str] = {}

    raw_labels = data.get("tier_labels") or {}
    labels: dict[str, str] = {}
    if not isinstance(raw_labels, Mapping):
        errors["tier_labels"] = "tier_labels must be an object of tier number to label."
    else:
        for key, value in raw_labels.items():
            tier = read_int(key, None)
            if tier is None or tier < 0:
                errors["tier_labels"] = f"tier label key {key!r} is not a tier number."
                break
            labels[str(tier)] = str(value)

    default_below = str(data.get("default_below_tier") or DEFAULT_BELOW_TIER).strip()
    if default_below not in BELOW_TIER_STATES:
        errors["default_below_tier"] = (
            "default_below_tier must be one of " + ", ".join(BELOW_TIER_STATES) + "."
        )

    if errors:
        raise DisclosureError("The tier policy could not be read.", errors)
    return {"tier_labels": labels, "default_below_tier": default_below}


# --------------------------------------------------------------------------- #
# Link state
# --------------------------------------------------------------------------- #


def effective_state(recipient: Mapping[str, Any], now: datetime | None = None) -> str:
    """The state of a link, after expiry is applied to the stored state.

    A revoked link is revoked whatever the clock says: revocation is an explicit
    act, not a point in time. Expiry is derived, so a link that passed its
    ``expires_at`` reads as expired without a write.
    """
    stored = str(recipient.get("state") or ACTIVE_STATE)
    if stored == REVOKED_STATE:
        return REVOKED_STATE
    moment = as_datetime(recipient.get("expires_at"))
    if moment is not None and now is not None and moment <= now:
        return EXPIRED_STATE
    return ACTIVE_STATE


def is_live(recipient: Mapping[str, Any], now: datetime | None = None) -> bool:
    """Is the link active, so its tier discloses anything?"""
    return effective_state(recipient, now) == ACTIVE_STATE


# --------------------------------------------------------------------------- #
# Disclosure
# --------------------------------------------------------------------------- #


def disclosure_for(section: Mapping[str, Any], viewer_tier: int, *, granted: bool = False) -> str:
    """What a viewer at ``viewer_tier`` is shown of ``section``.

    A full view when the viewer is at or above the section's minimum tier, or
    when the section is granted to them regardless of tier. Otherwise the
    section's ``below_tier`` state. The state is read from the section rather
    than decided here, so the seller's choice of teaser, hidden or on-request is
    what a below-tier viewer gets.
    """
    min_tier = read_int(section.get("min_tier"), 0) or 0
    if granted or viewer_tier >= min_tier:
        return FULL
    below = str(section.get("below_tier") or HIDDEN)
    return below if below in BELOW_TIER_STATES else HIDDEN


def render_section(
    section: Mapping[str, Any], viewer_tier: int, *, granted: bool = False
) -> dict[str, Any]:
    """One section as a viewer sees it, with contents only for a full view.

    A hidden section is still returned here, so a caller can count it; the room
    renderer drops it. A teaser carries the teaser text and never the contents.
    """
    min_tier = read_int(section.get("min_tier"), 0) or 0
    disclosure = disclosure_for(section, viewer_tier, granted=granted)
    row: dict[str, Any] = {
        "id": section.get("id"),
        "name": section.get("name"),
        "min_tier": min_tier,
        "disclosure": disclosure,
        "order": read_int(section.get("order"), 0) or 0,
    }

    if disclosure == FULL:
        if section.get("summary") is not None:
            row["summary"] = section.get("summary")
        if section.get("documents") is not None:
            row["documents"] = list(section.get("documents") or [])
        if granted and viewer_tier < min_tier:
            row["granted"] = True
    elif disclosure == TEASER:
        row["teaser"] = section.get("teaser") or "This section is not unlocked yet."
    elif disclosure == ON_REQUEST:
        row["message"] = "Available on request."
    return row


def render_room(
    sections: Iterable[Mapping[str, Any]],
    viewer_tier: int,
    granted_ids: Iterable[str] = frozenset(),
) -> list[dict[str, Any]]:
    """The room as a viewer sees it, in the seller's order.

    A hidden section is absent from the list, not present with a flag. A page
    that greyed a row out would still ship its name in the response, and a
    below-tier recipient must not receive what they may not see.
    """
    grants = {str(granted) for granted in granted_ids}
    ordered = sorted(
        sections,
        key=lambda section: (
            read_int(section.get("order"), 0) or 0,
            str(section.get("created_at") or ""),
        ),
    )
    rows: list[dict[str, Any]] = []
    for section in ordered:
        row = render_section(section, viewer_tier, granted=str(section.get("id")) in grants)
        if row["disclosure"] == HIDDEN:
            continue
        rows.append(row)
    return rows


# --------------------------------------------------------------------------- #
# Access links
# --------------------------------------------------------------------------- #


def new_token() -> str:
    """A fresh, URL-safe access token, issued once and stored only as a hash."""
    return secrets.token_urlsafe(24)


def token_hash(token: str) -> str:
    """The SHA-256 hex digest of a token, which is what the store keeps."""
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def token_hint(token: str) -> str:
    """The last four characters of a token, for a seller to tell two links apart."""
    text = str(token)
    return text[-4:] if len(text) >= 4 else text


def tier_label(labels: Mapping[str, Any], tier: int) -> str:
    """The seller's name for a tier, or a neutral fallback the API did not send."""
    return str(labels.get(str(tier)) or f"Tier {tier}")
