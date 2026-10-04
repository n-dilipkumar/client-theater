"""WF-070: require NDA acceptance before a viewer sees the room.

The researched specification for this workflow is
``docs/research/digital-sales-room-workflows/wf/WF-070.md``. It is a gate on the
same ``Link`` row WF-069 gates, so it lives in this package and reuses WF-069's
vocabulary rather than standing up a second link model beside it.

What the research fixes, and what it leaves open
------------------------------------------------

Fixed, and implemented exactly as stated:

* ``enable_agreement`` / ``agreement_id`` are fields on the link. The gate is a
  property of the link, so two links can point at two different NDAs for two
  different buyers.
* ``agreement_id`` is "Required when ``enable_agreement`` is true", so enabling
  the gate without an agreement is refused rather than accepted and enforced
  against nothing.
* The CLI collapses the pair: "``--agreement <id>`` ... The single flag both
  enables the gate and sets the agreement id." :func:`normalize_gate` implements
  that collapse, so a caller may send ``agreement`` alone.
* "Before any document renders, is shown the NDA and must accept it. Only after
  acceptance does the room's content load." :meth:`AgreementEngine.content` is the
  only route that releases content, and it refuses without a matching acceptance.
* "Rep can later turn the gate on/off", so the update is tri-state: absent leaves
  the setting alone, a boolean sets it, and an explicit ``null`` clears the
  agreement id.

Left open by the research, and decided here, with the decision named
------------------------------------------------------------------

The issue marks three of these ``[inferred]``. Each one is a real decision, so
each is recorded rather than buried:

**Where an acceptance lives.** "The buyer's acceptance is recorded against the
viewer's session." Taken literally, which means this workflow mints its own
viewer session (:data:`SESSION_COLLECTION`), and an acceptance names that
session. The alternative - putting the acceptance straight on the link - was
rejected: it would make "accepted" a property of the link rather than of a
person who opened it, so the second reader of a forwarded URL would inherit the
first reader's acceptance. A link nobody has opened is not accepted by anyone.

**What an Agreement is.** "No Agreement resource is published", so the NDA text
had to be modelled. :data:`AGREEMENT_COLLECTION` holds it, with a body, a
title, an optional governing law and an optional effective date. Inventing an
e-signature provider would have been fiction, and the research does not ask for
one, so acceptance here is a recorded act by the viewer, not a signature.

**What the acceptance is bound to.** This is the rule that matters, and it is
the one a reviewer should check hardest: an acceptance names the digest of the
text the viewer actually saw, and content is released only against an acceptance
whose digest still matches the agreement. So editing the NDA after a buyer
accepted it does not silently grandfather that buyer into the new terms, and
pointing a link at a different agreement does not release it either. The
alternative - matching on ``agreement_id`` alone - was rejected because it makes
a seller's edit to their own legal text the one change that quietly weakens a
gate already in force.

What this module does not decide
--------------------------------

No e-signature, no countersignature, no PDF generation and no plan gating. The
research says "Available on the Data Rooms plan and above" without publishing
what the plan gate reads, and guessing a billing check would mean writing a rule
nobody researched. :func:`vocabulary` serves that as an explicit
``not_implemented`` list rather than leaving a reviewer to guess whether the
absence was an oversight.

Imports: the store, and this package's own pure rules. No framework, no
``dsr.api``, no SQLite handle, so every rule below is exercised without a server.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Callable

from dsr.link_gating import rules, secrets as link_secrets
from dsr.store import RecordStore

# --------------------------------------------------------------------------- #
# Collections. Namespaced per ticket, because every feature shares one
# `records` table and `find()` matches on collection before anything else.
# --------------------------------------------------------------------------- #

#: The NDA itself: the text a viewer is shown and accepts.
AGREEMENT_COLLECTION = "wf070_agreement"

#: A viewer who has opened a gated link. The acceptance below names one of these.
SESSION_COLLECTION = "wf070_viewer_session"

#: A recorded acceptance, bound to a viewer session and to a text digest.
ACCEPTANCE_COLLECTION = "wf070_acceptance"

# --------------------------------------------------------------------------- #
# The gate fields on the link row.
#
# Not copied into WF-069's module on purpose. `dsr.link_gating.rules` already
# lists both names in PRESET_COVERED_FIELDS - quoted verbatim from the research's
# extensibility note - and WF-069 carries preset-controlled fields it does not
# itself enforce verbatim onto the link. So a link seeded from a governed
# baseline already arrives with `enable_agreement` and `agreement_id` in its
# payload, stored and reported by WF-069 and enforced by nothing. This module is
# what gives those two fields meaning, which is the whole of WF-070.
# --------------------------------------------------------------------------- #

#: The two names, taken from the preset-covered list so the vocabulary this
#: module serves cannot drift from the vocabulary WF-069 already publishes.
ENABLE_FIELD = "enable_agreement"
AGREEMENT_REF_FIELD = "agreement_id"

#: CLI flag table: "`--agreement <id>` | none | Require viewers to accept this
#: agreement (NDA) before viewing." The default is none, so the gate is off until
#: a rep turns it on.
DEFAULT_ENABLE_AGREEMENT = False

#: The convenience flag's name, which does both jobs at once.
CONVENIENCE_FLAG = "agreement"

# --------------------------------------------------------------------------- #
# The states a viewer is in.
# --------------------------------------------------------------------------- #

#: The NDA is shown and nothing else is. No content has been released.
STATE_AGREEMENT = "agreement"

#: Content is released. Either the gate is off, or this viewer accepted.
STATE_OPEN = "open"

#: The link expired or was revoked. One wording for both, so a viewer holding a
#: forwarded URL cannot tell "this deal closed" from "you were cut off". The
#: boundary rule itself is WF-069's: `expiry_state` compares `now > moment`.
STATE_CLOSED = "closed"

#: Every state, in the order a viewer meets them.
STATE_SEQUENCE = (STATE_AGREEMENT, STATE_OPEN)

# --------------------------------------------------------------------------- #
# Errors.
#
# All three are declared here and raised by nothing else in the product. WF-069
# already maps `rules.GateError`, `rules.GateDenied` and `rules.LinkNotFound`,
# and the host refuses a second feature registering a handler for the same type.
# These are distinct types on purpose, and none of them is a builtin: a handler
# for `ValueError` or `PermissionError` would intercept those across the whole
# product.
# --------------------------------------------------------------------------- #


class AgreementError(ValueError):
    """An agreement or a gate setting this workflow will not accept.

    Carries a field-keyed map, because a rep filling in a form needs the message
    beside the input that caused it. Same shape as `dsr.link_gating.rules.GateError`,
    and rendered as ``errors`` by the HTTP layer.
    """

    def __init__(self, message: str, errors: Mapping[str, str] | None = None) -> None:
        super().__init__(message)
        self.errors: dict[str, str] = dict(errors or {})


class AgreementDenied(PermissionError):
    """A viewer the gate will not let through.

    ``reason`` is a stable machine token, so the frontend can tell "you have not
    accepted yet" from "this link has expired" without string-matching an English
    sentence.
    """

    #: Content asked for before this viewer accepted.
    REASON_NOT_ACCEPTED = "agreement_not_accepted"

    #: The gate is on and this request presented no viewer session at all.
    REASON_SESSION_REQUIRED = "viewer_session_required"

    #: A session was presented that this link did not mint.
    REASON_SESSION_UNKNOWN = "viewer_session_unknown"

    #: The viewer accepted an earlier text, or a different agreement, and the
    #: link now points at something else. Named separately from NOT_ACCEPTED
    #: because the remedy differs: NOT_ACCEPTED means "read and click", this one
    #: means "your acceptance no longer covers what you would be reading".
    REASON_SUPERSEDED = "agreement_changed"

    #: The gate is on and names an agreement that is not there. Fails closed.
    REASON_UNAVAILABLE = "agreement_unavailable"

    #: Expired or revoked. One sentence for both, by name rather than by a second
    #: literal that could drift from WF-069's.
    REASON_CLOSED = "link_closed"

    _CLOSED = "This link has expired. Ask the sender for a new one."

    MESSAGES = {
        REASON_NOT_ACCEPTED: "Read the agreement and accept it to continue.",
        REASON_SESSION_REQUIRED: "Open the link to start a pass through the gate.",
        REASON_SESSION_UNKNOWN: "Open the link again to start a new pass through the gate.",
        REASON_SUPERSEDED: "The agreement changed. Read the current version and accept it.",
        REASON_UNAVAILABLE: "The agreement for this link is not available. Ask the sender.",
        REASON_CLOSED: _CLOSED,
    }

    def __init__(self, reason: str) -> None:
        super().__init__(self.MESSAGES.get(reason, "This link could not be opened."))
        self.reason = reason


class AgreementNotFound(LookupError):
    """No such agreement, or no such link, or neither was ever this workflow's.

    Its own type rather than the store's ``RecordNotFound``, because a feature may
    only map error types it raises itself: registering a handler for a shared type
    would intercept that exception across the whole product.
    """


# --------------------------------------------------------------------------- #
# Text digest
# --------------------------------------------------------------------------- #


def body_digest(body: Any) -> str:
    """A fingerprint of the agreement text a viewer is shown.

    Whitespace is collapsed before hashing, and that is a decision rather than an
    oversight. A re-wrap of the same paragraphs is not a change to what the viewer
    agreed to, and treating it as one would make an acceptance quietly expire on a
    cosmetic edit, which trains a seller to stop trusting the gate. Every other
    edit changes the digest and so re-opens the gate, which is the direction that
    matters: too many acceptances to re-collect is a nuisance, and too few is a
    disclosure.
    """
    normalised = " ".join(str(body or "").split())
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


def acceptance_covers(acceptance: Mapping[str, Any], agreement_id: Any, body: Any) -> bool:
    """Does this acceptance cover the agreement as it stands right now?

    Both halves matter. The id guards against a link being pointed at a different
    agreement, and the digest guards against the text of *this* agreement being
    edited. Either one alone leaves a hole: id-only misses the edit, and
    digest-only would let an unrelated agreement whose text happens to match
    release the content.

    ``agreement_id`` is passed in rather than read off a record, because the id
    lives in the record *envelope* and not in ``data``. Reading it from the payload
    would yield ``None``, and every comparison would be against ``None``.
    """
    if not acceptance:
        return False
    if acceptance.get("agreement_id") != agreement_id:
        return False
    return acceptance.get("body_digest") == body_digest(body)


# --------------------------------------------------------------------------- #
# Gate settings
# --------------------------------------------------------------------------- #


def as_bool(value: Any, field: str) -> bool:
    """Accept a boolean, or the ``on``/``off`` the CLI documents.

    CLI update table: "`--enable-agreement <on|off>` | Toggle the agreement (NDA)
    gate." The third state is *absent*, and :func:`normalize_gate` is where that is
    honoured; this function only turns the two present ones into a bool.
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
    raise AgreementError(f"{field} must be a boolean", {field: "Use true or false."})


def normalize_gate(
    payload: Mapping[str, Any],
    *,
    base: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate and normalise the agreement gate for a create or an update.

    ``base`` is the link's current gate, so a field the payload does not mention
    is carried over rather than defaulted. That is what makes the tri-state update
    work: ``{}`` changes nothing, ``{"enable_agreement": true}`` turns the gate on,
    and ``{"agreement_id": null}`` clears the selection without touching the flag.

    The convenience flag comes first, because "the single flag both enables the
    gate and sets the agreement id" means sending ``agreement`` alone is a
    complete request, and a caller who also sent ``enable_agreement`` explicitly
    still gets what they asked for.
    """
    body = dict(payload or {})
    current = dict(base or {})
    errors: dict[str, str] = {}

    if CONVENIENCE_FLAG in body and body[CONVENIENCE_FLAG] is not None:
        body[AGREEMENT_REF_FIELD] = body[CONVENIENCE_FLAG]
        if ENABLE_FIELD not in body:
            body[ENABLE_FIELD] = True

    gate: dict[str, Any] = {
        ENABLE_FIELD: bool(current.get(ENABLE_FIELD, DEFAULT_ENABLE_AGREEMENT)),
        AGREEMENT_REF_FIELD: current.get(AGREEMENT_REF_FIELD),
    }

    if ENABLE_FIELD in body:
        try:
            gate[ENABLE_FIELD] = as_bool(body[ENABLE_FIELD], ENABLE_FIELD)
        except AgreementError as exc:
            errors.update(exc.errors)

    if AGREEMENT_REF_FIELD in body:
        given = body[AGREEMENT_REF_FIELD]
        if given is None:
            gate[AGREEMENT_REF_FIELD] = None
        elif isinstance(given, str):
            cleaned = given.strip()
            if not cleaned:
                # An empty string is not "no agreement". It is the one value that
                # could never have been an id, and reading it as "clear" would let
                # a form that submits an empty box silently ungate a link.
                errors[AGREEMENT_REF_FIELD] = "Choose an agreement, or send null to clear it."
            else:
                gate[AGREEMENT_REF_FIELD] = cleaned
        else:
            errors[AGREEMENT_REF_FIELD] = "Give the id of an agreement."

    if gate[ENABLE_FIELD] and not gate[AGREEMENT_REF_FIELD]:
        # "Required when enable_agreement is true." Enforced here so no caller can
        # end up with a gate on and nothing to accept, which would be a link that
        # looks gated and releases everything.
        errors[AGREEMENT_REF_FIELD] = "An agreement gate needs an agreement to accept."

    if errors:
        raise AgreementError("the agreement gate could not be read", errors)
    return gate


def gate_required(link: Mapping[str, Any]) -> bool:
    """Does this link ask the viewer to accept an agreement?

    Only the flag. A link may keep ``agreement_id`` selected while the gate is
    off - that is how a rep pauses a gate mid-deal without losing the selection,
    and it is why ``gate_required`` does not look at the id.
    """
    return bool(link.get(ENABLE_FIELD))


def gate_projection(link: Mapping[str, Any], agreement: Mapping[str, Any] | None) -> dict[str, Any]:
    """The gate as a safe-to-display projection, for the seller's link read.

    ``agreement_ok`` is false when the gate is on and names an agreement this
    workflow cannot resolve. That is the fail-closed state, and a seller has to be
    able to see it: a link that looks gated and releases everything is the worst
    state this workflow can be in, and it is invisible from the flag alone.
    """
    required = gate_required(link)
    return {
        "enabled": required,
        "agreement_id": link.get(AGREEMENT_REF_FIELD),
        "agreement_title": (agreement or {}).get("title"),
        "agreement_ok": (not required) or agreement is not None,
        "fields": [ENABLE_FIELD, AGREEMENT_REF_FIELD],
    }


# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #

#: Agreement kinds this workflow names. The research only ever says "agreement
#: (NDA)", so this is a two-value vocabulary and not a taxonomy.
KIND_NDA = "nda"
KIND_AGREEMENT = "agreement"
AGREEMENT_KINDS = (KIND_NDA, KIND_AGREEMENT)

DEFAULT_AGREEMENT_KIND = KIND_NDA


def validate_agreement(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a new agreement and return the payload to store.

    A title and a body are both required. An agreement with no text is not
    something a viewer can accept, and storing one would produce a gate that shows
    an empty box and records an acceptance of nothing.
    """
    if not isinstance(payload, Mapping):
        raise AgreementError("an agreement is an object", {"body": "Send a JSON object."})

    errors: dict[str, str] = {}
    title = str(payload.get("title") or "").strip()
    if not title:
        errors["title"] = "Name the agreement, e.g. Northwind mutual NDA."

    body = payload.get("body")
    if not isinstance(body, str) or not body.strip():
        errors["body"] = "Paste the agreement text the viewer will read."

    kind = str(payload.get("kind") or DEFAULT_AGREEMENT_KIND).strip().lower()
    if kind not in AGREEMENT_KINDS:
        errors["kind"] = f"Use one of: {', '.join(AGREEMENT_KINDS)}."

    if errors:
        raise AgreementError("the agreement could not be read", errors)

    stored: dict[str, Any] = {
        "title": title,
        "body": body,
        "body_digest": body_digest(body),
        "kind": kind,
        "version": 1,
        "governing_law": str(payload.get("governing_law") or "").strip() or None,
        "effective_date": str(payload.get("effective_date") or "").strip() or None,
    }
    return stored


def agreement_changes(payload: Mapping[str, Any], *, current: Mapping[str, Any]) -> dict[str, Any]:
    """The patch for an update to an existing agreement.

    A closed field list, and the reason is the same as it is in WF-069: an update
    is a merge patch over the whole payload, so an open list would let an edit
    rewrite the digest or the version as a side effect of changing the title.

    ``version`` and ``body_digest`` move together, and only when the body really
    changed. A rep fixing a typo in the title must not re-open a gate for a buyer
    who already accepted, and a rep who really did change the terms must.
    """
    errors: dict[str, str] = {}
    patch: dict[str, Any] = {}

    if "title" in payload:
        title = str(payload.get("title") or "").strip()
        if not title:
            errors["title"] = "Name the agreement."
        else:
            patch["title"] = title

    if "kind" in payload:
        kind = str(payload.get("kind") or "").strip().lower()
        if kind not in AGREEMENT_KINDS:
            errors["kind"] = f"Use one of: {', '.join(AGREEMENT_KINDS)}."
        else:
            patch["kind"] = kind

    if "governing_law" in payload:
        patch["governing_law"] = str(payload.get("governing_law") or "").strip() or None

    if "effective_date" in payload:
        patch["effective_date"] = str(payload.get("effective_date") or "").strip() or None

    if "body" in payload:
        body = payload.get("body")
        if not isinstance(body, str) or not body.strip():
            errors["body"] = "Paste the agreement text the viewer will read."
        elif body_digest(body) != body_digest(current.get("body")):
            # Only a real change to the text moves the version, and that is the
            # whole point: the version is what a viewer accepted against.
            patch["body"] = body
            patch["body_digest"] = body_digest(body)
            patch["version"] = int(current.get("version") or 1) + 1

    if errors:
        raise AgreementError("the agreement could not be updated", errors)
    return patch


def vocabulary() -> dict[str, Any]:
    """The vocabulary this workflow enforces, so the UI need not hard-code it."""
    return {
        "fields": {
            ENABLE_FIELD: {
                "default": DEFAULT_ENABLE_AGREEMENT,
                "type": "boolean",
                "cli": "--enable-agreement on|off",
                "summary": "Require viewers to accept an agreement (NDA) before viewing.",
            },
            AGREEMENT_REF_FIELD: {
                "default": None,
                "type": "string | null",
                "cli": f"--{CONVENIENCE_FLAG} <id>",
                "summary": "ID of the agreement viewers must accept. Required when the gate is on.",
            },
        },
        "convenience_flag": {
            "field": CONVENIENCE_FLAG,
            "enables": ENABLE_FIELD,
            "sets": AGREEMENT_REF_FIELD,
            "summary": "One flag that both enables the gate and sets the agreement id.",
        },
        "states": list(STATE_SEQUENCE) + [STATE_CLOSED],
        "kinds": list(AGREEMENT_KINDS),
        "acceptance_binds_to": "agreement_id and a digest of the text shown",
        "not_implemented": [
            "No e-signature and no countersignature. The research names an "
            "Agreement object and an acceptance, and never a signature provider, so "
            "acceptance here is a recorded act by the viewer.",
            "No plan gate. OpenAPI says 'Available on the Data Rooms plan and above' "
            "without publishing what the plan is read from.",
            "No expiry or withdrawal of an acceptance. The research states none, and a "
            "retention period is a privacy decision nobody has made.",
            "No agreement revision history as its own record. The version moves on the "
            "agreement row and prior acceptances keep the version they accepted.",
        ],
    }


# --------------------------------------------------------------------------- #
# The engine
# --------------------------------------------------------------------------- #


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AgreementEngine:
    """Every read and write this workflow makes, in one place.

    The store is the only thing it touches. There is no SQLite handle and no HTTP
    object here, so the whole gate is exercised in tests without a server.
    """

    def __init__(self, store: RecordStore, *, now: Callable[[], datetime] | None = None) -> None:
        self.store = store
        self._now = now or _utcnow

    # -- helpers ------------------------------------------------------------ #

    def _stamp(self) -> str:
        return self._now().isoformat(timespec="milliseconds")

    @staticmethod
    def _data(record: Mapping[str, Any]) -> dict[str, Any]:
        return dict(record.get("data") or {})

    def _link(self, link_id: Any) -> dict[str, Any]:
        """Fetch the link this gate hangs off, or raise :class:`AgreementNotFound`.

        Read through ``store.db`` rather than ``store.get`` for one reason, and
        WF-069 hit the same one: ``RecordStore.get`` does not forward
        ``include_deleted``, so a revoked link came back as ``None`` and looked
        like an id that never existed. This workflow needs the difference, because
        a revoked link must answer with the closed page rather than a 404 that
        tells the viewer the link was real once. ``store.db`` is the same audited
        wrapper underneath and this is a read, so nothing about the audit
        guarantee changes.
        """
        record = None
        if isinstance(link_id, str) and link_id:
            try:
                record = self.store.db.get(link_id, include_deleted=True)
            except Exception:  # the store's own miss, or a malformed id
                record = None
        if record is None or record.get("collection") != rules.LINK_COLLECTION:
            raise AgreementNotFound(str(link_id))
        return record

    def _open_link(self, link_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        """A link a viewer may still be walking through.

        Revocation and expiry are evaluated here, on this request, not once at
        creation. The expiry comparison is WF-069's boundary rule, reused rather
        than reimplemented so the two gates cannot disagree about the instant a
        link closes.
        """
        record = self._link(link_id)
        data = self._data(record)
        if record.get("deleted_at") is not None:
            raise AgreementDenied(AgreementDenied.REASON_CLOSED)
        expired, _reason, _moment = rules.expiry_state(data.get("expires_at"), self._now())
        if expired:
            raise AgreementDenied(AgreementDenied.REASON_CLOSED)
        return record, data

    def _agreement(self, agreement_id: Any) -> dict[str, Any]:
        record = None
        if isinstance(agreement_id, str) and agreement_id:
            try:
                record = self.store.get(agreement_id)
            except Exception:
                record = None
        if record is None or record.get("collection") != AGREEMENT_COLLECTION:
            raise AgreementNotFound(str(agreement_id))
        return record

    def _agreement_or_none(self, link: Mapping[str, Any]) -> dict[str, Any] | None:
        """The agreement this link points at, or ``None`` if it cannot be resolved.

        ``None`` is not an error here. It is the fail-closed state: the gate is on
        and names something that is not there, so content does not move, and
        :func:`gate_projection` reports ``agreement_ok: false`` so the seller sees
        why on the board.
        """
        if not gate_required(link):
            return None
        try:
            return self._agreement(link.get(AGREEMENT_REF_FIELD))
        except AgreementNotFound:
            return None

    @staticmethod
    def _agreement_view(record: Mapping[str, Any], *, include_body: bool = True) -> dict[str, Any]:
        data = AgreementEngine._data(record)
        view = {
            "id": record["id"],
            "room_id": rules.room_ref_of(data, record),
            "title": data.get("title"),
            "kind": data.get("kind"),
            "governing_law": data.get("governing_law"),
            "effective_date": data.get("effective_date"),
            "version": data.get("version"),
            "body_digest": data.get("body_digest"),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            "characters": len(str(data.get("body") or "")),
        }
        if include_body:
            view["body"] = data.get("body")
        return view

    # -- agreements --------------------------------------------------------- #

    def create_agreement(
        self,
        room_id: str,
        payload: Mapping[str, Any],
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Write an NDA a viewer can be shown and accept.

        ``source`` is required and comes from the route. A domain function that
        hardcodes a URL as the source of a write leaves the audit log naming a
        route the app stopped serving, which is the recurring defect the contract
        names.
        """
        if not isinstance(room_id, str) or not room_id:
            raise AgreementError("an agreement belongs to a room", {"room_id": "Room is required."})
        stored = validate_agreement(payload)
        record = self.store.create(
            AGREEMENT_COLLECTION,
            # `room_ref` is the payload-side twin of the envelope's `room_id`, and
            # for the same reason as in WF-069: `room_id` is stripped out of `data`
            # before the dynamic index is built, so an agreement that stored its
            # room there would be unfilterable by `find()`.
            {**stored, rules.ROOM_REF: room_id, "created_at": self._stamp()},
            room_id=room_id,
            actor=actor,
            source=source,
        )
        return self._agreement_view(record)

    def list_agreements(self, room_id: str | None = None) -> list[dict[str, Any]]:
        where = {rules.ROOM_REF: room_id} if room_id else {}
        rows = self.store.find(AGREEMENT_COLLECTION, where, limit=200)
        # Bodies are omitted from the list: a board wants titles and versions, and
        # shipping every NDA's full text to render a list is how legal text ends up
        # in a browser cache nobody chose to put it in.
        return [self._agreement_view(row, include_body=False) for row in rows]

    def read_agreement(self, agreement_id: str) -> dict[str, Any]:
        return self._agreement_view(self._agreement(agreement_id))

    def update_agreement(
        self,
        agreement_id: str,
        changes: Mapping[str, Any],
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Edit an agreement. Changing the text moves its version.

        Prior acceptances are kept, not deleted. They name the version and the
        digest they were given under, so the record of who accepted what survives
        the edit while the gate re-opens for anyone who has not accepted the
        current text.
        """
        record = self._agreement(agreement_id)
        patch = agreement_changes(changes, current=self._data(record))
        if not patch:
            # An update that changes nothing is not an error, but it must still
            # return the current state rather than a half-applied one.
            return self._agreement_view(record)
        patch["updated_at"] = self._stamp()
        updated = self.store.update(record["id"], patch, actor=actor, source=source)
        return self._agreement_view(updated)

    def delete_agreement(
        self, agreement_id: str, *, source: str, actor: str | None = None
    ) -> dict[str, Any]:
        """Retire an agreement, and report the links that now fail closed.

        A soft delete, for WF-069's reason: "Revocation is expiry, not
        disappearance." The text and every acceptance of it survive, so the record
        of who accepted what is still answerable after the NDA is retired.

        The links that named this agreement keep naming it. They are reported here
        rather than silently repaired, because a gate that quietly opened itself
        would be the one change nobody could audit - and because the seller has to
        decide whether those links should point at a different NDA or stop asking.
        :meth:`content` fails closed for all of them from the very next request.
        """
        record = self._agreement(agreement_id)
        data = self._data(record)
        deleted = self.store.delete(record["id"], actor=actor, source=source)
        room_id = rules.room_ref_of(data, record)
        affected = [
            gate["id"]
            for gate in self.list_gates(room_id)
            if gate["gate"]["agreement_id"] == record["id"]
        ]
        return {
            "id": record["id"],
            "room_id": room_id,
            "title": data.get("title"),
            "retired": True,
            "retired_at": deleted.get("updated_at") or self._stamp(),
            "gates_now_closed": affected,
        }

    # -- the gate on a link ------------------------------------------------- #

    def set_gate(
        self,
        link_id: str,
        changes: Mapping[str, Any],
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Turn the agreement gate on or off, or point it at another NDA.

        Mirrors ``PATCH /v1/links/{id}``. The settings live on the link row, which
        is where the research says they live, so this is a merge patch on the same
        payload WF-069 writes. Only the two agreement fields are touched, and they
        are written explicitly rather than merged in, so a stale value can never
        survive a clear.
        """
        record, data = self._open_link(link_id)
        gate = normalize_gate(changes, base=data)
        if gate[AGREEMENT_REF_FIELD]:
            # Refuse a gate pointed at nothing that exists. Catching it here means
            # the link never reaches the state where it looks gated and releases
            # everything, which is the failure this workflow exists to prevent.
            self._agreement(gate[AGREEMENT_REF_FIELD])

        if gate[ENABLE_FIELD] == bool(data.get(ENABLE_FIELD)) and gate[
            AGREEMENT_REF_FIELD
        ] == data.get(AGREEMENT_REF_FIELD):
            # Nothing changed, so nothing is written. An update that re-stamps a row
            # it did not change fills the audit log with no-ops and makes the log
            # harder to read than it needs to be.
            return self.link_gate(record["id"])

        patch: dict[str, Any] = {ENABLE_FIELD: gate[ENABLE_FIELD]}
        patch[AGREEMENT_REF_FIELD] = gate[AGREEMENT_REF_FIELD]
        patch["agreement_updated_at"] = self._stamp()
        updated = self.store.update(record["id"], patch, actor=actor, source=source)
        updated_data = self._data(updated)
        return {
            "id": record["id"],
            "room_id": rules.room_ref_of(updated_data, updated),
            "title": updated_data.get("title"),
            "gate": gate_projection(updated_data, self._agreement_or_none(updated_data)),
            "updated_at": updated.get("updated_at"),
        }

    def link_gate(self, link_id: str) -> dict[str, Any]:
        """Confirm the gate state. Mirrors ``GET /v1/links/{id}`` for these fields.

        A revoked link answers rather than 404s, so a seller can still see which
        NDA a withdrawn link was pointing at.
        """
        record = self._link(link_id)
        data = self._data(record)
        agreement = self._agreement_or_none(data)
        expired, reason, _moment = rules.expiry_state(data.get("expires_at"), self._now())
        revoked = record.get("deleted_at") is not None
        return {
            "id": record["id"],
            "room_id": rules.room_ref_of(data, record),
            "title": data.get("title"),
            "gate": gate_projection(data, agreement),
            "agreement": self._agreement_view(agreement, include_body=False) if agreement else None,
            "revoked": revoked,
            "revoked_at": record.get("deleted_at"),
            "expired": expired,
            "expiry_reason": reason,
            "updated_at": record.get("updated_at"),
        }

    def list_gates(self, room_id: str | None = None) -> list[dict[str, Any]]:
        """Every link's gate, for the seller's board."""
        where = {rules.ROOM_REF: room_id} if room_id else {}
        rows = self.store.find(rules.LINK_COLLECTION, where, limit=200)
        return [self.link_gate(row["id"]) for row in rows]

    # -- the viewer --------------------------------------------------------- #

    def open_link(self, link_id: str, *, source: str) -> dict[str, Any]:
        """The buyer's first request: mint a viewer session and serve the NDA.

        The session is minted on every open, so nothing a viewer proved on an
        earlier pass carries into this one, including a pass they cleared a minute
        ago. When the gate is off no session is needed to reach content, and none
        is written - a link with no gate should not accumulate rows.
        """
        record, data = self._open_link(link_id)
        if not gate_required(data):
            return {
                "link_id": record["id"],
                "title": data.get("title"),
                "state": STATE_OPEN,
                "gate_required": False,
                "session_id": None,
                "agreement": None,
                "message": "This link is open.",
            }

        agreement = self._agreement_or_none(data)
        if agreement is None:
            # Fails closed, and says so. A gate on with nothing to accept must not
            # fall through to open content.
            raise AgreementDenied(AgreementDenied.REASON_UNAVAILABLE)

        token = link_secrets.mint_view_token()
        session = self.store.create(
            SESSION_COLLECTION,
            {
                "link_id": record["id"],
                rules.ROOM_REF: rules.room_ref_of(data, record),
                "agreement_id": agreement["id"],
                "token_hash": link_secrets.hash_token(token),
                "accepted": False,
                "opened_at": self._stamp(),
            },
            actor="viewer",
            source=source,
        )
        return {
            "link_id": record["id"],
            "title": data.get("title"),
            "state": STATE_AGREEMENT,
            "gate_required": True,
            "session_id": session["id"],
            "session_token": token,
            "agreement": self._agreement_view(agreement),
            "message": "Read the agreement and accept it to continue.",
        }

    def _session(self, link_id: str, session: Any) -> dict[str, Any]:
        """The viewer session this request belongs to, or refuse.

        A session is bound to the link that minted it, so one link's acceptance
        cannot be presented to a different link's gate. The token is compared by
        hash: the cleartext exists only in the response that issued it, and is
        never written to a record, an audit row or a log.
        """
        if not isinstance(session, str) or not session:
            raise AgreementDenied(AgreementDenied.REASON_SESSION_REQUIRED)
        row = None
        try:
            row = self.store.get(session)
        except Exception:
            row = None
        if (
            row is None
            or row.get("collection") != SESSION_COLLECTION
            or self._data(row).get("link_id") != link_id
        ):
            raise AgreementDenied(AgreementDenied.REASON_SESSION_UNKNOWN)
        return row

    def accept(
        self,
        link_id: str,
        session: Any,
        payload: Mapping[str, Any],
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Record the viewer's acceptance and release the content.

        An explicit ``accepted`` flag is required. A request that merely arrives at
        this route has not accepted anything, and treating the arrival as consent
        would make the gate a checkbox the buyer never has to see.
        """
        record, data = self._open_link(link_id)
        if not gate_required(data):
            raise AgreementDenied(AgreementDenied.REASON_NOT_ACCEPTED)
        if (payload or {}).get("accepted") is not True:
            raise AgreementError(
                "acceptance must be explicit",
                {"accepted": "Accept the agreement to continue."},
            )

        session_row = self._session(link_id, session)
        session_data = self._data(session_row)

        agreement = self._agreement_or_none(data)
        if agreement is None:
            raise AgreementDenied(AgreementDenied.REASON_UNAVAILABLE)

        agreement_data = self._data(agreement)
        stamp = self._stamp()
        address = str((payload or {}).get("email") or "").strip().lower() or None

        existing = self.store.find(
            ACCEPTANCE_COLLECTION,
            {
                "session_id": session_row["id"],
                "agreement_id": agreement["id"],
                # The digest is part of the key, not a filter on the result. Two
                # acceptances of two different texts are two facts, and keying on
                # the id alone would mean a viewer who accepted the amended text was
                # recorded as still holding the old acceptance - so the gate could
                # never re-open for them and re-accepting was a no-op.
                "body_digest": agreement_data.get("body_digest"),
            },
            limit=1,
        )
        if existing:
            # Accepting twice is not an error and does not write a second row. The
            # research specifies no cap on acceptance and no second confirmation,
            # and a duplicate row would make "how many people accepted" a question
            # with two answers.
            acceptance = existing[0]
            created = False
        else:
            acceptance = self.store.create(
                ACCEPTANCE_COLLECTION,
                {
                    "link_id": record["id"],
                    rules.ROOM_REF: rules.room_ref_of(data, record),
                    "session_id": session_row["id"],
                    "agreement_id": agreement["id"],
                    "agreement_version": agreement_data.get("version"),
                    "body_digest": agreement_data.get("body_digest"),
                    "email": address,
                    "accepted": True,
                    "accepted_at": stamp,
                },
                room_id=rules.room_ref_of(data, record),
                actor=actor,
                source=source,
            )
            created = True

        if address and session_data.get("email") != address:
            self.store.update(session_row["id"], {"email": address}, actor=actor, source=source)
        if not session_data.get("accepted"):
            self.store.update(
                session_row["id"],
                {"accepted": True, "accepted_at": stamp, "acceptance_id": acceptance["id"]},
                actor=actor,
                source=source,
            )

        return {
            "link_id": record["id"],
            "accepted": True,
            "created": created,
            "acceptance_id": acceptance["id"],
            "session_id": session_row["id"],
            "agreement_id": agreement["id"],
            "agreement_version": agreement_data.get("version"),
            "accepted_at": self._data(acceptance).get("accepted_at"),
            "state": STATE_OPEN,
            "content_released": True,
            "message": "Agreement accepted. The room is open.",
        }

    def content(self, link_id: str, session: Any, *, source: str) -> dict[str, Any]:
        """Release the room content, and only to a viewer who has accepted.

        This is the route the research's step four describes: "Only after
        acceptance does the room's content load." It re-evaluates the link on this
        request, so an acceptance recorded before an expiry or a revocation does
        not outlive it.

        ``remaining_gates`` reports the gates this workflow does *not* own. A link
        that also asks for a password has not been fully opened by accepting the
        NDA, and saying otherwise would be the most dangerous kind of wrong on
        this page.
        """
        record, data = self._open_link(link_id)
        if not gate_required(data):
            # No gate, no session, no acceptance. Content moves.
            released = False
            acceptance = None
        else:
            session_row = self._session(link_id, session)
            agreement = self._agreement_or_none(data)
            if agreement is None:
                raise AgreementDenied(AgreementDenied.REASON_UNAVAILABLE)
            acceptance = self._covering_acceptance(
                session_row["id"], agreement["id"], self._data(agreement).get("body")
            )
            if acceptance is None:
                # Tell the two failures apart. No acceptance for this text is the
                # ordinary case and the buyer can fix it by reading and clicking.
                # An acceptance that named an older text is not fixable that way,
                # and sending the buyer back to the same box to click it again would
                # be a loop.
                if self.store.count_where(
                    ACCEPTANCE_COLLECTION,
                    {"session_id": session_row["id"], "agreement_id": agreement["id"]},
                ):
                    raise AgreementDenied(AgreementDenied.REASON_SUPERSEDED)
                raise AgreementDenied(AgreementDenied.REASON_NOT_ACCEPTED)
            released = True

        target = data.get("target") or {}
        kind = str(target.get("kind") or "dataroom")
        collection = "document" if kind == "document" else "dataroom"
        resolved = None
        try:
            candidate = self.store.get(str(target.get("id") or ""))
        except Exception:
            candidate = None
        if candidate is not None and candidate.get("collection") == collection:
            resolved = candidate.get("data")

        remaining = [step for step in rules.steps_required(data)]
        return {
            "link_id": record["id"],
            "released": True,
            "gate_satisfied": released,
            "target": target,
            "resolved": resolved is not None,
            "content": link_secrets.redact(resolved),
            "acceptance_id": (acceptance or {}).get("id"),
            "agreement_id": (acceptance or {}).get("agreement_id"),
            "remaining_gates": remaining,
            "note": (
                "This agreement gate is one gate. Any step in remaining_gates is still "
                "owed by another workflow."
            ),
        }

    def _covering_acceptance(
        self, session_id: str, agreement_id: str, body: Any
    ) -> dict[str, Any] | None:
        """The acceptance for this session that still covers this agreement, if any.

        Scoped by session *and* agreement, so it cannot pick up a row belonging to
        another viewer or to a link that has since been pointed at a different NDA.
        """
        rows = self.store.find(
            ACCEPTANCE_COLLECTION,
            {"session_id": session_id, "agreement_id": agreement_id},
            limit=10,
        )
        for row in rows:
            if acceptance_covers(self._data(row), agreement_id, body):
                return row
        return None

    # -- reads -------------------------------------------------------------- #

    def list_acceptances(
        self, room_id: str | None = None, link_id: str | None = None
    ) -> list[dict[str, Any]]:
        where: dict[str, Any] = {}
        if room_id:
            where[rules.ROOM_REF] = room_id
        if link_id:
            where["link_id"] = link_id
        # The envelope's `id` is folded in, because a list of acceptances with no ids
        # is a list nobody can act on.
        return [
            {"id": row["id"], **self._data(row)}
            for row in self.store.find(ACCEPTANCE_COLLECTION, where, limit=200)
        ]

    def list_sessions(
        self, room_id: str | None = None, link_id: str | None = None
    ) -> list[dict[str, Any]]:
        where: dict[str, Any] = {}
        if room_id:
            where[rules.ROOM_REF] = room_id
        if link_id:
            where["link_id"] = link_id
        # `token_hash` is dropped by `redact` on the way out, so a session list can be
        # shown to a seller without a filter that could be forgotten later.
        return link_secrets.redact(
            [
                {"id": row["id"], "opened_at": row.get("created_at"), **self._data(row)}
                for row in self.store.find(SESSION_COLLECTION, where, limit=200)
            ]
        )

    def summary(self, room_id: str | None = None) -> dict[str, Any]:
        """The board's headline numbers. Reads only."""
        gates = self.list_gates(room_id)
        where = {rules.ROOM_REF: room_id} if room_id else {}
        gated = [gate for gate in gates if gate["gate"]["enabled"]]
        return {
            "agreements": self.store.count_where(AGREEMENT_COLLECTION, where),
            "links": len(gates),
            "gated": len(gated),
            "ungated": len(gates) - len(gated),
            "broken_gates": sum(1 for gate in gated if not gate["gate"]["agreement_ok"]),
            "acceptances": self.store.count_where(ACCEPTANCE_COLLECTION, where),
            "sessions": self.store.count_where(SESSION_COLLECTION, where),
            "accepted_sessions": self.store.count_where(
                SESSION_COLLECTION, {**where, "accepted": True}
            ),
            "generated_at": self._now().isoformat(timespec="milliseconds"),
        }
