"""One error hierarchy for the Slack-notification workflow.

Every refusal this package makes is the caller's to fix, so the types share a base
and the feature module registers a single handler for the base. Anything that is
not a :class:`SignalSlackError` is a bug and must propagate.

``code`` and ``status`` ride on the exception rather than being decided inside the
handler, because "this message carries a tag nobody publishes" and "this workspace
already has a connection" are both this package's errors and only one of them
conflicts with state that already exists. FastAPI accepts exception handlers on
the app object only, so the feature module exports the mapping as
``EXCEPTION_HANDLERS``; two features may not map one type, which is why the whole
hierarchy hangs off one base.

The three refusals the issue names by hand live here too. ``MissingBotScope`` and
``ChannelRequired`` are the two connect-time refusals; ``BotNotInChannel`` is the
dispatch-time refusal the private-channel rule needs. Each carries its own
``code`` so the record and the response say which rule fired, rather than one
generic 422 that a reader has to guess at.

``RecordNotFound`` is deliberately *not* claimed. The core app already maps it to
404, and a second handler for one type is a collision the host refuses.
"""

from __future__ import annotations


class SignalSlackError(ValueError):
    """A Slack-notification request cannot be honoured as written.

    A ``ValueError`` because every subclass is caused by something the caller
    sent. Nothing in this package raises for a fault of its own.
    """

    code = "signal_slack_error"
    status = 400


# --------------------------------------------------------------------------- #
# The connection: the two refusals the issue names at connect time
# --------------------------------------------------------------------------- #


class InvalidConnection(SignalSlackError):
    """A Slack connection cannot be stored.

    Raised for a missing workspace, a scope list that names something this build
    does not recognise, and a channel kind outside the two published ones.
    """

    code = "invalid_connection"
    status = 422


class MissingBotScope(InvalidConnection):
    """The credential does not carry the ``bot`` scope.

    "you need to provide Slack credentials with the 'bot' scope". A credential
    without it cannot post, so it is refused at connect time rather than stored and
    discovered later as a silent failure.
    """

    code = "missing_bot_scope"
    status = 422


class ChannelRequired(InvalidConnection):
    """No channel was chosen.

    "select a workspace (and a channel, required at connect time)". A connection
    with no channel is not a connection: every dispatch names a destination, and a
    default that does not exist is not one.
    """

    code = "channel_required"
    status = 422


class DuplicateConnection(SignalSlackError):
    """This room already has a connected Slack workspace."""

    code = "connection_already_exists"
    status = 409


class UnknownConnection(SignalSlackError):
    """No connection exists under the id the caller named."""

    code = "unknown_connection"
    status = 404


# --------------------------------------------------------------------------- #
# The automation
# --------------------------------------------------------------------------- #


class InvalidAutomation(SignalSlackError):
    """A Slack block cannot be saved.

    Raised for a missing name, a block kind outside the published set, a trigger
    kind outside the two the research names, a message that is blank or over the
    length ceiling, a message with a malformed mention or link, and a destination
    channel that is not a channel name.
    """

    code = "invalid_automation"
    status = 422


class DuplicateAutomation(SignalSlackError):
    """An automation with this name already exists in this room."""

    code = "automation_already_exists"
    status = 409


class UnknownAutomation(SignalSlackError):
    """No automation exists under the id the caller named."""

    code = "unknown_automation"
    status = 404


# --------------------------------------------------------------------------- #
# The dispatch
# --------------------------------------------------------------------------- #


class InvalidDispatch(SignalSlackError):
    """A notification decision cannot be recorded as described.

    Raised for a dispatch kind outside the two published ones and a test send with
    no test target. The rule refusals the issue names are *recorded* states, not
    this error: a rate-limited post and a bot that is not in a private channel are
    things the page must show, so they come back as a ``refused`` dispatch rather
    than as a 4xx that a reader has to translate.
    """

    code = "invalid_dispatch"
    status = 422


class UnknownDispatch(SignalSlackError):
    """No dispatch exists under the id the caller named."""

    code = "unknown_dispatch"
    status = 404


class UnknownSignal(SignalSlackError):
    """No buyer signal exists under the id the caller named.

    The signal is read from WF-027's ``intent_signal`` collection by its published
    name. A notification with no signal to notify about is refused rather than
    invented.
    """

    code = "unknown_signal"
    status = 404


class BotNotInChannel(SignalSlackError):
    """The bot is not a member of the private channel.

    "you must invite your bot to a channel with the /invite @Customer.io command".
    Carried as an exception so a caller *can* ask for a hard refusal, but the
    ordinary dispatch route records it as a ``refused`` decision instead, because
    the issue asks for the failure message a seller sees rather than a bare 409.
    """

    code = "bot_not_in_channel"
    status = 409


__all__ = [
    "BotNotInChannel",
    "ChannelRequired",
    "DuplicateAutomation",
    "DuplicateConnection",
    "InvalidAutomation",
    "InvalidConnection",
    "InvalidDispatch",
    "MissingBotScope",
    "SignalSlackError",
    "UnknownAutomation",
    "UnknownConnection",
    "UnknownDispatch",
    "UnknownSignal",
]
