"""WF-047: mirror room documents into CRM files.

The domain behind the researched workflow. The research document is the
specification: ``docs/research/digital-sales-room-workflows/wf/WF-047.md`` (source
``docs/research/raw/crm-integration.md`` section 14, three primary sources).

The researched flow, as the spec states it
-----------------------------------------
1. A rep uploads a deck or proposal into the room, or an asset is generated.
2. The rep toggles **Push to CRM** on the asset, or the connection is configured
   to mirror all room documents.
3. The room uploads the binary to the CRM files endpoint with
   ``multipart/form-data``, then writes the returned file id onto a CRM record, as
   a ``file``-type property value or as a note attached to the record.
4. The room stores ``crm_file_id`` on the asset row, so re-uploads are skipped and
   revisions create a new file.
5. The rep sees the CRM attachment from the record's Files panel or note timeline.

Sourced behaviour this package implements
-----------------------------------------
* **The upload.** "Files can be uploaded using a ``multipart/form-data`` ``POST``
  request to ``files/2026-09/files``" with the field ``file``. The spec's own
  ``data_flow`` adds the optional ``folderId`` and ``fileName``.
* **The folder.** "While a specific folder ID is not required at upload, it's
  recommended to upload files into a folder and not the root directory." with the
  warning "Folder requirements at upload are subject to change."
* **The returned id.** A ``file`` property "Allows for a file to be uploaded on a
  record or via a form. Stores a file ID."
* **The attachment.** "Attach files to records using the notes APIs."
* **The idempotency key.** ``crm_file_id`` on the asset row.
* **The reconciliation rule.** "find room assets with no ``crm_file_id`` and push
  them".
* **The connector interface.** ``upload(bytes) -> fileId`` and
  ``attach(fileId, recordId)``, "so a third-party connector can support any
  file-capable CRM".

What this package refuses rather than invents
---------------------------------------------
* **Salesforce Files and Dataverse file columns.** The spec's gaps block says both
  "could not be sourced" and that "Only HubSpot's Files API is fully documented for
  this workflow". A connection naming either system is refused by name, carrying
  the gap text. A deployment with a real connector registers one and nothing here
  changes.
* **A Salesforce blob body.** The spec says the blob page "was readable only as a
  link index; no payload example is quoted here" and the issue says "Do not invent
  a payload".
* **A note payload.** The spec quotes that a file is attached "using the notes
  APIs" and quotes no body, so the attachment is recorded, not fabricated.
* **A file size limit.** "Uploaded files are subject to the limits and supported
  formats" with no number quoted, so no number is applied here.

Every judgement call beyond the sourced half is in
:mod:`dsr.crm_file_sync.inferences`, served at ``GET /api/wf-047/inferences``.

Schema flexibility
------------------
Everything this package stores is ordinary JSON in ``records.data``. Four
collections: the connections a room pushes through, the room assets, the mirror
log, and ``crm_remote_file``, which stands in for the vendor's own storage because
this product holds no CRM credential. A team that needs ``legal_reviewer`` on its
assets adds one key; no migration and no typed column is involved.
"""

from __future__ import annotations

from dsr.crm_file_sync import assets, connectors, engine, errors, inferences, vocabulary
from dsr.crm_file_sync.assets import (
    content_digest,
    decode_content,
    encode_content,
    is_revision,
    mirror_state,
    needs_mirror,
    normalise_asset,
    push_enabled,
    summarise,
)
from dsr.crm_file_sync.connectors import (
    Attachment,
    FileConnector,
    SimulatedConnector,
    UploadedFile,
    UploadRequest,
    connector_for,
    register_connector,
    supported_systems,
    unregister_connector,
)
from dsr.crm_file_sync.engine import CrmFileSyncEngine
from dsr.crm_file_sync.errors import (
    AssetRefused,
    AttachRefused,
    CrmFileSyncError,
    DuplicateConnection,
    FolderRefused,
    NoOwnerRecord,
    UnknownAsset,
    UnknownConnection,
    UnknownMirror,
    UnsupportedSystem,
    UploadRefused,
)
from dsr.crm_file_sync.inferences import INFERENCES
from dsr.crm_file_sync.vocabulary import ASSETS, CONNECTIONS, MIRRORS, REMOTE_FILES

__all__ = [
    # modules
    "assets",
    "connectors",
    "engine",
    "errors",
    "inferences",
    "vocabulary",
    # engine
    "CrmFileSyncEngine",
    # collections
    "ASSETS",
    "CONNECTIONS",
    "MIRRORS",
    "REMOTE_FILES",
    # asset rules
    "content_digest",
    "decode_content",
    "encode_content",
    "is_revision",
    "mirror_state",
    "needs_mirror",
    "normalise_asset",
    "push_enabled",
    "summarise",
    # connectors
    "Attachment",
    "FileConnector",
    "SimulatedConnector",
    "UploadRequest",
    "UploadedFile",
    "connector_for",
    "register_connector",
    "supported_systems",
    "unregister_connector",
    # errors
    "AssetRefused",
    "AttachRefused",
    "CrmFileSyncError",
    "DuplicateConnection",
    "FolderRefused",
    "NoOwnerRecord",
    "UnknownAsset",
    "UnknownConnection",
    "UnknownMirror",
    "UnsupportedSystem",
    "UploadRefused",
    # inferences
    "INFERENCES",
]
