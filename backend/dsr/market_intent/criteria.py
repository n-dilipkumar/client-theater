"""Intent criteria, and the page-filter grammar they are written in.

Sourced
-------
* "intent criteria per page" - a criterion is about a page, so a criterion with
  no page filter is an unfinished one and is refused.
* The filter vocabulary, verbatim: "Path is equal to / Path is not equal to /
  Path contains / Path does not contain / Path starts with", plus Domain.
* "If you're reviewing companies that have shown intent, the specific domain and
  page path that qualified the company will be tagged with Intent." The
  matching filter is therefore not a detail - it is the row's evidence, and
  :func:`qualify_view` returns which one matched so it can be tagged.
* "Buyer intent uses a hierarchical root-domain model ... activity from
  subdomains is rolled up into the root domain", which is why the ``domain``
  field of a filter compares roots and not hosts.
* "if you have SMB Intent as a criterion and a custom property called Showing
  SMB Intent, that property will automatically update to reflect whether an
  existing company meets or no longer meets the SMB Intent criteria" - the
  criterion carries the ``derived_property`` name and
  :mod:`dsr.market_intent.automations` writes it back.

Design inference
----------------
* **Case sensitivity.** Every one of the five operators compares exactly as
  written, so ``/Pricing`` and ``/pricing`` are different pages. The research
  does not say, and it matters because the match is what gets tagged with
  Intent: a filter that matched a differently-cased path would tag a page the
  visitor never saw.
* **Clauses are OR'd within a criterion.** A criterion describes a set of pages
  that mean the same thing (``/pricing`` and ``/contact-sales`` both mean
  "ready to talk"), and refusing a criterion with more than one filter would
  make the feature useless. Across criteria the rule is also OR: any criterion
  met is intent, because each one is independently a statement of what counts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from dsr.market_intent import domains
from dsr.market_intent.errors import InvalidConfiguration, InvalidPathFilter

#: The researched grammar, in the order the UI lists it, with the vendor's own
#: labels. Served over HTTP so a client's operator picker is generated from the
#: server's list rather than from a copy of it.
PATH_OPERATORS: tuple[str, ...] = ("eq", "neq", "contains", "not_contains", "starts_with")

PATH_OPERATOR_LABELS: dict[str, str] = {
    "eq": "Path is equal to",
    "neq": "Path is not equal to",
    "contains": "Path contains",
    "not_contains": "Path does not contain",
    "starts_with": "Path starts with",
}


def _match_path(operator: str, pattern: str, path: str) -> bool:
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
    raise InvalidPathFilter(
        f"{operator!r} is not one of the path filters buyer intent offers: "
        f"{', '.join(PATH_OPERATORS)}"
    )


@dataclass(frozen=True)
class PageFilter:
    """One researched "Specific page views" clause.

    ``domain`` is optional and compares *roots*, so a filter written against
    ``northwind.com`` catches a visit to ``careers.northwind.com`` - which is the
    roll-up the research describes, and the reason a page filter cannot be
    expressed as a substring on the URL.
    """

    operator: str
    path: str
    domain: str = ""

    @classmethod
    def parse(cls, payload: Any, *, where: str) -> "PageFilter":
        if not isinstance(payload, Mapping):
            raise InvalidPathFilter(f"{where} must be an object with operator and path")
        operator = payload.get("operator")
        if not isinstance(operator, str) or operator.strip().lower() not in PATH_OPERATOR_LABELS:
            raise InvalidPathFilter(
                f"{where}.operator must be one of {', '.join(PATH_OPERATORS)}; "
                f"the research documents exactly these five"
            )
        path = payload.get("path")
        if not isinstance(path, str) or not path.startswith("/"):
            raise InvalidPathFilter(f"{where}.path must be a site path beginning with '/'")
        domain = payload.get("domain") or ""
        if not isinstance(domain, str):
            raise InvalidPathFilter(f"{where}.domain must be a string")
        if domain and not domains.is_valid_domain(domain):
            raise InvalidPathFilter(
                f"{where}.domain {domain!r} is not a registrable domain; an exclusion or a "
                "page filter keyed on something that cannot be reduced to a root domain "
                "would silently match nothing"
            )
        return cls(operator=operator.strip().lower(), path=path, domain=domain.strip().lower())

    def matches(self, page: Mapping[str, Any]) -> bool:
        """Whether one page view satisfies this clause."""
        if self.domain:
            host = page.get("host") or page.get("url") or ""
            if domains.resolve(host).root != self.domain:
                return False
        return _match_path(self.operator, self.path, str(page.get("path") or ""))

    def to_dict(self) -> dict[str, Any]:
        return {"operator": self.operator, "path": self.path, "domain": self.domain or None}


def parse_page_filters(payload: Any, *, where: str = "page_filters") -> tuple[PageFilter, ...]:
    """Parse a list of page filters, refusing anything outside the grammar."""
    if payload is None:
        return ()
    if isinstance(payload, Mapping):
        payload = [payload]
    if not isinstance(payload, Sequence) or isinstance(payload, (str, bytes)):
        raise InvalidPathFilter(f"{where} must be a list of page filters")
    return tuple(
        PageFilter.parse(entry, where=f"{where}[{index}]") for index, entry in enumerate(payload)
    )


@dataclass(frozen=True)
class Criterion:
    """An intent criterion: a named set of pages, and the property it derives.

    ``site`` is the root domain the criterion is scoped to, for the case where
    the tracking code fires on more than one site. Left empty, the criterion
    applies to any tracked page view, which is the common case for a
    single-domain seller.
    """

    id: str
    name: str
    page_filters: tuple[PageFilter, ...]
    derived_property: str = ""
    site: str = ""
    active: bool = True

    @classmethod
    def parse(cls, payload: Any, *, record_id: str) -> "Criterion":
        if not isinstance(payload, Mapping):
            raise InvalidConfiguration("an intent criterion must be an object")
        name = payload.get("name")
        if not isinstance(name, str) or not name.strip():
            raise InvalidConfiguration("an intent criterion needs a name")
        filters = parse_page_filters(payload.get("page_filters"))
        if not filters:
            raise InvalidConfiguration(
                f"intent criterion {name!r} declares no page filter. The research describes "
                "intent criteria as per page, so a criterion with no page is an unfinished one "
                "rather than a broad one"
            )
        site = payload.get("site") or ""
        if not isinstance(site, str):
            raise InvalidConfiguration("criterion.site must be a string")
        site = site.strip().lower()
        if site and not domains.is_valid_domain(site):
            raise InvalidConfiguration(
                f"criterion.site {site!r} is not a registrable domain; buyer intent keys a "
                "company on its root domain and a criterion has to name one"
            )
        derived = payload.get("derived_property") or ""
        if not isinstance(derived, str):
            raise InvalidConfiguration("criterion.derived_property must be a string")
        return cls(
            id=record_id,
            name=name.strip(),
            page_filters=filters,
            derived_property=derived.strip(),
            site=site,
            active=True,
        )

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "Criterion":
        data = record.get("data") or {}
        return cls(
            id=str(record.get("id")),
            name=str(data.get("name") or "criterion"),
            page_filters=tuple(
                PageFilter(
                    operator=str(entry.get("operator")),
                    path=str(entry.get("path")),
                    domain=str(entry.get("domain") or ""),
                )
                for entry in data.get("page_filters") or []
            ),
            derived_property=str(data.get("derived_property") or ""),
            site=str(data.get("site") or ""),
            active=bool(data.get("active", True)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "page_filters": [entry.to_dict() for entry in self.page_filters],
            "derived_property": self.derived_property or None,
            "site": self.site or None,
            "active": self.active,
        }

    def matches(self, page: Mapping[str, Any]) -> bool:
        """Whether a page view satisfies this criterion.

        Reports nothing about *which* filter matched: that is
        :func:`qualify_view`'s job, because the matched filter is what gets
        tagged with Intent and a criterion with four equivalent pages should
        name the one that actually fired.
        """
        if not self.active:
            return False
        if self.site and domains.resolve(page.get("host") or page.get("url") or "").root != self.site:
            return False
        return any(entry.matches(page) for entry in self.page_filters)

    def qualifying_filter(self, page: Mapping[str, Any]) -> dict[str, Any] | None:
        """The filter that matched, which is the row's Intent tag.

        First match in declaration order, so the tag is stable for a given
        criterion and page rather than depending on set iteration.
        """
        if not self.active:
            return None
        if self.site and domains.resolve(page.get("host") or page.get("url") or "").root != self.site:
            return None
        for entry in self.page_filters:
            if entry.matches(page):
                return {
                    "operator": entry.operator,
                    "operator_label": PATH_OPERATOR_LABELS[entry.operator],
                    "path": entry.path,
                    "domain": entry.domain or None,
                    "visited_path": page.get("path"),
                    "visited_host": page.get("host"),
                    "at": page.get("occurred_at"),
                    "criterion_id": self.id,
                    "criterion": self.name,
                    "derived_property": self.derived_property or None,
                }
        return None


def qualify_view(
    page: Mapping[str, Any],
    criteria: Iterable[Criterion],
) -> dict[str, Any] | None:
    """The first criterion this page view satisfies, with its evidence.

    ``None`` when nothing matches, which is the ordinary case for most traffic:
    a page view that matches nothing is still stored, it simply does not make
    the company an intent company.
    """
    for criterion in criteria:
        matched = criterion.qualifying_filter(page)
        if matched is not None:
            return matched
    return None


def derived_properties(
    page_views: Sequence[Mapping[str, Any]],
    criteria: Iterable[Criterion],
) -> dict[str, bool]:
    """Every derived intent property, for one company, right now.

    "that property will automatically update to reflect whether an existing
    company meets or no longer meets the SMB Intent criteria" - so this returns a
    value for *every* criterion that names a property, including ``False``, and
    a criterion that has stopped matching has to be able to write that back. A
    property that only appeared while qualifying could never be unset, and a CRM
    record holding ``showing_smb_intent: true`` for a company that stopped
    qualifying is a sentence a seller would read and believe.

    "No longer meets" is what fixes the rule, and the rule is the *trailing run*
    :func:`dsr.market_intent.views.entered_at` uses: the property is true while
    the company's most recent activity qualifies, and goes false the moment its
    newest page view does not. "Ever matched" would be the obvious alternative
    and it cannot express the second half of the sentence at all.

    This is deliberately *not* the same question as the row's ``visitor_intent``,
    which is "has this company shown intent" and stays true once it has. The two
    are two sentences in the research: "Showing visitor intent" against the
    table, and "meets or no longer meets" against a property on a CRM record.
    """
    ordered = sorted(page_views, key=lambda row: str(row.get("occurred_at") or ""))
    values: dict[str, bool] = {}
    for criterion in criteria:
        if not criterion.derived_property or not criterion.active:
            continue
        run = 0
        for page in reversed(ordered):
            if criterion.matches(page):
                run += 1
            else:
                break
        values[criterion.derived_property] = run > 0
    return values
