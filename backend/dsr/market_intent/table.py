"""The buyer-intent table: one row per company, computed from what was observed.

Sourced
-------
"Buyer intent stores company-level website activity in a table, including
details such as website visits, unique visitors, last visit, and top page views."
and, on the record-level card, "Website visits: the count sessions of website
visits from this company. Unique visitors ... Last seen ... Top page views: the
pages with the most visits from visitors from this company."

Four things follow from those two sentences, and they are the four columns:

* **Website visits counts sessions, not page views.** One session of six page
  views is one visit, so the counter is distinct ``session_id``. A row that
  counted page views here would report a visitor who read a pricing page six
  times as having visited six times, which is the number a seller acts on.
* **Unique visitors counts identities, not visits.** Distinct ``visitor_id``, so
  one person in three sessions is one visitor.
* **Last seen is the newest observation of any kind**, which is what the
  Research tab's companies need too - a company first seen researching a topic
  has a last seen and no last visit, and reporting a dash for it would hide the
  company the research is about.
* **Top page views orders by count, and the tie has to be broken.** Two paths
  with the same number of visits are ordered by most recent and then by path, so
  the column does not reorder itself between two reads of the same data.

Every row is computed, never stored. There is no ``intent_company_row``
collection holding a cached aggregate, because a cached aggregate is a number
that can disagree with the visits behind it and there would be no way to tell
which one was lying. The cost is a read of the visit collection per table load,
which is bounded by ``names.SCAN_LIMIT`` and reported when it bites.

Research intent
---------------
"It can also surface broader intent signals beyond your website, such as
companies researching topics across the web or company news like funding,
executive hires, layoffs, product launches, and mergers." Both are intent, so
``research_intent`` is true for either, and ``research_evidence`` says which -
so the Overview can break out "companies showing research intent" without a
company that raised money being reported as a company that read about cloud
security.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from dsr.market_intent import domains
from dsr.market_intent.criteria import Criterion, qualify_view
from dsr.market_intent.names import EVIDENCE_LIMIT, TOP_PAGE_LIMIT
from dsr.market_intent.timeframe import Window


@dataclass
class Snapshot:
    """Everything the table is built from, read once by the engine.

    Passed in rather than read here so that the projection is a pure function of
    its inputs: a test can build a snapshot by hand, and a route can build one
    from the store without the table knowing the store exists.
    """

    visits: list[dict[str, Any]] = field(default_factory=list)
    research: list[dict[str, Any]] = field(default_factory=list)
    companies: dict[str, dict[str, Any]] = field(default_factory=dict)
    contacts: list[dict[str, Any]] = field(default_factory=list)
    criteria: list[Criterion] = field(default_factory=list)
    topics: list[dict[str, Any]] = field(default_factory=list)
    markets: list[dict[str, Any]] = field(default_factory=list)
    exclusions: dict[str, dict[str, Any]] = field(default_factory=dict)
    trackings: dict[str, dict[str, Any]] = field(default_factory=dict)
    enrolments: list[dict[str, Any]] = field(default_factory=list)
    truncated: dict[str, bool] = field(default_factory=dict)


def _sort_key(moment: Any) -> str:
    return str(moment or "")


def _by_newest(records: Iterable[Mapping[str, Any]], field_name: str = "occurred_at") -> list[Mapping[str, Any]]:
    return sorted(records, key=lambda row: _sort_key(row.get(field_name)), reverse=True)


def market_for(
    company_countries: set[str],
    company_industry: str,
    markets: Sequence[Mapping[str, Any]],
) -> list[str]:
    """Which target markets a company is in.

    "In my target markets" is the filter the two net-new stock categories both
    name, so it has to be answerable for a company the CRM knows nothing about -
    a company discovered only through a research observation carries a country
    and possibly an industry and no CRM record at all.
    """
    countries = {value.upper() for value in company_countries if value}
    industry = (company_industry or "").strip().lower()
    matched: list[str] = []
    for market in markets:
        wanted_countries = {
            str(value).strip().upper() for value in (market.get("countries") or []) if str(value).strip()
        }
        wanted_industries = {
            str(value).strip().lower() for value in (market.get("industries") or []) if str(value).strip()
        }
        if countries & wanted_countries or (industry and industry in wanted_industries):
            matched.append(str(market.get("id")))
    return sorted(matched)


def top_page_views(visits: Sequence[Mapping[str, Any]], limit: int = TOP_PAGE_LIMIT) -> list[dict[str, Any]]:
    """"the pages with the most visits from visitors from this company".

    Counted by *visits*, not by page views: the card says "the pages with the
    most visits", and one session reading four pages contributes one visit to
    each of the four. Ordered by that count, then by page views, then by how
    recently the path was seen, then by the path itself, so the column is stable
    across two reads of the same data.
    """
    by_path: dict[str, dict[str, Any]] = {}
    sessions: dict[str, set[str]] = defaultdict(set)
    visitors: dict[str, set[str]] = defaultdict(set)
    for visit in visits:
        path = str(visit.get("path") or "/")
        entry = by_path.setdefault(
            path,
            {
                "path": path,
                "host": visit.get("host"),
                "root_domain": visit.get("root_domain"),
                "views": 0,
                "visits": 0,
                "unique_visitors": 0,
                "last_viewed_at": None,
            },
        )
        entry["views"] = int(entry["views"]) + 1
        if visit.get("session_id"):
            sessions[path].add(str(visit["session_id"]))
        if visit.get("visitor_id"):
            visitors[path].add(str(visit["visitor_id"]))
        seen = str(visit.get("occurred_at") or "")
        if seen > str(entry.get("last_viewed_at") or ""):
            entry["last_viewed_at"] = seen
    for path, entry in by_path.items():
        entry["visits"] = len(sessions.get(path, ()))
        entry["unique_visitors"] = len(visitors.get(path, ()))
    ordered = sorted(
        by_path.values(),
        key=lambda entry: (
            -int(entry["visits"]),
            -int(entry["views"]),
            _negated(entry["last_viewed_at"]),
            entry["path"],
        ),
    )
    return ordered[:limit]


def _negated(value: Any) -> str:
    """Sort key that orders timestamps newest-first inside an ascending sort."""
    text = str(value or "")
    return "".join(chr(0x10FFFF - ord(char)) for char in text)


def _row_for(
    key: str,
    visits: list[Mapping[str, Any]],
    observations: list[Mapping[str, Any]],
    snapshot: Snapshot,
) -> dict[str, Any]:
    ordered_visits = sorted(visits, key=lambda row: _sort_key(row.get("occurred_at")))
    ordered_research = _by_newest(observations)

    page_views = len(ordered_visits)
    sessions = {str(row.get("session_id")) for row in ordered_visits if row.get("session_id")}
    visitors = {str(row.get("visitor_id")) for row in ordered_visits if row.get("visitor_id")}
    last_visit = _sort_key(ordered_visits[-1].get("occurred_at")) if ordered_visits else None
    first_visit = _sort_key(ordered_visits[0].get("occurred_at")) if ordered_visits else None

    # The Intent tag: which criterion, which filter, which path. Recomputed here
    # against the *current* criteria so a criterion added today tags yesterday's
    # visits and a withdrawn one stops tagging them.
    intent_evidence: list[dict[str, Any]] = []
    for visit in _by_newest(ordered_visits):
        matched = qualify_view(visit, snapshot.criteria)
        if matched is not None:
            matched = dict(matched)
            matched["tagged"] = "Intent"
            intent_evidence.append(matched)
        if len(intent_evidence) >= EVIDENCE_LIMIT:
            break

    research_evidence: list[dict[str, Any]] = []
    for observation in ordered_research:
        if observation.get("kind") == "topic":
            research_evidence.append(
                {
                    "kind": "topic",
                    "topic": observation.get("topic"),
                    "topic_id": observation.get("topic_id"),
                    "topic_matched": observation.get("topic_matched"),
                    "headline": observation.get("headline"),
                    "at": observation.get("occurred_at"),
                }
            )
        else:
            research_evidence.append(
                {
                    "kind": "news",
                    "signal_type": observation.get("signal_type"),
                    "headline": observation.get("headline"),
                    "source_url": observation.get("source_url"),
                    "at": observation.get("occurred_at"),
                }
            )
        if len(research_evidence) >= EVIDENCE_LIMIT:
            break

    countries = {str(row.get("country")) for row in ordered_visits if row.get("country")}
    countries |= {
        str(row.get("country")) for row in ordered_research if row.get("country")
    }
    industries = {str(row.get("industry")) for row in ordered_research if row.get("industry")}

    company_record = snapshot.companies.get(key)
    company_data: dict[str, Any] = (company_record or {}).get("data") or {}
    if company_data.get("country"):
        countries.add(str(company_data["country"]))
    if company_data.get("industries"):
        industries |= {str(value) for value in company_data["industries"]}
    industry = str(company_data.get("industry") or "") or (
        sorted(industries)[0] if industries else ""
    )

    markets = market_for(countries, industry, snapshot.markets)
    market_names = sorted(
        str(market.get("name") or "")
        for market in snapshot.markets
        if str(market.get("id")) in set(markets)
    )

    last_seen_candidates = [
        value
        for value in [last_visit, _sort_key(ordered_research[0].get("occurred_at")) if ordered_research else None]
        if value
    ]

    exclusion = snapshot.exclusions.get(key)
    tracking = snapshot.trackings.get(key)
    enrolments = [row for row in snapshot.enrolments if row.get("company_key") == key]
    contacts = [row for row in snapshot.contacts if row.get("company_key") == key]

    return {
        "company_key": key,
        "company_id": (company_record or {}).get("id"),
        "name": company_data.get("name") or domains.display_name(key),
        "root_domain": domains.resolve(key).root,
        "hosts": sorted({str(row.get("host")) for row in ordered_visits if row.get("host")}),
        "page_views": page_views,
        "website_visits": len(sessions),
        "unique_visitors": len(visitors),
        "first_visit_at": first_visit,
        "last_visit_at": last_visit or None,
        "last_seen_at": max(last_seen_candidates) if last_seen_candidates else None,
        "top_page_views": top_page_views(ordered_visits),
        "countries": sorted(countries),
        "industry": industry or None,
        "traffic_sources": sorted(
            {str(row.get("traffic_source")) for row in ordered_visits if row.get("traffic_source")}
        ),
        "visitor_intent": bool(intent_evidence),
        "visitor_intent_evidence": intent_evidence,
        "research_intent": bool(research_evidence),
        "research_evidence": research_evidence,
        "news_signals": sum(1 for row in ordered_research if row.get("kind") == "news"),
        "topic_matches": sum(1 for row in ordered_research if row.get("kind") == "topic"),
        "in_crm": company_record is not None,
        # "Companies currently in your account will appear with a HubSpot icon."
        # Served as data so the page can draw a labelled badge rather than the
        # row silently being present or absent.
        "crm_icon": "hubspot" if company_record is not None else None,
        "segment": company_data.get("segment"),
        "lifecycle_stage": company_data.get("lifecycle_stage"),
        "deal_stage": company_data.get("deal_stage"),
        "owner": company_data.get("owner"),
        "record_source": company_data.get("record_source"),
        "added_at": company_data.get("added_at"),
        "added_via": company_data.get("added_via"),
        "in_target_markets": markets,
        "in_target_market_names": market_names,
        "derived_properties": company_data.get("derived_properties") or {},
        "excluded": exclusion is not None,
        "excluded_at": (exclusion or {}).get("created_at"),
        "tracking": tracking,
        "tracked": bool(tracking),
        "enrolments": sorted(
            (
                {
                    "workflow": row.get("workflow"),
                    "at": row.get("created_at"),
                    "via": row.get("via"),
                }
                for row in enrolments
            ),
            key=lambda row: str(row.get("at") or ""),
        ),
        "contact_count": len(contacts),
    }


def build_rows(snapshot: Snapshot) -> list[dict[str, Any]]:
    """One row per company, from every source the research names.

    The key set is the union of the three ways a company comes into existence:
    a tracked visit, a research observation, and a CRM record added by an
    automation. A company only in the third set has no visits and no intent at
    all, and it is still a row - it is in the account, and the table is where a
    seller would look for it.
    """
    visits_by_key: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for visit in snapshot.visits:
        key = visit.get("company_key")
        if key:
            visits_by_key[str(key)].append(visit)

    research_by_key: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for observation in snapshot.research:
        key = observation.get("root_domain")
        if key:
            research_by_key[str(key)].append(observation)

    keys = set(visits_by_key) | set(research_by_key) | set(snapshot.companies)
    return [
        _row_for(key, visits_by_key.get(key, []), research_by_key.get(key, []), snapshot)
        for key in sorted(keys)
    ]


def row_for(snapshot: Snapshot, key: str) -> dict[str, Any] | None:
    """One company's row, or ``None`` when the table has never seen it."""
    resolved = domains.resolve(key).root
    for row in build_rows(snapshot):
        if row["company_key"] == resolved:
            return row
    return None


def unattributed(snapshot: Snapshot) -> int:
    """Page views whose visitor is still anonymous.

    "Buyer intent connects anonymous web visitors to known companies' IP
    addresses." A visit it cannot connect is real traffic and is stored, but it
    is not a company row, and the count is reported rather than dropped so a
    tracking-code problem is visible instead of looking like a quiet market.
    """
    return sum(1 for visit in snapshot.visits if not visit.get("company_key"))


# --------------------------------------------------------------------------- #
# Filtering and sorting
# --------------------------------------------------------------------------- #


def matches_filters(
    row: Mapping[str, Any],
    filters: Mapping[str, Any],
    *,
    window: Window | None,
) -> bool:
    """Whether a row's *attributes* satisfy a filter set.

    The time-frame half of the filter is applied here, to ``last_visit_at``,
    because the researched time frame is "last-visit based". A company with no
    website visit therefore cannot pass a view that has a time frame, which is
    the direct consequence of that word and is why the stock auto-add
    categories, which name no time frame, are evaluated without one.

    The path and traffic half of the filter is *not* applied here: those are
    properties of one observation rather than of a company, and
    :func:`dsr.market_intent.views.entered_at` is where the two halves meet.
    """
    if window is not None and not window.contains(row.get("last_visit_at")):
        return False

    if filters.get("visitor_intent") and not row.get("visitor_intent"):
        return False

    wanted_sources = set(filters.get("traffic_sources") or ())
    if wanted_sources and not (set(row.get("traffic_sources") or ()) & wanted_sources):
        return False

    wanted_countries = {str(value).upper() for value in (filters.get("countries") or ())}
    if wanted_countries and not (set(row.get("countries") or ()) & wanted_countries):
        return False

    if filters.get("in_target_markets") and not row.get("in_target_markets"):
        return False

    segment = filters.get("segment")
    if segment and str(row.get("segment") or "") != str(segment):
        return False

    for field_name in ("lifecycle_stages", "deal_stages", "owners"):
        wanted = {str(value) for value in (filters.get(field_name) or ())}
        if wanted and str(row.get(field_name[:-1]) or "") not in wanted:
            return False

    if filters.get("page_filters") and not _row_matches_paths(row, filters["page_filters"]):
        return False

    return True


def _row_matches_paths(row: Mapping[str, Any], path_filters: Sequence[Mapping[str, Any]]) -> bool:
    """Whether any of the row's own qualifying evidence matches a page filter.

    Matched against the stored evidence rather than by re-reading the visits,
    because the evidence already records the path and the host that were actually
    served - which is what the Intent tag claims.
    """
    wanted = [
        (str(entry.get("operator")), str(entry.get("path")), str(entry.get("domain") or ""))
        for entry in path_filters
    ]
    for evidence in row.get("visitor_intent_evidence") or ():
        visited = str(evidence.get("visited_path") or "")
        host = str(evidence.get("visited_host") or "")
        for operator, pattern, domain in wanted:
            if domain and domains.resolve(host).root != domain:
                continue
            if _path_matches(operator, pattern, visited):
                return True
    return False


def _path_matches(operator: str, pattern: str, path: str) -> bool:
    if operator == "eq":
        return path == pattern
    if operator == "neq":
        return path != pattern
    if operator == "contains":
        return pattern in path
    if operator == "not_contains":
        return pattern not in path
    if operator == "starts_with":
        return path.startswith(pattern)
    return False


def sort_rows(
    rows: list[dict[str, Any]],
    *,
    key: str = "page_views",
    direction: str = "desc",
) -> list[dict[str, Any]]:
    """Order the table by one of the three researched sort keys.

    "Sort by Page views, Unique visitors, or Last visit (asc/desc)."

    A row with no last visit sorts after every row that has one, in *both*
    directions. Its absence is not old, so putting it first in an ascending sort
    would claim that a company nobody has visited is the least recently seen one
    - which is why the ascending case reverses only the rows that have a value
    rather than the whole list.
    """
    if key == "page_views":
        ordered = sorted(
            rows,
            key=lambda row: (-int(row.get("page_views") or 0), str(row.get("company_key"))),
        )
    elif key == "unique_visitors":
        ordered = sorted(
            rows,
            key=lambda row: (-int(row.get("unique_visitors") or 0), str(row.get("company_key"))),
        )
    else:  # last_visit
        present = [row for row in rows if row.get("last_visit_at")]
        absent = [row for row in rows if not row.get("last_visit_at")]
        present.sort(
            key=lambda row: (_negated(row.get("last_visit_at")), str(row.get("company_key")))
        )
        ordered = present + sorted(absent, key=lambda row: str(row.get("company_key")))

    if direction != "asc":
        return ordered

    if key != "last_visit":
        return list(reversed(ordered))
    tail = len(ordered) - sum(1 for row in rows if not row.get("last_visit_at"))
    return list(reversed(ordered[:tail])) + ordered[tail:]


def describe_window(window: Window | None) -> dict[str, Any] | None:
    """The window a table read used, or ``None`` when it used no time frame."""
    return window.to_dict() if window is not None else None
