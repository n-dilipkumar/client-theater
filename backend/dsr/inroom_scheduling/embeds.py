"""The embed: what a room installs, and which components it is allowed to render.

Step 1 and step 2 of the researched user flow, and the one product decision they
add up to.

Step 1: "Builder installs the embeddable booking components and stands up an OAuth
client so the app can act on behalf of a scheduling user."

Step 2: "The sales room renders a **Booker** (and optionally an **Availability**
calendar, **Event Type** editor, calendar-connect buttons for Google/Outlook/Apple,
and a payment form)."

So an embed is a per-room record: the event type the room books against, the
components the room may render, the CSS custom properties, and the token the
whole thing acts with. Four constraints are enforced here rather than documented
and hoped for.

**A room must be able to book.** The research's step 5 is that "the prospect books
entirely in-room; no Chili-Piper-like external page is shown", which is
meaningless for a room whose embed renders only an event type editor. A room
saving an embed with no booking component is refused, with the set named.

**The optional components really are optional.** Availability, event type,
calendar connect and payment form may each be absent. The research says
"optionally" for the first two and "and a payment form" for the rest, and a room
that only wants a Booker should not have to configure an Availability calendar to
satisfy a validator.

**A payment form needs something to pay for.** The data_sources line has "Stripe
(optional payment)", and a PaymentForm on an event type with no price renders a
form that takes a payment for nothing. So the component is refused unless the event
type carries a price - at embed time, where the person configuring can still fix
it, rather than at booking time.

**CSS custom properties are a closed set.** The research names "CSS custom
properties for styling" and cites the documentation page without quoting a single
name, so this build publishes its own namespaced set and refuses an unknown one.
A typo'd property name would otherwise be accepted and silently have no effect,
which is the worst of both worlds for a person styling an embed.

Also here, because it is an embed concern and nothing else needs it: the OAuth
client record and the access token standing in front of it. The research's step 1
is "stands up an OAuth client", and the data_flow begins "OAuth access token ->
Booker component calls Cal API v2". The token is a record with a subject, a
scope list and an expiry - and **never the secret**. What this product stores is
the fact that a token exists and when it lapses, which is all it needs to answer
"can this room book?", and refusing a secret-shaped field is what keeps an
accidental paste from becoming a stored credential.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence

from dsr.inroom_scheduling.errors import EmbedConfigError, TokenExpired
from dsr.inroom_scheduling.schedules import iso, parse_instant
from dsr.inroom_scheduling.vocabulary import (
    EMBED_BOOKING_COMPONENTS,
    OAUTH_FLOW_QUOTE,
    require_component,
    require_css_variable,
)

#: The scopes a Cal OAuth client can ask for. The research says only "stands up an
#: OAuth client" and names no scope, so this is this build's - published so a client
#: renders the picker from the same list the validator checks against.
OAUTH_SCOPES: tuple[str, ...] = (
    "DEFAULT_EVENT_TYPE",
    "BOOKING",
    "EVENT_TYPE",
    "SCHEDULE",
    "CALENDAR_READ",
    "CALENDAR_WRITE",
)

#: Field names that would mean a secret was pasted into a record. Refused by name
#: in the error rather than silently dropped: a person who pastes a token into
#: ``client_secret`` needs to be told, not quietly stored.
SECRET_FIELD_NAMES: frozenset[str] = frozenset(
    {"client_secret", "clientSecret", "access_token", "accessToken", "secret", "password", "api_key", "apiKey"}
)


def _reject_secrets(payload: Mapping[str, Any], *, where: str) -> None:
    """Refuse a payload carrying anything shaped like a credential.

    The data_flow starts at "OAuth access token", so a token in hand is the
    expected input - and storing it would put a live credential in a table that is
    read by the audit log, the schema explorer and the room page. This product
    stores the token's *existence* and expiry, never its value, and this is the
    check that keeps that true when somebody wires a new client by hand.
    """
    for name in payload:
        if name in SECRET_FIELD_NAMES:
            raise EmbedConfigError(
                f"{where}.{name} looks like a credential and is refused. This product records that "
                f"a token exists and when it lapses, never its value. {OAUTH_FLOW_QUOTE}"
            )


def normalise_oauth_client(spec: Mapping[str, Any], *, field: str = "client") -> dict[str, Any]:
    """An OAuth client record, validated.

    ``client_id``, ``redirect_uri`` and a non-empty scope list are required, because
    a client with no client id cannot be stood up and a client with no scopes has
    nothing to ask for. The ``token`` is a separate, later write: :func:`grant_token`
    is the only thing that sets it.
    """
    _reject_secrets(spec, where=field)
    body = dict(spec or {})
    client_id = str(body.get("client_id") or body.get("clientId") or "").strip()
    if not client_id:
        raise EmbedConfigError(f"{field}.client_id is required; {OAUTH_FLOW_QUOTE}")
    redirect_uri = str(body.get("redirect_uri") or body.get("redirectUri") or "").strip()
    if not redirect_uri:
        raise EmbedConfigError(f"{field}.redirect_uri is required; an OAuth client needs one to return to")

    raw_scopes = body.get("scopes")
    if isinstance(raw_scopes, str):
        scopes = [chunk.strip() for chunk in raw_scopes.split(",") if chunk.strip()]
    elif isinstance(raw_scopes, Sequence):
        scopes = [str(scope).strip() for scope in raw_scopes if str(scope).strip()]
    else:
        scopes = []
    if not scopes:
        raise EmbedConfigError(
            f"{field}.scopes is required; the published set is " + ", ".join(OAUTH_SCOPES)
        )
    unknown = [scope for scope in scopes if scope not in OAUTH_SCOPES]
    if unknown:
        raise EmbedConfigError(
            f"{field}.scopes contains {unknown}; the published set is " + ", ".join(OAUTH_SCOPES)
        )

    return {
        "name": str(body.get("name") or client_id).strip(),
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scopes": scopes,
        "has_token": bool(body.get("has_token", False)),
    }


def grant_token(
    client: Mapping[str, Any],
    *,
    subject: str,
    scopes: Sequence[str] | None = None,
    now: datetime,
    lifetime_minutes: int = 60 * 24 * 30,
) -> dict[str, Any]:
    """The token state, as an embed record carries it.

    Three fields and no secret: who the token acts for, what it may do, and when
    it stops working. The lifetime is the research's own shape - an OAuth access
    token is short-lived and is re-granted - defaulted to 30 days because the
    research does not publish a figure, registered as the ``token-lifetime``
    inference.

    A grant replaces rather than merges the previous scope list, because a narrowed
    grant is a *different* grant: keeping an old scope alongside a new one would
    make the record claim more than the token can do, and the only visible symptom
    would be a request the vendor rejects.
    """
    _reject_secrets({"subject": subject}, where="token")
    who = str(subject or "").strip()
    if not who:
        raise EmbedConfigError(
            "token.subject is required; a token that acts for nobody is not a grant, and the "
            "researched step is 'so the app can act on behalf of a scheduling user'"
        )
    granted = list(scopes if scopes is not None else client.get("scopes") or [])
    unknown = [scope for scope in granted if scope not in OAUTH_SCOPES]
    if unknown:
        raise EmbedConfigError(
            f"token.scopes contains {unknown}; the published set is " + ", ".join(OAUTH_SCOPES)
        )
    return {
        "subject": who,
        "scopes": granted,
        "granted_at": iso(now),
        "expires_at": iso(now + timedelta(minutes=int(lifetime_minutes))),
    }


def token_state(client: Mapping[str, Any], *, now: datetime) -> dict[str, Any]:
    """What a room can do with its token right now.

    ``live`` is the answer, and it is false for three different reasons that the
    caller has to tell apart: no token, a lapsed one, and a token whose scope list
    does not include booking. The first two are a reconnect; the third is a
    configuration mistake, and sending somebody to reconnect for it wastes an
    afternoon.
    """
    token = client.get("token") if isinstance(client.get("token"), Mapping) else None
    if not token:
        return {
            "has_token": False,
            "live": False,
            "reason": "no_token",
            "detail": "no OAuth token has been granted for this client yet",
        }
    expires_at = parse_instant(token.get("expires_at"), field="token.expires_at")
    if now >= expires_at:
        return {
            "has_token": True,
            "live": False,
            "reason": "expired",
            "expires_at": iso(expires_at),
            "detail": "the access token has expired; the client needs a new grant to book",
        }
    if "BOOKING" not in (token.get("scopes") or []):
        return {
            "has_token": True,
            "live": False,
            "reason": "no_booking_scope",
            "expires_at": iso(expires_at),
            "detail": (
                "the token is live but was not granted the BOOKING scope, so it cannot create a "
                "booking; reconnecting with the same scopes will not help"
            ),
        }
    return {
        "has_token": True,
        "live": True,
        "reason": "live",
        "expires_at": iso(expires_at),
        "subject": token.get("subject"),
        "scopes": list(token.get("scopes") or []),
        "detail": "the token is live and may create bookings",
    }


def require_live_token(client: Mapping[str, Any], *, now: datetime) -> dict[str, Any]:
    """The live token state, or a refusal that says which of the three is wrong."""
    state = token_state(client, now=now)
    if state["live"]:
        return state
    raise TokenExpired(
        f"this embed cannot book: {state['detail']}",
        reason=str(state["reason"]),
        expires_at=str(state.get("expires_at") or ""),
    )


def normalise_embed(
    spec: Mapping[str, Any],
    *,
    event_type: Mapping[str, Any] | None = None,
    field: str = "embed",
) -> dict[str, Any]:
    """A room's embed configuration, validated.

    The four rules in this module's docstring are enforced here, in this order,
    because each one depends on the ones before it: components are resolved first
    (a room cannot be told it has no booking component before we know which ones
    it asked for), then the booking-component rule, then the payment rule (which
    needs the event type's price), then the custom properties.
    """
    _reject_secrets(spec, where=field)
    body = dict(spec or {})

    raw_components = body.get("components")
    if raw_components in (None, ""):
        # The researched default: step 2 says the room "renders a Booker", with the
        # rest optional. A room that says nothing gets a Booker, which is the one
        # component step 5 requires to mean anything.
        components = ["booker"]
    elif isinstance(raw_components, Sequence) and not isinstance(raw_components, (str, bytes)):
        components = [require_component(str(name).strip()) for name in raw_components if str(name).strip()]
    else:
        raise EmbedConfigError(f"{field}.components must be a list of published component names")

    seen: list[str] = []
    for name in components:
        if name not in seen:
            seen.append(name)
    components = seen

    if not any(name in EMBED_BOOKING_COMPONENTS for name in components):
        raise EmbedConfigError(
            f"{field} renders no booking component. The researched step 5 is that the prospect "
            "'books entirely in-room; no Chili-Piper-like external page is shown', which needs one of "
            + ", ".join(EMBED_BOOKING_COMPONENTS)
            + ". The optional components are "
            + ", ".join(name for name in components)
        )

    if "payment_form" in components:
        price = (event_type or {}).get("price")
        if price in (None, "", 0):
            raise EmbedConfigError(
                f"{field} includes the payment form, but the event type has no price. The research "
                "lists Stripe as '(optional payment)', and a payment form on a free event type takes "
                "money for nothing."
            )

    css: dict[str, str] = {}
    for name, value in dict(body.get("css") or body.get("css_variables") or {}).items():
        css[require_css_variable(str(name).strip())] = str(value)

    payload: dict[str, Any] = {
        "eventTypeId": body.get("eventTypeId") or body.get("event_type_id"),
        "routingFormId": body.get("routingFormId") or body.get("routing_form_id"),
        "components": components,
        "css": css,
        "time_zone": body.get("time_zone") or body.get("timeZone") or "UTC",
        "language": body.get("language"),
        "theme": body.get("theme") or "dark",
        "reservationDuration": body.get("reservationDuration"),
        "durationMinutes": body.get("durationMinutes") or body.get("duration_minutes"),
        "hideBookerCalendar": bool(body.get("hideBookerCalendar") or body.get("hide_booker_calendar")),
        "layout": body.get("layout") or "month_view",
    }
    return payload


def embed_summary(
    embed: Mapping[str, Any],
    *,
    event_type: Mapping[str, Any] | None,
    token: Mapping[str, Any],
    now: datetime,
) -> dict[str, Any]:
    """What the page shows at the top: which event, which components, can it book.

    ``can_book`` is the single number a builder actually wants, and it is false for
    a reason the summary names. Everything else here is the context that makes the
    reason fixable.
    """
    components = list(embed.get("components") or [])
    return {
        "eventTypeId": embed.get("eventTypeId"),
        "event_title": (event_type or {}).get("title"),
        "event_kind": (event_type or {}).get("kind"),
        "length_minutes": (event_type or {}).get("length_minutes"),
        "routingFormId": embed.get("routingFormId"),
        "components": components,
        "has_booking_component": any(name in EMBED_BOOKING_COMPONENTS for name in components),
        "css": dict(embed.get("css") or {}),
        "time_zone": embed.get("time_zone"),
        "theme": embed.get("theme"),
        "can_book": bool(token.get("live")),
        "token": dict(token),
        "at": iso(now),
    }
