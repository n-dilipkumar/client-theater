"""Tracked page views and research observations, and how each becomes a company.

Sourced
-------
* "the HubSpot tracking code installed on your website collects visitor data,
  such as website activity data, IP addresses, and other online identifiers.
  This data is used to monitor your website traffic and for website visits to be
  matched to companies."
* "Buyer intent connects anonymous web visitors to known companies' IP
  addresses. Companies currently in your account will appear with a HubSpot
  icon."
* "Buyer intent stores company-level website activity in a table, including
  details such as website visits, unique visitors, last visit, and top page
  views."
* "it can also surface broader intent signals beyond your website, such as
  companies researching topics across the web or company news like funding,
  executive hires, layoffs, product launches, and mergers."
* "To review the most recent page views from a company, including IP-derived
  country and date and time of the website visit" - so the country on a page view
  is the IP-derived one and it is stored with the visit.

What is *not* stored
--------------------
The Intent tag is not stored on a page view. The research says the qualifying
domain and path "will be tagged with Intent", and the extensibility rule says a
custom intent property "will automatically update to reflect whether an existing
company meets or no longer meets" the criteria. Both are only true if the tag is
recomputed against the *current* criteria on every read: a tag written at ingest
would leave yesterday's visits untagged by a criterion added today, and would
never be removed when a criterion is withdrawn. So this module writes facts and
:mod:`dsr.market_intent.table` decides what they mean.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from dsr.market_intent import domains
from dsr.market_intent.errors import InvalidObservation, UnknownVocabularyValue
from dsr.market_intent.timeframe import require_utc
from dsr.market_intent.vocabulary import (
    NEWS_SIGNAL_TYPES,
    RESEARCH_KINDS,
    TRAFFIC_SOURCES,
    lifecycle_rank,
)

#: How a page view was matched to a company. ``none`` means the visitor is
#: still anonymous: a page view with no resolvable company is stored, because it
#: is real traffic, but it is not a row in the buyer-intent table.
#:
#: ``ip_match`` is the upstream company IP-to-company matching the research names
#: in ``data_sources``: the tracking pipeline resolved the address and the
#: resolution arrives with the page view. It is reported separately from ``ip``
#: so a reader can tell "we knew this address" from "somebody told us".
ATTRIBUTIONS: tuple[str, ...] = ("ip_match", "ip", "contact", "email_domain", "none")

_EMAIL = re.compile(r"^[^@\s]+@([^@\s]+)$")
_COUNTRY = re.compile(r"^[A-Za-z]{2}$")


def _clean(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def parse_country(value: Any, *, where: str) -> str:
    """An IP-derived country as a two-letter code, or empty."""
    text = _clean(value)
    if not text:
        return ""
    if not _COUNTRY.match(text):
        raise InvalidObservation(
            f"{where} must be a two-letter ISO country code derived from the visitor's IP "
            f"address, got {text!r}"
        )
    return text.upper()


def normalise_visit(payload: Any, *, resolver: Any = None) -> dict[str, Any]:
    """Validate a tracked page view and reduce it to what the table needs.

    ``resolver`` is called as ``resolver(ip, contact)`` and returns
    ``(company_key, attribution)`` or ``(None, "none")``. It is a parameter
    rather than a store call so that attribution - the step the research
    describes as "anonymous -> known company" - can be tested without a
    database, and so the same normaliser works from a seed and from a route.
    """
    if not isinstance(payload, Mapping):
        raise InvalidObservation("a page view must be an object")

    url = _clean(payload.get("url"))
    host = _clean(payload.get("host")) or domains.parse_host(url)
    path = payload.get("path")
    if not host:
        raise InvalidObservation(
            "a page view needs a url or a host; the tracking code fires on a page, and a "
            "visit with no host cannot be matched to a company"
        )
    if path is None and url:
        split_path = url.split("?", 1)[0]
        marker = f"{host}"
        index = split_path.lower().find(marker.lower())
        path = split_path[index + len(marker) :] if index >= 0 else "/"
    if not isinstance(path, str) or not path.startswith("/"):
        raise InvalidObservation(
            f"a page view needs a path beginning with '/', got {path!r}; the researched "
            "page filters are all stated in terms of Path"
        )

    info = domains.resolve(host)
    if not info.root:
        raise InvalidObservation(f"host {host!r} has no registrable domain to key a company on")

    occurred_at = require_utc(payload.get("occurred_at"), what="occurred_at").isoformat()
    session_id = _clean(payload.get("session_id"))
    if not session_id:
        raise InvalidObservation(
            "a page view needs a session id; the Buyer Intent card counts 'the count of "
            "sessions of website visits from this company', so a visit with no session is a "
            "page view that will read as zero visits"
        )
    visitor_id = _clean(payload.get("visitor_id"))
    if not visitor_id:
        raise InvalidObservation(
            "a page view needs a visitor id; 'Unique visitors' is a count of distinct "
            "visitors and an anonymous visitor still has to be distinguishable from the "
            "next one"
        )

    traffic_source = _clean(payload.get("traffic_source")).lower() or "other"
    if traffic_source not in TRAFFIC_SOURCES:
        raise UnknownVocabularyValue(
            f"traffic_source {traffic_source!r} is not one of {', '.join(TRAFFIC_SOURCES)}"
        )

    ip = _clean(payload.get("ip"))
    contact = _clean(payload.get("known_contact"))
    if contact and not _EMAIL.match(contact) and "@" in contact:
        raise InvalidObservation(
            "known_contact must be an email address or a contact id, not a partial identity"
        )

    company_key, attribution = (None, "none")
    if resolver is not None:
        company_key, attribution = resolver(ip, contact, _clean(payload.get("company_domain")))
    if company_key and attribution == "none":
        attribution = "contact" if contact else "ip"

    return {
        "kind": "visit",
        "url": url or f"https://{info.host}{path}",
        "host": info.host,
        "path": path,
        "root_domain": info.root,
        "subdomains": list(info.subdomains),
        "is_ip": info.is_ip,
        "occurred_at": occurred_at,
        "session_id": session_id,
        "visitor_id": visitor_id,
        "ip": ip or None,
        "known_contact": contact or None,
        "traffic_source": traffic_source,
        "country": parse_country(payload.get("country"), where="country"),
        "company_key": company_key,
        "attribution": attribution,
        "known": bool(company_key),
    }

def normalise_research(payload: Any) -> dict[str, Any]:
    """Validate a research observation: a topic match, or a company news signal.

    Both are the "broader intent signals beyond your website" the research
    names, and both are attributed to a company by root domain, because a topic
    match names a company and a news item does too. A company discovered only
    this way has no website visits at all, which is why the table and the stock
    auto-add categories are built from a union rather than from visits alone.
    """
    if not isinstance(payload, Mapping):
        raise InvalidObservation("a research observation must be an object")

    kind = _clean(payload.get("kind")).lower()
    if kind not in RESEARCH_KINDS:
        raise UnknownVocabularyValue(
            f"kind must be one of {', '.join(RESEARCH_KINDS)}, got {kind!r}"
        )

    domain = payload.get("company_domain") or payload.get("root_domain") or payload.get("url")
    info = domains.resolve(domain)
    if not info.root:
        raise InvalidObservation(
            "a research observation names the company it is about: give company_domain as a "
            "domain or a URL"
        )

    occurred_at = require_utc(payload.get("occurred_at"), what="occurred_at").isoformat()

    if kind == "topic":
        topic = _clean(payload.get("topic"))
        if not topic:
            raise InvalidObservation(
                "a topic observation needs the topic that was researched; the Research tab is "
                "opened for topic research, so a match with no topic names nothing"
            )
        return {
            "kind": "topic",
            "root_domain": info.root,
            "company_domain": info.root,
            "topic": topic,
            "headline": _clean(payload.get("headline")) or None,
            "source_url": _clean(payload.get("source_url")) or None,
            "occurred_at": occurred_at,
            "country": parse_country(payload.get("country"), where="country"),
            "industry": _clean(payload.get("industry")) or None,
            "topic_id": None,
            "topic_matched": None,
        }

    signal_type = _clean(payload.get("signal_type")).lower()
    if signal_type not in NEWS_SIGNAL_TYPES:
        raise UnknownVocabularyValue(
            f"signal_type must be one of {', '.join(NEWS_SIGNAL_TYPES)}, got {signal_type!r}; "
            "the research enumerates them as funding, executive hires, layoffs, product "
            "launches, and mergers"
        )
    headline = _clean(payload.get("headline"))
    if not headline:
        raise InvalidObservation(
            "a news signal needs a headline; the Research tab shows what happened, and a "
            "signal type with no headline is a label with no news attached"
        )
    return {
        "kind": "news",
        "root_domain": info.root,
        "company_domain": info.root,
        "signal_type": signal_type,
        "headline": headline,
        "source_url": _clean(payload.get("source_url")) or None,
        "occurred_at": occurred_at,
        "country": parse_country(payload.get("country"), where="country"),
        "industry": _clean(payload.get("industry")) or None,
    }


def mark_topics(observation: dict[str, Any], topics: Sequence[Mapping[str, Any]]) -> None:
    """Record which configured research topic a topic observation matched.

    Mutates ``observation`` in place because the caller is walking a list it is
    about to aggregate, and returning a copy per observation would obscure that
    the match is part of the observation rather than a later verdict.

    Matching is case-insensitive substring over a topic's ``terms``; a topic
    declared with no terms matches its own name. This is an inference - the
    research says "set up research topics" and never says how one is matched -
    and it is named in :mod:`dsr.market_intent.inferences`.
    """
    if observation.get("kind") != "topic":
        return
    text = str(observation.get("topic") or "").lower()
    for topic in topics:
        terms = [str(term).lower() for term in (topic.get("terms") or []) if str(term).strip()]
        if not terms:
            terms = [str(topic.get("name") or "").lower()]
        for term in terms:
            if term and term in text:
                observation["topic_id"] = topic.get("id")
                observation["topic_matched"] = term
                return


def contact_domain(contact: Any) -> str:
    """The root domain an email or contact id implies, or empty."""
    text = _clean(contact)
    if not text:
        return ""
    match = _EMAIL.match(text)
    if match:
        return domains.resolve(match.group(1)).root
    if text.startswith("www.") or "." in text:
        return domains.resolve(text).root
    return ""


def is_forward_only(previous: Any, proposed: Any) -> bool:
    """Whether ``proposed`` moves a lifecycle stage backwards.

    ``lifecyclestage is forward-only``, quoted from the CRM API the research
    cites. A stage the product does not know the position of is *not* treated as
    a regression: the research names no vocabulary, a team adding a stage must
    not need coordination, and refusing a change the product cannot order would
    make an unfamiliar stage unwriteable. Such a move is reported as unchecked
    rather than as allowed, by the caller.
    """
    before = lifecycle_rank(previous)
    after = lifecycle_rank(proposed)
    if before is None or after is None:
        return False
    return after < before


def describe_attribution(attribution: Any) -> str:
    """A sentence for how a visit was matched to a company."""
    return {
        "ip_match": "Matched to a company by the tracking pipeline's IP-to-company lookup.",
        "ip": "Matched to a company by the visitor's IP address.",
        "contact": "Matched to a company through a known contact.",
        "email_domain": "Matched to a company by the contact's email domain.",
        "none": "Still anonymous: no company was matched to this visit.",
    }.get(str(attribution), "Matched by an unnamed method.")
