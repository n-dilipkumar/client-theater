"""What the vendor's own quota headers say, read for one purpose.

The research's step 2: "Every response is inspected for the vendor's quota headers
and written to the room's **Quota** meter." The headers are:

*Salesforce*, on every REST call::

    Sforce-Limit-Info: api-usage=10018/100000; api-bursts=1/750

*HubSpot*, on every call::

    X-HubSpot-RateLimit-Max: 100
    X-HubSpot-RateLimit-Remaining: 87
    X-HubSpot-RateLimit-Interval-Milliseconds: 10000
    X-HubSpot-RateLimit-Daily: 250000
    X-HubSpot-RateLimit-Daily-Remaining: 249812

**Why this module is not a second quota reader.** ``dsr.integ_monitor.quota``
already parses both vendors' headers into a normalised ``{daily, window}`` pair
for the health dashboard, and that parser is HubSpot-specific and stays where it
is. The two modules read *disjoint* things and neither reinterprets the other's
output:

* :mod:`dsr.integ_monitor.quota` answers "what does this vendor's quota model say,
  as a reading a dashboard can chart" - and it refuses a vendor whose numbers it
  cannot source.
* this module answers the one question a token bucket has to answer - "how much
  room is left, and how soon does it come back" - and returns ``None`` for
  anything a bucket cannot use.

So a caller that wants the dashboard pair calls WF-049. A caller that wants to
know whether to spend a token reads this. Nothing here is a general quota
normaliser and it does not try to be.

**A header the room cannot read is dropped, not guessed.** ``half()`` is never
fabricated: a partial answer reports which half arrived and notes the other as
absent, because a percentage of a maximum the room made up is a number that looks
authoritative and is not.
"""

from __future__ import annotations

from typing import Any, Mapping

from dsr.throttle.classify import THROTTLE_STATUS, header


def _number(value: str | None) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _pair(used_text: str | None, total_text: str | None) -> dict[str, Any]:
    """``{used, total, remaining}`` from a ``<used>/<total>`` header fragment."""
    used = _number(used_text)
    total = _number(total_text)
    remaining = None if used is None or total is None else total - used
    return {"used": used, "total": total, "remaining": remaining}


def sforce_limit_info(headers: Mapping[str, Any] | None) -> dict[str, Any]:
    """Read ``Sforce-Limit-Info``, which is two independent segments.

    [sourced] "**Field name**: `Sforce-Limit-Info` ... `api-usage`-Specifies the
    daily API usage for the organization against which the call was made. The
    first number is the number of API calls used, and the second number is the API
    limit for the organization. ... **Example**: `Sforce-Limit-Info:
    api-usage=10018/100000; api-bursts=1/750`"

    Both segments are read because the daily cap and the burst allowance are
    different budgets, and a Salesforce connection has to be able to say which one
    is nearly gone. A segment this build has no reading for is reported under
    ``ignored`` with its text, so nothing is silently dropped.
    """
    raw = header(headers, "Sforce-Limit-Info")
    reading: dict[str, Any] = {
        "source": "Sforce-Limit-Info",
        "raw": raw,
        "daily": {"used": None, "total": None, "remaining": None},
        "burst": {"used": None, "total": None, "remaining": None},
        "ignored": [],
        "known": False,
    }
    if not raw:
        reading["ignored"].append("the response carried no Sforce-Limit-Info header")
        return reading

    for segment in (part.strip() for part in str(raw).split(";")):
        if not segment:
            continue
        name, separator, value = segment.partition("=")
        if not separator:
            reading["ignored"].append(f"ignored segment {segment!r}: it is not a name=value pair")
            continue
        used_text, slash, total_text = value.partition("/")
        if not slash:
            reading["ignored"].append(
                f"ignored segment {segment!r}: the researched format is name=<used>/<total>"
            )
            continue
        key = name.strip().lower()
        if key == "api-usage":
            reading["daily"] = _pair(used_text, total_text)
        elif key == "api-bursts":
            reading["burst"] = _pair(used_text, total_text)
        else:
            reading["ignored"].append(
                f"ignored segment {segment!r}: the source set documents api-usage and api-bursts only"
            )

    reading["known"] = reading["daily"]["remaining"] is not None
    return reading


def hubspot_rate_limit(headers: Mapping[str, Any] | None) -> dict[str, Any]:
    """Read the ``X-HubSpot-RateLimit-*`` family, both halves.

    [sourced] "X-HubSpot-RateLimit-Interval-Milliseconds | The window of time
    that the X-HubSpot-RateLimit-Max and X-HubSpot-RateLimit-Remaining headers
    apply to. ... a value of 10000 would be a window of 10 seconds" and
    "X-HubSpot-RateLimit-Daily | ... Note that this header is not included in the
    response to API requests authorized using OAuth".

    That note is why the daily half is ``known: false`` rather than zero when the
    headers are absent. A connection authorised with OAuth will never send them,
    and reporting a daily budget of zero would refuse every call forever.
    """
    maximum = _number(header(headers, "X-HubSpot-RateLimit-Max"))
    remaining = _number(header(headers, "X-HubSpot-RateLimit-Remaining"))
    interval = _number(header(headers, "X-HubSpot-RateLimit-Interval-Milliseconds"))
    daily_max = _number(header(headers, "X-HubSpot-RateLimit-Daily"))
    daily_remaining = _number(header(headers, "X-HubSpot-RateLimit-Daily-Remaining"))

    window = {
        "used": None if maximum is None or remaining is None else maximum - remaining,
        "total": maximum,
        "remaining": remaining,
        "window_seconds": interval / 1000.0 if interval is not None else None,
    }
    daily = {"used": None, "total": daily_max, "remaining": daily_remaining}
    return {
        "source": "X-HubSpot-RateLimit-*",
        "raw": {
            key: value
            for key, value in (headers or {}).items()
            if str(key).lower().startswith("x-hubspot-ratelimit-")
        },
        "window": window,
        "daily": daily,
        "known": remaining is not None or daily_remaining is not None,
    }


def read(vendor: str, headers: Mapping[str, Any] | None) -> dict[str, Any]:
    """Whatever this vendor's own headers say, in one shape.

    A vendor with no header-based quota surface - Dataverse, whose numbers the
    research could not source - returns ``known: false`` and the gap, which is the
    answer rather than an absence of one.
    """
    name = str(vendor or "").strip().lower()
    if name == "salesforce":
        return sforce_limit_info(headers)
    if name == "hubspot":
        return hubspot_rate_limit(headers)
    return {
        "source": None,
        "raw": None,
        "known": False,
        "daily": {"used": None, "total": None, "remaining": None},
        "window": {"used": None, "total": None, "remaining": None, "window_seconds": None},
        "gap": (
            f"{name or 'this vendor'} has no header-based quota surface in the researched source "
            f"set, so the room reads nothing from its responses. The only signal it has is the "
            f"{THROTTLE_STATUS} the vendor answers with when a limit is exceeded."
        ),
    }


def remaining_of(reading: Mapping[str, Any] | None, half: str = "daily") -> float | None:
    """The remaining count in one half, or ``None`` when the vendor said nothing."""
    if not reading:
        return None
    section = reading.get(half)
    if not isinstance(section, Mapping):
        return None
    value = section.get("remaining")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


__all__ = [
    "sforce_limit_info",
    "hubspot_rate_limit",
    "read",
    "remaining_of",
]
