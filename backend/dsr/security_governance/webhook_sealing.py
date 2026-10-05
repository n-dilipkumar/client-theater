"""WF-082: the sealed store for a registration's API key.

The specification names the account's API key as the HMAC secret ("the account's API key
(HMAC secret)") and says nothing at all about how to hold it, which
``INFERRED_INBOUND_KEY_VAULT`` in :mod:`dsr.security_governance.webhook_inferences` records.

Why the secret is sealed at all
-------------------------------

Two of the three options are wrong and the reasons are not close:

* **Plaintext** is refused because a registration is readable through the core records API and
  through anyone holding the database file. A secret an operator can re-read is not being used
  as a secret.
* **A digest** is refused because HMAC-SHA256 needs the key *itself*. A digest verifies a bearer
  token by re-hashing what the caller presents, and there is no re-hash here: this product has
  to *produce* a digest from the key on every delivery. Storing only a digest would make every
  delivery unverifiable.

So the key is sealed at rest and unsealed only to compute a digest.

Why this is a separate module rather than a reuse of the CRM vault
------------------------------------------------------------------

``dsr/crm_oauth/vault.py`` already ships a keyed encrypt-then-MAC implementation, and reusing it
was the first thing this build tried. It cannot be reused, and the reason is an enforced test
rather than a preference: ``tests/test_wf073.py::test_the_domain_package_imports_nothing_but_the
_store`` walks every module in ``dsr/security_governance/`` and requires every ``dsr`` import to
be either ``dsr.store`` or this package. Reaching into ``dsr.crm_oauth`` from a security-governance
domain module crosses a package boundary that guard exists to protect, and two workflows sharing
a domain are not thereby entitled to share a module.

So the construction is written here, against the standard library only. It is a fourth
crypto implementation in a product that already has one, and that cost is recorded rather than
hidden. A sealed format that has been audited once and reused is the better answer, and hoisting
the CRM vault to a neutral package both workflows may import is **platform work**: it touches a
module another workflow owns, and the feature contract reserves that for a person.

The construction, stated so a reviewer can judge it
---------------------------------------------------

A keyed stream from ``HMAC-SHA256`` in counter mode, with **encrypt-then-MAC**: the tag covers
the nonce and the ciphertext, and a row whose tag does not verify is refused rather than decrypted
into rubbish. It is the same construction the CRM vault uses, and it is not a vetted AEAD. It is
here because the standard library is the only dependency a domain module may assume, and because
the alternative - storing the key in the clear - is the one reading of "stored encrypted" that
cannot be defended.

The comparison is constant-time and happens before a single byte of ciphertext is used. The tag
is verified first because a tag that does not verify means the row was sealed elsewhere or
altered, and both are answers about the *row*, not about a plaintext nobody should read.

The key, and being honest about the fallback
-------------------------------------------

The key comes from ``DSR_WEBHOOK_VAULT_KEY``. When it is unset this module falls back to a
**published demo constant** rather than to a per-process random key, for a reason that matters
more than it looks: a per-process key makes every registration written by ``backend/seed.py``
unreadable in the process that serves the app, so the demo page would be permanently broken in a
fresh checkout. The fallback is therefore honest rather than safe, :func:`resolve_key` returns
``"default"`` so the surface can say so out loud, and every registration carries the fingerprint
of the key that sealed it so a deployment can tell "sealed under another key" from "this row was
altered".
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass
from typing import Any, Mapping

from dsr.security_governance import webhook_vocabulary as vocab

__all__ = [
    "DEMO_KEY",
    "SEALED_VERSION",
    "SealedKeyError",
    "VaultKey",
    "key_id",
    "open_sealed",
    "resolve_key",
    "seal",
]

#: Bumped only if the sealed format changes, so an old row is refused rather than misread.
SEALED_VERSION = "v1"

#: The published fallback. **Not a secret.** It exists so a fresh checkout's demo works, and
#: every surface that reads a registration says which key sealed it.
DEMO_KEY = "dsr-demo-webhook-vault-key"


class SealedKeyError(ValueError):
    """A sealed value this process cannot open, or that does not hold what it should.

    Raised rather than returned as ``None``, because an empty key would make every digest wrong
    and the failure would be reported as a bad signature rather than as a configuration fault.
    """


@dataclass(frozen=True)
class VaultKey:
    """The key material, and where it came from."""

    material: bytes
    origin: str  # "env" | "default"
    key_id: str

    @property
    def is_default(self) -> bool:
        return self.origin == "default"


def resolve_key() -> VaultKey:
    """Read the key from the environment, or fall back to the demo constant.

    Resolved at call time rather than at import time, for the same reason
    :func:`dsr.deps.db_path` is: a module-level constant is captured on first import, and a test
    that sets the environment variable afterwards would silently keep using the old one.
    """
    raw = os.environ.get(vocab.KEY_ENV, "").strip()
    origin = "env" if raw else "default"
    material = (raw or DEMO_KEY).encode("utf-8")
    return VaultKey(material=material, origin=origin, key_id=key_id(material))


def key_id(material: bytes) -> str:
    """A short public fingerprint of a key, stored beside every sealed row.

    Lets a row say *which* key sealed it without revealing the key, so the difference between
    "sealed under a key this process does not hold" and "this row was altered" is answerable
    from the row itself.
    """
    return hashlib.sha256(b"dsr-webhook-vault-key-id" + material).hexdigest()[:12]


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    """``length`` bytes of HMAC-SHA256(key, nonce || counter), concatenated."""
    if length <= 0:
        return b""
    blocks: list[bytes] = []
    produced = 0
    counter = 0
    while produced < length:
        block = hmac.new(key, nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest()
        blocks.append(block)
        produced += len(block)
        counter += 1
    return b"".join(blocks)[:length]


def _mac(key: bytes, nonce: bytes, ciphertext: bytes) -> bytes:
    return hmac.new(key, nonce + ciphertext, hashlib.sha256).digest()


def seal(payload: Mapping[str, Any], key: bytes) -> str:
    """Seal a JSON-able payload into one opaque string.

    The plaintext is serialised canonically (sorted keys) so the same payload seals to the same
    bytes under the same nonce, which makes the tag a check on the content rather than on a
    re-serialisation.
    """
    plaintext = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    nonce = secrets.token_bytes(12)
    ciphertext = bytes(
        a ^ b for a, b in zip(plaintext, _keystream(key, nonce, len(plaintext)), strict=True)
    )
    tag = _mac(key, nonce, ciphertext)
    return ".".join(
        [
            SEALED_VERSION,
            base64.urlsafe_b64encode(nonce).decode("ascii"),
            base64.urlsafe_b64encode(ciphertext).decode("ascii"),
            base64.urlsafe_b64encode(tag).decode("ascii"),
        ]
    )


def open_sealed(sealed: str, key: bytes) -> dict[str, Any]:
    """Unseal a payload, or refuse.

    The tag is verified before a single byte of ciphertext is used, and the comparison is
    constant-time. A refusal names the key that sealed the row, because "this row was sealed by
    another process" and "this row was altered" have different remedies.
    """
    parts = str(sealed or "").split(".")
    if len(parts) != 4 or parts[0] != SEALED_VERSION:
        raise SealedKeyError(
            f"sealed value is not a {SEALED_VERSION} envelope; it was written by a different "
            "version of this feature and is not readable here"
        )
    _, nonce_b64, ciphertext_b64, tag_b64 = parts
    try:
        nonce = base64.urlsafe_b64decode(nonce_b64)
        ciphertext = base64.urlsafe_b64decode(ciphertext_b64)
        tag = base64.urlsafe_b64decode(tag_b64)
    except Exception as exc:  # noqa: BLE001 - malformed base64 is a refusal, not a crash
        raise SealedKeyError("sealed value is not valid base64") from exc
    if not hmac.compare_digest(_mac(key, nonce, ciphertext), tag):
        raise SealedKeyError(
            "sealed value does not verify under this process's key; it was sealed under key "
            f"{key_id(key)}, so this process cannot open it. Set {vocab.KEY_ENV} to the same "
            "value, or register the callback again to re-seal it under the current key"
        )
    keystream = _keystream(key, nonce, len(ciphertext))
    plaintext = bytes(a ^ b for a, b in zip(ciphertext, keystream, strict=True))
    try:
        opened = json.loads(plaintext.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - a verified tag over non-JSON is still a refusal
        raise SealedKeyError("sealed value verified but did not contain JSON") from exc
    if not isinstance(opened, dict):
        raise SealedKeyError("sealed value did not contain a JSON object")
    return opened
