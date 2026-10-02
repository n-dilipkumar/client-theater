"""Early access revocation that keeps the evidence (WF-076).

The researched workflow, in its own words, is six operations on a digital
sales room and its share links:

* **Revoke a link.** "Soft-deletes the link. The public URL stops resolving
  immediately; **the row is kept in the database for audit.** If the link used
  a custom-domain slug, the slug is renamed so the original can be reused."
* **Delete a group.** "Deletes the group, its memberships, its permissions, AND
  every share link pointing at it - active group links stop resolving
  immediately."
* **Remove a member.** "The underlying viewer is kept - only their membership in
  this group is removed."
* **Hide an item.** ``PUT .../groups/{gid}/permissions`` - "hide an item
  (``view off``, ``download off``)."
* **Detach a document.** A join-row delete: "the team-library document and its
  attachments to other datarooms are left intact."
* **Delete the dataroom.** "Deletion cascades to every link and folder and is
  unrecoverable; documents stay in the team library."

Two properties run through all six and are the reason the workflow exists.

**Access is cut at request time, with no grace period.** The research is
explicit: "Revocation is request-time, not scheduled - in-flight viewers are cut
on their next request. There is no grace period and no cached-copy recall: the
guarantee is 'the public URL stops resolving immediately.'" So the resolution
rule is a pure function of what is live *now* (:func:`Revocation.resolve`), and
there is deliberately no scheduled job, no un-revoke route and no recovery
window to get wrong.

**The row survives.** A revoked link is *soft*-deleted. The record is not gone,
it is marked, and the audit row that describes the revocation is written in the
same transaction as the change - which this product gets for free, because
every write goes through :class:`~dsr.db.audited.AuditedDatabase`. That is why
nothing here opens a SQLite connection, and why the cascade paths use
:meth:`~dsr.db.audited.AuditedDatabase.bulk_delete`: a cascade that half-applied
would describe work that did not happen, and its ``before_state`` is what a
reviewer reads to answer "which slug did that cascade free".

Vocabulary owned here
---------------------
The six collections below are this feature's alone. They are named
``revocation_*`` rather than after the vendor's entities so that no two features
can claim one path or one collection:

======================  ==========================================
Collection              The researched thing it holds
======================  ==========================================
``revocation_link``     Papermark ``Link``: a share link, and the row
                        that a revoke marks rather than removes.
``revocation_group``    ``DataroomGroup``: an audience inside a room.
``revocation_member``   ``DataroomGroupMember``: one buyer in one group.
``revocation_permission``  ``DataroomGroupPermission``: ``view`` /
                        ``download`` for one item in one group.
``revocation_viewer``   The underlying viewer a membership points at.
                        Removed memberships leave this alone.
``revocation_grant``    The ``DataroomDocument`` join row. Detaching
                        deletes this row and nothing else.
======================  ==========================================

Nothing is a migration and nothing is a typed column: every field above lives
in ``records.data`` as ordinary JSON, so a team can add ``embargo_until`` or
``legal_hold`` to a link without coordinating with anyone.

Room mapping
------------
The vendor's *dataroom* is this product's ``room``, and the vendor's *team
library document* is a ``document`` record. Two consequences worth stating
rather than leaving for a reviewer to find:

* A room's ``frozen`` flag is read from the core ``room`` payload
  (``room.data.frozen``). A team that already writes that field gets the freeze
  behaviour for free; a team that does not gets an unfrozen room, which is the
  documented default.
* A document's *team* is read from ``document.data.team`` when present. The core
  demo documents do not carry one, so :meth:`Revocation.attach` treats a
  document with no team as belonging to the dataroom's own team rather than
  refusing everything. The research is categorical about cross-team attaches -
  "cross-team attaches are refused" - so the refusal is implemented, and the
  demo data seeds one genuinely cross-team document to make it reachable.

Boundary: the core ``room`` and ``document`` records are not this feature's to
delete. A "delete the dataroom" cascade here removes the *access graph* - every
link, group, membership, permission and join row in the room - and leaves the
room row itself alone, because WF-001 owns it and eleven other features read
it. Documents survive, which is the researched outcome and is asserted by test.
"""

from __future__ import annotations

from typing import Any, Mapping

# --------------------------------------------------------------------------- #
# Collections. One owner each; see the module docstring.
# --------------------------------------------------------------------------- #

LINKS = "revocation_link"
GROUPS = "revocation_group"
MEMBERS = "revocation_member"
PERMISSIONS = "revocation_permission"
VIEWERS = "revocation_viewer"
GRANTS = "revocation_grant"

#: Every collection this feature writes, in cascade order. ``purge`` walks this
#: list, so adding a collection here is all it takes for the dataroom cascade to
#: cover it - which is the failure mode worth preventing, because a cascade that
#: forgets a collection leaves live links behind.
ALL_COLLECTIONS: tuple[str, ...] = (LINKS, GROUPS, MEMBERS, PERMISSIONS, VIEWERS, GRANTS)

#: The collections the researched *irreversible* cascades remove. ``VIEWERS`` is
#: deliberately absent: "The underlying viewer is kept - only their membership in
#: this group is removed", and a dataroom delete takes its audiences with it but
#: not the people who were ever in them.
CASCADE_COLLECTIONS: tuple[str, ...] = (MEMBERS, PERMISSIONS, LINKS, GROUPS, GRANTS)

# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #

#: What a link can point at. A ``room`` link is open to anyone holding it; a
#: ``group`` link is gated on the requester being a live member of the group,
#: which is what makes "remove just that group membership" a way to stop one
#: buyer without touching the link.
TARGET_ROOM = "room"
TARGET_GROUP = "group"
TARGETS: tuple[str, ...] = (TARGET_ROOM, TARGET_GROUP)

#: The two researched reasons a link stops resolving, kept distinct because the
#: audit row that answers "who cut this off, and how" depends on which.
REVOKED_DIRECTLY = "revoked"
REVOKED_BY_GROUP = "cascade_group"
REVOKED_BY_ROOM = "cascade_room"
REVOCATION_CAUSES: tuple[str, ...] = (REVOKED_DIRECTLY, REVOKED_BY_GROUP, REVOKED_BY_ROOM)

#: Why resolution failed. ``not_found`` is the answer for a slug nobody holds;
#: the rest say what did.
RESOLVES = "ok"
NOT_FOUND = "not_found"
GONE_REVOKED = "revoked"
GONE_CASCADE_GROUP = "group_deleted"
GONE_CASCADE_ROOM = "dataroom_deleted"
GONE_MEMBER_REMOVED = "not_a_member"

#: Sourced: "Revocation is request-time, not scheduled - in-flight viewers are
#: cut on their next request. There is no grace period and no cached-copy
#: recall." This is a constant rather than a setting precisely because it is not
#: one: there is no value of this that would satisfy the research other than 0,
#: and a configurable grace period is the exact knob someone would turn to undo
#: the guarantee.
GRACE_PERIOD_MINUTES = 0

#: Sourced: "There is ... no cached-copy recall". Recorded on every revocation
#: result so a rep reading the API knows a copy already downloaded is still out
#: there, rather than inferring it from silence.
CACHED_COPY_RECALL = "none"

#: Sourced: group delete "matches the dashboard's behavior and cannot be undone";
#: dataroom delete "is unrecoverable". Neither is undone here, and there is no
#: route that would. The row is kept; reissuing access is a different act.
IRREVERSIBLE = False

#: Sourced: "Soft-deletes the link ... the row is kept in the database for
#: audit." The soft delete is ``AuditedDatabase.delete`` without ``hard``, which
#: keeps the row *and* its dynamic index, so ``include_deleted=True`` reads and a
#: restore stay cheap.
HARD_DELETE = False

#: The prefix a freed slug is renamed to. Papermark only says the slug "is
#: renamed"; naming the shape is this build's decision, and making it recognisable
#: means a leaked URL shows a tombstone rather than an unrelated live room.
REVOKED_SLUG_PREFIX = "revoked-"

#: Sourced: "If the link used a custom-domain slug, the slug is renamed so the
#: original can be reused." The rename is conditional on a custom domain, which
#: is the one place the research draws a line and this build keeps it.
CUSTOM_DOMAIN = "custom_domain"

#: The actor recorded for a request that identifies nobody, matching the rest of
#: the product.
DEFAULT_ACTOR = "dana"


def is_custom_domain(domain: Any) -> bool:
    """Does this link's domain count as a custom domain?

    Sourced narrowly: the rename happens "if the link used a custom-domain
    slug", so a link served from the product's own host has no registry entry to
    free and must keep its slug for the audit trail to read sensibly.
    """
    value = str(domain or "").strip().lower()
    return bool(value) and value != "app"


def tombstone_slug(link_id: str, original: str) -> str:
    """The slug a revoked custom-domain link is renamed to.

    Derived from the link id rather than the clock, so re-revoking the same row
    produces the same name and a test does not have to freeze time to assert it.
    The original slug is kept in ``original_slug`` - the point of the rename is
    that the *original* becomes reusable, not that the name is destroyed.
    """
    tail = link_id.split("_")[-1][:8] or link_id[:8]
    stem = str(original or "link").strip().strip("/").replace("/", "-")[:40] or "link"
    return f"{REVOKED_SLUG_PREFIX}{stem}-{tail}"


def payload(record: Mapping[str, Any]) -> Mapping[str, Any]:
    """The JSON body of a record, whether or not it is wrapped.

    ``AuditedDatabase`` returns ``{"id":..., "data": {...}}`` while this module's
    own helpers are handed plain dictionaries in unit tests, so every read goes
    through here rather than assuming one shape.
    """
    inner = record.get("data")
    return inner if isinstance(inner, Mapping) else record


# --------------------------------------------------------------------------- #
# Refusals. One type per reason, so the router maps one type to one status.
# --------------------------------------------------------------------------- #


class RevocationRefusal(RuntimeError):
    """Base for every refusal this module raises."""


class RevocationNotFound(RevocationRefusal):
    """A room, link, group, member, permission or join row does not exist.

    Also raised when the thing exists but has already been revoked, *unless* the
    caller asked for the retained row - a second revoke of the same link is a
    conflict rather than a 404, and :class:`RevocationConflict` says so.
    """


class RevocationConflict(RevocationRefusal):
    """The request is well-formed but the thing is already in that state.

    Revoking a revoked link, detaching a detached document, and taking a slug
    that is still held are all this. Each is a 409 rather than a silent no-op,
    because a caller that believes it cut access off needs to be told when it
    had already happened.
    """


class RevocationBadRequest(RevocationRefusal):
    """The request is malformed, or omits a confirmation an irreversible act needs."""


class RevocationFrozen(RevocationRefusal):
    """The dataroom is frozen and this operation changes its structure.

    Sourced: "Frozen datarooms refuse new attachments", repeated on detach and
    on move. Revocation is *not* in that list, so a frozen room can still have
    its access cut - see :data:`FROZEN_REFUSALS`.
    """


class RevocationCrossTeam(RevocationRefusal):
    """The document belongs to a different team from the dataroom.

    Sourced: "The document must belong to the same team as the dataroom;
    cross-team attaches are refused."
    """


#: Operations a frozen dataroom refuses. Taken from the three the research names
#: and nothing else: "Frozen datarooms refuse new attachments", repeated for
#: detach and for move. ``revoke_link`` is absent on purpose, and the absence is
#: the interesting part - embargoing a document by freezing the room must not
#: leave last month's links live, so freezing governs structure, not access.
FROZEN_REFUSALS: frozenset[str] = frozenset({"attach", "detach", "move"})


__all__ = [
    "ALL_COLLECTIONS",
    "CACHED_COPY_RECALL",
    "CASCADE_COLLECTIONS",
    "CUSTOM_DOMAIN",
    "DEFAULT_ACTOR",
    "FROZEN_REFUSALS",
    "GRACE_PERIOD_MINUTES",
    "GRANTS",
    "GROUPS",
    "HARD_DELETE",
    "IRREVERSIBLE",
    "LINKS",
    "MEMBERS",
    "PERMISSIONS",
    "RESOLVES",
    "REVOKED_BY_GROUP",
    "REVOKED_BY_ROOM",
    "REVOKED_DIRECTLY",
    "REVOKED_SLUG_PREFIX",
    "REVOCATION_CAUSES",
    "Revocation",
    "RevocationBadRequest",
    "RevocationConflict",
    "RevocationCrossTeam",
    "RevocationFrozen",
    "RevocationNotFound",
    "RevocationRefusal",
    "TARGETS",
    "TARGET_GROUP",
    "TARGET_ROOM",
    "VIEWERS",
    "is_custom_domain",
    "payload",
    "tombstone_slug",
]


def __getattr__(name: str) -> Any:
    """Expose :class:`Revocation` lazily, so ``import dsr.revocation`` is cheap.

    The engine imports this module, so importing it eagerly here would be a
    cycle. :func:`__getattr__` is the module-level equivalent of a deferred
    import and keeps ``from dsr.revocation import Revocation`` working.
    """
    if name == "Revocation":
        from dsr.revocation.engine import Revocation

        return Revocation
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
