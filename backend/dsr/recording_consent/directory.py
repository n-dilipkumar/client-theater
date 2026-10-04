"""The user directory, and the one key the consent profile resolves on.

Step 6 of the researched flow is "Admin assigns the profile to users; the profile
is set as default for new team members", and the data sources name "Gong user
directory + per-user settings (import emails, record-by-Gong flag, 'if the
invitation of this user to a web conference will prevent its recording')".

The key
-------

The research is explicit and this module is built on it: ``organizerEmail`` is
"The email address of the user creating the meeting, the Gong consent page link
will be used according to the settings of this user." So the profile is resolved
**by the organiser's email**, through this directory, and never by the room and
never by the booking. :data:`~dsr.recording_consent.vocabulary.PROFILE_RESOLUTION_KEY`
names the key so a test can assert it rather than trust a docstring.

Resolving on the room would look reasonable and would be wrong: a shared booking
page can be booked by any rep, and the consent rule that applies is the organiser's,
so a room-scoped lookup would apply one seller's consent settings to another
seller's meeting. That is a compliance defect, not a cosmetic one, so it is
refused here rather than left to a caller.

The third per-user flag
-----------------------

``blocks_recording`` is the researched third setting, quoted as "if the invitation
of this user to a web conference will prevent its recording". It is the one flag
that is a property of the *participant* rather than of the seller, and it changes
what the caller must do: the booking is issued, but the recording is called off
before the invite goes out. See :func:`recording_blocked_by_invitee`.
"""

from __future__ import annotations

from typing import Any

from dsr.recording_consent.errors import OrganizerUnmapped

#: The per-user settings the research names. Three flags and one assignment.
DIRECTORY_DEFAULTS: dict[str, Any] = {
    "import_emails": True,
    "record_by_gong": True,
    "blocks_recording": False,
    "profile_id": None,
    "is_default_for_new_members": False,
}


def normalise_email(email: Any) -> str:
    """Fold an email to the form the directory keys on.

    Case is folded and surrounding space is dropped. Nothing else is rewritten:
    the research treats the address as an opaque key into a vendor directory, so
    rewriting a local part or stripping a ``+tag`` would resolve to a different
    user, or to no user, and either outcome is a wrong consent decision.
    """
    return str(email or "").strip().lower()


def normalise_user(payload: Any) -> dict[str, Any]:
    """Validate one directory entry.

    A user without an email cannot be resolved by the key the research names, so
    the email is required and the failure is a message that says which field.
    """
    from dsr.recording_consent.errors import ProfileInvalid

    if not isinstance(payload, dict):
        raise ProfileInvalid("A directory user must be an object.", {"body": "must be an object"})

    errors: dict[str, str] = {}
    email = normalise_email(payload.get("email"))
    if not email:
        errors["email"] = "is required; the consent profile resolves on the organiser email"

    name = str(payload.get("name") or "").strip()
    if not name:
        errors["name"] = "is required"

    for flag, default in DIRECTORY_DEFAULTS.items():
        if flag in ("profile_id", "is_default_for_new_members"):
            continue
        value = payload.get(flag, default)
        if not isinstance(value, bool):
            if value in (0, 1):
                continue
            errors[flag] = "must be true or false"

    profile_id = payload.get("profile_id")
    if profile_id is not None and not isinstance(profile_id, str):
        errors["profile_id"] = "must be a consent profile id or null"

    if errors:
        raise ProfileInvalid(
            f"{len(errors)} field(s) are not acceptable: " + "; ".join(sorted(errors)), errors
        )

    result = dict(DIRECTORY_DEFAULTS)
    result.update(
        {
            "email": email,
            "name": name,
            "profile_id": profile_id or None,
            "import_emails": bool(payload.get("import_emails", True)),
            "record_by_gong": bool(payload.get("record_by_gong", True)),
            "blocks_recording": bool(payload.get("blocks_recording", False)),
            "is_default_for_new_members": bool(payload.get("is_default_for_new_members", False)),
        }
    )
    return result


def resolve(
    users: list[dict[str, Any]],
    organizer_email: Any,
    default_profile_id: str | None = None,
) -> dict[str, Any]:
    """Find the directory entry for an organiser and the profile it resolves to.

    Resolution order, and each step's reason:

    1. The email must match a user. No user means no profile, and the research
       documents that as a 404, so this raises
       :class:`~dsr.recording_consent.errors.OrganizerUnmapped` rather than
       quietly applying an organisation default to a stranger.
    2. A profile assigned to that user wins. This is step 6's "Admin assigns the
       profile to users".
    3. Otherwise the default-for-new-members profile, then the supplied
       organisation fallback. Step 6 names the first of those two; the second is
       this product's own answer to a user who has never been assigned a profile,
       and it is recorded on the result so a reviewer can see it was a fallback
       and not an assignment.

    Returns a dict with ``user``, ``profile_id`` and ``source``. ``source`` is
    one of ``assigned``, ``default_for_new_members`` or ``organisation_default``,
    because "which profile applied" is the first question anyone asks after an
    incident.
    """
    email = normalise_email(organizer_email)
    if not email:
        raise OrganizerUnmapped(
            "organizerEmail is required; the research resolves the consent profile by user.",
            {"organizer_email": "is required"},
        )

    for user in users:
        if normalise_email(user.get("email")) == email:
            if user.get("profile_id"):
                return {"user": user, "profile_id": user["profile_id"], "source": "assigned"}
            if user.get("is_default_for_new_members") and default_profile_id:
                return {
                    "user": user,
                    "profile_id": default_profile_id,
                    "source": "default_for_new_members",
                }
            if default_profile_id:
                return {
                    "user": user,
                    "profile_id": default_profile_id,
                    "source": "organisation_default",
                }
            return {"user": user, "profile_id": None, "source": "unprofiled"}

    raise OrganizerUnmapped(
        f"No user matches organizerEmail {email!r}. The research documents this as "
        f"{'404'}: {vocab_404()}.",
        {"organizer_email": "no directory user has this email"},
    )


def vocab_404() -> str:
    """The researched 404 text, quoted rather than paraphrased."""
    from dsr.recording_consent import vocabulary as vocab

    return vocab.DOCUMENTED_ERRORS[404]


def invitee_address(invitee: Any) -> str:
    """The address of one invitee, in whichever shape the caller supplied.

    The research calls them ``invitees[]`` and the request field carries objects,
    so a dict with an ``email`` key is the normal shape. A bare string is accepted
    too, because a caller holding a plain address list should not have to wrap it
    first.

    Both shapes are handled explicitly rather than by stringifying whatever
    arrives. ``str()`` on a dict produces ``{'email': 'guest@example', ...}``, which
    matches no directory entry, so a whole class of blocker would silently go
    unnoticed - and the failure looks like "no blockers", which is the safe-looking
    answer rather than the alarming one.
    """
    if isinstance(invitee, dict):
        return normalise_email(invitee.get("email"))
    return normalise_email(invitee)


def recording_blocked_by_invitee(users: list[dict[str, Any]], invitees: Any) -> list[str]:
    """Invitees whose own setting prevents the recording.

    The research quotes the setting as "if the invitation of this user to a web
    conference will prevent its recording". It is the one per-user flag that is a
    property of the participant rather than of the seller, so a booking may be
    valid and its recording still impossible.

    Returns the matching addresses rather than a boolean, because the caller has
    to be able to tell the organiser *which* invitee caused it, and a boolean
    would leave the only useful part of the answer in the log.
    """
    wanted = {
        address
        for address in (
            invitee_address(invitee)
            for invitee in (invitees if isinstance(invitees, list) else [invitees])
        )
        if address
    }
    return sorted(
        {
            normalise_email(user.get("email"))
            for user in users
            if user.get("blocks_recording") and normalise_email(user.get("email")) in wanted
        }
    )


def can_record(users: list[dict[str, Any]], organizer_email: Any) -> tuple[bool, str]:
    """Whether the organiser's own directory entry permits recording.

    The research names a per-user "record-by-Gong flag". A seller who is not
    recordable still books meetings and still gets consent pages; what they do
    not get is a recording, and saying so at booking time is better than issuing
    a link that promises a recording the product will not make.
    """
    email = normalise_email(organizer_email)
    for user in users:
        if normalise_email(user.get("email")) == email:
            if not user.get("record_by_gong", True):
                return False, "the organiser's directory entry has record_by_gong off"
            return True, ""
    raise OrganizerUnmapped(
        f"No user matches organizerEmail {email!r}.", {"organizer_email": "no directory user"}
    )
