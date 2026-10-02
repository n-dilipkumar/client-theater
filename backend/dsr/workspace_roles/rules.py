"""The role rules of WF-077, as pure functions.

Nothing here touches a database or a clock. Every function takes the workspace's
memberships and the proposed change and answers what would happen, which is what
lets the preview route and the write route share one implementation and therefore
cannot disagree - the same shape
:mod:`dsr.reassign.rules` uses for the same reason.

The four researched rules, each with the sentence it comes from:

* The **owner**'s role cannot be changed. ``OWNER_IMMUTABLE_QUOTE``.
* The **last admin**'s role cannot be changed. ``LAST_ADMIN_QUOTE``.
* A **Guest** promoted to a non-``Collaborator`` role is auto-upgraded to a Full
  seat. ``SEAT_UPGRADE_QUOTE``.
* The caller must be "an organization admin, a workspace admin, or hold a role
  with permission to edit member roles". ``CALLER_QUOTE``.

The no-implicit-hierarchy rule belongs to the scopes half and lives in
:mod:`dsr.workspace_roles.scopes`, because "no hierarchy" is a statement about
token scope sets and not about human roles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from dsr.workspace_roles import vocabulary as vocab
from dsr.workspace_roles.errors import RoleError, RoleForbidden

# --------------------------------------------------------------------------- #
# Normalisation
# --------------------------------------------------------------------------- #


def normalise_seat(raw: Any) -> str:
    """Fold a seat name to one of :data:`vocabulary.SEATS`.

    Unknown input is refused rather than defaulted. Defaulting a seat would be
    the one change in this module that silently *widens* somebody's entitlement,
    and a typo in an API call should not quietly buy a Full seat.
    """
    text = str(raw or "").strip().lower()
    if text not in vocab.SEATS:
        raise RoleError(
            f"seat must be one of {', '.join(vocab.SEATS)}, not {raw!r}",
            seat=raw,
        )
    return text


def role_key(name: str) -> str:
    """The lookup key for a role name: lowercased, spaces to underscores.

    Roles are *stored* in the vendor's own spelling (``Admin``) so a role read
    back out of the audit log is the name that was written. Comparisons use this
    key, so ``"admin"``, ``"Admin"`` and ``"ADMIN"`` are one role - which is what
    a client sending the documented name expects - while the stored spelling is
    untouched.
    """
    return str(name or "").strip().lower().replace(" ", "_")


def is_built_in(name: str) -> bool:
    """Whether a name is one of the four built-in roles, compared case-insensitively.

    The *stored* spelling is never touched - a role read out of the audit log is
    the name that was written - but every comparison folds case, so a client
    sending ``admin`` hits the same role as one sending ``Admin``. Without this the
    same role would be reachable by two spellings and refused by a third, which is
    the sort of inconsistency that turns into a support ticket.
    """
    return role_key(name) in {role_key(built_in) for built_in in vocab.BUILT_IN_ROLES}


def display_role(role: Mapping[str, Any], custom: Mapping[str, Any] | None = None) -> str:
    """The name to show and store for a member, given their role record.

    A custom role carries its own ``name``, which is the whole point of "or the
    name of a custom role": the display name is whatever the workspace defined,
    and the built-in spelling is used only when the role is built in.
    """
    raw = role.get("role")
    if raw and is_built_in(str(raw)):
        return str(raw)
    if custom and custom.get("name"):
        return str(custom["name"])
    return str(raw or "")


# --------------------------------------------------------------------------- #
# Permissions
# --------------------------------------------------------------------------- #


def permissions_for(
    role: Mapping[str, Any],
    *,
    custom_roles: Iterable[Mapping[str, Any]] = (),
) -> tuple[str, ...]:
    """Every permission a role grants, built-in or workspace-defined.

    ``Admin`` is not special-cased: it carries the whole permission set, which is
    what lets "the last member with admin privileges" be a statement about
    permissions rather than about a name. A custom role grants exactly what it
    declares and nothing else, because "or the name of a custom role" means the
    workspace defines its own rights.
    """
    name = str(role.get("role") or "")
    if name == vocab.ROLE_OWNER:
        return vocab.PERMISSIONS
    if is_built_in(name):
        return vocab.ROLE_PERMISSIONS[name]

    key = role_key(name)
    for candidate in custom_roles:
        if candidate.get("deleted"):
            continue
        if role_key(str(candidate.get("name") or "")) == key or candidate.get("id") == name:
            return tuple(str(p) for p in (candidate.get("permissions") or ()))
    return ()


def has_permission(
    role: Mapping[str, Any],
    permission: str,
    *,
    custom_roles: Iterable[Mapping[str, Any]] = (),
) -> bool:
    return permission in permissions_for(role, custom_roles=custom_roles)


def is_admin(role: Mapping[str, Any], *, custom_roles: Iterable[Mapping[str, Any]] = ()) -> bool:
    """Whether a role counts as "admin privileges" for the last-admin rule.

    The research says the *last member with admin privileges*, not "the last
    member whose role is called Admin". So a custom role that grants every
    permission is an admin for this purpose, and a custom role that grants only
    some of them is not. That is the literal reading and it is also the safe one:
    widening "admin" would let a demotion slip past the guard rail.
    """
    if role.get("role") == vocab.ROLE_OWNER:
        return True
    granted = set(permissions_for(role, custom_roles=custom_roles))
    return bool(granted) and granted >= set(vocab.PERMISSIONS)


def require_permission(
    role: Mapping[str, Any],
    permission: str,
    *,
    actor: str = "",
    custom_roles: Iterable[Mapping[str, Any]] = (),
) -> None:
    """The caller rule, as a refusal. ``CALLER_QUOTE``.

    "You must be an organization admin, a workspace admin, or hold a role with
    permission to edit member roles" - so the check is on a permission, and the
    two built-in roles that carry it are Admin and Manager. A member with no
    membership at all is refused rather than treated as privileged: failing closed
    is the only safe default for an access check.
    """
    if has_permission(role, permission, custom_roles=custom_roles):
        return
    raise RoleForbidden(
        f"{actor or 'the caller'} must be an organization admin, a workspace admin, or hold a "
        f"role with permission to edit member roles (needs {permission})",
        actor=actor,
        permission=permission,
        role=str(role.get("role") or ""),
    )


# --------------------------------------------------------------------------- #
# The change decision
# --------------------------------------------------------------------------- #

REFUSED_UNKNOWN_ROLE = "unknown_role"
REFUSED_OWNER = "owner_role_immutable"
REFUSED_LAST_ADMIN = "last_admin_role_immutable"
REFUSED_INACTIVE = "member_inactive"
REFUSED_UNKNOWN_MEMBER = "member_not_found"
REFUSED_NO_CHANGE = "no_change"

REFUSALS: tuple[dict[str, str], ...] = (
    {
        "outcome": REFUSED_OWNER,
        "rule": vocab.OWNER_IMMUTABLE_QUOTE,
        "meaning": "The workspace owner's role is fixed. It is not a row in a list that can be edited.",
    },
    {
        "outcome": REFUSED_LAST_ADMIN,
        "rule": vocab.LAST_ADMIN_QUOTE,
        "meaning": (
            "Demoting or removing the last member holding every permission would leave the "
            "workspace with nobody who can administer it, so the change is refused."
        ),
    },
    {
        "outcome": REFUSED_UNKNOWN_ROLE,
        "rule": vocab.BUILT_IN_ROLE_QUOTE,
        "meaning": (
            "The role field accepts a built-in role name or the name of a custom role defined "
            "in this workspace, and nothing else."
        ),
    },
    {
        "outcome": REFUSED_INACTIVE,
        "rule": "Derived: the research says a role change takes effect on the member's next request.",
        "meaning": "An inactive membership has no next request, so changing it would be invisible.",
    },
    {
        "outcome": REFUSED_NO_CHANGE,
        "rule": "Derived: the change is written to the membership record, so a no-op writes nothing.",
        "meaning": "The member already holds that role, so nothing is written and no audit row appears.",
    },
)

ACCEPTED = "accepted"
SEAT_UPGRADED = "seat_upgraded"


@dataclass(frozen=True)
class RoleDecision:
    """What a proposed role change would do, before anything is written.

    ``seat`` is the seat the member will hold afterwards, which is not always the
    seat they hold now: that is the auto-upgrade, and it is visible here rather
    than applied silently in the write path.
    """

    outcome: str
    member_id: str
    current_role: str
    requested_role: str
    current_seat: str
    resulting_seat: str
    seat_changed: bool = False
    reason: str = ""
    rule: str = ""
    patch: Mapping[str, Any] = field(default_factory=dict)

    @property
    def accepted(self) -> bool:
        return self.outcome == ACCEPTED

    @property
    def refused(self) -> bool:
        return not self.accepted

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "accepted": self.accepted,
            "member_id": self.member_id,
            "current_role": self.current_role,
            "requested_role": self.requested_role,
            "current_seat": self.current_seat,
            "resulting_seat": self.resulting_seat,
            "seat_changed": self.seat_changed,
            "reason": self.reason,
            "rule": self.rule,
            "patch": dict(self.patch),
        }


def _quote(outcome: str) -> str:
    for entry in REFUSALS:
        if entry["outcome"] == outcome:
            return str(entry["rule"])
    return ""


def _reason(outcome: str) -> str:
    for entry in REFUSALS:
        if entry["outcome"] == outcome:
            return str(entry["meaning"])
    return ""


def decide_role_change(
    *,
    member: Mapping[str, Any] | None,
    memberships: Sequence[Mapping[str, Any]],
    requested_role: str,
    custom_roles: Sequence[Mapping[str, Any]] = (),
) -> RoleDecision:
    """Decide one role change against the researched guard rails.

    ``memberships`` is every live membership in the workspace, which is what the
    last-admin rule counts. The owner is excluded from that count, because the
    owner is immutable anyway and a workspace whose only admin is the owner cannot
    be locked: the owner can never be demoted, so the workspace is never without
    an admin.

    Pure: no clock, no store, no side effect. Both the preview route and the write
    route call this, so a preview that says "refused" is a refusal that will
    actually happen.
    """
    member_id = str((member or {}).get("id") or "")
    current_role = str((member or {}).get("role") or "")
    current_seat = str((member or {}).get("seat") or vocab.SEAT_FULL)

    def refuse(outcome: str, reason: str = "") -> RoleDecision:
        return RoleDecision(
            outcome=outcome,
            member_id=member_id,
            current_role=current_role,
            requested_role=requested_role,
            current_seat=current_seat,
            resulting_seat=current_seat,
            reason=reason or _reason(outcome),
            rule=_quote(outcome),
        )

    if member is None:
        return refuse(REFUSED_UNKNOWN_MEMBER)

    wanted = str(requested_role or "").strip()
    if not wanted:
        return refuse(REFUSED_UNKNOWN_ROLE, "a role name is required")

    # The owner is checked before anything else, because it is the rule with no
    # exception and no configuration.
    if current_role == vocab.ROLE_OWNER or member.get("is_owner"):
        return refuse(REFUSED_OWNER)

    if member.get("active") is False:
        return refuse(REFUSED_INACTIVE)

    known = is_built_in(wanted) or any(
        not candidate.get("deleted")
        and role_key(str(candidate.get("name") or "")) == role_key(wanted)
        for candidate in custom_roles
    )
    if not known:
        return refuse(REFUSED_UNKNOWN_ROLE)

    if role_key(wanted) == role_key(current_role):
        return refuse(REFUSED_NO_CHANGE)

    # The last-admin guard rail, counted over live members other than the owner.
    if is_admin(member, custom_roles=custom_roles):
        others = [
            other
            for other in memberships
            if other.get("id") != member_id
            and other.get("active") is not False
            and str(other.get("role") or "") != vocab.ROLE_OWNER
        ]
        remaining = [
            other
            for other in others
            if is_admin(other, custom_roles=custom_roles)
            and role_key(str(other.get("role") or "")) != role_key(wanted)
        ]
        if not remaining:
            return refuse(REFUSED_LAST_ADMIN)

    # The seat auto-upgrade, taken literally: a Guest promoted to *anything other
    # than Collaborator* becomes a Full seat.
    #
    # "Anything other than Collaborator" is the whole rule, so a workspace-defined
    # custom role counts as "other than Collaborator" and the guest is upgraded.
    # Reading it as "any built-in role other than Collaborator" would leave a guest
    # promotable to a custom role with no seat, which the sentence does not allow.
    resulting_seat = current_seat
    if current_seat == vocab.SEAT_GUEST and role_key(wanted) != role_key(vocab.COLLABORATOR):
        resulting_seat = vocab.SEAT_FULL

    patch: dict[str, Any] = {"role": wanted}
    if resulting_seat != current_seat:
        patch["seat"] = resulting_seat

    seat_changed = resulting_seat != current_seat
    return RoleDecision(
        outcome=ACCEPTED,
        member_id=member_id,
        current_role=current_role,
        requested_role=wanted,
        current_seat=current_seat,
        resulting_seat=resulting_seat,
        seat_changed=seat_changed,
        reason=(
            f"{current_role} -> {wanted}"
            + (
                f"; seat upgraded {current_seat} -> {resulting_seat} (guest_seat_upgrade)"
                if seat_changed
                else ""
            )
        ),
        rule=vocab.SEAT_UPGRADE_QUOTE if seat_changed else "",
        patch=patch,
    )


def outcome_table() -> list[dict[str, str]]:
    """The refusal table, for the UI and for a reviewer to disagree with."""
    return [dict(entry) for entry in REFUSALS]
