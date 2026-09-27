"""The signing secret, the signature, and the rotation overlap.

Sourced
-------
The research says two things about this, and only two:

* Dock: "Delivery reliability: Dock documents a secret for verifying requests",
  and the user flow is "Copy the generated **secret** (View Key) and verify each
  delivery's signature."
* Seismic: "Seismic sends a ``x-seismic-signature`` header with each webhook
  request. This header contains a HMAC signature of the request body", plus
  "signing-secret rotation with ``x-seismic-signature-old``".

So: a secret exists, it is generated rather than chosen, it is copyable by the
operator ("View Key"), the delivery is signed, and rotation overlaps for a
while. Everything below is the implementation of exactly that.

What is an inference, and named as one
--------------------------------------
* The **algorithm** (HMAC-SHA256) and the **digest encoding**. The research says
  "a HMAC signature" without naming the hash or the encoding.
* The **header names**. The research quotes Seismic's ``x-seismic-signature``,
  which is Seismic's header for Seismic's payloads; sending that name with our
  payload would be a lie to anyone reading their logs. This package uses its own
  ``X-DSR-Signature`` and names it in :data:`SIGNATURE_HEADER`.
* **When the previous secret is dropped.** See
  :func:`rotation_overlap` and the ``key-rotation-overlap`` entry in
  :mod:`dsr.event_stream.inferences`.

Rotation, in one paragraph
-------------------------
:func:`rotate` keeps the outgoing secret for exactly one generation. Until that
new secret has been used successfully, or the secret is rotated again, every
delivery carries **both** signatures: ``X-DSR-Signature`` under the new secret
and ``X-DSR-Signature-Old`` under the old one. That is the whole point of
``x-seismic-signature-old``: a subscriber that has not yet deployed the new
secret can still verify a delivery, so rotating a signing key does not break
every integration at once.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Any, Mapping

#: Prefix on a generated secret, so a secret pasted into a log is recognisable
#: and a rep can tell it from an API token at a glance.
SECRET_PREFIX = "whsec_"

#: Bytes of entropy behind the prefix. 32 bytes is 256 bits, which is the usual
#: choice for a signing key and is far more than an operator will ever rotate.
SECRET_BYTES = 32

ALGORITHM = "HMAC-SHA256"

#: The header carrying the signature of the exact bytes sent.
SIGNATURE_HEADER = "X-DSR-Signature"

#: The header carrying the signature under the *previous* secret, during the
#: rotation overlap. Named for the researched ``x-seismic-signature-old``.
PREVIOUS_SIGNATURE_HEADER = "X-DSR-Signature-Old"

#: The header naming the event, so a subscriber can route without parsing the
#: body. Additive; the body is authoritative.
EVENT_HEADER = "X-DSR-Event"

#: A stable id for this delivery, for a subscriber deduplicating on.
DELIVERY_HEADER = "X-DSR-Delivery"

#: How much of a secret is shown when it is masked.
MASK_KEEP = 4


def generate_secret() -> str:
    """A fresh signing secret.

    Generated, never accepted from the caller. The research's flow is "Create
    Webhook, name it, enter the HTTPS target URL ... Copy the generated
    secret", so the secret is a product artefact: accepting one from the client
    would let a caller pick a secret they then disclose.
    """
    return SECRET_PREFIX + secrets.token_urlsafe(SECRET_BYTES)


def sign(secret: str | None, body: bytes) -> str | None:
    """HMAC-SHA256 over the exact bytes being sent, or ``None`` without a secret.

    Over the *bytes*, not the serialised-then-reserialised object: a signature
    only means anything if what is verified is byte-identical to what was sent,
    and ``json.dumps`` is not required to be stable across two calls.
    """
    if not secret:
        return None
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify(secret: str | None, body: bytes, signature: str | None) -> bool:
    """Whether ``signature`` is the HMAC of ``body`` under ``secret``.

    Constant-time on purpose: a comparison that returns early on the first
    differing byte is a byte-at-a-time oracle, and this is exactly the function
    a subscriber would write while looking at this one.
    """
    expected = sign(secret, body)
    if expected is None or signature is None:
        return False
    return hmac.compare_digest(expected, signature)


def sign_headers(
    body: bytes,
    *,
    secret: str | None,
    previous_secret: str | None = None,
    event: str = "",
    delivery_id: str = "",
) -> dict[str, str]:
    """The headers one delivery goes out with.

    Both signatures are present during a rotation overlap, and both are omitted
    when there is no secret at all - a webhook with no secret is unsigned rather
    than signed with an empty key, which would be worse than no signature.
    """
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "digital-sales-room-event-stream/1.0",
        "Accept": "application/json",
    }
    if event:
        headers[EVENT_HEADER] = event
    if delivery_id:
        headers[DELIVERY_HEADER] = delivery_id
    current = sign(secret, body)
    if current:
        headers[SIGNATURE_HEADER] = current
    older = sign(previous_secret, body)
    if older:
        headers[PREVIOUS_SIGNATURE_HEADER] = older
    return headers


def mask(secret: str | None) -> str | None:
    """What every read path shows instead of the secret.

    ``whsec_…3QkF``. Enough to tell two webhooks apart in a list, useless to
    anyone reading over a shoulder or in an audit mirror.
    """
    if not secret:
        return None
    if len(secret) <= len(SECRET_PREFIX) + MASK_KEEP:
        return SECRET_PREFIX + "…"
    return secret[: len(SECRET_PREFIX)] + "…" + secret[-MASK_KEEP:]


def rotation_overlap(webhook: Mapping[str, Any]) -> str | None:
    """The previous secret still offered alongside the current one, or ``None``.

    ``None`` ends the overlap. Two things end it, and only two:

    * a delivery has **succeeded** under the new secret, so the overlap has
      demonstrably done its job; or
    * the secret has been rotated **again**, so only one generation of overlap
      is ever in flight.

    Ended by neither of those, the overlap stays open however long it takes,
    because a subscriber that has not deployed the new secret is exactly the
    case the overlap exists for, and silently closing the window on a schedule
    nobody agreed to is how a rotation becomes an outage.
    """
    if not webhook.get("previous_secret"):
        return None
    if webhook.get("previous_secret_confirmed"):
        return None
    return str(webhook["previous_secret"])


def confirm_rotation(webhook: Mapping[str, Any]) -> dict[str, Any]:
    """The patch that closes a rotation overlap after a successful delivery.

    ``previous_secret_confirmed`` rather than clearing ``previous_secret``, so
    the fact that a rotation happened and was confirmed survives the patch: a
    reader of the record can see there *was* an overlap and when it ended.
    """
    if not webhook.get("previous_secret"):
        return {}
    return {"previous_secret_confirmed": True}
