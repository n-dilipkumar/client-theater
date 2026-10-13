"""WF-047 vocabulary: the fixed words this workflow uses.

Every value here is either quoted from the research or is a local reading that
:mod:`dsr.crm_file_sync.inferences` records as an inference. The distinction is
load-bearing for this workflow in particular, because the research's own gaps
block says two of the three systems could not be sourced at all, so a reader
needs to be able to tell at a glance which half of this vocabulary has a source
behind it.

Sources, in full
-----------------
``docs/research/digital-sales-room-workflows/wf/WF-047.md``, which quotes
``docs/research/raw/crm-integration.md`` section 14 and three primary sources:
HubSpot's files guide, HubSpot's CRM properties guide, and Salesforce's blob
page.

* **The upload.** "Files can be uploaded using a multipart/form-data ``POST``
  request to ``files/2026-09/files`` with the following fields" including
  ``file``. The research's own ``data_flow`` names the two optional fields as
  ``folderId`` and ``fileName``.
* **The folder.** "While a specific folder ID is not required at upload, it's
  recommended to upload files into a folder and not the root directory." and
  "Folder requirements at upload are subject to change."
* **The returned id.** The ``data_flow`` says the CRM "stores the file and
  returns a file id", and the properties guide says a ``file`` property
  "Allows for a file to be uploaded on a record or via a form. Stores a file ID."
* **The attachment.** "Attach files to records using the notes APIs."
* **The idempotency key.** "The room stores ``crm_file_id`` on the asset row, so
  re-uploads are skipped and revisions create a new file."
* **The reconciliation rule.** "find room assets with no ``crm_file_id`` and
  push them".

The gaps
--------
The research is explicit that only HubSpot's Files API is documented for this
workflow:

* "Salesforce Files (``ContentVersion`` / ``ContentDocument`` /
  ``ContentDocumentLink``) could not be sourced - every candidate
  ``resources-content*`` URL returned 404."
* "Microsoft Dataverse file/image columns and ``annotation``/``attachment``
  tables could not be sourced - the corresponding doc pages returned 404."

So :data:`FILE_CAPABLE_SYSTEMS` holds one system and
:data:`UNSOURCED_SYSTEMS` holds two, each carrying the gap text. A connector
request for an unsourced system is refused by name. It is not quietly dropped
and it is not invented.
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# Systems
# --------------------------------------------------------------------------- #

#: Every CRM system this product knows about, in the product's own spelling.
#: Sourced: the research names all three across the CRM integration corpus, and
#: the three are the same three :mod:`dsr.crm_integration.vocabulary` publishes.
SYSTEMS: tuple[str, ...] = ("hubspot", "salesforce", "dataverse")

#: The one system whose file upload is fully documented. Sourced: "Only HubSpot's
#: Files API is fully documented for this workflow."
FILE_CAPABLE_SYSTEMS: tuple[str, ...] = ("hubspot",)

#: The two systems the research could not source for files, each with the gap
#: text quoted from the specification. Sourced: the gaps block, quoted in this
#: module's docstring.
UNSOURCED_SYSTEMS: dict[str, str] = {
    "salesforce": (
        "Salesforce Files (ContentVersion / ContentDocument / ContentDocumentLink) could not be "
        "sourced: every candidate resources-content* URL returned 404. The Salesforce blob page was "
        "reachable only as a link index and the research quotes no payload for it, so this build "
        "does not invent one."
    ),
    "dataverse": (
        "Microsoft Dataverse file/image columns and annotation/attachment tables could not be "
        "sourced: the corresponding documentation pages returned 404."
    ),
}

#: The vendor paths this build knows, as the research spells them. The leading
#: slash is added by the request builder, because the research writes the path
#: without one.
UPLOAD_PATH = "files/2026-09/files"

#: The content type the research names for the upload request.
UPLOAD_CONTENT_TYPE = "multipart/form-data"

#: The multipart field names the research names: ``file`` is required, and the
#: ``data_flow`` adds ``folderId`` and ``fileName`` as optional.
UPLOAD_REQUIRED_FIELDS: tuple[str, ...] = ("file",)
UPLOAD_OPTIONAL_FIELDS: tuple[str, ...] = ("folderId", "fileName")

#: The vendor's own recommendation, quoted. It is published as data so a page can
#: show why a connection that has no folder is not in breach of anything.
FOLDER_RECOMMENDATION = (
    "While a specific folder ID is not required at upload, it's recommended to upload files into a "
    "folder and not the root directory."
)

#: The vendor's own warning about that recommendation. It is why a folder the
#: vendor later rejects produces a named failure on the mirror row instead of a
#: silent fall back to the root directory.
FOLDER_CAVEAT = "Folder requirements at upload are subject to change."


# --------------------------------------------------------------------------- #
# Attach modes
# --------------------------------------------------------------------------- #

#: How the returned file id is written back to the CRM record that owns it. The
#: ``data_flow`` allows both: "room writes that id into a ``file``-type property
#: (or attaches it to a note)".
ATTACH_NOTE = "note"
ATTACH_FILE_PROPERTY = "file_property"

#: Both modes, with the note first. The note is the default because the vendor's
#: own guide says "Attach files to records using the notes APIs" and does not say
#: the same about a property.
ATTACH_MODES: tuple[str, ...] = (ATTACH_NOTE, ATTACH_FILE_PROPERTY)
DEFAULT_ATTACH_MODE = ATTACH_NOTE

#: What each mode does, in the words the research uses. Served as data so a page
#: renders the reason rather than a word this build invented.
ATTACH_MODE_NOTES: dict[str, str] = {
    ATTACH_NOTE: (
        "Attach files to records using the notes APIs. The file id is written into a note attached "
        "to the record, so the attachment appears on the record's timeline."
    ),
    ATTACH_FILE_PROPERTY: (
        "A file-type property Allows for a file to be uploaded on a record or via a form. Stores a "
        "file ID. The connection must name the property, because a tenant that has not created a "
        "file-type property has nothing to write into."
    ),
}

#: The CRM object that owns the attachment. The issue names the dependency: WF-042
#: "provisions the room's CRM deal, account and contact records", and without one
#: "this workflow has no contact or deal record to own the attachment, so the
#: returned file id has nowhere to be written".
OWNER_OBJECTS: tuple[str, ...] = ("deal", "contact", "account")
DEFAULT_OWNER_OBJECT = "deal"


# --------------------------------------------------------------------------- #
# Asset mirror states: the "Push to CRM" chip on the Documents page
# --------------------------------------------------------------------------- #

#: Never mirrored. The researched chip's own starting state.
PENDING = "pending"
#: Mirrored, and the bytes have not changed since.
MIRRORED = "mirrored"
#: Mirrored once, and the bytes have changed since. A revision is waiting.
STALE = "stale"
#: The last mirror attempt was refused. The refusal is named on the mirror row.
FAILED = "failed"
#: Push to CRM is switched off for this asset, so nothing was sent.
WITHHELD = "withheld"

MIRROR_STATES: tuple[str, ...] = (PENDING, MIRRORED, STALE, FAILED, WITHHELD)

#: A plain-language sentence per state. The design floor forbids status by colour
#: alone, so every state carries words as well as a tone.
MIRROR_STATE_NOTES: dict[str, str] = {
    PENDING: "Not pushed to the CRM yet. No file id is stored on this asset.",
    MIRRORED: "Pushed. The CRM holds this file and the file id is stored on this asset.",
    STALE: "Changed since the last push. The next mirror creates a new CRM file.",
    FAILED: "The last push was refused. The reason is named on the mirror row.",
    WITHHELD: "Push to CRM is off for this asset, so nothing was sent.",
}


# --------------------------------------------------------------------------- #
# Mirror outcomes: one per mirror attempt
# --------------------------------------------------------------------------- #

#: Uploaded and attached. The researched success.
OUTCOME_MIRRORED = "mirrored"
#: The bytes have not changed since the last mirror, so nothing was sent. This is
#: the researched idempotency rule: "re-uploads are skipped".
OUTCOME_SKIPPED_UNCHANGED = "skipped_unchanged"
#: The push is off for this asset.
OUTCOME_WITHHELD = "withheld"
#: The connection names no CRM record to own the attachment. This is the
#: dependency WF-047 has on WF-042, and it is refused rather than invented.
OUTCOME_NO_OWNER_RECORD = "no_owner_record"
#: A named refusal. The reason rides on the row.
OUTCOME_FAILED = "failed"

MIRROR_OUTCOMES: tuple[str, ...] = (
    OUTCOME_MIRRORED,
    OUTCOME_SKIPPED_UNCHANGED,
    OUTCOME_WITHHELD,
    OUTCOME_NO_OWNER_RECORD,
    OUTCOME_FAILED,
)


# --------------------------------------------------------------------------- #
# Reconciliation
# --------------------------------------------------------------------------- #

#: The researched rule, quoted: "find room assets with no ``crm_file_id`` and
#: push them".
RECONCILE_RULE = "find room assets with no crm_file_id and push them"

#: The floor at which a reconciliation run is allowed to ask again, in seconds.
#:
#: Derived, not sourced: the research gives the rule and no cadence. 300 seconds
#: is the same floor :mod:`dsr.crm_backfill.plan` derives for the polled vendor
#: loops in this product, so one number governs every polled vendor workflow here
#: rather than each package inventing its own. A scheduler that calls the
#: reconciliation route more often than this is told the run was not due, and the
#: ``force`` flag skips the floor because a room that restarted has not been
#: respecting anybody's interval. See the ``reconcile-is-routed-not-scheduled``
#: inference.
DEFAULT_POLL_INTERVAL_SECONDS = 300

#: The smallest interval this build accepts, for the same reason
#: :mod:`dsr.crm_backfill.plan` refuses one: a floor of zero is a floor nobody set.
MIN_POLL_INTERVAL_SECONDS = 30


# --------------------------------------------------------------------------- #
# Keys the domain owns
# --------------------------------------------------------------------------- #

#: The vendor-agnostic slot the research names. "The room's asset table holds the
#: vendor-agnostic ``crm_file_id`` slot, so file sync state survives connector
#: swaps." A field a team adds beside it is theirs: the payload is ordinary JSON
#: and nothing here validates an unknown key.
ASSET_FILE_ID_KEY = "crm_file_id"
ASSET_DIGEST_KEY = "content_digest"
ASSET_MIRRORED_DIGEST_KEY = "mirrored_content_digest"

#: The room's own asset shape, as far as this workflow reads it. ``source_id`` is
#: the id of the room asset record this row mirrors, and the room may add any
#: other key it likes.
ASSET_SOURCE_ID_KEY = "source_id"

#: The collection names. Three collections of ordinary JSON, and the remote
#: collection stands in for the vendor's own file storage.
CONNECTIONS = "crm_file_connection"
ASSETS = "crm_file_asset"
MIRRORS = "crm_file_mirror"
REMOTE_FILES = "crm_remote_file"
