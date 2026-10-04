"""The lead list: in-market companies, filtered and ranked.

Flow step five, in full: "In the lead list, apply the **Pages** filter to isolate
companies that visited those pages; combine with Segment filters, tags, and the
ICP." And the data flow ends at "a **ranked list** of in-market companies with
contact candidates".

The Pages filter is the one the workflow is named for, so it is computed rather
than stored: a company matches a page when any path it has visited satisfies that
page's condition. Defining a page today therefore puts a company that visited the
path last month into the filter, and removing a page today takes it out again,
without a sweep and without a stale stored answer.

The ranking is the judgement call. The research says "ranked" and names no sort
key, so the reading and the alternatives are recorded in
:mod:`dsr.visitor_identification.inferences`. The order is total - page views,
then last visit, then the company key - so two runs over the same data return the
same list, which a rank on a floating quantity alone would not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from dsr.visitor_identification.company import detail_of
from dsr.visitor_identification.errors import InvalidIcp, UnknownLeadFilter
from dsr.visitor_identification.paths import matches
from dsr.visitor_identification.vocabulary import (
    COMPANY_FIELDS,
    ICP_PROFILES,
    LEAD_FILTERS,
)

#: The keys a caller may send as a lead filter. Anything else is refused by name,
#: because the filter set is the researched one and a seventh control has no
#: sourced meaning.
_LEAD_QUERY_KEYS = frozenset(LEAD_FILTERS) | {"limit"}

MAX_LIMIT = 200
DEFAULT_LIMIT = 50

#: The two things a saved ideal customer profile can be about. "size" is a named
#: company field and "country" is a named capture parameter, so both are facts this
#: workflow already holds about a company.
ICP_FIELDS: tuple[str, ...] = ("sizes", "countries")


@dataclass(frozen=True)
class LeadFilters:
    """One lead-list query: which companies, and how many."""

    pages: tuple[str, ...] = ()
    segment: str = ""
    tags: tuple[str, ...] = ()
    icp: str = ""
    country: str = ""
    size: str = ""
    limit: int = DEFAULT_LIMIT
    matched_pages: tuple[str, ...] = field(default=(), compare=False)

    def describe(self) -> dict[str, Any]:
        """The filter set as it was applied, for the response to carry."""
        return {
            "pages": list(self.pages),
            "segment": self.segment,
            "tags": list(self.tags),
            "icp": self.icp,
            "country": self.country,
            "size": self.size,
            "limit": self.limit,
        }


def parse_filters(query: dict[str, Any]) -> LeadFilters:
    """A lead-list query string, checked against the published filter set."""
    body = {str(key): value for key, value in (query or {}).items()}
    unknown = sorted(key for key in body if key not in _LEAD_QUERY_KEYS)
    if unknown:
        raise UnknownLeadFilter(
            f"{unknown[0]!r} is not a filter this lead list publishes. The researched filters are "
            "the Pages filter, Segment filters, tags, and the ICP."
        )

    return LeadFilters(
        pages=_strings(body.get("page")),
        segment=str(body.get("segment") or "").strip(),
        tags=_strings(body.get("tag")),
        icp=str(body.get("icp") or "").strip(),
        country=str(body.get("country") or "").strip(),
        size=str(body.get("size") or "").strip(),
        limit=_limit(body.get("limit")),
    )


def _strings(value: Any) -> tuple[str, ...]:
    """A repeatable query parameter, as a tuple.

    A query string carries a list by repeating a key, so one string is a list of
    one and a missing key is an empty list. A single scalar is accepted because a
    caller that builds the URL by hand sends one value.
    """
    if value is None:
        return ()
    entries = value if isinstance(value, (list, tuple)) else [value]
    out: list[str] = []
    for entry in entries:
        text = str(entry or "").strip()
        if text and text not in out:
            out.append(text)
    return tuple(out)


def _limit(value: Any) -> int:
    if value is None or value == "":
        return DEFAULT_LIMIT
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise UnknownLeadFilter(f"limit {value!r} is not a whole number") from exc
    return max(1, min(number, MAX_LIMIT))


def matched_page_ids(data: dict[str, Any], pages: list[dict[str, Any]]) -> list[str]:
    """Which of these pages a company has satisfied, in the order given.

    The company's own visited paths are the evidence, so a page added today and a
    visit from last month are compared at read time.
    """
    seen = list((data or {}).get("paths") or [])
    if not seen:
        return []
    out: list[str] = []
    for page in pages:
        path = page.get("path")
        if not path:
            continue
        if any(matches(page.get("condition"), path, visit) for visit in seen):
            page_id = str(page.get("id") or "")
            if page_id:
                out.append(page_id)
    return out


def _icp_criteria(payload: dict[str, Any]) -> dict[str, list[str]]:
    body = dict(payload or {})
    unknown = sorted(str(key) for key in body if key not in {"name", *ICP_FIELDS})
    if unknown:
        raise InvalidIcp(f"{unknown[0]!r} is not a field of an ideal customer profile.")
    criteria = {field: _strings(body.get(field)) for field in ICP_FIELDS}
    if not any(criteria[field] for field in ICP_FIELDS):
        raise InvalidIcp(
            "an ideal customer profile needs at least one criterion. Name the company sizes or the "
            "countries it covers, otherwise it includes every company and narrows nothing."
        )
    return criteria


def parse_icp(payload: dict[str, Any]) -> dict[str, Any]:
    """A saved ideal customer profile, checked."""
    body = dict(payload or {})
    name = str(body.get("name") or "").strip()
    if not name:
        raise InvalidIcp("an ideal customer profile needs a name. The lead-list filter shows it.")
    criteria = _icp_criteria(body)
    record = {"name": name}
    record.update(criteria)
    return record


def icp_matches(data: dict[str, Any], profile: dict[str, Any]) -> bool:
    """Does a company fall inside a saved ideal customer profile?

    Any of the values within one field, and every field: a profile naming three
    company sizes is a statement about a band of companies, not a demand that a
    company be all three at once, while a profile naming sizes *and* countries is
    a statement about companies in those bands *from* those countries. Reading
    either half as "any" would make the filter widen when a seller adds a
    criterion, which is the opposite of what adding a criterion is for.
    """
    payload = data or {}
    size = str(payload.get("size") or "").strip().lower()
    countries = {str(entry).strip().lower() for entry in payload.get("countries") or []}
    sizes = [str(wanted).strip().lower() for wanted in profile.get("sizes") or []]
    wanted = [str(entry).strip().lower() for entry in profile.get("countries") or []]
    if sizes and size not in sizes:
        return False
    if wanted and not countries.intersection(wanted):
        return False
    return True


def _passes(data: dict[str, Any], filters: LeadFilters) -> bool:
    payload = data or {}
    if filters.pages:
        if not set(filters.matched_pages).intersection(filters.pages):
            return False
    if filters.segment:
        if str(payload.get("segment") or "").strip().lower() != filters.segment.lower():
            return False
    if filters.tags:
        held = {str(tag).strip().lower() for tag in payload.get("tags") or []}
        if not held.issuperset({tag.lower() for tag in filters.tags}):
            return False
    if filters.country:
        countries = {str(entry).strip().lower() for entry in payload.get("countries") or []}
        if filters.country.strip().lower() not in countries:
            return False
    if filters.size:
        if str(payload.get("size") or "").strip().lower() != filters.size.strip().lower():
            return False
    return True


def build_rows(
    companies: list[dict[str, Any]],
    filters: LeadFilters,
    pages: list[dict[str, Any]],
    profile: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """The ranked lead list for one filter set.

    Ranking is page views, then last visit, then the company key. The third term
    is not decoration: without it two companies with the same count and the same
    last visit have no defined order, and a table that reshuffles between two
    requests over the same data cannot be read.
    The three passes below are one sort written most-significant-last, because
    :func:`sorted` is stable and a single key would have to invert the timestamp
    as text to make a larger moment sort earlier.
    """
    rows: list[dict[str, Any]] = []
    for record in companies:
        data = record.get("data") or {}
        matched = matched_page_ids(data, pages)
        scoped = LeadFilters(
            pages=filters.pages,
            segment=filters.segment,
            tags=filters.tags,
            icp=filters.icp,
            country=filters.country,
            size=filters.size,
            limit=filters.limit,
            matched_pages=tuple(matched),
        )
        if not _passes(data, scoped):
            continue
        if profile is not None and not icp_matches(data, profile):
            continue
        row = {
            "id": record.get("id"),
            "revision": record.get("revision"),
        }
        row.update(detail_of(data))
        row.update(
            {
                "company_key": data.get("company_key") or "",
                "segment": data.get("segment") or "",
                "tags": list(data.get("tags") or []),
                "countries": list(data.get("countries") or []),
                "page_views": int(data.get("page_views") or 0),
                "distinct_paths": len(data.get("paths") or []),
                "contact_count": len(data.get("contacts") or []),
                "first_seen_at": data.get("first_seen_at") or "",
                "last_visit_at": data.get("last_visit_at") or "",
                "matched_pages": list(matched),
            }
        )
        rows.append(row)

    # Least significant first. Company key ascending is the total order, last
    # visit descending is the researched recency, page views descending leads.
    rows.sort(key=lambda row: str(row["company_key"]))
    rows.sort(key=lambda row: str(row["last_visit_at"] or ""), reverse=True)
    rows.sort(key=lambda row: -int(row["page_views"]))
    return rows[: filters.limit]


def profile_of(store, icp_id: str) -> dict[str, Any] | None:
    """The saved profile with that id, or None.

    The caller turns None into a refusal: a filter naming a profile that is not
    there must not quietly include every company, because the seller would be
    looking at an unfiltered list believing it was a targeted one.
    """
    if not icp_id:
        return None
    for record in store.list(ICP_PROFILES, limit=1000):
        if record.get("id") == icp_id:
            return record.get("data") or {}
    return None


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The numbers the lead list reports above itself."""
    return {
        "companies": len(rows),
        "page_views": sum(int(row.get("page_views") or 0) for row in rows),
        "with_contact_candidates": sum(1 for row in rows if int(row.get("contact_count") or 0)),
        "countries": sorted(
            {str(country) for row in rows for country in row.get("countries") or []}
        ),
        "sizes": sorted({str(row.get("size")) for row in rows if row.get("size")}),
        "researched_fields": list(COMPANY_FIELDS),
    }
