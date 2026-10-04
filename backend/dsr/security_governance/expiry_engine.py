"""WF-081: the writes. A request sent with an expiry, its reminders, its sweep.

The engine is the only module in the expiry half of this package that writes. It
holds a store and a clock and nothing else, and the HTTP layer builds one per
request, so a test can move the clock by hand and reach every boundary here.

What the specification fixes, and where each rule lives
-------------------------------------------------------

* "Only signature requests that explicitly set an ``expires_at`` will expire."
  :func:`~dsr.security_governance.expiry_rules.has_expiry` is asked before
  anything else happens to a request, in every method below.
* "``expires_at`` must be an integer epoch timestamp in seconds between 1-90 days
  in the future" and "``expires_at`` will be rounded down to the nearest hour" -
  validated in
  :func:`~dsr.security_governance.expiry_rules.coerce_expires_at` at send and at
  update, before a single row is written.
* "Signature request reminder emails will be sent to the signer 3 and 7 days
  before" with a 24-hour dedupe - :meth:`ExpiryEngine.remind`.
* "On expiry, unsigned signatures flip to ``expired``" and "Completed signers
  stay ``signed``" - :meth:`ExpiryEngine.sweep`.
* "All parties to the signature request will still have access to the document
  including audit trail" - nothing here deletes anything. The sweep updates, and
  every read path stays open on a terminal request.

The order the rules are enforced in
----------------------------------

Validate before writing. A request refused for a bad ``expires_at`` must leave no
trace, because the audit log is this product's guarantee and a row describing a
request that changed nothing is a row a reader has to learn to discount.

``source`` is required
----------------------

Every writing method takes ``source`` as a keyword, and it is the route that served
the write. No string in this module is a literal path: the feature module builds
each one from its own router, so an audit row names a route the app actually
serves. ``source`` is a required keyword rather than a default of ``None``
precisely so that omitting it is a ``TypeError`` at the call site rather than a
quiet row with no route on it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from dsr.security_governance import expiry_rules as rules, expiry_vocabulary as vocab
from dsr.store import RecordStore


class ExpiryEngine:
    """Every write and read this workflow performs, over one audited store.

    ``now`` is a callable rather than a value so a test can move the clock. Every
    boundary in this package - the 1-day minimum, the 90-day ceiling, the 3-and-7
    day windows, the 24-hour dedupe and the deadline itself - is a comparison
    against this clock, and a test that cannot choose the instant cannot test any
    of them.
    """

    def __init__(self, store: RecordStore, now: Callable[[], datetime] | None = None) -> None:
        self.store = store
        self._now = now or rules.utcnow

    # -- helpers ------------------------------------------------------------ #

    def _moment(self) -> datetime:
        return self._now()

    def _record(self, request_id: str) -> dict[str, Any]:
        record = self.store.get(request_id)
        if record is None or record.get("collection") != vocab.EXPIRY_COLLECTION:
            raise rules.ExpiryRequestNotFound(request_id)
        return record

    def _signer_index(self, data: Mapping[str, Any], email: str) -> int:
        wanted = str(email or "").strip().lower()
        for index, signer in enumerate(data.get("signatures") or []):
            if (
                isinstance(signer, Mapping)
                and str(signer.get("email", "")).strip().lower() == wanted
            ):
                return index
        raise rules.ExpirySignerNotFound(str(email))

    def _ledger(self, request_id: str, email: str | None = None) -> list[dict[str, Any]]:
        """This request's reminder rows, narrowed to one signer when asked.

        Read with ``find()`` on the two indexed paths rather than by listing the
        collection, because the ledger is the one thing here that grows without
        bound and a request's own rows are a small slice of it.
        """
        where: dict[str, Any] = {"request_id": request_id}
        if email:
            where["email"] = str(email).strip().lower()
        rows = self.store.find(vocab.REMINDER_COLLECTION, where, limit=500)
        return sorted((dict(row.get("data") or {}) for row in rows), key=lambda r: str(r.get("at")))

    # -- send: a request created with an expiry ----------------------------- #

    def send(
        self,
        room_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Send a request, optionally with an expiry. The researched first step.

        "Sender sets an expiry when sending (``expires_at``, an epoch timestamp)".
        The same field is accepted here as on
        :meth:`update_expiry`, because the specification lists send and update as
        the two ways it is set, and one validator serves both.

        A payload with no ``expires_at`` is a perfectly good request that will
        never expire. That is not a degraded case and is not answered with a
        default: "By default signature requests do not expire."

        ``embedded`` in the payload chooses the delivery mode. The specification
        draws a hard line on it: "Emails are muted in all embedded signing flows."
        A request sent embedded gets its reminders recorded as events and no
        email, and its expiry publishes a ``signature_request_expired`` event
        rather than sending a message.
        """
        data = dict(payload or {})
        now = self._moment()
        expires_at = rules.coerce_expires_at(data.get(vocab.EXPIRES_AT), now)
        signers = rules.normalise_signers(data.get("signatures") or data.get("signers"))
        flow = self._flow(data.get("flow") or data.get("invite_path"))

        body: dict[str, Any] = {
            rules.ROOM_REF: room_id,
            "subject": data.get("subject") or f"Agreement for {room_id}",
            "document": data.get("document"),
            "requester_email": data.get("requester_email"),
            "flow": flow,
            "embedded": flow == vocab.FLOW_EMBEDDED,
            "email_muted": flow == vocab.FLOW_EMBEDDED,
            vocab.EXPIRES_AT: expires_at,
            "has_expiry": expires_at is not None,
            "signatures": signers,
            "status": vocab.REQUEST_STATUS_PENDING,
            "closed": False,
            "sent_at": rules.stamp(now),
        }
        # Everything else is carried through untouched. The store is
        # schema-flexible by design, and a field this workflow does not interpret
        # is a field another team owns.
        for key, value in data.items():
            if key not in ("signatures", "signers", vocab.EXPIRES_AT, "flow", "invite_path"):
                body.setdefault(key, value)

        record = self.store.create(
            vocab.EXPIRY_COLLECTION, body, room_id=room_id, actor=actor, source=source
        )
        return self.project(record, now)

    def _flow(self, value: Any) -> str:
        """The delivery mode, from either spelling the specification uses.

        ``invite_path`` is the research's own word for how a request reaches a
        signer, and ``flow`` is the plainer one. Both are accepted because a
        client built from the vendor's docs sends the first and a client built
        from this product's page sends the second.
        """
        text = str(value or vocab.FLOW_HOSTED).strip().lower()
        if text in ("embed", "embedded", "create_embedded"):
            return vocab.FLOW_EMBEDDED
        if text in ("hosted", "email", "send", ""):
            return vocab.FLOW_HOSTED
        raise rules.ExpiryError(
            "expiry_not_an_integer", f"flow must be hosted or embedded, not {value!r}"
        )

    # -- read --------------------------------------------------------------- #

    def request_view(self, request_id: str, tz_name: str | None = None) -> dict[str, Any]:
        """One request as the signer sees it: the banner, the signers, the ledger.

        Read on a terminal request, deliberately. "All parties to the signature
        request will still have access to the document including audit trail,
        similar to ``declined`` signature requests."
        """
        record = self._record(request_id)
        return self.project(record, self._moment(), tz_name=tz_name)

    def requests(self, room_id: str | None = None) -> list[dict[str, Any]]:
        """Every request, newest first, optionally narrowed to one room."""
        now = self._moment()
        records = self.store.list(vocab.EXPIRY_COLLECTION, limit=200, order_by="created_at")
        rows = []
        for record in records:
            if room_id and rules.room_ref_of(record.get("data") or {}, record) != room_id:
                continue
            rows.append(self.project(record, now))
        return rows

    def project(
        self, record: Mapping[str, Any], now: datetime | None = None, *, tz_name: str | None = None
    ) -> dict[str, Any]:
        """A stored request as the API returns it.

        The projection carries the invariants on every response, so a row read
        out of a list, a log export or a test says what expiry is without the
        reader having to find this module.
        """
        moment = now or self._moment()
        data = dict(record.get("data") or {})
        status = rules.request_status(data, moment)
        signers = rules.normalise_signers(data.get("signatures"))
        remaining = rules.seconds_remaining(data, moment)

        view: list[dict[str, Any]] = []
        for signer in signers:
            entry = dict(signer)
            entry["timezone"] = tz_name or signer.get("preferred_timezone")
            entry["expiry_banner"] = self._banner(data, entry.get("timezone"), moment)
            view.append(entry)

        payload: dict[str, Any] = {
            "id": record.get("id"),
            "room_id": rules.room_ref_of(data, record),
            "subject": data.get("subject"),
            "document": data.get("document"),
            "flow": data.get("flow") or vocab.FLOW_HOSTED,
            "email_muted": bool(data.get("email_muted")),
            "has_expiry": rules.has_expiry(data),
            vocab.EXPIRES_AT: rules.expires_at_of(data),
            "status": status,
            "closed": bool(data.get("closed")) or status != vocab.REQUEST_STATUS_PENDING,
            "seconds_remaining": remaining,
            "days_remaining": rules.days_remaining(data, moment),
            "reminders_due": rules.due_reminders(data, moment),
            "signatures": view,
            "sent_at": data.get("sent_at"),
            "updated_at": record.get("updated_at"),
            "revision": record.get("revision"),
        }
        if rules.has_expiry(data):
            payload["expiry_view"] = rules.local_text(
                rules.expires_at_of(data), tz_name or self._house_timezone(data)
            )
        payload["invariants"] = {
            "document_survives": vocab.DOCUMENT_SURVIVES,
            "closed_not_deleted": vocab.DOCUMENT_SURVIVES,
            "absent_expiry_never_expires": (
                "Only signature requests that explicitly set an expires_at will expire."
            ),
        }
        return payload

    def _house_timezone(self, data: Mapping[str, Any]) -> str | None:
        """The timezone a signer with no preference is read in.

        The first signer's stored preference, so one request renders consistently
        in the list view. ``None`` falls through to UTC and the response says so.
        """
        for signer in rules.normalise_signers(data.get("signatures")):
            if signer.get("preferred_timezone"):
                return str(signer["preferred_timezone"])
        return None

    def _banner(
        self, data: Mapping[str, Any], tz_name: str | None, moment: datetime
    ) -> dict[str, Any] | None:
        """The banner a signer reads before signing.

        "During signing, the signer will see the signature request expiration date
        in the banner next to the number of required fields." Both halves are
        here: the deadline in the signer's own timezone, and the count of fields
        still to place. A request with no expiry has no banner date, which is the
        documented behaviour rather than a missing field.
        """
        deadline = rules.expires_at_of(data)
        if deadline is None:
            return None
        outstanding = sum(
            1
            for signer in rules.normalise_signers(data.get("signatures"))
            if str(signer.get("status_code")) not in (vocab.STATUS_SIGNED, "completed")
        )
        view = rules.local_text(deadline, tz_name)
        view["required_fields"] = outstanding
        view["status_code"] = rules.request_status(data, moment)
        return view

    # -- update the expiry --------------------------------------------------- #

    def update_expiry(
        self,
        request_id: str,
        payload: Mapping[str, Any],
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Set, move or clear the deadline. Mirrors ``PUT /update``.

        An explicit ``null`` clears the expiry and returns the request to the
        documented default: "By default signature requests do not expire." It is
        allowed only while the request is open. Once the request is terminal,
        the mutation path is shut - "They will not be able to sign or modify the
        signature request" - and this refuses with ``request_closed``.

        The same range check runs here as on send. A deadline moved past 90 days
        is refused rather than stored, because a limit that only applies at create
        time is not a limit.
        """
        record = self._record(request_id)
        now = self._moment()
        data = dict(record.get("data") or {})
        if rules.is_terminal(data, now):
            raise rules.ExpiryError("request_closed")

        supplied = payload.get(vocab.EXPIRES_AT, _MISSING)
        if supplied is _MISSING:
            return self.project(record, now)
        expires_at = rules.coerce_expires_at(supplied, now)

        patch = {
            vocab.EXPIRES_AT: expires_at,
            "has_expiry": expires_at is not None,
        }
        updated = self.store.update(request_id, patch, actor=actor, source=source)
        return self.project(updated, now)

    # -- the reminder scheduler --------------------------------------------- #

    def remind(
        self,
        request_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Send every reminder due for this request, and record each decision.

        "Signature request reminder emails will be sent to the signer 3 and 7 days
        before the signature request expires" and "If a signer was already
        reminded within 24 hours, we will skip the automated reminder."

        Both outcomes are rows. A send and a skip are written to the ledger with
        the same shape and differ only in ``outcome``, because the skip is the
        rule that is easy to get wrong and a skip nobody can see is a skip nobody
        can verify.

        An explicit ``email`` narrows the run to one signer, which is what the
        specification's singular "was already reminded" is about. With no email,
        every signer is evaluated, so one signer being deduped never suppresses
        another's reminder.

        An embedded request sends nothing and records an event per reminder
        instead: "Emails are muted in all embedded signing flows."
        """
        record = self._record(request_id)
        now = self._moment()
        data = dict(record.get("data") or {})
        body = dict(payload or {})
        only = str(body.get("email") or "").strip()

        signers = rules.normalise_signers(data.get("signatures"))
        if only:
            wanted = only.lower()
            signers = [row for row in signers if str(row["email"]).lower() == wanted]
            if not signers:
                raise rules.ExpirySignerNotFound(only)

        embedded = bool(data.get("embedded"))
        decisions: list[dict[str, Any]] = []

        def write_ledger(entry: dict[str, Any]) -> None:
            """Write one decision to the ledger, send or skip alike.

            A skip is a row for the same reason a send is. "If a signer was already
            reminded within 24 hours, we will skip the automated reminder" is the
            rule this workflow is most likely to get wrong, and a skip that leaves
            no trace is a skip nobody can verify happened at all.
            """
            row = {
                rules.ROOM_REF: rules.room_ref_of(data, record),
                "request_id": request_id,
                "email": entry["email"],
                "lead_days": entry.get("lead_days"),
                vocab.EXPIRES_AT: rules.expires_at_of(data),
                "outcome": entry["outcome"],
                "channel": entry.get("channel") or ("event" if embedded else "email"),
                "reason": entry["reason"],
                "at": entry["at"],
            }
            self.store.create(
                vocab.REMINDER_COLLECTION,
                row,
                room_id=row[rules.ROOM_REF],
                actor=actor,
                source=source,
            )

        for signer in signers:
            email = str(signer["email"])
            if str(signer.get("status_code")) in (vocab.STATUS_SIGNED, "completed"):
                decisions.append(
                    {
                        "email": email,
                        "outcome": "skipped",
                        "reason": "already_signed",
                        "lead_days": None,
                        "at": rules.stamp(now),
                    }
                )
                write_ledger(decisions[-1])
                continue

            plan = rules.reminder_plan(data, self._ledger(request_id, email), now)
            if not plan["due_now"]:
                decisions.append(
                    {
                        "email": email,
                        "outcome": "skipped",
                        "reason": "no_window_open",
                        "lead_days": None,
                        "at": rules.stamp(now),
                    }
                )
                write_ledger(decisions[-1])
                continue
            if plan["blocked_by_dedupe"]:
                decisions.append(
                    {
                        "email": email,
                        "outcome": "skipped",
                        "reason": "deduped_within_24h",
                        "lead_days": plan["lead_days"],
                        "at": rules.stamp(now),
                    }
                )
                write_ledger(decisions[-1])
                continue

            channel = "event" if embedded else "email"
            decisions.append(
                {
                    "email": email,
                    "outcome": "sent",
                    "reason": "window_open",
                    "channel": channel,
                    "lead_days": plan["lead_days"],
                    "at": rules.stamp(now),
                }
            )
            write_ledger(decisions[-1])
            if embedded:
                self._publish(
                    record,
                    vocab.EVENT_REMINDER_SENT,
                    {"email": email, "lead_days": plan["lead_days"]},
                    now,
                    source=source,
                    actor=actor,
                )

        return {
            "request_id": request_id,
            "embedded": embedded,
            "email_muted": embedded,
            "expires_at": rules.expires_at_of(data),
            "decisions": decisions,
            "sent": sum(1 for row in decisions if row["outcome"] == "sent"),
            "skipped": sum(1 for row in decisions if row["outcome"] == "skipped"),
        }

    def reminders(
        self, room_id: str | None = None, request_id: str | None = None
    ) -> list[dict[str, Any]]:
        """The ledger, newest first. Sends and skips alike."""
        where: dict[str, Any] = {}
        if request_id:
            where["request_id"] = request_id
        if room_id:
            where[rules.ROOM_REF] = room_id
        rows = self.store.find(vocab.REMINDER_COLLECTION, where, limit=500)
        projected = []
        for record in rows:
            row = dict(record.get("data") or {})
            row["id"] = record.get("id")
            projected.append(row)
        return sorted(projected, key=lambda r: str(r.get("at")), reverse=True)

    # -- signing ------------------------------------------------------------- #

    def sign(
        self,
        request_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """One signer signs their part, or is refused with the reason why.

        "If they attempt to sign the signature request past the expiration date,
        they will receive an error stating that the signature request is closed."

        Two refusals sit in front of the write and both come from the
        specification. ``already_signed`` is this signer's own row saying they are
        done. ``request_closed`` is the deadline, and it is the sentence the
        specification quotes. A signer who is refused is told which of the two
        happened, because "you already signed" and "this is closed" send them to
        completely different places.

        This writes the signer's own ``status_code`` and nothing else. It does not
        write the request's status: that is derived from its signers on every
        read, so a stored status can never disagree with the rows underneath it.
        """
        record = self._record(request_id)
        now = self._moment()
        data = dict(record.get("data") or {})
        body = dict(payload or {})
        email = str(body.get("email") or "").strip()
        if not email:
            raise rules.ExpirySignerNotFound("")

        index = self._signer_index(data, email)
        signer = dict((data.get("signatures") or [])[index])

        # This signer's own row is checked before the deadline. A signer who
        # already signed is told exactly that, and not that the request is
        # closed, because "you already signed" and "this is closed" send them to
        # two completely different places and only one of them is true.
        if str(signer.get("status_code")) in (vocab.STATUS_SIGNED, "completed"):
            raise rules.ExpiryError("already_signed")
        if rules.is_terminal(data, now):
            raise rules.ExpiryError("request_closed")

        updated_signer = dict(signer)
        updated_signer["status_code"] = vocab.STATUS_SIGNED
        updated_signer["signed_at"] = rules.stamp(now)

        signatures = [dict(row) if isinstance(row, Mapping) else row for row in data["signatures"]]
        signatures[index] = updated_signer

        patch: dict[str, Any] = {"signatures": signatures}
        if not rules.has_expiry(data):
            # An agreement with no expiry stays open whatever happens, so nothing
            # here touches a deadline. Recorded rather than assumed, because this
            # is the branch a request created without an expiry always takes.
            patch["never_expires"] = True
        updated = self.store.update(request_id, patch, actor=actor, source=source)
        return self.project(updated, now)

    def can_sign(self, request_id: str, email: str | None = None) -> dict[str, Any]:
        """May this person sign right now, and why not if not.

        A ``GET`` because it decides nothing and writes nothing. A signer who
        arrives early is not forbidden, so the answer is a report rather than a
        refusal, and it names the reason the same way the refusal would.
        """
        record = self._record(request_id)
        now = self._moment()
        data = dict(record.get("data") or {})
        status = rules.request_status(data, now)

        result: dict[str, Any] = {
            "request_id": request_id,
            "status": status,
            "closed": status != vocab.REQUEST_STATUS_PENDING,
            "has_expiry": rules.has_expiry(data),
            "expires_at": rules.expires_at_of(data),
            "seconds_remaining": rules.seconds_remaining(data, now),
        }
        if email:
            index = self._signer_index(data, str(email))
            signer = (data.get("signatures") or [])[index]
            code = str(signer.get("status_code"))
            result["email"] = str(email)
            result["status_code"] = code
            if code in (vocab.STATUS_SIGNED, "completed"):
                result["can_sign"] = False
                result["reason"] = "already_signed"
            elif status != vocab.REQUEST_STATUS_PENDING:
                result["can_sign"] = False
                result["reason"] = "request_closed"
            else:
                result["can_sign"] = True
                result["reason"] = "open"
        else:
            result["can_sign"] = status == vocab.REQUEST_STATUS_PENDING
            result["reason"] = "open" if result["can_sign"] else "request_closed"
        return result

    # -- the sweep ----------------------------------------------------------- #

    def sweep(
        self,
        room_id: str | None,
        payload: Mapping[str, Any] | None = None,
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Close every request in scope whose deadline has passed.

        "The expiry sweep marks incomplete signatures ``expired``" and "Once a
        signature request has expired, it is considered to be in a final status
        like ``declined`` and ``completed`` signature requests."

        Four things it deliberately does not do.

        * It does not delete anything. "The document itself isn't deleted." The
          update is the only write, and the document and the audit trail stay
          readable.
        * It does not touch a request with no expiry, which is
          ``has_expiry`` on every row it considers.
        * It does not touch a signer who signed. "Completed signers stay
          ``signed``."
        * It does not touch a request that is complete. An agreement whose last
          signer signed before the deadline is completed, not expired.

        Each swept request writes one ``signature_request_expired`` event per
        embedded flow and one per non-embedded flow, carrying the audit sentence
        the specification asks for: the expiration date and the signers who did
        not sign by it.
        """
        now = self._moment()
        body = dict(payload or {})
        only = str(body.get("request_id") or "").strip()

        candidates = self.store.list(vocab.EXPIRY_COLLECTION, limit=200, order_by="created_at")
        swept: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []

        for entry in candidates:
            data = dict(entry.get("data") or {})
            record_id = str(entry.get("id"))
            if room_id and rules.room_ref_of(data, entry) != room_id:
                continue
            if only and record_id != only:
                continue
            reason = self._sweep_skip_reason(data, now)
            if reason:
                # The status is reported on the skip, because the request's own
                # ``status`` field is derived from its signers and its deadline. A
                # skip with no status leaves the reader to recompute it, and the
                # whole point of a skip is that it is recorded rather than implied.
                skipped.append(
                    {"id": record_id, "reason": reason, "status": rules.request_status(data, now)}
                )
                continue

            updated_data = rules.apply_sweep(data)
            updated = self.store.update(
                record_id,
                {
                    "signatures": updated_data["signatures"],
                    "closed": True,
                    "status": vocab.REQUEST_STATUS_EXPIRED,
                    "expired_at": rules.stamp(now),
                },
                actor=actor,
                source=source,
            )
            note = rules.audit_note(updated_data, now)
            self._publish(
                updated,
                vocab.EVENT_EXPIRED,
                {
                    "expired_at": rules.expires_at_of(data),
                    "did_not_sign": [row["email"] for row in rules.swept_signers(data)],
                    "audit_note": note,
                },
                now,
                source=source,
                actor=actor,
            )
            swept.append(
                {
                    "id": record_id,
                    "status": vocab.REQUEST_STATUS_EXPIRED,
                    "swept": rules.swept_signers(updated_data),
                    "kept_signed": [
                        str(signer.get("email"))
                        for signer in (updated.get("data") or {}).get("signatures", [])
                        if isinstance(signer, Mapping)
                        and str(signer.get("status_code")) in (vocab.STATUS_SIGNED, "completed")
                    ],
                    "audit_note": note,
                }
            )

        return {
            "room_id": room_id,
            "at": rules.stamp(now),
            "swept": swept,
            "swept_count": len(swept),
            "skipped": skipped,
            "skipped_count": len(skipped),
            "documents_deleted": 0,
            "invariants": {
                "document_survives": vocab.DOCUMENT_SURVIVES,
                "link_survives": vocab.LINK_SURVIVES,
                "completed_signers_kept": (
                    "On expiry, unsigned signatures flip to expired. Completed signers stay signed."
                ),
            },
        }

    def _sweep_skip_reason(self, data: Mapping[str, Any], now: datetime) -> str:
        """Why this request was left alone, or ``""`` to sweep it.

        The order is the order of the rules. No expiry first, because a request
        without one must never close. Then a request already closed, because the
        sweep has to be safe to run twice: an hour of downtime is a scheduler
        nobody ran, and re-sweeping a closed request would write a second
        ``signature_request_expired`` event for one deadline. An integration that
        counted those events would count a buyer being told twice.

        Then the deadline, because a request still inside its window is not due.
        Then completion, because a buyer who signed in time is not swept.
        """
        if not rules.has_expiry(data):
            return "no_expiry_set"
        if data.get("closed"):
            return "already_closed"
        if not rules.is_expired(data, now):
            return "deadline_not_passed"
        if rules.is_complete(data):
            return "already_complete"
        return ""

    # -- the event stream ---------------------------------------------------- #

    def _publish(
        self,
        record: Mapping[str, Any],
        event: str,
        payload: Mapping[str, Any],
        now: datetime,
        *,
        source: str,
        actor: str | None,
    ) -> dict[str, Any]:
        """One row in this workflow's event stream.

        The specification's sixth notification rule: a non-embedded flow emails
        every signer and the requester, while an embedded flow sends the event and
        mutes email. There is no third shape and no silent case - every reminder
        and every expiry publishes exactly one event, and the ``channel`` field
        says whether a message or only the event went out.
        """
        data = dict(record.get("data") or {})
        room = rules.room_ref_of(data, record)
        row = {
            rules.ROOM_REF: room,
            "request_id": record.get("id"),
            "event": event,
            "channel": "event" if data.get("embedded") else "email",
            "email_muted": bool(data.get("embedded")),
            "at": rules.stamp(now),
            "payload": dict(payload),
        }
        created = self.store.create(
            vocab.EVENT_COLLECTION, row, room_id=room, actor=actor, source=source
        )
        return dict(created.get("data") or {})

    def events(
        self,
        room_id: str | None = None,
        request_id: str | None = None,
        event: str | None = None,
    ) -> list[dict[str, Any]]:
        """The event stream, newest first, filtered by whichever of the three
        the caller names."""
        where: dict[str, Any] = {}
        if request_id:
            where["request_id"] = request_id
        if room_id:
            where[rules.ROOM_REF] = room_id
        if event:
            where["event"] = event
        rows = self.store.find(vocab.EVENT_COLLECTION, where, limit=500)
        projected = []
        for record in rows:
            row = dict(record.get("data") or {})
            row["id"] = record.get("id")
            projected.append(row)
        return sorted(projected, key=lambda r: str(r.get("at")), reverse=True)


class _Missing:
    """A sentinel distinct from ``None``, because ``null`` clears and omission does not."""


_MISSING = _Missing()
