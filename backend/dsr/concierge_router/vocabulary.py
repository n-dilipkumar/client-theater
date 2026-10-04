"""The researched terms for WF-051, each beside the sentence that fixes it.

The research for this workflow is a description of a Concierge Router built in
Chili Piper's Flow Builder. Most of its vocabulary is quoted there: the node
types, the three trigger actions, the two rule kinds, the three assignment
choices, the two Edge API calls, and the webhook payload name. This module holds
those, and holds the ones this build had to *choose* separately in
:mod:`dsr.concierge_router.inferences`, so the line between what was sourced and
what was picked is visible in one place.

Everything here is served at ``GET /api/wf-051/vocabulary`` as data, so a client
renders its pickers from the same source the validator enforces against and a
term added here reaches every client at once.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# --------------------------------------------------------------------------- #
# The nodes
# --------------------------------------------------------------------------- #

#: The node a router's flow always begins with.
#: "Trigger will **always** be your first node in a Concierge Router, and it
#: indicates which action will make your router be triggered"
TRIGGER = "trigger"

#: "Admin adds `Routing Rule` / `Catch All` nodes."
ROUTING_RULE = "routing_rule"
CATCH_ALL = "catch_all"

#: "On a rule match, admin adds a `Display Calendar` node"
DISPLAY_CALENDAR = "display_calendar"

#: "The router's default post-booking nodes fire (e.g. `Create Event`, `Update
#: Field`, `Update Ownership`)"
CREATE_EVENT = "create_event"
UPDATE_FIELD = "update_field"
UPDATE_OWNERSHIP = "update_ownership"
REDIRECT_TO = "redirect_to"

#: The `Not Scheduled` path nodes.
ASSIGN_TO = "assign_to"
SEND_NOTIFICATION = "send_notification"

#: Every node type the research names. A node outside this set is refused on save.
NODE_TYPES: tuple[str, ...] = (
    TRIGGER,
    ROUTING_RULE,
    CATCH_ALL,
    DISPLAY_CALENDAR,
    CREATE_EVENT,
    UPDATE_FIELD,
    UPDATE_OWNERSHIP,
    REDIRECT_TO,
    ASSIGN_TO,
    SEND_NOTIFICATION,
)

#: The nodes that read the rules, and the two that end a chain.
RULE_NODE_TYPES: tuple[str, ...] = (ROUTING_RULE, CATCH_ALL)

#: The node that offers the seller's calendar to the prospect.
CALENDAR_NODE_TYPES: tuple[str, ...] = (DISPLAY_CALENDAR,)

#: The nodes that fire *after* a booking is committed. "The router's default
#: post-booking nodes fire (e.g. `Create Event`, `Update Field`, `Update
#: Ownership`) and/or `Redirect To` a thank-you page."
POST_BOOKING_NODE_TYPES: tuple[str, ...] = (
    CREATE_EVENT,
    UPDATE_FIELD,
    UPDATE_OWNERSHIP,
    REDIRECT_TO,
)

#: The nodes on the researched `Not Scheduled` path. "``Not Scheduled`` paths run
#: ``Assign To`` (distribute the prospect) and ``Send Notification`` (email or
#: Slack)"
NOT_SCHEDULED_NODE_TYPES: tuple[str, ...] = (ASSIGN_TO, SEND_NOTIFICATION)

#: Nodes carrying the researched **Time Elapsed** timer. "`Redirect To` has its own
#: countdown timer before bouncing the prospect", and the ``Display Calendar``
#: node's timer decides when a booking is abandoned.
TIMER_NODE_TYPES: tuple[str, ...] = (DISPLAY_CALENDAR, REDIRECT_TO)


# --------------------------------------------------------------------------- #
# The trigger actions
# --------------------------------------------------------------------------- #

#: "First node is always `Trigger`; enables `Webform is submitted`, `In-app`,
#: and/or `Router Link`."
WEBFORM_SUBMITTED = "webform_is_submitted"
IN_APP = "in_app"
ROUTER_LINK = "router_link"

TRIGGER_ACTIONS: tuple[str, ...] = (WEBFORM_SUBMITTED, IN_APP, ROUTER_LINK)


# --------------------------------------------------------------------------- #
# The rule kinds
# --------------------------------------------------------------------------- #

#: "Rules are either **CRM Ownership** rules (check Lead/Contact/Account owner
#: against a **Team**) or **Without Ownership** rules (CRM values or Data Field
#: values)."
CRM_OWNERSHIP_RULE = "crm_ownership"
WITHOUT_OWNERSHIP_RULE = "without_ownership"

RULE_KINDS: tuple[str, ...] = (CRM_OWNERSHIP_RULE, WITHOUT_OWNERSHIP_RULE)

#: The CRM objects a CRM Ownership rule checks the owner of. The researched
#: ``data_flow`` names five: "Lead/Contact/Account/Opportunity/Case, incl.
#: Salesforce Lead-to-Account matching". The rule kind itself names three, so the
#: object set a *rule* may check is those three. ``opportunity`` and ``case`` are
#: reachable as ``Without Ownership`` field sources instead - see
#: ``CRM_FIELD_SOURCES`` and the ``lead-case-opportunity`` inference.
CRM_OWNERSHIP_OBJECTS: tuple[str, ...] = ("lead", "contact", "account")

#: Every object the researched ``data_flow`` names as a live CRM object value.
#: Listed separately from :data:`CRM_OWNERSHIP_OBJECTS` because two of the five
#: are reachable only as field values.
CRM_FIELD_SOURCES: tuple[str, ...] = (
    "lead",
    "contact",
    "account",
    "opportunity",
    "case",
)

#: The vendor spellings, as the research names them. Salesforce is the only vendor
#: the research spells out per object, so the HubSpot names come from the
#: research's own HubSpot object list ("HubSpot `Contact`, `Company`, `Deal`,
#: `Ticket`").
CRM_VENDOR_NAMES: dict[str, dict[str, str]] = {
    "lead": {"salesforce": "Lead", "hubspot": "Contact"},
    "contact": {"salesforce": "Contact", "hubspot": "Contact"},
    "account": {"salesforce": "Account", "hubspot": "Company"},
    "opportunity": {"salesforce": "Opportunity", "hubspot": "Deal"},
    "case": {"salesforce": "Case", "hubspot": "Ticket"},
}

#: The objects whose owner a CRM Ownership rule can check.
CRM_OWNERSHIP_OWNER_BEARING = CRM_OWNERSHIP_OBJECTS


# --------------------------------------------------------------------------- #
# The assignment choices
# --------------------------------------------------------------------------- #

#: "admin adds a `Display Calendar` node choosing **Owner**, **Round-Robin**, or
#: **Individual user**"
OWNER_ASSIGNMENT = "owner"
ROUND_ROBIN_ASSIGNMENT = "round_robin"
INDIVIDUAL_USER_ASSIGNMENT = "individual_user"

ASSIGNMENT_TYPES: tuple[str, ...] = (
    OWNER_ASSIGNMENT,
    ROUND_ROBIN_ASSIGNMENT,
    INDIVIDUAL_USER_ASSIGNMENT,
)

#: How each assignment names its target in the researched response. The sample
#: carries `"assignment": { "userId": "...", "type": "user" }`, so ``user`` is the
#: wire spelling of :data:`INDIVIDUAL_USER_ASSIGNMENT`. ``Owner`` and
#: ``Round-Robin`` name a *policy* rather than a user, and the researched
#: extensibility note explains why: "Assignment Tables let one path serve every
#: territory" and the `Not Scheduled` path's `Assign To` "distribute[s] the
#: prospect". So those two carry a policy reference rather than a resolved user,
#: and the resolved user lands beside them once the engine has run the policy.
ASSIGNMENT_WIRE_TYPE: dict[str, str] = {
    OWNER_ASSIGNMENT: "owner",
    ROUND_ROBIN_ASSIGNMENT: "round_robin",
    INDIVIDUAL_USER_ASSIGNMENT: "user",
}


# --------------------------------------------------------------------------- #
# The deployment surfaces
# --------------------------------------------------------------------------- #

#: "The router is published and deployed (embedded/deployed to web form, in-app
#: button, or a router link)."
WEB_FORM = "web_form"
IN_APP_BUTTON = "in_app_button"
ROUTER_LINK_DEPLOYMENT = "router_link"

DEPLOYMENT_SURFACES: tuple[str, ...] = (WEB_FORM, IN_APP_BUTTON, ROUTER_LINK_DEPLOYMENT)

PUBLISHED = "published"
DRAFT = "draft"
ARCHIVED = "archived"

PUBLISH_STATES: tuple[str, ...] = (PUBLISHED, DRAFT, ARCHIVED)


# --------------------------------------------------------------------------- #
# The researched API calls
# --------------------------------------------------------------------------- #

#: "Edge API token with `Concierge.schedule` scope, generated in `Command Center >
#: Credentials`."
EDGE_SCOPE = "Concierge.schedule"

#: The two researched calls, with the paths the issue quotes. Kept as data rather
#: than as the only copy of the strings, so a client renders the mapping rather
#: than compiling it.
EDGE_ROUTE_OPERATION = "POST /org/concierge/routers/[routerSlug]/rest"
EDGE_SCHEDULE_OPERATION = "POST /org/concierge/routing/[routeId]/schedule-simple"

EDGE_ENDPOINTS: dict[str, str] = {
    "route": "POST https://fire.chilipiper.com/api/fire-edge/v1/org/concierge/routers/"
    "[routerSlug]/rest",
    "schedule": "POST https://fire.chilipiper.com/api/fire-edge/v1/org/concierge/routing/"
    "[routeId]/schedule-simple",
}

#: The fields the researched route response names. "the route response shape is
#: documented only as a sample: `"routeId"`, `"routingLink"`, `"schedulingAllowed"`,
#: `assignment{userId,type}`"
ROUTE_RESPONSE_FIELDS: tuple[str, ...] = (
    "routeId",
    "routingLink",
    "schedulingAllowed",
    "assignment",
)
ASSIGNMENT_FIELDS: tuple[str, ...] = ("userId", "type")

#: The second call "returns `meetingId`".
SCHEDULE_RESPONSE_FIELDS: tuple[str, ...] = ("meetingId",)


# --------------------------------------------------------------------------- #
# The booking states
# --------------------------------------------------------------------------- #

#: The route session waits for a commit.
PENDING = "pending"

#: A slot was committed. The researched second call returns a ``meetingId``.
BOOKED = "booked"

#: The researched timer fired: "when it expires the meeting will be considered not
#: scheduled, and you can notify your rep to follow up". See the
#: ``time-elapsed-makes-a-booking-not-scheduled`` inference for why this is a
#: state rather than a deletion.
NOT_SCHEDULED = "not_scheduled"

#: The prospect was told the meeting was not scheduled and the router did not
#: offer a slot in the first place, so no timer was needed.
NOT_OFFERED = "not_offered"

BOOKING_STATES: tuple[str, ...] = (PENDING, BOOKED, NOT_SCHEDULED, NOT_OFFERED)

TERMINAL_BOOKING_STATES: tuple[str, ...] = (BOOKED, NOT_SCHEDULED, NOT_OFFERED)

#: The states a page may still act on. ``not_scheduled`` is not here: the researched
#: follow-up runs off the state rather than a caller asking again.
OPEN_BOOKING_STATES: tuple[str, ...] = (PENDING,)


# --------------------------------------------------------------------------- #
# The routing outcomes
# --------------------------------------------------------------------------- #

#: A named rule matched and routed the prospect.
RULE_MATCHED = "rule_matched"

#: The chain reached its catch-all, so the prospect is acknowledged without a rule
#: having claimed them.
CATCH_ALL_MATCHED = "catch_all_matched"

#: A rule matched and then deliberately declined, so no calendar is offered.
RULE_DECLINED = "rule_declined"

ROUTING_OUTCOMES: tuple[str, ...] = (RULE_MATCHED, CATCH_ALL_MATCHED, RULE_DECLINED)


# --------------------------------------------------------------------------- #
# The guest payload
# --------------------------------------------------------------------------- #

#: "Guests become the `primaryGuestDataFields` payload in webhooks."
PRIMARY_GUEST_PAYLOAD = "primaryGuestDataFields"

#: The fields this build puts in that payload, and where each comes from. The
#: research names the payload and no field of it, so the list is derived and
#: recorded - see the ``primary-guest-data-fields`` inference.
PRIMARY_GUEST_FIELDS: tuple[dict[str, Any], ...] = (
    {
        "name": "firstName",
        "required": False,
        "from": "form_field",
        "note": "the prospect's given name as the mapped webform field spells it",
    },
    {
        "name": "lastName",
        "required": False,
        "from": "form_field",
        "note": "the prospect's family name as the mapped webform field spells it",
    },
    {
        "name": "email",
        "required": True,
        "from": "form_field",
        "note": "the one field with no substitute: the CRM rules read a live object, and an "
        "object is found by address",
    },
    {
        "name": "company",
        "required": False,
        "from": "form_field",
        "note": "the company the prospect typed; the Account is still matched by domain",
    },
    {
        "name": "phone",
        "required": False,
        "from": "form_field",
        "note": "a free-text phone from the form, which is the only form of it the research has",
    },
    {
        "name": "ownerId",
        "required": False,
        "from": "routing_result",
        "note": "the seller the rule assigned, under the name the researched assignment "
        "response uses",
    },
    {
        "name": "meetingType",
        "required": False,
        "from": "routing_result",
        "note": "the Meeting Type the matched Display Calendar node offered",
    },
)

#: The names a guest may send under. Chili Piper's own payload is camelCase, and a
#: marketing webform posts snake_case or kebab-case HTML field names, so both are
#: accepted and normalised to the camelCase above.
GUEST_EMAIL_ALIASES: tuple[str, ...] = (
    "email",
    "guestEmail",
    "primaryGuestEmail",
    "work_email",
    "workEmail",
    "email_address",
    "emailAddress",
)

GUEST_FIRST_NAME_ALIASES: tuple[str, ...] = ("firstName", "first_name", "firstname", "given_name")
GUEST_LAST_NAME_ALIASES: tuple[str, ...] = (
    "lastName",
    "last_name",
    "lastname",
    "family_name",
    "surname",
)
GUEST_COMPANY_ALIASES: tuple[str, ...] = (
    "company",
    "company_name",
    "companyName",
    "account",
    "organisation",
)
GUEST_PHONE_ALIASES: tuple[str, ...] = (
    "phone",
    "phone_number",
    "phoneNumber",
    "telephone",
    "mobile",
)


# --------------------------------------------------------------------------- #
# The notification channels
# --------------------------------------------------------------------------- #

#: "`Send Notification` (email or Slack)"
EMAIL_CHANNEL = "email"
SLACK_CHANNEL = "slack"

NOTIFICATION_CHANNELS: tuple[str, ...] = (EMAIL_CHANNEL, SLACK_CHANNEL)


# --------------------------------------------------------------------------- #
# Defaults this build chose, each named rather than written inline
# --------------------------------------------------------------------------- #

#: The researched **Time Elapsed** timer on the `Display Calendar` node. The
#: research names the behaviour ("the meeting will be considered not scheduled")
#: and publishes no duration, so this is a named default a node can override.
DEFAULT_TIMER_MINUTES = 60

#: The largest number of slots one route call offers. The researched response
#: names no bound, so this build needs one: an unbounded list over a long window is
#: a response nobody can render.
DEFAULT_MAX_SLOTS = 40

#: The furthest ahead a route call looks for availability.
DEFAULT_HORIZON_DAYS = 21

#: The meeting length when the router's meeting type declares none.
DEFAULT_DURATION_MINUTES = 30

#: The round-robin cursor's position, so a rotation is reproducible rather than
#: dependent on how many rows the store happened to return.
DEFAULT_ROUND_ROBIN_POSITION = 0


# --------------------------------------------------------------------------- #
# Lookups and validation
# --------------------------------------------------------------------------- #


def _one_of(value: str, allowed: tuple[str, ...], what: str) -> str:
    candidate = str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")
    if candidate not in allowed:
        raise ValueError(f"{what} must be one of {', '.join(allowed)}; got {value!r}")
    return candidate


def require_node_type(value: str) -> str:
    """Normalise a node type, or refuse a type the research does not name."""
    return _one_of(value, NODE_TYPES, "node type")


def require_trigger_action(value: str) -> str:
    """Normalise a trigger action, or refuse one the research does not name."""
    return _one_of(value, TRIGGER_ACTIONS, "trigger action")


def require_rule_kind(value: str) -> str:
    """Normalise a rule kind, or refuse one the research does not name."""
    return _one_of(value, RULE_KINDS, "rule kind")


def require_assignment_type(value: str) -> str:
    """Normalise an assignment choice, or refuse one the research does not name."""
    return _one_of(value, ASSIGNMENT_TYPES, "assignment type")


def require_crm_object(value: str) -> str:
    """Normalise a CRM object the ``data_flow`` names, or refuse another."""
    return _one_of(value, CRM_FIELD_SOURCES, "CRM object")


def require_ownership_object(value: str) -> str:
    """Normalise an object a CRM Ownership *rule* may check the owner of.

    Only the three the rule kind names. ``opportunity`` and ``case`` are
    refused here with a message naming the field route instead, because they
    carry values rather than owners in this build - see the
    ``lead-case-opportunity`` inference.
    """
    candidate = _one_of(value, CRM_FIELD_SOURCES, "CRM object")
    if candidate not in CRM_OWNERSHIP_OBJECTS:
        raise ValueError(
            f"a CRM Ownership rule checks the owner of {', '.join(CRM_OWNERSHIP_OBJECTS)}; "
            f"{candidate!r} is a field value, so rule it as a Without Ownership rule instead"
        )
    return candidate


def require_deployment_surface(value: str) -> str:
    """Normalise a deployment surface, or refuse one the research does not name."""
    return _one_of(value, DEPLOYMENT_SURFACES, "deployment surface")


def require_publish_state(value: str) -> str:
    """Normalise a publish state, or refuse one this build does not have."""
    return _one_of(value, PUBLISH_STATES, "publish state")


def require_booking_state(value: str) -> str:
    """Normalise a booking state, or refuse one this build does not have."""
    return _one_of(value, BOOKING_STATES, "booking state")


def require_notification_channel(value: str) -> str:
    """Normalise a notification channel, or refuse one the research does not name."""
    return _one_of(value, NOTIFICATION_CHANNELS, "notification channel")


def normalise_rule_kind_for_node(value: str) -> str | None:
    """The rule kind a node carries, or None when it is not a rule node.

    ``crm_ownership`` is also a valid node ``kind`` for a rule that carries no
    conditions, so an unrecognised kind on a rule node is None rather than an
    error here - :func:`~dsr.concierge_router.nodes.require_catch_all` decides
    whether the chain is usable.
    """
    try:
        return require_rule_kind(value)
    except ValueError:
        return None


def normalise_field_name(value: str) -> str:
    """Fold a form field name to the snake_case this build stores it under.

    A marketing webform posts whatever HTML ``name`` attribute the page author
    wrote, and the research's own map is by hand ("``Find Form`` -> ``Map Fields``
    automap, or ``Add Mapping`` manual mapping"). Folding both sides the same way
    is what lets a hand-written map match a field the form spells in camelCase.

    Every character that is not a letter or a digit becomes one underscore, and
    runs of underscores collapse to a single one. That is what makes
    ``work-email``, ``workEmail`` and ``work_email`` the same field name. Treating
    an underscore as a letter would leave ``work_email`` alone while a camelCase
    sibling became ``work_email`` too, so the two spellings of one field would
    stop agreeing.
    """
    text = str(value or "").strip()
    out: list[str] = []
    for index, char in enumerate(text):
        if char.isupper() and index and (not text[index - 1].isupper()):
            out.append("_")
        if char.isalnum():
            out.append(char.lower())
        else:
            out.append("_")
    folded = "".join(out)
    return "_".join(part for part in folded.split("_") if part)


def utcnow() -> datetime:
    """An aware UTC datetime. The one place the package reads the wall clock."""
    return datetime.now(timezone.utc)


def parse_timestamp(value: Any) -> datetime:
    """Coerce a timestamp to an aware UTC datetime.

    A naive timestamp is read as UTC rather than refused: a webform post and a
    router declaration both carry times nobody labelled, and refusing them would
    refuse ordinary input. A value that is not a timestamp at all raises
    ``ValueError``, which the callers turn into a domain refusal.
    """
    if isinstance(value, datetime):
        moment = value
    else:
        text = str(value or "").strip()
        if not text:
            raise ValueError("a timestamp is required")
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def iso(moment: datetime) -> str:
    """Render a UTC instant in the researched payload's own spelling."""
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def published_vocabulary() -> dict[str, Any]:
    """Every published term, as data.

    A read with no side effect, so it needs no store. Grouped the way a builder
    page groups pickers: what the flow is made of, what starts it, how a rule
    decides, what a match hands the prospect, where a router can be deployed, the
    two API calls, and the states a booking can be in.
    """
    return {
        "nodes": {
            "types": list(NODE_TYPES),
            "rule_nodes": list(RULE_NODE_TYPES),
            "calendar_nodes": list(CALENDAR_NODE_TYPES),
            "post_booking_nodes": list(POST_BOOKING_NODE_TYPES),
            "not_scheduled_nodes": list(NOT_SCHEDULED_NODE_TYPES),
            "timer_nodes": list(TIMER_NODE_TYPES),
            "trigger": TRIGGER,
            "catch_all": CATCH_ALL,
        },
        "trigger_actions": list(TRIGGER_ACTIONS),
        "rules": {
            "kinds": list(RULE_KINDS),
            "ownership_objects": list(CRM_OWNERSHIP_OBJECTS),
            "field_sources": list(CRM_FIELD_SOURCES),
            "vendor_names": {name: dict(map_) for name, map_ in CRM_VENDOR_NAMES.items()},
        },
        "assignment": {
            "types": list(ASSIGNMENT_TYPES),
            "wire_type": dict(ASSIGNMENT_WIRE_TYPE),
        },
        "deployment": {
            "surfaces": list(DEPLOYMENT_SURFACES),
            "states": list(PUBLISH_STATES),
        },
        "api": {
            "edge_scope": EDGE_SCOPE,
            "operations": dict(EDGE_ENDPOINTS),
            "route_response_fields": list(ROUTE_RESPONSE_FIELDS),
            "assignment_fields": list(ASSIGNMENT_FIELDS),
            "schedule_response_fields": list(SCHEDULE_RESPONSE_FIELDS),
        },
        "bookings": {
            "states": list(BOOKING_STATES),
            "terminal_states": list(TERMINAL_BOOKING_STATES),
            "open_states": list(OPEN_BOOKING_STATES),
            "routing_outcomes": list(ROUTING_OUTCOMES),
            "guest_payload": PRIMARY_GUEST_PAYLOAD,
            "guest_fields": [dict(field) for field in PRIMARY_GUEST_FIELDS],
            "guest_email_aliases": list(GUEST_EMAIL_ALIASES),
        },
        "notifications": {"channels": list(NOTIFICATION_CHANNELS)},
        "defaults": {
            "timer_minutes": DEFAULT_TIMER_MINUTES,
            "max_slots": DEFAULT_MAX_SLOTS,
            "horizon_days": DEFAULT_HORIZON_DAYS,
            "duration_minutes": DEFAULT_DURATION_MINUTES,
            "round_robin_position": DEFAULT_ROUND_ROBIN_POSITION,
        },
        "quotes": {
            "router": (
                "A Concierge Router is an online scheduler that can be integrated with your form "
                "or application or shared via a link. Once a prospect submits a form, the "
                "Concierge automatically qualifies them, routes them to the correct salesperson, "
                "and displays a calendar to instantly book a time - all in seconds."
            ),
            "trigger_first": (
                "Trigger will **always** be your first node in a Concierge Router, and it "
                "indicates which action will make your router be triggered"
            ),
            "catch_all": (
                "Each router **must** end with a '**Catch All**' path to make sure you define the "
                "routing and acknowledge all inbound Leads."
            ),
            "time_elapsed": (
                "when it expires the meeting will be considered not scheduled, and you can notify "
                "your rep to follow up"
            ),
            "route_response": (
                '"routeId": "...", "routingLink": "...", "schedulingAllowed": true, '
                '"assignment": { "userId": "...", "type": "user" }'
            ),
        },
    }
