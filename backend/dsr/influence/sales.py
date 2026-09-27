"""Content & Sales Influence: the revenue each asset is associated with.

The researched precondition, verbatim
-------------------------------------
"**Content & Sales Influence** — This report will be shown assuming you have
integrated with your CRM, and have connected accounts & deals/opportunities to
workspaces. For each piece of content, you will see the breakdown of revenue and
deals associated with the asset."

Two things follow from that sentence and they shape this module.

**It is shown, not raised.** A screen that 500s because the CRM is not
connected is worse than a screen that says which of the three links is missing,
so the default answer is 200 with ``available: false`` and a named blocker per
missing link. A caller that wants a hard precondition - a BI export that must
not silently publish an empty revenue column - passes ``strict=true`` and gets
428, the status this codebase already uses for "not configured yet".

**"Associated" is not "caused".** The research is explicit elsewhere: "Content-
to-revenue association exists only as a per-asset breakdown, not as a model"
(Dock's own limitation, recorded as gap 6 in the research corpus). So this
module never infers. An asset is associated with a deal when all three of the
following hold, and the evidence for each is returned with the row:

1. the deal is linked to a workspace that resolves to a live room;
2. the deal names the asset;
3. that asset was actually shared with, or viewed by, someone in that
   workspace.

A deal that names an asset with no engagement in its own workspace is reported
as an *unassociated link* with the reason, not counted. A workspace with
engagement whose deal does not name the asset is reported as an *unattributed
workspace* with the reason. Both are the state the research warns a team will
meet - "connected accounts & deals" is a human linking task - and hiding them
would let a revenue number look complete when it is not.
"""

from __future__ import annotations

import os
from typing import Any, Mapping

from dsr.influence.errors import (
    CrmNotLinked,
    InfluenceError,
    InfluenceNotLinked,
    UnknownRoom,
)
from dsr.influence.metrics import Report, compile_report
from dsr.influence.vocab import (
    ACCOUNT_COLLECTION,
    ASSET_COLLECTION,
    DEAL_COLLECTION,
    DOWNLOADED,
    ROOM_COLLECTION,
    SHARED,
    Filters,
    scan,
)
from dsr.store import RecordStore

#: Revenue is only summed within one currency. Mixing currencies into a single
#: number is arithmetic that answers a question nobody asked, and the research
#: names no conversion rate, so the report refuses to invent one.
DEFAULT_CURRENCY = "USD"


def _room_name(record: Mapping[str, Any]) -> str:
    """A workspace's human name, for a reader looking at a revenue row."""
    data = record.get("data") or {}
    for key in ("name", "title", "account"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return str(record.get("id", ""))


def _currency() -> str:
    return (os.environ.get("DSR_CRM_CURRENCY") or DEFAULT_CURRENCY).strip().upper() or DEFAULT_CURRENCY


def _amount(payload: Mapping[str, Any], raw: Any) -> float:
    """A deal amount, refusing rather than defaulting.

    A revenue breakdown that silently counted a missing amount as zero would be
    a breakdown a reader cannot trust, and the sources never say a deal has no
    amount - so an unparseable amount is a refusal.
    """
    if raw is None or raw == "":
        raise InfluenceError(
            f"a deal needs an amount to appear in Content & Sales Influence; got {raw!r}"
        )
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        raise InfluenceError(f"amount {raw!r} is not a number")
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise InfluenceError(f"amount {raw!r} is not a number") from exc
    if value < 0:
        raise InfluenceError(f"amount {raw!r} cannot be negative")
    return round(value, 2)


def _rooms_by_id(store: RecordStore) -> dict[str, dict[str, Any]]:
    return {str(record["id"]): record for record in scan(store, ROOM_COLLECTION).records}


def _deal_name(data: Mapping[str, Any]) -> str:
    for key in ("name", "title", "label"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return str(data.get("crm_id") or "unnamed deal")


def _account_name(data: Mapping[str, Any]) -> str:
    """The display name of a ``crm_account`` record."""
    for key in ("name", "account", "title", "label"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _deal_account(data: Mapping[str, Any]) -> str:
    """The account a ``crm_deal`` record names.

    Only the ``account`` field, and not the deal's own ``name``. Reading the
    first non-empty string field would compare a deal's *title* against the
    account list and never match, which is a precondition that reads as
    permanently unmet and sends a reader looking for a CRM sync that is already
    working.
    """
    value = data.get("account")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return ""


def _is_won(data: Mapping[str, Any]) -> bool:
    """Whether a deal is won, from either spelling a CRM uses."""
    if "won" in data:
        return bool(data["won"])
    return str(data.get("stage") or "").strip().lower() in {
        "won",
        "closed won",
        "closedwon",
        "closed-won",
    }


# --------------------------------------------------------------------------- #
# Preconditions
# --------------------------------------------------------------------------- #


def preconditions(store: RecordStore, *, room_id: str | None = None) -> dict[str, Any]:
    """Which of the researched preconditions hold, and which do not.

    Each blocker is a named code with the thing a person has to do, because
    "unavailable" with no next step is the version of this message that gets
    filed as a bug.
    """
    accounts = scan(store, ACCOUNT_COLLECTION).records
    deals = scan(store, DEAL_COLLECTION, room_id=room_id).records
    rooms = _rooms_by_id(store)

    linked_rooms = [
        record for record in deals if record.get("room_id") in rooms
    ]
    account_names = {_account_name(record.get("data") or {}).lower() for record in accounts}
    account_names.discard("")
    linked_accounts = [
        record
        for record in linked_rooms
        if _deal_account(record.get("data") or {}).lower() in account_names
    ]
    linked_assets = [
        record
        for record in linked_rooms
        if _asset_ids(record.get("data") or {})
    ]

    blockers: list[dict[str, str]] = []
    if not accounts:
        blockers.append(
            {
                "code": "crm_not_integrated",
                "detail": "no CRM account is recorded, so no deal can be attributed to one",
                "remedy": "register the CRM accounts, then link a deal to a workspace",
            }
        )
    if not linked_rooms:
        blockers.append(
            {
                "code": "deals_not_linked",
                "detail": "no deal is connected to a workspace that exists",
                "remedy": "connect accounts and deals to workspaces; the join is per workspace",
            }
        )
    if accounts and linked_rooms and not linked_accounts:
        # Only reported once accounts exist. With no accounts at all the
        # ``crm_not_integrated`` blocker already says the thing, and two
        # blockers for one missing fact reads as two things to go and fix.
        blockers.append(
            {
                "code": "accounts_not_linked",
                "detail": "a deal is connected to a workspace but names no known CRM account",
                "remedy": "set the deal's account to a registered CRM account name",
            }
        )
    if linked_rooms and not linked_assets:
        blockers.append(
            {
                "code": "assets_not_linked",
                "detail": "a deal is connected to a workspace but names no library asset",
                "remedy": "name the assets on the deal that the content influenced",
            }
        )

    return {
        "available": not blockers,
        "blockers": blockers,
        "checked": {
            "crm_accounts": len(accounts),
            "deals": len(deals),
            "deals_linked_to_workspaces": len(linked_rooms),
            "deals_naming_a_known_account": len(linked_accounts),
            "deals_naming_an_asset": len(linked_assets),
        },
    }


# --------------------------------------------------------------------------- #
# The report
# --------------------------------------------------------------------------- #


def _asset_ids(data: Mapping[str, Any]) -> list[str]:
    """The asset ids a deal names, from either spelling."""
    raw = data.get("assets")
    if raw is None:
        raw = data.get("asset_ids")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return []
    names: list[str] = []
    for item in raw:
        if item is None:
            continue
        text = str(item).strip()
        if text and text not in names:
            names.append(text)
    return names


def _evidence(report: Report, asset_id: str, room_id: str | None) -> dict[str, Any]:
    """What an asset did in one workspace, which is the whole basis of a link."""
    shares = views = downloads = 0
    seconds = 0
    people: set[str] = set()
    last_at: str | None = None
    for event in report.events:
        if str(event.get("asset_id")) != asset_id:
            continue
        if room_id is not None and event.get("room_id") != room_id:
            continue
        action = str(event.get("action"))
        if action == SHARED:
            shares += 1
        elif action == DOWNLOADED:
            downloads += 1
        elif event.get("audience") == "internal":
            views += 1
        else:
            views += 1
        value = event.get("seconds")
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            seconds += int(value)
        person = event.get("person")
        if person:
            people.add(str(person))
        when = event.get("at")
        if isinstance(when, str) and (last_at is None or when > last_at):
            last_at = when
    return {
        "views": views,
        "shares": shares,
        "downloads": downloads,
        "seconds_on_content": seconds,
        "viewers": len(people),
        "last_activity_at": last_at,
    }


def _has_engagement(evidence: Mapping[str, Any]) -> bool:
    return bool(evidence["views"] or evidence["shares"] or evidence["downloads"])


def sales_influence(
    store: RecordStore,
    *,
    filters: Filters | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    """The per-asset revenue breakdown.

    ``strict=True`` turns a missing precondition into a 428 instead of an
    ``available: false`` body, for a caller that must not publish an empty
    revenue column.
    """
    active = filters or Filters()
    status = preconditions(store, room_id=active.room_id)
    if not status["available"] and strict:
        raise CrmNotLinked(status["blockers"])

    report = compile_report(store, active)
    rooms = _rooms_by_id(store)
    deals = scan(store, DEAL_COLLECTION, room_id=active.room_id).records
    account_names = {
        _account_name(record.get("data") or {}).lower()
        for record in scan(store, ACCOUNT_COLLECTION).records
    }
    account_names.discard("")

    # asset_id -> the rows that associate revenue with it
    per_asset: dict[str, dict[str, Any]] = {}
    deal_rows: list[dict[str, Any]] = []
    unassociated: list[dict[str, Any]] = []
    unattributed: list[dict[str, Any]] = []
    unknown_assets: set[str] = set()
    #: Deal ids that reached at least one asset, so their revenue is counted once.
    attributed: set[str] = set()
    # Two revenue sums, deliberately not one. `deal_revenue` is everything the
    # connected deals are worth; `revenue` is only what the report can actually
    # attribute to content. Collapsing them is how a report ends up claiming
    # content influenced a pipeline it never touched.
    #
    # Both are accumulated per *deal*, never per asset-deal pair. A deal the
    # research's own linking puts on two assets is one deal, and summing its
    # amount twice would report a pipeline larger than the pipeline. The
    # per-asset rows still each carry the full deal amount - which is what "the
    # revenue of the deals this asset is associated with" means - so the asset
    # rows deliberately overlap and their sum is not the total.
    deal_revenue: dict[str, float] = {}
    associated_revenue: dict[str, float] = {}
    associated_won = 0.0
    associated_open = 0.0
    linked_deals = 0

    for record in deals:
        data = record.get("data") or {}
        room_id = str(record["room_id"]) if record.get("room_id") else ""
        room = rooms.get(room_id)
        if room is None:
            # An unresolvable workspace means the deal cannot be attributed to
            # any workspace's content, and `preconditions` has already said so.
            continue
        linked_deals += 1
        currency = str(data.get("currency") or _currency()).upper()
        try:
            amount = _amount(data, data.get("amount"))
        except InfluenceError:
            # A malformed amount on a synced row must not take the report down;
            # it is counted as no revenue here and `preconditions` still reports
            # the deal, so the row is visible to whoever can fix it.
            amount = 0.0
        deal_revenue[currency] = round(deal_revenue.get(currency, 0.0) + amount, 2)
        won = _is_won(data)

        account = _deal_account(data)
        room_label = _room_name(room)
        associations: list[dict[str, Any]] = []
        for asset_id in _asset_ids(data):
            row = report.row(asset_id)
            if row is None:
                # The deal names an asset this report cannot see. Either it is
                # not in the library, or the active filters excluded it. Both
                # are named so the reader is not left guessing which.
                live = store.get(asset_id)
                unknown_assets.add(str(asset_id))
                if live is not None and live.get("collection") == ASSET_COLLECTION:
                    unknown_assets.add(f"{asset_id} (in the library, out of the active filter)")
                continue
            evidence = _evidence(report, asset_id, room_id)
            if not _has_engagement(evidence):
                unassociated.append(
                    {
                        "deal_id": str(record["id"]),
                        "deal": _deal_name(data),
                        "asset_id": asset_id,
                        "asset_title": row.title,
                        "room_id": room_id,
                        "room": room_label,
                        "reason": (
                            f"{row.title} has no share, view or download recorded in "
                            f"{room_label}, and this report does not infer influence"
                        ),
                    }
                )
                continue
            association = {
                "deal_id": str(record["id"]),
                "asset_id": asset_id,
                "asset_title": row.title,
                "deal": _deal_name(data),
                "crm_id": data.get("crm_id"),
                "account": account,
                "account_known": account.lower() in account_names,
                "amount": amount,
                "currency": currency,
                "stage": data.get("stage"),
                "won": won,
                "room_id": room_id,
                "room": room_label,
                "closed_at": data.get("closed_at"),
                "evidence": evidence,
            }
            associations.append(association)

            bucket = per_asset.setdefault(
                asset_id,
                {
                    "asset_id": asset_id,
                    "title": row.title,
                    "kind": row.kind,
                    "collections": list(row.collections),
                    "revenue": 0.0,
                    "revenue_by_currency": {},
                    "deal_count": 0,
                    "won_revenue": 0.0,
                    "open_revenue": 0.0,
                    "workspaces": set(),
                    "deals": [],
                    "views": 0,
                    "shares": 0,
                    "downloads": 0,
                },
            )
            bucket["revenue"] = round(bucket["revenue"] + amount, 2)
            bucket["revenue_by_currency"][currency] = round(
                bucket["revenue_by_currency"].get(currency, 0.0) + amount, 2
            )
            bucket["deal_count"] += 1
            bucket["workspaces"].add(room_id)
            bucket["views"] += evidence["views"]
            bucket["shares"] += evidence["shares"]
            bucket["downloads"] += evidence["downloads"]
            if won:
                bucket["won_revenue"] = round(bucket["won_revenue"] + amount, 2)
            else:
                bucket["open_revenue"] = round(bucket["open_revenue"] + amount, 2)
            bucket["deals"].append(association)
            # Counted once per deal, on the deal's first association.
            if str(record["id"]) not in attributed:
                attributed.add(str(record["id"]))
                associated_revenue[currency] = round(
                    associated_revenue.get(currency, 0.0) + amount, 2
                )
                if won:
                    associated_won += amount
                else:
                    associated_open += amount

        # A workspace where content was engaged with but whose deal names no
        # asset: the human has not finished the linking task the research says
        # this report depends on.
        engaged_here = [event for event in report.events if event.get("room_id") == room_id]
        if engaged_here and not _asset_ids(data):
            titles = {
                report.row(str(event["asset_id"])).title
                for event in engaged_here
                if report.row(str(event["asset_id"])) is not None
            }
            unattributed.append(
                {
                    "deal_id": str(record["id"]),
                    "deal": _deal_name(data),
                    "room_id": room_id,
                    "room": room_label,
                    "reason": (
                        f"{len(engaged_here)} content event(s) happened in {room_label} but "
                        "the deal names no asset, so no revenue is attributed to any of them"
                    ),
                    "assets_engaged": sorted(titles),
                }
            )

        deal_rows.append(
            {
                "deal_id": str(record["id"]),
                "deal": _deal_name(data),
                "crm_id": data.get("crm_id"),
                "account": account,
                "account_known": account.lower() in account_names,
                "amount": amount,
                "currency": currency,
                "stage": data.get("stage"),
                "won": won,
                "room_id": room_id,
                "room": room_label,
                "associated_assets": [entry["asset_id"] for entry in associations],
            }
        )

    assets = [
        {**bucket, "workspaces": sorted(bucket["workspaces"]), "workspace_count": len(bucket["workspaces"])}
        for bucket in per_asset.values()
    ]
    assets.sort(key=lambda entry: (-entry["revenue"], entry["title"]))

    def _single(by_currency: dict[str, float]) -> float | None:
        """One number when every deal shares a currency, otherwise ``None``."""
        if len(by_currency) > 1:
            return None
        return round(sum(by_currency.values()), 2)

    return {
        "available": status["available"],
        "blockers": status["blockers"],
        "preconditions": status["checked"],
        "filters": report.filters.as_dict(),
        "room_id": active.room_id,
        "currency": None if len(associated_revenue) > 1 else next(iter(associated_revenue), None),
        "revenue_mixed_currencies": len(associated_revenue) > 1
        or len(deal_revenue) > 1,
        "revenue_by_currency": associated_revenue,
        "deal_revenue_by_currency": deal_revenue,
        "totals": {
            "revenue": _single(associated_revenue),
            "deal_revenue": _single(deal_revenue),
            "open_revenue": round(associated_open, 2),
            "won_revenue": round(associated_won, 2),
            "deal_count": len(deal_rows),
            "linked_deal_count": linked_deals,
            "influenced_assets": len(assets),
            "influenced_workspaces": len(
                {entry["room_id"] for entry in deal_rows if entry["associated_assets"]}
            ),
        },
        "assets": assets,
        "deals": sorted(deal_rows, key=lambda entry: (-entry["amount"], entry["deal"])),
        "unassociated_links": unassociated,
        "unattributed_workspaces": unattributed,
        "unknown_assets": sorted(set(unknown_assets)),
        "truncated": report.truncated,
    }


# --------------------------------------------------------------------------- #
# The links
# --------------------------------------------------------------------------- #


def _validate_assets(store: RecordStore, raw: Any) -> list[str]:
    """Every named asset must be a live library asset.

    A link to an asset that is not there can never be associated with anything,
    and the research's own premise is that content governance is what this report
    is for - so a dangling reference is a mistake to report at the moment it is
    made, not a row that silently contributes zero.
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        raise InfluenceError(f"assets must be a list of library asset ids; got {raw!r}")
    resolved: list[str] = []
    for item in raw:
        text = str(item).strip()
        if not text:
            continue
        record = store.get(text)
        if record is None or record.get("collection") != ASSET_COLLECTION:
            raise InfluenceNotLinked(text)
        if text not in resolved:
            resolved.append(text)
    return resolved


#: Payload keys this module interprets itself. Everything else is copied through
#: untouched, because the repo's rule is that a team adding a field to a record
#: must not need coordination with anyone - and a link route that silently
#: dropped ``hubspot_id`` would make that true for this one collection and false
#: for every other.
_LINK_FIELDS = frozenset(
    {
        "name",
        "title",
        "room_id",
        "roomId",
        "amount",
        "assets",
        "asset_ids",
        "currency",
        "crm_id",
        "crmId",
        "won",
    }
)

_ACCOUNT_FIELDS = frozenset({"name", "account", "crm_id", "crmId"})


def _passthrough(payload: Mapping[str, Any], known: frozenset[str]) -> dict[str, Any]:
    """Every payload key this module does not interpret, copied verbatim."""
    extra: dict[str, Any] = {}
    for key, value in payload.items():
        if key in known or value is None or value == "":
            continue
        extra[str(key)] = value
    return extra


def link_deal(
    store: RecordStore,
    payload: Mapping[str, Any] | None = None,
    *,
    actor: str | None = None,
    source: str,
) -> dict[str, Any]:
    """Connect a CRM deal to a workspace and name the assets it is attributed to.

    This is the other half of the researched precondition - "have connected
    accounts & deals/opportunities to workspaces" - and it is a write, so it is
    audited and ``source`` is required. Upsert on ``crm_id``: a CRM sync that
    sends the same deal twice updates the one deal rather than creating a second
    one, because two rows for one deal would double its revenue in every
    breakdown above.
    """
    body = dict(payload or {})
    name = str(body.get("name") or body.get("title") or "").strip()
    if not name:
        raise InfluenceError("a deal needs a name")
    room_id = str(body.get("room_id") or body.get("roomId") or "").strip()
    if not room_id:
        raise InfluenceError(
            "a deal needs room_id: Content & Sales Influence joins content to revenue "
            "through the workspace the content was shared in"
        )
    room = store.get(room_id)
    if room is None or room.get("collection") != ROOM_COLLECTION:
        raise UnknownRoom(room_id)

    crm_id = str(body.get("crm_id") or body.get("crmId") or "").strip() or None
    # `room_id` is deliberately *not* in the payload: it is part of the record
    # envelope, and the audited store strips reserved keys out of `data` on the
    # way in. Repeating it here would look like it were stored twice and would
    # silently not be.
    data: dict[str, Any] = {
        "name": name,
        "amount": _amount(body, body.get("amount")),
        "currency": str(body.get("currency") or _currency()).upper(),
        "assets": _validate_assets(store, body.get("assets") or body.get("asset_ids")),
    }
    for key in ("account", "stage", "crm_id", "closed_at", "owner"):
        value = body.get(key)
        if value not in (None, ""):
            data[key] = str(value) if not isinstance(value, (int, float)) else value
    data.update(_passthrough(body, _LINK_FIELDS))
    if "won" in body:
        data["won"] = bool(body["won"])
    elif "stage" in data and _is_won(data):
        data["won"] = True

    existing = None
    if crm_id:
        found = store.find(DEAL_COLLECTION, {"crm_id": crm_id}, limit=1)
        existing = found[0] if found else None

    if existing is not None:
        record = store.update(existing["id"], data, actor=actor, source=source)
        return {"created": False, "deal": record}
    record = store.create(DEAL_COLLECTION, data, room_id=room_id, actor=actor, source=source)
    return {"created": True, "deal": record}


def link_account(
    store: RecordStore,
    payload: Mapping[str, Any] | None = None,
    *,
    actor: str | None = None,
    source: str,
) -> dict[str, Any]:
    """Register the CRM account a deal can be attributed to.

    Also an upsert on ``crm_id``, for the same reason as :func:`link_deal`. The
    account list is short and is read as a name key, because that is what a
    workspace carries and what a deal payload names.
    """
    body = dict(payload or {})
    name = str(body.get("name") or body.get("account") or "").strip()
    if not name:
        raise InfluenceError("a CRM account needs a name")
    crm_id = str(body.get("crm_id") or body.get("crmId") or "").strip() or None
    data: dict[str, Any] = {"name": name}
    for key in ("crm_id", "domain", "industry", "owner"):
        value = body.get(key)
        if value not in (None, ""):
            data[key] = str(value)
    data.update(_passthrough(body, _ACCOUNT_FIELDS))

    existing = None
    if crm_id:
        found = store.find(ACCOUNT_COLLECTION, {"crm_id": crm_id}, limit=1)
        existing = found[0] if found else None
    if existing is not None:
        return {"created": False, "account": store.update(existing["id"], data, actor=actor, source=source)}
    return {"created": True, "account": store.create(ACCOUNT_COLLECTION, data, actor=actor, source=source)}


def summarise_links(store: RecordStore) -> dict[str, Any]:
    """Every recorded link, for a reviewer to read the join's inputs directly."""
    accounts = [record["data"] | {"id": record["id"]} for record in scan(store, ACCOUNT_COLLECTION).records]
    deals = [record["data"] | {"id": record["id"]} for record in scan(store, DEAL_COLLECTION).records]
    return {
        "accounts": accounts,
        "deals": deals,
        "counts": {"accounts": len(accounts), "deals": len(deals)},
        "collections": {"accounts": ACCOUNT_COLLECTION, "deals": DEAL_COLLECTION},
    }
