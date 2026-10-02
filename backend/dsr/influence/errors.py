"""WF-019's own error types.

Every refusal in :mod:`dsr.influence` is one of these. They are this feature's
classes, raised by nothing else in the product, which is what makes registering
an ``EXCEPTION_HANDLERS`` mapping for them safe: a handler for a type nothing
else raises cannot intercept an unrelated error anywhere else.

The split matters more than the names:

* :class:`UnknownRoom` and :class:`UnknownAsset` are *naming* failures. The
  caller pointed at something that is not there, so the answer is 404.
* :class:`UnparseableTime` is a *parsing* failure and answers 400 with the
  offending value in the message, because the researched report's two
  independent time windows are the most common thing a client gets wrong.
* :class:`CrmNotLinked` is the research's own precondition, not a mistake:
  "This report will be shown assuming you have integrated with your CRM, and have
  connected accounts & deals/opportunities to workspaces."  Asking for the
  breakdown in strict mode before that is true is asking for something this
  report cannot produce, and the codebase already has a status for "not
  configured yet" (428, as WF-006 uses for a missing analytics token).
* :class:`InfluenceError` is the base and answers 400, so a caller that hits a
  refusal added later still gets a sensible status rather than a 500.
"""

from __future__ import annotations

from typing import Any


class InfluenceError(Exception):
    """Base for every refusal this workflow makes. 400."""


class UnknownRoom(InfluenceError):
    """A room id that does not resolve to a live room. 404."""

    def __init__(self, room_id: str) -> None:
        super().__init__(f"room {room_id!r} not found")
        self.room_id = room_id


class UnknownAsset(InfluenceError):
    """A library asset id that does not resolve to a live asset. 404."""

    def __init__(self, asset_id: str) -> None:
        super().__init__(f"content asset {asset_id!r} not found")
        self.asset_id = asset_id


class UnparseableTime(InfluenceError):
    """A timestamp or window bound that is not ISO-8601. 400."""

    def __init__(self, value: object, what: str = "timestamp") -> None:
        super().__init__(f"{what} {value!r} is not an ISO-8601 timestamp")
        self.value = value
        self.what = what


class InvalidWindow(InfluenceError):
    """A time window whose start is after its end. 400."""

    def __init__(self, what: str, start: object, end: object) -> None:
        super().__init__(f"{what} window starts after it ends: {start} > {end}")
        self.what = what


class CrmNotLinked(InfluenceError):
    """Content & Sales Influence asked for before its precondition holds. 428."""

    def __init__(self, blockers: list[dict[str, str]]) -> None:
        detail = "; ".join(blocker["detail"] for blocker in blockers) or "no CRM links are recorded"
        super().__init__(f"Content & Sales Influence is unavailable: {detail}")
        self.blockers = blockers


class AssetOutOfScope(UnknownAsset):
    """A real library asset that the active filters exclude. 404.

    A subclass rather than a new status, so one handler covers both "no such
    asset" and "that asset is real but your collection filter excludes it" - but
    the two need different messages, because they send a reader to different
    places: one to fix an id, the other to widen a filter.
    """

    def __init__(self, asset_id: str, reason: str) -> None:
        super().__init__(asset_id)
        self.reason = reason
        self.args = (f"content asset {asset_id!r} is in the library but out of scope: {reason}",)


class UnknownChoice(InfluenceError):
    """An enumerated choice the report does not accept. 400.

    A chart grain, a Top content sort column, a sort direction. These are not
    timestamps, so they do not borrow
    :class:`UnparseableTime`'s message, and the message always names the set
    that would have been accepted.
    """

    def __init__(self, value: object, what: str, allowed: Any = ()) -> None:
        allowed = tuple(allowed)
        detail = f"{what} {value!r} is not supported"
        if allowed:
            detail += "; expected one of " + ", ".join(str(item) for item in allowed)
        super().__init__(detail)
        self.value = value
        self.what = what
        self.allowed = allowed


class InfluenceNotLinked(UnknownAsset):
    """A link names a library asset that does not exist. 404.

    A subclass of :class:`UnknownAsset` on purpose, so it inherits the one
    handler rather than claiming a second one for what is the same fact - a
    caller asked about an asset and the asset is not there. The message names
    which side of the link is broken, because the caller posted a CRM payload
    and the asset it named is the part that is wrong.
    """

    def __init__(self, asset_id: str) -> None:
        super().__init__(asset_id)
        # `UnknownAsset` formats the id into its own message, and this subclass
        # needs a different one - so the text is set here rather than passed up.
        self.args = (
            f"link names content asset {asset_id!r}, which is not in the library; "
            "connect the asset before linking a deal to it",
        )
        self.asset_id = asset_id
