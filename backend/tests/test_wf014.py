"""WF-014: expire or cap access to a room.

The rules are in ``dsr.access_controls`` and are pure, so most of this file tests
them without a database at a chosen instant: an expiry boundary is a fact about
two timestamps, and testing it against the wall clock would make it pass or fail
depending on when CI runs.

What is tested here, and why each matters:

* the UTC boundary, at the exact instant and one microsecond either side;
* ``count_where`` agreeing with ``find``, because a cap counted by the wrong
  primitive is a cap that is wrong quietly;
* ``expires at`` versus ``capped at`` on a page that is both;
* ``Set Live`` leaving the view cap in place, which is the documented trap and the
  rule most likely to be "fixed" by someone who has not read the vendor note;
* what a buyer is shown once access has lapsed, which is the part of this
  workflow a person actually experiences.
"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from dsr.access_controls import (
    ACCESS_STATUSES,
    BUYER_DECLINED,
    BUYER_EXPIRED,
    BUYER_EXPIRING_SOON,
    BUYER_OPEN,
    BUYER_UNPUBLISHED,
    BUYER_VIEW_LIMIT,
    EXPIRY_WARNING_DAYS,
    MAX_EXPIRY_DAYS,
    PAGE_VIEWS,
    PAGES,
    ROOMS,
    STATUS_DECLINED,
    STATUS_DRAFT,
    STATUS_LIVE,
    STATUS_VIEW_LIMIT,
    AccessControls,
    AccessWindowConflict,
    AccessWindowForbidden,
    AccessWindowInvalid,
    AccessWindowNotFound,
    compute_expires_at,
    derive,
    end_of_utc_day,
    page_is_published,
    parse_instant,
    utcnow,
    validate_expiry,
    validate_view_limit,
    view_count,
    window_of,
)
from dsr.db.audited import AuditedDatabase
from dsr.permissions import INSTANCE_ADMIN, ROOM_COLLABORATOR, VIEWER
from dsr.store import RecordStore

MODULE = "dsr.features.wf014_access_controls"
PREFIX = "/api/wf-014"
OWNER = ROOM_COLLABORATOR

# A fixed instant, so nothing here depends on when the suite runs.
PUBLISHED = datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc)


def load_feature():
    """Load the feature module the way the host does, by path.

    Resolved from this file rather than the process CWD, because the suite runs
    with ``backend`` as its working directory.
    """
    here = Path(__file__).resolve().parent
    path = here.parent / "dsr" / "features" / "wf014_access_controls.py"
    spec = importlib.util.spec_from_file_location("wf014", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def db(tmp_path):
    database = AuditedDatabase(tmp_path / "access_controls.db", mirror_dir=tmp_path / "mirror")
    yield database
    database.close()


@pytest.fixture()
def store(db):
    return RecordStore(db)


@pytest.fixture()
def engine(store):
    return AccessControls(store)


@pytest.fixture()
def room(store):
    return store.create(ROOMS, {"name": "Northwind", "status": "active"}, actor="dana")


@pytest.fixture()
def published_page(store, room):
    """A page with a published revision, which is what makes it have a link."""
    page = store.create(
        PAGES,
        {"slug": "overview", "title": "Overview", "blocks": []},
        room_id=room["id"],
        actor="dana",
    )
    revision = store.create(
        "page_revision",
        {"page_id": page["id"], "number": 1, "blocks": []},
        room_id=room["id"],
        actor="dana",
    )
    return store.update(
        page["id"],
        {"published_revision_id": revision["id"], "published_at": PUBLISHED.isoformat()},
        actor="dana",
    )


def window_for(days: int = 30, *, starts_at: datetime = PUBLISHED, **extra) -> dict:
    """A bare page record carrying an expiry window, for the pure tests."""
    expiry = {"enabled": True, "days": days, "starts_at": starts_at.isoformat()}
    window = {"expiry": expiry, "view_limit": {"enabled": False, "max_views": None}}
    window.update(extra)
    return {
        "id": "pg1",
        "room_id": "r1",
        "revision": 1,
        "data": {"published_revision_id": "rev1", "access_window": window},
    }


def with_cap(max_views: int) -> dict:
    """A published page carrying *only* a view cap, expiry untouched.

    Built from scratch rather than from ``window_for`` so the "only a cap" cases
    really have no expiry on them.
    """
    return {
        "id": "pg1",
        "room_id": "r1",
        "revision": 1,
        "data": {
            "published_revision_id": "rev1",
            "access_window": {
                "expiry": {"enabled": False, "days": None, "starts_at": None},
                "view_limit": {"enabled": True, "max_views": max_views},
            },
        },
    }


# --------------------------------------------------------------------------- #
# Instants
# --------------------------------------------------------------------------- #


def test_a_naive_timestamp_is_read_as_utc_rather_than_as_local_time():
    """The research fixes the cut-off in UTC, so the server's zone is not a fact
    about the seller's window."""
    assert parse_instant("2026-03-01T09:00:00") == PUBLISHED
    assert parse_instant("2026-03-01T09:00:00Z") == PUBLISHED
    assert parse_instant("2026-03-01T10:00:00+01:00") == PUBLISHED


@pytest.mark.parametrize(
    "value",
    [None, "", "   ", "not-a-date", 42, [], {}, "2026-13-45T00:00:00Z"],
)
def test_an_unparseable_instant_is_none_rather_than_an_exception(value):
    """A caller should not have to catch to find out a field is empty."""
    assert parse_instant(value) is None


def test_end_of_utc_day_is_the_last_microsecond_not_the_next_midnight():
    """Otherwise there is an instant that is in neither day."""
    last = end_of_utc_day(PUBLISHED)
    assert last == datetime(2026, 3, 1, 23, 59, 59, 999999, tzinfo=timezone.utc)
    assert last + timedelta(microseconds=1) == datetime(2026, 3, 2, tzinfo=timezone.utc)


def test_the_boundary_is_the_end_of_the_expiry_date_in_utc():
    """30 days from 9am on 1 March is the end of 31 March, not 9am on 31 March.

    Liferay: "Access ends at the end of the expiration date in UTC."
    """
    assert compute_expires_at(PUBLISHED, 30) == datetime(
        2026, 3, 31, 23, 59, 59, 999999, tzinfo=timezone.utc
    )


# --------------------------------------------------------------------------- #
# The expiry boundary
# --------------------------------------------------------------------------- #


def test_a_fresh_window_is_open_and_barely_live():
    window = derive(window_for(), views=0, now=PUBLISHED)
    assert window.expired is False
    assert window.accessible is True
    assert window.status == STATUS_LIVE
    assert window.buyer_state == BUYER_OPEN


def test_the_page_is_open_at_the_exact_expiry_instant():
    """``23:59:59.999999`` is the last instant the window covers."""
    expires_at = compute_expires_at(PUBLISHED, 30)
    window = derive(window_for(), views=0, now=expires_at)
    assert window.expired is False
    assert window.accessible is True


def test_the_page_is_closed_one_microsecond_after_the_boundary():
    """The comparison is strict, so the boundary has exactly one answer."""
    expires_at = compute_expires_at(PUBLISHED, 30)
    window = derive(window_for(), views=0, now=expires_at + timedelta(microseconds=1))
    assert window.expired is True
    assert window.accessible is False
    assert window.status == STATUS_DECLINED


def test_expiry_takes_the_status_to_declined_with_nothing_set_by_hand():
    """ "On the expiration date, the page will automatically switch to a Declined
    status." So the transition needs no seller action, which is why status is
    derived on read rather than written."""
    window = derive(window_for(), views=0, now=utcnow() + timedelta(days=400))
    assert window.manual_status is None
    assert window.status == STATUS_DECLINED


def test_days_remaining_rounds_up_so_a_live_link_never_reads_zero():
    """A countdown showing "0 days" while the link still works is a bug a user
    can see."""
    expires_at = compute_expires_at(PUBLISHED, 30)
    assert derive(window_for(), views=0, now=expires_at - timedelta(hours=4)).days_remaining == 1
    assert derive(window_for(), views=0, now=expires_at - timedelta(seconds=1)).days_remaining == 1
    # Zero seconds left really is zero days. The copy says "today" rather than
    # "in 0 days", which is why this instant is not a special case in the rule.
    assert derive(window_for(), views=0, now=expires_at).days_remaining == 0


def test_the_last_microsecond_of_a_window_does_not_read_as_zero_days(engine, published_page):
    """The user-visible half of the rounding rule."""
    page_id = published_page["id"]
    engine.set_expiry(page_id, enabled=True, days=30, role=OWNER, actor="dana", now=PUBLISHED)
    expires_at = engine.window(page_id, now=PUBLISHED).expires_at
    assert engine.buyer_view(page_id, now=expires_at)["message"].endswith("expires today.")


def test_days_remaining_is_none_when_no_window_is_open():
    assert derive({"id": "p", "data": {}}, views=0, now=PUBLISHED).days_remaining is None


# --------------------------------------------------------------------------- #
# The seven-day warning
# --------------------------------------------------------------------------- #


def test_the_warning_horizon_is_seven_days_as_researched():
    """ "if the expiration date is within 7 days they'll see a persistent message
    in the bottom left corner"."""
    assert EXPIRY_WARNING_DAYS == 7


def test_the_warning_is_off_at_seven_days_and_a_second_and_on_at_seven_days():
    expires_at = compute_expires_at(PUBLISHED, 30)
    just_outside = derive(window_for(), views=0, now=expires_at - timedelta(days=7, seconds=1))
    at_edge = derive(window_for(), views=0, now=expires_at - timedelta(days=7))
    assert just_outside.expiring_soon is False
    assert just_outside.buyer_state == BUYER_OPEN
    assert at_edge.expiring_soon is True
    assert at_edge.buyer_state == BUYER_EXPIRING_SOON


def test_a_warning_page_is_still_open_to_buyers():
    """The warning sits alongside the content; it does not replace it.

    24 days in leaves 6 days and 15 hours of a 30-day window, which is inside the
    seven-day horizon.
    """
    window = derive(window_for(), views=0, now=PUBLISHED + timedelta(days=24))
    assert window.expiring_soon is True
    assert window.accessible is True


def test_an_expired_page_is_never_merely_expiring_soon():
    """Otherwise a dead link would still be asking the seller to wait."""
    window = derive(window_for(), views=0, now=utcnow() + timedelta(days=400))
    assert window.expiring_soon is False
    assert window.buyer_state == BUYER_EXPIRED


# --------------------------------------------------------------------------- #
# expires at vs capped at
# --------------------------------------------------------------------------- #


def test_a_cap_masks_access_while_the_stored_status_stays_published():
    """The headline rule of this workflow.

    "Once your client reaches the view limit, you'll see the status on your
    dashboard change to View Limit. The Page will retain a Live status, although
    it's been disabled by the view limit."
    """
    window = derive(with_cap(2), views=2, now=PUBLISHED)
    assert window.capped is True
    assert window.status == STATUS_VIEW_LIMIT
    # The page's own status is a different field and this feature does not touch it.
    assert window.to_dict()["stored_status"] == "published"
    assert window.accessible is False


def test_live_does_not_imply_accessible():
    """The rule the whole workflow exists to prevent, pinned on its own."""
    window = derive(with_cap(10), views=10, now=PUBLISHED)
    assert window.status == STATUS_VIEW_LIMIT
    assert window.accessible is False


def test_a_cap_of_one_is_met_by_the_first_view():
    """ "Once that number has been reached, your clients will see an error
    message" - reached, so at the number and not one past it."""
    assert derive(with_cap(1), views=0, now=PUBLISHED).capped is False
    assert derive(with_cap(1), views=1, now=PUBLISHED).capped is True


def test_expired_outranks_capped_and_both_are_reported():
    """The research never says which badge wins when both land.

    Expiry is the only constraint that moves the status; the cap only masks one.
    So a page that did both declined on a date - and a seller who is only told one
    reason will remove the wrong constraint.
    """
    page = with_cap(1)
    page["data"]["access_window"]["expiry"] = {
        "enabled": True,
        "days": 30,
        "starts_at": PUBLISHED.isoformat(),
    }
    window = derive(page, views=7, now=PUBLISHED + timedelta(days=40))
    assert window.status == STATUS_DECLINED
    assert set(window.reasons) == {BUYER_EXPIRED, BUYER_VIEW_LIMIT}


def test_the_two_constraints_are_independent():
    """Neither reads the other, which is what "orthogonal, independently
    toggleable" means in practice."""
    only_expiry = derive(window_for(days=30), views=999, now=PUBLISHED)
    assert only_expiry.view_limit_enabled is False
    assert only_expiry.capped is False
    assert only_expiry.status == STATUS_LIVE

    only_cap = derive(with_cap(50), views=1, now=PUBLISHED)
    assert only_cap.expiry_enabled is False
    assert only_cap.expires_at is None
    assert only_cap.status == STATUS_LIVE


# --------------------------------------------------------------------------- #
# A draft page keeps the setting, and the clock does not run
# --------------------------------------------------------------------------- #


def test_a_draft_page_with_expiry_enabled_is_never_expired():
    """ "A draft page keeps the setting until you publish it" - the count has not
    begun, so there is no instant to compare against."""
    page = {
        "id": "pg2",
        "room_id": "r1",
        "data": {
            "access_window": {
                "expiry": {"enabled": True, "days": 1, "starts_at": None},
                "view_limit": {"enabled": False, "max_views": None},
            }
        },
    }
    window = derive(page, views=0, now=PUBLISHED + timedelta(days=900))
    assert window.published is False
    assert window.expires_at is None
    assert window.expired is False
    assert window.status == STATUS_DRAFT
    assert window.buyer_state == BUYER_UNPUBLISHED


def test_publication_is_read_by_the_products_own_rule():
    """``dsr.pages`` answers this with published_revision_id. Restating it here as
    a different test is how two features end up disagreeing about a record."""
    from dsr.pages import PageService

    for payload in (
        {"published_revision_id": "rev1"},
        {},
        {"published_revision_id": None},
    ):
        page = {"data": payload}
        assert page_is_published(page) is (PageService._next_status(payload, []) == "published")


# --------------------------------------------------------------------------- #
# count_where: the aggregate the cap depends on
# --------------------------------------------------------------------------- #


def test_the_cap_is_counted_and_not_measured_by_loading_rows(store, engine, published_page):
    """The gap WF-009 recorded in its own commit message: "The other, count, has
    no honest equivalent on the facade ... a capped ``len(list(...))`` would
    quietly downgrade it."

    So the count comes from ``count_where`` over the dynamic index. This test pins
    that the two agree, which is the only way to know the aggregate is honest.
    """
    page_id = published_page["id"]
    for index in range(3):
        engine.record_view(page_id, viewer=f"buyer{index}@northwind.example", at=PUBLISHED)

    counted = view_count(store, page_id)
    fetched = len(store.find(PAGE_VIEWS, {"page_id": page_id}))
    assert counted == 3
    assert counted == fetched


def test_count_where_agrees_with_find_for_numbers_and_booleans(store, published_page):
    """The dynamic index stores booleans numerically, so a cap read through the
    wrong affinity would match nothing rather than everything."""
    page_id = published_page["id"]
    other = store.create(
        PAGE_VIEWS,
        {"page_id": "some-other-page", "viewer": None, "first": True},
        room_id=published_page["room_id"],
        actor="dana",
    )
    assert other["id"]

    for path, value in (("first", True), ("first", False), ("max_views", 3), ("max_views", 0)):
        where = {"page_id": page_id, path: value}
        assert store.count_where(PAGE_VIEWS, where) == len(store.find(PAGE_VIEWS, where))


def test_the_count_excludes_soft_deleted_rows_by_default(store, engine, published_page):
    """A deleted view is not a view. Counting it would close a room early."""
    page_id = published_page["id"]
    engine.record_view(page_id, viewer="a@northwind.example", at=PUBLISHED)
    engine.record_view(page_id, viewer="b@northwind.example", at=PUBLISHED)
    assert view_count(store, page_id) == 2

    rows = store.find(PAGE_VIEWS, {"page_id": page_id})
    store.delete(rows[0]["id"], actor="dana")
    assert view_count(store, page_id) == 1
    assert store.count_where(PAGE_VIEWS, {"page_id": page_id}, include_deleted=True) == 2


def test_views_of_other_pages_are_never_counted(store, engine, published_page, room):
    """Two pages sharing a room would each close on the other's views if the
    count were scoped to the room."""
    other = store.create(PAGES, {"slug": "other", "blocks": []}, room_id=room["id"], actor="dana")
    revision = store.create(
        "page_revision",
        {"page_id": other["id"], "number": 1, "blocks": []},
        room_id=room["id"],
        actor="dana",
    )
    other = store.update(other["id"], {"published_revision_id": revision["id"]}, actor="dana")

    engine.record_view(published_page["id"], viewer="a@x.example", at=PUBLISHED)
    engine.record_view(other["id"], viewer="b@x.example", at=PUBLISHED)
    engine.record_view(other["id"], viewer="c@x.example", at=PUBLISHED)

    assert view_count(store, published_page["id"]) == 1
    assert view_count(store, other["id"]) == 2


# --------------------------------------------------------------------------- #
# Recording a view
# --------------------------------------------------------------------------- #


def test_recording_a_view_counts_it_and_returns_the_new_state(engine, published_page):
    result = engine.record_view(published_page["id"], viewer="a@northwind.example", at=PUBLISHED)
    assert result["view_limit"]["views"] == 1
    assert result["view_limit"]["views_remaining"] is None


def test_recording_a_view_on_a_closed_page_is_refused_and_records_nothing(
    store, engine, published_page
):
    """A closed page that still accrues views has counted views nobody made, and a
    cap raised later would open onto an inflated total."""
    page_id = published_page["id"]
    engine.set_view_limit(page_id, enabled=True, max_views=1, role=OWNER, actor="dana")
    engine.record_view(page_id, viewer="a@northwind.example", at=PUBLISHED)

    with pytest.raises(AccessWindowConflict):
        engine.record_view(page_id, viewer="b@northwind.example", at=PUBLISHED)

    assert view_count(store, page_id) == 1


def test_raising_a_cap_above_the_current_count_reopens_the_link(engine, published_page):
    """The views really were views of that page, so the count is not reset by
    changing the limit."""
    page_id = published_page["id"]
    engine.set_view_limit(page_id, enabled=True, max_views=2, role=OWNER, actor="dana")
    for index in range(2):
        engine.record_view(page_id, viewer=f"b{index}@x.example", at=PUBLISHED)
    assert engine.window(page_id, now=PUBLISHED).accessible is False

    engine.set_view_limit(page_id, enabled=True, max_views=5, role=OWNER, actor="dana")
    assert engine.window(page_id, now=PUBLISHED).accessible is True
    assert engine.window(page_id, now=PUBLISHED).views == 2


# --------------------------------------------------------------------------- #
# Set Live, and the cap it does not clear
# --------------------------------------------------------------------------- #


def test_set_live_reopens_a_hand_declined_page(engine, published_page):
    """ "You can always set the page Live once again if needed." """
    page_id = published_page["id"]
    engine.decline(page_id, role=OWNER, actor="dana")
    assert engine.window(page_id, now=PUBLISHED).accessible is False

    engine.set_live(page_id, role=OWNER, actor="dana", now=PUBLISHED)
    assert engine.window(page_id, now=PUBLISHED).accessible is True


def test_set_live_restarts_the_expiry_clock(engine, published_page):
    """Otherwise "you can always set the page Live once again" would be false: the
    page would re-decline against a date still in the past, and revival would be a
    no-op."""
    page_id = published_page["id"]
    engine.set_expiry(page_id, enabled=True, days=2, role=OWNER, actor="dana", now=PUBLISHED)
    later = PUBLISHED + timedelta(days=5)
    assert engine.window(page_id, now=later).accessible is False

    engine.set_live(page_id, role=OWNER, actor="dana", now=later)
    revived = engine.window(page_id, now=later)
    assert revived.accessible is True
    assert revived.expires_at == compute_expires_at(later, 2)


def test_set_live_does_not_clear_the_view_limit(engine, published_page):
    """The documented trap, and the rule most likely to be "fixed" by someone who
    has not read the note.

    "If your page reaches the view limit and then you manually set it as Declined,
    you can still manually set it Live later. If you do that, the view limit
    setting will still in place, so you'll want to use the steps above to remove
    it."
    """
    page_id = published_page["id"]
    engine.set_view_limit(page_id, enabled=True, max_views=1, role=OWNER, actor="dana")
    engine.record_view(page_id, viewer="a@northwind.example", at=PUBLISHED)
    engine.decline(page_id, role=OWNER, actor="dana")

    engine.set_live(page_id, role=OWNER, actor="dana", now=PUBLISHED)

    window = engine.window(page_id, now=PUBLISHED)
    assert window.manual_status == "live"
    assert window.view_limit_enabled is True
    assert window.max_views == 1
    assert window.capped is True
    assert window.accessible is False


def test_removing_the_cap_is_what_reopens_a_view_limited_page(engine, published_page):
    """ "you'll want to use the steps above to remove it" - so the removal is its
    own action, not a side effect of reviving."""
    page_id = published_page["id"]
    engine.set_view_limit(page_id, enabled=True, max_views=1, role=OWNER, actor="dana")
    engine.record_view(page_id, viewer="a@northwind.example", at=PUBLISHED)
    engine.clear_view_limit(page_id, role=OWNER, actor="dana")

    window = engine.window(page_id, now=PUBLISHED)
    assert window.view_limit_enabled is False
    assert window.accessible is True


def test_clearing_expiry_leaves_the_cap_alone(engine, published_page):
    """The two settings are separate switches; clearing one is not clearing both."""
    page_id = published_page["id"]
    engine.set_expiry(page_id, enabled=True, days=30, role=OWNER, actor="dana", now=PUBLISHED)
    engine.set_view_limit(page_id, enabled=True, max_views=3, role=OWNER, actor="dana")
    engine.clear_expiry(page_id, role=OWNER, actor="dana")

    window = engine.window(page_id, now=PUBLISHED)
    assert window.expiry_enabled is False
    assert window.view_limit_enabled is True
    assert window.max_views == 3


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("days", [None, 0, -1, "abc", True, 3.5, MAX_EXPIRY_DAYS + 1])
def test_an_unusable_day_count_is_refused_rather_than_rounded(days):
    """A seller who ticks the box, leaves it empty and saves must not come back to
    a link that quietly never expires."""
    with pytest.raises(AccessWindowInvalid):
        validate_expiry(True, days)


@pytest.mark.parametrize("max_views", [None, 0, -5, "many", False, 2.5])
def test_an_unusable_cap_is_refused(max_views):
    with pytest.raises(AccessWindowInvalid):
        validate_view_limit(True, max_views)


def test_a_numeric_string_day_count_is_accepted():
    """A form posts strings; rejecting them would fail every browser."""
    assert validate_expiry(True, "30") == {"enabled": True, "days": 30, "starts_at": None}


def test_disabling_a_constraint_forgets_its_numbers():
    """Otherwise a stale count is one click from being re-enabled by accident."""
    assert validate_expiry(False, 30) == {"enabled": False, "days": None, "starts_at": None}
    assert validate_view_limit(False, 30) == {"enabled": False, "max_views": None}


def test_an_unconfigured_page_reads_as_both_constraints_off():
    """Callers never branch on the key being absent."""
    stored = window_of({"data": {}})
    assert stored["expiry"]["enabled"] is False
    assert stored["view_limit"]["enabled"] is False
    assert stored["manual_status"] is None


# --------------------------------------------------------------------------- #
# Permissions
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("role", [VIEWER, None, "instance_administrator", "nonsense"])
def test_a_caller_who_may_not_manage_settings_cannot_change_the_window(
    engine, published_page, role
):
    page_id = published_page["id"]
    with pytest.raises(AccessWindowForbidden):
        engine.set_expiry(page_id, enabled=True, days=30, role=role, actor="dana", now=PUBLISHED)
    with pytest.raises(AccessWindowForbidden):
        engine.set_view_limit(page_id, enabled=True, max_views=5, role=role, actor="dana")
    with pytest.raises(AccessWindowForbidden):
        engine.decline(page_id, role=role, actor="dana")


def test_a_collaborator_and_an_instance_admin_both_may(engine, published_page):
    page_id = published_page["id"]
    for role in (OWNER, INSTANCE_ADMIN):
        window = engine.set_view_limit(page_id, enabled=True, max_views=99, role=role, actor="dana")
        assert window.view_limit_enabled is True


def test_an_archived_room_is_read_only_for_its_window(store, engine, published_page, room):
    """The room's own status is honoured, so an archived room's window is frozen
    exactly as its other writes are."""
    store.update(room["id"], {"status": "archived"}, actor="dana")
    with pytest.raises(AccessWindowForbidden):
        engine.set_expiry(
            published_page["id"], enabled=True, days=30, role=OWNER, actor="dana", now=PUBLISHED
        )


# --------------------------------------------------------------------------- #
# Not found
# --------------------------------------------------------------------------- #


def test_a_page_that_does_not_exist_is_not_found_not_a_crash(engine, room):
    with pytest.raises(AccessWindowNotFound):
        engine.window("no-such-page", now=PUBLISHED)


def test_a_record_of_another_collection_is_not_a_page(engine, room):
    """Guessing another collection's id must not return its contents as a page."""
    with pytest.raises(AccessWindowNotFound):
        engine.window(room["id"], now=PUBLISHED)


# --------------------------------------------------------------------------- #
# The buyer experience
# --------------------------------------------------------------------------- #


def test_an_open_link_shows_content_and_no_message(engine, published_page):
    view = engine.buyer_view(published_page["id"], now=PUBLISHED)
    assert view["state"] == BUYER_OPEN
    assert view["show_content"] is True
    assert view["message"] == ""
    assert view["error"] is False


def test_a_warning_link_shows_content_and_a_message_bottom_left(engine, published_page):
    """ "a persistent message in the bottom left corner" - beside the content, not
    instead of it.

    26 days into a 30-day window leaves 4 days and 15 hours, which rounds up to
    the five days the buyer is told.
    """
    page_id = published_page["id"]
    engine.set_expiry(page_id, enabled=True, days=30, role=OWNER, actor="dana", now=PUBLISHED)
    view = engine.buyer_view(page_id, now=PUBLISHED + timedelta(days=26))
    assert view["state"] == BUYER_EXPIRING_SOON
    assert view["show_content"] is True
    assert view["warning"] is True
    assert view["message_placement"] == "bottom-left"
    assert "5 days" in view["message"]


def test_tomorrows_expiry_says_tomorrow_and_not_one_day(engine, published_page):
    """23 hours left rounds up to one day, and one day is spoken as tomorrow."""
    page_id = published_page["id"]
    engine.set_expiry(page_id, enabled=True, days=30, role=OWNER, actor="dana", now=PUBLISHED)
    view = engine.buyer_view(page_id, now=PUBLISHED + timedelta(days=29, hours=16))
    assert view["days_remaining"] == 1
    assert view["message"] == "This room's link expires tomorrow."


def test_an_expired_link_replaces_the_content_with_an_error(engine, published_page):
    """What the research promises a buyer: "Once that time is up, the link will
    display an error message instead."
    """
    page_id = published_page["id"]
    engine.set_expiry(page_id, enabled=True, days=2, role=OWNER, actor="dana", now=PUBLISHED)
    view = engine.buyer_view(page_id, now=PUBLISHED + timedelta(days=3))
    assert view["state"] == BUYER_EXPIRED
    assert view["show_content"] is False
    assert view["error"] is True
    assert view["message_placement"] == "replaces-content"
    assert view["message"]


def test_a_capped_link_replaces_the_content_with_an_error(engine, published_page):
    """ "your clients will see an error message when they view the page" """
    page_id = published_page["id"]
    engine.set_view_limit(page_id, enabled=True, max_views=1, role=OWNER, actor="dana")
    engine.record_view(page_id, viewer="a@northwind.example", at=PUBLISHED)
    view = engine.buyer_view(page_id, now=PUBLISHED)
    assert view["state"] == BUYER_VIEW_LIMIT
    assert view["show_content"] is False
    assert view["message"]


def test_a_hand_declined_link_replaces_the_content_with_an_error(engine, published_page):
    page_id = published_page["id"]
    engine.decline(page_id, role=OWNER, actor="dana")
    view = engine.buyer_view(page_id, now=PUBLISHED)
    assert view["state"] == BUYER_DECLINED
    assert view["show_content"] is False


def test_an_unpublished_link_is_never_shown_to_a_buyer(engine, store, room):
    page = store.create(PAGES, {"slug": "draft", "blocks": []}, room_id=room["id"], actor="dana")
    view = engine.buyer_view(page["id"], now=PUBLISHED)
    assert view["state"] == BUYER_UNPUBLISHED
    assert view["show_content"] is False


def test_the_buyer_view_needs_no_role(engine, published_page):
    """Someone following a link has not identified themselves, and the answer does
    not depend on who they are."""
    view = engine.buyer_view(published_page["id"], now=PUBLISHED)
    assert view["show_content"] is True


# --------------------------------------------------------------------------- #
# Every write is audited
# --------------------------------------------------------------------------- #


def test_a_changed_window_leaves_an_audit_row(engine, store, published_page):
    page_id = published_page["id"]
    engine.set_expiry(page_id, enabled=True, days=30, role=OWNER, actor="dana", now=PUBLISHED)
    rows = store.audit(action="update", record_id=page_id)
    assert rows, "changing a window must be audited"
    assert any(row.get("actor") == "dana" for row in rows)


def test_a_recorded_view_is_audited_too(store, engine, published_page):
    """The buyer's click is the input the cap depends on, so it is part of the
    trail rather than an anonymous side effect."""
    page_id = published_page["id"]
    engine.record_view(page_id, viewer="a@northwind.example", at=PUBLISHED)
    rows = store.audit(collection=PAGE_VIEWS)
    assert len(rows) == 1
    assert rows[0]["actor"] == "a@northwind.example"


def test_a_refused_view_leaves_no_trail_at_all(store, engine, published_page):
    """Nothing happened, so nothing is written - not even a row that says the
    click was turned away."""
    page_id = published_page["id"]
    engine.set_view_limit(page_id, enabled=True, max_views=1, role=OWNER, actor="dana")
    engine.record_view(page_id, viewer="a@northwind.example", at=PUBLISHED)
    before = len(store.audit(collection=PAGE_VIEWS))
    with pytest.raises(AccessWindowConflict):
        engine.record_view(page_id, viewer="b@northwind.example", at=PUBLISHED)
    assert len(store.audit(collection=PAGE_VIEWS)) == before


# --------------------------------------------------------------------------- #
# The plugin's own structural decisions
# --------------------------------------------------------------------------- #


def test_the_feature_owns_a_unique_prefix_and_the_domain_module_is_named_for_this_ticket():
    """``backend/dsr/access.py`` is WF-015's and is live on main. Two features
    cannot own one module path."""
    feature = load_feature()
    assert feature.FEATURE["ticket"] == "WF-014"
    assert feature.router.prefix == PREFIX
    assert (Path(__file__).resolve().parent.parent / "dsr" / "access_controls.py").exists()


def test_the_module_name_is_not_the_one_wf015_owns():
    import dsr.access as wf015
    import dsr.access_controls

    assert dsr.access_controls.__name__ != wf015.__name__
    assert not hasattr(dsr.access_controls, "MODE_IDENTIFY")


def test_the_feature_imports_its_dependencies_from_deps_never_from_api():
    source = (
        Path(__file__).resolve().parent.parent / "dsr" / "features" / "wf014_access_controls.py"
    ).read_text(encoding="utf-8")
    assert "from dsr.deps import" in source
    assert "from dsr.api import" not in source


def test_the_domain_module_opens_no_connection_of_its_own():
    """The audit guarantee is that the row and the change share a transaction. A
    module that opened SQLite itself would step outside it."""
    source = (Path(__file__).resolve().parent.parent / "dsr" / "access_controls.py").read_text(
        encoding="utf-8"
    )
    assert "sqlite3" not in source
    assert "connect(" not in source


def test_every_badge_and_buyer_state_is_reachable_and_distinct():
    assert len(set(ACCESS_STATUSES)) == len(ACCESS_STATUSES)
    assert set(ACCESS_STATUSES) == {
        STATUS_DRAFT,
        STATUS_LIVE,
        "expiring_soon",
        STATUS_DECLINED,
        STATUS_VIEW_LIMIT,
    }
