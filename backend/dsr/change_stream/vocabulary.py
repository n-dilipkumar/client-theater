"""The values WF-043 fixes by name, and the one spelling it calls out.

Everything here is either quoted from the research for this workflow
(``docs/research/digital-sales-room-workflows/wf/WF-043.md``) or is the smallest
vocabulary that has to exist for one of those quotes to be enforceable. Nothing
in this module is a preference: if a value is not on this list, the API refuses
it rather than storing something the research never described.

The three facts that do real work
---------------------------------

**Change Data Capture fires on four things.** "Changes include creation of a new
record, updates to an existing record, deletion of a record, and undeletion of a
record." That sentence is the whole of the change-type vocabulary, so
:data:`CHANGE_TYPES` is exactly those four and nothing else. A vendor that also
emits gap and overflow markers is out of scope for this workflow - reconciling a
dropped stream is section 18 of the same research file and has its own ticket.

**The standard channel is one specific string, and its case matters.**
"A subscription channel is a stream of change events that correspond to one or
more entities. Change Data Capture provides predefined standard channels and you
can create your own custom channels. ... The channel name is case-sensitive."
So ``/data/ChangeEvents`` and ``/data/changeevents`` are *two different channels*,
and this build refuses to let a caller create the second one by accident without
naming it.

**Enrichment is not universal.** "Event enrichment is supported for subscribers
that use Pub/Sub API, CometD (Streaming API), or event relays." That is a closed
list of three transports, and it is the reason a delta-link subscriber cannot
ask for an enriched field.
"""

from __future__ import annotations

from typing import Any

#: "Receive near-real-time changes of Salesforce records ... Changes include
#: creation of a new record, updates to an existing record, deletion of a record,
#: and undeletion of a record." Exactly four, and the canonical spelling the
#: vendor uses on the wire.
CHANGE_TYPES: tuple[str, ...] = ("CREATE", "UPDATE", "DELETE", "UNDELETE")

#: The change types whose events carry enriched fields. "Fields that you select
#: for enrichment are included in change events for update and delete
#: operations."
ENRICHED_CHANGE_TYPES: tuple[str, ...] = ("UPDATE", "DELETE")

#: The change types whose events do not. "Enriched fields aren't included in
#: change events for create and undelete operations because these events contain
#: all the populated fields." So an enriched field on a CREATE is not a gap, and
#: treating it as one would be a defect.
UNENRICHED_CHANGE_TYPES: tuple[str, ...] = ("CREATE", "UNDELETE")

#: The predefined standard channel. Spelled exactly as the research spells it.
STANDARD_CHANNEL = "/data/ChangeEvents"

#: The same string, folded. Used only to decide whether a caller meant the
#: standard channel - never to resolve a name, because the name is case
#: sensitive.
STANDARD_CHANNEL_FOLDED = STANDARD_CHANNEL.casefold()

#: "Change Data Capture provides predefined standard channels and you can create
#: your own custom channels."
CHANNEL_KINDS: tuple[str, ...] = ("standard", "custom")

#: "Available in: Enterprise, Performance, Unlimited, and Developer editions."
#: Any other edition cannot enable CDC, and the refusal names this tuple.
CDC_EDITIONS: tuple[str, ...] = ("Enterprise", "Performance", "Unlimited", "Developer")

#: The vendor systems the research describes, and where each one's change stream
#: comes from.
CRM_SYSTEMS: tuple[str, ...] = ("salesforce", "dataverse", "hubspot")

#: The subscriber transports the research names, with the vendor each belongs to.
#:
#: ``pubsub``          Salesforce Pub/Sub API ``Subscribe`` RPC
#: ``cometd``          Salesforce CometD (Streaming API)
#: ``relay``           Salesforce event relay
#: ``delta_link``      Dataverse ``Prefer: odata.track-changes``
#: ``workflow_webhook`` HubSpot workflow webhook action
TRANSPORTS: tuple[str, ...] = ("pubsub", "cometd", "relay", "delta_link", "workflow_webhook")

TRANSPORT_VENDORS: dict[str, str] = {
    "pubsub": "salesforce",
    "cometd": "salesforce",
    "relay": "salesforce",
    "delta_link": "dataverse",
    "workflow_webhook": "hubspot",
}

#: "Event enrichment is supported for subscribers that use Pub/Sub API, CometD
#: (Streaming API), or event relays." A closed list, and the reason the
#: Dataverse and HubSpot transports cannot be enriched.
ENRICHMENT_TRANSPORTS: tuple[str, ...] = ("pubsub", "cometd", "relay")

#: "subscriber deserialisation (Avro for Pub/Sub, JSON for CometD)".
#:
#: Only those two are published. A transport the research does not name has no
#: sourced wire format, which is recorded as ``None`` rather than guessed - see
#: the ``avro-is-the-transport-clients-job`` entry in
#: :mod:`dsr.change_stream.inferences`.
WIRE_FORMATS: dict[str, str | None] = {
    "pubsub": "avro",
    "cometd": "json",
    "relay": None,
    "delta_link": None,
    "workflow_webhook": None,
}

#: "We recommend you set the buffer size to 3 MB." The default, and the
#: recommendation the usage report compares against. It is a *recommendation*,
#: so a channel that sizes differently gets a note and not a refusal.
RECOMMENDED_BUFFER_BYTES: int = 3 * 1024 * 1024

#: "The room's subscriber client opens a long-lived subscription". A
#: subscription is open or closed, and the research describes nothing in between.
SUBSCRIPTION_STATES: tuple[str, ...] = ("open", "closed")

#: The states a stored change event can be in. ``buffered`` means the event is
#: in the per-transaction buffer and has not reached the room's replica.
EVENT_STATES: tuple[str, ...] = ("buffered", "committed")

#: A replica row is live or a tombstone. The tombstone is this build's decision
#: and not a researched fact; see ``delete-is-a-tombstone`` in
#: :mod:`dsr.change_stream.inferences`.
REPLICA_STATES: tuple[str, ...] = ("live", "deleted")

#: The field on a replica row that names the buyer's panel, as the research
#: describes it: "The room refreshes the affected buyer's deal panel."
DEAL_PANEL = "deal"

#: Dataverse. "This header requests that a delta link is returned, which you can
#: later use to retrieve table changes."
CHANGE_TRACKING_PREFERENCE = "odata.track-changes"

#: The Dataverse API version the research writes its paths against.
DATAVERSE_API_VERSION = "v9.2"

#: "The entity sets that represent tables where change tracking is enabled have
#: this annotation." Kept verbatim, because a client that has to recognise the
#: annotation has to match it exactly.
CHANGE_TRACKING_ANNOTATION = (
    '<Annotation Term="Org.OData.Capabilities.V1.ChangeTracking">'
    '<Record><PropertyValue Property="Supported" Bool="true" />'
)

#: The four query options Dataverse refuses while change tracking is on, and the
#: message it refuses them with.
#:
#: "$filter, $orderby, $expand, and $top aren't supported when you use the
#: Prefer: odata.track-changes header ... If you use these query options ... you
#: get an error message: The "${filter|orderby|expand|top}" query parameter isn't
#: supported when Change Tracking is enabled."
#:
#: The pipe in the documentation is an alternation, so the message names the one
#: option the caller actually sent. ``$select`` is deliberately *not* in this
#: tuple: the research's own example poll carries it.
UNSUPPORTED_DELTA_QUERY_OPTIONS: dict[str, str] = {
    "filter": "$filter",
    "orderby": "$orderby",
    "expand": "$expand",
    "top": "$top",
}

UNSUPPORTED_DELTA_QUERY_OPTION_MESSAGE = (
    'The "{option}" query parameter isn\'t supported when Change Tracking is enabled.'
)

#: "Webhook calls made via workflows do not count towards the API rate limit."
HUBSPOT_WORKFLOW_CALLS_EXEMPT_FROM_RATE_LIMIT = True

#: "You can create up to 1,000 webhook subscriptions per app."
HUBSPOT_WEBHOOK_SUBSCRIPTION_LIMIT = 1000

#: The default budget a room's own outbound calls to the vendor draw on, so the
#: exemption above has something to be exempt *from*. Not researched; see
#: ``hubspot-rate-limit-budget`` in :mod:`dsr.change_stream.inferences`.
DEFAULT_API_CALL_BUDGET = 100


def normalise_change_type(value: Any) -> str | None:
    """The canonical spelling of a change type, or ``None`` if it is not one.

    Case is folded before the comparison and the result is upper-cased, because
    the research's case-sensitivity rule is about *channel names* and says
    nothing about how a client spells ``changeType``. Folding the case and
    refusing an unknown word are different decisions; only the second one is
    the research's.
    """
    if not isinstance(value, str):
        return None
    upper = value.strip().upper()
    return upper if upper in CHANGE_TYPES else None


def is_enriched_change_type(change_type: str) -> bool:
    """Whether an event of this type carries enriched fields.

    This is the difference the research draws: create and undelete "contain all
    the populated fields", so an enriched field on one of them is redundant
    rather than missing.
    """
    return change_type in ENRICHED_CHANGE_TYPES


def is_standard_channel(name: str) -> bool:
    """Whether ``name`` names the standard channel, whatever its casing.

    Used only to decide whether a caller is asking for the standard channel, so
    that the enrichment-isolation rule can refuse them. Resolution itself is
    case-sensitive, and deliberately does not go through here.
    """
    return isinstance(name, str) and name.strip().casefold() == STANDARD_CHANNEL_FOLDED


def wire_format_for(transport: str) -> str | None:
    """The deserialisation format the research names for this transport."""
    return WIRE_FORMATS.get(transport)


def supports_enrichment(transport: str) -> bool:
    """Whether the research says this transport supports event enrichment."""
    return transport in ENRICHMENT_TRANSPORTS


def edition_supports_cdc(edition: Any) -> bool:
    """Whether Change Data Capture is available in this edition."""
    if not isinstance(edition, str):
        return False
    folded = {name.casefold() for name in CDC_EDITIONS}
    return edition.strip().casefold() in folded


def vendor_for(transport: str) -> str:
    """The vendor whose mechanism this transport is."""
    return TRANSPORT_VENDORS.get(transport, "")


def describe() -> dict[str, Any]:
    """Every vocabulary this package enforces, served as data.

    A client renders its pickers from this rather than from a list compiled into
    a page, so a value added here reaches every client at once, and a caller
    asking "what does this API accept" gets an answer rather than a 400.
    """
    return {
        "change_types": list(CHANGE_TYPES),
        "enriched_change_types": list(ENRICHED_CHANGE_TYPES),
        "unenriched_change_types": list(UNENRICHED_CHANGE_TYPES),
        "change_type_note": (
            "create and undelete events contain all the populated fields, so an "
            "enriched field on one of them is redundant rather than missing"
        ),
        "standard_channel": STANDARD_CHANNEL,
        "channel_name_is_case_sensitive": True,
        "channel_kinds": list(CHANNEL_KINDS),
        "cdc_editions": list(CDC_EDITIONS),
        "crm_systems": list(CRM_SYSTEMS),
        "transports": [
            {
                "id": name,
                "vendor": vendor_for(name),
                "wire_format": wire_format_for(name),
                "supports_enrichment": supports_enrichment(name),
            }
            for name in TRANSPORTS
        ],
        "recommended_buffer_bytes": RECOMMENDED_BUFFER_BYTES,
        "subscription_states": list(SUBSCRIPTION_STATES),
        "event_states": list(EVENT_STATES),
        "replica_states": list(REPLICA_STATES),
        "deal_panel": DEAL_PANEL,
        "dataverse": {
            "api_version": DATAVERSE_API_VERSION,
            "track_changes_preference": CHANGE_TRACKING_PREFERENCE,
            "change_tracking_annotation": CHANGE_TRACKING_ANNOTATION,
            "unsupported_query_options": dict(UNSUPPORTED_DELTA_QUERY_OPTIONS),
            "unsupported_query_option_message": UNSUPPORTED_DELTA_QUERY_OPTION_MESSAGE,
            "change_tracking_is_irreversible": True,
        },
        "hubspot": {
            "workflow_calls_exempt_from_rate_limit": HUBSPOT_WORKFLOW_CALLS_EXEMPT_FROM_RATE_LIMIT,
            "webhook_subscription_limit": HUBSPOT_WEBHOOK_SUBSCRIPTION_LIMIT,
            "subscription_rest_api": (
                "not implemented; the research records that the webhook subscriptions "
                "REST page could not be read, so no endpoint or method is claimed"
            ),
        },
    }
