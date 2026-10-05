"""Every researched term WF-074 enforces against, with the evidence it came from.

The specification for this workflow is ``docs/research/digital-sales-room-workflows/wf/WF-074.md``,
quoted in full in issue 149, and it draws on section 7 of
``docs/research/raw/security-governance.md``. Four Papermark sources are cited: the group
sharing guide, the datarooms CLI page, the links CLI page and ``openapi.json``. Every
constant below is quoted from one of those, or derived from a quote by a derivation
recorded in :mod:`dsr.audience_permissions.inferences`.

The sentence that governs the whole workflow
-------------------------------------------

The guide says "A new group sees **nothing** until you grant permissions." That is the
default, and it is the sentence every rule in this package is written against: an item
with no permission row is invisible to that audience. Nothing here inverts it, and nothing
here treats an absent row as a wildcard.

Two things are in this package that are not in a typical vocabulary table, and both are
load-bearing.

**The three size caps, exactly as stated.** ``domains[]`` max 100, ``emails[]`` max 500 per
call, permissions max 1000 entries. They are the vendor's own request bounds, quoted from
``openapi.json``, so they are named as constants rather than typed inline at four call
sites where a later edit would change one and not the other.

**The closed permission entry.** ``PermissionEntry`` "requires ``item_id``, ``item_type``
(``dataroom_document`` | ``dataroom_folder``), ``can_view``, ``can_download``" and "declares
``additionalProperties: false``". That last clause is the reason :data:`PERMISSION_ENTRY_FIELDS`
is a closed tuple and :func:`~dsr.audience_permissions.rules.build_permission` rejects
anything else. An entry that accepted a spare key would be an entry the vendor's own
validator would refuse, and a store that accepted it would then hold rows the API cannot
read back.

What this build does not claim
------------------------------

One of the specification's own data sources is the buyer's email "from the link's email
gate", and one product surface is the dashboard's permissions grid. Neither is a public API
in the cited sources, so what this build does is server-side filtering over the room's own
document and folder rows. The rows themselves are owned by WF-003's library and are read
here, never written. :data:`ASSUMPTION` carries that to every response.

Who owns the setting
--------------------

The specification says the two ACL scopes "are deliberately distinct and their conflict is
resolved by an explicit, documented rule, which is the pattern worth copying". So both
scopes live here, and the rule that resolves their conflict is a refusal rather than a
precedence order. :data:`SCOPE_OWNER` names this workflow as the owner so a second
workflow does not implement the same grid a second time.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- #
# Collections
# --------------------------------------------------------------------------- #
#
# Namespaced with the ticket, because every feature shares one `records` table and
# `find()` matches on collection before it matches on anything else. The four collections
# below are the specification's "Four linked records".

GROUP_COLLECTION = "wf074_dataroom_group"
MEMBER_COLLECTION = "wf074_dataroom_group_member"
PERMISSION_COLLECTION = "wf074_dataroom_group_permission"
LINK_COLLECTION = "wf074_link"
LINK_PERMISSION_COLLECTION = "wf074_link_permission"

ALL_COLLECTIONS = (
    GROUP_COLLECTION,
    MEMBER_COLLECTION,
    PERMISSION_COLLECTION,
    LINK_COLLECTION,
    LINK_PERMISSION_COLLECTION,
)

#: The collections holding the items a permission entry points at. They are **not** in
#: :data:`ALL_COLLECTIONS` because this workflow does not own them: they are WF-003's
#: library rows and this workflow only reads them. Naming them here is how the engine can
#: resolve an ``item_id`` without importing another package, and naming them in a payload
#: is how the page can say whose rows these are.
DOCUMENT_COLLECTION = "document"
FOLDER_COLLECTION = "documentFolder"

ITEM_COLLECTIONS = (DOCUMENT_COLLECTION, FOLDER_COLLECTION)

#: The room's own library rows name their parent folder with this key, and the room root
#: is the sentinel below rather than a record. Read from ``dsr.library``'s own convention
#: rather than invented, so an ancestor walk over these rows terminates on the same string
#: that wrote it.
PARENT_FOLDER_FIELD = "parentFolderId"
ROOT_FOLDER = "root"

#: The payload-side twin of the envelope's ``room_id``.
#:
#: Not ``room_id``, and that is not a style preference. ``room_id`` is part of the record
#: *envelope*, so the store strips it out of ``data`` before the dynamic index is built.
#: A record that stored its room there would be unfilterable by ``find()``, and a "filter by
#: room" that silently returns nothing is the kind of defect that ships. The envelope still
#: carries ``room_id``; every response projects this key back to it.
ROOM_REF = "room_ref"


# --------------------------------------------------------------------------- #
# Item types
# --------------------------------------------------------------------------- #
#
# Quoted from ``PermissionEntry.item_type``, which the specification renders as
# "``dataroom_document`` | ``dataroom_folder``". The wire names are kept verbatim because
# the vendor's own request objects use them, and translating them here would force every
# caller to hold a lookup table to talk to this API.

ITEM_TYPE_DOCUMENT = "dataroom_document"
ITEM_TYPE_FOLDER = "dataroom_folder"

ITEM_TYPES = (ITEM_TYPE_DOCUMENT, ITEM_TYPE_FOLDER)

ITEM_TYPE_LABELS = {
    ITEM_TYPE_DOCUMENT: "Document",
    ITEM_TYPE_FOLDER: "Folder",
}

#: Which collection an item type resolves against. One table, read in one place, so the
#: document type can never resolve against the folder collection after a reordering.
ITEM_TYPE_COLLECTIONS = {
    ITEM_TYPE_DOCUMENT: DOCUMENT_COLLECTION,
    ITEM_TYPE_FOLDER: FOLDER_COLLECTION,
}


# --------------------------------------------------------------------------- #
# The closed permission entry
# --------------------------------------------------------------------------- #

PERMISSION_ENTRY_FIELDS = ("item_id", "item_type", "can_view", "can_download")

CAN_VIEW = "can_view"
CAN_DOWNLOAD = "can_download"

#: The flags a caller must send. Both are required and both are booleans: the
#: specification's own example gives an audience a folder it may browse but not download,
#: so a shape with a default would hide the case that matters most.
REQUIRED_ENTRY_FIELDS = PERMISSION_ENTRY_FIELDS


# --------------------------------------------------------------------------- #
# The two ACL scopes and their write semantics
# --------------------------------------------------------------------------- #
#
# The extensibility note is explicit and quotes both descriptions. Group permissions are
# delta: "entries you send are upserted, entries you omit keep their current state". Link
# permissions are full-replace: "the payload is the complete desired state, items not
# listed lose their override, and an empty array clears everything".

SCOPE_GROUP = "group"
SCOPE_LINK = "link"

SCOPES = (SCOPE_GROUP, SCOPE_LINK)

SCOPE_SEMANTICS = {
    SCOPE_GROUP: "delta",
    SCOPE_LINK: "full_replace",
}

SCOPE_DESCRIPTIONS = {
    SCOPE_GROUP: (
        "Entries you send are upserted. An item you omit keeps the state it already has, "
        "including the state it had before this call."
    ),
    SCOPE_LINK: (
        "The payload is the complete desired state. An item you do not list loses its "
        "override. An empty array clears every override on the link."
    ),
}


# --------------------------------------------------------------------------- #
# Link audiences
# --------------------------------------------------------------------------- #

#: ``audience_type``, quoted: a link is ``audience_type: "group"`` with a ``group_id``, or
#: ``"general"``. The two are the only values, and the difference between them is the whole
#: subject of this workflow: a general link takes its visibility from its own ACL, and a
#: group link takes it from its group.
AUDIENCE_GROUP = "group"
AUDIENCE_GENERAL = "general"

AUDIENCE_TYPES = (AUDIENCE_GROUP, AUDIENCE_GENERAL)

AUDIENCE_LABELS = {
    AUDIENCE_GROUP: "Group",
    AUDIENCE_GENERAL: "General",
}


# --------------------------------------------------------------------------- #
# Membership
# --------------------------------------------------------------------------- #
#
# The data flow fixes the order: "the requester's email is matched against memberships
# (explicit email, then domain, then ``allow_all``)". Three steps, in that order, and the
# order is what the tests pin.

MEMBERSHIP_BY_EMAIL = "by_email"
MEMBERSHIP_BY_DOMAIN = "by_domain"
MEMBERSHIP_ALLOW_ALL = "allow_all"
MEMBERSHIP_NONE = "not_a_member"

MEMBERSHIP_STEPS = (MEMBERSHIP_BY_EMAIL, MEMBERSHIP_BY_DOMAIN, MEMBERSHIP_ALLOW_ALL)

#: The membership answer is a step rather than a boolean, so a page can say which rule
#: admitted the viewer. These are the keys the resolved membership carries.
MEMBERSHIP_STEP_FIELD = "membership_step"
MEMBER_OF_FIELD = "is_member"
MATCHED_DOMAIN_FIELD = "matched_domain"
MEMBER_ID_FIELD = "member_id"

#: The key an email is stored under, in a member row and in a resolved membership. Named
#: once because both the write and the read must agree, and a page that reads it does not
#: guess.
EMAIL_FIELD = "email"

MEMBERSHIP_LABELS = {
    MEMBERSHIP_BY_EMAIL: "An explicit member email.",
    MEMBERSHIP_BY_DOMAIN: "An email domain on the group.",
    MEMBERSHIP_ALLOW_ALL: "The group allows anyone through the link's other gates.",
    MEMBERSHIP_NONE: "Not a member of this group.",
}

#: ``allow_all``, quoted: "When true, anyone who passes the link's other access gates is
#: treated as a group member - the email/domain membership check is skipped." It is step
#: three and it short-circuits the other two, because a group that allows everyone is not
#: a group that checks.
ALLOW_ALL = "allow_all"

#: ``domains``, quoted: "Email domains whose addresses are automatically treated as members
#: (e.g. ``@acme.com`` admits ``jane@acme.com``). Accepts bare (``acme.com``) or
#: ``@``-prefixed (``@acme.com``) domains; both are lowercased and normalized to
#: ``@acme.com``. Duplicates are removed."
DOMAINS_FIELD = "domains"
DOMAIN_PREFIX = "@"

#: The key a link points at its group with. On the link row, so the engine can find every
#: link a group owns by one indexed lookup.
GROUP_ID_FIELD = "group_id"
DOMAIN_RULE_TEXT = (
    "Give a domain bare as acme.com or with a leading @ as @acme.com. Both are lowercased "
    "and stored as @acme.com, and duplicates are removed."
)

#: Adding members is silent and idempotent, quoted: "Viewers are created for unknown
#: addresses; already-present members are skipped, so the call is idempotent. **No
#: invitation emails are sent.**" So this workflow sends nothing and the constant below is
#: carried on every response that added a member.
NO_INVITATIONS = "No invitation email is sent. The address becomes a member when it appears on the group's own list."


# --------------------------------------------------------------------------- #
# The three size caps
# --------------------------------------------------------------------------- #
#
# Quoted from ``openapi.json`` by the issue: "``domains[]`` max 100", "``emails[]`` max
# 500, max 1000 entries". Named here so the validation, the page and the tests all read
# the same number.

MAX_DOMAINS = 100
MAX_MEMBERS_PER_CALL = 500
MAX_PERMISSIONS_PER_CALL = 1000


# --------------------------------------------------------------------------- #
# Group links are always email-gated
# --------------------------------------------------------------------------- #

#: The guide says "Group links are always email-gated; a viewer must be a member (by email
#: or domain) to get in, unless the group has ``allow_all``." There is no setting that turns
#: this off, so there is no field to set and no route to flip it.
LINK_EMAIL_GATED = True

EMAIL_GATE_NOTE = (
    "A group link is always email-gated. A viewer has to be a member, by email or by domain, "
    "unless the group allows everyone. There is no setting that turns this off."
)


# --------------------------------------------------------------------------- #
# The conflict between the two scopes
# --------------------------------------------------------------------------- #

#: Quoted: "Rejected with ``422`` on links with ``audience_type: \"group\"`` - their group
#: determines visibility; switch the link to ``audience_type: \"general\"`` first."
SCOPE_CONFLICT_REJECTED = "group_link_rejects_link_overrides"

SCOPE_CONFLICT_MESSAGE = (
    "This link belongs to a group, and the group determines what the link shows. Switch the "
    "link to a general audience before setting permissions on the link itself."
)


# --------------------------------------------------------------------------- #
# Visibility outcomes
# --------------------------------------------------------------------------- #

VISIBLE = "visible"
VIEW_ONLY = "view_only"
NOT_A_MEMBER = "not_a_member"
HIDDEN_NO_PERMISSION = "hidden_no_permission"
LINK_OVERRIDE_WITHHELD = "link_override_withheld"

VISIBILITY_STATES = (
    VISIBLE,
    VIEW_ONLY,
    NOT_A_MEMBER,
    HIDDEN_NO_PERMISSION,
    LINK_OVERRIDE_WITHHELD,
)

#: The second denial state, kept apart from :data:`HIDDEN_NO_PERMISSION` because the row
#: exists and says ``can_view: false``. "Nobody granted it" and "somebody revoked it" are
#: different facts for a rep looking at a grid, and only one of them is this workflow's
#: shipped default.
HIDDEN_CAN_VIEW_FALSE = "hidden_can_view_false"

#: Why an item is hidden, so a page can say which of the two rules hid it. Both rules are
#: refusals, and the specification requires both to be refusals rather than a precedence
#: order: an item without a row is hidden, and a link that belongs to a group cannot be
#: overridden.
DENY_NO_PERMISSION_ROW = "no_permission_row"
DENY_CAN_VIEW_FALSE = "can_view_is_false"

DENY_REASONS = (DENY_NO_PERMISSION_ROW, DENY_CAN_VIEW_FALSE)

#: The keys a per-item decision carries. ``state`` is one of the names above; the two
#: flags are the row's own booleans; ``row_present`` answers whether a grant existed at
#: all, which is what separates the two denials.
DENY_REASON_FIELD = "deny_reason"
ROW_PRESENT_FIELD = "row_present"

#: Set on a folder row this workflow wrote to keep the tree navigable. Kept so the grid
#: can say which folders were opened for an item rather than which the rep chose.
AUTO_OPENED_FIELD = "auto_opened"

#: What a write did, per scope. ``touched`` is the entries that actually changed, not the
#: entries that were sent: a delta call that re-sends an identical row changed nothing.
#: ``untouched`` is what a delta left alone, and ``dropped`` is what a full replace
#: removed. Each writer returns only the one that applies to its own semantics.
TOUCHED_KEY = "touched"
UNTOUCHED_KEY = "untouched"
DROPPED_KEY = "dropped"


# --------------------------------------------------------------------------- #
# Group summary counts
# --------------------------------------------------------------------------- #
#
# ``member_count`` and ``link_count`` are the specification's own field names on
# ``DataroomGroup``. Both are counted, never stored, so a count cannot drift from the rows
# it describes.

MEMBER_COUNT_FIELD = "member_count"
LINK_COUNT_FIELD = "link_count"
PERMISSION_COUNT_FIELD = "permission_count"

#: The audience type key on a link row. Read by :func:`rules.require_link_scope` to
#: decide whether a link override is allowed at all.
AUDIENCE_TYPE_FIELD = "audience_type"


# --------------------------------------------------------------------------- #
# What this workflow is and is not worth
# --------------------------------------------------------------------------- #

#: Rendered on the page and carried in every response, because the cheap way to keep a
#: claim honest is to make it part of the data rather than a line of copy somebody can
#: delete.
LIMITATION = (
    "This scopes what an audience is shown. It does not invite anybody, it does not send an "
    "email, and it does not check that the address behind a request belongs to the person "
    "who made it. The items are the room's own document and folder rows, read and filtered, "
    "never copied."
)

NOT_PROOF = (
    "A membership match records that an address matched a list, a domain or an open group. "
    "It does not establish who used the address."
)

ASSUMPTION = (
    "Two parts of this workflow are assumptions rather than sourced facts. The item ids this "
    "grid points at are the room's own library document and folder rows, because the cited "
    "sources describe a vendor's own DataroomDocument join rows and no such join table is "
    "public API here. And the permissions grid surface is documented only as a dashboard, so "
    "this page is a reconstruction of it: the object it edits is sourced, its layout is not."
)

SCOPE_OWNER = (
    "This workflow owns the per-item access control list for both scopes: the group's "
    "delta-upsert set and the link's full-replace set, and the rule that a group link "
    "refuses link overrides."
)

#: The keys every response carries, so a caller cannot read a permission set without also
#: reading what it is worth and which parts of it are assumed.
OWNER_FIELD = "scope_owner"
ASSUMPTION_FIELD = "not_sourced"
LIMITATION_FIELD = "limitation"
NOT_PROOF_FIELD = "not_proof"


def vocabulary_payload() -> dict[str, Any]:
    """Everything this module asserts, for a reviewer and for the page.

    Served by ``GET /api/wf-074/vocabulary`` so the grid cannot drift from the rules that
    validate it: the item types, the two scopes and their write semantics, the membership
    order, the three size caps and the honesty sentences all come from the same tables the
    validator reads.
    """

    return {
        "item_types": [
            {
                "id": item_type,
                "label": ITEM_TYPE_LABELS[item_type],
                "collection": ITEM_TYPE_COLLECTIONS[item_type],
                "parent_field": PARENT_FOLDER_FIELD,
                "root": ROOT_FOLDER,
            }
            for item_type in ITEM_TYPES
        ],
        "permission_entry": {
            "required": list(REQUIRED_ENTRY_FIELDS),
            "closed": True,
            "fields": list(PERMISSION_ENTRY_FIELDS),
            "rules": "An entry carries these four keys and no others.",
        },
        "scopes": [
            {
                "id": scope,
                "semantics": SCOPE_SEMANTICS[scope],
                "description": SCOPE_DESCRIPTIONS[scope],
            }
            for scope in SCOPES
        ],
        "audiences": [
            {"id": audience, "label": AUDIENCE_LABELS[audience]} for audience in AUDIENCE_TYPES
        ],
        "membership": {
            "steps": [{"id": step, "label": MEMBERSHIP_LABELS[step]} for step in MEMBERSHIP_STEPS],
            "allow_all_field": ALLOW_ALL,
            "allow_all": (
                "When true, anyone who passes the link's other access gates is treated as a "
                "group member and the email and domain checks are skipped."
            ),
            "domain_rule": DOMAIN_RULE_TEXT,
            "no_invitations": NO_INVITATIONS,
        },
        "caps": {
            "domains": MAX_DOMAINS,
            "members_per_call": MAX_MEMBERS_PER_CALL,
            "permissions_per_call": MAX_PERMISSIONS_PER_CALL,
        },
        "link_gating": {
            "email_gated": LINK_EMAIL_GATED,
            "note": EMAIL_GATE_NOTE,
        },
        "scope_conflict": {
            "rule": SCOPE_CONFLICT_REJECTED,
            "message": SCOPE_CONFLICT_MESSAGE,
        },
        "visibility_states": list(VISIBILITY_STATES) + [HIDDEN_CAN_VIEW_FALSE],
        "deny_reasons": list(DENY_REASONS),
        "collections": list(ALL_COLLECTIONS),
        "item_collections": list(ITEM_COLLECTIONS),
        OWNER_FIELD: SCOPE_OWNER,
        ASSUMPTION_FIELD: ASSUMPTION,
        LIMITATION_FIELD: LIMITATION,
        NOT_PROOF_FIELD: NOT_PROOF,
    }
