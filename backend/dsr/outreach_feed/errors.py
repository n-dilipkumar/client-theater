"""One error hierarchy for the seller activity feed package (WF-026).

Every refusal this package makes is a caller's to fix, so they share a base class
and the HTTP layer registers one handler per *distinct HTTP answer* rather than
one per message. Anything that is not a :class:`FeedError` is a bug and must
propagate.

The split is small and it is driven by what the caller can do about it:

``FeedError``
    The request cannot be honoured as written. ``400``.
``SignatureError``
    The inbound webhook did not prove it came from Outreach. ``401``. Distinct
    from a malformed body because a caller who fixes the body still fails, and a
    caller who fixes the secret should not have to touch the body.
``FeedNotConfiguredError``
    The request is well formed but this installation is not ready to answer it -
    no app, no S2S token, no webhook secret. ``428``, so a client can tell
    "not configured yet" from "not allowed" from "the request failed" without
    parsing a message.
``UnknownRoom``
    A room id does not resolve. ``404``.

The two error types the *core* app already maps - ``RecordNotFound`` and
``AuditError`` - are deliberately not claimed here. Two handlers for one type is
a collision the feature host refuses, and the core mappings are already correct.
"""

from __future__ import annotations


class FeedError(ValueError):
    """A request cannot be honoured as written."""


class AppError(FeedError):
    """The Outreach app registration is unusable as given."""


class EventNameError(FeedError):
    """An event name is not the researched ``<app identifier>:<event id>`` shape."""


class TemplateError(FeedError):
    """A card template carries a placeholder Outreach will not replace."""


class EventTypeError(FeedError):
    """A configured custom event is unusable as given."""


class ProspectLinkError(FeedError):
    """A room-to-prospect link is unusable as given."""


class WebhookError(FeedError):
    """An inbound Outreach webhook payload cannot be read."""


class SignatureError(WebhookError):
    """The ``Outreach-Webhook-Signature`` header is missing or does not verify."""


class FeedNotConfiguredError(FeedError):
    """This installation is not set up to answer the request yet."""


class UnknownRoom(LookupError):
    """A room id does not resolve to a live room record."""
