"""WF-056: book a meeting with no scheduling UI.

The researched flow, in the order the research states it:

1. An admin generates a scoped token in ``Command Center > Credentials``,
   choosing ``Schedule`` for the section and ``Read`` where listing is needed.
2. A backend, a custom frontend or an AI assistant makes call #1 - *discover or
   route* - which returns a ``routeId`` and a list of ``startTimes``.
3. It picks a ``startTime`` and makes call #2 - *book* - passing ``routeId`` and
   ``startTime``, and the response carries a ``meetingId``.
4. Calendar invites go out immediately.
5. If the session expired or the slot was taken, it re-runs step 1 with a fresh
   session, because sessions are single-use and short-lived.

This module is that flow over the audited store, and it is where the two calls
meet: :meth:`HeadlessBooking.discover` and :meth:`HeadlessBooking.book`. The
session rules it enforces are in :mod:`dsr.headless_booking.sessions`; the
vocabulary it enforces is in :mod:`dsr.headless_booking.vocabulary`.

Three design decisions a reviewer should not have to reverse-engineer
-----------------------------------------------------------------------

**``source`` is a required keyword on every write.** The audit row has to name
the route that actually served the write, and a domain function that hardcoded a
URL string is a defect this codebase has shipped before. So ``source`` has no
default on any method that writes, and a test asserts every recorded source
names a route the host actually mounted.

**A booking is one transaction.** The research says a commit produces a meeting
record, calendar invites, an optional CRM writeback and a webhook push, and that
invites go out *immediately*. Writing those as four independent transactions
would let a crash leave a meeting with no invite and no webhook, which is the
half-state the data flow does not describe. So they are written through one
``db.transaction()``: all of them or none, and the session's move to ``booked``
commits with them.

**The session is updated last, and it is what gates the booking.** The
availability recheck happens first, inside the same transaction, so a slot taken
between the two calls is caught before anything is written. A refusal writes the
call log and moves the session to a terminal state, and nothing else.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from dsr.db.audited import new_id
from dsr.headless_booking import (
    assets as assets_mod,
    credentials as creds,
    sessions as sessions_mod,
)
from dsr.headless_booking.availability import Busy, parse_calendar_block, parse_interval
from dsr.headless_booking.errors import (
    HeadlessBookingError,
    NotFound,
    PermissionDenied,
    refusal,
)
from dsr.headless_booking.vocabulary import (
    OWNERSHIP_LINK_TYPE,
    SESSION_STATES,
    WEBHOOK_EVENT,
    WEBHOOK_NAME,
    require_section,
)
from dsr.store import RecordStore

#: The collections this feature owns. Named here rather than inlined so a
#: reviewer can see the whole footprint in one place, and so a team can tell
#: which rows are this workflow's.
ASSET_COLLECTION = "dsr_headless_asset"
SESSION_COLLECTION = "dsr_headless_session"
MEETING_COLLECTION = "dsr_headless_meeting"
INVITE_COLLECTION = "dsr_headless_invite"
WEBHOOK_COLLECTION = "dsr_headless_webhook"
CALL_COLLECTION = "dsr_headless_call"
CREDENTIAL_COLLECTION = "dsr_headless_credential"
CALENDAR_COLLECTION = "dsr_headless_calendar_block"

COLLECTIONS = (
    ASSET_COLLECTION,
    SESSION_COLLECTION,
    MEETING_COLLECTION,
    INVITE_COLLECTION,
    WEBHOOK_COLLECTION,
    CALL_COLLECTION,
    CREDENTIAL_COLLECTION,
    CALENDAR_COLLECTION,
)

#: Recipients a booking always invites, in addition to the host.
GUEST_ROLE = "guest"
HOST_ROLE = "host"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class HeadlessBooking:
    """The two researched calls, over the audited store.

    The engine holds nothing but the store handle and a clock, so it is built
    per request by the feature module and a test can hand it a fixed clock. That
    also leaves the clock overridable, which the TTL rules need: a test for
    session expiry that depended on real time would be a test that either
    sleeps or flakes.
    """

    def __init__(self, store: RecordStore, clock: Any = None) -> None:
        self.store = store
        self._clock = clock or _utcnow

    def now(self) -> datetime:
        moment = self._clock()
        if isinstance(moment, datetime) and moment.tzinfo is None:
            raise HeadlessBookingError(
                "the clock must return an aware datetime; a naive one has no instant"
            )
        return moment

    # ------------------------------------------------------------------ #
    # Authorisation
    # ------------------------------------------------------------------ #

    def authorise(
        self,
        payload: Mapping[str, Any] | None,
        *,
        section: str,
        permission: str,
    ) -> tuple[str | None, str, dict[str, Any] | None]:
        """Resolve who is making this call, and whether they may.

        Returns ``(credential_id, authorised_as, spec)``. Three outcomes, all
        stated rather than implied:

        * a token that checks out - ``authorised_as`` is ``"token"``;
        * a credential id that checks out - ``"credential_id"``;
        * neither - ``"installation"``, at full scope.

        The third is a judgement call and it is named
        ``token-lookup-by-id-or-by-secret`` in :mod:`dsr.headless_booking.inferences`.
        It exists so the in-product console can exercise the permission rules; a
        deployment that wants always-on scoping refuses the absent case, which is
        one branch.
        """
        body = dict(payload or {})
        presented = body.get("token")
        credential_id = body.get("credential_id")

        if presented not in (None, ""):
            wanted = require_section(section)
            for record in self.store.find(CREDENTIAL_COLLECTION, {"enabled": True}, limit=1000):
                if creds.digest(str(presented)) == record["data"].get("token_hash"):
                    if credential_id not in (None, "") and record["id"] != str(credential_id):
                        # Both were sent and they name different credentials. A
                        # caller that does this has a bug, and quietly honouring
                        # one of them - whichever the code happens to check
                        # first - is how a permission check gets bypassed. So the
                        # disagreement is refused rather than resolved.
                        raise PermissionDenied(
                            "the token and the credential_id name different credentials, so neither "
                            "can be trusted to be the intended one; send one or the other",
                            required="token",
                            section=section,
                        )
                    creds.require_scope(record["data"], wanted, permission)
                    return record["id"], "token", dict(record["data"])
            # A token was presented and it matched nothing. Falling through to
            # the "no credential" case would authorise the call as the
            # installation, which turns a bad credential into full access - the
            # exact failure a scoped token exists to prevent. A revoked token
            # and a wrong one say the same thing on purpose: confirming that a
            # token was once valid is information a scanner wants.
            raise PermissionDenied(
                "that token is not valid for any active credential, or the credential has been "
                "revoked",
                required="token",
                section=section,
            )

        if credential_id not in (None, ""):
            record = self.store.get(str(credential_id))
            if record is None or record["collection"] != CREDENTIAL_COLLECTION:
                raise NotFound(
                    f"credential {credential_id} not found",
                    resource="credential",
                    record_id=str(credential_id),
                )
            creds.require_scope(record["data"], section, permission)
            return record["id"], "credential_id", dict(record["data"])

        if bool(body.get("require_credential")):
            raise PermissionDenied(
                "this endpoint was called with require_credential, so a token or a credential_id "
                "is required; the installation cannot act on its behalf",
                required="token",
                section=section,
            )
        return None, "installation", None

    # ------------------------------------------------------------------ #
    # Credentials: user-flow step 1
    # ------------------------------------------------------------------ #

    def create_credential(
        self,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """``Generate Token``: validate, mint, store a digest, reveal once.

        The role check runs *before* validation, because the researched refusal
        ("Workspace Managers do not have access to the credentials page") is
        about the caller and is true whatever they were going to send.

        The plaintext token is returned here and never again. It is not in the
        stored record, it is not in the audit row's ``after_state``, and it
        cannot be recovered from anything this product holds - see the
        ``stored-token-is-a-digest`` inference.
        """
        body = dict(payload or {})
        creds.require_generator_role(body.get("role") or actor)
        spec = creds.normalise(body)
        token = creds.generate_token()
        spec.update(creds.mask(token))
        spec["token_hash"] = creds.digest(token)
        spec["shown_once_at"] = self.now().isoformat().replace("+00:00", "Z")
        record = self.store.create(
            CREDENTIAL_COLLECTION,
            spec,
            room_id=body.get("room_id"),
            actor=actor,
            source=source,
        )
        served = creds.public(record)
        # The one and only appearance of the secret, on the response rather than
        # on the row.
        served["token"] = token
        served["shown_once"] = True
        served["notice"] = (
            "This token is not stored and cannot be shown again. Store it now; if it is lost, "
            "revoke this credential and generate another."
        )
        return served

    def list_credentials(self, room_id: str | None = None) -> list[dict[str, Any]]:
        rows = self.store.list(CREDENTIAL_COLLECTION, room_id=room_id, limit=1000)
        return [creds.public(row) for row in rows]

    def get_credential(self, credential_id: str) -> dict[str, Any]:
        record = self.store.get(credential_id)
        if record is None or record["collection"] != CREDENTIAL_COLLECTION:
            raise NotFound(
                f"credential {credential_id} not found",
                resource="credential",
                record_id=credential_id,
            )
        return creds.public(record)

    def revoke_credential(
        self, credential_id: str, *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Revoke by soft delete. A revoked token stops verifying immediately."""
        record = self.store.get(credential_id)
        if record is None or record["collection"] != CREDENTIAL_COLLECTION:
            raise NotFound(
                f"credential {credential_id} not found",
                resource="credential",
                record_id=credential_id,
            )
        self.store.delete(credential_id, actor=actor, source=source)
        return {"id": credential_id, "revoked": True}

    # ------------------------------------------------------------------ #
    # Assets and calendar blocks
    # ------------------------------------------------------------------ #

    def create_asset(
        self,
        room_id: str | None,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        spec = assets_mod.normalise(payload)
        # The room is not taken from the body: it is the routing key for the
        # store, and a payload field that quietly differs from it is a class of
        # bug this codebase has already had.
        spec.pop("room_id", None)
        return self.store.create(
            ASSET_COLLECTION, spec, room_id=room_id, actor=actor, source=source
        )

    def list_assets(
        self,
        room_id: str | None = None,
        section: str | None = None,
        link_type: str | None = None,
        enabled: bool | None = None,
    ) -> list[dict[str, Any]]:
        rows = self.store.list(ASSET_COLLECTION, room_id=room_id, limit=1000)
        if section is not None:
            wanted = require_section(section)
            rows = [row for row in rows if row["data"].get("section") == wanted]
        if link_type is not None:
            rows = [row for row in rows if row["data"].get("link_type") == link_type]
        if enabled is not None:
            rows = [row for row in rows if bool(row["data"].get("enabled", True)) is enabled]
        return rows

    def get_asset(self, asset_id: str, *, section: str | None = None) -> dict[str, Any]:
        record = self.store.get(asset_id)
        if record is None or record["collection"] != ASSET_COLLECTION:
            raise NotFound(f"asset {asset_id} not found", resource="asset", record_id=asset_id)
        if section is not None and record["data"].get("section") != require_section(section):
            raise HeadlessBookingError(
                f"asset {asset_id} is a {record['data'].get('section')!r} asset, not "
                f"{require_section(section)!r}"
            )
        return record

    def update_asset(
        self,
        asset_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Patch an asset, re-validated against the merged result.

        Re-validated because a patch can otherwise leave an asset that no longer
        satisfies a rule: a host with no calendar address, working hours that run
        backwards, a grid that is not a positive number of minutes. A link
        switched to ``ownership`` is *not* refused - the guestEmail requirement
        belongs to the call, not to the asset, because the guest arrives with the
        lead rather than being fixed configuration.
        """
        record = self.get_asset(asset_id)
        merged = {**record["data"], **dict(payload or {})}
        spec = assets_mod.normalise(merged)
        return self.store.update(asset_id, spec, actor=actor, source=source)

    def delete_asset(
        self, asset_id: str, *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        self.get_asset(asset_id)
        return self.store.delete(asset_id, actor=actor, source=source)

    def add_calendar_block(
        self,
        room_id: str | None,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """File one busy block against a host's calendar.

        ``host_email`` is the key, and it is a *host* key rather than an asset
        key on purpose: the same person's Google or Outlook calendar is the same
        whether a meeting is booked through a Concierge router, a scheduling link
        or a handoff path, and a per-asset key would let this product double-book
        them across surfaces.
        """
        body = dict(payload or {})
        host = str(body.get("host_email") or "").strip().lower()
        if not host:
            raise HeadlessBookingError(
                "host_email is required: a calendar block belongs to a host's calendar, and the "
                "availability engine reads blocks by host"
            )
        block = parse_calendar_block(body)
        return self.store.create(
            CALENDAR_COLLECTION,
            {
                "host_email": host,
                "path_id": str(body.get("path_id") or "").strip() or None,
                "starts_at": block.starts_at.isoformat().replace("+00:00", "Z"),
                "ends_at": block.ends_at.isoformat().replace("+00:00", "Z"),
                "label": block.label,
                "source_system": str(body.get("source_system") or "manual"),
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )

    def list_calendar_blocks(
        self, room_id: str | None = None, host_email: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        rows = self.store.list(CALENDAR_COLLECTION, room_id=room_id, limit=min(limit, 1000))
        if host_email:
            wanted = str(host_email).strip().lower()
            rows = [row for row in rows if row["data"].get("host_email") == wanted]
        return rows

    def busy_by_key(self) -> dict[str, list[Busy]]:
        """Every calendar block, indexed by the key the slot engine asks for.

        The key is ``host_email`` or ``host_email|path_id`` - the same
        :func:`~dsr.headless_booking.assets.host_calendar_key` the asset side
        builds. One read of the whole collection rather than a query per asset,
        because a session may consult several paths and the block set is small
        and shared.
        """
        blocks: dict[str, list[Busy]] = {}
        for row in self.store.list(CALENDAR_COLLECTION, limit=1000):
            data = row["data"]
            host = str(data.get("host_email") or "").strip().lower()
            if not host:
                continue
            key = f"{host}|{data['path_id']}" if data.get("path_id") else host
            window = _window(data, start_key="starts_at", end_key="ends_at")
            if window is None:
                # A block this layer wrote badly is skipped rather than
                # defaulted: a fabricated all-day block would silently empty
                # the host's availability, and an empty slot list is far harder
                # to diagnose than a missing row.
                continue
            blocks.setdefault(key, []).append(
                Busy(starts_at=window[0], ends_at=window[1], label=str(data.get("label") or ""))
            )
        return blocks

    # ------------------------------------------------------------------ #
    # Call #1: discover or route
    # ------------------------------------------------------------------ #

    def discover(
        self,
        room_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Call #1. Open a single-use session on a slot list.

        Reads nothing outside the store and writes three things: the session, the
        calendar blocks are only read, and one call-log row. The booking happens
        in a different call, which is the researched point of the whole
        workflow.
        """
        body = dict(payload or {})
        section = require_section(body.get("section"))
        now = self.now()

        credential_id, authorised_as, _spec = self.authorise(
            body, section=section, permission="read"
        )

        asset_id = str(body.get("asset_id") or "").strip()
        if not asset_id:
            raise refusal(
                "asset_unknown",
                "asset_id is required: call #1 addresses a bookable asset, and the research names "
                "three of them",
            )
        asset = self.get_asset(asset_id, section=section)
        spec = dict(asset["data"])
        if not bool(spec.get("enabled", True)):
            raise refusal(
                "asset_disabled",
                f"asset {asset_id} is disabled, so it yields no availability. A bookable asset that "
                "is switched off must refuse rather than quietly offer slots nothing will honour.",
            )

        window_start, window = parse_interval(body.get("interval"))
        if window_start < now:
            raise refusal(
                "interval_starts_at_in_past",
                f"interval.startsAt {window_start.isoformat()} is in the past; a session over a "
                "window that has already passed can only return slots that have already passed",
            )

        guest = dict(body.get("guest") or {})
        guest_email = str(guest.get("guestEmail") or body.get("guest_email") or "").strip().lower()
        if spec.get("link_type") == OWNERSHIP_LINK_TYPE and not guest_email:
            # The researched requirement, enforced at the call that needs it.
            raise refusal(
                "guest_email_required",
                "this is an Ownership scheduling link, and guestEmail is required in the init call "
                "so the owner can be resolved from the CRM",
            )

        if section == "handoff" and not (body.get("booker_id") or spec.get("booker_id")):
            raise refusal(
                "booker_required",
                "a handoff init is addressed to a booker: the researched path is "
                "/handoff/workspace/{workspaceId}/booker/{userId}/init-simple",
            )

        paths_spec: list[Mapping[str, Any]] = list(spec.get("paths") or [])
        session = sessions_mod.open_session(
            route_id=new_id("route"),
            section=section,
            asset={**spec, "id": asset_id},
            room_id=room_id,
            window_start=window_start,
            window=window,
            paths_spec=paths_spec,
            busy_by_key=self.busy_by_key(),
            booked_by_key=self.booked_by_key(),
            now=now,
            timeout_in_ms=body.get("timeout_in_ms"),
            guest_email=guest_email,
            credential_id=credential_id,
            authorised_as=authorised_as,
            max_slots=body.get("max_slots"),
        )

        if not session.all_start_times:
            self.record_call(
                room_id=room_id,
                tool=_tool(section, "discover_or_route"),
                section=section,
                outcome="no_availability",
                detail="the host has no free slot inside the interval",
                actor=actor,
                source=source,
                credential_id=credential_id,
                authorised_as=authorised_as,
            )
            raise refusal(
                "no_availability",
                f"no availability for {asset_id} between {session.interval_starts_at} and "
                f"{sessions_mod.format_slot(window_start + window)}. Widen the interval, or check "
                "the host's calendar blocks.",
            )

        # The stored record's own id *is* the routeId. Call #2 addresses the
        # session by that identifier, so minting two ids and keeping them in sync
        # would leave a window in which a caller holds a routeId that resolves to
        # nothing - which reads as "unknown session" and sends them looking for a
        # bug that is not there.
        record = self.store.create(
            SESSION_COLLECTION,
            session.to_dict(),
            record_id=session.route_id,
            room_id=room_id,
            actor=actor,
            source=source,
        )
        payload_out = session.to_payload(record["id"], room_id)
        payload_out["asset_name"] = spec.get("name")
        payload_out["host_email"] = spec.get("host_email")
        payload_out["call"] = "discover_or_route"
        payload_out["instructions"] = instructions(section, asset_id, spec, payload_out)
        self.record_call(
            room_id=room_id,
            tool=_tool(section, "discover_or_route"),
            section=section,
            outcome="opened",
            detail=f"{session.slot_count} slot(s) across {len(session.paths)} path(s)",
            actor=actor,
            source=source,
            credential_id=credential_id,
            authorised_as=authorised_as,
            route_id=session.route_id,
        )
        return payload_out

    # ------------------------------------------------------------------ #
    # Call #2: book
    # ------------------------------------------------------------------ #

    def book(
        self,
        room_id: str,
        route_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Call #2. Commit one meeting, and consume the session doing it.

        The order of operations is the researched behaviour and it matters:

        1. load the session, and refuse a spent or stale one *before* anything
           is written;
        2. check the credential's ``schedule`` permission for the section;
        3. resolve ``startTime`` against the slots **this session** returned;
        4. re-check availability, because the research says the slot may have
           been taken since call #1;
        5. write the meeting, the invites, the webhook event, the CRM
           writeback record and the session's terminal state in one transaction.

        A refusal at any step writes the call log and, for a failure that reached
        the scheduler, moves the session to a terminal state. It never writes a
        meeting.
        """
        body = dict(payload or {})
        now = self.now()

        record = self.store.get(route_id)
        if record is None or record["collection"] != SESSION_COLLECTION:
            raise NotFound(
                f"session {route_id} not found. Sessions are created by the discover or route call "
                "and are single-use, so an unknown id is a spent or invented one.",
                resource="session",
                record_id=route_id,
                room_id=room_id,
            )
        session = sessions_mod.Session.from_record(record)
        section = require_section(session.section)
        if room_id and record.get("room_id") and record["room_id"] != room_id:
            raise NotFound(
                f"session {route_id} belongs to room {record['room_id']}, not {room_id}",
                resource="session",
                record_id=route_id,
                room_id=room_id,
            )

        # Step 1, before any write: a spent or stale session is not bookable.
        #
        # An expired session is *persisted* as expired here rather than only
        # refused. `get_session` projects the expiry for a reader, but a
        # projection is not a record: without this write, a session that
        # expired would still read as `open` forever, and the session list would
        # show it as bookable when it is not.
        if session.state in sessions_mod.TERMINAL_SESSION_STATES:
            # The call log records `session_consumed` for *any* second attempt on
            # a spent routeId, whatever ended it. Recording the session's own
            # outcome here instead - "booked" for a second call on a booked one -
            # would make the researched retry invisible in the very log that
            # exists to make it visible. The ending reason travels in the detail.
            ended_by = session.outcome or session.state
            self.record_call(
                room_id=room_id,
                tool=_tool(section, "book"),
                section=section,
                outcome="session_consumed",
                detail=(
                    f"the session was already {session.state}"
                    f"{f' ({ended_by})' if ended_by != session.state else ''}. "
                    "Sessions are single-use; this call should have been a fresh discover."
                ),
                actor=actor,
                source=source,
                credential_id=session.credential_id,
                authorised_as=session.authorised_as,
                route_id=route_id,
                start_time=body.get("startTime"),
            )
            raise refusal(
                "session_consumed",
                f"session {route_id} is {session.state}"
                + (f" ({ended_by})" if ended_by != session.state else "")
                + f". {session.detail or 'Sessions are single-use.'} "
                f"Next step: {session.next_step()}",
            )
        if session.is_expired(now):
            # `mark_expired` rather than `consume`: consume raises, and the write
            # that records the expiry has to happen *before* the refusal unwinds
            # past it.
            session.mark_expired(now)
            self.store.update(
                route_id,
                {"state": session.state, "outcome": session.outcome, "detail": session.detail},
                actor=actor,
                source=source,
            )
            self.record_call(
                room_id=room_id,
                tool=_tool(section, "book"),
                section=section,
                outcome="session_expired",
                detail=session.detail,
                actor=actor,
                source=source,
                credential_id=session.credential_id,
                authorised_as=session.authorised_as,
                route_id=route_id,
                start_time=body.get("startTime"),
            )
            raise refusal(
                "session_expired",
                f"session {route_id} expired at {sessions_mod.format_slot(session.expires_at)}. "
                f"Next step: {session.next_step()}",
            )

        # Step 2: the permission check, before the scheduler is reached, so a
        # caller without Schedule has not consumed anything.
        self.authorise(body, section=section, permission="schedule")

        # Steps 3 to 5, inside one transaction: a failure past this point
        # consumes the session, which the researched rule requires, and it must
        # commit atomically with whatever the attempt managed to write.
        try:
            return self._commit(
                room_id=room_id,
                session=session,
                payload=body,
                now=now,
                actor=actor,
                source=source,
            )
        except (HeadlessBookingError, PermissionDenied, NotFound) as exc:
            reason = _reason_for(exc)
            try:
                # A failure past this point spends the session. The state is the
                # published three-value one and the reason is the researched
                # code, kept apart so a summary counting "failed" is counting
                # failures rather than one particular failure.
                session.consume(now, "failed", str(exc), reason)
            except HeadlessBookingError:
                # Already terminal, which cannot happen on this path and is
                # swallowed deliberately rather than masking the real failure.
                pass
            self.store.update(
                route_id,
                {"state": session.state, "outcome": session.outcome, "detail": session.detail},
                actor=actor,
                source=source,
            )
            self.record_call(
                room_id=room_id,
                tool=_tool(section, "book"),
                section=section,
                outcome=reason,
                detail=str(exc),
                actor=actor,
                source=source,
                credential_id=session.credential_id,
                authorised_as=session.authorised_as,
                route_id=route_id,
                start_time=body.get("startTime"),
            )
            raise

    def _commit(
        self,
        *,
        room_id: str,
        session: sessions_mod.Session,
        payload: Mapping[str, Any],
        now: datetime,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """The transaction. Everything below either all lands or none does."""
        start_time = payload.get("startTime")
        path_id = payload.get("pathId") or payload.get("path_id")
        canonical, instant, path, wire = session.resolve_slot(start_time, path_id)

        asset = self.get_asset(session.asset_id, section=session.section)
        spec = dict(asset["data"])
        path_spec = next(
            (entry for entry in spec.get("paths") or [] if entry.get("path_id") == path.path_id),
            None,
        )
        config = {**spec, **(path_spec or {})}
        meeting_minutes = int(config.get("duration_minutes") or 30)
        window = (instant, instant + timedelta(minutes=meeting_minutes))

        # Step 4: the availability recheck, against the *current* state. The
        # researched flow calls this out by name: the caller re-runs step 1 if
        # the slot was taken, so this product has to notice.
        conflicts = self._conflicts(
            key=assets_mod.host_calendar_key(session.section, spec, path_spec),
            starts_at=window[0],
            ends_at=window[1],
            exclude_session=session.route_id,
        )
        if conflicts["busy"] or conflicts["booked"]:
            raise refusal(
                "slot_taken",
                f"slot {canonical} is no longer available: "
                + ", ".join(
                    part
                    for part in (
                        "the host's calendar has a block over it" if conflicts["busy"] else "",
                        "a meeting is already booked for the host" if conflicts["booked"] else "",
                    )
                    if part
                )
                + ". The researched remedy is a fresh session: re-run the discover or route call.",
            )
        guest = dict(payload.get("guest") or {})
        guest_email = (
            str(guest.get("guestEmail") or payload.get("guest_email") or session.guest_email or "")
            .strip()
            .lower()
        )
        if not guest_email:
            # A booking with no guest has nobody to invite, and "Calendar invites
            # are sent immediately" is the documented consequence of committing.
            raise refusal(
                "guest_email_required",
                "a booking needs a guest email: calendar invites are sent immediately, and there "
                "is nobody to send them to",
            )
        guest_name = str(guest.get("name") or guest.get("fullName") or "").strip()
        host_email = str(config.get("host_email") or "").strip().lower()
        host_name = str(config.get("host_name") or "").strip()
        provider = str(config.get("provider") or "zoom")
        meeting_id = new_id("mtg")

        # The writeback is a *record* of what would be written, not a call. See
        # the `crm-writeback-is-recorded` inference.
        crm_writeback: dict[str, Any] = {"enabled": False, "written": False}
        raw_writeback = payload.get("crm_writeback") or config.get("crm_writeback")
        if raw_writeback:
            crm_writeback = {
                "enabled": True,
                "written": True,
                "mode": "recorded",
                "object": str((raw_writeback or {}).get("object") or "Event"),
                "fields": sorted(
                    (raw_writeback or {}).get("fields") or ["startTime", "guestEmail"]
                ),
            }

        with self.store.db.transaction(actor=actor or "api", source=source) as tx:
            # The stored record's own id *is* the meetingId, for the same reason
            # the session's is its routeId: call #2's response names an
            # identifier the caller will read straight back off
            # `GET /meetings/{meeting_id}`, and two ids kept in sync would leave
            # a window in which that read 404s on a meeting that exists.
            meeting = tx.create(
                MEETING_COLLECTION,
                {
                    "meetingId": meeting_id,
                    "routeId": session.route_id,
                    "routingId": session.route_id,
                    "pathId": path.path_id,
                    "section": session.section,
                    "asset_id": session.asset_id,
                    "asset_name": spec.get("name"),
                    "room_id": room_id,
                    "startTime": canonical,
                    "start_time_verbatim": wire,
                    "start_time_was_verbatim": wire == canonical,
                    "endsAt": (window[1]).isoformat().replace("+00:00", "Z"),
                    "duration_minutes": meeting_minutes,
                    "guest_email": guest_email,
                    "guest_name": guest_name,
                    "host_email": host_email,
                    "host_name": host_name,
                    "booker": str(payload.get("booker") or spec.get("booker_id") or "").strip(),
                    "provider": provider,
                    "meeting_link": assets_mod.meeting_link(provider, meeting_id),
                    "meeting_link_is_derived": True,
                    "invites_sent": False,
                    "invite_count": 0,
                    "crm_writeback": crm_writeback,
                    "credential_id": session.credential_id,
                    "authorised_as": session.authorised_as,
                    "interval_starts_at": session.interval_starts_at,
                    "booked_at": now.isoformat().replace("+00:00", "Z"),
                },
                record_id=meeting_id,
                room_id=room_id,
            )

            # The researched consequence of the commit, written in the same
            # transaction so a meeting can never exist without them. The rows
            # are collected as they are created so the response can return the
            # same records the store now holds, rather than a re-derivation that
            # could disagree with them.
            invite_rows: list[dict[str, Any]] = []
            for role, email, name in (
                (GUEST_ROLE, guest_email, guest_name),
                (HOST_ROLE, host_email, host_name),
            ):
                if not email:
                    continue
                invite_rows.append(
                    tx.create(
                        INVITE_COLLECTION,
                        {
                            "meeting_id": meeting_id,
                            "role": role,
                            "email": email,
                            "name": name,
                            "sent": False,
                            "delivery": "recorded",
                            "sent_at": now.isoformat().replace("+00:00", "Z"),
                            "note": (
                                "The research says calendar invites are sent immediately on "
                                "booking. This product holds no calendar or SMTP credential, so the "
                                "invite is recorded and not transmitted; the record says so rather "
                                "than claiming a send that did not happen."
                            ),
                        },
                        room_id=room_id,
                    )
                )

            webhook = tx.create(
                WEBHOOK_COLLECTION,
                {
                    "event": WEBHOOK_EVENT,
                    "webhook": WEBHOOK_NAME,
                    "meeting_id": meeting_id,
                    "route_id": session.route_id,
                    "room_id": room_id,
                    "endpoint": str(spec.get("webhook_url") or "") or None,
                    "delivered": False,
                    "delivery": "recorded",
                    "emitted_at": now.isoformat().replace("+00:00", "Z"),
                    "payload": {
                        "event": WEBHOOK_EVENT,
                        "meetingId": meeting_id,
                        "startTime": canonical,
                        "guestEmail": guest_email,
                        "hostEmail": host_email,
                    },
                    "note": (
                        "Bookings immediately emit the For New Meeting webhook. The event is "
                        "written in the same transaction as the meeting; it is not delivered, "
                        "because this product opens no outbound connection."
                    ),
                },
                room_id=room_id,
            )

            tx.update(
                session.route_id,
                {
                    "state": "booked",
                    "outcome": "booked",
                    "detail": f"meeting {meeting_id} committed at {canonical}",
                    "meeting_id": meeting_id,
                },
            )

        self.record_call(
            room_id=room_id,
            tool=_tool(session.section, "book"),
            section=session.section,
            outcome="booked",
            detail=f"meeting {meeting_id} at {canonical}",
            actor=actor,
            source=source,
            credential_id=session.credential_id,
            authorised_as=session.authorised_as,
            route_id=session.route_id,
            start_time=wire,
            meeting_id=meeting_id,
        )
        return {
            "meetingId": meeting_id,
            "meeting": meeting,
            "invites": invite_rows,
            "invites_sent": False,
            "invite_note": (
                "Recorded, not transmitted: 'Calendar invites are sent immediately' is implemented "
                "as a record written in the same transaction as the meeting."
            ),
            "webhook": webhook,
            "webhook_event": WEBHOOK_EVENT,
            "routeId": session.route_id,
            "session_state": "booked",
            "next_step": (
                "Do not call this routeId again. Sessions are single-use; the next booking starts "
                "with a fresh discover or route call."
            ),
        }

    # ------------------------------------------------------------------ #
    # Availability, from two sources
    # ------------------------------------------------------------------ #

    def _conflicts(
        self,
        *,
        key: str,
        starts_at: datetime,
        ends_at: datetime,
        exclude_session: str | None = None,
    ) -> dict[str, bool]:
        """Is this window still free? From the calendar *and* from our bookings.

        Both are checked and reported separately, because they mean different
        things to the person reading the refusal: a busy block is the host's own
        calendar, and a booked meeting is something this product did.
        """
        host = key.split("|", 1)[0]
        path_id = key.split("|", 1)[1] if "|" in key else None
        busy = False
        for row in self.store.list(CALENDAR_COLLECTION, limit=1000):
            data = row["data"]
            if str(data.get("host_email") or "").lower() != host:
                continue
            if (data.get("path_id") or None) != (path_id or None):
                continue
            window = _window(data, start_key="starts_at", end_key="ends_at")
            if window is None:
                continue
            if window[0] < ends_at and starts_at < window[1]:
                busy = True
                break
        booked = False
        for row in self.store.find(MEETING_COLLECTION, {"host_email": host}, limit=1000):
            data = row["data"]
            if path_id and data.get("pathId") != path_id:
                continue
            if data.get("routeId") == exclude_session:
                continue
            other = _window(data)
            # A row whose timestamps are unreadable is skipped rather than
            # treated as a conflict: inventing a conflict from a row we cannot
            # parse would refuse bookings over bad data of our own making.
            if other is None:
                continue
            other_start, other_end = other
            if other_start < ends_at and starts_at < other_end:
                booked = True
                break
        return {"busy": busy, "booked": booked}

    def booked_by_key(self) -> dict[str, list[tuple[datetime, datetime]]]:
        """Meetings this product has already committed, indexed the same way."""
        booked: dict[str, list[tuple[datetime, datetime]]] = {}
        for row in self.store.list(MEETING_COLLECTION, limit=1000):
            data = row["data"]
            window = _window(data, start_key="startTime", end_key="endsAt")
            if window is None:
                continue
            start, end = window
            host = str(data.get("host_email") or "").lower()
            if not host:
                continue
            path_id = data.get("pathId")
            key = f"{host}|{path_id}" if path_id else host
            booked.setdefault(key, []).append((start, end))
        return booked

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #

    def list_sessions(
        self,
        room_id: str | None = None,
        section: str | None = None,
        state: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        rows = self.store.list(SESSION_COLLECTION, room_id=room_id, limit=min(max(limit, 1), 1000))
        if section is not None:
            wanted = require_section(section)
            rows = [row for row in rows if row["data"].get("section") == wanted]
        if state is not None:
            rows = [row for row in rows if row["data"].get("state") == state]
        return rows

    def get_session(self, route_id: str) -> dict[str, Any]:
        record = self.store.get(route_id)
        if record is None or record["collection"] != SESSION_COLLECTION:
            raise NotFound(f"session {route_id} not found", resource="session", record_id=route_id)
        data = dict(record["data"])
        # The freshness check is a *read*, so a caller polling a session learns
        # it has expired without a write. Only a book call changes the state.
        session = sessions_mod.Session.from_record(record)
        if session.state == "open" and session.is_expired(self.now()):
            data["state"] = "expired"
            data["state_is_projected"] = True
            data["outcome"] = "session_expired"
        data["next_step"] = (
            "re-run the discover or route call for a fresh session"
            if data.get("state") in ("booked", "failed", "expired")
            else f"call the book endpoint with one of the {session.slot_count} start times, verbatim"
        )
        data["retry_with_same_route_id"] = data.get("state") == "open"
        return {"id": record["id"], "room_id": record.get("room_id"), "data": data}

    def list_meetings(
        self,
        room_id: str | None = None,
        section: str | None = None,
        host_email: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        rows = self.store.list(MEETING_COLLECTION, room_id=room_id, limit=min(max(limit, 1), 1000))
        if section is not None:
            wanted = require_section(section)
            rows = [row for row in rows if row["data"].get("section") == wanted]
        if host_email:
            wanted_host = str(host_email).strip().lower()
            rows = [row for row in rows if row["data"].get("host_email") == wanted_host]
        return rows

    def get_meeting(self, meeting_id: str) -> dict[str, Any]:
        record = self.store.get(meeting_id)
        if record is None or record["collection"] != MEETING_COLLECTION:
            raise NotFound(
                f"meeting {meeting_id} not found", resource="meeting", record_id=meeting_id
            )
        return {
            **record,
            "invites": self.store.find(INVITE_COLLECTION, {"meeting_id": meeting_id}, limit=100),
            "webhook": self.store.find(WEBHOOK_COLLECTION, {"meeting_id": meeting_id}, limit=10),
        }

    def record_call(
        self,
        *,
        room_id: str | None,
        tool: str | None,
        section: str,
        outcome: str,
        detail: str = "",
        actor: str | None = None,
        source: str,
        credential_id: str | None = None,
        authorised_as: str = "installation",
        route_id: str | None = None,
        start_time: Any = None,
        meeting_id: str | None = None,
    ) -> dict[str, Any]:
        """One row per call, refused ones included.

        This is where the researched instruction becomes visible rather than
        merely obeyed: a caller that retries the same ``routeId`` twice leaves
        two rows here, the second saying ``session_consumed``, and a reviewer can
        see that from the log without reading a line of code.
        """
        return self.store.create(
            CALL_COLLECTION,
            {
                "tool": tool,
                "call": "discover_or_route"
                if tool and tool.endswith(("init", "route-by-slug"))
                else ("book" if tool else "discover_or_route"),
                "section": section,
                "outcome": outcome,
                "detail": detail,
                "route_id": route_id,
                "start_time": str(start_time) if start_time not in (None, "") else None,
                "meeting_id": meeting_id,
                "credential_id": credential_id,
                "authorised_as": authorised_as,
                "retry_with_same_route_id": False,
                "at": self.now().isoformat().replace("+00:00", "Z"),
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )

    def calls(
        self,
        room_id: str | None = None,
        outcome: str | None = None,
        route_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        rows = self.store.list(CALL_COLLECTION, room_id=room_id, limit=min(max(limit, 1), 1000))
        if outcome is not None:
            rows = [row for row in rows if row["data"].get("outcome") == outcome]
        if route_id is not None:
            rows = [row for row in rows if row["data"].get("route_id") == route_id]
        return rows

    def summary(self, room_id: str | None = None) -> dict[str, Any]:
        """Counts for the page header, over exactly the rows the filters return."""
        sessions = self.list_sessions(room_id=room_id, limit=1000)
        meetings = self.list_meetings(room_id=room_id, limit=1000)
        calls = self.calls(room_id=room_id, limit=1000)
        by_state: dict[str, int] = {name: 0 for name in SESSION_STATES}
        for row in sessions:
            state = str(row["data"].get("state") or "open")
            by_state[state] = by_state.get(state, 0) + 1
        by_outcome: dict[str, int] = {}
        for row in calls:
            outcome = str(row["data"].get("outcome") or "unknown")
            by_outcome[outcome] = by_outcome.get(outcome, 0) + 1
        by_section: dict[str, int] = {}
        for row in meetings:
            name = str(row["data"].get("section") or "unknown")
            by_section[name] = by_section.get(name, 0) + 1
        return {
            "room_id": room_id,
            "assets": len(self.list_assets(room_id=room_id)),
            "credentials": len(self.list_credentials(room_id=room_id)),
            "calendar_blocks": len(self.list_calendar_blocks(room_id=room_id, limit=1000)),
            "sessions": len(sessions),
            "sessions_by_state": by_state,
            "meetings": len(meetings),
            "meetings_by_section": by_section,
            "calls": len(calls),
            "calls_by_outcome": by_outcome,
            # A caller that retried a spent routeId is the researched mistake
            # this workflow exists to make hard, so it gets its own count rather
            # than being buried in `calls_by_outcome`. Every one of these is a
            # call that should have been a fresh discover.
            "retries_on_a_spent_session": by_outcome.get("session_consumed", 0),
            # The two counters are kept apart on purpose. "Sent" and "delivered"
            # are both zero because this product opens no outbound connection;
            # the records are written and say so. Reporting one number here
            # would invite a reader to believe invites went somewhere.
            "invites_sent": 0,
            "invites_recorded": len(
                self.store.list(INVITE_COLLECTION, room_id=room_id, limit=1000)
            ),
            "webhooks_delivered": 0,
            "webhooks_recorded": len(
                self.store.list(WEBHOOK_COLLECTION, room_id=room_id, limit=1000)
            ),
        }


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _instant(raw: Any) -> datetime:
    """An aware UTC datetime from a stored timestamp, or ``None`` if unusable.

    ``None`` rather than a sentinel, because every caller has to decide what an
    unreadable timestamp means. An earlier version returned the epoch here, and
    that was a real bug: the epoch overlaps nothing, so a corrupt row silently
    stopped being a conflict, and the double-booking this module exists to
    prevent became possible through a row this product wrote badly. Returning
    ``None`` forces the caller to skip the row explicitly and say so.
    """
    from dsr.headless_booking.availability import parse_instant

    if raw in (None, ""):
        return None
    try:
        return parse_instant(raw, field="timestamp")
    except HeadlessBookingError:
        return None


def _window(data: Mapping[str, Any], start_key: str = "startTime", end_key: str = "endsAt"):
    """``(start, end)`` from a stored pair, or ``None`` if either is unreadable."""
    start = _instant(data.get(start_key))
    end = _instant(data.get(end_key))
    if start is None or end is None or end <= start:
        return None
    return start, end


def _tool(section: str, call: str) -> str | None:
    from dsr.headless_booking.vocabulary import tool_for

    return tool_for(section, call)


def _reason_for(exc: Exception) -> str:
    """The researched reason a refusal corresponds to.

    Read off the error rather than searched for in its message. An earlier
    version pattern-matched the prose for words like "expired" and "single-use",
    which meant a reworded message silently changed a recorded outcome - and a
    call log that renames its own failures is not evidence of anything. The
    fallback is a single named value rather than a heuristic, so an un-coded
    refusal is *visible* as un-coded instead of being guessed at.
    """
    return str(getattr(exc, "reason", "") or "book_failed")


def instructions(
    section: str, asset_id: str, spec: Mapping[str, Any], session: Mapping[str, Any]
) -> dict[str, Any]:
    """The researched ``Custom API`` tab, as data.

    The research names the feature: a router's ``Custom API`` tab with a
    ``Copy`` button for the URL and a starter body, and a ``Share Instructions``
    button for developers. So the two calls are served with a copyable URL, a
    body that already has the route filled in, and the scoping note the
    credential needs - which is the actual thing a developer takes away from that
    screen.
    """
    from dsr.headless_booking.vocabulary import (
        INIT_ENDPOINTS,
        MCP_TOOLS,
        SCHEDULE_ENDPOINTS,
        SECTION_PERMISSIONS,
    )

    init_path = INIT_ENDPOINTS[section]
    if section == "concierge":
        init_path = init_path.replace(
            "{routerSlug}", str(spec.get("router_slug") or "{routerSlug}")
        )
    if section == "handoff":
        init_path = init_path.replace(
            "{workspaceId}", str(spec.get("workspace_id") or "{workspaceId}")
        )
        init_path = init_path.replace("{userId}", str(spec.get("booker_id") or "{userId}"))

    schedule_path = SCHEDULE_ENDPOINTS[section]
    if section == "handoff":
        first = next(iter(spec.get("paths") or [{}]), {})
        schedule_path = (
            schedule_path.replace("{routingId}", session["routeId"])
            .replace("{routerId}", asset_id)
            .replace("{pathId}", str(first.get("path_id") or "{pathId}"))
            .replace("{userId}", str(spec.get("booker_id") or "{userId}"))
        )
    else:
        schedule_path = schedule_path.replace("{routeId}", session["routeId"])

    return {
        "transport": "edge",
        "section": section,
        "asset_id": asset_id,
        "mcp_tools": list(MCP_TOOLS[section]),
        "credentials_required": list(SECTION_PERMISSIONS[section]),
        "step_1": {
            "name": "discover or route",
            "method": "POST",
            "path": init_path,
            "body": {
                "section": section,
                "asset_id": asset_id,
                "interval": {
                    "startsAt": session["interval_starts_at"],
                    "duration": session["interval_duration_minutes"],
                },
                "guest": {"guestEmail": "<the guest's email>"},
                **({"timeout_in_ms": 900_000} if section == "concierge" else {}),
            },
        },
        "step_2": {
            "name": "book",
            "method": "POST",
            "path": schedule_path,
            "body": {
                "startTime": "<one of schedulingData[].startTimes, passed back verbatim>",
                **({"pathId": "<a pathId from schedulingData>"} if section == "handoff" else {}),
            },
        },
        "warnings": [
            "Sessions are single-use. Do not retry the schedule call with the same routeId - start "
            "again from the discover or route step.",
            "Slot times are UTC. Pass startTime back verbatim.",
            "Calendar invites are sent immediately on booking, and the booking emits the "
            f"{WEBHOOK_NAME} webhook.",
        ],
    }
