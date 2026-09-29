"""The one object this workflow is driven through.

:class:`MarketIntentEngine` is the façade the HTTP layer calls. It holds nothing
but a :class:`~dsr.store.RecordStore` handle, which is why the feature module
builds one per request from ``StoreDep`` rather than hanging it on
``app.state``: an ``app.state`` entry would mean editing ``dsr/api.py``, and the
whole point of the feature host is that adding a workflow is adding a file.

Every method that writes takes a required ``source=``. That is not decoration.
The audit row is the product's guarantee, and an audit row that names a string
rather than a route cannot be traced back to the request that caused it - which
is a defect this codebase has already shipped once. A required keyword means the
omission is a ``TypeError`` at the call site rather than a silently untraceable
row in production.

The researched flow, end to end
-------------------------------
Tracking code fires :meth:`record_visit`; the visit is matched to a company by IP
or by a known contact, rolled up to its root domain, and stored as a fact.
:meth:`companies` builds the table from those facts and from research
observations. :meth:`save_view` persists a filter set; :meth:`save_automation`
switches on Add new companies and Track intent signals and stamps *when* each was
switched on. :meth:`run` then applies them - and only to companies that entered
the view after that stamp, which is the researched note made executable.
:meth:`run_category` does the same for the four stock categories, which name no
view and no time frame.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from dsr.market_intent import credits as credit_rules
from dsr.market_intent import names, observations, table
from dsr.market_intent import views as view_rules
from dsr.market_intent.criteria import Criterion, derived_properties, qualify_view
from dsr.market_intent.domains import is_valid_domain
from dsr.market_intent.domains import resolve as resolve_domain
from dsr.market_intent.domains import display_name
from dsr.market_intent.errors import (
    AlreadyExcluded,
    CreditsRequired,
    DomainExcluded,
    EnrichmentPermissionRequired,
    InvalidAutomation,
    InvalidConfiguration,
    InvalidSort,
    LifecycleStageRegression,
    UnknownCategory,
    UnknownVocabularyValue,
)
from dsr.market_intent.timeframe import resolve_window
from dsr.market_intent.vocabulary import (
    AUTOMATION_ADD,
    AUTOMATION_LABELS,
    AUTOMATION_TRACK,
    CATEGORY_IDS,
    CATEGORY_REQUIREMENTS,
    CREDIT_GATED_CAPABILITIES,
    ENRICHMENT_GATED_OPERATIONS,
    ENRICHMENT_PERMISSION,
    RECORD_SOURCE_BUYER_INTENT,
    describe as describe_vocabulary,
    lifecycle_rank,
)
from dsr.store import RecordStore

#: The documented outbound plan, recorded on every added company rather than
#: called. The research is explicit that its page is UI-first and that "no public
#: buyer-intent REST endpoint was documented in the page read", and the four
#: primitives it does cite are the documented shapes for a custom build. Storing
#: them as data keeps them visible and keeps a network call out of a route that
#: has to be audited and deterministic.
CRM_PLAN: dict[str, Any] = {
    "executed": False,
    "reason": (
        "The research's buyer-intent page documents no public REST endpoint, and these are the "
        "CRM API primitives it cites for a custom build. No outbound call is made."
    ),
    "record_source": {"property": "Record source", "value": RECORD_SOURCE_BUYER_INTENT},
    "auto_enrol_into": ["crm_records", "static_segments", "workflows"],
    "cited_primitives": [
        {
            "method": "POST",
            "path": "/crm/v3/objects/contacts/batch/upsert",
            "note": "",
        },
        {
            "method": "PATCH",
            "path": "/crm/v3/objects/contacts/{contactId}",
            "note": "lifecyclestage is forward-only",
        },
        {
            "method": "PUT",
            "path": "/crm/v3/objects/contacts/{contactId}/associations/"
            "{toObjectType}/{toObjectId}/{associationTypeId}",
            "note": "",
        },
        {
            "method": "GET",
            "path": "/crm/v4/associations/{fromObjectType}/{toObjectType}/labels",
            "note": "resolves association type IDs",
        },
    ],
    "scopes": ["crm.objects.contacts.read", "crm.objects.contacts.write"],
}

#: The credit-gated capabilities, and the enrichment-gated operations, are
#: declared in the vocabulary; these are the checks that read them.
SETTINGS_ID = "portal"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _as_aware(value: Any) -> datetime:
    """The engine's clock as an aware datetime, for the window resolver."""
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


class MarketIntentEngine:
    """Track in-market companies from intent signals, and add them to the CRM."""

    def __init__(self, store: RecordStore, *, now: Any = None) -> None:
        self.store = store
        # Injectable so a test can assert on ordering, watermarks, and billing
        # periods without freezing the whole process clock. The billing rule is
        # about "the same billing period", so the clock is not decoration here.
        self._now = now or _now

    # -- clock -------------------------------------------------------------- #

    def now(self) -> str:
        return self._now()

    def _window_for(self, filters: view_rules.FilterSet) -> Any:
        if not filters.timeframe:
            return None
        return resolve_window(now=_as_aware(self.now()), days=filters.days)

    # -- vocabulary and inferences ------------------------------------------ #

    def vocabulary(self) -> dict[str, Any]:
        return describe_vocabulary()

    def inferences(self) -> dict[str, Any]:
        from dsr.market_intent import inferences as inferences_module

        return inferences_module.describe()

    # -- settings and gating ------------------------------------------------ #

    def _settings_record(self) -> dict[str, Any] | None:
        found = self.store.find(names.SETTINGS, {"scope": SETTINGS_ID}, limit=1)
        return found[0] if found else None

    def settings(self) -> dict[str, Any]:
        """The portal's gating state.

        Defaults fail closed on both gates, and that is a decision rather than an
        oversight: "To access buyer intent features like filtering by segments and
        excluding companies, you need HubSpot Credits", so a portal that has
        never said it has credits does not get them, and an actor nobody has
        granted the Data enrichment permission does not have it.
        """
        record = self._settings_record()
        data = (record or {}).get("data") or {}
        return {
            "scope": SETTINGS_ID,
            "credits_enabled": bool(data.get("credits_enabled", False)),
            "enrichment_actors": sorted(str(value) for value in (data.get("enrichment_actors") or [])),
            "enrichment_permission": ENRICHMENT_PERMISSION,
            "record_id": (record or {}).get("id"),
        }

    def update_settings(self, patch: Any, *, actor: str | None, source: str) -> dict[str, Any]:
        """Change the gating state. Audited, because it changes what is possible."""
        if not isinstance(patch, Mapping):
            raise InvalidConfiguration("a settings patch must be an object")
        current = self.settings()
        updated: dict[str, Any] = {"scope": SETTINGS_ID}
        if "credits_enabled" in patch:
            if not isinstance(patch.get("credits_enabled"), bool):
                raise InvalidConfiguration("credits_enabled must be true or false")
            updated["credits_enabled"] = patch["credits_enabled"]
        else:
            updated["credits_enabled"] = current["credits_enabled"]
        if "enrichment_actors" in patch:
            granted = patch.get("enrichment_actors")
            if granted is None:
                granted = []
            if not isinstance(granted, (list, tuple, set)):
                raise InvalidConfiguration("enrichment_actors must be a list of actor names")
            updated["enrichment_actors"] = sorted({str(value).strip() for value in granted if str(value).strip()})
        else:
            updated["enrichment_actors"] = current["enrichment_actors"]

        record = self._settings_record()
        if record is None:
            record = self.store.create(names.SETTINGS, updated, actor=actor, source=source)
        else:
            record = self.store.update(record["id"], updated, actor=actor, source=source)
        return {**self.settings(), "record_id": record["id"]}

    def capabilities(self, actor: str | None = None) -> dict[str, Any]:
        """What this caller may do, so a page can hide what would be refused."""
        settings = self.settings()
        granted = str(actor or "") in settings["enrichment_actors"]
        return {
            "actor": actor or None,
            "credits_enabled": settings["credits_enabled"],
            "credit_gated_capabilities": list(CREDIT_GATED_CAPABILITIES),
            "enrichment_permission": ENRICHMENT_PERMISSION,
            "enrichment_granted": granted,
            "enrichment_gated_operations": list(ENRICHMENT_GATED_OPERATIONS),
            "can_filter_by_segment": settings["credits_enabled"],
            "can_manage_exclusions": settings["credits_enabled"],
            "can_add_companies": granted,
            "can_track_companies": granted,
            "can_enrol_companies": granted,
            "note": (
                "To access buyer intent features like filtering by segments and excluding "
                "companies, you need HubSpot Credits. To add and enrich companies from buyer "
                "intent, Super Admin must assign users with Data enrichment permissions."
            ),
        }

    def _require_credits(self, capability: str) -> None:
        if self.settings()["credits_enabled"]:
            return
        raise CreditsRequired(
            f"buyer intent features like {capability.replace('_', ' ')} need HubSpot Credits, "
            "and this portal does not have them",
            capability=capability,
        )

    def _require_enrichment(self, operation: str, actor: str | None) -> None:
        settings = self.settings()
        if str(actor or "") in settings["enrichment_actors"]:
            return
        raise EnrichmentPermissionRequired(
            f"{operation.replace('_', ' ')} writes a CRM record on the strength of an intent "
            "signal, which needs the Data enrichment permission; a Super Admin assigns it to a "
            f"user, and {actor or 'this caller'} does not hold it",
            actor=str(actor or ""),
        )

    # -- intent criteria, topics, markets, exclusions ------------------------ #

    def criteria(self) -> list[Criterion]:
        rows = self.store.list(names.CRITERIA, limit=names.SCAN_LIMIT, order_by="created_at")
        return [Criterion.from_record(row) for row in rows]

    def add_criterion(self, payload: Any, *, actor: str | None, source: str) -> dict[str, Any]:
        probe = Criterion.parse(payload, record_id="")
        record = self.store.create(
            names.CRITERIA,
            {
                "name": probe.name,
                "page_filters": [entry.to_dict() for entry in probe.page_filters],
                "derived_property": probe.derived_property,
                "site": probe.site,
                "active": True,
            },
            actor=actor,
            source=source,
        )
        return Criterion.from_record(record).to_dict()

    def withdraw_criterion(self, criterion_id: str, *, actor: str | None, source: str) -> dict[str, Any]:
        """Withdraw a criterion, keeping the row.

        Soft delete, not removal, because the derived property it wrote onto
        company records has to be re-derived to ``false`` rather than orphaned -
        and because a saved view naming it has to be able to say which criterion
        it can no longer match.
        """
        row = self.store.get(criterion_id)
        if row is None or row.get("collection") != names.CRITERIA:
            return {"criterion_id": criterion_id, "found": False, "withdrawn": False}
        updated = self.store.update(criterion_id, {"active": False}, actor=actor, source=source)
        return {
            "criterion_id": criterion_id,
            "found": True,
            "withdrawn": True,
            "criterion": Criterion.from_record(updated).to_dict(),
        }

    def topics(self) -> list[dict[str, Any]]:
        return [
            {**(row.get("data") or {}), "id": row["id"], "active": bool((row.get("data") or {}).get("active", True))}
            for row in self.store.list(names.TOPICS, limit=names.SCAN_LIMIT, order_by="created_at")
        ]

    def add_topic(self, payload: Any, *, actor: str | None, source: str) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise InvalidConfiguration("a research topic must be an object")
        name = payload.get("name")
        if not isinstance(name, str) or not name.strip():
            raise InvalidConfiguration("a research topic needs a name")
        terms = payload.get("terms") or []
        if not isinstance(terms, (list, tuple)):
            raise InvalidConfiguration("research topic terms must be a list of strings")
        record = self.store.create(
            names.TOPICS,
            {
                "name": name.strip(),
                "terms": sorted({str(term).strip().lower() for term in terms if str(term).strip()}),
                "active": True,
            },
            actor=actor,
            source=source,
        )
        return {**(record.get("data") or {}), "id": record["id"]}

    def withdraw_topic(self, topic_id: str, *, actor: str | None, source: str) -> dict[str, Any]:
        row = self.store.get(topic_id)
        if row is None or row.get("collection") != names.TOPICS:
            return {"topic_id": topic_id, "found": False, "withdrawn": False}
        updated = self.store.update(topic_id, {"active": False}, actor=actor, source=source)
        return {"topic_id": topic_id, "found": True, "withdrawn": True,
                "topic": {**(updated.get("data") or {}), "id": topic_id}}

    def markets(self) -> list[dict[str, Any]]:
        return [
            {**(row.get("data") or {}), "id": row["id"]}
            for row in self.store.list(names.MARKETS, limit=names.SCAN_LIMIT, order_by="created_at")
        ]

    def add_market(self, payload: Any, *, actor: str | None, source: str) -> dict[str, Any]:
        """A target market. "In my target markets" needs something to be in."""
        if not isinstance(payload, Mapping):
            raise InvalidConfiguration("a target market must be an object")
        name = payload.get("name")
        if not isinstance(name, str) or not name.strip():
            raise InvalidConfiguration("a target market needs a name")
        countries = [str(value).strip().upper() for value in (payload.get("countries") or []) if str(value).strip()]
        industries = [str(value).strip().lower() for value in (payload.get("industries") or []) if str(value).strip()]
        if not countries and not industries:
            raise InvalidConfiguration(
                f"target market {name!r} names neither countries nor industries, so every company "
                "would be in it and the 'In my target markets' filter would do nothing"
            )
        record = self.store.create(
            names.MARKETS,
            {
                "name": name.strip(),
                "countries": sorted(set(countries)),
                "industries": sorted(set(industries)),
            },
            actor=actor,
            source=source,
        )
        return {**(record.get("data") or {}), "id": record["id"]}

    def _exclusion_keys(self) -> dict[str, dict[str, Any]]:
        rows = self.store.find(names.EXCLUSIONS, {}, limit=names.SCAN_LIMIT)
        return {
            str((row.get("data") or {}).get("domain")): {"domain": (row.get("data") or {}).get("domain"),
                                                        "at": row.get("created_at")}
            for row in rows
            if (row.get("data") or {}).get("domain")
        }

    def exclusions(self) -> list[dict[str, Any]]:
        self._require_credits("exclusions")
        return sorted(self._exclusion_keys().values(), key=lambda row: str(row["domain"]))

    def exclude(self, payload: Any, *, actor: str | None, source: str) -> dict[str, Any]:
        """Exclude a domain, and everything rolled up into it.

        "exclusions by domain" plus the root-domain model: excluding
        ``www.south.example.com`` excludes ``south.example.com`` and every other
        host under it, because a filter keyed on a subdomain would stop excluding
        anything the moment the company used a different one.
        """
        self._require_credits("exclusions")
        raw = payload.get("domain") if isinstance(payload, Mapping) else payload
        info = resolve_domain(raw)
        if not info.root or info.is_ip or not is_valid_domain(raw):
            raise InvalidConfiguration(
                f"{raw!r} is not a registrable domain; an exclusion keyed on something that "
                "cannot be reduced to a root domain would exclude nothing"
            )
        existing = self._exclusion_keys()
        if info.root in existing:
            raise AlreadyExcluded(
                f"{info.root} is already excluded; two rows for one domain would make 'is it "
                "excluded?' have two answers"
            )
        record = self.store.create(
            names.EXCLUSIONS,
            {"domain": info.root, "excluded_as": info.host, "reason": _reason(payload)},
            actor=actor,
            source=source,
        )
        return {**(record.get("data") or {}), "id": record["id"], "root_domain": info.root}

    def unexclude(self, domain: str, *, actor: str | None, source: str) -> dict[str, Any]:
        self._require_credits("exclusions")
        info = resolve_domain(domain)
        existing = self._exclusion_keys()
        if info.root not in existing:
            return {"domain": info.root, "found": False, "removed": False}
        row = self.store.find(names.EXCLUSIONS, {"domain": info.root}, limit=1)[0]
        self.store.delete(row["id"], actor=actor, source=source)
        return {"domain": info.root, "found": True, "removed": True}

    # -- observations -------------------------------------------------------- #

    def _resolver(self) -> Any:
        """Match an anonymous visitor to a company, as the data flow describes.

        "Buyer intent connects anonymous web visitors to known companies' IP
        addresses" for the companies already in the account, and the research's
        ``data_sources`` names "company IP-to-company matching" for the rest -
        a pipeline step outside this product whose *result* arrives with the page
        view as ``company_domain``. Both are honoured, and the first is tried
        first so a company the account already knows is never attributed on
        somebody else's word.

        The IP map is read off the company records rather than a separate table,
        because a company's known addresses are a property of the company and a
        team adding one should not have to add it in two places.
        """
        companies = self.store.list(names.COMPANIES, limit=names.SCAN_LIMIT)
        by_ip: dict[str, str] = {}
        for row in companies:
            data = row.get("data") or {}
            key = str(data.get("root_domain") or "")
            if not key:
                continue
            for address in data.get("known_ips") or []:
                by_ip[str(address).strip()] = key

        contacts = self.store.list(names.CONTACTS, limit=names.SCAN_LIMIT)
        by_contact: dict[str, str] = {}
        for row in contacts:
            data = row.get("data") or {}
            key = str(data.get("company_key") or "")
            if not key:
                continue
            for field_name in ("email", "contact_id"):
                value = str(data.get(field_name) or "").strip().lower()
                if value:
                    by_contact[value] = key

        def resolve_attribution(
            ip: str, contact: str, declared: str
        ) -> tuple[str | None, str]:
            if ip:
                match = by_ip.get(ip.strip())
                if match:
                    return (match, "ip")
            if contact:
                needle = contact.strip().lower()
                match = by_contact.get(needle)
                if match:
                    return (match, "contact")
                domain = observations.contact_domain(needle)
                if domain:
                    # A known contact's email domain is the same roll-up the
                    # table uses, so a company with no IP on file is still
                    # reachable by someone who wrote to it.
                    for row in companies:
                        data = row.get("data") or {}
                        if str(data.get("root_domain") or "") == domain:
                            return (domain, "email_domain")
            if declared:
                return (resolve_domain(declared).root, "ip_match")
            return (None, "none")

        return resolve_attribution

    def record_visit(self, payload: Any, *, actor: str | None, source: str) -> dict[str, Any]:
        """One tracked page view. Facts only; nothing derived is stored."""
        data = observations.normalise_visit(payload, resolver=self._resolver())
        record = self.store.create(names.VISITS, data, actor=actor, source=source)
        return {
            "id": record["id"],
            "company_key": data["company_key"],
            "root_domain": data["root_domain"],
            "host": data["host"],
            "path": data["path"],
            "occurred_at": data["occurred_at"],
            "attribution": data["attribution"],
            "attribution_note": observations.describe_attribution(data["attribution"]),
            "known": data["known"],
            "visit_id": record["id"],
        }

    def record_research(self, payload: Any, *, actor: str | None, source: str) -> dict[str, Any]:
        """One research observation: a topic match, or a company news signal."""
        data = observations.normalise_research(payload)
        topics = [row for row in self.topics() if row.get("active", True)]
        observations.mark_topics(data, topics)
        record = self.store.create(names.RESEARCH, data, actor=actor, source=source)
        return {"id": record["id"], **data}

    # -- the table ------------------------------------------------------------ #

    def _snapshot(self) -> table.Snapshot:
        """Read everything the table is built from, once.

        Ordered by ``created_at`` rather than by ``occurred_at``: the store's
        ``order_by`` allowlist covers the envelope's own columns, and a team
        adding a field must not need a migration to make it sortable. Every
        consumer here sorts by ``occurred_at`` in Python anyway, which is what
        makes the table's own "Last visit" ordering independent of insertion
        order.
        """
        limit = names.SCAN_LIMIT
        visits = self.store.list(names.VISITS, limit=limit, order_by="created_at")
        research = self.store.list(names.RESEARCH, limit=limit, order_by="created_at")
        companies = self.store.list(names.COMPANIES, limit=limit, order_by="created_at")
        contacts = self.store.list(names.CONTACTS, limit=limit, order_by="created_at")
        trackings = {
            str((row.get("data") or {}).get("company_key")): {"company_key": (row.get("data") or {}).get("company_key"),
                                                              "since": (row.get("data") or {}).get("since"),
                                                              "period": (row.get("data") or {}).get("period"),
                                                              "periods": (row.get("data") or {}).get("periods") or [],
                                                              "last_charged_period": (row.get("data") or {}).get(
                                                                  "last_charged_period")}
            for row in self.store.list(names.TRACKING, limit=limit, order_by="created_at")
            if (row.get("data") or {}).get("company_key")
        }
        return table.Snapshot(
            visits=[row.get("data") or {} for row in visits],
            research=[row.get("data") or {} for row in research],
            companies={str((row.get("data") or {}).get("root_domain")): row for row in companies
                       if (row.get("data") or {}).get("root_domain")},
            contacts=[row.get("data") or {} for row in contacts],
            criteria=self.criteria(),
            topics=[row for row in self.topics() if row.get("active", True)],
            markets=[
                {"id": row["id"], **(row.get("data") or {})}
                for row in self.store.list(names.MARKETS, limit=limit)
            ],
            exclusions=self._exclusion_keys(),
            trackings=trackings,
            enrolments=[row.get("data") or {} for row in self.store.list(names.ENROLMENTS, limit=limit)],
            truncated={
                names.VISITS: len(visits) >= limit,
                names.RESEARCH: len(research) >= limit,
                names.COMPANIES: len(companies) >= limit,
            },
        )

    def _rows(self, snapshot: table.Snapshot | None = None) -> list[dict[str, Any]]:
        return table.build_rows(snapshot if snapshot is not None else self._snapshot())

    def companies(
        self,
        filters: view_rules.FilterSet | None = None,
        *,
        actor: str | None = None,
        now: Any = None,
    ) -> dict[str, Any]:
        """The Visitors tab: every company, filtered and sorted.

        An excluded domain is not in this table at all. That is the point of an
        exclusion - "excluding companies" is what the research says the credits
        buy - and hiding it rather than greying it is what makes it effective.
        """
        if filters is not None and filters.segment:
            # The vocabulary id, not a phrase: a client branches on the id, and a
            # message that names the capability is what the 402 detail is for.
            self._require_credits(CREDIT_GATED_CAPABILITIES[0])
        snapshot = self._snapshot()
        rows = [row for row in table.build_rows(snapshot) if not row.get("excluded")]
        window = self._window_for(filters) if filters is not None else None
        matched = rows
        if filters is not None:
            match_dict = filters.as_match_dict()
            matched = [row for row in rows if table.matches_filters(row, match_dict, window=window)]
        ordered = table.sort_rows(matched, key=filters.sort, direction=filters.direction) if filters else table.sort_rows(matched)
        return {
            "count": len(ordered),
            "total_before_filters": len(rows),
            "companies": ordered,
            "window": table.describe_window(window),
            "filters": filters.to_dict() if filters is not None else None,
            "unattributed_views": table.unattributed(snapshot),
            "truncated": {name: flag for name, flag in snapshot.truncated.items() if flag},
            "note": (
                "Buyer intent stores company-level website activity in a table, including website "
                "visits, unique visitors, last visit, and top page views. Activity from "
                "subdomains is rolled up into the root domain, which is what makes one row per "
                "company."
            ),
        }

    def company(self, company_key: str, *, actor: str | None = None) -> dict[str, Any] | None:
        """One company, with the About tab's fields."""
        snapshot = self._snapshot()
        row = table.row_for(snapshot, company_key)
        return row

    def update_company(
        self, company_key: str, patch: Any, *, actor: str | None, source: str
    ) -> dict[str, Any]:
        """Update a company record's CRM fields.

        ``lifecyclestage is forward-only`` is the one rule here with a primary
        source, and it is enforced rather than documented: a company whose stage
        moved backwards drops out of every view that filters on it, silently.
        """
        if not isinstance(patch, Mapping):
            raise InvalidConfiguration("a company patch must be an object")
        key = resolve_domain(company_key).root
        existing = self.store.find(names.COMPANIES, {"root_domain": key}, limit=1)
        if not existing:
            return {"company_key": key, "found": False, "updated": False}
        record = existing[0]
        current = record.get("data") or {}

        proposed = str(patch.get("lifecycle_stage") or current.get("lifecycle_stage") or "")
        if proposed and current.get("lifecycle_stage"):
            if observations.is_forward_only(current.get("lifecycle_stage"), proposed):
                raise LifecycleStageRegression(
                    f"lifecyclestage is forward-only, and moving {current.get('lifecycle_stage')!r} "
                    f"back to {proposed!r} would drop the company out of every view that filters "
                    "on it without saying why"
                )

        updates: dict[str, Any] = {}
        for field_name in ("name", "segment", "deal_stage", "owner", "industry", "country"):
            if field_name in patch:
                updates[field_name] = patch.get(field_name)
        if "lifecycle_stage" in patch:
            updates["lifecycle_stage"] = proposed or None
        if "known_ips" in patch:
            known = patch.get("known_ips") or []
            if not isinstance(known, (list, tuple)):
                raise InvalidConfiguration("known_ips must be a list of IP addresses")
            updates["known_ips"] = sorted({str(value).strip() for value in known if str(value).strip()})
        extra = patch.get("properties")
        if isinstance(extra, Mapping):
            updates["properties"] = {**(current.get("properties") or {}), **dict(extra)}

        before_stage = str(current.get("lifecycle_stage") or "")
        changed = bool(proposed and proposed != before_stage)
        updated = self.store.update(record["id"], updates, actor=actor, source=source)
        if not changed:
            move = "unchanged"
        elif lifecycle_rank(before_stage) is None or lifecycle_rank(proposed) is None:
            # "lifecyclestage is forward-only" is sourced, but the *order* is not,
            # and this product cannot say whether an unfamiliar stage is a
            # regression. It says so rather than claiming a direction it does not
            # have.
            move = "unchecked"
        else:
            move = "forward"
        return {
            "company_key": key,
            "found": True,
            "updated": True,
            "record_id": updated["id"],
            "lifecycle_stage": updated["data"].get("lifecycle_stage"),
            "lifecycle_move": move,
            "record": updated["data"],
        }

    def card(self, company_key: str, *, actor: str | None = None) -> dict[str, Any]:
        """The Buyer Intent card, with the four researched fields.

        "Settings -> Objects -> Companies -> Record Customization -> Add cards" -
        the card is a thing added to the company record, and these are the fields
        it carries. Anything else on the row is the record, not the card.
        """
        snapshot = self._snapshot()
        row = table.row_for(snapshot, company_key)
        if row is None:
            return {"company_key": resolve_domain(company_key).root, "found": False}
        return {
            "found": True,
            "company_key": row["company_key"],
            "name": row["name"],
            "root_domain": row["root_domain"],
            "fields": {
                "website_visits": {
                    "value": row["website_visits"],
                    "label": "Website visits",
                    "note": "the count of sessions of website visits from this company",
                },
                "unique_visitors": {"value": row["unique_visitors"], "label": "Unique visitors"},
                "last_seen": {"value": row["last_seen_at"], "label": "Last seen"},
                "top_page_views": {
                    "value": row["top_page_views"],
                    "label": "Top page views",
                    "note": "the pages with the most visits from visitors from this company",
                },
            },
            "full_activity": {"tab": "page-views", "count": row["page_views"]},
            "in_crm": row["in_crm"],
            "crm_icon": row["crm_icon"],
            "record_source": row["record_source"],
        }

    def page_views(self, company_key: str, *, limit: int = 50) -> dict[str, Any]:
        """The Recent page views drill-down, with the Intent tag on each row.

        "including IP-derived country and date and time of the website visit" -
        both are stored with the visit, so neither has to be reconstructed. The
        tag is recomputed here rather than read off the visit, for the reason in
        :mod:`dsr.market_intent.observations`: a criterion added today has to be
        able to tag a visit from last week, and a withdrawn one has to stop.
        """
        key = resolve_domain(company_key).root
        snapshot = self._snapshot()
        criteria = snapshot.criteria
        newest_first = sorted(
            [
                visit
                for visit in snapshot.visits
                if str(visit.get("company_key") or "") == key
            ],
            key=lambda visit: str(visit.get("occurred_at") or ""),
            reverse=True,
        )
        rows: list[dict[str, Any]] = []
        for visit in newest_first[: max(1, min(int(limit), 200))]:
            matched = qualify_view(visit, criteria)
            rows.append(
                {
                    "occurred_at": visit.get("occurred_at"),
                    "host": visit.get("host"),
                    "path": visit.get("path"),
                    "url": visit.get("url"),
                    "country": visit.get("country"),
                    "country_source": "ip" if visit.get("country") else None,
                    "traffic_source": visit.get("traffic_source"),
                    "session_id": visit.get("session_id"),
                    "visitor_id": visit.get("visitor_id"),
                    "intent": ({**matched, "tagged": "Intent"} if matched else None),
                }
            )
        return {
            "company_key": key,
            "count": len(rows),
            "total": len(newest_first),
            "page_views": rows,
            "note": (
                "If you're reviewing companies that have shown intent, the specific domain and "
                "page path that qualified the company will be tagged with Intent."
            ),
        }

    def contacts(self, company_key: str) -> dict[str, Any]:
        """The Contacts tab.

        "you can also review the contact's last touch, last engagement, and any
        recently scheduled interactions such as planned meetings."
        """
        key = resolve_domain(company_key).root
        rows = [
            dict(row.get("data") or {})
            for row in self.store.list(names.CONTACTS, limit=names.SCAN_LIMIT)
            if str((row.get("data") or {}).get("company_key") or "") == key
        ]
        return {
            "company_key": key,
            "count": len(rows),
            "contacts": sorted(rows, key=lambda row: str(row.get("last_touch_at") or ""), reverse=True),
            "fields": ["last_touch_at", "last_engagement_at", "scheduled"],
        }

    def research_tab(self, filters: view_rules.FilterSet | None = None, *, actor: str | None = None) -> dict[str, Any]:
        """The Research tab: who is researching a topic, and who is in the news.

        Two different things under one tab, reported separately so a company that
        raised money is not counted as a company reading about cloud security.
        """
        snapshot = self._snapshot()
        rows = [row for row in table.build_rows(snapshot) if not row.get("excluded")]
        window = self._window_for(filters) if filters is not None else None
        if filters is not None:
            rows = [
                row
                for row in rows
                if table.matches_filters(row, filters.as_match_dict(), window=window)
                and (row.get("research_intent") or not filters.visitor_intent)
            ]
        with_research = [row for row in rows if row.get("research_intent")]
        topics: dict[str, int] = {}
        news: dict[str, int] = {}
        for row in with_research:
            for evidence in row.get("research_evidence") or []:
                bucket = topics if evidence.get("kind") == "topic" else news
                label = str(evidence.get("topic_matched") or evidence.get("topic") or evidence.get("signal_type"))
                bucket[label] = bucket.get(label, 0) + 1
        return {
            "count": len(with_research),
            "companies": with_research,
            "topic_matches": {key: topics[key] for key in sorted(topics)},
            "news_signals": {key: news[key] for key in sorted(news)},
            "news_signal_types": [
                {
                    "signal_type": signal,
                    "companies": [
                        row["company_key"]
                        for row in with_research
                        for evidence in row.get("research_evidence") or []
                        if evidence.get("kind") == "news" and evidence.get("signal_type") == signal
                    ],
                }
                for signal in sorted(news)
            ],
            "window": table.describe_window(window),
        }

    # -- saved views --------------------------------------------------------- #

    def views(self) -> list[dict[str, Any]]:
        rows = self.store.list(names.VIEWS, limit=names.SCAN_LIMIT, order_by="created_at")
        return [{**(row.get("data") or {}), "id": row["id"], "created_at": row.get("created_at")} for row in rows]

    def view(self, view_id: str) -> dict[str, Any] | None:
        for row in self.store.list(names.VIEWS, limit=names.SCAN_LIMIT):
            if row["id"] == view_id:
                return {**(row.get("data") or {}), "id": row["id"], "created_at": row.get("created_at")}
        return None

    def save_view(self, payload: Any, *, actor: str | None, source: str) -> dict[str, Any]:
        """"Click Save view to persist the filter set as a named view."

        A name already in use is refused rather than overwritten: an automation
        is attached to a view, and two views under one name would make which one
        it watches a matter of load order.
        """
        if not isinstance(payload, Mapping):
            raise InvalidConfiguration("a saved view must be an object")
        name = payload.get("name")
        if not isinstance(name, str) or not name.strip():
            raise InvalidConfiguration("a saved view needs a name")
        filters = view_rules.FilterSet.parse(payload.get("filters"))
        trimmed = name.strip()
        for existing in self.views():
            if str(existing.get("name") or "") == trimmed:
                from dsr.market_intent.errors import DuplicateViewName

                raise DuplicateViewName(
                    f"a saved view named {trimmed!r} already exists; overwriting it would leave "
                    "its automation watching a filter set nobody chose"
                )
        record = self.store.create(
            names.VIEWS,
            {"name": trimmed, "filters": filters.to_dict()},
            actor=actor,
            source=source,
        )
        return {"id": record["id"], "name": trimmed, "filters": filters.to_dict(), "created_at": record["created_at"]}

    def withdraw_view(self, view_id: str, *, actor: str | None, source: str) -> dict[str, Any]:
        """Remove a saved view and the automation attached to it.

        Together, because an automation whose view no longer exists has nothing
        to fire on, and leaving it would report a healthy automation that does
        nothing at all.
        """
        existing = self.view(view_id)
        if existing is None:
            return {"view_id": view_id, "found": False, "removed": False}
        self.store.delete(view_id, actor=actor, source=source)
        removed = []
        for row in self.store.find(names.AUTOMATIONS, {"view_id": view_id}, limit=10):
            self.store.delete(row["id"], actor=actor, source=source)
            removed.append(row["id"])
        return {"view_id": view_id, "found": True, "removed": True, "automations_removed": removed}

    def _view_memberships(
        self, view: Mapping[str, Any], snapshot: table.Snapshot
    ) -> tuple[list[view_rules.Membership], Any, view_rules.FilterSet]:
        """Every company currently in a view, the window, and the filter set.

        Takes the snapshot rather than taking one so that a caller applying an
        automation re-reads the table once and then acts on one consistent view
        of it, instead of re-reading per company inside the loop.

        Excluded domains are *not* filtered here. :meth:`view_companies` hides
        them from the seller's list, and :meth:`run` keeps seeing them so that
        the run can report ``domain_excluded`` as the reason it declined to act -
        a company the view's own filter set matches but the configuration
        forbids is worth a line in the response and worth nothing in a list.
        """
        filters = view_rules.FilterSet.parse(view.get("filters"))
        window = self._window_for(filters)
        rows = table.build_rows(snapshot)
        memberships = view_rules.memberships(rows, filters, snapshot)
        return (
            [entry for entry in memberships if view_rules.in_view(entry, filters, window=window)],
            window,
            filters,
        )

    def view_companies(self, view_id: str, *, actor: str | None = None) -> dict[str, Any]:
        """Which companies are in a saved view, and when each entered it.

        The entry time is the researched note made visible: a company that has
        been in the view for months shows an ``entered_at`` older than the
        automation's switch-on, which is why it is not auto-added.
        """
        view = self.view(view_id)
        if view is None:
            return {"view_id": view_id, "found": False}
        snapshot = self._snapshot()
        members, window, filters = self._view_memberships(view, snapshot)
        automation = self.automation_for(view_id) or {}
        add_enabled_at = automation.get("add_enabled_at")
        track_enabled_at = automation.get("track_enabled_at")
        rows = [
            {
                "company_key": entry.company_key,
                "entered_at": entry.entered_at,
                "entered_after_auto_add": (
                    bool(entry.entered_at and add_enabled_at and entry.entered_at > add_enabled_at)
                ),
                "entered_after_tracking": (
                    bool(entry.entered_at and track_enabled_at and entry.entered_at > track_enabled_at)
                ),
                "company": entry.row,
            }
            for entry in members
            if not entry.row.get("excluded")
        ]
        rows.sort(key=lambda entry: (str(entry.get("entered_at") or ""), entry["company_key"]), reverse=True)
        return {
            "view_id": view_id,
            "name": view.get("name"),
            "found": True,
            "count": len(rows),
            "companies": rows,
            "window": table.describe_window(window),
            "filters": filters.to_dict(),
            "automation": automation,
            "note": (
                "auto-add will only add companies that enter your saved views after enabling the "
                "auto-add. It will not add all existing companies in your saved views."
            ),
        }

    # -- automations --------------------------------------------------------- #

    def automations(self) -> list[dict[str, Any]]:
        rows = self.store.list(names.AUTOMATIONS, limit=names.SCAN_LIMIT, order_by="created_at")
        return [{**(row.get("data") or {}), "id": row["id"], "created_at": row.get("created_at")} for row in rows]

    def automation_for(self, view_id: str) -> dict[str, Any] | None:
        found = self.store.find(names.AUTOMATIONS, {"view_id": view_id}, limit=1)
        if not found:
            return None
        return {**(found[0].get("data") or {}), "id": found[0]["id"], "created_at": found[0].get("created_at")}

    def save_automation(self, payload: Any, *, actor: str | None, source: str) -> dict[str, Any]:
        """"Toggle the switches on. At the bottom, click Save automation."

        Switching a toggle on stamps *when* it was switched on, and that stamp is
        the watermark the researched note is about. Saving a toggle that is
        already on does not re-stamp it: re-stamping would move the watermark
        forward on every save and a company that entered the view last week would
        never be added, which is the opposite of what the toggle promises.
        """
        if not isinstance(payload, Mapping):
            raise InvalidAutomation("an automation must be an object")
        view_id = str(payload.get("view_id") or "")
        if not view_id:
            raise InvalidAutomation(
                "an automation names the saved view it fires on; automations are configured per "
                "view, from the view's own entry in the left panel"
            )
        if self.view(view_id) is None:
            raise InvalidAutomation(
                f"no saved view with id {view_id!r}; automations are configured per saved view, so "
                "this one would have nothing to fire on"
            )

        existing = self.automation_for(view_id)
        at = self.now()
        add_on = bool(payload.get(AUTOMATION_ADD, False))
        track_on = bool(payload.get(AUTOMATION_TRACK, False))
        prior = existing or {}
        data: dict[str, Any] = {
            "view_id": view_id,
            AUTOMATION_ADD: add_on,
            AUTOMATION_TRACK: track_on,
            # Only stamped on the transition to on. Never cleared: the researched
            # note is about when auto-add was *enabled*, and a toggle switched
            # off and on again must not add everything that arrived in between.
            "add_enabled_at": at if add_on and not prior.get(AUTOMATION_ADD) else prior.get("add_enabled_at"),
            "track_enabled_at": at if track_on and not prior.get(AUTOMATION_TRACK) else prior.get(
                "track_enabled_at"
            ),
            "saved_at": at,
        }
        if existing is None:
            record = self.store.create(names.AUTOMATIONS, data, actor=actor, source=source)
        else:
            record = self.store.update(existing["id"], data, actor=actor, source=source)
        return {**data, "id": record["id"], "created_at": record["created_at"]}

    def run(self, automation_id: str, *, actor: str | None, source: str) -> dict[str, Any]:
        """Apply one view's automation to the companies currently in its view.

        The researched rule, in full: "auto-add will only add companies that
        enter your saved views after enabling the auto-add. It will not add all
        existing companies in your saved views." A company already qualifying
        when the switch was thrown is reported in ``held_back`` with that reason,
        so the rule is something a reviewer can read off the response rather than
        something they have to take on trust.
        """
        automation = self.automation(automation_id)
        if automation is None:
            return {"automation_id": automation_id, "found": False}
        view = self.view(str(automation.get("view_id") or ""))
        if view is None:
            raise InvalidAutomation(
                f"automation {automation_id} points at saved view {automation.get('view_id')!r}, "
                "which no longer exists"
            )

        snapshot = self._snapshot()
        members, window, _filters = self._view_memberships(view, snapshot)
        at = self.now()
        add_enabled_at = str(automation.get("add_enabled_at") or "")
        track_enabled_at = str(automation.get("track_enabled_at") or "")
        add_on = bool(automation.get(AUTOMATION_ADD))
        track_on = bool(automation.get(AUTOMATION_TRACK))

        added: list[dict[str, Any]] = []
        tracked: list[dict[str, Any]] = []
        held_back: list[dict[str, Any]] = []

        for member in members:
            row = member.row
            key = member.company_key
            entered = str(member.entered_at or "")
            outcome: dict[str, Any] = {"company_key": key, "entered_at": member.entered_at}

            if row.get("excluded"):
                held_back.append({**outcome, "action": AUTOMATION_ADD, "reason": "domain_excluded"})
                continue

            if add_on and not row.get("in_crm"):
                if not add_enabled_at:
                    held_back.append({**outcome, "action": AUTOMATION_ADD, "reason": "never_enabled"})
                elif entered <= add_enabled_at:
                    held_back.append(
                        {
                            **outcome,
                            "action": AUTOMATION_ADD,
                            "reason": "entered_before_auto_add_was_enabled",
                            "enabled_at": add_enabled_at,
                        }
                    )
                else:
                    result = self._add_company(
                        row,
                        snapshot,
                        via=f"auto-add:{view.get('name')}",
                        actor=actor,
                        source=source,
                        at=at,
                    )
                    added.append(result)
                    outcome["added"] = result.get("outcome")

            if track_on and not row.get("tracked"):
                if not track_enabled_at:
                    held_back.append({**outcome, "action": AUTOMATION_TRACK, "reason": "never_enabled"})
                elif entered <= track_enabled_at:
                    held_back.append(
                        {
                            **outcome,
                            "action": AUTOMATION_TRACK,
                            "reason": "entered_before_tracking_was_enabled",
                            "enabled_at": track_enabled_at,
                        }
                    )
                else:
                    result = self._track_company(
                        row, via=f"auto-track:{view.get('name')}", actor=actor, source=source, at=at
                    )
                    tracked.append(result)

        derived = self.refresh_derived_properties(actor=actor, source=source, at=at)

        return {
            "automation_id": automation_id,
            "found": True,
            "view": {"id": view["id"], "name": view.get("name")},
            "toggles": {
                AUTOMATION_ADD: add_on,
                AUTOMATION_TRACK: track_on,
                "labels": AUTOMATION_LABELS,
            },
            "watermarks": {"add_enabled_at": add_enabled_at or None, "track_enabled_at": track_enabled_at or None},
            "window": table.describe_window(window),
            "matched": len(members),
            "added": added,
            "tracked": tracked,
            "held_back": held_back,
            "derived_properties_refreshed": derived,
            "note": (
                "auto-add will only add companies that enter your saved views after enabling the "
                "auto-add. It will not add all existing companies in your saved views."
            ),
        }

    def automation(self, automation_id: str) -> dict[str, Any] | None:
        for row in self.store.list(names.AUTOMATIONS, limit=names.SCAN_LIMIT):
            if row["id"] == automation_id:
                return {**(row.get("data") or {}), "id": row["id"]}
        return None

    # -- the four stock auto-add categories ----------------------------------- #

    def categories(self) -> list[dict[str, Any]]:
        states = {
            str((row.get("data") or {}).get("category_id")): (row.get("data") or {})
            for row in self.store.list(names.CATEGORIES, limit=len(CATEGORY_IDS) + 4)
        }
        return [
            {
                "id": category_id,
                **CATEGORY_REQUIREMENTS[category_id],
                "enabled": bool(states.get(category_id, {}).get("enabled")),
                "enabled_at": states.get(category_id, {}).get("enabled_at"),
                "last_run_at": states.get(category_id, {}).get("last_run_at"),
                "added": sorted(states.get(category_id, {}).get("added") or []),
                "enriched": sorted(states.get(category_id, {}).get("enriched") or []),
            }
            for category_id in CATEGORY_IDS
        ]

    def set_category(self, category_id: str, payload: Any, *, actor: str | None, source: str) -> dict[str, Any]:
        """Enable or disable one stock category, stamping when it was enabled."""
        if category_id not in CATEGORY_REQUIREMENTS:
            raise UnknownCategory(
                f"{category_id!r} is not one of the four stock auto-add categories: "
                f"{', '.join(CATEGORY_IDS)}"
            )
        if not isinstance(payload, Mapping):
            raise InvalidConfiguration("a category toggle must be an object with enabled true or false")
        unexpected = sorted(set(payload) - {"enabled"})
        if unexpected:
            # The research enumerates the four and calls them stock. A caller
            # sending "requires_in_crm" is asking to redefine what "net-new
            # companies with visitor intent" means, and a stock category that can
            # be redefined is not stock.
            raise InvalidConfiguration(
                f"{category_id!r} is a stock category and its definition is fixed; only "
                f"'enabled' may be sent, and these were refused: {', '.join(unexpected)}"
            )
        enabled = bool(payload.get("enabled", True))
        found = self.store.find(names.CATEGORIES, {"category_id": category_id}, limit=1)
        at = self.now()
        prior = (found[0].get("data") or {}) if found else {}
        data: dict[str, Any] = {
            "category_id": category_id,
            "enabled": enabled,
            "enabled_at": at if enabled and not prior.get("enabled") else prior.get("enabled_at"),
            "added": prior.get("added") or [],
            "enriched": prior.get("enriched") or [],
        }
        if found:
            record = self.store.update(found[0]["id"], data, actor=actor, source=source)
        else:
            record = self.store.create(names.CATEGORIES, data, actor=actor, source=source)
        return {**data, "id": record["id"], "label": CATEGORY_REQUIREMENTS[category_id]["label"]}

    @staticmethod
    def category_matches(row: Mapping[str, Any], requirements: Mapping[str, Any]) -> bool:
        """One stock category's predicate, over a table row.

        Read off the research's own wording for each, which is not uniform; see
        ``category_target_market_asymmetry`` in
        :mod:`dsr.market_intent.inferences`.
        """
        if requirements.get("requires_target_market") and not row.get("in_target_markets"):
            return False
        if requirements.get("requires_visitor_intent") and not row.get("visitor_intent"):
            return False
        if requirements.get("requires_research_intent") and not row.get("research_intent"):
            return False
        if bool(requirements.get("requires_in_crm")) != bool(row.get("in_crm")):
            return False
        return True

    def run_category(self, category_id: str, *, actor: str | None, source: str) -> dict[str, Any]:
        """Apply one stock category across the whole table.

        Same watermark as the per-view automation and for the same reason: a
        category switched on today is a decision about companies that arrive
        today. The research states the rule for saved views; applying it to the
        stock categories is an inference and is named as one.
        """
        requirements = CATEGORY_REQUIREMENTS.get(category_id)
        if requirements is None:
            raise UnknownCategory(
                f"{category_id!r} is not one of the four stock auto-add categories: "
                f"{', '.join(CATEGORY_IDS)}"
            )
        found = self.store.find(names.CATEGORIES, {"category_id": category_id}, limit=1)
        state = (found[0].get("data") or {}) if found else {}
        at = self.now()
        if not state.get("enabled"):
            return {
                "category_id": category_id,
                "label": requirements["label"],
                "enabled": False,
                "found": True,
                "matched": 0,
                "added": [],
                "held_back": [],
                "reason": "This category is off; the research says these are turned on optionally.",
            }

        enabled_at = str(state.get("enabled_at") or "")
        snapshot = self._snapshot()
        rows = [row for row in table.build_rows(snapshot) if not row.get("excluded")]
        matched = [row for row in rows if self.category_matches(row, requirements)]

        added: list[dict[str, Any]] = []
        enriched: list[dict[str, Any]] = []
        held_back: list[dict[str, Any]] = []
        for row in matched:
            key = str(row["company_key"])
            last_seen = str(row.get("last_seen_at") or "")
            if not enabled_at:
                held_back.append({"company_key": key, "reason": "never_enabled"})
            elif last_seen <= enabled_at:
                held_back.append(
                    {
                        "company_key": key,
                        "reason": "last_seen_before_the_category_was_enabled",
                        "last_seen_at": row.get("last_seen_at"),
                        "enabled_at": enabled_at,
                    }
                )
            elif row.get("in_crm"):
                # Already in the account, so the action is the enrich half of
                # "add and enrich", not a second record.
                enriched.append(
                    self._enrich_company(
                        row,
                        snapshot,
                        via=f"auto-enrich:{category_id}",
                        actor=actor,
                        source=source,
                        at=at,
                    )
                )
            else:
                added.append(
                    self._add_company(
                        row,
                        snapshot,
                        via=f"auto-add:{category_id}",
                        actor=actor,
                        source=source,
                        at=at,
                    )
                )

        data = {
            "category_id": category_id,
            "enabled": True,
            "enabled_at": enabled_at or None,
            "last_run_at": at,
            "added": sorted({str(entry.get("company_key")) for entry in added}),
            "enriched": sorted(
                {
                    str(entry.get("company_key"))
                    for entry in enriched
                    if entry.get("outcome") == "enriched"
                }
            ),
        }
        if found:
            self.store.update(found[0]["id"], data, actor=actor, source=source)
        else:
            self.store.create(names.CATEGORIES, data, actor=actor, source=source)

        derived = self.refresh_derived_properties(actor=actor, source=source, at=at)
        return {
            "category_id": category_id,
            "label": requirements["label"],
            "description": requirements["description"],
            "enabled": True,
            "found": True,
            "matched": len(matched),
            "matched_companies": [str(row["company_key"]) for row in matched],
            "added": added,
            "enriched": enriched,
            "held_back": held_back,
            "derived_properties_refreshed": derived,
        }

    # -- writing a company, and tracking one ---------------------------------- #

    def _add_company(
        self,
        row: Mapping[str, Any],
        snapshot: table.Snapshot,
        *,
        via: str,
        actor: str | None,
        source: str,
        at: str,
    ) -> dict[str, Any]:
        """Add one company to the CRM, with ``Record source: Buyer-Intent``.

        Refused outright when the actor does not hold the Data enrichment
        permission, rather than skipped: an automation that quietly did nothing
        because of a missing permission is an automation a seller believes is
        running.
        """
        self._require_enrichment("auto-add", actor)
        key = str(row["company_key"])
        derived = self._derived_for(snapshot, key)
        existing = self.store.find(names.COMPANIES, {"root_domain": key}, limit=1)
        if existing:
            data = existing[0].get("data") or {}
            if data.get("derived_properties") != derived:
                self.store.update(
                    existing[0]["id"],
                    {"derived_properties": derived, "derived_refreshed_at": at},
                    actor=actor,
                    source=source,
                )
            return {
                "company_key": key,
                "outcome": "already_added",
                "company_id": existing[0]["id"],
                "record_source": data.get("record_source"),
                "added_via": data.get("added_via"),
                "derived_properties": derived,
            }
        record = self.store.create(
            names.COMPANIES,
            {
                "root_domain": key,
                # A derived placeholder, not an enrichment: the tracking code
                # matched an address, not a company, so there is no name to store.
                "name": row.get("name") or display_name(key),
                # "When adding a company to your CRM from buyer intent, the company will have a
                # Record source property value of Buyer-Intent."
                "record_source": RECORD_SOURCE_BUYER_INTENT,
                "added_at": at,
                "added_via": via,
                "derived_properties": derived,
                "known_ips": [],
                "crm_plan": CRM_PLAN,
            },
            actor=actor,
            source=source,
        )
        charge = credit_rules.charge(
            self.store, company_key=key, action=credit_rules.ACTION_ADD, at=at, actor=actor, source=source
        )
        return {
            "company_key": key,
            "outcome": "added",
            "company_id": record["id"],
            "record_source": RECORD_SOURCE_BUYER_INTENT,
            "added_via": via,
            "derived_properties": derived,
            "credit": {
                "outcome": charge["outcome"],
                "charged": charge["charged"],
                "waived": charge["waived"],
                "period": charge["period"],
            },
        }

    def _enrich_company(
        self,
        row: Mapping[str, Any],
        snapshot: table.Snapshot,
        *,
        via: str,
        actor: str | None,
        source: str,
        at: str,
    ) -> dict[str, Any]:
        """Refresh an existing company record from what is now known about it.

        The "in-CRM with visitor intent" category matches companies that are
        *already* in the CRM, so creating a second record for one would be the
        wrong action - and the research's own gate is "to add **and enrich**
        companies from buyer intent", which names this half explicitly.

        The properties written are the criteria's own: "that property will
        automatically update to reflect whether an existing company meets or no
        longer meets the SMB Intent criteria". A record whose ``showing_smb_
        intent`` still reads true for a company that stopped qualifying is a
        sentence a seller would read and believe.
        """
        self._require_enrichment("auto-enrich", actor)
        key = str(row["company_key"])
        existing = self.store.find(names.COMPANIES, {"root_domain": key}, limit=1)
        if not existing:
            return {"company_key": key, "outcome": "not_in_crm"}
        derived = self._derived_for(snapshot, key)
        current = existing[0].get("data") or {}
        changes = {
            "derived_properties": derived,
            "intent_last_seen_at": at,
            "last_enriched_via": via,
        }
        if current.get("derived_properties") != derived:
            self.store.update(existing[0]["id"], changes, actor=actor, source=source)
            return {
                "company_key": key,
                "outcome": "enriched",
                "company_id": existing[0]["id"],
                "changed": ["derived_properties"],
                "derived_properties": derived,
            }
        return {
            "company_key": key,
            "outcome": "unchanged",
            "company_id": existing[0]["id"],
            "derived_properties": derived,
        }

    @staticmethod
    def _derived_for(snapshot: table.Snapshot, company_key: str) -> dict[str, bool]:
        """A company's derived intent properties, from its own qualifying visits.

        "if you have SMB Intent as a criterion and a custom property called
        Showing SMB Intent, that property will automatically update to reflect
        whether an existing company meets or no longer meets the SMB Intent
        criteria." A value appears for every criterion that names a property,
        including ``False``: a property that could only be set and never unset
        would be a claim on a CRM record that outlasts its evidence.

        Built from the caller's snapshot rather than a fresh read, because this
        is called once per company inside a run and a fresh read each time would
        make a run quadratic in the size of the table.
        """
        visits = [
            visit
            for visit in snapshot.visits
            if str(visit.get("company_key") or "") == str(company_key)
        ]
        return derived_properties(visits, snapshot.criteria)

    def _track_company(
        self, row: Mapping[str, Any], *, via: str, actor: str | None, source: str, at: str
    ) -> dict[str, Any]:
        """Start tracking a company, and charge for it.

        Tracking is the thing the billing sentence is about, so this is the only
        place that charges ``ACTION_TRACK``.
        """
        self._require_enrichment("auto-track", actor)
        key = str(row["company_key"])
        existing = self.store.find(names.TRACKING, {"company_key": key}, limit=1)
        period = credit_rules.period_for(at)
        if existing:
            return {
                "company_key": key,
                "outcome": "already_tracked",
                "tracking_id": existing[0]["id"],
                "since": (existing[0].get("data") or {}).get("since"),
            }
        record = self.store.create(
            names.TRACKING,
            {
                "company_key": key,
                "since": at,
                "period": period,
                "periods": [period],
                "last_charged_period": period,
                "via": via,
            },
            actor=actor,
            source=source,
        )
        charge = credit_rules.charge(
            self.store, company_key=key, action=credit_rules.ACTION_TRACK, at=at, actor=actor, source=source
        )
        return {
            "company_key": key,
            "outcome": "tracked",
            "tracking_id": record["id"],
            "since": at,
            "via": via,
            "credit": {
                "outcome": charge["outcome"],
                "charged": charge["charged"],
                "waived": charge["waived"],
                "period": charge["period"],
            },
        }

    def refresh_derived_properties(self, *, actor: str | None, source: str, at: str) -> int:
        """Re-derive every criterion's custom property on every company record.

        Run by both automations rather than by a read, because writing a CRM
        record from a GET is a surprise, and a read that writes cannot be served
        from a cache. The number of records changed is reported so a caller can
        tell "nothing qualified" from "nothing was checked".
        """
        snapshot = self._snapshot()
        criteria = self.criteria()
        if not criteria:
            return 0
        visits_by_key: dict[str, list[Mapping[str, Any]]] = {}
        for visit in snapshot.visits:
            key = str(visit.get("company_key") or "")
            if key:
                visits_by_key.setdefault(key, []).append(visit)
        changed = 0
        for key, record in snapshot.companies.items():
            derived = derived_properties(visits_by_key.get(key, []), criteria)
            if derived == (record.get("data") or {}).get("derived_properties"):
                continue
            self.store.update(
                record["id"],
                {"derived_properties": derived, "derived_refreshed_at": at},
                actor=actor,
                source=source,
            )
            changed += 1
        return changed

    def enroll(self, company_key: str, payload: Any, *, actor: str | None, source: str) -> dict[str, Any]:
        """Manual "Enroll in workflow" from the Visitors tab.

        No watermark, because this is a person pointing at a company in front of
        them. It is still permission-gated, because it writes a CRM record on the
        strength of a signal rather than on something the seller typed.
        """
        self._require_enrichment("manual-enrolment", actor)
        key = resolve_domain(company_key).root
        if key in self._exclusion_keys():
            raise DomainExcluded(
                f"{key} is on the exclusions list, so it is not in the table and cannot be "
                "enrolled; remove the exclusion first if that is what you meant"
            )
        if not isinstance(payload, Mapping):
            raise InvalidConfiguration("an enrolment must name the workflow to enrol the company in")
        workflow = str(payload.get("workflow") or "").strip()
        if not workflow:
            raise InvalidConfiguration(
                "an enrolment needs a workflow; the research's control is 'Enroll in workflow' "
                "and enrolling a company in nothing is not a thing it can do"
            )
        for row in self.store.find(names.ENROLMENTS, {"company_key": key, "workflow": workflow}, limit=1):
            return {
                "company_key": key,
                "workflow": workflow,
                "outcome": "already_enrolled",
                "enrolment_id": row["id"],
                "at": row.get("created_at"),
            }
        record = self.store.create(
            names.ENROLMENTS,
            {
                "company_key": key,
                "workflow": workflow,
                "via": "manual",
                "actor": actor,
                "segment": str(payload.get("segment") or "") or None,
            },
            actor=actor,
            source=source,
        )
        return {
            "company_key": key,
            "workflow": workflow,
            "outcome": "enrolled",
            "enrolment_id": record["id"],
            "at": record["created_at"],
        }

    # -- tracking and credits ------------------------------------------------- #

    def tracked(self) -> list[dict[str, Any]]:
        rows = self.store.list(names.TRACKING, limit=names.SCAN_LIMIT, order_by="created_at")
        return [{**(row.get("data") or {}), "id": row["id"], "created_at": row.get("created_at")} for row in rows]

    def renew(self, *, actor: str | None, source: str) -> dict[str, Any]:
        """"After that initial charge, tracking continues to be charged monthly."

        One charge per tracked company per period, and a second call in the same
        period charges nothing - which falls out of the one-row-per-company-per-
        period ledger rather than out of a check here.
        """
        at = self.now()
        period = credit_rules.period_for(at)
        renewed: list[dict[str, Any]] = []
        for row in self.store.list(names.TRACKING, limit=names.SCAN_LIMIT, order_by="created_at"):
            data = row.get("data") or {}
            key = str(data.get("company_key") or "")
            if not key:
                continue
            periods = list(data.get("periods") or [])
            charge = credit_rules.charge(
                self.store, company_key=key, action=credit_rules.ACTION_TRACK, at=at, actor=actor, source=source
            )
            if period not in periods:
                periods.append(period)
                self.store.update(
                    row["id"],
                    {"periods": sorted(periods), "last_charged_period": period, "renewed_at": at},
                    actor=actor,
                    source=source,
                )
            renewed.append(
                {
                    "company_key": key,
                    "period": period,
                    "credit_outcome": charge["outcome"],
                    "charged": charge["charged"],
                    "waived": charge["waived"],
                }
            )
        return {
            "period": period,
            "tracked_companies": len(renewed),
            "renewed": renewed,
            "charged_total": sum(entry["charged"] for entry in renewed),
            "note": (
                "Tracking a company costs 10 credits ... After that initial charge, tracking "
                "continues to be charged monthly."
            ),
        }

    def credits(self, *, actor: str | None = None) -> dict[str, Any]:
        rows = self.store.list(names.CREDITS, limit=names.SCAN_LIMIT, order_by="created_at")
        summary = credit_rules.summarise([row.get("data") or {} for row in rows])
        return {
            "credits_enabled": self.settings()["credits_enabled"],
            "entries": [
                {**(row.get("data") or {}), "id": row["id"], "created_at": row.get("created_at")}
                for row in rows
            ],
            **summary,
        }

    # -- the Overview tab ------------------------------------------------------ #

    def overview(self, *, actor: str | None = None) -> dict[str, Any]:
        """The Overview tab's five researched numbers, and the four categories.

        "Monitor the Overview tab (companies showing research intent, visitor
        intent, converted-to-lifecycle-stage, Added Companies, Popular
        auto-adds)" - the five are counted over the whole table rather than over
        one room's slice of it, because a view is a filter and the Overview is
        not.
        """
        snapshot = self._snapshot()
        rows = [row for row in table.build_rows(snapshot) if not row.get("excluded")]
        categories = self.categories()
        popular = []
        for category in categories:
            popular.append(
                {
                    "id": category["id"],
                    "label": category["label"],
                    "enabled": category["enabled"],
                    "added": len(category.get("added") or []),
                    "enriched": len(category.get("enriched") or []),
                    "companies": len(category.get("added") or [])
                    + len(category.get("enriched") or []),
                }
            )
        popular.sort(key=lambda entry: (-int(entry["companies"]), entry["id"]))
        # Zero across the board is a real state - nothing has been switched on
        # yet - and it is reported as such rather than as an empty list a page
        # would have to guess about.
        return {
            "companies_showing_research_intent": sum(1 for row in rows if row.get("research_intent")),
            "companies_showing_visitor_intent": sum(1 for row in rows if row.get("visitor_intent")),
            "companies_converted_to_lifecycle_stage": sum(
                1 for row in rows if row.get("in_crm") and row.get("lifecycle_stage")
            ),
            "added_companies": sum(1 for row in rows if row.get("in_crm")),
            "popular_auto_adds": popular,
            "stock_categories": categories,
            "tracked_companies": sum(1 for row in rows if row.get("tracked")),
            "news_signals": sum(int(row.get("news_signals") or 0) for row in rows),
            "unattributed_views": table.unattributed(snapshot),
            "excluded_domains": len(snapshot.exclusions),
            "credits": credit_rules.summarise(
                [(row.get("data") or {}) for row in self.store.list(names.CREDITS, limit=names.SCAN_LIMIT)]
            ),
            "capabilities": self.capabilities(actor),
            "tab": "Overview",
        }


def _reason(payload: Any) -> str | None:
    """An optional note on an exclusion, kept verbatim rather than interpreted."""
    if isinstance(payload, Mapping):
        value = str(payload.get("reason") or "").strip()
        return value or None
    return None
