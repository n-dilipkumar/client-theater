"""WF-132: the pure rules of in-room conversation, with no store and no framework.

Everything in this module is a function of its arguments. That is what lets the
rules be unit-tested against a hand-written value rather than a database, and it
is the same split WF-004 makes between :mod:`dsr.roles` and its HTTP surface.

The rules are:

* what a comment body is (trimmed, non-empty, bounded, no control characters);
* how a mention is read out of a body (``@handle`` or ``@address``);
* what an anchor is (one of the four kinds, with a non-empty id);
* who may comment, who may edit content, and who the champion is, expressed over
  the role ids WF-004 defines;
* which party a row came from (the room owner is the seller, a grant holder is a
  buyer);
* which buyer questions are unanswered, and therefore escalate.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from dsr.inroom_collaboration import vocabulary as v
from dsr.inroom_collaboration.errors import InRoomValidation

# --------------------------------------------------------------------------- #
# Comments
# --------------------------------------------------------------------------- #

#: A mention is an at-sign followed by a handle or a full address. The handle
#: class stops at whitespace and punctuation, so "@Dana," reads as "Dana"; the
#: optional address suffix lets "@a.buyer@northwind.example" read as one token.
_MENTION_RE = re.compile(r"@([A-Za-z0-9._%+\-]+(?:@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})?)")

#: Control characters other than tab and newline make a body unrenderable and
#: are almost always a paste artefact. They are rejected rather than stripped, so
#: the author is told rather than silently edited.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def normalise_body(value: Any, *, field: str = "body") -> str:
    """Return a comment body that obeys the rules, or raise :class:`InRoomValidation`.

    Trims surrounding whitespace, refuses an empty body, refuses a body with a
    control character, and refuses a body past
    :data:`~dsr.inroom_collaboration.vocabulary.MAX_COMMENT_LENGTH`.
    """
    if value is None:
        raise InRoomValidation("a comment body is required", {field: "required"})
    text = str(value).strip()
    if not text:
        raise InRoomValidation("a comment body cannot be empty", {field: "empty"})
    if _CONTROL_RE.search(text):
        raise InRoomValidation(
            "a comment body cannot contain control characters", {field: "control_characters"}
        )
    if len(text) > v.MAX_COMMENT_LENGTH:
        raise InRoomValidation(
            f"a comment body is limited to {v.MAX_COMMENT_LENGTH} characters",
            {field: f"too_long:{len(text)}"},
        )
    return text


def extract_mentions(body: str) -> list[str]:
    """Every mentioned handle in a body, lower-cased and de-duplicated in order.

    The order is the order they appear, because a UI that renders the mentions
    beside the comment wants them in reading order, not sorted.
    """
    seen: set[str] = set()
    found: list[str] = []
    for match in _MENTION_RE.finditer(body or ""):
        handle = match.group(1).lower()
        if handle and handle not in seen:
            seen.add(handle)
            found.append(handle)
    return found


# --------------------------------------------------------------------------- #
# Anchors
# --------------------------------------------------------------------------- #


def validate_anchor(anchor_type: Any, anchor_id: Any) -> tuple[str, str]:
    """Return a ``(kind, id)`` pair, or raise :class:`InRoomValidation`.

    The kind must be one of the four the research names. The id is required and
    is treated as an opaque string: this workflow does not own the page, section,
    asset or task collections, so it does not pretend to validate that the id
    exists there.
    """
    kind = str(anchor_type or "").strip().lower()
    if kind not in v.ANCHOR_KINDS:
        raise InRoomValidation(
            f"anchor_type must be one of {', '.join(v.ANCHOR_KINDS)}",
            {"anchor_type": kind or "required"},
        )
    identifier = str(anchor_id or "").strip()
    if not identifier:
        raise InRoomValidation("anchor_id is required", {"anchor_id": "required"})
    return kind, identifier


def anchor_label(anchor_type: str, anchor_label: Any, anchor_id: str) -> str:
    """A human label for an anchor, falling back to a readable id.

    A caller may pass the object's own title; when it does not, the label names
    the kind and the id so the thread list is still readable.
    """
    text = str(anchor_label or "").strip()
    if text:
        return text
    return f"{v.ANCHOR_LABELS.get(anchor_type, anchor_type)} {anchor_id}"


# --------------------------------------------------------------------------- #
# Roles and sides
# --------------------------------------------------------------------------- #


def normalise_principal(value: Any) -> str:
    """A principal is an address or a handle, compared case-insensitively."""
    return str(value or "").strip().lower()


def side_for(actor: Any, room_owner: Any) -> str:
    """Which side of the deal an actor is on.

    The room's ``owner`` is the seller, which is the only seller identity this
    repository records. Everyone else - a grant holder, or an actor the room does
    not know - is treated as a buyer, and the permission rules decide whether a
    buyer with no role may act.
    """
    who = normalise_principal(actor)
    owner = normalise_principal(room_owner)
    if who and owner and who == owner:
        return v.SIDE_SELLER
    return v.SIDE_BUYER


def can_comment(role: Any) -> bool:
    """Whether a role may comment or reply.

    Every documented role may comment, and the owner is the room. An actor with
    no role in the room is not on the list, so a stranger cannot post.
    """
    return role in v.COMMENT_ROLES


def can_edit_content(role: Any) -> bool:
    """Whether a role may edit a champion-editable section.

    Room Collaborator and the owner may; Content Contributor and Viewer may not.
    """
    return role in v.EDIT_ROLES


def is_champion(role: Any, side: Any) -> bool:
    """Whether this actor is the buyer-side champion the research names.

    A champion is a buyer-side holder of a content-managing role. The distinction
    exists so the self-service surface can be labelled; the permission is the
    same :func:`can_edit_content` a seller uses, which is what "without a seller"
    means - no separate route and no second rule.
    """
    return side == v.SIDE_BUYER and can_edit_content(role)


def role_label(role: Any) -> str:
    """A readable label for a role id, tolerating a role a team has added."""
    labels = {
        v.ROLE_OWNER: "Owner",
        v.ROLE_COLLABORATOR: "Room Collaborator",
        v.ROLE_CONTRIBUTOR: "Content Contributor",
        v.ROLE_VIEWER: "Viewer",
    }
    if role in labels:
        return labels[role]
    return (str(role) if role else "no role").replace("_", " ").title()


# --------------------------------------------------------------------------- #
# Questions and escalation
# --------------------------------------------------------------------------- #

#: Roles that answer a buyer's question. A reply from any of these, or from any
#: seller-side author, closes a question for the purpose of escalation.
_ANSWERER_ROLES = (v.ROLE_OWNER, v.ROLE_COLLABORATOR, v.ROLE_CONTRIBUTOR)


def _as_datetime(value: Any) -> datetime | None:
    """Read an ISO timestamp, or ``None`` when it is not one.

    A malformed timestamp must not stop a list from rendering, so an unreadable
    one reads as "no time" and the question is simply not yet overdue.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def unanswered_buyer_questions(
    comments: Iterable[Mapping[str, Any]],
    *,
    now: datetime,
    thread_state: str = v.STATE_OPEN,
    sla_hours: int = v.UNANSWERED_QUESTION_HOURS,
) -> list[dict[str, Any]]:
    """Buyer questions in one thread that no seller or champion has answered.

    A question is answered when a later comment in the same thread comes from a
    seller, or from a buyer holding an answering role - a champion replying to
    their own colleague counts, which is the point of champion self-service. A
    resolved thread has no unanswered questions: resolving is the seller's or
    champion's statement that the thread is done.

    Returns one entry per unanswered question, oldest first, carrying the hours it
    has been open. The caller writes the escalation rows; this function decides
    only which questions qualify, so it stays testable without a store.
    """
    ordered = sorted(comments, key=lambda c: (str(c.get("at") or ""), str(c.get("id") or "")))
    if thread_state == v.STATE_RESOLVED:
        return []

    unanswered: list[dict[str, Any]] = []
    for index, comment in enumerate(ordered):
        if comment.get("kind") != v.KIND_QUESTION or comment.get("author_side") != v.SIDE_BUYER:
            continue
        answered = False
        for later in ordered[index + 1 :]:
            author_side = later.get("author_side")
            author_role = later.get("author_role")
            if author_side == v.SIDE_SELLER or author_role in _ANSWERER_ROLES:
                answered = True
                break
        if answered:
            continue
        asked_at = _as_datetime(comment.get("at"))
        if asked_at is None:
            # An unreadable timestamp is not an escalation: the age is unknown, and
            # escalating on unknown age would page a manager from a typo.
            continue
        hours = round((now - asked_at).total_seconds() / 3600, 1)
        if hours < sla_hours:
            continue
        unanswered.append(
            {
                "comment_ref": comment.get("id"),
                "author": comment.get("author"),
                "hours_open": hours,
                "at": comment.get("at"),
                "body": comment.get("body"),
            }
        )
    return unanswered
