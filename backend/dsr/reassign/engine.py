"""The reassignment flow over the audited store, end to end.

The researched data flow, in the order this module performs it:

1. take the meeting record and the Distribution settings of the meeting booked;
2. decide, with the rules in :mod:`dsr.reassign.rules` and nothing else;
3. on a refusal, write nothing and say why;
4. on a decision, take the new host's availability for granted - the window was
   already checked - and book the meeting for them;
5. update the invite with the new assignee's name, links and details;
6. move the round-robin credit, unless a no-show credit-back already did;
7. write the Events History row: who, to whom, when, and the source;
8. build the webhooks that fire.

Steps 4 to 8 are one transaction. A reassignment that moved the meeting but
failed to write the history row would leave the two disagreeing, and the audit
log is supposed to be the thing that cannot drift from the data - so a partial
reassignment is not a state this module can produce.

Every write method takes ``source`` as a keyword with no default and hands it
down. The feature module builds that string from ``router.prefix`` at the route,
so the audit row names the route that actually served the write rather than a
path a refactor could leave behind. A hardcoded source has shipped in this
codebase before, as an audit log that kept naming a route the app had stopped
serving.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from dsr.reassign import distribution as dist
from dsr.reassign import rules, webhooks
from dsr.reassign.distribution import normalise_meeting
from dsr.reassign.errors import MeetingStateError, ReassignError
from dsr.reassign.vocabulary import (
    DEFAULT_STATUS,
    EDITABLE_AND_LOCKED,
    REASSIGNABLE_STATUSES,
    SURFACE_LABELS,
    changed_invite_fields,
    invite_for,
    require_status,
    require_surface,
    require_tab,
)
from dsr.store import RecordStore

#: Collections. Named constants because the tests filter the audit log on them
#: and a typo in a string literal would make an audit assertion pass vacuously.
MEETING_COLLECTION = "meeting"
DISTRIBUTION_COLLECTION = "distribution"
HOST_COLLECTION = "host"
REASSIGNMENT_COLLECTION = "reassignment"
HISTORY_COLLECTION = "meeting_events_history"

#: The two Meetings Activity tabs, step 1: "**Upcoming** (or **Past**) tab".
UPCOMING = "upcoming"
PAST = "past"

#: How many Events History rows one meeting keeps. The researched artifact is a
#: tab a person reads, not an unbounded log, and a room with a thousand
#: reassignments should not return a thousand rows to a browser.
HISTORY_LIMIT = 200


class ReassignEngine:
    """Every read and write this workflow performs, over one audited store.

    Built per request from :data:`dsr.deps.StoreDep`, for the same reason the
    other features build their engine per request: it holds nothing beyond the
    store and a clock, so both are trivially overridable in a test and no
    long-lived object has to be hung off ``app.state`` - which is a shared file
    this feature may not edit.
    """

    def __init__(
        self,
        store: RecordStore,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    # -- reads --------------------------------------------------------------- #

    def _require(self, collection: str, record_id: str, label: str) -> dict[str, Any]:
        record = self.store.get(record_id)
        if record is None or record.get("collection") != collection:
            raise ReassignError(f"{label} {record_id} not found")
        return record

    def get_meeting(self, meeting_id: str) -> dict[str, Any] | None:
        record = self.store.get(meeting_id)
        if record is None or record.get("collection") != MEETING_COLLECTION:
            return None
        return record

    def require_meeting(self, meeting_id: str) -> dict[str, Any]:
        meeting = self.get_meeting(meeting_id)
        if meeting is None:
            raise ReassignError(f"meeting {meeting_id} not found")
        return meeting

    def meetings(self, room_id: str | None = None, **filters: Any) -> list[dict[str, Any]]:
        """Meetings for a room, filtered through the dynamic index.

        Every filter is a dotted JSON path into ``data``, which is the whole
        point of the schema-flexible store: a team that later adds a field gets
        to filter on it without a migration or a change to this method.

        ``find`` is asked for the room's rows in *both* branches rather than
        filtering in Python afterwards. The room scope is a column, not a JSON
        path, so the room is applied with an explicit filter on the returned
        rows either way - but only the filtered branch is doing a whole-table
        scan, and the unfiltered branch is a single indexed read. The two are
        otherwise the same query, so a filter and no filter cannot disagree.
        """
        where = {key: value for key, value in filters.items() if value not in (None, "")}
        if where:
            records = self.store.find(MEETING_COLLECTION, where, limit=1000)
        elif room_id is None:
            records = self.store.list(MEETING_COLLECTION, limit=1000)
        else:
            records = self.store.list(MEETING_COLLECTION, room_id=room_id, limit=1000)
        if room_id is not None:
            records = [record for record in records if record.get("room_id") == room_id]
        return records

    def meeting_activity(
        self, room_id: str, tab: str = "all", status: str | None = None, **filters: Any
    ) -> list[dict[str, Any]]:
        """The Meetings Activity list, split by the Upcoming and Past tabs.

        "Upcoming" and "Past" are decided by where the meeting *starts*
        relative to now, not by its status. A meeting whose status is still
        ``scheduled`` but whose start time has passed belongs in Past - that is
        the tab a rep opening Meetings Activity in the morning is looking for -
        and a meeting that was cancelled for next week is still Upcoming, because
        the question the tab answers is when it is, not whether it will happen.
        """
        which = require_tab(tab)
        now = self._clock()
        records = self.meetings(room_id=room_id, status=status, **filters)
        out: list[dict[str, Any]] = []
        for record in records:
            if which == UPCOMING:
                if dist.parse_instant(record["data"]["starts_at"]) < now:
                    continue
            elif which == PAST:
                if dist.parse_instant(record["data"]["starts_at"]) >= now:
                    continue
            out.append(record)
        return sorted(out, key=lambda r: (r["data"]["starts_at"], r["id"]), reverse=which != PAST)

    def distributions(self, room_id: str | None = None) -> list[dict[str, Any]]:
        return self.store.list(DISTRIBUTION_COLLECTION, room_id=room_id, limit=1000)

    def hosts(self, room_id: str | None = None, team: str | None = None, active: bool | None = None) -> list[dict[str, Any]]:
        records = self.store.list(HOST_COLLECTION, room_id=room_id, limit=1000)
        if team:
            records = [r for r in records if r["data"].get("team") == team]
        if active is not None:
            records = [r for r in records if bool(r["data"].get("active", True)) is active]
        return records

    def require_host(self, host_id: str) -> dict[str, Any]:
        return self._require(HOST_COLLECTION, host_id, "host")

    def reassignments(self, room_id: str | None = None, meeting_id: str | None = None) -> list[dict[str, Any]]:
        records = self.store.list(REASSIGNMENT_COLLECTION, room_id=room_id, limit=1000)
        if meeting_id:
            records = [r for r in records if r["data"].get("meeting_id") == meeting_id]
        return records

    def get_reassignment(self, reassignment_id: str) -> dict[str, Any] | None:
        record = self.store.get(reassignment_id)
        if record is None or record.get("collection") != REASSIGNMENT_COLLECTION:
            return None
        return record

    def events_history(self, meeting_id: str | None = None, limit: int = HISTORY_LIMIT) -> list[dict[str, Any]]:
        """The Events History tab, newest first.

        One row per reassignment, carrying the four things the research says the
        tab displays: who reassigned it, to whom, when, and the source.

        ``store.list`` is asked for ``updated_at`` descending rather than left on
        its default, so the order is stated here rather than inherited. Its
        tie-break on the record id makes the order *total* even when two
        reassignments land in the same millisecond - which they do, in a seed and
        in any test that reuses a clock - so a history tab that can disagree with
        itself about order is not something this has to worry about producing.
        """
        records = self.store.list(HISTORY_COLLECTION, limit=1000, order_by="updated_at", descending=True)
        if meeting_id:
            records = [r for r in records if r["data"].get("meeting_id") == meeting_id]
        return records[: max(1, min(int(limit), HISTORY_LIMIT))]

    def meeting_history(self, meeting_id: str) -> dict[str, Any]:
        """One meeting's reassignment history, and whether it is past due.

        ``needs_reassignment`` is a rep's queue rather than a rule: a meeting
        whose start is within the distribution's minimum notice window is the
        one a reassignment is about to be too late for, which is the situation
        the researched note about ignoring the notice exists to rescue.
        """
        meeting = self.require_meeting(meeting_id)
        history = self.events_history(meeting_id=meeting_id)
        # `["data"]`, not the envelope: the bounds are read off the distribution's
        # own fields, and an envelope carries them one level down. Passing the
        # envelope here would read every bound as unset, which is a quiet
        # "nothing is ever urgent" rather than an error.
        distribution = self._distribution_for(meeting["data"])["data"]
        now = self._clock()
        starts_at = dist.parse_instant(meeting["data"]["starts_at"])
        bounds = dist.evaluate_bounds(distribution, starts_at, now)
        notice_minutes = distribution.get("min_notice_minutes")
        return {
            "meeting_id": meeting_id,
            "host_id": meeting["data"].get("host_id"),
            "reassignment_count": len(history),
            "history": history,
            "bounds": bounds,
            "notice_window_open": not bounds["min_notice_breached"] if notice_minutes else True,
            "needs_reassignment": bounds["min_notice_breached"],
        }

    # -- the decision -------------------------------------------------------- #

    def _distribution_for(self, meeting: Mapping[str, Any]) -> dict[str, Any]:
        """The Distribution settings of the meeting booked.

        Found by name, and by the meeting's own record when it names one. The
        meeting carries ``distribution`` as a name rather than an id because
        the scheduler reopens the *same* Distribution context: the name is the
        context, and a request that supplies a different one has to be refused
        rather than silently resolved to whatever id it passed.
        """
        return self._distribution_named(str(meeting.get("distribution") or "").strip())

    def _decision(
        self, meeting_id: str, request: Mapping[str, Any], *, require_target: bool = True
    ) -> rules.Decision:
        meeting = self.require_meeting(meeting_id)
        status = require_status(meeting["data"].get("status"), field="meeting.status")
        if status not in REASSIGNABLE_STATUSES:
            # 409, not 400: the request was well formed and conflicts with the
            # meeting's state. A rep who tries to hand over a cancelled meeting
            # is not making a mistake about the request.
            raise MeetingStateError(
                f"meeting {meeting_id} is {status.replace('_', ' ')}, and only a "
                f"{' or '.join(s.replace('_', ' ') for s in sorted(REASSIGNABLE_STATUSES))} meeting can be "
                f"reassigned"
            )
        distribution_record = self._distribution_for(meeting["data"])
        # `hosts()` returns record envelopes; the decision works on payloads
        # and reads each host's `id`, which only the envelope carries. Merging
        # the two here means `decide` never has to know which shape it got.
        hosts = [record["data"] | {"id": record["id"]} for record in self.hosts()]
        decision = rules.decide(
            meeting["data"] | {"id": meeting["id"], "invite": meeting["data"].get("invite") or {}},
            distribution_record["data"],
            hosts,
            request,
            self._clock(),
            distribution_id=distribution_record["id"],
            require_target=require_target,
        )
        return decision

    def preview(self, meeting_id: str, request: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """What a reassignment request *would* do. Writes nothing at all.

        The read-only half of :meth:`reassign`, calling the same
        :func:`~dsr.reassign.rules.decide`, so the answer a form is shown before
        the operator commits is the answer the commit produces. A preview that
        could disagree with the write would be worse than no preview.
        """
        return self._decision(meeting_id, request or {}).to_dict()

    def prepare_meeting(self, body: dict[str, Any]) -> dict[str, Any]:
        """A validated meeting, with its two locked fields taken from the distribution.

        The Meeting Type and the Workspace are the distribution's, not the
        caller's: "You cannot change the Meeting Type or Workspace", and a field
        that cannot be changed should not have to be *sent* in order to be
        correct. Filling them here also means the value is written once, at the
        distribution, rather than restated on every meeting and free to drift.

        A body that *does* name them is still checked - the normalisation
        requires both, and the lock in :func:`~dsr.reassign.rules.decide` is
        what refuses a request that tries to change one. This only fills the
        blanks.
        """
        name = str(body.get("distribution") or "").strip()
        if not name:
            raise ReassignError(
                "a meeting must name the distribution it was booked from; the Distribution settings of "
                "the meeting booked are what a reassignment takes into account"
            )
        record = self._distribution_named(name)
        filled = dict(body)
        filled.setdefault("meeting_type", record["data"].get("meeting_type"))
        filled.setdefault("workspace", record["data"].get("workspace"))
        filled.setdefault("team", record["data"].get("team"))
        return normalise_meeting(filled)

    def _distribution_named(self, name: str) -> dict[str, Any]:
        for record in self.store.list(DISTRIBUTION_COLLECTION, limit=1000):
            if record["data"].get("name") == name:
                return record
        raise ReassignError(
            f"distribution {name!r} is not configured; the Distribution settings of the meeting booked are "
            f"what a reassignment takes into account, so they have to exist"
        )

    def require_slot_within_bounds(self, meeting: Mapping[str, Any], host_id: str) -> dict[str, Any]:
        """Refuse a *booking* whose slot breaches the distribution's bounds.

        The counterpart to the reassign path's exemption, and what gives that
        exemption its researched purpose: "so it can always rescue a stale
        booking" only means something if a stale booking would otherwise have
        been refused. Enforcing the bounds here is therefore part of landing the
        researched rule rather than a separate policy of this build.

        Checked against the distribution of the meeting booked, and against the
        proposed host's own calendar, because a booking that doubles up a host
        is refused on the same grounds the "known and free" pick is.
        """
        distribution_record = self._distribution_for(dict(meeting))
        starts_at = dist.parse_instant(meeting.get("starts_at"), field="starts_at")
        ends_at = dist.parse_instant(meeting.get("ends_at"), field="ends_at")
        verdict = dist.evaluate_bounds(distribution_record["data"], starts_at, self._clock())
        if verdict["would_block"]:
            breached = " and ".join(verdict["breached"])
            # The numbers of the breached bounds, not of every bound: a
            # distribution that configures only a max range should not be told
            # about a min-notice limit it does not have.
            limits = []
            if verdict["min_notice_breached"]:
                limits.append(f"{verdict['min_notice_minutes']} minutes")
            if verdict["max_range_breached"]:
                limits.append(f"{verdict['max_range_days']} days")
            raise ReassignError(
                f"a new booking cannot start {' or '.join(limits)} out; this slot breaches the "
                f"{breached} of the {distribution_record['data'].get('name')} distribution. "
                f"Reassigning an existing meeting does ignore those bounds - that is the researched "
                f"rescue path."
            )
        # `["data"]` for the same reason as in `meeting_history`: the calendar
        # blocks live on the host's payload, not on its envelope. Reading the
        # envelope would find no blocks at all and accept the double booking.
        host = self.require_host(host_id)["data"]
        clashes = dist.conflicts_with(host, starts_at, ends_at)
        if clashes:
            raise ReassignError(
                f"{host.get('name')} is not free for that slot; it clashes with "
                f"{clashes[0].get('label') or clashes[0]['starts_at']}"
            )
        return verdict

    def availability(self, meeting_id: str, request: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Which hosts could take this booking, and why the others could not.

        Backs "If the target person is known and free, pick them and hit
        Reassign": the picker needs the ineligible rows as much as the eligible
        ones, because a rep who had someone in mind needs to see that they are
        on the list and busy, not that they are missing.
        """
        request = dict(request or {})
        decision = self._decision(meeting_id, request, require_target=False)
        eligible = [row for row in decision.candidates if row["eligible"]]
        return {
            "meeting_id": meeting_id,
            "assign_to": decision.assign_to,
            "mode": decision.mode,
            "starts_at": decision.starts_at,
            "ends_at": decision.ends_at,
            "eligible": eligible,
            "ineligible": [row for row in decision.candidates if not row["eligible"]],
            "candidates": decision.candidates,
            "outcome": decision.outcome,
            "refused": decision.refused,
            "reason": decision.reason,
        }

    # -- the workflow -------------------------------------------------------- #

    def reassign(
        self,
        meeting_id: str,
        request: Mapping[str, Any] | None = None,
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Reassign a booked meeting. Steps 1 to 8 of the researched flow.

        ``source`` is keyword-only with no default: a caller that forgets it
        fails loudly rather than writing a null into the audit log, which is the
        defect the brief names by name.
        """
        request = dict(request or {})
        decision = self._decision(meeting_id, request)
        if decision.refused:
            # A refusal writes nothing. There is no reassignment to record, and
            # "we tried and did not" would be indistinguishable from "we tried
            # and did" to a reader who did not open the detail.
            raise ReassignError(decision.reason)

        now = self._clock()
        at = dist.format_instant(now)
        meeting = self.require_meeting(meeting_id)
        previous_host = self.require_host(decision.from_host["id"])
        new_host = self.require_host(decision.to_host["id"])
        surface = require_surface(request.get("surface"), field="surface")
        # The invite is rebuilt from the new host, never merged into the old one,
        # so a field they have not set goes to null rather than keeping the
        # previous host's dial-in. See the
        # "invite-fields-do-not-survive-the-host-change" inference.
        invite_after = invite_for(new_host["data"])
        invite_changes = changed_invite_fields(decision.invite_before, invite_after)

        meeting_patch: dict[str, Any] = {
            "host_id": new_host["id"],
            "status": meeting["data"].get("status", DEFAULT_STATUS),
            "invite": invite_after,
            "reassigned_at": at,
            "reassignment_count": int(meeting["data"].get("reassignment_count", 0) or 0) + 1,
            "last_reassignment_source": surface,
        }
        if decision.slot_changed:
            meeting_patch["starts_at"] = decision.starts_at
            meeting_patch["ends_at"] = decision.ends_at

        reassignment_payload = {
            "meeting_id": meeting_id,
            "from_host_id": previous_host["id"],
            "to_host_id": new_host["id"],
            "from_host": decision.from_host,
            "to_host": decision.to_host,
            "assign_to": decision.assign_to,
            "mode": decision.mode,
            "surface": surface,
            "surface_label": SURFACE_LABELS[surface],
            "requested_by": str(request.get("requested_by") or "").strip() or (actor or "unknown"),
            "requested_by_email": str(request.get("requested_by_email") or "").strip() or None,
            "reason": decision.reason,
            "outcome": decision.outcome,
            "starts_at": decision.starts_at,
            "ends_at": decision.ends_at,
            "slot_changed": decision.slot_changed,
            "bounds_bypassed": decision.bounds.get("bypassed", []),
            "credit_movement": decision.credit,
            "invite_before": decision.invite_before,
            "invite_after": invite_after,
            "invite_fields_changed": invite_changes,
            "webhooks": decision.webhooks,
            "at": at,
        }

        history_payload = {
            "meeting_id": meeting_id,
            "meeting_title": meeting["data"].get("title"),
            "reassigned_by": reassignment_payload["requested_by"],
            "reassigned_by_email": reassignment_payload["requested_by_email"],
            "reassigned_to_host_id": new_host["id"],
            "reassigned_to": new_host["data"].get("name"),
            "from_host_id": previous_host["id"],
            "from_host": previous_host["data"].get("name"),
            "reassignment_source": surface,
            "reassignment_source_label": SURFACE_LABELS[surface],
            "at": at,
        }

        with self.store.db.transaction(actor=actor, source=source) as tx:
            meeting_record = tx.update(meeting_id, meeting_patch, actor=actor, source=source)
            reassignment = tx.create(
                REASSIGNMENT_COLLECTION,
                reassignment_payload,
                room_id=meeting_record["room_id"],
                actor=actor,
                source=source,
            )
            history = tx.create(
                HISTORY_COLLECTION,
                history_payload,
                room_id=meeting_record["room_id"],
                actor=actor,
                source=source,
            )
            credit = self._apply_credit(tx, decision, previous_host, new_host, actor=actor, source=source)

        payloads = self._webhook_payloads(meeting_record, new_host, previous_host, reassignment, at)
        response = {
            "reassignment": reassignment,
            "meeting": meeting_record,
            "history": history,
            "webhooks": payloads,
            "invite_fields_changed": invite_changes,
            "credit": credit,
            "bounds_bypassed": decision.bounds.get("bypassed", []),
        }
        return response

    def _apply_credit(
        self,
        tx: Any,
        decision: rules.Decision,
        previous_host: Mapping[str, Any],
        new_host: Mapping[str, Any],
        *,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Move the round-robin credit, unless a no-show already returned it.

        Inside the caller's transaction, so the credit and the reassignment
        either both land or neither does: a rotation that counted a booking
        nobody was given is the same drift the history row would have to
        contradict.
        """
        patch = dist.credit_patch(decision.credit)
        if patch is None:
            return dict(decision.credit)
        previous_delta, new_delta = patch
        tx.update(
            previous_host["id"],
            {"round_robin_credits": int(previous_host["data"].get("round_robin_credits", 0) or 0) + previous_delta},
            actor=actor,
            source=source,
        )
        tx.update(
            new_host["id"],
            {"round_robin_credits": int(new_host["data"].get("round_robin_credits", 0) or 0) + new_delta},
            actor=actor,
            source=source,
        )
        return dict(decision.credit)

    def _webhook_payloads(
        self,
        meeting: Mapping[str, Any],
        new_host: Mapping[str, Any],
        previous_host: Mapping[str, Any],
        reassignment: Mapping[str, Any],
        at: str,
    ) -> list[dict[str, Any]]:
        """Build the payloads for the webhooks that fire, and nothing else.

        ``BOOKING_REASSIGNED`` is included only for a round-robin booking,
        because that is the scope its documentation states. Building it for
        every reassignment would tell a CRM to re-point ownership for a booking
        whose rotation was never in play, and a CRM that trusts that webhook
        acts on it.
        """
        data = meeting["data"]
        payloads = [
            webhooks.meeting_update_payload(
                data, new_host["data"], reassignment=reassignment["data"], at=at
            )
        ]
        if bool(data.get("round_robin")):
            payloads.append(
                webhooks.booking_reassigned_payload(
                    data,
                    new_host["data"],
                    previous_host["data"],
                    reassignment=reassignment["data"],
                    booking_uid=str(data.get("booking_uid") or meeting["id"]),
                    at=at,
                )
            )
        return payloads

    # -- summaries ----------------------------------------------------------- #

    def summary(self, room_id: str | None = None) -> dict[str, Any]:
        """Counts for the page header, over the same rows the list returns."""
        meetings = self.meetings(room_id=room_id)
        reassignments = self.reassignments(room_id=room_id)
        history = self.store.list(HISTORY_COLLECTION, room_id=room_id, limit=1000)
        now = self._clock()

        by_status: dict[str, int] = {}
        for record in meetings:
            key = str(record["data"].get("status") or DEFAULT_STATUS)
            by_status[key] = by_status.get(key, 0) + 1

        by_source: dict[str, int] = {}
        for record in history:
            key = str(record["data"].get("reassignment_source") or "unknown")
            by_source[key] = by_source.get(key, 0) + 1

        hosts = self.hosts()
        reassigned_hosts = {r["data"].get("to_host_id") for r in reassignments}
        return {
            "meetings": len(meetings),
            "upcoming": sum(1 for r in meetings if dist.parse_instant(r["data"]["starts_at"]) >= now),
            "past": sum(1 for r in meetings if dist.parse_instant(r["data"]["starts_at"]) < now),
            "reassignments": len(reassignments),
            "history_rows": len(history),
            "hosts": len(hosts),
            "active_hosts": sum(1 for r in hosts if r["data"].get("active", True)),
            "inactive_hosts": sum(1 for r in hosts if not r["data"].get("active", True)),
            "hosts_who_have_kept_a_booking": len(reassigned_hosts & {r["id"] for r in hosts}),
            "by_status": by_status,
            "by_source": by_source,
            "bounds_quote": EDITABLE_AND_LOCKED,
        }
