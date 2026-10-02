"""Who the generated task is assigned to, and how that was decided.

The research is unusually explicit about this step, which is why most of this
module is a transcription rather than a decision:

    "For task assignment, we will use an object precedence order of User, Content,
    Person, Account."

and, for the Account row of the same table:

    "The most engaged Person on the Account in the Last 30 days (Highest Buyer
    Engagement Score) / If there is no engagement, relate the task to the last
    person whose most recent contact was with the Account Owner."

So: walk the four objects in order, and the first the signal carries decides which
object the task is related to. If that object is the Account, the person is found
by the two-step rule above, inside a 30-day window.

The one thing this module refuses to do is compute a Buyer Engagement Score.
The research's own gaps appendix records that "Salesloft references a 'Buyer
Engagement Score' as a tie-breaker for task assignment but does not document how
it is computed", so the score is *data supplied by the caller* and this module only
orders by it. Inventing a formula from this product's engagement records would be
the single most likely way to get this wrong quietly: a plausible-looking number
that is not the vendor's number.

Two smaller readings, both recorded in :mod:`dsr.plays.inferences`:

* **an unresolvable assignment is not an error.** The task is still created, and it
  says who it is for. Refusing to create it would lose the evidence that the
  automation fired.
* **ties are broken deterministically**, most recent engagement first, then person
  id. The research says "Highest Buyer Engagement Score" and nothing about a tie.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from dsr.plays.errors import DispatchError
from dsr.plays.vocabulary import (
    ASSIGNMENT_OBJECT,
    ASSIGNMENT_PRECEDENCE,
    ASSIGNMENT_RULE,
    ENGAGEMENT_WINDOW_DAYS,
)

#: Every rule :func:`resolve_assignment` can end on, and what it means. Published
#: so a client can render the reason without hard-coding strings, and so a test can
#: assert that every reason the resolver can return is written down.
RULES: dict[str, str] = {
    "user_precedence": (
        "the signal names a User, and User is first in the researched precedence order"
    ),
    "content_precedence": (
        "the signal names Email Content, and Content is second in the researched precedence order"
    ),
    "person_precedence": (
        "the signal names a Person, and Person is third in the researched precedence order"
    ),
    "account_engagement_score": (
        "the signal names an Account and no more specific object, so the task goes to the "
        "most engaged Person on that Account in the last "
        f"{ENGAGEMENT_WINDOW_DAYS} days, by highest Buyer Engagement Score"
    ),
    "account_last_contact": (
        "the Account has no engagement in the window, so the task is related to the last "
        "person whose most recent contact was with the Account Owner"
    ),
    "account_no_candidates": (
        "the Account was named but no candidate satisfies either half of the researched "
        "fallback, so the task is created and reported unassigned"
    ),
    "no_object": (
        "the signal carries none of the four assignment objects, so there is nothing to "
        "assign it to"
    ),
    "object_unresolved": (
        "the precedence named the object, but this product holds no record of it, so the "
        "task is created and reported unassigned"
    ),
}


def _parse(value: Any) -> datetime | None:
    """An ISO timestamp as an aware UTC datetime, or ``None`` if it is not one."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _present(attribution: Mapping[str, Any], key: str) -> str | None:
    value = attribution.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def window_start(now: datetime, days: int = ENGAGEMENT_WINDOW_DAYS) -> datetime:
    """The start of the researched engagement window."""
    return now - timedelta(days=days)


def resolve_assignment(
    attribution: Mapping[str, Any] | None,
    *,
    subjects: Mapping[str, Any] | None = None,
    candidates: Sequence[Mapping[str, Any]] = (),
    now: datetime | None = None,
) -> dict[str, Any]:
    """Decide which object a task is related to, and which seller it belongs to.

    ``attribution`` is the signal's attribution object, using WF-027's researched
    keys. ``subjects`` is a caller's map from an object id to whatever it knows
    about it - ``{"person_id": ..., "seller": ...}`` - because the Person, Account
    and Content records this precedence names live in the vendor's platform, not in
    a Digital Sales Room. Absent, the object is still reported and the task is
    reported unassigned rather than pointed at a guess.

    ``candidates`` is the Account's roster, used only by the Account branch. Each
    entry is ``{person_id, engagement_score, engaged_at, last_contact_at,
    contact_was_with_account_owner, seller}``.

    The return value always carries ``considered``: every one of the four objects
    with whether the signal named it and whether it decided. A reader can check the
    reasoning rather than trust it.
    """
    if attribution is not None and not isinstance(attribution, Mapping):
        raise DispatchError("attribution must be an object of Salesloft ids")
    attribution = attribution or {}
    subjects = subjects or {}
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)

    considered = [
        {
            "attribution": key,
            "object": ASSIGNMENT_OBJECT[key],
            "precedence": index,
            "present": _present(attribution, key) is not None,
            "decides": False,
        }
        for index, key in enumerate(ASSIGNMENT_PRECEDENCE)
    ]

    deciding: dict[str, Any] | None = None
    for entry in considered:
        value = _present(attribution, entry["attribution"])
        if value is None:
            continue
        entry["decides"] = True
        entry["named"] = value
        deciding = entry
        break

    if deciding is None:
        return {
            "assigned": False,
            "object": None,
            "attribution": None,
            "object_id": None,
            "person_id": None,
            "seller": None,
            "rule": "no_object",
            "reason": (
                "The signal names none of "
                f"{', '.join(ASSIGNMENT_OBJECT[key] for key in ASSIGNMENT_PRECEDENCE)}, "
                "which is the researched precedence order, so there is nothing to assign the "
                "task to. The task is "
                "still created: refusing to create it would lose the evidence that the Play "
                "fired."
            ),
            "considered": considered,
            "notes": [],
        }

    key = deciding["attribution"]
    object_id = deciding["named"]

    if key == "account_id":
        branch = _resolve_account(object_id, candidates, moment)
        notes = branch.pop("notes")
        subject = subjects.get(object_id) or {}
        return {
            "assigned": bool(branch.get("person_id")),
            "object": ASSIGNMENT_OBJECT[key],
            "attribution": key,
            "object_id": object_id,
            "person_id": branch.get("person_id"),
            "seller": subject.get("seller") or branch.get("seller"),
            "rule": branch["rule"],
            "reason": branch["reason"],
            "considered": considered,
            "notes": notes,
        }

    subject = subjects.get(object_id) or {}
    if not isinstance(subject, Mapping):
        subject = {}
    person_id = subject.get("person_id")
    # A User *is* the seller, so a signal naming one resolves with no further data
    # at all. Content and Person name objects whose owner this product does not
    # hold, which is why they need a `subjects` entry to become a named seller.
    seller = subject.get("seller") or (object_id if key == "user_guid" else None)
    assigned = person_id is not None or seller is not None
    if assigned:
        reason = (
            f"The signal names {ASSIGNMENT_OBJECT[key]} {object_id!r}, which is "
            f"{ASSIGNMENT_OBJECT[key]}'s place in the researched precedence order"
            + (
                ", and a User is the seller directly"
                if key == "user_guid"
                else f", and the caller supplied {'a person' if person_id else 'a seller'} for it"
            )
            + "."
        )
    else:
        reason = (
            f"The signal names {ASSIGNMENT_OBJECT[key]} {object_id!r}, so the task is "
            f"related to that {ASSIGNMENT_OBJECT[key].lower()}. This product holds no "
            "record of the object, so the task is created unassigned rather than pointed "
            "at a guess. Pass `subjects` with a person or a seller for that id to fill it "
            "in."
        )
    return {
        "assigned": assigned,
        "object": ASSIGNMENT_OBJECT[key],
        "attribution": key,
        "object_id": object_id,
        "person_id": person_id,
        "seller": seller,
        "rule": ASSIGNMENT_RULE[key],
        "reason": reason,
        "considered": considered,
        "notes": [],
    }


def _resolve_account(
    account_id: str, candidates: Sequence[Mapping[str, Any]], now: datetime
) -> dict[str, Any]:
    """The Account row of the researched assignment table, in its own words.

    "The most engaged Person on the Account in the Last 30 days (Highest Buyer
    Engagement Score) / If there is no engagement, relate the task to the last person
    whose most recent contact was with the Account Owner."
    """
    notes: list[dict[str, Any]] = []
    start = window_start(now)
    in_window: list[tuple[float, datetime, Mapping[str, Any]]] = []
    for candidate in candidates or ():
        if not isinstance(candidate, Mapping):
            notes.append(
                {
                    "code": "candidate_not_an_object",
                    "detail": f"ignored a candidate of type {type(candidate).__name__}",
                }
            )
            continue
        engaged = _parse(candidate.get("engaged_at") or candidate.get("last_engaged_at"))
        if engaged is None or engaged < start or engaged > now:
            continue
        score = candidate.get("engagement_score", candidate.get("buyer_engagement_score"))
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            notes.append(
                {
                    "code": "no_engagement_score",
                    "person_id": candidate.get("person_id"),
                    "detail": (
                        f"{candidate.get('person_id')!r} engaged on {engaged.isoformat()}, "
                        "inside the window, but carries no numeric engagement_score, so it "
                        "cannot be compared and was not considered. This is the documented "
                        "gap: the research names a Buyer Engagement Score and does not "
                        "publish how it is computed, so the score is supplied as data."
                    ),
                }
            )
            continue
        in_window.append((float(score), engaged, candidate))

    if in_window:
        # Highest score wins. Ties break on the more recent engagement, then on the
        # person id, so two runs of the same data resolve the same way - the
        # research says "Highest" and nothing about a tie.
        in_window.sort(key=lambda row: (-row[0], -row[1].timestamp(), str(row[2].get("person_id"))))
        score, engaged, winner = in_window[0]
        ties = [row for row in in_window if row[0] == score]
        if len(ties) > 1:
            notes.append(
                {
                    "code": "engagement_score_tie",
                    "detail": (
                        f"{len(ties)} people on this Account share the top score of "
                        f"{score:g}; the most recent engagement at "
                        f"{engaged.isoformat()} was taken, then the person id. The research "
                        'says "Highest Buyer Engagement Score" and states no tie-break.'
                    ),
                }
            )
        return {
            "rule": "account_engagement_score",
            "person_id": winner.get("person_id"),
            "seller": winner.get("seller"),
            "reason": (
                f"The signal names Account {account_id!r}, and {winner.get('person_id')!r} is "
                f"the most engaged Person on it inside the last {ENGAGEMENT_WINDOW_DAYS} "
                f"days, with a Buyer Engagement Score of {score:g} on "
                f"{engaged.isoformat()}. The research says the task goes to that person."
            ),
            "notes": notes,
        }

    last_contact: list[tuple[datetime, Mapping[str, Any]]] = []
    for candidate in candidates or ():
        if not isinstance(candidate, Mapping):
            continue
        if candidate.get("contact_was_with_account_owner") is not True:
            continue
        contacted = _parse(candidate.get("last_contact_at"))
        if contacted is None or contacted > now:
            continue
        last_contact.append((contacted, candidate))

    if last_contact:
        last_contact.sort(key=lambda row: (-row[0].timestamp(), str(row[1].get("person_id"))))
        contacted, winner = last_contact[0]
        return {
            "rule": "account_last_contact",
            "person_id": winner.get("person_id"),
            "seller": winner.get("seller"),
            "reason": (
                f"The signal names Account {account_id!r} and nobody on it engaged inside "
                f'the last {ENGAGEMENT_WINDOW_DAYS} days, so - "If there is no engagement, '
                "relate the task to the last person whose most recent contact was with the "
                f'Account Owner" - the task goes to {winner.get("person_id")!r}, last '
                f"contacted on {contacted.isoformat()}."
            ),
            "notes": notes,
        }

    return {
        "rule": "account_no_candidates",
        "person_id": None,
        "seller": None,
        "reason": (
            f"The signal names Account {account_id!r}, but no candidate person on it "
            f"engaged inside the last {ENGAGEMENT_WINDOW_DAYS} days and none records a "
            "most recent contact with the Account Owner, so both halves of the researched "
            "fallback come up empty. The task is created and reported unassigned."
            + (
                f" {len(notes)} candidate(s) were rejected on the way; see notes."
                if notes
                else " No candidate roster was supplied."
            )
        ),
        "notes": notes,
    }


def describe() -> dict[str, Any]:
    """The assignment rules, published for clients and for the page."""
    return {
        "precedence": [
            {
                "attribution": key,
                "object": ASSIGNMENT_OBJECT[key],
                "precedence": index,
            }
            for index, key in enumerate(ASSIGNMENT_PRECEDENCE)
        ],
        "engagement_window_days": ENGAGEMENT_WINDOW_DAYS,
        "account_fallback": [
            "The most engaged Person on the Account in the Last "
            f"{ENGAGEMENT_WINDOW_DAYS} days (Highest Buyer Engagement Score)",
            "If there is no engagement, relate the task to the last person whose most "
            "recent contact was with the Account Owner",
        ],
        "rules": RULES,
        "buyer_engagement_score": (
            "supplied, not computed. The research's gaps appendix records that the vendor "
            "references a Buyer Engagement Score as a tie-breaker but documents no formula, "
            "so a score this module cannot justify is never invented here. Send "
            "`engagement_score` per candidate."
        ),
    }
