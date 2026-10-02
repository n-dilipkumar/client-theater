"""Outbound webhook delivery.

The transport is a protocol so tests can drive delivery without a socket and so
a team can swap in a queue, a different HTTP client, or a CRM SDK without
touching the sync logic. Everything above this line only sees
:class:`DeliveryResult`.

Design note
-----------
Retries and the HMAC signature are *inferences*, not sourced capabilities. The
research documents that the source product rate-limits with ``429`` and that a
webhook is the general-purpose seam, but it says nothing about retry policy or
how a subscriber proves a payload came from us. A webhook that ships customer
page metadata to an arbitrary URL with no authentication and no retry is not
shippable, so both are here, both are optional, and both are recorded as
inferences rather than as findings.

Every attempt is a record, not memory
------------------------------------
A retry loop is the one place in this workflow where the interesting state is
per-attempt, and the branch kept that state in a :class:`DeliveryReport` that
lived only for the length of the call. Only ``attempt_statuses`` - a list of
integers - reached the Activity Log, so after a restart nobody could tell *why*
attempt two happened, what the endpoint said, or how long it took. The report
therefore serialises every attempt in full, and the sync engine stores that
alongside the event. The record is the retry history; there is no in-memory
copy that could disagree with it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol

# Statuses worth trying again. 429 is the rate limit the research documents;
# the 5xx set is the ordinary "try again" family.
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})

DEFAULT_TIMEOUT = 5.0
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF = 0.5


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
        """True when retrying cannot help, so a human has to step in."""
        return not self.ok and not self.retryable


class Transport(Protocol):
    """Anything that can POST a webhook body."""

    def post(
        self, url: str, body: bytes, headers: Mapping[str, str], timeout: float
    ) -> DeliveryResult: ...


class UrllibTransport:
    """Default transport. Standard library only, no dependency added."""

    def post(
        self, url: str, body: bytes, headers: Mapping[str, str], timeout: float
    ) -> DeliveryResult:
        request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                text = response.read(4096).decode("utf-8", "replace")
                return DeliveryResult(
                    ok=200 <= response.status < 300,
                    status=response.status,
                    body=text,
                    duration_ms=round((time.perf_counter() - started) * 1000, 2),
                )
        except urllib.error.HTTPError as exc:
            try:
                text = exc.read(4096).decode("utf-8", "replace")
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
            return DeliveryResult(
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
                retryable=True,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )


def _retry_after(headers: Any) -> float | None:
    raw = None
    if headers is not None:
        try:
            raw = headers.get("Retry-After")
        except Exception:  # pragma: no cover - defensive against odd header maps
            raw = None
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return None


def sign(secret: str | None, body: bytes) -> str | None:
    """HMAC-SHA256 over the exact bytes sent, or ``None`` without a secret."""
    if not secret:
        return None
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


#: How much of an endpoint's response body to keep per attempt. Enough to see
#: why a subscriber refused the payload, bounded so a chatty endpoint cannot
#: bloat one audit row without limit.
BODY_SAMPLE = 512


@dataclass
class DeliveryReport:
    """The outcome of delivering one payload, across every attempt."""

    result: DeliveryResult
    attempts: int = 0
    history: list[DeliveryResult] = field(default_factory=list)
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.result.ok

    @property
    def needs_manual_update(self) -> bool:
        """Whether a human has to fix this by hand.

        Mirrors the research's "track if they will need manual updating": a
        failure the code cannot retry its way out of.
        """
        return self.result.permanent_failure

    def to_dict(self) -> dict[str, Any]:
        """The report as the Activity Log stores it.

        ``attempt_statuses`` is the at-a-glance version and stays for the list
        view. ``attempts`` is the full per-attempt record: the whole point of the
        retry policy is that its decisions stay legible after the process that
        made them has gone, so the first and last error, the endpoint's own
        words, and how long each try took are all kept rather than summarised
        away to a status code.
        """
        return {
            "attempts": self.attempts,
            "http_status": self.result.status,
            "error": self.result.error,
            "needs_manual_update": self.needs_manual_update,
            "response": {"status": self.result.status, "body": self.result.body},
            "attempt_statuses": [r.status for r in self.history],
            "attempt_log": [
                {
                    "attempt": number,
                    "ok": result.ok,
                    "status": result.status,
                    "error": result.error,
                    "retryable": result.retryable,
                    "retry_after": result.retry_after,
                    "duration_ms": result.duration_ms,
                    "body": result.body[:BODY_SAMPLE],
                }
                for number, result in enumerate(self.history, start=1)
            ],
            # The exact headers the payload went out with, signature included:
            # if a subscriber ever disputes a delivery, this is what proves what
            # was sent. The secret itself is not here, only the HMAC over it.
            "request_headers": dict(self.headers),
        }


def deliver(
    transport: Transport,
    url: str,
    payload: Mapping[str, Any],
    *,
    event: str,
    delivery_id: str,
    secret: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    backoff: float = DEFAULT_BACKOFF,
    sleep: Callable[[float], None] = time.sleep,
) -> DeliveryReport:
    """POST a payload to a subscriber, retrying the failures worth retrying.

    The body is serialised once so that what is signed is exactly what is sent,
    which is the only way a signature means anything.
    """
    body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "digital-sales-room-webhooks/0.1",
        "X-DSR-Event": event,
        "X-DSR-Delivery": delivery_id,
    }
    signature = sign(secret, body)
    if signature:
        headers["X-DSR-Signature"] = signature

    report = DeliveryReport(
        result=DeliveryResult(ok=False, error="not attempted"), body=body, headers=headers
    )

    for attempt in range(1, max(1, int(max_attempts)) + 1):
        result = transport.post(url, body, headers, timeout)
        report.attempts = attempt
        report.history.append(result)
        report.result = result
        if result.ok or not result.retryable:
            break
        if attempt < max_attempts:
            # A rate limiter told us when to come back; believe it.
            wait = (
                result.retry_after
                if result.retry_after is not None
                else backoff * (2 ** (attempt - 1))
            )
            if wait > 0:
                sleep(min(wait, 30.0))

    return report
