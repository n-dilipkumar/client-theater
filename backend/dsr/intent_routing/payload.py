"""The alert payload: which pages, how long, which stakeholder.

The research quotes the alert context exactly, and this module is only that quote.
"the account owner gets an email and a Slack DM with the context ("which pages, how
long, which stakeholder")". Three facts, so the payload carries three facts under
those three names, plus the arithmetic behind them.

Nothing else is in the payload. Not the CRM opportunity's stage, not the seller's
whole engagement history, not a scoring breakdown. The reason is that a payload
grows: every field added is one more thing a rep skims past, and the three quoted
facts are the three a rep can act on without opening the CRM. Anything the rep
needs beyond those three belongs on the signal record, which is linked, rather than
in the alert, which is read on a phone.
"""

from __future__ import annotations

from typing import Any

#: The three researched facts, in the order the research quotes them.
PAYLOAD_FIELDS: tuple[str, ...] = ("pages", "dwell", "stakeholder")


def payload_for(
    engagement: Any,
    account: Any,
    stakeholder: dict[str, str],
    crossings: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the alert payload for one signal.

    ``pages`` is the distinct pages the buyer read, in the order they reached
    them, with the seconds spent on each where the observation recorded them. The
    order is kept because "which pages" reads as a path through the room rather
    than as a set, and a rep who sees ``/overview`` then ``/pricing`` knows
    something a rep who sees the same two pages unordered does not.

    ``dwell`` carries both the single longest page read and the window total,
    because the sourced threshold is on the longest read and a rep asking "how long
    were they in here" means the total. Reporting only one of the two makes the
    other a question the alert cannot answer.
    """
    return {
        "pages": {
            "visited": list(getattr(engagement, "pages", ()) or ()),
            "count": len(getattr(engagement, "pages", ()) or ()),
            "seconds_by_path": _seconds_by_path(engagement),
        },
        "dwell": {
            "longest_page_seconds": int(getattr(engagement, "dwell_seconds", 0) or 0),
            "window_total_seconds": int(getattr(engagement, "total_dwell_seconds", 0) or 0),
            "threshold_seconds": int(
                next(
                    (entry["threshold"] for entry in crossings if entry["kind"] == "dwell"),
                    0,
                )
            ),
            "last_seen_at": _moment(getattr(engagement, "last_seen_at", None)),
        },
        "stakeholder": {
            "name": stakeholder.get("name", ""),
            "role": stakeholder.get("role", ""),
            "identified": stakeholder.get("identified", "false"),
            "source": stakeholder.get("source", "unknown"),
            "addressable": False,
            "note": (
                "No address. The visitor-identification workflow keeps company contacts as a name "
                "and a role by design, so an anonymous stakeholder cannot be written to."
            ),
        },
        "account": {
            "company_key": getattr(account, "company_key", ""),
            "name": getattr(account, "company_name", ""),
            "opportunity_id": getattr(account, "opportunity_id", ""),
            "accountable": getattr(account, "accountable", ""),
        },
        "triggered_by": [dict(entry) for entry in crossings],
    }


def _seconds_by_path(engagement: Any) -> dict[str, int]:
    """Seconds per page, when the observation recorded a breakdown.

    The observation carries one ``dwell_seconds`` for the longest page rather than a
    per-path map, because that is what the threshold reads and a per-path map would
    make the caller assemble something this workflow then has to trust. A caller
    that does know the breakdown may supply one, and it is passed through untouched.
    """
    raw = getattr(engagement, "seconds_by_path", None)
    if isinstance(raw, dict):
        return {str(path): int(value or 0) for path, value in raw.items()}
    return {}


def _moment(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else ""


def subject_for(payload: dict[str, Any], room_label: str = "") -> str:
    """The one-line subject of the email alert.

    Plain ASCII, and no arrow. The seeder prints every character a feature's
    ``seed`` returns on a Windows console, and a rightwards arrow in one recovered
    feature broke the whole seeder, so this module holds no character outside
    cp1252 in any string the seeder can reach. The same rule applies here because
    the subject is stored in a record the seeder prints a sample of.
    """
    stakeholder = payload.get("stakeholder") or {}
    pages = payload.get("pages") or {}
    dwell = payload.get("dwell") or {}
    who = stakeholder.get("name") or payload.get("account", {}).get("name") or "A buyer"
    where = room_label or "your room"
    count = int(pages.get("count") or 0)
    seconds = int(dwell.get("longest_page_seconds") or 0)
    return f"{who} read {count} page(s) in {where}, {seconds}s on the longest"


def body_for(payload: dict[str, Any], room_label: str = "") -> str:
    """The body of the email alert, as plain text.

    Plain text on purpose. The alert is read on a phone between meetings, and a
    multipart alternative would be a second rendering of the same three facts to
    keep in step with the first.
    """
    stakeholder = payload.get("stakeholder") or {}
    pages = payload.get("pages") or {}
    dwell = payload.get("dwell") or {}
    account = payload.get("account") or {}
    triggered = payload.get("triggered_by") or []

    lines = [
        subject_for(payload, room_label),
        "",
        "Which pages",
    ]
    visited = pages.get("visited") or []
    if visited:
        lines.extend(f"  - {path}" for path in visited)
    else:
        lines.append("  - none recorded in this window")

    lines.extend(
        [
            "",
            "How long",
            (
                f"  {dwell.get('longest_page_seconds', 0)}s on the longest page, "
                f"{dwell.get('window_total_seconds', 0)}s in total, "
                f"last seen {dwell.get('last_seen_at') or 'at an unrecorded time'}"
            ),
            "",
            "Which stakeholder",
            (
                f"  {stakeholder.get('name') or 'not identified'}"
                + (f", {stakeholder.get('role')}" if stakeholder.get("role") else "")
            ),
            "",
            "Why this reached you",
        ]
    )
    lines.extend(
        f"  - {entry.get('label')}: {entry.get('observed')} {entry.get('unit')} "
        f"(threshold {entry.get('threshold')})"
        for entry in triggered
    )
    lines.extend(
        [
            "",
            (
                f"Account {account.get('name') or account.get('company_key')}"
                + (
                    f", opportunity {account['opportunity_id']}"
                    if account.get("opportunity_id")
                    else ""
                )
            ),
            "A follow-up task is on the opportunity. This product records alerts; it does not",
            "send mail on your behalf.",
        ]
    )
    return "\n".join(lines)


__all__ = ["PAYLOAD_FIELDS", "body_for", "payload_for", "subject_for"]
