"""The engine: calendars, panels, searches, bookings - all through the audited store.

:class:`SlotFinder` is the whole researched pipeline, and it is the only place
the arithmetic runs:

1. enumerate candidates from ``timeConstraint{activityDomain, timeSlots}`` and
   ``meetingDuration``;
2. read free/busy for the panel's calendars, expanding distribution lists inside
   the researched ``groupExpansionMax`` (100) and ``calendarExpansionMax`` (50)
   caps;
3. score each candidate with the researched per-attendee availability weights -
   **free=100, unknown=49, busy=0** - averaged over the invited set;
4. keep the ones that clear ``minAttendeePercentage``;
5. sort **high→low, then chronologically**;
6. attach a ``suggestionReason``, and when there are none, an
   ``emptySuggestionsReason`` and the documented re-call adjustments.

Then :meth:`SlotFinder.find` writes one ``panel_search`` row per call, so the
room has a log of what was asked, what came back, and which adjustment followed -
which is the research's automations line, and the only automation it specifies.

:meth:`SlotFinder.book` is the commit: the researched
``POST /calendars/{calendarId}/events``, the event created **on the
organizer's** calendar, with a fresh conference when asked for. It re-reads
availability first, because the research's own note says the suggestions are
"fine-tuned from time to time" and availability is a pull read with no
invalidation - so a slot from last week is a snapshot of a fact that moves.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from dsr.panel_time.calendar import (
    BusyMap,
    Expansion,
    LocalDirectory,
    UrllibProvider,
    expand_invited,
    render_commit_request,
    render_free_busy_request,
)
from dsr.panel_time.errors import (
    CalendarShapeError,
    ConstraintError,
    LimitExceeded,
    NotFound,
    PanelShapeError,
    PanelTimeNotConfigured,
    SlotUnavailable,
)
from dsr.panel_time.reasons import (
    ADJUSTMENT_MEANING,
    RETUNABLE_KEYS,
    RETUNE_ADJUSTMENTS,
    apply_adjustment,
    derive_empty_reason,
    describe_search,
    retune_adjustments,
    suggestion_reason,
)
from dsr.panel_time.slots import (
    apply_house_rules,
    enumerate_candidates,
    evaluate,
    normalise_house_rules,
    normalise_time_constraint,
    rank,
    window,
)
from dsr.panel_time.timeutils import (
    Clock,
    format_duration,
    format_instant,
    parse_duration,
    parse_instant,
)
from dsr.panel_time.vocabulary import (
    CALENDAR_EXPANSION_MAX_LIMIT,
    CALENDAR_EXPANSION_MAX_QUOTE,
    CREATE_CONFERENCE_DEFAULT,
    DEFAULT_MEETING_DURATION,
    DEFAULT_SLOT_INTERVAL,
    GROUP_EXPANSION_MAX_LIMIT,
    LOCATION_TYPES,
    MAX_SUGGESTIONS_LIMIT,
    PROVIDERS,
    RANK_CONFIDENCE,
    RANKERS,
    RETURN_SUGGESTION_REASONS_DEFAULT,
)
from dsr.store import RecordStore

#: The four record kinds this feature owns. Namespaced so nothing collides with
#: the core dataset or another feature's collections. ``panel_`` because the
#: research's subject is a multi-person panel, and every one of these is scoped
#: to one.
CALENDAR_COLLECTION = "panel_calendar"
PANEL_COLLECTION = "panel_search_panel"
SEARCH_COLLECTION = "panel_search"
BOOKING_COLLECTION = "panel_booking"

COLLECTIONS: tuple[str, ...] = (
    CALENDAR_COLLECTION,
    PANEL_COLLECTION,
    SEARCH_COLLECTION,
    BOOKING_COLLECTION,
)

#: The provider names a search can use. A free/busy read is installation-wide but
#: a panel names the dialect it talks to, because the two render different
#: requests and the research specifies both.
PROVIDER_NAMES: tuple[str, ...] = ("local", "urllib")

#: The one adjustment that helps a search whatever its state, and the default a
#: re-call takes when the search it is retrying found something. Named here so the
#: engine and :mod:`dsr.panel_time.reasons` cannot drift on the spelling.
WIDEN_WINDOW = "widen_window"


class SlotFinder:
    """Find a time that works for a multi-person panel, then book it."""

    def __init__(
        self,
        store: RecordStore,
        *,
        provider_factory: Callable[[Mapping[str, Any]], Any] | None = None,
    ) -> None:
        self.store = store
        self._provider_factory = provider_factory or self._default_provider

    def _default_provider(self, panel_record: Mapping[str, Any]) -> Any:
        """The transport a panel names, over the audited store.

        Read from the record's ``data``, not from the envelope: the panel's
        ``provider`` is a payload key like every other, and reading it off the
        envelope would quietly give every panel the local directory regardless of
        what it asked for.
        """
        data = panel_record.get("data") or {}
        name = str(data.get("provider") or "local")
        if name == "local":
            return LocalDirectory()
        if name == "urllib":
            return UrllibProvider(access_token=data.get("access_token"))
        raise PanelTimeNotConfigured(
            f"provider must be one of {list(PROVIDER_NAMES)}; got {name!r}"
        )

    # -- rooms -------------------------------------------------------------- #

    def require_room(self, room_id: str) -> dict[str, Any]:
        """The room, or a refusal that says which id did not resolve.

        Every room-scoped route starts here, so a typo in a room id is a 404
        naming the room rather than an empty list that looks like a room with
        nothing in it.
        """
        record = self.store.get(str(room_id))
        if record is None or record["collection"] != "room":
            raise NotFound("room", room_id)
        return record

    # -- calendars ---------------------------------------------------------- #

    def list_calendars(
        self,
        *,
        kind: str | None = None,
        provider: str | None = None,
        readable: bool | None = None,
    ) -> list[dict[str, Any]]:
        """Registered calendars, filterable by kind, provider and readability.

        A calendar is *installation-wide*, not room-scoped, because the
        research's step 1 places the find-a-time surface "in a sales room, a CRM
        record, or a scheduling page" - one person's calendar is the same
        calendar in all three.
        """
        where: dict[str, Any] = {}
        if kind:
            where["kind"] = kind
        if provider:
            where["provider"] = provider
        if readable is not None:
            where["readable"] = bool(readable)
        records = (
            self.store.find(CALENDAR_COLLECTION, where, limit=1000)
            if where
            else self.store.list(CALENDAR_COLLECTION, limit=1000)
        )
        return [_calendar_summary(record) for record in records]

    def get_calendar(self, calendar_id: str) -> dict[str, Any]:
        return _calendar_summary(self._live(CALENDAR_COLLECTION, calendar_id, "calendar"))

    def create_calendar(
        self, payload: Mapping[str, Any], *, actor: str | None, source: str
    ) -> dict[str, Any]:
        return _calendar_summary(
            self.store.create(
                CALENDAR_COLLECTION,
                _calendar_payload(payload),
                actor=actor,
                source=source,
            )
        )

    def update_calendar(
        self,
        calendar_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        record = self._live(CALENDAR_COLLECTION, calendar_id, "calendar")
        patch = _calendar_payload(payload, partial=True)
        if not patch:
            return _calendar_summary(record)
        return _calendar_summary(self.store.update(record["id"], patch, actor=actor, source=source))

    def delete_calendar(
        self, calendar_id: str, *, actor: str | None, source: str
    ) -> dict[str, Any]:
        record = self._live(CALENDAR_COLLECTION, calendar_id, "calendar")
        return self.store.delete(record["id"], actor=actor, source=source)

    # -- panels -------------------------------------------------------------- #

    def list_panels(
        self,
        room_id: str,
        *,
        provider: str | None = None,
        room_only: bool | None = None,
    ) -> list[dict[str, Any]]:
        """The panels declared for this room, newest first.

        Filterable on the provider and on ``room_only`` - the last one is the
        useful case for a scheduling page that shares one panel across rooms -
        through the dynamic index, so a new key is filterable the day a
        deployment writes it.
        """
        self.require_room(room_id)
        where: dict[str, Any] = {}
        if provider:
            where["provider"] = provider
        if room_only is not None:
            where["room_only"] = bool(room_only)
        records = (
            self.store.find(PANEL_COLLECTION, where, limit=1000)
            if where
            else self.store.list(PANEL_COLLECTION, room_id=str(room_id), limit=1000)
        )
        return [
            _panel_summary(record)
            for record in records
            if record.get("room_id") in (None, str(room_id))
        ]

    def get_panel(self, room_id: str, panel_id: str) -> dict[str, Any]:
        self.require_room(room_id)
        return _panel_summary(self._panel_record(room_id, panel_id))

    def create_panel(
        self, room_id: str, payload: Mapping[str, Any], *, actor: str | None, source: str
    ) -> dict[str, Any]:
        self.require_room(room_id)
        data = _panel_payload(payload)
        return _panel_summary(
            self.store.create(
                PANEL_COLLECTION, data, room_id=str(room_id), actor=actor, source=source
            )
        )

    def update_panel(
        self,
        room_id: str,
        panel_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        self.require_room(room_id)
        record = self._panel_record(room_id, panel_id)
        patch = _panel_payload(payload, partial=True)
        if not patch:
            return _panel_summary(record)
        return _panel_summary(self.store.update(record["id"], patch, actor=actor, source=source))

    def delete_panel(
        self, room_id: str, panel_id: str, *, actor: str | None, source: str
    ) -> dict[str, Any]:
        self.require_room(room_id)
        record = self._panel_record(room_id, panel_id)
        return self.store.delete(record["id"], actor=actor, source=source)

    # -- the researched search ----------------------------------------------- #

    def find(
        self,
        room_id: str,
        panel_id: str,
        *,
        actor: str | None = None,
        source: str,
        dry_run: bool = False,
        overrides: Mapping[str, Any] | None = None,
        parent_search_id: str | None = None,
        adjustment: str | None = None,
    ) -> dict[str, Any]:
        """The researched find-a-time call, from participants to a ranked shortlist.

        **An empty shortlist is a write and a ``200``, not a ``4xx``.** The
        research's automations line is the reason: ``emptySuggestionsReason`` "is
        documented as the signal to re-call with adjusted parameters", so a caller
        who cannot read that signal cannot act on the one thing the documentation
        tells them to do. A refusal would throw away the shortlist, the reason,
        and the adjustment that would have fixed it, and the room's log would show
        a gap where the interesting thing happened.

        ``dry_run`` computes and returns without writing, for the preview route -
        so "what would this panel find today" costs nothing and leaves no row.

        ``overrides`` are the per-call knobs, and they win over the panel's, which
        is what makes "try a different date range without editing the panel"
        possible. ``parent_search_id`` and ``adjustment`` are set only by a
        retune, and are what makes the researched re-call auditable as *this call
        plus that change*.
        """
        self.require_room(room_id)
        panel_record = self._panel_record(room_id, panel_id)
        data = {**panel_record["data"], **dict(overrides or {})}
        result = self._run(panel_record, data, room_id=room_id)
        if dry_run:
            result.pop("_panel_record", None)
            return result
        return self.store_search(
            room_id,
            panel_record,
            result,
            actor=actor,
            source=source,
            parent_search_id=parent_search_id,
            adjustment=adjustment,
        )

    def _run(
        self,
        panel_record: Mapping[str, Any],
        data: Mapping[str, Any],
        *,
        room_id: str,
    ) -> dict[str, Any]:
        """The pipeline, without touching the store. Raises only for bad input."""
        panel_id = str(panel_record["id"])
        provider_name = str(data.get("provider") or "local")
        if provider_name not in PROVIDER_NAMES:
            raise PanelTimeNotConfigured(
                f"provider must be one of {list(PROVIDER_NAMES)}; got {provider_name!r}"
            )

        constraint = normalise_time_constraint(data.get("time_constraint"))
        duration_text = str(data.get("meeting_duration") or DEFAULT_MEETING_DURATION)
        duration = parse_duration(duration_text, field="meeting_duration")
        interval_text = str(data.get("slot_interval") or DEFAULT_SLOT_INTERVAL)
        parse_duration(interval_text, field="slot_interval")

        threshold = data.get("min_attendee_percentage")
        threshold = 0 if threshold is None else threshold
        if not isinstance(threshold, int) or isinstance(threshold, bool):
            raise ConstraintError("min_attendee_percentage must be an integer")
        if not 0 <= threshold <= 100:
            raise ConstraintError(
                f"min_attendee_percentage must be between 0 and 100; got {threshold}"
            )

        ranker = str(data.get("ranker") or RANK_CONFIDENCE)
        if ranker not in RANKERS:
            raise ConstraintError(f"ranker must be one of {list(RANKERS)}; got {ranker!r}")
        unknown_penalty = data.get("unknown_penalty") or 0

        rules = normalise_house_rules(data.get("house_rules"))
        show_reasons = data.get("return_suggestion_reasons")
        show_reasons = (
            RETURN_SUGGESTION_REASONS_DEFAULT if show_reasons is None else bool(show_reasons)
        )
        time_zone = str(data.get("time_zone") or "UTC")
        clock = Clock.resolve(time_zone)

        # -- step 2: which calendars, and after expanding which groups? ------- #
        calendars = self._resolve_calendars(data)
        if not calendars:
            raise PanelTimeNotConfigured(
                "this installation has no calendars registered, so there is nothing to read. "
                "Register one first - a person, a room, or a distribution list to expand. "
                f"(The calendar collection is {CALENDAR_COLLECTION!r}.)"
            )
        # The whole registry, so a distribution list can expand into a member the
        # panel never named. Without it "invite this group" would quietly mean
        # "invite whichever of its members were already listed by hand".
        registry = {str(r["id"]): r for r in self.store.list(CALENDAR_COLLECTION, limit=1000)}
        expansion = expand_invited(
            calendars,
            index=registry,
            group_expansion_max=int(data.get("group_expansion_max") or GROUP_EXPANSION_MAX_LIMIT),
            calendar_expansion_max=int(
                data.get("calendar_expansion_max") or CALENDAR_EXPANSION_MAX_LIMIT
            ),
        )
        invited = expansion.invited
        by_id = {str(record["id"]): record for record in (*calendars, *registry.values())}

        # -- step 3: the free/busy read -------------------------------------- #
        time_min, time_max = window(constraint)
        provider = self._provider_factory(panel_record)
        availability = provider.send(
            [by_id[calendar_id] for calendar_id in invited],
            time_min=time_min,
            time_max=time_max,
            provider=str(data.get("calendar_provider") or "google"),
        )

        # -- steps 1, 4, 5: enumerate, score, keep, sort --------------------- #
        candidates = enumerate_candidates(
            constraint,
            meeting_duration=format_duration(duration),
            slot_interval=interval_text,
            clock=clock,
        )
        allowed, house_refusals = apply_house_rules(
            candidates, rules, availability, invited, clock=clock
        )
        scored = evaluate(allowed, availability, invited, min_attendee_percentage=threshold)
        ordered = rank(scored, ranker=ranker, unknown_penalty=unknown_penalty)
        passing = [row for row in ordered if row.meets_threshold]
        limit = int(data.get("max_suggestions") or MAX_SUGGESTIONS_LIMIT)
        shortlist = passing[:limit]

        suggestions: list[dict[str, Any]] = []
        for row in shortlist:
            entry = row.describe(return_suggestion_reasons=show_reasons)
            entry["time_zone"] = time_zone
            entry["statuses"] = _statuses(row)
            # The researched toggle's *whole* effect is the key's absence, so the
            # reason is attached only when the toggle is on. Setting it to None
            # instead would leave a null where the research shows no key at all.
            if show_reasons:
                entry["suggestion_reason"] = suggestion_reason(row)
            suggestions.append(entry)

        # -- the researched empty-suggestions path --------------------------- #
        empty_reason: str | None = None
        adjustments: list[dict[str, Any]] = []
        if not suggestions:
            empty_reason = derive_empty_reason(
                candidates=candidates,
                invited=invited,
                organizer_id=self._resolve_organizer(data),
                suggested=shortlist,
                house_rule_refusals=len(house_refusals),
                min_attendee_percentage=threshold,
            )
            adjustments = retune_adjustments(
                empty_reason,
                days=int(data.get("widen_days") or 7),
                min_attendee_percentage=threshold,
            )

        warnings = _warnings(
            clock, expansion, availability, invited, show_reasons, rules, data, by_id
        )

        return {
            "ok": True,
            "room_id": str(room_id),
            "panel_id": panel_id,
            "panel_name": str(data.get("name") or ""),
            "provider": provider_name,
            "calendar_provider": str(data.get("calendar_provider") or "google"),
            "request": render_free_busy_request(
                provider=str(data.get("calendar_provider") or "google"),
                calendar_ids=list(invited),
                time_min=time_min,
                time_max=time_max,
                time_zone=time_zone,
                group_expansion_max=expansion.groups_expansion_max,
                calendar_expansion_max=expansion.calendar_expansion_max,
                meeting_duration=format_duration(duration),
                min_attendee_percentage=threshold,
                return_suggestion_reasons=show_reasons,
                attendees=_attendees(data, by_id, invited),
                time_constraint=constraint,
                location_constraint=data.get("location_constraint"),
                by_user=data.get("graph_user"),
            ),
            "expansion": expansion.describe(),
            "invited": [
                {
                    "id": calendar_id,
                    "email": str(by_id[calendar_id]["data"].get("email") or ""),
                    "name": str(by_id[calendar_id]["data"].get("name") or ""),
                    "kind": str(by_id[calendar_id]["data"].get("kind") or "person"),
                    "readable": not availability.is_unreadable(calendar_id),
                    "unavailable_reason": availability.unreadable.get(calendar_id, ""),
                }
                for calendar_id in invited
            ],
            "time_constraint": constraint,
            "time_window": {
                "time_min": format_instant(time_min),
                "time_max": format_instant(time_max),
            },
            "meeting_duration": format_duration(duration),
            "slot_interval": interval_text,
            "min_attendee_percentage": threshold,
            "return_suggestion_reasons": show_reasons,
            "ranker": ranker,
            "house_rules": rules,
            "counts": {
                "candidates": len(candidates),
                "after_house_rules": len(allowed),
                "house_rule_refusals": len(house_refusals),
                "passing_threshold": len(passing),
                "suggested": len(suggestions),
                "invited": len(invited),
                "unreadable": len([c for c in invited if availability.is_unreadable(c)]),
            },
            "suggestions": suggestions,
            "house_rule_refusals": house_refusals,
            "below_threshold": [
                {
                    **row.candidate.describe(),
                    "confidence": row.confidence,
                    "free_percentage": row.free_percentage,
                    "reason": (
                        f"only {row.free_percentage}% of the invited calendars are free, below "
                        f"the panel's min_attendee_percentage of {threshold}%"
                    ),
                }
                for row in ordered
                if not row.meets_threshold
            ][:limit],
            "empty_suggestions_reason": empty_reason,
            "suggested_adjustments": adjustments,
            "retune_adjustments": [
                {"id": name, "why": ADJUSTMENT_MEANING[name]} for name in RETUNE_ADJUSTMENTS
            ],
            "retunable_parameters": list(RETUNABLE_KEYS),
            "warnings": warnings,
            "time_zone_detail": clock.describe(),
            "availability": availability.describe(),
            "graph_result": describe_search(
                suggestions,
                return_suggestion_reasons=show_reasons,
                empty_reason=empty_reason,
                min_attendee_percentage=threshold,
            ),
            "_panel_record": dict(panel_record),
        }

    def store_search(
        self,
        room_id: str,
        panel_record: Mapping[str, Any],
        result: Mapping[str, Any],
        *,
        actor: str | None,
        source: str,
        parent_search_id: str | None = None,
        adjustment: str | None = None,
    ) -> dict[str, Any]:
        """Persist one find-a-time call, with its provenance."""
        data = {
            "panel_id": str(panel_record["id"]),
            "panel_name": str(panel_record["data"].get("name") or ""),
            "parent_search_id": parent_search_id,
            "adjustment": adjustment,
            "room_only": bool(panel_record["data"].get("room_only")),
            **{
                key: value
                for key, value in result.items()
                if not key.startswith("_") and key not in ("id", "created_at")
            },
        }
        record = self.store.create(
            SEARCH_COLLECTION, data, room_id=str(room_id), actor=actor, source=source
        )
        return {
            **data,
            "id": record["id"],
            "room_id": record["room_id"],
            "created_at": record["created_at"],
        }

    # -- the run log --------------------------------------------------------- #

    def list_searches(
        self,
        room_id: str,
        *,
        panel_id: str | None = None,
        empty: bool | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Every find-a-time call made for this room, newest first.

        An empty result is a row, not a gap: the failure is the thing a rep has
        to read, and the researched re-call means the row is the input to the
        next call. ``empty`` filters on the presence of
        ``emptySuggestionsReason`` - the researched property - so a room can be
        listed showing only the searches that came back with nothing.
        """
        self.require_room(room_id)
        where: dict[str, Any] = {}
        if panel_id:
            where["panel_id"] = panel_id
        records = (
            self.store.find(SEARCH_COLLECTION, where, limit=1000)
            if where
            else self.store.list(SEARCH_COLLECTION, room_id=str(room_id), limit=1000)
        )
        rows = [
            _search_summary(record)
            for record in records
            if record.get("room_id") in (None, str(room_id))
        ]
        if empty is not None:
            rows = [row for row in rows if bool(row.get("empty_suggestions_reason")) is empty]
        return rows[: max(1, min(int(limit), 1000))]

    def get_search(self, room_id: str, search_id: str) -> dict[str, Any]:
        self.require_room(room_id)
        return self._search_record(room_id, search_id)

    # -- the researched re-call ---------------------------------------------- #

    def retune(
        self,
        room_id: str,
        search_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """The researched automation, as a route.

        "Based on this value, you can better adjust the parameters and call
        findMeetingTimes again." So this takes the search that came back empty,
        applies **one** named adjustment to the parameters that produced it, runs
        the call again, and writes a *new* search row linked to the first. The
        original row is untouched: the parameters that produced the empty result
        are the thing a reviewer needs, and an update would overwrite them.
        """
        self.require_room(room_id)
        previous = self._search_record(room_id, search_id)
        empty_reason = previous.get("empty_suggestions_reason")
        offered = (
            [entry["id"] for entry in retune_adjustments(str(empty_reason))]
            if empty_reason
            else [WIDEN_WINDOW]
        )
        requested = payload.get("adjustment")
        if requested is None:
            # The *first* one the reason suggests, in the order
            # ``retune_adjustments`` lists them - not alphabetically. The order is
            # the judgement (widen before relax) and alphabetical order would
            # quietly throw it away. A search that found something has no reason
            # to follow, so it gets the single adjustment that helps in every
            # case.
            chosen = offered[0]
        else:
            chosen = str(requested)
            if chosen not in RETUNE_ADJUSTMENTS:
                raise ConstraintError(
                    f"adjustment must be one of {list(RETUNE_ADJUSTMENTS)}; got {chosen!r}"
                )
            if chosen not in offered:
                if not empty_reason:
                    raise ConstraintError(
                        f"this search returned suggestions, so it has no emptySuggestionsReason "
                        f"to adjust for. The only adjustment a working search takes is "
                        f"{WIDEN_WINDOW!r} - asking for {chosen!r} means you believe the result "
                        "was empty, and it was not."
                    )
                raise ConstraintError(
                    f"adjustment {chosen!r} is not one of the adjustments this "
                    f"emptySuggestionsReason ({empty_reason}) suggests; it offers "
                    f"{offered}. A re-call that adjusts nothing the reason "
                    "names is not a re-call."
                )

        panel_record = self._panel_record(room_id, str(previous["panel_id"]))
        days = payload.get("days")
        parameters = {
            "time_constraint": previous.get("time_constraint"),
            "meeting_duration": previous.get("meeting_duration"),
            "slot_interval": previous.get("slot_interval"),
            "min_attendee_percentage": previous.get("min_attendee_percentage"),
            "house_rules": previous.get("house_rules"),
            "calendars": panel_record["data"].get("calendars"),
        }
        adjusted = apply_adjustment(parameters, chosen, days=int(days) if days is not None else 7)
        if adjusted.pop("invite_organizer", False):
            adjusted["calendars"] = _with_organizer(panel_record["data"])
        data = {**panel_record["data"], **adjusted}
        result = self._run(panel_record, data, room_id=room_id)
        return self.store_search(
            room_id,
            panel_record,
            result,
            actor=actor,
            source=source,
            parent_search_id=search_id,
            adjustment=chosen,
        )

    # -- the researched commit ----------------------------------------------- #

    def book(
        self,
        room_id: str,
        search_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Create the event on the organizer's calendar for a chosen slot.

        [sourced] step 5: "On pick, the app creates the event on the organizer's
        calendar (optionally creating a fresh conference)."

        The three refusals, in the order a caller meets them:

        * the slot is not one this search returned - the flow says the user picks
          one from the list, so an arbitrary instant is a caller bug;
        * the organizer's calendar is now busy, which would create an event on
          top of another one;
        * the slot no longer clears the panel's ``minAttendeePercentage``, which
          is the researched drift note made into a check.

        Availability is re-read here rather than trusted from the search, because
        the research's gap list says the free/busy read is a *pull* with no
        invalidation, and the research itself notes suggestions are "fine-tuned
        from time to time".
        """
        self.require_room(room_id)
        search = self._search_record(room_id, search_id)
        panel_record = self._panel_record(room_id, str(search["panel_id"]))
        panel_data = panel_record["data"]

        wanted = payload.get("start") or payload.get("slot_start")
        if not wanted:
            raise ConstraintError("start is required: the instant of the chosen slot")
        chosen = parse_instant(wanted, field="start")

        known = [entry for entry in search.get("suggestions") or [] if entry.get("start")]
        match = next((entry for entry in known if parse_instant(entry["start"]) == chosen), None)
        if match is None:
            raise SlotUnavailable(
                f"{format_instant(chosen)} is not one of the {len(known)} slot(s) this search "
                f"returned. Run the search again and pick from the list - the researched flow "
                "is 'ranked candidate slots are returned ... the user picks one'."
            )

        create_conference = payload.get("create_conference")
        create_conference = (
            CREATE_CONFERENCE_DEFAULT if create_conference is None else bool(create_conference)
        )
        summary = str(payload.get("summary") or search.get("panel_name") or "Panel meeting")

        # -- the re-read ------------------------------------------------------ #
        organizer = self._resolve_organizer(panel_data)
        if not organizer:
            raise SlotUnavailable(
                "this panel names no organizer, so there is no calendar to create the event on. "
                "The researched step 5 creates the event on the organizer's calendar."
            )
        constraint = search.get("time_constraint") or {}
        time_min, time_max = window(constraint)
        # The window has moved since the search; pad it so a booking just outside
        # the searched slots is still evaluated rather than silently accepted.
        provider = self._provider_factory(panel_record)
        availability = provider.send(
            self._calendars_for(panel_data),
            time_min=min(time_min, chosen),
            time_max=max(time_max, parse_instant(match["end"])),
            provider=str(panel_data.get("calendar_provider") or "google"),
        )
        invited = [entry["id"] for entry in search.get("invited") or []]
        fresh = evaluate(
            [_candidate_of(match)],
            availability,
            invited,
            min_attendee_percentage=int(search.get("min_attendee_percentage") or 0),
        )[0]

        if organizer in fresh.busy:
            raise SlotUnavailable(
                f"the organizer's calendar is now busy at {format_instant(chosen)}. The event "
                "would be created on top of another one, so this booking is refused. Re-run the "
                "search: the research notes the suggestions are fine-tuned from time to time."
            )
        if not fresh.meets_threshold:
            changed = sorted(set(fresh.busy))
            raise SlotUnavailable(
                f"{format_instant(chosen)} no longer clears the panel's "
                f"min_attendee_percentage of {search.get('min_attendee_percentage')}%: "
                f"{fresh.free_percentage}% of the invited calendars are still free. Busy now: "
                f"{', '.join(changed) or 'none'}. Re-run the search."
            )

        # -- the researched commit -------------------------------------------- #
        organizer_record = self._live(CALENDAR_COLLECTION, organizer, "calendar")
        request = render_commit_request(
            provider=str(panel_data.get("calendar_provider") or "google"),
            calendar_id=organizer,
            summary=summary,
            start=chosen,
            end=parse_instant(match["end"]),
            attendees=_attendees(
                panel_data,
                {str(r["id"]): r for r in self._calendars_for(panel_data)},
                invited,
            ),
            location_constraint=panel_data.get("location_constraint"),
            create_conference=create_conference,
            time_zone=str(panel_data.get("time_zone") or "UTC"),
        )
        provider = self._provider_factory(panel_record)
        committed = provider.commit(
            request,
            calendar_id=organizer,
            room_id=str(room_id),
            actor=actor,
            source=source,
        )

        record = self.store.create(
            BOOKING_COLLECTION,
            {
                "panel_id": panel_record["id"],
                "search_id": str(search["id"]),
                "summary": summary,
                "start": format_instant(chosen),
                "end": match["end"],
                "organizer_id": organizer,
                "organizer_email": str(organizer_record["data"].get("email") or ""),
                "time_zone": str(panel_data.get("time_zone") or "UTC"),
                "confidence": int(match.get("confidence") or 0),
                "create_conference": create_conference,
                "conference": committed.get("conference"),
                "event_id": committed.get("event_id"),
                "provider": request.get("provider"),
                "request": request,
                "attendees": _attendees(
                    panel_data,
                    {str(r["id"]): r for r in self._calendars_for(panel_data)},
                    invited,
                ),
                "location_constraint": panel_data.get("location_constraint"),
                "revalidated": {
                    "confidence": fresh.confidence,
                    "free": list(fresh.free),
                    "busy": list(fresh.busy),
                    "unknown": list(fresh.unknown),
                },
            },
            room_id=str(room_id),
            actor=actor,
            source=source,
        )
        return _booking_summary(record)

    def list_bookings(self, room_id: str, *, panel_id: str | None = None) -> list[dict[str, Any]]:
        """Every panel booked for this room, newest first."""
        self.require_room(room_id)
        where: dict[str, Any] = {}
        if panel_id:
            where["panel_id"] = panel_id
        records = (
            self.store.find(BOOKING_COLLECTION, where, limit=1000)
            if where
            else self.store.list(BOOKING_COLLECTION, room_id=str(room_id), limit=1000)
        )
        return [
            _booking_summary(record)
            for record in records
            if record.get("room_id") in (None, str(room_id))
        ]

    def get_booking(self, room_id: str, booking_id: str) -> dict[str, Any]:
        self.require_room(room_id)
        return _booking_summary(self._live(BOOKING_COLLECTION, booking_id, "booking"))

    # -- internals ----------------------------------------------------------- #

    def _resolve_calendars(self, data: Mapping[str, Any]) -> list[dict[str, Any]]:
        """The panel's calendars, in the panel's own order.

        Order matters twice over: the ranking's *chronological* tie-break is
        between equally-confident slots, and a group expands in the position its
        author put it. So this is a list, not a set, and a duplicate address is
        invited once at its first position.
        """
        addresses = data.get("calendars")
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        if isinstance(addresses, (list, tuple)):
            for entry in addresses:
                record = self._calendar_for_entry(entry)
                if record is not None and str(record["id"]) not in seen:
                    seen.add(str(record["id"]))
                    records.append(record)
        elif isinstance(addresses, Mapping):
            for calendar_id in addresses:
                record = self._live(CALENDAR_COLLECTION, calendar_id, "calendar")
                if str(record["id"]) not in seen:
                    seen.add(str(record["id"]))
                    records.append(record)
        organizer = self._resolve_organizer(data)
        if organizer and organizer not in seen:
            found = self._live(CALENDAR_COLLECTION, organizer, "calendar")
            seen.add(str(found["id"]))
            records.insert(0, found)
        return records

    def _resolve_organizer(self, data: Mapping[str, Any]) -> str | None:
        """The organizer's calendar **id**, from whatever the panel named.

        The panel may name the organizer by record id or by email address, and
        both are read here. An email is the friendlier thing to write in a panel
        payload - the research's step 2 says the app "collects the attendees' email
        addresses" - and it is what the demo data uses, so the id form is not the
        only one that can work.
        """
        organizer = data.get("organizer")
        if not organizer:
            return None
        key = str(organizer)
        live = self.store.get(key)
        if live is not None and live["collection"] == CALENDAR_COLLECTION:
            return key
        found = self.store.find(CALENDAR_COLLECTION, {"email": key}, limit=1)
        if found:
            return str(found[0]["id"])
        raise NotFound("calendar", key)

    def _calendar_for_entry(self, entry: object) -> dict[str, Any] | None:
        if isinstance(entry, str):
            address = entry
        elif isinstance(entry, Mapping):
            address = str(entry.get("id") or entry.get("email") or "")
        else:
            address = ""
        if not address:
            return None
        record = self.store.find(CALENDAR_COLLECTION, {"email": address}, limit=1)
        if record:
            return record[0]
        live = self.store.get(address)
        if live is not None and live["collection"] == CALENDAR_COLLECTION:
            return live
        raise NotFound("calendar", address)

    def _calendars_for(self, data: Mapping[str, Any]) -> list[dict[str, Any]]:
        return self._resolve_calendars(data)

    def _live(self, collection: str, record_id: str, resource: str) -> dict[str, Any]:
        record = self.store.get(str(record_id))
        if record is None or record["collection"] != collection:
            raise NotFound(resource, record_id)
        return record

    def _panel_record(self, room_id: str, panel_id: str) -> dict[str, Any]:
        record = self._live(PANEL_COLLECTION, panel_id, "panel")
        if record.get("room_id") not in (None, str(room_id)):
            raise NotFound("panel", panel_id, room_id)
        return record

    def _search_record(self, room_id: str, search_id: str) -> dict[str, Any]:
        record = self._live(SEARCH_COLLECTION, search_id, "search")
        if record.get("room_id") not in (None, str(room_id)):
            raise NotFound("search", search_id, room_id)
        return _search_summary(record)


# --------------------------------------------------------------------------- #
# Payload validation
# --------------------------------------------------------------------------- #


def _calendar_payload(payload: Mapping[str, Any], *, partial: bool = False) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise CalendarShapeError(
            f"a calendar payload must be an object; got {type(payload).__name__}"
        )
    data: dict[str, Any] = {}

    if payload.get("email") is not None or payload.get("name") is not None or not partial:
        email = str(payload.get("email") or "").strip()
        if not partial and not email:
            raise CalendarShapeError(
                "a calendar needs an email; it is what a free/busy read is asked about"
            )
        if email:
            if "@" not in email or email.startswith("@") or email.endswith("@"):
                raise CalendarShapeError(f"email must be an address; got {email!r}")
            data["email"] = email
    if payload.get("name") is not None:
        data["name"] = str(payload["name"])
    if payload.get("kind") is not None:
        kind = str(payload["kind"])
        if kind not in ("person", "room", "group"):
            raise CalendarShapeError(
                f"kind must be one of ['person', 'room', 'group']; got {kind!r}"
            )
        data["kind"] = kind
    if payload.get("provider") is not None:
        provider = str(payload["provider"])
        if provider not in PROVIDERS:
            raise CalendarShapeError(f"provider must be one of {list(PROVIDERS)}; got {provider!r}")
        data["provider"] = provider
    if payload.get("members") is not None:
        members = payload["members"]
        if not isinstance(members, (list, tuple)):
            raise CalendarShapeError("members must be a list of email addresses")
        data["members"] = [str(member) for member in members]
    if payload.get("readable") is not None:
        data["readable"] = bool(payload["readable"])
    if payload.get("unavailable_reason") is not None:
        data["unavailable_reason"] = str(payload["unavailable_reason"])
    if payload.get("busy") is not None:
        from dsr.panel_time.calendar import _busy_blocks

        _busy_blocks(payload["busy"])  # raises CalendarShapeError on a bad interval
        data["busy"] = [
            dict(entry) if isinstance(entry, Mapping) else entry for entry in payload["busy"]
        ]
    if payload.get("time_zone") is not None:
        data["time_zone"] = str(payload["time_zone"])
    if not partial and "kind" not in data:
        data["kind"] = "person"

    extra = sorted(
        set(payload)
        - {
            "email",
            "name",
            "kind",
            "provider",
            "members",
            "readable",
            "unavailable_reason",
            "busy",
            "time_zone",
        }
    )
    for key in extra:
        data[key] = payload[key]
    if not data and partial:
        return {}
    return data


def _panel_payload(payload: Mapping[str, Any], *, partial: bool = False) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise PanelShapeError(f"a panel payload must be an object; got {type(payload).__name__}")
    data: dict[str, Any] = {}

    if payload.get("name") is not None or not partial:
        name = str(payload.get("name") or "").strip()
        if not partial and not name:
            raise PanelShapeError(
                "a panel needs a name; the researched flow starts by picking a set of participants"
            )
        if name:
            data["name"] = name
    if payload.get("provider") is not None:
        provider = str(payload["provider"])
        if provider not in PROVIDER_NAMES:
            raise PanelShapeError(
                f"provider must be one of {list(PROVIDER_NAMES)}; got {provider!r}"
            )
        data["provider"] = provider
    if payload.get("calendar_provider") is not None:
        provider = str(payload["calendar_provider"])
        if provider not in PROVIDERS:
            raise PanelShapeError(
                f"calendar_provider must be one of {list(PROVIDERS)}; got {provider!r}"
            )
        data["calendar_provider"] = provider
    if payload.get("organizer") is not None:
        data["organizer"] = str(payload["organizer"])
    if payload.get("calendars") is not None:
        if not isinstance(payload["calendars"], (list, tuple, Mapping)):
            raise PanelShapeError(
                "calendars must be a list of addresses or an object of per-calendar settings"
            )
        data["calendars"] = payload["calendars"]
    if payload.get("time_constraint") is not None:
        data["time_constraint"] = normalise_time_constraint(payload["time_constraint"])
    if payload.get("meeting_duration") is not None:
        data["meeting_duration"] = format_duration(
            parse_duration(payload["meeting_duration"], field="meeting_duration")
        )
    if payload.get("slot_interval") is not None:
        data["slot_interval"] = format_duration(
            parse_duration(payload["slot_interval"], field="slot_interval")
        )
    if payload.get("min_attendee_percentage") is not None:
        value = payload["min_attendee_percentage"]
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 100:
            raise ConstraintError("min_attendee_percentage must be an integer between 0 and 100")
        data["min_attendee_percentage"] = value
    if payload.get("return_suggestion_reasons") is not None:
        data["return_suggestion_reasons"] = bool(payload["return_suggestion_reasons"])
    if payload.get("ranker") is not None:
        ranker = str(payload["ranker"])
        if ranker not in RANKERS:
            raise PanelShapeError(f"ranker must be one of {list(RANKERS)}; got {ranker!r}")
        data["ranker"] = ranker
    if payload.get("unknown_penalty") is not None:
        penalty = payload["unknown_penalty"]
        if not isinstance(penalty, int) or isinstance(penalty, bool) or penalty < 0:
            raise ConstraintError("unknown_penalty must be a non-negative integer")
        data["unknown_penalty"] = penalty
    if payload.get("house_rules") is not None:
        data["house_rules"] = normalise_house_rules(payload["house_rules"])
    if payload.get("location_constraint") is not None:
        data["location_constraint"] = _location_constraint(payload["location_constraint"])
    if payload.get("time_zone") is not None:
        data["time_zone"] = str(payload["time_zone"])
    for key in ("max_suggestions", "group_expansion_max", "calendar_expansion_max", "widen_days"):
        if payload.get(key) is not None:
            value = payload[key]
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ConstraintError(f"{key} must be a positive integer")
            data[key] = value
    # The two researched capacity knobs are checked at *declaration*, not only at
    # search. A panel that names a knob above its documented maximum could never be
    # searched, and storing it would put a row in the room's list that looks
    # runnable and is not - the same argument as refusing an unknown transport here.
    for key, limit in (
        ("calendar_expansion_max", CALENDAR_EXPANSION_MAX_LIMIT),
        ("group_expansion_max", GROUP_EXPANSION_MAX_LIMIT),
    ):
        if data.get(key) is not None and data[key] > limit:
            raise LimitExceeded(
                f"{key} is {data[key]}; the documented maximum is {limit} "
                f'("{CALENDAR_EXPANSION_MAX_QUOTE}")'
                if key == "calendar_expansion_max"
                else f"{key} is {data[key]}; the documented maximum is {limit}"
            )
    if payload.get("graph_user") is not None:
        data["graph_user"] = str(payload["graph_user"])
    if payload.get("room_only") is not None:
        data["room_only"] = bool(payload["room_only"])
    if payload.get("access_token") is not None:
        # Deliberately *not* stored. An ``access_token`` in the payload would land
        # in ``records.data`` and therefore in the audit row's ``after_state`` and
        # in the JSONL mirror on disk - so the secret would outlive the request by
        # design. The panel records only that one was supplied, and a deployment
        # hands the real one to a provider it replaces on ``provider_factory``,
        # where a credential store can supply it without writing it down.
        data["access_token_present"] = bool(payload["access_token"])
    if not partial and "time_constraint" not in data:
        raise PanelShapeError(
            "a panel needs a time_constraint. The researched flow step 1 is 'picks a set of "
            "participants + a date range', and the date range is what the search enumerates."
        )
    extra = sorted(
        set(payload)
        - {
            "name",
            "provider",
            "calendar_provider",
            "organizer",
            "calendars",
            "time_constraint",
            "meeting_duration",
            "slot_interval",
            "min_attendee_percentage",
            "return_suggestion_reasons",
            "ranker",
            "unknown_penalty",
            "house_rules",
            "location_constraint",
            "time_zone",
            "max_suggestions",
            "group_expansion_max",
            "calendar_expansion_max",
            "widen_days",
            "graph_user",
            "room_only",
            "access_token",
        }
    )
    for key in extra:
        data[key] = payload[key]
    return data


def _location_constraint(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise PanelShapeError(
            f"location_constraint must be an object; got {type(raw).__name__}. The research's "
            'step 2 is "location constraints (room / "suggest a location")".'
        )
    kind = str(raw.get("type") or "suggest")
    if kind not in LOCATION_TYPES:
        raise PanelShapeError(
            f"location_constraint.type must be one of {list(LOCATION_TYPES)}; got {kind!r}. The "
            'research names two: a room, or "suggest a location".'
        )
    data: dict[str, Any] = {"type": kind}
    if raw.get("room_id") is not None:
        data["room_id"] = str(raw["room_id"])
    if raw.get("display_name") is not None:
        data["display_name"] = str(raw["display_name"])
    for key in sorted(set(raw) - {"type", "room_id", "display_name"}):
        data[key] = raw[key]
    return data


# --------------------------------------------------------------------------- #
# Summaries
# --------------------------------------------------------------------------- #


def _with_organizer(data: Mapping[str, Any]) -> Any:
    """The panel's calendar list with the organizer put first.

    First, not last: the researched average is over the invited set, and the
    organizer is the one attendee whose availability the research's step 5
    depends on, so it belongs at the head of the list a reader scans.
    """
    organizer = data.get("organizer")
    if not organizer:
        raise SlotUnavailable(
            "this panel names no organizer, so there is no calendar to create the event on. "
            "Set the panel's organizer, or the researched step 5 cannot complete."
        )
    current = data.get("calendars")
    if isinstance(current, Mapping):
        return {str(organizer): {}, **{k: v for k, v in current.items() if k != str(organizer)}}
    if not isinstance(current, (list, tuple)):
        return [str(organizer)]
    return [str(organizer), *[str(entry) for entry in current if str(entry) != str(organizer)]]


def _attendees(
    data: Mapping[str, Any], by_id: Mapping[str, Any], invited: Sequence[str]
) -> list[dict[str, Any]]:
    """The researched attendee list: an address and whether they are required.

    [sourced] step 2: "The app collects the attendees' email addresses". The
    attendee type is this build's, and it is ``required`` for every invited
    calendar, because the confidence average is over the *invited* set - an
    optional attendee who is busy would otherwise change the score of a slot
    whose attendees are all free.
    """
    entries: list[dict[str, Any]] = []
    for calendar_id in invited:
        record = by_id.get(calendar_id) or {}
        address = str((record.get("data") or {}).get("email") or "")
        if address:
            entries.append(
                {"email": address, "name": str((record.get("data") or {}).get("name") or "")}
            )
    if not entries:
        organizer = data.get("organizer")
        if isinstance(organizer, str) and "@" in organizer:
            entries.append({"email": organizer, "name": ""})
    return entries


def _address_of(by_id: Mapping[str, Any], calendar_id: str) -> str | None:
    record = by_id.get(str(calendar_id))
    if not record:
        return None
    return str((record.get("data") or {}).get("email") or "")


def _statuses(evaluation: Any) -> list[str]:
    """One status per invited calendar, so the graph-shaped result is legible."""
    statuses = (
        ["free"] * len(evaluation.free)
        + ["tentative"] * len(evaluation.unknown)
        + ["none"] * len(evaluation.busy)
    )
    return statuses


def _candidate_of(entry: Mapping[str, Any]) -> Any:
    from dsr.panel_time.slots import Candidate

    return Candidate(
        parse_instant(entry["start"]),
        parse_instant(entry["end"]),
        int(entry.get("slot_index") or 0),
    )


def _warnings(
    clock: Clock,
    expansion: Expansion,
    availability: BusyMap,
    invited: Sequence[str],
    show_reasons: bool,
    rules: Mapping[str, Any],
    data: Mapping[str, Any],
    by_id: Mapping[str, Any],
) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    organizer = data.get("organizer")
    resolved = (
        next(
            (
                calendar_id
                for calendar_id in invited
                if _address_of(by_id, calendar_id) == organizer
            ),
            None,
        )
        if organizer
        else None
    )
    if not organizer or resolved is None:
        # The researched step 5 creates the event *on the organizer's calendar*, so
        # a shortlist nobody can book is worth a shortlist. Warned on every search
        # rather than only on an empty one, because the rep picks the slot before
        # the commit - and by then it is a surprise.
        warnings.append(
            {
                "code": "not_organized_as_attendee",
                "detail": (
                    "this panel names no organizer"
                    if not organizer
                    else f"this panel's organizer ({organizer}) is not among the invited calendars"
                )
                + ", so the researched step 5 has no calendar to create the event on. "
                "The shortlist below is still what the calendars say, but booking it "
                "will be refused until the panel names an organizer that is invited.",
            }
        )
    if not clock.exact:
        warnings.append(
            {
                "code": "time_zone_fallback_to_utc",
                "detail": clock.describe()["note"],  # type: ignore[index]
            }
        )
    for entry in expansion.unexpanded:
        warnings.append(
            {
                "code": f"group_unexpanded:{entry['reason']}",
                "detail": str(entry.get("detail") or ""),
            }
        )
    for calendar_id in invited:
        if availability.is_unreadable(calendar_id):
            warnings.append(
                {
                    "code": "calendar_unreadable",
                    "detail": f"{calendar_id}: {availability.unreadable[calendar_id]} - counted at "
                    "the researched 49% for an unknown status",
                }
            )
    if not show_reasons:
        warnings.append(
            {
                "code": "suggestion_reasons_suppressed",
                "detail": "returnSuggestionReasons is off, so no suggestionReason is returned - "
                "the researched toggle's whole effect is the key's absence",
            }
        )
    if rules.get("extra"):
        warnings.append(
            {
                "code": "house_rules_carried_unrecognised",
                "detail": f"these house rules are carried through untouched and not enforced here: "
                f"{sorted(rules['extra'])}. See the 'house-rule-extra-keys' inference.",
            }
        )
    return warnings


def _calendar_summary(record: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(record.get("data") or {})
    busy = data.get("busy") or []
    readable = data.get("readable", True) and not data.get("unavailable_reason")
    return {
        "id": record.get("id"),
        "room_id": record.get("room_id"),
        "email": data.get("email"),
        "name": data.get("name"),
        "kind": data.get("kind", "person"),
        "provider": data.get("provider"),
        "time_zone": data.get("time_zone"),
        "readable": bool(readable),
        "unavailable_reason": data.get("unavailable_reason", ""),
        "member_count": len(data.get("members") or []),
        "busy_blocks": len(busy) if isinstance(busy, (list, tuple)) else 0,
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
    }


def _panel_summary(record: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(record.get("data") or {})
    addresses = data.get("calendars")
    if isinstance(addresses, Mapping):
        count = len(addresses)
    elif isinstance(addresses, (list, tuple)):
        count = len(addresses)
    else:
        count = 0
    return {
        "id": record.get("id"),
        "room_id": record.get("room_id"),
        "name": data.get("name"),
        "provider": data.get("provider", "local"),
        "calendar_provider": data.get("calendar_provider", "google"),
        "organizer": data.get("organizer"),
        "access_token_present": bool(data.get("access_token_present")),
        "calendar_count": count,
        "meeting_duration": data.get("meeting_duration", DEFAULT_MEETING_DURATION),
        "slot_interval": data.get("slot_interval", DEFAULT_SLOT_INTERVAL),
        "min_attendee_percentage": data.get("min_attendee_percentage", 0),
        "return_suggestion_reasons": data.get(
            "return_suggestion_reasons", RETURN_SUGGESTION_REASONS_DEFAULT
        ),
        "ranker": data.get("ranker", RANK_CONFIDENCE),
        "house_rules": data.get("house_rules") or {},
        "location_constraint": data.get("location_constraint") or {},
        "time_zone": data.get("time_zone", "UTC"),
        "room_only": data.get("room_only", False),
        "time_constraint": data.get("time_constraint") or {},
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
    }


def _search_summary(record: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(record.get("data") or {})
    return {
        "id": record.get("id"),
        "room_id": record.get("room_id"),
        "panel_id": data.get("panel_id"),
        "panel_name": data.get("panel_name"),
        "parent_search_id": data.get("parent_search_id"),
        "adjustment": data.get("adjustment"),
        "provider": data.get("provider"),
        "time_window": data.get("time_window") or {},
        "meeting_duration": data.get("meeting_duration"),
        "min_attendee_percentage": data.get("min_attendee_percentage", 0),
        "return_suggestion_reasons": data.get("return_suggestion_reasons", True),
        "ranker": data.get("ranker", RANK_CONFIDENCE),
        "counts": data.get("counts") or {},
        "suggestions": data.get("suggestions") or [],
        "suggestion_count": len(data.get("suggestions") or []),
        "empty_suggestions_reason": data.get("empty_suggestions_reason"),
        "suggested_adjustments": data.get("suggested_adjustments") or [],
        "retune_adjustments": data.get("retune_adjustments") or [],
        "retunable_parameters": data.get("retunable_parameters") or [],
        "warnings": data.get("warnings") or [],
        "house_rules": data.get("house_rules") or {},
        "time_constraint": data.get("time_constraint") or {},
        "invited": data.get("invited") or [],
        "expansion": data.get("expansion") or {},
        "request": data.get("request") or {},
        "time_zone_detail": data.get("time_zone_detail") or {},
        "graph_result": data.get("graph_result") or {},
        "created_at": record.get("created_at"),
    }


def _booking_summary(record: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(record.get("data") or {})
    return {
        "id": record.get("id"),
        "room_id": record.get("room_id"),
        "panel_id": data.get("panel_id"),
        "search_id": data.get("search_id"),
        "summary": data.get("summary"),
        "start": data.get("start"),
        "end": data.get("end"),
        "time_zone": data.get("time_zone", "UTC"),
        "organizer_id": data.get("organizer_id"),
        "organizer_email": data.get("organizer_email"),
        "event_id": data.get("event_id"),
        "conference": data.get("conference"),
        "create_conference": data.get("create_conference", True),
        "confidence": data.get("confidence"),
        "provider": data.get("provider"),
        "request": data.get("request") or {},
        "attendees": data.get("attendees") or [],
        "location_constraint": data.get("location_constraint") or {},
        "revalidated": data.get("revalidated") or {},
        "created_at": record.get("created_at"),
    }
