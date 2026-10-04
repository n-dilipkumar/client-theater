"""The e-vault rules: the ready event, the back-pressure signal, and the sealed bytes.

Every rule here is the researched specification for WF-080 made executable. The
specification is ``docs/research/digital-sales-room-workflows/wf/WF-080.md``, quoted in
full in issue 128, and the docstring on each rule names the evidence it came from.

The six rules the rest of the product leans on
----------------------------------------------

**A subscription must hear the ready event.** The specification's user flow opens with
"Integrator creates a webhook subscription for ``document_completed_pdf_ready``", and
its extensibility note says "subscribe to the *ready* event rather than polling status".
So a subscription that does not carry that trigger is refused. It is the one field this
workflow insists on, because a room that cannot hear the ready event has no way to learn
the PDF exists, and a workflow that refuses to poll has no second mechanism.

**A delivery is identified by its delivery id, not by its document id.** The evidence
says the ``X-PandaDoc-Webhook-Event-Id`` header exists "to process each webhook
notification once... even when PandaDoc retries delivery". A document id is therefore
never a dedupe key: one document fires several notifications, and a document id as the
key would drop all but the first. A delivery with no id at all is refused rather than
stored undeduplicated, because "processed once" is the requirement and an event this
build cannot identify cannot be processed once.

**202 is an outcome with a header and no body.** The evidence quotes the vendor: "The
signed document file is not ready yet... Retry after the indicated number of seconds. No
response body is returned." So the shape is exact - 202, a ``Retry-After`` header, and
no body - and the room stores it as a named outcome rather than raising it. The seconds
are derived, and the derivation is recorded as DERIVED_RETRY_AFTER_SECONDS.

**A document whose signers have not all finished is refused, not delayed.** Back-
pressure means work is in progress. "All signers complete; PandaDoc generates the PDF"
is step two of the user flow, so before that there is no generation to wait for, and a
202 with a ``Retry-After`` would be telling a client to retry a PDF nobody has started
producing. The two cases are different failures and get different answers: 202 while
generating, 409 before it.

**The sealed endpoint is production only.** "Production key only - This endpoint only
works with a Production key. You'll get a 401 Unauthorized error when trying to use a
Sandbox key", and the extensibility note draws the consequence: "sandbox-based
integration tests must use the plain download endpoint". So a sandbox caller is not
misconfigured, it is somewhere else, and the answer names the endpoint that does work.

**Sealed bytes are byte-stable; plain bytes are not.** "the ``/download-protected``
endpoint always returns the same digitally sealed PDF file, while ``/download`` allows
for watermark customization". So :func:`build_pdf` is a pure function of its arguments,
the sealed variant passes nothing variable, and the plain variant passes the watermark.
Two sealed retrievals are byte-identical and share a digest. A watermarked plain
retrieval is a different file with a different digest, and that is the whole reason the
two endpoints exist.

What this module does not decide
--------------------------------

Whether the vendor actually sealed anything. This build synthesises the artifact bytes
deterministically from the document's own fields so that the byte-stability claim is
testable, and it says so: :data:`~dsr.security_governance.evault_vocabulary.SEAL_SCOPE`
ships with every response. A digest comparison is what this room can prove. It is not a
certificate-chain validation, and no function here pretends otherwise.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from dsr.security_governance import evault_vocabulary as vocab

#: The data key this workflow stores a room reference under.
#:
#: Not ``room_id``, and that is not a style preference. ``room_id`` is part of the record
#: *envelope*, so ``AuditedDatabase._insert_record`` strips it out of ``data`` before the
#: dynamic index is built. A row that stored its room there would be unfilterable by
#: ``find()``. Reads use ``RecordStore.list(..., room_id=...)``, which filters on the
#: envelope column, and every projection carries ``room_id`` back.
ROOM_REF = "room_ref"

#: The data key a child row uses to point at its executed document. This one really does
#: live in ``data``, because it is this workflow's own field and the dynamic index has to
#: resolve it for the attempt and delivery logs.
DOCUMENT_REF = "document_ref"


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
#
# All seven are declared here and raised by nothing else in the product, which is what
# makes it safe to map them in the feature module: the host refuses a second feature
# registering a handler for the same type, and a handler for ValueError or
# LookupError would intercept those exceptions across the whole application.


class SubscriptionInvalid(ValueError):
    """A subscription this workflow will not accept.

    Carries a field-keyed map, because the caller filling in a form needs each message
    beside the input that caused it rather than one combined sentence.
    """

    def __init__(self, message: str, errors: Mapping[str, str] | None = None) -> None:
        super().__init__(message)
        self.errors: dict[str, str] = dict(errors or {})


class DocumentInvalid(ValueError):
    """An executed document row this workflow will not accept.

    Field-keyed for the same reason as :class:`SubscriptionInvalid`, and kept as its own
    type rather than folded into it so the two surfaces can answer with different codes:
    a bad subscription is a 400 on a subscription, and a bad document row is a 400 on a
    document, and a client fixing one should not be handed the other's message.
    """

    def __init__(self, message: str, errors: Mapping[str, str] | None = None) -> None:
        super().__init__(message)
        self.errors: dict[str, str] = dict(errors or {})


class SubscriptionNotFound(LookupError):
    """No such subscription, or it was never a subscription this workflow owns."""


class DocumentNotFound(LookupError):
    """No such executed document, or it belongs to another collection."""


class ArtifactNotFound(LookupError):
    """No such artifact, or it belongs to another collection.

    Its own type rather than the store's ``RecordNotFound``, for the same reason as
    :class:`DocumentNotFound`: a feature may only map error types it raises itself.

    It is raised by :meth:`EvaultEngine.read_artifact` only. The download routes never raise
    it, because the honest answer for a sealed document whose vault read has not happened yet
    is 202 with a ``Retry-After`` header: the file exists in the vault, this variant has just
    not been fetched from it, and a 404 for a file that is there would be a lie.
    """


class NotCompleted(ValueError):
    """The signers have not all finished, so no PDF is being produced.

    The refusal, not a delay. ``status`` is 409 and ``code`` names the state, so the
    HTTP layer can answer without knowing which document state it is looking at.
    """

    def __init__(self, message: str, code: str = "not_completed", state: str = "") -> None:
        super().__init__(message)
        self.status = 409
        self.code = code
        self.state = state


class SandboxKeyRejected(PermissionError):
    """The sealed endpoint was called with a sandbox key.

    A refusal, not a configuration fault. ``status`` is 401 because that is the status the
    vendor documents for exactly this case, and ``code`` is stable so a client can branch
    on it rather than on the message.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.status = 401
        self.code = "sandbox_key_rejected"


class Throttled(RuntimeError):
    """Too many retrievals for one document inside the window.

    ``status`` is 429 and ``code`` is ``throttled``, which is the specification's own
    wording: "429 -> ``throttled``". Naming it is the whole point, because the
    specification forbids surfacing it as a generic failure.
    """

    def __init__(self, message: str, retry_after_seconds: int) -> None:
        super().__init__(message)
        self.status = 429
        self.code = "throttled"
        self.retry_after_seconds = int(retry_after_seconds)


# --------------------------------------------------------------------------- #
# Timestamps
# --------------------------------------------------------------------------- #


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def stamp(moment: datetime | None = None) -> str:
    """An ISO 8601 instant, always in UTC, with milliseconds.

    Milliseconds because a throttle window is measured in seconds and a log that rounded
    to the second would make two retrievals inside one window indistinguishable.
    """

    return (moment or utcnow()).astimezone(timezone.utc).isoformat(timespec="milliseconds")


def parse_stamp(value: Any) -> datetime | None:
    """Read a stamp this module wrote back into a datetime, or ``None``.

    Never raises. A row whose timestamp cannot be read is counted as being outside every
    window rather than as being inside one, because a throttled client must be able to
    recover, and an unreadable stamp must not be the thing that keeps it locked out.
    """

    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


# --------------------------------------------------------------------------- #
# Subscriptions
# --------------------------------------------------------------------------- #


def coerce_triggers(value: Any, field: str = "triggers") -> tuple[str, ...]:
    """Read a subscription's ``triggers`` list.

    Returns the names, de-duplicated and in the order the caller gave them. Every name
    must be a non-empty string; anything else is a validation failure rather than
    something coerced, because a trigger list that silently dropped an entry would leave
    a room believing it hears an event it does not hear.
    """

    if value is None:
        raise SubscriptionInvalid(
            "A subscription must name at least one trigger.",
            {field: "triggers is required."},
        )
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise SubscriptionInvalid(
            "triggers must be a list of event names.",
            {field: "triggers must be a list of event names, not a single string."},
        )

    names: list[str] = []
    for entry in value:
        if not isinstance(entry, str) or not entry.strip():
            raise SubscriptionInvalid(
                "Every trigger must be a non-empty event name.",
                {field: f"{entry!r} is not an event name."},
            )
        name = entry.strip()
        if name not in names:
            names.append(name)
    if not names:
        raise SubscriptionInvalid(
            "A subscription must name at least one trigger.",
            {field: "triggers is required."},
        )
    return tuple(names)


def require_ready_trigger(triggers: Sequence[str]) -> None:
    """Refuse a subscription that cannot hear the ready event.

    The one field this workflow insists on. "Integrator creates a webhook subscription
    for ``document_completed_pdf_ready``", and "subscribe to the *ready* event rather
    than polling status". A subscription without it is refused rather than created: this
    workflow does not poll, so a subscription that cannot hear the ready event has no
    other way to learn the PDF exists.

    Other triggers are allowed and stored. This workflow does not interpret them, and
    refusing a name the vendor documents for some other event would be refusing a fact
    about the vendor rather than a fact about this room.
    """

    missing = [name for name in vocab.REQUIRED_TRIGGERS if name not in triggers]
    if missing:
        raise SubscriptionInvalid(
            "A subscription must carry the ready event.",
            {
                "triggers": (
                    f"missing {', '.join(missing)}. This workflow subscribes to the ready "
                    "event and does not poll, so it cannot learn the PDF exists otherwise."
                )
            },
        )


def derive_shared_key(document_id: str) -> str:
    """The shared key a subscription carries, derived rather than invented by the caller.

    The specification marks this inferred: "webhook subscription record with a shared key
    ``[inferred - the subscription model has a shared key per the docs index, e.g.
    'Update Webhook Subscription Shared Key']``". So it is a derivation and the derivation
    is recorded as INFERRED_SUBSCRIPTION_SHARED_KEY.

    The key is a hash of the vendor document id under a fixed label, so it is stable for
    a document, differs between documents, and carries nothing about this room's own
    secrets. It is a join value, not a credential: the room compares it for equality and
    no route in this workflow presents it to anyone.
    """

    seed = f"pandadoc-webhook-subscription-shared-key:{document_id}".encode("utf-8")
    return "shr_" + hashlib.sha256(seed).hexdigest()[:32]


def coerce_environment(value: Any, field: str = "environment") -> str:
    """Read the environment a key belongs to.

    The two the specification names between them: "Production key only" and "trying to
    use a Sandbox key". Anything else is refused rather than defaulted, because
    defaulting an unrecognised environment to sandbox would silently turn a production
    retrieval into a 401, and defaulting it to production would hand a sealed artifact
    to a caller that should not have one.
    """

    if value is None:
        return vocab.ENVIRONMENT_PRODUCTION
    if not isinstance(value, str) or value.strip().lower() not in vocab.ENVIRONMENTS:
        raise SubscriptionInvalid(
            "environment must be one of the two the specification names.",
            {field: f"environment must be one of {', '.join(vocab.ENVIRONMENTS)}."},
        )
    return value.strip().lower()


def coerce_variant(value: Any, field: str = "variant") -> str:
    """Read which of the two download endpoints was called.

    The specification is explicit that the two are not interchangeable, so the variant is
    a closed set of two rather than a free string. An unrecognised variant is refused
    because silently treating it as ``plain`` would serve unbranded bytes to a caller
    that asked for something else.
    """

    if value is None:
        return vocab.VARIANT_SEALED
    if not isinstance(value, str) or value.strip().lower() not in vocab.VARIANTS:
        raise DocumentInvalid(
            "variant must be one of the two download endpoints the specification names.",
            {"variant": f"variant must be one of {', '.join(vocab.VARIANTS)}."},
        )
    return value.strip().lower()


def sealed_allowed(environment: str) -> bool:
    """May this environment call the sealed endpoint at all?"""

    return environment == vocab.SEALED_ENVIRONMENT


def coerce_state(value: Any, field: str = "state") -> str:
    """Read an executed document's state.

    A closed set of four, taken from the specification's data flow: "Completion -> async
    PDF generation -> e-vault storage". A state outside the set is refused rather than
    stored, because an unknown state has no defined meaning for the download route and a
    stored one would be a state the route has to guess about.
    """

    if value is None:
        return vocab.DEFAULT_DOCUMENT_STATE
    if not isinstance(value, str) or value.strip().lower() not in vocab.DOCUMENT_STATES:
        raise DocumentInvalid(
            "state must be one of the four the specification's data flow describes.",
            {field: f"state must be one of {', '.join(vocab.DOCUMENT_STATES)}."},
        )
    return value.strip().lower()


def coerce_watermark(value: Any) -> str:
    """Read the watermark to apply to a plain download.

    Only meaningful for the plain variant, which is the only one the specification says
    allows "watermark customization". A watermark is stripped of the characters that
    would need escaping inside a PDF text string, so a caller cannot inject a closing
    parenthesis and corrupt the artifact it is asking to have stamped.
    """

    if value is None:
        return ""
    if not isinstance(value, str):
        raise DocumentInvalid(
            "watermark must be a string.", {"watermark": "watermark must be a string."}
        )
    cleaned = re.sub(r"[^A-Za-z0-9 .:/-]", " ", value)
    return " ".join(cleaned.split())[:80]


# --------------------------------------------------------------------------- #
# The event
# --------------------------------------------------------------------------- #


def event_name_of(payload: Mapping[str, Any]) -> str:
    """The event name as it arrived, or an empty string.

    Read from the three keys the vendor's payloads are known to use, and returned as it
    was found rather than normalised. The extensibility note says the webhook is "the
    trigger", so the name is stored for the reader; whether the room acts on it is decided
    against the document's state, not against the string.
    """

    for key in vocab.EVENT_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def document_id_of(payload: Mapping[str, Any]) -> str:
    """The vendor document id, read from the shapes a payload is known to carry.

    The specification says only that "the handler extracts the document ``id`` from the
    payload". It does not quote the envelope, so the accepted shapes are derived and
    recorded as DERIVED_EVENT_PAYLOAD_SHAPE. PandaDoc nests the document under ``data``,
    which is tried first; a flat ``documentId`` and a bare ``id`` follow.

    An id under ``data`` wins over a top-level ``id`` because ``data.id`` is the document
    and a top-level ``id`` in a webhook envelope is far more often the notification's own
    identifier - which is what :func:`delivery_id_of` is for.
    """

    nested = payload.get("data")
    if isinstance(nested, Mapping):
        value = nested.get("id")
        if isinstance(value, str) and value.strip():
            return value.strip()

    document_id = payload.get("documentId")
    if isinstance(document_id, str) and document_id.strip():
        return document_id.strip()

    nested = payload.get("document")
    if isinstance(nested, Mapping):
        value = nested.get("id")
        if isinstance(value, str) and value.strip():
            return value.strip()

    value = payload.get("id")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return ""


def delivery_id_of(headers: Mapping[str, Any], payload: Mapping[str, Any]) -> tuple[str, str]:
    """The delivery id, and which source supplied it.

    Returns ``(delivery_id, source)`` where ``source`` is ``header`` or ``payload``, and
    raises when neither is present.

    The header is the documented mechanism and is tried first: the evidence quotes
    ``X-PandaDoc-Webhook-Event-Id`` by name for exactly this purpose. The payload keys are
    fallbacks, and they are tried in the order that avoids the trap - ``data.id`` is read
    as the document, so a bare top-level ``id`` is only considered last.

    A notification with no id is refused. "Process each webhook notification once... even
    when PandaDoc retries delivery" cannot be honoured for a row this build cannot
    identify, and storing an undeduplicated row would fail the requirement silently,
    which is the one outcome worse than refusing.
    """

    header_value = _header(headers, vocab.DEDUPE_HEADER)
    if header_value:
        return header_value, vocab.DELIVERY_FROM_HEADER

    for key in vocab.DELIVERY_ID_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip(), vocab.DELIVERY_FROM_PAYLOAD

    raise SubscriptionInvalid(
        "The notification carries no delivery id, so it cannot be applied exactly once.",
        {
            vocab.DEDUPE_HEADER: (
                f"expected the {vocab.DEDUPE_HEADER} header, or one of "
                f"{', '.join(vocab.DELIVERY_ID_KEYS)} in the body."
            )
        },
    )


def _header(headers: Mapping[str, Any], name: str) -> str:
    """Read one header, case-insensitively.

    HTTP header names are case-insensitive and Starlette hands them back lower-cased, so
    a lookup by the documented spelling would miss every real request. A mapping whose
    keys are not strings is ignored rather than raising.
    """

    if not isinstance(headers, Mapping):
        return ""
    wanted = name.lower()
    for key, value in headers.items():
        if isinstance(key, str) and key.lower() == wanted and isinstance(value, str):
            if value.strip():
                return value.strip()
    return ""


# --------------------------------------------------------------------------- #
# Back-pressure and throttling
# --------------------------------------------------------------------------- #


def retry_after_seconds(subscription: Mapping[str, Any] | None = None) -> int:
    """How many seconds to tell a client to wait.

    The vendor's sentence is "Retry after the indicated number of seconds" and the
    specification marks no number, so the default is derived and recorded as
    DERIVED_RETRY_AFTER_SECONDS. A subscription may raise it, because the room knows its
    own vault and the vendor does not.

    The floor is one second. A ``Retry-After: 0`` tells a client to retry immediately,
    which is a busy loop against an endpoint that has already said it is not ready.
    """

    raw = 0
    if isinstance(subscription, Mapping):
        raw = subscription.get("retry_after_seconds") or 0
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 0
    return max(1, value or vocab.RETRY_AFTER_SECONDS)


def is_back_pressure(state: str) -> bool:
    """Is the PDF for this document still being produced?

    True only for :data:`~dsr.security_governance.evault_vocabulary.STATE_GENERATING`.
    A document still awaiting signatures is a refusal and a failed generation is a
    refusal, because in neither case is there work in progress to wait for.
    """

    return state == vocab.STATE_GENERATING


def artifact_ready(state: str) -> bool:
    """Does this state have a PDF in the e-vault that can be served?"""

    return state in vocab.ARTIFACT_READY_STATES


def within_window(at: Any, now: datetime, window_seconds: int) -> bool:
    """Is this attempt inside the throttle window that ends at ``now``?

    The window is half-open and the boundary is inclusive of ``now``: an attempt stamped
    exactly ``window_seconds`` ago still counts. A boundary that excluded it would let a
    client issue one extra call per window, which is the number a limit exists to stop.
    """

    moment = parse_stamp(at)
    if moment is None:
        return False
    return moment >= now - timedelta(seconds=window_seconds)


def counted_attempts(rows: Sequence[Mapping[str, Any]], now: datetime) -> int:
    """How many retrievals counted against the throttle limit.

    Only the outcomes the vendor answered count. A throttled attempt must not count, and
    neither must one refused by the environment gate: if a refused call incremented the
    counter, a client that kept retrying would extend the window that refused it and never
    recover. A limit that a client cannot escape is a lockout wearing a limit's name.
    """

    return sum(
        1
        for row in rows
        if row.get("outcome") in vocab.VENDOR_ANSWERED_OUTCOMES
        and within_window(row.get("at"), now, vocab.THROTTLE_WINDOW_SECONDS)
    )


def retry_after_from_window(rows: Sequence[Mapping[str, Any]], now: datetime) -> int:
    """How long until the oldest counted attempt leaves the window.

    Returns the remaining seconds, or the default when nothing is counted. Derived rather
    than fixed, because a client told to wait a constant when the window has four seconds
    left waits five seconds too long, and the specification calls the vendor's signal a
    back-pressure mechanism rather than a suggestion.
    """

    oldest: datetime | None = None
    for row in rows:
        if row.get("outcome") not in vocab.VENDOR_ANSWERED_OUTCOMES:
            continue
        moment = parse_stamp(row.get("at"))
        if moment is None:
            continue
        if oldest is None or moment < oldest:
            oldest = moment
    if oldest is None:
        return vocab.RETRY_AFTER_SECONDS
    remaining = math_ceil_seconds((now - oldest).total_seconds())
    return max(1, vocab.THROTTLE_WINDOW_SECONDS - remaining + 1)


def math_ceil_seconds(value: float) -> int:
    """Round a positive duration up to whole seconds, never below zero.

    Named rather than inlined because the throttle arithmetic has to round the same way in
    both directions: a window that rounds down would expire early, and one that rounds up
    would hold a client past the limit.
    """

    if value <= 0:
        return 0
    return int(value) if float(value).is_integer() else int(value) + 1


# --------------------------------------------------------------------------- #
# The artifact
# --------------------------------------------------------------------------- #


def build_pdf(title: str, lines: Sequence[str]) -> bytes:
    """A minimal, valid PDF whose bytes are a pure function of its arguments.

    The specification calls the sealed artifact "digitally sealed, verifiable" and says
    ``/download-protected`` "always returns the same digitally sealed PDF file". Both
    halves of that claim have to be checkable rather than asserted, and they are only
    checkable if the bytes are reproducible. So the builder takes its text and returns
    bytes, with a correct cross-reference table and byte offsets, and the test asserts
    that two calls with the same arguments produce identical bytes and that a watermark
    changes them.

    The text is ASCII. A PDF string literal is bytes, and a byte that depends on an
    encoding choice is a byte whose stability this module would then be claiming without
    evidence. Non-ASCII is transliterated to a space rather than escaped.
    """

    body_lines = [_ascii_line(title, "Untitled")]
    body_lines.extend(_ascii_line(str(line), "") for line in lines)

    content = _content_stream(body_lines)
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        b"<< /Length "
        + str(len(content)).encode("ascii")
        + b" >>\nstream\n"
        + content
        + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode("ascii") + obj + b"\nendobj\n"

    xref_offset = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("ascii")
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n".encode("ascii")
    out += f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    return bytes(out)


def _ascii_line(value: str, fallback: str) -> str:
    """One line of ASCII text, with the characters a PDF string cannot hold removed."""

    cleaned = "".join(char if 32 <= ord(char) < 127 else " " for char in value)
    cleaned = " ".join(cleaned.split())
    return cleaned or fallback


def _escape(text: str) -> bytes:
    """Escape a PDF string literal.

    Backslash, both parentheses and the control characters. The builder already removes
    non-ASCII, but escaping is not optional: the text is caller-supplied through the
    watermark field, and an unescaped ``)`` would close the string and change the file.
    """

    out = bytearray()
    for char in text:
        if char in ("\\", "(", ")"):
            out += b"\\" + char.encode("ascii")
        elif ord(char) < 32:
            out += b" "
        else:
            out += char.encode("ascii")
    return bytes(out)


def _content_stream(lines: Sequence[str]) -> bytes:
    """One page of text, 12pt Helvetica, 72pt from the left and top."""

    out = bytearray()
    y = 780
    for line in lines:
        out += (
            b"BT /F1 12 Tf 72 " + str(y).encode("ascii") + b" Td (" + _escape(line) + b") Tj ET\n"
        )
        y -= 18
    return bytes(out)


def sha256_hex(payload: bytes | str) -> str:
    """The SHA-256 digest of the artifact, lower-case hex.

    Over the bytes, not over a base64 copy of them, so the digest a caller computes from
    the response it received matches the digest this room recorded. That equality is the
    only verification this build claims, and it is why SEAL_SCOPE says "the file has not
    changed" rather than "the signature is valid".
    """

    raw = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
    return hashlib.sha256(raw).hexdigest()


def artifact_document(document_id: str, variant: str, watermark: str = "") -> list[str]:
    """The text lines a generated artifact carries.

    Both variants print the same first two lines, which is what makes the trade-off
    legible in the artifact itself: the document id and the variant are stated on the
    file, so a reader holding two files can see which is which without a database. The
    plain variant then adds the watermark line, and the sealed variant adds the sentence
    that says it is byte-stable.
    """

    lines = [
        "Executed agreement",
        f"Document: {document_id}",
        f"Variant: {variant}",
    ]
    if variant == vocab.VARIANT_PLAIN:
        lines.append(f"Watermark: {watermark}" if watermark else "Watermark: none")
        lines.append("This copy is not sealed. The bytes change with the watermark.")
    else:
        lines.append("Digitally sealed. The same bytes are returned for every request.")
    return lines


def artifact_digest(document_id: str, variant: str, watermark: str = "") -> str:
    """The digest the artifact for these arguments will have.

    Computed rather than stored twice, so the digest a response carries and the digest of
    the bytes the response carries cannot drift apart.
    """

    return sha256_hex(build_pdf(document_id, artifact_document(document_id, variant, watermark)))


def describe_variant(variant: str) -> dict[str, Any]:
    """One variant as the vocabulary states it, with the two booleans derived from the
    rules rather than restated here."""

    return {
        "variant": variant,
        "endpoint": vocab.VARIANT_ENDPOINTS[variant],
        "summary": vocab.VARIANT_SUMMARIES[variant],
        "byte_stable": byte_stable(variant),
        "watermarkable": watermarkable(variant),
        "environments": (
            [vocab.SEALED_ENVIRONMENT]
            if variant == vocab.VARIANT_SEALED
            else list(vocab.ENVIRONMENTS)
        ),
    }


def byte_stable(variant: str) -> bool:
    """Does this variant return the same bytes for the same document, every time?"""

    return vocab.byte_stable(variant)


def watermarkable(variant: str) -> bool:
    """Can a watermark or branding be applied to this variant?"""

    return vocab.watermarkable(variant)


# --------------------------------------------------------------------------- #
# The vocabulary, as data
# --------------------------------------------------------------------------- #


def vocabulary() -> dict[str, Any]:
    """Every published term, served so a client cannot disagree with the validator."""

    return {
        "ticket": "WF-080",
        "trigger": vocab.PDF_READY_TRIGGER,
        "required_triggers": list(vocab.REQUIRED_TRIGGERS),
        "event_keys": list(vocab.EVENT_KEYS),
        "dedupe_header": vocab.DEDUPE_HEADER,
        "delivery_id_keys": list(vocab.DELIVERY_ID_KEYS),
        "environments": list(vocab.ENVIRONMENTS),
        "sealed_environment": vocab.SEALED_ENVIRONMENT,
        "sandbox_remedy": vocab.SANDBOX_REMEDY,
        "variants": [describe_variant(name) for name in vocab.VARIANTS],
        "variant_tradeoff": vocab.VARIANT_TRADEOFF,
        "document_states": [
            {
                "state": name,
                "label": vocab.STATE_LABELS[name],
                "artifact_ready": artifact_ready(name),
                "back_pressure": is_back_pressure(name),
            }
            for name in vocab.DOCUMENT_STATES
        ],
        "fetch_outcomes": [
            {"outcome": name, "summary": vocab.OUTCOME_SUMMARIES[name]}
            for name in vocab.FETCH_OUTCOMES
        ],
        "vendor_answered_outcomes": list(vocab.VENDOR_ANSWERED_OUTCOMES),
        "retry_after_header": vocab.RETRY_AFTER_HEADER,
        "retry_after_seconds": vocab.RETRY_AFTER_SECONDS,
        "media_type": vocab.PDF_MEDIA_TYPE,
        "throttle": {
            "window_seconds": vocab.THROTTLE_WINDOW_SECONDS,
            "limit": vocab.THROTTLE_LIMIT,
            "status": vocab.STATUS_THROTTLED,
            "code": vocab.OUTCOME_THROTTLED,
        },
        "statuses": {
            "ready": vocab.STATUS_READY,
            "accepted": vocab.STATUS_ACCEPTED,
            "sandbox": vocab.STATUS_SANDBOX,
            "throttled": vocab.STATUS_THROTTLED,
        },
        "collections": list(vocab.ALL_COLLECTIONS),
        vocab.EFFECT_FIELD: vocab.EFFECT,
        vocab.TRADEOFF_FIELD: vocab.VARIANT_TRADEOFF,
        vocab.SEAL_SCOPE_FIELD: vocab.SEAL_SCOPE,
        vocab.NO_POLLING_FIELD: vocab.NO_POLLING,
    }
