"""WF-049's core transformation, in :func:`normalise`.

The research states it in one sentence: *heterogeneous vendor quota models
(daily+burst, per-10s, per-app, per-account) normalise into one "remaining
today / remaining this window" pair*. Everything in this module serves that
sentence. Three vendors, four readable surfaces, one output shape:

    {"daily":  {"known": .., "max": .., "used": .., "remaining": .., "remaining_pct": ..},
     "window": {"known": .., "max": .., "used": .., "remaining": .., "window_seconds": ..,
                "remaining_pct": ..}}

The reading rules come from the vendors' own words, quoted beside each parser:

* **Salesforce** reports daily org-wide caps. The ``Sforce-Limit-Info`` header
  rides every REST call as ``api-usage=<used>/<total>``, and the
  ``GET /limits/`` resource returns ``{"name", "max", "remaining"}`` rows. Both
  land in the *daily* half; Salesforce has no burst window in the source set.
* **HubSpot** reports both halves on every call:
  ``X-HubSpot-RateLimit-Max`` / ``-Remaining`` / ``-Interval-Milliseconds`` are
  the burst window, and ``-Daily`` / ``-Daily-Remaining`` are the daily cap -
  except on OAuth, where "**this header is not included**", so the daily half is
  *unknown*, never zero.
* **Dataverse** has no sourced numeric quota at all. The research says so
  itself - the Service Protection limit table "was not found at a readable
  URL" - so a Dataverse quota reading is refused with that gap named rather
  than answered with invented numbers. What Dataverse does provide is the
  change-tracking audit and the ``globalmetadataversion`` drift signal, which
  live in :func:`normalise_change_tracking`.

Nothing here opens a socket. Every payload is handed in by the connector -
the room holds no vendor credentials - so the parsers are pure and the whole
module is testable without a network.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping

from dsr.integ_monitor.errors import InvalidQuotaSurface
from dsr.integ_monitor.timestamps import iso, observed_at
from dsr.integ_monitor.vocabulary import QUOTA_SURFACES, require_vendor

#: The two halves of the normalised pair, in the research's own words.
HALVES = ("daily", "window")


# --------------------------------------------------------------------------- #
# The normalised pair
# --------------------------------------------------------------------------- #


def half(
    *,
    max_value: float | None = None,
    used: float | None = None,
    remaining: float | None = None,
    window_seconds: float | None = None,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    """One half of the pair, filling in whichever two of max/used/remaining exist.

    A half is ``known`` when at least *remaining* or *max* could be read, so a
    caller can tell "the vendor said nothing" from "the vendor said something
    this room did not fully understand". ``remaining_pct`` is computed only when
    both ends of the division are actually there - a percentage against a
    guessed maximum would be a number that looks authoritative and is not.
    """
    if remaining is None and used is not None and max_value is not None:
        remaining = max_value - used
    if max_value is None and remaining is not None and used is not None:
        max_value = remaining + used
    if remaining is not None and max_value is not None and remaining > max_value:
        raise InvalidQuotaSurface(
            f"remaining ({remaining}) is greater than max ({max_value}); a quota surface "
            "that says so is lying, and the room does not record it"
        )
    if remaining is not None and remaining < 0:
        raise InvalidQuotaSurface(
            f"remaining ({remaining}) is negative; the room does not record it"
        )
    known = remaining is not None or max_value is not None or used is not None
    return {
        "known": known,
        "max": max_value,
        "used": used,
        "remaining": remaining,
        "remaining_pct": (
            round(100.0 * remaining / max_value, 2)
            if remaining is not None and max_value not in (None, 0)
            else None
        ),
        "window_seconds": window_seconds,
        "notes": list(notes or []),
    }


def empty_half(note: str) -> dict[str, Any]:
    """The unknown half: the vendor said nothing this room can read."""
    return {
        "known": False,
        "max": None,
        "used": None,
        "remaining": None,
        "remaining_pct": None,
        "window_seconds": None,
        "notes": [note],
    }


# --------------------------------------------------------------------------- #
# Salesforce
# --------------------------------------------------------------------------- #


def _sf_from_header(payload: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """The daily half from ``Sforce-Limit-Info: api-usage=<used>/<total>``.

    [sourced] "Sforce-Limit-Info" ... "you can use the information to monitor
    your API usage". The limit it reports is the org's daily REST API request
    cap, so it lands in the *daily* half.
    """
    value = str(payload.get("header") or payload.get("value") or "").strip()
    if not value:
        raise InvalidQuotaSurface(
            "the Sforce-Limit-Info reading carries no header value; pass the header as "
            '{"header": "api-usage=123/500000"}'
        )
    if ":" in value.split("=")[0]:
        value = value.partition(":")[1].strip() if value.count(":") == 1 else value
    notes: list[str] = []
    segments = [part.strip() for part in value.split(";") if part.strip()]
    usage = next(
        (part for part in segments if part.lower().startswith("api-usage")),
        None,
    )
    for part in segments:
        if part is not usage:
            notes.append(f"ignored segment {part!r}: the source set documents api-usage only")
    if usage is None:
        raise InvalidQuotaSurface(
            f"no api-usage segment in {value!r}; the header reads "
            "Sforce-Limit-Info: api-usage=<used>/<total>"
        )
    pair = usage.partition("=")[2]
    used_text, _, total_text = pair.partition("/")
    try:
        used = float(used_text)
        total = float(total_text)
    except ValueError as exc:
        raise InvalidQuotaSurface(
            f"cannot read {usage!r}: expected api-usage=<used>/<total> ({exc})"
        ) from exc
    daily = half(max_value=total, used=used)
    daily["notes"] = daily["notes"] + notes
    return daily, notes


def _sf_from_limits(payload: Mapping[str, Any], limit_name: str | None) -> dict[str, Any]:
    """The daily half from ``GET /services/data/vXX.X/limits/``.

    [sourced] "For each limit, this resource returns the maximum allocation and
    the remaining allocation based on usage."

    ``limit_name`` is the connector's own declared key into the org's limit
    table - the room cannot know which row a connector wants, so the connector
    says, and an answer without that row is *unknown with a note naming the
    rows that were there* rather than a silent zero.
    """
    body = payload.get("body", payload)
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except json.JSONDecodeError as exc:
            raise InvalidQuotaSurface(f"the limits body is not valid JSON ({exc})") from exc
    rows = body.get("limits", body) if isinstance(body, Mapping) else body
    if not isinstance(rows, list) or not rows:
        raise InvalidQuotaSurface(
            "the limits resource answer is not a list of {name, max, remaining} rows"
        )
    parsed: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping) or not row.get("name"):
            continue
        try:
            entry = {
                "name": str(row["name"]),
                "max": float(row["max"]),
                "remaining": float(row["remaining"]),
            }
        except (TypeError, ValueError, KeyError):
            continue
        parsed.append(entry)
    if not parsed:
        raise InvalidQuotaSurface(
            "the limits resource answer carried no row with readable name, max and remaining"
        )
    wanted = str(limit_name or "").strip().lower()
    match = next((entry for entry in parsed if wanted and wanted in entry["name"].lower()), None)
    if match is None:
        names = ", ".join(entry["name"] for entry in parsed)
        return {
            "known": False,
            "max": None,
            "used": None,
            "remaining": None,
            "remaining_pct": None,
            "window_seconds": None,
            "notes": [
                f"the org's limit table carries {names} and no row matches the connector's "
                f"declared limit_name {limit_name!r}; set limit_name to one of them"
            ],
        }
    return half(max_value=match["max"], remaining=match["remaining"])


# --------------------------------------------------------------------------- #
# HubSpot
# --------------------------------------------------------------------------- #


def _hs_header(payload: Mapping[str, Any], name: str) -> str | None:
    """A rate-limit header value, case-insensitively."""
    wanted = name.lower()
    for key, value in (payload.get("headers") or {}).items():
        if str(key).lower() == wanted:
            return str(value).strip()
    return None


def _number(value: str | None, *, name: str) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError as exc:
        raise InvalidQuotaSurface(f"{name} is not a number: {value!r}") from exc


def _hs_from_headers(payload: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Both halves from the ``X-HubSpot-RateLimit-*`` headers.

    [sourced] "X-HubSpot-RateLimit-Interval-Milliseconds | The window of time
    that the X-HubSpot-RateLimit-Max and X-HubSpot-RateLimit-Remaining headers
    apply to. ... a value of 10000 would be a window of 10 seconds", and
    "X-HubSpot-RateLimit-Daily | ... Note that this header is not included in
    the response to API requests authorized using OAuth".

    That note is the whole of the daily rule for OAuth connections: absent
    daily headers mean *unknown*, never zero, and the observation says so.
    """
    maximum = _number(
        _hs_header(payload, "X-HubSpot-RateLimit-Max"), name="X-HubSpot-RateLimit-Max"
    )
    remaining = _number(
        _hs_header(payload, "X-HubSpot-RateLimit-Remaining"), name="X-HubSpot-RateLimit-Remaining"
    )
    interval = _number(
        _hs_header(payload, "X-HubSpot-RateLimit-Interval-Milliseconds"),
        name="X-HubSpot-RateLimit-Interval-Milliseconds",
    )
    daily_max = _number(
        _hs_header(payload, "X-HubSpot-RateLimit-Daily"), name="X-HubSpot-RateLimit-Daily"
    )
    daily_remaining = _number(
        _hs_header(payload, "X-HubSpot-RateLimit-Daily-Remaining"),
        name="X-HubSpot-RateLimit-Daily-Remaining",
    )

    if maximum is not None and remaining is not None:
        window_seconds = interval / 1000.0 if interval is not None else None
        window = half(max_value=maximum, remaining=remaining, window_seconds=window_seconds)
        if interval is None:
            window["notes"].append(
                "no X-HubSpot-RateLimit-Interval-Milliseconds header: the window length is "
                "unknown, so a percentage of the window is the only reading available"
            )
    elif maximum is not None or remaining is not None:
        raise InvalidQuotaSurface(
            "the Max and Remaining rate-limit headers arrive together; one without the "
            "other cannot form a window"
        )
    else:
        window = empty_half("no X-HubSpot-RateLimit-Max/Remaining headers in this response")

    if daily_max is not None and daily_remaining is not None:
        daily = half(max_value=daily_max, remaining=daily_remaining)
    elif daily_max is None and daily_remaining is None:
        daily = empty_half(
            "no X-HubSpot-RateLimit-Daily headers: not included in responses to API "
            "requests authorized using OAuth"
        )
    else:
        # One daily header without the other. The pair cannot be completed but
        # what the vendor did say is kept, so the dashboard reports a partial
        # reading rather than nothing.
        present, absent = ("max", "remaining") if daily_max is not None else ("remaining", "max")
        value = daily_max if present == "max" else daily_remaining
        daily = half(
            max_value=value if present == "max" else None,
            remaining=value if present == "remaining" else None,
        )
        daily["notes"].append(
            f"only the Daily-{present} header was sent; the Daily-{absent} "
            "header was not, so this half is partial"
        )
    return daily, window


def _hs_from_account(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The daily half from the account-information endpoint, when it says one.

    [sourced] "HubSpot's account information API endpoints provide account
    configuration and usage data ... the daily API usage and limits for legacy
    private apps."

    The research's own gap is that the usage fields "were not enumerated in the
    page read", so this parser reads the two shapes the endpoint plausibly
    serves - a ``daily`` object, or top-level ``dailyUsageCount`` /
    ``dailyLimit`` keys - and otherwise reports **unknown with the gap quoted**
    rather than a number the room made up.
    """
    body = payload.get("body", payload)
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except json.JSONDecodeError as exc:
            raise InvalidQuotaSurface(
                f"the account-information body is not valid JSON ({exc})"
            ) from exc
    if not isinstance(body, Mapping):
        raise InvalidQuotaSurface("the account-information body must be a JSON object")

    nested = body.get("daily")
    if isinstance(nested, Mapping):
        maximum = nested.get("limit", nested.get("max"))
        remaining = nested.get("remaining")
        used = nested.get("used")
        if remaining is not None or maximum is not None:
            return half(
                max_value=_optional_number(maximum, "daily.limit"),
                remaining=_optional_number(remaining, "daily.remaining"),
                used=_optional_number(used, "daily.used"),
            )

    top_used, top_max = body.get("dailyUsageCount"), body.get("dailyLimit")
    if top_used is not None or top_max is not None:
        return half(
            max_value=_optional_number(top_max, "dailyLimit"),
            used=_optional_number(top_used, "dailyUsageCount"),
        )

    result = empty_half(
        "the account-information body carried no daily usage fields this room can read; "
        "HubSpot's newer account-information usage fields were not enumerated in the "
        "research's page read"
    )
    result["verbatim"] = body
    return result


def _optional_number(value: Any, name: str) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise InvalidQuotaSurface(f"{name} is not a number: {value!r}") from exc


# --------------------------------------------------------------------------- #
# The entry point
# --------------------------------------------------------------------------- #


def normalise(
    vendor: str,
    surface: str,
    payload: Mapping[str, Any] | None,
    *,
    limit_name: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """One vendor answer in, one normalised reading out.

    ``surface`` is one of the vendor's own documented surfaces - served at
    ``/vocabulary`` under ``quotas.surfaces``. ``payload`` carries the answer:
    a header value for the header surfaces, the body for the resource ones.

    The reading is *stored, not interpreted*: the alert thresholds and the
    dashboard read this shape later, which is what makes a reading auditable
    after the fact - the observation carries the vendor, the surface, the
    instant it was taken, and the pair, and nothing else was involved.
    """
    vendor = require_vendor(vendor)
    payload = dict(payload or {})
    surface_id = str(surface or "").strip()
    documented = {entry["id"] for entry in QUOTA_SURFACES.get(vendor, ())}
    notes: list[str] = []
    verbatim = None

    if surface_id not in documented:
        if vendor == "dataverse":
            raise InvalidQuotaSurface(
                f"dataverse has no sourced quota surface ({surface_id!r}); the research's own "
                "gap says its Service Protection limit table was not found at a readable URL, "
                "and the room does not answer with invented numbers. Record the change-tracking "
                "audit instead."
            )
        raise InvalidQuotaSurface(
            f"surface {surface_id!r} is not one of {vendor}'s documented quota surfaces "
            f"({', '.join(sorted(documented)) or 'none'})"
        )

    if vendor == "salesforce" and surface_id == "limit_info_header":
        daily, ignored = _sf_from_header(payload)
        window = empty_half("Salesforce reports no burst window on this surface")
        notes.extend(ignored)
    elif vendor == "salesforce" and surface_id == "limits_resource":
        daily = _sf_from_limits(payload, limit_name)
        window = empty_half("Salesforce reports no burst window on this surface")
        verbatim = _verbatim_limits(payload)
    elif vendor == "salesforce" and surface_id == "event_usage_metric":
        # [sourced] the object is named and the sentence about Enhanced Usage
        # Metrics is quoted, but its record shape is not, so this stores the
        # rows verbatim and answers *unknown* rather than inventing keys.
        rows = _event_usage_rows(payload)
        daily = empty_half(
            "the research names the PlatformEventUsageMetric object but quotes no record "
            "shape; the rows are stored verbatim and no quota pair is computed"
        )
        daily["verbatim"] = rows
        window = empty_half("no window model is documented for event-delivery usage")
    elif vendor == "hubspot" and surface_id == "rate_limit_headers":
        daily, window = _hs_from_headers(payload)
    elif vendor == "hubspot" and surface_id == "account_information":
        daily = _hs_from_account(payload)
        window = empty_half("the account-information endpoint reports no burst window")
    else:  # pragma: no cover - the branches above are exhaustive per VENDORS
        raise InvalidQuotaSurface(f"unhandled surface {vendor}/{surface_id}")

    moment = observed_at(payload, now=now) if payload else (now or datetime.now(timezone.utc))
    return {
        "vendor": vendor,
        "surface": surface_id,
        "observed_at": iso(moment),
        "daily": daily,
        "window": window,
        "notes": notes,
    }


def _verbatim_limits(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    body = payload.get("body", payload)
    rows = body.get("limits", body) if isinstance(body, Mapping) else body
    return [
        {"name": row.get("name"), "max": row.get("max"), "remaining": row.get("remaining")}
        for row in (rows or [])
        if isinstance(row, Mapping) and row.get("name")
    ]


def _event_usage_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    body = payload.get("body", payload)
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except json.JSONDecodeError as exc:
            raise InvalidQuotaSurface(f"the usage-metric body is not valid JSON ({exc})") from exc
    rows = body.get("records", body) if isinstance(body, Mapping) else body
    if not isinstance(rows, list):
        raise InvalidQuotaSurface("the usage-metric body must be a list of records")
    return [dict(row) for row in rows if isinstance(row, Mapping)]


__all__ = [
    "HALVES",
    "half",
    "empty_half",
    "normalise",
]
