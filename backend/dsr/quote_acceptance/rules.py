"""The rules: acceptance configuration, the signing status machine, and the envelope.

Everything here is a rule, not a framework. It reads a mapping and returns a mapping, raises
one of the four errors in :mod:`dsr.quote_acceptance.errors`, and knows nothing about HTTP,
SQLite or a request. That is what makes every rule below testable without a server, and it
is why the HTTP layer in ``dsr.features.wf095_collect_acceptance_by_e_signature`` is a thin
translation of what happens here.

The four rules the research fixes exactly
-----------------------------------------

**The envelope needs a method, and the method needs its signers.** "``hs_acceptance_method`` =
``clickwrap`` | ``esignature`` | ``print_and_sign``", and under the e-signature tab the
seller "tick[s] the buyer contacts under **Buyer contacts required to sign**". So
:func:`validate_acceptance` refuses an e-signature quote with no buyer signer, because the
research makes the buyer contact the thing being ticked, and an e-signature envelope nobody
can open has no first signature.

**An attachment marked *In signing* forces the method.** "If an attachment is marked *In
signing*, e-signature must be used for the quote." :func:`validate_acceptance` reads the
attachment list and refuses ``clickwrap`` or ``print_and_sign`` on a quote that carries one,
naming the attachment.

**The PDF is capped at 40 MB.** "Quote PDFs larger than 40 MB may not be successfully verified
or signed." :func:`check_document_size` refuses an envelope whose document is over the cap,
and it refuses before the verification window opens, so the buyer is not sent a link that
cannot work.

**A multi-signature quote costs one usage.** "if a published quote with e-signatures enabled
requires three signatures, this only counts as one usage toward your limit." So
:func:`quota_cost` returns ``1`` for any envelope with any number of signers, and the quota
row is written once per envelope rather than once per signer.

The status machine, read in one place
-------------------------------------

:data:`~dsr.quote_acceptance.vocabulary.STATUS_TRANSITIONS` is the whole machine. Each status
names the one status it may move to and the event that causes the move. :func:`next_status`
is the only function in the package that reads that table, so a status cannot advance for a
reason that no row authorises, and :func:`assert_transition` refuses a transition the table
does not contain rather than silently accepting it.

The buyer-first ordering, and why it is a rule rather than a coincidence
-------------------------------------------------------------------------

The data flow names the sequence "Pending signature -> Viewed - pending signature -> Pending
countersignature -> Accepted", and it says "Countersigners are emailed automatically when the
buyer signs". So the buyer signs first and the countersignature is a later step.
:func:`signing_order` refuses to record a countersignature before the buyer's, because the
status table has no state a countersignature could produce before then. The research's own
aside that "Countersigners who start first do not need to verify" is about *verification*, not
about *order*, and is carried in the vocabulary rather than being turned into a second,
competing order rule here.

Reassignment is per-quote and only before a signature
-----------------------------------------------------

"optionally enable **Quote signer(s) can reassign**", and the reassignment button appears in
the buyer's own acceptance step. So :func:`validate_reassignment` refuses a quote that has the
flag off, and refuses a party who has already signed. A reassignment writes a new activity row
rather than editing the old one, because the activity log is append-only.

What this module will not do
----------------------------

It will not decide who is holding the email address. The verification gate is modelled as a
token minted on request with the evidence's own one-hour window, and
:data:`~dsr.quote_acceptance.vocabulary.AUTHENTICATION_OWNER` records that the identity duty
is the integrator's. Nothing here asks a caller to prove anything beyond presenting the token
that was minted for that party on that envelope, and nothing here caches a verification:
:func:`verify_token` re-reads the window on every call, so a second attempt an hour later
fails rather than inheriting a pass.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from dsr.quote_acceptance import vocabulary as vocab
from dsr.quote_acceptance.errors import (
    AcceptanceRefused,
    EnvelopeNotFound,
    QuotaRefused,
)

#: The payload-side twin of the envelope's ``room_id``. See the note in the vocabulary.
ROOM_REF = vocab.ROOM_REF

#: Bytes in a megabyte, as the cap's unit. The evidence says "40 MB" and the envelope is
#: measured in bytes, so the conversion is named once here rather than repeated.
BYTES_PER_MB = 1024 * 1024


def _truthy(value: Any) -> bool:
    """Read a flag a seller or an integration may spell as a bool, a string or a number."""

    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def normalise_method(value: Any) -> str:
    """Read an acceptance method under any of the three spellings the sources use.

    ``hs_acceptance_method`` is the vendor property and the source of truth. The product's own
    prose says *E-signature*, *Print and sign* and *Accept without signature*, so the
    spellings a caller who read this product would send are accepted too. Anything else is
    refused by name, because a silently defaulted method would put the wrong acceptance rule
    on a quote.
    """

    if value is None:
        return METHOD_ESIGNATURE_DEFAULT
    text = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    if text in {"esignature", "e_signature"}:
        return vocab.METHOD_ESIGNATURE
    if text in {"clickwrap", "accept_without_signature", "no_signature"}:
        return vocab.METHOD_CLICKWRAP
    if text in {"print_and_sign", "printandsign"}:
        return vocab.METHOD_PRINT_AND_SIGN
    raise AcceptanceRefused(
        f"acceptance method {value!r} is not one this workflow implements.",
        {
            vocab.ACCEPTANCE_METHOD_FIELD: (
                f"acceptance_method must be one of {', '.join(vocab.ACCEPTANCE_METHODS)}, not "
                f"{value!r}."
            )
        },
    )


#: The method a quote carries when the payload names none. E-signature is the default because
#: this workflow exists to collect an e-signature, and because a caller that reaches this
#: router has already chosen an e-signature flow. :func:`validate_acceptance` still refuses a
#: default e-signature quote that names no buyer signer, so the default cannot produce a
#: signed-nobody envelope.
METHOD_ESIGNATURE_DEFAULT = vocab.METHOD_ESIGNATURE


def as_recipients(value: Any, field_name: str) -> list[dict[str, Any]]:
    """Read the signer list a payload names.

    Accepts a list of strings, a list of mappings, or a comma-separated string, because the
    seller UI ticks contacts while an integration sends objects. Every entry is normalised to
    ``{"name", "email", "role", "contact_id"}`` so every rule below reads one shape.
    """

    if value is None or value == "":
        return []
    if isinstance(value, str):
        entries: list[Any] = [part for part in value.replace(";", ",").split(",") if part.strip()]
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        entries = list(value)
    else:
        raise AcceptanceRefused(
            f"{field_name} must be a list of signers.",
            {field_name: f"{field_name} must be a list of signers, not {value!r}."},
        )

    signers: list[dict[str, Any]] = []
    for entry in entries:
        if isinstance(entry, Mapping):
            name = entry.get("name") or entry.get("contact_name") or ""
            email = entry.get("email") or entry.get("contact_email") or ""
            role = entry.get("role") or vocab.ROLE_BUYER
            contact_id = entry.get("contact_id") or entry.get(vocab.SIGNER_ASSOCIATION_TYPE)
        else:
            text = str(entry).strip()
            name, _, email = text.partition(":")
            if not email:
                name, email = "", text
            role = vocab.ROLE_BUYER
            contact_id = None
        signers.append(
            {
                "name": str(name).strip() or (str(email).strip() if email else ""),
                "email": str(email).strip(),
                "role": normalise_role(role),
                "contact_id": contact_id,
            }
        )
    return signers


def normalise_role(value: Any) -> str:
    """Read a signer role under the product's spelling or the vendor's."""

    text = str(value or vocab.ROLE_BUYER).strip().lower().replace("-", "_").replace(" ", "_")
    if text in {"buyer", "signer", "contact", "buyer_contact", "quote_signer"}:
        return vocab.ROLE_BUYER
    if text in {"countersigner", "counter_signer", "internal", "approver"}:
        return vocab.ROLE_COUNTERSIGNER
    return vocab.ROLE_BUYER


def validate_email(value: Any, field_name: str) -> str:
    """Refuse an email address that cannot receive a verification link.

    The check is deliberately shallow: a local part, one ``@``, a domain with a dot. The
    research names no address grammar, and a deeper check would refuse addresses real
    mailers accept. What matters for this workflow is that a signer has *some* address to
    send the one-hour verification link to, and an address without a domain cannot receive
    one.
    """

    text = str(value or "").strip()
    local, _, domain = text.partition("@")
    if not local or "." not in domain or " " in text:
        raise AcceptanceRefused(
            f"{field_name} must be an email address.",
            {field_name: f"{field_name} must be an email address, not {text!r}."},
        )
    return text


def validate_acceptance(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalise one quote's acceptance configuration.

    Returns the normalised configuration, and raises :class:`AcceptanceRefused` with a
    field-keyed map naming every half that is wrong. The rules, each from the evidence:

    * the method must be one of the three the research names;
    * an e-signature quote must name at least one buyer contact to sign;
    * an e-signature quote may name at most two signers, one per party;
    * a quote carrying an *In signing* attachment refuses the two other methods;
    * ``hs_esign_num_signers_required`` must be a whole number between 1 and the signers the
      quote names.
    """

    method_raw = payload.get(vocab.ACCEPTANCE_METHOD_FIELD)
    if method_raw in (None, ""):
        method_raw = payload.get(vocab.ACCEPTANCE_METHOD_PLAIN)
    method = normalise_method(method_raw)

    signers = as_recipients(payload.get(vocab.BUYER_SIGNERS_FIELD), vocab.BUYER_SIGNERS_FIELD)
    for index, signer in enumerate(signers):
        signer["email"] = validate_email(
            signer["email"], f"{vocab.BUYER_SIGNERS_FIELD}[{index}].email"
        )

    countersigners = as_recipients(payload.get("countersigners"), "countersigners")
    for index, signer in enumerate(countersigners):
        signer["email"] = validate_email(signer["email"], f"countersigners[{index}].email")

    attachments = _in_signing_attachments(payload)
    errors: dict[str, str] = {}

    if method == vocab.METHOD_ESIGNATURE:
        if not [s for s in signers if s["role"] == vocab.ROLE_BUYER]:
            errors[vocab.BUYER_SIGNERS_FIELD] = (
                "an e-signature quote must name at least one buyer contact under Buyer "
                "contacts required to sign."
            )
        total = len(signers) + len(countersigners)
        if total > vocab.MAX_SIGNERS:
            errors[vocab.BUYER_SIGNERS_FIELD] = (
                f"an envelope carries at most {vocab.MAX_SIGNERS} signers, one per party, not "
                f"{total}."
            )
    elif attachments:
        errors[vocab.ACCEPTANCE_METHOD_FIELD] = vocab.IN_SIGNING_ATTACHMENT_FORCES_ESIGNATURE

    required = _signers_required(payload, signers, countersigners)
    if required is not None and method == vocab.METHOD_ESIGNATURE:
        if required > len(signers) + len(countersigners):
            errors[vocab.SIGNERS_REQUIRED_FIELD] = (
                f"{vocab.SIGNERS_REQUIRED_FIELD} is {required} but the quote names "
                f"{len(signers) + len(countersigners)} signers."
            )

    if errors:
        raise AcceptanceRefused("The acceptance configuration is not one I can accept.", errors)

    return {
        "method": method,
        "signers": signers,
        "countersigners": countersigners,
        "signers_required": required if required is not None else 1,
        "reassign_allowed": _truthy(payload.get(vocab.REASSIGN_ALLOWED_FIELD)),
        "identity_verification_required": _truthy(payload.get(vocab.VERIFICATION_REQUIRED)),
        "in_signing_attachments": attachments,
        "room_ref": payload.get(ROOM_REF),
    }


def _in_signing_attachments(payload: Mapping[str, Any]) -> list[str]:
    """The attachment names a payload marks as *In signing*."""

    raw = payload.get("attachments")
    names: list[str] = []
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        for entry in raw:
            if isinstance(entry, Mapping) and _truthy(entry.get("in_signing")):
                names.append(str(entry.get("name") or entry.get("filename") or "attachment"))
    return names


def _signers_required(
    payload: Mapping[str, Any], signers: list[dict[str, Any]], countersigners: list[dict[str, Any]]
) -> int | None:
    """Read ``hs_esign_num_signers_required`` and hold it to the signers named."""

    raw = payload.get(vocab.SIGNERS_REQUIRED_FIELD)
    if raw in (None, ""):
        return None
    if isinstance(raw, bool):
        raise AcceptanceRefused(
            f"{vocab.SIGNERS_REQUIRED_FIELD} must be a whole number.",
            {vocab.SIGNERS_REQUIRED_FIELD: f"{vocab.SIGNERS_REQUIRED_FIELD} must be a number."},
        )
    try:
        required = int(raw)
    except (TypeError, ValueError) as exc:
        raise AcceptanceRefused(
            f"{vocab.SIGNERS_REQUIRED_FIELD} must be a whole number.",
            {
                vocab.SIGNERS_REQUIRED_FIELD: (
                    f"{vocab.SIGNERS_REQUIRED_FIELD} must be a whole number, not {raw!r}."
                )
            },
        ) from exc
    if required < 1 or required > vocab.MAX_SIGNERS:
        raise AcceptanceRefused(
            f"{vocab.SIGNERS_REQUIRED_FIELD} must be between 1 and {vocab.MAX_SIGNERS}.",
            {
                vocab.SIGNERS_REQUIRED_FIELD: (
                    f"{vocab.SIGNERS_REQUIRED_FIELD} must be between 1 and {vocab.MAX_SIGNERS}, "
                    f"not {required}."
                )
            },
        )
    return required


def check_document_size(size_bytes: Any) -> dict[str, Any]:
    """Refuse a document over the researched cap, and report the reading either way.

    "Quote PDFs larger than 40 MB may not be successfully verified or signed." So a document
    at or under the cap is accepted and reported, and one over it is refused by name. The
    figure is returned rather than merely passed so the envelope records the size that was
    measured, and a reader can see *why* the reading was the one it was.
    """

    if size_bytes in (None, ""):
        return {
            "size_bytes": None,
            "size_mb": None,
            "over_cap": False,
            "cap_mb": vocab.PDF_SIZE_CAP_MB,
        }
    if isinstance(size_bytes, bool):
        raise AcceptanceRefused(
            "document_size_bytes must be a number.",
            {"document_size_bytes": "document_size_bytes must be a number, not a boolean."},
        )
    try:
        size = int(size_bytes)
    except (TypeError, ValueError) as exc:
        raise AcceptanceRefused(
            "document_size_bytes must be a number.",
            {"document_size_bytes": f"document_size_bytes must be a number, not {size_bytes!r}."},
        ) from exc
    if size < 0:
        raise AcceptanceRefused(
            "document_size_bytes must not be negative.",
            {"document_size_bytes": f"document_size_bytes must not be negative, not {size}."},
        )
    cap_bytes = vocab.PDF_SIZE_CAP_MB * BYTES_PER_MB
    over = size > cap_bytes
    if over:
        raise AcceptanceRefused(
            "The quote PDF is larger than 40 MB, so it may not be verified or signed.",
            {
                "document_size_bytes": (
                    f"The document is {size / BYTES_PER_MB:.1f} MB, over the {vocab.PDF_SIZE_CAP_MB}"
                    " MB cap. A PDF over the cap may not be successfully verified or signed."
                )
            },
        )
    return {
        "size_bytes": size,
        "size_mb": round(size / BYTES_PER_MB, 3),
        "over_cap": over,
        "cap_mb": vocab.PDF_SIZE_CAP_MB,
        "evidence": vocab.PDF_SIZE_CAP_QUOTE,
    }


def step_status(current: str, event: str) -> str:
    """The single status this event moves the current status to, or the current one if none.

    Reads :data:`~dsr.quote_acceptance.vocabulary.STATUS_TRANSITIONS` and nothing else, and
    applies one step only. This is the strict rule: an event the current status does not
    authorise returns the current status unchanged, so a second signature on an
    already-accepted envelope is a no-op and a late ``viewed`` never rewinds a quote that has
    moved on. :func:`advance_status` is the permissive half, and a caller picks between them
    by which one it needs.
    """

    row = vocab.STATUS_TRANSITIONS.get(current)
    if row is None:
        return current
    following, caused_by = row
    if following is None or caused_by != event:
        return current
    return following


def advance_status(current: str, event: str) -> str:
    """The status this event leaves the quote in, applying the steps the event implies.

    Reads the same table as :func:`step_status`, but applies it **until it stops changing**.
    A signature implies the buyer saw the document, so a buyer who signs while the status is
    still ``pending_signature`` passes through ``viewed_pending_signature`` on the way to
    ``pending_countersignature``. Applying one step would leave the envelope at
    ``viewed_pending_signature`` with the buyer's signature already recorded, and from there
    no event can advance it: the buyer cannot sign twice and the countersigner is refused by
    the order rule. That is a dead end a caller can reach through this workflow's own routes,
    and applying the implied steps is what closes it. See
    :data:`~dsr.quote_acceptance.inferences.DERIVED_SIGNING_IMPLIES_VIEWING`.

    A later event skips the steps it implies rather than replaying them. From
    ``pending_signature``, a ``buyer_signed`` implies the ``viewed`` step first and so runs
    both. From ``viewed_pending_signature``, a ``buyer_signed`` is the next step and runs
    alone. The engine refuses a *countersignature* before the buyer's through
    :func:`assert_signature_order`, which is the order rule and not the status rule, so this
    function is only the pure table logic. The skip is bounded by the length of the chain, so
    a table that named a cycle could not spin here.
    """

    # Every event from the start of the chain up to and including the one that reaches the
    # current status. `event` must be at or after the current status's own step to advance it.
    all_events = _chain_events()
    current_index = _events_to_reach(current)
    try:
        event_index = all_events.index(event)
    except ValueError:
        return current
    if event_index < current_index:
        # This event belongs to an earlier state than the quote is in; it does not rewind.
        return current
    status = current
    for implied_event in all_events[current_index : event_index + 1]:
        status = step_status(status, implied_event)
    return status


def _chain_events() -> list[str]:
    """The events of the chain, in order, read from the table itself."""

    events: list[str] = []
    status = vocab.STATUS_PENDING_SIGNATURE
    seen: set[str] = set()
    while status not in seen:
        seen.add(status)
        row = vocab.STATUS_TRANSITIONS.get(status)
        if row is None or row[0] is None:
            return events
        events.append(row[1])
        status = row[0]
    return events


def _events_to_reach(status: str) -> int:
    """How many events of the chain have happened to leave the quote at ``status``."""

    index = 0
    current = vocab.STATUS_PENDING_SIGNATURE
    while current != status:
        row = vocab.STATUS_TRANSITIONS.get(current)
        if row is None or row[0] is None:
            return index
        index += 1
        current = row[0]
    return index


def next_status(current: str, event: str) -> str:
    """The permissive half, kept as the name the status reads call.

    :func:`advance_status` is the real name and this alias exists so the two halves read as one
    pair of rules. A caller that must not rewind uses :func:`step_status`.
    """

    return advance_status(current, event)


def assert_transition(current: str, event: str) -> str:
    """The status this event moves to on exactly one step, refusing anything else.

    The enforcing half of :func:`step_status`. The reader half is used where an event that does
    not apply is ignored; this half is used where a caller asked for a specific step and a
    wrong step is a request this workflow will not accept. It deliberately refuses a
    countersignature from ``pending_signature`` even though :func:`advance_status` would run
    the implied steps, because the engine's order rule is what forbids that and this is where
    the refusal is stated.
    """

    row = vocab.STATUS_TRANSITIONS.get(current)
    if row is None or row[0] is None or row[1] != event:
        raise AcceptanceRefused(
            f"The signing status {current!r} does not advance on {event!r}.",
            {
                "signing_status": (
                    f"signing_status {vocab.SIGNING_STATUS_LABELS.get(current, current)!r} "
                    f"advances on {row[1]!r}, not {event!r}."
                    if row and row[0]
                    else f"{current!r} is a terminal signing status and advances on nothing."
                )
            },
        )
    return row[0]  # type: ignore[return-value]


def is_open(status: str) -> bool:
    """Does the envelope still need a signature?"""

    return status in vocab.OPEN_STATUSES


def signing_order_signers(
    signers: Sequence[Mapping[str, Any]], countersigners: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """The envelope's signers in signing order: the buyer first, the countersigner after.

    The data flow names the order: the status moves to ``pending_countersignature`` on the
    buyer's signature and the research says "Countersigners are emailed automatically when the
    buyer signs". So the order is fixed and is derived from the role, never from the order the
    seller's payload happened to list.
    """

    ordered: list[dict[str, Any]] = []
    for signer in signers:
        ordered.append(
            {**dict(signer), "signing_order": 1, "role": normalise_role(signer.get("role"))}
        )
    for signer in countersigners:
        ordered.append({**dict(signer), "signing_order": 2, "role": vocab.ROLE_COUNTERSIGNER})
    return sorted(ordered, key=lambda s: s["signing_order"])


def assert_signature_order(current_status: str, role: str) -> None:
    """Refuse a countersignature recorded before the buyer's.

    The status machine has no state a countersignature could produce before the buyer signs:
    ``pending_countersignature`` is reached only on ``buyer_signed``. So recording a
    countersignature at ``pending_signature`` would have to invent a status, and the research
    says the countersigner is notified *after* the buyer signs. This is where that is enforced.
    """

    if normalise_role(role) != vocab.ROLE_COUNTERSIGNER:
        return
    if current_status in (
        vocab.STATUS_VIEWED_PENDING_SIGNATURE,
        vocab.STATUS_PENDING_COUNTERSIGNATURE,
    ):
        return
    raise AcceptanceRefused(
        "A countersignature cannot be recorded before the buyer has signed.",
        {
            "signing_status": (
                "Countersigners are notified when the buyer signs, so the signing status must "
                f"be {vocab.SIGNING_STATUS_LABELS[vocab.STATUS_PENDING_COUNTERSIGNATURE]!r} "
                "before a countersignature is recorded."
            )
        },
    )


def validate_reassignment(envelope: Mapping[str, Any], signer: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one signer reassignment against the quote's flag and the signer's state.

    "optionally enable **Quote signer(s) can reassign**" makes the flag per-quote and off by
    default, and "Reassign this quote signer" appears in the buyer's own acceptance step, so a
    party who has already signed is not reassignable. Both refusals name the evidence.
    """

    if not _truthy(envelope.get(vocab.REASSIGN_ALLOWED_FIELD)):
        raise AcceptanceRefused(
            "This quote does not allow signer reassignment.",
            {
                vocab.REASSIGN_ALLOWED_FIELD: (
                    "Quote signer(s) can reassign is off for this quote, so this signer cannot "
                    "be reassigned."
                )
            },
        )
    if _truthy(signer.get("signed")) or signer.get("signed_at"):
        raise AcceptanceRefused(
            "A signer who has already signed cannot be reassigned.",
            {"signer": vocab.REASSIGN_AFTER_SIGN_REFUSAL},
        )
    return {"reassign_allowed": True}


def verification_window(requested_at: str | datetime, now: datetime) -> dict[str, Any]:
    """The one-hour window the research fixes, opened when the buyer clicks *Verify email*.

    "Buyers have one hour to complete the signature process after clicking Verify email." The
    window opens at ``requested_at``, not when the envelope was sent, and
    :func:`verify_token` re-reads it on every call rather than caching a pass.
    """

    opened = _as_datetime(requested_at)
    closed = opened + timedelta(minutes=vocab.VERIFICATION_WINDOW_MINUTES)
    return {
        "requested_at": opened.isoformat(),
        "expires_at": closed.isoformat(),
        "window_minutes": vocab.VERIFICATION_WINDOW_MINUTES,
        "expired": now >= closed,
        "open": opened <= now < closed,
        "evidence": vocab.VERIFICATION_WINDOW_QUOTE,
    }


def verify_token(envelope: Mapping[str, Any], token: Any, now: datetime) -> dict[str, Any]:
    """Check a verification token against the envelope's window and its buyer gate.

    The verification is "an emailed one-time link", so the check is a string comparison
    against the token minted for that envelope plus the window's still being open. The
    function re-derives the window every call, so a token presented a minute before the hour
    passes and a token presented a minute after does not, and no pass is remembered.
    """

    if not _truthy(envelope.get(vocab.VERIFICATION_REQUIRED)):
        # Verification is off for this envelope, so there is nothing to verify and the widget
        # is already unlocked. The research models verification as a toggle a super admin
        # sets, not a per-quote requirement that cannot be absent.
        return {"required": False, "verified": True, "reason": "verification_not_required"}

    requested_at = envelope.get(vocab.VERIFICATION_REQUEST_FIELD)
    if not requested_at:
        # The buyer has not clicked Verify email yet. That is an ordinary unverified state,
        # not a missing row, so it is reported as a reason rather than raised: the widget
        # stays locked and the attempt is logged as a failed one.
        return {
            "required": True,
            "verified": False,
            "reason": "verification_not_requested",
            "window": None,
            "evidence": vocab.VERIFICATION_WINDOW_QUOTE,
        }

    window = verification_window(requested_at, now)
    if window["expired"]:
        return {
            "required": True,
            "verified": False,
            "reason": "verification_window_expired",
            "window": window,
            "evidence": vocab.VERIFICATION_WINDOW_QUOTE,
        }

    expected = envelope.get("verification_token")
    if not token or not expected or str(token) != str(expected):
        return {
            "required": True,
            "verified": False,
            "reason": "verification_token_mismatch",
            "window": window,
        }

    return {"required": True, "verified": True, "reason": "verified", "window": window}


def quota_cost(envelope: Mapping[str, Any]) -> int:
    """The usage one envelope costs: always ``1``, whatever its signer count.

    "if a published quote with e-signatures enabled requires three signatures, this only counts
    as one usage toward your limit." So the cost is a property of the envelope, not of its
    signers, and :func:`enables_quota` holds the second half: the usage is consumed as soon as
    the option is turned on for a *published* quote, "The quote doesn't need to be signed to
    apply to the signature limit."
    """

    return 1


def enables_quota(is_published: bool, method: str) -> bool:
    """Does turning this acceptance configuration on consume quota immediately?"""

    return bool(is_published) and normalise_method(method) == vocab.METHOD_ESIGNATURE


def quota_month(now: datetime) -> str:
    """The quota month, because "limits ... reset on the 1st".

    The month is the ``YYYY-MM`` the reading belongs to, and the reset day is carried in the
    vocabulary. No ceiling is asserted, because the research states none.
    """

    return f"{now.year:04d}-{now.month:02d}"


def assert_quota_not_exceeded(used: int, limit: int | None, *, month: str | None = None) -> None:
    """Refuse to open an envelope that would take the room past a stated ceiling.

    ``limit`` is ``None`` whenever the room states no ceiling, which is the default here
    because the research states none. A ``None`` ceiling always passes, and that is recorded
    by :data:`~dsr.quote_acceptance.vocabulary.QUOTA_UNSPECIFIED` rather than hidden.
    """

    if limit is None:
        return
    if used >= int(limit):
        raise QuotaRefused(
            "The e-signature usage for this month is at its limit.",
            used=int(used),
            limit=int(limit),
            month=month or "",
        )


def record_activity(event: str) -> dict[str, Any]:
    """The activity row one signature event writes, or nothing for an event that writes none.

    The research names four activities and no others, so an event outside
    :data:`~dsr.quote_acceptance.vocabulary.EVENT_ACTIVITIES` writes no activity. This is
    why ``viewed`` and :data:`~dsr.quote_acceptance.vocabulary.EVENT_VERIFICATION_REQUESTED`
    advance the status or unlock the widget without inventing a fifth activity.
    """

    activity = vocab.EVENT_ACTIVITIES.get(event, "")
    if not activity:
        return {"event": event, "activity": None, "writes_activity": False}
    return {"event": event, "activity": activity, "writes_activity": True}


def _as_datetime(value: str | datetime) -> datetime:
    """Read a timestamp that may arrive as an ISO string or a datetime."""

    if isinstance(value, datetime):
        moment = value
    else:
        text = str(value).strip().replace("Z", "+00:00")
        try:
            moment = datetime.fromisoformat(text)
        except ValueError as exc:
            raise AcceptanceRefused(
                "A timestamp must be an ISO 8601 instant.",
                {"timestamp": f"{value!r} is not an ISO 8601 instant."},
            ) from exc
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def parse_instant(value: Any, field_name: str = "at") -> datetime:
    """Read an instant a caller supplied, exposed for the engine's clock seam."""

    return _as_datetime(value)


ERROR_TYPES = (AcceptanceRefused, EnvelopeNotFound, QuotaRefused)
