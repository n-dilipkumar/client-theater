"""WF-004 — invite buyers to a room with a role and an access expiry.

This is the Share dialog's domain: the role vocabulary, the delegation rule,
the invitation window, and the expiry arithmetic. It sits on top of
:class:`~dsr.store.RecordStore` and writes only through it, so every change it
makes lands in the audit log in the same transaction.

What the research documents, and what is implemented here
-------------------------------------------------------
* Three room roles, with the descriptions Liferay publishes for them.
* One role and one expiration date apply to the whole invitation.
* An invitation expires 48 hours after it is sent.
* An invitee who already belongs to the room joins as soon as the invitation
  is sent; a new invitee joins when they accept.
* Access ends at the end of the expiration date in UTC, silently: past that
  instant the person is simply no longer in *Who Has Access*.
* An invite without an expiration date reads *No Expiration*.
* When access lapses within seven days, the row and a dialog banner warn about
  it — and only members who can share the room ever see that surface.
* Viewers cannot share. Collaborators and Contributors may assign Content
  Contributor and Viewer; only the room's owner may assign Room Collaborator.
* The Owner can never be changed or removed.

Record shape
------------
Two collections, because an emailed invitation and an access grant are
different things with different lifetimes: an invitation has a 48-hour
acceptance window, a grant has an expiry date or none at all. The choice was
recorded in ``orchestration/decisions/jev-audit.jsonl``.

* ``room_invitation`` — one row per invited address.
* ``room_access`` — one row per person who has, or once had, access.

Both are ordinary schema-flexible records. Every field below lives in
``records.data`` as JSON, and a team adding ``costs_center`` to a grant needs no
migration and no coordination with anyone.
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping, Sequence

from dsr.db.audited import utcnow
from dsr.store import RecordStore

# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #

INVITATIONS = "room_invitation"
ACCESS = "room_access"

#: An invitation must be accepted within this many hours of being sent.
INVITATION_TTL_HOURS = 48

#: Access lapsing inside this window raises the row label and the banner.
EXPIRING_SOON_DAYS = 7

OWNER = "room_owner"
"""Pseudo-role for the room's owner. Not assignable: the owner is the room, not
a grant, and the research records that the owner cannot be changed."""

#: The three documented room roles, most privileged first. The order is the
#: privilege order and is what the delegation rule is written against.
ROLES: tuple[dict[str, Any], ...] = (
    {
        "id": "room_collaborator",
        "label": "Room Collaborator",
        "description": "Can manage pages and documents, add room comments, and share the room.",
    },
    {
        "id": "content_contributor",
        "label": "Content Contributor",
        "description": "Can view room content, add comments, upload documents, and share the room.",
    },
    {
        "id": "viewer",
        "label": "Viewer",
        "description": "Can view documents and add comments, but cannot upload documents.",
    },
)

ROLE_IDS: tuple[str, ...] = tuple(role["id"] for role in ROLES)
DEFAULT_ROLE = "viewer"
_ROLE_LABELS = {role["id"]: role["label"] for role in ROLES}
_ROLE_IDS = set(ROLE_IDS)

#: Roles a Room Collaborator or Content Contributor may hand out. Only the owner
#: may assign Room Collaborator.
_DELEGABLE = ("content_contributor", "viewer")

#: The room action/permission table maps Share to Update, so sharing needs an
#: update-capable role. A Viewer gets no Share button.
_CAN_SHARE = {"room_owner", "room_collaborator", "content_contributor"}

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
_DATE_FORMAT = "%Y-%m-%d"


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class AccessError(Exception):
    """Base class for refusals the API maps onto a status code."""

    status = 400


class AccessDenied(AccessError):
    """The actor's role does not permit the action."""

    status = 403


class AccessInvalid(AccessError):
    """The request was well-formed but the values make no sense."""

    status = 400


class AccessMissing(AccessError):
    """The referenced record does not exist, or is no longer live."""

    status = 404


class ConfirmationRequired(AccessError):
    """A destructive change arrived without the documented safety check.

    The research records that the vendor's role change and removal "neither
    asks for confirmation". That is a safety gap, not a feature, so this
    implementation asks. See ``docs/wf-004.md``.
    """

    status = 428


# --------------------------------------------------------------------------- #
# Roles and permissions
# --------------------------------------------------------------------------- #


def role_label(role_id: str | None) -> str:
    """Human label for a role id, tolerating roles a team has added."""
    if role_id == OWNER:
        return "Owner"
    if role_id in _ROLE_LABELS:
        return _ROLE_LABELS[role_id]
    return (role_id or "no role").replace("_", " ").title()


def can_share(role_id: str | None) -> bool:
    """Whether a role may open the Share dialog and change access."""
    return role_id in _CAN_SHARE


def assignable_roles(role_id: str | None) -> list[str]:
    """Role ids this actor may hand out.

    Implements the documented delegation rule: collaborators and contributors
    may assign Content Contributor and Viewer, and only the owner may assign
    Room Collaborator.
    """
    if role_id == OWNER:
        return list(ROLE_IDS)
    if role_id in ("room_collaborator", "content_contributor"):
        return list(_DELEGABLE)
    return []


def role_vocabulary(actor_role: str | None) -> list[dict[str, Any]]:
    """The role vocabulary annotated with what this actor may do with it.

    Served to the UI so the Share dialog can disable a role rather than hide
    it, and so a team that adds a role to the server shows up here without a
    frontend change.
    """
    allowed = set(assignable_roles(actor_role))
    vocabulary = []
    for role in ROLES:
        entry = dict(role)
        entry["assignable"] = role["id"] in allowed
        vocabulary.append(entry)
    return vocabulary


# --------------------------------------------------------------------------- #
# Dates
# --------------------------------------------------------------------------- #


def _now(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def normalise_date(value: Any, *, field: str = "access_valid_until") -> str | None:
    """Validate a calendar date and return it as ``YYYY-MM-DD``.

    ``None`` and the empty string mean *No Expiration*, which is a documented
    outcome rather than a missing value.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if hasattr(value, "strftime") and not isinstance(value, str):
        return value.strftime(_DATE_FORMAT)
    text = str(value).strip()
    try:
        return datetime.strptime(text, _DATE_FORMAT).strftime(_DATE_FORMAT)
    except ValueError as exc:
        raise AccessInvalid(f"{field} must be a date as YYYY-MM-DD, got {text!r}") from exc


def end_of_day_utc(day: str | None) -> datetime | None:
    """The instant access ends: the last moment of ``day`` in UTC.

    The research is explicit that "access ends at the end of the expiration
    date in UTC", so a grant valid until 2026-12-31 is still valid for the
    whole of that UTC day and lapses a millisecond later.
    """
    if not day:
        return None
    parsed = datetime.strptime(day, _DATE_FORMAT).replace(tzinfo=timezone.utc)
    return parsed.replace(hour=23, minute=59, second=59, microsecond=999_000)


def _iso(moment: datetime | None) -> str | None:
    if moment is None:
        return None
    return moment.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# --------------------------------------------------------------------------- #
# Addresses
# --------------------------------------------------------------------------- #


def normalise_email(value: Any) -> str:
    """Lower-case and validate one address."""
    text = str(value or "").strip().lower()
    if not text:
        raise AccessInvalid("an email address is required")
    if not _EMAIL_RE.match(text) or ".." in text:
        raise AccessInvalid(f"{text!r} is not a valid email address")
    return text


def normalise_emails(values: Iterable[Any]) -> list[str]:
    """Normalise, validate, and de-duplicate a list of addresses in order.

    The Share dialog commits an address on Enter or a comma, so the API
    receives a list; duplicates within one invitation would otherwise produce
    two invitations for one person.
    """
    if isinstance(values, (str, bytes)):
        values = [values]
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        email = normalise_email(value)
        if email not in seen:
            seen.add(email)
            result.append(email)
    if not result:
        raise AccessInvalid("at least one email address is required")
    return result


# --------------------------------------------------------------------------- #
# Service
# --------------------------------------------------------------------------- #


class AccessService:
    """Share dialog behaviour for one room, over the audited record store."""

    def __init__(
        self,
        store: RecordStore,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    # -- clock ------------------------------------------------------------- #

    def now(self) -> datetime:
        return _now(self._clock)

    # -- lookups ----------------------------------------------------------- #

    def room(self, room_id: str) -> dict[str, Any]:
        record = self.store.get(room_id)
        if record is None or record["collection"] != "room":
            raise AccessMissing(f"room {room_id} not found")
        return record

    def invitations(self, room_id: str) -> list[dict[str, Any]]:
        return self.store.list(INVITATIONS, room_id=room_id, limit=500)

    def grants(self, room_id: str) -> list[dict[str, Any]]:
        return self.store.list(ACCESS, room_id=room_id, limit=500)

    def access_record(self, access_id: str) -> dict[str, Any]:
        record = self.store.get(access_id)
        if record is None or record["collection"] != ACCESS:
            raise AccessMissing(f"access grant {access_id} not found")
        return record

    def invitation_record(self, invitation_id: str) -> dict[str, Any]:
        record = self.store.get(invitation_id)
        if record is None or record["collection"] != INVITATIONS:
            raise AccessMissing(f"invitation {invitation_id} not found")
        return record

    def is_owner(self, room: Mapping[str, Any], principal: str | None) -> bool:
        owner = (room.get("data") or {}).get("owner")
        return bool(principal) and bool(owner) and str(owner).strip().lower() == str(principal).strip().lower()

    def actor_role(self, room_id: str, actor: str | None) -> str | None:
        """The role ``actor`` holds in this room, or ``None`` for no access.

        The owner is the room's ``owner`` field. Everyone else needs a live
        grant, so removing someone really does take their access away rather
        than leaving a stale row that still answers for them.
        """
        room = self.room(room_id)
        if self.is_owner(room, actor):
            return OWNER
        if not actor:
            return None
        principal = str(actor).strip().lower()
        now = self.now()
        for record in self.grants(room_id):
            if (record["data"].get("principal") or "").strip().lower() != principal:
                continue
            if self._lapsed(record["data"].get("access_valid_until"), now):
                continue
            return record["data"].get("role") or DEFAULT_ROLE
        return None

    def _require_actor(self, room_id: str, actor: str | None) -> tuple[dict[str, Any], str]:
        """Resolve the actor and refuse anyone who cannot share the room."""
        room = self.room(room_id)
        role = self.actor_role(room_id, actor)
        if not can_share(role):
            label = role_label(role) if role else "no access to this room"
            raise AccessDenied(f"{actor or 'anonymous'} has {label} and cannot share this room")
        return room, str(role)

    def _require_assignable(self, actor_role: str, target_role: str) -> None:
        if target_role not in _ROLE_IDS:
            raise AccessInvalid(
                f"unknown role {target_role!r}; known roles are {', '.join(ROLE_IDS)}"
            )
        if target_role not in assignable_roles(actor_role):
            raise AccessDenied(
                f"a {role_label(actor_role)} cannot assign {role_label(target_role)}; "
                "only the room owner can assign Room Collaborator"
            )

    @staticmethod
    def _lapsed(access_valid_until: str | None, now: datetime) -> bool:
        """Whether a grant is past the end of its expiration date in UTC."""
        deadline = end_of_day_utc(access_valid_until)
        return deadline is not None and now > deadline

    # -- reads ------------------------------------------------------------- #

    def _member_view(self, room: Mapping[str, Any], record: Mapping[str, Any], now: datetime) -> dict[str, Any]:
        data = record.get("data") or {}
        owner = self.is_owner(room, data.get("principal"))
        role = OWNER if owner else (data.get("role") or DEFAULT_ROLE)
        expires_at = end_of_day_utc(data.get("access_valid_until"))
        expiring_soon = expires_at is not None and now <= expires_at and expires_at - now <= timedelta(
            days=EXPIRING_SOON_DAYS
        )
        return {
            "id": record["id"],
            "principal": data.get("principal"),
            "role": role,
            "role_label": role_label(role),
            "access_valid_until": data.get("access_valid_until"),
            "expires_at_utc": _iso(expires_at),
            "expiring_soon": expiring_soon,
            "days_until_expiry": (
                round((expires_at - now).total_seconds() / 86400, 1) if expires_at else None
            ),
            "joined_immediately": bool(data.get("joined_immediately")),
            "source": data.get("source"),
            "invitation_id": data.get("invitation_id"),
            "owner": owner,
            "removable": not owner,
            "revision": record.get("revision"),
        }

    def _invitation_view(self, record: Mapping[str, Any], now: datetime) -> dict[str, Any]:
        data = record.get("data") or {}
        expires_at = data.get("expires_at")
        try:
            deadline = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            deadline = None
        hours_left = (
            round((deadline - now).total_seconds() / 3600, 1)
            if deadline and deadline > now
            else 0.0
        )
        return {
            "id": record["id"],
            "email": data.get("email"),
            "role": data.get("role"),
            "role_label": role_label(data.get("role")),
            "access_valid_until": data.get("access_valid_until"),
            "state": data.get("state"),
            "sent_at": data.get("sent_at"),
            "sent_by": data.get("sent_by"),
            "expires_at": expires_at,
            "hours_until_expiry": hours_left,
            "expired": hours_left <= 0,
            "joined_immediately": bool(data.get("joined_immediately")),
            "access_id": data.get("access_id"),
        }

    def snapshot(self, room_id: str, actor: str | None = None) -> dict[str, Any]:
        """Everything the Share dialog needs, computed in one read.

        Lapsed grants are dropped here rather than deleted in the background:
        the documented cut-off is silent, so the honest implementation is a
        read-time filter and the row stays for the audit trail.
        """
        room = self.room(room_id)
        now = self.now()
        role = self.actor_role(room_id, actor)

        members: list[dict[str, Any]] = []
        for record in self.grants(room_id):
            if self._lapsed((record["data"] or {}).get("access_valid_until"), now):
                continue
            members.append(self._member_view(room, record, now))

        pending = [
            self._invitation_view(record, now)
            for record in self.invitations(room_id)
            if (record["data"] or {}).get("state") == "pending"
        ]
        # Oldest first, so the invitation closest to its 48-hour deadline is the
        # one at the top. The id tiebreak matters because a single invite button
        # press stamps every address with the same `sent_at`.
        pending.sort(key=lambda item: (item["sent_at"] or "", item["id"]))

        # The banner counts lapsing access, not pending invitations: a warning
        # about an invite would be noise, a warning about a lapsed grant is not.
        expiring = [m for m in members if m["expiring_soon"]]
        return {
            "room_id": room_id,
            "room_name": (room.get("data") or {}).get("name"),
            "as_of": _iso(now),
            "invitation_ttl_hours": INVITATION_TTL_HOURS,
            "expiring_soon_days": EXPIRING_SOON_DAYS,
            "actor": {
                "principal": actor,
                "role": role,
                "role_label": role_label(role) if role else "No access",
                "is_owner": role == OWNER,
                "can_share": can_share(role),
                "assignable_roles": assignable_roles(role),
            },
            "roles": role_vocabulary(role),
            "members": members,
            "pending_invitations": pending,
            "expiring_soon_count": len(expiring),
            "banner": _banner(len(expiring)),
        }

    # -- writes ------------------------------------------------------------ #

    def invite(
        self,
        room_id: str,
        emails: Iterable[Any],
        *,
        role: str = DEFAULT_ROLE,
        access_valid_until: Any = None,
        actor: str,
    ) -> dict[str, Any]:
        """Send one invitation per address with one role and one expiry.

        The research is explicit that a single role and a single date apply to
        the whole invitation; per-person differences are made afterwards in
        *Who Has Access*.
        """
        room, actor_role = self._require_actor(room_id, actor)
        addresses = normalise_emails(emails)
        self._require_assignable(actor_role, role)

        expires_on = normalise_date(access_valid_until)
        now = self.now()
        if expires_on is not None and end_of_day_utc(expires_on) <= now:
            raise AccessInvalid(
                f"access_valid_until {expires_on} is in the past; access would be over before it starts"
            )

        sent_at = _iso(now) or utcnow()
        invitation_deadline = _iso(now + timedelta(hours=INVITATION_TTL_HOURS))
        source = f"POST /api/rooms/{room_id}/invitations"
        common = {
            "sent_at": sent_at,
            "sent_by": actor,
            "room": (room.get("data") or {}).get("name"),
        }

        invitations: list[dict[str, Any]] = []
        joined: list[str] = []
        for address in addresses:
            invitation = self.store.create(
                INVITATIONS,
                {
                    **common,
                    "email": address,
                    "role": role,
                    "access_valid_until": expires_on,
                    "state": "pending",
                    "token": secrets.token_urlsafe(24),
                    "expires_at": invitation_deadline,
                },
                room_id=room_id,
                actor=actor,
                source=source,
            )
            invitations.append(invitation)

            # An invitee who already belongs to the room joins on send, exactly
            # as documented; a new invitee joins when they accept the email.
            existing = self._live_grant(room_id, address)
            if existing is not None:
                self._apply_grant(existing, role=role, access_valid_until=expires_on,
                                  actor=actor, source=source, joined_immediately=True,
                                  invitation_id=invitation["id"])
                self.store.update(
                    invitation["id"],
                    {
                        "state": "accepted",
                        "accepted_at": sent_at,
                        "access_id": existing["id"],
                        "joined_immediately": True,
                    },
                    actor=actor,
                    source=source,
                )
                joined.append(address)

        return {
            "room_id": room_id,
            "role": role,
            "access_valid_until": expires_on,
            "invitation_ttl_hours": INVITATION_TTL_HOURS,
            "invites": len(invitations),
            "joined_immediately": joined,
            "pending": [address for address in addresses if address not in joined],
            "invitations": [self._invitation_view(r, now) for r in invitations],
            "message": _invite_message(len(invitations), role, expires_on, len(joined)),
        }

    def accept(self, invitation_id: str, actor: str | None = None) -> dict[str, Any]:
        """Accept an invitation, creating the grant it promised.

        The invitee becomes a member of the room on acceptance, with the role
        and expiry the inviter chose.
        """
        invitation = self.invitation_record(invitation_id)
        data = invitation["data"]
        room_id = invitation["room_id"]
        now = self.now()

        if data.get("state") != "pending":
            raise AccessInvalid(
                f"invitation {invitation_id} is {data.get('state')}, not pending"
            )
        try:
            deadline = datetime.fromisoformat(str(data.get("expires_at")).replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise AccessInvalid(f"invitation {invitation_id} has no acceptance window") from exc
        if now > deadline:
            # Record the lapse rather than only reporting it, so an expired
            # invitation is visible in the audit trail. Send a new one.
            self.store.update(
                invitation_id, {"state": "expired", "expired_at": _iso(now)},
                actor=actor or "system", source=f"POST /api/invitations/{invitation_id}/accept",
            )
            raise AccessInvalid(
                f"invitation {invitation_id} expired after {INVITATION_TTL_HOURS} hours; send a new invitation"
            )

        room_id = room_id or ""
        email = data["email"]
        existing = self._live_grant(room_id, email)
        if existing is not None:
            grant = self._apply_grant(
                existing,
                role=data.get("role") or DEFAULT_ROLE,
                access_valid_until=data.get("access_valid_until"),
                actor=actor or email,
                source=f"POST /api/invitations/{invitation_id}/accept",
                joined_immediately=True,
                invitation_id=invitation_id,
            )
        else:
            grant = self.store.create(
                ACCESS,
                {
                    "principal": email,
                    "principal_type": "email",
                    "role": data.get("role") or DEFAULT_ROLE,
                    "access_valid_until": data.get("access_valid_until"),
                    "source": "invitation",
                    "invitation_id": invitation_id,
                    "granted_by": data.get("sent_by"),
                    "joined_immediately": False,
                    "joined_at": _iso(now),
                },
                room_id=room_id,
                actor=actor or email,
                source=f"POST /api/invitations/{invitation_id}/accept",
            )

        self.store.update(
            invitation_id,
            {"state": "accepted", "accepted_at": _iso(now), "access_id": grant["id"]},
            actor=actor or email,
            source=f"POST /api/invitations/{invitation_id}/accept",
        )
        return {
            "invitation": self._invitation_view(self.invitation_record(invitation_id), now),
            "access": grant,
        }

    def update_access(
        self,
        access_id: str,
        *,
        actor: str,
        role: Any = None,
        access_valid_until: Any = None,
        set_expiry: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Change a role or an expiry date in *Who Has Access*.

        A role change is guarded twice: the delegation rule decides whether the
        actor may hand out that role at all, and ``confirm`` records that the
        person in front of the screen acknowledged the change.
        """
        record = self.access_record(access_id)
        room_id = record["room_id"]
        room, actor_role = self._require_actor(room_id, actor)
        data = record["data"]

        if self.is_owner(room, data.get("principal")):
            raise AccessInvalid("the room owner cannot be changed or removed")

        current_role = data.get("role") or DEFAULT_ROLE
        if role is None:
            new_role = current_role
        else:
            new_role = str(role).strip()
            # Checked before the delegation rule so assigning the owner role
            # reports the real reason rather than "unknown role".
            if new_role == OWNER:
                raise AccessInvalid("the owner role cannot be assigned")
            self._require_assignable(actor_role, new_role)
            if new_role != current_role and not confirm:
                raise ConfirmationRequired(
                    f"changing {data.get('principal')} from {role_label(current_role)} to "
                    f"{role_label(new_role)} needs confirmation"
                )

        patch: dict[str, Any] = {"role": new_role}
        if set_expiry:
            patch["access_valid_until"] = normalise_date(access_valid_until)

        if set_expiry and patch["access_valid_until"] is not None:
            deadline = end_of_day_utc(patch["access_valid_until"])
            if deadline <= self.now():
                raise AccessInvalid(
                    f"access_valid_until {patch['access_valid_until']} is in the past"
                )

        updated = self._apply_grant(
            record,
            role=new_role,
            access_valid_until=patch.get("access_valid_until", data.get("access_valid_until")),
            actor=actor,
            source=f"PATCH /api/access/{access_id}",
            joined_immediately=bool(data.get("joined_immediately")),
            invitation_id=data.get("invitation_id"),
        )
        return {
            "access": self._member_view(room, updated, self.now()),
            "room_id": room_id,
        }

    def remove_access(self, access_id: str, *, actor: str, confirm: bool = False) -> dict[str, Any]:
        """Remove someone from *Who Has Access*.

        The grant is soft-deleted rather than destroyed so the audit trail and
        the invitation that produced it stay intact.
        """
        record = self.access_record(access_id)
        room_id = record["room_id"]
        room, _ = self._require_actor(room_id, actor)
        data = record["data"]

        if self.is_owner(room, data.get("principal")):
            raise AccessInvalid("the room owner cannot be changed or removed")
        if not confirm:
            raise ConfirmationRequired(
                f"removing {data.get('principal')} from this room needs confirmation"
            )

        self.store.delete(
            access_id, actor=actor, source=f"DELETE /api/access/{access_id}"
        )
        # An invitation still in flight would otherwise resurrect the access.
        for invitation in self.invitations(room_id):
            if (
                invitation["data"].get("state") == "pending"
                and (invitation["data"].get("email") or "").lower()
                == (data.get("principal") or "").lower()
            ):
                self.store.update(
                    invitation["id"],
                    {"state": "revoked", "revoked_at": _iso(self.now())},
                    actor=actor,
                    source=f"DELETE /api/access/{access_id}",
                )
        return {"removed": access_id, "room_id": room_id, "principal": data.get("principal")}

    # -- internals --------------------------------------------------------- #

    def _live_grant(self, room_id: str, principal: str) -> dict[str, Any] | None:
        """The live grant for one person in one room, if there is one."""
        needle = (principal or "").strip().lower()
        now = self.now()
        for record in self.grants(room_id):
            data = record["data"]
            if (data.get("principal") or "").strip().lower() != needle:
                continue
            if self._lapsed(data.get("access_valid_until"), now):
                continue
            return record
        return None

    def _apply_grant(
        self,
        record: Mapping[str, Any],
        *,
        role: str,
        access_valid_until: str | None,
        actor: str,
        source: str,
        joined_immediately: bool,
        invitation_id: str | None,
    ) -> dict[str, Any]:
        """Write the role and expiry onto an existing grant.

        The row always exists by the time this is called: either it was found by
        :meth:`_live_grant` on send, or the caller is editing it in
        *Who Has Access*. Creating a fresh grant is :meth:`accept`'s job, where
        the invitation is the record of where the access came from.
        """
        payload = {
            "role": role,
            "access_valid_until": access_valid_until,
            "granted_by": actor,
            "joined_immediately": joined_immediately,
        }
        if invitation_id:
            payload["invitation_id"] = invitation_id
        return self.store.update(record["id"], payload, actor=actor, source=source)


# --------------------------------------------------------------------------- #
# Copy
# --------------------------------------------------------------------------- #


def _banner(count: int) -> str | None:
    """The documented imminent-expiry banner, or nothing when nobody is lapsing."""
    if count <= 0:
        return None
    noun = "user" if count == 1 else "users"
    verb = "has" if count == 1 else "have"
    return f"{count} {noun} {verb} access expiring within {EXPIRING_SOON_DAYS} days."


def _invite_message(count: int, role: str, expires_on: str | None, joined: int) -> str:
    """The confirmation shown after Invite is pressed.

    ``count`` is how many invitations went out and ``joined`` how many of those
    recipients were already in the room and so joined on send.
    """
    role_text = role_label(role)
    expiry_text = f"until {expires_on} (UTC)" if expires_on else "with no expiration"
    message = f"{count} invitation{'' if count == 1 else 's'} sent as {role_text} {expiry_text}."
    if joined:
        message += f" {joined} already had access and joined immediately."
    message += f" Each invitation expires in {INVITATION_TTL_HOURS} hours."
    return message
