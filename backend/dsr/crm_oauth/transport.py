"""Outbound HTTP, and the seam the suite and the seeder both replace.

Every call this workflow makes to a vendor - the code→token exchange, the token
refresh, and the low-cost probe - goes through :class:`Transport`. That is what
lets the whole token lifecycle be driven in tests and in the demo without a
socket, and it is also the fourth researched interface's other half: a connector
builds the request, the transport carries it.

The transport never raises for an HTTP status. A 400 from a token endpoint is
*data* - it is the vendor saying the code was no good - and collapsing it into
an exception would lose the body that says why. Only a genuinely undeliverable
request comes back as ``ok=False`` with ``status=0``.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

#: The researched timeouts do not exist for this workflow - WF-026's five
#: seconds belong to a different vendor. Ten seconds is ours, and it is an
#: inference: see :mod:`dsr.crm_oauth.inferences`.
DEFAULT_TIMEOUT_SECONDS = 10.0

#: How much of a vendor's response body is kept when it is quoted back in an
#: error or a token event. Enough to diagnose, bounded so a large HTML error
#: page cannot end up in a record.
BODY_SAMPLE_CHARS = 600


@dataclass(frozen=True)
class HttpResult:
    """One HTTP exchange, successful or not."""

    ok: bool
    status: int
    body: str = ""
    headers: Mapping[str, str] = field(default_factory=dict)
    error: str = ""
    duration_ms: float = 0.0

    def json(self) -> dict[str, Any]:
        """The body parsed as JSON, or ``{}`` when it is not JSON.

        A vendor that answers an error with HTML is common enough that a parse
        failure here has to be a value rather than an exception.
        """
        try:
            parsed = json.loads(self.body)
        except (json.JSONDecodeError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @property
    def sample(self) -> str:
        """A bounded, single-line excerpt of the body, for an error message."""
        text = " ".join(self.body.split())
        return text[:BODY_SAMPLE_CHARS]


class Transport(Protocol):
    """What a connector needs from the world to make a request."""

    def request(
        self,
        method: str,
        url: str,
        *,
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> HttpResult:
        """Perform one request. Never raises for a status code."""


class UrllibTransport:
    """The real transport, on the standard library.

    Used in production; replaced by a scripted one in the tests and by
    :class:`~dsr.features.wf034_connect_a_crm_org_to_the_sales_room_oa.DemoTransport`
    in the demo, so neither ever opens a socket.
    """

    def request(
        self,
        method: str,
        url: str,
        *,
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> HttpResult:
        request = urllib.request.Request(url, data=body, method=method.upper())
        for name, value in (headers or {}).items():
            request.add_header(name, value)
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                raw = response.read().decode("utf-8", "replace")
                return HttpResult(
                    ok=True,
                    status=int(response.status),
                    body=raw,
                    headers={k.lower(): v for k, v in response.headers.items()},
                    duration_ms=round((time.perf_counter() - started) * 1000, 3),
                )
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            return HttpResult(
                ok=False,
                status=int(exc.code),
                body=raw,
                headers={k.lower(): v for k, v in (exc.headers or {}).items()},
                error=f"HTTP {exc.code}",
                duration_ms=round((time.perf_counter() - started) * 1000, 3),
            )
        except Exception as exc:  # noqa: BLE001 - a transport must not crash a sweep
            return HttpResult(
                ok=False,
                status=0,
                error=f"{type(exc).__name__}: {exc}",
                duration_ms=round((time.perf_counter() - started) * 1000, 3),
            )


def form_body(fields: Mapping[str, Any]) -> bytes:
    """Encode a form-encoded request body, dropping empty values.

    ``application/x-www-form-urlencoded`` is what an OAuth token endpoint takes,
    and a field sent as an empty string is a field the vendor will reject with a
    message about a field it should not have received.
    """
    from urllib.parse import urlencode

    pairs = [(key, str(value)) for key, value in fields.items() if value not in (None, "")]
    return urlencode(pairs).encode("utf-8")


def redact_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Headers with every credential value replaced.

    Used wherever headers are recorded. The ``Authorization`` value *is* the
    bearer token, and a token event is readable by anyone who can read a token
    event.
    """
    secret_names = {"authorization", "proxy-authorization", "cookie", "x-api-key"}
    return {
        name: ("<redacted>" if name.lower() in secret_names else value)
        for name, value in headers.items()
    }


__all__ = [
    "BODY_SAMPLE_CHARS",
    "DEFAULT_TIMEOUT_SECONDS",
    "HttpResult",
    "Transport",
    "UrllibTransport",
    "form_body",
    "redact_headers",
]
