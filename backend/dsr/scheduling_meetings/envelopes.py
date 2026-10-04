"""The envelope shape: recipients, fields, the join key, and the approver gate.

Everything the research says about ``POST /api/v2/envelope/create`` is checked
here, in pure functions, so the validator is one thing a test can hold and the
HTTP layer is only routing.
"""

from __future__ import annotations

import hmac
from typing import Any, Iterable, Mapping

from dsr.scheduling_meetings.errors import (
    InvalidExternalId,
    MalformedField,
    MalformedRecipient,
    MissingRecipients,
    UnknownFieldType,
    UnknownRecipientRole,
)
from dsr.scheduling_meetings.vocabulary import (
    APPROVER,
    COORDINATE_MAX,
    COORDINATE_MIN,
    EVENT_DOCUMENT_RECIPIENT_COMPLETED,
    EVENT_DOCUMENT_REJECTED,
    EVENT_RECIPIENT_EXPIRED,
    FIELD_TYPES,
    RECIPIENT_ROLES,
    SIGNER,
    SIGNING_ROLES,
    WEBHOOK_SECRET_HEADER,
)

# --------------------------------------------------------------------------- #
# The join key
# --------------------------------------------------------------------------- #

#: The prefix of a derived external id. Short, stable, and names this product's
#: own naming so a reader looking at a signing URL can tell which side chose it.
#: The derivation is recorded in :mod:`dsr.scheduling_meetings.inferences`.
EXTERNAL_ID_PREFIX = "dsr-map"

#: How many random characters a derived external id carries. Long enough that a
#: join key is not guessable from the room id alone, short enough to sit in a URL
#: query string next to the vendor's own token.
EXTERNAL_ID_RANDOM_CHARS = 12

#: What a body may not put in a join key. The vendor carries the external id in a
#: URL query string and in a webhook body, so a value that is not a plain token
#: cannot round-trip through either.
_FORBIDDEN_EXTERNAL_ID_CHARS = set("/\\?#&=\"'<> \t\n\r")


def external_id_for(room_id: str, plan_id: str, token: str) -> str:
    """The join key this build puts in a plan's ``externalId``.

    The research names the key and not its format: "The external ID is stored with
    the created document and included in webhook payloads", and "``externalId`` is
    the join key back to the deal room". It also publishes the example
    ``?externalId=order-12345``, which is a vendor's transaction id and not
    something this product can produce.

    So the format is derived: ``dsr-map.<room>.<plan>.<token>``. Every part the
    join has to survive is in it - which room, which plan, and an unguessable
    token so two plans in one room can never collide - and it stays a single URL
    query parameter. A caller that has its own transaction id sets
    ``external_id`` on the plan instead and this is never called.
    """
    parts = [EXTERNAL_ID_PREFIX, _clean_part(room_id), _clean_part(plan_id), _clean_part(token)]
    if len(parts) != 4 or any(not part for part in parts):
        raise InvalidExternalId(
            f"cannot derive an externalId from room={room_id!r} plan={plan_id!r} token={token!r}; "
            "set an explicit external_id instead"
        )
    return ".".join(parts)


def _clean_part(value: str) -> str:
    """One dot-free, URL-safe token. A room id with a space cannot be a path segment."""
    text = "".join(char for char in str(value) if char.isalnum() or char in "-_")
    return text.strip("-")


def require_external_id(value: Any) -> str:
    """A caller-supplied join key, checked against what a URL can carry.

    This is the guard on the one thing a webhook resolves a plan by. A join key
    containing a separator would let a body name a plan it was not sent for.
    """
    if not isinstance(value, str) or not value.strip():
        raise InvalidExternalId("external_id must be a non-empty string")
    text = value.strip()
    if len(text) > 200:
        raise InvalidExternalId(f"external_id is {len(text)} characters; the limit is 200")
    bad = sorted(_FORBIDDEN_EXTERNAL_ID_CHARS & set(text))
    if bad:
        raise InvalidExternalId(
            "external_id may not contain " + ", ".join(repr(char) for char in bad) + "; "
            "the key is carried in a URL query string and in webhook bodies"
        )
    return text


def read_external_id(payload: Mapping[str, Any]) -> str:
    """The join key out of a webhook body, however the vendor spelled it.

    "The external ID is stored with the created document and included in webhook
    payloads." This build looks for the three spellings in
    :data:`~dsr.scheduling_meetings.vocabulary.EXTERNAL_ID_KEYS` rather than one,
    because a vendor renaming its own field should not silently un-resolve every
    event this room receives.
    """
    for key in ("externalId", "external_id", "externalid"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    nested = payload.get("payload") or payload.get("document") or payload.get("data")
    if isinstance(nested, Mapping):
        return read_external_id(nested)
    return ""


# --------------------------------------------------------------------------- #
# Recipients
# --------------------------------------------------------------------------- #


def _email_looks_like_one(value: Any) -> bool:
    """A cheap shape check: one ``@``, something either side, no spaces.

    Not a full RFC 5322 parse. The research's invitation path is an email a
    recipient opens, so an address with no ``@`` is unusable and one with spaces
    is unsendable. Anything this accepts is the vendor's problem.
    """
    if not isinstance(value, str):
        return False
    text = value.strip()
    if text.count("@") != 1 or any(char.isspace() for char in text):
        return False
    local, _, domain = text.partition("@")
    return bool(local) and bool(domain) and "." in domain


def normalise_recipient(payload: Mapping[str, Any]) -> dict[str, Any]:
    """One recipient in the shape the researched create call expects.

    "supplying ``recipients[]`` each with ``email``, ``name``, ``role`` and
    ``fields[]``". All three required keys are checked, the role is checked against
    the five the research lists, and the address is checked against the shape the
    invitation path needs.

    ``signing_order`` is optional and defaults to 1. The research's
    "per-recipient ``signingOrder``" is a position, not a flag, so an absent one
    means "first" rather than "unorderable".
    """
    if not isinstance(payload, Mapping):
        raise MalformedRecipient(f"a recipient must be an object, not {type(payload).__name__}")

    email = payload.get("email")
    if not _email_looks_like_one(email):
        raise MalformedRecipient(
            f"recipient email {email!r} is not an address; the research invites recipients by email"
        )

    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        raise MalformedRecipient(f"recipient {email!r} has no name to show the other parties")

    role = payload.get("role")
    if not isinstance(role, str):
        raise MalformedRecipient(f"recipient {email!r} has no role; the research names five")
    role = role.strip().upper()
    if role not in RECIPIENT_ROLES:
        raise UnknownRecipientRole(
            f"recipient role {role!r} is not one of {', '.join(RECIPIENT_ROLES)}"
        )

    order = payload.get("signing_order", payload.get("signingOrder", 1))
    if isinstance(order, bool) or not isinstance(order, int):
        raise MalformedRecipient(f"recipient {email!r} has a non-integer signing_order {order!r}")

    fields = payload.get("fields") or []
    if not isinstance(fields, list):
        raise MalformedRecipient(f"recipient {email!r} has fields that are not a list")

    return {
        "email": str(email).strip(),
        "name": name.strip(),
        "role": role,
        "signing_order": order,
        "fields": [validate_field(field, index) for index, field in enumerate(fields)],
        # Not part of the researched recipient shape. Carried because a seller has
        # to be able to say who on their own side is in the room, and because an
        # event has to be able to name a recipient the vendor only knows by address.
        "party": str(payload.get("party") or ("buyer" if role == SIGNER else "seller")),
    }


def build_recipients(payloads: Iterable[Any]) -> list[dict[str, Any]]:
    """Every recipient on a plan, checked together rather than one at a time.

    Two rules apply to the set and not to any single member:

    * **at least one signing role.** "supplying ``recipients[]`` each with
      ``email``, ``name``, ``role``", and a mutual action plan nobody signs is not
      mutual. A list of only CCs and VIEWERs is refused for the same reason.
    * **one role per address.** Two records for the same person with different
      roles is a data-entry mistake the vendor would resolve by one of them
      silently winning, so it is refused here instead.
    """
    recipients = [normalise_recipient(entry) for entry in payloads]
    if not recipients:
        raise MissingRecipients("a plan needs recipients[]; the research sends it to named people")

    if not any(entry["role"] in SIGNING_ROLES for entry in recipients):
        raise MissingRecipients(
            f"a plan needs at least one of {', '.join(SIGNING_ROLES)}; "
            f"got {', '.join(sorted({entry['role'] for entry in recipients}))}"
        )

    seen: dict[str, str] = {}
    for entry in recipients:
        earlier = seen.get(entry["email"].lower())
        if earlier is not None:
            raise MalformedRecipient(
                f"{entry['email']} appears twice, once as {earlier} and once as {entry['role']}"
            )
        seen[entry["email"].lower()] = entry["role"]

    return recipients


def required_roles(recipients: Iterable[Mapping[str, Any]]) -> list[str]:
    """Which signing roles this plan actually uses, in the research's order.

    A plan with an approver is a different workflow from a plan without one, and a
    page has to say which it is looking at. Returned in ``SIGNING_ROLES`` order
    rather than the caller's, so two plans read the same way.
    """
    present = {str(entry.get("role") or "").upper() for entry in recipients}
    return [role for role in SIGNING_ROLES if role in present]


# --------------------------------------------------------------------------- #
# Fields
# --------------------------------------------------------------------------- #


def _coordinate(value: Any, key: str, email: str) -> float:
    """One percentage coordinate, checked against the scale the research quotes.

    "``positionX`` | Horizontal position from left edge (0 = left, 100 = right)".
    A coordinate past either end is not a field that would land on the page.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MalformedField(f"recipient {email} has a non-numeric {key} {value!r}")
    number = float(value)
    if not COORDINATE_MIN <= number <= COORDINATE_MAX:
        raise MalformedField(
            f"recipient {email} has {key}={number}; the research quotes "
            f"{COORDINATE_MIN} to {COORDINATE_MAX}"
        )
    return number


def validate_field(payload: Any, index: int = 0) -> dict[str, Any]:
    """One placed field, in the shape the researched create call expects.

    "``fields[]`` (``type`` ``SIGNATURE``/``NAME``/``DATE``/..., ``page``,
    ``positionX``, ``positionY``, ``width``, ``height`` as percentages,
    ``identifier`` = file index)".

    ``width`` and ``height`` default to a tenth of the page rather than being
    required, because a signature box has an obvious size and the research makes
    the *position* the thing the seller chooses. ``identifier`` defaults to 0,
    which is "Index of the file (0 for first file)" - a plan sent as one PDF.
    """
    if not isinstance(payload, Mapping):
        raise MalformedField(f"field {index} is not an object, but {type(payload).__name__}")

    kind = payload.get("type")
    if not isinstance(kind, str):
        raise MalformedField(f"field {index} has no type")
    kind = kind.strip().upper()
    if kind not in FIELD_TYPES:
        raise UnknownFieldType(
            f"field {index} has type {kind!r}, which is not one of {', '.join(FIELD_TYPES)}"
        )

    email = str(payload.get("recipient_email") or "")
    page = payload.get("page", 0)
    if isinstance(page, bool) or not isinstance(page, int) or page < 0:
        raise MalformedField(f"field {index} has page {page!r}; page numbers start at 0")

    identifier = payload.get("identifier", 0)
    if isinstance(identifier, bool) or not isinstance(identifier, int) or identifier < 0:
        raise MalformedField(
            f"field {index} has identifier {identifier!r}; it is a file index, 0 for the first file"
        )

    x = _coordinate(payload.get("positionX", payload.get("position_x", 0)), "positionX", email)
    y = _coordinate(payload.get("positionY", payload.get("position_y", 0)), "positionY", email)
    width = _coordinate(payload.get("width", 20), "width", email)
    height = _coordinate(payload.get("height", 6), "height", email)

    field: dict[str, Any] = {
        "type": kind,
        "page": page,
        "positionX": x,
        "positionY": y,
        "width": width,
        "height": height,
        "identifier": identifier,
        "required": bool(payload.get("required", kind in ("SIGNATURE", "NAME", "DATE"))),
    }
    label = payload.get("label")
    if isinstance(label, str) and label.strip():
        field["label"] = label.strip()
    return field


# --------------------------------------------------------------------------- #
# The approver gate
# --------------------------------------------------------------------------- #

#: A recipient is done when any of these reached them. "Recipient completes their
#: action" covers both signing and approving, and a rejection ends their part too -
#: though it ends the plan, which :func:`dsr.scheduling_meetings.events.apply_event`
#: handles rather than this module.
RECIPIENT_FINISHING_EVENTS: tuple[str, ...] = (
    EVENT_DOCUMENT_RECIPIENT_COMPLETED,
    EVENT_DOCUMENT_REJECTED,
    EVENT_RECIPIENT_EXPIRED,
)

_APPROVED_ROLES: frozenset[str] = frozenset({SIGNER, APPROVER})


def approver_block(recipients: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The approvers on a plan who have not approved yet.

    Empty means signers may proceed. A plan with no approver at all also returns
    empty, because "Must approve before signers can sign" is a constraint on an
    approver's existence and not a rule that every plan needs one.
    """
    pending = []
    for recipient in recipients:
        role = str(recipient.get("role") or "")
        if role != APPROVER:
            continue
        if str(recipient.get("status") or "").upper() in ("APPROVED", "COMPLETED"):
            continue
        pending.append(dict(recipient))
    return pending


def signing_unlocked(recipients: Iterable[Mapping[str, Any]]) -> tuple[bool, list[str]]:
    """May a signer go ahead, and if not, who still has to act.

    The research states the gate in one line: "APPROVER | Must approve before
    signers can sign". This reads it off the recipient list rather than off a flag
    somebody has to remember to set, because a flag and the recipient list can
    disagree and the research names only the role.

    Returns the decision and the approvers still blocking it, so a page can say
    which person is holding the plan up rather than only that it is held up.
    """
    pending = approver_block(recipients)
    if not pending:
        return True, []
    return False, [
        f"{entry.get('name') or entry.get('email')} must approve before signers can sign"
        for entry in pending
    ]


# --------------------------------------------------------------------------- #
# Webhook authentication
# --------------------------------------------------------------------------- #


def secret_matches(presented: str | None, configured: str | None) -> bool:
    """Does the event carry the secret this plan was configured with?

    "Check the ``X-Documenso-Secret`` header matches your configured secret."

    Compared with :func:`hmac.compare_digest` so a caller cannot learn the secret
    one character at a time by timing the answer. A plan with no secret configured
    can never be authenticated, and says so rather than accepting anything - an
    unconfigured plan has no way to know who is allowed to move its milestone.
    """
    if not presented or not configured:
        return False
    return _constant_time(presented.strip(), str(configured))


def _constant_time(left: str, right: str) -> bool:
    """Constant-time string comparison, with the two rough edges handled.

    :func:`hmac.compare_digest` on two ``str`` compares their UTF-8 bytes, and it
    raises on a non-ASCII string. Both bytes and the non-ASCII case are handled
    here so a caller with a non-ASCII secret gets an answer rather than an
    exception.
    """
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


#: Re-exported so a caller building an event does not have to know the header name
#: from the vocabulary module as well.
SECRET_HEADER = WEBHOOK_SECRET_HEADER
