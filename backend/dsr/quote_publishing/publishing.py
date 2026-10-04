"""WF-094: the engine that publishes a quote and shares it.

Everything that reads or writes a record is here. The rules it applies live in
:mod:`.rules` and the names it applies them with live in :mod:`.vocabulary`, so a
reviewer can check a rule against the research without reading the write path.

Where a published quote's state lives was put to Jev before this was written
(audit ``jev-20261004T215156-29936-16381``, verdict ``pass``, confidence 1.00).
The single quote record won. The reasoning is worth repeating here because it
governs the shape of every method below: the research says ``hs_quote_link`` is
"a read-only property and cannot be set through the API after publishing", and
that ``hs_status`` is what unlocking moves. So publishing is one state
transition on the quote, the computed properties land on that same record, and
nothing else has to be consulted to answer "what is the public URL for this
quote".

Storage
-------
Every write goes through :class:`~dsr.store.RecordStore`, so the audit row is
written in the same transaction as the change. There is no migration and no
typed column: the computed properties are ordinary keys in ``records.data``,
which is why a team can add its own field to a quote without coordinating with
anyone.

Every write method takes a required ``source``: the route that served the
request, supplied by the HTTP layer. That is what keeps the audit row naming a
path the app actually serves, rather than a label written once and never
checked.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from dsr.db.audited import utcnow
from dsr.quote_publishing.rules import (
    QuoteRuleError,
    attachment_note,
    build_slug,
    compute_total,
    may_publish,
    normalise_status,
    pdf_download_link,
    pdf_location,
    public_link,
    require_cc,
    require_publishable,
    require_to,
    require_unlock_target,
    resolve_domain,
    resolve_locale,
)
from dsr.quote_publishing.vocabulary import (
    ACTIVITY,
    ACTIVITY_LINK_COPIED,
    ACTIVITY_PDF_REQUESTED,
    ACTIVITY_PUBLISHED,
    ACTIVITY_SENT,
    ACTIVITY_SHARED,
    KNOWN_STATUSES,
    LANGUAGES,
    LOCALES,
    PUBLISHED,
    QUOTE,
    SHARED,
    TIMEZONES,
    UNLOCK_TARGETS,
)
from dsr.store import RecordStore

#: Where a workspace's quote domain and locale settings are read from. It is a
#: collection like any other, so a team can put the settings wherever its own
#: layout puts them, and a build with none falls back to the researched defaults.
SETTINGS = "quote_settings"


class QuotePublishingService:
    """Publish a quote, freeze its totals, and share it.

    Every write method requires ``source``. The rules that decide whether a write
    is allowed are the ones in :mod:`.rules`; this class is only the part that
    reads and writes.
    """

    def __init__(self, store: RecordStore) -> None:
        self.store = store

    # -- reads ------------------------------------------------------------- #

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
            raise QuoteRuleError(f"quote {quote_id} not found")
        return record

    def list_quotes(
        self, *, room_id: str | None = None, status: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Quotes newest-updated first, optionally narrowed to one status."""
        if status:
            records = self.store.find(
                QUOTE, {"status": normalise_status(status)}, limit=min(limit, 1000)
            )
            if room_id:
                records = [r for r in records if r.get("room_id") == room_id]
            return records[:limit]
        return self.store.list(QUOTE, room_id=room_id, limit=limit)

    def settings(self, *, room_id: str | None = None) -> dict[str, Any]:
        """The workspace's quote domain and locale settings.

        "By default, quotes are hosted on the landing page primary domain
        connected to your account", so a workspace that has connected no domain
        gets the researched default. The answer carries ``domain_configured``
        because the caller records it, and a reader later needs to know whether
        a link was served from a real domain or from the fallback.
        """
        records = self.store.list(SETTINGS, room_id=room_id, limit=1)
        raw = records[0]["data"] if records else {}
        configured = str(raw.get("domain") or "").strip()
        domain = resolve_domain(configured)
        return {
            **raw,
            "domain": domain,
            "domain_configured": bool(configured),
            "domain_fallback": not bool(configured),
            **resolve_locale(
                raw,
                allowed_languages=LANGUAGES,
                allowed_locales=LOCALES,
                allowed_timezones=TIMEZONES,
            ),
        }

    # -- publishing --------------------------------------------------------- #

    def publish(
        self,
        quote_id: str,
        *,
        actor: str | None = None,
        shared_only: bool = False,
        source: str,
    ) -> dict[str, Any]:
        """Move a quote into its published or shared state and freeze its total.

        ``shared_only`` publishes without freezing. The research keeps the two
        apart: "when you click **Share**, the quote moves to a *Shared* status,
        even if it hasn't been sent to the buyer", and separately
        "the overall amount is locked and can't be modified after it's
        published". Sharing makes a link copyable and a PDF downloadable; it does
        not by itself commit the workspace to the figures. So ``SHARED`` is
        reachable without the freeze, and ``PUBLISHED`` always carries it.

        A ``SHARED`` quote may be published, which is what makes sharing a first
        step rather than a dead end. That edge is a design decision, not a quoted
        sentence; Jev chose it over a terminal ``SHARED`` (audit
        ``jev-20261004T215726-29100-46936``, ``pass``, 0.92).

        The caller never supplies a link, a slug or a domain. The research calls
        ``hs_quote_link`` read-only and says ``hs_domain`` and ``hs_slug`` are
        set by state, so this method computes them and refuses a link in the
        request rather than ignoring one.
        """
        quote = self.require_quote(quote_id)
        current = normalise_status(quote["data"].get("status"))
        require_publishable(current)

        settings = self.settings(room_id=quote.get("room_id"))
        target = SHARED if shared_only else PUBLISHED
        now = utcnow()

        total = compute_total(quote["data"].get("line_items") or [])
        quote_number = quote["data"].get("quote_number") or quote["data"].get("hs_quote_number")
        slug = build_slug(quote_number, quote["id"])
        domain = settings["domain"]
        link = public_link(domain, slug)

        payload: dict[str, Any] = {
            "status": target,
            "hs_status": target,
            "hs_domain": domain,
            "hs_slug": slug,
            "hs_quote_link": link,
            "hs_pdf_download_link": pdf_download_link(link, quote["id"]),
            "hs_quote_number": quote_number,
            "hs_language": settings["language"],
            "hs_locale": settings["locale"],
            "hs_timezone": settings["timezone"],
            "domain_fallback": settings["domain_fallback"],
            "published_at": now,
            "published_by": actor,
        }
        if shared_only:
            # A shared quote keeps its editable total. The researched freeze
            # attaches to publication, so nothing here claims one is locked.
            payload["shared_at"] = now
            payload["shared_by"] = actor
            payload["hs_locked"] = False
        else:
            payload["hs_locked"] = True
            payload["hs_quote_amount"] = total
            payload["locked_amount"] = total

        updated = self.store.update(quote["id"], payload, actor=actor, source=source)
        self.log_activity(
            quote,
            activity=ACTIVITY_SHARED if shared_only else ACTIVITY_PUBLISHED,
            actor=actor,
            source=source,
            detail={
                "status": target,
                "locked": not shared_only,
                "hs_quote_link": link,
            },
        )
        return view_quote(updated)

    def unlock(
        self,
        quote_id: str,
        target: str,
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Release a published quote's totals by moving it to an editable status.

        "To modify any properties after you've published a quote, you must first
        update the ``hs_status`` of the quote back to ``DRAFT``,
        ``PENDING_APPROVAL``, or ``REJECTED``." The target is checked against
        that list rather than trusted, so an unlock cannot be aimed at a status
        that keeps the quote frozen.

        The public link is recomputed, not reused. A quote that has been edited
        since it was published should not keep handing a buyer the figures that
        were frozen under it, and the recomputation gives a new slug only when
        the quote number changed, so an ordinary unlock keeps the link stable.
        """
        quote = self.require_quote(quote_id)
        wanted = require_unlock_target(target)
        current = normalise_status(quote["data"].get("status"))

        if current == wanted:
            # Already editable at this status. Recording it as a release would
            # claim work that did not happen.
            return view_quote(quote)

        settings = self.settings(room_id=quote.get("room_id"))
        quote_number = quote["data"].get("quote_number") or quote["data"].get("hs_quote_number")
        slug = build_slug(quote_number, quote["id"])
        link = public_link(settings["domain"], slug)

        updated = self.store.update(
            quote["id"],
            {
                "status": wanted,
                "hs_status": wanted,
                "hs_locked": False,
                "hs_quote_link": link,
                "hs_domain": settings["domain"],
                "hs_slug": slug,
                "hs_pdf_download_link": pdf_download_link(link, quote["id"]),
                "unlocked_at": utcnow(),
                "unlocked_by": actor,
                "unlocked_from": current,
            },
            actor=actor,
            source=source,
        )
        return view_quote(updated)

    # -- sharing ------------------------------------------------------------ #

    def share_link(self, quote_id: str, *, actor: str | None = None, source: str) -> dict[str, Any]:
        """The link a seller would copy, recorded as an activity.

        "in the **Copy link, download PDF** tab click **Copy link**". Copying a
        link is not a send, so this records that a link was handed out and
        nothing else. It refuses on a quote that has no link rather than
        composing one, because a copy-link action on an unpublished quote is the
        one case where a seller would be given a URL that does not resolve.
        """
        quote = self.require_quote(quote_id)
        link = quote["data"].get("hs_quote_link")
        if not link:
            raise QuoteRuleError(
                f"quote {quote_id} has no public link; publish it before copying one"
            )
        self.log_activity(
            quote,
            activity=ACTIVITY_LINK_COPIED,
            actor=actor,
            source=source,
            detail={"hs_quote_link": link},
        )
        return {
            "quote_id": quote["id"],
            "hs_quote_link": link,
            "hs_pdf_download_link": quote["data"].get("hs_pdf_download_link"),
            "copied_at": utcnow(),
        }

    def send_email(
        self,
        quote_id: str,
        *,
        to: Sequence[str] | str | None = None,
        cc: Sequence[str] | str | None = None,
        contact_email: str | None = None,
        subject: str | None = None,
        body: str | None = None,
        from_address: str | None = None,
        pdf_size_bytes: int | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Record a quote email and its share event.

        The researched flow is one click with four decisions in it: the **To**
        auto-fills from the associated contact and is changeable, **Cc** takes
        up to nine addresses, **From** defaults to the configured default, and
        the PDF is attached only below the cap. Each of those is a rule in
        :mod:`.rules` and is applied here.

        No message is transmitted. This product records what a workspace did; it
        has no mail transport, and inventing one would be a workflow this
        research does not describe. The event is the record.
        """
        quote = self.require_quote(quote_id)
        link = quote["data"].get("hs_quote_link")
        if not link:
            raise QuoteRuleError(f"quote {quote_id} has no public link to send")

        recipient = require_to(to, contact_email)
        copied = require_cc(list(cc)) if cc else []
        note = attachment_note(pdf_size_bytes)

        settings = self.settings(room_id=quote.get("room_id"))
        payload = {
            "kind": "quote_email",
            # The same key as every other activity row, so one query returns the
            # quote's whole stream. The email is the *Quote sent* activity with
            # the delivery details on it, rather than a second shape in the same
            # collection that a reader has to know to special-case.
            "activity": ACTIVITY_SENT,
            "quote_id": quote["id"],
            "to": recipient,
            "cc": copied,
            "from": from_address or settings.get("default_from_address"),
            "subject": subject
            or f"Your quote {quote['data'].get('hs_quote_number') or quote['id']}",
            "body": body,
            "hs_quote_link": link,
            "hs_pdf_download_link": quote["data"].get("hs_pdf_download_link"),
            "pdf_attached": note is None and pdf_size_bytes is not None,
            "pdf_size_bytes": pdf_size_bytes,
            "attachment_note": note,
            "sent_at": utcnow(),
            "sent_by": actor,
        }
        # One row, not two. The event *is* the "Quote sent" activity: it carries
        # the label the research names and the delivery details on the same
        # record, so the quote's activity stream cannot disagree with itself
        # about how many times the quote was sent.
        event = self.store.create(
            ACTIVITY, payload, room_id=quote.get("room_id"), actor=actor, source=source
        )
        self.store.update(
            quote["id"],
            {"sent": True, "sent_at": payload["sent_at"], "sent_to": recipient},
            actor=actor,
            source=source,
        )
        return event

    def request_download(
        self,
        quote_id: str,
        *,
        file_name: str | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Record a PDF download request and name its output location.

        "the generated PDF file is always saved to the default location:
        ``<record_name>_<record_id>``". The name is recorded so a reader can see
        where the file landed without this workflow claiming to have written
        anything to a file system.
        """
        quote = self.require_quote(quote_id)
        location = pdf_location(
            file_name or quote["data"].get("title") or "quote",
            quote["id"],
        )
        event = self.store.create(
            ACTIVITY,
            {
                "kind": "quote_activity",
                "quote_id": quote["id"],
                "activity": ACTIVITY_PDF_REQUESTED,
                "location": location,
                "detail": {"location": location},
                "occurred_at": utcnow(),
                "requested_by": actor,
            },
            room_id=quote.get("room_id"),
            actor=actor,
            source=source,
        )
        return {"event_id": event["id"], "location": location}

    # -- activities --------------------------------------------------------- #

    def log_activity(
        self,
        quote: Mapping[str, Any],
        *,
        activity: str,
        actor: str | None,
        source: str,
        detail: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record one of the automatic quote activities.

        "Quote activity (``Quote published``, ``Quote sent``) is logged
        automatically." Those two are the set the research names; the two extra
        labels this module writes are the link and PDF actions from the Copy link
        and download tab, and they are recorded here so the quote's activity
        stream is the one place a reader looks.
        """
        return self.store.create(
            ACTIVITY,
            {
                "kind": "quote_activity",
                "quote_id": quote["id"],
                "activity": activity,
                "detail": dict(detail or {}),
                "occurred_at": utcnow(),
            },
            room_id=quote.get("room_id"),
            actor=actor,
            source=source,
        )

    def list_activity(self, quote_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        """A quote's activity stream, newest first."""
        return self.store.find(ACTIVITY, {"quote_id": quote_id}, limit=min(limit, 1000))


# --------------------------------------------------------------------------- #
# Derived views
# --------------------------------------------------------------------------- #


def view_quote(record: Mapping[str, Any]) -> dict[str, Any]:
    """Add the derived publish facts to a quote without touching what is stored.

    ``data`` is returned exactly as persisted. Everything computed lives under
    ``derived``, so a client cannot mistake a computation for stored state, which
    is the same rule the rest of this codebase follows.
    """
    data = record.get("data", {})
    status = normalise_status(data.get("status"))
    locked = data.get("hs_locked") is True
    return {
        **record,
        "derived": {
            "status": status,
            "known_status": status in KNOWN_STATUSES,
            "locked": locked,
            "frozen_amount": data.get("locked_amount"),
            "shareable": bool(data.get("hs_quote_link")),
            "sent": data.get("sent") is True,
            "unlock_targets": list(UNLOCK_TARGETS),
            "can_publish": may_publish(status),
            "can_unlock": locked,
        },
    }
