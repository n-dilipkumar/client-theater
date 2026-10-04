"""The signing rule, which is the exact rule the research quotes.

This module has one idea and it is not this build's. Quoted:

* "**X-Chili-Signature** | HMAC-SHA256 signature of the payload (hex-encoded)"
* "**X-Chili-Timestamp** | Unix timestamp (seconds) when the request was signed"
* "Step 2: Construct the signed payload by concatenating the timestamp and the
  raw request body, separated by a period: ``{timestamp}.{raw_request_body}``"
* "Signature mismatch | Ensure you're verifying against the raw request body,
  not a re-serialised/parsed JSON object."

So the signing input is ``f"{timestamp}.{raw_body}"``, the digest is hex encoded,
and the raw body is the body that was sent. Nothing here is a choice. Changing
:func:`signing_input` changes what every subscriber would have to compute, so it
is one function and the reason is written on it.

Replay protection is the consumer's, not the sender's
-----------------------------------------------------
The research gives the window as the consumer's and says so: *"Replay protection
is left to the consumer (``MAX_AGE_SECONDS = 300``)"*. So :func:`verify` accepts
an optional window and the sender never sets one. A sender that enforced the
window would refuse a correctly signed request whose clock runs fast, and that is
a clock bug wearing the costume of a security control. :data:`MAX_AGE_SECONDS` is
published so the *reader* knows what to apply, and
:func:`~dsr.meeting_webhook_fanout.vocabulary.describe` serves it.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone

from dsr.meeting_webhook_fanout.vocabulary import (
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
)

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

#: The consumer's freshness window, quoted and not enforced here. See the module
#: docstring.
MAX_AGE_SECONDS = 300


def signing_input(timestamp: str, raw_body: str) -> str:
    """The exact bytes a signature covers, in the vendor's order.

    Timestamp, then a period, then the raw request body. Quoted verbatim above.
    A signature computed over a re-serialised JSON object does not verify, and
    the research names that as the cause of a signature mismatch - so
    ``raw_body`` is the body that was sent, never a re-encoding of it.
    """
    return f"{timestamp}.{raw_body}"


def unix_seconds(when: datetime | None = None) -> str:
    """A unix timestamp in seconds, as the header documents.

    Seconds and not milliseconds: the header documentation says *"Unix timestamp
    (seconds) when the request was signed"*. A float is floored rather than
    rounded, so the number a subscriber recomputes is the number that was sent.
    """
    moment = when or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return str(int(moment.timestamp()))


def sign(secret: str, timestamp: str, raw_body: str) -> str:
    """The hex HMAC-SHA256 a subscriber recomputes to check this delivery.

    Exposed so a test, a CLI, and a person writing a subscriber all compute the
    same value this module produces, rather than each re-deriving the canonical
    string and getting it subtly wrong. Hex and not base64, because the header
    documentation says *"(hex-encoded)"*.
    """
    return hmac.new(
        secret.encode("utf-8"),
        signing_input(timestamp, raw_body).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def headers_for(secret: str, timestamp: str, raw_body: str) -> dict[str, str]:
    """The two headers a subscriber needs, already signed."""
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
    window_seconds: int | None = None,
    now: datetime | None = None,
) -> tuple[bool, str]:
    """Check one delivery the way a subscriber would.

    Returns ``(ok, reason)`` where ``reason`` is empty on success. The reasons are
    ``signature_missing``, ``timestamp_missing``, ``signature_stale`` and
    ``signature_mismatch``, which are four different mistakes and a reader fixing
    one is not fixing another.

    ``window_seconds`` is ``None`` by default and that is deliberate: the sender
    never refuses on age, because the research puts replay protection on the
    consumer. A subscriber that *is* a consumer passes its own window here, and
    the shipped example uses :data:`MAX_AGE_SECONDS`.

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

    The header documents seconds and that is what this build sends. A sender that
    puts milliseconds in the same header is still a sender, so the magnitude
    decides. The threshold is the year 5138 in seconds, which no clock this
    century reaches.
    """
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number / 1000.0 if number > 1e11 else number
