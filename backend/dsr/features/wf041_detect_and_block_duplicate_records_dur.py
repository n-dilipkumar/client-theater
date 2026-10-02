"""WF-041: detect and block duplicate records during sync.

A researched workflow, not a port: there is no source branch. The research
document is the specification, and what it specifies is the CRM's duplicate
behaviour and the three ways a connector can respond to it.

What the research specifies
---------------------------

A lead or contact arrives from the room (a form fill, a CTA, a file download by
an unknown email). The connector sends the write with duplicate detection
enabled. The CRM's matching rule answers with one of three things: a clean
create, "a duplicate alert with the matching record id", or a hard block. The
connection's configured policy then decides: block the write and show the
existing record, update the existing record, or create the duplicate anyway with
an acknowledgement. The decision and the matched record id are logged on the
room row.

The seven sources it rests on, and what each contributes:

* Salesforce's ``Duplicate Rule Header`` - three boolean fields
  (``allowSave``, ``includeRecordDetails``, ``runAsCurrentUser``) whose defaults
  are all false, and which apply to "the record that is being created, updated,
  or upserted". This is the header every policy maps onto, and the reason
  ``allow`` sets ``allowSave`` while the other three set ``includeRecordDetails``.
* Salesforce's error codes - ``300`` "The value returned when an external ID
  exists in more than one record", with "no records are created or updated".
  That sentence is why a multi-match is a hard block rather than a policy option.
* Salesforce's upsert page - a field with both ``External ID`` and ``Unique``
  selected, where "The ``Unique`` attribute prevents the creation of
  duplicates". That is why a unique index outranks an ``allow`` policy.
* HubSpot's properties guide - ``hasUniqueValue: true``, and upsert by
  ``idProperty`` so "a repeat write updates rather than duplicates".
* HubSpot's contacts page - "email address is the primary unique identifier to
  avoid duplicate contacts", with domain an additional identifier for companies.
* Dataverse's alternate keys - "Alternate keys use database indexes to enforce
  uniqueness", so "a duplicate key write fails".

What this module is
-------------------

Only the three things a feature is allowed to add: the HTTP surface, the mapping
from domain errors to responses, and the demo data. The behaviour is in
:mod:`dsr.dedupe`, which is where it can be tested without a request.

Decisions in here a reviewer would otherwise have to reverse-engineer
-------------------------------------------------------------------

**Room-scoped paths are room-scoped.** Step 5 of the flow puts the decision on
the room row, so the routes that evaluate and list decisions take
``/rooms/{room_id}/...``. The unscoped routes are the ones genuinely not about a
room: the vocabularies, the policies, the connections, the matchers, and the rows
a duplicate rule matches against.

**``source`` is built from ``router.prefix`` and passed down.** The audit row
must name the route that actually served the write, so every write route builds
its source string here and hands it to a domain method that requires it. The
defect this prevents - an audit log recording a path the app no longer serves -
has shipped in this codebase before, so the suite asserts that every source
recorded matches a route the host actually mounted.

**A blocked write writes nothing at all.** Not the CRM row, not a stub. A
refusal that left a row behind would be counted as a write by anything reading
the store, which is the opposite of what "blocks the write" means. The decision
record is still written, because "nothing happened, and here is why" is what a
rep needs to read.

**``check`` writes nothing and ``ingest`` writes everything.** They call the
same ``evaluate``, so a form can warn "we already have this contact" before a rep
submits and get exactly the answer the write will produce.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.dedupe import (
    ROOM_ANNOTATION_LIMIT,
    DedupeEngine,
    DedupeError,
    inferences as dedupe_inferences,
    matching as dedupe_matching,
    policy as dedupe_policy,
    rules as dedupe_rules,
    vocabulary as dedupe_vocab,
)
from dsr.deps import StoreDep
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-041-detect-and-block-duplicate-records-dur",
    "ticket": "WF-041",
    "name": "Detect and block duplicate records during sync",
    "description": (
        "Check an inbound lead against the CRM's duplicate rule, then block, update, or "
        "deliberately create the duplicate according to the connection's policy, and log the "
        "decision with the matched record id on the room."
    ),
    "nav": [{"id": "duplicate-guard", "label": "Duplicate guard"}],
}

router = APIRouter(prefix="/api/wf-041", tags=["wf-041"])


def get_dedupe(store: RecordStore = StoreDep) -> DedupeEngine:
    """A :class:`DedupeEngine` over the process-wide audited store.

    Per request, for the same reason WF-016 builds its ``CRMSync`` per request:
    the engine holds nothing beyond the store, the CRM seam, the matcher registry
    and a clock, and building it here leaves all four overridable in a test
    instead of hanging a long-lived object off ``app.state`` - which is a shared
    file this feature may not edit.
    """
    return DedupeEngine(store)


DedupeDep = Depends(get_dedupe)


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _dedupe_error(request: Request, exc: DedupeError) -> JSONResponse:
    """Well-formed JSON asking for something this layer will not do. 400.

    One handler for the whole hierarchy, including the two subclasses: every
    refusal in :mod:`dsr.dedupe` is the caller's to fix, whether it is a typo in
    a policy name, a connection that does not exist, or a write a unique index
    will not permit. ``RecordNotFound`` is deliberately *not* claimed - the core
    app already maps it to 404, and two handlers for one type is a collision the
    host refuses.
    """
    return JSONResponse(status_code=400, content={"error": "dedupe_error", "detail": str(exc)})


EXCEPTION_HANDLERS = {DedupeError: _dedupe_error}


# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #


@router.get("/vocabulary", summary="The researched vocabulary")
def vocabulary() -> dict[str, Any]:
    """Every published term: the header and its fields, the policies, the outcomes,
    the matching keys, and each vendor's duplicate mechanism.

    Served as data so a client renders its pickers from the same source the
    validator enforces against, and so a key added in one place reaches every
    client at once.
    """
    return {
        **dedupe_vocab.published_vocabulary(),
        "policies_detail": dedupe_policy.catalogue(),
        # Named `outcome_table`, not `outcomes`: the vocabulary already publishes
        # `outcomes` as the flat list, and a client that got a dict where it
        # expected a list would break on the first render.
        "outcome_table": dedupe_rules.outcome_table(),
        "matchers": dedupe_matching.match_summary(),
    }


@router.get("/inferences", summary="Every judgement call this build makes")
def inferences() -> dict[str, Any]:
    """What the research leaves open, what this build chose, and how to change it.

    The research for WF-041 states its own limits - most sharply that Salesforce
    auto-merge "is therefore *not* claimed" - and those gaps are product
    behaviour, not comments. Collected here so a reviewer can disagree with a
    *named* entry instead of finding it in a diff.

    A read with no side effect, so it needs no store.
    """
    return dedupe_inferences.describe()


@router.get("/matchers", summary="Registered matchers")
def matchers() -> dict[str, Any]:
    """The matcher registry, including any a third party has registered.

    The research's extensibility note says "A third party can register additional
    matchers (fuzzy domain + name) evaluated in the room *before* calling the
    CRM", so the registry is a product surface rather than an internal detail: a
    deployment that registers a matcher should be able to see that it took.
    """
    return dedupe_matching.match_summary()


@router.get("/policies", summary="The per-connection dedupe policies")
def policies() -> dict[str, Any]:
    """The four policies, each with the researched sentence it comes from.

    Two of the four end in a refusal rather than a write, and the payload says so
    before an administrator saves one: ``block`` by choice, and ``merge`` because
    the research explicitly declines to claim the merge action.
    """
    return dedupe_policy.catalogue()


@router.get("/policies/{policy}", summary="One policy in full")
def policy_detail(policy: str) -> dict[str, Any]:
    """One policy, including the header options it sends.

    ``DedupeError`` maps to 400, so an unknown policy is a 400 with a message
    naming the published set rather than a bare 404.
    """
    return {
        **dedupe_policy.describe(policy),
        "header": dedupe_vocab.build_duplicate_rule_header(policy),
    }


@router.get("/header", summary="The Duplicate Rule Header a policy would send")
def header_preview(
    policy: str = Query(default="block"),
    run_as_current_user: bool = Query(default=False),
    dedupe: DedupeEngine = DedupeDep,
) -> dict[str, Any]:
    """The header for a policy, in both its structured and wire forms.

    ``runAsCurrentUser`` is "use the current user's sharing rules", which is
    about visibility rather than the duplicate decision, so it is a separate
    query parameter here and a per-connection flag in the engine.
    """
    return dedupe.header_preview(policy, run_as_current_user=run_as_current_user)


# --------------------------------------------------------------------------- #
# Connections
# --------------------------------------------------------------------------- #


@router.get("/summary", summary="Counts for the page header")
def summary(
    room_id: str | None = Query(default=None),
    dedupe: DedupeEngine = DedupeDep,
) -> dict[str, Any]:
    """Decisions by outcome and policy, and how many CRM round trips were avoided.

    The summary is computed over exactly the rows the same filters would return,
    so a room-scoped total above an unscoped list cannot be misread as a
    product-wide one.
    """
    return dedupe.summary(room_id=room_id)


@router.get("/connections", summary="List dedupe connections")
def list_connections(
    room_id: str | None = Query(default=None),
    vendor: str | None = Query(default=None),
    policy: str | None = Query(default=None),
    dedupe: DedupeEngine = DedupeDep,
) -> dict[str, Any]:
    """The connections whose policies the duplicate rules read.

    The full envelope per connection, because a rep configuring a policy needs
    the record id to patch it and the resolved key list to know what it will
    actually match on.
    """
    records = dedupe.list_connections(room_id=room_id, vendor=vendor, policy=policy)
    return {"count": len(records), "connections": records}


@router.post("/connections", status_code=201, summary="Declare a connection")
def create_connection(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    dedupe: DedupeEngine = DedupeDep,
) -> dict[str, Any]:
    """Declare a connection and its dedupe policy.

    "Dedupe policy is a per-connection enum (block / update / merge), so a
    deployment can escalate to 'auto-merge' for high-confidence cases" - so the
    policy lives here rather than being a product-wide constant, and two
    connections to two CRMs can disagree.

    Validated before the row is created, so a bad policy or a unique key that is
    not a configured key cannot leave a half-configured connection behind.
    """
    return dedupe.create_connection(
        payload, actor=actor, source=f"POST {router.prefix}/connections"
    )


@router.get("/connections/{connection_id}", summary="Read one connection")
def read_connection(connection_id: str, dedupe: DedupeEngine = DedupeDep) -> dict[str, Any]:
    """One connection, or 404 if it does not exist."""
    record = dedupe.get_connection(connection_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"connection {connection_id} not found")
    return record


@router.patch("/connections/{connection_id}", summary="Patch a connection")
def update_connection(
    connection_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    dedupe: DedupeEngine = DedupeDep,
) -> dict[str, Any]:
    """Patch a connection's policy, keys, or vendor.

    Re-validated against the merged result, so a patch cannot leave a connection
    whose ``unique_keys`` names a key it no longer matches on.
    """
    return dedupe.update_connection(
        connection_id,
        payload,
        actor=actor,
        source=f"PATCH {router.prefix}/connections/{connection_id}",
    )


@router.delete("/connections/{connection_id}", summary="Delete a connection")
def delete_connection(
    connection_id: str,
    actor: str | None = Query(default=None),
    dedupe: DedupeEngine = DedupeDep,
) -> Response:
    """Soft-delete a connection. 204.

    Its decisions stay auditable, which is the point: the history of what a
    policy did outlives the policy.
    """
    if dedupe.get_connection(connection_id) is None:
        raise HTTPException(status_code=404, detail=f"connection {connection_id} not found")
    dedupe.delete_connection(
        connection_id, actor=actor, source=f"DELETE {router.prefix}/connections/{connection_id}"
    )
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# The rows a duplicate rule matches against
# --------------------------------------------------------------------------- #


@router.get("/records", summary="List the rows duplicate rules match against")
def list_records(
    room_id: str | None = Query(default=None),
    object_type: str | None = Query(default=None, description="contact | company | account"),
    limit: int = Query(default=100, ge=1, le=1000),
    dedupe: DedupeEngine = DedupeDep,
) -> dict[str, Any]:
    """The account and contact rows the rules match against.

    These stand in for the CRM's own tables. A rep reads them to understand *why*
    a decision blocked, and a team populates them from its own import.
    """
    records = dedupe.list_records(room_id=room_id, object_type=object_type, limit=limit)
    return {"count": len(records), "records": records}


@router.post("/records", status_code=201, summary="Register a row to match against")
def create_record(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    dedupe: DedupeEngine = DedupeDep,
) -> dict[str, Any]:
    """Register a row the duplicate rules will match against.

    Needs at least one matching key - an email, an external ID, an account number
    or a domain - because a row that cannot be matched on cannot be a duplicate.
    """
    return dedupe.create_record(
        payload, room_id=room_id, actor=actor, source=f"POST {router.prefix}/records"
    )


# --------------------------------------------------------------------------- #
# Decisions
# --------------------------------------------------------------------------- #


@router.get("/decisions", summary="List duplicate decisions")
def list_decisions(
    room_id: str | None = Query(default=None),
    outcome: str | None = Query(default=None),
    policy: str | None = Query(default=None),
    connection_id: str | None = Query(default=None),
    needs_human: bool | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    dedupe: DedupeEngine = DedupeDep,
) -> dict[str, Any]:
    """Every decision, newest first.

    ``needs_human`` separates the two kinds of no-write: a ``blocked`` decision is
    the policy working as configured, while a ``hard_blocked`` or ``escalated``
    one is something a person has to resolve.
    """
    records = dedupe.decisions(
        room_id=room_id,
        outcome=outcome,
        policy=policy,
        connection_id=connection_id,
        needs_human=needs_human,
        limit=limit,
    )
    return {"count": len(records), "decisions": records}


@router.get("/rooms/{room_id}/decisions", summary="A room's duplicate decisions")
def room_decisions(
    room_id: str,
    outcome: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    dedupe: DedupeEngine = DedupeDep,
) -> dict[str, Any]:
    """The decisions taken for one room.

    Room-scoped because step 5 of the researched flow puts the decision on the
    room row, so a rep reads a room's duplicates from that room and not from a
    product-wide feed.
    """
    if dedupe.store.get(room_id) is None:
        raise HTTPException(status_code=404, detail=f"room {room_id} not found")
    records = dedupe.decisions(room_id=room_id, outcome=outcome, limit=limit)
    return {"room_id": room_id, "count": len(records), "decisions": records}


@router.get("/rooms/{room_id}/decisions/{decision_id}", summary="One decision in full")
def read_decision(
    room_id: str,
    decision_id: str,
    dedupe: DedupeEngine = DedupeDep,
) -> dict[str, Any]:
    """One decision, with the header it sent and the match detail it recorded."""
    record = dedupe.get_decision(decision_id)
    if record is None or record.get("room_id") != room_id:
        raise HTTPException(
            status_code=404, detail=f"decision {decision_id} not found on room {room_id}"
        )
    return record


# --------------------------------------------------------------------------- #
# The workflow
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/dedupe", summary="The duplicate annotation on a room")
def room_annotation(room_id: str, dedupe: DedupeEngine = DedupeDep) -> dict[str, Any]:
    """The researched step-5 annotation: the latest decision and the matched record id.

    Read straight off the room row, because that is where the research says it
    lives, rather than being reconstructed from the decision records.
    """
    room = dedupe.store.get(room_id)
    if room is None:
        raise HTTPException(status_code=404, detail=f"room {room_id} not found")
    annotation = dict(room["data"].get("dedupe") or {})
    return {
        "room_id": room_id,
        "dedupe": annotation,
        "history": list(annotation.get("history") or []),
        "limit": ROOM_ANNOTATION_LIMIT,
    }


@router.post("/rooms/{room_id}/check", summary="Evaluate without writing")
def check(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    connection_id: str | None = Query(default=None),
    dedupe: DedupeEngine = DedupeDep,
) -> dict[str, Any]:
    """Run steps 2 to 4 for an inbound row and report what *would* happen.

    Writes nothing at all - not a decision record, not a CRM row, not the room
    annotation. It is the read-only half of :func:`ingest`, for a form that wants
    to warn "we already have this contact" before the rep submits.

    The answer is identical to what ``ingest`` would decide, because both call the
    same ``evaluate``; only the consequences differ.
    """
    if dedupe.store.get(room_id) is None:
        raise HTTPException(status_code=404, detail=f"room {room_id} not found")
    connection = dedupe.resolve_connection(connection_id)
    return dedupe.evaluate(payload, connection, room_id=room_id).to_dict()


@router.post("/rooms/{room_id}/ingest", status_code=201, summary="Run the full workflow")
def ingest(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    connection_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    dedupe: DedupeEngine = DedupeDep,
) -> dict[str, Any]:
    """Steps 1 to 5: evaluate, act, and log. The workflow, end to end.

    The response carries the decision record, which says what was written
    (``created``, ``updated``, ``created_duplicate``) or that nothing was
    (``blocked``, ``hard_blocked``, ``escalated``), the header that was sent, the
    matched record ids, and ``crm_called`` - false when the in-room check answered
    without a round trip.

    A disabled connection is refused rather than ignored: evaluating with the
    duplicate rules switched off would write rows no rule had checked.
    """
    return dedupe.ingest(
        room_id,
        payload,
        connection_id=connection_id,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{room_id}/ingest",
    )


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

#: The existing CRM rows the demo's decisions match against.
#:
#: Every arrangement here earns its place by making one researched rule visible:
#:
#: * ``tomas.vela@contoso.example`` and ``Tomas.Vela@Contoso.Example`` are one
#:   address in two spellings, and ``www.northwind.example.`` is one domain in
#:   three. The normalisers are what turn those into single matches, so a reviewer
#:   can see why the case-sensitivity of the index does not create duplicates.
#: * Two rows share ``ext-2001``, which is the researched 300: a hard block where
#:   "no records are created or updated".
#: * ``solowebb.example`` and ``soluspring.example`` each appear on exactly one
#:   row, so a domain match lands on a single record rather than turning into a
#:   multi-match. A demo that shared one domain everywhere would make every
#:   domain-keyed decision a hard block and teach the wrong thing.
DEMO_RECORDS: tuple[dict[str, Any], ...] = (
    {
        "object_type": "contact",
        "name": "Priya Raman",
        "email": "priya.raman@northwind.example",
        "domain": "northwind.example",
        "external_id": "ext-1001",
        "title": "Procurement lead",
    },
    {
        "object_type": "contact",
        "name": "Priya Raman",
        "email": "Priya.Raman@Northwind.example",
        "domain": "www.northwind.example.",
        "external_id": "ext-1002",
        "title": "Procurement lead",
    },
    {
        "object_type": "account",
        "name": "Northwind Traders",
        "domain": "northwind.example",
        "account_number": "NW-4471",
    },
    {
        "object_type": "contact",
        "name": "Tomas Vela",
        "email": "tomas.vela@contoso.example",
        "domain": "contoso.example",
        "external_id": "ext-2001",
    },
    {
        "object_type": "contact",
        "name": "Alba Ries",
        "email": "alba.ries@fabrikam.example",
        "domain": "fabrikam.example",
        # Shares ext-2001 with Tomas on purpose. Two rows on one external ID is
        # the researched 300, and it is the only arrangement that produces a 300.
        "external_id": "ext-2001",
    },
    {
        "object_type": "contact",
        "name": "Marcus Webb",
        "email": "marcus.webb@solowebb.example",
        "domain": "solowebb.example",
    },
    {
        "object_type": "contact",
        "name": "Dana Okoro",
        "email": "dana.okoro@fabrikam-ops.example",
        "domain": "fabrikam-ops.example",
    },
    {
        "object_type": "contact",
        "name": "Nadia Farouk",
        "email": "nadia.farouk@soluspring.example",
        "domain": "soluspring.example",
        # Seeded into the first room on purpose, so the fuzzy case below is also
        # the one the in-room pre-check answers without calling the CRM.
        "room_index": 0,
    },
)

#: The connections the demo declares, one per policy plus the two configurations
#: worth seeing: a permissive policy with a unique index (which the index wins),
#: and a disabled connection.
DEMO_CONNECTIONS: tuple[dict[str, Any], ...] = (
    {
        "name": "Northwind Salesforce (block)",
        "vendor": "salesforce",
        "policy": "block",
        "keys": ["external_id", "email", "account_number", "domain"],
        "unique_keys": ["email"],
    },
    {
        "name": "Northwind Salesforce (update)",
        "vendor": "salesforce",
        "policy": "update",
        "keys": ["external_id", "email", "account_number", "domain"],
        "unique_keys": ["email"],
    },
    {
        # allow, with the email unique index dropped, so the duplicate can
        # actually be written.
        "name": "Fabrikam HubSpot (allow, no unique index)",
        "vendor": "hubspot",
        "policy": "allow",
        "keys": ["email", "domain"],
        "unique_keys": [],
    },
    {
        # allow with the unique index still on: the researched conflict, where
        # "The Unique attribute prevents the creation of duplicates" beats the
        # administrator's permissive policy.
        "name": "Northwind HubSpot (allow, unique email)",
        "vendor": "hubspot",
        "policy": "allow",
        "keys": ["email", "domain"],
        "unique_keys": ["email"],
    },
    {
        "name": "Contoso Dataverse (merge escalation)",
        "vendor": "dataverse",
        "policy": "merge",
        "keys": ["email", "account_number"],
        "unique_keys": ["email"],
    },
    {
        # Off on purpose, so a reviewer can see what a disabled connection does:
        # it refuses the write rather than quietly skipping the check.
        "name": "Legacy connector (disabled)",
        "vendor": "dataverse",
        "policy": "block",
        "keys": ["email"],
        "unique_keys": ["email"],
        "enabled": False,
    },
)

#: One inbound row per outcome the research names, each with a *different* person
#: so the cases cannot interfere with one another - an ``allow`` decision writes
#: a real duplicate, and a shared person would make the next case a multi-match
#: and quietly relabel it.
#:
#: Between them they reach all six outcomes, both hard blocks, all three header
#: shapes, and both matchers (an exact key and the fuzzy one).
DEMO_INGESTS: tuple[dict[str, Any], ...] = (
    {
        "label": "created: a person the CRM has never seen",
        "connection": "Northwind Salesforce (block)",
        "inbound": {
            "object_type": "contact",
            "name": "Wen Li",
            "email": "wen.li@newco.example",
            "domain": "newco.example",
            "title": "Operations",
        },
    },
    {
        "label": "blocked: a repeat form fill finds the existing contact",
        "connection": "Northwind Salesforce (block)",
        "inbound": {
            "object_type": "contact",
            "name": "Tomas Vela",
            # Padded and shouted. The email normaliser makes this one address, and
            # no external id is sent - a browser form has none, and sending one
            # would point at a second row and turn this into a multi-match.
            "email": "  TOMA.Vela@Contoso.Example  ",
            "domain": "CONTOSO.example",
        },
    },
    {
        "label": "hard blocked: one external ID on two rows is the researched 300",
        "connection": "Northwind Salesforce (block)",
        "inbound": {
            "object_type": "contact",
            "name": "Rui Silva",
            # A domain of its own, so the only key that fires is the external id
            # and the decision is the researched 300 rather than an ambiguous
            # two-keys-two-records block.
            "email": "rui@silva-consulting.example",
            "domain": "silva-consulting.example",
            "external_id": "ext-2001",
        },
    },
    {
        "label": "updated: the same person under the update policy",
        "connection": "Northwind Salesforce (update)",
        "inbound": {
            "object_type": "contact",
            "name": "Alba Ries",
            "email": "alba.ries@fabrikam.example",
            "domain": "fabrikam.example",
            # The field the update actually lands on, so the demo shows a write.
            "title": "Renewals director",
        },
    },
    {
        "label": "hard blocked: a unique index refuses the permissive policy",
        "connection": "Northwind HubSpot (allow, unique email)",
        "inbound": {
            "object_type": "contact",
            "name": "Marcus Webb",
            "email": "MARCUS.WEBB@solowebb.example",
            "domain": "solowebb.example",
        },
    },
    {
        "label": "created anyway: allow, and the unique index is off",
        "connection": "Fabrikam HubSpot (allow, no unique index)",
        "inbound": {
            "object_type": "contact",
            "name": "Dana Okoro",
            "email": "dana.okoro@fabrikam-ops.example",
            "domain": "fabrikam-ops.example",
            "source_page": "pricing",
        },
    },
    {
        "label": "escalated: merge is the research's target and claims no action",
        "connection": "Contoso Dataverse (merge escalation)",
        "inbound": {
            "object_type": "contact",
            "name": "Tomas Vela",
            "email": "tomas.vela@contoso.example",
            "domain": "contoso.example",
        },
    },
    {
        # Deliberately pinned to the room holding Nadia's row, so this case is
        # also the one the in-room pre-check answers: crm_called is false here and
        # true for every other block in the demo.
        "label": "blocked by the in-room check, before any CRM call",
        "connection": "Northwind Salesforce (block)",
        "room_index": 0,
        "inbound": {
            "object_type": "contact",
            "name": "Nadia A. Farouk",
            "email": "nadia.a.farouk@soluspring.example",
            "domain": "soluspring.example",
        },
    },
)

#: The inbound rows that a disabled connection must refuse. Kept apart from
#: :data:`DEMO_INGESTS` because it is expected to raise, and a seed that raised
#: would be skipped by ``backend/seed.py`` with the whole feature's demo lost.
DEMO_DISABLED_INGEST: dict[str, Any] = {
    "connection": "Legacy connector (disabled)",
    "inbound": {"object_type": "contact", "name": "Rui Silva", "email": "rui.silva@newco.example"},
}


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed the duplicate rules' rows, six connections, and eight decisions.

    The decisions come from running the real :class:`DedupeEngine` over the real
    rules, so the demo cannot show a shape the workflow would not produce, and
    seeding opens no socket because the CRM here is the audited store.

    Deliberately mixed. A demo of only clean creates would teach nothing about
    the three things this workflow exists for: the block, the 300 hard block, and
    the escalation that needs a person. All three are here, alongside the
    unique-index refusal, a fuzzy hit, and a case the in-room check answered
    without calling the CRM at all.

    Returns a description the seeder prints, and which says how many of each
    outcome landed so a reviewer can see at a glance that the demo is mixed.
    """
    store = RecordStore(db)
    dedupe = DedupeEngine(store, clock=lambda: context["now"])
    source = "seed"
    rooms: list[tuple[str, str]] = list(context.get("room_ids") or [])
    if not rooms:
        # The rows and connections are still worth having with no room to scope a
        # decision to; the seeder prints what was skipped.
        return "0 decisions (no rooms to scope them to)"

    def room_at(index: int) -> str:
        return rooms[index % len(rooms)][0]

    for spec in DEMO_RECORDS:
        body = dict(spec)
        index = body.pop("room_index", None)
        dedupe.create_record(
            body, room_id=room_at(index) if index is not None else None, actor="dana", source=source
        )

    by_name: dict[str, str] = {}
    for spec in DEMO_CONNECTIONS:
        record = dedupe.create_connection(spec, actor="dana", source=source)
        by_name[str(record["data"]["name"])] = record["id"]

    outcomes: list[str] = []
    for index, case in enumerate(DEMO_INGESTS):
        case_room = room_at(case["room_index"]) if "room_index" in case else room_at(index)
        decision = dedupe.ingest(
            case_room,
            case["inbound"],
            connection_id=by_name.get(case["connection"]),
            actor="dana",
            source=source,
        )
        outcomes.append(str(decision["data"]["outcome"]))

    # A disabled connection must refuse rather than quietly skip the check. Run
    # it here and expect the refusal, so the demo asserts the behaviour rather
    # than a reader having to take the connection's word for it.
    disabled_refused = False
    try:
        dedupe.ingest(
            rooms[0][0],
            DEMO_DISABLED_INGEST["inbound"],
            connection_id=by_name.get(DEMO_DISABLED_INGEST["connection"]),
            actor="dana",
            source=source,
        )
    except DedupeError:
        disabled_refused = True

    tally: dict[str, int] = {}
    for outcome in outcomes:
        tally[outcome] = tally.get(outcome, 0) + 1
    avoided = dedupe.summary()["crm_calls_avoided"]
    return (
        f"{len(DEMO_RECORDS)} rows, {len(DEMO_CONNECTIONS)} connections, {len(outcomes)} decisions "
        f"({avoided} answered without a CRM call), outcomes: "
        + ", ".join(f"{count} {name}" for name, count in sorted(tally.items()))
        + (", 1 disabled connection refused" if disabled_refused else "")
    )
