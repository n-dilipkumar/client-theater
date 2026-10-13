"""WF-097: the engine that records quote activity and maintains the counters.

Everything here reads or writes a record. The rules it applies live in
:mod:`.rules` and the names it applies them with live in :mod:`.vocabulary`, so a
reviewer can check a rule against the research without reading the write path.

Where the activity records and the counters live was decided by the ticket note
before any of this was written, not here: "This workflow can own its own activity
records and its own counters inside ``records.data``." That becomes two places,
and neither is a migration.

The activity records
    One collection of this workflow's own, ``quote_engagement_event``, with a
    ``quote_id`` on every row. Several pending workflows touch a quote and none
    of them claims the activity log, so rows written here cannot be shaped by
    another feature's rules, and a reader of this timeline knows that every row in
    it came from the vocabulary this module enforces.

The counters
    A nested ``engagement`` block on the quote record itself. The researched
    columns are described as properties of the quote object, and the index page
    reads them as columns on the quote, so they belong on the quote. The block is
    nested rather than flat because two features patching the same quote should
    not have to know each other's key names: each patches its own sub-object and
    neither deletes the other's numbers.

Both are ordinary JSON in ``records.data``. There is no typed column, so a team
adding a field to a quote needs no coordination with anyone.

The counters are recomputed from the whole activity log on every write rather
than incremented, which is the one property here worth reading the reason for:
a counter that increments can disagree with the log it was incremented from, and
nothing in this product reads the log to correct it. A counter that recomputes
cannot disagree with anything.

Every write takes a required ``source``: the route that served the request,
supplied by the HTTP layer. That is what keeps every audit row naming a path the
app actually serves rather than a label written once and never checked.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from dsr.db.audited import utcnow
from dsr.quote_engagement.rules import (
    QuoteEngagementRuleError,
    chart_points,
    counts_as_view,
    event_side,
    iso,
    normalise_event,
    normalise_surface,
    parse_timestamp,
    pipeline_overview,
    read_expiration,
    read_owner,
    read_signing_status,
    read_status,
    recommend_follow_ups,
    require_threshold,
    require_window,
    summarise_events,
)
from dsr.quote_engagement.vocabulary import (
    ACTIVITY_COUNT,
    ACTIVITY_UNCLASSIFIED,
    ALL_EVENTS,
    BUYER_EVENTS,
    CHAT_STARTED,
    COUNTER_LABELS,
    COUNTERS,
    COUNTERS_KEY,
    EMAIL_CHANNEL_CONNECTED,
    EVENT,
    EXPIRATION_FIELDS,
    FOLLOW_UP_REASON_BASIS,
    FOLLOW_UP_REASON_LABELS,
    HIGH_ACTIVITY_THRESHOLD,
    LAST_COMMUNICATION_AT,
    MAX_THRESHOLD,
    MIN_THRESHOLD,
    OWNER_FIELDS,
    QUOTE,
    QUOTE_SENT,
    RESENDABLE_STATES,
    RESENDABLE_STATUSES,
    SELLER_EVENTS,
    SETTINGS,
    SIGNING_STATUS_FIELDS,
    VIEW_COUNT,
)
from dsr.store import RecordStore

#: The follow-up windows a workspace may configure. They are settings with no
#: default number, for the same reason the threshold has none: the research says
#: "no activity in a long time" and "quotes that'll expire soon" and gives no
#: number for either, so a workspace that has not chosen one gets the reason it
#: could not be evaluated rather than a window this workflow invented.
NO_ACTIVITY_AFTER_DAYS = "no_activity_after_days"
EXPIRING_WITHIN_DAYS = "expiring_within_days"

#: Every key the settings record may carry. A workspace that sets one key and not
#: the others gets the ones it set, which is what lets an admin configure only
#: the threshold on a day when they do not care about expiry.
SETTINGS_KEYS = (
    HIGH_ACTIVITY_THRESHOLD,
    EMAIL_CHANNEL_CONNECTED,
    LAST_COMMUNICATION_AT,
    NO_ACTIVITY_AFTER_DAYS,
    EXPIRING_WITHIN_DAYS,
)

#: The two windows, paired with the name each is refused under. The name reaches
#: the caller in the message, so a workspace that typed 0 into the wrong box is
#: told which box.
WINDOW_KEYS = (
    (NO_ACTIVITY_AFTER_DAYS, "no_activity_after_days"),
    (EXPIRING_WITHIN_DAYS, "expiring_within_days"),
)

#: The event a follow-up send records. It is the researched ``Quote sent`` label
#: rather than a label of its own, and it is written through the same path as
#: every other event so the timeline cannot disagree with itself about how many
#: times a quote was sent.
FOLLOW_UP_EVENT = QUOTE_SENT


def _now() -> datetime:
    """The current moment, as the aware datetime the rules take."""
    return parse_timestamp(utcnow()) or datetime.now().astimezone()


class QuoteEngagementService:
    """Record quote activity, maintain the four counters, and drive follow-up.

    Every write method requires ``source``. The rules that decide whether a write
    is allowed are the ones in :mod:`.rules`; this class is only the part that
    reads and writes.
    """

    def __init__(self, store: RecordStore) -> None:
        self.store = store

    # -- settings ----------------------------------------------------------- #

    def settings(self, *, room_id: str | None = None) -> dict[str, Any]:
        """The workspace's analysis settings, with each absent key marked absent.

        "The activity threshold is configurable" and the research gives no
        number, so there is no default here. Every configured key is echoed back
        and every unconfigured one comes back ``None`` and listed under
        ``unconfigured_keys``, so a page can say which figure it cannot show and
        why, rather than rendering a zero that reads like a measurement.
        """
        records = self.store.list(SETTINGS, room_id=room_id, limit=1)
        return self.resolve_settings(records[0].get("data") if records else {})

    @staticmethod
    def resolve_settings(raw: Mapping[str, Any] | None) -> dict[str, Any]:
        """Read a settings payload into the shape the routes and rules expect.

        Pure, and therefore testable without a database. It validates every value
        it reads through the same rule the engine applies, so a settings row
        written directly through the generic records endpoint is held to the same
        bounds as one written through this feature's own route.
        """
        payload = dict(raw or {})
        return {
            HIGH_ACTIVITY_THRESHOLD: require_threshold(payload.get(HIGH_ACTIVITY_THRESHOLD)),
            EMAIL_CHANNEL_CONNECTED: payload.get(EMAIL_CHANNEL_CONNECTED) is True,
            LAST_COMMUNICATION_AT: iso(parse_timestamp(payload.get(LAST_COMMUNICATION_AT))),
            NO_ACTIVITY_AFTER_DAYS: require_window(
                payload.get(NO_ACTIVITY_AFTER_DAYS), NO_ACTIVITY_AFTER_DAYS
            ),
            EXPIRING_WITHIN_DAYS: require_window(
                payload.get(EXPIRING_WITHIN_DAYS), EXPIRING_WITHIN_DAYS
            ),
            "configured_keys": sorted(
                key for key in SETTINGS_KEYS if payload.get(key) not in (None, "")
            ),
            "unconfigured_keys": sorted(
                key for key in SETTINGS_KEYS if payload.get(key) in (None, "")
            ),
        }

    def save_settings(
        self,
        patch: Mapping[str, Any],
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Write the workspace's analysis settings.

        The patch is merged into whatever is already stored rather than replacing
        it, so writing only the threshold leaves an admin's email-channel flag
        alone. Every value is validated before anything is written, so a refused
        settings write leaves the stored row exactly as it was: there is no state
        in which half a setting applied.
        """
        unknown = sorted(set(patch or {}) - set(SETTINGS_KEYS))
        if unknown:
            allowed = ", ".join(SETTINGS_KEYS)
            raise QuoteEngagementRuleError(
                f"unknown analysis setting(s): {', '.join(unknown)}; expected: {allowed}"
            )

        # Validate everything first. A patch that clears a value with null is
        # refused rather than silently dropped, because a caller who sends null
        # to unset a setting deserves to be told the workflow does not unset, not
        # to watch the old value survive in silence.
        for key, name in WINDOW_KEYS:
            if key in patch:
                require_window(patch[key], name)
        if HIGH_ACTIVITY_THRESHOLD in patch:
            require_threshold(patch[HIGH_ACTIVITY_THRESHOLD])
        if EMAIL_CHANNEL_CONNECTED in patch and not isinstance(
            patch[EMAIL_CHANNEL_CONNECTED], bool
        ):
            raise QuoteEngagementRuleError(f"{EMAIL_CHANNEL_CONNECTED} must be true or false")
        if LAST_COMMUNICATION_AT in patch and patch[LAST_COMMUNICATION_AT] is not None:
            if parse_timestamp(patch[LAST_COMMUNICATION_AT]) is None:
                raise QuoteEngagementRuleError(f"{LAST_COMMUNICATION_AT} must be an ISO timestamp")
        for key, value in (patch or {}).items():
            if value is None:
                raise QuoteEngagementRuleError(
                    f"{key} cannot be set to nothing; write a value the rules accept"
                )

        # ``store.update`` merges, so a patch of one key leaves the rest of the
        # row as it was. A workspace with no settings row yet gets one created
        # with exactly the keys it sent.
        records = self.store.list(SETTINGS, room_id=room_id, limit=1)
        if records:
            self.store.update(records[0]["id"], dict(patch), actor=actor, source=source)
        else:
            self.store.create(SETTINGS, dict(patch), room_id=room_id, actor=actor, source=source)
        return self.settings(room_id=room_id)

    # -- quotes ------------------------------------------------------------- #

    def get_quote(self, quote_id: str) -> dict[str, Any] | None:
        """A quote record, or ``None`` when the id is not a quote."""
        record = self.store.get(quote_id)
        if record is None or record["collection"] != QUOTE:
            return None
        return record

    def require_quote(self, quote_id: str) -> dict[str, Any]:
        """A quote record, or a refusal naming the id."""
        record = self.get_quote(quote_id)
        if record is None:
            raise QuoteEngagementRuleError(f"quote {quote_id} not found")
        return record

    def list_quotes(
        self,
        *,
        room_id: str | None = None,
        status: str | None = None,
        activity_class: str | None = None,
        owner: str | None = None,
        signing_status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Quotes with their counters recomputed, narrowed by the index filters.

        "filter by **Status**, **Last activity date**, **Owner**, **Signing
        status**, or **Advanced filters**." Status, owner and signing status are
        taken as arguments. Advanced filters is the generic ``where`` the store
        already offers, served by :meth:`advanced`, so it is not rebuilt here.

        The counters are recomputed from each quote's activity log on read. A
        caller that trusted the stored block alone would see a quote whose log
        grew after the last write carrying a stale Activity Count, and the whole
        point of this workflow is that the count is trustworthy.
        """
        records = self.store.list(QUOTE, room_id=room_id, limit=min(limit, 1000) * 4)
        settings = self.settings(room_id=room_id)
        moment = _now()

        rows: list[dict[str, Any]] = []
        for record in records:
            data = record.get("data") or {}
            rows.append(
                {
                    "id": record["id"],
                    "room_id": record.get("room_id"),
                    "data": data,
                    "engagement": self.counters_for(record, settings=settings, now=moment),
                    "status": read_status(data),
                    "owner": read_owner(data),
                    "signing_status": read_signing_status(data),
                    "expires_at": iso(read_expiration(data)),
                }
            )

        if status:
            wanted = str(status).strip().upper()
            rows = [row for row in rows if row["status"] == wanted]
        if owner:
            rows = [row for row in rows if (row["owner"] or "") == str(owner)]
        if signing_status:
            wanted_text = str(signing_status).casefold()
            rows = [row for row in rows if (row["signing_status"] or "").casefold() == wanted_text]
        if activity_class:
            wanted_class = str(activity_class).casefold()
            rows = [
                row
                for row in rows
                if (row["engagement"].get("activity_class") or "").casefold() == wanted_class
            ]

        rows.sort(
            key=lambda row: str(row["engagement"].get("last_activity_at") or ""), reverse=True
        )
        return rows[:limit]

    def advanced(
        self, raw: Mapping[str, Any], *, room_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Advanced filters, delegated to the store's own JSON-path query.

        "Advanced filters with saved views" is the generic store query: it
        resolves a dotted JSON path through the dynamic index, so a team can
        filter on a field it added itself without this workflow knowing that field
        exists. Rebuilding it here would be a second filter language that drifts
        from the first.

        The room is applied to the rows the index answered for, not inside the
        ``where``. The store resolves every ``where`` key as a path inside the
        record's ``data``, and ``room_id`` is an envelope column rather than a
        data field, so a ``where`` that names it matches no record at all and a
        filter typed in one room would return nothing. The index is therefore
        asked for as many rows as it will give, and the room filter narrows
        them, so a narrow room does not truncate the page the caller asked for.
        """
        filters = dict(raw or {})
        page = max(1, min(int(limit), 1000))
        records = self.store.find(QUOTE, filters, limit=1000 if room_id else page)
        if room_id:
            records = [row for row in records if row.get("room_id") == room_id]
        return records[:page]

    # -- activity ----------------------------------------------------------- #

    def record_event(
        self,
        quote_id: str,
        *,
        event: Any,
        surface: Any = None,
        actor: str | None = None,
        occurred_at: Any = None,
        source: str,
    ) -> dict[str, Any]:
        """Record one activity event and bring the quote's counters up to date.

        "Activity events are written server-side without user action." This is
        that write. The label is checked against the researched vocabulary, the
        surface is checked against the surfaces the rules know, and the counters
        are recomputed from the whole log afterwards.

        ``occurred_at`` is accepted so a caller importing history can backdate an
        event. A timestamp in the future is refused rather than stored: the
        counters compare every event against now, so a future event would make
        "days since last activity" negative on a page that never renders a
        negative age.
        """
        quote = self.require_quote(quote_id)
        label = normalise_event(event)
        where = normalise_surface(surface, label)
        moment = self._event_moment(occurred_at)

        payload = {
            "kind": "quote_engagement_event",
            "quote_id": quote["id"],
            "event": label,
            "side": event_side(label),
            "surface": where,
            "counted_as_view": counts_as_view(label, where),
            "occurred_at": iso(moment),
            "recorded_by": actor,
        }
        row = self.store.create(
            EVENT, payload, room_id=quote.get("room_id"), actor=actor, source=source
        )
        counters = self.refresh_counters(quote, actor=actor, source=source)
        return {"event": row, "engagement": counters}

    def _event_moment(self, occurred_at: Any) -> datetime:
        """When an event happened, refusing a value this workflow cannot read."""
        now = _now()
        if occurred_at in (None, ""):
            return now
        moment = parse_timestamp(occurred_at)
        if moment is None:
            raise QuoteEngagementRuleError("occurred_at must be an ISO timestamp")
        if moment > now:
            raise QuoteEngagementRuleError("occurred_at is in the future")
        return moment

    def events_for(self, quote_id: str, *, limit: int = 1000) -> list[dict[str, Any]]:
        """Every activity record for one quote."""
        return self.store.find(EVENT, {"quote_id": quote_id}, limit=min(limit, 1000))

    def timeline(self, quote_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        """A quote's activity timeline, newest first.

        "Open a single quote > **Preview** > *Quote activities* card in the right
        sidebar; or **Actions > Activity** for the full timeline." Both surfaces
        read this one ordered list, so the sidebar card and the full timeline
        cannot show two different histories of the same quote.
        """
        quote = self.require_quote(quote_id)
        counters = self.counters_for(quote)
        rows = []
        for record in self.events_for(quote["id"]):
            data = record.get("data") or {}
            rows.append(
                {
                    "id": record["id"],
                    "event": data.get("event"),
                    "side": data.get("side"),
                    "surface": data.get("surface"),
                    "counted_as_view": data.get("counted_as_view") is True,
                    "occurred_at": data.get("occurred_at") or record.get("created_at"),
                    "recorded_by": data.get("recorded_by"),
                    "activity_class": counters.get("activity_class"),
                }
            )
        rows.sort(key=lambda row: str(row.get("occurred_at") or ""), reverse=True)
        return rows[:limit]

    # -- counters ----------------------------------------------------------- #

    def counters_for(
        self,
        quote: Mapping[str, Any],
        *,
        settings: Mapping[str, Any] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """The four researched counters for one quote, recomputed from its log.

        The settings come from the quote's own room when the caller does not pass
        them, so two rooms with two different thresholds do not read each other's
        setting.
        """
        resolved = settings if settings is not None else self.settings(room_id=quote.get("room_id"))
        events = [record.get("data") or {} for record in self.events_for(quote["id"])]
        return summarise_events(
            events,
            now=now or _now(),
            threshold=resolved.get(HIGH_ACTIVITY_THRESHOLD),
            email_channel_connected=bool(resolved.get(EMAIL_CHANNEL_CONNECTED)),
            last_communication_at=resolved.get(LAST_COMMUNICATION_AT),
        )

    def refresh_counters(
        self,
        quote: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Recompute a quote's counters and write them onto the quote record.

        The write is a patch, not a replacement: ``store.update`` merges, so the
        quote's own fields and any other feature's block on the same record are
        left exactly as they were.
        """
        counters = self.counters_for(quote)
        self.store.update(quote["id"], {COUNTERS_KEY: counters}, actor=actor, source=source)
        return counters

    def engagement(self, quote_id: str) -> dict[str, Any]:
        """One quote's counters and the facts around them, without writing."""
        quote = self.require_quote(quote_id)
        counters = self.counters_for(quote)
        data = quote.get("data") or {}
        return {
            "quote_id": quote["id"],
            "title": data.get("title") or data.get("hs_title") or quote["id"],
            "status": read_status(data),
            "owner": read_owner(data),
            "signing_status": read_signing_status(data),
            "expires_at": iso(read_expiration(data)),
            "engagement": counters,
            "events": len(self.events_for(quote["id"])),
        }

    # -- dashboard panels --------------------------------------------------- #

    def chart(
        self,
        *,
        room_id: str | None = None,
        include_expired: bool = False,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """The bubble chart's points, plus how many expired quotes it hid."""
        settings = self.settings(room_id=room_id)
        return chart_points(
            self.list_quotes(room_id=room_id, limit=1000),
            now=now,
            threshold=settings.get(HIGH_ACTIVITY_THRESHOLD),
            include_expired=include_expired,
        )

    def overview(
        self, *, room_id: str | None = None, now: datetime | None = None
    ) -> dict[str, Any]:
        """Pipeline overview figures plus the most recently active quotes.

        "Under **Pipeline overview** read the value of high-activity quotes,
        quotes at risk of expiring, and the quote acceptance rate; under **Recent
        quote activity** see name/last activity/amount/expiration."

        The overview and the recent-activity list are one answer because they are
        one panel, and two queries could be taken a second apart and disagree.
        """
        settings = self.settings(room_id=room_id)
        quotes = self.list_quotes(room_id=room_id, limit=1000)
        figures = pipeline_overview(
            quotes,
            now=now or _now(),
            threshold=settings.get(HIGH_ACTIVITY_THRESHOLD),
            expiring_within_days=settings.get(EXPIRING_WITHIN_DAYS),
        )
        recent = sorted(
            quotes,
            key=lambda row: str(row["engagement"].get("last_activity_at") or ""),
            reverse=True,
        )[:10]
        return {
            **figures,
            "settings": settings,
            "recent_activity": [
                {
                    "quote_id": row["id"],
                    "name": row["data"].get("title") or row["id"],
                    "last_activity_at": row["engagement"].get("last_activity_at"),
                    "activity_count": row["engagement"].get(ACTIVITY_COUNT, 0),
                    "view_count": row["engagement"].get(VIEW_COUNT, 0),
                    "amount": row["data"].get("hs_quote_amount")
                    or row["data"].get("locked_amount"),
                    "expires_at": row["expires_at"],
                    "activity_class": row["engagement"].get("activity_class"),
                }
                for row in recent
            ],
        }

    def follow_ups(
        self, *, room_id: str | None = None, limit: int = 50, now: datetime | None = None
    ) -> dict[str, Any]:
        """The Recommended follow-ups panel, with the windows it was measured on."""
        settings = self.settings(room_id=room_id)
        rows = recommend_follow_ups(
            self.list_quotes(room_id=room_id, limit=1000),
            now=now or _now(),
            no_activity_after_days=settings.get(NO_ACTIVITY_AFTER_DAYS),
            expiring_within_days=settings.get(EXPIRING_WITHIN_DAYS),
            email_channel_connected=bool(settings.get(EMAIL_CHANNEL_CONNECTED)),
            last_communication_at=settings.get(LAST_COMMUNICATION_AT),
        )
        return {
            "count": len(rows),
            "entries": rows[:limit],
            "windows": {key: settings.get(key) for key, _ in WINDOW_KEYS},
            "windows_configured": {key: settings.get(key) is not None for key, _ in WINDOW_KEYS},
            "email_channel_connected": bool(settings.get(EMAIL_CHANNEL_CONNECTED)),
        }

    # -- follow-up send ----------------------------------------------------- #

    def send_follow_up(
        self,
        quote_id: str,
        *,
        actor: str | None = None,
        surface: Any = None,
        source: str,
    ) -> dict[str, Any]:
        """Act on a recommendation: record a new **Quote sent** for the quote.

        "clicking **Send a follow up** which opens the **Send quote** dialog" and
        "the seller re-sends the quote email from the recommendation, generating a
        new `Quote sent` activity and (if previously expired/recalled) a new
        send."

        The send is recorded through the same path as every other event, so the
        timeline cannot disagree with itself about how many times the quote was
        sent and the Activity Count goes up by exactly one. The quote's own
        status is not changed: whether an expired quote becomes live again is the
        expiry workflow's decision, not this one's, and a workflow that re-stated
        a quote would be claiming work another workflow owns. The answer says
        instead whether the send renewed an expired or recalled quote, so a caller
        can report that without guessing.

        No message is transmitted. This product has no mail transport, and the
        send in the researched platform is a recorded event. What is recorded is
        the send.
        """
        quote = self.require_quote(quote_id)
        data = quote.get("data") or {}
        status = read_status(data)
        expired = status in RESENDABLE_STATUSES or any(
            label in RESENDABLE_STATES for label in self._event_labels(quote["id"])
        )

        result = self.record_event(
            quote["id"],
            event=FOLLOW_UP_EVENT,
            surface=surface or "email",
            actor=actor,
            source=source,
        )
        return {
            **result,
            "quote_id": quote["id"],
            "renewed": expired,
            "renewed_from": status if status in RESENDABLE_STATUSES else None,
            "opened": "send_quote_dialog",
            "message": (
                "Recorded a new Quote sent. The quote had expired or been recalled, "
                "so this is a new send."
                if expired
                else "Recorded a new Quote sent."
            ),
        }

    def _event_labels(self, quote_id: str) -> list[str]:
        """The labels already recorded against a quote."""
        return [
            str((record.get("data") or {}).get("event") or "")
            for record in self.events_for(quote_id)
        ]

    # -- summary ------------------------------------------------------------ #

    def summary(self, *, room_id: str | None = None, now: datetime | None = None) -> dict[str, Any]:
        """The headline figures, with each one that cannot be computed named."""
        settings = self.settings(room_id=room_id)
        quotes = self.list_quotes(room_id=room_id, limit=1000)
        moment = now or _now()
        follow = self.follow_ups(room_id=room_id, now=moment)
        figures = pipeline_overview(
            quotes,
            now=moment,
            threshold=settings.get(HIGH_ACTIVITY_THRESHOLD),
            expiring_within_days=settings.get(EXPIRING_WITHIN_DAYS),
        )
        return {
            "quotes": len(quotes),
            "activity_events": sum(
                int(row["engagement"].get(ACTIVITY_COUNT) or 0) for row in quotes
            ),
            "view_count": sum(int(row["engagement"].get(VIEW_COUNT) or 0) for row in quotes),
            "counters": list(COUNTERS),
            "high_activity_quotes": figures["high_activity_quotes"],
            "low_activity_quotes": figures["low_activity_quotes"],
            "unclassified_quotes": figures["unclassified_quotes"],
            "quotes_at_risk": figures["quotes_at_risk"],
            "acceptance_rate": figures["acceptance_rate"],
            "follow_ups": follow["count"],
            "settings": settings,
            "unavailable": [
                key
                for key, value in (
                    (HIGH_ACTIVITY_THRESHOLD, settings.get(HIGH_ACTIVITY_THRESHOLD)),
                    *[(key, settings.get(key)) for key, _ in WINDOW_KEYS],
                )
                if value is None
            ],
        }


# --------------------------------------------------------------------------- #
# Derived views
# --------------------------------------------------------------------------- #


def vocabulary() -> dict[str, Any]:
    """Everything the page renders, served from one place on the server.

    The event vocabulary, the four counter labels, the threshold bounds and the
    follow-up reasons are all here, so the panel cannot tell a seller a bound the
    rules do not enforce or offer a label the rules will not accept.
    """
    return {
        "ticket": "WF-097",
        "events": {
            "buyer": list(BUYER_EVENTS),
            "seller": list(SELLER_EVENTS),
            "all": list(ALL_EVENTS),
        },
        "view_event": "Quote viewed",
        "view_surfaces": {
            "quote_link": "Counts as a view",
            "email_pdf_attachment": "Not a view: the buyer opened the PDF in the email",
        },
        "activity_classes": {
            "high": "High activity",
            "low": "Low activity",
            "unclassified": ACTIVITY_UNCLASSIFIED,
        },
        "counters": list(COUNTERS),
        "counter_labels": dict(COUNTER_LABELS),
        "limits": {
            "threshold_min": MIN_THRESHOLD,
            "threshold_max": MAX_THRESHOLD,
            "threshold_default": None,
            "window_min": 1,
            "window_max": 3650,
        },
        "follow_up_reasons": dict(FOLLOW_UP_REASON_LABELS),
        "follow_up_reason_basis": dict(FOLLOW_UP_REASON_BASIS),
        "resendable_states": list(RESENDABLE_STATES),
        "settings_keys": list(SETTINGS_KEYS),
        "collections": {"quote": QUOTE, "event": EVENT, "settings": SETTINGS},
        "read_only_fields": {
            "expiration": list(EXPIRATION_FIELDS),
            "owner": list(OWNER_FIELDS),
            "signing_status": list(SIGNING_STATUS_FIELDS),
        },
        "not_built": [
            "Revenue analytics suite reports",
            "Custom single-object reports",
            "Board-view status chips",
            "Export of published quotes",
            "Email transmission",
        ],
        "chat_started": CHAT_STARTED,
        "chat_started_note": (
            "Chat started is printed inside the seller list in the research and "
            "defined as a buyer action. Its side is recorded as buyer."
        ),
    }


__all__ = (
    "EXPIRING_WITHIN_DAYS",
    "FOLLOW_UP_EVENT",
    "NO_ACTIVITY_AFTER_DAYS",
    "SETTINGS_KEYS",
    "WINDOW_KEYS",
    "QuoteEngagementService",
    "vocabulary",
)
