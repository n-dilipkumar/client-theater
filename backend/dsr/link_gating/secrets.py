"""Credential primitives for WF-069. Nothing here persists anything.

The research for this workflow puts three secrets in front of a buyer link - a
password, a one-time code, and the view token that says the buyer passed - and
the audit log is a permanent record of every write this workflow makes. So the
rule this module exists to make enforceable is the simplest one:

    **No cleartext secret is ever written to a record payload, an audit row, a
    log line, a response body, or an error message.**

Every function here returns either a hash or a minted value, and every function
that takes a secret takes it as an argument and returns nothing about it. The
hash format is self-describing, so the work factor can be raised later without a
migration:

    ``pbkdf2_sha256$<iterations>$<base64 salt>$<hex digest>``

Two callers share the format with different work factors, and the difference is
deliberate:

* :func:`hash_password` runs at 120,000 iterations, because a link password is
  the durable secret and the check happens once per buyer session.
* :func:`hash_code` runs at :data:`CODE_ITERATIONS`, ten times lower, because a
  one-time code is drawn from a space of a million, is good for a single use,
  and is checked while a buyer is staring at a spinner. Buying extra brute-force
  resistance there costs a visible latency on every step and protects a secret
  that is already worthless once used.

Neither is Argon2 or bcrypt, because this backend is deliberately
zero-dependency and ``hashlib`` ships with CPython. That is a real limitation
rather than a preference, and it is recorded here so the choice is visible to a
reviewer instead of looking like an oversight.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
from typing import Any, Mapping

#: PBKDF2 work factor for a durable link password.
PASSWORD_ITERATIONS = 120_000

#: PBKDF2 work factor for a one-time code. See the module docstring.
CODE_ITERATIONS = 12_000

#: The hash-format tag. Written into every stored hash so the format is
#: recognisable without out-of-band configuration.
PREFIX = "pbkdf2_sha256"

#: Six digits, which is what every transactional email one-time code has been
#: for thirty years and what a buyer can read off a phone screen without a
#: keyboard. Leading zeros are preserved: the code is a string, never an int.
CODE_DIGITS = 6

_HASH_RE = re.compile(
    r"^pbkdf2_sha256\$(?P<iterations>\d+)\$(?P<salt>[A-Za-z0-9+/=]+)\$(?P<digest>[0-9a-f]+)$"
)


def _derive(secret: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt, iterations)


def _encode(secret: str, salt: bytes, iterations: int) -> str:
    digest = _derive(secret, salt, iterations)
    salt_b64 = base64.b64encode(salt).decode("ascii")
    return f"{PREFIX}${iterations}${salt_b64}${digest.hex()}"


def _decode(stored: Any) -> tuple[int, bytes, str] | None:
    """Parse a stored hash, or return ``None`` if it is not one of ours.

    Returning ``None`` rather than raising is the point. A stored hash can be
    truncated, migrated, or written by a human with ``db.create``; a verifier
    that raises on malformed input turns a corrupt row into a 500 on a route a
    buyer is standing on. A corrupt row is a refusal, not a crash.
    """
    if not isinstance(stored, str):
        return None
    match = _HASH_RE.match(stored)
    if match is None:
        return None
    try:
        iterations = int(match.group("iterations"))
        salt = base64.b64decode(match.group("salt"), validate=True)
    except (ValueError, TypeError):
        return None
    if iterations < 1 or not salt:
        return None
    return iterations, salt, match.group("digest")


def hash_password(
    password: str, *, salt: str | None = None, iterations: int = PASSWORD_ITERATIONS
) -> str:
    """Hash a link password for storage. The cleartext is never persisted."""
    if not password:
        raise ValueError("password is required")
    salt_bytes = base64.b64decode(salt) if salt else os.urandom(16)
    return _encode(password, salt_bytes, iterations)


def verify_password(password: str, stored: Any) -> bool:
    """Constant-time check of a candidate password against a stored hash.

    ``hmac.compare_digest`` rather than ``==`` so the comparison time does not
    leak how much of a guessed password was correct.
    """
    parsed = _decode(stored)
    if parsed is None:
        return False
    iterations, salt, digest = parsed
    candidate = _derive(password or "", salt, iterations).hex()
    return hmac.compare_digest(candidate, digest)


def mint_code(*, digits: int = CODE_DIGITS) -> str:
    """A fresh numeric one-time code, as a string so leading zeros survive."""
    upper = 10**digits
    return str(secrets.randbelow(upper)).zfill(digits)


def hash_code(code: str, *, salt: str | None = None, iterations: int = CODE_ITERATIONS) -> str:
    """Hash a one-time code for storage.

    Same format as a password hash, lower work factor. The code is a bearer
    value for a single step of a single viewer request, so what matters is that
    it never sits in a record payload - not that it resists an offline attack on
    a value that stops being useful the moment it is used.
    """
    if not code:
        raise ValueError("code is required")
    salt_bytes = base64.b64decode(salt) if salt else os.urandom(16)
    return _encode(code, salt_bytes, iterations)


def verify_code(code: str, stored: Any) -> bool:
    """Constant-time check of a candidate one-time code."""
    parsed = _decode(stored)
    if parsed is None:
        return False
    iterations, salt, digest = parsed
    candidate = _derive(code or "", salt, iterations).hex()
    return hmac.compare_digest(candidate, digest)


def mint_view_token() -> str:
    """The token handed to a buyer who has cleared the whole gate.

    It carries no meaning of its own: it is looked up in the view-session
    collection, where the link, the verified email and the granting time live.
    That is deliberate. A self-describing token would have to encode the buyer,
    and an encoded token is a token that has to be a secret.
    """
    return f"wf069_view_{secrets.token_urlsafe(32)}"


def hash_token(token: str) -> str:
    """Digest a high-entropy bearer token for lookup.

    Plain SHA-256, not PBKDF2, and the asymmetry with :func:`hash_password` is
    correct rather than an inconsistency. A password is a low-entropy secret a
    dictionary can attack offline, so it needs a work factor. This token is 256
    bits from ``secrets``: there is nothing to dictionary, so stretching it
    would only add latency to every document read. Hashing it still matters,
    because the token is stored in a record payload and a record payload is
    mirrored to disk and written to the audit log.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def mint_link_id() -> str:
    """The public handle a buyer follows.

    The research has the platform "mint a public view URL and return the link id
    + URL", so the record id *is* the public handle. Minting it with a
    recognisable prefix costs nothing and makes an id in a log or a support
    ticket self-describing.
    """
    return f"wf069_link_{secrets.token_hex(12)}"


def mint_delivery_id() -> str:
    return f"wf069_msg_{secrets.token_hex(12)}"


#: Keys this module owns. :func:`redact` strips them from anything on its way out.
#:
#: ``view_token`` is deliberately **not** in this set, which needs saying because it
#: looks like an oversight. The view token is the buyer's own credential and the
#: whole point of the grant is to hand it to them: the response that says "you are
#: through" is the only response that carries it, once, at the moment it is issued.
#: Redacting it there produced a 200 that said ``granted: true`` and then gave the
#: buyer nothing to present, which is a gate that opens onto a wall.
#:
#: What keeps that safe is not the deny-list but the other two properties: the token
#: is stored only as ``token_hash``, and it appears in exactly one response and in no
#: record, no audit row and no log. ``tests/test_wf069.py`` asserts that directly -
#: the token is looked for in every other response the router can produce.
SECRET_KEYS = frozenset(
    {
        "password_hash",
        "password",
        "code_hash",
        "code",
        "token_hash",
    }
)


def redact(value: Any) -> Any:
    """A copy of ``value`` with every secret key removed.

    Applied to every response and every error message this workflow produces, so
    that no endpoint can echo a hash by accident - not now, and not later when
    someone adds a field and a route in the same commit. Nested objects are
    walked, because the generic record API hands back whole payloads and a
    top-level-only filter would miss a hash nested three levels down.
    """
    if isinstance(value, Mapping):
        return {key: redact(child) for key, child in value.items() if key not in SECRET_KEYS}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value
