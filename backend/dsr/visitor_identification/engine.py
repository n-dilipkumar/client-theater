"""The façade the HTTP layer calls, and the only place in the package that writes.

:class:`VisitorEngine` holds the store handle and a clock. Every write it makes
takes the ``source`` its caller supplies, because the audit row must name the
route that served the write: a hardcoded string inside a domain method is a
defect, and the same class of bug has shipped in this codebase before, as a
feature whose audit log kept naming a path the app had stopped serving.

The order of the capture pipeline is the order of the data flow the research
gives, and each step is a decision a test can name:

1. Read the payload into a :class:`~dsr.visitor_identification.capture.Capture`.
   A person-level key is refused here, before anything is written.
2. Check the client id against the installed snippets.
3. Match the request against the company database, or create the company record.
4. Record the page-visit event, keyed to the path.
5. Count the visit, and note the address, network and country on the company.
6. Evaluate every intent page of that client against the path, at read time.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from dsr.store import RecordStore
from dsr.visitor_identification import leads, pages as page_rules
from dsr.visitor_identification.capture import parse_capture
from dsr.visitor_identification.company import (
    blank_company,
    counters_of,
    detail_of,
    keyed,
    parse_detail,
    require_company,
    resolve,
    with_capture,
)
from dsr.visitor_identification.errors import (
    CompanyAlreadyIdentified,
    CompanyKeyRequired,
    InvalidInstallation,
    UnknownIcp,
    UnknownInstallation,
    UnknownPage,
)
from dsr.visitor_identification.inferences import INFERENCES
from dsr.visitor_identification.paths import matches
from dsr.visitor_identification.vocabulary import (
    COMPANIES,
    DOWNSTREAM_SURFACES,
    ICP_PROFILES,
    INSTALLATIONS,
    PAGES,
    VISITS,
    describe as describe_vocabulary,
)

#: How many visits the company drill-down returns by default, and the ceiling.
DEFAULT_VISIT_LIMIT = 50
MAX_VISIT_LIMIT = 200

#: Every collection this feature reads whole. The lead table and the Pages list are
#: small by construction - one row per company, one per defined page - and the
#: store clamps a page at a thousand, so a read of everything is a bounded read.
PAGE_SIZE = 1000


class VisitorEngine:
    """Identify anonymous requests as companies, and filter the result by page."""

    def __init__(self, store: RecordStore, now: Any = None) -> None:
        self.store = store
        self._now = now

    # -- clock --------------------------------------------------------------- #

    def now(self) -> datetime:
        """The current moment, or the one a caller pinned.

        A callable or a value rather than a fixed stamp, so a test can advance
        time between two calls and a seed can hold one moment for every row.
        """
        if callable(self._now):
            return self._now()
        if isinstance(self._now, datetime):
            return self._now
        return datetime.now(timezone.utc)

    def stamp(self) -> str:
        return self.now().isoformat()

    # -- published vocabulary and inferences --------------------------------- #

    def vocabulary(self) -> dict[str, Any]:
        return describe_vocabulary()

    def inferences(self) -> dict[str, Any]:
        return {"count": len(INFERENCES), "inferences": [dict(e) for e in INFERENCES]}

    # -- installations -------------------------------------------------------- #

    def installations(self) -> dict[str, Any]:
        """The tracking snippets this portal has installed."""
        listed = [
            {
                "id": record.get("id"),
                "client_id": (record.get("data") or {}).get("client_id"),
                "site": (record.get("data") or {}).get("site"),
                "installed_at": (record.get("data") or {}).get("installed_at"),
            }
            for record in self.store.list(INSTALLATIONS, limit=PAGE_SIZE, order_by="created_at")
        ]
        return {"count": len(listed), "installations": listed}

    def install(self, payload: dict[str, Any], *, actor: str, source: str) -> dict[str, Any]:
        """Step 1: record the snippet, its site, and the Client ID it issued.

        The flow says to note the Client ID, and a capture is refused without one,
        so the install is what makes a capture addressable. Installing the same
        client id twice is not an error: it answers 200 with ``created: False`` and
        re-points the site, because re-running the install guide is how a seller
        arrives here twice.
        """
        body = dict(payload or {})
        client_id = str(body.get("client_id") or "").strip()
        if not client_id:
            raise InvalidInstallation(
                "an installation needs the Client ID the tracking snippet issued."
            )
        site = str(body.get("site") or "").strip()
        existing = self._installation(client_id)
        record = {
            "client_id": client_id,
            "site": site or ((existing or {}).get("data") or {}).get("site", ""),
            "installed_at": ((existing or {}).get("data") or {}).get("installed_at")
            or self.stamp(),
            "updated_at": self.stamp(),
        }
        if existing:
            self.store.update(existing["id"], record, actor=actor, source=source)
            return {
                "installed": True,
                "created": False,
                "client_id": client_id,
                "site": record["site"],
                "id": existing["id"],
            }
        created = self.store.create(INSTALLATIONS, record, actor=actor, source=source)
        return {
            "installed": True,
            "created": True,
            "client_id": client_id,
            "site": record["site"],
            "id": created.get("id"),
        }

    def _installation(self, client_id: str) -> dict[str, Any] | None:
        rows = self.store.find(INSTALLATIONS, {"client_id": client_id}, limit=1)
        return rows[0] if rows else None

    def _require_installation(self, client_id: str) -> dict[str, Any]:
        found = self._installation(client_id)
        if found is None:
            raise UnknownInstallation(
                f"no tracking snippet is installed under client id {client_id!r}. Install the "
                "snippet, then send the Client ID it issued."
            )
        return found

    # -- the capture ---------------------------------------------------------- #

    def capture(self, payload: dict[str, Any], *, actor: str, source: str) -> dict[str, Any]:
        """One anonymous request, identified to a company and never to a person.

        The response carries the five researched fields, the counters, and which
        intent pages the path satisfied. It carries nothing that describes a
        person, because the capture vocabulary is closed and no such value was
        stored.
        """
        request = parse_capture(payload, now=self.now())
        self._require_installation(request.client_id)
        who = request.actor or actor

        existing, company_key = resolve(self.store, request)
        created = existing is None
        record = (
            self.store.create(
                COMPANIES, blank_company(request, company_key), actor=who, source=source
            )
            if created
            else existing
        )

        data = record.get("data") or {}
        updated = self.store.update(
            record["id"], with_capture(data, request), actor=who, source=source
        )
        visit = self.store.create(
            VISITS,
            {**request.to_record(), "company_key": updated["data"]["company_key"]},
            actor=who,
            source=source,
        )

        page_list = [
            page_rules.row(entry) for entry in page_rules.for_client(self.store, request.client_id)
        ]
        matched = [
            page
            for page in page_list
            if matches(page.get("condition"), page.get("path"), request.path)
        ]

        return {
            "company_key": updated["data"]["company_key"],
            "company_created": created,
            "identified": "company",
            "company_level_only": True,
            "visit_id": visit.get("id"),
            "path": request.path,
            "captured_at": request.captured_at,
            "client_id": request.client_id,
            "company": {**detail_of(updated["data"]), **counters_of(updated["data"])},
            "matched_pages": matched,
            "matched_page_ids": [str(page["id"]) for page in matched],
            "matches": bool(matched),
        }

    def visits(self, company_key: str, *, limit: int = DEFAULT_VISIT_LIMIT) -> dict[str, Any]:
        """One company's page-visit events, newest first, grouped by path.

        "page-visit events recorded per URL path" - one row per request, each
        carrying the public parameters it arrived with. The ``top_paths`` roll-up
        answers "which pages has this company read" without a second query.
        """
        company = require_company(self.store, company_key)
        rows = self.store.find(VISITS, {"company_key": company_key}, limit=PAGE_SIZE)
        rows.sort(
            key=lambda row: str(
                (row.get("data") or {}).get("captured_at") or row.get("created_at") or ""
            ),
            reverse=True,
        )
        by_path: dict[str, int] = {}
        for row in rows:
            path = str((row.get("data") or {}).get("path") or "")
            by_path[path] = by_path.get(path, 0) + 1
        shown = rows[: max(1, min(int(limit), MAX_VISIT_LIMIT))]
        return {
            "company_key": company_key,
            "record_id": company.get("id"),
            "count": len(shown),
            "total": len(rows),
            "top_paths": sorted(by_path.items(), key=lambda entry: (-entry[1], entry[0])),
            "visits": [
                {
                    "id": row.get("id"),
                    "path": (row.get("data") or {}).get("path"),
                    "country": (row.get("data") or {}).get("country"),
                    "network": (row.get("data") or {}).get("network"),
                    "ip_address": (row.get("data") or {}).get("ip_address"),
                    "captured_at": (row.get("data") or {}).get("captured_at"),
                }
                for row in shown
            ],
        }

    # -- intent pages --------------------------------------------------------- #

    def pages(self, client_id: str = "") -> dict[str, Any]:
        """The Pages list for one client, or for every client when none is named."""
        if client_id:
            listed = [page_rules.row(r) for r in page_rules.for_client(self.store, client_id)]
        else:
            listed = [page_rules.row(r) for r in self._page_records()]
        return {"count": len(listed), "client_id": client_id, "pages": listed}

    def _page_records(self) -> list[dict[str, Any]]:
        return self.store.list(PAGES, limit=PAGE_SIZE, order_by="created_at")

    def define_page(
        self, payload: dict[str, Any], *, client_id: str, actor: str, source: str
    ) -> dict[str, Any]:
        """Steps 3 and 4: a name, a path without the domain, and a match condition."""
        page = page_rules.parse_page(payload, client_id=client_id)
        record = self.store.create(PAGES, page_rules.to_record(page), actor=actor, source=source)
        return page_rules.row(record)

    def amend_page(
        self,
        page_id: str,
        payload: dict[str, Any],
        *,
        client_id: str = "",
        actor: str = "",
        source: str = "",
    ) -> dict[str, Any]:
        """Change a page definition, re-checked the way a new one is checked."""
        record = self._require_page(page_id)
        merged = page_rules.amend(record.get("data") or {}, payload, client_id=client_id)
        checked = page_rules.parse_page(merged, client_id=client_id)
        updated = self.store.update(
            record["id"], page_rules.to_record(checked), actor=actor or "system", source=source
        )
        return page_rules.row(updated)

    def drop_page(self, page_id: str, *, actor: str = "system", source: str) -> dict[str, Any]:
        """Remove a page from the Pages list.

        Removed rather than deactivated, because the research describes one list
        with no states on it, and a page that matches nothing is indistinguishable
        from a page nobody defined.
        """
        record = self._require_page(page_id)
        self.store.delete(record["id"], actor=actor, source=source)
        return {"removed": True, "id": page_id, "name": (record.get("data") or {}).get("name")}

    def _require_page(self, page_id: str) -> dict[str, Any]:
        for record in self._page_records():
            if record.get("id") == page_id:
                return record
        raise UnknownPage(f"no intent page has id {page_id!r}.")

    def _matched_pages_for(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        listed = [page_rules.row(record) for record in self._page_records()]
        matched = set(leads.matched_page_ids(data, listed))
        return [page for page in listed if str(page.get("id")) in matched]

    def matched_pages(self, company_key: str) -> dict[str, Any]:
        """The pages a company's own visited paths satisfy, evaluated now."""
        company = require_company(self.store, company_key)
        listed = self._matched_pages_for(company.get("data") or {})
        return {
            "company_key": company_key,
            "count": len(listed),
            "pages": listed,
        }

    # -- ideal customer profiles ---------------------------------------------- #

    def profiles(self) -> dict[str, Any]:
        listed = [
            {"id": record.get("id"), **(record.get("data") or {})}
            for record in self.store.list(ICP_PROFILES, limit=PAGE_SIZE, order_by="created_at")
        ]
        return {"count": len(listed), "profiles": listed}

    def save_profile(self, payload: dict[str, Any], *, actor: str, source: str) -> dict[str, Any]:
        """Save an ideal customer profile for the lead-list filter.

        Re-saving under a name already in use updates it rather than refusing:
        the research describes one ICP and a seller tuning it is the ordinary case,
        and unlike a saved *view* there is no automation bound to the name.
        """
        profile = leads.parse_icp(payload)
        existing = self._profile_named(profile["name"])
        if existing:
            self.store.update(existing["id"], profile, actor=actor, source=source)
            return {"id": existing["id"], "created": False, **profile}
        created = self.store.create(ICP_PROFILES, profile, actor=actor, source=source)
        return {"id": created.get("id"), "created": True, **profile}

    def _profile_named(self, name: str) -> dict[str, Any] | None:
        for record in self.store.list(ICP_PROFILES, limit=PAGE_SIZE):
            if str((record.get("data") or {}).get("name") or "").lower() == str(name).lower():
                return record
        return None

    def drop_profile(
        self, profile_id: str, *, actor: str = "system", source: str
    ) -> dict[str, Any]:
        """Remove a saved profile. A lead-list filter naming it then refuses."""
        for record in self.store.list(ICP_PROFILES, limit=PAGE_SIZE):
            if record.get("id") == profile_id:
                self.store.delete(record["id"], actor=actor, source=source)
                return {"removed": True, "id": profile_id}
        raise UnknownIcp(f"no ideal customer profile has id {profile_id!r}.")

    def _require_profile(self, profile_id: str) -> dict[str, Any]:
        for record in self.store.list(ICP_PROFILES, limit=PAGE_SIZE):
            if record.get("id") == profile_id:
                return record.get("data") or {}
        raise UnknownIcp(f"no ideal customer profile has id {profile_id!r}.")

    # -- the lead list -------------------------------------------------------- #

    def lead_list(self, query: dict[str, Any]) -> dict[str, Any]:
        """Steps 5 and 6: the ranked list, filtered, with the five researched fields."""
        filters = leads.parse_filters(query)
        page_rows = [page_rules.row(record) for record in self._page_records()]
        known = {str(page.get("id")) for page in page_rows}
        for page_id in filters.pages:
            if str(page_id) not in known:
                raise UnknownPage(f"no intent page has id {page_id!r}.")

        profile = self._require_profile(filters.icp) if filters.icp else None
        rows = leads.build_rows(
            self.store.list(COMPANIES, limit=PAGE_SIZE), filters, page_rows, profile
        )
        return {
            "filters": filters.describe(),
            "summary": leads.summarise(rows),
            "pages": page_rows,
            "companies": rows,
        }

    def company(self, company_key: str) -> dict[str, Any]:
        """Step 6: one company, with its five fields and everything derived from it.

        The five researched fields come back present and possibly empty, because a
        company this workflow identified five minutes ago has none of them and that
        is a normal state, not a broken one.
        """
        record = require_company(self.store, company_key)
        data = record.get("data") or {}
        return {
            "id": record.get("id"),
            "revision": record.get("revision"),
            **detail_of(data),
            **counters_of(data),
            "identified_from": data.get("identified_from") or "capture",
            "matched_pages": self._matched_pages_for(data),
            "visits_href": f"/companies/{company_key}/visits",
            "downstream": [dict(entry) for entry in DOWNSTREAM_SURFACES],
        }

    def update_company(
        self, company_key: str, payload: dict[str, Any], *, actor: str, source: str
    ) -> dict[str, Any]:
        """Set the researched company fields: name, website, address, size, contacts.

        Also the segment and the tags, because the lead-list filter needs them and
        the research names both. Everything else on the record is derived from
        captures and is not writable here.
        """
        record = require_company(self.store, company_key)
        detail = parse_detail(payload)
        updated = self.store.update(record["id"], detail, actor=actor, source=source)
        return {
            "id": updated.get("id"),
            "revision": updated.get("revision"),
            **detail_of(updated["data"]),
            **counters_of(updated["data"]),
        }

    def create_company(self, payload: dict[str, Any], *, actor: str, source: str) -> dict[str, Any]:
        """Add a company by hand, for a site that has not been seen yet.

        "matched against the company database" implies the database has entries of
        its own, and a seller who already knows a company is in market should be
        able to say so before its network appears.
        """
        body = dict(payload or {})
        company_key = str(body.get("company_key") or "").strip()
        if not company_key:
            raise CompanyKeyRequired(
                "a company added by hand needs a company_key. There is no network behind this row "
                "to derive one from."
            )
        if self._find_by_key(keyed(company_key)) is not None:
            raise CompanyAlreadyIdentified(f"company {company_key!r} is already identified.")
        body.pop("company_key", None)
        detail = parse_detail(body)
        stamp = self.stamp()
        record = {
            "company_key": keyed(company_key),
            "identified_from": "manual",
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
            "first_seen_at": stamp,
            "last_visit_at": stamp,
            **detail,
        }
        created = self.store.create(COMPANIES, record, actor=actor, source=source)
        return {
            "id": created.get("id"),
            "revision": created.get("revision"),
            **detail_of(created["data"]),
            **counters_of(created["data"]),
        }

    def _find_by_key(self, company_key: str) -> dict[str, Any] | None:
        rows = self.store.find(COMPANIES, {"company_key": company_key}, limit=1)
        return rows[0] if rows else None
