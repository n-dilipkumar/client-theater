"""Account and contact resolution: from a company to an opportunity and a name.

The researched data flow puts this step between threshold evaluation and alert
dispatch, and it is where the workflow's two dependencies meet. The
visitor-identification workflow (WF-031) provisioned the company-level visitor
record. The CRM-read workflow (WF-042) provisioned the opportunity, the account
and the contact. This module joins them.

The join is the interesting part, and it is honest about its own weakness. WF-042
keys its records by the vendor's record id and WF-031 keys its companies by a
network, and there is no field on either side that carries the other's key. So the
match here is a **string match on the company website, falling back to a
normalised company name**. That is stated in every resolution result, served at
``GET /api/wf-133/inferences``, and it is why an unmatched company is refused with
409 rather than routed to a recipient nobody chose.

The research states that reverse-IP company resolution "is the one piece with no
clean OSS equivalent". This build therefore calls no vendor at all. It reads the
company WF-031 already identified and refuses anything it cannot resolve. That is
a recorded decision, not an omission: reaching for a paid provider here would put
a per-event cost and a licence inside the one step that has to be answerable from
the audit log alone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from dsr.intent_routing.errors import UnknownCompany
from dsr.intent_routing.vocabulary import TEAM_PATHS
from dsr.store import RecordStore

#: WF-031's collections. Both are portal-wide, so neither is read with a room
#: filter: a company seen in another seller's room is still the same company.
IDENTIFIED_COMPANY = "identified_company"
COMPANY_PAGE_VISIT = "company_page_visit"

#: WF-042's collections. Both are room-scoped, and a room-scoped ``list`` emits
#: ``AND room_id = ?``, so a row written without a room is invisible here on
#: purpose rather than by accident.
CRM_IDENTITY = "crm_read_identity"
CRM_RECORD = "crm_read_record"

#: The vendor column names an account's web address arrives under. Different
#: vendors spell it differently and the research names Salesforce, Dataverse and
#: HubSpot, so the set is read rather than assumed.
WEBSITE_FIELDS: tuple[str, ...] = ("Website", "website", "Domain", "domain", "URL", "url")
NAME_FIELDS: tuple[str, ...] = ("Name", "name", "AccountName", "account_name", "Company")

#: Words a company name carries that are not part of the name itself. Stripping
#: them is what makes "Northwind Traders Ltd" and "Northwind Traders Limited" one
#: match instead of two misses.
_NAME_NOISE = re.compile(
    r"\b(limited|ltd|llc|plc|inc|incorporated|gmbh|ag|bv|sa|sas|pty|co|company|"
    r"corporation|corp|group|holdings?|international)\b",
    re.IGNORECASE,
)
_NON_WORD = re.compile(r"[^a-z0-9]+")

#: How the account was matched. Served on every resolution so a reader never has
#: to guess whether a match was a vendor id or a string.
MATCH_WEBSITE = "website_domain"
MATCH_NAME = "company_name"
MATCH_OWNER = "crm_owner"
MATCH_TEAM = "crm_team"
MATCH_WATCHLIST = "watchlist"
MATCH_FALLBACK = "fallback"
MATCHES: tuple[str, ...] = (
    MATCH_WEBSITE,
    MATCH_NAME,
    MATCH_OWNER,
    MATCH_TEAM,
    MATCH_WATCHLIST,
    MATCH_FALLBACK,
)

MATCH_UNKNOWN = "unmatched"


@dataclass(frozen=True)
class Account:
    """A company, and whatever the CRM could be told about it.

    ``opportunity_id`` and ``owner_id`` empty with ``matched`` set to
    ``unmatched`` is the normal state for a company nobody has imported yet, and it
    is the state the engine refuses rather than routes.
    """

    company_key: str
    company_name: str
    website: str
    size: str
    segment: str
    countries: tuple[str, ...] = ()
    contacts: tuple[dict[str, str], ...] = ()
    page_views: int = 0
    paths: tuple[str, ...] = ()
    last_visit_at: str = ""
    matched: str = MATCH_UNKNOWN
    crm_system: str = ""
    account_id: str = ""
    opportunity_id: str = ""
    owner_id: str = ""
    owner_email: str = ""
    owner_team: str = ""

    @property
    def resolved(self) -> bool:
        """Whether a follow-up task has an opportunity to hang from.

        The researched flow ends with a task on the opportunity, so this is the one
        property that decides whether the workflow can complete its own flow.
        """
        return bool(self.opportunity_id)

    @property
    def accountable(self) -> str:
        """Whoever is accountable for this account right now.

        The opportunity owner when the CRM named one, the owner team otherwise,
        and the account itself when neither is on file. Used as the recipient
        identity when no address is available, so an alert always names a person
        or a team even when it cannot address them.
        """
        return self.owner_id or self.owner_team or self.company_key

    def host(self) -> str:
        """The website's host, lower-cased, or an empty string."""
        return host_of(self.website)

    def to_dict(self) -> dict[str, Any]:
        return {
            "company_key": self.company_key,
            "company_name": self.company_name,
            "website": self.website,
            "host": self.host(),
            "size": self.size,
            "segment": self.segment,
            "countries": list(self.countries),
            "contacts": [dict(entry) for entry in self.contacts],
            "page_views": self.page_views,
            "paths": list(self.paths),
            "last_visit_at": self.last_visit_at,
            "matched": self.matched,
            "resolved": self.resolved,
            "accountable": self.accountable,
            "crm_system": self.crm_system,
            "account_id": self.account_id,
            "opportunity_id": self.opportunity_id,
            "owner_id": self.owner_id,
            "owner_email": self.owner_email,
            "owner_team": self.owner_team,
        }


def host_of(url: str) -> str:
    """The host of a web address, lower-cased and without a leading ``www.``.

    Written by hand rather than pulled from ``urllib.parse`` because this package
    depends on nothing but the store, and a hand-rolled host reader for two inputs
    is smaller than the import would justify. Anything that is not recognisably a
    host comes back empty, which makes it a non-match rather than a wrong match.
    """
    text = (url or "").strip().lower()
    if not text:
        return ""
    if "://" in text:
        text = text.split("://", 1)[1]
    text = text.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    if "@" in text:
        text = text.rsplit("@", 1)[1]
    text = text.split(":", 1)[0].strip().strip(".")
    if not text or "." not in text or " " in text:
        return ""
    if text.startswith("www."):
        text = text[4:]
    return text


def normalise_name(name: str) -> str:
    """A company name reduced to the words that identify it.

    Lower-cased, punctuation collapsed to single spaces, and the legal-form words
    removed. Two names that differ only in their legal suffix are one company, and
    a CRM that spells the suffix differently from a website that omits it is the
    ordinary case rather than the exception.
    """
    text = _NAME_NOISE.sub(" ", (name or "").lower())
    text = _NON_WORD.sub(" ", text)
    return " ".join(text.split())


def _first_field(fields: dict[str, Any], names: tuple[str, ...]) -> str:
    for name in names:
        value = fields.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _contacts(raw: Any) -> tuple[dict[str, str], ...]:
    """The researched contact list, read as name and role only.

    WF-031 refuses an email address on a company contact by design, so there is no
    address to read here even if a caller sent one. The list is kept because the
    alert context names the stakeholder, and a role with no name is not a
    stakeholder.
    """
    if not isinstance(raw, list):
        return ()
    kept: list[dict[str, str]] = []
    for entry in raw:
        if isinstance(entry, dict):
            name = str(entry.get("name") or "").strip()
            role = str(entry.get("role") or "").strip()
        elif isinstance(entry, str):
            name, role = entry.strip(), ""
        else:
            continue
        if name or role:
            kept.append({"name": name, "role": role})
    return tuple(kept)


def find_company(store: RecordStore, company_key: str) -> dict[str, Any] | None:
    """The WF-031 company record for a key, or ``None``.

    Read by key rather than through WF-031's own resolver, because WF-031
    resolves an *address* to a company and this workflow is handed the key that
    address already resolved to. Reading the record is what makes the dependency
    one-directional: WF-031 does not know this workflow exists.
    """
    for record in store.find(IDENTIFIED_COMPANY, {"company_key": company_key}, limit=5):
        if str(record.get("data", {}).get("company_key")) == company_key:
            return record
    return None


def require_company(store: RecordStore, company_key: str) -> dict[str, Any]:
    """The WF-031 company record for a key, or refuse.

    Unknown is 404 rather than 409: the caller named something that is not there,
    which is a different fault from naming something that is there but cannot be
    routed.
    """
    record = find_company(store, company_key)
    if record is None:
        raise UnknownCompany(
            f"no identified company is on file under {company_key!r}. This workflow routes an "
            "alert to a company the visitor-identification workflow has already identified, and "
            "it refuses to guess one."
        )
    return record


def _unresolved(company_key: str, data: dict[str, Any]) -> Account:
    return Account(
        company_key=company_key,
        company_name=str(data.get("name") or "").strip(),
        website=str(data.get("website") or "").strip(),
        size=str(data.get("size") or "").strip(),
        segment=str(data.get("segment") or "").strip(),
        countries=tuple(str(entry).upper() for entry in (data.get("countries") or []) if entry),
        contacts=_contacts(data.get("contacts")),
        page_views=int(data.get("page_views") or 0),
        paths=tuple(str(entry) for entry in (data.get("paths") or []) if entry),
        last_visit_at=str(data.get("last_visit_at") or ""),
        matched=MATCH_UNKNOWN,
    )


def crm_owner_team(row: dict[str, Any]) -> str:
    """Read a territory from a CRM record, tolerating its absence.

    WF-042 defines no team field. It copies unknown payload keys through untouched,
    so a team is whatever an importing team put there, and these are the two
    spellings this product has seen: nested under ``crm_owner.team`` and flat as
    ``team``. An absent team is empty, not an error, because most records have none.
    """
    for path in TEAM_PATHS:
        cursor: Any = row
        for step in path:
            if not isinstance(cursor, dict):
                cursor = None
                break
            cursor = cursor.get(step)
        if isinstance(cursor, str) and cursor.strip():
            return cursor.strip()
    return ""


def _owner_email(rows: list[dict[str, Any]], owner_id: str) -> str:
    """An address for the owner, if any CRM row carries one.

    Most do not. WF-042 keys an owner by the vendor's opaque owner id and does not
    carry the owner's address, and vendors put the *buyer's* address on a contact
    row, which is a different person. So this returns an empty string in the
    ordinary case, and the engine's dispatch path treats that as "no address on
    file" rather than sending to the buyer.
    """
    if not owner_id:
        return ""
    for row in rows:
        if str(row.get("owner_id") or "") != owner_id:
            continue
        fields = row.get("fields") or {}
        address = row.get("owner_email") or fields.get("OwnerEmail") or fields.get("owner_email")
        if isinstance(address, str) and "@" in address:
            return address.strip().lower()
    return ""


def _crm_rows(store: RecordStore, room_id: str) -> list[dict[str, Any]]:
    """Every CRM record in a room, with its data lifted to the top level.

    Read without a ``where`` filter on purpose: the filter keys are vendor column
    names nested inside ``fields``, and the dynamic index resolves dotted paths
    into ``data`` rather than into a payload the caller has not shaped yet. A room
    holds a few hundred of these, so the scan is bounded and done once per
    resolution.
    """
    rows: list[dict[str, Any]] = []
    for record in store.list(CRM_RECORD, room_id=room_id, limit=1000):
        data = record.get("data") or {}
        rows.append({**data, "_record_id": record.get("id"), "room_id": record.get("room_id")})
    return rows


def match_account(
    rows: list[dict[str, Any]], account: Account
) -> tuple[dict[str, Any], str] | None:
    """Find the CRM account row for a company, and say how it was matched.

    Website host first, then normalised company name. The host is tried first
    because a domain is a single canonical identifier while a name is a string a
    human typed twice, possibly differently.

    The method is returned rather than recomputed by the caller because "matched"
    has to name the evidence that was actually used. A company whose website
    happens to be set and whose name is what matched would otherwise be reported as
    a website match, and a rep reading that would believe a guarantee the code did
    not make.
    """
    if not rows:
        return None

    wanted_host = account.host()
    if wanted_host:
        for row in rows:
            if str(row.get("object") or "") != "account":
                continue
            fields = row.get("fields") or {}
            if host_of(_first_field(fields, WEBSITE_FIELDS)) == wanted_host:
                return row, MATCH_WEBSITE

    wanted_name = normalise_name(account.company_name)
    if wanted_name:
        for row in rows:
            if str(row.get("object") or "") != "account":
                continue
            fields = row.get("fields") or {}
            if normalise_name(_first_field(fields, NAME_FIELDS)) == wanted_name:
                return row, MATCH_NAME
    return None


def resolve(store: RecordStore, company_key: str, *, room_id: str) -> Account:
    """Read the company, then attach whatever the CRM can be matched to.

    Returns an unresolved ``Account`` rather than raising when the company is known
    but no opportunity can be attached. Deciding whether an unresolved account is a
    refusal is the engine's call, not this function's, because the engine also
    knows whether the account is on a watchlist.
    """
    record = require_company(store, company_key)
    data = record.get("data") or {}
    account = _unresolved(company_key, data)

    rows = _crm_rows(store, room_id)
    matched = match_account(rows, account)
    if matched is None:
        return account
    account_row, method = matched

    account_id = str(account_row.get("external_id") or "")
    system = str(account_row.get("system") or "")

    opportunity_id = ""
    owner_id = str(account_row.get("owner_id") or "")
    identity_row = _identity_for(store, room_id, account_id)
    if identity_row is not None:
        opportunity_id = str(identity_row.get("deal_id") or "")
        owner_id = owner_id or str(identity_row.get("owner_id") or "")
        system = system or str(identity_row.get("system") or "")
    if not opportunity_id:
        deal_row = next((row for row in rows if str(row.get("object") or "") == "deal"), None)
        if deal_row is not None and _same_account(deal_row, account_id):
            opportunity_id = str(deal_row.get("external_id") or "")
            owner_id = owner_id or str(deal_row.get("owner_id") or "")

    return Account(
        company_key=account.company_key,
        company_name=account.company_name,
        website=account.website,
        size=account.size,
        segment=account.segment,
        countries=account.countries,
        contacts=account.contacts,
        page_views=account.page_views,
        paths=account.paths,
        last_visit_at=account.last_visit_at,
        matched=method,
        crm_system=system,
        account_id=account_id,
        opportunity_id=opportunity_id,
        owner_id=owner_id,
        owner_email=_owner_email(rows, owner_id),
        owner_team=crm_owner_team(account_row),
    )


def _same_account(deal_row: dict[str, Any], account_id: str) -> bool:
    """Whether a deal row belongs to an account.

    Vendors name the relationship differently, so every spelling this product has
    seen is read. A vendor that names none of them simply means the opportunity is
    not resolvable from the account, and the engine refuses rather than guessing.
    """
    if not account_id:
        return False
    fields = deal_row.get("fields") or {}
    for name in ("AccountId", "Account", "AccountID", "account_id", "ParentAccountId"):
        value = fields.get(name)
        if isinstance(value, str) and value.strip() == account_id:
            return True
        if isinstance(value, dict):
            nested = value.get("Id") or value.get("id")
            if isinstance(nested, str) and nested.strip() == account_id:
                return True
    return False


def _identity_for(store: RecordStore, room_id: str, account_id: str) -> dict[str, Any] | None:
    """The room's CRM identity for an account, if one is registered."""
    if not account_id:
        return None
    for record in store.list(CRM_IDENTITY, room_id=room_id, limit=1000):
        data = record.get("data") or {}
        if str(data.get("account_id") or "") == account_id:
            return dict(data)
    return None


def stakeholder_of(engagement: Any, account: Account) -> dict[str, str]:
    """Name the stakeholder for the alert context.

    The observation wins over the company contact list, because the observation
    is what this caller saw in this room and the contact list is whatever was
    entered once. When neither names one, the entry says so rather than
    substituting the company: an alert that claims a stakeholder it does not have
    is worse than one that admits the gap.
    """
    name = str(getattr(engagement, "stakeholder", "") or "").strip()
    role = str(getattr(engagement, "stakeholder_role", "") or "").strip()
    source = "observation"
    if not name and account.contacts:
        name = account.contacts[0].get("name", "")
        role = role or account.contacts[0].get("role", "")
        source = "company_contact"
    if not name:
        return {
            "name": "",
            "role": "",
            "source": "unknown",
            "email": "",
            "identified": "false",
        }
    return {"name": name, "role": role, "source": source, "email": "", "identified": "true"}


def last_visit(store: RecordStore, company_key: str) -> dict[str, Any] | None:
    """The most recent page visit WF-031 recorded for a company.

    Read so the alert can say when the buyer was last seen rather than only what
    they did, which is the difference between an alert a rep can act on and one
    they have to investigate first.
    """
    visits: list[dict[str, Any]] = store.find(
        COMPANY_PAGE_VISIT, {"company_key": company_key}, limit=200
    )
    if not visits:
        return None
    # Sorted in Python rather than in SQL because ``find`` takes no ordering: it
    # filters through the dynamic index, which has no column to sort on. Two
    # ISO 8601 strings from the same offset sort correctly as text, and WF-031
    # refuses a capture whose moment carries no offset, so the strings compare.
    newest = max(
        visits, key=lambda record: str((record.get("data") or {}).get("captured_at") or "")
    )
    data = newest.get("data") or {}
    return {
        "path": str(data.get("path") or ""),
        "captured_at": str(data.get("captured_at") or ""),
        "country": str(data.get("country") or ""),
    }


__all__ = [
    "COMPANY_PAGE_VISIT",
    "CRM_IDENTITY",
    "CRM_RECORD",
    "IDENTIFIED_COMPANY",
    "MATCHES",
    "MATCH_FALLBACK",
    "MATCH_NAME",
    "MATCH_OWNER",
    "MATCH_TEAM",
    "MATCH_UNKNOWN",
    "MATCH_WATCHLIST",
    "MATCH_WEBSITE",
    "Account",
    "crm_owner_team",
    "find_company",
    "host_of",
    "last_visit",
    "match_account",
    "normalise_name",
    "require_company",
    "resolve",
    "stakeholder_of",
]
