"""WF-113: the inbound signature check, reusing the shipped signing rule.

The signing rule is not this workflow's, and it is not re-invented here. It is
the exact rule WF-066 already ships for the product's outbound webhooks:

* the signed input is ``f"{timestamp}.{raw_body}"`` - the timestamp, a period,
  and the raw request body;
* the digest is HMAC-SHA256 over that string, hex encoded;
* the digest and the timestamp ride in two headers.

WF-066 puts those headers on the wire as ``X-Chili-Signature`` and
``X-Chili-Timestamp``, which name a vendor. This workflow receives from any
external service, so it uses the neutral ``X-DSR-Signature`` and ``X-DSR-Timestamp``
namespace, but the *rule* is identical, and a test pins this module's
:func:`signing_input` and :func:`sign` to
``dsr.meeting_webhook_fanout.signing`` so the two cannot drift. That is what
"reuse the vocabulary rather than re-declare it" means here: one rule, two
header namespaces, held together by a test.

Why the domain package does not import the canonical module
------------------------------------------------------------
The brief requires a domain package to import nothing but the store, and the
enforced architectural guard names "another workflow's package" as the defect.
So the rule is declared here and pinned by test instead of imported. The choice
was made by Jev at confidence 0.97, audit ``jev-20261013T172714-13864-34127``.

The freshness window belongs to the receiver
--------------------------------------------
WF-066 is a sender, so the research leaves replay protection to the consumer and
WF-066 refuses nothing on age. WF-113 *is* the consumer of an inbound delivery,
so :func:`verify` enforces :data:`MAX_AGE_SECONDS` by default. The window is a
parameter so a caller or a test can widen or disable it, and the caller passes
its clock in rather than reading ``datetime.now`` at import time.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone

__all__ = [
    "MAX_AGE_SECONDS",
    "SIGNATURE_HEADER",
    "TIMESTAMP_HEADER",
    "headers_for",
    "sign",
    "signing_input",
    "unix_seconds",
    "verify",
]

#: Quoted by the shared rule: the consumer applies a five-minute window
#: (``MAX_AGE_SECONDS = 300``). WF-113 is the consumer, so it enforces it.
MAX_AGE_SECONDS = 300

#: The neutral header namespace. The rule is WF-066's; the vendor name is not.
SIGNATURE_HEADER = "X-DSR-Signature"
TIMESTAMP_HEADER = "X-DSR-Timestamp"


def signing_input(timestamp: str, raw_body: str) -> str:
    """The exact string a signature covers: ``{timestamp}.{raw_body}``."""
    return f"{timestamp}.{raw_body}"


def unix_seconds(when: datetime | None = None) -> str:
    """A unix timestamp in seconds, floored rather than rounded."""
    moment = when or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return str(int(moment.timestamp()))


def sign(secret: str, timestamp: str, raw_body: str) -> str:
    """The hex HMAC-SHA256 a sender computes to sign one delivery."""
    return hmac.new(
        secret.encode("utf-8"),
        signing_input(timestamp, raw_body).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def headers_for(secret: str, timestamp: str, raw_body: str) -> dict[str, str]:
    """The two headers a caller needs to sign a test delivery."""
    return {
        SIGNATURE_HEADER: sign(secret, timestamp, raw_body),
        TIMESTAMP_HEADER: timestamp,
        "Content-Type": "application/json",
    }


def verify(
    secret: str,
    raw_body: str,
    *,
    signature: str | None,
    timestamp: str | None,
    window_seconds: int | None = MAX_AGE_SECONDS,
    now: datetime | None = None,
) -> tuple[bool, str]:
    """Check one inbound delivery, the way the receiver must.

    Returns ``(ok, reason)``. The reasons are ``signature_missing``,
    ``timestamp_missing``, ``signature_stale`` and ``signature_mismatch``: four
    different mistakes with four different fixes, so a sender is not sent looking
    for the wrong one.

    Comparison is constant time. A signature is the one place where a comparison
    that leaks its timing leaks the secret.
    """
    if not signature or not timestamp:
        return False, "signature_missing" if not signature else "timestamp_missing"

    if window_seconds is not None:
        sent = _normalise_epoch(timestamp)
        if sent is None:
            return False, "timestamp_missing"
        reference = (now or datetime.now(timezone.utc)).timestamp()
        if abs(reference - sent) > window_seconds:
            return False, "signature_stale"

    expected = sign(secret, timestamp, raw_body)
    if hmac.compare_digest(signature.strip().lower(), expected.lower()):
        return True, ""
    return False, "signature_mismatch"


def _normalise_epoch(value: str) -> float | None:
    """A sender's timestamp in seconds, whatever unit it arrived in.

    The magnitude decides, so a sender that puts milliseconds in the header is
    still a sender. The threshold is the year 5138 in seconds, which no clock
    this century reaches.
    """
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number / 1000.0 if number > 1e11 else number
