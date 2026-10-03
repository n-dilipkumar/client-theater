"""Connector telemetry for WF-049: the health half of the researched dashboard.

The research's dashboard line: "Dashboard shows remaining daily + burst quota,
sync success rate, mean latency, error-class breakdown (validation / throttle /
auth / vendor-5xx), and the live change-stream lag." Quota is
:mod:`dsr.integ_monitor.quota`; this module is the rest.

* **Telemetry** - one record per connector call a connector reports, and the
  aggregates the dashboard reads: success rate over a window, mean latency,
  and the four researched error classes.
* **Error classes** - derived from the HTTP status when the connector does not
  name a class itself, and the connector's word wins when it does, because a
  connector that knows "403 REQUEST_LIMIT_EXCEEDED" is a throttle is telling
  the room something a status code cannot.
* **Stream lag** - the "live change-stream lag" reading: either handed in
  directly as seconds, or computed from the instant the vendor produced a
  change and the instant the connector received it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from dsr.integ_monitor.errors import InvalidTelemetry
from dsr.integ_monitor.timestamps import check_not_ahead, iso, parse_instant
from dsr.integ_monitor.vocabulary import ERROR_CLASSES, require_vendor  # noqa: F401

#: The status-to-class mapping used when a sample names no class itself.
#:
#: [sourced classes] "error-class breakdown (validation / throttle / auth /
#: vendor-5xx)". The mapping below is this build's reading of which statuses
#: fall in which class: 401 is the auth failure the researched HubSpot and
#: Salesforce flows both describe; 429 is throttling on every vendor in the
#: set; 5xx is the vendor's own server fault; other 4xx are the caller's
#: validation problem. 403 is deliberately *not* auto-mapped to throttle: the
#: same status means "forbidden" on one vendor and "limit exceeded" on
#: another, and a connector that knows which should say so (see the
#: ``throttle-is-429-and-the-vendors-word-wins`` inference).
STATUS_CLASSES: tuple[tuple[int, str], ...] = (
    (401, "auth"),
    (429, "throttle"),
)

#: Successful calls are not an error class, but the room records them so the
#: success rate and mean latency are computed from the same population the
#: breakdown is computed from.
OK_STATUSES = frozenset({200, 201, 202, 204})

#: How big one telemetry sample's latency may be, in ms. A number a connector
#: cannot mean (negative, or above a day) is refused rather than skewing a mean.
LATENCY_MAX_MS = 24 * 60 * 60 * 1000


def class_from_status(status: int | None) -> str | None:
    """The researched error class an HTTP status falls in, or ``None``.

    2xx is not a class at all - a call that succeeded has no error class, and
    recording one would put successes in the breakdown.
    """
    status = int(status) if status is not None else None
    if status is None or status in OK_STATUSES:
        return None
    for boundary, error_class in STATUS_CLASSES:
        if status == boundary:
            return error_class
    if 500 <= status <= 599:
        return "vendor_5xx"
    if 400 <= status <= 499:
        return "validation"
    return None


def check_class(error_class: Any) -> str | None:
    """A class the caller named, or ``None``. Unknown names are refused."""
    if error_class in (None, ""):
        return None
    text = str(error_class).strip().lower()
    if text not in ERROR_CLASSES:
        raise InvalidTelemetry(
            f"error class {text!r} is not one of {', '.join(ERROR_CLASSES)}; the four classes "
            "are the researched breakdown, and a fifth one is an inference, not a sample"
        )
    return text


def check_samples(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The calls a connector reports, in the shape :func:`aggregate` reads.

    One sample: ``{"ok": true}`` or ``{"ok": false, "status": 429,
    "latency_ms": 812}``, optionally with an explicit ``error_class`` and an
    ``at``. ``status`` alone works - ``ok`` is derived from it - because the
    connector already knows the status it got.
    """
    calls = payload.get("calls", payload.get("samples"))
    if calls is None and isinstance(payload, list):
        calls = payload
    if not isinstance(calls, list) or not calls:
        raise InvalidTelemetry(
            "calls is required: a telemetry sample is at least one call the connector made, "
            "each as {ok, status, latency_ms, error_class, at}"
        )
    prepared: list[dict[str, Any]] = []
    for index, call in enumerate(calls):
        if not isinstance(call, Mapping):
            raise InvalidTelemetry(f"call {index} must be a JSON object; got {type(call).__name__}")
        status = call.get("status")
        ok = call.get("ok")
        if ok is None and status is not None:
            try:
                ok = int(status) in OK_STATUSES
            except (TypeError, ValueError) as exc:
                raise InvalidTelemetry(f"call {index} status is not a number: {status!r}") from exc
        if not isinstance(ok, bool):
            raise InvalidTelemetry(
                f"call {index} needs ok or a status; a sample the room can neither succeed "
                "nor fail is not a call"
            )
        derived = class_from_status(int(status)) if status is not None else None
        named = check_class(call.get("error_class"))
        if ok and derived is not None:
            derived = None
        if not ok and derived is None and named is None:
            raise InvalidTelemetry(
                f"call {index} failed without a status or an error_class; a failure the room "
                "cannot classify is a gap in the connector's report, not a sample"
            )
        prepared.append(
            {
                "ok": ok,
                "status": int(status) if status is not None else None,
                "error_class": named or derived,
                "latency_ms": _latency(call.get("latency_ms"), index),
                "at": call.get("at"),
            }
        )
    return prepared


def _latency(value: Any, index: int) -> float | None:
    if value in (None, ""):
        return None
    try:
        latency = float(value)
    except (TypeError, ValueError) as exc:
        raise InvalidTelemetry(f"call {index} latency_ms is not a number: {value!r}") from exc
    if latency < 0 or latency > LATENCY_MAX_MS:
        raise InvalidTelemetry(
            f"call {index} latency_ms is {latency}, which is not a latency; refusing it "
            "rather than letting one bad sample skew a mean"
        )
    return latency


def aggregate(
    samples: Sequence[Mapping[str, Any]],
    *,
    window_seconds: float = 86400.0,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The dashboard's health numbers, computed over one window.

    Success rate, mean latency and the error-class breakdown are computed over
    exactly the samples inside the window, so a filtered view does not report
    totals for the whole table. A connector with no samples says so with
    ``known: false`` - an honest "no data" rather than a 100% success rate
    nobody earned.
    """
    moment = now or datetime.now(timezone.utc)
    cutoff = moment - timedelta(seconds=window_seconds)
    inside = []
    outside = 0
    for sample in samples:
        at = parse_instant(sample.get("at"))
        if at is None or at >= cutoff:
            inside.append(sample)
        else:
            outside += 1

    total = len(inside)
    succeeded = sum(1 for sample in inside if sample.get("ok"))
    latencies = [
        float(sample["latency_ms"]) for sample in inside if sample.get("latency_ms") is not None
    ]
    breakdown = {name: 0 for name in ERROR_CLASSES}
    for sample in inside:
        error_class = sample.get("error_class")
        if error_class in breakdown:
            breakdown[error_class] += 1
    return {
        "known": total > 0,
        "window_seconds": window_seconds,
        "as_of": iso(moment),
        "calls": total,
        "succeeded": succeeded,
        "failed": total - succeeded,
        "success_rate": round(succeeded / total, 4) if total else None,
        "mean_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else None,
        "error_breakdown": breakdown,
        "outside_window": outside,
    }


def check_lag(payload: Mapping[str, Any], *, now: datetime | None = None) -> float:
    """The change-stream lag one observation reports, in seconds.

    [sourced] "the live change-stream lag" and the automation's "when the
    change-stream lag exceeds N seconds".

    Two shapes, because connectors genuinely carry both: a direct
    ``lag_seconds``, or ``source_event_at`` + ``observed_at`` from which the
    lag is the difference. Computed lags are clamped at zero rather than
    recorded negative - a receiver ahead of its source is a clock skew, and a
    negative lag would make a lag rule un-fireable.
    """
    now = now or datetime.now(timezone.utc)
    direct = payload.get("lag_seconds")
    if direct not in (None, ""):
        try:
            lag = float(direct)
        except (TypeError, ValueError) as exc:
            raise InvalidTelemetry(f"lag_seconds is not a number: {direct!r}") from exc
        if lag < 0:
            raise InvalidTelemetry(f"lag_seconds is {lag}; lag is not negative")
        return lag
    source_at = parse_instant(payload.get("source_event_at"))
    observed = parse_instant(payload.get("observed_at"))
    if source_at is None and observed is None:
        raise InvalidTelemetry(
            "a lag observation needs lag_seconds, or source_event_at and observed_at; "
            "the room cannot compute a lag from nothing"
        )
    check_not_ahead(observed, now=now)
    observed = observed or now
    if source_at is None:
        raise InvalidTelemetry(
            "observed_at alone cannot produce a lag; pass source_event_at too, or lag_seconds"
        )
    return max(0.0, (observed - source_at).total_seconds())


__all__ = [
    "STATUS_CLASSES",
    "OK_STATUSES",
    "LATENCY_MAX_MS",
    "class_from_status",
    "check_class",
    "check_samples",
    "aggregate",
    "check_lag",
]
