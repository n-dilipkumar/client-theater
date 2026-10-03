"""The CRM seam: records, owners, teams, and the owner lookup itself.

Everything this package knows about where owner ids come from lives here, so the
researched half above the seam can be tested with rows of its own and a real
connector would replace this file and nothing else. Same reasoning as
:class:`dsr.dedupe.engine.StoreCrm`: the research documents request shapes for a
vendor this product has no client for, so calling a fake HTTP client at a real CRM
would be a claim it cannot back.

The lookup this module implements is the whole of the researched data flow's first
arrow: "guest email (or CRM record id) -> CRM lookup for owner id".

**Resolution order is a judgement call and says so.** The evidence sentence is
"lead, contact, or account owner, resolved at booking time", which is alphabetical
and therefore fixes the *set* of objects, not their order. Lead before Contact
before Account is this build's reading, and
:data:`~dsr.ownership_routing.vocabulary.RESOLUTION_ORDER_RATIONALE` gives the
reasoning; the ``resolution-order`` inference in
:mod:`dsr.ownership_routing.inferences` records it as changeable.

**A record with no owner is skipped, not treated as the answer.** The researched
flow routes to an owner; a record whose owner field is empty cannot name one, so
the lookup continues to the next object type. That is different from *no record*,
which produces :class:`~dsr.ownership_routing.errors.NoOwnerResolved`.

**Lead-to-Account is read, not configured.** "For Lead-to-Account (L2A) Matching in
**Salesforce**, we will use the existing settings defined in your workspace" - so
whether a Lead is followed to its Account is a workspace setting this package
consumes, and its absence is treated as "off" rather than guessed on.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from dsr.ownership_routing.errors import OwnerUnknown
from dsr.ownership_routing.vocabulary import (
    CRM_OBJECT_TYPES,
    RESOLUTION_ORDER_RATIONALE,
    normalise_domain,
    normalise_email,
)

#: The records the lookup reads. This is the product's stand-in for the CRM's own
#: ``Lead`` / ``Contact`` / ``Account`` tables.
RECORD_COLLECTION = "crm_owner_record"

#: The reps the resolution can land on. An owner id is only useful if some row
#: answers to it, and the calendar is read off this row.
REP_COLLECTION = "crm_owner_rep"

#: The workspace's Lead-to-Account setting, as the research frames it: "we will use
#: the existing settings defined in your workspace".
SETTINGS_COLLECTION = "crm_owner_setting"


class StoreCrm:
    """The CRM seam. Owner lookups over the audited store.

    Deliberately the only place in the package that knows where owner ids come
    from. Reads come back as **views** - the row's own fields with the record id
    merged in - because that is what a CRM's own response looks like, so a routing
    decision records the CRM's fields rather than this product's storage envelope.
    """

    def __init__(self, store) -> None:
        self.store = store
        #: Every lookup this instance has answered, newest last. The seed and the
        #: tests read it to assert what the resolution actually consulted and in
        #: what order, which is the property the precedence rule is about.
        self.lookups: list[dict[str, Any]] = []

    # -- views -------------------------------------------------------------- #

    @staticmethod
    def view(record: Mapping[str, Any]) -> dict[str, Any]:
        """A stored record as a CRM-shaped row: its fields, plus its id."""
        data = record.get("data")
        payload = dict(data) if isinstance(data, Mapping) else dict(record)
        payload["id"] = record.get("id")
        payload["room_id"] = record.get("room_id")
        return payload

    def _views(self, records: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        return [self.view(record) for record in records]

    # -- settings ----------------------------------------------------------- #

    def lead_to_account_matching(self, key: str = "default") -> bool:
        """Whether this workspace follows a Lead to its Account.

        Defaults to **off**. The research says the existing workspace setting is
        used, and a workspace that has declared nothing has not said yes to
        anything - so the safe reading of an absent setting is off rather than a
        silent walk from every Lead to every Account it happens to share a domain
        with.
        """
        matches = self.store.find(SETTINGS_COLLECTION, {"key": key}, limit=1)
        if not matches:
            return False
        return bool(matches[0]["data"].get("lead_to_account_matching", False))

    # -- records ------------------------------------------------------------ #

    def records(
        self,
        *,
        room_id: str | None = None,
        object_type: str | None = None,
        owner_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """The Lead / Contact / Account rows the lookup reads."""
        where: dict[str, Any] = {}
        if object_type:
            where["object_type"] = object_type
        if owner_id:
            where["owner_id"] = owner_id
        records = (
            self.store.find(RECORD_COLLECTION, where, limit=limit)
            if where
            else self.store.list(RECORD_COLLECTION, room_id=room_id, limit=limit)
        )
        if room_id is not None:
            records = [record for record in records if record.get("room_id") == room_id]
        return self._views(records)

    def record(self, record_id: str) -> dict[str, Any] | None:
        found = self.store.get(record_id)
        return self.view(found) if found and found["collection"] == RECORD_COLLECTION else None

    def require_record(self, record_id: str) -> dict[str, Any]:
        found = self.record(record_id)
        if found is None:
            from dsr.ownership_routing.errors import OwnershipError

            raise OwnershipError(f"no CRM record with id {record_id!r}")
        return found

    # -- reps --------------------------------------------------------------- #

    def reps(self, *, limit: int = 200) -> list[dict[str, Any]]:
        """Every rep an owner id could resolve to."""
        return self._views(self.store.list(REP_COLLECTION, limit=limit))

    def rep(self, owner_id: str) -> dict[str, Any] | None:
        """The rep row for an owner id, matched on the CRM's owner field.

        Matched through ``find`` on the indexed ``owner_id`` rather than by listing
        and scanning, because the id is the CRM's and not this product's: the record
        id of the rep row is an implementation detail a caller never sees.
        """
        if not owner_id:
            return None
        matches = self.store.find(REP_COLLECTION, {"owner_id": owner_id}, limit=2)
        if len(matches) > 1:
            from dsr.ownership_routing.errors import AmbiguousOwner

            raise AmbiguousOwner(
                f"owner id {owner_id!r} answers to {len(matches)} reps; the CRM owner field is meant "
                "to identify one person, and there is no single calendar to read for it"
            )
        return self.view(matches[0]) if matches else None

    def require_rep(self, owner_id: str) -> dict[str, Any]:
        """The rep for an owner id, or a refusal that names the id.

        Kept separate from :meth:`rep` because "no such rep" and "no owner" are
        different failures with different remedies: the first is a workspace that
        has not caught up with its CRM, the second is a CRM fact.
        """
        found = self.rep(owner_id)
        if found is None:
            raise OwnerUnknown(
                f"the CRM record names owner {owner_id!r}, but no rep in this workspace answers to "
                "that id; sync the workspace's reps or fix the record's owner"
            )
        return found

    def account_by_reference(self, reference: str) -> dict[str, Any] | None:
        """The Account a Lead points at, by whichever id the workspace uses.

        A Lead's ``account_id`` names the Account in the **CRM**, so it is usually
        not this store's record id. Rather than force a deployment to restate its
        CRM ids in our terms, the reference is matched against each of the three
        things an Account might be called by: this store's record id, the
        ``account_id`` it carries itself, and its ``external_id``. All three are
        indexed, so this stays a lookup rather than a scan.

        Returns None when the reference names no Account, which the caller treats
        as "the walk goes nowhere" - the setting being on is not a promise that
        every Lead has a reachable Account.
        """
        reference = str(reference or "").strip()
        if not reference:
            return None
        direct = self.record(reference)
        if direct is not None and str(direct.get("object_type") or "").casefold() == "account":
            return direct
        for field in ("account_id", "external_id"):
            matches = self.store.find(RECORD_COLLECTION, {field: reference}, limit=2)
            accounts = [
                self.view(record)
                for record in matches
                if str((record.get("data") or {}).get("object_type") or "").casefold() == "account"
            ]
            if len(accounts) == 1:
                return accounts[0]
        return None

    # -- the lookup --------------------------------------------------------- #

    def find_by_guest(self, guest_email: str) -> list[dict[str, Any]]:
        """Every CRM record that could be the guest's, newest-inserted first.

        Matched on the normalised email because that is the field the researched
        init payload carries, and on the email *domain* as well: an Account has no
        email, it has a website domain, and the research's third object is the
        account owner. A record naming neither is matched by neither and is simply
        not a candidate.
        """
        email = normalise_email(guest_email)
        if not email:
            return []
        domain = normalise_domain(email.split("@")[-1]) if "@" in email else ""
        rows = self.store.list(RECORD_COLLECTION, limit=1000)
        matched = []
        for record in rows:
            data = record.get("data") or {}
            record_email = normalise_email(data.get("email"))
            record_domain = normalise_domain(data.get("domain") or data.get("website"))
            if (email and record_email == email) or (domain and record_domain == domain):
                matched.append(self.view(record))
        # Insertion order is the store's own, so `created_at` with the rowid
        # tie-break behind it: the newest record for an address is the one a rep
        # has most recently touched.
        matched.sort(
            key=lambda row: (str(row.get("created_at") or ""), str(row.get("id") or "")),
            reverse=True,
        )
        return matched

    def resolve_owner(
        self,
        *,
        guest_email: str,
        record_id: str | None = None,
        order: Iterable[str] = CRM_OBJECT_TYPES,
    ) -> dict[str, Any]:
        """The owner of the guest's CRM record, and how it was found.

        Returns a dict rather than an owner id, because the *why* is the product:
        a rep looking at a routing decision needs to see that it was the Account
        owner rather than the Lead owner, because those are two different people
        with two different books of business.

        ``record_id`` short-circuits the guest search, which is the researched
        "(or CRM record id)" in the data flow: a caller that already knows the
        record does not need the address re-derived from it.

        Takes no ``room_id``, on purpose. The CRM is account-level and the room
        scopes the routing decision rather than the lookup, so filtering candidates
        by room here would hide records that exist - and the prospect would reach
        the catch-all for a reason no decision record could explain.
        """
        wanted = [str(entry).strip().casefold() for entry in order if str(entry).strip()]
        sequence = wanted or list(CRM_OBJECT_TYPES)
        if record_id:
            candidates = [self.require_record(record_id)]
        else:
            candidates = self.find_by_guest(guest_email)

        self.lookups.append(
            {
                "guest_email": normalise_email(guest_email),
                "record_id": record_id or "",
                "order": list(sequence),
                "candidates": [row.get("id") for row in candidates],
            }
        )

        skipped: list[dict[str, str]] = []
        for object_type in sequence:
            for candidate in candidates:
                candidate_type = str(candidate.get("object_type") or "").casefold()
                if candidate_type != object_type:
                    continue
                owner_id = str(candidate.get("owner_id") or "").strip()
                if not owner_id:
                    # A record that names nobody is not the answer; the lookup
                    # keeps going rather than reporting an empty owner.
                    skipped.append(
                        {
                            "record_id": str(candidate.get("id") or ""),
                            "object_type": object_type,
                            "reason": "no_owner_id",
                        }
                    )
                    continue
                return {
                    "owner_id": owner_id,
                    "owner_source": "crm",
                    "matched_object_type": object_type,
                    "matched_record_id": str(candidate.get("id") or ""),
                    "matched_email": str(candidate.get("email") or ""),
                    "considered": [row.get("id") for row in candidates],
                    "skipped": skipped,
                    "resolution_order": list(sequence),
                    "resolution_order_rationale": RESOLUTION_ORDER_RATIONALE,
                }

        # Nothing owned the guest. If the workspace follows Lead-to-Account, the
        # Lead is the thing that would have carried the account, so try it once
        # more with the Account object type in front. This is the L2A setting
        # being *read*, which is what the research says it is used for.
        if self.lead_to_account_matching():
            for candidate in candidates:
                if str(candidate.get("object_type") or "").casefold() != "lead":
                    continue
                related = str(candidate.get("account_id") or candidate.get("account") or "").strip()
                if not related:
                    continue
                # The account is fetched **by id**, not picked out of the guest's
                # candidates. That is what makes this a Lead-to-Account walk rather
                # than a second pass of the email search: an Account normally carries
                # no address of the prospect's, so it would not be in the candidate
                # list at all, and searching for it among the candidates would make
                # the setting do nothing while appearing to work.
                account = self.account_by_reference(related)
                if account is None:
                    continue
                owner_id = str(account.get("owner_id") or "").strip()
                if not owner_id:
                    continue
                accounts = [account]
                return {
                    "owner_id": owner_id,
                    "owner_source": "crm",
                    "matched_object_type": "account",
                    "matched_record_id": str(accounts[0].get("id") or ""),
                    "matched_email": str(accounts[0].get("email") or ""),
                    "considered": [row.get("id") for row in candidates],
                    "skipped": skipped,
                    "via_lead": str(candidate.get("id") or ""),
                    "l2a_matching": True,
                    "resolution_order": list(sequence),
                    "resolution_order_rationale": RESOLUTION_ORDER_RATIONALE,
                }

        from dsr.ownership_routing.errors import NoOwnerResolved

        raise NoOwnerResolved(
            "no CRM record with an owner answers to this guest; the research resolves the lead, "
            "contact, or account owner, and this workspace has none for "
            + (normalise_email(guest_email) or "the supplied guest email")
        )
