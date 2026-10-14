"""WF-132: every researched term in-room conversation and champion self-service enforces.

This module is the researched specification for WF-132 made executable, and the
only place a constant named after the specification lives. The specification is
quoted in full in issue 168, which quotes
``docs/research/digital-sales-room-workflows/wf/WF-132.md``.

What the research fixes, and what it leaves open
------------------------------------------------

The research names a user flow, a data flow, four automations, seven distinct
sources and one deliberate flag:

* **The flow**: a buyer comments on a specific page or asset, tags a colleague,
  the thread is answered by the seller or the champion, and the champion edits the
  content, project plan and timeline themselves so a buyer never emails a zip.
* **The anchor**: "Contextual Comments: Leave comments directly on Deal Room
  content - including PDFs, sections, assets, and Mutual Action Plan (MAP) tasks."
  Those four anchor kinds are sourced, and this module names no fifth.
* **The data flow**: "Comment/thread anchored to a room object (page, asset, MAP
  task) -> mentions/notifications -> resolution state; champion edits -> content
  delta -> change log." Each arrow is a collection here.
* **The flag**: "Flagged contested: a room you cannot talk inside may not be a
  room at all." The flag is recorded, not hidden. See :data:`CONTESTED`.
* **Four automations**, without saying which this plugin owns. This build owns
  three - auto-notify on mention, auto-subscribe watchers, and auto-escalate an
  unanswered buyer question - and delegates the fourth. See
  :data:`NOT_IMPLEMENTED`.

Reuse, not re-declaration
-------------------------

Three vocabularies this workflow depends on already exist and are read, not
copied:

* **The roles vocabulary** lives in :mod:`dsr.roles`, WF-004's invite-buyer
  workflow: the three documented room roles plus the unassignable owner role, and
  the ``room_access`` grants that name each buyer's principal and role. This
  workflow resolves "who may comment" and "who may edit content" against those
  roles by reading the same store records, and a test asserts the two
  vocabularies are equal rather than assuming it.
* **The room** lives in the core ``room`` collection: the ``owner`` field is the
  seller, and everyone else on a live grant is a buyer. That is what makes
  "champion self-service" mean a buyer-side participant acting without a seller.
* **The MAP task** lives in WF-124's ``wf124_task`` collection. This workflow
  anchors comments to a task id and records a thread-to-task conversion, but it
  does not write WF-124's rows; see :data:`NOT_IMPLEMENTED`.

Nothing here imports the framework. See :mod:`dsr.inroom_collaboration.engine`
for the only module that reads or writes.
"""

from __future__ import annotations

from typing import Any

TICKET = "WF-132"
FEATURE_ID = "wf-132-inroom-conversation"
NAME = "In-room conversation and champion self-service"
PREFIX = "/api/wf-132"

#: The contract at the top of the ticket. There is one route prefix and the
#: feature module derives every audit source from it.
ROUTE_PREFIX = PREFIX

# --------------------------------------------------------------------------- #
# The contested flag
# --------------------------------------------------------------------------- #
#
# Quoted from the spec's own criticality note: "Flagged contested: a room you
# cannot talk inside may not be a room at all." The note is the reason this
# workflow is worth building as rules rather than assuming it: a reviewer has to
# be able to see the doubt, not have it smoothed away. So it is a constant here,
# it is in the vocabulary response, and it is stamped onto every thread record.

CONTESTED = True
CONTESTED_REASON = (
    "The research flags this workflow as contested: a room you cannot talk "
    "inside may not be a room at all. In-room conversation is therefore treated "
    "as a first-class room surface rather than an add-on, and the flag is kept "
    "visible instead of hidden."
)

# --------------------------------------------------------------------------- #
# Collections
# --------------------------------------------------------------------------- #
#
# Five collections, because the data flow has two halves and each arrow is a
# different thing with a different lifetime. All five are ordinary
# schema-flexible records in ``records.data``; a team adding ``sentiment`` to a
# comment needs no migration and no coordination with anyone.

THREAD_COLLECTION = "wf132_thread"
COMMENT_COLLECTION = "wf132_comment"
MENTION_COLLECTION = "wf132_mention"
EDIT_COLLECTION = "wf132_content_edit"
ESCALATION_COLLECTION = "wf132_question_escalation"
CONVERSION_COLLECTION = "wf132_thread_conversion"

#: WF-004's access grants, read here rather than re-declared. A tag resolves to a
#: recipient by matching this collection's ``principal`` and the room's ``owner``.
#: A test asserts this equals :data:`dsr.roles.ACCESS`.
ACCESS_COLLECTION = "room_access"

#: The core room collection. WF-132 writes no rooms; it reads one to learn who the
#: seller is and to scope a thread to a room.
ROOM_COLLECTION = "room"

#: WF-124's MAP tasks. Anchoring is by id only; this workflow writes no task rows.
MAP_TASK_COLLECTION = "wf124_task"

# --------------------------------------------------------------------------- #
# Anchors
# --------------------------------------------------------------------------- #
#
# Quoted from the evidence: comments land "directly on Deal Room content -
# including PDFs, sections, assets, and Mutual Action Plan (MAP) tasks". The spec
# maps those onto page, section, asset and MAP task, and this module uses that
# four.

ANCHOR_PAGE = "page"
ANCHOR_SECTION = "section"
ANCHOR_ASSET = "asset"
ANCHOR_MAP_TASK = "map_task"

ANCHOR_KINDS: tuple[str, ...] = (ANCHOR_PAGE, ANCHOR_SECTION, ANCHOR_ASSET, ANCHOR_MAP_TASK)

ANCHOR_LABELS: dict[str, str] = {
    ANCHOR_PAGE: "Page",
    ANCHOR_SECTION: "Section",
    ANCHOR_ASSET: "Asset",
    ANCHOR_MAP_TASK: "MAP task",
}

#: Anchor kinds this workflow accepts but cannot cross-check against an owning
#: workflow. The spec claims the page/section dependency (WF-002) and the MAP-task
#: dependency (WF-124) and explicitly does not claim the document library, so an
#: asset anchor is accepted by id with nothing behind it. Recorded rather than
#: silently allowed.
UNVALIDATED_ANCHORS: tuple[str, ...] = (ANCHOR_ASSET,)

# --------------------------------------------------------------------------- #
# Sides, states and kinds
# --------------------------------------------------------------------------- #

#: Which side of the deal wrote a row. The room's ``owner`` is the seller; a live
#: grant holder is a buyer. This is what makes the champion a buyer-side actor.
SIDE_SELLER = "seller"
SIDE_BUYER = "buyer"
SIDES: tuple[str, ...] = (SIDE_SELLER, SIDE_BUYER)

#: A thread is open until someone resolves it, and resolving is not deleting: the
#: record and its comments stay for the audit trail.
STATE_OPEN = "open"
STATE_RESOLVED = "resolved"
THREAD_STATES: tuple[str, ...] = (STATE_OPEN, STATE_RESOLVED)

#: A comment is a plain comment, a reply within the thread, or a question. A
#: question is a distinct kind because only a question can be escalated.
KIND_COMMENT = "comment"
KIND_REPLY = "reply"
KIND_QUESTION = "question"
COMMENT_KINDS: tuple[str, ...] = (KIND_COMMENT, KIND_REPLY, KIND_QUESTION)

#: A mention is notified when it resolves to a live participant, and unresolved
#: when the tagged handle belongs to nobody in the room - which is the WF-004
#: dependency made visible rather than dropped.
MENTION_NOTIFIED = "notified"
MENTION_UNRESOLVED = "unresolved"
MENTION_STATES: tuple[str, ...] = (MENTION_NOTIFIED, MENTION_UNRESOLVED)

# --------------------------------------------------------------------------- #
# Roles, reused from WF-004
# --------------------------------------------------------------------------- #
#
# The ids are WF-004's. They are repeated as constants here because this package
# may import nothing but the store, and a test asserts the two sets are equal, so
# drift is a failing test rather than a silent divergence.

ROLE_OWNER = "room_owner"
ROLE_COLLABORATOR = "room_collaborator"
ROLE_CONTRIBUTOR = "content_contributor"
ROLE_VIEWER = "viewer"

#: Every documented role may comment: WF-004's own role descriptions say a room
#: collaborator, a content contributor and a viewer can all "add comments", and
#: the owner is the room. A person with no role is not on this list.
COMMENT_ROLES: tuple[str, ...] = (
    ROLE_OWNER,
    ROLE_COLLABORATOR,
    ROLE_CONTRIBUTOR,
    ROLE_VIEWER,
)

#: The roles that may edit content. WF-004 says a Room Collaborator "can manage
#: pages and documents" and an owner owns the room; a content contributor can
#: upload but not manage, and a viewer only views and comments. A buyer-side
#: holder of one of these is the champion the research names.
EDIT_ROLES: tuple[str, ...] = (ROLE_OWNER, ROLE_COLLABORATOR)

#: The role a buyer-side champion holds, used only to label the self-service
#: surface. The permission itself is :data:`EDIT_ROLES`.
CHAMPION_ROLE = ROLE_COLLABORATOR

# --------------------------------------------------------------------------- #
# Rules
# --------------------------------------------------------------------------- #

#: How long a buyer question may sit unanswered before it escalates. The research
#: names the automation ("auto-escalate unanswered buyer questions to the rep's
#: manager or the account owner") and no interval, so this is an inference
#: recorded here rather than a sourced number. One business day is the shortest
#: interval that is not obviously wrong to a sales team.
UNANSWERED_QUESTION_HOURS = 24

#: A comment body is bounded so one paste cannot make a thread unreadable. The
#: research names no limit, so this is an inference; 4000 characters is roughly a
#: screen and a half of prose.
MAX_COMMENT_LENGTH = 4000

#: A reply must name the comment it answers. This is the id prefix the engine
#: expects; kept here so the rule and its message share one definition.
MAX_THREAD_DEPTH = 1

# --------------------------------------------------------------------------- #
# The vocabulary response
# --------------------------------------------------------------------------- #


def vocabulary() -> dict[str, Any]:
    """The vocabulary this workflow enforces, so the UI need not hard-code it.

    Includes what is deliberately absent, under ``not_implemented``, and the
    contested flag, under ``contested``. A reviewer who cannot tell "we decided
    not to" from "we forgot to" has to read the source, and the contested flag is
    the specification's own doubt: it belongs in the response, not in a comment.
    """
    return {
        "ticket": TICKET,
        "feature_id": FEATURE_ID,
        "prefix": PREFIX,
        "anchor_kinds": list(ANCHOR_KINDS),
        "anchor_labels": dict(ANCHOR_LABELS),
        "unvalidated_anchors": list(UNVALIDATED_ANCHORS),
        "sides": list(SIDES),
        "thread_states": list(THREAD_STATES),
        "comment_kinds": list(COMMENT_KINDS),
        "mention_states": list(MENTION_STATES),
        "comment_roles": list(COMMENT_ROLES),
        "edit_roles": list(EDIT_ROLES),
        "champion_role": CHAMPION_ROLE,
        "unanswered_question_hours": UNANSWERED_QUESTION_HOURS,
        "max_comment_length": MAX_COMMENT_LENGTH,
        "collections": {
            "thread": THREAD_COLLECTION,
            "comment": COMMENT_COLLECTION,
            "mention": MENTION_COLLECTION,
            "content_edit": EDIT_COLLECTION,
            "question_escalation": ESCALATION_COLLECTION,
            "thread_conversion": CONVERSION_COLLECTION,
            "access_grants": ACCESS_COLLECTION,
        },
        "contested": {
            "flag": CONTESTED,
            "reason": CONTESTED_REASON,
            "how_it_is_built": (
                "Comments, threads and tagging are built as rules on the room's "
                "own objects, and every thread record carries the flag, rather "
                "than the workflow being omitted because it is contested."
            ),
        },
        "automations": {
            "auto_notify_on_mention": "A mention resolves to a live participant and is recorded as notified.",
            "auto_subscribe_watchers": "The thread author and every resolved recipient are added to the thread's watchers.",
            "auto_escalate_unanswered_question": (
                f"A buyer question with no seller or champion reply escalates after "
                f"{UNANSWERED_QUESTION_HOURS} hours to the room owner."
            ),
            "auto_thread_to_task_conversion": (
                "Recorded as a conversion row naming the thread and the MAP task it "
                "should become. The task row itself belongs to WF-124 and is not "
                "written here."
            ),
        },
        "decisions": {
            "transport": (
                "Polling, not WebSocket or SSE. The research leaves the transport open "
                "('Real-time transport (WebSocket/SSE) or polling') and the repository "
                "has no streaming transport; a read model answers every screen."
            ),
            "champion_scope": (
                "A champion is a buyer-side holder of a content-managing role. They "
                "edit through the same routes a seller uses, which is what 'without a "
                "seller' means here; there is no buyer-only edit route."
            ),
            "repetition": (
                "A change log is append-only. The research names 'champion edits -> "
                "content delta -> change log' and no retention or revert rule, so this "
                "workflow records the delta and reverts nothing."
            ),
        },
        "not_implemented": [
            "No real message delivery. The research names 'email + Slack/Microsoft "
            "Teams notification' and no vendor endpoint, so a mention is recorded as "
            "notified and nothing is sent.",
            "No CRM stakeholder lookup. The research names 'CRM for stakeholder "
            "identity', and this repository resolves recipients from WF-004's room "
            "grants instead, which is the stakeholder list the room already has.",
            "No writing of the MAP task row. Auto-thread-to-MAP-task conversion is "
            "recorded here; the task itself is WF-124's record.",
            "No document-library anchor validation. The spec does not claim the "
            "workflow that provisions the asset library, so an asset anchor is "
            "accepted by id and listed under unvalidated_anchors.",
        ],
    }
