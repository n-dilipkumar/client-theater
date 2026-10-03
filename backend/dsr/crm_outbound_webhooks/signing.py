"""Request authentication: the three researched methods, and one of them verified.

The research names three ways a CRM-side webhook action can authenticate, and
this module implements exactly those three:

* ``signature`` - "To use a request signature in your webhook header: Click the
  **Authentication type** dropdown menu. Then, select **Include request signature
  in header**. Then, enter your HubSpot App ID."
* ``api_key`` - "Set the value of API key name to ``Authorization``. Set the value
  of API key location to ``Request Header**", or the API key in query params.
* ``bearer`` - "The secret value must be in the format ``Bearer [YOUR_TOKEN]``."

The signature **scheme** is the one thing here that these three sources do not
publish, and the module says so at the top rather than presenting it as sourced.
The research's extensibility line is explicit about the direction: "Because the
room can verify the request signature, it does not need a per-workflow secret.
Request-signature verification is a documented HubSpot capability the room can lean
on instead of inventing its own HMAC scheme." So this leans on the vendor's own
published v3 shape - HMAC-SHA256 over method, URI, body and timestamp, base64 in
the request-signature header - rather than inventing one. The header names, the
canonical string and the replay window are all the
``hubspot-request-signature-scheme`` inference, and all three are changeable in
one place.

A GET delivery signs an empty body, because there is no body to sign; the
properties arrive in the query string instead, which is why the research's
"both POST and GET" is honoured rather than treated as an oversight.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import datetime, timezone
from typing import Any, Mapping

from dsr.crm_outbound_webhooks.vocabulary import (
    API_KEY_LOCATIONS,
    BEARER_PREFIX,
    SIGNATURE_HEADER,
    SIGNATURE_TIMESTAMP_HEADER,
)

__all__ = [
    "canonical_string",
    "sign",
    "sign_hex",
    "verify",
]


def canonical_string(method: str, uri: str, body: str, timestamp: str) -> str:
    """The exact bytes a signature covers, in the vendor's order.

    Method, then the request URI as sent (query string included, because for a
    GET the query string *is* the payload), then the raw body, then the
    timestamp. Changing the order here changes what the CRM would have to sign,
    so it is one function and the reason is written on it.
    """
    return f"{method.upper()}\n{uri}\n{body}\n{timestamp}"


def _digest(secret: str, method: str, uri: str, body: str, timestamp: str) -> bytes:
    return hmac.new(
        secret.encode("utf-8"),
        canonical_string(method, uri, body, timestamp).encode("utf-8"),
        hashlib.sha256,
    ).digest()


def sign(secret: str, method: str, uri: str, body: str, timestamp: str) -> str:
    """The base64 HMAC-SHA256 signature a sender would produce.

    Exposed so a test, a CLI, and a person setting up the CRM-side action can
    all compute the same value this module verifies, rather than each of them
    re-deriving a canonical string and getting it subtly wrong.
    """
    return base64.b64encode(_digest(secret, method, uri, body, timestamp)).decode("ascii")


def sign_hex(secret: str, method: str, uri: str, body: str, timestamp: str) -> str:
    """The same digest, hex encoded - the other form a sender may present."""
    return _digest(secret, method, uri, body, timestamp).hex()


def _constant_time_equals(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def _normalise_epoch(value: str) -> float | None:
    """A sender's timestamp in seconds, whatever unit it arrived in.

    The vendor's header carries milliseconds, and the vocabulary publishes that
    unit. A sender that puts seconds in the same header is still a sender, and
    refusing it would be a units bug wearing the costume of a security control -
    so the magnitude decides. The threshold is the year 5138 expressed in seconds,
    which no clock this century reaches, so no plausible timestamp is misread.
    """
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number / 1000.0 if number > 1e11 else number


def _within_window(timestamp: str, *, now: datetime | None, window_seconds: int) -> bool:
    """Whether a signed timestamp is close enough to now to be accepted.

    Compared as a signed distance in either direction: a sender whose clock runs
    fast is as legitimate as one whose clock runs slow, and refusing it would be
    a bug dressed up as a security control. A timestamp that is not a number at
    all is outside the window, because an unparseable timestamp cannot be shown
    to be recent.
    """
    sent = _normalise_epoch(timestamp)
    if sent is None:
        return False
    reference = (now or datetime.now(timezone.utc)).timestamp()
    return abs(reference - sent) <= window_seconds


def verify(
    auth: Mapping[str, Any],
    *,
    method: str,
    uri: str,
    body: str,
    headers: Mapping[str, str] | None,
    query: Mapping[str, Any] | None,
    now: datetime | None = None,
    window_seconds: int | None = None,
) -> tuple[bool, str]:
    """Check one request against the endpoint's configured authentication.

    Returns ``(ok, reason)`` where ``reason`` is empty on success and otherwise
    one of the keys in :data:`dsr.crm_outbound_webhooks.vocabulary.REASONS`, so
    the delivery log and the HTTP answer can carry the same word.

    Header lookup is case-insensitive, because HTTP header names are, and a sender
    that got the case wrong is still the sender. A missing header and a wrong
    value are different reasons because they are different mistakes, and the rep
    fixing one is not fixing the other.
    """
    mode = str(auth.get("mode") or "").strip().lower()
    headers = headers or {}
    query = query or {}
    secret = str(auth.get("secret") or "")
    window = int(
        auth.get("replay_window_seconds")
        if auth.get("replay_window_seconds") is not None
        else window_seconds
        if window_seconds is not None
        else 300
    )

    def header(name: str) -> str | None:
        for key, value in headers.items():
            if str(key).lower() == name:
                return None if value is None else str(value)
        return None

    if mode == "signature":
        return _verify_signature(
            secret,
            method=method,
            uri=uri,
            body=body,
            signature=header(SIGNATURE_HEADER),
            timestamp=header(SIGNATURE_TIMESTAMP_HEADER),
            now=now,
            window=window,
        )

    if mode == "api_key":
        name = str(auth.get("name") or "api_key").strip() or "api_key"
        location = str(auth.get("location") or "header").strip().lower()
        if location not in API_KEY_LOCATIONS:
            location = "header"
        presented: str | None = None
        if location == "query":
            for key, value in query.items():
                if str(key) == name:
                    presented = None if value is None else str(value)
                    break
        else:
            presented = header(name)
        if presented is None or not presented:
            return False, "api_key_missing"
        return (
            (True, "")
            if _constant_time_equals(presented, secret)
            else (
                False,
                "api_key_mismatch",
            )
        )

    if mode == "bearer":
        authorization = header("authorization")
        if authorization is None or not authorization.strip():
            return False, "bearer_missing"
        scheme, separator, token = authorization.strip().partition(" ")
        if not separator or scheme.lower() != BEARER_PREFIX.strip().lower() or not token.strip():
            return False, "bearer_malformed"
        return (
            (True, "")
            if _constant_time_equals(token.strip(), secret)
            else (
                False,
                "bearer_mismatch",
            )
        )

    # An endpoint whose authentication mode this build does not know is refused
    # at save time, so reaching here means the record was written by something
    # that did not go through the validator. Fails closed.
    return False, "api_key_mismatch"


def _verify_signature(
    secret: str,
    *,
    method: str,
    uri: str,
    body: str,
    signature: str | None,
    timestamp: str | None,
    now: datetime | None,
    window: int,
) -> tuple[bool, str]:
    if not signature or not timestamp:
        return False, "signature_missing"
    if not _within_window(timestamp, now=now, window_seconds=window):
        return False, "signature_stale"
    # The vendor's published encoding is base64. A hex digest is accepted as well
    # because a signature is the one place where being strict about encoding
    # costs a working integration and gains nothing: both are unforgeable without
    # the secret, and the caller can see which one it was told to send.
    for candidate in (
        sign(secret, method, uri, body, timestamp),
        sign_hex(secret, method, uri, body, timestamp),
    ):
        if _constant_time_equals(signature.strip(), candidate):
            return True, ""
    return False, "signature_mismatch"
