"""The score arithmetic: what a contact's history is worth, and what would be written.

The research's data flow is the sentence this module implements, in order:

    "DSR engagement (Views, Clicks, Downloads, Interactions, MAP task activity) synced to
    HubSpot as Dock activity properties on the contact, evaluated against lead-score criteria
    + filters (date, link name, file name, task name), added/subtracted from the
    `HubSpot Score` contact property, then rep prioritisation and lifecycle automation."

**The score is recomputed from the whole history, not accumulated per event.** The
research says the rule "is continuous - every matching Dock activity event
re-evaluates the score without user action", and *re-evaluates* is the operative
word. Jev chose this over accumulation at audit
``jev-20261004T024258-6152-78859``, and the consequence is the one a seller can feel:
a replayed webhook moves nothing, and withdrawing a criterion removes its points at
the next event rather than leaving a claim on the contact that nothing backs.

The cost of that reading is stated rather than hidden: the work is proportional to
the contact's history, so a busy contact re-reads more rows. That is why every run
records which events contributed what, and why the ``score`` route reports the
per-criterion tally instead of a bare number.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from dsr.lead_score.criteria import matches, tally_reasons
from dsr.lead_score.vocabulary import (
    BATCH_KEY_FIELD,
    BUCKET_SIGN,
    CRM_PLAN,
    LIFECYCLE_CONSTRAINT,
    lifecycle_rank,
)


def contributions_for(
    criteria: Sequence[Mapping[str, Any]], events: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Every criterion against one contact's activity, and what each is worth.

    A criterion is worth its score for each event that matched it, signed by its
    bucket. That is "added/subtracted from the HubSpot Score contact property" read
    literally, with the total assembled from the history rather than incremented as
    events arrive.

    Rows for criteria that matched nothing are kept, with the reason tally, because
    the interesting fact about a criterion that moved nobody's score is usually which
    of five Dock properties its events actually were.
    """
    rows: list[dict[str, Any]] = []
    for index, criterion in enumerate(criteria):
        family = str(criterion.get("family") or "")
        sign = BUCKET_SIGN.get(str(criterion.get("bucket") or ""), 0)
        score = int(criterion.get("score") or 0)
        hits: list[Mapping[str, Any]] = []
        misses: list[dict[str, Any]] = []
        for event in events:
            verdict = matches(criterion, event)
            if verdict["matched"]:
                hits.append(event)
            else:
                misses.append({"reason": verdict["reason"], "detail": verdict["detail"]})
        hit_ids = [str(event["id"]) for event in hits if event.get("id")]
        points = sign * score * len(hits)
        rows.append(
            {
                "index": index,
                "criterion_id": str(criterion.get("id") or ""),
                "label": str(criterion.get("label") or ""),
                "family": family,
                "bucket": str(criterion.get("bucket") or ""),
                "score": score,
                "score_property": str(criterion.get("score_property") or ""),
                "property_resolved": bool(criterion.get("property_resolved", True)),
                "refinements": dict(criterion.get("refinements") or {}),
                "matched_count": len(hits),
                "miss_count": len(misses),
                "points": points,
                "hit_activity_ids": hit_ids,
                "reason": "matched" if hits else _miss_reason(misses),
                "detail": (
                    f"{len(hits)} of the contact's {len(events)} event(s) matched, worth "
                    f"{points} point(s)."
                    if hits
                    else (
                        f"none of the contact's {len(events)} event(s) matched. The reasons "
                        f"are {tally_reasons(misses)}"
                        if misses
                        else (
                            f"the contact has no activity in this room, so this {family} "
                            f"criterion cannot match"
                        )
                    )
                ),
            }
        )
    return rows


def _miss_reason(misses: Sequence[Mapping[str, Any]]) -> str:
    """The single reason a criterion matched nothing, for a response field.

    ``no_events`` when the contact has no activity at all, which is a different fact
    from "the events were all the wrong Dock property" and is worth distinguishing in
    a response a person is reading to find out why nothing scored.
    """
    if misses:
        return str(misses[0].get("reason") or "did_not_match")
    return "no_events"


def recompute(
    rows: Sequence[Mapping[str, Any]],
    *,
    previous: int,
    minimum_score: int | None,
    evaluated_at: str,
) -> dict[str, Any]:
    """The contact's score after this evaluation, with the movement it caused.

    ``minimum_score`` is the only clamp, and it is off unless a CRM organisation sets
    it. The sources never state a floor, so inventing one would silently make every
    negative criterion a no-op on a contact sitting at zero - a behaviour nobody asked
    for. The setting exists so the floor is a visible choice rather than a hidden one.
    """
    raw = sum(int(row.get("points") or 0) for row in rows)
    clamped = raw if minimum_score is None else max(int(minimum_score), raw)
    return {
        "raw_score": raw,
        "score": clamped,
        "from_score": int(previous),
        "to_score": clamped,
        "delta": clamped - int(previous),
        "changed": clamped != int(previous),
        "floor_applied": minimum_score is not None and clamped != raw,
        "minimum_score": minimum_score,
        "evaluated_at": evaluated_at,
        "criteria": [dict(row) for row in rows],
        "contributions": [
            {
                "criterion_id": row["criterion_id"],
                "label": row["label"],
                "family": row["family"],
                "bucket": row["bucket"],
                "points": row["points"],
                "events": row["matched_count"],
                "score_property": row["score_property"],
                "property_resolved": row["property_resolved"],
            }
            for row in rows
            if int(row.get("points") or 0) != 0
        ],
    }


def lifecycle_write_verdict(requested: Any, current: Any) -> dict[str, Any] | None:
    """Whether a ``lifecyclestage`` value a caller supplied may be written, and why not.

    The research quotes the constraint rather than paraphrasing it: "When you include
    the `lifecyclestage` property, you can only set the value *forward* in the stage
    order." A score write has no business moving a lifecycle stage at all, so this
    build never writes one; the function exists so a caller that supplies the property
    gets the refusal **with the reason attached** rather than a silently ignored
    field, which is the failure this codebase is built to avoid.

    Returns ``None`` when nothing was requested, and otherwise a row carrying
    ``accepted`` (always ``False`` here), the stage order comparison, and the quoted
    constraint.
    """
    if requested is None or (isinstance(requested, str) and not requested.strip()):
        return None
    wanted = lifecycle_rank(requested)
    have = lifecycle_rank(current)
    if wanted is None:
        comparison = f"{requested!r} is not one of the eight lifecycle stages"
    elif have is None:
        comparison = (
            f"the contact's current stage is {current!r}, which is not one of the eight "
            f"lifecycle stages, so no stage order can be read"
        )
    elif wanted <= have:
        comparison = (
            f"{requested!r} is at or behind {current!r}, and the value can only be set forward"
        )
    else:
        comparison = f"{requested!r} is ahead of {current!r}"
    return {
        "property": "lifecyclestage",
        "accepted": False,
        "requested": requested,
        "current": current,
        "comparison": comparison,
        "reason": LIFECYCLE_CONSTRAINT,
        "note": (
            "A score write carries the score and nothing else. This row is here so a caller "
            "that sends the property is told why it did not land."
        ),
    }


def url_path(endpoint: str) -> str:
    """The URL path out of a ``METHOD /path`` endpoint string.

    ``CRM_PLAN`` holds the vendor's own documented spelling, which includes the
    method, because that is the string a reader compares against the API guide. A
    recorded request needs the path on its own and already carries ``method`` beside
    it, so the two are split here rather than by rewriting the published vocabulary.
    """
    _, _, path = str(endpoint).partition(" ")
    return path


def crm_plan_for(
    *,
    contact: str,
    room_id: str,
    account: str,
    score: int,
    score_property: str,
    from_score: int,
    evaluated_at: str,
    lifecyclestage: Any = None,
    current_lifecycle_stage: Any = None,
) -> dict[str, Any]:
    """Exactly what the research's CRM endpoints would receive, marked unexecuted.

    Three requests, one per endpoint the research names:

    * ``write_contact`` - the single-contact PATCH that moves the score.
    * ``batch_upsert`` - the same write as a batch, keyed on the email, because the
      research's batch endpoint is an *upsert* and needs a key.
    * ``association_labels`` - the read that tells this build which association type
      ID links the contact to the deal, which is what makes the score attributable to
      an account.

    A ``lifecyclestage`` value is reported and dropped rather than written; see
    :func:`lifecycle_write_verdict`.
    """
    properties = {score_property: score}
    dropped = [
        row for row in [lifecycle_write_verdict(lifecyclestage, current_lifecycle_stage)] if row
    ]

    return {
        "executed": False,
        "contact": contact,
        "account": account,
        "room_id": room_id,
        "requests": [
            {
                "purpose": "write_contact",
                "endpoint": CRM_PLAN["write_contact"],
                "method": "PATCH",
                "path": url_path(CRM_PLAN["write_contact"]).replace("{contactId}", str(contact)),
                "body": {"properties": properties, "evaluated_at": evaluated_at},
            },
            {
                "purpose": "batch_upsert",
                "endpoint": CRM_PLAN["batch_upsert"],
                "method": "POST",
                "path": url_path(CRM_PLAN["batch_upsert"]),
                "body": {
                    "inputs": [
                        {
                            "id": contact,
                            "idProperty": BATCH_KEY_FIELD,
                            "properties": {BATCH_KEY_FIELD: contact, **properties},
                        }
                    ]
                },
            },
            {
                "purpose": "association_labels",
                "endpoint": CRM_PLAN["association_labels"],
                "method": "POST",
                "path": url_path(CRM_PLAN["association_labels"])
                .replace("{fromObjectType}", "contacts")
                .replace("{toObjectType}", "deals"),
                "body": {
                    "reads": "the association type ID that links a contact to a deal, so a "
                    "score can be attributed to the account it came from"
                },
            },
        ],
        "dropped_properties": dropped,
        "previous_score": from_score,
        "score_property": score_property,
    }


def batch_plan_for(contacts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The batch-upsert body for every contact a run touched.

    Recorded so a team with a real HubSpot client has one request rather than N, which
    is the whole reason the research names ``batch/upsert`` beside the single PATCH.
    """
    inputs = [
        {
            "id": str(row.get("contact") or ""),
            "idProperty": BATCH_KEY_FIELD,
            "properties": {
                BATCH_KEY_FIELD: str(row.get("contact") or ""),
                str(row.get("score_property") or "HubSpot Score"): int(row.get("score") or 0),
            },
        }
        for row in contacts
    ]
    return {
        "endpoint": CRM_PLAN["batch_upsert"],
        "method": "POST",
        "path": url_path(CRM_PLAN["batch_upsert"]),
        "body": {"inputs": inputs},
        "count": len(inputs),
        "executed": False,
    }
