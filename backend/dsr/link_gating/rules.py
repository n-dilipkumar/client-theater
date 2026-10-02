"""The gate settings on a buyer link, and what they mean for a viewer.

This is the researched specification for WF-069 made executable. Every constant
and every rule below comes from
``docs/research/digital-sales-room-workflows/wf/WF-069.md``; the docstring on each
rule names the evidence it came from, because the point of this workflow is that
the decisions were researched rather than chosen.

The three rules the rest of the product leans on
------------------------------------------------

**Authentication subsumes protection.** The research describes
``email_authenticated`` as "Viewer must verify their email with a one-time code
(stronger than ``--email-protected``)" and has the buyer walk the gate as email
entry, then a code, then the password. Stronger than, not alternative to. So
``email_authenticated: true`` implies ``email_protected: true``; asking for
authentication and not protection is not a contradictory request, it is the same
request stated with a redundant flag, and it is accepted and normalised rather
than refused.

**Expiry is a boundary, not a duration.** "After ``expires_at``, the URL returns a
friendly 'this link has expired' page." At the instant named, the link is still
open; strictly after it, it is closed. ``now > expires_at`` and not ``>=``. An
expiry that closed a link a microsecond early would silently shorten every
window a seller set, and the buyer would never see why.

**Revocation is expiry, not disappearance.** "Works immediately. Anyone with the
URL gets the expired page on their next request." So a revoked link answers with
the same friendly page an expired one does, and the document is not deleted. A
buyer holding a forwarded URL must not be able to tell the difference between
"this deal closed" and "you were cut off", and the seller keeps the row and its
history either way.

What this module does not decide
--------------------------------

An attempt cap on the code and a lifetime on the code. Both are absent from the
research, and both are the kind of rule a guessed value gets wrong in the
expensive direction: a cap low enough to stop a script also locks out a buyer
who fat-fingers a six-digit code, and a lifetime too short expires a code sitting
in an inbox that is slow to refresh. ``redact``-grade honesty about a missing
requirement beats a plausible number, so what is implemented is exactly what was
researched - the code is single-use, and that is inherent in the phrase "one-time
code". ``gate.py`` notes where a cap would go if the product ever wants one.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

# --------------------------------------------------------------------------- #
# Collections. Namespaced, because every feature shares one `records` table and
# `find()` matches on collection before it matches on anything else.
# --------------------------------------------------------------------------- #

LINK_COLLECTION = "wf069_gated_link"
PRESET_COLLECTION = "wf069_gate_preset"
VISITOR_COLLECTION = "wf069_gate_visitor"
CODE_COLLECTION = "wf069_gate_code"
DELIVERY_COLLECTION = "wf069_gate_delivery"
SESSION_COLLECTION = "wf069_gate_view_session"
VIEW_COLLECTION = "wf069_gate_view"
NOTIFICATION_COLLECTION = "wf069_gate_notification"

# --------------------------------------------------------------------------- #
# Defaults. All three are the documented ones, not house opinions.
# --------------------------------------------------------------------------- #

#: OpenAPI `CreateLinkRequest`: "email_protected (boolean, default true)".
DEFAULT_EMAIL_PROTECTED = True

#: OpenAPI `CreateLinkRequest`: "email_authenticated (boolean, default false)".
DEFAULT_EMAIL_AUTHENTICATED = False

#: Automations: "`enable_notification` defaults to on, so the team is notified on
#: each view of the link."
DEFAULT_ENABLE_NOTIFICATION = True

#: CLI flag table: "`--expires <iso>` | never | ISO 8601 datetime". No expiry set
#: means the link does not expire.
NEVER_EXPIRES = None

# --------------------------------------------------------------------------- #
# The gate steps, in the order the guide says a buyer walks them.
# --------------------------------------------------------------------------- #

#: Expired, or revoked. Both answer with the same friendly page.
STEP_EXPIRED = "expired"
#: The buyer must type their email address.
STEP_EMAIL = "email"
#: The buyer must prove they own the inbox with a one-time code.
STEP_CODE = "code"
#: The buyer must type the link password.
STEP_PASSWORD = "password"
#: Nothing is left to ask. The document is available.
STEP_OPEN = "open"

#: The order a buyer meets, used to render the progress indicator.
GATE_SEQUENCE = (STEP_EMAIL, STEP_CODE, STEP_PASSWORD)

# --------------------------------------------------------------------------- #
# Presets
# --------------------------------------------------------------------------- #

#: The documented preset-covered fields, quoted verbatim from the extensibility
#: note: "Covered fields: `password`, `expires_at`, `email_protected`,
#: `email_authenticated`, `allow_download`, `allow_list`, `deny_list`,
#: `enable_watermark`, `watermark_config`, `enable_screenshot_protection`,
#: `enable_confidential_view`, `enable_agreement`, `agreement_id`,
#: `welcome_message`, `enable_notification`, `show_banner`, and
#: `custom_fields`."
#:
#: This workflow *interprets* the first five: password, expiry, and the two
#: email flags plus notification. The rest belong to other workflows' domains -
#: `allow_list`/`deny_list` is WF-015's domain allowlist, watermarking and
#: screenshot protection are a confidentiality workflow's, `agreement_id` is
#: e--signature's - and interpreting them here would mean implementing rules
#: nobody researched. They are still accepted and carried on the link, because
#: the store is schema-flexible by design and a preset that seeds a governed
#: baseline is only useful if the baseline survives the trip. :func:`gated_fields`
#: is the boundary: those fields are stored and reported, never enforced here.
PRESET_COVERED_FIELDS = (
    "password",
    "expires_at",
    "email_protected",
    "email_authenticated",
    "allow_download",
    "allow_list",
    "deny_list",
    "enable_watermark",
    "watermark_config",
    "enable_screenshot_protection",
    "enable_confidential_view",
    "enable_agreement",
    "agreement_id",
    "welcome_message",
    "enable_notification",
    "show_banner",
    "custom_fields",
)

#: The subset of :data:`PRESET_COVERED_FIELDS` this workflow gives meaning to.
GATED_FIELDS = (
    "password",
    "expires_at",
    "email_protected",
    "email_authenticated",
    "enable_notification",
)

# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


#: The data key this workflow stores a room reference under.
#:
#: Not ``room_id``, and that is not a style preference. ``room_id`` is part of the
#: record *envelope*, so :func:`dsr.db.audited.AuditedDatabase._insert_record`
#: strips it out of ``data`` before the dynamic index is built. A link that stored
#: its room there would be unfilterable by ``find()`` - and a "filter links by
#: room" that silently returns nothing is the kind of defect that ships. The
#: envelope still carries ``room_id``; this is the payload-side twin of it, and
#: every response projects it back to ``room_id``.
ROOM_REF = "room_ref"


def room_ref_of(data: Mapping[str, Any], record: Mapping[str, Any] | None = None) -> Any:
    """The room a payload belongs to: the payload's own key, else the envelope's."""
    value = data.get(ROOM_REF)
    if value:
        return value
    return (record or {}).get("room_id")


class GateError(ValueError):
    """A gate setting this workflow will not accept.

    Carries a field-keyed map, because a seller filling in a form needs the
    message next to the input that caused it and not one combined sentence. The
    same shape :mod:`dsr.access` uses, and the HTTP layer renders it as ``errors``.
    """

    def __init__(self, message: str, errors: Mapping[str, str] | None = None) -> None:
        super().__init__(message)
        self.errors: dict[str, str] = dict(errors or {})


class GateDenied(PermissionError):
    """A buyer the gate will not let through.

    ``reason`` is a stable machine token rather than a sentence, so the frontend
    can choose between "that password did not match" and "this link has expired"
    without string-matching an English message. The message itself is safe to
    show a buyer: it is written to say what happened without confirming which
    half of a credential was wrong.
    """

    #: Stable tokens, and what a buyer is told for each.
    REASON_PASSWORD = "password_rejected"
    REASON_CODE = "code_rejected"
    REASON_EMAIL_REQUIRED = "email_required"
    REASON_EMAIL_UNVERIFIED = "email_not_verified"
    REASON_CODE_REQUIRED = "code_required"
    REASON_SESSION = "view_token_rejected"
    #: The two ways a link stops working. Their wording is identical on purpose:
    #: "Anyone with the URL gets the expired page on their next request", so a
    #: buyer must not be able to read "you were cut off" off the message.
    REASON_EXPIRED = "link_expired"
    REASON_REVOKED = "link_revoked"
    #: The step asked for is not a step this link has.
    REASON_NOT_REQUIRED = "step_not_required"
    REASON_OUT_OF_ORDER = "step_out_of_order"

    _CLOSED = "This link has expired. Ask the sender for a new one."

    MESSAGES = {
        REASON_PASSWORD: "That password did not match.",
        REASON_CODE: "That code did not match. Check the email we sent and try again.",
        REASON_EMAIL_REQUIRED: "Enter the email address you were sent this link with.",
        REASON_EMAIL_UNVERIFIED: "Confirm the code we emailed you before continuing.",
        REASON_CODE_REQUIRED: "Confirm the code we emailed you first.",
        REASON_SESSION: "Open the link again to start a new pass through the gate.",
        REASON_EXPIRED: _CLOSED,
        REASON_REVOKED: _CLOSED,
        REASON_NOT_REQUIRED: "This link does not ask for that.",
        REASON_OUT_OF_ORDER: "Finish the earlier step before this one.",
    }

    def __init__(self, reason: str) -> None:
        super().__init__(self.MESSAGES.get(reason, "That link could not be opened."))
        self.reason = reason


class LinkNotFound(LookupError):
    """No such link, or it was never a link this workflow owns.

    Its own type rather than the store's ``RecordNotFound``, because a feature may
    only map error types it raises itself: registering a handler for a shared type
    would intercept that exception across the whole product.
    """


# --------------------------------------------------------------------------- #
# Email and expiry parsing
# --------------------------------------------------------------------------- #

_EMAIL_RE = re.compile(r"^[^@\s]+@([^@\s]+\.[^@\s]+)$")

#: ``YYYY-MM-DD`` with nothing after it: the shape a seller types into a date box.
_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def is_valid_email(email: Any) -> bool:
    return isinstance(email, str) and bool(_EMAIL_RE.match(email.strip()))


def normalize_email(email: Any) -> str:
    """Lowercase and trim an address, or refuse it.

    Case-folding the domain is required for correctness - ``@Example.com`` and
    ``@example.com`` are the same inbox - and folding the local part is a
    deliberate extra. It makes ``Buyer@`` and ``buyer@`` one visitor rather than
    two, which is what stops a buyer who has verified one address from appearing
    unverified under a capitalisation of it. The address is only ever compared
    and displayed; it is never used to send anything without the code step
    proving the inbox first.
    """
    if not isinstance(email, str):
        raise GateError("email is required", {"email": "Enter your email address."})
    cleaned = email.strip().lower()
    if not is_valid_email(cleaned):
        raise GateError("email is not a usable address", {"email": "Enter a valid email address."})
    return cleaned


def parse_moment(value: Any) -> datetime | None:
    """Parse an ``expires_at`` into an aware UTC datetime, or ``None`` for never.

    Accepts what the CLI accepts ("ISO 8601 datetime"), plus a bare date because
    a seller typing 31 December means the end of that day rather than its start,
    which is the reading that does not silently shorten the window by 24 hours.

    A timestamp with no offset is read as UTC rather than as local time. The
    research says "ISO 8601 datetime" and says nothing about zones, and a
    deployment whose host clock is not UTC would otherwise store an expiry that
    moves when the host's zone changes.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, date):
        moment = datetime.combine(value, time.max)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith(("Z", "z")):
            text = f"{text[:-1]}+00:00"
        try:
            moment = datetime.fromisoformat(text)
        except ValueError as exc:
            raise GateError(
                "expires_at must be an ISO date or timestamp",
                {"expires_at": "Use an ISO 8601 date or timestamp, or null for no expiry."},
            ) from exc
        if _DATE_ONLY_RE.match(text):
            # A bare date means the end of that day. ``fromisoformat`` hands back
            # midnight, and a seller typing 31 December would get a window 24 hours
            # shorter than they asked for with nothing to say so.
            moment = moment.replace(hour=23, minute=59, second=59, microsecond=999_999)
    else:
        raise GateError(
            "expires_at must be an ISO date or timestamp",
            {"expires_at": "Use an ISO 8601 date or timestamp, or null for no expiry."},
        )
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def expiry_state(expires_at: Any, now: datetime) -> tuple[bool, str | None, datetime | None]:
    """``(expired, reason, moment)`` for a link's stored expiry.

    The comparison is ``now > moment``: at the instant named the link is still
    open. See the module docstring.
    """
    try:
        moment = parse_moment(expires_at)
    except GateError:
        # A stored value that no longer parses is treated as expired rather than
        # as valid. Failing open on an unreadable expiry would mean a corrupted
        # row silently opens a gated link; failing closed means it shows the
        # friendly page and the seller sees why on the board.
        return True, "expires_at_unreadable", None
    if moment is None:
        return False, None, None
    if now > moment:
        return True, "expired", moment
    return False, None, moment


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #

_BOOL_FIELDS = ("email_protected", "email_authenticated", "enable_notification")


def _as_bool(value: Any, field: str) -> bool:
    """Accept a boolean, or the ``on``/``off`` the CLI documents.

    features_tools: "CLI `links update` with tri-state `on|off` booleans". The
    third state is *absent*, and :func:`apply_changes` is where that is honoured;
    this function only has to turn the two present ones into a bool.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("on", "true", "yes", "1"):
            return True
        if lowered in ("off", "false", "no", "0"):
            return False
    if value in (0, 1):
        return bool(value)
    raise GateError(f"{field} must be a boolean", {field: "Use true or false."})


def _as_password(value: Any) -> str | None:
    """A password, or ``None`` for no password.

    OpenAPI: ``password`` is "nullable string, minLength 1". So ``null`` clears
    the password and the empty string is *not* a way to clear it - it is the one
    value that could never have been a password, and treating it as "off" would
    let a form that submits an empty box silently ungate a link.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise GateError("password must be a string", {"password": "Enter the link password."})
    if len(value) < 1:
        raise GateError(
            "password is empty",
            {"password": "Enter the link password, or leave the field out to remove it."},
        )
    return value


def normalize_settings(
    payload: Mapping[str, Any],
    *,
    base: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate and normalise the gate fields of a create or an update.

    ``base`` is the current settings on an update; a field the payload does not
    mention is carried over rather than defaulted, which is what makes the
    tri-state update work. Returns the settings with the password replaced by
    ``password_hash`` and never by a cleartext.
    """
    settings: dict[str, Any] = dict(base or {})
    errors: dict[str, str] = {}

    for field in _BOOL_FIELDS:
        if field not in payload:
            if field not in settings:
                settings[field] = (
                    DEFAULT_ENABLE_NOTIFICATION
                    if field == "enable_notification"
                    else DEFAULT_EMAIL_PROTECTED
                    if field == "email_protected"
                    else DEFAULT_EMAIL_AUTHENTICATED
                )
            continue
        try:
            settings[field] = _as_bool(payload[field], field)
        except GateError as exc:
            errors.update(exc.errors)

    if "expires_at" in payload:
        try:
            moment = parse_moment(payload["expires_at"])
        except GateError as exc:
            errors.update(exc.errors)
        else:
            settings["expires_at"] = moment.isoformat() if moment else None

    if "password" in payload:
        try:
            secret = _as_password(payload["password"])
        except GateError as exc:
            errors.update(exc.errors)
        else:
            if secret is None:
                # Written as an explicit null rather than dropped. ``update`` is a
                # shallow *merge* patch, so a key that is merely absent keeps
                # whatever was there - and "remove the password" that quietly
                # leaves the old hash in place is the worst possible failure for
                # this field.
                settings["password_hash"] = None
            else:
                # Imported lazily so this module keeps no crypto import of its own
                # and the hashing work factor lives in exactly one place.
                from dsr.link_gating import secrets as link_secrets

                settings["password_hash"] = link_secrets.hash_password(secret)
        settings["password_set"] = settings.get("password_hash") is not None

    if "email_authenticated" in settings and settings["email_authenticated"]:
        # Stronger than protected, not alternative to it. See the module docstring.
        # One-directional on purpose: turning authentication off must not also
        # turn protection off, because a rep who wanted proof of identity may
        # well still want to know who is reading.
        settings["email_protected"] = True

    settings.setdefault("password_set", settings.get("password_hash") is not None)

    if errors:
        raise GateError("the gate settings could not be read", errors)
    return settings


def email_required(settings: Mapping[str, Any]) -> bool:
    """Does this link ask the buyer for an email address at all?"""
    return bool(settings.get("email_protected")) or bool(settings.get("email_authenticated"))


def code_required(settings: Mapping[str, Any]) -> bool:
    """Does this link ask the buyer to prove they own the inbox?"""
    return bool(settings.get("email_authenticated"))


def password_required(settings: Mapping[str, Any]) -> bool:
    return bool(settings.get("password_hash"))


def steps_required(settings: Mapping[str, Any]) -> list[str]:
    """The gate steps this link actually asks for, in order."""
    required: list[str] = []
    if email_required(settings):
        required.append(STEP_EMAIL)
    if code_required(settings):
        required.append(STEP_CODE)
    if password_required(settings):
        required.append(STEP_PASSWORD)
    return required


def gate_step(
    settings: Mapping[str, Any], *, now: datetime, expired: bool, revoked: bool
) -> dict[str, Any]:
    """What the buyer is asked for next, and why.

    ``revoked`` and ``expired`` both land on :data:`STEP_EXPIRED` with the same
    wording a buyer sees, because the research requires a revoked link to be
    indistinguishable from an expired one from the outside.
    """
    required = steps_required(settings)
    if revoked:
        return {
            "step": STEP_EXPIRED,
            "reason": "revoked",
            # The same sentence an expired link gets, by name rather than by a second
            # literal that could drift from it.
            "message": GateDenied.MESSAGES[GateDenied.REASON_EXPIRED],
            "required": required,
        }
    if expired:
        return {
            "step": STEP_EXPIRED,
            "reason": "expired",
            "message": GateDenied.MESSAGES[GateDenied.REASON_EXPIRED],
            "required": required,
        }
    if not required:
        return {
            "step": STEP_OPEN,
            "reason": None,
            "message": "This link is open.",
            "required": [],
        }
    # Only what the callers read. The full settings projection is `gated_fields`, and
    # duplicating the two booleans here under a confusing name was how a reader would
    # come to trust this branch instead of that one.
    return {
        "step": required[0],
        "reason": None,
        "message": None,
        "required": required,
    }


# --------------------------------------------------------------------------- #
# Presets
# --------------------------------------------------------------------------- #


def resolve_preset(
    preset: Mapping[str, Any] | None,
    payload: Mapping[str, Any],
) -> tuple[dict[str, Any], list[str], list[str]]:
    """``(defaults, from_preset, overridden)`` for a create.

    "Every preset-controlled field becomes the default; any field you also pass
    explicitly overrides the preset."

    The subtlety is ``expires_at: null``. OpenAPI: "Pass null to override a
    preset's expiry with none." So absence and an explicit null are different
    requests and have to stay different, which is why this takes the raw payload
    and keys off *membership* rather than off truthiness. A caller that sent
    ``expires_at: null`` against a preset with an expiry is asking for a link
    that never expires, and dropping the key would hand them the opposite.
    """
    defaults: dict[str, Any] = {}
    from_preset: list[str] = []
    overridden: list[str] = []

    carried = dict(preset or {}).get("fields") or {}
    for field in PRESET_COVERED_FIELDS:
        if field == "password":
            # Handled on its own because a preset stores the password *hashed*, under
            # ``password_hash``, so looking for a ``password`` key here would find
            # nothing and the preset would silently seed a link with no password at
            # all. Two records holding one cleartext password is one more place to
            # leak it from and no additional capability, so the hash travels instead.
            preset_hash = carried.get("password_hash")
            if not preset_hash:
                # Nothing to override either: "overridden" means the preset supplied
                # something the caller replaced, and a preset with no password
                # supplied nothing.
                continue
            if "password" in payload:
                overridden.append(field)
                continue
            defaults["password_hash"] = preset_hash
            defaults["password_set"] = True
            from_preset.append(field)
            continue

        if field not in carried:
            continue
        if field in payload:
            overridden.append(field)
            continue
        defaults[field] = carried[field]
        from_preset.append(field)

    return defaults, from_preset, overridden


def validate_preset(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a governed baseline.

    Only the fields this workflow understands are validated; the rest of
    :data:`PRESET_COVERED_FIELDS` is carried through untouched, because a preset
    that had to be edited to add another workflow's field would defeat the point
    of a governed baseline.
    """
    from dsr.link_gating import secrets as link_secrets

    if not isinstance(payload, Mapping):
        raise GateError("a preset is an object", {"body": "Send a JSON object."})

    name = str(payload.get("name") or "").strip()
    if not name:
        raise GateError(
            "a preset needs a name", {"name": "Name the baseline, e.g. Enterprise deal."}
        )

    fields = dict(payload.get("fields") or {})
    unknown = [f for f in fields if f not in PRESET_COVERED_FIELDS]
    if unknown:
        raise GateError(
            "a preset may only carry preset-controlled fields",
            {
                "fields": (
                    f"Not preset-controlled: {', '.join(sorted(unknown))}. "
                    f"Covered: {', '.join(PRESET_COVERED_FIELDS)}."
                )
            },
        )

    normalised = normalize_settings({f: v for f, v in fields.items() if f in GATED_FIELDS})
    # Start from what this workflow does *not* interpret, so a governed baseline
    # survives the trip intact: `allow_list` is WF-015's rule and watermarking is
    # nobody's yet, and a preset that silently dropped them would not be a baseline.
    stored: dict[str, Any] = {
        field: value for field, value in fields.items() if field not in GATED_FIELDS
    }
    for field, value in normalised.items():
        if field not in ("password_hash", "password_set"):
            stored[field] = value
    if "password" in fields:
        # Hashed once here, copied by hash to every link the preset seeds.
        stored["password_hash"] = link_secrets.hash_password(str(fields["password"]))
        stored["password_set"] = True
    elif normalised.get("password_hash") is None and "password" not in fields:
        stored.pop("password_hash", None)
        stored.pop("password_set", None)
    return {"name": name, "fields": stored}


def gated_fields(settings: Mapping[str, Any]) -> dict[str, Any]:
    """The gate settings as a safe-to-display projection.

    Booleans and the expiry, never the password hash. Used by every response this
    workflow returns.
    """
    return {
        "email_protected": bool(settings.get("email_protected")),
        "email_authenticated": bool(settings.get("email_authenticated")),
        "enable_notification": bool(
            settings.get("enable_notification", DEFAULT_ENABLE_NOTIFICATION)
        ),
        "expires_at": settings.get("expires_at"),
        "password_set": bool(settings.get("password_hash")),
        "email_required": email_required(settings),
        "code_required": code_required(settings),
        "steps": steps_required(settings),
    }


def remaining_window(expires_at: Any, now: datetime) -> timedelta | None:
    """How long the link has left, or ``None`` when it never expires."""
    try:
        moment = parse_moment(expires_at)
    except GateError:
        return None
    if moment is None:
        return None
    return moment - now
