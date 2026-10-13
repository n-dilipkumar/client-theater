"""The pure rules for a Slack notification: connection, message, and the decision.

Nothing here touches the store, the framework, or the clock. A connection payload,
an automation payload, a signal record, a customer profile, a moment and the
previous dispatch moments all arrive as arguments, so a rule can be read and
checked on its own and a test that goes through HTTP to check one number cannot
tell a broken rule from a broken handler.

The decision is a pure function of its inputs, and it is where the two kinds of
outcome become one shape:

* ``recorded`` - every rule passed, and the message that WOULD be posted to the
  channel is in ``message``. This product has no Slack transport, so nothing is
  sent; the record is the product's output.
* ``refused`` - a rule stopped it, and ``refusal`` names which of
  :data:`~dsr.signal_slack.vocabulary.REFUSAL_CODES` fired.

A refusal is a value, not an exception, because the page has to show a seller
*why* a notification was not recorded as deliverable. The three refusals the issue
names by hand are all here: the bot scope and the channel are connect-time
refusals and live in :func:`normalise_connection`; the private-channel invite is a
decision-time refusal and lives in :func:`decide`.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Mapping

from dsr.signal_slack import liquid
from dsr.signal_slack.errors import (
    ChannelRequired,
    InvalidAutomation,
    InvalidConnection,
    InvalidDispatch,
    MissingBotScope,
)
from dsr.signal_slack.vocabulary import (
    BLOCK_KIND,
    CHANNEL_KINDS,
    DELIVERY,
    DELIVERY_NOTE,
    DISPATCH_KINDS,
    KNOWN_SCOPES,
    MAX_CHANNEL_LENGTH,
    PRIVATE_CHANNEL_QUOTE,
    REQUIRED_SCOPE,
    STATE_CONNECTED,
    STATE_LIVE,
    STATE_RECORDED,
    STATE_REFUSED,
    TRIGGER_KINDS,
    rate_limit,
)

#: The order the decision applies its rules in. The list is the specification of
#: precedence: a caller that fixes the first refusal it is shown will clear the
#: ones behind it.
REFUSAL_PRECEDENCE: tuple[str, ...] = (
    "no_connection",
    "connection_revoked",
    "automation_not_live",
    "channel_required",
    "bot_not_in_channel",
    "rate_limited",
)


# --------------------------------------------------------------------------- #
# The connection
# --------------------------------------------------------------------------- #


def normalise_workspace(value: Any) -> str:
    """A workspace name, required and non-empty."""
    if not isinstance(value, str) or not value.strip():
        raise InvalidConnection("a Slack connection needs the workspace it connects to")
    return value.strip()


def normalise_channel(value: Any) -> str:
    """A channel name, folded to one spelling.

    A leading ``#`` is optional and dropped, and case is folded, because ``#Sales``
    and ``sales`` are one channel. A space is refused rather than stripped: Slack
    channel names do not contain one, so a name with a space is a string a human
    typed and not a channel.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ChannelRequired(
            "a Slack connection needs a channel. The research requires one at connect time, "
            "because every notification names a destination."
        )
    if not isinstance(value, str):
        raise InvalidConnection(f"a channel must be a string; got {type(value).__name__}")
    text = value.strip()
    if text.startswith("#"):
        text = text[1:]
    text = text.lower()
    if not text:
        raise ChannelRequired("a Slack connection needs a channel, and '#' alone is not one.")
    if any(character.isspace() for character in text):
        raise InvalidConnection(
            f"{value!r} is not a channel name: it contains a space. Slack channel names do not."
        )
    if len(text) > MAX_CHANNEL_LENGTH:
        raise InvalidConnection(
            f"{value!r} is longer than {MAX_CHANNEL_LENGTH} characters, which is longer than "
            "a Slack channel name."
        )
    return text


def require_bot_scope(scopes: Any) -> list[str]:
    """The credential's scopes, with the ``bot`` scope required.

    "you need to provide Slack credentials with the 'bot' scope". Omitted scopes are
    treated as missing the bot scope rather than defaulted to it: a credential whose
    grant is unknown is not a credential this product may claim can post.
    """
    if not isinstance(scopes, (list, tuple)) or not scopes:
        raise MissingBotScope(
            "the credential carries no scopes, and the 'bot' scope is required - "
            + REQUIRED_SCOPE
            + " is not optional. The research: "
            + "you need to provide Slack credentials with the 'bot' scope."
        )
    names = [str(scope).strip() for scope in scopes]
    unknown = sorted({name for name in names if name not in KNOWN_SCOPES})
    if unknown:
        raise InvalidConnection(
            "unknown scope(s): " + ", ".join(unknown) + ". Known scopes: " + ", ".join(KNOWN_SCOPES)
        )
    if REQUIRED_SCOPE not in names:
        raise MissingBotScope(
            "the credential does not carry the 'bot' scope, which is required. "
            "The research: you need to provide Slack credentials with the 'bot' scope."
        )
    return names


def normalise_connection(payload: Mapping[str, Any]) -> dict[str, Any]:
    """A connection payload onto the shape the engine stores."""
    if not isinstance(payload, Mapping):
        raise InvalidConnection("a connection must be a JSON object")
    channel_kind = str(payload.get("channel_kind") or "public").strip().lower()
    if channel_kind not in CHANNEL_KINDS:
        raise InvalidConnection(
            f"channel_kind must be one of {', '.join(CHANNEL_KINDS)}; got {channel_kind!r}"
        )
    return {
        "workspace": normalise_workspace(payload.get("workspace")),
        "channel": normalise_channel(payload.get("channel")),
        "channel_kind": channel_kind,
        "scopes": require_bot_scope(payload.get("scopes")),
        "bot_user": str(payload.get("bot_user") or "").strip(),
        "bot_in_channel": bool(payload.get("bot_in_channel", channel_kind == "public")),
        "state": STATE_CONNECTED,
        "note": str(payload.get("note") or "").strip(),
    }


# --------------------------------------------------------------------------- #
# The automation
# --------------------------------------------------------------------------- #


def normalise_automation(payload: Mapping[str, Any]) -> dict[str, Any]:
    """A Slack block onto the shape the engine stores.

    The message is rendered once here, against an empty profile, so a malformed
    mention or link is refused at save time rather than at the first dispatch.
    """
    if not isinstance(payload, Mapping):
        raise InvalidAutomation("an automation must be a JSON object")
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        raise InvalidAutomation("a Slack block needs a name")
    block_kind = str(payload.get("block_kind") or BLOCK_KIND).strip().lower()
    if block_kind != BLOCK_KIND:
        raise InvalidAutomation(
            f"block_kind must be {BLOCK_KIND!r}; got {block_kind!r}. This build ships one block."
        )
    trigger = str(payload.get("trigger") or "signal").strip().lower()
    if trigger not in TRIGGER_KINDS:
        raise InvalidAutomation(
            f"trigger must be one of {', '.join(TRIGGER_KINDS)}; got {trigger!r}"
        )
    message = payload.get("message")
    if not isinstance(message, str) or not message.strip():
        raise InvalidAutomation("a Slack block needs a message")
    if len(message) > _message_max():
        raise InvalidAutomation(
            f"the message is longer than {_message_max()} characters, which this build refuses."
        )
    liquid.render_message(message, {})
    channel = payload.get("channel")
    return {
        "name": name.strip(),
        "block_kind": block_kind,
        "trigger": trigger,
        "message": message,
        "channel": normalise_channel(channel) if channel else "",
        "state": _automation_state(payload.get("state")),
        "note": str(payload.get("note") or "").strip(),
    }


def _message_max() -> int:
    # Imported lazily to keep the module-level import list short and obvious.
    from dsr.signal_slack.vocabulary import MESSAGE_MAX_LENGTH

    return MESSAGE_MAX_LENGTH


def _automation_state(value: Any) -> str:
    from dsr.signal_slack.vocabulary import AUTOMATION_STATES, STATE_DRAFT

    state = str(value or STATE_DRAFT).strip().lower()
    if state not in AUTOMATION_STATES:
        raise InvalidAutomation(
            f"state must be one of {', '.join(AUTOMATION_STATES)}; got {value!r}"
        )
    return state


def normalise_dispatch_kind(value: Any) -> str:
    kind = str(value or "live").strip().lower()
    if kind not in DISPATCH_KINDS:
        raise InvalidDispatch(f"kind must be one of {', '.join(DISPATCH_KINDS)}; got {value!r}")
    return kind


# --------------------------------------------------------------------------- #
# The rate limit
# --------------------------------------------------------------------------- #


def _moment(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed


def check_rate_limit(priors: Any, *, kind: str, now: datetime) -> dict[str, Any]:
    """Whether this dispatch is inside the named limit for its kind.

    ``priors`` is every moment a dispatch of the same kind to the same destination
    was recorded. The limit is "requests per window", so the check is a count of
    the moments inside the window ending at ``now``. ``retry_after_seconds`` is how
    long until the oldest of those leaves, which is the number a caller needs.
    """
    limit = rate_limit(kind)
    window = float(limit["per_seconds"])
    allowed = int(limit["requests"])
    moments = [moment for moment in (_moment(value) for value in (priors or [])) if moment]
    cutoff = now - timedelta(seconds=window)
    inside = sorted((moment for moment in moments if cutoff < moment <= now), reverse=True)
    if len(inside) >= allowed:
        oldest = inside[allowed - 1]
        retry_after = round(window - (now - oldest).total_seconds(), 3)
        return {
            "limited": True,
            "in_window": len(inside),
            "retry_after_seconds": max(retry_after, 0.0),
            **limit,
        }
    return {"limited": False, "in_window": len(inside), "retry_after_seconds": 0.0, **limit}


# --------------------------------------------------------------------------- #
# The decision
# --------------------------------------------------------------------------- #


def resolve_channel(
    connection: Mapping[str, Any] | None,
    automation: Mapping[str, Any] | None,
    override: Any,
) -> str:
    """The destination channel: the per-message choice, then the block, then the connection.

    "Choose the destination channel per message" makes the override the first
    source; the block's default and the connection's channel are the fallbacks, in
    that order, because the more specific setting is the one a seller expects to win.
    """
    for candidate in (
        override,
        (automation or {}).get("channel"),
        (connection or {}).get("channel"),
    ):
        if candidate:
            return normalise_channel(candidate)
    raise ChannelRequired(
        "no destination channel. Choose one per message: the research has the channel selected "
        "per message, and a notification with no destination has nowhere to go."
    )


def signal_summary(signal: Mapping[str, Any] | None) -> dict[str, Any]:
    """The trigger, flattened to what the message and the record show.

    The fields are WF-027's: ``type``, ``urgency``, ``signal_name`` and ``rendered``
    all live on an ``intent_signal`` row. Nothing is invented when one is absent;
    an empty summary says the dispatch is a test send rather than a signal-driven one.
    """
    if not signal:
        return {"id": "", "type": "", "urgency": "", "name": "", "description": ""}
    data = dict(signal.get("data") or signal)
    rendered = data.get("rendered")
    description = ""
    if isinstance(rendered, Mapping):
        description = str(rendered.get("text") or rendered.get("description") or "")
    return {
        "id": str(signal.get("id") or ""),
        "type": str(data.get("type") or ""),
        "urgency": str(data.get("urgency") or ""),
        "name": str(data.get("signal_name") or ""),
        "description": description,
        "attribution": dict(data.get("attribution") or {}),
    }


def decide(
    *,
    connection: Mapping[str, Any] | None,
    automation: Mapping[str, Any] | None,
    signal: Mapping[str, Any] | None,
    customer: Mapping[str, Any] | None,
    channel: Any = None,
    kind: str = "live",
    now: datetime,
    priors: Any = (),
) -> dict[str, Any]:
    """The whole decision: what would be posted, or which refusal stopped it.

    The return value is one shape for both outcomes, so a caller never has to
    branch to read the channel or the reason. ``refusal`` is empty on a recorded
    decision and one of :data:`~dsr.signal_slack.vocabulary.REFUSAL_CODES` on a
    refused one.
    """
    kind = normalise_dispatch_kind(kind)
    summary = signal_summary(signal)
    outcome: dict[str, Any] = {
        "kind": kind,
        "state": STATE_RECORDED,
        "refusal": "",
        "reason": "",
        "channel": "",
        "workspace": "",
        "message": {},
        "signal": summary,
        "limit": rate_limit(kind),
        "retry_after_seconds": 0.0,
        "delivery": DELIVERY,
        "delivery_note": DELIVERY_NOTE,
        "attribution_excluded": True,
    }

    def refuse(code: str, reason: str, **extra: Any) -> dict[str, Any]:
        outcome.update(
            state=STATE_REFUSED,
            refusal=code,
            reason=reason,
            deliverable=False,
            **extra,
        )
        return outcome

    if connection is None:
        return refuse(
            "no_connection",
            "this room has no connected Slack workspace, so there is no bot to post through.",
        )

    state = str(connection.get("state") or STATE_CONNECTED)
    if state != STATE_CONNECTED:
        return refuse(
            "connection_revoked",
            "the Slack connection for this room is revoked, so it cannot post.",
        )

    channel_kind = str(connection.get("channel_kind") or "public")
    if kind == "live" and str((automation or {}).get("state") or "") != STATE_LIVE:
        return refuse(
            "automation_not_live",
            "the automation is not live, so a signal does not fire it. Publish the block first.",
        )

    try:
        destination = resolve_channel(connection, automation, channel)
    except ChannelRequired as exc:
        return refuse("channel_required", str(exc))

    outcome["channel"] = destination
    outcome["workspace"] = str(connection.get("workspace") or "")

    if channel_kind == "private" and not connection.get("bot_in_channel"):
        return refuse(
            "bot_not_in_channel",
            "the bot is not in the private channel " + destination + ". " + PRIVATE_CHANNEL_QUOTE,
        )

    rate = check_rate_limit(priors, kind=kind, now=now)
    outcome["limit"] = rate
    if rate["limited"]:
        return refuse(
            "rate_limited",
            f"the {kind} path is limited to {rate['requests']} request(s) per "
            f"{rate['per_seconds']} second(s) and {rate['in_window']} were recorded inside the "
            "window.",
            retry_after_seconds=rate["retry_after_seconds"],
        )

    template = str((automation or {}).get("message") or "")
    outcome["message"] = liquid.render_message(template, customer)
    outcome["deliverable"] = True
    return outcome


__all__ = [
    "REFUSAL_PRECEDENCE",
    "check_rate_limit",
    "decide",
    "normalise_automation",
    "normalise_channel",
    "normalise_connection",
    "normalise_dispatch_kind",
    "normalise_workspace",
    "require_bot_scope",
    "resolve_channel",
    "signal_summary",
]
