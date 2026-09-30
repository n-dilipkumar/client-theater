"""One error hierarchy for the change-stream package.

Every refusal this package makes is a caller's mistake or a conflict with state
that already exists, so the types share a base and the feature module registers a
single handler for it. Anything that is *not* a :class:`ChangeStreamError` is a
bug and must propagate.

``code`` and ``status`` ride on the exception rather than being decided inside the
handler, for the same reason :mod:`dsr.signals.errors` does it: asking to
enrich a standard channel and sending a change type the research never listed are
both this package's errors, and only one of them is a malformed request. FastAPI
only accepts exception handlers on the app object, so the feature module exports
this mapping as ``EXCEPTION_HANDLERS``; two features may not map the same type,
which is why the whole hierarchy hangs off one base class.
"""

from __future__ import annotations


class ChangeStreamError(ValueError):
    """A change-stream request cannot be honoured as written.

    A ``ValueError`` because every subclass is caused by something the caller
    sent, or by a conflict between that and a state that already exists.
    Nothing in this package raises for a fault of its own.
    """

    code = "change_stream_error"
    status = 400


# --------------------------------------------------------------------------- #
# The org and its edition
# --------------------------------------------------------------------------- #


class OrgError(ChangeStreamError):
    """A CRM org cannot be described as asked."""

    code = "org_error"


class UnknownOrg(OrgError):
    """No org record matches that id.

    404 rather than 409: the row is not there at all, which is different from
    being there and refusing the change.
    """

    code = "org_not_found"
    status = 404


class DuplicateOrg(OrgError):
    """Two org records for one system.

    The research's edition gate is a property of the org, so two records for one
    system would give the gate two answers.
    """

    code = "org_already_registered"
    status = 409


class EditionDoesNotSupportCdc(OrgError):
    """Available in: Enterprise, Performance, Unlimited, and Developer editions.

    Raised when a caller asks to enable Change Data Capture on an org whose
    edition is not one of the four. 409 rather than 400: the request is well
    formed, and what it conflicts with is the org the caller already declared.
    The message names the editions that do have it, because "not supported" on
    its own sends someone to the edition list page.
    """

    code = "edition_does_not_support_change_data_capture"
    status = 409


class CdcNotEnabled(OrgError):
    """The org exists but Change Data Capture has not been enabled on it.

    This is user-flow step one, and it is the first thing a room has to do. A
    subscription opened against an org that never enabled CDC would be
    subscribing to nothing, so the refusal happens before any row is written.
    """

    code = "change_data_capture_not_enabled"
    status = 409


# --------------------------------------------------------------------------- #
# Channels
# --------------------------------------------------------------------------- #


class ChannelError(ChangeStreamError):
    """A subscription channel cannot be described as asked."""

    code = "channel_error"


class UnknownChannel(ChannelError):
    """No channel record matches that id."""

    code = "channel_not_found"
    status = 404


class DuplicateChannelName(ChannelError):
    """A channel of that name already exists on that org.

    The comparison is case-*sensitive*, because "The channel name is
    case-sensitive." So this fires for ``/data/ChangeEvents`` twice and not for
    ``/data/ChangeEvents`` against ``/data/changeevents`` - which are two
    channels, and saying so is the point.
    """

    code = "channel_name_already_in_use"
    status = 409


class StandardChannelEnrichmentRefused(ChannelError):
    """We recommend that you configure event enrichment on a custom channel and
    not the standard /data/ChangeEvents channel. This way, other subscribers that
    receive change events on the standard channel don't receive unchanged fields
    that they don't expect.

    Refused rather than recommended-against, and the reason is in the sentence
    the research quotes: the harm lands on *other subscribers*, which this
    product cannot enumerate and therefore cannot weigh. A warning is invisible
    to the parties it hurts.
    """

    code = "enrichment_not_available_on_the_standard_channel"
    status = 409


class UnsupportedEnrichmentTransport(ChannelError):
    """Event enrichment is supported for subscribers that use Pub/Sub API,
    CometD (Streaming API), or event relays.

    400: the transport is a real one this API accepts, and the enrichment is
    what cannot be had on it. The message names the three transports that can.
    """

    code = "enrichment_not_supported_by_this_transport"


class ChannelInUse(ChannelError):
    """A channel a live subscription is still reading cannot be deleted.

    409, and a different type from :class:`UnknownChannel` on purpose: the row is
    there, the request is well formed, and what it conflicts with is a subscription
    that is still open. A 404 would tell the caller the channel does not exist,
    which is exactly the kind of message that sends someone to the wrong page.
    """

    code = "channel_has_open_subscriptions"
    status = 409


class FieldMapError(ChannelError):
    """The field map cannot be used to resolve a change event."""

    code = "field_map_error"


class EntityNotOnChannel(ChangeStreamError):
    """An event names an entity the channel does not stream.

    "A subscription channel is a stream of change events that correspond to one
    or more entities." An event for an entity the channel does not carry is a
    subscriber bug - it is subscribed to the wrong channel - and it is refused
    rather than quietly dropped, because a silently dropped event is a change
    that never reaches the room's replica.
    """

    code = "entity_not_on_channel"
    status = 409


# --------------------------------------------------------------------------- #
# Subscriptions
# --------------------------------------------------------------------------- #


class SubscriptionError(ChangeStreamError):
    """A subscription cannot be opened or driven as asked."""

    code = "subscription_error"


class UnknownSubscription(SubscriptionError):
    """No subscription record matches that id."""

    code = "subscription_not_found"
    status = 404


class SubscriptionClosed(SubscriptionError):
    """The subscription is closed and no longer receives events.

    A subscription is "long-lived" and the research describes two states for it,
    so the third - half-closed, still accepting - has no basis here.
    """

    code = "subscription_closed"
    status = 409


class NoOutstandingFetchRequest(SubscriptionError):
    """An event was delivered with no FetchRequest to cover it.

    The Subscribe method uses bidirectional streaming, enabling the client to
    request more events as it consumes events. The client can control the flow of
    events received by setting the number of requested events in the FetchRequest
    parameter.

    The flow control is the point of the bidirectional stream, so an event
    arriving with nothing outstanding is an event the subscriber was not
    authorised to receive. Refused, and reported, because a silently delivered
    event would make the counter on ``/usage`` a lie.
    """

    code = "no_outstanding_fetch_request"
    status = 409


class SubscriptionLimitExceeded(SubscriptionError):
    """You can create up to 1,000 webhook subscriptions per app.

    409, naming the limit and the count, because the caller's next question is
    "how many more" and the answer is zero.
    """

    code = "webhook_subscription_limit_reached"
    status = 409


# --------------------------------------------------------------------------- #
# Change events
# --------------------------------------------------------------------------- #


class EventError(ChangeStreamError):
    """A change event cannot be accepted as written."""

    code = "change_event_error"


class UnknownChangeType(EventError):
    """Changes include creation of a new record, updates to an existing
    record, deletion of a record, and undeletion of a record.

    The vocabulary is those four. A fifth - the vendor's gap and overflow
    markers, or anything a caller invents - is refused with the list, because
    reconciling a dropped stream is a different researched workflow and this one
    has no rule for it.
    """

    code = "unknown_change_type"


class MissingTransactionKey(EventError):
    """An event has no ``transactionKey``.

    The buffering rule is "buffers the change under its transactionKey, and only
    commits ... when the key changes", so an event with no key cannot be placed
    in the buffer and there is no default the research states.
    """

    code = "transaction_key_required"


class MalformedEvent(EventError):
    """An event is missing something this workflow reads by name."""

    code = "malformed_change_event"


class RecordUnresolvable(EventError):
    """The event cannot be matched to a replica row, and enrichment is the fix.

    This is the rule the whole enrichment feature exists for. "If the room needs
    an unchanged field (e.g. the external ID) to resolve the record, that field is
    added as an enriched field on the channel." An update event carries only what
    changed, so without the sync key in the payload *or* in the enriched fields
    there is nothing to resolve against - and this is the only error in the
    package that tells the caller exactly which field to add.

    409: the event is well formed, and what it conflicts with is the channel's
    enrichment configuration. The message names the field.
    """

    code = "enrichment_required"
    status = 409


class ReplicaWriteError(EventError):
    """A committed transaction could not be applied to the room's replica.

    Deliberately not carrying the record id: the whole point of the buffer is
    that a transaction is applied as a unit, so the message names the transaction
    and the caller re-reads it.
    """

    code = "replica_write_failed"
    status = 409


# --------------------------------------------------------------------------- #
# Dataverse change tracking
# --------------------------------------------------------------------------- #


class DataverseError(ChangeStreamError):
    """A change-tracking operation cannot be honoured as written."""

    code = "dataverse_error"


class UnknownTable(DataverseError):
    """No Dataverse table record matches that id."""

    code = "table_not_found"
    status = 404


class ChangeTrackingDisabled(DataverseError):
    """The table does not have change tracking on.

    409: the poll is well formed, and what it conflicts with is the table's
    configuration. "After you enable change tracking for a table, you can't
    disable it" means the only way to be in this state is to have never enabled
    it, and the research's own remedy is the Power Apps **Track changes** table
    property, which this API names in the message.
    """

    code = "change_tracking_not_enabled"
    status = 409


class ChangeTrackingIrreversible(DataverseError):
    """After you enable change tracking for a table, you can't disable it.

    The one genuinely one-way rule in this workflow, and enforced as a refusal
    rather than as a soft delete. A room that believes it turned the property off
    would keep polling a delta link it thinks it abandoned.
    """

    code = "change_tracking_cannot_be_disabled"
    status = 409


class MissingTrackChangesPreference(DataverseError):
    """You can track changes made in tables by using Web API requests that
    include the Prefer: odata.track-changes header. This header requests that a
    delta link is returned.

    A poll without the header is a plain read, not a delta poll. Answering one
    with a delta link would invent the guarantee the header is there to ask for.
    """

    code = "odata_track_changes_preference_required"


class UnsupportedDeltaQueryOption(DataverseError):
    """A query option Dataverse refuses while change tracking is enabled.

    The message is the vendor's own, verbatim, naming the option that was sent.
    """

    code = "unsupported_query_option_with_change_tracking"
