"""POSTing the payload to the destination, and recording what came back.

What the research says, and what this module therefore does not have
-------------------------------------------------------------------
Nothing. This workflow's five sources describe a destination that "can handle
receiving the data object being sent via the webhooks" and a token to prove the
traffic is ours. There is no timeout, no retry count, no backoff schedule, and
no dead-letter queue anywhere in them.

That is worth being explicit about, because the sibling features in this
repository do have those things - a 10-second timeout and a 26-rtry ladder -
and they are *not* borrowed here. They come from a different ticket's vendor,
and copying them would be this build inventing a policy the research does not
state. See ``no-retry-ladder-and-an-inferred-timeout`` in
:mod:`dsr.intent_stream.inferences`.

What is here instead is the minimum a delivery log needs to be worth reading: one
attempt, its outcome, the status, a bounded slice of the response body, and how
long it took. :data:`RETRYABLE_STATUSES` is recorded on the row as advice - "try
this again" versus "this will never work" - and nothing consults it to schedule
anything. The re-attempt itself is a route a person calls
(:meth:`~dsr.intent_stream.stream.IntentStream.resend`).

The transport is a seam
-----------------------
:class:`Transport` has one method. The suite and the demo seeder supply
:class:`FakeTransport`, so a hundred tests of the once-versus-updates rule and
the contact filters never open a socket, and ``backend/seed.py`` never tries to
POST to ``hooks.example`` from a machine with no route to it.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from dsr.intent_stream.vocabulary import (
    DEFAULT_TIMEOUT_SECONDS,
    MAX_RESPONSE_BYTES,
    RETRYABLE_STATUSES,
    SUCCESS_STATUSES,
)


@dataclass(frozen=True)
class DeliveryResult:
    """What one attempt got back.

    ``retryable`` is advice, never a schedule. See the module docstring.
    """

    ok: bool
    status: int | None = None
    body: str = ""
    error: str = ""
    duration_ms: float = 0.0
    retryable: bool = False
    #: The resolved URL after any redirect the client followed. Recorded because
    #: a POST that arrived somewhere other than the URL that was configured is
    #: the single most confusing thing to find in a webhook log.
    final_url: str = ""


class Transport(Protocol):
    """The one method a destination has to have."""

    def post(
        self,
        url: str,
        body: bytes,
        headers: Mapping[str, str],
        timeout: float,
    ) -> DeliveryResult: ...


class UrllibTransport:
    """The real transport, on the standard library only.

    ``urllib`` rather than ``httpx`` because ``httpx`` is a test dependency in
    this project, not a runtime one (``backend/pyproject.toml`` lists it under
    ``dev``), and a feature that imported it would break the installed package.
    """

    def __init__(self, *, timeout: float = DEFAULT_TIMEOUT_SECONDS, opener: Any = None) -> None:
        self.timeout = timeout
        # Redirects are followed rather than refused: a destination that answers
        # 301 to its canonical URL is a working destination, and refusing would
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
                return DeliveryResult(
                    ok=int(response.status) in SUCCESS_STATUSES,
                    status=int(response.status),
                    body=text,
                    duration_ms=round((time.perf_counter() - started) * 1000, 3),
                    retryable=int(response.status) in RETRYABLE_STATUSES,
                    final_url=str(response.geturl()),
                )
        except urllib.error.HTTPError as exc:
            # A 4xx/5xx is an *answer*, not a transport failure: the destination
            # was reached and refused. Reading the body is what makes a refused
            # POST diagnosable, so it is kept even though `exc` is an error.
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
                retryable=status in RETRYABLE_STATUSES,
                final_url=str(getattr(exc, "url", "") or url),
            )
        except Exception as exc:  # URLError, socket timeout, DNS, TLS
            return DeliveryResult(
                ok=False,
                status=None,
                error=f"{type(exc).__name__}: {exc}",
                duration_ms=round((time.perf_counter() - started) * 1000, 3),
                # A transport failure is the most retryable thing there is, and
                # is the one case where this advice is not a guess.
                retryable=True,
                final_url=url,
            )


def _read(reader: Any) -> str:
    """A bounded, decoded slice of a response body."""
    raw = reader(MAX_RESPONSE_BYTES + 1) if callable(reader) else reader
    if isinstance(raw, str):
        raw = raw.encode("utf-8", "replace")
    truncated = len(raw) > MAX_RESPONSE_BYTES
    text = raw[:MAX_RESPONSE_BYTES].decode("utf-8", "replace")
    return f"{text}…[truncated]" if truncated else text


def encode(body: Mapping[str, Any]) -> bytes:
    """The request body, deterministic and UTF-8.

    ``sort_keys`` so two identical payloads serialise identically, which is what
    lets a test compare a recorded delivery against a rebuilt one.
    """
    return json.dumps(body, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")


def classify(result: DeliveryResult) -> str:
    """The delivery state a result becomes."""
    return "delivered" if result.ok else "failed"


def is_retryable(result: DeliveryResult) -> bool:
    """Whether a re-attempt could plausibly succeed.

    Advice for the operator, and the only place
    :data:`~dsr.intent_stream.vocabulary.RETRYABLE_STATUSES` is consulted.
    """
    if result.ok:
        return False
    if result.retryable:
        return True
    if result.status is None:
        return True
    return result.status in RETRYABLE_STATUSES


@dataclass
class Attempt:
    """One attempt, as recorded on the delivery row.

    ``attempt_log`` holds every attempt rather than a summary of them, so the
    reason attempt two happened is still readable after the request that made it
    has gone.
    """

    number: int
    ok: bool
    status: int | None
    error: str
    duration_ms: float
    retryable: bool
    at: str
    via: str = "visit"
    #: Python attribute names stay snake_case; the stored row is camelCase, which
    #: is what :func:`dsr.intent_stream.stream._attempt` writes. Keeping the two
    #: spellings apart here is deliberate - the dataclass is a Python object, the
    #: row is the wire and storage shape.
    final_url: str = ""
    body_excerpt: str = ""
