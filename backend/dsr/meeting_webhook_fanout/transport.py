"""POSTing the payload to the destination, and recording what came back.

What the research says, and what this module therefore does not have
-------------------------------------------------------------------
Nothing. The three sources describe a destination that *"can handle receiving the
data object being sent via the webhooks"* and a signature it checks. There is no
timeout, no retry count, no backoff ladder and no dead-letter queue anywhere in
them.

That is worth saying out loud, because sibling features in this repository do
have those things, and copying them here would be this build inventing a policy
the evidence does not state. One attempt is what the evidence supports. The
re-attempt is a route a person calls,
:meth:`~dsr.meeting_webhook_fanout.fanout.MeetingWebhookFanout.redeliver`.

The transport is a seam
-----------------------
:class:`Transport` has one method. The suite and the demo seeder supply a fake,
so a hundred tests of the signing and fan-out rules never open a socket, and
``backend/seed.py`` never tries to POST to ``hooks.example`` from a machine with
no route to it. :class:`UrllibTransport` uses the standard library only, because
``httpx`` is a test dependency in this project and not a runtime one
(``backend/pyproject.toml`` lists it under ``dev``).
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

#: One attempt gets this long before it is recorded as a transport failure. A
#: value the research does not state, so it is named here rather than buried in a
#: call, and it is served in the vocabulary.
DEFAULT_TIMEOUT_SECONDS = 5.0

#: A response body is kept as a bounded slice. A subscriber that returns a whole
#: HTML error page must not end up inside a delivery row.
MAX_RESPONSE_BYTES = 2048

#: The statuses a 2xx covers. Recorded as ``delivered`` or ``failed``; nothing
#: consults this to schedule anything, because this build has no scheduler.
SUCCESS_MIN = 200
SUCCESS_MAX = 299


@dataclass(frozen=True)
class DeliveryResult:
    """What one attempt got back.

    ``retryable`` is advice for the person reading the log, never a schedule.
    """

    ok: bool
    status: int | None = None
    body: str = ""
    error: str = ""
    duration_ms: float = 0.0
    retryable: bool = False
    final_url: str = ""


class Transport(Protocol):
    """The one method a subscriber destination has to have."""

    def post(
        self,
        url: str,
        body: bytes,
        headers: Mapping[str, str],
        timeout: float,
    ) -> DeliveryResult: ...


class UrllibTransport:
    """The real transport, on the standard library only."""

    def __init__(self, *, timeout: float = DEFAULT_TIMEOUT_SECONDS, opener: Any = None) -> None:
        self.timeout = timeout
        # Redirects are followed rather than refused: a subscriber that answers
        # 301 to its canonical URL is a working subscriber, and refusing would
        # fail a POST the operator can see plainly in their own logs. The
        # resolved URL comes back on the result so the hop is not invisible.
        self._opener = opener or urllib.request.build_opener()

    def post(
        self,
        url: str,
        body: bytes,
        headers: Mapping[str, str],
        timeout: float,
    ) -> DeliveryResult:
        request = urllib.request.Request(url, data=body, method="POST")
        for name, value in headers.items():
            request.add_header(name, value)
        started = time.perf_counter()
        try:
            with self._opener.open(request, timeout=timeout) as response:
                text = _read(response.read)
                status = int(response.status)
                return DeliveryResult(
                    ok=SUCCESS_MIN <= status <= SUCCESS_MAX,
                    status=status,
                    body=text,
                    duration_ms=round((time.perf_counter() - started) * 1000, 3),
                    retryable=status >= 500 or status == 429,
                    final_url=str(response.geturl()),
                )
        except urllib.error.HTTPError as exc:
            # A 4xx or 5xx is an *answer*, not a transport failure: the subscriber
            # was reached and refused. Reading the body is what makes a refused
            # POST diagnosable, so it is kept even though exc is an error.
            text = ""
            try:
                text = _read(exc.read)
            except Exception:  # pragma: no cover - a body we cannot read is not fatal
                text = ""
            status = int(exc.code)
            return DeliveryResult(
                ok=False,
                status=status,
                body=text,
                error=f"HTTP {status}",
                duration_ms=round((time.perf_counter() - started) * 1000, 3),
                retryable=status >= 500 or status == 429,
                final_url=str(getattr(exc, "url", "") or url),
            )
        except Exception as exc:  # URLError, socket timeout, DNS, TLS
            return DeliveryResult(
                ok=False,
                status=None,
                error=f"{type(exc).__name__}: {exc}",
                duration_ms=round((time.perf_counter() - started) * 1000, 3),
                retryable=True,
                final_url=url,
            )


class FakeTransport:
    """A transport that records what it was asked to send and answers as told.

    The demo seeder uses this so seeding never opens a socket. It is in the
    domain package rather than the test tree because ``backend/seed.py`` needs it
    and the seeder is not a test.
    """

    def __init__(
        self,
        *,
        status: int = 202,
        body: str = "",
        error: str = "",
        fail: bool = False,
    ) -> None:
        self.status = status
        self.body = body
        self.error = error
        self.fail = fail
        self.sent: list[dict[str, Any]] = []

    def post(
        self,
        url: str,
        body: bytes,
        headers: Mapping[str, str],
        timeout: float,
    ) -> DeliveryResult:
        self.sent.append(
            {"url": url, "body": body.decode("utf-8", "replace"), "headers": dict(headers)}
        )
        if self.fail:
            return DeliveryResult(
                ok=False,
                status=None,
                error=self.error or "URLError: no route to the subscriber",
                retryable=True,
                final_url=url,
            )
        return DeliveryResult(
            ok=SUCCESS_MIN <= self.status <= SUCCESS_MAX,
            status=self.status,
            body=self.body,
            error="" if self.status < 400 else f"HTTP {self.status}",
            retryable=self.status >= 500 or self.status == 429,
            final_url=url,
        )


def _read(reader: Any) -> str:
    """A bounded, decoded slice of a response body."""
    raw = reader(MAX_RESPONSE_BYTES + 1) if callable(reader) else reader
    if isinstance(raw, str):
        raw = raw.encode("utf-8", "replace")
    truncated = len(raw) > MAX_RESPONSE_BYTES
    text = raw[:MAX_RESPONSE_BYTES].decode("utf-8", "replace")
    return f"{text}...[truncated]" if truncated else text


def is_retryable(result: DeliveryResult) -> bool:
    """Whether a re-attempt could plausibly succeed.

    Two sources of truth, in this order. The ``retryable`` flag is what the
    transport itself decided while it had the live response in hand. The status
    list is the fallback for a :class:`DeliveryResult` built by hand or by a
    fake, where nobody set the flag: a 5xx or a 429 is worth another attempt and
    a 4xx is not, and nothing else here schedules anything either way.

    Advice for the person reading the log, and the only place this is consulted.
    """
    if result.ok:
        return False
    if result.retryable:
        return True
    if result.status is None:
        return True
    return result.status >= 500 or result.status == 429
