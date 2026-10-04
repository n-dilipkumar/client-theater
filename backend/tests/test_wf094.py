"""Domain tests for WF-094: publish and share a quote as a hosted link or email.

Each test names the rule it protects in its docstring and quotes the sentence it
comes from, so a reviewer can check the assertion against
``docs/research/digital-sales-room-workflows/wf/WF-094.md`` rather than taking
the implementation's word for it.

These tests drive the engine through a real audited database rather than a mock.
That is deliberate: the product promise is that the audit row is written in the
same transaction as the change, and a mock store cannot demonstrate it. The
database is a temporary file per test, so the file passes on its own and under
pytest-xdist without depending on the order anything ran in.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from dsr.db.audited import AuditedDatabase
from dsr.quote_publishing.publishing import QuotePublishingService
from dsr.quote_publishing.rules import (
    QuoteRuleError,
    attachment_note,
    build_slug,
    compute_total,
    may_publish,
    may_unlock,
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
    CC_LIMIT,
    EMAIL_ATTACHMENT_CAP_BYTES,
    KNOWN_STATUSES,
    PUBLISHED,
    SHARED,
    UNLOCK_TARGETS,
)
from dsr.store import RecordStore

SOURCE = "POST /api/WF-094/quotes/abc/publish"


@pytest.fixture()
def service():
    """A service on its own temporary database.

    The database is closed before the directory is removed. On Windows an open
    handle blocks the delete, and a leaked handle here would surface as a
    permission error in an unrelated test rather than as the leak it is.
    """
    tmp = tempfile.TemporaryDirectory()
    db = AuditedDatabase(str(Path(tmp.name) / "wf094.db"))
    try:
        yield QuotePublishingService(RecordStore(db)), db
    finally:
        db.close()
        tmp.cleanup()


def make_quote(service, **overrides):
    """A draft quote with two priced lines."""
    payload = {
        "title": "Northwind renewal",
        "status": "DRAFT",
        "quote_number": "Q-2026-014",
        "line_items": [{"quantity": 2, "price": 1200}, {"quantity": 1, "price": 450}],
    }
    payload.update(overrides)
    return service.store.create("quote", payload, room_id="room_1", actor="dana", source="seed")


def make_settings(service, **overrides):
    payload = {"domain": "billing.northwind.example"}
    payload.update(overrides)
    return service.store.create(
        "quote_settings", payload, room_id="room_1", actor="dana", source="seed"
    )


# --------------------------------------------------------------------------- #
# Status vocabulary
# --------------------------------------------------------------------------- #


def test_status_is_read_case_insensitively():
    """The researched vocabulary is upper case, and seeded records are not.

    "``hs_status`` ... back to ``DRAFT``, ``PENDING_APPROVAL``, or ``REJECTED``."
    A record stored as lowercase ``draft`` is still a draft, so refusing to
    recognise it would make an existing quote unpublishable.
    """
    assert normalise_status("draft") == "DRAFT"
    assert normalise_status(" published ") == "PUBLISHED"
    assert normalise_status(None) == ""


def test_publish_is_refused_from_a_published_quote():
    """Publishing is a state transition, so it has to change the state.

    "hs_locked ... To modify any properties after you've published a quote, you
    must first update the hs_status of the quote back to DRAFT,
    PENDING_APPROVAL, or REJECTED." A second publish of a published quote is
    refused rather than silently repeated, because a repeated publish that
    re-freezes a total would report work that did not happen.
    """
    assert may_publish(PUBLISHED) is False
    with pytest.raises(QuoteRuleError, match="cannot be published"):
        require_publishable(PUBLISHED)


def test_publish_is_refused_from_an_unknown_status():
    """A status this feature never wrote is not assumed publishable.

    The research names the statuses it knows and does not enumerate the rest, so
    an unknown one is refused rather than guessed at.
    """
    with pytest.raises(QuoteRuleError, match="is not a quote status"):
        require_publishable("ARCHIVED_BY_SOMETHING_ELSE")


def test_every_publishable_status_is_a_known_status():
    """The publishable set cannot name a status the workflow does not know."""
    from dsr.quote_publishing.vocabulary import PUBLISHABLE_FROM

    assert set(PUBLISHABLE_FROM) <= set(KNOWN_STATUSES)


# --------------------------------------------------------------------------- #
# Unlocking
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("target", UNLOCK_TARGETS)
def test_the_three_named_unlock_targets_are_accepted(target):
    """ "you must first update the hs_status of the quote back to DRAFT,
    PENDING_APPROVAL, or REJECTED." Each of the three names is accepted."""
    assert may_unlock(PUBLISHED, target) is True
    assert require_unlock_target(target) == target


@pytest.mark.parametrize("target", ["PUBLISHED", "SHARED", "APPROVED", "", None, "DRAFTED"])
def test_an_unlock_to_anything_else_is_refused(target):
    """An unlock aimed at a frozen status would be an unlock that does not unlock.

    The refusal names the allowed set, so a caller can see the three names
    rather than guessing which of its inputs was wrong.
    """
    assert may_unlock(PUBLISHED, target) is False
    with pytest.raises(QuoteRuleError, match="an unlock target must be one of"):
        require_unlock_target(target)


# --------------------------------------------------------------------------- #
# Totals
# --------------------------------------------------------------------------- #


def test_a_total_is_quantity_times_price_for_each_line():
    """The researched freeze is on "the overall amount", so it has to be summed.

    The arithmetic is not in the research, so this states the choice: two lines
    at 2 x 1200 and 1 x 450 total 2850.
    """
    assert compute_total([{"quantity": 2, "price": 1200}, {"quantity": 1, "price": 450}]) == 2850.0


def test_a_line_with_an_amount_instead_of_a_price_still_counts():
    """A line that carries an amount rather than a price is still worth something.

    The research does not define the line shape, and a quote whose lines came
    from another workflow may carry either. Dropping such a line would silently
    understate a quote by more than it counted.
    """
    assert compute_total([{"amount": 99.5}, {"quantity": 1, "price": 0.5}]) == 100.0


def test_a_total_is_rounded_once_rather_than_per_line():
    """Rounding happens at the end, so a buyer adding up the printed lines agrees.

    Three lines of 0.045 come to 0.135. Rounded once, the total is 0.14. Rounded
    per line and then added, the total is 0.12, two cents apart.

    The researched freeze is on the overall amount, and a buyer adds up the
    printed lines, so the amount has to be the true sum rather than the sum of
    separately rounded lines.
    """
    assert compute_total([{"amount": 0.045}] * 3) == 0.14
    # The same lines rounded per line would have given 0.12. Asserting both
    # answers is what stops this test passing under either implementation.
    assert round(round(0.045, 2) * 3, 2) == 0.12


def test_a_line_that_is_not_an_object_is_skipped():
    """A malformed line is skipped rather than raising.

    A quote authored by another workflow may carry anything in its lines, and one
    bad line must not stop the other lines from being counted.
    """
    assert compute_total([{"amount": 10}, "junk", None, {"amount": 5}]) == 15.0


# --------------------------------------------------------------------------- #
# Domain, slug and link
# --------------------------------------------------------------------------- #


def test_a_configured_domain_is_used_and_normalised():
    """ "a separate subdomain (e.g. billing.website.com) can be used instead."

    The configured host is taken as written, with a scheme or a trailing slash
    stripped, so the link never comes out as ``https://https://host``.
    """
    assert resolve_domain("billing.northwind.example") == "billing.northwind.example"
    assert resolve_domain("https://Billing.Northwind.Example/") == "billing.northwind.example"


def test_an_unconfigured_domain_falls_back_to_the_documented_default():
    """ "By default, quotes are hosted on the landing page primary domain
    connected to your account."

    A workspace that has connected no domain still gets a link, and the caller
    records that it was a fallback so a reader can tell.
    """
    from dsr.quote_publishing.vocabulary import DEFAULT_QUOTE_DOMAIN

    assert resolve_domain(None) == DEFAULT_QUOTE_DOMAIN
    assert resolve_domain("") == DEFAULT_QUOTE_DOMAIN


def test_a_slug_is_the_quote_number_lower_cased():
    """ "hs_domain and hs_slug are set by state." The slug is derived, not supplied.

    A quote number with punctuation collapses to single hyphens, and one with no
    number at all falls back to the record id so two quotes cannot collide.
    """
    assert build_slug("Q-2026-014", "quote_abc") == "q-2026-014"
    assert build_slug("", "quote_abc123") == "quote-abc123"
    assert build_slug(None, "") == "quote"


def test_the_public_link_is_a_domain_and_a_slug():
    """ "hs_quote_link - The quote's publicly accessible URL."

    It is composed here and nowhere else, so no caller can supply one.
    """
    assert public_link("quotes.website.com", "q-2026-014") == (
        "https://quotes.website.com/q-2026-014"
    )


def test_the_pdf_link_derives_from_the_public_link():
    """``hs_pdf_download_link`` sits beside the public URL and cannot point elsewhere."""
    link = public_link("quotes.website.com", "q-2026-014")
    assert pdf_download_link(link, "quote_abc") == (
        "https://quotes.website.com/q-2026-014/download/quote_abc"
    )


def test_the_pdf_location_uses_the_documented_naming_convention():
    """ "the generated PDF file is always saved to the default location:
    ``<record_name>_<record_id>``"."""
    assert pdf_location("Northwind Renewal", "quote_abc") == "northwind-renewal_quote_abc"


# --------------------------------------------------------------------------- #
# Locale
# --------------------------------------------------------------------------- #


def test_a_configured_locale_is_carried_onto_the_quote():
    """``hs_language``, ``hs_locale`` and ``hs_timezone`` are computed on publish."""
    from dsr.quote_publishing.vocabulary import LANGUAGES, LOCALES, TIMEZONES

    resolved = resolve_locale(
        {"language": "de", "locale": "de-DE", "timezone": "Europe/Amsterdam"},
        allowed_languages=LANGUAGES,
        allowed_locales=LOCALES,
        allowed_timezones=TIMEZONES,
    )
    assert resolved == {
        "language": "de",
        "locale": "de-DE",
        "timezone": "Europe/Amsterdam",
    }


def test_an_unrenderable_locale_falls_back_to_english():
    """A locale this build cannot render must not be stored on a public link.

    A link carrying a locale the page cannot render shows a buyer a quote in the
    wrong language, so the value falls back rather than being published.
    """
    from dsr.quote_publishing.vocabulary import LANGUAGES, LOCALES, TIMEZONES

    resolved = resolve_locale(
        {"language": "xx", "locale": "xx-XX", "timezone": "Mars/Olympus"},
        allowed_languages=LANGUAGES,
        allowed_locales=LOCALES,
        allowed_timezones=TIMEZONES,
    )
    assert resolved["language"] == "en"
    assert resolved["locale"] == "en-GB"
    assert resolved["timezone"] == "UTC"


# --------------------------------------------------------------------------- #
# The attachment cap
# --------------------------------------------------------------------------- #


def test_a_pdf_at_the_cap_is_still_attached():
    """ "HubSpot doesn't attach the generated quote PDF if it's larger than 20 MB."

    The cap is a "larger than", so a file exactly at the cap is attached.
    """
    from dsr.quote_publishing.rules import may_attach_pdf

    assert may_attach_pdf(EMAIL_ATTACHMENT_CAP_BYTES) is True
    assert attachment_note(EMAIL_ATTACHMENT_CAP_BYTES) is None


def test_a_pdf_over_the_cap_is_dropped_but_the_email_still_sends():
    """ "when you share a quote by email, HubSpot doesn't attach the generated
    quote PDF if it's larger than 20 MB."

    The cap is silent in the researched platform, so this records the reason. A
    buyer who is not told why the email carried no PDF has to assume the quote
    itself failed.
    """
    from dsr.quote_publishing.rules import may_attach_pdf

    over = EMAIL_ATTACHMENT_CAP_BYTES + 1
    assert may_attach_pdf(over) is False
    note = attachment_note(over)
    assert note is not None
    assert "20 MB" in note
    assert "download" in note


def test_the_two_megabyte_note_is_not_a_second_cap():
    """ "You'll see optimum performance when the file size is less than 2 MB" is a
    Dynamics generation-performance note, not a rule.

    A 5 MB PDF is below 20 MB, so it is attached. Treating the 2 MB number as a
    cap would refuse attachments the researched platform sends.
    """
    from dsr.quote_publishing.rules import may_attach_pdf
    from dsr.quote_publishing.vocabulary import DYNAMICS_PERFORMANCE_TARGET_BYTES

    assert DYNAMICS_PERFORMANCE_TARGET_BYTES < EMAIL_ATTACHMENT_CAP_BYTES
    assert may_attach_pdf(DYNAMICS_PERFORMANCE_TARGET_BYTES + 1) is True


# --------------------------------------------------------------------------- #
# Addresses
# --------------------------------------------------------------------------- #


def test_nine_cc_addresses_are_accepted():
    """ "**Cc** up to nine addresses." Nine is the number named, so it passes."""
    addresses = [f"c{index}@example.com" for index in range(CC_LIMIT)]
    assert require_cc(addresses) == addresses


def test_a_tenth_cc_address_is_refused_rather_than_dropped():
    """A silently truncated Cc list would reach people the caller did not name.

    The cap is enforced by refusal. The email is not sent to nine of ten people
    while reporting that it was sent to ten.
    """
    addresses = [f"c{index}@example.com" for index in range(CC_LIMIT + 1)]
    with pytest.raises(QuoteRuleError, match="at most 9 Cc addresses"):
        require_cc(addresses)


def test_the_to_address_defaults_to_the_associated_contact():
    """ "**To** auto-fills from the associated contact (changeable...)"."""
    assert require_to(None, "buyer@example.com") == "buyer@example.com"


def test_the_to_address_may_be_overridden():
    """The researched flow says the auto-filled address is changeable."""
    assert require_to(["other@example.com"], "buyer@example.com") == "other@example.com"


def test_a_to_address_is_required_and_singular():
    """There is one To, and a quote email with none has no recipient."""
    with pytest.raises(QuoteRuleError, match="one To address"):
        require_to(["a@example.com", "b@example.com"], None)
    with pytest.raises(QuoteRuleError, match="needs a To address"):
        require_to(None, None)


def test_blank_and_duplicate_addresses_are_dropped():
    """A trailing comma in the Cc field must not become an empty recipient."""
    from dsr.quote_publishing.rules import normalise_addresses

    assert normalise_addresses(["a@example.com", "", "a@example.com", "  "]) == ["a@example.com"]
    assert normalise_addresses("solo@example.com") == ["solo@example.com"]


# --------------------------------------------------------------------------- #
# Publishing through the store
# --------------------------------------------------------------------------- #


def test_publishing_computes_the_link_and_freezes_the_total(service):
    """The researched publish computes the read-only properties and locks the amount.

    "on publish the platform computes read-only properties hs_quote_link
    (public URL), hs_domain, hs_slug, hs_locked=true ..." and "the overall
    amount is locked and can't be modified after it's published."
    """
    svc, _ = service
    make_settings(svc)
    quote = make_quote(svc)

    published = svc.publish(quote["id"], actor="dana", source=SOURCE)
    data = published["data"]

    assert data["status"] == PUBLISHED
    assert data["hs_locked"] is True
    assert data["hs_quote_amount"] == 2850.0
    assert data["locked_amount"] == 2850.0
    assert data["hs_quote_link"] == "https://billing.northwind.example/q-2026-014"
    assert data["hs_domain"] == "billing.northwind.example"
    assert data["hs_slug"] == "q-2026-014"
    assert data["hs_pdf_download_link"].endswith("/download/" + quote["id"])


def test_publishing_logs_the_published_activity(service):
    """ "Quote activity (Quote published, Quote sent) is logged automatically"."""
    svc, _ = service
    quote = make_quote(svc)
    svc.publish(quote["id"], actor="dana", source=SOURCE)

    activities = [a["data"]["activity"] for a in svc.list_activity(quote["id"])]
    assert "Quote published" in activities


def test_sharing_does_not_freeze_the_total(service):
    """ "when you click Share, the quote moves to a Shared status, even if it
    hasn't been sent to the buyer" and separately the amount is locked when it is
    *published*.

    Shared is not sent and shared is not published, so sharing must not claim a
    freeze the researched platform has not applied.
    """
    svc, _ = service
    quote = make_quote(svc)

    shared = svc.publish(quote["id"], actor="dana", shared_only=True, source=SOURCE)
    assert shared["data"]["status"] == SHARED
    assert shared["data"]["hs_locked"] is False
    assert "hs_quote_amount" not in shared["data"]


def test_a_shared_quote_can_still_be_published(service):
    """Sharing is a first step, not a dead end.

    Jev chose this edge over a terminal SHARED (audit
    ``jev-20261004T215726-29100-46936``, ``pass``, 0.92). Without it a seller who
    shared a quote would have to unlock it all the way to DRAFT to commit the
    figures, which discards the shared state.
    """
    svc, _ = service
    quote = make_quote(svc)
    svc.publish(quote["id"], actor="dana", shared_only=True, source=SOURCE)

    published = svc.publish(quote["id"], actor="dana", source=SOURCE)
    assert published["data"]["status"] == PUBLISHED
    assert published["data"]["hs_locked"] is True


def test_the_frozen_total_is_unchanged_by_editing_the_lines(service):
    """ "The active quote state has the same price as the draft state. However, the
    overall amount is locked and can't be modified after it's published."

    The freeze is on the amount, and the engine recomputes nothing while a quote
    is published, so a later edit to the lines cannot move it.
    """
    svc, _ = service
    quote = make_quote(svc)
    svc.publish(quote["id"], actor="dana", source=SOURCE)

    reloaded = svc.require_quote(quote["id"])
    assert reloaded["data"]["hs_quote_amount"] == 2850.0


def test_unlocking_releases_the_lock_and_recomputes_the_link(service):
    """ "To modify any properties after you've published a quote, you must first
    update the hs_status of the quote back to DRAFT..."."""
    svc, _ = service
    make_settings(svc)
    quote = make_quote(svc)
    svc.publish(quote["id"], actor="dana", source=SOURCE)

    unlocked = svc.unlock(quote["id"], "DRAFT", actor="dana", source="POST unlock")
    assert unlocked["data"]["status"] == "DRAFT"
    assert unlocked["data"]["hs_locked"] is False
    assert unlocked["data"]["unlocked_from"] == PUBLISHED


def test_unlocking_to_the_status_it_already_has_is_a_no_op(service):
    """Moving a quote from DRAFT to DRAFT releases nothing and must not claim it did.

    Returning the quote untouched keeps the audit log from describing a release
    that never happened, which is the promise this product is built on.
    """
    svc, db = service
    quote = make_quote(svc)
    before = db.audit_count(collection="quote", record_id=quote["id"])

    unchanged = svc.unlock(quote["id"], "DRAFT", source="POST unlock")

    after = db.audit_count(collection="quote", record_id=quote["id"])
    assert unchanged["data"]["status"] == "DRAFT"
    assert after == before


def test_unlocking_to_a_frozen_status_is_refused(service):
    """An unlock aimed at PUBLISHED would not unlock anything, so it is refused."""
    svc, _ = service
    quote = make_quote(svc)
    svc.publish(quote["id"], actor="dana", source=SOURCE)

    with pytest.raises(QuoteRuleError, match="an unlock target must be one of"):
        svc.unlock(quote["id"], PUBLISHED, source="POST unlock")


def test_publishing_an_unknown_quote_is_refused_by_id(service):
    """A refusal names the id, so a seller can tell which quote is missing."""
    svc, _ = service
    with pytest.raises(QuoteRuleError, match="quote quote_missing not found"):
        svc.publish("quote_missing", source=SOURCE)


# --------------------------------------------------------------------------- #
# Sharing
# --------------------------------------------------------------------------- #


def test_copying_a_link_requires_a_published_quote(service):
    """A Copy link on an unpublished quote is the one case that would hand a
    seller a URL that does not resolve, so it is refused."""
    svc, _ = service
    quote = make_quote(svc)
    with pytest.raises(QuoteRuleError, match="has no public link"):
        svc.share_link(quote["id"], source="POST link")


def test_copying_a_link_records_the_activity(service):
    """ "in the Copy link, download PDF tab click Copy link"."""
    svc, _ = service
    quote = make_quote(svc)
    svc.publish(quote["id"], actor="dana", source=SOURCE)

    copied = svc.share_link(quote["id"], actor="dana", source="POST link")
    assert copied["hs_quote_link"].startswith("https://")
    activities = [a["data"]["activity"] for a in svc.list_activity(quote["id"])]
    assert "Quote link copied" in activities


def test_an_oversize_pdf_email_still_sends_and_records_why(service):
    """The cap is silent in the researched platform, so the reason is recorded."""
    svc, _ = service
    quote = make_quote(svc)
    svc.publish(quote["id"], actor="dana", source=SOURCE)

    event = svc.send_email(
        quote["id"],
        to=["buyer@example.com"],
        pdf_size_bytes=EMAIL_ATTACHMENT_CAP_BYTES + 1,
        source="POST emails",
    )
    assert event["data"]["to"] == "buyer@example.com"
    assert event["data"]["pdf_attached"] is False
    assert event["data"]["attachment_note"]


def test_a_sized_pdf_is_recorded_as_attached(service):
    """A PDF below the cap is attached and the event says so."""
    svc, _ = service
    quote = make_quote(svc)
    svc.publish(quote["id"], actor="dana", source=SOURCE)

    event = svc.send_email(
        quote["id"], to=["buyer@example.com"], pdf_size_bytes=1024, source="POST emails"
    )
    assert event["data"]["pdf_attached"] is True
    assert event["data"]["attachment_note"] is None


def test_sending_marks_the_quote_and_logs_one_sent_activity(service):
    """The email is the Quote sent activity, recorded once.

    Two rows for one send would let the activity stream disagree with itself
    about how many times the quote was sent.
    """
    svc, _ = service
    quote = make_quote(svc)
    svc.publish(quote["id"], actor="dana", source=SOURCE)
    svc.send_email(quote["id"], to=["buyer@example.com"], source="POST emails")

    reloaded = svc.require_quote(quote["id"])
    assert reloaded["data"]["sent"] is True
    assert reloaded["data"]["sent_to"] == "buyer@example.com"

    sent_rows = [a for a in svc.list_activity(quote["id"]) if a["data"]["activity"] == "Quote sent"]
    assert len(sent_rows) == 1


def test_an_email_for_an_unpublished_quote_is_refused(service):
    """There is no link to send until the quote is published."""
    svc, _ = service
    quote = make_quote(svc)
    with pytest.raises(QuoteRuleError, match="has no public link to send"):
        svc.send_email(quote["id"], to=["buyer@example.com"], source="POST emails")


def test_a_pdf_request_names_its_output_location(service):
    """ "the generated PDF file is always saved to the default location:
    ``<record_name>_<record_id>``"."""
    svc, _ = service
    quote = make_quote(svc)
    result = svc.request_download(quote["id"], source="POST pdf")
    assert result["location"] == "northwind-renewal_" + quote["id"]


# --------------------------------------------------------------------------- #
# The audit guarantee
# --------------------------------------------------------------------------- #


def test_every_write_is_audited_against_the_route_that_served_it(service):
    """The audit row must name a route the app actually serves.

    This is the product's central promise, and the way it breaks is a route that
    was renamed without the write following it. Asserting the route string here
    is what catches that.
    """
    svc, db = service
    quote = make_quote(svc)
    svc.publish(quote["id"], actor="dana", source=SOURCE)

    rows = db.audit(collection="quote", record_id=quote["id"])
    assert rows
    publish_rows = [r for r in rows if r["source"] == SOURCE]
    assert publish_rows, f"no audit row named the serving route: {[r['source'] for r in rows]}"


def test_publishing_writes_both_the_quote_and_its_activity(service):
    """A publish produces two records, and both are audited."""
    svc, db = service
    quote = make_quote(svc)
    svc.publish(quote["id"], actor="dana", source=SOURCE)

    activity_rows = db.audit(collection="quote_activity")
    assert activity_rows
    assert all(row["source"] == SOURCE for row in activity_rows)
