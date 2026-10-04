"""The facade the HTTP layer calls, and the only module here that stores anything.

Every read and write goes through the :class:`~dsr.store.RecordStore` the caller
hands in, so the audit row is written in the same transaction as the change. This
module opens no connection, imports no framework, and never writes a ``source=``
string of its own: the HTTP layer builds every source from the router, so the
audit log cannot name a route the app stopped serving.

The collections are the five in
:data:`~dsr.recording_consent.vocabulary.ALL_COLLECTIONS`, and this package reads
and writes no others. It does not import another feature's module, because a
consent profile and a conferencing link are objects this workflow owns outright.

One record per booking
----------------------

Jev chose this shape over two collections and over an append-only event log
(audit ``jev-20261004T064834-23344-14922``, confidence 0.80). The consequence is
that :meth:`ConsentEngine.open` is the only place a booking becomes visible, and
every later call patches that one record.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from dsr.recording_consent import (
    decisions,
    directory,
    links,
    precall,
    profiles,
    vocabulary as vocab,
)
from dsr.recording_consent.errors import (
    BookingNotFound,
    ConsentPageDisabled,
    LinkSuperseded,
    OrganizerUnmapped,
    ProfileInvalid,
    ProfileNotFound,
)
from dsr.store import RecordStore


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(moment: datetime) -> str:
    return moment.isoformat(timespec="milliseconds")


class ConsentEngine:
    """The flow over the audited store, with no outbound socket.

    The clock is injected rather than read at call time. Every rule in this
    package is a statement about an instant - a pre-call window, a superseded
    link, a recording that started - so the clock has to be the test's, and an
    engine that built its own could only be tested by waiting.
    """

    def __init__(self, store: RecordStore, now: Any = None) -> None:
        self.store = store
        self._now = now or _utcnow

    # -- reads -------------------------------------------------------------- #

    def profiles(self, room_id: str | None = None) -> list[dict[str, Any]]:
        """Every consent profile, newest first."""
        return self.store.list(vocab.PROFILE_COLLECTION, room_id=room_id, limit=200)

    def profile(self, profile_id: str) -> dict[str, Any]:
        """One profile, or refuse.

        Raises rather than returning ``None``, so a caller that forgets to check
        fails at the point of the mistake instead of one line later with an
        ``AttributeError`` that says nothing.
        """
        record = self.store.get(profile_id)
        if record is None or record["collection"] != vocab.PROFILE_COLLECTION:
            raise ProfileNotFound(
                f"No consent profile {profile_id!r}.", {"profile_id": "no such profile"}
            )
        return record

    def users(self, room_id: str | None = None) -> list[dict[str, Any]]:
        """The user directory."""
        return self.store.list(vocab.DIRECTORY_COLLECTION, room_id=room_id, limit=500)

    def recordings(self, room_id: str | None = None) -> list[dict[str, Any]]:
        """One row per booking's consent and recording state."""
        return self.store.list(vocab.CONSENT_RECORDING_COLLECTION, room_id=room_id, limit=200)

    def recording(self, booking_id: str) -> dict[str, Any]:
        """One booking's consent and recording record."""
        found = self.store.find(vocab.CONSENT_RECORDING_COLLECTION, {"booking_id": booking_id})
        if not found:
            raise BookingNotFound(
                f"No consent record for booking {booking_id!r}. Open one first.",
                {"booking_id": "no consent record for this booking"},
            )
        return found[0]

    def emails(self, booking_id: str | None = None) -> list[dict[str, Any]]:
        """Pre-call emails the planner produced."""
        where = {"booking_id": booking_id} if booking_id else {}
        return self.store.find(vocab.PRECALL_EMAIL_COLLECTION, where, limit=200)

    def runs(self, booking_id: str | None = None) -> list[dict[str, Any]]:
        """Recording runs, one row per step, oldest first.

        Sorted on the run's own ``at`` stamp rather than left in the store's
        default order. ``find()`` returns newest-first, and a lifecycle a reader
        cannot read forwards is not a lifecycle. Two rows written in the same
        millisecond tie, so the store's rowid tie-break is left to do its job by
        asking for every row rather than paginating.
        """
        where = {"booking_id": booking_id} if booking_id else {}
        rows = self.store.find(vocab.RECORDING_RUN_COLLECTION, where, limit=1000)
        # Sorted on the row's own `seq`, not on `at`. Two steps written in the same
        # millisecond tie on a timestamp, and the store's tie-break is the row id,
        # which is `uuid4().hex` - unique, random with respect to insertion, and so
        # arbitrary across runs on identical data. A lifecycle a reader cannot read
        # forwards is not a lifecycle, and the tie would show up as an intermittent
        # failure rather than as a wrong answer anyone could see.
        return sorted(rows, key=lambda row: (int(row["data"].get("seq") or 0), str(row["id"])))

    # -- step 1: the profile ------------------------------------------------ #

    def create_profile(
        self,
        payload: Any,
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """Admin centre > Data capture > Recording consent > Add Profile."""
        data = profiles.normalise(payload)
        return self.store.create(
            vocab.PROFILE_COLLECTION, data, room_id=room_id, actor=actor, source=source
        )

    def update_profile(
        self,
        profile_id: str,
        changes: Any,
        *,
        actor: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """Patch a profile and revalidate the whole thing.

        The revalidation is not an optimisation to skip. Turning the consent page
        on makes the profile invalid without a provider, and the request that
        turns it on is rarely the request that adds one.
        """
        existing = self.profile(profile_id)
        data = profiles.patch(existing["data"], changes)
        return self.store.update(profile_id, data, actor=actor, source=source)

    def set_default_profile(self, profile_id: str, **kwargs: Any) -> dict[str, Any]:
        """Mark one profile the default for users who have no assignment.

        The "exactly one" part lives here rather than in the HTTP layer, because it
        is a property of the data and not of the route. An invariant a second
        caller can bypass is not an invariant, and the caller that bypasses it here
        is a seed or a script rather than a browser.

        Step 6: "the profile is set as default for new team members". Two defaults
        would make "which profile applies to an unassigned user" unanswerable, and
        that answer is what a consent question depends on.
        """
        self.profile(profile_id)
        for record in self.profiles():
            if record["id"] == profile_id or not record["data"].get("is_default"):
                continue
            self.update_profile(record["id"], {"is_default": False}, **kwargs)
        return self.update_profile(profile_id, {"is_default": True}, **kwargs)

    def preview_consent_page(self, profile_id: str, base_url: str = "") -> dict[str, Any]:
        """Step 3's consent page preview."""
        return links.consent_page_preview(self.profile(profile_id)["data"], base_url)

    # -- step 6: the directory ---------------------------------------------- #

    def add_user(
        self,
        payload: Any,
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """One directory entry, with the three per-user flags the research names."""
        data = directory.normalise_user(payload)
        for existing in self.users():
            if directory.normalise_email(existing["data"].get("email")) == data["email"]:
                raise ProfileInvalid(
                    f"{data['email']} is already in the directory.",
                    {"email": "is already in the directory"},
                )
        return self.store.create(
            vocab.DIRECTORY_COLLECTION, data, room_id=room_id, actor=actor, source=source
        )

    def default_profile_id(self) -> str | None:
        """The profile marked the default for new team members."""
        for record in self.profiles():
            if record["data"].get("is_default"):
                return record["id"]
        return None

    def resolve_profile(self, organizer_email: str) -> dict[str, Any]:
        """The profile that applies to an organiser, and how it was found."""
        users = [record["data"] for record in self.users()]
        resolved = directory.resolve(users, organizer_email, self.default_profile_id())
        if not resolved["profile_id"]:
            raise OrganizerUnmapped(
                f"{directory.normalise_email(organizer_email)} has no consent profile "
                "assigned and the organisation has no default profile.",
                {"organizer_email": "resolves to no consent profile"},
            )
        self.profile(resolved["profile_id"])
        return resolved

    # -- step 7: opening a booking ------------------------------------------ #

    def open(
        self,
        booking: Any,
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """Issue a consent-enabled link for one booking and record the state.

        The order here is the order the research states, and each check is one the
        research documents rather than one this product invented:

        1. The organiser resolves to a user and a profile. Gong documents the
           missing-user case as 404, and the profile is resolved by user.
        2. The consent page must be on. Gong documents the missing case as 409, so
           this is refused before any link is issued rather than after the call
           fails.
        3. The organiser's own record-by-Gong flag is checked, and so is every
           invitee for the flag that prevents an invitation from recording.
        4. The outbound request is built and stored on the record.
        5. The link is issued and the machine starts in the state the profile
           implies.
        """
        if not isinstance(booking, dict):
            raise ProfileInvalid("A booking must be an object.", {"body": "must be an object"})

        errors: dict[str, str] = {}
        booking_id = str(booking.get("booking_id") or booking.get("id") or "").strip()
        if not booking_id:
            errors["booking_id"] = "is required; the consent record is keyed on it"
        organizer_email = directory.normalise_email(
            booking.get(vocab.PROFILE_RESOLUTION_KEY) or booking.get("organizer_email")
        )
        if not organizer_email:
            errors[vocab.PROFILE_RESOLUTION_KEY] = "is required; the research resolves by user"
        if errors:
            raise ProfileInvalid(
                f"{len(errors)} field(s) are missing: " + "; ".join(sorted(errors)), errors
            )

        resolved = self.resolve_profile(organizer_email)
        profile_record = self.profile(resolved["profile_id"])
        profile = profile_record["data"]

        if not profile.get(vocab.CONSENT_PAGE_SWITCH):
            raise ConsentPageDisabled(
                "The consent page is off, so Gong cannot issue a consent-enabled link. "
                f"Gong documents this as {409}: "
                f"{vocab.DOCUMENTED_ERRORS[409]}.",
                {vocab.CONSENT_PAGE_SWITCH: "must be on before a booking can be consented"},
            )

        users = [record["data"] for record in self.users()]
        recordable, why = directory.can_record(users, organizer_email)
        if not recordable:
            raise ProfileInvalid(
                f"This booking cannot be recorded: {why}.",
                {"organizer_email": "is not recordable"},
            )

        invitees = booking.get("invitees") or []
        blockers = directory.recording_blocked_by_invitee(users, invitees)
        if blockers:
            raise ProfileInvalid(
                "The invitation of "
                + ", ".join(blockers)
                + " would prevent this meeting from being recorded, so the booking is "
                "refused before the invite is sent.",
                {"invitees": "contains an address whose own setting prevents recording"},
            )

        providers = profile.get("providers") or {}
        if profile.get(vocab.DEFAULT_PROVIDER_FIELD):
            provider = str(profile[vocab.DEFAULT_PROVIDER_FIELD])
        elif providers:
            provider = next(iter(providers))
        else:
            # A profile that passed validation with no provider still has to name
            # one to build a request, and every researched provider is a real
            # conference. Zoom is the first the research lists, so it is the one
            # chosen, and the record says which it was.
            provider = next(iter(vocab.PROVIDERS))

        title = str(booking.get("title") or "Sales meeting")
        start_time = str(booking.get("start_time") or booking.get("startTime") or "")
        end_time = str(booking.get("end_time") or booking.get("endTime") or "")

        external, internal = precall.recipients(invitees, organizer_email)
        request = links.new_meeting_request(
            organizer_email=organizer_email,
            start_time=start_time,
            end_time=end_time,
            title=title,
            invitees=list(invitees) if isinstance(invitees, list) else [],
            external_id=booking_id,
            provider=provider,
        )

        # The vendor call is not made here. What is stored is the exact request
        # the research quotes, so a reviewer can read the researched call without
        # a socket having opened. See the inference recorded for this.
        bot_invites = [vocab.RECORDING_BOT_EMAIL]
        response = links.new_meeting_response(
            {
                "status": 201,
                "requestId": f"req-{booking_id}",
                "meetingId": f"mtg-{booking_id}",
                "meetingUrl": f"https://consent.example/{provider}/{booking_id}",
                "additionalInvitees": bot_invites,
            },
            request,
            vocab.RECORDING_BOT_EMAIL,
        )

        machine = decisions.initial(profile)
        moment = self._now()
        link = links.link_record(response, booking_id, profile_record["id"], _stamp(moment))

        record = self.store.create(
            vocab.CONSENT_RECORDING_COLLECTION,
            {
                "booking_id": booking_id,
                "room_id": room_id,
                "organizer_email": organizer_email,
                "profile_id": profile_record["id"],
                "profile_resolution": resolved["source"],
                "profile_resolution_key": vocab.PROFILE_RESOLUTION_KEY,
                "title": title,
                "start_time": start_time,
                "end_time": end_time,
                "provider": provider,
                "provider_link_kind": (profile.get("providers") or {}).get(provider),
                "external_invitees": external,
                "internal_invitees": internal,
                "bot_invited": response["additional_invitees"],
                "audio_prompt_suppressed": links.prompt_suppressed(profile),
                "outbound_request": request,
                "consent_link": link,
                **machine.to_data(),
                "opened_at": _stamp(moment),
                "updated_at": _stamp(moment),
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )

        self.store.create(
            vocab.RECORDING_RUN_COLLECTION,
            {
                "booking_id": booking_id,
                "recording_state": machine.recording_state,
                "consent_state": machine.consent_state,
                "state": machine.state,
                "event": "opened",
                # The first row, so the lifecycle starts at 1 rather than at 0. A
                # reader counting steps should not have to know whether the opening
                # counts as a step.
                "seq": 1,
                "at": _stamp(moment),
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
        return record

    # -- the participant's decision ----------------------------------------- #

    def decide(
        self,
        booking_id: str,
        decision: str,
        *,
        actor: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """Record a participant's decision, or the bot joining without one."""
        if decision not in decisions.DECISION_STEPS:
            raise ProfileInvalid(
                f"{decision!r} is not a decision this workflow records.",
                {"decision": "must be one of " + ", ".join(sorted(decisions.DECISION_STEPS))},
            )
        record = self.recording(booking_id)
        profile = self.profile(record["data"]["profile_id"])["data"]
        machine = decisions.machine_from_data(record["data"])
        moved = decisions.advance(machine, decisions.DECISION_STEPS[decision], profile)

        ok, why = decisions.is_consistent(moved)
        if not ok:
            raise ProfileInvalid(
                f"The step {decision!r} would leave the record inconsistent: {why}.",
                {"decision": why},
            )

        moment = self._now()
        return self._patch(record, moved, event=decision, moment=moment, actor=actor, source=source)

    def start_recording(self, booking_id: str, **kwargs: Any) -> dict[str, Any]:
        """The recording bot joined and the call is being recorded.

        Refused from a ``blocked`` recording, which is what makes "the call was
        recorded without consent" a state this machine cannot produce rather than
        a mistake a reviewer has to catch afterwards.
        """
        return self._step(booking_id, "start_recording", **kwargs)

    def finish_recording(self, booking_id: str, **kwargs: Any) -> dict[str, Any]:
        """The call ended. A recording exists only if one was started."""
        return self._step(booking_id, "finish_recording", **kwargs)

    def cancel_recording(self, booking_id: str, **kwargs: Any) -> dict[str, Any]:
        """The call ended with no recording."""
        return self._step(booking_id, "cancel_recording", **kwargs)

    def _step(self, booking_id: str, step: str, **kwargs: Any) -> dict[str, Any]:
        record = self.recording(booking_id)
        profile = self.profile(record["data"]["profile_id"])["data"]
        machine = decisions.machine_from_data(record["data"])
        moved = decisions.advance(machine, step, profile)
        ok, why = decisions.is_consistent(moved)
        if not ok:
            raise ProfileInvalid(
                f"The step {step!r} would leave the record inconsistent: {why}.",
                {"step": why},
            )
        return self._patch(record, moved, event=step, moment=self._now(), **kwargs)

    def _patch(
        self,
        record: dict[str, Any],
        machine: decisions.Machine,
        *,
        event: str,
        moment: datetime,
        actor: str | None = None,
        source: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Patch the one record and append the run row, in that order.

        The run row is written after the patch and carries the resulting state, so
        the history reads as a sequence of states rather than as a set of
        un-ordered observations. It is a second write and not part of the same
        transaction as the patch, which is a limitation of the store rather than a
        choice: :class:`~dsr.db.audited.AuditedDatabase` writes one record per
        call, and the consent record is the one the audit guarantee is about.
        """
        patch = {
            **machine.to_data(),
            "updated_at": _stamp(moment),
            "last_event": event,
            **(extra or {}),
        }
        updated = self.store.update(record["id"], patch, actor=actor, source=source)
        self.store.create(
            vocab.RECORDING_RUN_COLLECTION,
            {
                "booking_id": record["data"]["booking_id"],
                "state": machine.state,
                "consent_state": machine.consent_state,
                "recording_state": machine.recording_state,
                "event": event,
                "seq": len(self.runs(record["data"]["booking_id"])) + 1,
                "at": _stamp(moment),
            },
            room_id=record["room_id"],
            actor=actor,
            source=source,
        )
        return updated

    # -- step 7: re-issuing the link ---------------------------------------- #

    def current_link(self, booking_id: str, meeting_id: str | None = None) -> dict[str, Any]:
        """The link that currently joins the call.

        Raises :class:`~dsr.recording_consent.errors.LinkSuperseded` rather than
        returning ``None``. A booking whose only link has been superseded has no
        way in, and that is a distinct fact from "this booking has no link yet" -
        the caller can fix the second by issuing one and cannot fix the first by
        anything except issuing one, so the two need different words.

        Passing ``meeting_id`` asks the question a caller with an older invite
        actually has: "is the link in this calendar invite still current?" A link
        that has been replaced raises rather than answering with its replacement,
        because silently handing back a different link would put a new URL in an
        invite that was already sent.
        """
        record = self.recording(booking_id)
        link = dict(record["data"].get("consent_link") or {})
        if not link:
            raise LinkSuperseded(
                f"Booking {booking_id} has no consent link yet. Open one first.",
                {"consent_link": "no link has been issued"},
            )
        if link.get("link_state") != "active":
            raise LinkSuperseded(
                f"Booking {booking_id}'s consent link is {link.get('link_state')}. "
                f"It was replaced by {link.get('superseded_by')} and no longer joins "
                "the call.",
                {"consent_link": "the link has been superseded"},
            )
        if meeting_id is not None and str(link.get("meeting_id")) != str(meeting_id):
            superseded = dict(record["data"].get("superseded_link") or {})
            raise LinkSuperseded(
                f"Meeting {meeting_id} is not this booking's current link. "
                f"It was replaced by {link.get('meeting_id')} at "
                f"{superseded.get('superseded_at') or 'an earlier change'}.",
                {"meeting_id": "has been superseded by a newer link"},
            )
        return link

    def reissue_link(self, booking_id: str, **kwargs: Any) -> dict[str, Any]:
        """Change the consent link, disabling the previous one.

        "Each time you change this link, the previous link is disabled."
        Implemented as a state change, so the audit row for the link that was
        replaced survives. See ``links.retire_previous`` and the inference recorded
        for it.
        """
        record = self.recording(booking_id)
        data = record["data"]
        moment = self._now()
        now_iso = _stamp(moment)

        old = dict(data.get("consent_link") or {})
        response = {
            "request_id": f"req-{data['booking_id']}-r{len(self.runs(booking_id)) + 1}",
            "meeting_id": f"mtg-{data['booking_id']}-r{len(self.runs(booking_id)) + 1}",
            "meeting_url": f"https://consent.example/{data.get('provider')}/{data['booking_id']}"
            f"-r{len(self.runs(booking_id)) + 1}",
            "additional_invitees": list(old.get("additional_invitees") or []),
            "additional_invitees_source": old.get("additional_invitees_source"),
            "provider": data.get("provider"),
        }
        new_link = links.link_record(response, data["booking_id"], data["profile_id"], now_iso)
        retired = links.retire_previous(old, new_link, now_iso)

        patch = {
            "consent_link": new_link,
            "superseded_link": retired,
            "link_change_count": int(data.get("link_change_count") or 0) + 1,
            "updated_at": now_iso,
        }
        return self.store.update(record["id"], patch, **kwargs)

    # -- step 4: the pre-call email ------------------------------------------ #

    def plan_precall_email(
        self,
        booking_id: str,
        *,
        sender_name: str = "",
        sender_company: str = "",
        meeting_hour: str = "",
        actor: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """Send the pre-call email if the window is open, and record why not.

        Returns the stored email row on a send and a refusal dict carrying the
        reason on a skip. The reason is one of the researched strings this
        package owns, and an operator asking "why did nobody get the reminder"
        gets it from the response rather than from a log line.
        """
        record = self.recording(booking_id)
        data = record["data"]
        profile = self.profile(data["profile_id"])["data"]

        if not profile.get(profiles.PRECALL_EMAIL_SWITCH):
            return self._skip(booking_id, "precall_email_disabled", record)

        start_time = _parse_iso(str(data.get("start_time") or ""))
        if start_time is None:
            return self._skip(booking_id, "no_start_time", record)

        already = bool(self.emails(booking_id))
        due, why = precall.should_send(self._now(), start_time, already_sent=already)
        if not due:
            return self._skip(booking_id, why, record)

        external = data.get("external_invitees") or []
        if not external:
            return self._skip(booking_id, "no_external_invitees", record)

        moment = self._now()
        email = precall.render(
            profile,
            sender_name=sender_name or "Your host",
            sender_company=sender_company or "Your company",
            meeting_title=str(data.get("title") or "Sales meeting"),
            meeting_hour=meeting_hour or start_time.strftime("%H:%M"),
            to=list(external),
            now_iso=_stamp(moment),
        )
        email["booking_id"] = booking_id
        email["window_reason"] = why
        email["window_minutes"] = list(vocab.PRECALL_WINDOW_MINUTES)
        return self.store.create(
            vocab.PRECALL_EMAIL_COLLECTION,
            email,
            room_id=data.get("room_id"),
            actor=actor,
            source=source,
        )

    def _skip(self, booking_id: str, reason: str, record: dict[str, Any]) -> dict[str, Any]:
        """A skip is a result, not a failure.

        The email is not sent and the caller gets the reason. Recording it as a
        refusal would be wrong: nothing was wrong with the request, the window
        simply was not open, and an operator reading a run of refusals would see a
        product that is failing rather than one that is waiting.
        """
        return {
            "booking_id": booking_id,
            "status": "skipped",
            "reason": reason,
            "sent": False,
            "room_id": record["data"].get("room_id"),
        }

    # -- the board ---------------------------------------------------------- #

    def summary(self, room_id: str | None = None) -> dict[str, Any]:
        """Counts for the page header, and the states that need attention."""
        recordings = self.recordings(room_id)
        by_state: dict[str, int] = {state: 0 for state in vocab.STATES}
        by_consent: dict[str, int] = {state: 0 for state in vocab.CONSENT_STATES}
        blocked = 0
        cancelled = 0
        inconsistent: list[str] = []

        for record in recordings:
            data = record["data"]
            state = str(data.get("state") or "")
            by_state[state] = by_state.get(state, 0) + 1
            by_consent[str(data.get("consent_state") or "not_required")] = (
                by_consent.get(str(data.get("consent_state") or "not_required"), 0) + 1
            )
            if data.get("recording_state") == "blocked":
                blocked += 1
            if data.get("recording_state") == "cancelled":
                cancelled += 1
            machine = decisions.machine_from_data(data)
            ok, why = decisions.is_consistent(machine)
            if not ok:
                inconsistent.append(f"{data.get('booking_id')}: {why}")

        profiles_list = self.profiles(room_id)
        return {
            "room_id": room_id,
            "profiles": len(profiles_list),
            "profiles_with_consent_page": sum(
                1 for p in profiles_list if p["data"].get(vocab.CONSENT_PAGE_SWITCH)
            ),
            "profiles_enforcing": sum(
                1 for p in profiles_list if p["data"].get(vocab.ENFORCEMENT_SWITCH)
            ),
            "users": len(self.users(room_id)),
            "bookings": len(recordings),
            "by_state": by_state,
            "by_consent_state": by_consent,
            "recordings_blocked": blocked,
            "recordings_cancelled": cancelled,
            "recordings_complete": sum(
                1 for r in recordings if r["data"].get("recording_state") == "complete"
            ),
            "inconsistent": inconsistent,
            "resolution_key": vocab.PROFILE_RESOLUTION_KEY,
            "providers": sorted(vocab.PROVIDERS),
            "precall_window_minutes": list(vocab.PRECALL_WINDOW_MINUTES),
            "prompt_mode": vocab.DEFAULT_PROMPT_MODE,
        }


def _parse_iso(text: str) -> datetime | None:
    """Parse an ISO timestamp, or return ``None``.

    A booking's start may arrive in several shapes, and a booking record this
    workflow did not write is not guaranteed to carry one at all. A record with no
    parseable start gets the ``no_start_time`` skip rather than an exception,
    because an unreadable timestamp is a fact about the booking and not a fault in
    the planner.
    """
    text = text.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


__all__ = [
    "ConsentEngine",
    "OrganizerUnmapped",
    "ProfileInvalid",
    "ProfileNotFound",
]
