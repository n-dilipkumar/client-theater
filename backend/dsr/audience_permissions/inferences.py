"""Every judgement call WF-074 made, with the alternative it rejected.

The specification instructs an implementer directly: "An implementer who needs a flow the
evidence does not contain must derive it and record the derivation, not assume it." This
module is that record.

Each entry names the open question, the evidence that left it open, the options, the one
this build took, and what the rejected options would have cost. A derivation with no
rejected alternative recorded is a guess wearing a derivation's clothes.

The HTTP layer serves this table at ``GET /api/wf-074/decisions`` so the record is
readable by whoever reviews the feature, rather than buried in a docstring.
"""

from __future__ import annotations

from typing import Any

from dsr.audience_permissions import vocabulary as vocab

DECISIONS: dict[str, dict[str, Any]] = {
    "DERIVED_ITEM_IDS_ARE_THIS_ROOMS_LIBRARY_ROWS": {
        "question": "What rows do the permission entries point at?",
        "left_open_by": (
            'The specification\'s data sources name "Papermark `DataroomGroup`, '
            '`DataroomGroupMember`, `DataroomGroupPermission` (and the parallel '
            '`LinkPermission` set); `DataroomDocument` join rows (`ddoc_...`) and data room '
            'folder rows as the `item_id` targets". Those are a vendor\'s own internal join '
            'rows, and no such collection is public API in the cited sources.'
        ),
        "options": {
            "this_rooms_library": (
                "Point the entries at the room's own document and folder rows, which "
                "already exist as dsr.library's 'document' and 'documentFolder' collections."
            ),
            "own_join_table": "Add a 'dataroom_document' join table this workflow owns.",
            "opaque_strings": "Accept any item_id as an opaque string with nothing behind it.",
        },
        "chosen": "this_rooms_library",
        "rejected_because": (
            "A join table this workflow owns would hold a second copy of every document, so "
            "a grant could point at an id whose document no longer exists and nothing would "
            "ever say so. Opaque strings are worse in the same direction and quieter: the "
            "grid would render every entry against an item it cannot name, and 'a new group "
            "sees nothing until you grant permissions' could never be demonstrated because "
            "no item would ever resolve. Reading the room's own rows means an entry that "
            "dangles is a fact the workflow can report rather than a state it created."
        ),
        "cost_of_the_choice": (
            "This workflow depends on WF-003 having run. A room with no library rows has no "
            "items to grant, and the page must say that rather than show an empty grid that "
            "looks like a permissions problem. It also means this workflow reads a "
            "collection it does not own, so a team reshaping those rows affects it. "
            ":data:`~dsr.audience_permissions.vocabulary.ASSUMPTION` carries both onto "
            "every response."
        ),
    },
    "DERIVED_ROOM_KEY_IS_ROOM_REF_IN_THE_PAYLOAD": {
        "question": "Where does the room live on a group, a member or a permission row?",
        "left_open_by": (
            "Every record in this product is scoped by the envelope's ``room_id``. The "
            "workflow also needs the room as a filterable payload field, because the "
            "permission set is read by ``find()`` and by the per-room page."
        ),
        "options": {
            "room_ref_payload": (
                "Store the room in the payload under room_ref and project it back to "
                "room_id in every response."
            ),
            "envelope_only": "Use the envelope's room_id alone and filter with store.list().",
            "both_under_room_id": "Store room_id in the payload as well as on the envelope.",
        },
        "chosen": "room_ref_payload",
        "rejected_because": (
            "The envelope is the reason both alternatives fail. AuditedDatabase strips the "
            "reserved keys out of ``data`` before the dynamic index is built, so a payload "
            "key named room_id is discarded on write and a find() on it returns nothing - a "
            "room filter that silently matches no rows is the defect this naming prevents, "
            "and it has already bitten this product once in another feature. Envelope-only "
            "means list() instead of find(), which cannot filter on a group_id at all, and "
            "every permission lookup would have to load a room's whole table and filter in "
            "Python."
        ),
        "cost_of_the_choice": (
            "Two names for one fact. Every response has to project room_ref back to "
            "room_id or a caller reading the payload directly sees a key the envelope does "
            "not have, and a test has to remember which of the two it is asserting on."
        ),
    },
    "DERIVED_ROOM_FILTER_ON_GROUP_ROWS": {
        "question": "How is a group's permissions looked up, given two rows can name one item?",
        "left_open_by": (
            "The specification names a group permission row as item_id plus item_type plus "
            "the two flags. This repository gives every record its own uuid, so the "
            "item_id is not a record id here and cannot be used as the row's primary key."
        ),
        "options": {
            "query_by_group_and_key": (
                "Keep one row per (group, item_type, item_id) and find it with a dotted-path "
                "find() on group_id and item_id."
            ),
            "row_id_is_the_item": "Name the permission row itself with the item's id.",
            "store_the_acl_as_one_field": "Keep the whole permission set in a single array on the group.",
        ),
        "chosen": "query_by_group_and_key",
        "rejected_because": (
            "row_id_is_the_item breaks the moment a room has a document and a folder with "
            "the same id string, which the vendor's own ids do not promise to prevent, and "
            "the research does not say they are prefixed the way this repository's are. "
            "store_the_acl_as_one_field is the one that looks cheapest and is the worst: a "
            "1000-entry array rewritten on every single grant is a write to every permission "
            "row in the room each time one flag flips, the audit log records one change per "
            "flag rather than per grant, and find() cannot answer 'which groups can see this "
            "document' without loading every group's whole set. One row per grant is also "
            "what the specification's own data flow describes."
        ),
        "cost_of_the_choice": (
            "A permission write reads first to decide insert or update, so an upsert is two "
            "operations rather than one. Inside a transaction that is correct and cheap, and "
            "outside one it would be a race - which is why both writers go through "
            "db.transaction() and never the single-record methods."
        ),
    },
    "DERIVED_DOWNLOAD_NEVER_IMPLIES_VIEW": {
        "question": "What does can_download true with can_view false mean?",
        "left_open_by": (
            "The entry has two independent required booleans and the specification's example "
            "gives an audience a folder it may browse but not download. It never states the "
            "reverse combination."
        ),
        "options": {
            "download_implies_view": "A row that permits download always permits view as well.",
            "flags_independent": "The two flags mean exactly what they say and never imply each other.",
        },
        "chosen": "flags_independent",
        "rejected_because": (
            "Making download imply view would let a caller write can_view false with "
            "can_download true and get a state the two-field shape was designed to prevent: "
            "bytes leaving the room for an item the rep said nobody could open. Normalising "
            "it silently is worse than refusing it, because the rep's grid would show one "
            "thing and the room would serve another. So the pair is stored as sent and "
            "resolved as sent, and can_view false hides the item whatever download says."
        ),
        "cost_of_the_choice": (
            "A rep can save a row that reads oddly, and the grid has to render it without "
            "help. The decided state for it is hidden_can_view_false with a deny reason, so "
            "the row is not invisible, it is named."
        ),
    },
    "DERIVED_ANCESTORS_GRANT_VIEW_NOT_DOWNLOAD": {
        "question": "What flags does an auto-opened ancestor folder get?",
        "left_open_by": (
            'The source says "Ancestor folders of any item made visible are automatically '
            'set to `can_view: true` so the folder tree stays navigable." It names one flag '
            "and does not say what happens to the other.'
        ),
        "options": {
            "view_only": "Auto-opened ancestors get can_view true and can_download false.",
            "copy_the_child": "An ancestor inherits the flags of the item that opened it.",
        },
        "chosen": "view_only",
        "rejected_because": (
            "Copying the child's flags would grant download on a folder nobody chose, and a "
            "folder is exactly the kind of item whose download is the whole point of the "
            "second flag: the specification's own example is a folder an audience may browse "
            "but not download. Granting it because some document inside happened to be "
            "downloadable would hand out the whole folder's bytes on the strength of one "
            "row. Only the flag the source names is set."
        ),
        "cost_of_the_choice": (
            "An auto-opened folder reads as view_only on the grid even when every document "
            "inside it is downloadable, which can look wrong to a rep. It is marked with "
            ":data:`~dsr.audience_permissions.vocabulary.AUTO_OPENED_FIELD` so the page can "
            "say it was opened for a child rather than chosen."
        ),
    },
    "DERIVED_MALFORMED_TREE_ANSWERS_WITH_WHAT_IT_CAN_PROVE": {
        "question": "What happens when a folder's parent chain is broken or circular?",
        "left_open_by": (
            "The ancestor walk is specified. The failure case is not. parentFolderId lives "
            "in an open JSON payload, so nothing in this product stops a team writing a "
            "folder whose parent is itself, or a parent that does not exist."
        ),
        "options": {
            "walk_with_a_guard": (
                "Stop at the root, at a missing parent and at a repeat, and return the chain "
                "that was proven."
            ),
            "reject_the_write": "Refuse a grant whose item has a broken parent chain.",
            "walk_forever": "Follow the chain without a guard.",
        ),
        "chosen": "walk_with_a_guard",
        "rejected_because": (
            "Refusing the write couples this workflow's grant path to the integrity of a "
            "collection it does not own: one bad folder row in WF-003 would make every grant "
            "in the room fail with a message about somebody else's data. Walking without a "
            "guard turns one malformed row into a request that never returns, which is the "
            "failure a single bad record must not be able to cause. So the walk is "
            "defensive and the broken chain is reported as a found partial chain, and the "
            "grid still shows what it can prove."
        ),
        "cost_of_the_choice": (
            "A folder whose parent is missing is never auto-opened, so an item deep in a "
            "broken tree is granted but not reachable through the folder tree. The item row "
            "itself is still correct, so the grant is not lost."
        ),
    },
    "DERIVED_DUPLICATE_ENTRIES_IN_ONE_CALL_ARE_REFUSED": {
        "question": "What happens when one payload names the same item twice?",
        "left_open_by": (
            "The specification says entries are upserted and says nothing about two rows "
            "for one item in the same call."
        ),
        "options": {
            "refuse": "Two entries for one item in one call is a validation failure.",
            "last_wins": "The later entry overwrites the earlier one.",
            "first_wins": "The earlier entry is kept and the later one ignored.",
        ),
        "chosen": "refuse",
        "rejected_because": (
            "Both 'wins' rules make the outcome depend on list order for a caller that "
            "already has two contradictory answers. Refusing says the payload does not "
            "contain one desired state, which is true, and it costs the rep one edit. A "
            "silent winner here is a permissions defect that surfaces as the wrong document "
            "in front of the wrong buyer."
        ),
        "cost_of_the_choice": (
            "A rep pasting a row twice gets an error instead of a no-op. That is a worse "
            "experience than either wins rule and is the deliberate price."
        ),
    },
    "DERIVED_EMAIL_GATE_HAS_NO_OFF_SWITCH": {
        "question": "Is email gating stored per link, or is it a property of the audience?",
        "left_open_by": (
            'The guide says "Group links are always email-gated; a viewer must be a member '
            '(by email or domain) to get in, unless the group has `allow_all`." It states '
            "the rule as unconditional and describes no field that carries it."
        ),
        "options": {
            "derived_property": "Derive it from audience_type. There is no field and no route.",
            "stored_flag": "Store an email_protected flag on every link.",
        },
        "chosen": "derived_property",
        "rejected_because": (
            "A stored flag can be set to false on a group link, and a group link with the "
            "gate off admits anyone holding the URL, which is the one thing the evidence "
            "says cannot happen. There is no value the flag could hold that was not "
            "researched, so adding one would be an invented capability that reads as a "
            "supported one. Deriving it means the gate cannot be turned off because there "
            "is nothing to turn."
        ),
        "cost_of_the_choice": (
            "A rep who wants a link anyone can open has to set audience_type to general, "
            "which is the documented way out and the reason the conflict message names it."
        ),
    },
    "DERIVED_MEMBER_ROWS_HAVE_NO_INVITATION_STATE": {
        "question": "Does adding a member record that they were invited?",
        "left_open_by": (
            'The source is emphatic: "Viewers are created for unknown addresses; '
            'already-present members are skipped, so the call is idempotent. **No invitation '
            'emails are sent.**" The workflow otherwise invites people, through WF-004.'
        ),
        "options": {
            "no_invite_state": "Record only the membership, and state on the response that nothing was sent.",
            "invited_flag": "Store an invited flag for consistency with the other workflows.",
        },
        "chosen": "no_invite_state",
        "rejected_because": (
            "An invited flag on a row where nothing was sent is a false record, and the "
            "false records are worse than the missing ones because somebody will eventually "
            "act on them. The absence is instead carried in the response text on every call "
            "that added a member, so the fact travels with the result rather than living in "
            "a field nobody reads."
        ),
        "cost_of_the_choice": (
            "This workflow cannot tell a rep whether a buyer knows they were added, so it "
            "cannot answer that question. :data:`~dsr.audience_permissions.vocabulary."
            "LIMITATION` says so in every response rather than leaving it to be discovered."
        ),
    },
}


def describe() -> list[dict[str, Any]]:
    """Every recorded decision, in a stable order."""
    return [{"id": key, **value} for key, value in DECISIONS.items()]


def describe_one(decision_id: str) -> dict[str, Any]:
    """One decision by id, or an empty mapping the HTTP layer turns into a 404."""
    found = DECISIONS.get(decision_id)
    if found is None:
        return {}
    return {"id": decision_id, **found}


def count() -> int:
    return len(DECISIONS)


#: Served beside the decisions so a reader can see which states the grid reports without
#: opening the vocabulary route as well.
VISIBILITY_STATES = list(vocab.VISIBILITY_STATES) + [vocab.HIDDEN_CAN_VIEW_FALSE]