"""Outbound delivery of one activity-feed event.

The transport is a protocol so the suite can drive delivery without a socket and
so a team can swap in a queue or a vendor SDK without touching the engine.
Everything above this line only sees :class:`PostResult`.

The timeout is sourced
---------------------
:data:`~dsr.outreach_feed.vocabulary.SENDER_TIMEOUT_SECONDS` is 5.0, because the
research states "The timeout while waiting for response is set to 5 seconds" for
the webhook seam. It is reused for the outbound write for the same reason it
applies to the inbound one: 5 seconds is what the other end of this integration
is documented to wait, so it is the budget this integration speaks in.

The retry policy is not sourced
-------------------------------
The research says nothing about retrying the outbound write. It says something
adjacent and much stronger - **Outreach does not retry webhook deliveries upon
receiving any of the Status Codes including 500 Internal Server Error and 429 Too
Many Requests** - which is a statement about the *inbound* direction but tells us
the platform's posture: nobody downstream is going to resend anything for us. So
this build retries a failure worth retrying, and, more importantly, records every
attempt. See :class:`PostReport` and :func:`post_json`.

Every attempt is a record, not memory
-------------------------------------
A retry loop is the one place where the interesting state is per-attempt. A report
that lived only for the length of the call would leave, after a restart, nothing
but a list of status codes: not *why* attempt two happened, not what the endpoint
said, not how long it took. :meth:`PostReport.to_dict` therefore serialises every
attempt in full, and the engine stores it. The record is the retry history; there
is no in-memory copy that could disagree with it.

The token is never in the record
--------------------------------
``request_headers`` is kept because it is what proves what was sent - except the
``Authorization`` value, which *is* the S2S token. :meth:`PostReport.to_dict`
redacts it, and a test asserts the token appears in no stored row.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol

from dsr.outreach_feed.vocabulary import SENDER_TIMEOUT_SECONDS

#: Statuses worth trying again. The research documents no retry ladder, so this is
#: the ordinary "try again" family - see ``inferences.py`` for the record.
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF = 0.5

#: The sourced timeout, re-exported under the name the retry knobs sit beside.
#: It is not a knob: 5 seconds is what the other end of this integration waits.
DEFAULT_TIMEOUT = SENDER_TIMEOUT_SECONDS

#: How much of an endpoint's response body to keep per attempt.
BODY_SAMPLE = 512

USER_AGENT = "digital-sales-room-activity-feed/0.1"

REDACTED = "Bearer ***redacted***"


@dataclass(frozen=True)
class PostResult:
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
    """Anything that can POST a JSON body to the event endpoint."""

    def post(self, url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> PostResult: ...


class UrllibTransport:
    """Default transport. Standard library only, no dependency added."""

    def post(self, url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> PostResult:
        request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                text = response.read(BODY_SAMPLE).decode("utf-8", "replace")
                return PostResult(
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
            return PostResult(
                ok=False,
                status=exc.code,
                body=text,
                error=f"HTTP {exc.code}",
                retryable=exc.code in RETRYABLE_STATUS,
                retry_after=_retry_after(exc.headers),
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )
        except Exception as exc:  # noqa: BLE001 - a dead host must not raise
            return PostResult(
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


@dataclass
class PostReport:
    """The outcome of delivering one payload, across every attempt."""

    result: PostResult
    attempts: int = 0
    history: list[PostResult] = field(default_factory=list)
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.result.ok

    @property
    def needs_manual_update(self) -> bool:
        """Whether a human has to fix this by hand.

        A failure the code cannot retry its way out of. The name is the research's
        own framing in the sibling workflow: an activity log a rep reads is only
        useful if it says which rows will not fix themselves.
        """
        return self.result.permanent_failure

    def to_dict(self) -> dict[str, Any]:
        """The report as the delivery log stores it.

        ``attempt_statuses`` is the at-a-glance version and stays for the list view.
        ``attempt_log`` is the full per-attempt record, kept because the point of
        the retry policy is that its decisions stay legible after the process that
        made them has gone.
        """
        return {
            "attempts": self.attempts,
            "http_status": self.result.status,
            "error": self.result.error,
            "needs_manual_update": self.needs_manual_update,
            "response": {"status": self.result.status, "body": self.result.body[:BODY_SAMPLE]},
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
            "request_headers": redact_headers(self.headers),
        }


def redact_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    """Copy a header map with the ``Authorization`` value replaced.

    The headers are kept because they are the evidence of what was sent, but the
    ``Authorization`` value *is* the S2S token. A delivery row is read by humans
    and exported through the generic records API, so the token is never stored in
    one.
    """
    result: dict[str, str] = {}
    for key, value in dict(headers or {}).items():
        if str(key).lower() == "authorization":
            result[str(key)] = REDACTED
        else:
            result[str(key)] = value
    return result


def post_json(
    transport: Transport,
    url: str,
    payload: Mapping[str, Any],
    *,
    headers: Mapping[str, str] | None = None,
    timeout: float = SENDER_TIMEOUT_SECONDS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    backoff: float = DEFAULT_BACKOFF,
    sleep: Callable[[float], None] = time.sleep,
) -> PostReport:
    """POST a payload, retrying the failures worth retrying.

    The body is serialised once so that what is recorded is exactly what was
    sent, which is the only way a delivery log can be evidence.
    """
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    sent = {
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }
    sent.update({str(k): str(v) for k, v in dict(headers or {}).items()})

    report = PostReport(result=PostResult(ok=False, error="not attempted"), body=body, headers=sent)

    for attempt in range(1, max(1, int(max_attempts)) + 1):
        result = transport.post(url, body, sent, timeout)
        report.attempts = attempt
        report.history.append(result)
        report.result = result
        if result.ok or not result.retryable:
            break
        if attempt < max_attempts:
            # A rate limiter told us when to come back; believe it.
            wait = result.retry_after if result.retry_after is not None else backoff * (2 ** (attempt - 1))
            if wait > 0:
                sleep(min(wait, 30.0))

    return report
