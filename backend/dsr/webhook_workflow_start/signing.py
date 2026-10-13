"""WF-114: the inbound signature check, reusing the shipped signing rule.

The signing rule is not this workflow's, and it is not re-invented here. It is
the exact rule WF-113 ships for its inbound webhook and WF-066 ships for the
product's outbound webhook:

* the signed input is ``f"{timestamp}.{raw_body}"`` - the timestamp, a period,
  and the raw request body;
* the digest is HMAC-SHA256 over that string, hex encoded;
* the digest and the timestamp ride in two headers named ``X-DSR-Signature`` and
  ``X-DSR-Timestamp``.

Why the domain package does not import WF-113's module
------------------------------------------------------
The architectural guard requires a domain package to import nothing but the
store, and it names "another workflow's package" as the defect. So the rule is
declared here and pinned by a test in ``backend/tests/test_wf114.py`` that puts
this module and ``dsr.webhook_ingest.signing`` side by side and asserts the
signed input, the digest, the header names and the window are equal. That is what
"reuse the vocabulary rather than re-declare it" means here: one rule, two
header namespaces, held together by a test.

Fail closed
-----------
WF-113 makes the signature optional per automation, because the researched flow
copies one generated URL and names no signing step. WF-114's issue is explicit
that this is not good enough: *"The spec gives no authentication, signature or
allowlist rule for the inbound URL. Decide one, fail closed, and record the
decision. A minted URL that anyone can call is a published write endpoint."* So
:func:`verify` here has no optional mode. A missing signature is a refusal, not a
fallback.

The freshness window belongs to the receiver
--------------------------------------------
WF-066 is a sender, so the research leaves replay protection to the consumer and
WF-066 refuses nothing on age. WF-114 *is* the consumer of an inbound delivery,
so :func:`verify` enforces :data:`~dsr.webhook_workflow_start.vocabulary.MAX_AGE_SECONDS`
by default. The window is a parameter so a caller or a test can widen it, and the
caller passes its clock in rather than reading ``datetime.now`` at import time.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from typing import Any

from dsr.webhook_workflow_start.vocabulary import MAX_AGE_SECONDS

__all__ = [
    "HEADERS_REQUIRED_ALWAYS",
    "MAX_AGE_SECONDS",
    "SIGNATURE_HEADER",
    "TIMESTAMP_HEADER",
    "headers_for",
    "mint_signing_value",
    "sign",
    "signing_input",
    "unix_seconds",
    "verify",
]

#: Fail closed: the two headers are required on every delivery, always.
HEADERS_REQUIRED_ALWAYS = True

SIGNATURE_HEADER = "X-DSR-Signature"
TIMESTAMP_HEADER = "X-DSR-Timestamp"

#: The reason words :func:`verify` returns. They are the vocabulary's own, so a
#: caller can write ``vocabulary.REASON_SIGNATURE_STALE`` and match.
REASON_SIGNATURE_MISSING = "signature_missing"
REASON_TIMESTAMP_MISSING = "timestamp_missing"
REASON_SIGNATURE_STALE = "signature_stale"
REASON_SIGNATURE_MISMATCH = "signature_mismatch"

#: The magnitude above which a timestamp is read as milliseconds. The threshold
#: is the year 5138 in seconds, which no clock this century reaches.
_MILLISECOND_THRESHOLD = 1e11


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


def mint_signing_value() -> str:
    """A fresh signing value for a new trigger.

    ``token_urlsafe`` rather than ``token_hex``: the value is copied by a person
    into an external service, and a value with no lookalike characters is one
    fewer support ticket. 32 bytes is the standard size for a shared secret.
    """
    import secrets

    return secrets.token_urlsafe(32)


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
        return False, REASON_SIGNATURE_MISSING if not signature else REASON_TIMESTAMP_MISSING

    if window_seconds is not None:
        sent = _normalise_epoch(timestamp)
        if sent is None:
            return False, REASON_TIMESTAMP_MISSING
        reference = (now or datetime.now(timezone.utc)).timestamp()
        if abs(reference - sent) > window_seconds:
            return False, REASON_SIGNATURE_STALE

    expected = sign(secret, timestamp, raw_body)
    if hmac.compare_digest(signature.strip().lower(), expected.lower()):
        return True, ""
    return False, REASON_SIGNATURE_MISMATCH


def _normalise_epoch(value: Any) -> float | None:
    """A sender's timestamp in seconds, whatever unit it arrived in.

    The magnitude decides, so a sender that puts milliseconds in the header is
    still a sender.
    """
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number / 1000.0 if number > _MILLISECOND_THRESHOLD else number
