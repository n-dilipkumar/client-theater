"""WF-052: the published vocabulary, with the researched sentence for each term.

Everything here is either quoted from the research or named by it. A term the
research does not contain is marked as an inference in
:mod:`dsr.lead_qualification.inferences` rather than presented here as fact, so a
reviewer can tell the two apart without reading the code.

The three load-bearing terms
----------------------------

``schedulingAllowed``
    The gate signal. The research says it plainly: "``schedulingAllowed`` is the
    signal to gate on". A caller screens and ranks leads cheaply and opens the
    scheduler only for the ones this says yes to.

``routingLink``
    Returned but not used by this workflow. The research says the response carries
    "a ``routingLink`` the lead can be redirected to later", and the caller
    decides later whether to open a scheduler at all.

``routeId``
    "The ``routeId`` is returned but no slot list is computed and no session is
    consumed." It is therefore *derived*, never stored. See
    :func:`dsr.lead_qualification.rules.route_id_for`.
"""

from __future__ import annotations

from typing import Any

#: The ticket this vocabulary belongs to.
TICKET = "WF-052"

#: The researched call. Its distinguishing feature is the **absence** of one
#: field, so the path alone does not say which of the two access patterns this is.
EDGE_PATH = "https://fire.chilipiper.com/api/fire-edge/v1/org/concierge/routers/[routerSlug]/rest"

#: The two access patterns the research's table names side by side. This workflow
#: is the first one. The second belongs to the workflow that books a slot.
ACCESS_PATTERNS = {
    "return_a_booking_url": {
        "request": "form data (no interval)",
        "answer": "A routingLink to redirect the lead to",
        "consumes_a_session": False,
        "in_this_workflow": True,
    },
    "schedule_programmatically": {
        "request": "form data plus interval",
        "answer": "Available startTimes plus a routingId for the second call",
        "consumes_a_session": True,
        "in_this_workflow": False,
    },
}

#: The field whose presence switches workflow. "The difference is whether you pass
#: an ``interval``." A body that carries one is refused here rather than quietly
#: turning this into the booking flow.
INTERVAL_FIELD = "interval"

#: The three verdicts this build returns.
#:
#: ``qualified`` and ``not_scheduled`` are the research's two outcomes. The
#: research names its writeback paths ``Not Scheduled`` and ``Disqualified`` and
#: gives them the same consequence, so this build collapses both onto
#: ``not_scheduled`` and leaves the distinction to the rule's own name.
#: ``unroutable`` is an inference: the research asks the workflow to "Confirm a
#: lead is routable" without saying what an unroutable lead answers with.
VERDICTS = ("qualified", "not_scheduled", "unroutable")

#: The assignment types. The research shows exactly one, ``"user"``; ``unassigned``
#: is this build's name for the state where no assignee resolves.
ASSIGNMENT_TYPES = ("user", "unassigned")

#: Rule kinds. The research says rules are "CRM Ownership rules" or "Without
#: Ownership rules (CRM values or Data Field values)". This workflow reads CRM
#: values and Data Fields, and does not resolve an owner from a CRM record, which
#: is WF-053's job, so the ownership kind is absent here on purpose.
RULE_KINDS = ("data_field", "crm_field", "catch_all")

#: The CRM objects the research lists as readable values for a rule.
CRM_OBJECTS = (
    "lead",
    "contact",
    "account",
    "opportunity",
    "case",
    "campaign",
    "event",
)

#: Comparison operators. ``equals``, ``in``, ``exists`` and ``contains`` cover the
#: conditions the research describes ("CRM values or Data Field values"); ``gt``,
#: ``lt`` and ``not_equals`` are this build's additions, recorded as inferences.
OPERATORS = ("equals", "not_equals", "contains", "in", "exists", "gt", "lt", "gte", "lte")

#: The two collections this workflow reads a rule from. ``form`` is the webform
#: payload mapped to Chili Piper Data Fields; the rest are CRM objects.
RULE_SOURCES = ("form",) + CRM_OBJECTS

#: The default tenant host used to build a ``routingLink`` when a router declares
#: none. The research's sample link is
#: ``https://your-tenant.chilipiper.com/concierge-router/[routerSlug]/routing/[routeId]``.
DEFAULT_TENANT = "your-tenant.chilipiper.com"

#: Quoted research. Each is the sentence the term above is built on.
QUOTES = {
    "no_session": ("No routing session is consumed until scheduling occurs."),
    "distinguishing_field": "the difference is whether you pass an `interval`.",
    "gate_signal": "`schedulingAllowed` is the signal to gate on.",
    "summary": (
        "Confirm a lead is routable; See which user they would be assigned to; "
        "Decide whether to surface a scheduler. No routing session is consumed until "
        "scheduling occurs."
    ),
    "automations": (
        "none fire beyond rule evaluation - deliberately. No reminder, no assign, no "
        "calendar side effects."
    ),
    "catch_all": (
        "Each router must end with a 'Catch All' path to make sure you define the "
        "routing and acknowledge all inbound Leads."
    ),
    "availability_not_queried": (
        "same as #1 (webform, Data Fields, CRM objects, calendars) - but availability is "
        "not queried."
    ),
}

#: The two ways a caller may use the answer. The research names both and chooses
#: neither, so both are built: (a) the caller redirects the lead later, (b) the
#: backend writes the result into the CRM.
CALLER_PATHS = {
    "redirect_later": {
        "description": "The caller keeps the routingLink and decides later whether to "
        "offer a scheduler.",
        "writes_here": False,
    },
    "crm_writeback": {
        "description": "The caller records the qualification, which stages the CRM "
        "writeback for a Not Scheduled or Disqualified path.",
        "writes_here": True,
    },
}

#: What this workflow refuses to do, named rather than merely omitted.
GUARANTEES = {
    "consumes_no_session": True,
    "queries_no_availability": True,
    "computes_no_slots": True,
    "fires_no_automation": True,
    "assigns_nobody": True,
    "reads_no_calendar": True,
}


def published_vocabulary() -> dict[str, Any]:
    """The whole vocabulary, served as data.

    Returned rather than hard-coded at the route so a term added here reaches
    every client at once. A page renders its pickers from this, so a picker
    option cannot drift from the rule the validator enforces.
    """
    return {
        "ticket": TICKET,
        "edge_call": EDGE_PATH,
        "access_patterns": ACCESS_PATTERNS,
        "interval_field": INTERVAL_FIELD,
        "verdicts": list(VERDICTS),
        "assignment_types": list(ASSIGNMENT_TYPES),
        "rule_kinds": list(RULE_KINDS),
        "crm_objects": list(CRM_OBJECTS),
        "operators": list(OPERATORS),
        "rule_sources": list(RULE_SOURCES),
        "default_tenant": DEFAULT_TENANT,
        "caller_paths": CALLER_PATHS,
        "guarantees": dict(GUARANTEES),
        "quotes": dict(QUOTES),
    }
