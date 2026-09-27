"""Sending one CRM create, and keeping the record of every attempt.

The transport is a protocol, so the suite can drive a create without a socket and so a
deployment can swap in a vendor SDK without touching the engine. Everything above this
line only sees :class:`CreateResult`.

The timeout is not sourced
--------------------------
The research says nothing about how long to wait. :data:`DEFAULT_TIMEOUT` is 10 seconds,
chosen because a create is one round trip to a third party and 5 seconds is the timeout
the *sibling* Outreach workflow documents, which is a different vendor and a different
budget. See the ``timeout`` inference.

The retry ladder is not sourced either
--------------------------------------
The research says only that the worker "retries with backoff". :data:`RETRYABLE_STATUS`
in :mod:`dsr.crm_engagement.vocabulary` is the ordinary try-again family plus the two
codes this workflow's own research makes load-bearing: a 429, and a 409 from a CRM
rejecting a duplicate on the unique sync key. See the ``retry_ladder`` inference.

Every attempt is a record, not memory
-------------------------------------
The researched step 5 says a failure "surfaces the failure in the admin **Sync log**
panel". A log that kept only the last status would surface the failure and throw away the
reason attempt two happened - which is the only thing that tells a rep whether to fix the
integration or to wait. So every attempt is serialised in full and stored on the queue
row, and :meth:`CreateReport.to_dict` is the shape the Sync log reads. There is no
in-memory copy that could disagree with it.

The token is never in the record
--------------------------------
``request_headers`` is kept because it is the evidence of what was sent - except the
``Authorization`` value, which *is* the connector's token.
:meth:`CreateRequest.to_dict` redacts it, and a test asserts the token appears in no
stored row.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol

from dsr.crm_engagement.payloads import CreateRequest, error_detail, extract_record_id, success_codes
from dsr.crm_engagement.vocabulary import (
    BODY_SAMPLE,
    DEFAULT_BACKOFF,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_TIMEOUT,
    RETRYABLE_STATUS,
)


@dataclass(frozen=True)
class CreateResult:
    """One HTTP attempt's outcome, normalised across transports."""

    ok: bool
    status: int | None = None
    headers: Mapping[str, str] = field(default_factory=dict)
    body: str = ""
    error: str | None = None
    duration_ms: float = 0.0

    @property
    def retryable(self) -> bool:
        """Whether trying again could plausibly help.

        A status in the retryable family, or a request that never completed - a DNS
        failure or a refused connection is the network's problem this second and no one's
        fault. Anything else is the vendor telling us the request itself was wrong, and
        repeating it verbatim would only waste their quota.
        """
        if self.ok:
            return False
        if self.status is None:
            return True
        if self.status == 409:
            # The sync key is unique in the CRM by design, so a 409 is the uniqueness
            # working. Repeating the create cannot make the collision go away; see
            # dsr/crm_engagement/queue.py for how it is surfaced.
            return False
        return self.status in RETRYABLE_STATUS

    @property
    def permanent_failure(self) -> bool:
        return not self.ok and not self.retryable


class Transport(Protocol):
    """Anything that can POST a JSON body to a CRM create endpoint."""

    def post(
        self, url: str, body: bytes, headers: Mapping[str, str], timeout: float
    ) -> CreateResult: ...


class UrllibTransport:
    """Default transport. Standard library only, no dependency added."""

    def post(
        self, url: str, body: bytes, headers: Mapping[str, str], timeout: float
    ) -> CreateResult:
        request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                text = response.read(BODY_SAMPLE).decode("utf-8", "replace")
                return CreateResult(
                    ok=200 <= response.status < 300,
                    status=response.status,
                    headers=dict(response.headers),
                    body=text,
                    duration_ms=round((time.perf_counter() - started) * 1000, 2),
                )
        except urllib.error.HTTPError as exc:
            try:
                text = exc.read(BODY_SAMPLE).decode("utf-8", "replace")
            except Exception:  # pragma: no cover - the body is best effort only
                text = ""
            return CreateResult(
                ok=False,
                status=exc.code,
                headers=dict(exc.headers or {}),
                body=text,
                error=f"HTTP {exc.code}",
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )
        except Exception as exc:  # noqa: BLE001 - a dead host must not raise
            return CreateResult(
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )


@dataclass
class CreateReport:
    """The outcome of creating one CRM row, across every attempt."""

    result: CreateResult
    attempts: int = 0
    history: list[CreateResult] = field(default_factory=list)
    request: CreateRequest | None = None

    @property
    def ok(self) -> bool:
        return self.result.ok

    @property
    def needs_manual_update(self) -> bool:
        """Whether a human has to fix this by hand.

        A failure the code cannot retry its way out of, plus the one researched failure
        that arrives dressed as a success - a create the vendor accepted with no record
        id in it, which leaves the room unable to update the row it just made.
        """
        return self.result.permanent_failure or (self.ok and not self.record_id()["id"])

    def record_id(self) -> dict[str, Any]:
        """Where the new row's id came from, or why there is not one."""
        if self.request is None:
            return {"id": "", "where": None, "sourced": False, "basis": None, "searched": []}
        return extract_record_id(
            self.request.vendor, self.result.status, self.result.headers, self.result.body
        )

    def vendor_detail(self) -> dict[str, Any]:
        """The vendor's own explanation of the outcome, including any annotations."""
        return error_detail(self.result.body)

    def to_dict(self) -> dict[str, Any]:
        """The report as the Sync log stores it.

        ``attempt_statuses`` is the at-a-glance version and stays for the list view;
        ``attempt_log`` is the full per-attempt record, kept because the point of the
        retry policy is that its decisions stay legible after the process that made them
        has gone.
        """
        located = self.record_id()
        return {
            "attempts": self.attempts,
            "http_status": self.result.status,
            "error": self.result.error,
            "needs_manual_update": self.needs_manual_update,
            "succeeded": self.ok,
            "crm_record_id": located["id"] or None,
            "crm_record_id_from": located["where"],
            "crm_record_id_sourced": bool(located["sourced"]),
            "crm_record_id_locations_searched": located["searched"],
            "vendor_error": self.vendor_detail(),
            "attempt_statuses": [r.status for r in self.history],
            "attempt_log": [
                {
                    "attempt": number,
                    "ok": result.ok,
                    "status": result.status,
                    "error": result.error,
                    "duration_ms": result.duration_ms,
                    "vendor_error": error_detail(result.body),
                }
                for number, result in enumerate(self.history, start=1)
            ],
            "request": self.request.to_dict() if self.request is not None else None,
        }


def post_create(
    transport: Transport,
    request: CreateRequest,
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    backoff: float = DEFAULT_BACKOFF,
    sleep: Callable[[float], None] = time.sleep,
) -> CreateReport:
    """POST one create, retrying the failures worth retrying.

    The body is serialised once, so what the Sync log records is exactly what went over
    the wire - which is the only way a log can be evidence.
    """
    body = json.dumps(request.body, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    report = CreateReport(result=CreateResult(ok=False, error="not attempted"), request=request)
    allowed = success_codes(request.vendor)

    for attempt in range(1, max(1, int(max_attempts)) + 1):
        result = transport.post(request.url, body, request.headers, DEFAULT_TIMEOUT)
        report.attempts = attempt
        report.history.append(result)
        report.result = result
        # A status in the vendor's documented create-success set is a success even if
        # this build's transport called it one already; a 2xx outside that set is not,
        # and is left to the engine to report as unmapped rather than marked synced.
        if result.ok and (result.status is None or result.status in allowed):
            break
        if not result.retryable:
            break
        if attempt < max_attempts:
            wait = backoff * (2 ** (attempt - 1))
            if wait > 0:
                sleep(min(wait, 30.0))

    if report.result.ok and report.result.status not in allowed:
        # Keep the transport's own verdict out of the report's ``ok``; the engine decides
        # what an undocumented status means and needs to be able to say so.
        report.history[-1] = CreateResult(
            ok=False,
            status=report.result.status,
            headers=report.result.headers,
            body=report.result.body,
            error=report.result.error or f"HTTP {report.result.status}",
            duration_ms=report.result.duration_ms,
        )
        report.result = report.history[-1]

    return report
