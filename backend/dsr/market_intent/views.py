"""Saved views: the left panel's filter set, and the moment a company entered it.

Sourced
-------
"Click **Save view** to persist the filter set as a named view", and the filter
set is the left panel's own list, in the research's order: "**Time frame** (last-
visit based, up to 90 days, midnight-UTC based), **Showing visitor intent**,
**Traffic source**, **Visitor country**, **Specific page views** (Path equal /
not equal / contains / does not contain / starts with, plus Domain), **In my
target markets**, **Filter by segment**, and **HubSpot CRM** (Lifecycle stage,
Deal Stage, Owner)".

And, the sentence the whole automation rests on:

    "Please note: auto-add will only add companies that enter your saved views
    after enabling the auto-add. It will not add all existing companies in your
    saved views."

Entering
--------
A company enters a view when it starts matching and leaves when it stops. The
moment it entered is therefore not a stored flag and not the time a run noticed
- it is the timestamp of the *oldest observation in the trailing run of
qualifying observations*, walked from the company's newest activity backwards
until the first one that does not qualify.

That derivation is what makes the researched note true rather than nearly true.
Stamping the entry at run time would break it in the case that matters most: a
company that entered the view last month, before the automation was switched on,
would be stamped today, be *after* the switch-on time, and be auto-added -
which is precisely what the research says will not happen.

The three asymmetries below are inferences and are named in
:mod:`dsr.market_intent.inferences`:

* **A view with page filters is website-only.** Every researched path filter is
  a site path, and a research observation has none, so a company known only
  from a topic match cannot satisfy one.
* **A view with a traffic-source filter is website-only.** A research
  observation has no traffic source; there is nothing to compare.
* **"Showing visitor intent" off is unconstrained, not its negation.** It is a
  switch on a filter panel, so off means "do not narrow by intent", not "show
  only companies with no intent". Reading it as the negation would make a view
  the user turned the filter *off* on return the exact companies they were
  filtering out.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from dsr.market_intent.criteria import Criterion, PageFilter, parse_page_filters, qualify_view
from dsr.market_intent.errors import (
    InvalidConfiguration,
    InvalidSort,
    UnknownVocabularyValue,
)
from dsr.market_intent.timeframe import DEFAULT_DAYS
from dsr.market_intent.vocabulary import DIRECTIONS, SORT_KEYS, TRAFFIC_SOURCES

#: The left panel's fields, in the order the research lists them. Served so a
#: client can render the panel in the documented order without hard-coding it.
FILTER_FIELDS: tuple[str, ...] = (
    "days",
    "visitor_intent",
    "traffic_sources",
    "countries",
    "page_filters",
    "in_target_markets",
    "segment",
    "lifecycle_stages",
    "deal_stages",
    "owners",
)


def _strings(value: Any, *, where: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, Sequence):
        raise InvalidConfiguration(f"{where} must be a list of strings")
    return tuple(str(entry).strip() for entry in value if str(entry).strip())


def _flag(value: Any) -> bool:
    return bool(value)


@dataclass(frozen=True)
class FilterSet:
    """One view's filter set, and the sort the table is shown in."""

    #: ``None`` means "no time frame at all", which is how a view asks for
    #: everything rather than for the thirty-day default. The research names a
    #: 90-day ceiling but no default, so the default is an inference.
    timeframe: bool = True
    days: int = DEFAULT_DAYS
    visitor_intent: bool = False
    traffic_sources: tuple[str, ...] = ()
    countries: tuple[str, ...] = ()
    page_filters: tuple[PageFilter, ...] = ()
    in_target_markets: bool = False
    segment: str = ""
    lifecycle_stages: tuple[str, ...] = ()
    deal_stages: tuple[str, ...] = ()
    owners: tuple[str, ...] = ()
    sort: str = "page_views"
    direction: str = "desc"

    @classmethod
    def parse(cls, payload: Any) -> "FilterSet":
        if payload is None:
            payload = {}
        if not isinstance(payload, Mapping):
            raise InvalidConfiguration("a view's filters must be an object")

        timeframe = True
        days: Any = DEFAULT_DAYS
        if "days" in payload:
            days = payload.get("days")
            if days is None:
                timeframe = False
                days = DEFAULT_DAYS

        sources = tuple(
            value.lower()
            for value in _strings(payload.get("traffic_sources"), where="traffic_sources")
        )
        for source in sources:
            if source not in TRAFFIC_SOURCES:
                raise UnknownVocabularyValue(
                    f"traffic_sources contains {source!r}, which is not one of "
                    f"{', '.join(TRAFFIC_SOURCES)}"
                )

        countries = tuple(
            value.upper() for value in _strings(payload.get("countries"), where="countries")
        )

        sort = str(payload.get("sort") or "page_views").strip().lower()
        if sort not in SORT_KEYS:
            raise InvalidSort(
                f"sort must be one of {', '.join(SORT_KEYS)}; the table offers Page views, "
                "Unique visitors, and Last visit, and nothing else"
            )
        direction = str(payload.get("direction") or "desc").strip().lower()
        if direction not in DIRECTIONS:
            raise InvalidSort(f"direction must be one of {', '.join(DIRECTIONS)}")

        return cls(
            timeframe=timeframe,
            days=days,
            visitor_intent=_flag(payload.get("visitor_intent")),
            traffic_sources=sources,
            countries=countries,
            page_filters=parse_page_filters(payload.get("page_filters")),
            in_target_markets=_flag(payload.get("in_target_markets")),
            segment=str(payload.get("segment") or "").strip(),
            lifecycle_stages=_strings(payload.get("lifecycle_stages"), where="lifecycle_stages"),
            deal_stages=_strings(payload.get("deal_stages"), where="deal_stages"),
            owners=_strings(payload.get("owners"), where="owners"),
            sort=sort,
            direction=direction,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "days": self.days if self.timeframe else None,
            "timeframe": self.timeframe,
            "visitor_intent": self.visitor_intent,
            "traffic_sources": list(self.traffic_sources),
            "countries": list(self.countries),
            "page_filters": [entry.to_dict() for entry in self.page_filters],
            "in_target_markets": self.in_target_markets,
            "segment": self.segment or None,
            "lifecycle_stages": list(self.lifecycle_stages),
            "deal_stages": list(self.deal_stages),
            "owners": list(self.owners),
            "sort": self.sort,
            "direction": self.direction,
        }

    def as_match_dict(self) -> dict[str, Any]:
        """The shape :func:`dsr.market_intent.table.matches_filters` reads."""
        return {
            "visitor_intent": self.visitor_intent,
            "traffic_sources": tuple(self.traffic_sources),
            "countries": tuple(self.countries),
            "in_target_markets": self.in_target_markets,
            "segment": self.segment,
            "lifecycle_stages": tuple(self.lifecycle_stages),
            "deal_stages": tuple(self.deal_stages),
            "owners": tuple(self.owners),
            "page_filters": tuple(
                {"operator": entry.operator, "path": entry.path, "domain": entry.domain}
                for entry in self.page_filters
            ),
        }

    def is_website_only(self) -> bool:
        """Whether this view can only ever contain companies that visited."""
        return bool(self.page_filters) or bool(self.traffic_sources)


@dataclass(frozen=True)
class Membership:
    """One company's membership of a view, and when it entered."""

    company_key: str
    entered_at: str | None
    row: dict[str, Any]


def observations_for(row: Mapping[str, Any], snapshot: Any) -> list[dict[str, Any]]:
    """A company's whole activity stream, newest first.

    Visits and research observations, merged. A company only ever has one of
    them, and a company with both has them interleaved in time - which matters,
    because the trailing run below is cut on the first observation that does not
    qualify regardless of which kind it was.
    """
    key = str(row.get("company_key") or "")
    merged: list[dict[str, Any]] = []
    for visit in snapshot.visits:
        if str(visit.get("company_key") or "") == key:
            merged.append(visit)
    for observation in snapshot.research:
        if str(observation.get("root_domain") or "") == key:
            merged.append(observation)
    merged.sort(key=lambda entry: str(entry.get("occurred_at") or ""), reverse=True)
    return merged


def observation_matches(
    observation: Mapping[str, Any],
    filters: FilterSet,
    criteria: Sequence[Criterion],
) -> bool:
    """Whether one observation satisfies a view's stream-level filters."""
    if observation.get("kind") == "news":
        # A news item is not a visit and has no page, no session, and no traffic
        # source. It can still satisfy a view that filters on nothing but the
        # company, which is the "Research" tab's own use.
        if filters.is_website_only():
            return False
        if filters.countries:
            return str(observation.get("country") or "").upper() in filters.countries
        return True

    if filters.is_website_only() and observation.get("kind") != "visit":
        return False

    if observation.get("kind") == "visit":
        if filters.page_filters:
            if not any(entry.matches(observation) for entry in filters.page_filters):
                return False
        elif filters.visitor_intent and not qualify_view(observation, criteria):
            return False
        if filters.traffic_sources:
            if str(observation.get("traffic_source") or "") not in filters.traffic_sources:
                return False
    elif filters.countries:
        if str(observation.get("country") or "").upper() not in filters.countries:
            return False

    if filters.countries and observation.get("kind") == "visit":
        if str(observation.get("country") or "").upper() not in filters.countries:
            return False

    return True


def entered_at(
    row: Mapping[str, Any],
    filters: FilterSet,
    snapshot: Any,
) -> str | None:
    """When this company entered the view, or ``None`` if it is not in it.

    Walks the company's observations from newest to oldest while they qualify,
    and the answer is the oldest one in that run. ``None`` when the newest
    observation does not qualify - the company is not in the view, and no
    earlier entry survives.
    """
    stream = observations_for(row, snapshot)
    if not stream:
        return None
    if not observation_matches(stream[0], filters, snapshot.criteria):
        return None
    entered = str(stream[0].get("occurred_at") or "")
    for observation in stream[1:]:
        if not observation_matches(observation, filters, snapshot.criteria):
            break
        entered = str(observation.get("occurred_at") or "")
    return entered or None


def memberships(
    rows: Sequence[Mapping[str, Any]],
    filters: FilterSet,
    snapshot: Any,
) -> list[Membership]:
    """Every row's membership, in the same order the rows arrived."""
    return [
        Membership(
            company_key=str(row.get("company_key") or ""),
            entered_at=entered_at(row, filters, snapshot),
            row=dict(row),
        )
        for row in rows
    ]


def in_view(
    membership: Membership,
    filters: FilterSet,
    *,
    window: Any = None,
) -> bool:
    """Whether a membership survives the view's attribute filters too.

    The two halves are separate on purpose. ``entered_at`` answers "is this
    company's activity stream in the view", which is a question about
    observations; this answers "does the company itself still qualify", which is
    a question about its CRM attributes and its last visit. A company can be in
    the view's stream and still fall out of the view on a time frame.
    """
    from dsr.market_intent.table import matches_filters

    if membership.entered_at is None:
        return False
    return matches_filters(membership.row, filters.as_match_dict(), window=window)
