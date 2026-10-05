"""WF-103: the pure rules, and nothing that reads or writes.

Every function here takes values and returns values. Nothing opens a database, nothing
knows what HTTP is, and nothing writes. The engine composes them; the feature module
exposes them.

What lives here, and why each piece is here
-------------------------------------------

* :func:`build_document_request` assembles the create body. The research spells the
  fields ("`name`, `template_uuid`, `recipients[]` ... plus `content_placeholders[]` ...
  or a plain `pricing_tables[]`"), so the assembler follows that spelling and refuses
  what the researched call requires.
* :func:`evaluate_send` is the send gate, and it is the piece with two separate
  refusals in it that must never be merged. One is a 409 about a document that is not
  rendered yet; the other is the internal approval gate.
* :func:`quote_update_effect` makes the researched destructive default explicit. "Any
  section or item omitted from the payload will be deleted" is a sentence that will
  surprise a caller once, and a function that returns *what will be deleted* is how a
  surprise becomes a decision.
* :func:`shared_link_for` implements the researched fallback, and
  :func:`verify_signature` the researched HMAC check.
* :func:`apply_webhook` is the idempotent de-duplication, and :func:`stage_for` the
  writeback mapping.

Every refusal raised here carries a code from
:data:`~dsr.proposal_documents.vocabulary.ERROR_CODES`, which is the one place that
vocabulary is named.
"""

from __future__ import annotations

import hmac
from collections.abc import Mapping, Sequence
from hashlib import sha256
from typing import Any

from dsr.proposal_documents import vocabulary as vocab
from dsr.proposal_documents.errors import ProposalRefusal, refuse

# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #


def stamp(moment: Any) -> str:
    """An ISO timestamp, accepting a datetime or a string.

    The engine passes ``datetime.now(timezone.utc)`` and the seeder passes whatever
    ``context["now"]`` holds, so both spellings have to survive here rather than at
    each call site.
    """
    if moment is None:
        return ""
    isoformat = getattr(moment, "isoformat", None)
    return str(isoformat() if callable(isoformat) else moment)


def _number(value: Any, default: float = 0.0) -> float:
    """A finite float, or ``default``.

    A pricing table is arithmetic over open JSON, and a team that wrote ``"12"`` for
    a price means twelve. A value that is not a number at all becomes the default
    rather than raising, because the researched payload is caller-supplied and one bad
    field should not refuse a whole document.
    """
    if isinstance(value, bool) or value is None:
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed != parsed or parsed in (float("inf"), float("-inf")):  # NaN / inf
        return default
    return parsed


def _text(value: Any) -> str:
    return str(value or "").strip()


# --------------------------------------------------------------------------- #
# Recipients
# --------------------------------------------------------------------------- #


def normalise_role(value: Any) -> str:
    """Read a recipient role in the researched spelling, or refuse it.

    "per-recipient `role`/`roles` (signer, cc, approver)" is the set. A fourth role
    is a refusal rather than a pass-through, because a role this build does not
    recognise is a delivery instruction it cannot honour, and silently defaulting to
    ``signer`` would make an approval recipient into a signer.
    """
    text = _text(value).lower().replace("-", "_").replace(" ", "_")
    if text in ("signers", "sender"):
        text = vocab.ROLE_SIGNER
    elif text in ("carbon_copy", "copies", "bcc", "cc"):
        text = vocab.ROLE_CC
    elif text in ("approvers", "approval"):
        text = vocab.ROLE_APPROVER
    if text not in vocab.RECIPIENT_ROLES:
        raise refuse(
            "unknown_recipient_role",
            f"role={text or 'empty'} is not one of {', '.join(vocab.RECIPIENT_ROLES)}",
            role=vocab.ERROR_CODES["unknown_recipient_role"][1],
        )
    return text


def validate_recipients(raw: Any) -> list[dict[str, Any]]:
    """Normalise the researched ``recipients[]`` into the fields the create call uses.

    "recipients[] (email, first_name, last_name, `role`)", plus ``signing_order`` from
    the extensibility section. An entry with no email is refused rather than dropped:
    a recipient the vendor could not address is a document that silently goes
    somewhere other than where the caller meant.
    """
    if not isinstance(raw, (list, tuple)):
        raise refuse(
            "recipient_needs_an_email",
            "recipients must be a list; the researched create call takes recipients[]",
            recipients="The create call names recipients[] as a list.",
        )
    seen: list[str] = []
    result: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            raise refuse(
                "recipient_needs_an_email",
                "a recipient must be an object with an email",
                recipients="A recipient must be an object carrying an email address.",
            )
        email = _text(entry.get("email"))
        if not email:
            raise refuse(
                "recipient_needs_an_email",
                "every recipient needs an email address",
                email=vocab.ERROR_CODES["recipient_needs_an_email"][1],
            )
        if email in seen:
            raise refuse(
                "recipient_needs_an_email",
                f"{email} is listed twice; one address is one recipient",
                email=f"{email} is listed twice.",
            )
        seen.append(email)
        result.append(
            {
                "email": email,
                "first_name": _text(entry.get("first_name")),
                "last_name": _text(entry.get("last_name")),
                "role": normalise_role(entry.get("role") or vocab.ROLE_SIGNER),
                "signing_order": _number(entry.get("signing_order"), 0.0),
            }
        )
    return result


# --------------------------------------------------------------------------- #
# The pricing table
# --------------------------------------------------------------------------- #


def build_pricing_table(
    line_items: Sequence[Mapping[str, Any]], *, currency: str = "USD", discount: Any = 0
) -> dict[str, Any]:
    """Build the researched ``pricing_tables[]`` from the CRM's line items.

    "pricing_tables[] with `options.currency`, `options.discount`,
    `sections[].rows[].data`/`options`". The room computes the totals rather than
    trusting a caller to supply them, because the sum a buyer signs is the sum the CRM
    believes and a disagreement between the two is the defect this workflow exists to
    catch.
    """
    rows: list[dict[str, Any]] = []
    for item in line_items or ():
        if not isinstance(item, Mapping):
            continue
        qty = _number(item.get("qty"), 1.0)
        price = _number(item.get("price"))
        cost = _number(item.get("cost"))
        row_discount = _number(item.get("discount"), 0.0)
        row_tax = _number(item.get("tax"), 0.0)
        row_fee = _number(item.get("fee"), 0.0)
        net = price * (1.0 - row_discount / 100.0)
        total = net * (1.0 + row_tax / 100.0) + row_fee
        row: dict[str, Any] = {
            "sku": _text(item.get("sku")),
            "name": _text(item.get("name")) or _text(item.get("sku")),
            "qty": qty,
            "price": price,
            "cost": cost,
            "total": round(total, 2),
        }
        # Every researched item field that the CRM happened to carry is kept, because
        # payloads are open JSON and a team adding `contract_term` must not need a
        # change here to have it reach the document.
        for field in vocab.QUOTE_ITEM_FIELDS:
            if field in row or field not in item:
                continue
            row[field] = item[field]
        row.setdefault("discounts", [{"type": "percentage", "value": row_discount}])
        row.setdefault("taxes", [{"type": "percentage", "value": row_tax}])
        row.setdefault("fees", [{"type": "flat", "value": row_fee}])
        row.setdefault("billing_frequency", None)
        row.setdefault("contract_term", None)
        row.setdefault("pricing_method", None)
        row.setdefault("reference_type", None)
        row.setdefault("reference_id", None)
        rows.append({"data": row, "options": {}})

    subtotal = sum(
        _number(row["data"].get("price")) * _number(row["data"].get("qty")) for row in rows
    )
    overall_discount = _number(discount, 0.0)
    grand_total = subtotal * (1.0 - overall_discount / 100.0)

    return {
        "options": {
            "currency": _text(currency) or "USD",
            "discount": overall_discount,
        },
        "sections": [
            {
                "id": "default",
                "name": "Items",
                "rows": rows,
                "subtotal": round(subtotal, 2),
            }
        ],
        "grand_total": round(grand_total, 2),
        "item_count": len(rows),
    }


# --------------------------------------------------------------------------- #
# The create request
# --------------------------------------------------------------------------- #


def build_document_request(
    *,
    name: Any,
    template_uuid: Any,
    recipients: Any,
    line_items: Sequence[Mapping[str, Any]] = (),
    currency: str = "USD",
    discount: Any = 0,
    content_placeholders: Sequence[Mapping[str, Any]] = (),
    room_id: str | None = None,
) -> dict[str, Any]:
    """Assemble the body for ``POST /public/v1/documents``.

    The research gives the shape precisely: "with `name`, `template_uuid`,
    `recipients[]` (email, first_name, last_name, `role`), plus
    `content_placeholders[]` (each with `block_id` and `content_library_items[]`
    containing `pricing_tables[]` ...) or a plain `pricing_tables[]` for a template
    with a pricing table."

    Both of the researched placements are supported and exactly one is chosen: a
    template with a pricing table takes the plain ``pricing_tables[]``, and a template
    with content placeholders takes those. The choice is recorded in the returned
    ``placement`` key rather than inferred later, because a document whose pricing
    table went the wrong way is silent otherwise.
    """
    document_name = _text(name)
    if not document_name:
        raise refuse("deal_needs_a_name", name=vocab.ERROR_CODES["deal_needs_a_name"][1])
    template = _text(template_uuid)
    if not template:
        raise refuse(
            "template_uuid_required", template_uuid=vocab.ERROR_CODES["template_uuid_required"][1]
        )

    resolved_recipients = validate_recipients(recipients)
    table = build_pricing_table(line_items, currency=currency, discount=discount)

    placeholders = [dict(one) for one in (content_placeholders or ()) if isinstance(one, Mapping)]
    placement = "content_placeholders" if placeholders else "pricing_tables"

    body: dict[str, Any] = {
        "name": document_name,
        "template_uuid": template,
        "recipients": resolved_recipients,
        "placement": placement,
        "pricing_tables": [table],
        "metadata": {"room_id": room_id} if room_id else {},
    }
    if placeholders:
        body["content_placeholders"] = placeholders
    return body


# --------------------------------------------------------------------------- #
# The send gate
# --------------------------------------------------------------------------- #


def evaluate_send(
    state: Any, *, gate: str = vocab.GATE_OPEN, has_approval_workflow: bool = True
) -> dict[str, Any]:
    """Whether a document may be sent, and why not.

    This is the piece with two refusals that must not be merged, because the research
    gives them separately and they have different statuses:

    * "Attempting to send a document that is still in `document.uploaded` status
      returns a `409 Conflict` response." That is the vendor refusing a document that
      has not been rendered. A 409, not a 500 and not a 422.
    * "If the document's template has an approval workflow enabled, sending moves the
      document to `document.waiting_approval` instead of `document.sent`." That is the
      internal gate, and the second send is what delivers it.

    An approved document is sendable precisely because the research says so twice:
    the first send moves it to ``waiting_approval``, and "Once approved, you need to
    call the Send endpoint again to move it to `document.sent``."
    """
    current = vocab.normalise_state(state)
    approved = vocab.normalise_state(vocab.GATE_APPROVED) == vocab.GATE_APPROVED
    if current == vocab.STATE_APPROVED:
        approved = True
    gate_state = vocab.normalise_state(gate) or vocab.GATE_OPEN

    if current == vocab.STATE_UPLOADED:
        return {
            "sendable": False,
            "code": "document_not_yet_rendered",
            "status": 409,
            "state": current,
            "reason": vocab.ERROR_CODES["document_not_yet_rendered"][1],
        }
    if current in (vocab.STATE_SENT, vocab.STATE_VIEWED, vocab.STATE_COMPLETED):
        return {
            "sendable": False,
            "code": "document_already_sent",
            "status": 409,
            "state": current,
            "reason": vocab.ERROR_CODES["document_already_sent"][1],
        }
    if current in (vocab.STATE_CANCELLED, vocab.STATE_DECLINED):
        return {
            "sendable": False,
            "code": "document_send_not_permitted",
            "status": 409,
            "state": current,
            "reason": f"The document is {vocab.vendor_state(current)}, so it will not be sent.",
        }
    if has_approval_workflow and not approved:
        return {
            "sendable": False,
            "code": "no_approval_gate",
            "status": 409,
            "state": current,
            "gate": gate_state,
            "reason": vocab.ERROR_CODES["no_approval_gate"][1],
        }
    return {
        "sendable": True,
        "code": None,
        "status": 200,
        "state": current,
        "gate": gate_state,
        "reason": (
            vocab.SEND_AGAIN_QUOTE
            if current == vocab.STATE_WAITING_APPROVAL
            else "The document is ready to send."
        ),
    }


def require_sendable(
    state: Any, *, gate: str = vocab.GATE_OPEN, has_approval_workflow: bool = True
) -> dict[str, Any]:
    """Return the send verdict, or refuse with the researched status.

    A refusal carries the status the research names rather than the default 422, so
    "sending too early returns 409" is a 409 in this room too.
    """
    outcome = evaluate_send(state, gate=gate, has_approval_workflow=has_approval_workflow)
    if not outcome["sendable"]:
        raise ProposalRefusal(str(outcome["code"]), str(outcome["reason"]))
    return outcome


def after_send(state: Any) -> str:
    """The state a successful send leaves the document in.

    "sending moves the document to `document.waiting_approval` instead of
    `document.sent`" when the template carries an approval workflow, and
    `document.sent` when it does not. That is the whole difference the gate makes, and
    it is one function so the two branches cannot be written inconsistently.
    """
    current = vocab.normalise_state(state)
    if current == vocab.STATE_WAITING_APPROVAL:
        return vocab.STATE_SENT
    return vocab.STATE_WAITING_APPROVAL


# --------------------------------------------------------------------------- #
# The internal approval
# --------------------------------------------------------------------------- #


def normalise_decision(value: Any) -> str:
    """Read an approval decision, or refuse it.

    The two decisions are the researched pair. A rejection additionally needs a
    reason, which is checked by the caller because the check is about the second
    field rather than the decision itself.
    """
    text = _text(value).lower().replace("-", "_")
    if text in ("approved", "accepted", "ok"):
        text = vocab.APPROVAL_DECISION_APPROVE
    elif text in ("rejected", "changes_requested", "request_changes", "declined"):
        text = vocab.APPROVAL_DECISION_REJECT
    if text not in vocab.APPROVAL_DECISIONS:
        raise refuse(
            "unknown_approval_gate",
            "an approval is approve or reject",
            decision=vocab.ERROR_CODES["no_approval_gate"][1],
        )
    return text


def record_approval(
    gate: str, decision: str, *, approver: str = "", reason: str = "", message: str = ""
) -> dict[str, Any]:
    """The gate state one decision produces.

    A rejection with no reason is refused. "A rejection with nothing in it is not an
    instruction the seller can act on" is the sibling approval workflow's rule and it
    is the same rule here: the seller's next move depends on knowing what to change.
    """
    if decision == vocab.APPROVAL_DECISION_REJECT and not _text(reason):
        raise refuse(
            "reason_required_to_reject",
            reason=vocab.ERROR_CODES["reason_required_to_reject"][1],
        )
    return {
        "gate": vocab.APPROVAL_DECISION_REJECT
        if decision == vocab.APPROVAL_DECISION_REJECT
        else vocab.GATE_APPROVED,
        "decision": decision,
        vocab.APPROVAL_ACTOR_KEY: _text(approver) or None,
        vocab.APPROVAL_REASON_KEY: _text(reason) or None,
        vocab.APPROVAL_NOTE_KEY: _text(message) or None,
        "decided_at_gate": gate,
    }


# --------------------------------------------------------------------------- #
# The destructive quote update
# --------------------------------------------------------------------------- #


def quote_update_effect(current: Any, incoming: Any) -> dict[str, Any]:
    """What a quote update would remove, before it removes it.

    "Any section or item omitted from the payload will be deleted." That is the
    research, word for word, and it is a destructive default on a write. So this
    function computes the deletions and returns them, and
    :func:`require_quote_update` refuses an update that has not acknowledged them.
    A caller who wants the deletions has to say so.
    """
    existing_sections = [dict(one) for one in (current or []) if isinstance(one, Mapping)]
    new_sections = [dict(one) for one in (incoming or []) if isinstance(one, Mapping)]

    kept_ids = {_text(section.get("id")) for section in new_sections}
    removed_sections = [
        _text(section.get("id")) or str(index)
        for index, section in enumerate(existing_sections)
        if _text(section.get("id")) not in kept_ids
    ]

    removed_items: list[str] = []
    for section in new_sections:
        section_id = _text(section.get("id"))
        original = next(
            (one for one in existing_sections if _text(one.get("id")) == section_id), None
        )
        if original is None:
            continue
        kept_skus = {
            _text(item.get("sku"))
            for item in section.get("items") or []
            if isinstance(item, Mapping)
        }
        for item in original.get("items") or []:
            if not isinstance(item, Mapping):
                continue
            sku = _text(item.get("sku"))
            if sku not in kept_skus:
                removed_items.append(f"{section_id}:{sku}" if sku else f"{section_id}:unnamed")

    return {
        "removed_sections": removed_sections,
        "removed_items": removed_items,
        "destructive": bool(removed_sections or removed_items),
        "evidence": vocab.QUOTE_UPDATE_DELETES_OMISSIONS,
    }


def require_quote_update(
    current: Any, incoming: Any, *, acknowledge: bool = False
) -> dict[str, Any]:
    """Return the update effect, or refuse an unacknowledged destructive update.

    ``acknowledge`` is a required, explicit statement that the caller understands
    omissions are deletions. Nothing infers it from the size of the payload, because a
    payload that happens to list every section is indistinguishable from one that
    forgot a section, and only the caller knows which it meant.
    """
    effect = quote_update_effect(current, incoming)
    if effect["destructive"] and not acknowledge:
        raise refuse(
            "acknowledgement_required_for_deletions",
            effect["evidence"],
            sections=effect["evidence"],
        )
    return effect


# --------------------------------------------------------------------------- #
# ``shared_link`` and its researched fallback
# --------------------------------------------------------------------------- #


def shared_link_for(document: Mapping[str, Any], recipient: Mapping[str, Any]) -> dict[str, Any]:
    """The link a recipient uses, with the researched fallback applied.

    "`shared_link` field in the `recipients` array may be empty in webhook payloads
    due to asynchronous processing", and the automation note names the remedy: "fall
    back to Document Details". So an empty webhook link resolves against the
    document's own details link, and a document that has neither falls back to the
    derived stand-in - which is the only honest answer available, and it says which
    source it used.
    """
    document_id = _text(document.get("id"))
    details = _text(document.get("details_url"))
    webhook_link = _text(recipient.get("shared_link"))
    email = _text(recipient.get("email"))

    if webhook_link:
        return {
            "link": webhook_link,
            "source": vocab.SHARED_LINK_SOURCE_WEBHOOK,
            "is_derived": _is_derived_host(webhook_link),
            "note": None,
        }
    if details:
        return {
            "link": details,
            "source": vocab.SHARED_LINK_SOURCE_DETAILS,
            "is_derived": _is_derived_host(details),
            "note": vocab.SHARED_LINK_FALLBACK_NOTE,
        }
    return {
        "link": vocab.derived_shared_link(document_id, email),
        "source": vocab.SHARED_LINK_SOURCE_DERIVED,
        "is_derived": True,
        "note": vocab.SHARED_LINK_FALLBACK_NOTE + " " + vocab.DOCUMENT_DERIVED_NOTE,
    }


def _is_derived_host(link: str) -> bool:
    """Whether a link is one this room derived rather than one a vendor returned.

    Read off the reserved ``.invalid`` host rather than remembered, so a link this
    build produced is marked derived no matter which of the three sources served it.
    Marking the Document Details fallback as not derived would be the worst of the
    three cases: the research fallback would be quietly reporting a derived artefact
    as a vendor's own.
    """
    return vocab.DERIVED_HOST_SUFFIX in str(link or "")


# --------------------------------------------------------------------------- #
# The webhook
# --------------------------------------------------------------------------- #


def verify_signature(
    body: Any, signature: str, shared_key: str, *, encoding: str = "utf-8"
) -> bool:
    """Check the researched HMAC-SHA256 signature.

    "All webhook requests include an HMAC-SHA256 signature for verification using your
    shared key." Compared with :func:`hmac.compare_digest`, so a wrong signature is
    refused without telling the sender which byte was wrong.
    """
    if not shared_key or not signature:
        return False
    expected = hmac.new(str(shared_key).encode(encoding), _canonical(body), sha256).hexdigest()
    return hmac.compare_digest(expected, str(signature).strip().lower())


def _canonical(body: Any) -> bytes:
    """The bytes a signature is computed over.

    A webhook body arrives as a mapping, and the vendor signs the serialised request.
    Sorting the keys makes the check independent of the order the JSON parser happened
    to produce, which is the only way a signature check survives a real receiver.
    """
    import json

    if isinstance(body, (bytes, bytearray)):
        return bytes(body)
    if isinstance(body, str):
        return body.encode("utf-8")
    return json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def is_duplicate(event_id: str, seen: Any) -> bool:
    """Whether this webhook has already been applied.

    "retries are handled by de-duplicating on `X-PandaDoc-Webhook-Event-Id`." The
    check is against the ids actually recorded, not a timestamp, so a replay that
    arrives out of order is still caught.
    """
    wanted = _text(event_id)
    if not wanted:
        return False
    return wanted in {_text(one) for one in (seen or ())}


def apply_webhook(payload: Mapping[str, Any], document: Mapping[str, Any]) -> dict[str, Any]:
    """The document patch one ``document_state_changed`` webhook produces.

    Reads the researched fields and nothing else: the state, the per-recipient
    ``has_completed`` and ``signature_date``, the ``grand_total``, the
    ``linked_objects[]`` and the ``metadata``. The writeback is returned rather than
    performed here, because this module does not write.
    """
    state = vocab.normalise_state(payload.get("state") or payload.get("status"))
    recipients = []
    for entry in payload.get("recipients") or []:
        if not isinstance(entry, Mapping):
            continue
        recipients.append(
            {
                "email": _text(entry.get("email")),
                "role": _text(entry.get("role")) or vocab.ROLE_SIGNER,
                vocab.RECIPIENT_HAS_COMPLETED: bool(entry.get(vocab.RECIPIENT_HAS_COMPLETED)),
                vocab.RECIPIENT_SIGNATURE_DATE: entry.get(vocab.RECIPIENT_SIGNATURE_DATE),
            }
        )

    linked = [
        {
            "provider": _text(one.get("provider")) or vocab.LINKED_OBJECT_PROVIDER,
            "entity_type": _text(one.get("entity_type")) or vocab.LINKED_OBJECT_ENTITY_TYPE,
            "entity_id": _text(one.get("entity_id")),
        }
        for one in (payload.get(vocab.LINKED_OBJECT_KEY) or [])
        if isinstance(one, Mapping)
    ]

    patch: dict[str, Any] = {
        "state": state,
        "vendor_state": vocab.vendor_state(state),
        "grand_total": payload.get("grand_total"),
        "recipients": recipients,
        vocab.LINKED_OBJECT_KEY: linked,
        "metadata": dict(payload.get("metadata") or {}),
    }
    completed = [
        one["email"] for one in recipients if one[vocab.RECIPIENT_HAS_COMPLETED] and one["email"]
    ]
    patch["all_recipients_completed"] = bool(recipients) and len(completed) == len(recipients)
    patch["changed"] = vocab.normalise_state(document.get("state")) != state
    return patch


# --------------------------------------------------------------------------- #
# The writeback
# --------------------------------------------------------------------------- #


def stage_for(state: Any) -> str:
    """The CRM opportunity stage one document state maps to.

    "Update Opportunity status when PandaDoc status is updated" is the instruction;
    the stage *names* are not quoted, and the deal record owns its own stage
    vocabulary, so this is a derivation and is recorded as one. See
    ``DERIVED_STAGE_MAPPING`` in the inferences module.
    """
    current = vocab.normalise_state(state)
    return {
        vocab.STATE_UPLOADED: vocab.CRM_STAGE_DRAFT,
        vocab.STATE_DRAFT: vocab.CRM_STAGE_DRAFT,
        vocab.STATE_WAITING_APPROVAL: vocab.CRM_STAGE_DRAFT,
        vocab.STATE_APPROVED: vocab.CRM_STAGE_DRAFT,
        vocab.STATE_REJECTED: vocab.CRM_STAGE_DRAFT,
        vocab.STATE_SENT: vocab.CRM_STAGE_PROPOSAL_SENT,
        vocab.STATE_VIEWED: vocab.CRM_STAGE_PROPOSAL_VIEWED,
        vocab.STATE_COMPLETED: vocab.CRM_STAGE_CLOSED_WON,
        vocab.STATE_WAITING_PAY: vocab.CRM_STAGE_PROPOSAL_VIEWED,
        vocab.STATE_PAID: vocab.CRM_STAGE_CLOSED_WON,
        vocab.STATE_CANCELLED: vocab.CRM_STAGE_CLOSED_LOST,
        vocab.STATE_DECLINED: vocab.CRM_STAGE_CLOSED_LOST,
    }.get(current, vocab.CRM_STAGE_DRAFT)


def sync_from_document(document: Mapping[str, Any], stage: Any = None) -> dict[str, Any]:
    """The writeback one document state produces.

    "the integration writes status back to the CRM (opportunity stage/close date,
    notes & attachments including the signed PDF) and, via `linked_objects`, makes the
    document discoverable from the CRM record."

    Three parts, all returned for the engine to write: the stage, the note that makes
    the document discoverable from the deal, and the ``linked_objects`` entry. The
    close date is only proposed for a state that ends the deal, because writing one
    for a document that is merely viewed would close an opportunity a buyer is still
    reading.
    """
    state = vocab.normalise_state(document.get("state"))
    resolved = _text(stage) or stage_for(state)
    note = (
        f"{vocab.ACTIVITY_DOCUMENT_CREATED}: {document.get('name') or document.get('id')} "
        f"is {vocab.vendor_state(state)}."
    )
    return {
        "deal_id": _text(document.get("deal_id")),
        "stage": resolved,
        "close_date": True
        if resolved in (vocab.CRM_STAGE_CLOSED_WON, vocab.CRM_STAGE_CLOSED_LOST)
        else None,
        "note": note,
        vocab.LINKED_OBJECT_KEY: [
            {
                "provider": vocab.LINKED_OBJECT_PROVIDER,
                "entity_type": vocab.LINKED_OBJECT_ENTITY_TYPE,
                "entity_id": _text(document.get("deal_id")),
                "document_id": _text(document.get("id")),
            }
        ],
        "stage_source": "derived" if not _text(stage) else "caller",
    }
