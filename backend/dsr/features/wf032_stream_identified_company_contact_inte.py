"""WF-032: stream identified company/contact intent to your own systems.

A BUILD, not a port: there is no source branch. The research document *is* the
specification - ``docs/research/digital-sales-room-workflows/wf/WF-032.md``, whose
research source is section 17 of ``docs/research/raw/analytics-intent.md`` - and
the job was to land its decisions, not to improve on them.

What this module is
-------------------
The three things the plugin contract asks a feature to own, and nothing else:

* the **route table**, as an ``APIRouter`` on ``/api/wf-032``;
* the **error mapping**, as ``EXCEPTION_HANDLERS`` for the host to attach;
* the **demo rows**, as a ``seed(db, context)`` hook the seeder calls.

The researched rules live in :mod:`dsr.intent_stream`, which imports nothing from
``dsr.api`` and holds no HTTP at all. That is what keeps this module readable: a
route here is a translation of a request into one domain call, and every rule
that is argued about lives beside the other rules it is argued with.

The researched user flow, mapped onto these routes
--------------------------------------------------
1. "click **Workflows** and select **New Workflow**. Choose **Webhooks** in the
   popup" -> ``GET /vocabulary``, which publishes ``webhooks`` as the one
   researched workflow type and says so.
2. "Add a name for your Workflow and the URL you want to send data to" ->
   ``POST /workflows``, which refuses a name-less workflow and a URL that is not
   a destination an HTTP POST could reach - before writing anything, so a bad URL
   leaves no workflow behind.
3. "Add the conditions you want to be applied to the workflow to sort out which
   leads you want your Workflow to send based on **saved Segments** from your
   account" -> ``POST /segments`` (and ``PATCH``/``DELETE``) for the Segments,
   ``POST /workflows`` with ``conditions`` for the selection, and
   ``POST /segments/{id}/evaluate`` to find out why one did or did not match
   without creating a visit.
4. "the option to choose if the workflow should **only send a lead once** or if it
   should **send updates as well**" -> ``sendMode`` on the workflow, enforced in
   :meth:`dsr.intent_stream.stream.IntentStream._apply_workflow` and visible as a
   ``skipped`` / ``already_sent`` row on every later visit.
5. "only Company ... or Company + Contacts ... filtering the contact details on
   '**Keywords**' and required fields" -> ``payload`` and ``contactFilter`` on the
   workflow, ``GET /workflows/{id}/preview`` for the exact body, and
   ``GET /leads/{id}/contacts``.
6. "a token to use in your service or tool to prove that traffic is coming from
   the Albacross platform. It is **optional** to specify this automatically
   generated token" -> the token is generated at creation, returned once, masked
   everywhere else, revealed by ``POST /workflows/{id}/token``, and attached to
   every POST in a header and in the body.
7. "Start receiving JSON at the destination; combine with a webhook workflow such
   as 'Integrating with Microsoft Teams via Webhooks' or 'Integrating with Google
   Sheets via Webhooks'" -> ``GET /destinations`` for the researched recipes, and
   ``GET /deliveries`` for what each destination actually did.

And the trigger itself, which the research says is "the segment-matching company
visit - no user action": ``POST /visits`` writes the visit, refreshes the lead's
activity data, and evaluates every workflow - writing one delivery row per
workflow per outcome, including the ones where nothing was sent.

``source=`` comes from the route, never from the domain
-------------------------------------------------------
:func:`_source` builds every audit ``source`` from ``router.prefix``, so the audit
log and the route table cannot drift. The build brief names the defect this
prevents - a feature whose audit log kept recording a path the app had stopped
serving - and a domain function that hard-codes its own path cannot be caught by
reading the route table at all, so ``source`` is a *required* keyword on every
writing method in :mod:`dsr.intent_stream` and a test checks every recorded
source against the routes the host actually mounted.
"""

from __future__ import annotations

from typing import Any, Mapping

from fastapi import APIRouter, Body, Depends, Query, Request, Response
from fastapi.responses import JSONResponse

from dsr.deps import StoreDep
from dsr.intent_stream import IntentStream
from dsr.intent_stream.errors import IntentStreamError
from dsr.intent_stream.inferences import describe as describe_inferences
from dsr.intent_stream.segments import (
    OPERATORS as SEGMENT_OPERATORS,
    ORDERING_OPERATORS,
    UNARY_OPERATORS,
)
from dsr.intent_stream.vocabulary import vocabulary
from dsr.store import RecordStore, parse_where

FEATURE = {
    "id": "wf-032-stream-identified-company-contact-inte",
    "ticket": "WF-032",
    "name": "Stream identified company/contact intent to your own systems",
    "description": (
        "Save a Segment, point a webhook workflow at a destination, and watch every "
        "company visit that matches: what was sent, whether it was the first time or "
        "an update, which contacts survived the keyword and required-field filters, and "
        "where the researched rules said not to send."
    ),
    "nav": [{"id": "intent-stream", "label": "Intent stream"}],
}

router = APIRouter(prefix="/api/wf-032", tags=["wf032"])


# --------------------------------------------------------------------------- #
# The service
# --------------------------------------------------------------------------- #

#: One :class:`IntentStream` per store, rebuilt if the store itself changes.
#:
#: Cached rather than rebuilt per request so a scripted transport the tests
#: install is the one every request in a test uses. Keyed on the store, because a
#: new ``TestClient`` builds a new store and a service bound to a closed
#: database would fail in a way that has nothing to do with the code under test.
_STREAM: IntentStream | None = None


def get_stream(store: RecordStore = StoreDep) -> IntentStream:
    """The intent stream over the process-wide audited store.

    Composes the documented dependency seam rather than reading
    ``request.app.state``, so this module imports nothing from ``dsr.api``.
    """
    global _STREAM
    if _STREAM is None or _STREAM.store is not store:
        _STREAM = IntentStream(store)
    return _STREAM


StreamDep = Depends(get_stream)


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
#
# ``IntentStreamError`` is the base of every refusal in :mod:`dsr.intent_stream`
# and is raised by nothing else in the product, which is what makes it safe to
# hand to the host. A handler for ``ValueError`` would let this feature intercept
# exceptions raised anywhere in the app, since the domain error is itself one.
# One handler for the whole hierarchy also means the family keeps one shape on the
# wire, with the status carried per error rather than per class.
#
# ``RecordNotFound`` is deliberately *not* claimed: the core app already maps it
# to 404, and two handlers for one type is a collision the host refuses.


def _intent_stream_error(request: Request, exc: IntentStreamError) -> JSONResponse:
    """Render a domain refusal with its status, code, and remediation.

    ``detail`` is the one awkward part. The shared ``apiRequest`` in
    ``frontend/src/lib/api.js`` keeps only ``body.detail`` (and the status) and
    discards the rest of the body, so a client built on it can only ever show
    ``detail``. Rather than let the code, the remedy, and the correlation id be
    silently dropped on the way to the screen, the sentence in ``detail`` carries
    them. The structured fields are still on the wire verbatim for any other
    client.

    If ``apiRequest`` ever keeps the parsed body - as ``lib/api.js`` would if it
    set ``error.body`` - this composition can be dropped and the UI can read the
    fields again. That edit is a shared-file change and so is not made here.
    """
    payload = exc.to_payload()
    parts = [f"{exc.code}: {exc.detail}"]
    if exc.remediation:
        parts.append(exc.remediation)
    parts.append(f"correlation id {exc.correlation_id}")
    payload["detail"] = " ".join(parts)
    return JSONResponse(status_code=exc.status, content=payload, headers=exc.headers)


EXCEPTION_HANDLERS = {IntentStreamError: _intent_stream_error}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _source(verb: str, suffix: str = "") -> str:
    """The audit ``source`` for a write: the path this router actually serves.

    Built from ``router.prefix`` rather than written out, so the audit log and
    the route table cannot drift apart. A domain function that hard-codes its
    own path is the defect this prevents: the audit log would keep naming a
    route the app had stopped serving, which is worse than no audit row because
    it looks authoritative.
    """
    return f"{verb} {router.prefix}{suffix}"


def _filters(raw: str | None) -> dict[str, Any]:
    """Parse a ``where`` query parameter, or refuse it as this feature's own error."""
    try:
        return parse_where(raw)
    except ValueError as exc:
        raise IntentStreamError(
            str(exc),
            code="invalid_where",
            remediation='Send where={"state":"failed"} or where=workflowId=wf032_workflow_x.',
        ) from exc


# --------------------------------------------------------------------------- #
# Vocabulary, evidence, and the reference material
# --------------------------------------------------------------------------- #


@router.get("/vocabulary", summary="The researched vocabulary")
def get_vocabulary() -> dict[str, Any]:
    """The workflow type, the send modes, the payload modes, the rule grammar.

    Served as data so a client renders its pickers from the same source the
    validator enforces against, and so a claim about what this product accepts
    can be checked rather than believed. The Segment operators are merged in from
    the module that implements them, so the grammar on screen cannot drift from
    the grammar in the code.
    """
    result = vocabulary()
    result["segmentOperators"] = list(SEGMENT_OPERATORS)
    result["segmentOrderingOperators"] = list(ORDERING_OPERATORS)
    result["segmentUnaryOperators"] = list(UNARY_OPERATORS)
    return result


@router.get("/explain", summary="What are webhooks - the researched explainer")
def get_explain(stream: IntentStream = StreamDep) -> dict[str, Any]:
    """The explainer, the seven-step flow, the data flow, and the evidence.

    Every sentence on the page is traceable to one of the researched quotes this
    returns, which is the point of serving them: a reviewer can check the claim
    against the source instead of against the build.
    """
    return stream.explain()


@router.get("/inferences", summary="Every judgement call this build made")
def get_inferences() -> dict[str, Any]:
    """The researched half and the inferred half, side by side.

    A read with no side effect, so it needs no store - which is why it is the one
    reference route that takes no dependency. A reviewer can settle any single
    decision by name from here without running anything.
    """
    return describe_inferences()


@router.get("/destinations", summary="The researched destinations")
def get_destinations(stream: IntentStream = StreamDep) -> dict[str, Any]:
    """Microsoft Teams and Google Sheets as webhook recipes; the rest as surfaces.

    The split is the useful one: a *recipe* is a consumer you paste a URL into,
    and a *surface* is a vendor the research listed without reading a recipe for.
    """
    return stream.destinations()


# --------------------------------------------------------------------------- #
# Saved Segments
# --------------------------------------------------------------------------- #


@router.get("/segments", summary="Saved Segments")
def list_segments(stream: IntentStream = StreamDep) -> dict[str, Any]:
    """Every saved Segment, with its rules and the one-line summary of them."""
    segments = stream.list_segments()
    return {"count": len(segments), "segments": segments}


@router.post("/segments", status_code=201, summary="Save a Segment")
def create_segment(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    stream: IntentStream = StreamDep,
) -> dict[str, Any]:
    """Step 4's subject matter: a named, reusable selection of company leads.

    Rules are ``{path, operator, value}`` triples over the lead's own data, and
    the operator is refused here rather than at send time: a Segment that cannot
    be evaluated has to say so while somebody is looking at it, or it looks
    exactly like a Segment nobody is in.
    """
    return stream.create_segment(payload, actor=actor, source=_source("POST", "/segments"))


@router.get("/segments/{segment_id}", summary="One Segment, and who uses it")
def read_segment(segment_id: str, stream: IntentStream = StreamDep) -> dict[str, Any]:
    """One Segment, with ``usedBy`` so a delete's consequence is visible first."""
    return stream.read_segment(segment_id)


@router.patch("/segments/{segment_id}", summary="Rename, re-rule, or re-scope a Segment")
def update_segment(
    segment_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    stream: IntentStream = StreamDep,
) -> dict[str, Any]:
    """Patch a Segment.

    Rules are re-validated in full rather than merged, so a patch cannot leave a
    Segment holding a half-valid rule set.
    """
    return stream.update_segment(
        segment_id, payload, actor=actor, source=_source("PATCH", f"/segments/{segment_id}")
    )


@router.delete("/segments/{segment_id}", summary="Delete a Segment")
def delete_segment(
    segment_id: str,
    actor: str | None = Query(default=None),
    stream: IntentStream = StreamDep,
) -> dict[str, Any]:
    """Delete a Segment, unless a workflow still names it.

    A 409 in that case, not a cascade: a workflow pointing at a Segment that is
    gone would stop sending while looking perfectly healthy on both lists.
    """
    return stream.delete_segment(
        segment_id, actor=actor, source=_source("DELETE", f"/segments/{segment_id}")
    )


@router.post("/segments/{segment_id}/evaluate", summary="Why did this Segment match?")
def evaluate_segment(
    segment_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    lead_id: str | None = Query(default=None),
    stream: IntentStream = StreamDep,
) -> dict[str, Any]:
    """Run one Segment against one lead, and report every rule's verdict.

    A read with no side effect: no visit, so nothing is sent. This is the
    difference between "this company is not in my Segment" and "this Segment is
    broken", and it is the only way to tell them apart without waiting for the
    next real visit.
    """
    return stream.evaluate_segment_against(
        segment_id,
        lead_id=lead_id or (payload.get("leadId") or None),
        company=payload.get("company") if isinstance(payload.get("company"), Mapping) else None,
    )


# --------------------------------------------------------------------------- #
# Company leads and their contacts
# --------------------------------------------------------------------------- #


@router.get("/leads", summary="Identified company leads")
def list_leads(
    room_id: str | None = Query(default=None),
    where: str | None = Query(default=None, description='JSON object or "k=v,k2=v2"'),
    limit: int = Query(default=100, ge=1, le=1000),
    stream: IntentStream = StreamDep,
) -> dict[str, Any]:
    """The companies this account has identified, with their activity data.

    ``where`` filters on any dotted JSON path in the lead's own data, which is
    the same grammar a Segment uses - so a question you can ask here you can
    turn into a Segment without translating it.
    """
    leads = stream.list_leads(room_id=room_id, limit=limit, where=_filters(where))
    return {"count": len(leads), "leads": leads}


@router.post("/leads", status_code=201, summary="Record an identified company")
def create_lead(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    stream: IntentStream = StreamDep,
) -> dict[str, Any]:
    """Add an identified company lead.

    Identification is another workflow's job; this one streams the result. The
    payload is the lead's own arbitrary JSON, so a team can carry firmographics
    their own systems have and put them in a Segment the same day.
    """
    return stream.create_lead(
        payload, room_id=room_id, actor=actor, source=_source("POST", "/leads")
    )


@router.get("/leads/{lead_id}", summary="One company lead, with its workflow state")
def read_lead(lead_id: str, stream: IntentStream = StreamDep) -> dict[str, Any]:
    """One lead, with its contacts, its recent visits, and which workflows have sent it.

    The per-workflow send counts are the answer to the question the once-versus-
    updates choice raises: "why has this company not been sent again?"
    """
    return stream.read_lead(lead_id)


@router.patch("/leads/{lead_id}", summary="Change a company lead's data")
def update_lead(
    lead_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    stream: IntentStream = StreamDep,
) -> dict[str, Any]:
    """Patch a lead's data.

    ``companyId`` is refused rather than changed: it is this product's identity
    for the company, and a second row for the same company is how a "send once"
    rule sends twice.
    """
    return stream.update_lead(
        lead_id, payload, actor=actor, source=_source("PATCH", f"/leads/{lead_id}")
    )


@router.get("/leads/{lead_id}/contacts", summary="Contacts at the identified company")
def list_contacts(
    lead_id: str,
    limit: int = Query(default=500, ge=1, le=500),
    stream: IntentStream = StreamDep,
) -> dict[str, Any]:
    """Every contact known at this company, before any filter is applied.

    The filters live on the workflow, so this is the unfiltered list - which is
    what an operator needs in order to work out why a filter kept nobody.
    """
    contacts = stream.list_contacts(lead_id, limit=limit)
    return {"leadId": lead_id, "count": len(contacts), "contacts": contacts}


@router.post("/leads/{lead_id}/contacts", status_code=201, summary="Add a contact")
def create_contact(
    lead_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    stream: IntentStream = StreamDep,
) -> dict[str, Any]:
    """Add a person employed at the identified company.

    "Contacts for the company lead and contacts employed at the company" - so a
    contact belongs to a lead, and the payload names them.
    """
    return stream.create_contact(
        lead_id, payload, actor=actor, source=_source("POST", f"/leads/{lead_id}/contacts")
    )


# --------------------------------------------------------------------------- #
# The trigger
# --------------------------------------------------------------------------- #


@router.get("/visits", summary="Company visits")
def list_visits(
    room_id: str | None = Query(default=None),
    lead_id: str | None = Query(default=None),
    matched: bool | None = Query(
        default=None, description="false for a visit that matched no workflow"
    ),
    limit: int = Query(default=100, ge=1, le=1000),
    stream: IntentStream = StreamDep,
) -> dict[str, Any]:
    """Every company visit, including the ones that matched nothing.

    ``matched=false`` is the state an operator needs in order to debug a Segment:
    a visit that matched nothing is how "why is this company not in my Segment"
    becomes answerable.
    """
    visits = stream.list_visits(room_id=room_id, lead_id=lead_id, matched=matched, limit=limit)
    return {"count": len(visits), "visits": visits}


@router.post("/visits", status_code=201, summary="A company visits: evaluate and send")
def record_visit(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    stream: IntentStream = StreamDep,
) -> dict[str, Any]:
    """The researched automation, in one call.

    "A company visit matches a saved Segment -> the workflow's conditions are
    evaluated -> a JSON payload is built -> it is POSTed to your URL."

    The response carries one line per workflow, including the ones that sent
    nothing, because "it was paused", "you already have this lead" and "your
    Segment did not match" are the three answers an operator most needs and
    none of them can be reconstructed from a row that was never written.
    """
    return stream.record_visit(
        payload, room_id=room_id, actor=actor, source=_source("POST", "/visits")
    )


@router.get("/visits/{visit_id}", summary="One visit and every outcome it caused")
def read_visit(visit_id: str, stream: IntentStream = StreamDep) -> dict[str, Any]:
    """One visit, with the per-workflow outcomes it produced."""
    return stream.read_visit(visit_id)


# --------------------------------------------------------------------------- #
# Webhook workflows
# --------------------------------------------------------------------------- #


@router.get("/workflows", summary="Webhook workflows")
def list_workflows(
    room_id: str | None = Query(default=None),
    include_inactive: bool = Query(default=True),
    stream: IntentStream = StreamDep,
) -> dict[str, Any]:
    """Every workflow, with a masked token and never the token itself.

    The generated token appears in the create response and behind
    ``POST /workflows/{id}/token``. A secret readable out of a list is an
    accident waiting to happen, and a list is what a page renders without
    anybody deciding to show a secret.
    """
    workflows = stream.list_workflows(room_id=room_id, include_inactive=include_inactive)
    return {"count": len(workflows), "workflows": workflows}


@router.post("/workflows", status_code=201, summary="Create a webhook workflow")
def create_workflow(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    stream: IntentStream = StreamDep,
) -> dict[str, Any]:
    """Steps 3 to 6 of the researched flow, in the order it describes them.

    Name, destination URL, conditions from saved Segments, once-or-updates,
    Company-or-Company-plus-Contacts, and the generated token - validated in that
    order and written only once all of them hold, so a bad URL leaves no
    workflow behind.

    ``sendMode='updates'`` is the researched update promise: "you will receive the
    same lead with updated activity data if that lead visits your webpage again".
    """
    return stream.create_workflow(
        payload, room_id=room_id, actor=actor, source=_source("POST", "/workflows")
    )


@router.get("/workflows/{workflow_id}", summary="One workflow, in detail")
def read_workflow(workflow_id: str, stream: IntentStream = StreamDep) -> dict[str, Any]:
    """One workflow, with its Segments, its recent deliveries, and every lead it has sent."""
    return stream.read_workflow(workflow_id)


@router.patch("/workflows/{workflow_id}", summary="Pause, retarget, or re-scope a workflow")
def update_workflow(
    workflow_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    stream: IntentStream = StreamDep,
) -> dict[str, Any]:
    """Patch a workflow. ``{"active": false}`` is the researched "save changes" pause.

    A pause is a flag rather than a delete, because a rep who misconfigured a
    destination should not have to retype the workflow to stop the traffic - and
    because a paused workflow still records why it sent nothing.
    """
    return stream.update_workflow(
        workflow_id, payload, actor=actor, source=_source("PATCH", f"/workflows/{workflow_id}")
    )


@router.delete("/workflows/{workflow_id}", summary="Delete a workflow")
def delete_workflow(
    workflow_id: str,
    actor: str | None = Query(default=None),
    stream: IntentStream = StreamDep,
) -> Response:
    """Soft-delete a workflow, so the delivery log outlives it.

    204. The rows it produced keep naming it, and opening one still works -
    which is the opposite of what a hard delete would do to the audit trail this
    product is built on.
    """
    stream.delete_workflow(
        workflow_id, actor=actor, source=_source("DELETE", f"/workflows/{workflow_id}")
    )
    return Response(status_code=204)


@router.post("/workflows/{workflow_id}/token", summary="Show the generated token again")
def reveal_token(workflow_id: str, stream: IntentStream = StreamDep) -> dict[str, Any]:
    """ "you have a token to use in your service or tool".

    Deliberately writes nothing, so there is no audit row: this product audits
    mutations, and a signing secret readable out of the audit log on every page
    view of the workflows page would undo the masking everywhere else.
    """
    return stream.reveal_token(workflow_id)


@router.get("/workflows/{workflow_id}/preview", summary="The exact payload for a lead")
def preview_workflow(
    workflow_id: str,
    lead_id: str = Query(description="the company lead to build the body for"),
    stream: IntentStream = StreamDep,
) -> dict[str, Any]:
    """The JSON that would be POSTed for this lead right now, and whether it would be.

    A read with no side effect, so nothing is sent. Step 5 is "choose the output
    of data you want to be sent to your URL", and this is the only way to see
    that choice - including which contacts the keyword and required-field filters
    keep - before a company visits.
    """
    return stream.preview(workflow_id, lead_id=lead_id)


# --------------------------------------------------------------------------- #
# Deliveries
# --------------------------------------------------------------------------- #


@router.get("/deliveries", summary="What was sent, and what was not")
def list_deliveries(
    room_id: str | None = Query(default=None),
    workflow_id: str | None = Query(default=None),
    lead_id: str | None = Query(default=None),
    state: str | None = Query(default=None, description="delivered | failed | skipped"),
    skip_reason: str | None = Query(
        default=None, description="already_sent | workflow_inactive | segment_not_matched"
    ),
    where: str | None = Query(default=None, description='JSON object or "k=v,k2=v2"'),
    limit: int = Query(default=100, ge=1, le=1000),
    stream: IntentStream = StreamDep,
) -> dict[str, Any]:
    """Every decision this workflow made, one row each.

    Filter on ``state`` to separate the two problems an operator is chasing: a
    ``failed`` row is a destination that refused or could not be reached, and a
    ``skipped`` row is this product deliberately not sending - with the reason.
    """
    deliveries = stream.list_deliveries(
        room_id=room_id,
        workflow_id=workflow_id,
        lead_id=lead_id,
        state=state,
        skip_reason=skip_reason,
        where=_filters(where),
        limit=limit,
    )
    summary: dict[str, int] = {}
    for delivery in deliveries:
        summary[str(delivery["state"])] = summary.get(str(delivery["state"]), 0) + 1
    return {"count": len(deliveries), "summary": summary, "deliveries": deliveries}


@router.get("/deliveries/{delivery_id}", summary="One delivery, with every attempt")
def read_delivery(delivery_id: str, stream: IntentStream = StreamDep) -> dict[str, Any]:
    """One delivery row.

    ``attemptLog`` holds every attempt rather than a summary of them, so the
    reason a resend happened is still readable long after the request that made
    it has gone.
    """
    return stream.read_delivery(delivery_id)


@router.post("/deliveries/{delivery_id}/resend", summary="Try a failed delivery again")
def resend_delivery(
    delivery_id: str,
    actor: str | None = Query(default=None),
    stream: IntentStream = StreamDep,
) -> dict[str, Any]:
    """Re-attempt one failed delivery, in place, and append the attempt to its log.

    This workflow's sources describe no retry policy, so this is the only way a
    failed POST is tried again, and it is a person asking rather than a schedule
    firing. Refuses a ``skipped`` delivery (a researched rule said not to send)
    and refuses a ``delivered`` one (resending duplicates it at the destination).
    """
    return stream.resend(
        delivery_id, actor=actor, source=_source("POST", f"/deliveries/{delivery_id}/resend")
    )


# --------------------------------------------------------------------------- #
# Summary
# --------------------------------------------------------------------------- #


@router.get("/summary", summary="Counts for the whole account")
def summary(stream: IntentStream = StreamDep) -> dict[str, Any]:
    """Everything above the fold, unscoped."""
    return stream.summary()


@router.get("/fields", summary="The JSON paths actually in use")
def fields(stream: IntentStream = StreamDep) -> dict[str, Any]:
    """Discovery, per collection.

    A team adding a field to a lead can put it in a Segment the same day; this is
    how they find out what is already there.
    """
    return stream.fields()


@router.get("/rooms/{room_id}/summary", summary="Counts for one room's stream")
def room_summary(room_id: str, stream: IntentStream = StreamDep) -> dict[str, Any]:
    """Everything above the fold for one room.

    Scoped to exactly the rows the room-scoped lists return, so the tiles and the
    tables cannot disagree.
    """
    return stream.room_summary(room_id)


@router.get("/rooms/{room_id}/leads", summary="One room's company leads")
def room_leads(
    room_id: str,
    where: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    stream: IntentStream = StreamDep,
) -> dict[str, Any]:
    """Leads identified against one room."""
    leads = stream.list_leads(room_id=room_id, limit=limit, where=_filters(where))
    return {"roomId": room_id, "count": len(leads), "leads": leads}


@router.get("/rooms/{room_id}/workflows", summary="One room's workflows")
def room_workflows(
    room_id: str,
    include_inactive: bool = Query(default=True),
    stream: IntentStream = StreamDep,
) -> dict[str, Any]:
    """Workflows authored against one room.

    Scoped by the room the workflow belongs to, not by the room it sends. A
    workflow whose conditions name a ``roomId`` narrows what it sends, and that
    narrowing is visible on the workflow itself - this route is about grouping,
    not about filtering.
    """
    workflows = stream.list_workflows(room_id=room_id, include_inactive=include_inactive)
    return {"roomId": room_id, "count": len(workflows), "workflows": workflows}


@router.get("/rooms/{room_id}/visits", summary="One room's company visits")
def room_visits(
    room_id: str,
    matched: bool | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    stream: IntentStream = StreamDep,
) -> dict[str, Any]:
    """Visits against one room, including the ones that matched nothing."""
    visits = stream.list_visits(room_id=room_id, matched=matched, limit=limit)
    return {"roomId": room_id, "count": len(visits), "visits": visits}


@router.get("/rooms/{room_id}/deliveries", summary="One room's deliveries")
def room_deliveries(
    room_id: str,
    state: str | None = Query(default=None),
    workflow_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    stream: IntentStream = StreamDep,
) -> dict[str, Any]:
    """Deliveries for one room, with the state counts for exactly those rows."""
    deliveries = stream.list_deliveries(
        room_id=room_id, state=state, workflow_id=workflow_id, limit=limit
    )
    summary: dict[str, int] = {}
    for delivery in deliveries:
        summary[str(delivery["state"])] = summary.get(str(delivery["state"]), 0) + 1
    return {
        "roomId": room_id,
        "count": len(deliveries),
        "summary": summary,
        "deliveries": deliveries,
    }


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #
#
# The brief is explicit that demo data containing only success teaches a reviewer
# nothing, and that the features already on main seed a declined room, a failed
# delivery, a retried one, and a pending approval. This one seeds the states
# *this* research says matter:
#
# * company leads across four rooms, with the activity data a Segment is matched
#   against, and contacts at each - the research's "Albacross company leads +
#   activity data" and "contact data at the identified company";
# * a saved Segment per research-relevant grouping, including one that matches
#   nobody, because a Segment that matches nothing is a state an operator has to
#   be able to see;
# * a **once** workflow that has already sent a lead, so the log shows the second
#   visit being skipped with ``already_sent`` - the researched choice made visible
#   rather than asserted;
# * an **updates** workflow with the same lead sent twice, so the second payload
#   carries refreshed activity data and a higher update count - the other half of
#   the same sentence;
# * a **company + contacts** workflow whose keyword and required-field filters
#   exclude some contacts, so the delivery records ``contactsConsidered`` against
#   ``contactsIncluded`` and the exclusions with their reasons;
# * a **paused** workflow, which is the state that produces a
#   ``workflow_inactive`` skip;
# * a destination that answers ``500``, so the log has a row that needs a person
#   and whose ``retryable`` is true - the only failure classification a re-attempt
#   could fix;
# * a destination retired with a ``404``, so the log also has the row no re-attempt
#   will ever fix, and a lead whose only delivery failed, so ``once`` does not
#   silently lose it;
# * a room with no matching Segment at all, so a visit there matched nothing and
#   the reviewer can see the third skip reason too.

DEMO_HEALTHY_TARGET = "https://hooks.example/intent-warehouse"
DEMO_SHEETS_TARGET = "https://hooks.example/google-sheets-leads"
DEMO_TEAMS_TARGET = "https://hooks.example/microsoft-teams-people"
DEMO_SLACK_TARGET = "https://hooks.example/slack-intent"
DEMO_BROKEN_TARGET = "https://hooks.example/crm-bridge"
DEMO_FLAKY_TARGET = "https://hooks.example/flaky-intent-bridge"
DEMO_RETIRED_TARGET = "https://hooks.example/retired-intent-hook"

DEMO_SEGMENTS = (
    (
        "Enterprise software",
        "Software companies over 1000 employees, in market.",
        "any",
        [
            {"path": "industry", "operator": "eq", "value": "Software"},
            {"path": "employees", "operator": "gte", "value": 1000},
        ],
    ),
    (
        "In-market, EMEA",
        "Seen a page recently, in EMEA or APAC. The research's 'in-market' half.",
        "all",
        [
            {"path": "lastVisitAt", "operator": "exists", "value": None},
            {"path": "region", "operator": "in", "value": ["EMEA", "APAC"]},
        ],
    ),
    (
        "Pricing-page readers",
        "Read the pricing page - the closest thing this research gets to a page filter.",
        "any",
        [{"path": "pagesViewed", "operator": "contains", "value": "Pricing"}],
    ),
    (
        "Never matches yet",
        "A Segment nobody is in. Seeded on purpose: a Segment that matches nothing is "
        "a state an operator has to be able to see, and it is the only way the "
        "'segment_not_matched' row gets demonstrated.",
        "any",
        [{"path": "industry", "operator": "eq", "value": "Wholesale distribution"}],
    ),
)

DEMO_LEADS = (
    {
        "name": "Northwind Traders",
        "domain": "northwind.example",
        "industry": "Software",
        "region": "EMEA",
        "country": "GB",
        "employees": 4200,
        "firmographics": {"hiringSignal": "expanding", "fundingStage": "series-c"},
        "pagesViewed": ["Enterprise Overview Deck", "Pricing One-Pager", "API Integration Guide"],
        "contacts": (
            ("A. Buyer", "VP Engineering", "Engineering", "a.buyer@northwind.example", "vp"),
            (
                "B. Buyer",
                "Director of Security",
                "Security",
                "b.buyer@northwind.example",
                "director",
            ),
            # No keyword hit, so this one is excluded by the filter and the
            # delivery says so.
            (
                "R. Researcher",
                "Market Analyst",
                "Strategy",
                "research@northwind.example",
                "analyst",
            ),
        ),
    },
    {
        "name": "Contoso Health",
        "domain": "contoso.example",
        "industry": "Healthcare",
        "region": "EMEA",
        "country": "DE",
        "employees": 900,
        "firmographics": {"hiringSignal": "steady", "fundingStage": "private"},
        "pagesViewed": ["Security & Compliance Pack", "Implementation Roadmap"],
        "contacts": (
            (
                "Procurement Desk",
                "Procurement Manager",
                "Procurement",
                "procurement@contoso.example",
                "manager",
            ),
            (
                "P. Clinician",
                "Clinical Operations Lead",
                "Operations",
                "clinician@contoso.example",
                "lead",
            ),
        ),
    },
    {
        "name": "Fabrikam Logistics",
        "domain": "fabrikam.example",
        "industry": "Logistics",
        "region": "APAC",
        "country": "SG",
        "employees": 260,
        "firmographics": {"hiringSignal": "expanding"},
        "pagesViewed": ["Contract Draft", "Customer Reference — Northwind"],
        "contacts": (
            ("Ops Lead", "Head of Operations", "Operations", "ops@fabrikam.example", "head"),
        ),
    },
    {
        "name": "Adventure Works",
        "domain": "adventure.example",
        "industry": "Retail",
        "region": "AMER",
        "country": "US",
        "employees": 55,
        "firmographics": {},
        "pagesViewed": ["Implementation Roadmap"],
        "contacts": (
            # Excluded by both filters at once: no keyword, and no email.
            ("Unattributed Person", "", "", "", ""),
            # Excluded by the required-field filter alone: the keyword matches,
            # the email does not exist. Two exclusion reasons, one row each.
            ("S. Intern", "Security Intern", "Security", "", "intern"),
        ),
    },
    {
        # The one company no saved Segment selects: not software, not over 1000
        # staff, not in EMEA or APAC, and it has never read a pricing page. Seeded
        # so the demo holds a visit that matched *nothing*, which is the state an
        # operator needs in order to work out why.
        "name": "Litware Financial",
        "domain": "litware.example",
        "industry": "Banking",
        "region": "AMER",
        "country": "CA",
        "employees": 120,
        "firmographics": {"hiringSignal": "flat"},
        "pagesViewed": ["Implementation Roadmap"],
        "contacts": (("T. Analyst", "Risk Analyst", "Risk", "analyst@litware.example", "analyst"),),
    },
)

DEMO_WORKFLOWS = (
    {
        "key": "warehouse",
        "name": "Enterprise leads to the warehouse",
        "url": DEMO_HEALTHY_TARGET,
        "sendMode": "once",
        "payload": "company",
        "segments": ("Enterprise software",),
        "description": "The researched default: a lead, once, Company only.",
        "active": True,
    },
    {
        "key": "sheets",
        "name": "In-market leads to Google Sheets",
        "url": DEMO_SHEETS_TARGET,
        "sendMode": "updates",
        "payload": "company",
        "segments": ("In-market, EMEA",),
        "description": "'Send updates as well': the same lead, re-sent with refreshed activity data.",
        "active": True,
    },
    {
        "key": "teams",
        "name": "People at pricing readers to Microsoft Teams",
        "url": DEMO_TEAMS_TARGET,
        "sendMode": "updates",
        "payload": "company_contacts",
        "segments": ("Pricing-page readers",),
        "contactFilter": {"keywords": ["engineering", "security"], "requiredFields": ["email"]},
        "description": (
            "Company + Contacts, filtered on 'Keywords' and required fields, the way the "
            "research describes. Contoso and Adventure Works exercise the case where the "
            "filter keeps nobody and the company is sent anyway."
        ),
        "active": True,
    },
    {
        "key": "paused",
        "name": "Slack intent pings (paused)",
        "url": DEMO_SLACK_TARGET,
        "sendMode": "once",
        "payload": "company",
        "segments": ("Enterprise software", "In-market, EMEA"),
        "description": "Paused during the security review. Still records why it sent nothing.",
        "active": False,
    },
    {
        "key": "broken",
        "name": "CRM bridge (answering 500)",
        "url": DEMO_BROKEN_TARGET,
        "sendMode": "updates",
        "payload": "company",
        "segments": ("In-market, EMEA",),
        "description": "The destination is down. Every row is retryable, and needs a person.",
        "active": True,
    },
    {
        "key": "flaky",
        "name": "Flaky bridge, re-attempted by hand",
        "url": DEMO_FLAKY_TARGET,
        "sendMode": "once",
        "payload": "company",
        "segments": ("Enterprise software",),
        "description": (
            "Refuses twice, then accepts. Seeded so the demo holds a delivery whose attempt "
            "log has more than one entry - a retry that worked, next to a retry that cannot."
        ),
        "active": True,
    },
    {
        "key": "retired",
        "name": "Retired enrichment hook",
        "url": DEMO_RETIRED_TARGET,
        "sendMode": "once",
        "payload": "company",
        "segments": ("Enterprise software",),
        "description": (
            "Answers 404, which no re-attempt fixes. Because it is a once-only workflow whose "
            "delivery never succeeded, every later visit tries again - the researched 'send a "
            "lead once' rule is about what the destination has seen, not about what was tried."
        ),
        "active": True,
    },
    {
        "key": "orphan",
        "name": "Wholesale segment to the warehouse",
        "url": DEMO_HEALTHY_TARGET,
        "sendMode": "once",
        "payload": "company",
        "segments": ("Never matches yet",),
        "description": "Its Segment selects nobody, so every visit records a segment_not_matched row.",
        "active": True,
    },
)


class DemoTransport:
    """A scripted transport, so seeding the demo never opens a socket.

    A real :class:`~dsr.intent_stream.delivery.UrllibTransport` would try to POST
    to ``hooks.example`` from ``backend/seed.py``. This one answers from a fixed
    script, which also makes the demo's rows deterministic rather than dependent
    on what a hostname happens to answer today.

    The scripts are keyed on the URL and apply to every call, because that is the
    state these workflows are actually in: an endpoint that starts refusing after
    it was created is the common case, and a transport that could not produce it
    would mean this feature could never seed the states the research says the log
    exists to track. Nothing in this workflow's research verifies a destination at
    creation, so nothing here has to fake a successful verification first.
    """

    #: Permanently broken and never broken, because the two failures need
    #: different answers: one wants a re-attempt, the other wants a person to stop
    #: pointing at a URL that answers 404.
    FAILING = {DEMO_BROKEN_TARGET: 500, DEMO_RETIRED_TARGET: 404}

    #: Refuses this many times, then accepts.
    FLAKY_FAILURES = 2

    def __init__(self) -> None:
        self.calls: dict[str, int] = {}

    def post(self, url: str, body: bytes, headers: Mapping[str, str], timeout: float):
        from dsr.intent_stream.delivery import DeliveryResult
        from dsr.intent_stream.vocabulary import RETRYABLE_STATUSES

        self.calls[url] = self.calls.get(url, 0) + 1
        status = self.FAILING.get(url)
        if status is not None:
            return DeliveryResult(
                ok=False,
                status=status,
                body="upstream unavailable" if status >= 500 else "no such hook",
                error=f"HTTP {status}",
                retryable=status in RETRYABLE_STATUSES,
                duration_ms=12.0,
                final_url=url,
            )
        if url == DEMO_FLAKY_TARGET and self.calls[url] <= self.FLAKY_FAILURES:
            return DeliveryResult(
                ok=False,
                status=503,
                body="warming up",
                error="HTTP 503",
                retryable=True,
                duration_ms=8.0,
                final_url=url,
            )
        return DeliveryResult(ok=True, status=202, body="accepted", duration_ms=9.0, final_url=url)


def seed(db, context: dict[str, Any]) -> str:
    """Seed the Segments, leads, contacts, workflows, visits, and deliveries this research makes matter.

    Everything is produced by running the real :class:`IntentStream` over
    :class:`DemoTransport`, so the demo cannot show a shape the workflow would
    not produce - same envelope, same states, same audit rows.
    """
    from dsr.store import RecordStore

    store = RecordStore(db)
    now = context.get("now")
    rooms: list[tuple[str, str]] = list(context.get("room_ids") or [])
    stream = IntentStream(store, transport=DemoTransport(), now=now)
    source = "seed"

    segment_ids: dict[str, str] = {}
    for name, description, match, rules in DEMO_SEGMENTS:
        created = stream.create_segment(
            {"name": name, "description": description, "match": match, "rules": rules},
            source=source,
            actor="dana",
        )
        segment_ids[name] = created["segment"]["id"]

    if not rooms:
        # Nothing to attach a visit to. The Segments are still worth having, and
        # the seeder prints what was skipped rather than failing the whole seed.
        return (
            f"{len(segment_ids)} segments, 0 leads, 0 workflows (no rooms to scope the stream to)"
        )

    lead_ids: list[str] = []
    for index, spec in enumerate(DEMO_LEADS):
        room_id, _account = rooms[index % len(rooms)]
        lead = stream.create_lead(
            {key: value for key, value in spec.items() if key != "contacts"},
            room_id=room_id,
            source=source,
            actor="dana",
        )["lead"]
        lead_ids.append(lead["id"])
        for name, title, department, email, seniority in spec["contacts"]:
            stream.create_contact(
                lead["id"],
                {
                    "name": name,
                    "title": title,
                    "department": department,
                    "email": email,
                    "seniority": seniority,
                },
                source=source,
                actor="dana",
            )

    workflow_ids: dict[str, str] = {}
    for spec in DEMO_WORKFLOWS:
        # Every workflow is authored against the first room, so the room-scoped
        # route has something to show, and the room-scoped *conditions* are left
        # unset on purpose: an unscoped workflow is what the research describes,
        # and a demo that only ever exercised the narrowed form would hide the
        # "visits from four rooms" case.
        payload: dict[str, Any] = {
            "name": spec["name"],
            "url": spec["url"],
            "sendMode": spec["sendMode"],
            "payload": spec["payload"],
            "conditions": {"segmentIds": [segment_ids[name] for name in spec["segments"]]},
            "description": spec["description"],
            "active": spec["active"],
        }
        if "contactFilter" in spec:
            payload["contactFilter"] = spec["contactFilter"]
        created = stream.create_workflow(payload, room_id=rooms[0][0], source=source, actor="dana")
        workflow_ids[spec["key"]] = created["workflow"]["id"]

    northwind, contoso, fabrikam, adventure, litware = lead_ids
    visits = 0

    def visit(lead_id: str, room_index: int, pages: list[str], seconds: int, country: str) -> None:
        nonlocal visits
        room_id, _account = rooms[room_index % len(rooms)]
        stream.record_visit(
            {
                "leadId": lead_id,
                "pagesViewed": pages,
                "secondsOnPage": seconds,
                "country": country,
                "device": "desktop",
            },
            room_id=room_id,
            source=source,
            actor="dana",
        )
        visits += 1

    # Northwind, twice, then a third time. Between them they demonstrate:
    # the researched 'only send a lead once' skip, the researched 'send updates as
    # well' re-send with a higher update count, a paused workflow recording why it
    # sent nothing, a flaky destination failing twice and then accepting when
    # re-attempted by hand, and a retired destination answering 404 on every visit
    # because a lead that never arrived has not been sent.
    visit(northwind, 0, ["Enterprise Overview Deck", "Pricing One-Pager"], 240, "GB")
    visit(northwind, 0, ["Pricing One-Pager", "Security & Compliance Pack"], 95, "GB")
    # Contoso: neither of its contacts matches the keyword filter, so the Teams
    # workflow sends the company with contacts: [] rather than skipping the send.
    visit(contoso, 1, ["Pricing One-Pager", "API Integration Guide"], 180, "DE")
    visit(contoso, 1, ["Pricing One-Pager"], 45, "DE")
    # Fabrikam: in-market for EMEA/APAC, but not a software company and never a
    # pricing reader. A visit that matches some workflows and not others.
    visit(fabrikam, 2, ["Contract Draft"], 60, "SG")
    # Adventure Works: an AMER retail company with two contacts the filter
    # excludes for two different reasons.
    visit(adventure, 3, ["Pricing One-Pager", "Implementation Roadmap"], 30, "US")
    # Litware: no saved Segment selects it, so every workflow records why it sent
    # nothing and the visit itself is reported as matching nothing at all.
    visit(litware, 0, ["Implementation Roadmap"], 20, "CA")

    # A third Northwind visit, after the flaky bridge has been re-attempted by
    # hand below, so the log holds a lead that is *now* marked sent and is skipped
    # for the researched reason.
    visit(northwind, 0, ["Pricing One-Pager"], 30, "GB")

    # Re-attempt the flaky delivery by hand. The transport refuses twice, so the
    # second visit also failed; this third attempt is the one that works, and it
    # leaves a delivery row with an attempt log of three.
    resent = 0
    for row in stream.list_deliveries(workflow_id=workflow_ids["flaky"], state="failed", limit=10):
        if not row.get("retryable"):
            continue
        stream.resend(row["id"], source=source, actor="dana")
        resent += 1
        break

    contacts = sum(len(spec["contacts"]) for spec in DEMO_LEADS)
    deliveries = len(stream.list_deliveries(limit=1000))
    return (
        f"{len(segment_ids)} segments, {len(lead_ids)} leads, {contacts} contacts, "
        f"{len(workflow_ids)} workflows, {visits} visits, {deliveries} delivery decisions, "
        f"{resent} resend"
    )
