"""The engine: the researched user flow, in order, over the audited store.

Steps 1 to 5 of ``WF-058.md``, and the whole of them:

    1. Builder installs the embeddable booking components and stands up an OAuth
       client so the app can act on behalf of a scheduling user.
    2. The sales room renders a Booker (and optionally an Availability calendar,
       Event Type editor, calendar-connect buttons for Google/Outlook/Apple, and a
       payment form).
    3. For a bespoke flow, the app "intercepts a booking to introduce your custom
       flow and then submit the booking" - e.g. show a qualification form, hold the
       slot, then submit.
    4. Builder optionally uses a custom slot selection flow with
       ``handleSlotReservation`` for slot-hold semantics.
    5. The prospect books entirely in-room; no Chili-Piper-like external page is
       shown.

Two design points that are constraints rather than choices:

**``source`` is a required keyword on every write.** The audit row must name the
route that served the write, so the route builds it from ``router.prefix`` and
passes it down. A domain function that hardcoded a URL would let the audit log name
a path the app had stopped serving - that defect has shipped in this codebase
before, and making the parameter required is what stops it regressing silently.

**The scheduling API is a class, not a socket.** Nothing here opens a connection to
Cal.com. The research documents the request and response shapes, headers and
versions precisely, and this product has no Cal account, no token and no endpoint
it could reach. So the availability, hold and booking rules are exercised against
the audited store, which is the same stance WF-041 took with the CRM: the researched
surface is concentrated, and a real transport is a change to this one file.

**Reads never write.** The sweep that materialises a lapsed hold runs on the two
write paths where a stale row would change an answer - creating a hold, creating a
booking - and nowhere else, because a ``GET`` that quietly rewrites rows is a
surprise nobody can audit. Reads report the state the clock implies beside the
stored one, so the divergence is visible rather than hidden;
:mod:`dsr.inroom_scheduling.holds` explains the split.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Sequence

from dsr.db.audited import RecordNotFound
from dsr.inroom_scheduling.availability import (
    Occupancy,
    Selector,
    busy_intervals,
    read_selector,
    require_bookable,
    reschedule_uid,
    selector_label,
    slot_grid,
)
from dsr.inroom_scheduling.attendees import room_metadata, validate_metadata
from dsr.inroom_scheduling.bookings import (
    CANCELLED,
    INSTANT,
    RECURRING,
    booking_created_event,
    booking_payload,
    cancel_payload,
    read_booking_request,
    require_cancellable,
    require_team_event_for_instant,
    resolve_instant_start,
)
from dsr.inroom_scheduling.embeds import (
    embed_summary,
    grant_token,
    normalise_embed,
    normalise_oauth_client,
    require_live_token,
    token_state,
)
from dsr.inroom_scheduling.errors import (
    EmbedConfigError,
    HoldRequired,
    SchedulingError,
    SlotUnavailable,
    UnknownEventType,
)
from dsr.inroom_scheduling.events import (
    apply_booking_fields,
    calendar_summary,
    normalise_calendar_connection,
    normalise_event_type,
)
from dsr.inroom_scheduling.holds import (
    CONSUMED,
    EXPIRED,
    HELD,
    RELEASED,
    new_hold_payload,
    normalise_duration,
    read_hold,
    require_live,
    reservation_uid,
)
from dsr.inroom_scheduling.routing import normalise_form, route, routed_slots_response
from dsr.inroom_scheduling.schedules import (
    iso,
    normalise_host,
    parse_instant,
    parse_window,
)
from dsr.inroom_scheduling.vocabulary import (
    BOOKING_API_VERSION,
    BOOKING_CREATED,
    DEFAULT_RESERVATION_DURATION_MINUTES,
    MAX_SLOT_WINDOW_DAYS,
    MIN_DYNAMIC_USERNAMES,
    RESERVATION_RESPONSE_FIELDS,
    SLOTS_API_VERSION,
)
from dsr.store import RecordStore

# --------------------------------------------------------------------------- #
# Collections. Prefixed with the workflow so a reviewer reading the collections
# list can tell a scheduling row from any other feature's.
# --------------------------------------------------------------------------- #

CLIENT_COLLECTION = "scheduling_oauth_client"
EVENT_TYPE_COLLECTION = "scheduling_event_type"
CALENDAR_COLLECTION = "scheduling_calendar"
FORM_COLLECTION = "scheduling_routing_form"
RESERVATION_COLLECTION = "scheduling_reservation"
BOOKING_COLLECTION = "scheduling_booking"
EVENT_LOG_COLLECTION = "scheduling_booking_event"
WEBHOOK_COLLECTION = "scheduling_webhook"

#: The field on a room row carrying the embed, the last hold and the last booking.
#: The research does not say the embed lives on the room, but every route in this
#: feature is room-scoped because of step 5, and a room-scoped read that has to be
#: told which embed it means is not room-scoped in any useful sense.
ROOM_FIELD = "scheduling"

#: How many bookings the room row's convenience copy keeps. The booking records are
#: the durable copy; this is what a rep glances at, capped so a room with a
#: thousand bookings does not grow a thousand-element array.
ROOM_BOOKING_LIMIT = 20

#: How far ahead an instant booking looks for its first free slot, in days. The
#: research says an instant booking is immediate and gives no horizon, so this build
#: needs one; an embed's ``horizon_days`` widens it per room.
DEFAULT_HORIZON_DAYS = 14

#: The default length of a dynamic ``usernames`` query's meetings, which names no
#: event type and therefore no length. Thirty minutes is the value used throughout
#: the seeded demo, so a dynamic query and a personal event offer the same grid and
#: a reviewer comparing them is comparing like with like.
DYNAMIC_DEFAULT_LENGTH_MINUTES = 30


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _routed_event_type_ids(form: Mapping[str, Any]) -> list[str]:
    """Every event type a form can route to, including its catch-all.

    A rule that opted out of the fallback has no event type - that is what opting
    out means - so it contributes nothing here. Skipping it rather than passing
    ``None`` through matters because this list is what the existence check runs
    over, and a ``None`` in it would look for an event type whose id is the string
    "None".
    """
    return [
        *[
            str(rule["eventTypeId"])
            for rule in form.get("rules") or []
            if rule.get("eventTypeId")
        ],
        str(form.get("fallbackEventTypeId")),
    ]


def _digest(*parts: Any, prefix: str) -> str:
    """A short, stable id derived from its inputs.

    Deterministic rather than random so a seeded demo and a test can both name the
    row they expect. Not a credential and not claimed to be one: a hold uid names a
    claim inside this product's own store and grants nothing by itself.
    """
    import hashlib

    material = "|".join(str(part) for part in parts)
    return f"{prefix}{hashlib.sha256(material.encode('utf-8')).hexdigest()[:16]}"


class SchedulingEngine:
    """The bookable calendar, over the audited store.

    Constructed per request from ``StoreDep``, like every other feature's engine
    here. It holds nothing beyond the store and a clock, so per-request
    construction is equivalent and leaves both overridable in a test.
    """

    def __init__(
        self,
        store: RecordStore,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.clock = clock if clock is not None else _utcnow

    # -- step 1: the OAuth client ------------------------------------------- #

    def create_client(
        self, spec: Mapping[str, Any], *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Stand up an OAuth client, as step 1 describes."""
        return self.store.create(
            CLIENT_COLLECTION, normalise_oauth_client(spec), actor=actor, source=source
        )

    def list_clients(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return self.store.list(CLIENT_COLLECTION, limit=limit)

    def get_client(self, client_id: str) -> dict[str, Any] | None:
        return self.store.get(client_id)

    def require_client(self, client_id: str) -> dict[str, Any]:
        record = self.get_client(client_id)
        if record is None:
            raise UnknownEventType(f"oauth client {client_id} not found")
        return record

    def update_client(
        self, client_id: str, patch: Mapping[str, Any], *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Patch a client, re-running the same validation a create goes through.

        A partial patch is merged onto the stored client first, so a patch cannot
        leave a client whose scopes are no longer a published set, or which has
        gained a credential-shaped field. The token is carried across untouched:
        a grant is the only thing that sets it, and a patch that could write one
        would be a second, unguarded door to the same place.
        """
        current = self.require_client(client_id)
        data = _as_mapping(current.get("data"))
        merged = {**data, **dict(patch or {})}
        merged.pop("token", None)
        payload = normalise_oauth_client(merged)
        if data.get("token"):
            payload["token"] = data["token"]
        if "has_token" in merged:
            payload["has_token"] = bool(merged["has_token"])
        return self.store.update(client_id, payload, actor=actor, source=source)

    def delete_client(
        self, client_id: str, *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        self.require_client(client_id)
        return self.store.delete(client_id, actor=actor, source=source)

    def grant(
        self,
        client_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Record that a token now exists, and for whom.

        The value of the token is never stored - only the subject, the scopes and
        the expiry. That is enough to answer "can this room book?", and it is the
        whole of what this product does with a credential.
        """
        current = self.require_client(client_id)
        data = _as_mapping(current.get("data"))
        body = dict(payload or {})
        subject = body.get("subject") or _as_mapping(data.get("token")).get("subject")
        token = grant_token(
            data,
            subject=str(subject or ""),
            scopes=body.get("scopes"),
            now=self.clock(),
            lifetime_minutes=int(body.get("lifetime_minutes") or 30 * 24 * 60),
        )
        return self.store.update(
            client_id, {"token": token, "has_token": True}, actor=actor, source=source
        )

    def client_token_state(self, client: Mapping[str, Any]) -> dict[str, Any]:
        return token_state(_as_mapping(client.get("data")), now=self.clock())

    def resolve_client(self, client_id: Any) -> dict[str, Any]:
        """The client an embed books with: the one it names, or the only one.

        A room with exactly one client needs no configuration to book, which is the
        common case for a single-seller deployment. A room with several must say
        which, because "whichever was created last" is not a policy - and this build
        refuses rather than picking, so adding a second client can never silently
        change which account a room books through.
        """
        if client_id:
            return self.require_client(str(client_id))
        clients = self.list_clients(limit=2)
        if len(clients) == 1:
            return clients[0]
        if not clients:
            raise EmbedConfigError(
                "no OAuth client exists. The researched first step is that the builder 'stands up an "
                "OAuth client so the app can act on behalf of a scheduling user'."
            )
        raise EmbedConfigError(
            "several OAuth clients exist and the embed names none; set clientId on the embed rather "
            "than letting creation order pick one"
        )

    # -- step 2: event types and calendar connects -------------------------- #

    def create_event_type(
        self, spec: Mapping[str, Any], *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        return self.store.create(
            EVENT_TYPE_COLLECTION, normalise_event_type(spec), actor=actor, source=source
        )

    def list_event_types(
        self, *, kind: str | None = None, team_slug: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        where: dict[str, Any] = {}
        if kind is not None:
            where["kind"] = str(kind)
        if team_slug is not None:
            where["teamSlug"] = str(team_slug)
        return self.store.find(EVENT_TYPE_COLLECTION, where, limit=limit)

    def get_event_type(self, event_type_id: str) -> dict[str, Any] | None:
        return self._find_event_type_by({"eventTypeId": str(event_type_id)})

    def require_event_type(self, event_type_id: str) -> dict[str, Any]:
        record = self.get_event_type(event_type_id)
        if record is None:
            raise UnknownEventType(
                f"no event type has eventTypeId {event_type_id!r}; a query resolves by eventTypeId, by "
                "slug with a username or a teamSlug, or by a usernames list"
            )
        return record

    def update_event_type(
        self, event_type_id: str, patch: Mapping[str, Any], *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        current = self.require_event_type(event_type_id)
        merged = {**_as_mapping(current.get("data")), **dict(patch or {})}
        return self.store.update(
            event_type_id, normalise_event_type(merged), actor=actor, source=source
        )

    def delete_event_type(
        self, event_type_id: str, *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        self.require_event_type(event_type_id)
        return self.store.delete(event_type_id, actor=actor, source=source)

    def _find_event_type_by(self, where: Mapping[str, Any]) -> dict[str, Any] | None:
        wanted = {key: value for key, value in where.items() if value not in (None, "")}
        if not wanted:
            return None
        found = self.store.find(EVENT_TYPE_COLLECTION, wanted, limit=2)
        if not found:
            return None
        if len(found) > 1:
            raise UnknownEventType(
                f"{len(found)} event types match {dict(wanted)}; a query that resolves ambiguously "
                "cannot be answered, because it would offer a slot for one of them and book another"
            )
        return found[0]

    def resolve_event_type(self, selector: Selector) -> tuple[dict[str, Any], int]:
        """The event type and the ``min_available_hosts`` a selector implies.

        Three of the four researched selectors resolve to one stored event type. The
        fourth, ``usernames``, names no event type at all - "there is no specific
        event" - so the hosts are looked up across event types and the number of
        people who must be free comes from the research's own "2 or more people are
        available".

        The dynamic case gets a synthetic record rather than a special case
        everywhere downstream: a slot grid, a hold and a booking cannot tell whether
        the event type they are working on is a stored one or an assembled one, and
        making them able to would put a branch in three places instead of one.
        """
        if selector.dynamic:
            hosts = self._hosts_for_usernames(selector.usernames)
            data = {
                "eventTypeId": None,
                "slug": None,
                "kind": "dynamic",
                "title": " and ".join(host["username"] for host in hosts),
                "hosts": hosts,
                "host": ",".join(host["username"] for host in hosts),
                "length_minutes": DYNAMIC_DEFAULT_LENGTH_MINUTES,
                "seats": None,
                "location": "phone",
                "conference": None,
                "booking_fields": [],
            }
            return ({"id": None, "data": data}, MIN_DYNAMIC_USERNAMES)

        if selector.kind == "event_type_id":
            record = self._find_event_type_by({"eventTypeId": selector.event_type_id})
            if record is None:
                raise UnknownEventType(
                    f"no event type has eventTypeId {selector.event_type_id!r}; a slot query resolves "
                    "by eventTypeId, by slug with a username or a teamSlug, or by a usernames list"
                )
            return (record, 1)

        if selector.kind == "username":
            where = {"slug": selector.slug, "host": selector.username}
        else:
            where = {"slug": selector.slug, "teamSlug": selector.team_slug}
        record = self._find_event_type_by(where)
        if record is None:
            raise UnknownEventType(f"no event type matches {where}; a slot grid has to come from one")
        return (record, 1)

    def _hosts_for_usernames(self, usernames: Sequence[str]) -> list[dict[str, Any]]:
        """The working hours of each named host, or a refusal naming the strangers.

        A dynamic query naming somebody this product has never heard of is refused
        rather than answered with a default nine-to-five. Inventing availability for
        a stranger offers slots nobody can take, and the failure appears minutes
        later as a booking that could not be confirmed - a worse answer than saying
        up front that the host is unknown.
        """
        found: list[dict[str, Any]] = []
        known: list[str] = []
        every = self.list_event_types(limit=1000)
        for name in usernames:
            candidates = [
                host
                for record in every
                for host in (_as_mapping(record.get("data")).get("hosts") or [])
                if str(host.get("username")) == name
            ]
            if not candidates:
                continue
            # Several event types can share a host; the first working-hours
            # definition wins and the rest describe the same person's calendar.
            candidates.sort(key=lambda entry: (str(entry.get("time_zone") or ""), str(entry.get("start"))))
            found.append(normalise_host(candidates[0], field=f"usernames[{name}]"))
            known.append(name)
        unknown = [name for name in usernames if name not in known]
        if unknown:
            raise UnknownEventType(
                f"no working hours are known for {unknown}. A usernames query exists to find when "
                f"{MIN_DYNAMIC_USERNAMES} or more people are available, so answering it for a host "
                "this product has no calendar for would offer slots nobody can take."
            )
        return found

    def connect_calendar(
        self, spec: Mapping[str, Any], *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Record a Google/Outlook/Apple calendar connect.

        Idempotent per provider and host: connecting the same calendar twice updates
        the existing row rather than adding a second, because a page whose connect
        buttons stay enabled after a connection is a page that looks broken.
        """
        payload = normalise_calendar_connection(spec)
        existing = self.store.find(
            CALENDAR_COLLECTION,
            {"provider": payload["provider"], "host": payload["host"]},
            limit=1,
        )
        if existing:
            return self.store.update(existing[0]["id"], payload, actor=actor, source=source)
        return self.store.create(CALENDAR_COLLECTION, payload, actor=actor, source=source)

    def list_calendars(self, *, host: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        where: dict[str, Any] = {}
        if host is not None:
            where["host"] = str(host)
        return self.store.find(CALENDAR_COLLECTION, where, limit=limit)

    def disconnect_calendar(
        self, calendar_id: str, *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        record = self.store.get(calendar_id)
        if record is None:
            raise UnknownEventType(f"calendar connection {calendar_id} not found")
        return self.store.delete(calendar_id, actor=actor, source=source)

    def calendars(self) -> dict[str, Any]:
        return calendar_summary(self.list_calendars())

    # -- routing forms ------------------------------------------------------- #

    def create_form(
        self, spec: Mapping[str, Any], *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Create a routing form, which must end in a catch-all.

        Every event type a rule routes to, and the fallback, is checked to exist
        before the row is written. A form pointing at a missing event type fails on
        the prospect's first answer rather than at configuration time, which is the
        worst possible moment to find out.
        """
        payload = normalise_form(spec)
        for event_type_id in _routed_event_type_ids(payload):
            self.require_event_type(event_type_id)
        return self.store.create(FORM_COLLECTION, payload, actor=actor, source=source)

    def list_forms(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return self.store.list(FORM_COLLECTION, limit=limit)

    def get_form(self, form_id: str) -> dict[str, Any] | None:
        return self.store.get(form_id)

    def require_form(self, form_id: Any) -> dict[str, Any]:
        if form_id in (None, ""):
            raise UnknownEventType("this request needs a routing form")
        record = self.store.get(str(form_id))
        if record is None:
            raise UnknownEventType(f"routing form {form_id} not found")
        return record

    def update_form(
        self, form_id: str, patch: Mapping[str, Any], *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        current = self.require_form(form_id)
        merged = {**_as_mapping(current.get("data")), **dict(patch or {})}
        payload = normalise_form(merged)
        for event_type_id in _routed_event_type_ids(payload):
            self.require_event_type(event_type_id)
        return self.store.update(form_id, payload, actor=actor, source=source)

    def delete_form(self, form_id: str, *, actor: str | None = None, source: str) -> dict[str, Any]:
        self.require_form(form_id)
        return self.store.delete(form_id, actor=actor, source=source)

    def route_answers(self, form_id: str, responses: Mapping[str, Any]) -> dict[str, Any]:
        """Route a set of answers. A pure read of the form and the answers."""
        return route(_as_mapping(self.require_form(form_id).get("data")), responses)

    # -- the embed ----------------------------------------------------------- #

    def embed_record(self, room_id: str) -> dict[str, Any] | None:
        """The room's embed, read off the room row.

        Stored on the room rather than in its own collection, because step 5 says
        the booking happens in the room and a room-scoped read that had to be told
        which embed to use would not be room-scoped in any useful sense. A room with
        no ``scheduling`` field has no embed installed, which is a different state
        from an embed with nothing on it and is reported as such.
        """
        room = self.store.get(room_id)
        if room is None:
            raise RecordNotFound(room_id)
        body = _as_mapping(room.get("data")).get(ROOM_FIELD)
        if not isinstance(body, Mapping):
            return None
        embed = body.get("embed")
        if not isinstance(embed, Mapping) or not embed:
            return None
        return {**dict(embed), "room_id": room_id}

    def require_embed(self, room_id: str) -> dict[str, Any]:
        embed = self.embed_record(room_id)
        if embed is None:
            raise EmbedConfigError(
                f"room {room_id} has no embed installed. The researched first step is that the "
                "builder 'installs the embeddable booking components' before the room can render a "
                "Booker."
            )
        return embed

    def save_embed(
        self, room_id: str, spec: Mapping[str, Any], *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Install or replace a room's embed, and annotate the room row.

        Validated before anything is written, including the client, the event type
        and the routing form, so a room cannot end up with an embed pointing at
        something that does not exist.

        An embed that names no event type is resolved from its routing form's
        fallback. That is the researched custom flow rather than a convenience: the
        prospect answers a question, the form routes them, and the event type they
        land on is a consequence of their answer - so the fallback is the only
        event type the embed can honestly claim as its own before anyone has
        answered.
        """
        room = self.store.get(room_id)
        if room is None:
            raise RecordNotFound(room_id)
        body = dict(spec or {})
        if body.get("clientId") or body.get("client_id"):
            self.require_client(str(body.get("clientId") or body.get("client_id")))

        form_id = body.get("routingFormId") or body.get("routing_form_id")
        if form_id:
            self.require_form(form_id)

        named = body.get("eventTypeId") or body.get("event_type_id")
        if named in (None, ""):
            if not form_id:
                raise EmbedConfigError(
                    "an embed must name an eventTypeId, or a routingFormId whose fallback event type "
                    "is the one the room books against. Neither was given."
                )
            fallback = str(_as_mapping(self.require_form(form_id).get("data"))["fallbackEventTypeId"])
            body = {**body, "eventTypeId": fallback}

        event_type = self.require_event_type(str(body["eventTypeId"]))
        payload = normalise_embed(
            body, event_type=_as_mapping(event_type.get("data"))
        )
        payload["clientId"] = body.get("clientId") or body.get("client_id")
        payload["horizon_days"] = self._horizon(body)
        payload["require_hold"] = bool(body.get("require_hold") or body.get("requireHold"))

        self.annotate_room(room_id, {"embed": payload}, actor=actor, source=source)
        return self.embed_response(room_id)

    def _horizon(self, body: Mapping[str, Any]) -> int:
        raw = body.get("horizon_days")
        if raw in (None, ""):
            return DEFAULT_HORIZON_DAYS
        try:
            days = int(raw)
        except (TypeError, ValueError) as exc:
            raise EmbedConfigError("embed.horizon_days must be a whole number of days") from exc
        if days < 1 or days > MAX_SLOT_WINDOW_DAYS:
            raise EmbedConfigError(
                f"embed.horizon_days must be between 1 and {MAX_SLOT_WINDOW_DAYS}, got {days}"
            )
        return days

    def embed_response(self, room_id: str) -> dict[str, Any]:
        """The embed, with the event type it books and whether the token is live.

        ``can_book`` is the one number a builder actually wants, and it is false for
        a reason the payload names. Everything else here is the context that makes
        that reason fixable without leaving the page.
        """
        embed = self.require_embed(room_id)
        event_type = self.require_event_type(str(embed["eventTypeId"]))
        client = self.resolve_client(embed.get("clientId"))
        token = self.client_token_state(client)
        return {
            "room_id": room_id,
            "embed": embed,
            "client_id": client["id"],
            "summary": embed_summary(
                embed,
                event_type=_as_mapping(event_type.get("data")),
                token=token,
                now=self.clock(),
            ),
        }

    def annotate_room(
        self, room_id: str, patch: Mapping[str, Any], *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Merge a scheduling annotation into the room row.

        A shallow merge at the field, then a shallow merge inside it, so an install
        and a booking annotation do not each overwrite the other. The bookings
        history is capped at :data:`ROOM_BOOKING_LIMIT`; the booking records are the
        durable copy, and a capped convenience copy cannot answer a question about
        the eleventh booking, which is why the records are the ones you query.
        """
        room = self.store.get(room_id)
        if room is None:
            raise RecordNotFound(room_id)
        existing = _as_mapping(room.get("data")).get(ROOM_FIELD)
        body = dict(existing) if isinstance(existing, Mapping) else {}
        for key, value in dict(patch).items():
            if key == "bookings" and isinstance(value, list):
                body["bookings"] = (list(body.get("bookings") or []) + value)[-ROOM_BOOKING_LIMIT:]
            else:
                body[key] = value
        body["updated_at"] = iso(self.clock())
        return self.store.update(room_id, {ROOM_FIELD: body}, actor=actor, source=source)

    # -- availability -------------------------------------------------------- #

    def slots(
        self,
        room_id: str,
        params: Mapping[str, Any],
        *,
        selector: Selector | None = None,
    ) -> dict[str, Any]:
        """``GET /v2/slots`` for a room. Writes nothing.

        The room matters for one reason: step 5 says the prospect books in-room, so
        the grid a room renders is the grid its own embed, hosts and holds produce.
        A grid that ignored the room would offer times the room's holds and bookings
        have not accounted for.
        """
        if self.store.get(room_id) is None:
            raise RecordNotFound(room_id)
        now = self.clock()
        chosen = selector if selector is not None else read_selector(params)
        event_type, required = self.resolve_event_type(chosen)
        data = _as_mapping(event_type.get("data"))

        window = self._window_for(params, now=now, event_type=data)
        time_zone = self._time_zone(params, data)
        slots, _ = self._grid(
            data,
            window=window,
            now=now,
            time_zone=time_zone,
            room_id=room_id,
            required=required,
            exclude_booking_uid=reschedule_uid(params),
        )
        return {
            "room_id": room_id,
            "api_version": SLOTS_API_VERSION,
            "selector": selector_label(chosen),
            "kind": chosen.kind,
            "event_type_id": data.get("eventTypeId"),
            "time_zone": time_zone,
            "start": iso(window[0]),
            "end": iso(window[1]),
            "hosts_required": required,
            "min_dynamic_usernames": MIN_DYNAMIC_USERNAMES,
            "slot_count": len(slots),
            "available_count": sum(1 for slot in slots if slot["available"]),
            "slots": slots,
            "holds_are_visible": True,
            "saved": False,
        }

    def routed_slots(
        self, room_id: str, params: Mapping[str, Any], responses: Mapping[str, Any]
    ) -> dict[str, Any]:
        """``GET /v2/routing-forms/slots`` for a room. Saves nothing, at all.

        The research's own sentence: "It will not actually save the response just
        return the routed event type and slots when it can be booked." So this method
        takes no ``source`` and no actor, and calls no write on the store - the
        absence of those parameters is part of the enforcement, and a test asserts
        the record count is unchanged after driving it.

        The fall-through is the other researched constraint. No rule matched means
        the form's catch-all, and the response says ``reason: "fallback"`` with
        ``matched_rule: null`` rather than looking as though a rule fired. A rule
        that matched and opted out is ``routed: false`` - a decision somebody made,
        which is a different thing from a catch-all doing its job.
        """
        if self.store.get(room_id) is None:
            raise RecordNotFound(room_id)
        now = self.clock()
        embed = self.embed_record(room_id) or {}
        form_id = params.get("formId") or params.get("form_id") or embed.get("routingFormId")
        form = _as_mapping(self.require_form(form_id).get("data"))
        routing = route(form, responses)
        form_id = str(form_id)

        if not routing.get("routed"):
            return routed_slots_response(routing, [], form_id=form_id)

        data = _as_mapping(self.require_event_type(str(routing["eventTypeId"])).get("data"))
        window = self._window_for(params, now=now, event_type=data)
        slots, _ = self._grid(
            data,
            window=window,
            now=now,
            time_zone=self._time_zone(params, data),
            room_id=room_id,
            required=1,
            exclude_booking_uid=reschedule_uid(params),
        )
        return routed_slots_response(routing, slots, form_id=form_id)

    def _time_zone(self, params: Mapping[str, Any], data: Mapping[str, Any]) -> str:
        """The zone a grid's labels are rendered in.

        The request wins, then the event type's own zone, then UTC. A caller that
        passes ``timeZone`` gets labels where it asked for them, which is the whole
        point of the query parameter; a caller that does not gets the host's zone,
        which is the zone the working hours were written in.
        """
        named = params.get("timeZone") or params.get("time_zone")
        if named:
            from dsr.inroom_scheduling.schedules import resolve_zone

            resolve_zone(str(named))
            return str(named)
        return str(data.get("time_zone") or "UTC")

    def _window_for(
        self, params: Mapping[str, Any], *, now: datetime, event_type: Mapping[str, Any]
    ) -> tuple[datetime, datetime]:
        """The window a query covers: ``start``/``end``, or the embed's horizon.

        A booking names one start rather than a window, so its window is that start
        plus a day: long enough to contain the slot and the working day it sits in,
        short enough that the grid is not computed for a fortnight to check one time.
        With neither, the room's embed horizon supplies the window - and only when
        exactly one room has installed this event type, so two rooms with different
        horizons cannot end up with one room's setting governing another's grid.
        """
        raw_start = params.get("start")
        raw_end = params.get("end")
        if raw_start not in (None, "") and raw_end not in (None, ""):
            return parse_window(raw_start, raw_end, now=now)

        if raw_start not in (None, "") and raw_end in (None, ""):
            begin = parse_instant(raw_start, field="start")
            length = int(event_type.get("length_minutes") or DYNAMIC_DEFAULT_LENGTH_MINUTES)
            return parse_window(iso(begin), iso(begin + timedelta(days=1, minutes=length)), now=now)

        begin = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
        horizon = self._horizon_for(event_type)
        return (begin, begin + timedelta(days=horizon))

    def _horizon_for(self, event_type: Mapping[str, Any]) -> int:
        target = event_type.get("eventTypeId")
        if not target:
            return DEFAULT_HORIZON_DAYS
        embeds = [
            dict(_as_mapping(body.get("embed")))
            for record in self.store.list("room", limit=1000)
            for body in [_as_mapping(record.get("data")).get(ROOM_FIELD)]
            if isinstance(body, Mapping)
            and isinstance(body.get("embed"), Mapping)
            and body["embed"].get("eventTypeId") == target
        ]
        if len(embeds) != 1:
            return DEFAULT_HORIZON_DAYS
        return int(embeds[0].get("horizon_days") or DEFAULT_HORIZON_DAYS)

    def _seated_event_type_ids(self) -> set[str]:
        """The ``eventTypeId`` of every seated event type on this deployment."""
        return {
            str(_as_mapping(record.get("data")).get("eventTypeId"))
            for record in self.list_event_types(kind="seated", limit=1000)
            if _as_mapping(record.get("data")).get("eventTypeId")
        }

    def _grid(
        self,
        data: Mapping[str, Any],
        *,
        window: tuple[datetime, datetime],
        now: datetime,
        time_zone: str,
        room_id: str,
        required: int,
        exclude_booking_uid: str | None,
        exclude_hold_uid: str | None = None,
    ) -> tuple[list[dict[str, Any]], Occupancy]:
        """The slot grid for an event type's data, and the occupancy behind it.

        ``exclude_hold_uid`` is the researched custom booking flow made workable.
        A booking that presents a hold is submitting *against* that hold, so the
        hold must not make the slot unavailable to it - otherwise "hold the slot,
        then submit the booking" refuses at the last step, and the prospect is told
        somebody else took the slot they were holding thirty seconds ago.
        """
        hosts = [_as_mapping(host) for host in data.get("hosts") or []]
        entries = self._busy_entries()
        occupancy = self._occupancy(
            exclude_booking_uid=exclude_booking_uid, exclude_hold_uid=exclude_hold_uid
        )
        slots = slot_grid(
            hosts=hosts,
            length_minutes=int(data.get("length_minutes") or DYNAMIC_DEFAULT_LENGTH_MINUTES),
            window=window,
            now=now,
            time_zone=time_zone,
            busy=busy_intervals(entries, exclude_booking_uid=exclude_booking_uid),
            occupancy=occupancy,
            event_type_id=data.get("eventTypeId"),
            seats=data.get("seats"),
            min_available_hosts=required,
        )
        return (slots, occupancy)

    def _busy_entries(self) -> list[dict[str, Any]]:
        """What occupies a host, from bookings and from live holds, across every room.

        A host is busy whoever booked them. Two rooms sharing an event type share
        the host's calendar, so scoping this to one room would let room B offer
        09:00 to a prospect that room A already booked - and the double-booking
        would be invisible until the call.

        Two things are deliberately absent, and both would make a researched feature
        unusable:

        * a **cancelled** booking. Cancelling returns the slot to the grid, which is
          the whole point of cancelling, and a booking left in the busy set would
          make a cancellation indistinguishable from a no-op.
        * a **seated** booking. A seated event is several people in one meeting, so
          each booking occupies a *seat* rather than the host's calendar. Counting
          the first seated booking as host-busy would make a four-seat event bookable
          once, which is the opposite of what a seat count is for. Seats are counted
          in :meth:`_occupancy` instead.
        """
        seated = self._seated_event_type_ids()
        entries: list[dict[str, Any]] = []
        for record in self.store.list(BOOKING_COLLECTION, limit=1000):
            data = _as_mapping(record.get("data"))
            if str(data.get("status")) == CANCELLED:
                continue
            if str(data.get("eventTypeId")) in seated:
                continue
            entries.append(
                {
                    "uid": data.get("uid"),
                    "host": data.get("host"),
                    "start": data.get("start"),
                    "end": data.get("end"),
                }
            )
        now = self.clock()
        for record in self.store.list(RESERVATION_COLLECTION, limit=1000):
            data = _as_mapping(record.get("data"))
            if str(data.get("event_type_id")) in seated:
                continue
            hold = read_hold(record, now=now)
            if not hold.live:
                continue
            entries.append(
                {
                    "uid": data.get("reservationUid"),
                    "host": data.get("host"),
                    "start": data.get("start"),
                    "end": iso(hold.until),
                }
            )
        return entries

    def _occupancy(
        self, *, exclude_booking_uid: str | None, exclude_hold_uid: str | None = None
    ) -> Occupancy:
        """Which slots are held, and how many seats each slot has taken.

        Global, for the same reason :meth:`_busy_entries` is: a hold is a claim on
        a slot on a host's calendar, so it is a claim whoever asks. Scoping holds to
        one room would let a prospect hold a slot in room A and then be offered the
        same slot by room B.

        Seats are counted from bookings and from live holds, because a held seat is
        a seat somebody is about to take: a seated event counting only bookings would
        let five prospects hold the same last seat and discover it at submit time.

        A hold is a claim on a *slot*, so it appears in ``held_by`` and contributes
        zero seats on its own. A hold's seat is counted by the booking it becomes,
        and counting it twice would make a seated event fill up from holds alone -
        which is not what a hold means, and would leave a slot unavailable to a
        prospect who has not asked for it.
        """
        now = self.clock()
        held_by: dict[str, str] = {}
        for record in self.store.list(RESERVATION_COLLECTION, limit=1000):
            view = read_hold(record, now=now)
            if view.live and view.uid != exclude_hold_uid:
                held_by[iso(view.start)] = view.uid
        seat_usage: dict[str, int] = {}
        for record in self.store.list(BOOKING_COLLECTION, limit=1000):
            data = _as_mapping(record.get("data"))
            if str(data.get("status")) == CANCELLED:
                continue
            if exclude_booking_uid and str(data.get("uid")) == exclude_booking_uid:
                continue
            key = str(data.get("start"))
            seat_usage[key] = seat_usage.get(key, 0) + 1
        return Occupancy(held_by=held_by, seat_usage=seat_usage)

    # -- reservations -------------------------------------------------------- #

    def reserve(
        self,
        room_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """``POST /v2/slots/reservations``: hold a slot for a while.

        The researched hold semantics, all of them: the duration is customisable and
        defaults to five minutes, the response carries ``reservationUid``,
        ``reservationDuration`` and ``reservationUntil``, and the slot is unavailable
        to everybody else until the hold lapses.

        The hold is taken against the *room's* grid, so a hold cannot be placed on a
        slot the room's own event type does not offer - which is the same rule the
        booking path uses, so the two can never disagree about what is bookable.
        """
        embed = self.require_embed(room_id)
        client = self.resolve_client(embed.get("clientId"))
        require_live_token(_as_mapping(client.get("data")), now=self.clock())
        self._reap(room_id, actor=actor, source=source)

        now = self.clock()
        body = dict(payload or {})
        data = _as_mapping(self._event_type_for(room_id, body).get("data"))
        start = body.get("start")
        if start in (None, ""):
            raise SchedulingError("start is required: a reservation holds one slot")

        duration = normalise_duration(
            body.get("reservationDuration")
            if body.get("reservationDuration") not in (None, "")
            else embed.get("reservationDuration")
        )
        window = self._window_for({"start": start}, now=now, event_type=data)
        slots, _ = self._grid(
            data,
            window=window,
            now=now,
            time_zone=str(embed.get("time_zone") or "UTC"),
            room_id=room_id,
            required=1,
            exclude_booking_uid=reschedule_uid(body),
        )
        slot = require_bookable(slots, str(start))

        uid = reservation_uid(
            {
                "event_type_id": data.get("eventTypeId"),
                "start": slot["start"],
                "host": data.get("host"),
                "room_id": room_id,
            },
            now=now,
        )
        record = self.store.create(
            RESERVATION_COLLECTION,
            new_hold_payload(
                event_type_id=data.get("eventTypeId"),
                start=slot["start"],
                host=data.get("host"),
                duration_minutes=duration,
                now=now,
                uid=uid,
                room_id=room_id,
                attendee=_as_mapping(body.get("attendee")) or None,
            ),
            room_id=room_id,
            actor=actor,
            source=source,
        )
        self.annotate_room(
            room_id,
            {
                "last_hold": {
                    "uid": uid,
                    "start": slot["start"],
                    "until": iso(now + timedelta(minutes=duration)),
                }
            },
            actor=actor,
            source=source,
        )
        return {
            "room_id": room_id,
            "api_version": SLOTS_API_VERSION,
            "reservationUid": uid,
            "reservationDuration": duration,
            "reservationUntil": _as_mapping(record.get("data"))["reservationUntil"],
            "start": slot["start"],
            "end": slot["end"],
            "event_type_id": data.get("eventTypeId"),
            "default_duration_minutes": DEFAULT_RESERVATION_DURATION_MINUTES,
            "record_id": record["id"],
        }

    def hold(self, room_id: str, uid: str) -> dict[str, Any]:
        """A hold, read at the current moment. 404 when the room does not own it."""
        return self.hold_view(self._require_hold(room_id, uid))

    def hold_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """A stored hold, as a read sees it: the state the clock implies.

        The researched field names come first in the payload, and the stored state
        sits beside the computed one. A hold whose row still says ``held`` after its
        ``reservationUntil`` is exactly the researched automation - "no user action
        needed for the hold to expire" - and showing both is how a reviewer can see
        that the clock decided, not a job.
        """
        view = read_hold(record, now=self.clock()).to_dict()
        data = _as_mapping(record.get("data"))
        return {
            "reservationUid": view["reservationUid"],
            "reservationDuration": view["reservationDuration"],
            "reservationUntil": view["reservationUntil"],
            "start": view["start"],
            "state": view["state"],
            "stored_state": view["stored_state"],
            "expired_now": view["expired_now"],
            "event_type_id": view["event_type_id"],
            "host": view["host"],
            "room_id": record.get("room_id"),
            "attendee": data.get("attendee"),
            "record_id": record.get("id"),
            "response_fields": list(RESERVATION_RESPONSE_FIELDS),
        }

    def holds(self, room_id: str, *, state: str | None = None) -> list[dict[str, Any]]:
        """Every hold on a room, newest first, each read at the current moment."""
        views = [self.hold_view(record) for record in self._room_holds(room_id)]
        if state is not None:
            views = [view for view in views if view["state"] == str(state)]
        return views

    def _room_holds(self, room_id: str) -> list[dict[str, Any]]:
        if self.store.get(room_id) is None:
            raise RecordNotFound(room_id)
        records = self.store.list(RESERVATION_COLLECTION, room_id=room_id, limit=1000)
        return sorted(records, key=lambda record: str(record.get("updated_at") or ""), reverse=True)

    def _require_hold(self, room_id: str, uid: str) -> dict[str, Any]:
        for record in self._room_holds(room_id):
            if str(_as_mapping(record.get("data")).get("reservationUid")) == str(uid):
                return record
        raise UnknownEventType(
            f"reservation {uid} not found on room {room_id}; a hold belongs to the room that made it"
        )

    def extend_hold(
        self,
        room_id: str,
        uid: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """``PATCH /v2/slots/reservation/{reservationUid}``: change the duration.

        The new duration is measured from *now*, not from the original grant. That is
        the reading that makes extending useful - the point is to buy more time from
        this moment - and it is why ``reservationUntil`` moves while ``reserved_at``
        does not.
        """
        record = self._require_hold(room_id, uid)
        require_live(read_hold(record, now=self.clock()))
        duration = normalise_duration(dict(payload or {}).get("reservationDuration"))
        now = self.clock()
        self.store.update(
            record["id"],
            {
                "reservationDuration": duration,
                "reservationUntil": iso(now + timedelta(minutes=duration)),
                "extended_at": iso(now),
            },
            actor=actor,
            source=source,
        )
        return self.hold_view(self._require_hold(room_id, uid))

    def release_hold(
        self, room_id: str, uid: str, *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """``DELETE /v2/slots/reservation/{reservationUid}``: give the slot back.

        Releasing an already-released hold is refused rather than absorbed, because
        absorbing it would move the recorded release time on the second attempt and
        make the audit trail say the slot was given back later than it was.
        """
        record = self._require_hold(room_id, uid)
        data = _as_mapping(record.get("data"))
        if str(data.get("state")) != HELD:
            raise SchedulingError(
                f"reservation {uid} is {data.get('state')} and cannot be released; the slot it held "
                f"was already given back at {data.get('released_at') or 'an earlier call'}"
            )
        self.store.update(
            record["id"],
            {"state": RELEASED, "released_at": iso(self.clock())},
            actor=actor,
            source=source,
        )
        return self.hold_view(self._require_hold(room_id, uid))

    def _reap(self, room_id: str, *, actor: str | None, source: str) -> list[str]:
        """Materialise holds the clock has retired. Write paths only.

        Two researched behaviours meet here. A hold lapses with no action from
        anybody, and a booking is refused when the hold it waits on has gone. Running
        this on the two write paths - creating a hold, creating a booking - is where a
        stale ``held`` row would change an answer, so it is where the row is
        corrected. Reads never write: they report the computed state, so a ``GET``
        cannot quietly rewrite history.

        ``noticed_at`` and ``expired_at`` are both kept. The hold lapsed at
        ``reservationUntil``; the product noticed when this write ran. Those are two
        different facts and conflating them is how a hold looks like it lived five
        minutes longer than it did.
        """
        now = self.clock()
        reaped: list[str] = []
        for record in self.store.list(RESERVATION_COLLECTION, room_id=room_id, limit=1000):
            view = read_hold(record, now=now)
            if not view.expired_now:
                continue
            self.store.update(
                record["id"],
                {"state": EXPIRED, "expired_at": iso(view.until), "noticed_at": iso(now)},
                actor=actor,
                source=source,
            )
            reaped.append(view.uid)
        return reaped

    # -- bookings ------------------------------------------------------------ #

    def book(
        self,
        room_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """``POST /v2/bookings``, in-room, for a room that has an embed.

        The researched bespoke flow end to end: the room is checked, the token is
        checked, the payload is read against every documented limit, the slot is
        resolved against the live grid, the booking fields are merged with the event
        type's prefill and read-only rules, the hold is consumed if one was supplied,
        and ``BOOKING_CREATED`` is recorded.
        """
        embed = self.require_embed(room_id)
        client = self.resolve_client(embed.get("clientId"))
        require_live_token(_as_mapping(client.get("data")), now=self.clock())
        reaped = self._reap(room_id, actor=actor, source=source)

        now = self.clock()
        body = dict(payload or {})
        data = _as_mapping(self._event_type_for(room_id, body).get("data"))
        zone = str(embed.get("time_zone") or "UTC")

        request = read_booking_request(body, default_time_zone=zone)
        require_team_event_for_instant(request, data)

        # The hold the booking is submitting against is read before the grid, so the
        # grid does not report the prospect's own hold back to them as somebody
        # else's claim.
        presented_hold = None
        for key in ("reservationUid", "reservation_uid", "holdUid"):
            if body.get(key) not in (None, ""):
                presented_hold = str(body[key])
                break

        # The rescheduled booking, if it is one this room can move. Resolved *before*
        # the grid, because the busy-time exclusion has to be scoped to the room: a
        # ``bookingUidToReschedule`` naming another room's booking must not make that
        # booking invisible, or a room could book straight over a slot somebody else
        # holds by citing its id.
        existing = self._booking_by_uid(room_id, request.reschedule) if request.reschedule else None
        reschedulable = (
            str(_as_mapping(existing.get("data")).get("uid")) if existing is not None else None
        )

        window = self._window_for(
            {"start": request.start} if request.start else {}, now=now, event_type=data
        )
        slots, _ = self._grid(
            data,
            window=window,
            now=now,
            time_zone=zone,
            room_id=room_id,
            required=1,
            exclude_booking_uid=reschedulable,
            exclude_hold_uid=presented_hold,
        )
        if not request.start:
            # An instant booking named no start, so the soonest bookable slot is
            # the answer. Re-read through the same validator so the request the
            # booking is built from is one the validator has seen.
            request = read_booking_request(
                {**body, "start": resolve_instant_start(request, slots)}, default_time_zone=zone
            )
        slot = require_bookable(slots, request.start)
        fields = apply_booking_fields(data, request.booking_fields)

        hold_record = self._optional_hold(room_id, body, slot)
        if hold_record is None and embed.get("require_hold"):
            raise HoldRequired(
                "this room's embed requires a hold before a booking, and the request supplied no "
                "reservationUid. Reserve the slot first, or turn require_hold off."
            )

        existing = self._booking_by_uid(room_id, request.reschedule) if request.reschedule else None
        payload_out = booking_payload(
            request,
            uid=str(_as_mapping(existing.get("data")).get("uid"))
            if existing
            else _digest(data.get("eventTypeId"), slot["start"], iso(now), prefix="bkg_"),
            event_type=data,
            start=slot["start"],
            end=slot["end"],
            room_id=room_id,
            actor=actor,
            account=self._room_account(room_id),
        )
        # The room context is merged before the limits are re-checked, so an
        # already-full payload is refused rather than quietly losing the keys that
        # say which room it came from.
        payload_out["metadata"] = validate_metadata(
            request.metadata,
            extra=room_metadata(room_id=room_id, account=self._room_account(room_id)),
        )
        payload_out["bookingFieldsResponses"] = fields
        payload_out["booked_at"] = iso(now)
        payload_out["host"] = data.get("host")
        payload_out["held"] = hold_record is not None

        if existing is not None:
            return self._move_booking(
                existing,
                payload_out,
                room_id=room_id,
                hold_record=hold_record,
                actor=actor,
                source=source,
                reaped=reaped,
                slots_checked=len(slots),
            )

        record = self.store.create(
            BOOKING_COLLECTION, payload_out, room_id=room_id, actor=actor, source=source
        )
        if hold_record is not None:
            self._consume_hold(hold_record, record, actor=actor, source=source)
        self._emit_booking_created(record, room_id=room_id, actor=actor, source=source)
        return self._booking_response(
            record, reaped=reaped, extra={"rescheduled": False, "slots_checked": len(slots)}
        )

    def _booking_response(
        self,
        record: Mapping[str, Any],
        *,
        reaped: Sequence[str] = (),
        extra: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """What a create-booking call answers with.

        The booking's own payload is the body - not an envelope - because a client
        that copied a ``POST /v2/bookings`` call wants a booking back and not a
        storage record. ``record_id`` and ``room_id`` are added alongside, and
        ``holds_expired_before_this_write`` reports the holds this call noticed had
        lapsed, which is the one piece of the auto-expiry a caller can act on.
        """
        data = _as_mapping(record.get("data"))
        return {
            "room_id": record.get("room_id"),
            "booking": data,
            "uid": data.get("uid"),
            "eventTypeId": data.get("eventTypeId"),
            "start": data.get("start"),
            "end": data.get("end"),
            "kind": data.get("kind"),
            "video": data.get("video"),
            "metadata": data.get("metadata"),
            "status": data.get("status"),
            "record_id": record.get("id"),
            "holds_expired_before_this_write": list(reaped),
            **dict(extra or {}),
        }

    def _move_booking(
        self,
        existing: Mapping[str, Any],
        payload_out: Mapping[str, Any],
        *,
        room_id: str,
        hold_record: Mapping[str, Any] | None,
        actor: str | None,
        source: str,
        reaped: Sequence[str],
        slots_checked: int,
    ) -> dict[str, Any]:
        """Apply ``bookingUidToReschedule`` by moving the booking, not adding one.

        The researched field exists to exclude a booking's own slot from busy time,
        which only makes sense if the booking is the thing that moves. Creating a
        second record would leave the first holding the old time, and a booking
        history containing a meeting nobody is attending is worse than no history at
        all.

        So the record is updated, ``moved_from`` records where it was, and
        ``BOOKING_CREATED`` is **not** re-fired. This is a move, not a new booking,
        and a webhook consumer that treated it as one would notify the prospect
        twice about the same meeting.
        """
        data = dict(payload_out)
        data["moved_from"] = _as_mapping(existing.get("data")).get("start")
        data["moved_at"] = iso(self.clock())
        record = self.store.update(existing["id"], data, actor=actor, source=source)
        if hold_record is not None:
            self._consume_hold(hold_record, record, actor=actor, source=source)
        return self._booking_response(
            record,
            reaped=reaped,
            extra={
                "rescheduled": True,
                "moved_from": data["moved_from"],
                "slots_checked": slots_checked,
            },
        )

    def _optional_hold(
        self, room_id: str, body: Mapping[str, Any], slot: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        """The hold a booking is submitting against, if it named one.

        Three checks, each load-bearing:

        1. the hold must exist on *this* room - a hold belongs to the room that made
           it, and a hold from another room would let one prospect's hold decide
           another room's slot;
        2. the hold must be live, which is the researched auto-expiry;
        3. the hold must be for the slot being booked. Holding 09:00 and booking
           10:00 is not submitting against the hold, and without this check the hold
           would be consumed by a booking it never protected.

        The hold is also excluded from the grid the booking resolves its slot
        against, which is the fourth thing this method guarantees even though it is
        not a check: a prospect submitting against their own hold is not somebody
        else's claim on the slot, and treating them as one would refuse the booking
        at the last step of the researched custom flow.
        """
        uid = None
        for key in ("reservationUid", "reservation_uid", "holdUid"):
            if body.get(key) not in (None, ""):
                uid = str(body[key])
                break
        if uid is None:
            return None
        record = self._require_hold(room_id, uid)
        view = require_live(read_hold(record, now=self.clock()))
        if iso(view.start) != slot["start"]:
            raise SlotUnavailable(
                f"reservation {uid} holds {iso(view.start)} but the booking is for {slot['start']}; a "
                "hold is consumed only by the slot it was taken on",
                reason="hold_slot_mismatch",
                slot=dict(slot),
            )
        return record

    def _consume_hold(
        self,
        hold_record: Mapping[str, Any],
        booking: Mapping[str, Any],
        *,
        actor: str | None,
        source: str,
    ) -> None:
        """Take the hold. The slot is booked now, so the hold has done its job."""
        self.store.update(
            hold_record["id"],
            {
                "state": CONSUMED,
                "consumed_at": iso(self.clock()),
                "consumed_by": booking.get("id"),
                "consumed_by_uid": _as_mapping(booking.get("data")).get("uid"),
            },
            actor=actor,
            source=source,
        )

    def _emit_booking_created(
        self, record: Mapping[str, Any], *, room_id: str, actor: str | None, source: str
    ) -> dict[str, Any]:
        """Record ``BOOKING_CREATED`` and its webhook delivery.

        "On success, ``BOOKING_CREATED`` webhook fires; downstream automations can
        chain (see #16)." Recorded as a row rather than dispatched, because the
        automations that chain off it are other workflows in this product and a row
        is what they read - and because a webhook dispatched to a URL this product
        cannot verify would be a claim it cannot back.

        The delivery says ``pending``, not ``delivered``. The booking is real and
        the event is real; the transport is somebody else's to wire up, and a demo
        showing a delivered webhook would be teaching the wrong thing about what
        this build does.
        """
        data = _as_mapping(record.get("data"))
        event = booking_created_event(data, booking_record_id=str(record.get("id")))
        event_record = self.store.create(
            EVENT_LOG_COLLECTION, event, room_id=room_id, actor=actor, source=source
        )
        delivery = self.store.create(
            WEBHOOK_COLLECTION,
            {
                "event": BOOKING_CREATED,
                "status": "pending",
                "booking_uid": data.get("uid"),
                "room_id": room_id,
                "event_record_id": event_record.get("id"),
                "created_at": iso(self.clock()),
                "note": (
                    "The research says the webhook fires on success and downstream automations can "
                    "chain. The event is recorded so a consumer can pick it up; the transport is not "
                    "this build's to claim."
                ),
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
        self.annotate_room(
            room_id,
            {
                "last_booking": {
                    "uid": data.get("uid"),
                    "start": data.get("start"),
                    "video": data.get("video"),
                    "at": data.get("booked_at"),
                },
                "bookings": [
                    {
                        "uid": data.get("uid"),
                        "start": data.get("start"),
                        "status": data.get("status"),
                        "eventTypeId": data.get("eventTypeId"),
                        "at": data.get("booked_at"),
                    }
                ],
            },
            actor=actor,
            source=source,
        )
        return {"event": event_record, "delivery": delivery}

    def bookings(self, room_id: str, *, status: str | None = None) -> list[dict[str, Any]]:
        """A room's own bookings, newest first.

        ``list(room_id=...)`` rather than ``find({"room_id": ...})``, and that is not
        a style preference: ``room_id`` is in the store's reserved envelope
        vocabulary, so it is stripped from ``data`` on insert and never reaches the
        dynamic index. A ``find`` on it matches nothing, silently, and every
        room-scoped list in this feature would come back empty.

        ``status`` is filtered here in Python for the same reason: it *is* an
        indexed JSON path, but pairing it with the room column in one query would
        need a path that does not exist.
        """
        if self.store.get(room_id) is None:
            raise RecordNotFound(room_id)
        records = self.store.list(BOOKING_COLLECTION, room_id=room_id, limit=1000)
        rows = [{**_as_mapping(record.get("data")), "record_id": record["id"]} for record in records]
        if status is not None:
            rows = [row for row in rows if str(row.get("status")) == str(status)]
        return rows

    def booking(self, room_id: str, uid: str) -> dict[str, Any]:
        record = self._booking_by_uid(room_id, uid)
        if record is None:
            raise UnknownEventType(f"booking {uid} not found on room {room_id}")
        return {
            **_as_mapping(record.get("data")),
            "record_id": record["id"],
            "room_id": record.get("room_id"),
        }

    def _booking_by_uid(self, room_id: str, uid: str) -> dict[str, Any] | None:
        for record in self.store.find(BOOKING_COLLECTION, {"uid": str(uid)}, limit=2):
            if record.get("room_id") == room_id:
                return record
        return None

    def cancel_booking(
        self, room_id: str, uid: str, reason: str, *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Cancel a booking. The slot returns to the grid, and the record says why."""
        record = self._booking_by_uid(room_id, uid)
        if record is None:
            raise UnknownEventType(f"booking {uid} not found on room {room_id}")
        require_cancellable(_as_mapping(record.get("data")))
        updated = self.store.update(
            record["id"],
            cancel_payload(_as_mapping(record.get("data")), reason=reason, now=self.clock()),
            actor=actor,
            source=source,
        )
        self.store.create(
            EVENT_LOG_COLLECTION,
            {
                "event": "BOOKING_CANCELLED",
                "bookingUid": uid,
                "room_id": room_id,
                "reason": reason,
                "booking_record_id": record["id"],
                "booked_at": iso(self.clock()),
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
        return {**self.booking(room_id, uid), "record_id": updated["id"]}

    def booking_events(self, room_id: str, *, event: str | None = None) -> list[dict[str, Any]]:
        """A room's automation events, newest first. See :meth:`bookings` on scoping."""
        if self.store.get(room_id) is None:
            raise RecordNotFound(room_id)
        records = self.store.list(EVENT_LOG_COLLECTION, room_id=room_id, limit=1000)
        if event is None:
            return records
        return [record for record in records if str(_as_mapping(record.get("data")).get("event")) == str(event)]

    def webhooks(self, room_id: str, *, status: str | None = None) -> list[dict[str, Any]]:
        """A room's webhook deliveries. See :meth:`bookings` on scoping."""
        if self.store.get(room_id) is None:
            raise RecordNotFound(room_id)
        records = self.store.list(WEBHOOK_COLLECTION, room_id=room_id, limit=1000)
        if status is None:
            return records
        return [
            record for record in records if str(_as_mapping(record.get("data")).get("status")) == str(status)
        ]

    # -- shared helpers ------------------------------------------------------ #

    def _event_type_for(self, room_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        """The event type a write is about: the one named, or the embed's.

        A payload naming an event type the embed does not use is allowed, because a
        custom flow may route several event types through one room. What is not
        allowed is naming none while the room has no embed, which is the case
        :meth:`require_embed` already covers.
        """
        named = body.get("eventTypeId") or body.get("event_type_id")
        if named not in (None, ""):
            return self.require_event_type(str(named))
        embed = self.require_embed(room_id)
        if not embed.get("eventTypeId"):
            raise EmbedConfigError(
                f"room {room_id}'s embed names no eventTypeId, so there is nothing to book; install an "
                "embed whose eventTypeId resolves to an event type"
            )
        return self.require_event_type(str(embed["eventTypeId"]))

    def _room_account(self, room_id: str) -> str | None:
        room = self.store.get(room_id)
        if room is None:
            return None
        account = _as_mapping(room.get("data")).get("account")
        return str(account) if account else None

    def summary(self, *, room_id: str | None = None) -> dict[str, Any]:
        """Counts for the page header, over exactly the rows the page lists.

        ``room_id`` is echoed back and every count is computed with the same filter
        the corresponding list uses, so a room-scoped total above an unscoped list
        cannot be misread as a product-wide one.
        """
        now = self.clock()
        holds = self.holds(room_id) if room_id else self._all_hold_views()
        bookings = self.bookings(room_id) if room_id else self._all_bookings()

        by_state: dict[str, int] = {}
        for hold in holds:
            key = str(hold["state"])
            by_state[key] = by_state.get(key, 0) + 1
        by_kind: dict[str, int] = {}
        by_status: dict[str, int] = {}
        for booking in bookings:
            for tally, field in ((by_kind, "kind"), (by_status, "status")):
                key = str(booking.get(field))
                tally[key] = tally.get(key, 0) + 1

        return {
            "room_id": room_id,
            "at": iso(now),
            "api_versions": {"slots": SLOTS_API_VERSION, "bookings": BOOKING_API_VERSION},
            "oauth_clients": len(self.list_clients(limit=1000)),
            "event_types": len(self.list_event_types(limit=1000)),
            "team_event_types": len(self.list_event_types(kind="team", limit=1000)),
            "seated_event_types": len(self.list_event_types(kind="seated", limit=1000)),
            "routing_forms": len(self.list_forms(limit=1000)),
            "calendars": self.calendars(),
            "holds": len(holds),
            "holds_by_state": dict(sorted(by_state.items())),
            "holds_live": by_state.get(HELD, 0),
            "holds_expired": by_state.get(EXPIRED, 0),
            "default_hold_minutes": DEFAULT_RESERVATION_DURATION_MINUTES,
            "bookings": len(bookings),
            "bookings_by_kind": dict(sorted(by_kind.items())),
            "bookings_by_status": dict(sorted(by_status.items())),
            "instant_bookings": by_kind.get(INSTANT, 0),
            "recurring_bookings": by_kind.get(RECURRING, 0),
            "rooms_with_embed": len(self._rooms_with_embed()),
        }

    def _rooms_with_embed(self) -> list[str]:
        found: list[str] = []
        for record in self.store.list("room", limit=1000):
            body = _as_mapping(record.get("data")).get(ROOM_FIELD)
            if isinstance(body, Mapping) and isinstance(body.get("embed"), Mapping) and body["embed"]:
                found.append(str(record["id"]))
        return found

    def _all_hold_views(self) -> list[dict[str, Any]]:
        return [self.hold_view(record) for record in self.store.list(RESERVATION_COLLECTION, limit=1000)]

    def _all_bookings(self) -> list[dict[str, Any]]:
        return [
            _as_mapping(record.get("data")) for record in self.store.list(BOOKING_COLLECTION, limit=1000)
        ]
