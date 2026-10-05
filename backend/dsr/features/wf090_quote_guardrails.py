"""WF-090: enforce configuration and discount guardrails with quote rules.

The researched workflow, in full. An admin opens **Settings -> Objects -> Quotes ->
Rules**, creates a rule with a name, a definition in the vendor's small SQL-like DSL,
a **Rule outcome type** and a message to the quote creator, and switches it on. From
then on the rules evaluate continuously while a quote is edited: a **Show warning**
rule surfaces a message the seller may dismiss, and a **Block publish** rule stops the
quote reaching the buyer until it is fixed.

The domain is in :mod:`dsr.quoting_proposals.quote_guardrail_engine` and its grammar in
:mod:`dsr.quoting_proposals.quote_guardrail_rules`. What lives in this module is the
three things a workflow has to take out of shared files: the route table, the mapping
from domain errors to responses, and the demo data.

Hard rule 4: every write below is handed ``f"{router.prefix}..."``, built from
``router.prefix`` so the audit row and the route table cannot drift. A hardcoded URL
inside a domain function is a defect, and the same class of bug has shipped in this
codebase before: a feature's audit log kept naming a path the app had stopped serving.
``source`` is a *required* keyword on every writing function here, so omitting it is a
``TypeError`` at the call site rather than an untraceable row.

One handler for the whole error hierarchy.
:class:`~dsr.quoting_proposals.quote_guardrail_rules.GuardrailRefusal` is this workflow's
own type and the base of every refusal in it, and each refusal carries its own code and
status, so one handler answers 422 for a rule that cannot be read and 409 for a publish a
Block publish rule stopped, without a table saying which is which. ``RecordNotFound`` is
deliberately not claimed: the core app already maps it to 404, and two handlers for one
type is a collision the host refuses.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from dsr.deps import StoreDep
from dsr.quoting_proposals import (
    quote_guardrail_inferences as inferences,
    quote_guardrail_vocabulary as vocab,
)
from dsr.quoting_proposals.quote_guardrail_engine import QuoteGuardrailEngine
from dsr.quoting_proposals.quote_guardrail_rules import (
    GuardrailRefusal,
    describe_grammar,
)
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-090-quote-guardrails",
    "ticket": "WF-090",
    "name": "Enforce configuration and discount guardrails with quote rules",
    "description": (
        "Configure quote rules in the small DSL the vendor documents, evaluate them "
        "continuously while a quote is edited, warn the seller on a Show warning rule, "
        "and stop a Share or Publish on a Block publish rule."
    ),
    "nav": [{"id": "quote-guardrails", "label": "Quote guardrails"}],
}

router = APIRouter(prefix="/api/wf-090", tags=["WF-090"])


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _refusal(request: Request, exc: Any) -> JSONResponse:
    """A domain refusal, answered with the code and status it carries.

    ``errors`` rides along because a rule can be wrong in more than one way at once,
    and ``violations`` because a blocked publish names every rule that stopped it. A
    page puts the blocking rule's own message in front of the seller rather than a
    generic refusal.
    """
    return JSONResponse(
        status_code=exc.status,
        content={
            "error": exc.code,
            "detail": exc.detail,
            "status": exc.status,
            "reason": vocab.REASON_TEXTS.get(exc.code, exc.detail),
            "errors": getattr(exc, "errors", []),
            "violations": getattr(exc, "violations", []),
        },
    )


EXCEPTION_HANDLERS = {GuardrailRefusal: _refusal}


# --------------------------------------------------------------------------- #
# The service over the store
# --------------------------------------------------------------------------- #


def get_service(store: RecordStore = StoreDep) -> QuoteGuardrailEngine:
    return QuoteGuardrailEngine(store)


ServiceDep = Depends(get_service)


# --------------------------------------------------------------------------- #
# Reference data
# --------------------------------------------------------------------------- #


@router.get("/vocabulary", summary="Every published vocabulary and the grammar")
def vocabulary() -> dict[str, Any]:
    """Scopes, aggregates, quantifiers, outcomes, statuses, verdicts and reason codes.

    Served as data so a client renders its pickers and its reason messages from the
    server's vocabulary rather than from a list compiled into a page, and a value
    added here reaches every client at once. :func:`describe_grammar` rides beside it
    so the editor's examples and its two stated limits come from the same parser that
    refuses them.
    """
    catalogue = vocab.catalogue()
    # ``describe_grammar`` is the parser's own account of itself: its examples and its
    # two limits. Merged into the vocabulary's grammar block rather than placed beside
    # it, so an editor reads one object instead of two that can drift.
    catalogue["grammar"] = {**catalogue["grammar"], **describe_grammar()}
    return catalogue


@router.get("/inferences", summary="Every judgement call this workflow rests on")
def inference_report() -> dict[str, Any]:
    """What the research fixes, what it leaves open, and which reading was taken.

    The two Jev-validated decisions are served on their own under ``jev``, so a
    reviewer reads the question and its recorded confidence rather than a sentence
    claiming the choice was obvious.
    """
    return {"count": inferences.count(), "decisions": inferences.describe()}


# --------------------------------------------------------------------------- #
# Rules
# --------------------------------------------------------------------------- #


@router.get("/rules", summary="The configured quote rules")
def list_rules(
    room_id: str | None = Query(default=None),
    status: str | None = Query(default=None, description=f"One of {', '.join(vocab.STATUSES)}"),
    limit: int = Query(default=200, ge=1, le=1000),
    service: QuoteGuardrailEngine = ServiceDep,
) -> dict[str, Any]:
    """Every rule, oldest first, so the Manage tab shows them in the order they were added."""
    listed = service.list_rules(room_id=room_id, status=status, limit=limit)
    return {"room_id": room_id, "count": len(listed), "rules": listed}


@router.post("/rules", status_code=201, summary="Configure a quote rule")
def create_rule(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    service: QuoteGuardrailEngine = ServiceDep,
) -> dict[str, Any]:
    """Save a rule: a name, a definition, an outcome type and a message.

    The definition is parsed before the row is written, so a rule the engine can never
    read is refused here with the position of the fault rather than accepted and
    silently skipped at publish time. The response carries ``normalised`` beside the
    author's text, so the editor can show what the engine actually read.
    """
    return service.create_rule(
        payload, actor=actor, source=f"POST {router.prefix}/rules", room_id=room_id
    )


@router.post("/rules/validate", summary="Dry-run a rule definition")
def validate_rule(
    payload: dict[str, Any] = Body(default_factory=dict),
    definition: str | None = Query(default=None),
    service: QuoteGuardrailEngine = ServiceDep,
) -> dict[str, Any]:
    """The editor's dry run, and this build's answer to the researched sandbox.

    Never raises and always answers 200, because a typo in a definition is the editor
    working, not a failed request: ``valid: false`` with the refusal's code and the
    limit it hit is what the error panel beside the input renders.
    """
    body = dict(payload or {})
    return service.validate(
        definition if definition is not None else str(body.get(vocab.FIELD_DEFINITION) or "")
    )


@router.get("/rules/{rule_id}", summary="One quote rule")
def read_rule(rule_id: str, service: QuoteGuardrailEngine = ServiceDep) -> dict[str, Any]:
    return {"rule": service.rule(rule_id)}


@router.patch("/rules/{rule_id}", summary="Edit a rule, or turn its Status switch")
def patch_rule(
    rule_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    service: QuoteGuardrailEngine = ServiceDep,
) -> dict[str, Any]:
    """A merge patch, because the researched switch is one field of a saved rule.

    The merged rule is revalidated, so a patch that would leave an unreadable
    definition or an unknown outcome is refused rather than stored. Turning the switch
    off does not delete the rule: it reports ``guardrail_rule_disabled`` at the next
    evaluation, and the two have different fixes.
    """
    return service.patch_rule(
        rule_id, payload, actor=actor, source=f"PATCH {router.prefix}/rules/{rule_id}"
    )


@router.delete("/rules/{rule_id}", summary="Remove a quote rule")
def delete_rule(
    rule_id: str,
    actor: str | None = Query(default=None),
    service: QuoteGuardrailEngine = ServiceDep,
) -> dict[str, Any]:
    """Remove a rule. The evaluations it already produced are left alone and still explain themselves."""
    return service.delete_rule(
        rule_id, actor=actor, source=f"DELETE {router.prefix}/rules/{rule_id}"
    )


# --------------------------------------------------------------------------- #
# Evaluation and the publish gate
# --------------------------------------------------------------------------- #


@router.get("/quotes/{quote_id}/evaluate", summary="Evaluate the rules against a quote")
def evaluate_quote(
    quote_id: str,
    room_id: str | None = Query(default=None),
    service: QuoteGuardrailEngine = ServiceDep,
) -> dict[str, Any]:
    """Every verdict for one quote, split by what each one costs the seller.

    Writes nothing, because "rules evaluate continuously while the quote is edited"
    must not mean a row per keystroke. ``blocked`` is a boolean a page can bind to,
    and each verdict carries the rule's own message, the observed value, the threshold
    and a published reason code, so a seller is told what was read rather than only
    that something fired.
    """
    return service.evaluate_quote(quote_id, room_id=room_id)


@router.post("/quotes/{quote_id}/publish", summary="The gate: may this quote be published?")
def publish(
    quote_id: str,
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    service: QuoteGuardrailEngine = ServiceDep,
) -> dict[str, Any]:
    """The spec's hard stop, as the one route a publisher calls.

    A clean quote answers 200 with ``allowed: true`` and any warnings beside it. A
    violated **Block publish** rule refuses with 409 and the blocking rule's message.
    Both answers write a publish attempt and an evaluation, so "the product stopped
    this publish" is a row a reviewer can find rather than a gap.

    This route does not publish. WF-094 owns the publish; this is the gate in front of
    it, which is why a caller that reads ``allowed: true`` still has something else to
    call.
    """
    return service.publish(
        quote_id,
        actor=actor,
        source=f"POST {router.prefix}/quotes/{quote_id}/publish",
        room_id=room_id,
    )


@router.get("/quotes/{quote_id}/evaluations", summary="A quote's evaluation history")
def quote_evaluations(
    quote_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
    service: QuoteGuardrailEngine = ServiceDep,
) -> dict[str, Any]:
    listed = service.evaluations(quote_id, limit=limit)
    return {"quote_id": quote_id, "count": len(listed), "evaluations": listed}


@router.get("/evaluations", summary="Every recorded evaluation")
def list_evaluations(
    room_id: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    service: QuoteGuardrailEngine = ServiceDep,
) -> dict[str, Any]:
    listed = service.list_evaluations(room_id=room_id, limit=limit)
    return {"room_id": room_id, "count": len(listed), "evaluations": listed}


@router.get("/summary", summary="Rule counts and what the gate has done")
def room_summary(
    room_id: str | None = Query(default=None),
    service: QuoteGuardrailEngine = ServiceDep,
) -> dict[str, Any]:
    """Counts a reviewer acts on: which rules are on, and how the gate has answered."""
    return service.summary(room_id=room_id)


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #
#
# The branch added this to ``backend/seed.py``, which is a shared file that ten of
# the first twelve workflows rewrote purely to add their own rows. The seeder calls
# the hook below instead, so the demo travels with the feature that needs it.


def _demo_room(context: dict[str, Any]) -> str | None:
    """The first demo room id, whatever shape the seeder passes.

    The seeder passes ``(room_id, name)`` pairs on some paths and bare ids on others.
    A demo rule scoped to no room is global, which is a legitimate state, so a seed
    with no rooms still produces a working demo rather than nothing.
    """
    rooms = context.get("room_ids") or []
    if not rooms:
        return None
    first = rooms[0]
    if isinstance(first, (tuple, list)):
        return str(first[0])
    return str(first)


def seed(db, context: dict[str, Any]) -> str:
    """Seed the states the research says matter, not just the happy path.

    Everything is produced by driving the real
    :class:`~dsr.quoting_proposals.quote_guardrail_engine.QuoteGuardrailEngine` over
    the demo room, so the verdicts on screen are what this workflow actually computes
    rather than rows composed by hand. A seeded verdict and an API verdict cannot
    disagree.

    What is here, and why each is here:

    * a **Block publish** rule on the sum of line discounts, violated by one quote, so
      the hard stop is a row rather than a paragraph;
    * a **Block publish** rule using SOLD_TOGETHER, satisfied by a quote that sells all
      three bundled products;
    * an **INCOMPATIBLE** rule that fires on a quote mixing a legacy and a modern
      platform, so the second quantifier is visible as working;
    * a **Show warning** rule on a large quote, so the softer outcome is on screen
      beside the hard one;
    * a **disabled** rule, so the Status switch has an off state a reviewer can see;
    * a rule whose property the demo quote does not carry, so ``unverifiable`` is a real
      row instead of a claim.

    Every character of the returned string is encodable by cp1252. A single
    RIGHTWARDS ARROW in one recovered feature's return string broke the entire
    seeder on a Windows console.
    """
    from dsr.store import RecordStore

    room_id = _demo_room(context)
    base: Any = context.get("now")
    service = QuoteGuardrailEngine(RecordStore(db), now=(lambda: base) if base else None)
    source = "seed"
    actor = "dana"

    plans: tuple[dict[str, Any], ...] = (
        {
            "name": "Deep line discount needs sign off",
            vocab.FIELD_DEFINITION: "SUM([line_item.discount]) FROM line_item > 60",
            "outcome": vocab.OUTCOME_BLOCK,
            "message": "The total line discount is above 60 percent. Reduce it before publishing.",
            "status": vocab.STATUS_ENABLED,
        },
        {
            "name": "Enterprise bundle sold together needs sign off",
            vocab.FIELD_DEFINITION: (
                "SOLD_TOGETHER FROM line_item WHERE [hs_product_id] IN "
                '("ENT-LICENSE", "SUPPORT-ADDON", "TRAINING")'
            ),
            "outcome": vocab.OUTCOME_BLOCK,
            "message": (
                "This quote sells the enterprise license, support and training "
                "together. A manager signs off on the bundle."
            ),
            "status": vocab.STATUS_ENABLED,
        },
        {
            "name": "Do not mix legacy and modern",
            vocab.FIELD_DEFINITION: (
                'INCOMPATIBLE FROM line_item WHERE [platform] IN ("legacy", "modern")'
            ),
            "outcome": vocab.OUTCOME_BLOCK,
            "message": "This quote mixes a legacy and a modern platform line.",
            "status": vocab.STATUS_ENABLED,
        },
        {
            "name": "Large quote needs a second look",
            vocab.FIELD_DEFINITION: "[quote.hs_quote_amount] > 100000",
            "outcome": vocab.OUTCOME_WARNING,
            "message": "Quotes above 100,000 are usually reviewed by a manager.",
            "status": vocab.STATUS_ENABLED,
        },
        {
            "name": "Expiry must be set (switch off)",
            vocab.FIELD_DEFINITION: '[quote.hs_valid_until] = ""',
            "outcome": vocab.OUTCOME_WARNING,
            "message": "A quote should carry an expiry date.",
            "status": vocab.STATUS_DISABLED,
        },
        {
            "name": "Region must be one we serve",
            vocab.FIELD_DEFINITION: '[quote.region] = "unsupported"',
            "outcome": vocab.OUTCOME_WARNING,
            "message": "This region is not on the served list.",
            "status": vocab.STATUS_ENABLED,
        },
    )

    made = 0
    for plan in plans:
        service.create_rule(plan, actor=actor, source=source, room_id=room_id)
        made += 1

    quotes: tuple[dict[str, Any], ...] = (
        {
            "summary": "a discount block",
            "quote": {"name": "Northwind renewal", "hs_quote_amount": 48000, "segment": "renewal"},
            "items": [
                {"hs_product_id": "ENT-LICENSE", "platform": "modern", "discount": 40},
                {"hs_product_id": "SUPPORT-ADDON", "platform": "modern", "discount": 30},
            ],
        },
        {
            "summary": "a SOLD_TOGETHER block",
            "quote": {
                "name": "Halcyon expansion",
                "hs_quote_amount": 62000,
                "segment": "expansion",
            },
            "items": [
                {"hs_product_id": "ENT-LICENSE", "platform": "modern", "discount": 5},
                {"hs_product_id": "SUPPORT-ADDON", "platform": "modern", "discount": 5},
                {"hs_product_id": "TRAINING", "platform": "modern", "discount": 5},
            ],
        },
        {
            "summary": "an INCOMPATIBLE block",
            "quote": {
                "name": "Contoso migration",
                "hs_quote_amount": 30000,
                "segment": "migration",
            },
            "items": [
                {"hs_product_id": "ENT-LICENSE", "platform": "legacy", "discount": 5},
                {"hs_product_id": "SUPPORT-ADDON", "platform": "modern", "discount": 5},
            ],
        },
        {
            "summary": "publishable, with a large-quote warning",
            "quote": {"name": "Northwind pilot", "hs_quote_amount": 150000, "segment": "pilot"},
            "items": [
                {"hs_product_id": "ENT-LICENSE", "platform": "modern", "discount": 5},
                {"hs_product_id": "SUPPORT-ADDON", "platform": "modern", "discount": 5},
            ],
        },
    )

    blocked = 0
    published = 0
    effects: list[str] = []
    for plan in quotes:
        quote = service.store.create(
            vocab.SOURCE_QUOTES,
            {**plan["quote"], "hs_status": "draft", "creator": "dana"},
            room_id=room_id,
            actor=actor,
            source=source,
        )
        for item in plan["items"]:
            service.store.create(
                vocab.LINE_ITEMS,
                {"quote_id": quote["id"], **item},
                room_id=room_id,
                actor=actor,
                source=source,
            )
        outcome = service.evaluate_quote(quote["id"], room_id=room_id)
        service.record_evaluation(quote["id"], actor=actor, source=source, room_id=room_id)
        if outcome["blocked"]:
            blocked += 1
        else:
            published += 1
        effects.append(str(plan["summary"]))

    return (
        f"{made} quote rules in {room_id or 'every room'}, {len(quotes)} quotes "
        f"({'; '.join(effects)}), {blocked} blocked and {published} publishable"
    )
