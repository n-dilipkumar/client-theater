"""WF-098: the writes and the reads. A tracked quote, its reminders, its expiry.

Everything that touches the store lives here. The rules are in
:mod:`dsr.quoting_proposals.quote_expiry_rules`, the vocabulary in
:mod:`dsr.quoting_proposals.quote_expiry_vocabulary`, and neither knows the store
exists.

What this engine owns
---------------------

**The tracked quote.** Its expiration date, its label, its switch, the send and publish
instants, the send count, the acceptance entries and the void and archive flags. That is
the whole question this workflow answers: does this quote close, when, and what reminder
goes out. It never writes the authored quote WF-086 provisions, for the reason recorded
as ``the-quote-record-is-this-workflows-own`` in the inferences module.

**The reminder rules.** One row per rule, because the researched settings screen creates
and deletes them one at a time.

**The reminder ledger.** One row per decision, sent or skipped. A skip is a row for the
same reason a send is: the rules that suppress a reminder are the ones hardest to verify,
and a suppression nobody can see is a suppression nobody can check.

**The activity log.** "Reminder + expiration events are exposed as quote activities that
can drive workflows", so they are rows rather than log lines, and the expiry transition
writes the activity name the research quotes verbatim.

**The settings.** The default expiration window, the account time zone, the reminder send
time and the automated-reminders toggle.

``source`` is required everywhere
--------------------------------

Every writing method takes ``source`` as a keyword, and it is the route that served the
write. No string in this module is a literal path: the feature module builds each one from
its own router, so an audit row names a route the app actually serves. ``source`` is a
required keyword rather than a default of ``None`` precisely so that omitting it is a
``TypeError`` at the call site rather than a quiet row with no route on it.

The order the rules are enforced in
-----------------------------------

Validate before writing. A quote refused for a bad expiration date must leave no trace,
because the audit log is this product's guarantee and a row describing a quote that
changed nothing is a row a reader has to learn to discount.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from dsr.quoting_proposals import quote_expiry_rules as rules, quote_expiry_vocabulary as vocab
from dsr.store import RecordStore

#: The reference this workflow uses to name the record it tracks without writing it.
SOURCE_QUOTE_REF = "source_quote_ref"

#: The reference to the publish and share state WF-094 provisions.
SOURCE_SHARE_REF = "source_share_ref"

#: The gap the research records about the reminder schedule, restated here so a caller
#: reading a dispatch response sees it without opening the vocabulary.
NO_SCHEDULE_WRITE_API = (
    "The reminder schedule has no documented public write API on the pages read. This "
    "product's settings route is the store."
)


class QuoteExpiryEngine:
    """Every write and read this workflow performs, over one audited store.

    ``now`` is a callable rather than a value so a test can move the clock. Every boundary
    in this package - the 1-to-365-day window, the two reminder offsets, the account time
    zone, the deadline itself - is a comparison against this clock, and a test that cannot
    choose the instant cannot test any of them.
    """

    def __init__(self, store: RecordStore, now: Callable[[], datetime] | None = None) -> None:
        self.store = store
        self._now = now or rules.utcnow

    # -- helpers ------------------------------------------------------------ #

    def _moment(self) -> datetime:
        return self._now()

    def _record(self, quote_id: str) -> dict[str, Any]:
        record = self.store.get(quote_id)
        if record is None or record.get("collection") != vocab.QUOTES:
            raise rules.QuoteNotFound(quote_id)
        return record

    def _rule_record(self, rule_id: str) -> dict[str, Any]:
        record = self.store.get(rule_id)
        if record is None or record.get("collection") != vocab.REMINDER_RULES:
            raise rules.ReminderRuleNotFound(rule_id)
        return record

    def _settings_record(self, room_id: str) -> dict[str, Any] | None:
        """The settings for this room, or ``None`` when none were ever written.

        Read through ``find()`` on the room reference rather than by listing the
        collection, so two rooms never share a schedule.
        """

        rows = self.store.find(vocab.SETTINGS, {rules.ROOM_REF: room_id}, limit=10)
        # Sorted here rather than in SQL: `find()` narrows by the dynamic index and does
        # not take an order, and a settings row is written once and read many times, so
        # the oldest is the one that carries the instant the default was turned on.
        return min(rows, key=lambda record: str(record.get("created_at"))) if rows else None

    def _ledger(
        self, quote_id: str | None = None, rule_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Reminder rows, narrowed to a quote and a rule when asked.

        Read with ``find()`` on the two indexed paths rather than by listing the
        collection, because the ledger is the one thing here that grows without bound.
        """

        where: dict[str, Any] = {}
        if quote_id:
            where["quote_id"] = quote_id
        if rule_id:
            where["rule_id"] = rule_id
        rows = self.store.find(vocab.REMINDERS, where, limit=1000)
        return [dict(record.get("data") or {}) for record in rows]

    # -- settings ------------------------------------------------------------ #

    def settings(self, room_id: str | None = None) -> dict[str, Any]:
        """The account settings, with the researched defaults filled in.

        A room with no settings row still answers, and every field it could not find is
        reported in ``stored`` as ``False`` so a reader can tell a default from a value
        somebody set. An account that silently looked configured when it was not is how a
        seller discovers their reminders stopped months later.
        """

        record = self._settings_record(room_id) if room_id else None
        data = dict(record.get("data") or {}) if record else {}
        return {
            "room_id": room_id,
            "id": record.get("id") if record else None,
            "stored": record is not None,
            "updated_at": record.get("updated_at") if record else None,
            vocab.DEFAULT_EXPIRATION_DAYS: rules.coerce_default_days(
                data.get(vocab.DEFAULT_EXPIRATION_DAYS)
            ),
            # The bounds are reported under their own names rather than reused as keys.
            # The two constants are the numbers 1 and 365, so using either as a dict key
            # would put an integer where the response's field names are strings, and a
            # client reading `settings["365"]` learns nothing about what it asked for.
            "min_default_expiration_days": vocab.MIN_DEFAULT_EXPIRATION_DAYS,
            "max_default_expiration_days": vocab.MAX_DEFAULT_EXPIRATION_DAYS,
            vocab.ACCOUNT_TIMEZONE: str(data.get(vocab.ACCOUNT_TIMEZONE) or "UTC"),
            vocab.REMINDER_SEND_TIME: rules.coerce_send_time(data.get(vocab.REMINDER_SEND_TIME)),
            vocab.AUTOMATED_REMINDERS_ENABLED: rules.config_enabled(data),
            "gap": NO_SCHEDULE_WRITE_API,
            "zone_known": rules.zone_resolved(
                rules.zone_for(data.get(vocab.ACCOUNT_TIMEZONE)), data.get(vocab.ACCOUNT_TIMEZONE)
            ),
            "timezone_note": (
                None
                if rules.zone_resolved(
                    rules.zone_for(data.get(vocab.ACCOUNT_TIMEZONE)),
                    data.get(vocab.ACCOUNT_TIMEZONE),
                )
                else vocab.NOTE_UNRESOLVED_TIMEZONE
            ),
        }

    def save_settings(
        self,
        room_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Write the account settings: the default window, the zone, the send time, the toggle.

        "enter a default expiration time period between 1 and 365 days" is validated on
        this route, which is where the research says the number is entered. The bound is
        checked before a row is written, so a rejected setting leaves nothing behind.

        A payload with no ``default_expiration_days`` clears the default. That is a real
        operation rather than an omission, because a cleared default means new quotes stop
        inheriting a window, and it is distinguishable from a payload that never mentioned
        the field.
        """

        body = dict(payload or {})
        existing = self._settings_record(room_id)
        data = dict(existing.get("data") or {}) if existing else {}

        if vocab.DEFAULT_EXPIRATION_DAYS in body:
            data[vocab.DEFAULT_EXPIRATION_DAYS] = rules.coerce_default_days(
                body.get(vocab.DEFAULT_EXPIRATION_DAYS)
            )
            # The instant the default was turned on is what scopes it: "Any new quotes
            # created after the setting is turned on will automatically use the configured
            # expiration date."
            data["default_set_at"] = rules.stamp(self._moment())
        if vocab.ACCOUNT_TIMEZONE in body:
            data[vocab.ACCOUNT_TIMEZONE] = str(body.get(vocab.ACCOUNT_TIMEZONE) or "UTC").strip()
        if vocab.REMINDER_SEND_TIME in body:
            data[vocab.REMINDER_SEND_TIME] = rules.coerce_send_time(
                body.get(vocab.REMINDER_SEND_TIME)
            )
        if vocab.AUTOMATED_REMINDERS_ENABLED in body:
            data[vocab.AUTOMATED_REMINDERS_ENABLED] = rules.coerce_enabled(
                body.get(vocab.AUTOMATED_REMINDERS_ENABLED)
            )

        data[rules.ROOM_REF] = room_id
        data["updated_at"] = rules.stamp(self._moment())

        if existing:
            self.store.update(existing["id"], data, actor=actor, source=source)
            action = "updated"
        else:
            self.store.create(vocab.SETTINGS, data, room_id=room_id, actor=actor, source=source)
            action = "created"

        result = self.settings(room_id)
        result["action"] = action
        return result

    # -- reminder rules ------------------------------------------------------ #

    def add_rule(
        self,
        room_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Add one reminder rule: an offset kind, a number of days, and an optional label.

        "**+ Add reminder** / delete icon to manage several" and "Multiple independent
        reminder rules" both say a rule is its own thing, so a rule is a row. Two rules
        with the same number of days are still two rules and both fire.
        """

        body = dict(payload or {})
        entry = rules.normalise_rule(body)
        if entry is None:
            raise rules.QuoteExpiryRefusal("unknown_offset_kind", "offset_kind")

        data = dict(entry)
        data[rules.ROOM_REF] = room_id
        data["created_at"] = rules.stamp(self._moment())
        data["offset_quote"] = vocab.OFFSET_QUOTES[entry["offset_kind"]]
        record = self.store.create(
            vocab.REMINDER_RULES, data, room_id=room_id, actor=actor, source=source
        )
        return self._project_rule(record)

    def rules_for(self, room_id: str) -> list[dict[str, Any]]:
        """Every reminder rule on this account, oldest first."""

        rows = self.store.find(vocab.REMINDER_RULES, {rules.ROOM_REF: room_id}, limit=200)
        ordered = sorted(rows, key=lambda record: str(record.get("created_at")))
        return [self._project_rule(record) for record in ordered]

    def rule_view(self, room_id: str, rule_id: str) -> dict[str, Any]:
        """One rule, with the reminders it has sent."""

        record = self._rule_record(rule_id)
        view = self._project_rule(record)
        view["sent_count"] = len(self._ledger(rule_id=rule_id))
        return view

    def update_rule(
        self,
        room_id: str,
        rule_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Move a rule's days, its offset kind, its label or its enabled flag.

        A patch, so changing one number does not require resending the whole rule. The
        response says which fields changed, because a rule whose days silently stayed at
        the old value is the failure a seller cannot see.
        """

        record = self._rule_record(rule_id)
        body = dict(payload or {})
        patch: dict[str, Any] = {}

        if "days" in body:
            patch["days"] = rules.coerce_offset_days(body.get("days"))
        if "offset_kind" in body:
            kind = rules.coerce_offset_kind(body.get("offset_kind"))
            patch["offset_kind"] = kind
            patch["offset_quote"] = vocab.OFFSET_QUOTES[kind]
        if "label" in body:
            patch["label"] = str(body.get("label") or "")
        if "enabled" in body:
            patch["enabled"] = bool(body.get("enabled"))
        if not patch:
            return self._project_rule(record)

        updated = self.store.update(record["id"], patch, actor=actor, source=source)
        view = self._project_rule(updated)
        view["changed"] = sorted(patch)
        return view

    def delete_rule(
        self,
        room_id: str,
        rule_id: str,
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Delete one rule, by the id the researched settings screen names.

        The ledger rows the rule produced are kept. They are the record that a reminder
        went out, and a seller asking "did my buyer get that nudge" needs the answer after
        the rule is gone.
        """

        record = self._rule_record(rule_id)
        sent = self._ledger(rule_id=rule_id)
        deleted = self.store.delete(record["id"], actor=actor, source=source)
        return {
            "deleted": True,
            "rule_id": rule_id,
            "label": record.get("data", {}).get("label"),
            "ledger_rows_kept": len(sent),
            "record": dict(deleted.get("data") or {}),
        }

    def _project_rule(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """A stored rule as the API returns it."""

        data = dict(record.get("data") or {})
        kind = str(data.get("offset_kind") or vocab.OFFSET_AFTER_SEND)
        return {
            "id": record.get("id"),
            "room_id": rules.room_ref_of(data, record),
            "offset_kind": kind,
            "offset_quote": vocab.OFFSET_QUOTES.get(kind, kind),
            "offset_label": vocab.OFFSET_LABELS.get(kind, kind),
            "days": data.get("days"),
            "label": data.get("label") or f"{data.get('days')} {vocab.OFFSET_LABELS.get(kind, '')}",
            "enabled": bool(data.get("enabled", True)),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            "revision": record.get("revision"),
        }

    # -- tracked quotes ------------------------------------------------------ #

    def create_quote(
        self,
        room_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Track a quote with an expiration date, a label and a switch.

        Three separate controls, because the researched header module has three:
        "click the **date picker** to set a specific date, edit the **Label**, or toggle
        the **Expiration date** switch off."

        No date and no default is a valid quote that never expires. That is the documented
        behaviour and it is not answered with a substituted date, because a switch a
        seller turned off must not be overridden by a default.

        A quote may arrive already sent or published, because WF-094 provisions those
        events and the offsets count from them.
        """

        now = self._moment()
        body = dict(payload or {})
        settings = self.settings(room_id)

        data: dict[str, Any] = {
            rules.ROOM_REF: room_id,
            "title": str(body.get("title") or f"Quote for room {room_id}"),
            "seller_email": body.get("seller_email"),
            SOURCE_QUOTE_REF: body.get(SOURCE_QUOTE_REF),
            SOURCE_SHARE_REF: body.get(SOURCE_SHARE_REF),
            "recipients": rules.normalise_recipients(body.get("recipients")),
            vocab.EXPIRATION_DATE: _iso_or_none(body.get(vocab.EXPIRATION_DATE)),
            vocab.EXPIRATION_LABEL: str(body.get(vocab.EXPIRATION_LABEL) or ""),
            vocab.EXPIRATION_ENABLED: rules.coerce_enabled(body.get(vocab.EXPIRATION_ENABLED)),
            vocab.EFFECTIVE_DATE: _iso_or_none(body.get(vocab.EFFECTIVE_DATE)),
            vocab.DEFAULT_EXPIRATION_DAYS: settings[vocab.DEFAULT_EXPIRATION_DAYS],
            "default_set_at": None,
            vocab.SENT_AT: _iso_or_none(body.get(vocab.SENT_AT)),
            vocab.PUBLISHED_AT: _iso_or_none(body.get(vocab.PUBLISHED_AT)),
            vocab.SEND_COUNT: rules._counter(body.get(vocab.SEND_COUNT)),  # noqa: SLF001
            vocab.ESIGNATURE_QUOTA_CONSUMED: rules._counter(  # noqa: SLF001
                body.get(vocab.ESIGNATURE_QUOTA_CONSUMED)
            ),
            "acceptances": _normalise_acceptances(body.get("acceptances")),
            vocab.LINK_ACTIVE: bool(body.get(vocab.LINK_ACTIVE, True)),
            vocab.PUBLISHED: bool(body.get(vocab.PUBLISHED, bool(body.get(vocab.PUBLISHED_AT)))),
            vocab.HIDDEN_FROM_INDEX: False,
            vocab.BUYER_ACCESS: True,
            "created_at": rules.stamp(now),
        }
        for key, value in body.items():
            data.setdefault(key, value)

        record = self.store.create(vocab.QUOTES, data, room_id=room_id, actor=actor, source=source)
        return self.project(record, now)

    def quote_view(self, quote_id: str, tz_name: str | None = None) -> dict[str, Any]:
        """One quote as the API returns it, including what expiry would do to it.

        Read on a terminal quote, deliberately: "An expired quote can still be downloaded,
        cloned, voided or archived."
        """

        record = self._record(quote_id)
        return self.project(record, self._moment(), tz_name=tz_name)

    def quotes(
        self,
        room_id: str | None = None,
        *,
        state: str | None = None,
        expiring_within_days: float | None = None,
    ) -> list[dict[str, Any]]:
        """Every tracked quote, newest first, filtered by state and by how soon it expires.

        "status filters for \"expiring soon\"" is a researched surface, so the filter is
        here rather than left to the client to compute from the raw list.
        """

        now = self._moment()
        records = self.store.list(vocab.QUOTES, limit=200, order_by="created_at")
        rows: list[dict[str, Any]] = []
        for record in records:
            data = dict(record.get("data") or {})
            if room_id and rules.room_ref_of(data, record) != room_id:
                continue
            view = self.project(record, now)
            if state and view["state"] != state:
                continue
            if expiring_within_days is not None and not view["expiring_soon"]:
                continue
            rows.append(view)
        return rows

    def project(
        self,
        record: Mapping[str, Any],
        now: datetime | None = None,
        *,
        tz_name: str | None = None,
    ) -> dict[str, Any]:
        """A stored quote as the API returns it.

        The projection carries the invariants on every response, so a row read out of a
        list, a log export or a test says what expiry is without the reader having to find
        this module.
        """

        moment = now or self._moment()
        data = dict(record.get("data") or {})
        resolved = rules.resolve_expiration(data, moment)
        state = rules.quote_state(data, moment)
        remaining = rules.seconds_remaining(data, moment)
        days_left = rules.days_remaining(data, moment)
        due = rules.reminders_due(
            self._rules_for_room(rules.room_ref_of(data, record)),
            {**data, "id": record.get("id")},
            self.settings(rules.room_ref_of(data, record)),
            moment,
        )

        payload: dict[str, Any] = {
            "id": record.get("id"),
            "room_id": rules.room_ref_of(data, record),
            "title": data.get("title"),
            "seller_email": data.get("seller_email"),
            SOURCE_QUOTE_REF: data.get(SOURCE_QUOTE_REF),
            SOURCE_SHARE_REF: data.get(SOURCE_SHARE_REF),
            "recipients": data.get("recipients") or [],
            "state": state,
            "state_label": vocab.QUOTE_STATE_LABELS.get(state, state),
            "terminal": state in vocab.TERMINAL_QUOTE_STATES,
            vocab.EXPIRATION_DATE: resolved["expires_at"],
            "expiration_date_only": resolved["iso_date"],
            "expiration_label": data.get(vocab.EXPIRATION_LABEL) or "",
            "expiration_enabled": resolved["enabled"],
            "expiration_source": resolved["source"],
            "expiration_source_label": vocab.EXPIRY_SOURCE_LABELS.get(resolved["source"], ""),
            "expiration_window_days": resolved["window_days"],
            "sign_by_deadline": resolved["sign_by_deadline"],
            vocab.EFFECTIVE_DATE: data.get(vocab.EFFECTIVE_DATE),
            "days_remaining": days_left,
            "seconds_remaining": remaining,
            "expiring_soon": _expiring_soon(days_left),
            "survives_expiry": rules.survives_expiry(data, moment),
            "acceptances": data.get("acceptances") or [],
            vocab.SENT_AT: data.get(vocab.SENT_AT),
            vocab.PUBLISHED_AT: data.get(vocab.PUBLISHED_AT),
            vocab.SEND_COUNT: rules._counter(data.get(vocab.SEND_COUNT)),  # noqa: SLF001
            vocab.ESIGNATURE_QUOTA_CONSUMED: rules._counter(  # noqa: SLF001
                data.get(vocab.ESIGNATURE_QUOTA_CONSUMED)
            ),
            vocab.LINK_ACTIVE: bool(data.get(vocab.LINK_ACTIVE, True)),
            vocab.PUBLISHED: bool(data.get(vocab.PUBLISHED, False)),
            vocab.HIDDEN_FROM_INDEX: bool(data.get(vocab.HIDDEN_FROM_INDEX, False)),
            vocab.BUYER_ACCESS: bool(data.get(vocab.BUYER_ACCESS, True)),
            "voided_at": data.get("voided_at"),
            "archived_at": data.get("archived_at"),
            "reminders_due": due,
            "reminder_count": len(due),
            "can_accept": state not in (vocab.QUOTE_EXPIRED, vocab.QUOTE_ARCHIVED)
            and bool(data.get(vocab.BUYER_ACCESS, True)),
            "survivable_actions": list(vocab.SURVIVABLE_ACTIONS),
            "created_at": data.get("created_at"),
            "updated_at": record.get("updated_at"),
            "revision": record.get("revision"),
            "invariants": {
                "expiry_survives": vocab.EXPIRY_SURVIVES_QUOTE,
                "acceptance_closed": vocab.ACCEPTANCE_CLOSED_QUOTE,
                "survival": vocab.SURVIVAL_QUOTE,
                "resend_is_a_new_send": vocab.RESEND_QUOTE,
            },
        }
        if resolved["expires_at"]:
            payload["expiration_view"] = rules.local_reading(
                rules.coerce_instant(resolved["expires_at"], vocab.EXPIRATION_DATE),  # type: ignore[arg-type]
                tz_name or self.settings(rules.room_ref_of(data, record))[vocab.ACCOUNT_TIMEZONE],
            )
        return payload

    def _rules_for_room(self, room_id: Any) -> list[dict[str, Any]]:
        """The reminder rules for a room, as plain dicts the rules module can read.

        A rule read back from the store carries its record id, which is what the ledger
        keys on, so the id is preserved rather than dropped.
        """

        if not room_id:
            return []
        rows = self.store.find(vocab.REMINDER_RULES, {rules.ROOM_REF: room_id}, limit=200)
        ordered = sorted(rows, key=lambda record: str(record.get("created_at")))
        return [dict({"id": record.get("id")}, **(record.get("data") or {})) for record in ordered]

    def set_expiration(
        self,
        quote_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Set the date, edit the label, or turn the switch off.

        Three controls, three fields, and each is recognised by its own key so a caller
        that only sends ``expiration_label`` does not clear the date by omission. An
        explicit ``null`` on the date clears it, which is a real operation rather than an
        omission.

        Refused with 409 once the quote is expired, voided or archived. The expiration
        date of a quote that already met its deadline cannot be moved, or the Expired state
        would be terminal in name only. Accepted and signed quotes may still have their
        date moved, because the research's survival rule is about the buyer's action and
        not about whether a seller can see the deadline they were given.
        """

        record = self._record(quote_id)
        now = self._moment()
        data = dict(record.get("data") or {})
        body = dict(payload or {})
        state = rules.quote_state(data, now)
        if state in (vocab.QUOTE_EXPIRED, vocab.QUOTE_VOIDED, vocab.QUOTE_ARCHIVED):
            raise rules.QuoteNotEditable(state, "the quote has already met its deadline")

        patch: dict[str, Any] = {}
        touched: list[str] = []

        if vocab.EXPIRATION_DATE in body:
            patch[vocab.EXPIRATION_DATE] = _iso_or_none(body.get(vocab.EXPIRATION_DATE))
            patch["expiration_touched_at"] = rules.stamp(now)
            touched.append(vocab.EXPIRATION_DATE)
        if vocab.EXPIRATION_LABEL in body:
            patch[vocab.EXPIRATION_LABEL] = str(body.get(vocab.EXPIRATION_LABEL) or "")
            touched.append(vocab.EXPIRATION_LABEL)
        if vocab.EXPIRATION_ENABLED in body:
            enabled = rules.coerce_enabled(body.get(vocab.EXPIRATION_ENABLED))
            patch[vocab.EXPIRATION_ENABLED] = enabled
            touched.append(vocab.EXPIRATION_ENABLED)
            if not enabled:
                self._activity(record, data, vocab.ACTIVITY_EXPIRATION_OFF, now, {}, actor, source)
            elif vocab.EXPIRATION_DATE in patch:
                self._activity(
                    record,
                    data,
                    vocab.ACTIVITY_EXPIRATION_SET,
                    now,
                    {"date": patch[vocab.EXPIRATION_DATE]},
                    actor,
                    source,
                )
        if not touched:
            return self.project(record, now)

        updated = self.store.update(quote_id, patch, actor=actor, source=source)
        view = self.project(updated, now)
        view["changed"] = touched
        return view

    # -- sending -------------------------------------------------------------- #

    def send_quote(
        self,
        quote_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Send or publish the quote, and count the send.

        "resending counts as a new send (consuming e-signature quota again)". The count
        and the quota are separate fields and both go up, so a resend is visibly a new send
        and not a touch.

        The send instant moves, and the "days after sending quote" rules count from it. A
        "days before expiration date" rule does not move, because it counts from the
        expiration date and the resend does not change it.
        """

        record = self._record(quote_id)
        now = self._moment()
        data = dict(record.get("data") or {})
        body = dict(payload or {})

        # Read strictly: a send is a publish only when the caller says so. An omitted flag
        # means "not published", because the default reading of an absent boolean here
        # would publish every send and quietly turn a sent quote into a hosted link.
        publish = rules.coerce_enabled(body["publish"]) if "publish" in body else False
        send = rules.next_send(data, now)

        patch: dict[str, Any] = {
            vocab.SEND_COUNT: send[vocab.SEND_COUNT],
            vocab.ESIGNATURE_QUOTA_CONSUMED: send[vocab.ESIGNATURE_QUOTA_CONSUMED],
            vocab.SENT_AT: send[vocab.SENT_AT],
            "last_resent": bool(send["resent"]),
        }
        activity = vocab.ACTIVITY_SENT
        if publish:
            patch[vocab.PUBLISHED_AT] = send[vocab.SENT_AT]
            patch[vocab.PUBLISHED] = True
            activity = vocab.ACTIVITY_PUBLISHED

        updated = self.store.update(quote_id, patch, actor=actor, source=source)
        self._activity(
            updated,
            data,
            activity,
            now,
            {
                vocab.SEND_COUNT: send[vocab.SEND_COUNT],
                vocab.ESIGNATURE_QUOTA_CONSUMED: send[vocab.ESIGNATURE_QUOTA_CONSUMED],
                "resent": bool(send["resent"]),
            },
            actor,
            source,
        )
        view = self.project(updated, now)
        view["send"] = send
        return view

    # -- acceptance ------------------------------------------------------------ #

    def record_acceptance(
        self,
        quote_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Record what the buyer did, and when.

        "if the buyer has not accepted/e-signed/marked-signed by the expiration date". The
        action, the method and the instant are all stored, because the survival rule reads
        the instant and the per-method rule reads the method.

        Refused on an expired quote. "the buyer loses the ability to accept" is the one
        thing expiry takes away, and it is the reason this route exists at all.

        The refusal reads the derived state and not the stored ``expired_at`` flag, so it
        does not depend on somebody having run the expiry check first. A quote whose
        deadline has passed is expired whether or not the job has been called, and letting
        a buyer accept one in that window would be a hole in the rule the check enforces.

        A quote that already has a surviving action is recorded as a second entry rather
        than refused: a buyer who signs and then the seller marks it signed has done both,
        and the ledger should show both. What it will not do is overwrite the first entry,
        because the first entry's instant is the one the survival rule reads.
        """

        record = self._record(quote_id)
        now = self._moment()
        data = dict(record.get("data") or {})
        body = dict(payload or {})

        state = rules.quote_state(data, now)
        if state == vocab.QUOTE_EXPIRED:
            raise rules.QuoteExpiryRefusal("quote_acceptance_closed")
        if state in (vocab.QUOTE_VOIDED, vocab.QUOTE_ARCHIVED):
            # Void "will deactivate" the link URL and Archive "prevents buyers from
            # accessing" the quote, so a buyer cannot reach this route on either one. The
            # two are refused with the same code because they are the same outcome from
            # the buyer's side: there is no link to follow.
            raise rules.QuoteExpiryRefusal("quote_acceptance_closed")
        if not bool(data.get(vocab.BUYER_ACCESS, True)) or not bool(
            data.get(vocab.LINK_ACTIVE, True)
        ):
            raise rules.QuoteExpiryRefusal("quote_acceptance_closed")

        action = rules.coerce_buyer_action(body.get("action"))
        method = rules.coerce_acceptance_method(body.get("method"))
        at = rules.coerce_instant(body.get("at"), "at") or now

        entries = _normalise_acceptances(data.get("acceptances"))
        entries.append(
            {
                "action": action,
                "method": method,
                "at": rules.stamp(at),
                "actor": body.get("actor") or actor,
                "by_deadline": _acted_by_deadline(data, action, at, now),
            }
        )
        patch: dict[str, Any] = {
            "acceptances": entries,
            "accepted_at": entries[-1]["at"],
            "acceptance_method": method,
        }
        updated = self.store.update(quote_id, patch, actor=actor, source=source)
        self._activity(
            updated,
            data,
            vocab.ACTIVITY_ACCEPTED if action == vocab.ACTION_ACCEPTED else vocab.ACTIVITY_SIGNED,
            now,
            {"action": action, "method": method, "at": patch["accepted_at"]},
            actor,
            source,
        )
        view = self.project(updated, now)
        view["acceptance"] = entries[-1]
        view["survives_expiry"] = rules.survives_expiry(dict(updated.get("data") or {}), now)
        return view

    def can_accept(self, quote_id: str) -> dict[str, Any]:
        """May this buyer accept right now, and why not if not.

        A ``GET`` because it decides nothing and writes nothing. A buyer who arrives early
        is not refused, so the answer is a report, and it names the reason the refusal
        would.
        """

        record = self._record(quote_id)
        now = self._moment()
        data = dict(record.get("data") or {})
        state = rules.quote_state(data, now)

        can = True
        reason = "open"
        if state == vocab.QUOTE_EXPIRED:
            can, reason = False, "quote_expired"
        elif state == vocab.QUOTE_ARCHIVED:
            can, reason = False, "quote_archived"
        elif state == vocab.QUOTE_VOIDED:
            can, reason = False, "quote_voided"
        elif not bool(data.get(vocab.BUYER_ACCESS, True)):
            can, reason = False, "buyer_access_blocked"
        elif not bool(data.get(vocab.LINK_ACTIVE, True)):
            can, reason = False, "link_inactive"
        elif state in (vocab.QUOTE_ACCEPTED, vocab.QUOTE_SIGNED):
            can, reason = False, "already_acted"

        return {
            "quote_id": quote_id,
            "state": state,
            "can_accept": can,
            "reason": reason,
            "link_active": bool(data.get(vocab.LINK_ACTIVE, True)),
            "buyer_access": bool(data.get(vocab.BUYER_ACCESS, True)),
            "expiration_date": rules.resolve_expiration(data, now)["iso_date"],
            "sign_by_deadline": rules.is_sign_by_deadline(data, now),
            "note": vocab.ACCEPTANCE_CLOSED_QUOTE,
        }

    # -- void and archive ------------------------------------------------------ #

    def void_quote(
        self,
        quote_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Deactivate the quote link URL, and nothing else.

        "**Void:** ... The quote link URL will be deactivated." One consequence and one
        only, so one field moves. The quote itself stays readable and downloadable, which
        is what the researched Void entry says and what the expired-quote rule says about
        the other actions.
        """

        record = self._record(quote_id)
        now = self._moment()
        effects = rules.void_effects()
        patch: dict[str, Any] = {
            vocab.LINK_ACTIVE: effects[vocab.LINK_ACTIVE],
            "voided_at": rules.stamp(now),
        }
        updated = self.store.update(quote_id, patch, actor=actor, source=source)
        self._activity(
            updated,
            dict(record.get("data") or {}),
            vocab.ACTIVITY_VOIDED,
            now,
            dict(payload or {}),
            actor,
            source,
        )
        view = self.project(updated, now)
        view["voided"] = effects
        return view

    def archive_quote(
        self,
        quote_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Unpublish the quote, hide it from the index, and block buyer access.

        "**Archive:** ... The quote is unpublished, hidden from the default index page
        view, and prevents buyers from accessing it." Three consequences and three fields,
        because they are separately observable.
        """

        record = self._record(quote_id)
        now = self._moment()
        effects = rules.archive_effects()
        patch: dict[str, Any] = {
            vocab.PUBLISHED: effects[vocab.PUBLISHED],
            vocab.HIDDEN_FROM_INDEX: effects[vocab.HIDDEN_FROM_INDEX],
            vocab.BUYER_ACCESS: effects[vocab.BUYER_ACCESS],
            "archived_at": rules.stamp(now),
        }
        updated = self.store.update(quote_id, patch, actor=actor, source=source)
        self._activity(
            updated,
            dict(record.get("data") or {}),
            vocab.ACTIVITY_ARCHIVED,
            now,
            dict(payload or {}),
            actor,
            source,
        )
        view = self.project(updated, now)
        view["archived"] = effects
        return view

    # -- the reminder dispatch -------------------------------------------------- #

    def dispatch_reminders(
        self,
        room_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Evaluate every due rule for every quote in this room, and record every decision.

        "a scheduled job evaluates sent/published quotes relative to that date and
        dispatches reminder emails at the configured offsets". Both offsets, every rule,
        every quote.

        Sends and skips are both rows. A skip is recorded for the same reason a send is:
        each of the researched suppression rules is easy to get wrong, and a skip that
        leaves no trace is a skip nobody can verify happened at all.

        The dispatch names no delivery. It records the decision, the instant the rule was
        due and the recipients it covered, and reports that this product sends no message.
        """

        now = self._moment()
        body = dict(payload or {})
        settings = self.settings(room_id)
        rule_rows = self._rules_for_room(room_id)
        quotes = self.quotes(room_id)

        only = str(body.get("quote_id") or "").strip()
        if only:
            quotes = [row for row in quotes if row["id"] == only]
        rule_filter = str(body.get("rule_id") or "").strip()

        decisions: list[dict[str, Any]] = []
        for quote in quotes:
            for rule in rule_rows:
                if rule_filter and str(rule.get("id")) != rule_filter:
                    continue
                decisions.append(
                    self._dispatch_one(quote, rule, settings, now, actor=actor, source=source)
                )

        return {
            "room_id": room_id,
            "at": rules.stamp(now),
            "quotes_evaluated": len(quotes),
            "rules_evaluated": len(rule_rows),
            "decisions": decisions,
            "sent": sum(1 for row in decisions if row["outcome"] == vocab.OUTCOME_SENT),
            "skipped": sum(1 for row in decisions if row["outcome"] == vocab.OUTCOME_SKIPPED),
            "delivery": {
                "channel": "email",
                "sent_by_this_product": False,
                "note": (
                    "This product records the reminder decision and sends no message. An "
                    "integration consumes this ledger and delivers."
                ),
            },
            "gap": NO_SCHEDULE_WRITE_API,
            "settings": {
                "account_timezone": settings[vocab.ACCOUNT_TIMEZONE],
                "reminder_send_time": settings[vocab.REMINDER_SEND_TIME],
                "automated_reminders_enabled": settings[vocab.AUTOMATED_REMINDERS_ENABLED],
            },
        }

    def _dispatch_one(
        self,
        quote: Mapping[str, Any],
        rule: Mapping[str, Any],
        settings: Mapping[str, Any],
        now: datetime,
        *,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Evaluate one rule against one quote, and write the decision it reaches."""

        quote_id = quote.get("id")
        rule_id = rule.get("id")
        ledger = self._ledger(quote_id=quote_id, rule_id=rule_id)
        # The stored data, not the projection, because the rules read fields the projection
        # renames and a decision made on a renamed field is a decision nothing enforces.
        stored = self._record(str(quote_id))
        # The record id travels with the data because two rules read it off the quote: the
        # "has this rule already fired" check keys on it, and a quote with no id in hand
        # silently passes every dedupe and sends the same reminder on every pass.
        stored_data = dict(stored.get("data") or {}, id=stored.get("id"))

        reason = rules.skip_reason_for(rule, stored_data, settings, ledger, now)
        plan = rules.rule_due_at(rule, stored_data, settings, now)

        if reason:
            outcome = vocab.OUTCOME_SKIPPED
            detail = vocab.SKIP_REASON_TEXT.get(reason, reason)
        elif not rules.rule_is_due(rule, stored.get("data") or {}, settings, now):
            outcome = vocab.OUTCOME_SKIPPED
            reason = "not_yet_due"
            detail = f"This rule fires at {plan['due_at']}."
        else:
            outcome = vocab.OUTCOME_SENT
            reason = "window_open"
            detail = f"This rule was due at {plan['due_at']}."

        recipients = list(stored.get("data", {}).get("recipients") or [])
        row = {
            rules.ROOM_REF: quote.get("room_id"),
            "quote_id": quote_id,
            "rule_id": rule_id,
            "rule_label": rule.get("label"),
            "offset_kind": plan["offset_kind"],
            "offset_days": plan["offset_days"],
            "due_at": plan["due_at"],
            "account_timezone": plan["account_timezone"],
            "reminder_send_time": plan["send_time"],
            "outcome": outcome,
            "reason": reason,
            "detail": detail,
            "recipients": [entry.get("email") for entry in recipients],
            "delivered": False,
            "at": rules.stamp(now),
        }
        created = self.store.create(
            vocab.REMINDERS, row, room_id=quote.get("room_id"), actor=actor, source=source
        )
        if outcome == vocab.OUTCOME_SENT:
            self._activity(
                stored,
                stored.get("data") or {},
                vocab.ACTIVITY_REMINDER_SENT,
                now,
                {
                    "rule_id": rule_id,
                    "offset_kind": plan["offset_kind"],
                    "offset_days": plan["offset_days"],
                    "recipients": row["recipients"],
                },
                actor,
                source,
            )
        return dict(created.get("data") or {}, id=created.get("id"))

    def reminders(
        self,
        room_id: str | None = None,
        quote_id: str | None = None,
        rule_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """The reminder ledger, newest first. Sends and skips alike."""

        rows = self._ledger(quote_id=quote_id, rule_id=rule_id)
        if room_id:
            rows = [row for row in rows if row.get(rules.ROOM_REF) == room_id]
        projected = [dict(row) for row in rows]
        return sorted(projected, key=lambda row: str(row.get("at")), reverse=True)

    def reminder_preview(
        self,
        room_id: str,
        rule_id: str,
        quote_id: str | None = None,
    ) -> dict[str, Any]:
        """The reminder a rule would send, without sending it.

        "**Preview reminder email**" is one of the researched product surfaces, so it is a
        route. Without a quote id the first quote this room has is used, because the
        settings screen previews against whatever is on screen.
        """

        record = self._rule_record(rule_id)
        rule = dict({"id": record.get("id")}, **(record.get("data") or {}))
        settings = self.settings(room_id)
        if quote_id:
            quote_record = self._record(quote_id)
        else:
            quotes = self.quotes(room_id)
            if not quotes:
                raise rules.QuoteNotFound(f"no quote in room {room_id}")
            quote_record = self._record(str(quotes[0]["id"]))
        return rules.preview_reminder(
            rule, dict(quote_record.get("data") or {}, id=quote_record.get("id")), settings
        )

    # -- the expiry check ------------------------------------------------------- #

    def check_expiry(
        self,
        room_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Expire every quote in this room whose deadline has passed and whose buyer has not acted.

        "If a buyer hasn't accepted or signed a quote by the expiration date, it'll
        expire."

        Five things it deliberately does not do, each a researched sentence:

        * It does not delete anything. "expired quotes can still be downloaded, cloned,
          voided or archived."
        * It does not touch a quote whose expiration switch is off.
        * It does not touch a quote already terminal, because the check has to be safe to
          run twice: an hour of downtime is a job nobody ran, and a second pass would
          write a second ``Quote expired`` activity for one deadline. An integration that
          counted those would count a buyer being told twice.
        * It does not touch a quote that is not yet past its deadline.
        * It does not touch a quote the buyer accepted or signed in time, because "If a
          quote is accepted or signed before the expiration date, but hasn't been
          countersigned or paid, the quote won't expire."
        """

        now = self._moment()
        body = dict(payload or {})
        only = str(body.get("quote_id") or "").strip()

        candidates = self.quotes(room_id)
        expired: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []

        for quote in candidates:
            if only and quote["id"] != only:
                continue
            record = self._record(str(quote["id"]))
            data = dict(record.get("data") or {})
            reason = rules.expiry_skip_reason(data, now)
            if reason:
                skipped.append(
                    {
                        "quote_id": quote["id"],
                        "title": quote["title"],
                        "reason": reason,
                        "state": quote["state"],
                        "expiration_date": quote["expiration_date_only"],
                    }
                )
                continue

            note = rules.audit_note(data, now)
            updated = self.store.update(
                str(quote["id"]),
                {
                    "expired_at": rules.stamp(now),
                    "acceptance_closed": True,
                    "expiry_note": note,
                },
                actor=actor,
                source=source,
            )
            self._activity(
                updated,
                data,
                vocab.ACTIVITY_EXPIRED,
                now,
                {
                    "expiration_date": quote["expiration_date_only"],
                    "sign_by_deadline": quote["sign_by_deadline"],
                    "note": note,
                },
                actor,
                source,
            )
            expired.append(
                {
                    "quote_id": quote["id"],
                    "title": quote["title"],
                    "state": vocab.QUOTE_EXPIRED,
                    "expiration_date": quote["expiration_date_only"],
                    "sign_by_deadline": quote["sign_by_deadline"],
                    "activity": vocab.QUOTE_EXPIRED_ACTIVITY,
                    "note": note,
                }
            )

        return {
            "room_id": room_id,
            "at": rules.stamp(now),
            "evaluated": len(candidates),
            "expired": expired,
            "expired_count": len(expired),
            "skipped": skipped,
            "skipped_count": len(skipped),
            "quotes_deleted": 0,
            "invariants": {
                "expiry_survives": vocab.EXPIRY_SURVIVES_QUOTE,
                "acceptance_closed": vocab.ACCEPTANCE_CLOSED_QUOTE,
                "survival": vocab.SURVIVAL_QUOTE,
            },
        }

    # -- activities ------------------------------------------------------------- #

    def _activity(
        self,
        record: Mapping[str, Any],
        data: Mapping[str, Any],
        activity: str,
        now: datetime,
        payload: Mapping[str, Any],
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """One row in this workflow's activity log.

        "reminder + expiration events are exposed as quote activities that can drive
        workflows". Activities are rows rather than log lines for that reason: a workflow
        that can drive on them needs to read them, and a log line a consumer cannot query
        is not a surface.
        """

        row = {
            rules.ROOM_REF: rules.room_ref_of(data, record),
            "quote_id": record.get("id"),
            "activity": activity,
            "at": rules.stamp(now),
            "payload": dict(payload),
        }
        created = self.store.create(
            vocab.ACTIVITIES, row, room_id=row[rules.ROOM_REF], actor=actor, source=source
        )
        return dict(created.get("data") or {})

    def activities(
        self,
        room_id: str | None = None,
        quote_id: str | None = None,
        activity: str | None = None,
    ) -> list[dict[str, Any]]:
        """The activity log, newest first, filtered by whichever of the three is named."""

        where: dict[str, Any] = {}
        if quote_id:
            where["quote_id"] = quote_id
        if room_id:
            where[rules.ROOM_REF] = room_id
        if activity:
            where["activity"] = activity
        rows = self.store.find(vocab.ACTIVITIES, where, limit=500)
        projected = []
        for record in rows:
            row = dict(record.get("data") or {})
            row["id"] = record.get("id")
            projected.append(row)
        return sorted(projected, key=lambda row: str(row.get("at")), reverse=True)

    # -- summary ---------------------------------------------------------------- #

    def summary(self, room_id: str) -> dict[str, Any]:
        """Counts for the room's header, and the invariants beside them.

        Read back from the store rather than accumulated, so the header cannot describe a
        state the store does not hold. The invariants are here because they are the things
        a reader looks for and does not find on a page: expiry keeps the quote, an accepted
        quote survives, and a switch that is off never closes.
        """

        now = self._moment()
        quotes = self.quotes(room_id)
        ledger = self.reminders(room_id)
        activities = self.activities(room_id)
        settings = self.settings(room_id)

        return {
            "room_id": room_id,
            "quotes": len(quotes),
            "draft": sum(1 for row in quotes if row["state"] == vocab.QUOTE_DRAFT),
            "sent": sum(1 for row in quotes if row["state"] == vocab.QUOTE_SENT),
            "published": sum(1 for row in quotes if row["state"] == vocab.QUOTE_PUBLISHED),
            "accepted": sum(1 for row in quotes if row["state"] == vocab.QUOTE_ACCEPTED),
            "signed": sum(1 for row in quotes if row["state"] == vocab.QUOTE_SIGNED),
            "expired": sum(1 for row in quotes if row["state"] == vocab.QUOTE_EXPIRED),
            "voided": sum(1 for row in quotes if row["state"] == vocab.QUOTE_VOIDED),
            "archived": sum(1 for row in quotes if row["state"] == vocab.QUOTE_ARCHIVED),
            "with_expiration": sum(
                1 for row in quotes if row["expiration_enabled"] and row.get(vocab.EXPIRATION_DATE)
            ),
            "expiration_off": sum(1 for row in quotes if not row["expiration_enabled"]),
            "expiring_soon": sum(1 for row in quotes if row["expiring_soon"]),
            "sign_by_deadline": sum(1 for row in quotes if row["sign_by_deadline"]),
            "survived_expiry": sum(1 for row in quotes if row["survives_expiry"]),
            "reminders_sent": sum(1 for row in ledger if row.get("outcome") == vocab.OUTCOME_SENT),
            "reminders_skipped": sum(
                1 for row in ledger if row.get("outcome") == vocab.OUTCOME_SKIPPED
            ),
            "quotes_due_now": sum(1 for row in quotes if row["reminder_count"]),
            "expired_activities": sum(
                1 for row in activities if row.get("activity") == vocab.ACTIVITY_EXPIRED
            ),
            "reminder_activities": sum(
                1 for row in activities if row.get("activity") == vocab.ACTIVITY_REMINDER_SENT
            ),
            "reminder_rules": len(self._rules_for_room(room_id)),
            "default_expiration_days": settings[vocab.DEFAULT_EXPIRATION_DAYS],
            "account_timezone": settings[vocab.ACCOUNT_TIMEZONE],
            "reminder_send_time": settings[vocab.REMINDER_SEND_TIME],
            "automated_reminders_enabled": settings[vocab.AUTOMATED_REMINDERS_ENABLED],
            "evaluated_at": rules.stamp(now),
            "invariants": {
                "expiry_survives": vocab.EXPIRY_SURVIVES_QUOTE,
                "acceptance_closed": vocab.ACCEPTANCE_CLOSED_QUOTE,
                "survival": vocab.SURVIVAL_QUOTE,
                "resend_is_a_new_send": vocab.RESEND_QUOTE,
                "expiration_off": "A quote whose Expiration date switch is off never expires.",
            },
        }


# --------------------------------------------------------------------------- #
# Module helpers
# --------------------------------------------------------------------------- #


def _iso_or_none(value: Any) -> str | None:
    """A stored instant as an ISO string, or ``None``.

    ``None`` is a real value here: it is how an absent expiration date is stored, and how
    a cleared date is stored after a seller uses the switch.
    """

    moment = rules.coerce_instant(value, vocab.EXPIRATION_DATE)
    return rules.stamp(moment) if moment else None


def _expiring_soon(days_left: float | None) -> bool:
    """Is this quote inside the "expiring soon" filter?

    The filter is a page convenience, not a researched window: the research names "status
    filters for \"expiring soon\"" without saying how soon is soon. Seven days is this
    build's figure and it is reported in the vocabulary so a reader can see it is chosen
    rather than sourced.
    """

    if days_left is None:
        return False
    return 0 <= days_left <= vocab.EXPIRING_SOON_DAYS


def _acted_by_deadline(data: Mapping[str, Any], action: str, at: datetime, now: datetime) -> bool:
    """Did this action land at or before the deadline?

    Recorded on the acceptance row itself, so the ledger answers "did they get there in
    time" without recomputing it against a deadline that may since have moved.
    """

    deadline = rules.expires_at_of(data, now)
    if deadline is None:
        return False
    return at <= deadline


def _normalise_acceptances(raw: Any) -> list[dict[str, Any]]:
    """Every acceptance row on a quote, skipping the ones this workflow cannot read."""

    if not isinstance(raw, (list, tuple)):
        return []
    rows: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        try:
            action = rules.coerce_buyer_action(entry.get("action"))
            method = rules.coerce_acceptance_method(entry.get("method"))
        except rules.QuoteExpiryRefusal:
            continue
        at = rules.coerce_instant(entry.get("at"), "at")
        if at is None:
            continue
        rows.append(
            {
                "action": action,
                "method": method,
                "at": rules.stamp(at),
                "actor": entry.get("actor"),
                "by_deadline": bool(entry.get("by_deadline")),
            }
        )
    return rows
