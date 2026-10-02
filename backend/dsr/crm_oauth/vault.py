"""The credential vault: sealed at rest, keyed by the org/account id, and never read over HTTP.

The research is specific about two of the three things this module does:

* "stores the refresh token in the integration's credential vault, **keyed by
  the org/account id**" - so the key is the org/account, not the connection, and
  a credential with no org/account id is refused rather than filed under a
  default;
* "access token (bearer) **stored encrypted**" - so the bytes on disk are not
  the token.

It says nothing at all about key management, so the cipher and the key source
are inferences, published as such in :mod:`dsr.crm_oauth.inferences`.

What is implemented here
------------------------

A keyed stream from ``HMAC-SHA256`` in counter mode, with **encrypt-then-MAC**:
the tag covers the nonce and the ciphertext, and a row whose tag does not
verify is refused rather than decrypted into rubbish. That is a standard
construction, and it is also not a vetted AEAD. It is here because the standard
library is the only dependency this feature is allowed to assume, and because
the alternative - storing the token in the clear - is the one reading of
"stored encrypted" that cannot be defended.

The key comes from ``DSR_CRM_VAULT_KEY``. When it is unset the module falls back
to a **published demo constant** rather than to a per-process random key, for a
reason that matters more than it looks: a per-process key makes every credential
written by ``backend/seed.py`` unreadable in the process that serves the app, so
the demo's "Test connection" button would be permanently broken in a fresh
checkout. The fallback is therefore honest rather than safe, and
:func:`key_origin` returns ``"default"`` so the surface can say so out loud - the
connections list, the health view and the frontend all carry the warning.
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

from dsr.crm_oauth.errors import VaultSealedError
from dsr.store import RecordStore

#: Where the key comes from. Named, because a deployment that is not going to
#: set the environment variable needs to know it is running on the constant.
KEY_ENV = "DSR_CRM_VAULT_KEY"

#: The published fallback. **Not a secret.** It exists so a fresh checkout's demo
#: works, and every surface that reads a credential says which key it used.
DEMO_KEY = "dsr-demo-vault-key"

#: The org key the app-level client secret is filed under. The client secret is
#: a credential of the same kind as the token, and the research names the vendor
#: screen it comes from (HubSpot's "Developer Platform → app Auth page (client ID
#: / client secret)"), so it is sealed rather than kept in the settings row.
APP_ORG_KEY = "app"

#: Bumped only if the sealed format changes, so an old row is refused rather
#: than misread.
SEALED_VERSION = "v1"


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
    :func:`dsr.deps.db_path` is: a module-level constant would be captured on
    first import and a test that set the environment variable afterwards would
    silently keep using the old one.
    """
    raw = os.environ.get(KEY_ENV, "").strip()
    origin = "env" if raw else "default"
    material = (raw or DEMO_KEY).encode("utf-8")
    return VaultKey(material=material, origin=origin, key_id=key_id(material))


def key_id(material: bytes) -> str:
    """A short public fingerprint of a key, stored beside every sealed row.

    Lets a row say *which* key sealed it without revealing the key, so the
    difference between "sealed under a key this process does not hold" and
    "this row has been altered" is answerable from the row itself.
    """
    return hashlib.sha256(b"dsr-crm-vault-key-id" + material).hexdigest()[:12]


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

    The plaintext is serialised canonically (sorted keys) so the same payload
    seals to the same bytes under the same nonce - which makes the MAC a check on
    the content rather than on a re-serialisation.
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

    The tag is verified before a single byte of ciphertext is used, and the
    comparison is constant-time. A refusal names the key that sealed the row
    when it can tell, because "this row was sealed by another process" and
    "this row was altered" have different remedies.
    """
    parts = str(sealed or "").split(".")
    if len(parts) != 4 or parts[0] != SEALED_VERSION:
        raise VaultSealedError(
            f"sealed value is not a {SEALED_VERSION} envelope; it was written by a "
            "different version of this feature and is not readable here"
        )
    _, nonce_b64, ciphertext_b64, tag_b64 = parts
    try:
        nonce = base64.urlsafe_b64decode(nonce_b64)
        ciphertext = base64.urlsafe_b64decode(ciphertext_b64)
        tag = base64.urlsafe_b64decode(tag_b64)
    except Exception as exc:  # noqa: BLE001 - malformed base64 is a refusal, not a crash
        raise VaultSealedError("sealed value is not valid base64") from exc
    if not hmac.compare_digest(_mac(key, nonce, ciphertext), tag):
        raise VaultSealedError(
            "sealed value does not verify under this process's key; it was "
            f"sealed under key {key_id(key)}, so this process cannot open it. "
            f"Set {KEY_ENV} to the same value, or re-authorize the connection to "
            "re-seal it under the current key"
        )
    keystream = _keystream(key, nonce, len(ciphertext))
    plaintext = bytes(a ^ b for a, b in zip(ciphertext, keystream, strict=True))
    try:
        opened = json.loads(plaintext.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - a verified tag over non-JSON is still a refusal
        raise VaultSealedError("sealed value verified but did not contain JSON") from exc
    if not isinstance(opened, dict):
        raise VaultSealedError("sealed value did not contain a JSON object")
    return opened


class CredentialVault:
    """Sealed rows in ``crm_credential``, addressed by (connection, org key).

    **No route in this feature reads a sealed row.** There is a truthful wrinkle
    worth stating rather than hiding: the core app's generic records API lists any
    collection, so ``GET /api/records/crm_credential`` will return these rows. That
    is safe *because* the payload is an opaque sealed string plus the field
    **names** it holds - a reader gets ``sealed``, ``fields``, ``org_key``,
    ``sealed_key_id`` and nothing that unseals anything - and the suite asserts it,
    so the safety is a checked property rather than a claim.

    What no code path does is open a sealed row. Only :meth:`read`, in this class,
    holds the key.
    """

    def __init__(self, store: RecordStore, *, key: VaultKey | None = None) -> None:
        self.store = store
        self.key = key or resolve_key()

    # -- key -------------------------------------------------------------- #

    @property
    def key_origin(self) -> str:
        return self.key.origin

    def key_warning(self) -> str:
        """The sentence a surface shows when the key is the published default."""
        if not self.key.is_default:
            return ""
        return (
            f"This process holds no {KEY_ENV}, so credentials are sealed with the "
            "feature's published demo key, which is in the source. Set it to a "
            "real secret before storing a real CRM credential."
        )

    # -- writes ----------------------------------------------------------- #

    def put(
        self,
        connection_id: str,
        org_key: str,
        payload: Mapping[str, Any],
        *,
        kind: str,
        actor: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """Seal a payload under an org key, replacing whatever was there.

        One row per (connection, org key). A second write updates rather than
        accumulates, so a re-authorization cannot leave a stale refresh token
        beside the current one.
        """
        if not org_key:
            raise ValueError("org_key is required: the vault is keyed by the org/account id")
        body = {
            "connection_id": connection_id,
            "org_key": org_key,
            "kind": kind,
            "sealed": seal(payload, self.key.material),
            "sealed_key_id": self.key.key_id,
            "fields": sorted(str(name) for name in payload),
        }
        existing = self.find(connection_id, org_key)
        if existing is None:
            return self.store.create("crm_credential", body, actor=actor, source=source)
        return self.store.update(
            existing["id"],
            body,
            actor=actor,
            source=source,
        )

    def delete(
        self,
        connection_id: str,
        org_key: str,
        *,
        actor: str | None = None,
        source: str | None = None,
    ) -> bool:
        """Drop one sealed row. Returns whether there was one to drop."""
        existing = self.find(connection_id, org_key)
        if existing is None:
            return False
        self.store.delete(existing["id"], actor=actor, source=source)
        return True

    # -- reads ------------------------------------------------------------ #

    def find(self, connection_id: str, org_key: str) -> dict[str, Any] | None:
        """The row itself, sealed. Used to decide whether a write is a create."""
        rows = self.store.find(
            "crm_credential",
            {"connection_id": connection_id, "org_key": org_key},
            limit=10,
        )
        for row in rows:
            if row["data"].get("org_key") == org_key:
                return row
        return None

    def read(self, connection_id: str, org_key: str) -> dict[str, Any] | None:
        """The unsealed payload, or ``None`` when there is no such row.

        Raises :class:`~dsr.crm_oauth.errors.VaultSealedError` when the row is
        there and this process cannot open it, because returning ``None`` there
        would say "never authorized" when the truth is "sealed elsewhere".
        """
        row = self.find(connection_id, org_key)
        if row is None:
            return None
        return open_sealed(row["data"].get("sealed", ""), self.key.material)

    def list_rows(self, connection_id: str) -> list[dict[str, Any]]:
        """Every sealed row for a connection, as summaries. Never unsealed."""
        rows = self.store.find("crm_credential", {"connection_id": connection_id}, limit=100)
        return sorted(
            (
                self.summarise(row)
                for row in rows
                if row["data"].get("connection_id") == connection_id
            ),
            key=lambda entry: entry["org_key"],
        )

    def summarise(self, row: dict[str, Any]) -> dict[str, Any]:
        """What may be said about a sealed row without opening it."""
        data = row["data"]
        return {
            "org_key": data.get("org_key"),
            "kind": data.get("kind"),
            "sealed": True,
            "sealed_key_id": data.get("sealed_key_id"),
            "readable_here": data.get("sealed_key_id") == self.key.key_id,
            "fields": list(data.get("fields") or []),
            "updated_at": row.get("updated_at"),
        }


__all__ = [
    "APP_ORG_KEY",
    "DEMO_KEY",
    "KEY_ENV",
    "SEALED_VERSION",
    "CredentialVault",
    "VaultKey",
    "key_id",
    "open_sealed",
    "resolve_key",
    "seal",
]
