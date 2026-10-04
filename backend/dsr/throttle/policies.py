"""One declaration per connector, and the line between sourced and inferred.

The research's extensibility note is the shape of this module: "a third party can
add a new vendor by filling in a policy object, not by writing a new backoff
algorithm". So a policy is data - ``{burst, sustained, daily, retryHints}`` in the
research's own words - and every algorithm in this package reads it rather than
testing for a vendor name.

**The numbers are split by provenance, and the split is the point.**

*HubSpot* is the only vendor in the source set with published per-app and
per-account numbers, and they are quoted in full below. The OAuth figure is the
one that applies to a marketplace app: "each HubSpot account that installs your
app is limited to **110 requests every 10 seconds**". The per-app tier table is
quoted for the two shapes it names, and the burst is the sustained rate because
the vendor publishes one number and not two - recorded as an inference.

*Salesforce* publishes a daily cap **by name only**. The research quotes the
limit resource returning "``DailyApiRequests`` | Daily API calls", "``SingleEmail``",
"``ConcurrentSyncReportRuns``", "``DataStorageMB``", "``MaxContentDocumentsLimit``"
and says the per-edition numeric allocations "live in tables that were not
fetched". So the Salesforce policy names the limit it will watch and carries **no
number at all**. The bucket is blind on that connection by design: it spends what
the vendor's own ``Sforce-Limit-Info`` header reports, which is the number the
vendor counted.

*Dataverse* publishes the ``429`` and nothing numeric. The research says so itself:
"the Dataverse **Service Protection API Limits** page ... was not located at a
readable URL - every candidate path 404'd - so no Dataverse numeric limit is
quoted". So Dataverse's policy is a status and a retry hint, and no invented
number is added to fill the gap.

A policy that carries no number is therefore **not** a broken policy. It is the
honest reading of a vendor whose numbers this build could not source, and
:func:`describe` says which is which on the record.
"""

from __future__ import annotations

from typing import Any, Mapping

from dsr.throttle.classify import (
    LOCK_FLOOR_SECONDS,
    REQUEST_LIMIT_EXCEEDED,
    REQUEST_LIMIT_HTTP_STATUS,
    THROTTLE_STATUS,
    TRANSIENT_STATUSES,
)
from dsr.throttle.errors import UnknownPolicy

#: The vendors this build has a sourced policy for. A fourth is registered with
#: :func:`register`, which is the extension point the research asks for.
VENDORS: tuple[str, ...] = ("hubspot", "salesforce", "dataverse")

#: The default policy a connection with no vendor gets: every status and no number.
UNSOURCED_POLICY: dict[str, Any] = {
    "vendor": "",
    "burst": None,
    "sustained": None,
    "window_seconds": None,
    "daily": None,
    "limit_name": None,
    "retry_hints": {"retry_after": True},
    "lock_floor_seconds": 0,
    "retry_after_cap_seconds": 86_400,
    "sourced": False,
    "basis": (
        "No published limit is in the researched source set for this vendor, so the policy names "
        "no numbers. The room records the vendor's own refusals and waits on its own ladder."
    ),
    "gaps": [
        "no numeric limit was located at a readable URL, so the room declares none rather than "
        "guessing one"
    ],
}


def _hubspot() -> dict[str, Any]:
    return {
        "vendor": "hubspot",
        "burst": 110,
        "sustained": 110,
        "window_seconds": 10,
        "daily": 250_000,
        "limit_name": "X-HubSpot-RateLimit-Daily",
        "retry_hints": {
            "retry_after": True,
            "423": f"insert at least {LOCK_FLOOR_SECONDS}s between requests",
            "477": "honour Retry-After, capped at retry_after_cap_seconds",
            "transient_statuses": list(TRANSIENT_STATUSES),
        },
        "lock_floor_seconds": LOCK_FLOOR_SECONDS,
        "retry_after_cap_seconds": 86_400,
        "sourced": True,
        "basis": (
            'Quoted: "each HubSpot account that installs your app is limited to 110 requests '
            'every 10 seconds. This excludes the CRM Search API." Quoted, per app and per account: '
            '"Free and Starter | 100 / app | 250,000 / account; Professional | 190 / app | '
            '625,000 / account; Enterprise | 190 / app | 1,000,000 / account". Quoted: "Requests '
            "resulting in an error response shouldn't exceed 5% of your total daily requests.\""
        ),
        "gaps": [
            "the 110/10s figure is quoted for legacy public apps and OAuth apps on platform 2025.2 "
            "and 2026.03, and the daily limit is quoted as not sent on OAuth responses, so a private "
            "app needs its own numbers through the policy patch",
        ],
    }


def _salesforce() -> dict[str, Any]:
    return {
        "vendor": "salesforce",
        "burst": None,
        "sustained": None,
        "window_seconds": None,
        "daily": None,
        "limit_name": "DailyApiRequests",
        "retry_hints": {
            "retry_after": True,
            "request_limit": f"{REQUEST_LIMIT_EXCEEDED} on {REQUEST_LIMIT_HTTP_STATUS}",
            "usage_header": "Sforce-Limit-Info: api-usage=<used>/<total>; api-bursts=<used>/<total>",
            "limits_resource": "GET /services/data/vXX.X/limits/",
        },
        "lock_floor_seconds": 0,
        "retry_after_cap_seconds": 86_400,
        "sourced": True,
        "basis": (
            'Quoted: "For each limit, this resource returns the maximum allocation and the '
            'remaining allocation based on usage" for "DailyApiRequests | Daily API calls", '
            '"SingleEmail", "ConcurrentSyncReportRuns", "DataStorageMB" and '
            '"MaxContentDocumentsLimit". Quoted: "Sforce-Limit-Info: api-usage=10018/100000; '
            'api-bursts=1/750". Quoted: "If the error code is REQUEST_LIMIT_EXCEEDED, you\'ve '
            'exceeded API request limits in your org."'
        ),
        "gaps": [
            "the per-edition numeric DailyApiRequests allocation lives in a table the research did "
            "not fetch, so this policy names the limit and no number",
            "the burst rate is per-org and is not published; the room reads it from the api-bursts "
            "segment of Sforce-Limit-Info when the vendor sends one",
        ],
    }


def _dataverse() -> dict[str, Any]:
    return {
        "vendor": "dataverse",
        "burst": None,
        "sustained": None,
        "window_seconds": None,
        "daily": None,
        "limit_name": None,
        "retry_hints": {"retry_after": True, "throttle_status": THROTTLE_STATUS},
        "lock_floor_seconds": 0,
        "retry_after_cap_seconds": 86_400,
        "sourced": True,
        "basis": (
            'Quoted: "429 Too Many Requests Expect this status code when API limits are exceeded. '
            'For more information, see Service Protection API Limits."'
        ),
        "gaps": [
            "the Service Protection API Limits page was not located at a readable URL - every "
            "candidate path 404'd - so no Dataverse numeric limit is declared"
        ],
    }


#: The registered policies, keyed by vendor.
_REGISTRY: dict[str, dict[str, Any]] = {
    "hubspot": _hubspot(),
    "salesforce": _salesforce(),
    "dataverse": _dataverse(),
}

#: The fields a caller may patch on a connection. Not every policy field: a
#: connection's *vendor* is what it is, and ``basis`` and ``gaps`` are what the
#: research said rather than what an operator chose.
PATCHABLE: tuple[str, ...] = (
    "burst",
    "sustained",
    "window_seconds",
    "daily",
    "limit_name",
    "retry_after_cap_seconds",
    "lock_floor_seconds",
)


def register(policy: Mapping[str, Any]) -> dict[str, Any]:
    """Add a policy for a vendor this build has no sourced numbers for.

    This is the researched extension point: "adding a vendor means filling in a
    policy object, not writing a new backoff algorithm". The declaration is
    validated - a policy that names a rate but no window, or a window but no
    rate, would compute a bucket that never refills - and then stored in the
    process-wide registry.
    """
    prepared = prepare(policy)
    _REGISTRY[prepared["vendor"]] = prepared
    return dict(prepared)


def prepare(policy: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a policy declaration and fill in the defaults.

    Refuses the shapes that would produce a bucket that lies: a rate with no
    window, a window with no rate, a negative number, and a vendor name that is
    not a plain identifier.
    """
    if not isinstance(policy, Mapping):
        raise UnknownPolicy(f"a policy must be a JSON object; got {type(policy).__name__}")

    vendor = str(policy.get("vendor") or "").strip().lower()
    if not vendor or not vendor.replace("_", "").replace("-", "").isalnum():
        raise UnknownPolicy(
            f"a policy needs a vendor name made of letters, digits, dash or underscore; got "
            f"{policy.get('vendor')!r}"
        )

    base = dict(_REGISTRY.get(vendor) or UNSOURCED_POLICY)
    merged = {**base, **dict(policy), "vendor": vendor}
    merged["retry_hints"] = {
        **dict(base.get("retry_hints") or {}),
        **dict(policy.get("retry_hints") or {}),
    }
    merged["gaps"] = list(policy.get("gaps") or base.get("gaps") or [])

    for name in ("burst", "sustained", "window_seconds", "daily", "retry_after_cap_seconds"):
        merged[name] = _positive_or_none(merged.get(name), name, vendor)
    merged["lock_floor_seconds"] = _non_negative(
        merged.get("lock_floor_seconds"), "lock_floor_seconds"
    )

    if merged["sustained"] is not None and merged["window_seconds"] is None:
        raise UnknownPolicy(
            f"{vendor}: a sustained rate with no window_seconds cannot refill, because there is no "
            "length to refill it over. Set window_seconds or clear sustained."
        )
    if merged["window_seconds"] is not None and merged["sustained"] is None:
        raise UnknownPolicy(
            f"{vendor}: a window with no sustained rate cannot refill. Set sustained or clear "
            "window_seconds."
        )
    return merged


def _positive_or_none(value: Any, name: str, vendor: str) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise UnknownPolicy(f"{vendor}: {name} must be a number or null; got {value!r}") from None
    if number <= 0:
        raise UnknownPolicy(
            f"{vendor}: {name} is {number:g}; a limit is positive or it is not a limit"
        )
    return number


def _non_negative(value: Any, name: str) -> int:
    if value in (None, ""):
        return 0
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise UnknownPolicy(f"{name} must be a whole number of seconds; got {value!r}") from None
    if number < 0:
        raise UnknownPolicy(f"{name} is {number}; a delay is not negative")
    return number


def get(vendor: str) -> dict[str, Any]:
    """The registered policy for ``vendor``, or the unsourced one.

    The unsourced policy rather than an exception, because the research's whole
    extensibility claim is that a new vendor is a policy object away. A connector
    pointing at a vendor this build has not read about still gets a bucket that
    counts, a ladder that waits, and an honest ``sourced: false``.
    """
    name = str(vendor or "").strip().lower()
    return dict(_REGISTRY.get(name) or {**UNSOURCED_POLICY, "vendor": name})


def known(vendor: str) -> bool:
    """Whether this build has a sourced policy for ``vendor``."""
    return str(vendor or "").strip().lower() in _REGISTRY


def all_policies() -> dict[str, dict[str, Any]]:
    """Every registered policy, by vendor."""
    return {name: dict(policy) for name, policy in sorted(_REGISTRY.items())}


def describe(vendor: str) -> dict[str, Any]:
    """One policy, with the arithmetic the bucket will do spelled out.

    ``tokens_per_second`` and ``seconds_per_token`` are the two numbers an
    operator actually wants - "we may send 11 a second, so one call every 90
    milliseconds" - and deriving them here keeps them from being derived in three
    different places on a page.
    """
    policy = get(vendor)
    rate = (
        float(policy["sustained"]) / float(policy["window_seconds"])
        if policy.get("sustained") and policy.get("window_seconds")
        else 0.0
    )
    return {
        **policy,
        "known": known(policy.get("vendor", "")),
        "tokens_per_second": round(rate, 6),
        "seconds_per_token": round(1.0 / rate, 3) if rate else None,
        "preemptive": bool(policy.get("burst")),
        "field_names": list(PATCHABLE),
    }


__all__ = [
    "VENDORS",
    "PATCHABLE",
    "UNSOURCED_POLICY",
    "register",
    "prepare",
    "get",
    "known",
    "all_policies",
    "describe",
]
