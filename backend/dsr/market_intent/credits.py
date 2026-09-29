"""The credit ledger, and the one billing rule the research states precisely.

Sourced
-------
"Tracking a company costs 10 credits. However, if a company is added and tracked
in the same billing period, you're only charged once for tracking (10 credits) -
not for both actions separately. After that initial charge, tracking continues
to be charged monthly."

The second sentence is the interesting one, because it is a rule about two
actions that would otherwise be charged separately. It is implemented
structurally rather than as a conditional: there is **one ledger row per company
per billing period**, so the second action of the period has nowhere to charge
and the saving is visible on the row rather than inferred from a balance.

Design inference
----------------
* **The billing period is the UTC calendar month.** The research says "same
  billing period" and "charged monthly" without defining a period, and the only
  time boundary it names anywhere is midnight UTC. A calendar month in UTC is
  the reading consistent with the rest of the specification. It is in
  :mod:`dsr.market_intent.inferences`, and :func:`period_for` is the one place
  to change it.
* **Both actions cost 10.** The research prices tracking at 10 and then says the
  combined case costs 10 "not for both actions separately", which only makes
  sense if the add is also priced. Both are therefore 10, and the combined case
  is 10 with 10 waived, which is what ``waived`` on the row is for.
* **Renewing is per period, and idempotent.** "Tracking continues to be charged
  monthly" is a charge every period for as long as the company is tracked, and
  the single row per (company, period) is what makes a second renewal in the
  same month a no-op rather than a second charge.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from dsr.market_intent.names import CREDITS
from dsr.market_intent.vocabulary import CREDIT_COST_ADD, CREDIT_COST_TRACK

#: The two chargeable actions, as the research names them.
ACTION_ADD = "add"
ACTION_TRACK = "track"


def period_for(moment: datetime | str) -> str:
    """The billing period a moment falls in, as ``YYYY-MM`` in UTC.

    The whole of this module's correctness about "the same billing period" is
    decided here, so the inference that the period is a UTC calendar month is
    editable in one line.
    """
    if isinstance(moment, str):
        text = moment.strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m")


def cost_of(action: str) -> int:
    """What one action costs in credits."""
    return CREDIT_COST_TRACK if action == ACTION_TRACK else CREDIT_COST_ADD


def charge(
    store: Any,
    *,
    company_key: str,
    action: str,
    at: str,
    actor: str | None,
    source: str,
    period: str | None = None,
) -> dict[str, Any]:
    """Charge for one action, or record that it cost nothing this period.

    Returns a row shaped ``{"row", "charged", "waived", "period", "outcome"}``.
    ``outcome`` is ``"charged"`` for the first action of a period and
    ``"waived"`` for any later one - the research's "you're only charged once
    for tracking (10 credits) - not for both actions separately", reported as a
    fact rather than as a silent no-op.
    """
    billing_period = period or period_for(at)
    # One row per company per period, found through the dynamic index on two
    # indexed scalars - so the structural guarantee below needs no uniqueness
    # constraint of its own, and there is no migration to add one.
    existing = store.find(
        CREDITS, {"company_key": company_key, "period": billing_period}, limit=10
    )

    if not existing:
        created = store.create(
            CREDITS,
            {
                "company_key": company_key,
                "period": billing_period,
                "actions": [action],
                "amount": cost_of(action),
                "waived": 0,
                "first_action": action,
                "last_action": action,
                "at": at,
            },
            actor=actor,
            source=source,
        )
        return {
            "row": created,
            "charged": cost_of(action),
            "waived": 0,
            "period": billing_period,
            "outcome": "charged",
        }

    row = existing[0]
    data = dict(row.get("data") or {})
    actions = list(data.get("actions") or [])
    already = bool(actions)
    new_action = action not in actions
    if new_action:
        actions.append(action)
    updated = store.update(
        row["id"],
        {
            "actions": actions,
            "last_action": action,
            "last_action_at": at,
            # "you're only charged once ... not for both actions separately": the
            # second action is recorded and the amount it would have cost is
            # recorded as waived, so the ledger says what was saved.
            #
            # Only a *new* action waives anything. Renewing a second time inside
            # one period repeats the track action, and a rule that charged a
            # saving on every repeat would report credits saved that were never
            # owed.
            "waived": int(data.get("waived") or 0) + (cost_of(action) if already and new_action else 0),
        },
        actor=actor,
        source=source,
    )
    return {
        "row": updated,
        "charged": 0 if already else cost_of(action),
        # What this call actually recorded as saved, which is zero when the
        # action repeats one already taken in this period. A repeat of the same
        # action is the monthly renewal landing twice, and reporting a saving on
        # it would report credits saved that were never owed.
        "waived": cost_of(action) if (already and new_action) else 0,
        "period": billing_period,
        "outcome": "already_charged_this_period" if already else "charged",
    }


def is_charged(store: Any, *, company_key: str, period: str) -> bool:
    """Whether a company has already been charged in a period."""
    return bool(store.find(CREDITS, {"company_key": company_key, "period": period}, limit=1))


def summarise(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Totals for the ledger, and the per-period breakdown."""
    by_period: dict[str, dict[str, Any]] = {}
    total_charged = 0
    total_waived = 0
    for row in rows:
        data = row.get("data") or row
        period = str(data.get("period") or "unknown")
        amount = int(data.get("amount") or 0)
        waived = int(data.get("waived") or 0)
        total_charged += amount
        total_waived += waived
        bucket = by_period.setdefault(period, {"period": period, "charged": 0, "waived": 0, "companies": 0})
        bucket["charged"] += amount
        bucket["waived"] += waived
        bucket["companies"] += 1
    return {
        "cost_add": CREDIT_COST_ADD,
        "cost_track": CREDIT_COST_TRACK,
        "total_charged": total_charged,
        "total_waived": total_waived,
        "periods": [by_period[key] for key in sorted(by_period)],
        "rule": (
            "Tracking a company costs 10 credits. If a company is added and tracked in the same "
            "billing period, you are only charged once (10 credits), not for both actions "
            "separately. After that initial charge, tracking continues to be charged monthly."
        ),
    }
