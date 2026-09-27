"""Outbound delivery: one attempt per call, and the researched retry ladder as a
schedule rather than a sleep.

The retry policy is sourced
--------------------------
"``Total number of retries: 26``", on a ladder of "1 min, then 10 min, then
hourly x24", with a "**10 second**" response timeout. 1 + 1 + 24 = 26, which is
exactly the documented total, and the encoded ladder in
:mod:`dsr.event_stream.vocabulary` is checked against that total rather than
restating it.

Why the ladder is a schedule and not a loop
-------------------------------------------
Twenty-six retries spread over a day is not a thing a request thread should do.
A loop that slept for 25 hours inside a POST handler would hold a worker, time
out in every proxy in front of the app, and lose the whole ladder when the
process restarted - which is precisely the defect the audit-first design in this
repo exists to avoid: the interesting state would live in memory and die with
the request.

So the policy is split in two:

* :func:`attempt_delivery` performs **one** HTTP attempt and returns what
  happened. This is what the event route calls inline.
* :func:`schedule` turns that outcome plus the attempt number into the
  *documented* next step: the delay from the ladder, the absolute
  ``next_attempt_at``, and how many retries are left.

:func:`next_attempt` performs the following attempt when a driver asks for it -
the ``POST /deliveries/{id}/retry`` route, or the seeder. In a deployment a
queue worker calls the same function on a timer. The policy is therefore
*implemented* rather than *described*: the ladder is the researched one, and it
is enforced, it just is not enforced by blocking a request.

Every attempt is a record
-------------------------
:meth:`DeliveryReport.to_dict` serialises each attempt in full - status, error,
whether it was retryable, the endpoint's own body, and how long it took. The
reason a retry is scheduled is the thing an operator asks about at 2am and the
thing a status code alone cannot answer, so it is stored, not summarised.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Callable, Mapping, Protocol

from dsr.event_stream.signing import sign_headers
from dsr.event_stream.targets import iso, parse_now
from dsr.event_stream.vocabulary import MAX_RETRIES, RETRY_LADDER, WEBHOOK_TIMEOUT_SECONDS

#: Statuses worth trying again. 429 is the rate limit the research documents on
#: the REST seam and 408/425/5xx are the ordinary "try again" family. Anything
#: else - and this is the important half - is permanent: a 404 or a 410 will
#: answer identically forever, and retrying it twenty-six times over a day is a
#: way of making an operator's logs unreadable.
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})

#: "Seismic will wait for **10 seconds** for the webhook to respond."
DEFAULT_TIMEOUT = WEBHOOK_TIMEOUT_SECONDS

#: How much of an endpoint's response body to keep per attempt. Enough to see
#: why a subscriber refused the payload, bounded so one chatty endpoint cannot
#: grow an audit row without limit.
BODY_SAMPLE = 512

#: The furthest a ``Retry-After`` may push the next attempt. The ladder's own
#: largest step is an hour, so a subscriber asking for a week does not get to
#: park a delivery for a week; it is capped at the top of the documented
#: ladder and the cap is recorded on the attempt.
RETRY_AFTER_CAP = 3600.0


# --------------------------------------------------------------------------- #
# Transport
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DeliveryResult:
    """One HTTP attempt's outcome, normalised across transports."""

    ok: bool
    status: int | None = None
    body: str = ""
    error: str | None = None
    retryable: bool = False
    retry_after: float | None = None
    duration_ms: float = 0.0

    @property
    def permanent_failure(self) -> bool:
        """True when retrying cannot help, so a person has to look at it."""
        return not self.ok and not self.retryable


class Transport(Protocol):
    """Anything that can POST a webhook body.

    A protocol, not a class, so a deployment swaps in a queue, a different HTTP
    client, or a vendor SDK without touching anything above this line - and so
    a test can drive delivery without a socket.
    """

    def post(
        self, url: str, body: bytes, headers: Mapping[str, str], timeout: float
    ) -> DeliveryResult: ...


class UrllibTransport:
    """Default transport. Standard library only; no dependency added."""

    def post(
        self, url: str, body: bytes, headers: Mapping[str, str], timeout: float
    ) -> DeliveryResult:
        request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                text = response.read(BODY_SAMPLE).decode("utf-8", "replace")
                return DeliveryResult(
                    ok=200 <= response.status < 300,
                    status=response.status,
                    body=text,
                    duration_ms=round((time.perf_counter() - started) * 1000, 2),
                )
        except urllib.error.HTTPError as exc:
            try:
                text = exc.read(BODY_SAMPLE).decode("utf-8", "replace")
            except Exception:  # pragma: no cover - the body is best effort only
                text = ""
            return DeliveryResult(
                ok=False,
                status=exc.code,
                body=text,
                error=f"HTTP {exc.code}",
                retryable=exc.code in RETRYABLE_STATUS,
                retry_after=_retry_after(exc.headers),
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )
        except Exception as exc:  # noqa: BLE001 - a dead host must not raise
            # A connection that could not be made is retryable: the host may come
            # back. A DNS failure is arguably not, but the two are indistinguishable
            # from here and guessing wrong in the pessimistic direction would drop
            # deliveries over one bad DNS minute.
            return DeliveryResult(
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
                retryable=True,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )


def _retry_after(headers: Any) -> float | None:
    if headers is None:
        return None
    try:
        raw = headers.get("Retry-After")
    except Exception:  # pragma: no cover - defensive against odd header maps
        return None
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# The ladder
# --------------------------------------------------------------------------- #


def retry_delay(attempt: int) -> float | None:
    """Seconds to wait before the attempt after ``attempt``, or ``None``.

    ``retry_delay(1)`` is 60.0 - one minute, then ten, then hourly. ``None``
    means the ladder is finished: 26 retries is the documented total, so the
    attempt after the 27th does not exist.
    """
    if attempt < 1:
        raise ValueError("attempt numbers start at 1")
    if attempt > MAX_RETRIES:
        return None
    return RETRY_LADDER[attempt - 1]


def attempts_remaining(attempt: int) -> int:
    """How many attempts are still possible after ``attempt`` has failed.

    ``MAX_RETRIES + 1`` because the ladder's 26 delays mean 27 attempts: the
    first try, then one per rung. Getting this off by one is how a "retries
    exhausted" row reports one retry left and the next scheduled attempt never
    happens.
    """
    return max(0, MAX_RETRIES + 1 - attempt)


#: The delivery states a stored delivery row can be in. An inference - the
#: research documents a retry ladder, not a state machine - and named as one in
#: ``inferences.py`` entry ``delivery-states``.
DELIVERED = "delivered"
RETRYING = "retrying"
FAILED = "failed"
SKIPPED = "skipped"
TESTED = "tested"

DELIVERY_STATES: tuple[str, ...] = (DELIVERED, RETRYING, FAILED, SKIPPED, TESTED)


@dataclass
class DeliveryReport:
    """The outcome of one attempt, and the step the ladder says comes next."""

    attempt: int
    result: DeliveryResult
    body: bytes
    headers: dict[str, str] = field(default_factory=dict)
    at: str = ""
    state: str = FAILED
    retry_in_seconds: float | None = None
    next_attempt_at: str | None = None
    #: Attempts this product will still make by itself. Zero for a delivered,
    #: a skipped, and a permanently-failed delivery; the ladder's remaining
    #: length while a retry is scheduled.
    attempts_remaining: int = 0
    filter_matched: bool = True
    #: Attempts already on the row being retried, so a retried delivery keeps
    #: one history rather than fragmenting into a row per try.
    prior: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.result.ok

    def to_dict(self) -> dict[str, Any]:
        """What the delivery row stores.

        ``attempt_log`` is the full per-attempt history, not just this call's
        attempt, so the reason attempt 2 happened is still there when somebody
        asks three weeks later. ``request_headers`` is the exact set the payload
        went out with, signature included, which is the only evidence that would
        settle a disputed delivery.
        """
        latest = {
            "attempt": self.attempt,
            "ok": self.result.ok,
            "status": self.result.status,
            "error": self.result.error,
            "retryable": self.result.retryable,
            "retry_after": self.result.retry_after,
            "duration_ms": self.result.duration_ms,
            "response_body": self.result.body[:BODY_SAMPLE],
            "at": self.at,
        }
        return {
            "state": self.state,
            "attempt": self.attempt,
            "http_status": self.result.status,
            "error": self.result.error,
            "response": {"status": self.result.status, "body": self.result.body[:BODY_SAMPLE]},
            "retryable": self.result.retryable,
            "retry_in_seconds": self.retry_in_seconds,
            "next_attempt_at": self.next_attempt_at,
            "attempts_remaining": self.attempts_remaining,
            "attempts_total": self.attempt,
            "attempt_log": [*self.prior, latest],
            "filter_matched": self.filter_matched,
            "duration_ms": self.result.duration_ms,
            "at": self.at,
            "request_headers": dict(self.headers),
        }


def attempt_delivery(
    transport: Transport,
    url: str,
    payload: Mapping[str, Any],
    *,
    event: str,
    delivery_id: str,
    attempt: int = 1,
    secret: str | None = None,
    previous_secret: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    now: Any = None,
    filter_matched: bool = True,
    prior: list[dict[str, Any]] | None = None,
) -> DeliveryReport:
    """POST the payload **once** and say what the ladder says next.

    ``attempt`` is 1 for the first try and 2..27 for the retries, so the ladder
    index is the attempt number and the two cannot drift. ``prior`` is the
    attempt log already on the row being retried, so a retried delivery keeps
    one history rather than fragmenting into a row per try.
    """
    body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    headers = sign_headers(
        body,
        secret=secret,
        previous_secret=previous_secret,
        event=event,
        delivery_id=delivery_id,
    )
    result = transport.post(url, body, headers, timeout)
    at = iso(parse_now(now))
    report = DeliveryReport(
        attempt=attempt,
        result=result,
        body=body,
        headers=headers,
        at=at,
        filter_matched=filter_matched,
        prior=list(prior or []),
    )
    _apply_ladder(report, now=now)
    return report


def _apply_ladder(report: DeliveryReport, *, now: Any) -> None:
    """Turn an outcome into a state, and the state into the next rung."""
    if report.result.ok:
        report.state = DELIVERED
        report.retry_in_seconds = None
        report.next_attempt_at = None
        report.attempts_remaining = attempts_remaining(report.attempt)
        return

    if not report.result.retryable:
        # A 404, a 410, a refusal that will repeat: stop, and say so loudly
        # enough that somebody looks. `attempts_remaining` is 0 rather than the
        # ladder's full length, because it means "attempts this product will
        # still make by itself" - and for a permanent failure the answer is none,
        # not "twenty-six". The ladder was never started.
        report.state = FAILED
        report.retry_in_seconds = None
        report.next_attempt_at = None
        report.attempts_remaining = 0
        return

    delay = retry_delay(report.attempt)
    if delay is None:
        # The documented 26 retries are spent. This is the "needs a human" state.
        report.state = FAILED
        report.retry_in_seconds = None
        report.next_attempt_at = None
        report.attempts_remaining = 0
        return

    # A rate limiter that named a time is believed, but not past the top of the
    # documented ladder.
    if report.result.retry_after is not None:
        delay = min(max(0.0, report.result.retry_after), RETRY_AFTER_CAP)
    report.state = RETRYING
    report.retry_in_seconds = delay
    report.next_attempt_at = iso(parse_now(now) + _seconds(delay))
    report.attempts_remaining = attempts_remaining(report.attempt)


def next_attempt(
    transport: Transport,
    url: str,
    payload: Mapping[str, Any],
    previous: Mapping[str, Any],
    *,
    event: str,
    delivery_id: str,
    secret: str | None = None,
    previous_secret: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    now: Any = None,
    filter_matched: bool = True,
) -> DeliveryReport:
    """Perform the attempt *after* a stored delivery's last one.

    ``previous`` is the stored delivery's ``data``, used for the attempt number
    and for the attempt log, so a retried delivery keeps its history in one row
    instead of fragmenting into a row per try.
    """
    history = [dict(entry) for entry in (previous.get("attempt_log") or [])]
    attempt = int(previous.get("attempts_total") or len(history) or 0) + 1
    return attempt_delivery(
        transport,
        url,
        payload,
        event=event,
        delivery_id=delivery_id,
        attempt=min(attempt, MAX_RETRIES + 1),
        secret=secret,
        previous_secret=previous_secret,
        timeout=timeout,
        now=now,
        filter_matched=filter_matched,
        prior=history,
    )


def _seconds(value: float) -> timedelta:
    return timedelta(seconds=float(value))


def sleep_for(report: DeliveryReport, sleep: Callable[[float], None] = time.sleep) -> None:
    """Wait out a scheduled retry.

    Exists so a driver that *wants* to block - a CLI, a local script, a test -
    can, without the request path ever doing it. The HTTP layer does not call
    this; it schedules and returns.
    """
    if report.retry_in_seconds:
        sleep(report.retry_in_seconds)
