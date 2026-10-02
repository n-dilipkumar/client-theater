"""Payload composition: Company only, or Company + filtered Contacts.

Sourced: "Choose the output of data you want to be sent to your URL, for example
only Company for the company lead or Company + Contacts for the company lead and
contacts employed at the company. If you choose to include Contacts, you have
the option of filtering the contact details on 'Keywords' and required fields."

Three rules this module implements, and the reading behind each
-----------------------------------------------------------

**1. In ``company`` mode there is no ``contacts`` key at all.**

Not ``null``, not an empty list. A destination that branches on
``if "contacts" in body`` has to get the same answer in both modes, and a key
that is present and empty is the shape that makes that branch wrong. This is the
same rule WF-025 applies to an anonymous event's ``user``, for the same reason.

**2. Keywords and required fields filter *which contacts are sent*, not which
contact fields are sent.**

The research says "filtering the contact details on 'Keywords' and required
fields", which reads two ways. The reading taken here is the row filter: two
controls, side by side in a contact picker, one narrowing rows by text and one
narrowing rows by which fields are populated. The alternative - a field
projection - is not built, because a lead's ``data`` is arbitrary JSON by
policy, so "the contact's fields" is not a fixed list to project from, and
inventing one would be a coordination requirement for every team that adds a
contact field. ``contact-fields-are-not-projected`` in
:mod:`dsr.intent_stream.inferences` records the choice and the alternative.

**3. Contacts filtered down to nothing is still a delivery.**

The payload is *Company + Contacts*. If keywords exclude every contact, the
company is still the lead the Segment matched, and the researched output was
requested. So the POST goes out with ``contacts: []`` and the delivery records
how many were considered and how many survived. What it must never do is skip
the send, because then the destination cannot tell "no contacts matched your
keywords" from "this company was not interesting".
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from dsr.intent_stream.errors import LeadError
from dsr.intent_stream.segments import MISSING, resolve
from dsr.intent_stream.vocabulary import (
    KEYWORD_FIELDS,
    MATCH_ANY,
    PAYLOAD_COMPANY,
    PAYLOAD_COMPANY_CONTACTS,
    PAYLOAD_TYPE,
    TOKEN_BODY_FIELD,
)

#: Bounds on a filter, so a saved filter cannot become a payload-forging tool.
MAX_KEYWORDS = 50
MAX_REQUIRED_FIELDS = 50
MAX_KEYWORD_LENGTH = 100


def require_payload_mode(mode: Any) -> str:
    """Validate the payload output choice, defaulting to Company only."""
    if mode is None or mode == "":
        return PAYLOAD_COMPANY
    # "Company + Contacts" is the phrase the research uses, and a caller who
    # copied it out of the help centre should not have to know this product's
    # vocabulary. Collapse runs of separators so every spelling lands on one.
    text = re.sub(r"[^a-z0-9]+", "_", str(mode).strip().casefold()).strip("_")
    # The research writes the two options as prose - "Company", "Company +
    # Contacts" - so the prose spellings are accepted as well as the slugs. A
    # caller who copied the sentence out of the help centre should not have to
    # know this product's vocabulary to use it.
    if text in ("company", "company_only", "companyonly"):
        return PAYLOAD_COMPANY
    if text in (
        "company_contacts",
        "companyandcontacts",
        "company_and_contacts",
        "companycontacts",
    ):
        return PAYLOAD_COMPANY_CONTACTS
    raise LeadError(
        f"payload must be 'company' or 'company_contacts', not {mode!r}",
        remediation="The research offers exactly two outputs: only Company, or Company + Contacts.",
        status=422,
    )


def require_contact_filter(raw: Any) -> dict[str, list[str]]:
    """Normalise the optional contact filter into ``keywords`` + ``requiredFields``.

    Both are optional, and the research says so - "you **have the option** of
    filtering". So an absent filter is ``{"keywords": [], "requiredFields": []}``,
    which is not the same as a filter that matches nothing: it means "send every
    contact at the identified company", and the difference has to be visible in
    the delivery record.
    """
    if raw is None or raw == "":
        return {"keywords": [], "requiredFields": []}
    if isinstance(raw, str):
        # A bare string is read as the keyword list, the same shorthand
        # `?where=keywords=security,engineering` accepts elsewhere in the
        # product. A required-field list has no sensible one-word spelling.
        return {"keywords": _string_list(raw, "keywords", MAX_KEYWORDS), "requiredFields": []}
    if not isinstance(raw, Mapping):
        raise LeadError(
            f"contactFilter must be an object, not {type(raw).__name__}",
            remediation='Pass {"keywords": ["security"], "requiredFields": ["email"]}.',
            status=422,
        )

    keywords = _string_list(
        raw.get("keywords") if raw.get("keywords") is not None else raw.get("keyword"),
        "keywords",
        MAX_KEYWORDS,
    )
    required = _string_list(
        raw.get("requiredFields")
        if raw.get("requiredFields") is not None
        else raw.get("required_fields"),
        "requiredFields",
        MAX_REQUIRED_FIELDS,
    )
    return {"keywords": keywords, "requiredFields": required}


def _string_list(value: Any, name: str, cap: int) -> list[str]:
    """One list of non-empty strings, de-duplicated, order preserved."""
    if value is None or value == "":
        return []
    if isinstance(value, str):
        items = [part for part in value.replace(";", ",").split(",")]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        raise LeadError(
            f"{name} must be a list of strings, not {type(value).__name__}",
            remediation=f"Pass {name} as an array, or a comma-separated string.",
            status=422,
        )
    cleaned: list[str] = []
    for item in items:
        if not isinstance(item, str):
            raise LeadError(
                f"{name} contains a {type(item).__name__}, not a string",
                remediation="Keywords and required fields are both text.",
                status=422,
            )
        text = item.strip()
        if not text:
            continue
        if len(text) > MAX_KEYWORD_LENGTH:
            raise LeadError(
                f"{name} entry {text[:40]!r}... is {len(text)} characters, over the {MAX_KEYWORD_LENGTH} allowed",
                status=422,
            )
        if text not in cleaned:
            cleaned.append(text)
    if len(cleaned) > cap:
        raise LeadError(
            f"{name} holds {len(cleaned)} entries, more than the {cap} allowed",
            status=422,
        )
    return cleaned


def keyword_hit(contact: Mapping[str, Any], keywords: Sequence[str]) -> dict[str, Any]:
    """Which keyword matched which contact field, if any.

    A hit is reported with the field it landed in. "Your keywords matched nobody"
    and "your keywords matched nobody *in the fields we search*" are different
    answers, and only the second one is actionable.
    """
    if not keywords:
        return {"matched": True, "keyword": None, "field": None}
    lowered = [keyword.casefold() for keyword in keywords]
    for field in KEYWORD_FIELDS:
        value = resolve(contact, field)
        if value is MISSING:
            continue
        haystack = " ".join(_flatten(value)).casefold()
        if not haystack:
            continue
        for keyword, needle in zip(keywords, lowered, strict=True):
            if needle and needle in haystack:
                return {"matched": True, "keyword": keyword, "field": field}
    return {"matched": False, "keyword": None, "field": None}


def _flatten(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for item in value:
            out.extend(_flatten(item))
        return out
    if isinstance(value, Mapping):
        return [f"{key} {child}" for key, child in value.items()]
    if isinstance(value, bool):
        return ["true" if value else "false"]
    return [str(value)]


def missing_required_fields(contact: Mapping[str, Any], required: Sequence[str]) -> list[str]:
    """The required fields this contact does not have, in the order asked for."""
    absent: list[str] = []
    for field in required:
        value = resolve(contact, field)
        if value is MISSING or value is None or (isinstance(value, str) and not value.strip()):
            absent.append(field)
        elif isinstance(value, (list, tuple, dict)) and not value:
            absent.append(field)
    return absent


def filter_contacts(
    contacts: Sequence[Mapping[str, Any]],
    contact_filter: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Apply both researched filters and report what each one did.

    Returns the surviving contacts *and* the per-contact verdict, because "the
    payload had no contacts" is only actionable next to "these three were
    excluded: one by keyword, two for missing email".
    """
    keywords = list((contact_filter or {}).get("keywords") or [])
    required = list((contact_filter or {}).get("requiredFields") or [])
    kept: list[Mapping[str, Any]] = []
    excluded: list[dict[str, Any]] = []

    for contact in contacts:
        hit = keyword_hit(contact, keywords)
        absent = missing_required_fields(contact, required)
        if hit["matched"] and not absent:
            kept.append(contact)
            continue
        reasons: list[str] = []
        if not hit["matched"]:
            reasons.append(
                f"no keyword matched ({', '.join(keywords)})"
                if keywords
                else "keyword filter is empty"
            )
        if absent:
            reasons.append(f"missing required field(s): {', '.join(absent)}")
        excluded.append(
            {
                "id": contact.get("id"),
                "name": contact.get("name"),
                "reasons": reasons,
                "keyword": hit["keyword"],
                "keywordField": hit["field"],
                "missingRequiredFields": absent,
            }
        )

    return {
        "contacts": kept,
        "considered": len(contacts),
        "included": len(kept),
        "excluded": excluded,
        "keywords": keywords,
        "requiredFields": required,
        "keywordFields": list(KEYWORD_FIELDS),
    }


def build_payload(
    *,
    company: Mapping[str, Any],
    lead_id: str,
    room_id: str | None,
    contacts: Sequence[Mapping[str, Any]],
    workflow: Mapping[str, Any],
    conditions: Mapping[str, Any],
    send_count: int,
    token: str | None,
    sent_at: str,
    contact_filter: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The exact JSON body that will be POSTed to the destination.

    ``company`` is the lead's own ``data``, sent whole and unprojected. That is
    the schema-flexibility rule showing up on the wire: a team that has put
    ``firmographics.hiringSignal`` on a lead gets that field in the payload
    without anybody here knowing the field exists.

    ``isUpdate`` and ``updateCount`` carry the researched update rule: "if you
    choose to send updates as well, you will receive the same lead with updated
    activity data if that lead visits your webpage again." A destination with two
    workflows pointed at one URL, or a warehouse that wants first-sight versus
    re-engagement, needs to tell those apart, and the only difference between the
    two bodies is the activity data plus these two fields.
    """
    mode = str(workflow.get("payload") or PAYLOAD_COMPANY)
    is_update = send_count > 0
    body: dict[str, Any] = {
        "type": PAYLOAD_TYPE,
        "sentAt": sent_at,
        "workflow": {
            "id": workflow.get("id"),
            "name": workflow.get("name"),
            "sendMode": workflow.get("sendMode"),
            "payload": mode,
        },
        "lead": {
            "id": lead_id,
            "roomId": room_id,
            "companyId": company.get("companyId"),
            "matchedSegmentIds": list(conditions.get("matchedIds") or []),
            "segmentMatch": conditions.get("match", MATCH_ANY),
            "isUpdate": is_update,
            "updateCount": send_count,
        },
        "company": dict(company),
    }
    if token:
        # The body copy exists for a destination that cannot read a header - the
        # Sheets recipe the research names is exactly that case. The header is
        # the primary channel; see the vocabulary note on TOKEN_HEADER.
        body[TOKEN_BODY_FIELD] = token

    if mode == PAYLOAD_COMPANY_CONTACTS:
        result = filter_contacts(contacts, contact_filter)
        body["contacts"] = [dict(contact) for contact in result["contacts"]]
        body["contactsIncluded"] = result["included"]
        body["contactsConsidered"] = result["considered"]
    return body


def contact_filter_summary(contact_filter: Mapping[str, Any] | None) -> dict[str, Any]:
    """The filter as the page and the delivery record both show it."""
    keywords = list((contact_filter or {}).get("keywords") or [])
    required = list((contact_filter or {}).get("requiredFields") or [])
    if not keywords and not required:
        return {
            "active": False,
            "keywords": [],
            "requiredFields": [],
            "keywordFields": list(KEYWORD_FIELDS),
            "note": (
                "No filter set, so every contact at the identified company is sent. "
                "The research describes the filter as an option."
            ),
        }
    return {
        "active": True,
        "keywords": keywords,
        "requiredFields": required,
        "keywordFields": list(KEYWORD_FIELDS),
        "note": (
            "A contact is sent when it matches at least one keyword "
            f"(searched across {', '.join(KEYWORD_FIELDS)}) and has every required field."
        ),
    }
