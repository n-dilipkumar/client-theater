"""The room asset side: what an asset is, how a revision is detected, and when a
mirror may run.

Sourced versus inferred
-----------------------
Sourced, from the research's user flow and ``data_flow``:

* step 2: "Rep toggles **Push to CRM** on the asset (or the connection is
  configured to mirror all room documents)". So the toggle is a per-asset flag,
  and a connection may set the default for the room.
* step 4: "The room stores ``crm_file_id`` on the asset row, so re-uploads are
  skipped and revisions create a new file."
* the ``data_flow``: "room asset (bytes + filename + mime) -> multipart/form-data
  upload". So the three things an asset must carry are its bytes, its filename and
  its content type.

Inferred, and recorded in :mod:`dsr.crm_file_sync.inferences`:

* **How a revision is detected.** The research says revisions create a new file
  and does not say how one is detected. This build uses a digest of the bytes.
  The alternatives were considered and are recorded there: the store's envelope
  revision counter measures writes rather than content, so renaming a document
  would upload a new CRM file the rep never asked for.
* **What a mirror may skip.** A mirror whose digest equals the mirrored digest is
  answered ``skipped_unchanged`` with no vendor call, which is the researched
  idempotency rule made into a check rather than a hope.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any, Mapping

from dsr.crm_file_sync import vocabulary
from dsr.crm_file_sync.errors import AssetRefused

#: The digest algorithm. Named once, because the digest is written to a record
#: that outlives this process: a build that changed the algorithm without
#: changing the prefix would compare two different digests and re-upload
#: everything on the next deploy. The prefix carries the algorithm with it.
DIGEST_ALGORITHM = "sha256"
DIGEST_PREFIX = f"{DIGEST_ALGORITHM}:"


def content_digest(content: bytes) -> str:
    """The digest of the asset's bytes, prefixed with the algorithm that made it.

    The digest is of the bytes alone, not of the filename beside them. That is
    the decision the research leaves open, and the reasoning is in
    :mod:`dsr.crm_file_sync.inferences`: a renamed document is the same document,
    and the rep who renamed it did not ask for a second copy in the CRM.
    """
    return f"{DIGEST_PREFIX}{hashlib.sha256(content).hexdigest()}"


def decode_content(raw: Any) -> bytes:
    """Read an asset's bytes from the payload a caller sent.

    The room's asset storage holds the bytes base64-encoded, because a record
    payload is JSON and JSON has no byte type. A caller that already has a string
    may send it directly, in which case it is encoded as UTF-8 first.
    """
    if raw is None:
        return b""
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw)
    text = str(raw)
    if text == "":
        return b""
    try:
        return base64.b64decode(text, validate=True)
    except (ValueError, TypeError):
        # Not base64. A caller that sent plain text meant the text, and refusing
        # it would make the common case of a small generated asset impossible.
        return text.encode("utf-8")


def encode_content(content: bytes) -> str:
    """The stored form of an asset's bytes."""
    return base64.b64encode(content).decode("ascii")


def normalise_asset(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Check one asset payload and return the payload this workflow stores.

    Three rules, all the caller's to fix, so a refusal here is a 400:

    * a filename. The research's ``data_flow`` sends the filename as the upload's
      ``fileName``, and a file with no name is not a document a rep can find
      again in the CRM.
    * a content type. The upload is multipart and the vendor records what kind of
      file it received.
    * at least one byte. An empty upload is not a document.

    No size limit is applied, and that is a decision rather than an omission. The
    research says "Uploaded files are subject to the limits and supported formats"
    and quotes no number, so a limit here would be a number this build invented
    and a tenant would hit it as an unexplained refusal.
    """
    filename = str(payload.get("filename") or payload.get("name") or "").strip()
    mime_type = str(payload.get("mime_type") or payload.get("content_type") or "").strip()
    content = decode_content(payload.get("content_base64", payload.get("content")))

    if not filename:
        raise AssetRefused("filename is required: the upload names the file to the CRM")
    if not mime_type:
        raise AssetRefused("mime_type is required: the upload records what kind of file it is")
    if not content:
        raise AssetRefused("content_base64 is required and must decode to at least one byte")

    stored = dict(payload)
    stored["filename"] = filename
    stored["mime_type"] = mime_type
    stored["content_base64"] = encode_content(content)
    stored["byte_size"] = len(content)
    stored[vocabulary.ASSET_DIGEST_KEY] = content_digest(content)
    return stored


def mirror_state(asset: Mapping[str, Any], connection: Mapping[str, Any] | None = None) -> str:
    """The asset's "Push to CRM" chip, as the Documents page renders it.

    Derived from the row rather than stored, so a state can never drift away from
    the fields it describes. The order matters: a switch that is off is
    ``withheld`` whatever else is true, because nothing will be sent until it is
    on, and a row that failed is ``failed`` whatever its digest says, because the
    rep needs the reason more than they need to know the bytes are unchanged.
    """
    if not push_enabled(asset, connection):
        return vocabulary.WITHHELD
    if str(asset.get("last_outcome") or "") == vocabulary.OUTCOME_FAILED:
        return vocabulary.FAILED
    file_id = str(asset.get(vocabulary.ASSET_FILE_ID_KEY) or "")
    if not file_id:
        return vocabulary.PENDING
    mirrored = str(asset.get(vocabulary.ASSET_MIRRORED_DIGEST_KEY) or "")
    current = str(asset.get(vocabulary.ASSET_DIGEST_KEY) or "")
    if mirrored and current and mirrored != current:
        return vocabulary.STALE
    return vocabulary.MIRRORED


def push_enabled(asset: Mapping[str, Any], connection: Mapping[str, Any] | None = None) -> bool:
    """Whether "Push to CRM" is on for this asset.

    The researched rule: "Rep toggles **Push to CRM** on the asset (or the
    connection is configured to mirror all room documents)". So an asset's own flag
    wins when it has one, and the connection's ``mirror_all`` answers only for an
    asset that has not expressed a preference.
    """
    if "push_to_crm" in asset:
        return bool(asset.get("push_to_crm"))
    if connection is not None and "mirror_all" in connection:
        return bool(connection.get("mirror_all"))
    return False


def needs_mirror(asset: Mapping[str, Any], connection: Mapping[str, Any] | None = None) -> bool:
    """Whether a mirror of this asset would send anything.

    False when the push is off, and false when the mirrored digest already equals
    the current digest. That second rule is the researched idempotency, "re-uploads
    are skipped", applied before the vendor is asked rather than after it answers.
    """
    if not push_enabled(asset, connection):
        return False
    mirrored = str(asset.get(vocabulary.ASSET_MIRRORED_DIGEST_KEY) or "")
    current = str(asset.get(vocabulary.ASSET_DIGEST_KEY) or "")
    if mirrored and current and mirrored == current:
        return False
    return True


def is_revision(asset: Mapping[str, Any]) -> bool:
    """Whether this asset is a revision of something already in the CRM.

    True when a file id is stored and the digest has moved since the last mirror.
    That is the researched "revisions create a new file": the old file stays
    where it is and a new one is created, because a CRM file cannot be edited in
    place by the APIs this workflow uses.
    """
    if not str(asset.get(vocabulary.ASSET_FILE_ID_KEY) or ""):
        return False
    mirrored = str(asset.get(vocabulary.ASSET_MIRRORED_DIGEST_KEY) or "")
    current = str(asset.get(vocabulary.ASSET_DIGEST_KEY) or "")
    return bool(mirrored and current and mirrored != current)


def missing_file_id(asset: Mapping[str, Any]) -> bool:
    """Whether the asset has no ``crm_file_id``, which is the reconciliation rule.

    Quoted directly: "find room assets with no ``crm_file_id`` and push them".
    """
    return not str(asset.get(vocabulary.ASSET_FILE_ID_KEY) or "")


def summarise(
    asset: Mapping[str, Any], connection: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """One asset as the Documents page and the CRM Files panel read it.

    The vendor-agnostic ``crm_file_id`` is on the row, which is what the research
    means by "file sync state survives connector swaps": swap the connector and
    this value is still a fact about the CRM rather than about a connector.
    """
    state = mirror_state(asset, connection)
    return {
        "id": str(asset.get("id") or ""),
        "connection_id": str(asset.get("connection_id") or ""),
        "source_id": str(asset.get(vocabulary.ASSET_SOURCE_ID_KEY) or ""),
        "filename": str(asset.get("filename") or ""),
        "mime_type": str(asset.get("mime_type") or ""),
        "byte_size": int(asset.get("byte_size") or 0),
        "digest": str(asset.get(vocabulary.ASSET_DIGEST_KEY) or ""),
        "mirrored_digest": str(asset.get(vocabulary.ASSET_MIRRORED_DIGEST_KEY) or ""),
        "crm_file_id": str(asset.get(vocabulary.ASSET_FILE_ID_KEY) or ""),
        "state": state,
        "state_note": vocabulary.MIRROR_STATE_NOTES.get(state, ""),
        "push_to_crm": push_enabled(asset, connection),
        "is_revision": is_revision(asset),
        "needs_mirror": needs_mirror(asset, connection),
        "revision": int(asset.get("revision") or 0),
        "updated_at": str(asset.get("updated_at") or ""),
    }
