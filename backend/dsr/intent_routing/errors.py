"""One error hierarchy for real-time buyer-intent alerting and routing.

Every refusal this package makes is the caller's to fix, so the types share a base
and the feature module registers a single handler for the base. Anything that is
not an :class:`IntentRoutingError` is a bug and must propagate.

``code`` and ``status`` ride on the exception rather than being decided inside the
handler, because "this account is not on a watchlist" and "that watchlist already
exists" are both this package's errors and only one of them conflicts with state
that already exists. A handler that answered 400 for both would be lying about the
second. FastAPI only accepts exception handlers on the app object, so the feature
module exports this mapping as ``EXCEPTION_HANDLERS``; two features may not map one
type, which is why the whole hierarchy hangs off one base.

``RecordNotFound`` is deliberately not claimed here. The core app already maps it
to 404, and a second handler for one type is a collision the host refuses.
"""

from __future__ import annotations


class IntentRoutingError(ValueError):
    """An intent-alerting request cannot be honoured as written.

    A ``ValueError`` because every subclass is caused by something the caller sent.
    Nothing in this package raises for a fault of its own.
    """

    code = "intent_routing_error"
    status = 400


# --------------------------------------------------------------------------- #
# Engagement observations
# --------------------------------------------------------------------------- #


class InvalidEngagement(IntentRoutingError):
    """An engagement observation cannot be read.

    Raised for a missing company key, a missing room, a negative measurement, and
    a page list that is not a list of strings. The company key and the room are
    required together because a threshold crossing is meaningless without both: the
    thresholds say nothing about who or where, and the routing rules read the room.
    """

    code = "invalid_engagement"
    status = 422


class UnknownEngagementField(IntentRoutingError):
    """The observation carries a field this workflow does not name.

    The five threshold inputs are a closed list, and so is the shape of an
    observation. An open list is not a more flexible observation, it is one that
    stores whatever a caller is configured to send, and the measured value of a
    threshold crossing is the whole of this workflow's output. A field nobody
    thresholds on is a field a reader will later mistake for one that was.
    """

    code = "unknown_engagement_field"
    status = 422


# --------------------------------------------------------------------------- #
# Accounts and resolution
# --------------------------------------------------------------------------- #


class UnknownCompany(IntentRoutingError):
    """No company record exists for the key the caller named.

    The resolution step reads the company the visitor-identification workflow
    already identified. An unknown key means the address has never resolved, and
    this workflow refuses rather than routing an alert to a guessed recipient: the
    research states that reverse-IP resolution has no clean open-source equivalent,
    so the only defensible position is to route what is known and refuse the rest.
    """

    code = "unknown_company"
    status = 404


class UnresolvedAccount(IntentRoutingError):
    """The company is known but no opportunity could be attached to it.

    The researched flow ends with "a follow-up task is created on the
    opportunity", so an alert with no opportunity has nowhere to put its task. This
    is refused rather than written, because a follow-up task with no opportunity is
    a row nobody will ever see.
    """

    code = "unresolved_account"
    status = 409


# --------------------------------------------------------------------------- #
# Watchlists
# --------------------------------------------------------------------------- #


class InvalidWatchlist(IntentRoutingError):
    """A watchlist cannot be saved.

    Raised for a missing or blank name, a tier outside the three published ones,
    and accounts that are not a list of non-blank company keys.
    """

    code = "invalid_watchlist"
    status = 422


class DuplicateWatchlist(IntentRoutingError):
    """A watchlist with this name already exists in this room.

    Two watchlists sharing a name in one room would make the alert-routing screen
    ambiguous about which one a rep is looking at.
    """

    code = "watchlist_already_exists"
    status = 409


class UnknownWatchlist(IntentRoutingError):
    """No watchlist exists under the id the caller named."""

    code = "unknown_watchlist"
    status = 404


# --------------------------------------------------------------------------- #
# Routing rules
# --------------------------------------------------------------------------- #


class InvalidRule(IntentRoutingError):
    """A routing rule cannot be saved.

    Raised for a kind outside the four published ones, a fallback rule that is not
    last in its chain, a fallback rule that names recipients, a rule that matches
    on nothing, and a position that is not a whole number.
    """

    code = "invalid_routing_rule"
    status = 422


class DuplicateRule(IntentRoutingError):
    """A rule with this name already exists in this room."""

    code = "routing_rule_already_exists"
    status = 409


class UnknownRule(IntentRoutingError):
    """No routing rule exists under the id the caller named."""

    code = "unknown_routing_rule"
    status = 404


# --------------------------------------------------------------------------- #
# Signals, alerts and tasks
# --------------------------------------------------------------------------- #


class UnknownSignal(IntentRoutingError):
    """No intent signal exists under the id the caller named."""

    code = "unknown_signal"
    status = 404


class UnknownAlert(IntentRoutingError):
    """No alert exists under the id the caller named."""

    code = "unknown_alert"
    status = 404


class UnknownTask(IntentRoutingError):
    """No follow-up task exists under the id the caller named."""

    code = "unknown_task"
    status = 404


class InvalidAction(IntentRoutingError):
    """A rep action cannot be recorded.

    Raised for a kind outside the four published ones, a dismissal with no note,
    and an empty note. A dismissal without a reason is an alert a rep could not be
    bothered with, and keeping it would make the append-only log useless for the
    one question it exists to answer, which is which alerts were not worth a call.
    """

    code = "invalid_signal_action"
    status = 422


__all__ = [
    "DuplicateRule",
    "DuplicateWatchlist",
    "IntentRoutingError",
    "InvalidAction",
    "InvalidEngagement",
    "InvalidRule",
    "InvalidWatchlist",
    "UnknownAlert",
    "UnknownCompany",
    "UnknownEngagementField",
    "UnknownRule",
    "UnknownSignal",
    "UnknownTask",
    "UnknownWatchlist",
    "UnresolvedAccount",
]
