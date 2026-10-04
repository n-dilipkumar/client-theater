"""The company record, and how one anonymous request is matched to it.

The data flow is the whole of this module: "Anonymous website request ->
tracking script reads IP address, country, network and other public parameters ->
matched against the company database -> a **company-level** record is created
(never an individual user) -> page-visit events recorded per URL path".

Two shapes are derived from the one sentence the research gives about what a
company record holds, and both are recorded in
:mod:`dsr.visitor_identification.inferences`:

*The five fields.* "The insights provided include the company's name, website,
address, size, and a list of employees or contacts associated with the company."
Those five are :data:`~dsr.visitor_identification.vocabulary.COMPANY_FIELDS`, in
that order. A company the workflow has only just identified has none of them yet,
and that is not an error: it is what an unidentified company looks like.

*What identifies a company.* A network, never a person. The key is derived from
the network when the capture carries one and from the address when it does not,
and the lookup tries the narrowest evidence first: an exact address already on
file, then the network. Two facts about a network do not add up to a person, so
there is nothing here that could resolve a capture to an individual.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from dsr.store import RecordStore
from dsr.visitor_identification.capture import Capture
from dsr.visitor_identification.errors import (
    InvalidCompanyDetail,
    UnknownCompany,
)
from dsr.visitor_identification.vocabulary import (
    COMPANIES,
    COMPANY_FIELDS,
    CONTACT_FIELDS,
)

#: What may appear in a company key. A key lands in a URL path, so anything that
#: would need escaping - a ``/`` in a CIDR block above all - is folded to a dash.
_KEY_SAFE = re.compile(r"[^a-z0-9._-]+")

#: How much of the network digest is carried when two networks fold to one key.
_DIGEST_LENGTH = 6

#: The five researched detail fields, and the two others the lead list filters
#: on. The last is "Segment", the second last is "tagged leads"; both are named in
#: "apply the Pages filter ... combine with Segment filters, tags, and the ICP".
LIST_FIELDS = ("tags",)


def slug(value: str) -> str:
    """A URL-safe key for a network or an address.

    ``203.0.113.0/24`` becomes ``203.0.113.0-24``. Two different raw values can
    fold to one key - ``a/b`` and ``a-b`` both become ``a-b`` - so when a key is
    already taken by a different raw value, a short digest of the raw value is
    appended. Deterministic, so the same network always derives the same key.
    """
    text = _KEY_SAFE.sub("-", str(value or "").strip().lower()).strip("-")
    return text or "unknown"


def keyed(value: str) -> str:
    """:func:`slug`, plus a digest of the raw value whenever a character was folded.

    ``203.0.113.0/24`` becomes ``203.0.113.0-24-1f3c9a``. The digest is there because
    folding is lossy - ``a/b`` and ``a-b`` both fold to ``a-b`` - and a company key
    that names the wrong network attributes one company's traffic to another. A
    value that needs no folding is used as it is, because ``198.51.100.24`` and
    ``203.0.113.0/24`` read well in a URL and in an audit row.

    Deterministic: the same raw value always derives the same key, so a second
    capture from the same network finds the company the first one created.
    """
    raw = str(value or "").strip().lower()
    folded = slug(raw)
    if folded == raw:
        return folded
    return f"{folded}-{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:_DIGEST_LENGTH]}"


def derive_company_key(capture: Capture) -> str:
    """The key of the company a capture names, before any lookup.

    The network first, because a network is the coarser fact and the one the
    research names twice: the script "reads IP address, country, network" and it
    matches "against the company database". An address is the fallback for a
    capture that carries only one.
    """
    return keyed(capture.network or capture.ip_address)


def find_company(store: RecordStore, capture: Capture) -> dict[str, Any] | None:
    """The company a capture belongs to, or None.

    Narrowest evidence first. An exact address already on file beats the network,
    because a capture that arrives from an address we have already attributed to a
    company is that company whatever else it says, and a company can hold several
    networks.

    Both lookups go through ``find()``, which resolves dotted JSON paths through
    the dynamic index. That is why the address and network sets are stored as
    objects keyed by the value rather than as lists: ``_flatten`` gives an array
    member a *positional* path (``known_ips.3``), which cannot answer "is this
    address on file?". A map keyed by the value gives ``known_ips.198.51.100.24``,
    which can.
    """
    if capture.ip_address:
        hit = store.find(COMPANIES, {f"known_ips.{capture.ip_address}": True}, limit=10)
        if hit:
            return hit[0]
    if capture.network:
        hit = store.find(
            COMPANIES,
            {f"known_networks.{keyed(capture.network)}": capture.network.lower()},
            limit=10,
        )
        if hit:
            return hit[0]
    return None


def blank_company(capture: Capture, key: str) -> dict[str, Any]:
    """The payload of a company the workflow has just identified for the first time.

    Every researched detail field is present and empty, not absent. The drill-down
    names five columns, and a record missing them renders as five blanks with no
    way to tell "not known yet" from "not stored".
    """
    return {
        "company_key": key,
        "identified_from": "capture",
        "name": "",
        "website": "",
        "address": "",
        "size": "",
        "contacts": [],
        "segment": "",
        "tags": [],
        "countries": [],
        "known_ips": {},
        "known_networks": {},
        "page_views": 0,
        "paths": [],
        "first_seen_at": capture.captured_at,
        "last_visit_at": capture.captured_at,
    }


def with_capture(data: dict[str, Any], capture: Capture) -> dict[str, Any]:
    """The company payload after one more visit from this network.

    Returns a new mapping rather than mutating the one it was handed, so the
    caller can pass it straight to ``store.update`` and the audit row records the
    before and after.
    """
    payload = dict(data or {})
    known_ips = dict(payload.get("known_ips") or {})
    known_networks = dict(payload.get("known_networks") or {})
    countries = list(payload.get("countries") or [])
    paths = list(payload.get("paths") or [])

    if capture.ip_address:
        known_ips[capture.ip_address] = True
    if capture.network:
        known_networks[keyed(capture.network)] = capture.network.lower()
    if capture.country:
        country = capture.country.strip()
        if len(country) == 2:
            country = country.upper()
        if country and country not in countries:
            countries.append(country)
    if capture.path not in paths:
        paths.append(capture.path)

    payload["known_ips"] = known_ips
    payload["known_networks"] = known_networks
    payload["countries"] = countries
    payload["paths"] = paths
    payload["page_views"] = int(payload.get("page_views") or 0) + 1
    payload["last_visit_at"] = capture.captured_at
    payload["first_seen_at"] = payload.get("first_seen_at") or capture.captured_at
    return payload


def resolve(store: RecordStore, capture: Capture) -> tuple[dict[str, Any] | None, str]:
    """The company a capture belongs to, and the key it would take.

    Two values rather than a write, because creating the record is a decision the
    caller of this function makes with an audit source attached. Returns
    ``(None, key)`` for a capture from a network this portal has not identified.
    """
    existing = find_company(store, capture)
    if existing is not None:
        return existing, str(existing["data"].get("company_key") or "")
    return None, derive_company_key(capture)


def require_company(store: RecordStore, company_key: str) -> dict[str, Any]:
    """One company, or a refusal that says which key was not found."""
    rows = store.find(COMPANIES, {"company_key": company_key}, limit=5)
    for row in rows:
        if row["data"].get("company_key") == company_key:
            return row
    raise UnknownCompany(
        f"company {company_key!r} has not been identified. A company appears when a capture "
        "arrives from its network, or when it is added by hand."
    )


def parse_detail(payload: dict[str, Any]) -> dict[str, Any]:
    """The researched company fields a caller may set, checked.

    Only the five named fields, the segment, and the tags are accepted. An
    unknown key is refused rather than merged, because "record payloads are
    arbitrary JSON" cuts the other way from a *named* field: this endpoint exists
    to publish the researched set, and a client that wants its own field should
    write it through the generic record API rather than through a workflow that
    validates a different list.
    """
    body = dict(payload or {})
    known = set(COMPANY_FIELDS) | {"segment", "tags", *LIST_FIELDS}
    unknown = sorted(str(key) for key in body if key not in known)
    if unknown:
        raise InvalidCompanyDetail(
            f"{unknown[0]!r} is not a company field this workflow publishes. The researched fields "
            "are the name, the website, the address, the size, and the list of contacts, plus the "
            "segment and the tags the lead list filters on."
        )

    detail: dict[str, Any] = {}
    for field in ("name", "website", "address", "size"):
        if field not in body:
            continue
        detail[field] = _text_field(body[field], field)
    if "segment" in body:
        detail["segment"] = _text_field(body["segment"], "segment")
    if "tags" in body:
        detail["tags"] = _string_list(body["tags"], "tags")
    if "contacts" in body:
        detail["contacts"] = parse_contacts(body["contacts"])
    return detail


def _text_field(value: Any, field: str) -> str:
    """A named text field. Present but empty is refused, not cleared.

    "The insights provided include the company's name, website, address, size" -
    a value that is present and blank renders as a column a seller has to guess
    about. Sending the field at all is a statement that it is known.
    """
    if value is None:
        raise InvalidCompanyDetail(f"{field} cannot be null. Send the field only when it is known.")
    if isinstance(value, bool) or isinstance(value, (dict, list)):
        raise InvalidCompanyDetail(f"{field} must be text, got {type(value).__name__}")
    text = str(value).strip()
    if not text:
        raise InvalidCompanyDetail(
            f"{field} cannot be empty. Send the field only when it is known."
        )
    if field == "website" and not text.lower().startswith(("http://", "https://")):
        raise InvalidCompanyDetail(
            f"website {text!r} is not a URL. A website is the company's site, so it starts with "
            "http:// or https://."
        )
    return text


def _string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list):
        raise InvalidCompanyDetail(f"{field} must be a list of text, got {type(value).__name__}")
    out: list[str] = []
    for entry in value:
        if not isinstance(entry, str) or not entry.strip():
            raise InvalidCompanyDetail(
                f"{field} holds {entry!r}. Every entry must be non-empty text."
            )
        text = entry.strip()
        if text not in out:
            out.append(text)
    return out


def parse_contacts(value: Any) -> list[dict[str, str]]:
    """The researched contact list: a list of employees or contacts at the company.

    Two keys per contact, both named: the person, and the role - the "Role" of the
    researched auto-engage target-group selector. No email address, because this
    workflow's research does not name one and a contact row with an address in it
    would be the first person-level field in the package.
    """
    if not isinstance(value, list):
        raise InvalidCompanyDetail(f"contacts must be a list of people, got {type(value).__name__}")
    contacts: list[dict[str, str]] = []
    for entry in value:
        if not isinstance(entry, dict):
            raise InvalidCompanyDetail(f"contacts holds {entry!r}. Every entry must be a person.")
        unknown = sorted(str(key) for key in entry if key not in CONTACT_FIELDS)
        if unknown:
            raise InvalidCompanyDetail(
                f"a contact holds {unknown[0]!r}. A contact carries a name and a role, and nothing "
                "else."
            )
        name = str(entry.get("name") or "").strip()
        if not name:
            raise InvalidCompanyDetail("every contact needs a name")
        contact: dict[str, str] = {"name": name}
        role = str(entry.get("role") or "").strip()
        if role:
            contact["role"] = role
        contacts.append(contact)
    return contacts


def detail_of(data: dict[str, Any]) -> dict[str, Any]:
    """The five researched fields, as the drill-down shows them.

    Present and possibly empty, never absent: the drill-down has five columns and
    a company that has just been identified has five blanks in them.
    """
    payload = data or {}
    return {
        "name": payload.get("name") or "",
        "website": payload.get("website") or "",
        "address": payload.get("address") or "",
        "size": payload.get("size") or "",
        "contacts": list(payload.get("contacts") or []),
    }


def counters_of(data: dict[str, Any]) -> dict[str, Any]:
    """The identified-company counters, and the sets behind the lookups."""
    payload = data or {}
    return {
        "company_key": payload.get("company_key") or "",
        "segment": payload.get("segment") or "",
        "tags": list(payload.get("tags") or []),
        "countries": list(payload.get("countries") or []),
        "page_views": int(payload.get("page_views") or 0),
        "distinct_paths": len(payload.get("paths") or []),
        "first_seen_at": payload.get("first_seen_at") or "",
        "last_visit_at": payload.get("last_visit_at") or "",
        "known_ips": sorted(payload.get("known_ips") or {}),
        "known_networks": sorted((payload.get("known_networks") or {}).values()),
    }
