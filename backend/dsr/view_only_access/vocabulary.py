"""Every researched term WF-072 enforces against, with the evidence it came from.

The specification for this workflow is ``docs/research/digital-sales-room-workflows/wf/WF-072.md``,
quoted in full in issue 134. It draws on section 5 of
``docs/research/raw/security-governance.md`` and cites three Papermark sources: the
datarooms CLI page, the links CLI page and ``openapi.json``. Every constant below is quoted
from one of those, or derived from a quote by a derivation recorded in
:mod:`dsr.view_only_access.inferences`.

The sentence the whole workflow is built on
-------------------------------------------

The data flow says the three controls "compose": the room-level ``bulkDownload`` setting,
the link-level ``allow_download`` boolean whose documented default is ``false``, and the
per-item ``can_download`` in the permission table. "The viewer service checks all three
before serving file bytes", and the CLI states the consequence in one clause: an item's
``--download`` is "Allow downloading (**also needs** ``--allow-download`` on the link)".

So download is an AND, and :func:`dsr.view_only_access.rules.decide_download` is that
sentence as a function.

What this workflow owns, and what it reads
------------------------------------------

This package owns three collections and reads two.

``wf072_room_policy`` and ``wf072_link`` are this workflow's own booleans: the room-wide
``bulk_download`` escalation, and the link's ``allow_download``. The specification's
extensibility note says an implementation "can mirror it exactly: a room-level policy, a
link-level policy, and per-item ACLs", so the two policies are here.

``wf072_view`` is the download event. The OpenAPI ``View`` schema the specification quotes
fixes two of its fields and both stay nullable: ``downloaded_at`` is a nullable date-time
and ``download_type`` is a nullable string. "A successful file fetch writes a ``View`` row
with ``downloaded_at`` and ``download_type``, which is what the audit trail reads."

The per-item ``can_download`` is **not** owned here. The issue says this workflow "reads
and evaluates ``can_download``; it does not own the permission set that grants it. Record
that boundary rather than absorbing the other workflow." WF-074 owns those rows, so
:data:`WF074_LINK_PERMISSION_COLLECTION` and :data:`WF074_GROUP_PERMISSION_COLLECTION` are
named here as **read** sources, exactly as WF-074 names WF-003's ``document`` rows as a
read source of its own, and they are filtered to live (not revoked) rows.

The defaults are the safe ones
------------------------------

The CLI link flag reads "``--allow-download`` | off | Viewer can download the file
(**default is view-only**)". The extensibility note adds that presets can "lock
``allow_download`` to ``false`` by default so it must be explicitly overridden". So a room
policy or link written without the field reads as no download, and
:func:`~dsr.view_only_access.rules.coerce_flag` treats an absent field that way rather than
as a wildcard.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- #
# Collections
# --------------------------------------------------------------------------- #
#
# Namespaced with the ticket, because every feature shares one `records` table and
# `find()` matches on collection before it matches on anything else. The three owned
# collections are this workflow's own rows.

ROOM_POLICY_COLLECTION = "wf072_room_policy"
LINK_COLLECTION = "wf072_link"
VIEW_COLLECTION = "wf072_view"

ALL_COLLECTIONS = (
    ROOM_POLICY_COLLECTION,
    LINK_COLLECTION,
    VIEW_COLLECTION,
)

#: The two collections this workflow **reads** and never writes on the request path. They
#: belong to WF-074, which owns the per-item ACL for both of its scopes. Named here as
#: plain strings, not imports: importing ``dsr.audience_permissions`` would tie two
#: tickets' releases together, which is the coupling the collision ruling refuses.
WF074_LINK_PERMISSION_COLLECTION = "wf074_link_permission"
WF074_GROUP_PERMISSION_COLLECTION = "wf074_dataroom_group_permission"

PERMISSION_SOURCE_COLLECTIONS = (
    WF074_LINK_PERMISSION_COLLECTION,
    WF074_GROUP_PERMISSION_COLLECTION,
)

#: The permission rows' owner keys. A row names the link or the group it belongs to; this
#: workflow reads them to tell a live grant from a row that is not in force.
LINK_ID_FIELD = "link_id"
GROUP_ID_FIELD = "group_id"

#: The core collection the room rows live in. A policy row points at one of these.
ROOM_COLLECTION = "room"

#: The payload-side twins of the ``room_id`` envelope field.
#:
#: Not ``room_id``, and that is not a style preference. ``room_id`` is part of the record
#: *envelope*, so the store strips it out of ``data`` before the dynamic index is built. A
#: record that stored its room there would be unfilterable by ``find()``, and a "filter by
#: room" that silently returns nothing is the kind of defect that ships. The envelope still
#: carries ``room_id``; every response projects this key back to it.
ROOM_REF = "room_ref"
LINK_REF = "link_ref"

#: A link row's name, optional because the CLI generates one.
NAME_FIELD = "name"


# --------------------------------------------------------------------------- #
# The three controls
# --------------------------------------------------------------------------- #
#
# The field names are the vendor's own. Kept verbatim because the CLI and the API use them
# and translating them would force every caller to hold a lookup table to talk to this API.

#: ``Dataroom.settings.bulkDownload``; the CLI spells it ``--bulk-download`` and describes
#: it as "Allow viewers to download every document as a zip".
BULK_DOWNLOAD = "bulk_download"

#: ``Link.allow_download``; the CLI spells it ``--allow-download``, default ``off``.
ALLOW_DOWNLOAD = "allow_download"

#: ``PermissionEntry.can_download``; the CLI spells it ``--download`` and says it "also
#: needs ``--allow-download`` on the link".
CAN_DOWNLOAD = "can_download"

#: The three controls' field names, in the order the data flow names them.
CONTROLS = (BULK_DOWNLOAD, ALLOW_DOWNLOAD, CAN_DOWNLOAD)

#: The controls this workflow owns and can set. ``can_download`` is deliberately absent:
#: it belongs to WF-074's permission set and is read, not written here.
OWNED_CONTROLS = (BULK_DOWNLOAD, ALLOW_DOWNLOAD)

#: The safe defaults. Missing means no download.
DEFAULT_BULK_DOWNLOAD = False
DEFAULT_ALLOW_DOWNLOAD = False
DEFAULT_CAN_DOWNLOAD = False

#: The CLI spelling of each control, so a seller working from the command line can find
#: the same switch in this page without a lookup.
CLI_FLAGS = {
    BULK_DOWNLOAD: "--bulk-download on|off",
    ALLOW_DOWNLOAD: "--allow-download",
    CAN_DOWNLOAD: "--download <on|off>",
}

#: The vendor's own one-line descriptions, quoted from the CLI pages.
CONTROL_DESCRIPTIONS = {
    BULK_DOWNLOAD: "Allow viewers to download every document as a zip",
    ALLOW_DOWNLOAD: "Viewer can download the file (default is view-only)",
    CAN_DOWNLOAD: "Allow downloading (also needs --allow-download on the link)",
}

CONTROL_LABELS = {
    BULK_DOWNLOAD: "Bulk download",
    ALLOW_DOWNLOAD: "Allow download",
    CAN_DOWNLOAD: "Item download",
}

CONTROL_OWNERS = {
    BULK_DOWNLOAD: "this workflow, on the room policy",
    ALLOW_DOWNLOAD: "this workflow, on the link policy",
    CAN_DOWNLOAD: "WF-074's permission set, read here and never written",
}

#: The `on`/`off` spelling a CLI flag accepts, and the two booleans it maps to.
FLAG_ON = "on"
FLAG_OFF = "off"
FLAG_TOKENS = (FLAG_ON, FLAG_OFF)

#: What a policy write interprets. Any other key in the payload is stored and carried,
#: because a room settings payload also holds `conversations` and `agents` (the
#: specification's own "settings triple") and those belong to other workflows.
ROOM_POLICY_FIELDS = (BULK_DOWNLOAD,)
LINK_POLICY_FIELDS = (ALLOW_DOWNLOAD,)


# --------------------------------------------------------------------------- #
# The per-item grant read from WF-074
# --------------------------------------------------------------------------- #
#
# The row shape the OpenAPI schema fixes: `PermissionEntry` "requires item_id, item_type
# (dataroom_document | dataroom_folder), can_view, can_download". This workflow reads only
# `item_id`, `can_download` and the revocation stamp.

ITEM_ID = "item_id"
ITEM_TYPE = "item_type"

#: Set on a permission row a later write took out of force. A live row has this absent or
#: null; a revoked row carries a timestamp. The dynamic index cannot express "is null", so
#: revoked rows are filtered in Python, exactly as WF-074 does.
REVOKED_AT = "revoked_at"

ITEM_TYPE_DOCUMENT = "dataroom_document"
ITEM_TYPE_FOLDER = "dataroom_folder"

ITEM_TYPES = (ITEM_TYPE_DOCUMENT, ITEM_TYPE_FOLDER)

ITEM_TYPE_LABELS = {
    ITEM_TYPE_DOCUMENT: "Document",
    ITEM_TYPE_FOLDER: "Folder",
}


# --------------------------------------------------------------------------- #
# The view record: what a single download event carries
# --------------------------------------------------------------------------- #
#
# The specification's data flow names these fields on the `View` row and the OpenAPI schema
# fixes the two nullables: "`downloaded_at` (nullable date-time), `download_type` (nullable
# string)".

VIEWER_EMAIL = "viewer_email"
VIEW_TYPE = "view_type"
VIEWED_AT = "viewed_at"
DOWNLOADED_AT = "downloaded_at"
DOWNLOAD_TYPE = "download_type"

VIEW_FIELDS = (
    VIEWER_EMAIL,
    VIEW_TYPE,
    VIEWED_AT,
    DOWNLOADED_AT,
    DOWNLOAD_TYPE,
)

#: The view types. The specification names ``view_type`` but not its values. WF-075 derives
#: document/dataroom/link for the same field; this workflow reads a single item's bytes, so
#: it writes ``document``.
VIEW_TYPE_DOCUMENT = "document"
VIEW_TYPE_DATAROOM = "dataroom"

VIEW_TYPES = (VIEW_TYPE_DOCUMENT, VIEW_TYPE_DATAROOM)

#: The download kinds. The specification names ``download_type`` and not its values. WF-075
#: derives ``pdf`` and ``original`` for the same field; this workflow adds ``zip`` because
#: the room-wide escalation is documented as "download every document as a zip", which is a
#: download kind those two do not name.
DOWNLOAD_TYPE_ORIGINAL = "original"
DOWNLOAD_TYPE_PDF = "pdf"
DOWNLOAD_TYPE_ZIP = "zip"

DOWNLOAD_TYPES = (DOWNLOAD_TYPE_ORIGINAL, DOWNLOAD_TYPE_PDF, DOWNLOAD_TYPE_ZIP)

#: The answer for a view that downloaded nothing. Not a ``download_type`` value, so it
#: cannot be stored on a row by accident; it is the page's word for a view with no
#: ``downloaded_at``.
NO_DOWNLOAD = "not_downloaded"


# --------------------------------------------------------------------------- #
# The three-way AND, and the decision it produces
# --------------------------------------------------------------------------- #

#: The decision's own state names. ``download`` is a permitted fetch; ``view_only`` is the
#: refusal, and it is named after the CLI's documented default ("default is view-only"),
#: not after an error, because a refusal is the designed answer here.
STATE_DOWNLOAD = "download"
STATE_VIEW_ONLY = "view_only"

DECISION_STATES = (STATE_DOWNLOAD, STATE_VIEW_ONLY)

#: The keys a decision carries. ``download`` is the verdict; ``gates`` records each of the
#: three controls separately so a page can say which one withheld the file rather than only
#: that the file was withheld; ``blocked_by`` names the controls that did not permit.
DOWNLOAD_FIELD = "download"
STATE_FIELD = "state"
GATES_FIELD = "gates"
BLOCKED_BY_FIELD = "blocked_by"
REASON_FIELD = "reason"

#: The names each gate takes in a decision, so the page and the tests read one table.
GATE_ROOM = "room_bulk_download"
GATE_LINK = "link_allow_download"
GATE_ITEM = "item_can_download"

GATES = (GATE_ROOM, GATE_LINK, GATE_ITEM)

GATE_FIELDS = {
    GATE_ROOM: BULK_DOWNLOAD,
    GATE_LINK: ALLOW_DOWNLOAD,
    GATE_ITEM: CAN_DOWNLOAD,
}

GATE_LABELS = {
    GATE_ROOM: "The room's bulk download setting",
    GATE_LINK: "The link's allow download setting",
    GATE_ITEM: "The item's own download grant",
}

#: The item grant's read result. ``row_present`` separates "nobody granted it" from
#: "somebody revoked it": both deny, and only one is the shipped default.
GRANT_FIELD = "grant"
ROW_PRESENT_FIELD = "row_present"
GRANT_SOURCE_FIELD = "grant_source"

#: Why a grant is not in force. Kept apart so a page can tell the two denials apart.
GRANT_LIVE = "live"
GRANT_REVOKED = "revoked"
GRANT_MISSING = "no_row"

GRANT_STATES = (GRANT_LIVE, GRANT_REVOKED, GRANT_MISSING)


# --------------------------------------------------------------------------- #
# What this workflow is and is not worth
# --------------------------------------------------------------------------- #
#
# Rendered on the page and carried in every response, because the cheap way to keep a claim
# honest is to make it part of the data rather than a line of copy somebody can delete.

LIMITATION = (
    "This gates a download and records it. It does not stop a viewer photographing a screen, "
    "and it does not prove who used the link. A blocked download writes no view row, so the "
    "audit trail holds the downloads that happened and not the ones that were refused."
)

SCOPE_OWNER = (
    "This workflow owns the room-level bulk download policy and the link-level allow "
    "download policy. The per-item can_download grant belongs to WF-074's permission set and "
    "is read here, never written."
)

READ_SOURCE = (
    "The per-item can_download grant is read from WF-074's permission rows "
    "(wf074_link_permission and wf074_dataroom_group_permission) by collection name. This "
    "workflow does not import that package and does not write those rows on the request path."
)

ASSUMPTION = (
    "One product surface is an inference and the specification marks it: the dataroom "
    "settings panel '(Conversations / Agents / Bulk download) [inferred from the documented "
    "settings triple]'. This build implements bulk download alone and carries the other two "
    "settings through untouched."
)

#: The keys every response carries, so a caller cannot read the controls without also
#: reading what they are worth and which part is assumed.
OWNER_FIELD = "scope_owner"
READ_SOURCE_FIELD = "read_source"
ASSUMPTION_FIELD = "not_sourced"
LIMITATION_FIELD = "limitation"

HONESTY_FIELDS = (OWNER_FIELD, READ_SOURCE_FIELD, ASSUMPTION_FIELD, LIMITATION_FIELD)


def vocabulary_payload() -> dict[str, Any]:
    """Everything this module asserts, for a reviewer and for the page.

    Served by the vocabulary route so the panel cannot drift from the rules that
    validate it: the three controls with their owners, the safe defaults, the AND semantics,
    the read-source collections, the view shape and the honesty sentences all come from the
    same tables the validator reads.
    """

    return {
        "controls": [
            {
                "field": field,
                "label": CONTROL_LABELS[field],
                "description": CONTROL_DESCRIPTIONS[field],
                "cli_flag": CLI_FLAGS[field],
                "owner": CONTROL_OWNERS[field],
                "owned": field in OWNED_CONTROLS,
            }
            for field in CONTROLS
        ],
        "defaults": {
            BULK_DOWNLOAD: DEFAULT_BULK_DOWNLOAD,
            ALLOW_DOWNLOAD: DEFAULT_ALLOW_DOWNLOAD,
            CAN_DOWNLOAD: DEFAULT_CAN_DOWNLOAD,
        },
        "flag_tokens": list(FLAG_TOKENS),
        "and_semantics": (
            "A download is permitted only when all three controls permit it. The CLI states "
            "it for the item flag: an item's --download also needs --allow-download on the "
            "link."
        ),
        "gates": [
            {"name": name, "field": GATE_FIELDS[name], "label": GATE_LABELS[name]} for name in GATES
        ],
        "states": list(DECISION_STATES),
        "grant_states": list(GRANT_STATES),
        "item_types": [
            {"id": item_type, "label": ITEM_TYPE_LABELS[item_type]} for item_type in ITEM_TYPES
        ],
        "download_types": list(DOWNLOAD_TYPES),
        "no_download": NO_DOWNLOAD,
        "view_types": list(VIEW_TYPES),
        "view_fields": list(VIEW_FIELDS),
        "collections": list(ALL_COLLECTIONS),
        "read_collections": list(PERMISSION_SOURCE_COLLECTIONS),
        OWNER_FIELD: SCOPE_OWNER,
        READ_SOURCE_FIELD: READ_SOURCE,
        ASSUMPTION_FIELD: ASSUMPTION,
        LIMITATION_FIELD: LIMITATION,
    }
