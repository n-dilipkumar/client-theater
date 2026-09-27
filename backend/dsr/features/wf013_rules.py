"""WF-013: personalise room content with conditional rules.

Ported from ``feature/WF-013-personalise-room-content-with-conditional-rules``
onto the plugin host. The rules themselves live in :mod:`dsr.rules`, which the
port took over unchanged: it is pure, it is the researched specification made
executable, and it needs nothing from a database or a framework. This module is
the three things on the branch that were edits to shared files:

* the route table, which was an ``APIRouter`` the branch registered by hand in
  ``dsr/api.py`` and is now a router this feature owns and the host mounts;
* the error mapping, which was an ``@app.exception_handler(RuleError)`` block in
  ``dsr/api.py`` and is now an ``EXCEPTION_HANDLERS`` export the host attaches;
* the demo rows, which were an edit to ``backend/seed.py`` and are now a
  ``seed(db, context)`` hook the seeder calls.

Without that shape this workflow could not be merged without resolving a
conflict against the other eleven workflows, because all twelve appended to the
same three files.

Four deliberate departures from the branch, each required by the contract:

* **The prefix is ``/api/wf-013``.** The branch served ``/api/rules/catalog`` and
  ``/api/rooms/{room}/blocks`` from the shared app. Those paths are core
  vocabulary several other workflows want, and a feature that claims them is
  either a collision or dead code. The branch was never merged, so nothing
  external depended on the old paths.
* **Every write is handed the path this router actually serves.** The branch
  hard-coded ``source=f"PUT rule {block_id}"`` and friends inside its route
  functions, which is the defect the port brief calls out: the audit row stops
  naming a route anyone can call. ``source`` is now built from ``router.prefix``
  by :func:`_source`, so the two cannot drift.
* **The error mapping is a domain error, not an ``HTTPException``.** The branch
  wrapped every ``RuleError`` in a 422 by hand at each call site. Now
  ``RuleError`` propagates and the host-installed handler turns it into the
  response, which is what ``EXCEPTION_HANDLERS`` is for.
* **An unusable stored rule is contained per block.** See
  :func:`_evaluable_blocks`. Measured, not hypothetical: on this port's first
  cut, one block whose rule had reached storage unvalidated returned 422 for the
  entire room's ``preview`` and ``personalise``, so a single broken block cost
  every healthy block its decision. That is the opposite of S8, the behaviour the
  research is most explicit about, and it is fixed here rather than argued about.
* **Demo data lives here.** See :func:`seed`.

One thing the port could **not** carry over, and it is a finding rather than an
omission: the design doc's "no bypass" guarantee, that the generic
``/api/records/{collection}`` routes validate a ``rule`` key, was implemented on
the branch by editing ``dsr/api.py``. ``api.py`` is shared and the port must not
edit it, so the guarantee holds for this feature's own routes and for
:func:`guard_rule_payload` itself, but a rule can still be written unvalidated
through the generic record surface. :func:`guard_rule_payload` is exported so the
one-line change that closes it is available to whoever owns ``api.py``.
"""

from __future__ import annotations

from typing import Any, Mapping

from fastapi import APIRouter, Body, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from dsr import rules
from dsr.deps import StoreDep
from dsr.rules import RuleError
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-013-conditional-rules",
    "ticket": "WF-013",
    "name": "Personalise room content with conditional rules",
    "description": (
        "Reveal or hide a room's blocks from the variable values supplied when it is "
        "personalised: a Show-block-if builder, And/Or conditions, a read-only preview "
        "and a recorded generation-time decision."
    ),
    "nav": [{"id": "conditional-rules", "label": "Conditional rules"}],
}

router = APIRouter(prefix="/api/wf-013", tags=["wf013"])

# --------------------------------------------------------------------------- #
# Collections
# --------------------------------------------------------------------------- #
#
# Named here rather than repeated as string literals. No collection is a table
# and none of this is a schema: a rule is an ordinary JSON field inside a block's
# own ``data``, so a team can filter on ``rule.join`` through the dynamic index
# without a migration.

BLOCK_COLLECTION = "block"
VARIABLE_COLLECTION = "variable"
PERSONALISATION_COLLECTION = "personalisation"
SAVED_BLOCK_COLLECTION = "saved_block"


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
#
# ``RuleError`` is this workflow's own type, raised by :mod:`dsr.rules` and by
# nothing else in the product. That is what makes it safe to map here: the host
# refuses two features mapping the same type, and a feature that registered a
# handler for ``ValueError`` would intercept exceptions from the whole product.


def _rule_error(request: Request, exc: RuleError) -> JSONResponse:
    # 422: the request was well formed, the rule inside it is not one we accept.
    return JSONResponse(status_code=422, content={"error": "invalid_rule", "detail": str(exc)})


EXCEPTION_HANDLERS = {RuleError: _rule_error}


# --------------------------------------------------------------------------- #
# Guards
# --------------------------------------------------------------------------- #


def guard_rule_payload(
    payload: Mapping[str, Any],
    existing: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a ``rule`` key inside an arbitrary record payload.

    Returns the payload with ``rule`` replaced by its normalised form. Raises
    :class:`RuleError` if the rule is invalid, which the host-installed handler
    turns into a 422.

    This is the "no bypass" guard from the design doc, and it is exported rather
    than private for one reason: on the branch it was called from
    ``dsr/api.py`` so the generic record routes could not be used to slip an
    invalid rule past the dedicated endpoints. ``api.py`` is a shared file and a
    feature must not edit it, so on this branch the guard protects this feature's
    own routes only. Whichever change closes that gap needs this function, and it
    is here for it.
    """
    if not isinstance(payload, Mapping) or "rule" not in payload:
        return dict(payload)
    merged = {**(existing or {}), **dict(payload)}
    return {**payload, "rule": rules.validate_rule(payload.get("rule"), block=merged)}


def _source(verb: str, suffix: str = "") -> str:
    """The audit ``source`` for a write: the path this router actually serves.

    Built from ``router.prefix`` rather than written out, because hard rule 4 of
    the port brief is that a write's audit row must name the route that served
    it. Writing the string by hand is how the same defect shipped once already.
    """
    return f"{verb} {router.prefix}{suffix}"


# --------------------------------------------------------------------------- #
# Discovery (read-only)
# --------------------------------------------------------------------------- #


@router.get("/catalog", summary="The rule vocabulary")
def rule_catalog() -> dict[str, Any]:
    """Categories, modifiers, limits and the documented behaviours.

    Served as data so a client renders the builder from the server's vocabulary
    rather than a hard-coded list that can drift.
    """
    return rules.catalog()


@router.get("/variables", summary="Variables a condition can filter on")
def rule_variables(store: RecordStore = StoreDep) -> dict[str, Any]:
    """Every variable, account and CRM together.

    S12: CRM-imported variables "appear in the conditions list the same as the
    account Variable", so they come back in one flat list tagged with ``source``.
    Nothing here depends on a CRM being connected; a team adds records with
    ``source: "crm"`` whenever its integration lands.
    """
    records = store.list(VARIABLE_COLLECTION, limit=1000, order_by="created_at", descending=False)
    variables = []
    for record in records:
        data = record.get("data") or {}
        name = data.get("name")
        if not name:
            continue
        variables.append(
            {
                "id": record["id"],
                "name": name,
                "label": data.get("label") or name,
                "category": data.get("category") or "text",
                "source": data.get("source") or "account",
                "crm": data.get("crm"),
            }
        )
    return {
        "variables": variables,
        "count": len(variables),
        "sources": sorted({variable["source"] for variable in variables}),
    }


# --------------------------------------------------------------------------- #
# Blocks
# --------------------------------------------------------------------------- #


@router.post("/rooms/{room_id}/blocks", status_code=201, summary="Create a block")
def create_block(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Create a content block in a room.

    A convenience over the generic record route: it sets ``room_id`` and
    validates a rule if one is included in the same call. The payload stays
    entirely open, so a team can add block fields with no migration.
    """
    return store.create(
        BLOCK_COLLECTION,
        guard_rule_payload(payload),
        room_id=room_id,
        actor=actor,
        source=_source("POST", f"/rooms/{room_id}/blocks"),
    )


@router.get("/rooms/{room_id}/blocks", summary="Blocks in a room")
def list_blocks(room_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """Blocks in a room, in display order where a position was set."""
    records = _ordered_blocks(store, room_id)
    return {"room_id": room_id, "count": len(records), "blocks": records}


def _ordered_blocks(store: RecordStore, room_id: str) -> list[dict[str, Any]]:
    """Every block in a room, ordered by its open ``position`` field.

    ``position`` is read from the payload rather than a column, so a team that
    orders its blocks differently needs no migration.
    """
    records = store.list(BLOCK_COLLECTION, room_id=room_id, limit=1000)
    records.sort(
        key=lambda record: ((record.get("data") or {}).get("position", 0), record["created_at"])
    )
    return records


def _require_block(store: RecordStore, room_id: str, block_id: str) -> dict[str, Any]:
    record = store.get(block_id)
    if record is None or record["collection"] != BLOCK_COLLECTION:
        raise HTTPException(status_code=404, detail=f"block {block_id} not found")
    if record.get("room_id") != room_id:
        raise HTTPException(status_code=404, detail=f"block {block_id} is not in room {room_id}")
    return record


# --------------------------------------------------------------------------- #
# Rules
# --------------------------------------------------------------------------- #


@router.put("/rooms/{room_id}/blocks/{block_id}/rule", summary="Attach or replace a rule")
def put_rule(
    room_id: str,
    block_id: str,
    rule: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    expected_revision: int | None = Query(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Attach or replace a block's rule. Validated, then audited.

    S10 is enforced here: an Accept Block is rejected rather than quietly
    ignoring the rule, and the raised ``RuleError`` becomes a 422 through
    ``EXCEPTION_HANDLERS``.
    """
    block = _require_block(store, room_id, block_id)
    data = block.get("data") or {}
    normalised = rules.validate_rule(rule, block=data)
    return store.update(
        block_id,
        {"rule": normalised},
        actor=actor,
        source=_source("PUT", f"/rooms/{room_id}/blocks/{block_id}/rule"),
        expected_revision=expected_revision,
    )


@router.get("/rooms/{room_id}/blocks/{block_id}/rule", summary="Read a block's rule")
def get_rule(room_id: str, block_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """The block's rule plus the problems it would have if re-saved.

    The ``problems`` list matters because ``rule`` is a plain JSON field: a rule
    written before a modifier existed can be in storage without passing
    validation today, and a seller needs to be told rather than left with a rule
    that silently does nothing.
    """
    block = _require_block(store, room_id, block_id)
    data = block.get("data") or {}
    stored = data.get("rule")
    problems: list[str] = []
    if stored:
        try:
            rules.validate_rule(stored, block=data)
        except RuleError as exc:
            problems.append(str(exc))
    return {
        "block_id": block_id,
        "room_id": room_id,
        "rule": stored,
        "accepts_rules": rules.block_accepts_rules(data),
        "problems": problems,
    }


@router.delete("/rooms/{room_id}/blocks/{block_id}/rule", summary="Remove a rule")
def delete_rule(
    room_id: str,
    block_id: str,
    actor: str | None = Query(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Remove a block's rule. The block then always shows."""
    block = _require_block(store, room_id, block_id)
    if not (block.get("data") or {}).get("rule"):
        raise HTTPException(status_code=404, detail=f"block {block_id} has no rule to remove")
    return store.update(
        block_id,
        {"rule": None},
        actor=actor,
        source=_source("DELETE", f"/rooms/{room_id}/blocks/{block_id}/rule"),
    )


# --------------------------------------------------------------------------- #
# Evaluation: preview (no write) and personalise (write)
# --------------------------------------------------------------------------- #
#
# A2 in the design doc, chosen by a Jev gate
# (``jev-20260925T215835-29524-15211``, confidence 0.99): ``preview`` evaluates
# and stores nothing, ``personalise`` evaluates and records. Both return the
# identical decision shape, so a client renders either from one code path, and
# the repository's own contract that HTTP reads never reach the audit log stays
# true while a seller is still trying variable values.


def _decide(store: RecordStore, room_id: str, variables: Mapping[str, Any]) -> dict[str, Any]:
    blocks, problems = _evaluable_blocks(_ordered_blocks(store, room_id))
    decision = rules.personalise_blocks(blocks, variables)
    by_id = {entry["block_id"]: entry for entry in decision["blocks"]}
    for problem in problems:
        entry = by_id.get(problem["block_id"])
        if entry is not None:
            # A fifth reason, which §4 of the design doc does not enumerate.
            # Reporting `no_rule` here would be a lie: the block has a rule, and
            # it is unusable. The reason is the seller's only clue.
            entry["reason"] = "invalid_rule"
            entry["problems"] = [problem["problem"]]
    return {"room_id": room_id, "problems": problems, **decision}


def _evaluable_blocks(
    blocks: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Separate the blocks that can be evaluated from the ones that cannot.

    ``rule`` is a plain JSON field, so a rule can reach storage without having
    passed today's validation: through ``/api/records/block``, or from a store
    written before a modifier existed. ``evaluate_rule`` re-validates and raises,
    which without this function means one such block returns 422 for the *whole
    room* - every healthy block's decision lost to one broken one. That is
    measured, not hypothetical; see ``test_one_unusable_rule_does_not_cost_the_room_its_decision``.

    So an unusable rule is contained per block: the block is evaluated as though
    it had no rule, which shows it, and the problem is reported instead of raised.
    Failing open is the sourced behaviour of this ticket (S8: an incomplete rule
    is ignored and "the block of content will appear") and matches D5's reasoning
    that a bad value should hide a block, never break the page.

    The policy lives here, in the HTTP layer, rather than in :mod:`dsr.rules`,
    which stays the pure researched specification.
    """
    evaluable: list[dict[str, Any]] = []
    problems: list[dict[str, Any]] = []
    for block in blocks:
        data = block.get("data") or {}
        stored = data.get("rule")
        if not stored:
            evaluable.append(block)
            continue
        try:
            rules.validate_rule(stored, block=data)
        except RuleError as exc:
            problems.append(
                {
                    "block_id": block["id"],
                    "title": data.get("title"),
                    "problem": str(exc),
                }
            )
            evaluable.append({**block, "data": {**data, "rule": None}})
            continue
        evaluable.append(block)
    return evaluable, problems


@router.post("/rooms/{room_id}/preview", summary="Evaluate the rules without writing")
def preview_room(
    room_id: str,
    variables: dict[str, Any] = Body(default_factory=dict),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Evaluate every block and return the decision **without writing anything**.

    Repeated calls are free of audit noise, which is what makes it safe as a
    live preview while a seller is still choosing values.
    """
    # No room-existence gate on purpose: blocks and rooms are independent
    # collections, and previewing an empty room is a legitimate answer.
    return {"persisted": False, **_decide(store, room_id, variables)}


@router.post("/rooms/{room_id}/personalise", status_code=201, summary="Record the decision")
def personalise_room(
    room_id: str,
    variables: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Evaluate every block and **record** the generation-time decision.

    S13: rules are evaluated when variable values are supplied, not per viewer.
    The stored record keeps the values, the per-block booleans and the condition
    trace, so "which values produced which visible blocks" stays answerable.
    """
    decision = _decide(store, room_id, variables)
    record = store.create(
        PERSONALISATION_COLLECTION,
        {
            "variables": decision["variables"],
            "decision": decision["blocks"],
            "shown": decision["shown"],
            "hidden": decision["hidden"],
            "block_count": decision["block_count"],
            # Recorded, not just returned: a rule that would not validate is
            # something a seller has to come back to, and this record is the
            # replayable artifact of the decision.
            "problems": decision["problems"],
        },
        room_id=room_id,
        actor=actor,
        source=_source("POST", f"/rooms/{room_id}/personalise"),
    )
    return {"persisted": True, "personalisation": record, **decision}


@router.get("/rooms/{room_id}/personalisations", summary="Past decisions")
def list_personalisations(
    room_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Past generation-time decisions for a room, newest first."""
    records = store.list(PERSONALISATION_COLLECTION, room_id=room_id, limit=limit)
    return {"room_id": room_id, "count": len(records), "personalisations": records}


# --------------------------------------------------------------------------- #
# The documented rule-lossy boundary (S11)
# --------------------------------------------------------------------------- #


@router.post(
    "/rooms/{room_id}/blocks/{block_id}/save-to-library",
    status_code=201,
    summary="Copy a block to the library",
)
def save_block_to_library(
    room_id: str,
    block_id: str,
    actor: str | None = Query(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Copy a block into the library, dropping its rule **and saying so**.

    S11: "Saved Blocks cannot have rules setup and if a block with conditions on
    a template is saved to the Saved Block Library, it will not maintain the
    rule." The vendor does this silently. Here the loss is recorded on the stored
    record (``rules_dropped`` and ``dropped_rule``) and returned as a warning,
    because a silent unaudited drop is exactly what this project's audit
    guarantee exists to prevent.
    """
    block = _require_block(store, room_id, block_id)
    data = dict(block.get("data") or {})

    dropped_rule = data.pop("rule", None)
    payload: dict[str, Any] = {
        **data,
        "source_block_id": block_id,
        "source_room_id": room_id,
        "rules_dropped": dropped_rule is not None,
    }
    if dropped_rule is not None:
        payload["dropped_rule"] = dropped_rule

    record = store.create(
        SAVED_BLOCK_COLLECTION,
        payload,
        room_id=room_id,
        actor=actor,
        source=_source("POST", f"/rooms/{room_id}/blocks/{block_id}/save-to-library"),
    )

    response: dict[str, Any] = {"saved_block": record, "rules_dropped": dropped_rule is not None}
    if dropped_rule is not None:
        response["warning"] = (
            "This block had rules and they were not saved. A Saved Block cannot have "
            "rules, so the copy in the library will always be shown."
        )
        response["dropped_rule"] = dropped_rule
    return response


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #
#
# The branch added this to ``backend/seed.py``, which is a shared file that ten
# of the first twelve workflows rewrote purely to add their own rows. The seeder
# calls the hook below instead, so the demo travels with the feature that needs
# it. A feature whose page is empty in the demo is a feature nobody can review.


#: Variables a condition can filter on. ``source`` separates an account variable
#: from a CRM-imported one, because the researched product lists both in the
#: same conditions list and so does this one (S12).
SEED_VARIABLES: tuple[dict[str, Any], ...] = (
    {"name": "region", "label": "Region", "category": "text", "source": "account"},
    {"name": "seats", "label": "Seats", "category": "number", "source": "account"},
    {"name": "segment", "label": "Segment", "category": "text", "source": "crm", "crm": "salesforce"},
    {"name": "discount_code", "label": "Discount code", "category": "text", "source": "account"},
)


def _condition(variable: str, modifier: str, value: Any, category: str = "text") -> dict[str, Any]:
    return {"variable": variable, "category": category, "modifier": modifier, "value": value}


def seed(db, context: dict[str, Any]) -> str:
    """Make the core demo dataset readable as conditional content.

    Everything seeded here is chosen to make one researched behaviour visible in
    the UI without any interaction:

    * a rule that matches for the values the demo supplies, and one that does not;
    * an **And** rule joining a number and a text condition;
    * a block whose only condition has no variable, so the fail-open path (S8) is
      visible as a block that stays shown;
    * an **Accept Block**, the one type that cannot carry a rule (S10);
    * one recorded personalisation, so the history is not empty on a fresh
      database. Preview is deliberately not seeded: it writes nothing.

    Every write goes through the audited database, so running the seed twice
    produces a second complete set of audit rows rather than overwriting the
    first.
    """
    room_ids: list[tuple[str, str]] = context["room_ids"]
    if not room_ids:
        return "no rooms to attach to"

    for spec in SEED_VARIABLES:
        db.create(VARIABLE_COLLECTION, spec, actor="system", source="seed")

    block_plan: list[tuple[int, dict[str, Any]]] = [
        (0, {"title": "Welcome", "type": "text", "position": 1, "body": "Welcome to your room."}),
        (0, {"title": "Company overview", "type": "text", "position": 2, "body": "Who we are."}),
        (
            0,
            {
                "title": "AU & NZ pricing",
                "type": "pricing",
                "position": 3,
                "body": "Regional pricing for Australia and New Zealand.",
                "rule": {
                    "join": "or",
                    "conditions": [
                        _condition("region", "is", "Australia"),
                        _condition("region", "is", "New Zealand"),
                    ],
                },
            },
        ),
        (
            0,
            {
                "title": "Enterprise volume discount",
                "type": "pricing",
                "position": 4,
                "body": "Applies at 100 seats or more for enterprise segments.",
                "rule": {
                    "join": "and",
                    "conditions": [
                        _condition("seats", "is_more_than", 100, category="number"),
                        _condition("segment", "is", "enterprise"),
                    ],
                },
            },
        ),
        (1, {"title": "Security pack", "type": "pdf", "position": 1, "body": "Security documentation."}),
        (
            1,
            {
                "title": "Current promotion",
                "type": "banner",
                "position": 2,
                "body": "Promotion details.",
                # S8: an incomplete condition is ignored and the block appears, so
                # this block stays shown whatever the values. Seeded deliberately:
                # fail-open is a behaviour, not an error state, and the only way a
                # reviewer sees it is if it is on screen.
                "rule": {"join": "and", "conditions": [_condition("", "is", "")]},
            },
        ),
        (2, {"title": "Renewal terms", "type": "text", "position": 1, "body": "Renewal detail."}),
        (3, {"title": "Accept the proposal", "type": "accept", "position": 9, "body": "Accept and countersign."}),
    ]

    for index, spec in block_plan:
        room_id = room_ids[index % len(room_ids)][0]
        db.create(BLOCK_COLLECTION, spec, room_id=room_id, actor="dana", source="seed")

    # One recorded generation-time decision, so the history is not empty on a
    # fresh database. Evaluated through the same domain function the API uses, so
    # a seeded decision and an API decision cannot disagree.
    seeded_room = room_ids[0][0]
    seeded_blocks = db.list(BLOCK_COLLECTION, room_id=seeded_room, limit=100)
    decision = rules.personalise_blocks(seeded_blocks, {"region": "Australia", "seats": 40})
    db.create(
        PERSONALISATION_COLLECTION,
        {
            "variables": decision["variables"],
            "decision": decision["blocks"],
            "shown": decision["shown"],
            "hidden": decision["hidden"],
            "block_count": decision["block_count"],
        },
        room_id=seeded_room,
        actor="dana",
        source="seed",
    )

    return (
        f"{len(SEED_VARIABLES)} variables, {len(block_plan)} blocks, "
        f"1 personalisation ({len(decision['shown'])} shown, {len(decision['hidden'])} hidden)"
    )
