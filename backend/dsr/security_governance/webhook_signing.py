"""WF-082: the two keyed digests a provider callback carries.

The specification names two of them and they are not interchangeable, which is the
single most important fact in this module:

======================================  =================================  ===========
check                                  covers                              encoding
======================================  =================================  ===========
:data:`~webhook_vocabulary.CONTENT_SHA256`  the whole JSON payload            base64
``event_hash``                          ``event_time`` + ``event_type``     hex
======================================  =================================  ===========

Both are ``HMAC-SHA256(api_key, ...)``. The evidence gives both shell lines:

* ``echo -n $event_time$event_type | openssl dgst -sha256 -hmac $apikey``
* ``echo -n $json | openssl dgst -sha256 -hmac $apiKey``

``$event_time$event_type`` is a concatenation with **no separator**. That is one
character, and it is the character a reader supplies out of habit: the natural
spelling ``f"{event_time}:{event_type}"`` verifies nothing and rejects every real
delivery. So the canonical string is one function, named for what it is, and the
tests check it against the exact bytes ``openssl dgst`` would read.

``echo -n`` is the other half of it. It means no trailing newline, so the digest
covers exactly the bytes of the concatenation and not a line terminator. A caller that
appends ``"\\n"`` produces a different digest and a different failure.

Constant time, because it is free
---------------------------------

Both comparisons go through :func:`constant_time_equals`, which compares the decoded
digest bytes and never returns early. The specification says of the ``event_hash``
check only that the handler "recomputes the ``event_hash`` HMAC ... and compares", and
the sibling ticket says "compares ... in constant time". The reason to keep the
constant-time form anyway is that it costs one function call and no cleverness, and a
short-circuiting comparison of a MAC is the textbook way to turn a timing side
channel into a forged signature. It is here because it is free, not because the
evidence demanded it.

Why a hex and a base64 are both accepted for ``event_hash``
-----------------------------------------------------------

The evidence quotes ``openssl dgst -sha256``, whose default output is hex, so hex is
what a conforming sender sends. A base64 form is accepted as well, for one reason:
a signature is the one place where refusing a second encoding costs a working
integration and gains nothing. Both forms are unforgeable without the secret, the
caller can see which one it was told to send, and the answer records which encoding
matched. The same tolerance is **not** extended to ``Content-Sha256``, where the
evidence names base64 explicitly and a base64 parser on the receiving side is what
makes the header unambiguous.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
from typing import Any, Mapping

from dsr.security_governance import webhook_vocabulary as vocab

__all__ = [
    "canonical_event_string",
    "constant_time_equals",
    "content_sha256",
    "decode_digest",
    "event_hash",
    "verify_content_sha256",
    "verify_event_hash",
]

#: The two encodings an ``event_hash`` may arrive in, hex first because that is what
#: the evidence quotes.
EVENT_HASH_ENCODINGS = ("hex", "base64")


def canonical_event_string(event_time: Any, event_type: Any) -> str:
    """The exact bytes ``event_hash`` covers: the two values, concatenated.

    The concatenation uses
    :data:`~dsr.security_governance.webhook_vocabulary.EVENT_HASH_SEPARATOR`, which is
    the empty string. Naming it means the "no separator" rule is a thing a reader can
    check, rather than implied by a line of code that happens to do ``a + b``. It is
    interpolated rather than dropped so that the constant is load-bearing: if a future
    build ever needed a separator, this line would start honouring it.

    Both values are stringified rather than rejected when they are not strings. The
    fields arrive as JSON, and a provider that sent a numeric ``event_time`` is still
    a provider. Refusing the delivery over the type of a field whose value is correct
    would turn a working integration into an outage for no security gain. A field that
    is **absent** is a different matter, and the caller refuses that separately,
    because a missing field cannot be concatenated at all.
    """
    return f"{event_time}{vocab.EVENT_HASH_SEPARATOR}{event_type}"


def event_hash(api_key: str, event_time: Any, event_type: Any) -> str:
    """The hex ``event_hash`` a sender produces for this event."""
    return _digest(api_key, canonical_event_string(event_time, event_type)).hex()


def content_sha256(api_key: str, payload: bytes) -> str:
    """The base64 ``Content-Sha256`` header value for a payload.

    ``payload`` is bytes, not a string, and not a dict. The digest covers the bytes
    the handler actually received, so anything that re-encodes the body on the way -
    a proxy, a framework that parsed and re-serialised the form, a JSON library's
    key sorting - changes the digest. Passing the received bytes is the only way this
    function can be right about what it is checking.
    """
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    return base64.b64encode(_digest(api_key, payload)).decode("ascii")


def _digest(api_key: str, payload: str | bytes) -> bytes:
    material = payload.encode("utf-8") if isinstance(payload, str) else payload
    return hmac.new(str(api_key or "").encode("utf-8"), material, hashlib.sha256).digest()


def decode_digest(value: str, encoding: str) -> bytes | None:
    """Decode a presented digest, or return ``None`` if it is not that encoding.

    ``None`` rather than an exception, because a header that is not valid base64 is a
    refusal with a name, not a crash: the caller records ``content_sha256_malformed``
    and answers the provider with the catalogue entry rather than a 500.
    """
    text = str(value or "").strip()
    if not text:
        return None
    if encoding == "base64":
        try:
            return base64.b64decode(text, validate=True)
        except (binascii.Error, ValueError):
            return None
    if encoding == "hex":
        try:
            return bytes.fromhex(text)
        except ValueError:
            return None
    return None


def constant_time_equals(left: bytes, right: bytes) -> bool:
    """Compare two digests without an early exit.

    ``hmac.compare_digest`` is the stdlib's constant-time comparison and is used
    directly. Length is the one thing it leaks, and length is not a secret here: every
    SHA-256 digest is 32 bytes, so a wrong length is a wrong encoding rather than a
    guess.
    """
    return hmac.compare_digest(left, right)


def verify_content_sha256(api_key: str, payload: bytes, presented: str | None) -> dict[str, Any]:
    """Check the ``Content-Sha256`` header against the payload as received.

    Three refusals and they are three different mistakes: the header was absent, the
    header was not base64, and the header was base64 and wrong. An operator fixes
    those three differently, so the catalogue gives each its own ``remediation``.

    The third one is not paranoid. A proxy that re-encodes the request body between
    the provider and this handler changes the digest, and the only useful answer for
    the person holding that proxy is "the header was base64, it parsed, and it did not
    match", rather than a single undifferentiated "signature invalid".
    """
    result: dict[str, Any] = {
        "check": vocab.CONTENT_SHA256,
        "passed": False,
        "reason": None,
        "expected": None,
        "encoding": "base64",
    }
    if presented is None or not str(presented).strip():
        result["reason"] = "content_sha256_missing"
        return result

    expected = content_sha256(api_key, payload)
    result["expected"] = expected
    offered = decode_digest(presented, "base64")
    if offered is None:
        result["reason"] = "content_sha256_malformed"
        return result
    if constant_time_equals(offered, _digest(api_key, payload)):
        result["passed"] = True
        return result
    result["reason"] = "content_sha256_mismatch"
    return result


def verify_event_hash(
    api_key: str,
    event: Mapping[str, Any],
    *,
    presented: str | None = None,
) -> dict[str, Any]:
    """Check the payload's ``event_hash`` against the two fields it covers.

    A missing ``event_hash`` and a missing ``event_type`` are refused separately,
    because the two mean different things to whoever has to fix the sender: one says
    the payload is not this provider's, the other says it is a provider payload whose
    filter key is missing.

    The HMAC input is built from ``event_time`` and ``event_type`` **only**. Every
    other field of the payload is covered by the other check, so widening the input
    here would be inventing a scheme the provider does not use and would reject every
    real delivery.
    """
    result: dict[str, Any] = {
        "check": vocab.EVENT_HASH,
        "passed": False,
        "reason": None,
        "expected": None,
        "encoding": None,
        "input": None,
    }
    event_time = event.get("event_time")
    event_type = event.get(vocab.EVENT_TYPE_FIELD)
    if event_type in (None, ""):
        result["reason"] = "event_type_missing"
        return result

    canonical = canonical_event_string(event_time, event_type)
    result["input"] = canonical
    expected_bytes = _digest(api_key, canonical)
    result["expected"] = expected_bytes.hex()

    offered_text = presented if presented is not None else event.get("event_hash")
    if offered_text is None or not str(offered_text).strip():
        result["reason"] = "event_hash_missing"
        return result

    for encoding in EVENT_HASH_ENCODINGS:
        offered = decode_digest(offered_text, encoding)
        if offered is not None and constant_time_equals(offered, expected_bytes):
            result["passed"] = True
            result["encoding"] = encoding
            return result

    result["reason"] = "event_hash_mismatch"
    return result