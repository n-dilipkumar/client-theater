"""HubSpot inbound: the workflow webhook, the 1,000 cap, and the rate-limit fact.

The research gives HubSpot three sentences and one gap, and this module is exactly
those three sentences and the gap made into rules.

* **"Webhooks can be triggered as an action in any workflow, so you can use any
  workflow starting conditions as the criteria."** So the HubSpot seam is
  *inbound* and *conditional on a workflow's own starting conditions*, not a
  subscription this room registers against a REST API. That is the whole reason
  there is no HubSpot subscription REST surface here: the research records that
  the page documenting those endpoints "is client-rendered and its body could not
  be read; the subscription endpoints/methods are therefore not claimed." Building
  them would mean inventing an API contract from a page nobody read.

* **"Webhook calls made via workflows do not count towards the API rate limit."**
  A counting rule, and the one thing here that is about *outbound* calls. It only
  means something next to a budget, so this module keeps a budget and a count of
  the calls that came through a workflow - calls it never charged.

* **"You can create up to 1,000 webhook subscriptions per app."** A cap, counted
  per app, and enforced as a refusal on the 1,001st with the number in the
  message, because the caller's next question is "how many more".
"""

from __future__ import annotations

from typing import Any, Mapping

from dsr.change_stream import vocabulary
from dsr.change_stream.errors import ChannelError, SubscriptionLimitExceeded

#: The inbound seam's transport, as the research names it: a webhook *action* in
#: a workflow, fired from workflow enrollment.
HUBSPOT_TRANSPORT = "workflow_webhook"

#: The two facts that are quoted, held as data so the vocabulary endpoint and the
#: usage report read the same numbers the rules do.
WORKFLOW_CALLS_EXEMPT_FROM_RATE_LIMIT = vocabulary.HUBSPOT_WORKFLOW_CALLS_EXEMPT_FROM_RATE_LIMIT
WEBHOOK_SUBSCRIPTION_LIMIT = vocabulary.HUBSPOT_WEBHOOK_SUBSCRIPTION_LIMIT


def normalise_subscription(raw: Mapping[str, Any]) -> dict[str, Any]:
    """A room's registered HubSpot webhook target.

    ``workflow_id`` is the part that matters: the webhook fires on that
    workflow's starting conditions, which is what makes the enrollment the unit
    of filtering rather than this room's own.
    """
    if not isinstance(raw, Mapping):
        raise ChannelError("a HubSpot webhook subscription must be a JSON object")
    target = str(raw.get("target_url") or raw.get("targetUrl") or "").strip()
    if not target:
        raise ChannelError(
            "target_url is required: a workflow webhook action has to have somewhere to POST. "
            "Without it the subscription records an enrollment that can never arrive."
        )
    if not target.lower().startswith(("http://", "https://")):
        raise ChannelError(f"target_url must be an absolute http(s) URL; got {target!r}")
    return {
        "target_url": target,
        "workflow_id": str(raw.get("workflow_id") or raw.get("workflowId") or "").strip(),
        "workflow_name": str(raw.get("workflow_name") or ""),
        # The criteria are the workflow's, not ours. Carried so a reader can see
        # what this room is pretending to filter on and that it is not the filter.
        "criteria_owned_by": "the HubSpot workflow's starting conditions",
        "enabled": bool(raw.get("enabled", True)),
        "calls_received": 0,
        "calls_charged_to_budget": 0,
        "calls_exempt_from_rate_limit": 0,
    }


def charge_or_exempt(budget: Mapping[str, Any], *, via_workflow: bool) -> dict[str, Any]:
    """Spend one unit of the budget, or record that this call did not.

    The rule is one line in the research and the whole reason a budget exists:
    a call the workflow made is free, a call the app made is not. Reporting the
    exempt one as well is the point - a usage report that only showed the
    remaining budget would make the exemption invisible and therefore
    unauditable.
    """
    spent = int(budget.get("spent") or 0)
    limit = int(budget.get("limit") or vocabulary.DEFAULT_API_CALL_BUDGET)
    remaining = max(0, limit - spent)
    if via_workflow:
        return {
            "via_workflow": True,
            "charged": False,
            "spent": spent,
            "limit": limit,
            "remaining": remaining,
            "exempt_because": (
                '"Webhook calls made via workflows do not count towards the API rate limit."'
            ),
        }
    return {
        "via_workflow": False,
        "charged": True,
        "spent": spent + 1,
        "limit": limit,
        "remaining": max(0, limit - (spent + 1)),
        "exempt_because": None,
    }


def require_capacity(live_count: int) -> None:
    """Refuse the registration that would exceed the researched cap.

    "You can create up to 1,000 webhook subscriptions per app." Counted over
    the live subscriptions, so a cancelled one frees its slot - the vendor's
    sentence caps how many exist, not how many were ever made.
    """
    if int(live_count) < WEBHOOK_SUBSCRIPTION_LIMIT:
        return
    raise SubscriptionLimitExceeded(
        f"{WEBHOOK_SUBSCRIPTION_LIMIT} webhook subscriptions is the limit per app, and "
        f"{live_count} are live. Cancel one before adding another."
    )


def describe() -> dict[str, Any]:
    """The HubSpot half of this workflow, as the research states it."""
    return {
        "transport": HUBSPOT_TRANSPORT,
        "direction": "inbound",
        "fired_by": "a webhook action in any workflow, on that workflow's starting conditions",
        "webhook_subscription_limit": WEBHOOK_SUBSCRIPTION_LIMIT,
        "workflow_calls_exempt_from_rate_limit": WORKFLOW_CALLS_EXEMPT_FROM_RATE_LIMIT,
        "api_call_budget_default": vocabulary.DEFAULT_API_CALL_BUDGET,
        "api_call_budget_note": (
            "not researched: the vendor states the exemption but no budget, so one is "
            "declared here for the exemption to be an exemption from"
        ),
        "subscription_rest_api": (
            "not implemented. The research records that the webhook subscriptions REST page "
            "is client-rendered and its body could not be read, so the subscription "
            "endpoints and methods are not claimed and are not built."
        ),
    }
