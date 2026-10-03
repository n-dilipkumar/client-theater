"""The outbound transport seam for the sandbox validator.

The room's connectors talk to a vendor through one narrow interface:
``request(method, url, body, headers, timeout) -> SandboxResponse``. The
production implementation is :class:`UrllibTransport`; tests and the demo
drive the whole pipeline through :class:`ScriptedTransport` so nothing here
opens a socket unless a real validation is asked for.

Quota reading lives here too, because that is where a vendor's answer
carries it: a status of 429, a ``Retry-After``, a Salesforce
``Sforce-Limit-Info``, a HubSpot ``X-HubSpot-RateLimit-*``, or the generic
``X-RateLimit-Remaining``. One parser, so every probe reads the same thing.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from dsr.connector_sandbox.errors import VendorRequestError
from dsr.connector_sandbox.vocabulary import POISON_MARKER


@dataclass
class SandboxResponse:
    """One vendor answer, kept as plain data.

    ``status`` is the HTTP status, ``body`` the raw text, ``headers`` a
    case-insensitive-ish dict of the response headers the room cares about,
    and ``record_id`` the CRM record id the vendor handed back when the
    response carried one.
    """

    status: int
    body: str = ""
    headers: Mapping[str, str] = field(default_factory=dict)
    duration_ms: float = 0.0

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def json(self) -> Any:
        try:
            return json.loads(self.body) if self.body else None
        except json.JSONDecodeError:
            return None

    def header(self, name: str) -> str:
        lowered = name.lower()
        for key, value in self.headers.items():
            if key.lower() == lowered:
                return str(value)
        return ""


class Transport:
    """The interface a connector speaks. Everything else is pluggable.

    ``step`` names which probe the call belongs to; a scripted transport
    may key its answers on it, and the run records it in the request log.
    """

    #: How a run record names the transport that served it, so a simulated
    #: confirmation is never readable as a fact about a real CRM.
    transport_name = "transport"

    def request(
        self,
        method: str,
        url: str,
        *,
        body: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float = 10.0,
        step: str = "",
    ) -> SandboxResponse:
        raise NotImplementedError


class UrllibTransport(Transport):
    """The real transport: one urllib call per request, no retries.

    Retries are not this layer's business - the researched rule is that the
    room *behaves* on quota signals, and the quota probe watches exactly one
    call each, so an automatic retry here would blur the very behaviour the
    assertion scores.
    """

    transport_name = "urllib"

    def request(
        self,
        method: str,
        url: str,
        *,
        body: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float = 10.0,
        step: str = "",
    ) -> SandboxResponse:
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        sent = dict(headers or {})
        if payload is not None:
            sent.setdefault("Content-Type", "application/json")
        request = urllib.request.Request(url, data=payload, headers=sent, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                return SandboxResponse(
                    status=response.status,
                    body=response.read().decode("utf-8", "replace"),
                    headers=dict(response.headers.items()),
                )
        except urllib.error.HTTPError as exc:
            return SandboxResponse(
                status=exc.code,
                body=exc.read().decode("utf-8", "replace"),
                headers=dict(exc.headers.items()) if exc.headers else {},
            )
        except (OSError, urllib.error.URLError) as exc:
            raise VendorRequestError(f"vendor request failed: {exc}") from exc


class ScriptedTransport(Transport):
    """Answers from a script, so tests and the demo never open a socket.

    Each call consumes the next entry; when the script runs out the last
    entry repeats. A skipped probe does not consume an entry, so the
    positions stay honest. The run's requests are recorded either way,
    which is what the quota probe reads.
    """

    transport_name = "scripted"

    def __init__(
        self,
        responses: list[SandboxResponse | Callable[[dict[str, Any]], SandboxResponse]],
    ) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self._cursor = 0

    def request(
        self,
        method: str,
        url: str,
        *,
        body: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float = 10.0,
        step: str = "",
    ) -> SandboxResponse:
        record = {
            "method": method,
            "url": url,
            "body": dict(body or {}),
            "headers": dict(headers or {}),
            "timeout": timeout,
            "step": step,
        }
        self.calls.append(record)
        if not self.responses:
            return SandboxResponse(status=599, body='{"error": "script_exhausted"}')
        entry = self.responses[min(self._cursor, len(self.responses) - 1)]
        self._cursor += 1
        if callable(entry):
            return entry(record)
        return entry


class SimulatedTransport(Transport):
    """Answers each probe the way a compliant sandbox would, from the request.

    The route default, for the same reason WF-038's is: the research cites
    no credentials and no base URL for a real org, and a transport that
    failed to connect would make every assertion fail for a reason that has
    nothing to do with the connector. Every run it serves records
    ``transport: "simulated"`` in the run record, so a reviewer can never
    read one of its confirmations as a fact about a real CRM. A deployment
    with a real sandbox points ``DSR_WF048_TRANSPORT=real`` at
    :class:`UrllibTransport` - the one dependency a team overrides.
    """

    transport_name = "simulated"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._next = 0
        self._keys: dict[str, str] = {}

    def request(
        self,
        method: str,
        url: str,
        *,
        body: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float = 10.0,
        step: str = "",
    ) -> SandboxResponse:
        self.calls.append({"method": method, "url": url, "body": dict(body or {}), "step": step})
        info = f"api-usage={4 + len(self.calls)}/5000"

        if method == "PATCH" and isinstance(body, dict):
            key = url.rstrip("/").rsplit("/", 1)[-1]
            if key in self._keys:
                # The key is known: an upsert on it updates the SAME record.
                return SandboxResponse(
                    status=200,
                    body=json.dumps({"id": self._keys[key], "created": False, "success": True}),
                    headers={"Sforce-Limit-Info": info},
                )
            self._next += 1
            record_id = f"sim-{self._next:04d}"
            self._keys[key] = record_id
            return SandboxResponse(
                status=201,
                body=json.dumps({"id": record_id, "created": True, "success": True}),
                headers={"Sforce-Limit-Info": info},
            )

        if method == "POST" and isinstance(body, dict) and isinstance(body.get("records"), list):
            poisoned = any(
                POISON_MARKER in json.dumps(value, default=str)
                for record in body["records"]
                for value in record.values()
            )
            if poisoned:
                # The vendor's own validation rejects the bad row, and with
                # allOrNone=true the whole chunk rolls back: nothing written.
                results = []
                for _record in body["records"]:
                    results.append(
                        {
                            "success": False,
                            "errors": [
                                {
                                    "message": "FIELD_INTEGRITY_EXCEPTION: Invalid field value",
                                    "statusCode": "INVALID_FIELD",
                                }
                            ],
                        }
                    )
                return SandboxResponse(status=400, body=json.dumps({"records": results}))
            written = []
            for _record in body["records"]:
                self._next += 1
                written.append({"id": f"sim-{self._next:04d}", "success": True, "created": True})
            return SandboxResponse(status=200, body=json.dumps({"records": written}))

        return SandboxResponse(status=400, body='{"error": "unsupported_probe"}')


#: The quota signal the probes act on.
DEFAULT_BACKOFF_SECONDS = 60.0

#: Header forms the parser knows. Salesforce reports ``api-usage=used/total`` in
#: ``Sforce-Limit-Info``; HubSpot sends ``X-HubSpot-RateLimit-*``; everything
#: generic goes through ``X-RateLimit-Remaining`` and ``Retry-After``.
QUOTA_HEADER_PATTERNS = (
    "Sforce-Limit-Info",
    "X-HubSpot-RateLimit-Secondly-Remaining",
    "X-HubSpot-RateLimit-Daily-Remaining",
    "X-RateLimit-Remaining",
    "Retry-After",
)


def parse_quota(headers: Mapping[str, str], status: int = 0) -> dict[str, Any]:
    """Read the quota signal a vendor sent, as data.

    ``remaining`` is how many calls the vendor says are left; ``exhausted``
    is true when the vendor said 429 or reported a remaining balance at or
    below zero. ``retry_after`` carries the vendor's back-off advice when it
    sent one.
    """

    def find(name: str) -> str:
        lowered = name.lower()
        for key, value in dict(headers).items():
            if key.lower() == lowered:
                return str(value)
        return ""

    used = total = None
    limit_info = find("Sforce-Limit-Info")
    if limit_info:
        for part in limit_info.replace(";", ",").split(","):
            key, _, value = part.partition("=")
            if key.strip().lower() == "api-usage" and "/" in value:
                left, _, right = value.strip().partition("/")
                try:
                    used, total = int(left), int(right)
                except ValueError:
                    used, total = None, None
                break
    remaining: int | None = None
    for header_name in (
        "X-HubSpot-RateLimit-Secondly-Remaining",
        "X-HubSpot-RateLimit-Daily-Remaining",
        "X-RateLimit-Remaining",
    ):
        raw = find(header_name)
        if raw:
            try:
                remaining = int(raw)
            except ValueError:
                remaining = None
            break

    retry_after: int | None = None
    raw_retry = find("Retry-After")
    if raw_retry:
        try:
            retry_after = int(float(raw_retry))
        except ValueError:
            retry_after = None

    exhausted = status == 429 or (remaining is not None and remaining <= 0)
    return {
        "used": used,
        "total": total,
        "remaining": remaining,
        "retry_after": retry_after,
        "exhausted": exhausted,
        "hit_429": status == 429,
    }
