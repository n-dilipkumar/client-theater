"""The CRM on the other end of the writeback: records, matching, and selection.

``WF-065``'s two most quoted behaviours are both *choices among existing CRM
records*:

* "All created Events will be related to the Contact or Lead **by default**. If
  we have found a contact, you can additionally relate the Event to an
  **Account**, **Case**, **Opportunity**, or **Campaign**."
* "💡 For **Cases**, we will relate with the most recently created Open one, and
  for **Opportunities**, we will relate with the one that has the nearest Close
  Date."

Neither is observable without a CRM that already holds Contacts, Leads,
Accounts, Open and Closed Cases, and Opportunities at different distances from
the meeting. :class:`LocalCrm` is that CRM: an in-process one, backed by the
audited store, so the research's two selection rules are *executed* rather than
described in a comment.

Every row lives in :data:`CRM_RECORD_COLLECTION` and is arbitrary JSON, because
the storage contract forbids a migration or a typed column: a tenant adding a
field to its Opportunities ships a payload, not a schema change. The handful of
keys this module reads - ``crm_id``, ``vendor``, ``type``, ``email``, ``status``,
``owner``, ``created_on``, ``close_date``, ``account_id`` - are conventions, and
:func:`describe_record_keys` says so where a client can read it.

Faults are injected the way :mod:`dsr.atomic_bundle` injects them, and only from
Python: a caller that could forge a CRM failure could forge an audit row, so the
HTTP layer never accepts them.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable, Mapping

from dsr.store import RecordStore

#: Where the CRM's records live. Prefixed ``crm_booking_`` so no other feature can
#: claim the collection, and named after what it is: the vendor's own rows, not
#: the room's.
CRM_RECORD_COLLECTION = "crm_booking_record"

#: The Salesforce object types the research names, verbatim from
#: ``data_sources``: "Salesforce ``Lead``, ``Contact``, ``Account``,
#: ``Opportunity``, ``Case``, ``Campaign``, ``CampaignMember``, ``Event``".
TYPE_LEAD = "Lead"
TYPE_CONTACT = "Contact"
TYPE_ACCOUNT = "Account"
TYPE_OPPORTUNITY = "Opportunity"
TYPE_CASE = "Case"
TYPE_CAMPAIGN = "Campaign"
TYPE_CAMPAIGN_MEMBER = "CampaignMember"
TYPE_EVENT = "Event"

#: The HubSpot object types the research names, verbatim: "HubSpot ``Contact``,
#: ``Company``, ``Deal``, ``Ticket``, engagement".
TYPE_COMPANY = "Company"
TYPE_DEAL = "Deal"
TYPE_TICKET = "Ticket"
TYPE_ENGAGEMENT = "Engagement"

#: Every record type the research's ``data_sources`` sentence names, so
#: ``/vocabulary`` can report the whole list rather than a subset.
CRM_TYPES: tuple[str, ...] = (
    TYPE_LEAD,
    TYPE_CONTACT,
    TYPE_ACCOUNT,
    TYPE_OPPORTUNITY,
    TYPE_CASE,
    TYPE_CAMPAIGN,
    TYPE_CAMPAIGN_MEMBER,
    TYPE_EVENT,
    TYPE_COMPANY,
    TYPE_DEAL,
    TYPE_TICKET,
    TYPE_ENGAGEMENT,
)

#: The status value the Case selection rule filters on, quoted: "we will relate
#: with the most recently created **Open** one". Any status that is not this one
#: is not a candidate, and that is the whole point of the rule.
OPEN_STATUS = "Open"

#: The keys this module reads. Conventions, not schema - see the module docstring.
RECORD_KEYS: tuple[str, ...] = (
    "crm_id",
    "vendor",
    "type",
    "name",
    "email",
    "status",
    "owner",
    "created_on",
    "close_date",
    "account_id",
    "campaign_id",
    "fields",
    "activity_assigned_to",
    "source",
    "booking_id",
    "run_id",
    "node",
)

#: The note attached to a run this build could not fully interpret. The research
#: quotes Chili Piper's behaviour and never a vendor response body, so the raw
#: body is stored and the per-field outcomes are left to the local CRM's own
#: report rather than parsed from a shape the research does not give.
RESPONSE_NOT_PARSED_NOTE = (
    "This build does not parse a vendor CRM response body, so the vendor's own "
    "field-level outcomes are unknown. What became of each node is on the run "
    "record, produced by the engine that executed it."
)


def describe_record_keys() -> dict[str, Any]:
    """The conventions the local CRM reads, served so a client can see them."""
    return {
        "collection": CRM_RECORD_COLLECTION,
        "keys": list(RECORD_KEYS),
        "types": list(CRM_TYPES),
        "open_status": OPEN_STATUS,
        "note": (
            "These are conventions read by the local CRM, not a schema. A tenant "
            "may add any key to any row; nothing here requires a migration."
        ),
    }


# --------------------------------------------------------------------------- #
# Dates
# --------------------------------------------------------------------------- #


def parse_date(value: Any) -> date | None:
    """Read a CRM date, which may be ``YYYY-MM-DD`` or a full ISO timestamp.

    Salesforce dates and HubSpot dates arrive in both shapes, and a
    selection rule that silently treated one as unparseable would drop a
    candidate without saying so - which is the failure the Case rule exists to
    prevent.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    for candidate in (text, text[:10]):
        try:
            return date.fromisoformat(candidate)
        except ValueError:
            continue
    return None


def days_between(left: date, right: date) -> int:
    """Whole days from ``left`` to ``right``, negative when ``right`` is earlier."""
    return (right - left).days


# --------------------------------------------------------------------------- #
# The local CRM
# --------------------------------------------------------------------------- #


class LocalCrm:
    """An in-process CRM that answers the two researched questions for real.

    It holds vendor rows in the audited store and implements:

    * **matching by email** - "[sourced] matched by email", across the record
      types a create node may update, in a fixed and stated order;
    * **the related-object selection rules** - newest Open Case, nearest Close
      Date Opportunity, and the rest, each returning *why* it chose the row it
      chose so a rep can see the rule fire;
    * **fault injection**, so a failed Event - and therefore a retryable row in
      Events History - is reachable without a network.
    """

    def __init__(
        self,
        store: RecordStore,
        *,
        faults: Mapping[str, str] | None = None,
        room_id: str | None = None,
    ) -> None:
        self.store = store
        self.faults = dict(faults or {})
        self.room_id = room_id

    # -- reads -------------------------------------------------------------- #

    def records(
        self,
        record_type: str | None = None,
        *,
        vendor: str | None = None,
        include_deleted: bool = False,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Every record of one type, newest first, as the store returns them.

        Ordered by the store's own total order (``updated_at`` then ``id``) so
        two reads of the same data give the same list. Callers that need the
        *created* order - which the Case rule does - sort explicitly, because
        ``created_at`` and ``updated_at`` are different facts.
        """
        rows = self.store.list(
            CRM_RECORD_COLLECTION,
            room_id=self.room_id,
            limit=limit,
            include_deleted=include_deleted,
        )
        selected = [
            row
            for row in rows
            if (record_type is None or row["data"].get("type") == record_type)
            and (vendor is None or row["data"].get("vendor") == vendor)
        ]
        return selected

    def of_type(self, record_type: str) -> list[dict[str, Any]]:
        return self.records(record_type)

    def get(self, crm_id: str) -> dict[str, Any] | None:
        """One record by its vendor id, or ``None``.

        Deliberately a scan rather than an index lookup: the vendor id is
        arbitrary JSON in the row, and a filter on an indexed path would be one
        more convention to keep in step with the index.
        """
        for row in self.records(include_deleted=True):
            if row["data"].get("crm_id") == crm_id:
                return row
        return None

    # -- writes ------------------------------------------------------------- #

    def create(
        self,
        record_type: str,
        fields: Mapping[str, Any],
        *,
        source: str,
        vendor: str = "salesforce",
    ) -> dict[str, Any]:
        """Create one CRM record and return it.

        ``crm_id`` is minted the way a vendor mints one - a type prefix and a
        counter - so a reader can tell a Lead from a CampaignMember without looking
        the type up, which is what every vendor's own ids do.

        The counter is the **highest suffix already used for that prefix**, over
        every row including the soft-deleted ones, not the number of live rows. A
        live count would hand the same id out twice the moment anything was deleted
        - and a Delete Event behaviour deletes things - which is exactly how two
        records end up answering to one vendor id, with the second one silently
        shadowing the first. A soft-deleted row therefore still holds its id.
        """
        fault = self.faults.get(f"create:{record_type}")
        if fault:
            raise CrmRefused(f"create {record_type}", fault)
        payload: dict[str, Any] = {"vendor": vendor, "type": record_type, **dict(fields)}
        payload["crm_id"] = self._mint_id(record_type)
        payload.setdefault("created_on", date.today().isoformat())
        return self.store.create(
            CRM_RECORD_COLLECTION, payload, room_id=self.room_id, actor="crm", source=source
        )

    def _mint_id(self, record_type: str) -> str:
        """The next unused vendor id for this record type in this room."""
        prefix = f"{_prefix(record_type)}-"
        highest = 0
        for row in self.records(include_deleted=True):
            identifier = str(row["data"].get("crm_id") or "")
            if not identifier.startswith(prefix):
                continue
            try:
                highest = max(highest, int(identifier[len(prefix) :]))
            except ValueError:
                continue
        return f"{prefix}{highest + 1:04d}"

    def update(
        self, crm_id: str, patch: Mapping[str, Any], *, source: str
    ) -> dict[str, Any] | None:
        """Patch one record by vendor id, or return ``None`` if it is not there."""
        row = self.get(crm_id)
        if row is None:
            return None
        return self.store.update(row["id"], dict(patch), actor="crm", source=source)

    def delete(self, crm_id: str, *, source: str) -> dict[str, Any] | None:
        """Soft-delete one record by vendor id.

        A soft delete rather than a hard one, so an Events History row whose
        Event was deleted still resolves to the record it names - which is the
        only version of it that matters when someone asks what happened.
        """
        row = self.get(crm_id)
        if row is None:
            return None
        return self.store.delete(row["id"], actor="crm", source=source)

    def seed(
        self, rows: Iterable[Mapping[str, Any]], *, source: str = "seed"
    ) -> list[dict[str, Any]]:
        """Put vendor rows in place, for a demo or a test.

        The rows go through the same store as everything else, so a seeded CRM is
        audited exactly like a written one. A ``crm_id`` already present is
        **skipped** rather than refused, and a row with no ``crm_id`` is a refusal -
        so re-seeding a room is harmless while a genuinely malformed row is still
        loud. A seeder that raises takes the whole feature's demo with it, and the
        rows that made it through are the ones a reviewer would rather have.
        """
        created: list[dict[str, Any]] = []
        for row in rows:
            payload = dict(row)
            crm_id = str(payload.get("crm_id") or "")
            if not crm_id:
                raise ValueError("every seeded CRM record needs a crm_id")
            if self.get(crm_id) is not None:
                continue
            created.append(
                self.store.create(
                    CRM_RECORD_COLLECTION,
                    payload,
                    room_id=self.room_id,
                    actor="crm",
                    source=source,
                )
            )
        return created

    # -- the researched rules ---------------------------------------------- #

    def match_by_email(
        self, email: str, *, record_types: Iterable[str], source: str
    ) -> dict[str, Any]:
        """The record matching ``email``, searched in the order given.

        [sourced] "matched by email". The comparison is case-insensitive and
        whitespace-trimmed because every vendor's own matching is, and a booking
        form that lower-cases an address must still match the record.

        Returns a report rather than a row, because the caller needs to know
        *which types were searched* - "matched by email" with no stated
        precedence is a gap, and the answer is a stated order, not a lucky one.
        """
        searched = [str(name) for name in record_types]
        needle = (email or "").strip().lower()
        if not needle:
            return {"matched": None, "searched": searched, "reason": "no_email"}
        for name in searched:
            for row in self.of_type(name):
                candidate = str(row["data"].get("email") or "").strip().lower()
                if candidate == needle:
                    return {
                        "matched": row,
                        "searched": searched,
                        "reason": "matched_email",
                        "matched_type": name,
                    }
        return {"matched": None, "searched": searched, "reason": "no_email_match"}

    def select_related(
        self,
        record_type: str,
        *,
        record: Mapping[str, Any] | None,
        on: date | None,
        source: str,
    ) -> dict[str, Any]:
        """Run the researched selection rule for one Related Object type.

        The two rules the research states exactly:

        * **Case** - "we will relate with the most recently created Open one".
          Closed cases are not candidates, and among Open ones the newest
          ``created_on`` wins.
        * **Opportunity** - "the one that has the nearest Close Date". Nearest
          to the meeting, measured as the smallest absolute gap in days, with the
          earlier date winning a tie.

        The other two types have no stated rule, and :data:`SELECTION_RULES` says
        which rule this build applied to each so the choice is inspectable
        rather than buried.
        """
        from dsr.booking_crm.vocabulary import SELECTION_RULES

        rule = SELECTION_RULES.get(record_type, "most_recent")
        candidates = self.of_type(record_type)
        considered: list[dict[str, Any]] = []
        chosen: dict[str, Any] | None = None
        reason = ""

        if rule == "the_contact_account":
            account_id = str((record or {}).get("account_id") or "")
            if account_id:
                chosen = next(
                    (row for row in candidates if row["data"].get("crm_id") == account_id), None
                )
            reason = "contact_account" if chosen else "no_account_on_record"
        elif rule == "most_recent_open":
            open_rows = [row for row in candidates if row["data"].get("status") == OPEN_STATUS]
            considered = list(open_rows)
            excluded = len(candidates) - len(open_rows)
            ordered = sorted(
                open_rows,
                key=lambda row: (_date_key(row["data"].get("created_on")), str(row["id"])),
                reverse=True,
            )
            chosen = ordered[0] if ordered else None
            reason = "most_recent_open" if chosen else "no_open_case"
            if not chosen and excluded:
                reason = f"no_open_case ({excluded} closed case(s) not candidates)"
        elif rule == "nearest_close_date":
            dated = [row for row in candidates if parse_date(row["data"].get("close_date"))]
            considered = list(dated)
            if on is not None and dated:

                def gap(row: Mapping[str, Any]) -> tuple[int, date]:
                    close = parse_date(row["data"].get("close_date"))
                    assert close is not None  # narrowed by the filter above
                    return (abs(days_between(on, close)), close)  # type: ignore[return-value]

                chosen = min(dated, key=gap)
                reason = "nearest_close_date"
            elif dated:
                ordered = sorted(
                    dated,
                    key=lambda row: (parse_date(row["data"].get("close_date")), str(row["id"])),
                )
                chosen = ordered[0]
                reason = "no_meeting_date_so_earliest_close_date"
            else:
                reason = "no_close_date_on_any_candidate"
        else:  # most_recent
            ordered = sorted(
                candidates,
                key=lambda row: (_date_key(row["data"].get("created_on")), str(row["id"])),
                reverse=True,
            )
            chosen = ordered[0] if ordered else None
            reason = "most_recent" if chosen else "none_exist"

        return {
            "record_type": record_type,
            "rule": rule,
            "chosen": chosen,
            "reason": reason,
            "candidates": len(candidates),
            "considered": len(considered),
        }


class CrmRefused(RuntimeError):
    """The CRM refused one write, with a message it would have returned itself.

    Raised by :meth:`LocalCrm.create` and by the engine's field and ownership
    writes when a fault is injected. It carries a ``code`` because Events History
    shows "the detailed error" and a code is what a rep can search on.
    """

    def __init__(self, action: str, detail: str, code: str = "CRM_ERROR") -> None:
        self.action = action
        self.detail = detail
        self.code = code
        super().__init__(f"{action} refused by the CRM: {detail}")


def _prefix(record_type: str) -> str:
    """A short, readable id prefix, the way a vendor abbreviates an object type."""
    return {
        TYPE_LEAD: "lead",
        TYPE_CONTACT: "cnt",
        TYPE_ACCOUNT: "acc",
        TYPE_OPPORTUNITY: "opp",
        TYPE_CASE: "case",
        TYPE_CAMPAIGN: "camp",
        TYPE_CAMPAIGN_MEMBER: "cmb",
        TYPE_EVENT: "evt",
        TYPE_COMPANY: "comp",
        TYPE_DEAL: "deal",
        TYPE_TICKET: "tick",
        TYPE_ENGAGEMENT: "eng",
    }.get(record_type, record_type[:4].lower())


def _date_key(value: Any) -> str:
    """A sortable key for a possibly-absent date.

    An absent date sorts *first* in ascending order, so a row with no
    ``created_on`` is never the newest one. The Case rule depends on that: a
    Case with an unparseable date must not win "most recently created" by
    default.
    """
    parsed = parse_date(value)
    return parsed.isoformat() if parsed else ""


__all__ = [
    "CRM_RECORD_COLLECTION",
    "CRM_TYPES",
    "OPEN_STATUS",
    "RECORD_KEYS",
    "RESPONSE_NOT_PARSED_NOTE",
    "CrmRefused",
    "LocalCrm",
    "describe_record_keys",
    "days_between",
    "parse_date",
]
