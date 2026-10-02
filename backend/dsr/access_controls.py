"""WF-014: expire or cap access to a room.

Two constraints bound a room's link, and they compose:

* **Expiry** - a number of days, counted from the moment the page goes live.
* **A view cap** - a number of times the page may be opened.

Both are independent switches. Either can be on without the other, both can be on
at once, and a room may carry both, expire first, hit its cap first, or do
neither. The research calls them out as "two orthogonal, independently toggleable
constraints (time and count) that compose", and this module keeps them orthogonal:
nothing in the expiry path reads the cap, and nothing in the cap path reads the
expiry.

The rule this module exists to get right
----------------------------------------

**A status is not a claim that a link works.** The two come apart, and the
research is explicit that they do:

    "Once your client reaches the view limit, you'll see the status on your
    dashboard change to View Limit. The Page will retain a Live status, although
    it's been disabled by the view limit."

    "...so `Live` does not imply accessible."

So a page whose badge reads ``live`` may still be closed to every buyer, and the
badge alone can never decide whether a link opens. Everything a buyer experiences
is therefore *derived on read*, from the stored window plus a fresh count, rather
than read off a status field written once and left to rot.

Deriving on read is also the only correct choice against the vendor's own event
enum, which contains no expiry signal at all:

    "pageAccepted" "pagePartiallyAccepted" "pagePreviewAccepted" "pageViewed"
    "pageFirstViewed" "pageSetLive" "pageRevivedLive"

``pageDeclined`` is not published. Expiry-driven decline is a poll-only signal. A
build that waited for an event the vendor does not send would leave an expired
page open forever, which is the failure this whole workflow exists to prevent.

What is stored, and what is not
-------------------------------

Stored on the page record under ``data.access_window``::

    {"expiry":      {"enabled": bool, "days": int | None, "starts_at": str | None},
     "view_limit":  {"enabled": bool, "max_views": int | None},
     "manual_status": "live" | "declined" | None}

Everything else - ``expires_at``, the badge, whether a buyer may in, what a buyer
is told - is derived. In particular this feature never writes the page's own
``status``: that field belongs to ``dsr.pages`` and to the editor, and its
vocabulary there is ``draft`` / ``published``. This feature answers a different
question - whether the link works right now - and it answers it without editing
another feature's field. The consequence is worth stating plainly, because it is
the kind of thing a reviewer should not have to discover from the diff: a seller
reading ``status`` off a page record still sees ``published`` for a page that
expired an hour ago. They read ``access_status`` here instead, and
``GET /api/wf-014/pages?status=`` filters on it the way the vendor's
``GET /v1/pages?status=`` filters on the dashboard badge.

Where the research left a gap
-----------------------------

Five points are inferences rather than sourced rules. Each is marked at its
definition below with the reading taken and the reason, and all five are served
over ``GET /api/wf-014/vocabulary`` so a reviewer can check the claim against the
code that enforces it rather than against this docstring.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from dsr.db.audited import AuditError
from dsr.permissions import capabilities
from dsr.store import RecordStore

# --------------------------------------------------------------------------- #
# Collections
# --------------------------------------------------------------------------- #

ROOMS = "room"
PAGES = "page"

#: One row per page opening. The cap is an aggregate over this collection, which
#: is why it is counted with ``count_where`` rather than measured by loading rows
#: and taking a length.
#:
#: Deliberately not ``access_session``: that is WF-015's collection for verified
#: buyer sessions, and sharing a name would make two live features read each
#: other's rows.
PAGE_VIEWS = "page_view"

#: The key on a page's ``data`` holding this feature's configuration. Nested so
#: that enabling a cap cannot collide with a field another workflow added.
WINDOW_KEY = "access_window"

# --------------------------------------------------------------------------- #
# The badge vocabulary
# --------------------------------------------------------------------------- #

#: The page has never been published, so there is no link to open. The research
#: calls this ``draft``: "``draft`` selects the pages that you have not
#: published."
STATUS_DRAFT = "draft"

#: Published, unexpired, under its cap. The badge that most pages wear.
STATUS_LIVE = "live"

#: Published and open to buyers, but the link will stop working within
#: ``EXPIRY_WARNING_DAYS``. Not a vendor status: it is the seller-side reading of
#: the same condition the buyer sees as a warning message.
STATUS_EXPIRING = "expiring_soon"

#: Closed. Reached by expiry (automatically) or by a seller declining it by hand.
STATUS_DECLINED = "declined"

#: Published and still badged ``live``, but every buyer gets an error because the
#: cap is spent. The research is precise that the underlying status does *not*
#: change: "The Page will retain a Live status, although it's been disabled by
#: the view limit." So this is a badge, and the page's stored status is untouched.
STATUS_VIEW_LIMIT = "view_limit"

#: Every badge this feature can produce. Exposed over HTTP so a client never
#: hard-codes the list, which is how a second vocabulary starts.
ACCESS_STATUSES: tuple[str, ...] = (
    STATUS_DRAFT,
    STATUS_LIVE,
    STATUS_EXPIRING,
    STATUS_DECLINED,
    STATUS_VIEW_LIMIT,
)

#: What a buyer is doing, as opposed to what a seller's dashboard shows.
#:
#: ``open`` and ``expiring_soon`` show content. The other three replace it with an
#: error message, which is what the research describes: "Once that time is up,
#: the link will display an error message instead."
BUYER_OPEN = "open"
BUYER_EXPIRING_SOON = "expiring_soon"
BUYER_EXPIRED = "expired"
BUYER_VIEW_LIMIT = "view_limit"
BUYER_DECLINED = "declined"
BUYER_UNPUBLISHED = "unpublished"

BUYER_STATES: tuple[str, ...] = (
    BUYER_OPEN,
    BUYER_EXPIRING_SOON,
    BUYER_EXPIRED,
    BUYER_VIEW_LIMIT,
    BUYER_DECLINED,
    BUYER_UNPUBLISHED,
)

# --------------------------------------------------------------------------- #
# Constants the research fixes
# --------------------------------------------------------------------------- #

#: "When your client views the page, if the expiration date is within 7 days
#: they'll see a persistent message in the bottom left corner." The same seven-day
#: horizon Liferay uses for per-person access: "When someone's access expires
#: within seven days, their row shows a warning label."
EXPIRY_WARNING_DAYS = 7

#: "The count of days starts when you publish the page."
EXPIRY_STARTS_AT_PUBLISH = "publish"

#: A day window outside this range is a seller mistake rather than an intent, and
#: a cap beyond it cannot be reached in the life of a sales room. Both are
#: rejected with a 400 rather than clamped, so a caller that fat-fingers a field
#: finds out instead of silently getting a different window than it asked for.
MAX_EXPIRY_DAYS = 3650
MAX_VIEWS = 10_000_000

# --------------------------------------------------------------------------- #
# Refusals
# --------------------------------------------------------------------------- #


class AccessWindowRefusal(RuntimeError):
    """Base for every refusal this feature raises.

    Its own type, so the plugin host's exception-handler collision check stays
    clear: ``RoomRefusal`` belongs to WF-005, ``PolicyError`` to WF-015, and a
    second feature mapping either would fail to mount.
    """


class AccessWindowNotFound(AccessWindowRefusal):
    """No such page, or no such room."""


class AccessWindowForbidden(AccessWindowRefusal):
    """The caller may not change this window."""


class AccessWindowInvalid(AccessWindowRefusal):
    """The requested window is not one this workflow can hold."""


class AccessWindowConflict(AccessWindowRefusal):
    """The window is already in the state the request assumes it is not in."""


#: Subclass -> HTTP status, and the error code each one is reported under.
#: Split rather than collapsed into one 4xx so a caller's next step is guessable:
#: 404 means look up a different id, 403 means ask someone else, 400 means fix
#: the payload, 409 means re-read the state you are acting on.
REFUSAL_STATUS: dict[type, tuple[int, str]] = {
    AccessWindowNotFound: (404, "page_not_found"),
    AccessWindowForbidden: (403, "access_window_forbidden"),
    AccessWindowInvalid: (400, "invalid_access_window"),
    AccessWindowConflict: (409, "access_window_conflict"),
}

# --------------------------------------------------------------------------- #
# Instants
# --------------------------------------------------------------------------- #


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_instant(value: Any) -> datetime | None:
    """Read an ISO-8601 instant as UTC, or ``None`` if there is not one there.

    A timestamp with no offset is read as UTC rather than as this machine's local
    time. The research fixes the cut-off in UTC - "Access ends at the end of the
    expiration date in UTC" - so a seller's window must not depend on the time
    zone of whichever server happened to evaluate it.
    """
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def end_of_utc_day(moment: datetime) -> datetime:
    """The last instant of the UTC day ``moment`` falls in.

    ``23:59:59.999999`` rather than ``23:59:59`` because the instant after it is
    midnight and a one-microsecond gap at the boundary would leave an instant
    that is neither in the last day nor the first.
    """
    return moment.replace(hour=23, minute=59, second=59, microsecond=999999)


def compute_expires_at(starts_at: datetime, days: int) -> datetime:
    """When a window of ``days`` days, opened at ``starts_at``, closes.

    The boundary is the *end of the UTC day*, not the same clock time ``days``
    later. Inference, and the reading this build takes:

    * The only sourced statement that pins the boundary says "Access ends at the
      end of the expiration date in UTC" (Liferay). It defines an instant on the
      expiry date, not a duration.
    * The Qwilr phrasing - "the link of the page stops working after the number
      of days that you set" - reads as elapsed time and would cut a page published
      at 09:00 off at 09:00 on the last day.

    End-of-day is taken because it is the only rule that answers the question a
    seller actually asks ("does it die on the 1st or at 9am on the 1st?"), and
    because a seller who sets "30 days" expects the link to work for 30 whole
    days. It is also the rule that makes "expires at" a single testable instant
    rather than a comparison of two clocks.

    Consequence, stated so nobody has to find it in the diff: a page published at
    09:00 UTC on 2 October with ``days=30`` expires at 23:59:59.999999 UTC on 1
    November, and is open for every instant up to and including that one.
    """
    return end_of_utc_day(starts_at + timedelta(days=days))


# --------------------------------------------------------------------------- #
# Reading stored configuration
# --------------------------------------------------------------------------- #


def _int_or_none(value: Any) -> int | None:
    """Coerce a JSON number or numeric string to ``int``, or ``None``.

    ``bool`` is rejected explicitly: ``True`` is an ``int`` in Python, and a
    caller who sends ``{"days": true}`` has made a mistake worth reporting rather
    than reading as one day.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str):
        text = value.strip()
        if text.lstrip("+-").isdigit():
            return int(text)
    return None


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def window_of(page: Mapping[str, Any] | None) -> dict[str, Any]:
    """This feature's stored configuration on a page, always a complete shape.

    A page that has never been configured reads as both constraints off, so
    callers never branch on the key being absent.
    """
    data = _mapping(_mapping(page).get("data"))
    stored = _mapping(data.get(WINDOW_KEY))
    expiry = _mapping(stored.get("expiry"))
    limit = _mapping(stored.get("view_limit"))
    manual = str(stored.get("manual_status") or "").strip().lower() or None
    return {
        "expiry": {
            "enabled": bool(expiry.get("enabled")),
            "days": _int_or_none(expiry.get("days")),
            "starts_at": expiry.get("starts_at") or None,
        },
        "view_limit": {
            "enabled": bool(limit.get("enabled")),
            "max_views": _int_or_none(limit.get("max_views")),
        },
        "manual_status": manual if manual in ("live", "declined") else None,
    }


def page_is_published(page: Mapping[str, Any] | None) -> bool:
    """Whether the page has been published, by ``dsr.pages``' own rule.

    ``dsr.pages.PageService._next_status`` answers this as "published if
    ``published_revision_id`` else draft", and that is the editor's truth about
    this record. Restating it here as a different test is how two features end up
    disagreeing about whether a page exists, so the same field is read instead.
    ``test_wf014.py`` pins the agreement rather than trusting it.
    """
    return bool(_mapping(_mapping(page).get("data")).get("published_revision_id"))


def view_count(store: RecordStore, page_id: str) -> int:
    """How many times this page has been opened.

    Counted with ``count_where`` over the dynamic index, not
    ``len(list(...))``. This is the aggregate the promoted ``count_where``
    capability exists for, and the shape of the gap WF-009 recorded in its own
    commit message: "The other, count, has no honest equivalent on the facade ...
    a capped ``len(list(...))`` would quietly downgrade it."

    Fetching the rows to measure them would also be wrong on its own terms: it
    loads every view ever recorded to answer a question about none of them.
    """
    return store.count_where(PAGE_VIEWS, {"page_id": page_id})


# --------------------------------------------------------------------------- #
# The derived window
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class AccessWindow:
    """One page's access window, resolved at a single instant.

    A pure value: it holds the count it was given and answers every question
    about it without touching the store again. That is what makes the buyer
    experience testable - a buyer view is this object at a chosen ``now``, with
    no clock of its own and no database behind it.
    """

    page_id: str
    room_id: str
    published: bool
    manual_status: str | None
    expiry_enabled: bool
    expiry_days: int | None
    starts_at: datetime | None
    view_limit_enabled: bool
    max_views: int | None
    views: int
    now: datetime

    # -- derived instants ---------------------------------------------------- #

    @property
    def expires_at(self) -> datetime | None:
        """The instant the link stops working, or ``None`` if it never will.

        ``None`` unless every input is present: expiry enabled, a day count, and a
        moment to count from. A draft page has no ``starts_at``, because "A draft
        page keeps the setting until you publish it" - the count has not begun,
        so there is nothing to compare against and a draft never expires.
        """
        if not self.expiry_enabled or not self.expiry_days or not self.starts_at:
            return None
        return compute_expires_at(self.starts_at, self.expiry_days)

    @property
    def days_remaining(self) -> int | None:
        """Whole days left, rounded up, or ``None`` with no window open.

        Rounded up because a buyer with four hours left is told "1 day remaining",
        not "0 days" - a countdown that reads zero while the link still works is
        a bug a user can see.
        """
        expires_at = self.expires_at
        if expires_at is None:
            return None
        seconds = (expires_at - self.now).total_seconds()
        if seconds <= 0:
            return 0
        return -(-int(seconds) // 86400)

    # -- the two constraints, independently ---------------------------------- #

    @property
    def expired(self) -> bool:
        """Past the expiry instant.

        Strictly after: at exactly ``expires_at`` the page is still open, because
        that microsecond is the last one the window covers. This is the boundary
        the end-of-day rule exists to make unambiguous.
        """
        expires_at = self.expires_at
        return expires_at is not None and self.now > expires_at

    @property
    def capped(self) -> bool:
        """The view budget is spent.

        ``>=``, not ``>``: a cap of one view is met by the first view. Qwilr:
        "Once that number has been reached, your clients will see an error
        message when they view the page."
        """
        if not self.view_limit_enabled or not self.max_views:
            return False
        return self.views >= self.max_views

    @property
    def expiring_soon(self) -> bool:
        """Inside the seven-day warning horizon and still open."""
        expires_at = self.expires_at
        if expires_at is None or self.expired:
            return False
        return expires_at - self.now <= timedelta(days=EXPIRY_WARNING_DAYS)

    # -- the badge, which is not the same question --------------------------- #

    @property
    def reasons(self) -> tuple[str, ...]:
        """Every reason the link is closed, in the order they take effect.

        More than one is normal and is the case worth surfacing: a page can be
        both expired and over its cap, and a seller who only sees one of them
        will remove the wrong constraint.
        """
        found: list[str] = []
        if not self.published:
            found.append(BUYER_UNPUBLISHED)
        if self.expired:
            found.append(BUYER_EXPIRED)
        if self.capped:
            found.append(BUYER_VIEW_LIMIT)
        if self.manual_status == "declined":
            found.append(BUYER_DECLINED)
        return tuple(found)

    @property
    def status(self) -> str:
        """The dashboard badge.

        Ordered, and the order is the product's judgement:

        1. An unpublished page is ``draft``. Nothing else can be true of a link
           that was never opened.
        2. Expiry wins over the cap. It is the only constraint that *moves* the
           status - "On the expiration date, the page will automatically switch
           to a Declined status" - while the cap only masks one, so a page that
           did both is a page that declined on a date. Inference: the research
           never states which badge wins when both land, and a room whose clock
           ran out on a Tuesday should not read as merely view-limited.
        3. A hand-declined page is ``declined`` for the second documented reason:
           "You'll see this status on your dashboard for two reasons: The page
           has reached a link expiration you've set; You've manually set the page
           as Declined."
        4. Only then the cap, and only as a badge - the stored status stays
           ``live`` because the vendor is explicit that it does.
        5. Otherwise ``live``, which is the one badge that still does not promise
           a working link.
        """
        if not self.published:
            return STATUS_DRAFT
        if self.expired:
            return STATUS_DECLINED
        if self.manual_status == "declined":
            return STATUS_DECLINED
        if self.capped:
            return STATUS_VIEW_LIMIT
        if self.expiring_soon:
            return STATUS_EXPIRING
        return STATUS_LIVE

    @property
    def accessible(self) -> bool:
        """Whether a buyer may open this page at ``now``.

        Read the badge and you can still be wrong: ``live`` with a spent cap is
        closed. This is the predicate the buyer route answers with, and the only
        place in the product that should decide the question.
        """
        return not self.reasons

    @property
    def buyer_state(self) -> str:
        """What the buyer is shown, which is not what the seller is shown."""
        if not self.published:
            return BUYER_UNPUBLISHED
        if self.expired:
            return BUYER_EXPIRED
        if self.capped:
            return BUYER_VIEW_LIMIT
        if self.manual_status == "declined":
            return BUYER_DECLINED
        if self.expiring_soon:
            return BUYER_EXPIRING_SOON
        return BUYER_OPEN

    @property
    def buyer_message(self) -> str:
        """The sentence a buyer reads in place of, or above, the page.

        The research pins the placement of the warning - "a persistent message in
        the bottom left corner" - and the replacement behaviour for a closed
        link: "the link will display an error message instead." The copy here is
        this product's, not the vendor's; the *placement* and the replacement are
        theirs.
        """
        days = self.days_remaining
        state = self.buyer_state
        if state == BUYER_EXPIRED:
            return (
                "This room's link has expired and is no longer available."
                " Ask the person who shared it with you for a new link."
            )
        if state == BUYER_VIEW_LIMIT:
            return (
                "This room has reached its view limit and is no longer"
                " available. Ask the person who shared it with you for a new link."
            )
        if state == BUYER_DECLINED:
            return (
                "This room is no longer available."
                " Ask the person who shared it with you for a new link."
            )
        if state == BUYER_UNPUBLISHED:
            return "This room has not been published yet."
        if state == BUYER_EXPIRING_SOON:
            if days == 1:
                return "This room's link expires tomorrow."
            if days == 0:
                # Reachable for exactly one microsecond: the page is still open,
                # and "expires in 0 days" is copy nobody should read.
                return "This room's link expires today."
            return f"This room's link expires in {days} days."
        return ""

    def to_dict(self) -> dict[str, Any]:
        """The seller's view of one window.

        Carries both the stored configuration and the derived answers, and says
        which is which, so a reviewer can tell a rule from a setting.
        """
        expires_at = self.expires_at
        return {
            "page_id": self.page_id,
            "room_id": self.room_id,
            "published": self.published,
            "stored_status": "published" if self.published else "draft",
            "access_status": self.status,
            "accessible": self.accessible,
            "closed_by": list(self.reasons),
            "expiry": {
                "enabled": self.expiry_enabled,
                "days": self.expiry_days,
                "starts_at": self.starts_at.isoformat() if self.starts_at else None,
                "expires_at": expires_at.isoformat() if expires_at else None,
                "days_remaining": self.days_remaining,
                "warning_days": EXPIRY_WARNING_DAYS,
                "expiring_soon": self.expiring_soon,
                "clock": EXPIRY_STARTS_AT_PUBLISH,
            },
            "view_limit": {
                "enabled": self.view_limit_enabled,
                "max_views": self.max_views,
                "views": self.views,
                "views_remaining": (
                    max(self.max_views - self.views, 0)
                    if self.view_limit_enabled and self.max_views
                    else None
                ),
                "capped": self.capped,
                "counted_by": "count_where",
            },
            "manual_status": self.manual_status,
        }


def derive(
    page: Mapping[str, Any],
    *,
    views: int,
    now: datetime | None = None,
) -> AccessWindow:
    """Resolve a page record into an :class:`AccessWindow` at ``now``."""
    moment = now or utcnow()
    stored = window_of(page)
    record = _mapping(page)
    return AccessWindow(
        page_id=str(record.get("id") or ""),
        room_id=str(record.get("room_id") or ""),
        published=page_is_published(page),
        manual_status=stored["manual_status"],
        expiry_enabled=stored["expiry"]["enabled"],
        expiry_days=stored["expiry"]["days"],
        starts_at=parse_instant(stored["expiry"]["starts_at"]),
        view_limit_enabled=stored["view_limit"]["enabled"],
        max_views=stored["view_limit"]["max_views"],
        views=int(views),
        now=moment,
    )


# --------------------------------------------------------------------------- #
# Validating a requested window
# --------------------------------------------------------------------------- #


def validate_expiry(enabled: Any, days: Any) -> dict[str, Any]:
    """Check an expiry request, returning the fragment to store.

    Enabling without a usable day count is a 400 rather than a silently disabled
    constraint: a seller who ticks "Enable Link Expiry", leaves the box empty and
    saves must not come back to a link that quietly never expires.
    """
    on = bool(enabled)
    if not on:
        return {"enabled": False, "days": None, "starts_at": None}
    count = _int_or_none(days)
    if count is None:
        raise AccessWindowInvalid(
            "expiry.days must be a whole number of days when expiry is enabled"
        )
    if count < 1:
        raise AccessWindowInvalid(f"expiry.days must be at least 1; got {count}")
    if count > MAX_EXPIRY_DAYS:
        raise AccessWindowInvalid(f"expiry.days must be at most {MAX_EXPIRY_DAYS}; got {count}")
    return {"enabled": True, "days": count, "starts_at": None}


def validate_view_limit(enabled: Any, max_views: Any) -> dict[str, Any]:
    """Check a view-cap request, returning the fragment to store.

    Same reasoning as :func:`validate_expiry`: a cap of zero or a cap of "three"
    is a mistake to report, not a cap to round.
    """
    on = bool(enabled)
    if not on:
        return {"enabled": False, "max_views": None}
    count = _int_or_none(max_views)
    if count is None:
        raise AccessWindowInvalid(
            "view_limit.max_views must be a whole number when the limit is enabled"
        )
    if count < 1:
        raise AccessWindowInvalid(f"view_limit.max_views must be at least 1; got {count}")
    if count > MAX_VIEWS:
        raise AccessWindowInvalid(f"view_limit.max_views must be at most {MAX_VIEWS}; got {count}")
    return {"enabled": True, "max_views": count}


# --------------------------------------------------------------------------- #
# The engine
# --------------------------------------------------------------------------- #


class AccessControls:
    """Read and write access windows over the audited store.

    Every read and write goes through the store, so each change is written with
    its audit row in the same transaction. This feature opens no connection of
    its own and writes no SQL.
    """

    def __init__(self, store: RecordStore) -> None:
        self.store = store

    # -- reads --------------------------------------------------------------- #

    def page(self, page_id: str) -> dict[str, Any]:
        record = self.store.get(page_id)
        if record is None or str(record.get("collection")) != PAGES:
            raise AccessWindowNotFound(f"no page {page_id!r}")
        return record

    def room(self, room_id: str) -> dict[str, Any]:
        record = self.store.get(room_id)
        if record is None or str(record.get("collection")) != ROOMS:
            raise AccessWindowNotFound(f"no room {room_id!r}")
        return record

    def window(self, page_id: str, *, now: datetime | None = None) -> AccessWindow:
        """Resolve one page's window, counting its views as it goes."""
        page = self.page(page_id)
        return derive(page, views=view_count(self.store, page_id), now=now)

    def require_manage(self, page_id: str, *, role: Any, actor: str | None = None) -> dict:
        """Assert the caller may change this window, and return the room.

        Reuses ``dsr.permissions`` rather than inventing a second role model. The
        gate is ``can_manage_status``, which the product already defines as "may
        change this room's settings" for Room Collaborators, Content Contributors
        and instance administrators - the documented "Room Collaborator can manage
        pages and documents".

        The room's own status is passed through, which means an archived room is
        read-only here exactly as it is everywhere else: that room's window
        cannot be changed, only its archive undone.
        """
        page = self.page(page_id)
        room_id = str(page.get("room_id") or "")
        room = self.room(room_id) if room_id else None
        room_status = str(_mapping(_mapping(room).get("data")).get("status") or "active")
        allowed = capabilities(
            role,
            room_status=room_status,
            actor=actor,
            uploaded_by=None,
        )
        if not allowed.can_manage_status:
            raise AccessWindowForbidden(
                f"role {allowed.role_label!r} may not change a room's access window"
            )
        return page

    def windows(
        self,
        *,
        room_id: str | None = None,
        limit: int = 100,
        now: datetime | None = None,
    ) -> list[AccessWindow]:
        """Every window, optionally narrowed to one room.

        One ``count_where`` per page rather than one query for all pages: the
        counting twin of ``find`` answers one collection and one set of
        conditions, and N pages is N cheap indexed counts. Far better than
        materialising every view row to count them.
        """
        rows = (
            self.store.list(PAGES, room_id=room_id, limit=limit)
            if room_id
            else self.store.list(PAGES, limit=limit)
        )
        return [derive(row, views=view_count(self.store, str(row["id"])), now=now) for row in rows]

    # -- writes -------------------------------------------------------------- #

    def _write_window(
        self,
        page_id: str,
        patch: Mapping[str, Any],
        *,
        actor: str | None,
        source: str,
    ) -> AccessWindow:
        """Merge ``patch`` into the stored window and write it once.

        Read-modify-write rather than a whole-window replace, so two features
        editing different halves of the same object cannot erase each other.

        Because that is two steps, the write carries the revision it read. If
        another writer moved the page in between, ``expected_revision`` refuses the
        write and the caller is told to re-read, rather than the second change
        silently overwriting the first. This is the product's own optimistic
        concurrency control, not a lock bolted on here.
        """
        page = self.page(page_id)
        current = window_of(page)
        merged = {**current, **dict(patch)}
        try:
            self.store.update(
                page_id,
                {WINDOW_KEY: merged},
                actor=actor,
                source=source,
                expected_revision=int(page["revision"]),
            )
        except AuditError as exc:
            raise AccessWindowConflict(
                f"page {page_id} changed while its window was being written; re-read it"
            ) from exc
        return self.window(page_id)

    def set_expiry(
        self,
        page_id: str,
        *,
        enabled: Any,
        days: Any,
        role: Any,
        actor: str | None = None,
        now: datetime | None = None,
    ) -> AccessWindow:
        """Turn link expiry on or off, or change its length.

        "Changeable at any time." So this never refuses because of the page's
        current state, and never clears the cap - the two constraints do not read
        each other.

        The clock: "The count of days starts when you publish the page. A draft
        page keeps the setting until you publish it." So ``starts_at`` is stamped
        now if the page is already live, and left unset on a draft, which is what
        makes a draft page keep the setting without having started counting.
        """
        page = self.require_manage(page_id, role=role, actor=actor)
        fragment = validate_expiry(enabled, days)
        moment = now or utcnow()
        fragment["starts_at"] = moment.isoformat() if page_is_published(page) else None
        return self._write_window(
            page_id,
            {"expiry": fragment},
            actor=actor,
            source=f"PUT /api/wf-014/pages/{page_id}/expiry",
        )

    def clear_expiry(self, page_id: str, *, role: Any, actor: str | None = None) -> AccessWindow:
        """Remove the expiry setting entirely, leaving the cap alone."""
        self.require_manage(page_id, role=role, actor=actor)
        return self._write_window(
            page_id,
            {"expiry": {"enabled": False, "days": None, "starts_at": None}},
            actor=actor,
            source=f"DELETE /api/wf-014/pages/{page_id}/expiry",
        )

    def set_view_limit(
        self,
        page_id: str,
        *,
        enabled: Any,
        max_views: Any,
        role: Any,
        actor: str | None = None,
        now: datetime | None = None,
    ) -> AccessWindow:
        """Turn the view cap on or off, or change its size.

        A cap does not start counting from the moment it is set. Qwilr counts
        views of the page, and the research describes the switch being changed at
        any time, so the count is the page's lifetime total and a cap raised above
        today's count re-opens a closed link without clearing anything.
        """
        self.require_manage(page_id, role=role, actor=actor)
        fragment = validate_view_limit(enabled, max_views)
        return self._write_window(
            page_id,
            {"view_limit": fragment},
            actor=actor,
            source=f"PUT /api/wf-014/pages/{page_id}/view-limit",
        )

    def clear_view_limit(
        self, page_id: str, *, role: Any, actor: str | None = None
    ) -> AccessWindow:
        """Remove the cap. The documented way to re-open a view-limited page."""
        self.require_manage(page_id, role=role, actor=actor)
        return self._write_window(
            page_id,
            {"view_limit": {"enabled": False, "max_views": None}},
            actor=actor,
            source=f"DELETE /api/wf-014/pages/{page_id}/view-limit",
        )

    def record_view(
        self,
        page_id: str,
        *,
        viewer: str | None = None,
        at: datetime | None = None,
    ) -> dict[str, Any]:
        """Count one opening of the page, unless it is already closed.

        The one thing in this workflow that a buyer triggers. It refuses when the
        link is closed, and it does not record a row in that case: a page that is
        closed and still accrues views has counted views nobody made, and a cap
        raised later would then open onto an inflated total.

        Not wrapped in a transaction. The check and the write are two operations,
        so two buyers arriving together at the last view could both be admitted
        and leave the count one over the cap. Closing that needs a transaction
        that the store facade does not expose, and inventing one here would mean
        reaching past the facade for a connection the product keeps private. The
        cap is re-evaluated on every read, so the consequence is a page that
        closes one view late, never one that stays open.
        """
        window = self.window(page_id, now=at)
        if not window.accessible:
            raise AccessWindowConflict(
                f"page {page_id} is not open; a buyer is shown {window.buyer_state!r} instead"
            )
        self.store.create(
            PAGE_VIEWS,
            {
                "page_id": page_id,
                "room_id": window.room_id,
                "viewer": viewer,
                "at": (at or utcnow()).isoformat(),
            },
            actor=viewer or "buyer",
            source=f"POST /api/wf-014/pages/{page_id}/views",
        )
        return self.window(page_id, now=at).to_dict()

    # -- revival ------------------------------------------------------------- #

    def decline(self, page_id: str, *, role: Any, actor: str | None = None) -> AccessWindow:
        """Close the link by hand.

        The second documented reason for a Declined badge: "You've manually set
        the page as Declined." Idempotent-safe in the sense that matters -
        declining an already declined page is not a conflict, because the caller's
        intent already holds.
        """
        self.require_manage(page_id, role=role, actor=actor)
        return self._write_window(
            page_id,
            {"manual_status": "declined"},
            actor=actor,
            source=f"POST /api/wf-014/pages/{page_id}/decline",
        )

    def set_live(
        self,
        page_id: str,
        *,
        role: Any,
        actor: str | None = None,
        now: datetime | None = None,
    ) -> AccessWindow:
        """Revive a link: "Share -> Set Live".

        Two consequences, both from the research, and the second is the one that
        surprises people:

        1. **The expiry clock restarts.** "The count of days starts when you
           publish the page", and setting a page live again is publishing it
           again. It has to be, or "You can always set the page Live once again
           if needed" would be false: a page whose ``expires_at`` is still in the
           past would be declined the instant it was revived, and revival would
           be a no-op. So a revived page that has expiry enabled gets a fresh
           window from now.

        2. **The view cap survives.** This is the documented trap:

               "If your page reaches the view limit and then you manually set it
               as Declined, you can still manually set it Live later. If you do
               that, the view limit setting will still in place, so you'll want to
               use the steps above to remove it."

           So reviving clears the hand-declined flag and nothing else. The cap
           stays configured, the page stays closed, and the seller has to remove
           the cap explicitly - which is what "use the steps above to remove it"
           means. A ``set_live`` that quietly cleared the cap would re-open the
           room to buyers against a limit the seller deliberately set, and would
           disagree with the vendor's own documented behaviour.
        """
        self.require_manage(page_id, role=role, actor=actor)
        moment = now or utcnow()
        stored = window_of(self.page(page_id))
        patch: dict[str, Any] = {"manual_status": "live"}
        expiry = stored["expiry"]
        if expiry["enabled"] and expiry["days"]:
            patch["expiry"] = {**expiry, "starts_at": moment.isoformat()}
        return self._write_window(
            page_id,
            patch,
            actor=actor,
            source=f"POST /api/wf-014/pages/{page_id}/set-live",
        )

    # -- the two views ------------------------------------------------------- #

    def buyer_view(self, page_id: str, *, now: datetime | None = None) -> dict[str, Any]:
        """What a buyer opening this link gets, right now.

        The surface this whole workflow exists for: "Buyers see a persistent
        message bottom-left when expiry is within 7 days, and an error message
        once expiry/limit is hit." ``state`` is the branch the frontend takes,
        ``message`` is the sentence, and ``show_content`` says whether the page
        itself is served or replaced by it.

        Takes no role. A buyer reaching a link has not authenticated as anyone in
        particular, and the answer does not depend on who they are - which is also
        why the cap counts openings rather than people.
        """
        window = self.window(page_id, now=now)
        return {
            "page_id": window.page_id,
            "room_id": window.room_id,
            "state": window.buyer_state,
            "show_content": window.accessible,
            "message": window.buyer_message,
            "message_placement": "bottom-left" if window.accessible else "replaces-content",
            "warning": window.buyer_state == BUYER_EXPIRING_SOON,
            "error": not window.accessible,
            "days_remaining": window.days_remaining,
            "expires_at": window.expires_at.isoformat() if window.expires_at else None,
            "views_remaining": window.to_dict()["view_limit"]["views_remaining"],
        }


def vocabulary() -> dict[str, Any]:
    """The rules this workflow applies, readable without a store.

    Exposed so a reviewer can check what the feature claims against the code that
    enforces it, and so the page renders the rules rather than restating them.
    Every claim carries the quote it came from, and every gap is listed as a gap.
    """
    return {
        "constraints": ["expiry", "view_limit"],
        "independent": True,
        "access_statuses": list(ACCESS_STATUSES),
        "buyer_states": list(BUYER_STATES),
        "expiry_warning_days": EXPIRY_WARNING_DAYS,
        "expiry_clock": EXPIRY_STARTS_AT_PUBLISH,
        "boundary": (
            "access ends at the last instant of the expiry date in UTC; a page is "
            "open for every instant up to and including expires_at"
        ),
        "caps": {"max_expiry_days": MAX_EXPIRY_DAYS, "max_views": MAX_VIEWS},
        "stored_under": f"{PAGES}.data.{WINDOW_KEY}",
        "views_collection": PAGE_VIEWS,
        "counted_by": "count_where",
        "actions": {
            "set_expiry": {
                "requires": "can_manage_status",
                "effect": "bounds the link by a number of days counted from going live",
                "changeable": "at any time, independently of the cap",
            },
            "set_view_limit": {
                "requires": "can_manage_status",
                "effect": "closes the link once that many openings are recorded",
                "changeable": "at any time, independently of expiry",
            },
            "record_view": {
                "requires": "nothing; this is what a buyer does",
                "effect": "counts one opening, and refuses when the link is closed",
            },
            "decline": {"requires": "can_manage_status", "effect": "closes the link by hand"},
            "set_live": {
                "requires": "can_manage_status",
                "effect": "reopens the link and restarts the expiry clock",
                "does_not": "clear the view cap; the cap must be removed explicitly",
            },
        },
        "writes": [f"{PAGES}.data.{WINDOW_KEY}", PAGE_VIEWS],
        "derived_on_read": ["expires_at", "access_status", "accessible", "buyer_state"],
        "not_stored": [f"{PAGES}.data.status"],
        "inferred": [
            {
                "claim": "expiry closes at the end of the expiry date in UTC",
                "basis": (
                    "The only sourced boundary is Liferay's 'Access ends at the end "
                    "of the expiration date in UTC'. Qwilr's 'stops working after "
                    "the number of days that you set' reads as elapsed time and "
                    "would cut a 09:00 page off at 09:00 on the last day."
                ),
            },
            {
                "claim": "a room is expired when now is past expires_at, and open at exactly it",
                "basis": (
                    "Follows from the end-of-day rule: 23:59:59.999999 is the last "
                    "instant the window covers, so the comparison is strict."
                ),
            },
            {
                "claim": "expiry outranks the cap when a page is both",
                "basis": (
                    "The research never says which badge wins. Expiry is the only "
                    "constraint that moves the status - the page 'will "
                    "automatically switch to a Declined status' - while the cap "
                    "only masks it, so a page that did both declined on a date."
                ),
            },
            {
                "claim": "Set Live restarts the expiry clock",
                "basis": (
                    "'The count of days starts when you publish the page', and Set "
                    "Live publishes again. Without a restart, 'You can always set "
                    "the page Live once again if needed' would be false, because "
                    "the page would re-decline against a date still in the past."
                ),
            },
            {
                "claim": "a draft page never expires and a cap is a lifetime total",
                "basis": (
                    "'A draft page keeps the setting until you publish it' leaves "
                    "the count unstarted, so there is no instant to compare. The "
                    "cap is described as a limit on views of the page, and its "
                    "switch is changeable at any time, so it totals the page's "
                    "lifetime rather than restarting."
                ),
            },
        ],
        "not_implemented": [
            {
                "item": "password protection",
                "reason": (
                    "Listed as an optional fourth step of the Share flow and "
                    "gated to lower plans in the research, but it is neither an "
                    "expiry nor a cap, it has no documented semantics of its own "
                    "beyond 'on or off', and the vendor documents it as Share-popup "
                    "only with no API. Implementing a credential check nobody "
                    "researched would be inventing the rule rather than landing "
                    "it. It belongs with WF-015's identity work."
                ),
            },
            {
                "item": "the pending, accepted and blueprint statuses",
                "reason": (
                    "Part of the vendor's dashboard vocabulary, but they describe "
                    "an Accept-block acceptance workflow this product's page model "
                    "does not have: dsr.pages knows draft and published only. "
                    "Reporting a status no record can hold would be a badge that "
                    "lies."
                ),
            },
        ],
    }
