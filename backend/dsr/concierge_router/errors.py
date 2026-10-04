"""One error hierarchy for the concierge-router package.

Every refusal this package makes about a *request* is a caller's mistake or a
conflict with state that already exists, so the types share a base and the feature
module registers a single handler for it. Anything that is not a
:class:`RouterError` is a bug and must propagate.

``code`` and ``status`` ride on the exception rather than being decided inside the
handler, because these refusals are not all the same kind of thing. A router
declared with no ``Trigger`` node, or a chain with no ``Catch All``, is a 400:
the declaration could never have worked. A booking against a ``routeId`` that has
already been committed is a 409, because the request was well formed and what it
conflicts with is the state of the system. A handler that answered 400 for both
would be lying about the second.

FastAPI only accepts exception handlers on the app object, so the feature module
exports this mapping as ``EXCEPTION_HANDLERS``; two features may not map the same
type, which is why the whole hierarchy hangs off one base class.
"""

from __future__ import annotations


class RouterError(ValueError):
    """A concierge-router request cannot be honoured as written.

    A ``ValueError`` because every subclass is caused by something the caller
    sent, or by state the caller asked about. Nothing in this package raises for a
    fault of its own.
    """

    code = "router_error"
    status = 400


# --------------------------------------------------------------------------- #
# The declaration: a router cannot be published in the shape it was sent
# --------------------------------------------------------------------------- #


class RouterDeclarationError(RouterError):
    """The router cannot be declared as asked.

    Every subclass is a shape the researched flow says could not work, so each one
    is refused where the router is saved rather than discovered by a prospect.
    """

    code = "router_declaration_invalid"


class TriggerMustBeFirst(RouterDeclarationError):
    """The ``Trigger`` node is not the first node in the flow.

    "Trigger will **always** be your first node in a Concierge Router, and it
    indicates which action will make your router be triggered." The word is
    *always*, so this build enforces the ordering on save rather than treating it
    as a convention the builder UI happens to follow. A router whose first node is
    a routing rule has no declared trigger, so nothing can start it.
    """

    code = "router_trigger_not_first"


class TriggerActionMissing(RouterDeclarationError):
    """The ``Trigger`` node names none of the researched triggers.

    The node "enables ``Webform is submitted``, ``In-app``, and/or ``Router
    Link``". A trigger node that enables none of them is triggered by nothing.
    """

    code = "router_trigger_action_missing"


class RulesDoNotFallThrough(RouterDeclarationError):
    """The node chain does not end in a ``Catch All``.

    "Each router **must** end with a '**Catch All**' path to make sure you define
    the routing and acknowledge all inbound Leads." Refusing the declaration is the
    fix: a chain that can run out is an inbound lead who reaches the end of the
    router and is never acknowledged. For a well-formed router, *no host matched*
    is therefore unreachable at run time.
    """

    code = "router_rules_no_catch_all"


class NodeError(RouterDeclarationError):
    """A node cannot be placed where it was sent.

    Named separately from the rest because the remedy is a different node each
    time.
    """

    code = "router_node_invalid"


class UnknownNodeType(NodeError):
    """A node is not one of the researched node types.

    Refused by name so a typo becomes a message rather than a rule that never
    fires.
    """

    code = "router_node_unknown_type"


class UnknownRouter(RouterDeclarationError):
    """No router with that id exists."""

    code = "router_not_found"
    status = 404


class SellerNotUsable(RouterDeclarationError):
    """A seller cannot be registered as asked.

    The researched ``Display Calendar`` node chooses between Owner, Round-Robin and
    Individual user, and every one of those resolves to a seller row. A seller
    with no name is therefore not a record the router can ever book on, so it is
    refused where it is written rather than at the moment a prospect is routed to
    a seller who does not exist.
    """

    code = "router_seller_not_usable"


class FieldMappingError(RouterDeclarationError):
    """The ``Trigger`` node's field map is not a usable map.

    A webform field mapped onto a Data Field is how a rule can read what the
    prospect typed, so a map that names a field twice or maps nothing is refused
    where it is declared.
    """

    code = "router_field_map_invalid"


class TimerShorterThanElapsed(NodeError):
    """A timer's new deadline falls before the minutes a live session has elapsed.

    The ``Display Calendar`` node's **Time Elapsed** timer decides when a routing
    session becomes "not scheduled". Shortening it below the minutes already
    elapsed would retire a prospect who is still looking at the calendar, so the
    change is refused where the node is declared rather than at the moment it
    would hurt somebody.
    """

    code = "router_timer_shorter_than_elapsed"


class RedirectWithoutUrl(NodeError):
    """A ``Redirect To`` node carries no destination.

    "``Redirect To`` has its own countdown timer before bouncing the prospect." A
    countdown with nothing to bounce to is a timer that can only expire into a
    dead end, so the node is refused at declaration.
    """

    code = "router_redirect_without_url"


# --------------------------------------------------------------------------- #
# The two researched calls
# --------------------------------------------------------------------------- #


class RouteRequestError(RouterError):
    """The route request cannot be evaluated as written.

    A 400: the request named a router that refuses to answer, sent a form field
    nothing reads, or asked for a meeting type the ``Display Calendar`` node does
    not offer.
    """

    code = "route_request_invalid"


class RouterNotPublished(RouteRequestError):
    """The router exists but is not deployed.

    "The router is published and deployed (embedded/deployed to web form, in-app
    button, or a router link)." An unpublished router accepts no inbound request,
    and answering it with a route id would claim a deployment that does not exist.
    """

    code = "router_not_published"


class RouterUnavailable(RouteRequestError):
    """The router is published but is switched off.

    Distinct from :class:`RouterNotPublished`: the deployment exists and somebody
    turned the router off, which is a different repair from never having deployed
    it.
    """

    code = "router_disabled"


class NoRuleMatched(RouteRequestError):
    """The chain ran out without a catch-all.

    Unreachable for a router that saved, because
    :func:`~dsr.concierge_router.nodes.require_catch_all` refuses one. It stays a
    distinct error rather than being folded into the catch-all path because a
    router row imported from outside this package can carry a broken chain, and
    "your saved router has no catch-all" is a different sentence for an operator
    than "the prospect reached the deal desk".
    """

    code = "router_no_rule_matched"


class GuestIdentityRequired(RouteRequestError):
    """The inbound request carries no guest email and no CRM record id.

    The researched data flow reads "webform POST -> Trigger (form-field -> Data
    Field mapping) -> routing rule evaluation against (a) Chili Piper Data Fields
    and (b) live CRM object values". The CRM half of that needs an identity, so a
    request with neither is a request whose CRM rules can never match. It is not a
    refusal of the whole route: a router whose rules are all Data Field rules can
    still answer, which is exactly what this build does.
    """

    code = "router_guest_identity_missing"


class MeetingTypeNotOffered(RouteRequestError):
    """A booking names a meeting type the matched ``Display Calendar`` does not offer.

    "plus the **Meeting Type(s)** to offer" - so a slot is only bookable under a
    type the node offered, and the type on the booking is the one the prospect saw.
    """

    code = "router_meeting_type_not_offered"


# --------------------------------------------------------------------------- #
# The second call: the session the first call opened
# --------------------------------------------------------------------------- #


class RouteSessionError(RouterError):
    """The ``routeId`` named by the second call is not usable.

    409 for the same reason as the rest of the session errors: the request is well
    formed, the state is what conflicts. 404 where the id names nothing at all,
    because "no such route" and "that route is gone" are different answers.
    """

    code = "router_route_conflict"
    status = 409


class RouteNotFound(RouteSessionError):
    """No routing session with that ``routeId`` exists."""

    code = "router_route_not_found"
    status = 404


class RouteNotSchedulable(RouteNotFound):
    """The route exists but the router answered ``schedulingAllowed: false``.

    The researched route response carries ``schedulingAllowed``, so a false there
    is the product saying this prospect may not book. Answering with slots anyway
    would contradict the field the vendor's own response carries.
    """

    code = "router_not_schedulable"


class RouteConsumed(RouteSessionError):
    """The routing session has already been booked.

    The researched second call "commit[s] the chosen slot" and returns a
    ``meetingId``. A second commit on one session would put two prospects in one
    slot on one seller's calendar, so a consumed session refuses rather than
    re-answering.
    """

    code = "router_route_consumed"


class SlotNotOffered(RouteSessionError):
    """The requested start time is not one the route offered.

    The route response names the assignee and the scheduling permission, and the
    slot list is what the ``Display Calendar`` modal rendered. A time that was
    never offered is not merely out of stock: it is a time the seller's calendar
    was never shown to have free.
    """

    code = "router_slot_not_offered"


class GuestMismatch(RouteSessionError):
    """The guest on the second call is not the guest the session was opened for.

    The researched webhook payload is ``primaryGuestDataFields``, so the booking
    belongs to one guest. A different address on the second call is not the
    prospect who chose the slot.
    """

    code = "router_guest_mismatch"


class AssignmentNotBookable(RouteSessionError):
    """The matched assignment names no seller with a connected calendar.

    "chosen calendar/Meeting Type/assignee -> availability engine (Google/Outlook
    calendars)". The assignee is resolved by the rule; the calendar is what cannot
    be read. A seller with no connected calendar has no availability at all, so
    there is no slot to commit.
    """

    code = "router_assignee_not_bookable"
