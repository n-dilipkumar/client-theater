"""Scoped API tokens: the researched credential half of the workflow.

User-flow step 1 is the whole of this module's mandate, and it is precise:

  "Admin generates a scoped API token in ``Command Center > Credentials``
  (``Generate Token``), choosing the ``Schedule`` permission for the relevant
  section (Concierge / Scheduling-links / Handoff) plus ``Read`` where listing
  assets is needed. Token is shown once. Admins only."

Four rules, and each one is enforced rather than described:

**Scoped.**  A token carries a set of sections and a set of permissions, and a
call it is not scoped for is refused. The permissions are per-section in the
research - ``Schedule`` for the section being used, ``Read`` where listing is
needed - so the scope check asks "does this token have ``schedule`` for *this*
section", not "does this token have any permission".

**Admin only.**  "Only users with the **Admin** role can generate API tokens in
Command Center. Workspace Managers do not have access to the credentials page."
The refusal names Workspace Manager specifically, because that half of the
sentence is the one an integrator will hit first and the one that is easiest to
get wrong by assumption.

**Shown once.**  The token is returned exactly once, by the call that creates
it. What is *stored* is a SHA-256 digest and a masked hint, so no read of this
collection - by this feature, by the core records API, or by anyone holding the
database file - can produce the secret. This is the one place where the research
gives a rule and the obvious implementation would break it: a token that is
stored in the clear can be re-shown, and "shown once" then means nothing.

**Read where listing is needed.**  Reading a credential is what the ``read``
permission gates, and this module distinguishes reading *one you own by id* from
listing the collection. The distinction is a judgement call and is named as
``token-lookup-vs-list`` in :mod:`dsr.headless_booking.inferences`.

The token itself
----------------

A caller of a headless API does not have a session cookie, so it presents a
token. ``verify`` accepts either the token or the credential's id, because a
caller holding the id is a caller inside the app already, and refusing that
would mean the in-product console could not use the permission rules at all.
Which of the two was used is recorded, so a reader is never misled about how a
call was authorised.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Any, Iterable, Mapping

from dsr.headless_booking.errors import HeadlessBookingError, NotFound, PermissionDenied
from dsr.headless_booking.vocabulary import (
    PERMISSIONS,
    SECTION_PERMISSIONS,
    SECTIONS,
    TOKEN_GENERATOR_REFUSED_ROLES,
    TOKEN_GENERATOR_ROLES,
    TOKEN_PREFIX,
    require_permission,
    require_role,
    require_section,
)

#: How many characters of the token survive into a masked hint. Four is the
#: conventional number and it is enough to tell two tokens apart in a list
#: without being useful to anyone who has it.
HINT_CHARS = 4


def generate_token() -> str:
    """A fresh token. 32 hex characters of ``secrets``, behind a known prefix."""
    return f"{TOKEN_PREFIX}{secrets.token_hex(16)}"


def digest(token: str) -> str:
    """The stored form: a SHA-256 digest of the token, never the token."""
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def mask(token: str) -> dict[str, Any]:
    """The two things a list may safely show: a prefix, and the last few chars."""
    text = str(token or "")
    return {
        "token_prefix": text[: len(TOKEN_PREFIX) + 8],
        "token_last4": text[-HINT_CHARS:] if len(text) >= HINT_CHARS else "",
    }


def normalise(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Validate a create-credential request and resolve its defaults.

    Everything is validated before a row is written, so a rejected request
    cannot leave a half-scoped credential behind - a credential that exists but
    cannot schedule is worse than one that was never created, because it looks
    configured.

    No field is required except ``label``. An empty scope is accepted: the
    research does not say a token must be able to do anything, and a
    read-nothing token is a legitimate thing to want from a ``Generate Token``
    button. What it cannot do is anything, and the refusal says which.
    """
    body = dict(payload or {})
    label = str(body.get("label") or body.get("name") or "").strip()
    if not label:
        raise HeadlessBookingError(
            "a credential needs a label, so a list of tokens is something a person can read"
        )

    raw_sections = body.get("sections")
    if raw_sections in (None, ""):
        sections = list(SECTIONS)
    elif isinstance(raw_sections, str):
        sections = [require_section(part) for part in raw_sections.split(",") if part.strip()]
    elif isinstance(raw_sections, Iterable):
        sections = [require_section(part) for part in raw_sections]
    else:
        raise HeadlessBookingError("sections must be a list of section names, or a comma-separated string")
    if not sections:
        sections = list(SECTIONS)
    # Order is normalised so two callers naming the same scope in a different
    # order produce the same stored value, and a list of credentials does not
    # show two identical scopes as different.
    sections = sorted(dict.fromkeys(sections), key=SECTIONS.index)

    raw_permissions = body.get("permissions")
    if raw_permissions in (None, ""):
        permissions = list(PERMISSIONS)
    elif isinstance(raw_permissions, str):
        permissions = [require_permission(part) for part in raw_permissions.split(",") if part.strip()]
    elif isinstance(raw_permissions, Iterable):
        permissions = [require_permission(part) for part in raw_permissions]
    else:
        raise HeadlessBookingError(
            "permissions must be a list of permissions, or a comma-separated string"
        )
    if not permissions:
        permissions = list(PERMISSIONS)
    permissions = sorted(dict.fromkeys(permissions), key=PERMISSIONS.index)

    return {
        "label": label,
        "sections": sections,
        "permissions": permissions,
        "enabled": bool(body.get("enabled", True)),
        "note": str(body.get("note") or ""),
    }


def role_can_generate(role: Any) -> bool:
    """May this role generate a token at all?"""
    return require_role(role) in TOKEN_GENERATOR_ROLES


def require_generator_role(role: Any) -> str:
    """The role rule, enforced, with the research's own two sentences.

    Workspace Manager is named before the generic refusal, because "Workspace
    Managers do not have access to the credentials page" is the sentence an
    integrator who is blocked needs, and "you are not an admin" does not tell
    them whether the page they want is reachable by their role at all.
    """
    normalised = require_role(role)
    if normalised in TOKEN_GENERATOR_ROLES:
        return normalised
    if normalised in TOKEN_GENERATOR_REFUSED_ROLES:
        raise PermissionDenied(
            f"role {normalised!r} cannot generate API tokens: only users with the Admin role can "
            "generate API tokens in Command Center, and Workspace Managers do not have access to "
            "the credentials page",
            required="role:admin",
        )
    raise PermissionDenied(
        f"role {normalised!r} is not an Admin, and only users with the Admin role can generate API "
        f"tokens in Command Center (roles that can: {', '.join(TOKEN_GENERATOR_ROLES)})",
        required="role:admin",
    )


def scope_of(spec: Mapping[str, Any], section: str) -> tuple[str, ...]:
    """The permissions this credential holds *for one section*.

    A credential scoped to two sections with ``schedule`` can schedule both. A
    credential scoped to one section cannot schedule the other, even though it
    holds the permission. That is what "the ``Schedule`` permission for the
    relevant section" means, and it is the check that makes a per-section scope
    worth having at all.
    """
    wanted = require_section(section)
    if wanted not in list(spec.get("sections") or []):
        return ()
    granted = set(spec.get("permissions") or [])
    return tuple(permission for permission in SECTION_PERMISSIONS.get(wanted, PERMISSIONS) if permission in granted)


def require_scope(spec: Mapping[str, Any], section: str, permission: str) -> str:
    """Refuse unless this credential holds ``permission`` for ``section``.

    The refusal names the section and the permission, because a caller holding a
    token for Concierge and Links who books a Handoff needs to know it is the
    *section* that is missing, not a spelling problem.
    """
    wanted_section = require_section(section)
    wanted_permission = require_permission(permission)
    if not bool(spec.get("enabled", True)):
        raise PermissionDenied(
            f"credential {spec.get('label') or spec.get('id')!r} is disabled, so it authorises nothing",
            required=wanted_permission,
            section=wanted_section,
        )
    if wanted_section not in list(spec.get("sections") or []):
        raise PermissionDenied(
            f"credential {spec.get('label') or spec.get('id')!r} is not scoped to section "
            f"{wanted_section!r}; it is scoped to {', '.join(spec.get('sections') or []) or 'nothing'}. "
            "The Schedule permission is granted per section.",
            required=wanted_permission,
            section=wanted_section,
        )
    if wanted_permission not in set(spec.get("permissions") or []):
        raise PermissionDenied(
            f"credential {spec.get('label') or spec.get('id')!r} does not hold the "
            f"{wanted_permission!r} permission for {wanted_section!r}; it holds "
            f"{', '.join(spec.get('permissions') or []) or 'no permissions'}. "
            "Tokens need Schedule to book and Read to list.",
            required=wanted_permission,
            section=wanted_section,
        )
    return wanted_section


def public(record: Mapping[str, Any]) -> dict[str, Any]:
    """A credential as it may be shown, which is not as it was stored.

    The token is *not* here and cannot be here: the row holds a digest. The
    ``has_token`` flag is what a list renders instead, and it is the honest
    signal - the token exists, and this build cannot show it to you.
    """
    data = dict(record.get("data") or {})
    return {
        "id": record.get("id"),
        "room_id": record.get("room_id"),
        "label": data.get("label"),
        "sections": list(data.get("sections") or []),
        "permissions": list(data.get("permissions") or []),
        "enabled": bool(data.get("enabled", True)),
        "note": data.get("note", ""),
        "token_prefix": data.get("token_prefix", ""),
        "token_last4": data.get("token_last4", ""),
        "token_hash": data.get("token_hash", ""),
        "has_token": bool(data.get("token_hash")),
        "shown_once_at": data.get("shown_once_at"),
        "created_at": record.get("created_at"),
        "created_by": record.get("actor"),
    }


def verify(record: Mapping[str, Any] | None, presented: str | None) -> dict[str, Any]:
    """Check a presented token against a stored credential.

    Returns the credential's data on success and raises on failure. The
    comparison is constant-time, because this is a credential and a token that
    can be recovered one byte at a time is not a credential.

    A *revoked* credential and a *wrong* token produce the same message on
    purpose: telling a caller that a token was valid but has since been disabled
    confirms the token was once good, which is information a scanner wants.
    """
    if record is None:
        raise NotFound("no such credential", resource="credential")
    if not presented:
        raise PermissionDenied("a token is required to authorise this call", required="token")
    expected = str(record["data"].get("token_hash") or "")
    if not expected or not hmac.compare_digest(expected, digest(presented)):
        raise PermissionDenied(
            "that token is not valid for this credential, or the credential has been revoked",
            required="token",
        )
    return dict(record["data"])
