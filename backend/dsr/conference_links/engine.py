"""The one object this workflow is driven through.

:class:`ProvisioningEngine` is the façade the HTTP layer calls. It holds nothing
but a :class:`~dsr.store.RecordStore` handle, which is why the feature module
builds one per request from ``StoreDep`` rather than hanging it on ``app.state``
- an ``app.state`` entry would mean editing ``dsr/api.py``, and the whole point of
the feature host is that adding a workflow is adding a file.

Every method that writes takes a required ``source=``. That is not decoration.
The audit row is the product's guarantee, and an audit row that names a string
rather than a route cannot be traced back to the request that caused it - a
defect this codebase has already shipped once. A required keyword means the
omission is a ``TypeError`` at the call site rather than a silently untraceable
row in production.

Three collections, all schema-flexible
--------------------------------------
``meeting_location``, ``provider_connection``, ``booking``. None has a
migration, a typed column, or a required field beyond the researched contract,
because a team adding a field must not need to coordinate with anyone. Every
filter goes through ``find()`` and therefore through the dynamic index, so a
field added later is queryable the moment it is written.

Room scoping, and what it is not
--------------------------------
A booking belongs to the room the buyer was looking at, so a booking is keyed to
a room and the routes that reach one take a room id. That is a *key*, not an ACL.
This product's rooms carry no membership table, so "not in this room" and "not
there at all" are the same honest 404 - claiming a 403 would be claiming
something the store cannot back. See the ``room-scope-is-a-key-not-an-acl``
inference.

The chain a booking walks
-------------------------
:meth:`book` is the researched flow in one call - step 1's Location, step 2's
connection, step 3's fresh conference written into both Location fields - and it
returns what it decided rather than only what it created. :meth:`swap` is step 4,
and returns the researched ``previousLocation``/``location`` pair beside the
notification the researched endpoint raises with it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from dsr.conference_links import (
    connections,
    inferences as inferences_module,
    locations,
    minting,
    provider_status,
    swapping,
    vocabulary as vocab,
)
from dsr.conference_links.errors import (
    BookingNotFound,
    ConferenceLinkError,
    ConferenceNotFound,
    ConferenceReuse,
    ConnectionNotFound,
    DefaultLocationRequired,
    DuplicateProviderConnection,
    LocationNotFound,
    ProviderNotConnected,
    UnknownLocation,
)

#: The three collections this workflow owns. Named here so a filter and a route
#: cannot disagree about which one they mean.
MEETING_LOCATIONS = "meeting_location"
CONNECTIONS = "provider_connection"
BOOKINGS = "booking"

#: Where a Location config comes from when a booking names none. The researched
#: flow does not say, because the researched product's booking form always
#: resolves one. Recorded as the ``default-location`` inference's sibling in
#: :meth:`ProvisioningEngine.default_location`; here it is only the fact.
DEFAULT_LOCATION_KIND = "conference-details"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class ProvisioningEngine:
    """Configure a Location, connect a provider, and provision the link."""

    def __init__(self, store: Any, *, now: Any = None) -> None:
        self.store = store
        # Injectable so a test can assert on ordering and timestamps without
        # freezing the whole process clock.
        self._now = now or _now

    def stamp(self) -> str:
        text = self._now() if callable(self._now) else str(self._now)
        return text

    # -- vocabulary --------------------------------------------------------- #

    def vocabulary(self) -> dict[str, Any]:
        """Every researched value, served as data.

        A client renders its pickers from this rather than from a list compiled
        into the page, so a value added server-side reaches every client at
        once. The providers and the Location catalogue are included because both
        are researched value rather than configuration, and a reviewer should be
        able to read the whole vocabulary in one response.
        """
        return {
            **vocab.describe(),
            "providers": connections.provider_catalogue(),
            "location_catalogue": locations.catalogue(),
            "provisioning": {
                "retry_attempts": provider_status.RETRY_ATTEMPTS,
                "apps_status_fields": list(provider_status.STATUS_FIELDS),
            },
        }

    def inferences(self) -> dict[str, Any]:
        """Where this workflow stops being sourced. See :mod:`dsr.conference_links.inferences`."""
        return inferences_module.describe()

    # -- Meeting Type locations --------------------------------------------- #

    def present_location(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """A Location flattened to ``data`` plus its id, its state and its wire form.

        Every read of a Location goes through this, and so does every internal
        use of one, so there is exactly one shape for a Location in this package.

        Tolerant of a row whose ``kind`` is not one of the researched seven. The
        store is schema-flexible and any client can ``POST /api/records/
        meeting_location`` a payload this workflow would refuse, so a list
        endpoint that raised on one of those rows would be a 500 caused by a
        single bad record - and a page that shows nothing rather than a page that
        works is not a better answer. The row is presented with ``kind_errors``
        naming what is wrong, so the admin can see and fix it.
        """
        data = dict(record.get("data") or {})
        raw_kind = str(data.get("kind") or "")
        kind: str | None = None
        kind_error = ""
        try:
            kind = locations.normalise_kind(raw_kind)
        except ConferenceLinkError as exc:
            kind_error = str(exc)

        return {
            "id": record.get("id"),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            "revision": record.get("revision"),
            "is_default": bool(data.get("is_default")),
            "mints_conference": bool(kind and locations.needs_conference(kind)),
            "provider": locations.provider_for(kind) if kind else None,
            "gaps": locations.missing_for(kind, data) if kind else ["kind"],
            "wire_location": self._safe_wire(data),
            "kind_errors": [kind_error] if kind_error else [],
            **data,
        }

    def _safe_wire(self, data: Mapping[str, Any]) -> dict[str, Any] | None:
        """The Cal-shaped Location, or ``None`` for a Location too malformed to render.

        A Location can be stored with a gap - that is the researched decision in
        ``missing_for`` - and a preview endpoint must not raise on a record the
        list endpoint is happy to show. A client renders "not available" rather
        than a page that 500s on one bad row.
        """
        try:
            return locations.wire_location(str(data.get("kind") or ""), data)
        except Exception:
            return None

    def meeting_locations(
        self, *, kind: str | None = None, include_defaulted: bool = True
    ) -> list[dict[str, Any]]:
        """Every configured Location, newest first, optionally filtered by kind."""
        records = self.store.list(MEETING_LOCATIONS, limit=500)
        listed = [self.present_location(record) for record in records]
        if kind is not None:
            listed = [row for row in listed if row.get("kind") == locations.normalise_kind(kind)]
        if not include_defaulted:
            listed = [row for row in listed if not row.get("is_default")]
        return listed

    def meeting_location(self, location_id: str) -> dict[str, Any]:
        """One Location, or raise. Every read of one goes through here."""
        record = self.store.get(location_id)
        if record is None or record.get("collection") != MEETING_LOCATIONS:
            raise LocationNotFound(f"location {location_id} is not configured on any Meeting Type")
        return self.present_location(record)

    def _resolve_connection(self, data: Mapping[str, Any]) -> dict[str, Any] | None:
        """The connection record a Location names, if any.

        Resolved rather than stored on the Location so a swapped connection is
        picked up by every Location that names it. "a rep's integration is
        swapped" is researched user_flow step 4, and a Location that cached the
        old connection would keep provisioning through the credential that was
        just revoked.
        """
        connection_id = str(data.get("connection_id") or "").strip()
        if not connection_id:
            return None
        record = self.store.get(connection_id)
        if record is None or record.get("collection") != CONNECTIONS:
            return None
        return record

    def create_location(
        self, payload: Mapping[str, Any], *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Add one option to a Meeting Type's Location picker.

        A Location with ``is_default`` when none exists becomes the default, so
        the researched "Set as Default" control is never a prerequisite for a
        working Meeting Type. A *second* Location claiming the default is
        refused - see the ``exactly-one-default`` inference.
        """
        body = dict(payload)
        kind = locations.normalise_kind(body.get("kind"))
        body["kind"] = kind
        if not str(body.get("name") or "").strip():
            body["name"] = vocab.LOCATION_LABELS.get(kind, kind)

        existing = self.meeting_locations()
        if not body.get("is_default"):
            body["is_default"] = not any(row.get("is_default") for row in existing)
        if body.get("is_default") and any(row.get("is_default") for row in existing):
            raise DefaultLocationRequired(
                "a Meeting Type already has a default Location; use the set-default route to "
                "change which one is in force rather than creating a second default"
            )

        body["gaps"] = locations.missing_for(kind, body)
        body["one_time"] = locations.needs_conference(kind)
        body["expected_outcome"] = locations.expected_outcome(kind)

        if locations.needs_conference(kind) and not body.get("connection_id"):
            body["blocked_reason"] = vocab.CONNECTION_MANDATORY_QUOTE

        record = self.store.create(MEETING_LOCATIONS, body, actor=actor, source=source)
        return self.present_location(record)

    def amend_location(
        self, location_id: str, payload: Mapping[str, Any], *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Change a Location option, or be told every reason why not.

        The constraint is this build's and has one rule: a Location that has
        already provisioned a conference has decided where real people clicked,
        so the field that decides *which* provider gets the link is frozen.
        Renaming it, reordering it and re-describing it are still allowed,
        because none of those can invalidate a link somebody already used.
        Every offending field comes back at once, so correcting a rejected patch
        is one attempt rather than one per field.
        """
        current = self.meeting_location(location_id)
        proposed = {**current, **{k: v for k, v in dict(payload).items() if v is not None}}

        try:
            kind = locations.normalise_kind(proposed.get("kind"))
        except Exception as exc:
            raise LocationNotFound(str(exc)) from exc

        provisioned = bool(current.get("provisions_conferences")) or bool(
            self.store.count_where(BOOKINGS, {"location_id": location_id}, include_deleted=False)
        )
        blockers: list[str] = []
        if provisioned and kind != current.get("kind"):
            blockers.append(
                f"kind: {current.get('kind')} has already provisioned conferences, so it cannot "
                f"become {kind}; add a second Location option and swap bookings onto it instead"
            )
        if (
            provisioned
            and locations.needs_conference(kind)
            and not str(proposed.get("connection_id") or "").strip()
        ):
            blockers.append(
                "connection_id: a provisioned one-time Location cannot drop its connection"
            )

        if blockers:
            raise DefaultLocationRequired("; ".join(blockers))

        patch = {
            key: value
            for key, value in proposed.items()
            if key not in ("id", "created_at", "updated_at", "revision", "provisions_conferences")
        }
        patch["kind"] = kind
        patch["gaps"] = locations.missing_for(kind, patch)
        patch["one_time"] = locations.needs_conference(kind)
        patch["expected_outcome"] = locations.expected_outcome(kind)

        record = self.store.update(location_id, patch, actor=actor, source=source)
        return self.present_location(record)

    def remove_location(
        self, location_id: str, *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Remove a Location option. The default one has to move first.

        Removing the default would leave a Meeting Type whose picker has no
        answer, and a booking taken after that would have nothing to inherit. A
        Location that has provisioned conferences cannot be removed at all,
        because the audit trail and every booking naming it would point at
        nothing - which is the reason the core store soft-deletes rather than
        hard-deletes, and this build inherits that.
        """
        current = self.meeting_location(location_id)
        if self.store.count_where(BOOKINGS, {"location_id": location_id}, include_deleted=False):
            raise DefaultLocationRequired(
                f"location {location_id} has provisioned bookings; a booking names it and the "
                "audit trail must not point at nothing, so set another Location as the default "
                "and swap those bookings first"
            )
        if current.get("is_default"):
            others = [row for row in self.meeting_locations() if row.get("id") != location_id]
            if not others:
                raise DefaultLocationRequired(
                    "this is the only Location on the Meeting Type, and it is the default; "
                    "a Meeting Type with no Location cannot book"
                )
            raise DefaultLocationRequired(
                "set another Location as the default before removing this one; the researched "
                "'Set as Default' control is how a Meeting Type declares which option is in force"
            )
        return self.store.delete(location_id, actor=actor, source=source)

    def set_default_location(
        self, location_id: str, *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """The researched "Set as Default" control, in one atomic step.

        Clearing the old default and setting the new one as two writes would let
        a crash between them leave a Meeting Type with no default, which is the
        state this workflow refuses. A :meth:`~dsr.db.audited.AuditedDatabase.transaction`
        makes it one commit, and each of the two writes still gets its own audit
        row so the log says when the switch was thrown.
        """
        current = self.meeting_location(location_id)
        previous_default = next(
            (row for row in self.meeting_locations() if row.get("is_default")), None
        )
        if previous_default is not None and previous_default.get("id") == location_id:
            return current

        with self.store.db.transaction(actor=actor, source=source) as tx:
            if previous_default is not None:
                tx.update(previous_default["id"], {"is_default": False})
            tx.update(location_id, {"is_default": True})

        return self.meeting_location(location_id)

    def default_location(self) -> dict[str, Any] | None:
        """The Location a booking inherits when it names none.

        The researched flow does not say what happens when a caller books
        without naming a Location, because the researched product's form always
        resolves one. This build does not guess: with a default in force it is
        used, and with none it refuses the booking rather than inventing a
        provider. The refusal names both possibilities so the caller knows
        whether to set a default or name a Location.
        """
        for row in self.meeting_locations():
            if row.get("is_default"):
                return row
        return None

    # -- Integrations ------------------------------------------------------- #

    def present_connection(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """A connection flattened to ``data`` plus its readiness.

        The readiness report is computed on every read rather than stored, so a
        connection that goes stale the moment its token is revoked cannot keep
        claiming to be usable. ``token_present`` is a boolean and nothing more:
        the credential itself is never on the record, and therefore never in the
        audit log or its JSONL mirror.
        """
        data = dict(record.get("data") or {})
        return {
            "id": record.get("id"),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            "revision": record.get("revision"),
            "readiness": connections.readiness(record),
            **data,
        }

    def provider_connections(
        self, *, provider: str | None = None, ready: bool | None = None
    ) -> list[dict[str, Any]]:
        """Every connected provider, newest first."""
        records = self.store.list(CONNECTIONS, limit=500)
        listed = [self.present_connection(record) for record in records]
        if provider is not None:
            wanted = connections.normalise_provider(provider)
            listed = [row for row in listed if row.get("provider") == wanted]
        if ready is not None:
            listed = [row for row in listed if bool(row.get("readiness", {}).get("ready")) is ready]
        return listed

    def provider_connection(self, connection_id: str) -> dict[str, Any]:
        """One connection, or raise."""
        record = self.store.get(connection_id)
        if record is None or record.get("collection") != CONNECTIONS:
            raise ConnectionNotFound(f"connection {connection_id} is not on the Integrations tab")
        return self.present_connection(record)

    def connect(
        self, payload: Mapping[str, Any], *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Connect a provider on the Integrations tab.

        The researched mandatory step. One connection per host per provider, so a
        second is a 409 rather than a second credential the picker would have to
        choose between with nothing to choose on.
        """
        body = connections.normalise(payload)
        for record in self.store.list(CONNECTIONS, limit=500):
            if connections.find_duplicate([record], body["provider"], body["host"]) is not None:
                raise DuplicateProviderConnection(
                    f"{body['provider']} is already connected for host {body['host']}; a rep has "
                    "one credential per provider, and the researched swap replaces it rather than "
                    "adding a second"
                )
        record = self.store.create(CONNECTIONS, body, actor=actor, source=source)
        return self.present_connection(record)

    def reauthorize(
        self,
        connection_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Re-point a connection at a new credential, or move it to a new host.

        This is the researched "a rep's integration is swapped" half of step 4,
        and it is a distinct operation from :meth:`amend_connection` because a
        swap is an *event*: it changes what every Location naming this connection
        will provision through, and the bookings already provisioned through it
        keep their links. A PATCH that moved the host would be the same change
        wearing a quieter name.
        """
        current = self.provider_connection(connection_id)
        body = connections.normalise(payload, existing=current)
        if body["provider"] != current.get("provider") or body["host"] != current.get("host"):
            for record in self.store.list(CONNECTIONS, limit=500):
                if record.get("id") == connection_id:
                    continue
                if connections.find_duplicate([record], body["provider"], body["host"]):
                    raise DuplicateProviderConnection(
                        f"{body['provider']} is already connected for host {body['host']}"
                    )

        provisioned = self.store.count_where(BOOKINGS, {"connection_id": connection_id})
        record = self.store.update(
            connection_id,
            {
                **body,
                "swapped_at": self.stamp(),
                "bookings_through_this_connection": provisioned,
            },
            actor=actor,
            source=source,
        )
        result = self.present_connection(record)
        result["swap"] = {
            "from": {
                "provider": current.get("provider"),
                "host": current.get("host"),
                "state": (current.get("readiness") or {}).get("state"),
            },
            "to": {"provider": result.get("provider"), "host": result.get("host")},
            "bookings_already_provisioned": provisioned,
            "note": (
                "bookings already provisioned through this connection keep their links; the "
                "researched swap re-provisions a booking's location when it is asked to, and "
                "does not retroactively re-issue links nobody asked to change"
            ),
        }
        return result

    def amend_connection(
        self,
        connection_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Change a connection's note or state, and nothing that moves it.

        The researched provider and host are what a Location resolves to, and
        changing either of them under bookings that already exist is the
        :meth:`reauthorize` operation. A PATCH that did it silently would make
        the same change unreachable by name, which is exactly the sort of thing
        a reviewer discovers from the diff.
        """
        current = self.provider_connection(connection_id)
        proposed = {**current, **{k: v for k, v in dict(payload).items() if v is not None}}
        if str(proposed.get("provider") or "") != str(current.get("provider") or ""):
            raise ConnectionNotFound(
                "a connection's provider cannot be changed; remove it and connect the new one"
            )
        if str(proposed.get("host") or "") != str(current.get("host") or ""):
            raise ConnectionNotFound(
                "a connection's host cannot be changed by a patch; that is a re-authorisation, "
                "which is a separate request because it re-points every Location that names it"
            )
        body = connections.normalise(payload, existing=current)
        record = self.store.update(connection_id, body, actor=actor, source=source)
        return self.present_connection(record)

    def disconnect(
        self, connection_id: str, *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Remove a connection, refusing while a Location needs it.

        "Connecting Zoom on the Integrations tab is mandatory for this one to
        work" cuts both ways: a Location that names a connection which no longer
        exists is a Location that cannot book, and the seller finds out at the
        moment a prospect is waiting. So a connection that is still named is
        refused, and the message names how many Locations would be stranded.
        """
        self.provider_connection(connection_id)
        naming = self.store.count_where(MEETING_LOCATIONS, {"connection_id": connection_id})
        if naming:
            raise ProviderNotConnected(
                f"{naming} Location option(s) still name connection {connection_id}; re-point "
                "them at another connection or remove them before disconnecting"
            )
        return self.store.delete(connection_id, actor=actor, source=source)

    # -- Bookings ----------------------------------------------------------- #

    def present_booking(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """A booking flattened to ``data`` plus its id and its location state.

        ``state`` is derived, not stored, from the researched Location states -
        so it cannot drift from the fields it summarises, which is what lets the
        list page colour a booking by state without each row remembering to
        update it.

        ``provider_status`` is the *latest* failure report, and it is here on
        every read rather than only on the write's response. A field the write
        returns and the read does not is a field a client can only have if it
        happened to be the one that wrote, which is a worse API than one that
        simply omits it.
        """
        data = dict(record.get("data") or {})
        history = list(data.get("provider_status_history") or [])
        return {
            "id": record.get("id"),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            "revision": record.get("revision"),
            "state": booking_state(data),
            "provider_status": history[-1] if history else None,
            **data,
        }

    def _require_location(self, payload: Mapping[str, Any], room_id: str | None) -> dict[str, Any]:
        """The Location a booking will be taken on, or the reason it cannot be.

        A booking may name ``location_id`` or come to the researched default.
        Neither present is a refusal rather than a guess, and the message says
        which of the two fixes it is.
        """
        location_id = str(payload.get("location_id") or "").strip()
        if location_id:
            record = self.store.get(location_id)
            if record is None or record.get("collection") != MEETING_LOCATIONS:
                raise UnknownLocation(
                    f"location {location_id} is not configured on any Meeting Type; the "
                    "researched flow starts at the Meeting Type's Location setting"
                )
            return self.present_location(record)

        default = self.default_location()
        if default is None:
            raise UnknownLocation(
                "no Location is in force: name location_id, or set a default with the "
                "set-default route, because a booking with no Location cannot provision a link"
            )
        return default

    def book(
        self,
        room_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Take a booking and provision its Location, in one call.

        This is user_flow steps 1 to 3 end to end: the Location option is
        resolved, the mandatory connection is enforced, a *fresh* conference is
        minted for this booking alone, and the link is written into both
        researched Location fields - booking ``location`` and meeting
        ``meetingLocation``.

        A booking whose Location cannot produce a conference is still a booking.
        ``Conference Details`` is "for those who don't want to use one-time
        links", ``In-Person Meeting`` describes a place, and ``Ask the Guest``
        defers the decision to the guest - so those return the researched
        outcome and no conference, rather than refusing a real meeting. What
        refuses is a *caller* mistake, and a missing connection the research
        calls mandatory.
        """
        body = dict(payload)
        location = self._require_location(body, room_id)
        kind = str(location.get("kind"))

        # The default id counts what is already written rather than hashing the
        # room, because `hash()` on a str is salted per process - a demo built on
        # it would produce different ids on two runs of the same seeder, and a
        # reviewer diffing two seeds would be reading noise.
        booking_uid = (
            str(body.get("booking_uid") or "").strip()
            or f"bk_{self.stamp()}_{len(self.store.list(BOOKINGS, limit=1000)) + 1:04d}"
        )
        outcome = locations.expected_outcome(kind)
        data: dict[str, Any] = {
            "booking_uid": booking_uid,
            "location_id": location.get("id"),
            "location_kind": kind,
            "location_name": location.get("name"),
            "attendee_email": str(body.get("attendee_email") or body.get("email") or "").strip()
            or None,
            "attendee_name": str(body.get("attendee_name") or body.get("name") or "").strip()
            or None,
            "starts_at": body.get("starts_at") or body.get("start") or None,
            "provisions_conferences": False,
            "invite_template": str(body.get("invite_template") or swapping.DEFAULT_TEMPLATE),
            # The two researched dynamic tags, carried on the booking so the
            # invite read has something to substitute besides a path derived
            # from the uid. A caller that knows its own booking URLs supplies
            # them here; a caller that does not gets the derived ones.
            "reschedule_url": str(body.get("reschedule_url") or "").strip() or None,
            "cancel_url": str(body.get("cancel_url") or "").strip() or None,
        }

        if outcome == "conference-provisioned":
            data.update(
                self._provision(
                    data,
                    location,
                    room_id=room_id,
                    actor=actor,
                    source=source,
                )
            )
        elif outcome == "static":
            data.update(
                {
                    "location_provider": "static",
                    vocab.MEETING_LOCATION_FIELD: str(location.get("conference_details") or ""),
                    vocab.BOOKING_LOCATION_FIELD: locations.wire_location(kind, location),
                    "provision_outcome": outcome,
                    "location_state": "static",
                }
            )
        elif outcome == "in-person":
            data.update(
                {
                    "location_provider": "in-person",
                    vocab.MEETING_LOCATION_FIELD: str(
                        location.get("custom_text") or location.get("name") or ""
                    ),
                    vocab.BOOKING_LOCATION_FIELD: locations.wire_location(kind, location),
                    "provision_outcome": outcome,
                    "location_state": "in-person",
                }
            )
        else:
            data.update(
                {
                    "location_provider": "attendee-defined",
                    "provision_outcome": outcome,
                    "location_state": "awaiting-guest",
                    "guest_prompt": str(location.get("attendee_prompt") or "").strip() or None,
                }
            )

        record = self.store.create(BOOKINGS, data, room_id=room_id, actor=actor, source=source)
        return self.present_booking(record)

    def _provision(
        self,
        data: dict[str, Any],
        location: Mapping[str, Any],
        *,
        room_id: str | None,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Mint a fresh conference for one booking and write it into both fields.

        The researched mandatory connection is enforced here rather than earlier,
        so the refusal arrives with the booking in hand and the message can name
        what it was trying to provision. The reuse prohibition is enforced
        against every conference already written, which is why it is a store read
        and not a property of the identity function.
        """
        kind = str(location.get("kind"))
        connection_record = self._resolve_connection(location)
        readiness = connections.require_connected(kind, connection_record, location=location)

        connection_data = dict((connection_record or {}).get("data") or {})
        booking_uid = str(data["booking_uid"])
        minted = minting.mint(
            locations.provider_for(kind) or kind,
            booking_uid,
            scope=str(room_id or ""),
            # The *link* host, never the calendar id: a Workspace account id is
            # not a domain, and putting one in a join URL produces a link that
            # looks plausible and resolves nowhere.
            host=connection_data.get("link_host"),
            calendar_id=connection_data.get("calendar_id"),
            start=data.get("starts_at"),
        )
        claim = minting.claim(self._all_conferences(), minted["conference_id"], booking_uid)

        patch = {
            **minted,
            "conference_claim": claim,
            "provision_outcome": "conference-provisioned",
            "location_state": "provisioned",
            "provisions_conferences": True,
            "connection_id": str(location.get("connection_id") or "") or None,
            "connection_state_at_provision": readiness.get("state"),
        }
        patch.update(
            minting.apply_to_booking(
                minted,
                data,
                wire=locations.wire_location(kind, location, request_id=minted["conference_id"]),
            )
        )
        return patch

    def _all_conferences(self) -> list[dict[str, Any]]:
        """Every conference already provisioned, across every booking.

        Read across rooms on purpose. The researched warning is that reusing
        conference data "across different events" exposes a meeting, and a
        conference reused in *another room* is exactly that - scoping this check
        to the current room would make the prohibition hold only where nobody was
        looking.
        """
        found: list[dict[str, Any]] = []
        for record in self.store.list(BOOKINGS, limit=1000):
            data = record.get("data") or {}
            if data.get("conference_id"):
                found.append(
                    {
                        "conference_id": data.get("conference_id"),
                        "booking_uid": data.get("booking_uid"),
                        "room_id": record.get("room_id"),
                    }
                )
        return found

    def bookings(
        self,
        room_id: str | None = None,
        *,
        location_id: str | None = None,
        state: str | None = None,
        provider: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Bookings, newest first, filterable by Location, state or provider.

        Counted over the room when one is named, because a Meeting Type's
        bookings are the room's meetings and a header that reported the whole
        collection would be reporting somebody else's meetings.
        """
        records = (
            self.store.list(BOOKINGS, room_id=room_id, limit=limit)
            if room_id
            else self.store.list(BOOKINGS, limit=limit)
        )
        listed = [self.present_booking(record) for record in records]
        if location_id:
            listed = [row for row in listed if row.get("location_id") == location_id]
        if provider:
            listed = [row for row in listed if row.get("location_provider") == provider]
        if state:
            listed = [row for row in listed if row.get("state") == state]
        return listed

    def _require_booking(self, room_id: str, booking_uid: str) -> dict[str, Any]:
        """One booking in this room, or a 404.

        Both "not in this room" and "not there at all" answer 404, and the
        reason is in this module's docstring: the store has no membership table,
        so anything else would be a claim it cannot back.
        """
        for record in self.store.list(BOOKINGS, room_id=room_id, limit=1000):
            data = record.get("data") or {}
            if str(data.get("booking_uid") or "") == booking_uid or record.get("id") == booking_uid:
                return self.present_booking(record)
        raise BookingNotFound(f"booking {booking_uid} is not in room {room_id}")

    def booking(self, room_id: str, booking_uid: str) -> dict[str, Any]:
        """One booking, with its conference, its invite and its swap history."""
        record = self._require_booking(room_id, booking_uid)
        record["invite"] = self.invite(room_id, booking_uid)
        record["history"] = self.history(room_id, booking_uid)
        return record

    def conference(self, room_id: str, booking_uid: str) -> dict[str, Any]:
        """The conference on this booking, or raise.

        A booking whose Location is static, in-person or awaiting the guest has
        no conference, and that is a 404 rather than an empty object - there is
        nothing to show and a client that rendered an empty card would be
        implying one exists.
        """
        record = self._require_booking(room_id, booking_uid)
        if not record.get("conference_id"):
            raise ConferenceNotFound(
                f"booking {booking_uid} has no conference: its Location is "
                f"{record.get('location_kind')!r}, which is {record.get('provision_outcome')!r}"
            )
        return {
            "booking_uid": booking_uid,
            "conference_id": record.get("conference_id"),
            "provider": record.get("location_provider"),
            "url": record.get(vocab.MEETING_LOCATION_FIELD),
            "meeting_id": record.get("meeting_id"),
            "scope": record.get("scope"),
            "unique": record.get("unique"),
            "create_request": record.get("create_request"),
            "outbound_not_sent": True,
        }

    def invite(self, room_id: str, booking_uid: str) -> dict[str, Any]:
        """The invite body, with the researched dynamic tags resolved."""
        record = self._require_booking(room_id, booking_uid)
        return swapping.render_invite(
            record,
            str(record.get("invite_template") or ""),
            reschedule_url=str(record.get("reschedule_url") or "") or None,
            cancel_url=str(record.get("cancel_url") or "") or None,
        )

    def preview_invite(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Render an invite body without booking anything.

        A pure read of the caller's own template, so the admin page can show the
        two researched tags resolved before a booking exists to resolve them
        against.
        """
        body = dict(payload)
        return swapping.render_invite(
            body,
            str(body.get("invite_template") or ""),
            reschedule_url=str(body.get("reschedule_url") or "") or None,
            cancel_url=str(body.get("cancel_url") or "") or None,
        )

    def provision(
        self,
        room_id: str,
        booking_uid: str,
        payload: Mapping[str, Any] | None = None,
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Run the researched automation again for a booking that has no link.

        "automatic on booking" is what :meth:`book` does, so this exists for the
        two cases the research names: a retry after ``appsStatus`` reported a
        failure, and a booking whose Location was ``Ask the Guest`` and has since
        been answered. It is idempotent in the researched sense - the conference
        identity is derived from the booking, so a second attempt converges on
        the same conference rather than minting a second one.

        Refuses a booking that already has a link. "Always generate a unique
        conference for each event" makes a *second* conference for one event
        wrong, not merely redundant.
        """
        record = self._require_booking(room_id, booking_uid)
        if record.get("conference_id"):
            raise ConferenceReuse(
                f"booking {booking_uid} already holds conference {record.get('conference_id')}; "
                "generating a unique conference for each event means a second one is wrong. Use "
                "the location swap to move the booking to a different tool."
            )
        supplied = dict(payload or {})

        # The researched Ask the Guest option, answered. Checked *before* the
        # Location test below, because an Ask the Guest Location is a Location
        # that mints no conference and the whole point of this branch is that the
        # guest supplied the location instead.
        if str(record.get("location_state") or "") == "awaiting-guest" and supplied.get(
            "guest_location"
        ):
            # The guest's own location is recorded as `attendeeAddress`-shaped,
            # which is one of the eight researched location types and is exactly
            # what Cal names for a location an attendee supplied.
            answered = {
                "location_provider": "attendee-defined",
                vocab.MEETING_LOCATION_FIELD: str(supplied.get("guest_location")),
                vocab.BOOKING_LOCATION_FIELD: {
                    "type": "attendeeAddress",
                    "address": str(supplied.get("guest_location")),
                },
                "provision_outcome": "guest-supplied",
                "location_state": "provisioned",
            }
            updated = self.store.update(record["id"], answered, actor=actor, source=source)
            return self.present_booking(updated)

        location_id = str(record.get("location_id") or "")
        location = self.meeting_location(location_id) if location_id else None
        if location is None or not locations.needs_conference(str(location.get("kind"))):
            raise UnknownLocation(
                f"booking {booking_uid}'s Location is {record.get('location_kind')!r}, which does "
                "not mint a conference; swap it to a one-time provider to provision a link"
            )

        patch = self._provision(
            {
                **record,
                "booking_uid": record.get("booking_uid"),
                "starts_at": record.get("starts_at"),
            },
            location,
            room_id=room_id,
            actor=actor,
            source=source,
        )
        updated = self.store.update(record["id"], patch, actor=actor, source=source)
        result = self.present_booking(updated)
        result["provision"] = {
            "retried": True,
            "reason": supplied.get("reason") or "explicit re-provision",
        }
        return result

    def swap(
        self,
        room_id: str,
        booking_uid: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Move a booking to a different tool. user_flow step 4, in one call.

        Cal's researched endpoint "also provisions a conference link" and
        "Attendees are notified of the location change by email", so this does
        both: a new conference is minted under the new provider, the researched
        ``previousLocation``/``location`` pair is written, and the notification
        is recorded.

        The booking row and the location history are written in one transaction,
        so a swap that recorded a new link without a history - or a history
        without the link - cannot be observed.
        """
        record = self._require_booking(room_id, booking_uid)
        target = self._swap_target(record, payload)
        plan = swapping.plan_swap(
            record,
            target,
            reason=str(payload.get("reason") or "meeting-moved-tool"),
            detail=payload.get("detail"),
            resulting_url=self._resulting_url(record, target, room_id),
        )

        patch: dict[str, Any] = {
            "location_id": target.get("location_id") or record.get("location_id"),
            "location_kind": plan["target_kind"],
            "location_name": target.get("name") or record.get("location_name"),
            "previous_location": plan["previous_location"],
            vocab.PREVIOUS_LOCATION_FIELD: plan["previous_location"],
            "location_changed_at": self.stamp(),
            "location_change_reason": plan["reason"],
        }

        if plan["reprovisions"]:
            patch.update(
                self._provision(
                    {
                        **record,
                        "booking_uid": record.get("booking_uid"),
                        "starts_at": record.get("starts_at"),
                    },
                    target,
                    room_id=room_id,
                    actor=actor,
                    source=source,
                )
            )
            patch["location_state"] = "swapped"
            patch["provision_outcome"] = "conference-provisioned"
        else:
            # The old conference is *cleared*, not left behind. A booking that
            # kept its Zoom conference id while its Location became a static
            # link would report two links, and the stale one is the one a guest
            # might still have bookmarked.
            patch.update(
                {
                    "location_provider": plan["target_kind"],
                    "conference_id": None,
                    "meeting_id": None,
                    "create_request": None,
                    "gateway_url": None,
                    "redirects_to": None,
                    "provisions_conferences": False,
                    vocab.MEETING_LOCATION_FIELD: str(target.get("url") or ""),
                    vocab.BOOKING_LOCATION_FIELD: locations.wire_location(
                        plan["target_kind"], target
                    ),
                    "provision_outcome": locations.expected_outcome(plan["target_kind"]),
                    "location_state": "swapped",
                }
            )

        history = list(record.get("location_history") or [])
        history.append(
            {
                "at": patch["location_changed_at"],
                "reason": plan["reason"],
                "detail": plan["detail"],
                vocab.PREVIOUS_LOCATION_FIELD: plan["previous_location"],
                vocab.BOOKING_LOCATION_FIELD: plan["location"],
                "notification": plan["notification"],
            }
        )
        patch["location_history"] = history

        updated = self.store.update(record["id"], patch, actor=actor, source=source)
        result = self.present_booking(updated)
        result["swap"] = plan
        return result

    def _resulting_url(
        self, record: Mapping[str, Any], target: Mapping[str, Any], room_id: str
    ) -> str | None:
        """The join URL a swap to ``target`` would leave the booking at.

        Computed rather than read off the request, because the researched
        refusal is about the *result*: this build derives a conference's identity
        from the provider, the room and the booking, so a swap to the same
        provider lands on the same conference and therefore the same link. Naming
        the same provider is not a change; leaving the guest on the same URL is
        not a change either, and only the second is the one attendees would be
        told about.

        A target that mints no conference keeps whatever text it carries, and a
        target with neither returns ``None`` so the caller falls back to the
        target's own ``url``.
        """
        kind = str(target.get("kind") or "")
        if not locations.needs_conference(kind):
            return (
                str(
                    target.get("url")
                    or target.get("conference_details")
                    or target.get("custom_text")
                    or ""
                )
                or None
            )

        provider = locations.provider_for(kind) or kind
        connection_id = str(target.get("connection_id") or "").strip()
        connection = self.store.get(connection_id) if connection_id else None
        host = None
        if connection and connection.get("collection") == CONNECTIONS:
            host = (connection.get("data") or {}).get("link_host")

        return minting.mint(
            provider,
            str(record.get("booking_uid") or record.get("id") or ""),
            scope=str(room_id or ""),
            host=host,
        )["url"]

    def _swap_target(self, record: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
        """Where the booking is being moved to, as a Location-shaped dict.

        A swap may name a configured Location - the researched "the meeting
        moves to a different tool", which is a change of Meeting Type option -
        or supply the new location inline, which is the research's Gong/Zoom
        case and the case where an admin is moving one meeting without
        re-pointing the whole Meeting Type.

        An inline swap that names no connection is resolved **by provider**,
        never by inheriting the booking's. Inheriting is the obvious shortcut
        and it is wrong: moving a Zoom booking to Gong while carrying Zoom's
        credential would mint a Gong conference id from a Zoom connection, and
        the researched mandatory step - "Connecting Zoom on the Integrations tab
        is mandatory for this one to work" - would be satisfied by the wrong
        provider's key. So the lookup asks for a connection *for the target
        provider*, and if there is none :func:`require_connected` refuses,
        which is the same gate a first booking meets.
        """
        location_id = str(payload.get("location_id") or "").strip()
        if location_id:
            target = self.meeting_location(location_id)
            return {
                **target,
                "url": locations.wire_location(str(target.get("kind")), target).get("link")
                or str(target.get("conference_details") or target.get("custom_text") or ""),
            }

        kind = locations.normalise_kind(payload.get("kind") or record.get("location_kind"))
        inline = dict(payload)
        inline["kind"] = kind
        if not str(payload.get("connection_id") or "").strip():
            inline["connection_id"] = self._connection_for_provider(kind, inline)
        return {
            **inline,
            "id": None,
            "location_id": None,
            "name": str(payload.get("name") or vocab.LOCATION_LABELS.get(kind, kind)),
            "url": str(payload.get("url") or "").strip() or None,
        }

    def _connection_for_provider(
        self, kind: str, target: dict[str, Any] | None = None
    ) -> str | None:
        """A connection that is ready to provision on ``kind``'s provider, if any.

        Returns ``None`` rather than guessing when there is more than one
        candidate: a rep with two Zoom hosts has not said which one this meeting
        should use, and a swap that silently picks the newest would put one
        rep's meeting on another rep's calendar. The researched flow puts that
        choice on the Meeting Type, so this only ever returns a single match.

        When there is more than one, the ids are recorded on ``target`` so the
        refusal can name them. It has to: the alternative message is "no
        connection is configured", which is advice for a screen where the admin
        would find nothing to do.
        """
        provider = locations.provider_for(kind)
        if not provider:
            return None
        ready = [
            row
            for row in self.provider_connections(provider=provider)
            if (row.get("readiness") or {}).get("ready")
        ]
        if len(ready) != 1:
            if target is not None:
                target["provider_candidates"] = [str(row["id"]) for row in ready]
            return None
        return str(ready[0]["id"])

    def history(self, room_id: str, booking_uid: str) -> list[dict[str, Any]]:
        """The researched ``previousLocation`` trail for one booking.

        One entry per swap, oldest first, because the researched webhook payload
        "mirrors the standard booking payload, with one addition:
        ``previousLocation`` holds the location before the change". Reading a
        trail newest-first makes the previous value read as the current one.
        """
        record = self._require_booking(room_id, booking_uid)
        return [dict(entry) for entry in (record.get("location_history") or [])]

    def report_provider_status(
        self,
        room_id: str,
        booking_uid: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Record a provider's own failure report and apply the researched retry rule.

        "on provider failure Cal reports ``appsStatus[]`` per app ... which is
        what an integration should watch to retry or fall back". The report is
        normalised to the four researched fields, judged against the retry
        budget, and stored beside the booking so the meetings list can show why
        a link is missing.

        This is a *record*, not an exception. The booking happened, the prospect
        holds a slot, and the only useful thing to say about it is that the link
        is missing and which app failed.
        """
        record = self._require_booking(room_id, booking_uid)
        statuses = provider_status.normalise(payload)
        if not statuses:
            raise ConferenceLinkError(
                "an appsStatus report must name at least one app; "
                f"each entry needs {', '.join(provider_status.STATUS_FIELDS)}"
            )

        previous_attempts = int(record.get("provision_attempts") or 0)
        judged = provider_status.judge(statuses, attempts_made=previous_attempts)
        entry = provider_status.report(statuses, attempts_made=previous_attempts)
        static = self._static_fallback_for(record)

        history = list(record.get("provider_status_history") or [])
        history.append({"at": self.stamp(), **entry})
        patch: dict[str, Any] = {
            "provider_status_history": history,
            "appsStatus": statuses,
            "provision_state": judged["state"],
            "provision_attempts": judged["attempts_made"],
        }
        # The Location is marked failed only when the guest has nowhere to go.
        # A report naming two apps where one succeeded leaves a working link in
        # place, and calling that "no link yet" would be a lie a rep acts on -
        # they would stop sending the invite. So the report is always recorded
        # and the retry rule always applies, and the *location* only degrades
        # when there is no conference to degrade.
        if judged["state"] in ("failed", "retrying") and not record.get("conference_id"):
            patch["location_state"] = "provision-failed"
            if judged["state"] == "failed":
                patch["fallback"] = provider_status.fallback_for(statuses, fallback_location=static)
            else:
                patch["retryable"] = True
        elif judged["state"] in ("failed", "retrying"):
            patch["fallback"] = provider_status.fallback_for(statuses, fallback_location=static)
            patch["retryable"] = judged["retryable"]

        updated = self.store.update(record["id"], patch, actor=actor, source=source)
        result = self.present_booking(updated)
        result["provider_status"] = entry
        return result

    def _static_fallback_for(self, record: Mapping[str, Any]) -> str | None:
        """A configured static location to fall back to, if the rep has one.

        Read from the booking's own Meeting Type, so the fallback is one the rep
        declared rather than a global default that might belong to a different
        Meeting Type entirely.
        """
        location_id = str(record.get("location_id") or "")
        if not location_id:
            return None
        for row in self.meeting_locations():
            if row.get("id") == location_id and str(row.get("conference_details") or "").strip():
                return str(row["conference_details"]).strip()
        return None

    def provider_status_history(self, room_id: str, booking_uid: str) -> list[dict[str, Any]]:
        """Every failure report recorded for one booking, oldest first."""
        record = self._require_booking(room_id, booking_uid)
        return [dict(entry) for entry in (record.get("provider_status_history") or [])]

    # -- the room's summary -------------------------------------------------- #

    def summary(self, room_id: str) -> dict[str, Any]:
        """Counts for the room, and the automation note beside them.

        Counted over this room's bookings rather than the whole collection, so a
        room's header says what happened in that room. The three numbers worth
        reading first are ``one_time_linked``, ``missing_links`` and
        ``locations_without_a_connection`` - together they say how much of this
        workflow is actually running.
        """
        rows = self.bookings(room_id, limit=1000)
        by_state: dict[str, int] = {}
        for row in rows:
            state = str(row.get("state") or "unprovisioned")
            by_state[state] = by_state.get(state, 0) + 1

        configured = self.meeting_locations()
        stranded: list[dict[str, Any]] = []
        for row in configured:
            if not locations.needs_conference(str(row.get("kind"))):
                continue
            report = connections.readiness(self._resolve_connection(row))
            if not report.get("ready"):
                stranded.append(
                    {
                        "location_id": row.get("id"),
                        "name": row.get("name"),
                        "kind": row.get("kind"),
                        "missing": report.get("missing"),
                    }
                )

        one_time = [row for row in rows if row.get("provisions_conferences")]
        missing = [
            row
            for row in rows
            if row.get("location_kind") in vocab.ONE_TIME_KINDS and not row.get("conference_id")
        ]
        # A provider failure is counted apart from a missing link, because the
        # two mean different things to a seller: one is a meeting whose guests
        # cannot get in, the other is a meeting whose link is fine while some
        # app in the provisioning chain is not happy.
        unhealthy = [row for row in rows if row.get("provision_state") in ("failed", "retrying")]
        retryable = [row for row in unhealthy if row.get("retryable") is True]
        connected = {
            str(row.get("provider"))
            for row in self.provider_connections()
            if (row.get("readiness") or {}).get("ready")
        }

        return {
            "room_id": room_id,
            "count": len(rows),
            "by_state": dict(sorted(by_state.items())),
            "one_time_linked": len(one_time),
            "missing_links": len(missing),
            "provider_failures": len(unhealthy),
            "still_retryable": len(retryable),
            "awaiting_guest": by_state.get("awaiting-guest", 0),
            "static": by_state.get("static", 0),
            "in_person": by_state.get("in-person", 0),
            "provision_failed": by_state.get("provision-failed", 0),
            "locations": len(configured),
            "locations_without_a_connection": len(stranded),
            "stranded_locations": stranded,
            "providers_connected": sorted(connected),
            "providers_required": list(vocab.PICKER_PROVIDERS),
            "automation_note": (
                "Automatic on booking. A Location that mints a one-time link provisions when the "
                "booking is taken; the researched 'Ask the Guest' option waits for the guest, and "
                "a provider failure is recorded and retried rather than raised."
            ),
        }


def booking_state(data: Mapping[str, Any]) -> str:
    """Where a booking's Location stands, derived from its fields.

    Derived rather than stored so it cannot drift from the fields it summarises.
    The order matters and is the order a reader cares in: a link that exists
    wins over a state that says it did not, and a booking whose Location can
    never mint a link is never reported as failing.
    """
    if data.get("conference_id"):
        return "swapped" if data.get("previous_location") else "provisioned"
    if str(data.get("provision_state") or "") in ("retrying", "failed"):
        return "provision-failed"
    state = str(data.get("location_state") or "").strip()
    if state:
        return state
    return "unprovisioned"


__all__ = [
    "BOOKINGS",
    "CONNECTIONS",
    "DEFAULT_LOCATION_KIND",
    "MEETING_LOCATIONS",
    "ProvisioningEngine",
    "booking_state",
]
