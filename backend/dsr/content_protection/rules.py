"""The pure rules of WF-131: the policy matrix, the IP allow-list, the evaluation.

Nothing here reads or writes. Every function is a pure transformation of its
arguments, so the whole matrix can be exercised against a literal example rather
than through a database and without HTTP. The reads and the writes are in
:mod:`dsr.content_protection.engine`.

The one rule that is a bundle's whole job
-----------------------------------------

A buyer link already has four protections, each owned elsewhere. What no
workflow has is a way to ask **one** question - "may this recipient see this
link, right now, from here" - and get one answer that names the protection that
refused. :func:`evaluate` is that question, and it answers it by *composing* the
other workflows' rules, never by restating them:

============================  ==================================================
Protection                    Rule it delegates to
============================  ==================================================
expiry                        WF-069 ``link_gating.rules.expiry_state``
NDA acceptance                WF-070 ``link_gating.agreement.acceptance_covers``
download AND                  WF-072 ``view_only_access.rules.decide_download``
watermark                     WF-071 ``viewer_watermark`` (reported, never denies)
plan gate (on authoring)      WF-071 ``viewer_watermark.rules.require_plan``
============================  ==================================================

Re-declaring any of those would give the product two answers to one question,
which is the drift the bundle's reuse rule exists to prevent.

The rules that **are** this build's, because no workflow owns them
-----------------------------------------------------------------

* the **binding** of those protections to one recipient (:func:`normalize_policy`);
* the **IP allow-list** (:func:`normalize_allowlist`, :func:`ip_allowed`) - the
  spec names "allow-list IPs" as a seller setting and gives no vendor field, so
  this is the one control the workflow declares itself;
* the **bulk-download alert** (:func:`bulk_download_alert`) - "auto-alert on a
  bulk-download pattern", explicitly an alert and not WF-072's block;
* the **action code** a logged action carries (:func:`action_code_checked`).

Both closure reasons share WF-069's wording on purpose: "Anyone with the URL gets
the expired page on their next request", so a buyer must not be able to read "you
were cut off" off the message. A per-recipient revoke answers exactly like an
expiry, from the very next request, because that is what "dies immediately" means
when there is no edge to invalidate a token at.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from dsr.link_gating import rules as gate_rules
from dsr.link_gating.agreement import acceptance_covers
from dsr.view_only_access import rules as download_rules, vocabulary as download_vocab

from . import vocabulary as vocab
from .errors import ActionRefused, ContentProtectionError, ProtectionDenied

# --------------------------------------------------------------------------- #
# Recipients
# --------------------------------------------------------------------------- #


def normalize_recipient(value: Any) -> str:
    """A recipient address, through WF-069's own rule.

    Borrowed rather than re-implemented so that ``Buyer@Northwind.example`` and
    ``buyer@northwind.example`` are one recipient here and one visitor in the
    gate this bundles. A local regex would be a second spelling of the same idea,
    and the two would eventually disagree.
    """
    try:
        return gate_rules.normalize_email(value)
    except gate_rules.GateError as exc:
        raise ContentProtectionError(
            "recipient is not a usable address",
            {vocab.RECIPIENT: exc.errors.get("email", "Enter a valid email address.")},
        ) from exc


# --------------------------------------------------------------------------- #
# IP allow-list
# --------------------------------------------------------------------------- #


def normalize_allowlist(value: Any) -> list[str]:
    """Canonicalise an allow-list to a list of networks.

    Accepts a JSON list, or a string separated by commas or newlines because that
    is what a seller pastes out of a ticket. Each entry is an exact address or a
    CIDR block: ``203.0.113.9`` becomes ``203.0.113.9/32`` and ``10.0.0.0/8``
    stays itself. An entry that is not an address or a network is *refused at
    authoring time* rather than stored, because an allow-list a typo turns into a
    deny-everything is a link that stops working for the wrong reason, and one a
    typo turns into an allow-everything is a control that does nothing.

    An empty list is not an error: it means "open", which is the documented
    default (no allow-list set).
    """
    if value is None or value == "":
        return []
    if isinstance(value, str):
        raw: Iterable[Any] = [part for part in value.replace("\n", ",").split(",")]
    elif isinstance(value, Sequence):
        raw = value
    else:
        raise ContentProtectionError(
            "ip_allowlist must be a list of addresses or a comma-separated string",
            {vocab.FIELD_IP_ALLOWLIST: "Give addresses separated by commas, or a list."},
        )

    networks: list[str] = []
    for entry in raw:
        if isinstance(entry, str) and not entry.strip():
            continue
        text = str(entry).strip()
        try:
            # ``strict=False`` so ``10.0.0.1/24`` reads as its network rather than
            # being refused for having host bits set - a seller typing it means
            # the block that contains the address.
            network = ipaddress.ip_network(text, strict=False)
        except ValueError as exc:
            raise ContentProtectionError(
                f"{text!r} is not an IP address or CIDR block",
                {vocab.FIELD_IP_ALLOWLIST: f"{text!r} is not an IP address or CIDR block."},
            ) from exc
        canonical = str(network)
        if canonical not in networks:
            networks.append(canonical)
    return networks


def ip_allowed(ip_address: Any, allowlist: Any) -> bool:
    """May a request from ``ip_address`` open the link?

    An empty allow-list means open, which is the documented default (no allow-list
    set). A non-empty one fails closed on a missing or unparsable address: a
    request that cannot be shown to come from an allowed network is refused, which
    is the direction a control has to fail in. An IPv4 address checked against an
    IPv6 block (or the reverse) is refused rather than raising.
    """
    networks = normalize_allowlist(allowlist) if allowlist else []
    if not networks:
        return True
    text = str(ip_address or "").strip()
    if not text:
        return False
    try:
        candidate = ipaddress.ip_address(text)
    except ValueError:
        return False
    for entry in networks:
        try:
            network = ipaddress.ip_network(entry, strict=False)
        except ValueError:  # a stored entry that no longer parses: fail closed
            continue
        if candidate.version != network.version:
            continue
        if candidate in network:
            return True
    return False


# --------------------------------------------------------------------------- #
# Coercion
# --------------------------------------------------------------------------- #


def _as_bool(value: Any, field: str) -> bool:
    """A JSON boolean, or the ``on``/``off`` the CLI documents.

    Written here rather than borrowed because the refusal has to be this
    workflow's error type: re-raising another workflow's ``PolicyRefused`` from
    this module would be caught by that workflow's handler and reported as its
    failure, which would be a lie about which workflow refused.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        token = value.strip().lower()
        if token in ("on", "true", "yes", "1"):
            return True
        if token in ("off", "false", "no", "0", ""):
            return False
    raise ContentProtectionError(f"{field} must be a boolean", {field: "Use true or false."})


# --------------------------------------------------------------------------- #
# The policy matrix
# --------------------------------------------------------------------------- #

_BOOL_FIELDS = (
    vocab.FIELD_EMAIL_PROTECTED,
    vocab.FIELD_EMAIL_AUTHENTICATED,
    vocab.REQUIRE_PASSCODE,
    vocab.FIELD_ENABLE_AGREEMENT,
    vocab.FIELD_ENABLE_WATERMARK,
    vocab.FIELD_ALLOW_DOWNLOAD,
)


def normalize_policy(
    payload: Mapping[str, Any],
    *,
    base: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate and normalise a per-recipient policy for a create or an update.

    ``base`` is the current policy on an update, so a field the payload does not
    mention is carried over rather than defaulted. That is what makes the update
    tri-state: ``{}`` changes nothing, ``{"allow_download": true}`` turns one
    switch on, and ``{"agreement_id": null}`` clears the selection without
    touching the flag.

    Every boolean defaults to ``False`` - the safe direction. A policy written
    without a switch asks for no extra protection beyond the link's own gates,
    which is exactly what a per-recipient row means before a seller edits it.
    """
    if not isinstance(payload, Mapping):
        raise ContentProtectionError("a policy is an object", {"body": "Send a JSON object."})

    body = dict(payload)
    current = dict(base or {})
    errors: dict[str, str] = {}
    settings: dict[str, Any] = {}

    for field in _BOOL_FIELDS:
        if field in body:
            try:
                settings[field] = _as_bool(body[field], field)
            except ContentProtectionError as exc:
                errors.update(exc.errors)
        else:
            settings[field] = bool(current.get(field, False))

    if vocab.FIELD_AGREEMENT_ID in body:
        given = body[vocab.FIELD_AGREEMENT_ID]
        if given is None:
            settings[vocab.FIELD_AGREEMENT_ID] = None
        elif isinstance(given, str) and given.strip():
            settings[vocab.FIELD_AGREEMENT_ID] = given.strip()
        else:
            # An empty string is not "no agreement"; it is the one value that could
            # never have been an id, and reading it as clear would let a form that
            # submits an empty box silently ungate a link.
            errors[vocab.FIELD_AGREEMENT_ID] = "Choose an agreement, or send null to clear it."
    else:
        settings[vocab.FIELD_AGREEMENT_ID] = current.get(vocab.FIELD_AGREEMENT_ID)

    if settings[vocab.FIELD_ENABLE_AGREEMENT] and not settings[vocab.FIELD_AGREEMENT_ID]:
        # WF-070's own rule, honoured per recipient: a gate on with nothing to
        # accept is a link that looks gated and releases everything.
        errors[vocab.FIELD_AGREEMENT_ID] = "An NDA gate needs an agreement to accept."

    if vocab.FIELD_EXPIRES_AT in body:
        try:
            moment = gate_rules.parse_moment(body[vocab.FIELD_EXPIRES_AT])
        except gate_rules.GateError as exc:
            errors.update({vocab.FIELD_EXPIRES_AT: exc.errors.get("expires_at", str(exc))})
        else:
            settings[vocab.FIELD_EXPIRES_AT] = moment.isoformat() if moment else None
    else:
        settings[vocab.FIELD_EXPIRES_AT] = current.get(vocab.FIELD_EXPIRES_AT)

    if vocab.FIELD_IP_ALLOWLIST in body:
        try:
            settings[vocab.FIELD_IP_ALLOWLIST] = normalize_allowlist(body[vocab.FIELD_IP_ALLOWLIST])
        except ContentProtectionError as exc:
            errors.update(exc.errors)
    else:
        settings[vocab.FIELD_IP_ALLOWLIST] = list(current.get(vocab.FIELD_IP_ALLOWLIST) or [])

    if errors:
        raise ContentProtectionError("the per-recipient policy could not be read", errors)
    return settings


# --------------------------------------------------------------------------- #
# Effective settings: the link's baseline, tightened per recipient.
# --------------------------------------------------------------------------- #


def _readable_moment(value: Any) -> tuple[datetime | None, bool]:
    """``(moment, unreadable)`` for a stored expiry.

    An unreadable value is reported rather than raised, so :func:`evaluate` can
    fail closed on it exactly as WF-069's ``expiry_state`` fails closed on a
    corrupted ``expires_at``.
    """
    try:
        return gate_rules.parse_moment(value), False
    except gate_rules.GateError:
        return None, True


def effective_settings(link: Mapping[str, Any] | None, policy: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve the matrix: the link's gates, tightened by the recipient's row.

    A per-recipient policy may only **add** protection, never remove it. A
    recipient row that omitted the NDA gate cannot open a link the seller gated
    for everyone; it can only require more. That direction is the point of a
    per-recipient matrix on top of a link-wide gate, and it is why the O-R of the
    booleans below is ``or`` in every case but download (where WF-072's documented
    default is view-only, so the policy's own switch is the answer).
    """
    link = dict(link or {})
    policy = dict(policy or {})

    link_email = bool(
        link.get(vocab.FIELD_EMAIL_PROTECTED) or link.get(vocab.FIELD_EMAIL_AUTHENTICATED)
    )
    policy_email = bool(
        policy.get(vocab.FIELD_EMAIL_PROTECTED) or policy.get(vocab.FIELD_EMAIL_AUTHENTICATED)
    )
    link_code = bool(link.get(vocab.FIELD_EMAIL_AUTHENTICATED))
    policy_code = bool(policy.get(vocab.FIELD_EMAIL_AUTHENTICATED))

    link_nda = bool(link.get(vocab.FIELD_ENABLE_AGREEMENT))
    policy_nda = bool(policy.get(vocab.FIELD_ENABLE_AGREEMENT))

    # The passcode is WF-069's, stored once on the link. This workflow only binds
    # the *requirement* to a recipient, so a policy that asks for it on a link
    # with no password asks for something that cannot be satisfied.
    link_passcode = bool(link.get("password_hash")) or bool(link.get("password_set"))
    policy_passcode = bool(policy.get(vocab.REQUIRE_PASSCODE))

    link_moment, link_bad = _readable_moment(link.get(vocab.FIELD_EXPIRES_AT))
    policy_moment, policy_bad = _readable_moment(policy.get(vocab.FIELD_EXPIRES_AT))
    moments = [m for m in (link_moment, policy_moment) if m is not None]
    # At the instant named the link is still open; strictly after it is closed.
    # WF-069's boundary rule, applied to the earlier of the two expiries.
    expires_at = min(moments).isoformat() if moments else None

    return {
        "email_required": link_email or policy_email,
        "code_required": link_code or policy_code,
        "passcode_required": link_passcode and policy_passcode,
        "passcode_available": link_passcode,
        "agreement_required": link_nda or policy_nda,
        "agreement_id": policy.get(vocab.FIELD_AGREEMENT_ID) or link.get("agreement_id"),
        "expires_at": expires_at,
        "expiry_unreadable": link_bad or policy_bad,
        "revoked": bool(policy.get("revoked")) or link.get("deleted_at") is not None,
        "ip_allowlist": list(policy.get(vocab.FIELD_IP_ALLOWLIST) or []),
        "allow_download": bool(policy.get(vocab.FIELD_ALLOW_DOWNLOAD)),
        "watermark": bool(policy.get(vocab.FIELD_ENABLE_WATERMARK)),
    }


# --------------------------------------------------------------------------- #
# The evaluation
# --------------------------------------------------------------------------- #


def _protection(allowed: bool, *, required: bool, detail: str) -> dict[str, Any]:
    return {"allowed": allowed, "required": required, "detail": detail}


def evaluate(
    link: Mapping[str, Any] | None,
    policy: Mapping[str, Any],
    context: Mapping[str, Any] | None = None,
    *,
    now: datetime,
) -> dict[str, Any]:
    """May this recipient see this link, right now, from here?

    Returns one decision that names every protection and the ones that refused,
    rather than the first refusal, because a bundle's whole value is answering a
    compliance question ("what withheld this") in one call. ``watermark`` is
    reported and never withholds: WF-071's own Why says a watermark is "a
    deterrent and a trace, not an access control".

    ``context`` carries what the caller already proved about the viewer - the
    verified email, whether the passcode matched, whether the NDA was accepted,
    the client address, and the room/item download controls. This function does
    not verify a passcode or an acceptance itself; it delegates. See
    :func:`acceptance_covers` for the NDA and WF-072's ``decide_download`` for
    the file.
    """
    context = dict(context or {})
    settings = effective_settings(link, policy)

    protections: dict[str, dict[str, Any]] = {}
    denied_by: list[str] = []
    reasons: list[str] = []

    def deny(name: str, reason: str, *, required: bool, detail: str) -> None:
        protections[name] = _protection(False, required=required, detail=detail)
        denied_by.append(name)
        reasons.append(reason)

    # -- revocation, then expiry. Both answer with the same wording. ------- #
    if settings["revoked"]:
        deny(
            vocab.PROTECTION_REVOCATION,
            ProtectionDenied.REASON_REVOKED,
            required=True,
            detail="The policy was revoked, so the link is closed from this request on.",
        )
    else:
        protections[vocab.PROTECTION_REVOCATION] = _protection(
            True, required=False, detail="The link is not revoked."
        )

    expired = settings["expiry_unreadable"]
    if not expired and settings["expires_at"] is not None:
        moment, _ = _readable_moment(settings["expires_at"])
        if moment is not None and now > moment:
            expired = True
    if expired:
        reason = (
            ProtectionDenied.REASON_CLOSED
            if settings["revoked"]
            else ProtectionDenied.REASON_EXPIRED
        )
        deny(
            vocab.PROTECTION_EXPIRY,
            reason,
            required=True,
            detail=(
                "This policy's expiry has passed."
                if not settings["expiry_unreadable"]
                else "The stored expiry could not be read, so the link fails closed."
            ),
        )
    else:
        protections[vocab.PROTECTION_EXPIRY] = _protection(
            True, required=settings["expires_at"] is not None, detail="Not expired."
        )

    # -- the IP allow-list. WF-131's own control. ------------------------- #
    ip_ok = ip_allowed(context.get("ip"), settings["ip_allowlist"])
    if ip_ok:
        protections[vocab.PROTECTION_IP] = _protection(
            True,
            required=bool(settings["ip_allowlist"]),
            detail="The address is allowed, or no allow-list is set.",
        )
    else:
        deny(
            vocab.PROTECTION_IP,
            ProtectionDenied.REASON_IP,
            required=True,
            detail="The client address is not in this recipient's allow-list.",
        )

    # -- email verification, WF-069's. ------------------------------------ #
    if settings["email_required"]:
        verified = bool(context.get("email_verified"))
        if verified:
            protections[vocab.PROTECTION_EMAIL] = _protection(
                True, required=True, detail="The viewer proved the inbox."
            )
        else:
            deny(
                vocab.PROTECTION_EMAIL,
                ProtectionDenied.REASON_EMAIL,
                required=True,
                detail="This policy asks for a verified email address.",
            )
    else:
        protections[vocab.PROTECTION_EMAIL] = _protection(
            True, required=False, detail="This policy does not ask for an email."
        )

    # -- the passcode, WF-069's link password. ---------------------------- #
    if settings["passcode_required"]:
        if bool(context.get("passcode_ok")):
            protections[vocab.PROTECTION_PASSCODE] = _protection(
                True, required=True, detail="The passcode matched."
            )
        else:
            deny(
                vocab.PROTECTION_PASSCODE,
                ProtectionDenied.REASON_PASSCODE,
                required=True,
                detail="This policy asks for the link's passcode.",
            )
    else:
        protections[vocab.PROTECTION_PASSCODE] = _protection(
            True,
            required=False,
            detail=(
                "This policy does not ask for the passcode."
                if settings["passcode_available"]
                else "The link has no passcode to ask for."
            ),
        )

    # -- the NDA, WF-070's. ----------------------------------------------- #
    if settings["agreement_required"]:
        if bool(context.get("nda_accepted")):
            protections[vocab.PROTECTION_NDA] = _protection(
                True, required=True, detail="An acceptance covers the agreement."
            )
        else:
            deny(
                vocab.PROTECTION_NDA,
                ProtectionDenied.REASON_NDA,
                required=True,
                detail="No acceptance covers the agreement as it stands.",
            )
    else:
        protections[vocab.PROTECTION_NDA] = _protection(
            True, required=False, detail="This policy does not ask for an NDA."
        )

    # -- download: WF-072's three-way AND, reported not denied-as-access. -- #
    download_context = dict(context.get("download") or {})
    download = download_rules.decide_download(
        download_context.get(download_vocab.BULK_DOWNLOAD),
        settings["allow_download"],
        download_context.get(download_vocab.CAN_DOWNLOAD),
    )
    protections[vocab.PROTECTION_DOWNLOAD] = _protection(
        bool(download[download_vocab.DOWNLOAD_FIELD]),
        required=bool(settings["allow_download"]),
        detail=download[download_vocab.REASON_FIELD],
    )

    # -- watermark: reported, never denies. ------------------------------- #
    protections[vocab.PROTECTION_WATERMARK] = _protection(
        True,
        required=bool(settings["watermark"]),
        detail=(
            "Each view is watermarked with the viewer's identity."
            if settings["watermark"]
            else "This policy does not watermark the view."
        ),
    )

    allowed = not denied_by
    return {
        "decision": vocab.DECISION_ALLOW if allowed else vocab.DECISION_DENY,
        "allowed": allowed,
        "denied_by": denied_by,
        "reasons": reasons,
        "message": _message(reasons),
        "protections": protections,
        "effective": settings,
        "download": download,
        "watermark": {"enabled": settings["watermark"]},
        "recipient": policy.get(vocab.RECIPIENT),
        "evaluated_at": now.isoformat(timespec="milliseconds")
        if isinstance(now, datetime)
        else None,
    }


def _message(reasons: Sequence[str]) -> str:
    """The first refusal's wording, or the open sentence.

    One sentence for the viewer. The full list of refusals lives in ``reasons``
    and ``denied_by`` for the page and the audit row; a buyer is told the one
    thing they can act on next.
    """
    if not reasons:
        return "This policy permits the request."
    return ProtectionDenied.MESSAGES.get(reasons[0], "This link could not be opened.")


def acceptance_ok(acceptance: Mapping[str, Any] | None, agreement_id: Any, body: Any) -> bool:
    """Is a recorded acceptance sufficient? WF-070's rule, borrowed whole.

    Imported so a per-recipient matrix cannot develop a second opinion about what
    an acceptance covers. An acceptance names a text digest, so editing the NDA
    re-opens the gate for this recipient exactly as it does for every other one.
    """
    return acceptance_covers(acceptance or {}, agreement_id, body)


# --------------------------------------------------------------------------- #
# The action log
# --------------------------------------------------------------------------- #


def validate_action(action: Any, recipient: Any) -> tuple[str, str]:
    """``(action, recipient)``, or a refusal naming what was wrong.

    An unknown verb is refused at authoring time rather than stored, because a
    compliance review filters on the integer code and an unrecognised verb would
    land as WF-079's ``UNCLASSIFIED`` zero - a row that looks like it carries no
    event.
    """
    token = str(action or "").strip().lower()
    if token not in vocab.ACTIONS:
        raise ActionRefused(
            f"{token!r} is not one of {', '.join(vocab.ACTIONS)}",
            {"action": f"Use one of: {', '.join(vocab.ACTIONS)}."},
        )
    return token, normalize_recipient(recipient)


def action_code_checked(action: Any) -> int:
    """The WF-079 integer code for an action, refusing an unknown verb.

    The row carries this as ``event_code`` so WF-079's export reads a real
    integer without any coordination between the two workflows - the extension
    point WF-079's research asks for.
    """
    token = str(action or "").strip().lower()
    code = vocab.action_code(token)
    if code < 0:
        raise ActionRefused(
            f"{token!r} has no action code",
            {"action": f"Use one of: {', '.join(vocab.ACTIONS)}."},
        )
    return code


def bulk_download_alert(
    actions: Iterable[Mapping[str, Any]],
    *,
    now: datetime,
    threshold: int = vocab.DEFAULT_BULK_ALERT_THRESHOLD,
    window: timedelta | None = None,
) -> dict[str, Any]:
    """Is a bulk-download pattern present? An alert, never a block.

    The research's automation is "auto-alert on a bulk-download pattern", which
    is a reporting rule and distinct from WF-072's "block bulk download". So this
    function raises nothing and withholds nothing; it returns the pattern and the
    recipients who form it, and a caller decides what to do about it.

    Counted per recipient inside a rolling window ending at ``now``, so one buyer
    fetching five files in an hour is a pattern and five different buyers fetching
    one each is not.
    """
    window = window or timedelta(minutes=vocab.DEFAULT_BULK_ALERT_WINDOW_MINUTES)
    since = now - window
    counts: dict[str, int] = {}
    for row in actions:
        data = dict(row.get("data") if "data" in row else row)
        if str(data.get("action") or "").strip().lower() != vocab.ACTION_DOWNLOAD:
            continue
        moment, unreadable = _readable_moment(data.get("occurred_at") or data.get("at"))
        if unreadable or moment is None or moment < since or moment > now:
            continue
        recipient = str(data.get(vocab.RECIPIENT) or "").strip().lower() or "unknown"
        counts[recipient] = counts.get(recipient, 0) + 1

    flagged = sorted(name for name, count in counts.items() if count >= threshold)
    return {
        "alert": bool(flagged),
        "threshold": int(threshold),
        "window_minutes": int(window.total_seconds() // 60),
        "recipients": flagged,
        "counts": counts,
        "note": (
            "An alert on a pattern, not a block. Nothing was withheld; WF-072 owns "
            "the blocking rule."
        ),
    }


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "acceptance_ok",
    "action_code_checked",
    "bulk_download_alert",
    "effective_settings",
    "evaluate",
    "ip_allowed",
    "normalize_allowlist",
    "normalize_policy",
    "normalize_recipient",
    "now_utc",
    "validate_action",
]
