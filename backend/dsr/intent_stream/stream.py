"""The intent stream: save a workflow, record a visit, POST the payload.

This is the researched flow in execution order, and the module docstring of
:mod:`dsr.intent_stream` maps it to the routes. What matters here is the order of
operations, because most of the interesting behaviour is about what happens at
the edges:

* **A destination is validated before anything is written.** A URL with no host
  leaves no workflow behind, because the research's step 3 is the URL and a
  workflow with no URL cannot do the one thing it is for.
* **A visit is written before any workflow is evaluated.** A visit that matched
  nothing is the state an operator needs in order to debug a Segment.
* **Every workflow evaluated produces a row.** Matched-and-sent,
  matched-and-failed, already-sent, paused, or not-matched: all of them. A rule
  that does not fall through is a bug somebody hits in production, and the
  cheapest way to not have one is to make every branch write something.
* **Only a *successful* POST marks a lead as sent.** See
  ``a-failed-delivery-does-not-consume-a-once-only-lead``.

The clock is injected, so a test can assert on an update count, a ``sentAt``, and
a preview without waiting for anything, and the demo seeder can date its rows.
The transport is injected for the same reason the event-stream feature injects
one: the suite must never open a socket.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from dsr.intent_stream import (
    payloads as payload_module,
    segments as segment_module,
    tokens as token_module,
)
from dsr.intent_stream.delivery import (
    Attempt,
    Transport,
    UrllibTransport,
    classify,
    encode,
    is_retryable,
)
from dsr.intent_stream.errors import (
    DeliveryError,
    IntentStreamError,
    LeadError,
    SegmentError,
    TargetError,
    WorkflowError,
)
from dsr.intent_stream.registry import (
    Books,
    lead_state_id,
    summarise_contact,
    summarise_delivery,
    summarise_lead,
    summarise_segment,
    summarise_visit,
    summarise_workflow,
)
from dsr.intent_stream.vocabulary import (
    ALL_COLLECTIONS,
    DEFAULT_SEGMENT_MATCH,
    DEFAULT_SEND_MODE,
    DEFAULT_TIMEOUT_SECONDS,
    DELIVERY_STATES,
    DESTINATION_RECIPES,
    EVIDENCE,
    FLOW,
    KEYWORD_FIELDS,
    SKIP_ALREADY_SENT,
    SKIP_INACTIVE,
    SKIP_NOT_MATCHED,
    SKIP_REASONS,
    STATE_DELIVERED,
    STATE_FAILED,
    STATE_SKIPPED,
    TOKEN_BODY_FIELD,
    TOKEN_HEADER,
    WORKFLOW_TYPE,
)
from dsr.store import RecordStore

#: Bound on what a single visit may carry. A visit with a thousand pages in it is
#: a payload, not a visit.
MAX_PAGES_PER_VISIT = 50
MAX_CONTACTS_PER_LEAD = 500
MAX_LEADS = 5000
MAX_SEGMENTS = 500
MAX_WORKFLOWS = 500
MAX_SEGMENTS_PER_WORKFLOW = 50
MIN_URL_HOST_LENGTH = 4


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def validate_target_url(url: Any) -> str:
    """The destination URL, or a refusal naming what is wrong with it.

    Sourced: "Add a name for your Workflow and the URL you want to send data to",
    and "you'll need a destination that can handle receiving the data object being
    sent via the webhooks".

    Accepts ``http`` and ``https`` only. A ``file:``, ``ftp:`` or
    ``javascript:`` URL is not a destination that can handle an HTTP POST, and
    accepting one would be a way to make the app read or write somewhere it
    should not. ``http`` is accepted rather than refused, and the warning is
    returned to the caller - see ``http-scheme-allowed-and-warned``.
    """
    text = _text(url)
    if not text:
        raise TargetError(
            "a destination URL is required",
            remediation="Step 3 of the researched flow: add the URL you want to send data to.",
        )
    lowered = text.casefold()
    scheme, separator, remainder = lowered.partition("://")
    if not separator:
        raise TargetError(
            f"{text!r} is not a URL with a scheme",
            remediation="Use an absolute http:// or https:// URL, e.g. https://hooks.example/leads.",
        )
    if scheme not in ("http", "https"):
        raise TargetError(
            f"scheme {scheme!r} cannot receive an HTTP POST",
            remediation="Use http:// or https:// - the destination is reached by POST.",
        )
    if any(character in text for character in (" ", "\t", "\n", "\r")):
        raise TargetError(
            "a destination URL may not contain whitespace",
            remediation="Percent-encode the space, or fix the URL.",
        )
    host = remainder.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    # Strip credentials and port: they are not part of the host name, and a URL
    # of "https://:pass@" would otherwise pass an emptiness check.
    host = host.rpartition("@")[2].split(":", 1)[0]
    if len(host) < MIN_URL_HOST_LENGTH:
        raise TargetError(
            f"{text!r} has no usable host",
            remediation="Use an absolute URL such as https://hooks.example/leads.",
        )
    if ".." in host or host.startswith(".") or host.endswith("."):
        raise TargetError(
            f"{text!r} has a malformed host",
            remediation="Check the host name for a stray or doubled dot.",
        )
    return text


def url_warnings(url: str) -> list[str]:
    """What the operator should know about this URL before saving it."""
    warnings: list[str] = []
    if url.casefold().startswith("http://"):
        warnings.append(
            f"This destination is plain http, so the {TOKEN_HEADER} token crosses the wire in "
            "the clear and cannot prove the traffic came from this platform. Use https:// "
            "unless the receiver is on localhost."
        )
    return warnings


class IntentStream:
    """The workflow's behaviour, over one store."""

    def __init__(
        self,
        store: RecordStore,
        *,
        transport: Transport | None = None,
        now: datetime | None = None,
    ) -> None:
        self.store = store
        self.books = Books(store)
        self.transport: Transport = transport or UrllibTransport()
        self._now = now

    def moment(self) -> str:
        """The current time, in the envelope's format."""
        if self._now is None:
            return _utcnow()
        return self._now.astimezone(timezone.utc).isoformat(timespec="milliseconds")

    # ----------------------------------------------------------------------- #
    # Vocabulary, evidence, and the reference material
    # ----------------------------------------------------------------------- #

    def destinations(self) -> dict[str, Any]:
        """The researched destinations, split into recipes and other surfaces.

        The distinction is the useful one for somebody pointing a workflow at a
        URL: a *recipe* is a consumer you paste a URL into (the research names
        Microsoft Teams and Google Sheets), while a *surface* is a vendor listed
        in the Integrations & Connectors collection with no recipe read.
        """
        return {
            "count": len(DESTINATION_RECIPES),
            "recipes": [d for d in DESTINATION_RECIPES if d["kind"] == "webhook_recipe"],
            "surfaces": [d for d in DESTINATION_RECIPES if d["kind"] != "webhook_recipe"],
            "note": (
                "The research names Microsoft Teams and Google Sheets as webhook workflows to "
                "combine with, and lists the wider integration surfaces it did not read a "
                "recipe for."
            ),
        }

    def explain(self) -> dict[str, Any]:
        """The researched explainer, the flow, and the evidence behind both.

        Served rather than compiled into the page so that a claim on screen can be
        traced to the sentence it came from. Every entry carries the source id.
        """
        return {
            "whatAreWebhooks": (
                "A webhook posts a data object to a URL you own, every time something you "
                "care about happens. Nothing polls: the trigger is the company visit, and this "
                "product POSTs to your destination when a company matches a Segment you saved."
            ),
            "flow": [dict(step) for step in FLOW],
            "evidence": [dict(item) for item in EVIDENCE],
            "sources": sorted({item["source"] for item in EVIDENCE}),
            "dataFlow": [
                "A company visit matches a saved Segment",
                "The workflow's conditions are evaluated against the company lead",
                "A JSON payload is built: Company, or Company + filtered Contacts",
                "The payload is POSTed to your URL with the generated token",
                "Your system enriches, scores, or routes the company",
                "With 'send updates as well', the same company is re-sent with refreshed "
                "activity data on subsequent visits",
            ],
            "notDocumented": [
                "No public inbound REST reference was reachable, so this workflow has no "
                "inbound surface.",
                "No timeout, retry count, or backoff schedule appears in the sources, so none "
                "is implemented. See the no-retry-ladder-and-an-inferred-timeout inference.",
            ],
        }

    # ----------------------------------------------------------------------- #
    # Saved Segments
    # ----------------------------------------------------------------------- #

    def create_segment(
        self, payload: Mapping[str, Any], *, source: str, actor: str | None = None
    ) -> dict[str, Any]:
        name = _text(payload.get("name"))
        if not name:
            raise SegmentError(
                "a Segment needs a name",
                remediation="Name it after the group it selects, e.g. 'Enterprise software, 1000+'.",
            )
        rules = segment_module.require_rules(payload.get("rules"))
        match = segment_module.require_match(
            payload.get("match"), default=segment_module.DEFAULT_RULE_MATCH
        )
        existing = self.books.segments.find({"name": name}, limit=1)
        if existing:
            raise SegmentError(
                f"a Segment named {name!r} already exists",
                remediation="Segment names are how a workflow refers to one. Pick another name.",
                status=409,
            )
        if self._at_cap(self.books.segments.collection, MAX_SEGMENTS):
            raise SegmentError(f"this account already holds {MAX_SEGMENTS} Segments")
        record = self.books.segments.create(
            {
                "name": name,
                "description": _text(payload.get("description")),
                "match": match,
                "rules": rules,
                "summary": segment_module.describe_segment(
                    {"name": name, "match": match, "rules": rules}
                ),
            },
            source=source,
            actor=actor,
        )
        return {"segment": summarise_segment(record), "created": True}

    def list_segments(self) -> list[dict[str, Any]]:
        records = self.books.segments.list(limit=MAX_SEGMENTS)
        return [summarise_segment(record) for record in records]

    def read_segment(self, segment_id: str) -> dict[str, Any]:
        record = self._require(self.books.segments, segment_id, "Segment")
        payload = summarise_segment(record)
        payload["usedBy"] = self.workflows_using_segment(segment_id)
        return payload

    def update_segment(
        self, segment_id: str, payload: Mapping[str, Any], *, source: str, actor: str | None = None
    ) -> dict[str, Any]:
        record = self._require(self.books.segments, segment_id, "Segment")
        data = dict(record["data"])
        patch: dict[str, Any] = {}
        if "name" in payload:
            name = _text(payload.get("name"))
            if not name:
                raise SegmentError("a Segment needs a name")
            clash = [
                other
                for other in self.books.segments.find({"name": name}, limit=5)
                if other["id"] != record["id"]
            ]
            if clash:
                raise SegmentError(f"a Segment named {name!r} already exists", status=409)
            patch["name"] = name
        if "description" in payload:
            patch["description"] = _text(payload.get("description"))
        if "rules" in payload:
            rules = segment_module.require_rules(payload.get("rules"))
            patch["rules"] = rules
        if "match" in payload:
            patch["match"] = segment_module.require_match(
                payload.get("match"), default=segment_module.DEFAULT_RULE_MATCH
            )
        if not patch:
            raise SegmentError(
                "nothing to change",
                remediation="Send at least one of name, description, rules, match.",
            )
        merged = {**data, **patch}
        patch["summary"] = segment_module.describe_segment(merged)
        updated = self.books.segments.update(record["id"], patch, source=source, actor=actor)
        return {"segment": summarise_segment(updated), "updated": True}

    def delete_segment(
        self, segment_id: str, *, source: str, actor: str | None = None
    ) -> dict[str, Any]:
        record = self._require(self.books.segments, segment_id, "Segment")
        used_by = self.workflows_using_segment(segment_id)
        if used_by:
            # A cascade here would leave a workflow that looks healthy and never
            # fires. Refusing is the difference between a five-minute fix and an
            # afternoon of wondering why the Segment went quiet.
            raise DeliveryError(
                f"Segment {segment_id} is still used by {len(used_by)} workflow(s)",
                remediation=(
                    "Remove it from those workflows' conditions first, or change the workflows "
                    f"that name it: {', '.join(used_by)}."
                ),
            )
        self.books.segments.delete(record["id"], source=source, actor=actor)
        return {"id": record["id"], "deleted": True, "collection": self.books.segments.collection}

    def workflows_using_segment(self, segment_id: str) -> list[str]:
        """Names of the workflows whose conditions name this Segment."""
        hits: list[str] = []
        for record in self.books.workflows.list(limit=MAX_WORKFLOWS):
            ids = ((record["data"].get("conditions") or {}).get("segmentIds")) or []
            if segment_id in ids:
                hits.append(_text(record["data"].get("name")) or record["id"])
        return hits

    def evaluate_segment_against(
        self,
        segment_id: str,
        *,
        lead_id: str | None = None,
        company: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run one Segment against one lead, and show every rule's verdict.

        A read with no side effect, which is the point: the researcher can find out
        why a Segment did or did not match without creating a visit, and therefore
        without sending anything.
        """
        record = self._require(self.books.segments, segment_id, "Segment")
        if lead_id is not None and company is not None:
            raise SegmentError(
                "send either a leadId or inline company data, not both",
                remediation="Pass leadId to test a saved lead, or company to test a shape.",
            )
        if lead_id is not None:
            lead = self._require(self.books.leads, lead_id, "company lead")
            payload = dict(lead["data"])
        else:
            payload = dict(company or {})
        verdict = segment_module.evaluate_segment(record["data"], payload)
        verdict["segment"] = summarise_segment(record)
        return verdict

    # ----------------------------------------------------------------------- #
    # Company leads and their contacts
    # ----------------------------------------------------------------------- #

    def create_lead(
        self,
        payload: Mapping[str, Any],
        *,
        room_id: str | None = None,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        name = _text(payload.get("name"))
        if not name:
            raise LeadError(
                "a company lead needs a company name",
                remediation="The payload is built for a company lead; name the company.",
            )
        domain = _text(payload.get("domain"))
        company_id = _text(payload.get("companyId")) or f"co_{name.casefold().replace(' ', '-')}"
        if self.books.leads.find({"companyId": company_id}, limit=1):
            raise LeadError(
                f"company {company_id} is already identified in this account",
                remediation="A company lead is one row per identified company. PATCH the existing one.",
                status=409,
            )
        if self._at_cap(self.books.leads.collection, MAX_LEADS):
            raise LeadError(f"this account already holds {MAX_LEADS} company leads")
        at = self.moment()
        data = {
            key: value
            for key, value in payload.items()
            if key
            not in (
                "id",
                "collection",
                "room_id",
                "revision",
                "created_at",
                "updated_at",
                "deleted_at",
            )
        }
        data.update(
            {
                "companyId": company_id,
                "name": name,
                "domain": domain,
                "identifiedAt": at,
                "visitCount": int(payload.get("visitCount") or 0),
                "pagesViewed": _pages(payload.get("pagesViewed")),
                "lastVisitAt": _text(payload.get("lastVisitAt")) or None,
            }
        )
        record = self.books.leads.create(data, room_id=room_id, source=source, actor=actor)
        return {"lead": summarise_lead(record), "created": True}

    def list_leads(
        self,
        *,
        room_id: str | None = None,
        limit: int = 100,
        where: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        records = self._lead_records(room_id=room_id, limit=limit, where=where)
        return [summarise_lead(record) for record in records]

    def read_lead(self, lead_id: str) -> dict[str, Any]:
        record = self._require(self.books.leads, lead_id, "company lead")
        payload = summarise_lead(record)
        payload["data"] = record["data"]
        payload["contacts"] = [summarise_contact(row) for row in self._contact_records(lead_id)]
        payload["recentVisits"] = [
            summarise_visit(row) for row in self.books.visits.find({"leadId": lead_id}, limit=10)
        ]
        payload["workflows"] = self._workflow_state_for_lead(lead_id)
        return payload

    def update_lead(
        self,
        lead_id: str,
        payload: Mapping[str, Any],
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        record = self._require(self.books.leads, lead_id, "company lead")
        if "companyId" in payload:
            raise LeadError(
                "companyId is this product's identity for the company and does not change",
                remediation="Send the other fields; a second row would be a second company.",
                status=409,
            )
        patch = {
            key: value
            for key, value in payload.items()
            if key
            not in (
                "id",
                "collection",
                "room_id",
                "revision",
                "created_at",
                "updated_at",
                "deleted_at",
            )
        }
        if not patch:
            raise LeadError(
                "nothing to change", remediation="Send at least one field of the lead's data."
            )
        updated = self.books.leads.update(record["id"], patch, source=source, actor=actor)
        return {"lead": summarise_lead(updated), "updated": True}

    def list_contacts(self, lead_id: str, *, limit: int = 500) -> list[dict[str, Any]]:
        self._require(self.books.leads, lead_id, "company lead")
        return [summarise_contact(row) for row in self._contact_records(lead_id, limit=limit)]

    def create_contact(
        self,
        lead_id: str,
        payload: Mapping[str, Any],
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        lead = self._require(self.books.leads, lead_id, "company lead")
        name = _text(payload.get("name"))
        if not name:
            raise LeadError(
                "a contact needs a name",
                remediation="The payload sends 'contacts employed at the company', so name them.",
            )
        if (
            len(self._contact_records(lead_id, limit=MAX_CONTACTS_PER_LEAD))
            >= MAX_CONTACTS_PER_LEAD
        ):
            raise LeadError(f"this company already holds {MAX_CONTACTS_PER_LEAD} contacts")
        data = {
            key: value
            for key, value in payload.items()
            if key
            not in (
                "id",
                "collection",
                "room_id",
                "revision",
                "created_at",
                "updated_at",
                "deleted_at",
            )
        }
        data["leadId"] = lead["id"]
        data["companyId"] = lead["data"].get("companyId")
        data["name"] = name
        record = self.books.contacts.create(
            data, room_id=lead["room_id"], source=source, actor=actor
        )
        return {"contact": summarise_contact(record), "created": True}

    def _live_count(self, collection: str) -> int:
        """How many live records a collection holds.

        Read from the store's own discovery endpoint rather than from a
        ``list(limit=1)``, which returns at most one row and so cannot answer
        "is this collection at its cap?" at all. A cap check that can never fire
        is worse than no cap: it reads as a guard and protects nothing.
        """
        for row in self.store.collections():
            if row["collection"] == collection:
                return int(row.get("live") or 0)
        return 0

    def _at_cap(self, collection: str, cap: int) -> bool:
        return self._live_count(collection) >= cap

    def _contact_records(self, lead_id: str, *, limit: int = 500) -> list[dict[str, Any]]:
        return sorted(
            self.books.contacts.find({"leadId": lead_id}, limit=limit),
            key=lambda row: _text(row["data"].get("name")).casefold(),
        )

    def _lead_records(
        self,
        *,
        room_id: str | None = None,
        limit: int = 100,
        where: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if where:
            found = self.books.leads.find(where, limit=1000)
            if room_id is not None:
                found = [row for row in found if row["room_id"] == room_id]
        elif room_id is not None:
            found = self.books.leads.list(room_id=room_id, limit=limit)
        else:
            found = self.books.leads.list(limit=limit)
        return found[:limit]

    # ----------------------------------------------------------------------- #
    # The trigger: a company visit
    # ----------------------------------------------------------------------- #

    def record_visit(
        self,
        payload: Mapping[str, Any],
        *,
        room_id: str | None = None,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """The researched automation, end to end.

        "A company visit matches a saved Segment -> the workflow's conditions are
        evaluated -> a JSON payload is built -> it is POSTed to your URL."

        Order: write the visit, refresh the lead's activity data, evaluate every
        active workflow, and record one delivery row per workflow per outcome. The
        lead is refreshed *before* the payload is built, because the researched
        update mode promises "the same lead with updated activity data" - a
        payload built from the pre-visit state would not be that.
        """
        lead_id = _text(payload.get("leadId") or payload.get("lead_id"))
        if not lead_id:
            raise LeadError(
                "a visit needs the identified company it belongs to",
                remediation="Send leadId. Identification is another workflow's job; this one streams the result.",
            )
        lead = self._require(self.books.leads, lead_id, "company lead")
        room = room_id or lead["room_id"] or _text(payload.get("roomId"))
        at = _text(payload.get("at")) or self.moment()
        pages = _pages(payload.get("pagesViewed") or payload.get("pages"))

        # The lead's activity data is refreshed first, so every payload built in
        # this visit carries the visit that caused it.
        previous_visit_count = int(lead["data"].get("visitCount") or 0)
        seen_pages = list(lead["data"].get("pagesViewed") or [])
        for page in pages:
            if page not in seen_pages:
                seen_pages.append(page)
        activity = {
            "visitCount": previous_visit_count + 1,
            "lastVisitAt": at,
            "pagesViewed": seen_pages[:MAX_PAGES_PER_VISIT],
            "lastSecondsOnPage": payload.get("secondsOnPage"),
            "lastCountry": _text(payload.get("country")) or lead["data"].get("lastCountry"),
            "lastDevice": _text(payload.get("device")) or lead["data"].get("lastDevice"),
        }
        self.books.leads.update(
            lead["id"],
            activity,
            source=source,
            actor=actor,
        )
        lead = self._require(self.books.leads, lead_id, "company lead")
        lead_data = dict(lead["data"])

        visit = self.books.visits.create(
            {
                "leadId": lead["id"],
                "at": at,
                "pagesViewed": pages,
                "secondsOnPage": payload.get("secondsOnPage"),
                "country": _text(payload.get("country")),
                "device": _text(payload.get("device")),
                "referrer": _text(payload.get("referrer")),
                "visitNumber": previous_visit_count + 1,
                "matchedWorkflows": 0,
            },
            room_id=room,
            source=source,
            actor=actor,
        )

        contacts = [row["data"] for row in self._contact_records(lead["id"])]
        workflows = self.books.workflows.list(limit=MAX_WORKFLOWS)
        outcomes: list[dict[str, Any]] = []
        for record in workflows:
            outcomes.append(
                self._apply_workflow(
                    record,
                    lead=lead,
                    lead_data=lead_data,
                    contacts=contacts,
                    visit=visit,
                    source=source,
                    actor=actor,
                )
            )
        matched = sum(
            1 for outcome in outcomes if outcome["state"] in (STATE_DELIVERED, STATE_FAILED)
        )
        self.books.visits.update(
            visit["id"],
            {
                "matchedWorkflows": matched,
                "outcomes": [
                    {
                        "workflowId": o["workflowId"],
                        "state": o["state"],
                        "skipReason": o["skipReason"],
                    }
                    for o in outcomes
                ],
            },
            source=source,
            actor=actor,
        )
        return {
            "visit": summarise_visit(self._require(self.books.visits, visit["id"], "visit")),
            "company": summarise_lead(self._require(self.books.leads, lead["id"], "company lead")),
            "deliveries": outcomes,
            "matchedWorkflows": matched,
            "skipped": sum(1 for o in outcomes if o["state"] == STATE_SKIPPED),
        }

    def list_visits(
        self,
        *,
        room_id: str | None = None,
        lead_id: str | None = None,
        matched: bool | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        where: dict[str, Any] = {}
        if lead_id:
            where["leadId"] = lead_id
        if where:
            records = self.books.visits.find(where, limit=1000)
        else:
            records = self.books.visits.list(limit=limit)
        if room_id is not None:
            records = [row for row in records if row["room_id"] == room_id]
        if matched is not None:
            records = [
                row for row in records if bool(row["data"].get("matchedWorkflows")) is matched
            ]
        return [summarise_visit(row) for row in records[:limit]]

    def read_visit(self, visit_id: str) -> dict[str, Any]:
        record = self._require(self.books.visits, visit_id, "visit")
        payload = summarise_visit(record)
        payload["outcomes"] = record["data"].get("outcomes") or []
        return payload

    def _apply_workflow(
        self,
        record: Mapping[str, Any],
        *,
        lead: Mapping[str, Any],
        lead_data: Mapping[str, Any],
        contacts: Sequence[Mapping[str, Any]],
        visit: Mapping[str, Any],
        source: str,
        actor: str | None,
    ) -> dict[str, Any]:
        """Evaluate one workflow against this visit and record what it decided.

        Every branch ends in a written row. The skip reasons are the researched
        rules refusing, and each one is the answer an operator would otherwise have
        to guess at.
        """
        data = dict(record["data"])
        workflow_id = record["id"]
        conditions = dict(data.get("conditions") or {})
        contact_filter = data.get("contactFilter") or {"keywords": [], "requiredFields": []}
        room = visit["room_id"]
        conditions_room = conditions.get("roomId")

        # 1. Paused. Checked first, and recorded, so "nothing arrived" has an
        #    answer that is not silence.
        if not data.get("active", True):
            return self._record_skip(
                record,
                lead,
                visit,
                SKIP_INACTIVE,
                f"workflow {data.get('name')!r} is paused",
                source=source,
                actor=actor,
                room_id=room,
            )

        # 2. Room scope, when the workflow narrowed its conditions to one room.
        if conditions_room and room and conditions_room != room:
            return self._record_skip(
                record,
                lead,
                visit,
                SKIP_NOT_MATCHED,
                f"this workflow's conditions are scoped to room {conditions_room}, and the visit is in {room}",
                source=source,
                actor=actor,
                room_id=room,
            )

        # 3. The researched conditions: does the company match a saved Segment?
        wanted = list(conditions.get("segmentIds") or [])
        segments = [self._segment_data(sid) for sid in wanted]
        missing = [sid for sid, data in zip(wanted, segments, strict=True) if data is None]
        if missing:
            # A Segment that was hard-deleted out from under a workflow. Refusing
            # loudly beats sending a payload nobody can explain.
            raise WorkflowError(
                f"workflow {data.get('name')!r} names Segment(s) that no longer exist: {', '.join(missing)}",
                code="segment_missing",
                remediation="Recreate the Segment, or change the workflow's conditions.",
                status=409,
            )
        verdict = segment_module.evaluate_conditions(
            [s for s in segments if s is not None],
            lead_data,
            match=str(conditions.get("match") or DEFAULT_SEGMENT_MATCH),
        )
        if not verdict["matched"]:
            return self._record_skip(
                record,
                lead,
                visit,
                SKIP_NOT_MATCHED,
                verdict["reason"],
                conditions=verdict,
                source=source,
                actor=actor,
                room_id=room,
            )

        # 4. The researched once-versus-updates choice. Only a *successful* POST
        #    counts as sent, so a lead whose only delivery failed is retried on
        #    the next visit rather than being lost.
        state_record = self.books.states.get(lead_state_id(workflow_id, lead["id"]))
        send_count = int((state_record or {}).get("data", {}).get("sendCount") or 0)
        if str(data.get("sendMode") or DEFAULT_SEND_MODE) != "updates" and send_count > 0:
            return self._record_skip(
                record,
                lead,
                visit,
                SKIP_ALREADY_SENT,
                (
                    f"this workflow sends a lead only once, and it was sent on "
                    f"{state_record['data'].get('lastSentAt')}"
                ),
                conditions=verdict,
                source=source,
                actor=actor,
                room_id=room,
            )

        # 5. Build the researched payload and POST it.
        body = payload_module.build_payload(
            company=lead_data,
            lead_id=lead["id"],
            room_id=lead["room_id"],
            contacts=contacts,
            workflow={"id": workflow_id, **data},
            conditions=verdict,
            send_count=send_count,
            token=_text(data.get("token")) or None,
            sent_at=self.moment(),
            contact_filter=contact_filter,
        )
        result = self.transport.post(
            _text(data.get("url")),
            encode(body),
            token_module.headers_for(_text(data.get("token"))),
            _timeout(data),
        )
        state = classify(result)
        attempt = Attempt(
            number=1,
            ok=result.ok,
            status=result.status,
            error=result.error,
            duration_ms=result.duration_ms,
            retryable=is_retryable(result),
            at=self.moment(),
            final_url=result.final_url,
            body_excerpt=result.body[:512],
        )
        # Captured before the increment: this delivery is an update if and only
        # if the lead had already been sent to this workflow.
        was_update = send_count > 0
        if state == STATE_DELIVERED:
            send_count += 1
        delivery = self.books.deliveries.create(
            {
                "workflowId": workflow_id,
                "workflowName": data.get("name"),
                "leadId": lead["id"],
                "companyId": lead_data.get("companyId"),
                "visitId": visit["id"],
                "state": state,
                "skipReason": None,
                "status": result.status,
                "error": result.error,
                "responseExcerpt": result.body[:512],
                "durationMs": result.duration_ms,
                "retryable": attempt.retryable,
                "url": data.get("url"),
                "finalUrl": result.final_url,
                "tokenHeader": TOKEN_HEADER,
                "sendMode": data.get("sendMode"),
                "payload": data.get("payload"),
                "isUpdate": was_update,
                "updateCount": send_count,
                "conditions": verdict,
                "contactFilter": payload_module.contact_filter_summary(contact_filter),
                "contactsConsidered": body.get("contactsConsidered"),
                "contactsIncluded": body.get("contactsIncluded"),
                "payloadBytes": len(encode(body)),
                # The body as sent, minus the token. "What exactly did you send?"
                # is the first question anybody asks about a webhook, and a
                # delivery row that cannot answer it is a weak row. The token is
                # left out on purpose: it is already on the workflow row, and
                # duplicating it here would put a live secret into the audit log
                # for every delivery as well.
                "payloadBody": _without_token(body),
                "attemptLog": [_attempt(attempt)],
                "at": self.moment(),
            },
            room_id=room,
            source=source,
            actor=actor,
        )
        if state == STATE_DELIVERED:
            self._advance_state(
                workflow_id, lead, visit, delivery, send_count, source=source, actor=actor
            )
        return {
            **summarise_delivery(delivery),
            "payload": body,
            "conditions": verdict,
            "response": {"status": result.status, "error": result.error, "body": result.body[:512]},
        }

    def _record_skip(
        self,
        record: Mapping[str, Any],
        lead: Mapping[str, Any],
        visit: Mapping[str, Any],
        reason: str,
        detail: str,
        *,
        conditions: Mapping[str, Any] | None = None,
        source: str,
        actor: str | None,
        room_id: str | None,
    ) -> dict[str, Any]:
        """One decision that resulted in no POST, written down anyway."""
        delivery = self.books.deliveries.create(
            {
                "workflowId": record["id"],
                "workflowName": (record["data"] or {}).get("name"),
                "leadId": lead["id"],
                "companyId": (lead["data"] or {}).get("companyId"),
                "visitId": visit["id"],
                "state": STATE_SKIPPED,
                "skipReason": reason,
                "detail": detail,
                "conditions": dict(conditions or {}),
                "url": (record["data"] or {}).get("url"),
                "attemptLog": [],
                "at": self.moment(),
            },
            room_id=room_id,
            source=source,
            actor=actor,
        )
        return {**summarise_delivery(delivery), "detail": detail}

    def _advance_state(
        self,
        workflow_id: str,
        lead: Mapping[str, Any],
        visit: Mapping[str, Any],
        delivery: Mapping[str, Any],
        send_count: int,
        *,
        source: str,
        actor: str | None,
    ) -> None:
        """Record that this (workflow, lead) pair has now been sent ``send_count`` times."""
        state_id = lead_state_id(workflow_id, lead["id"])
        existing = self.books.states.get(state_id)
        patch = {
            "workflowId": workflow_id,
            "leadId": lead["id"],
            "companyId": (lead["data"] or {}).get("companyId"),
            "sendCount": send_count,
            "lastSentAt": delivery["data"].get("at"),
            "lastVisitId": visit["id"],
            "lastDeliveryId": delivery["id"],
            "lastUpdateCount": send_count,
        }
        if existing is None:
            patch["firstSentAt"] = delivery["data"].get("at")
            self.books.states.create(
                patch, record_id=state_id, room_id=lead["room_id"], source=source, actor=actor
            )
            return
        merged = {**(existing.get("data") or {}), **patch}
        merged["firstSentAt"] = (existing.get("data") or {}).get("firstSentAt") or delivery[
            "data"
        ].get("at")
        self.books.states.update(state_id, merged, source=source, actor=actor)

    def _workflow_state_for_lead(self, lead_id: str) -> list[dict[str, Any]]:
        """Which workflows have sent this lead, and how often."""
        rows: list[dict[str, Any]] = []
        for record in self.books.states.find({"leadId": lead_id}, limit=MAX_WORKFLOWS):
            data = record["data"]
            workflow = self.books.workflows.get(str(data.get("workflowId")))
            rows.append(
                {
                    "workflowId": data.get("workflowId"),
                    "workflowName": (workflow or {}).get("data", {}).get("name"),
                    "sendMode": (workflow or {}).get("data", {}).get("sendMode"),
                    "sendCount": data.get("sendCount"),
                    "firstSentAt": data.get("firstSentAt"),
                    "lastSentAt": data.get("lastSentAt"),
                    "lastDeliveryId": data.get("lastDeliveryId"),
                }
            )
        return rows

    # ----------------------------------------------------------------------- #
    # Webhook workflows
    # ----------------------------------------------------------------------- #

    def create_workflow(
        self,
        payload: Mapping[str, Any],
        *,
        room_id: str | None = None,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Steps 3 to 6 of the researched flow, in the order it describes them.

        The token is generated here and nowhere else, because the research says
        "automatically generated" - a caller that supplies one is refused rather
        than ignored, since a silently ignored field is how somebody ends up
        believing their own token is in force.
        """
        name = _text(payload.get("name"))
        if not name:
            raise WorkflowError(
                "a workflow needs a name",
                remediation="Step 3: 'Add a name for your Workflow and the URL you want to send data to.'",
            )
        url = validate_target_url(payload.get("url") or payload.get("destinationUrl"))
        if "token" in payload and payload.get("token"):
            raise WorkflowError(
                "the token is generated by this product and cannot be supplied",
                remediation=(
                    "The research calls it an 'automatically generated token'. Remove the field; "
                    "it is returned in this response and behind POST /workflows/{id}/token."
                ),
            )
        send_mode = str(_text(payload.get("sendMode")) or DEFAULT_SEND_MODE).casefold()
        if send_mode not in ("once", "updates"):
            raise WorkflowError(
                f"sendMode must be 'once' or 'updates', not {payload.get('sendMode')!r}",
                remediation=(
                    "The research offers 'only send a lead once' or 'send updates as well'. "
                    "With 'updates', the same lead is re-sent with refreshed activity data on "
                    "a later visit."
                ),
            )
        payload_mode = payload_module.require_payload_mode(payload.get("payload"))
        contact_filter = payload_module.require_contact_filter(payload.get("contactFilter"))
        conditions = self._require_conditions(payload.get("conditions"), room_id=room_id)
        if self._at_cap(self.books.workflows.collection, MAX_WORKFLOWS):
            raise WorkflowError(f"this account already holds {MAX_WORKFLOWS} workflows")

        token = token_module.generate()
        record = self.books.workflows.create(
            {
                "name": name,
                "type": WORKFLOW_TYPE,
                "url": url,
                "active": bool(payload.get("active", True)),
                "sendMode": send_mode,
                "payload": payload_mode,
                "conditions": conditions,
                "contactFilter": contact_filter,
                "token": token,
                "tokenHeader": TOKEN_HEADER,
                "timeoutSeconds": payload.get("timeoutSeconds"),
                "description": _text(payload.get("description")),
                "createdFrom": "WF-032 researched flow",
            },
            room_id=room_id,
            source=source,
            actor=actor,
        )
        return {
            "workflow": self._masked_workflow(record),
            "created": True,
            # The only place the token is ever returned in full, besides the
            # reveal route. "you have a token to use in your service or tool."
            "token": token,
            "tokenHeader": TOKEN_HEADER,
            "tokenNote": (
                "Copy this into the destination now. It is masked in every list and detail "
                "response from here on; POST /workflows/{workflow_id}/token shows it again."
            ),
            "warnings": url_warnings(url),
        }

    def _require_conditions(self, raw: Any, *, room_id: str | None) -> dict[str, Any]:
        """Validate the researched conditions: a non-empty selection of Segments.

        ``room_id`` is accepted and ignored, deliberately. It used to default the
        conditions' room scope, which meant a workflow created from a room-scoped
        page silently stopped matching visits in every other room - a surprise in
        the one field an operator would not think to check. The record's own
        ``room_id`` groups it; only an explicit ``conditions.roomId`` narrows what
        it sends.
        """
        if raw is None or raw == "":
            raise WorkflowError(
                "a workflow needs conditions",
                remediation=(
                    "Step 4: choose which leads to send, based on saved Segments. Pass "
                    'conditions: {"segmentIds": ["<segment id>"]}.'
                ),
            )
        if isinstance(raw, (list, tuple)):
            raw = {"segmentIds": list(raw)}
        if not isinstance(raw, Mapping):
            raise WorkflowError(
                f"conditions must be an object, not {type(raw).__name__}",
                remediation='Pass {"segmentIds": [...], "match": "any"|"all"}.',
            )
        ids = raw.get("segmentIds")
        if ids is None:
            ids = raw.get("segments") or []
        if isinstance(ids, str):
            ids = [part for part in ids.split(",") if part.strip()]
        if not isinstance(ids, (list, tuple)):
            raise WorkflowError(
                "conditions.segmentIds must be a list of Segment ids",
                remediation="Create the Segment first, then name it here.",
            )
        cleaned = [str(item).strip() for item in ids if str(item).strip()]
        if not cleaned:
            raise WorkflowError(
                "a workflow must name at least one saved Segment",
                remediation=(
                    "Step 4 makes conditions a step, not an option. A workflow with no Segment "
                    "would send every company in the account to a production endpoint."
                ),
            )
        if len(cleaned) > MAX_SEGMENTS_PER_WORKFLOW:
            raise WorkflowError(
                f"a workflow may name at most {MAX_SEGMENTS_PER_WORKFLOW} Segments, got {len(cleaned)}"
            )
        if len(set(cleaned)) != len(cleaned):
            raise WorkflowError("conditions.segmentIds repeats a Segment")
        missing = [
            segment_id for segment_id in cleaned if self.books.segments.get(segment_id) is None
        ]
        if missing:
            raise WorkflowError(
                f"no such Segment: {', '.join(missing)}",
                remediation="Create the Segment first with POST /segments, then name it here.",
            )
        match = segment_module.require_match(raw.get("match"), default=DEFAULT_SEGMENT_MATCH)
        scope_room = _text(raw.get("roomId")) or None
        return {
            "segmentIds": cleaned,
            "match": match,
            "roomId": scope_room,
            "summary": ", ".join(
                _text(self.books.segments.get(segment_id)["data"].get("name"))
                for segment_id in cleaned
            ),
        }

    def list_workflows(
        self, *, room_id: str | None = None, include_inactive: bool = True
    ) -> list[dict[str, Any]]:
        records = self.books.workflows.list(limit=MAX_WORKFLOWS)
        if room_id is not None:
            records = [row for row in records if row["room_id"] == room_id]
        workflows = [self._masked_workflow(row) for row in records]
        if not include_inactive:
            workflows = [w for w in workflows if w["active"]]
        return workflows

    def read_workflow(self, workflow_id: str) -> dict[str, Any]:
        record = self._require(self.books.workflows, workflow_id, "workflow")
        payload = self._masked_workflow(record)
        data = record["data"]
        payload["data"] = {key: value for key, value in data.items() if key != "token"}
        payload["segments"] = [
            summarise_segment(row)
            for row in (
                self._segment_data(segment_id)
                for segment_id in (data.get("conditions") or {}).get("segmentIds") or []
            )
            if row is not None
        ]
        payload["contactFilter"] = payload_module.contact_filter_summary(data.get("contactFilter"))
        payload["contactFilterFields"] = list(KEYWORD_FIELDS)
        payload["tokenHeader"] = TOKEN_HEADER
        payload["warnings"] = url_warnings(_text(data.get("url")))
        payload["recentDeliveries"] = [
            summarise_delivery(row)
            for row in self.books.deliveries.find({"workflowId": workflow_id}, limit=10)
        ]
        payload["leads"] = [
            {
                "leadId": row["data"].get("leadId"),
                "companyId": row["data"].get("companyId"),
                "sendCount": row["data"].get("sendCount"),
                "firstSentAt": row["data"].get("firstSentAt"),
                "lastSentAt": row["data"].get("lastSentAt"),
                "lastDeliveryId": row["data"].get("lastDeliveryId"),
            }
            for row in self.books.states.find({"workflowId": workflow_id}, limit=MAX_LEADS)
        ]
        return payload

    def update_workflow(
        self,
        workflow_id: str,
        payload: Mapping[str, Any],
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        record = self._require(self.books.workflows, workflow_id, "workflow")
        patch: dict[str, Any] = {}

        if "name" in payload:
            name = _text(payload.get("name"))
            if not name:
                raise WorkflowError("a workflow needs a name")
            patch["name"] = name
        if "description" in payload:
            patch["description"] = _text(payload.get("description"))
        if "url" in payload or "destinationUrl" in payload:
            patch["url"] = validate_target_url(payload.get("url") or payload.get("destinationUrl"))
        if "active" in payload:
            active = payload.get("active")
            if not isinstance(active, bool):
                raise WorkflowError(
                    f"active must be true or false, not {active!r}",
                    remediation="A paused workflow still records why it sent nothing.",
                )
            patch["active"] = active
        if "sendMode" in payload:
            mode = str(_text(payload.get("sendMode")) or DEFAULT_SEND_MODE).casefold()
            if mode not in ("once", "updates"):
                raise WorkflowError(
                    f"sendMode must be 'once' or 'updates', not {payload.get('sendMode')!r}"
                )
            patch["sendMode"] = mode
        if "payload" in payload:
            patch["payload"] = payload_module.require_payload_mode(payload.get("payload"))
        if "contactFilter" in payload:
            patch["contactFilter"] = payload_module.require_contact_filter(
                payload.get("contactFilter")
            )
        if "conditions" in payload:
            patch["conditions"] = self._require_conditions(
                payload.get("conditions"), room_id=record["room_id"]
            )
        if "token" in payload:
            raise WorkflowError(
                "the token is generated by this product and cannot be set or replaced",
                remediation="This workflow has no rotation route; create a new workflow instead.",
            )
        if not patch:
            raise WorkflowError(
                "nothing to change",
                remediation="Send at least one of name, description, url, active, sendMode, payload, contactFilter, conditions.",
            )
        updated = self.books.workflows.update(record["id"], patch, source=source, actor=actor)
        return {
            "workflow": self._masked_workflow(updated),
            "updated": True,
            "warnings": url_warnings(_text(updated["data"].get("url"))),
        }

    def delete_workflow(
        self, workflow_id: str, *, source: str, actor: str | None = None
    ) -> dict[str, Any]:
        record = self._require(self.books.workflows, workflow_id, "workflow")
        # Soft delete, so the delivery log outlives the workflow that produced it.
        # A hard delete would leave every delivery row naming a workflow nobody
        # can open, which is the failure mode this product's audit log exists to
        # prevent.
        self.books.workflows.delete(record["id"], source=source, actor=actor)
        return {"id": record["id"], "deleted": True, "collection": self.books.workflows.collection}

    def reveal_token(self, workflow_id: str) -> dict[str, Any]:
        """The token in full, for configuring the destination.

        A read that writes nothing, so it leaves no audit row: this product audits
        mutations, and a signing secret readable out of the audit log on every page
        view of the workflows page would undo the masking everywhere else.
        """
        record = self._require(self.books.workflows, workflow_id, "workflow")
        token = _text((record["data"] or {}).get("token"))
        return {
            "workflowId": workflow_id,
            "token": token,
            "tokenHeader": TOKEN_HEADER,
            "bodyField": "token",
            "optional": (
                "Enforcement is the destination's choice: the research calls the token "
                "'optional to specify ... in your system to verify the Webhook'."
            ),
        }

    def preview(self, workflow_id: str, *, lead_id: str) -> dict[str, Any]:
        """The exact JSON body that would be POSTed for this lead, right now.

        A read with no side effect, and therefore no POST. Step 5 is "choose the
        output of data you want to be sent to your URL", and this is the only way
        to see that choice before a company visits.
        """
        record = self._require(self.books.workflows, workflow_id, "workflow")
        data = dict(record["data"])
        lead = self._require(self.books.leads, lead_id, "company lead")
        conditions = dict(data.get("conditions") or {})
        segments = [
            row
            for row in (self._segment_data(sid) for sid in (conditions.get("segmentIds") or []))
            if row is not None
        ]
        verdict = segment_module.evaluate_conditions(
            segments,
            dict(lead["data"]),
            match=str(conditions.get("match") or DEFAULT_SEGMENT_MATCH),
        )
        state_record = self.books.states.get(lead_state_id(workflow_id, lead_id))
        send_count = int((state_record or {}).get("data", {}).get("sendCount") or 0)
        contacts = [row["data"] for row in self._contact_records(lead_id)]
        body = payload_module.build_payload(
            company=dict(lead["data"]),
            lead_id=lead["id"],
            room_id=lead["room_id"],
            contacts=contacts,
            workflow={"id": workflow_id, **data},
            conditions=verdict,
            send_count=send_count,
            token=_text(data.get("token")) or None,
            sent_at=self.moment(),
            contact_filter=data.get("contactFilter"),
        )
        # The token is masked out of the previewed body for the same reason it
        # is masked out of a list: a preview is a page somebody reads, and a live
        # secret on a page is a leak waiting for a shoulder-surfer.
        body[TOKEN_BODY_FIELD] = token_module.mask(data.get("token"))
        return {
            "workflowId": workflow_id,
            "leadId": lead_id,
            "wouldMatch": verdict["matched"],
            "conditions": verdict,
            "sendCount": send_count,
            "wouldSend": verdict["matched"]
            and (str(data.get("sendMode") or DEFAULT_SEND_MODE) == "updates" or send_count == 0),
            "contentType": "application/json",
            "headers": {TOKEN_HEADER: token_module.mask(data.get("token"))},
            "payload": body,
            "note": (
                "The token is masked here. POST /workflows/{workflow_id}/token shows it in full; "
                "the destination receives it unmasked."
            ),
        }

    # ----------------------------------------------------------------------- #
    # Deliveries
    # ----------------------------------------------------------------------- #

    def list_deliveries(
        self,
        *,
        room_id: str | None = None,
        workflow_id: str | None = None,
        lead_id: str | None = None,
        state: str | None = None,
        skip_reason: str | None = None,
        where: Mapping[str, Any] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        filters: dict[str, Any] = dict(where or {})
        for key, value in (
            ("workflowId", workflow_id),
            ("leadId", lead_id),
            ("state", state),
            ("skipReason", skip_reason),
        ):
            if value:
                filters[key] = value
        if filters:
            records = self.books.deliveries.find(filters, limit=1000)
        else:
            records = self.books.deliveries.list(limit=limit)
        if room_id is not None:
            records = [row for row in records if row["room_id"] == room_id]
        return [summarise_delivery(row) for row in records[:limit]]

    def read_delivery(self, delivery_id: str) -> dict[str, Any]:
        record = self._require(self.books.deliveries, delivery_id, "delivery")
        data = dict(record["data"])
        payload = summarise_delivery(record)
        payload["data"] = data
        payload["attemptLog"] = data.get("attemptLog") or []
        payload["payloadBody"] = data.get("payloadBody")
        payload["payloadBytes"] = data.get("payloadBytes")
        payload["responseExcerpt"] = data.get("responseExcerpt")
        payload["conditions"] = data.get("conditions")
        payload["contactFilter"] = data.get("contactFilter")
        payload["workflow"] = (
            self._masked_workflow(row)
            if (row := self.books.workflows.get(str(data.get("workflowId"))))
            else None
        )
        return payload

    def resend(self, delivery_id: str, *, source: str, actor: str | None = None) -> dict[str, Any]:
        """Re-attempt one delivery, in place.

        The researched sources describe no retry policy, so this is the only way a
        failed POST is tried again, and it is a person asking rather than a
        schedule firing. It refuses anything that was not a failure: re-sending a
        payload that arrived is how a warehouse gets the same row twice, and it is
        how a ``once`` lead gets counted twice.
        """
        record = self._require(self.books.deliveries, delivery_id, "delivery")
        data = dict(record["data"])
        if data.get("state") == STATE_SKIPPED:
            raise DeliveryError(
                f"delivery {delivery_id} was skipped ({data.get('skipReason')}), so there is nothing to resend",
                remediation=(
                    "A skipped delivery means a researched rule said not to send. Record a new "
                    "visit to exercise the workflow again."
                ),
            )
        if data.get("state") == STATE_DELIVERED:
            raise DeliveryError(
                f"delivery {delivery_id} already reached {data.get('url')}",
                remediation="Resending a delivered payload duplicates it at the destination.",
            )
        workflow = self.books.workflows.get(str(data.get("workflowId")))
        if workflow is None:
            raise DeliveryError(
                f"workflow {data.get('workflowId')} is gone, so there is nowhere to resend to",
                remediation="The destination URL and the generated token are on the delivery row.",
            )
        lead = self.books.leads.get(str(data.get("leadId")))
        workflow_data = dict(workflow["data"])
        lead_data = dict((lead or {}).get("data") or {})

        # The lead has moved on since the failed attempt. Re-building from the
        # lead as it is now is what "send updates as well" means, and for a
        # once-mode lead it is the only way the resend reaches the destination at
        # all with the data the operator is looking at.
        if lead is not None:
            contacts = [row["data"] for row in self._contact_records(lead["id"])]
            conditions = dict(
                data.get("conditions") or {"matchedIds": [], "match": DEFAULT_SEGMENT_MATCH}
            )
            state_record = self.books.states.get(lead_state_id(workflow["id"], lead["id"]))
            send_count = int((state_record or {}).get("data", {}).get("sendCount") or 0)
            body = payload_module.build_payload(
                company=lead_data,
                lead_id=lead["id"],
                room_id=lead["room_id"],
                contacts=contacts,
                workflow={"id": workflow["id"], **workflow_data},
                conditions=conditions,
                send_count=send_count,
                token=_text(workflow_data.get("token")) or None,
                sent_at=self.moment(),
                contact_filter=workflow_data.get("contactFilter"),
            )
        else:
            # The lead was deleted. The recorded payload is all there is, and it
            # is still worth sending: the destination asked for this company, and
            # the row is a faithful copy of what was offered. The token is
            # re-attached from the workflow, which is the only place it lives.
            body = dict(data.get("payloadBody") or {})
            body[TOKEN_BODY_FIELD] = _text(workflow_data.get("token"))
            body["sentAt"] = self.moment()
        payload_bytes = encode(body)
        result = self.transport.post(
            _text(workflow_data.get("url")),
            payload_bytes,
            token_module.headers_for(_text(workflow_data.get("token"))),
            _timeout(workflow_data),
        )
        attempt = Attempt(
            number=len(data.get("attemptLog") or []) + 1,
            ok=result.ok,
            status=result.status,
            error=result.error,
            duration_ms=result.duration_ms,
            retryable=is_retryable(result),
            at=self.moment(),
            via="resend",
            final_url=result.final_url,
            body_excerpt=result.body[:512],
        )
        state = classify(result)
        patch = {
            "state": state,
            "status": result.status,
            "error": result.error,
            "responseExcerpt": result.body[:512],
            "durationMs": result.duration_ms,
            "retryable": attempt.retryable,
            "finalUrl": result.final_url,
            "attemptLog": [*(data.get("attemptLog") or []), _attempt(attempt)],
            "resentAt": self.moment(),
        }
        updated = self.books.deliveries.update(record["id"], patch, source=source, actor=actor)
        if state == STATE_DELIVERED and lead is not None:
            send_count += 1
            visit_record = {"id": data.get("visitId"), "room_id": record["room_id"]}
            self._advance_state(
                workflow["id"],
                lead,
                visit_record,  # type: ignore[arg-type]
                updated,
                send_count,
                source=source,
                actor=actor,
            )
            patch["updateCount"] = send_count
            patch["isUpdate"] = bool(send_count)
            updated = self.books.deliveries.update(record["id"], patch, source=source, actor=actor)
        return {**summarise_delivery(updated), "payload": body, "attempt": _attempt(attempt)}

    # ----------------------------------------------------------------------- #
    # Summaries
    # ----------------------------------------------------------------------- #

    def summary(self, *, room_id: str | None = None) -> dict[str, Any]:
        """Everything above the fold, optionally for one room.

        Scoped to exactly the rows the room-scoped lists return, so the tiles and
        the tables cannot disagree.
        """
        workflows = self.books.workflows.list(limit=MAX_WORKFLOWS)
        if room_id is not None:
            workflows = [row for row in workflows if row["room_id"] == room_id]
        leads = self._lead_records(room_id=room_id, limit=MAX_LEADS)
        visits = self.list_visits(room_id=room_id, limit=1000)
        deliveries = self.list_deliveries(room_id=room_id, limit=1000)
        segments = self.list_segments()

        by_state = {state: 0 for state in DELIVERY_STATES}
        by_skip: dict[str, int] = {reason: 0 for reason in SKIP_REASONS}
        for delivery in deliveries:
            by_state[delivery["state"]] = by_state.get(delivery["state"], 0) + 1
            if delivery["skipReason"]:
                by_skip[delivery["skipReason"]] = by_skip.get(delivery["skipReason"], 0) + 1
        return {
            "roomId": room_id,
            "workflows": len(workflows),
            "activeWorkflows": sum(1 for w in workflows if (w["data"] or {}).get("active", True)),
            "segments": len(segments),
            "leads": len(leads),
            "leadsWithContacts": sum(
                1 for lead in leads if self._contact_records(lead["id"], limit=1)
            ),
            "visits": len(visits),
            "visitsMatching": sum(1 for visit in visits if visit["matchedWorkflows"]),
            "deliveries": len(deliveries),
            "byState": by_state,
            "bySkipReason": by_skip,
            "retryableFailures": sum(
                1
                for delivery in deliveries
                if delivery["state"] == STATE_FAILED and delivery["retryable"]
            ),
            "permanentFailures": sum(
                1
                for delivery in deliveries
                if delivery["state"] == STATE_FAILED and not delivery["retryable"]
            ),
            "collections": list(ALL_COLLECTIONS),
        }

    def room_summary(self, room_id: str) -> dict[str, Any]:
        return self.summary(room_id=room_id)

    def fields(self) -> dict[str, Any]:
        """The JSON paths actually in use, per collection.

        The discovery endpoint the schema-flexibility rule asks for: a team adding
        a field to a lead can put it in a Segment the same day, and this is how
        they find out what is already there.
        """
        result = {}
        for collection in ALL_COLLECTIONS:
            result[collection] = self.store.fields(collection)
        return {"collections": result}

    # ----------------------------------------------------------------------- #
    # Internals
    # ----------------------------------------------------------------------- #

    def _require(self, book: Any, record_id: str, label: str) -> dict[str, Any]:
        record = book.get(record_id)
        if record is None:
            raise IntentStreamError(
                f"{label} {record_id} not found",
                code="not_found",
                status=404,
                remediation="List them, or check the id. A wrong id is not the same answer as no data yet.",
            )
        return record

    def _masked_workflow(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """A workflow with its token masked, for every read path.

        The token lives on the row; this is the single place that decides it never
        leaves. ``reveal_token`` is the only way to read it, and
        ``create_workflow`` is the only way to be handed it. See
        ``token-is-never-in-a-list-response`` in
        :mod:`dsr.intent_stream.inferences`.
        """
        payload = summarise_workflow(record)
        token = _text((record.get("data") or {}).get("token"))
        payload["tokenHint"] = token_module.mask(token)
        payload["hasToken"] = bool(token)
        payload["tokenHeader"] = TOKEN_HEADER
        return payload

    def _segment_data(self, segment_id: str) -> dict[str, Any] | None:
        """A Segment's ``data`` with its id folded in, or ``None`` if it is gone.

        The id lives in the record envelope rather than in ``data``, and
        :func:`dsr.intent_stream.segments.evaluate_segment` reports
        ``matchedIds`` from the mapping it is given. Folding the id in here is
        what makes ``lead.matchedSegmentIds`` in the payload name the Segments
        that actually matched rather than an empty list.
        """
        record = self.books.segments.get(segment_id)
        if record is None:
            return None
        return {**dict(record["data"]), "id": record["id"]}


def _without_token(body: Mapping[str, Any]) -> dict[str, Any]:
    """The payload as sent, with the token field removed.

    Used for the copy stored on the delivery row. The token is a live secret and
    the delivery's ``data`` is read back by ``GET /api/records/{collection}/{id}``
    as well as by this feature, so a stored copy is the wrong place for it.
    """
    return {key: value for key, value in body.items() if key != TOKEN_BODY_FIELD}


def _timeout(data: Mapping[str, Any]) -> float:
    """The per-attempt timeout for a workflow, defaulting to the documented one.

    Read from the row rather than pinned at construction so a workflow that
    recorded a timeout keeps it across a process restart, and so the value the
    delivery log shows is the value the attempt actually used.
    """
    raw = data.get("timeoutSeconds")
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_TIMEOUT_SECONDS


def _attempt(attempt: Attempt) -> dict[str, Any]:
    """An attempt as it is stored: the dataclass, flattened and schema-flexible."""
    return {
        "number": attempt.number,
        "ok": attempt.ok,
        "status": attempt.status,
        "error": attempt.error,
        "durationMs": attempt.duration_ms,
        "retryable": attempt.retryable,
        "at": attempt.at,
        "via": attempt.via,
        "finalUrl": attempt.final_url,
        "bodyExcerpt": attempt.body_excerpt,
    }


def _pages(raw: Any) -> list[str]:
    """The pages a visit saw, de-duplicated, order preserved, bounded."""
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return []
    seen: list[str] = []
    for item in raw:
        text = _text(item)
        if text and text not in seen:
            seen.append(text)
    return seen[:MAX_PAGES_PER_VISIT]
