"""Alert routing: who hears about a signal, and who has already been told.

The research names "alert routing rules" as a product surface and states no rule
set for them, so this module derives one and the derivation is served at
``GET /api/wf-133/inferences`` beside the rest.

Three things live here.

**The recipient chain.** Four rule kinds are read in ``position`` order and the
first match wins unless the rule says otherwise. The CRM owner is tried first,
because the researched flow names that recipient explicitly: "the account owner
gets an email and a Slack DM". A team rule follows, because the research lists
"CRM ownership and territory rules" as a data source. The watchlist's own
recipients come next, and a fallback catches whatever is left, so an alert with no
owner has somewhere to go rather than nowhere.

**An address is not an identity.** A recipient is recorded twice: as whoever is
accountable, and as the address that reaches them. They are often not the same
thing and the second is frequently absent. WF-042 keys an owner by the vendor's
opaque owner id and carries no address for them, so an alert commonly names a
person it cannot write to. That is recorded as ``deliverable: false`` and the
dispatch reports why, rather than the alert borrowing the buyer's address.

**Contact suppression.** "auto-suppress contacted accounts" is named as an
automation with no stated rule. The derived rule is per account, per room, per
recipient, for twenty-four hours, and it is a count over this module's own alerts
rather than a vendor call.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from dsr.intent_routing.errors import InvalidRule
from dsr.intent_routing.vocabulary import (
    ALERTS,
    DISPATCH_QUEUED,
    EMAIL_CHANNEL,
    NOTIFICATION_CHANNELS,
    RULE_CRM_OWNER,
    RULE_FALLBACK,
    RULE_KINDS,
    RULE_TEAM,
    RULE_WATCHLIST,
    SLACK_CHANNEL,
    SUPPRESSION_HOURS,
)
from dsr.store import RecordStore

#: A rule's ``match`` may constrain any of these, and every constraint given must
#: hold. The list is closed for the same reason the observation's fields are: a
#: match key nobody implements is a rule that silently never fires.
MATCH_KEYS: tuple[str, ...] = ("segment", "country", "size", "team", "company_key")

#: Recipients the build will not accept. An address is the only thing that can be
#: queued for delivery, and a Slack handle is not an address, so accepting one here
#: would produce a message addressed to something the mail transport cannot use.
_ALLOWED_RULE_KINDS = frozenset(RULE_KINDS)


@dataclass(frozen=True)
class Recipient:
    """One party an alert is addressed to.

    ``who`` and ``address`` are separate on purpose. ``who`` is always populated
    when a rule matched, so the alert names an accountable party even when there is
    nowhere to send it. ``address`` is populated only when something on file is
    genuinely an address.
    """

    kind: str
    who: str
    address: str = ""
    rule_id: str = ""
    rule_name: str = ""

    @property
    def deliverable(self) -> bool:
        return bool(self.address)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "who": self.who,
            "address": self.address,
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "deliverable": self.deliverable,
        }


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _addresses(raw: Any, field_name: str = "notify") -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        raise InvalidRule(f"{field_name} must be a list of addresses")
    kept: list[str] = []
    for entry in raw:
        address = _text(entry)
        if not address:
            raise InvalidRule(f"{field_name} must not contain a blank address")
        if "@" not in address or address.startswith("@") or address.endswith("@"):
            raise InvalidRule(
                f"{address!r} is not an email address. This build queues a message for an email "
                "channel and nothing else, so a Slack handle has nowhere to go."
            )
        if address not in kept:
            kept.append(address.lower())
    return kept


def normalise_rule(payload: Any, *, fallback_count: int = 0) -> dict[str, Any]:
    """Read a routing rule, or refuse it by name.

    ``fallback_count`` is how many fallback rules the room already holds. A
    fallback is tried last by definition, so a second one would be dead code that
    reads in the UI like a second choice.
    """
    if not isinstance(payload, dict):
        raise InvalidRule("a routing rule must be an object")

    kind = _text(payload.get("kind"))
    if kind not in _ALLOWED_RULE_KINDS:
        raise InvalidRule(f"kind must be one of {', '.join(RULE_KINDS)}, got {kind or 'nothing'}")

    name = _text(payload.get("name"))
    if not name:
        raise InvalidRule("name is required")

    match = payload.get("match") or {}
    if not isinstance(match, dict):
        raise InvalidRule("match must be an object")
    unknown = sorted(set(match) - set(MATCH_KEYS))
    if unknown:
        raise InvalidRule(
            f"match names key(s) {', '.join(unknown)}, which no rule reads; "
            f"the readable keys are {', '.join(MATCH_KEYS)}"
        )
    criteria = {name_: _text(value).lower() for name_, value in match.items() if _text(value)}

    notify = _addresses(payload.get("notify"))

    if kind == RULE_FALLBACK:
        if criteria:
            raise InvalidRule("a fallback rule matches everything, so it takes no criteria")
        if notify:
            raise InvalidRule(
                "a fallback rule takes its recipients from the room's default, so it names none"
            )
        if fallback_count:
            raise InvalidRule(
                "this room already has a fallback rule, and only one is ever consulted"
            )

    if kind in (RULE_TEAM, RULE_WATCHLIST) and not criteria and not notify:
        raise InvalidRule(
            f"a {kind} rule must name at least one match criterion or one recipient, "
            "or it matches every signal in the room"
        )

    if kind == RULE_CRM_OWNER and notify:
        raise InvalidRule(
            "a crm_owner rule takes its recipient from the opportunity owner, so it names none"
        )

    position = payload.get("position", 0)
    if isinstance(position, bool) or not isinstance(position, (int, float)):
        raise InvalidRule("position must be a whole number")
    if int(position) != position or int(position) < 0:
        raise InvalidRule(f"position must be a whole number of at least 0, got {position!r}")

    return {
        "name": name,
        "kind": kind,
        "match": criteria,
        "notify": notify,
        "position": int(position),
        "stop": bool(payload.get("stop", True)),
        "note": _text(payload.get("note")),
    }


def rule_matches(rule: dict[str, Any], account: Any, watchlist: dict[str, Any] | None) -> bool:
    """Whether a rule's criteria all hold for an account.

    Compared case-insensitively as text, because every criterion this build reads
    is a label somebody typed rather than an identifier.
    """
    criteria = rule.get("match") or {}
    if not criteria:
        return True
    observed = {
        "segment": getattr(account, "segment", "") or "",
        "country": ",".join(getattr(account, "countries", ()) or ()),
        "size": getattr(account, "size", "") or "",
        "team": getattr(account, "owner_team", "") or "",
        "company_key": getattr(account, "company_key", "") or "",
    }
    for key, expected in criteria.items():
        actual = str(observed.get(key, "")).lower()
        if key == "country":
            if expected not in [part.strip() for part in actual.split(",")]:
                return False
            continue
        if actual != expected:
            return False
    if watchlist is None and "tier" in criteria:
        return False
    return True


def rule_recipients(rule: dict[str, Any], account: Any, room_default: str) -> list[Recipient]:
    """The recipients one rule produces, in the order the rule lists them."""
    kind = str(rule.get("kind") or "")
    who = str(rule.get("name") or "")
    if kind == RULE_CRM_OWNER:
        owner = getattr(account, "owner_id", "") or ""
        email = getattr(account, "owner_email", "") or ""
        if not owner:
            return []
        return [
            Recipient(
                kind=kind,
                who=owner,
                address=email,
                rule_id=str(rule.get("id") or ""),
                rule_name=who,
            )
        ]
    return [
        Recipient(
            kind=kind,
            who=who,
            address=address,
            rule_id=str(rule.get("id") or ""),
            rule_name=who,
        )
        for address in (rule.get("notify") or [])
    ]


#: The rule that is always in the chain. Not stored, not configurable, and not
#: editable: the research names the account owner as the recipient, so the owner is
#: consulted first whether or not a seller has configured anything.
IMPLICIT_OWNER_RULE = {"id": "", "name": "Opportunity owner", "kind": RULE_CRM_OWNER}


def _implicit_owner_rule() -> dict[str, Any]:
    return dict(IMPLICIT_OWNER_RULE)


def recipients_for(
    rules: list[dict[str, Any]],
    account: Any,
    *,
    watchlist: dict[str, Any] | None,
    room_default: str,
    watchlist_notify: list[str] | None = None,
) -> dict[str, Any]:
    """Walk the chain and report every recipient, and which rule produced it.

    **The opportunity owner goes first, and is not configurable.** The researched
    flow names that recipient explicitly: "the account owner gets an email and a
    Slack DM". The owner is a property of the deal rather than a preference, so
    making it a rule a seller has to remember to add would mean the researched
    behaviour only happens when somebody configured it. It is therefore injected
    ahead of the chain, and a configured ``crm_owner`` rule that names the same
    owner is removed by the de-duplication below rather than being an error.

    The rest of the chain is read to the end rather than stopped at the first
    match, because an alert that names one owner and silently drops the territory
    team is a routing decision nobody can inspect. A rule that says ``stop`` ends
    the walk for the rules after it; a rule that does not adds to the list. The
    default fallback address is added last and only when nothing else produced an
    address, so an alert never reaches nobody.
    """
    ordered = sorted(
        rules, key=lambda rule: (int(rule.get("position") or 0), str(rule.get("name")))
    )
    produced: list[Recipient] = list(rule_recipients(_implicit_owner_rule(), account, room_default))
    consulted: list[str] = [IMPLICIT_OWNER_RULE["name"]] if produced else []
    stopped = False

    for rule in ordered:
        if stopped:
            break
        if not rule_matches(rule, account, watchlist):
            continue
        if str(rule.get("kind") or "") == RULE_WATCHLIST and watchlist is None:
            continue
        if str(rule.get("kind") or "") == RULE_FALLBACK and room_default:
            produced.append(
                Recipient(
                    kind=RULE_FALLBACK,
                    who="room default",
                    address=room_default,
                    rule_id=str(rule.get("id") or ""),
                    rule_name=str(rule.get("name") or ""),
                )
            )
            consulted.append(str(rule.get("name") or ""))
            stopped = True
            continue
        found = rule_recipients(rule, account, room_default)
        consulted.append(str(rule.get("name") or ""))
        produced.extend(found)
        if bool(rule.get("stop", True)):
            stopped = True

    if watchlist_notify and not any(entry.deliverable for entry in produced):
        produced.extend(
            Recipient(
                kind=RULE_WATCHLIST,
                who=str(watchlist.get("name") or "watchlist"),
                address=address,
                rule_name=str(watchlist.get("name") or ""),
            )
            for address in watchlist_notify
        )

    seen: list[Recipient] = []
    for entry in produced:
        if entry.address:
            if any(other.address == entry.address for other in seen):
                continue
        elif any(not other.address and other.who == entry.who for other in seen):
            continue
        seen.append(entry)

    if not any(entry.deliverable for entry in seen) and room_default:
        seen.append(Recipient(kind=RULE_FALLBACK, who="room default", address=room_default))

    return {
        "recipients": [entry.as_dict() for entry in seen],
        "consulted": consulted,
        "stopped_early": stopped and len(consulted) < len(ordered),
        "deliverable": any(entry.deliverable for entry in seen),
    }


# --------------------------------------------------------------------------- #
# Contact suppression
# --------------------------------------------------------------------------- #


def suppressed_until(
    store: RecordStore,
    *,
    room_id: str,
    company_key: str,
    addresses: list[str],
    hours: int = SUPPRESSION_HOURS,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Whether these addresses were already told about this account, and until when.

    Derived rule, stated plainly: **one account, one room, one recipient, twenty-four
    hours**. The research says "auto-suppress contacted accounts" and gives no
    window. Twenty-four hours is one working day, which is the shortest span in
    which a second alert about the same account is noise rather than news.

    The window is measured from the alert that was dispatched, not from the moment
    it was written, because a rep who was told an hour ago is still the person who
    was told. Suppression is per recipient rather than per account, so a second
    recipient who has not been told is still told.
    """
    reference = now or datetime.now(timezone.utc)
    cutoff = reference - timedelta(hours=hours)
    wanted = {address.lower() for address in addresses if address}
    if not wanted:
        return {"suppressed": False, "reason": "", "until": "", "hours": hours}

    for record in store.list(ALERTS, room_id=room_id, limit=1000, order_by="updated_at"):
        data = record.get("data") or {}
        if str(data.get("company_key") or "") != company_key:
            continue
        dispatched_at = _parse_moment(data.get("dispatched_at"))
        if dispatched_at is None or dispatched_at < cutoff:
            continue
        for entry in data.get("recipients") or []:
            address = str((entry or {}).get("address") or "").lower()
            if address and address in wanted:
                until = dispatched_at + timedelta(hours=hours)
                return {
                    "suppressed": True,
                    "reason": (
                        f"{address} was already alerted about {company_key} at "
                        f"{data.get('dispatched_at')} and the window is {hours} hours"
                    ),
                    "until": until.isoformat(),
                    "hours": hours,
                }
    return {"suppressed": False, "reason": "", "until": "", "hours": hours}


def _parse_moment(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


# --------------------------------------------------------------------------- #
# The dispatch decision
# --------------------------------------------------------------------------- #


def dispatch_for(channel: str, recipients: list[dict[str, Any]]) -> dict[str, Any]:
    """Decide what happens on one channel, and say why.

    Four outcomes and no fifth.

    ``queued`` is the only one that means a message exists. This product has no
    outbound mail or Slack transport, so a state called ``sent`` would be a claim
    nothing in the codebase could support. The queued record is a real outbox row
    with a recipient, a subject and a body, and swapping a transport in behind it
    is an implementation of the same call rather than a change to this workflow.

    ``held_for_integration`` is what Slack gets. Slack is named as an API by the
    research and quoted by three of the nine sources it cites, but this product has
    no confirmed Slack surface and no Slack token, so the dispatch is recorded with
    the reason rather than silently omitted. A named recipient that is left out of
    the record is an alert that looks like it reached someone when it did not.

    ``skipped`` is the case the research's data sources make real: the accountable
    party is known and has no address on file.
    """
    addressable = [entry for entry in recipients if str(entry.get("address") or "")]
    if not addressable:
        who = ", ".join(str(entry.get("who") or "") for entry in recipients) or "nobody"
        return {
            "channel": channel,
            "state": "skipped",
            "to": "",
            "reason": (
                f"{who} is accountable for this account and has no {channel} address on file. "
                "The CRM carries an owner id, not an address for it."
            ),
            "queued_at": "",
        }
    if channel == SLACK_CHANNEL:
        return {
            "channel": channel,
            "state": "held_for_integration",
            "to": ", ".join(str(entry.get("address") or "") for entry in addressable),
            "reason": (
                "This product has no Slack surface and no Slack credential, so the direct message "
                "is recorded here and not attempted. Email is the channel this build delivers."
            ),
            "queued_at": "",
        }
    return {
        "channel": channel if channel == EMAIL_CHANNEL else channel,
        "state": DISPATCH_QUEUED,
        "to": ", ".join(str(entry.get("address") or "") for entry in addressable),
        "reason": "",
        "queued_at": "",
    }


def channel_states(dispatches: list[dict[str, Any]]) -> dict[str, str]:
    """One state per published channel, for the alert record and for tests."""
    return {
        str(entry.get("channel")): str(entry.get("state"))
        for entry in dispatches
        if entry.get("channel") in NOTIFICATION_CHANNELS
    }


__all__ = [
    "IMPLICIT_OWNER_RULE",
    "MATCH_KEYS",
    "Recipient",
    "channel_states",
    "dispatch_for",
    "normalise_rule",
    "recipients_for",
    "rule_matches",
    "rule_recipients",
    "suppressed_until",
]
