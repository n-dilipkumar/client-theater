"""Unit tests for the WF-015 gate's pure logic.

No database here on purpose: domain parsing, domain normalisation, the bot
heuristic, and the policy rules are the parts a future edit is most likely to
break silently, so they are pinned without the HTTP surface in the way.
"""

from __future__ import annotations

import pytest

from dsr.access import (
    MODE_IDENTIFY,
    MODE_OPEN,
    MODE_VERIFY_EMAIL,
    PolicyError,
    email_domain,
    is_valid_email,
    looks_like_bot,
    normalize_domains,
    required_fields,
    validate_policy,
)

# -- email parsing ----------------------------------------------------------- #


@pytest.mark.parametrize(
    ("email", "expected"),
    [
        ("Alex@Northwind.Example", "northwind.example"),
        ("  alex@northwind.example  ", "northwind.example"),
        ("a.b+tag@northwind.example", "northwind.example"),
    ],
)
def test_email_domain_extracts_the_lowercase_domain(email, expected):
    assert email_domain(email) == expected


@pytest.mark.parametrize(
    "email",
    [None, "", "alex", "alex@localhost", "a@b@c", "alex@", "@northwind.example", "alex @x.com", 42],
)
def test_email_domain_returns_none_when_there_is_not_one(email):
    """Callers should not have to re-parse, so an unparseable address is a
    first-class result rather than an exception."""
    assert email_domain(email) is None


@pytest.mark.parametrize("email", ["a.b+tag@x.co", "buyer@northwind.example"])
def test_is_valid_email_accepts_a_real_address(email):
    assert is_valid_email(email) is True


@pytest.mark.parametrize("email", ["a b@c.co", "@c.co", "c.co", "a@b", None, ""])
def test_is_valid_email_rejects_anything_else(email):
    assert is_valid_email(email) is False


# -- domain normalisation ---------------------------------------------------- #


def test_normalize_domains_strips_the_at_symbol_and_folds_case():
    # Sourced: the operator is told "You don't need to type the @ symbol".
    assert normalize_domains("@Northwind.Example, contoso.example") == [
        "northwind.example",
        "contoso.example",
    ]


def test_normalize_domains_dedupes_and_preserves_order():
    assert normalize_domains("b.example, a.example, B.EXAMPLE") == ["b.example", "a.example"]


def test_normalize_domains_tolerates_whitespace_newlines_and_empty_entries():
    assert normalize_domains(" a.example ,,\n b.example , ") == ["a.example", "b.example"]


def test_normalize_domains_accepts_a_list():
    assert normalize_domains(["a.example", "b.example"]) == ["a.example", "b.example"]


def test_normalize_domains_of_nothing_is_empty():
    assert normalize_domains(None) == []
    assert normalize_domains("") == []


def test_normalize_domains_rejects_something_that_is_not_a_domain():
    with pytest.raises(PolicyError) as excinfo:
        normalize_domains("not a domain")
    assert "allowed_domains" in excinfo.value.errors


def test_normalize_domains_rejects_a_non_string_entry():
    with pytest.raises(PolicyError):
        normalize_domains([{"domain": "a.example"}])


def test_normalize_domains_rejects_a_wrong_type_entirely():
    with pytest.raises(PolicyError):
        normalize_domains({"domain": "a.example"})


# -- the bot heuristic ------------------------------------------------------- #


@pytest.mark.parametrize(
    "agent",
    [
        "Microsoft Outlook preview scanner",
        "Mozilla/5.0 (compatible; Googlebot/2.1)",
        "curl/8.4.0",
        "Wget/1.21",
        "python-requests/2.31",
        "Slackbot-LinkExpanding 1.0",
        "HeadlessChrome/120.0",
    ],
)
def test_looks_like_bot_flags_scanner_and_preview_agents(agent):
    assert looks_like_bot({"User-Agent": agent}) is True


def test_looks_like_bot_flags_a_prefetch_purpose_header():
    # The research names preview services (Microsoft, Outlook) as the source.
    assert looks_like_bot({"Sec-Purpose": "prefetch"}) is True
    assert looks_like_bot({"X-Purpose": "preview"}) is True


def test_looks_like_bot_leaves_a_browser_alone():
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Accept": "text/html",
    }
    assert looks_like_bot(headers) is False


def test_looks_like_bot_handles_missing_headers():
    assert looks_like_bot(None) is False
    assert looks_like_bot({}) is False


# -- the policy rules -------------------------------------------------------- #


def test_validate_policy_accepts_the_three_tiers():
    assert validate_policy({"mode": MODE_OPEN})["mode"] == MODE_OPEN
    assert validate_policy({"mode": MODE_IDENTIFY, "collect_email": True})["mode"] == MODE_IDENTIFY
    verified = validate_policy({"mode": MODE_VERIFY_EMAIL, "collect_email": True})
    assert verified["mode"] == MODE_VERIFY_EMAIL
    assert verified["collect_email"] is True


def test_validate_policy_rejects_an_unknown_mode():
    with pytest.raises(PolicyError) as excinfo:
        validate_policy({"mode": "telepathy"})
    assert "mode" in excinfo.value.errors


def test_validate_policy_rejects_domain_security_without_email_verification():
    """Sourced: ticking Email Verification "will also allow Domain Security to be
    selected", so Domain Security on its own is a misconfiguration."""
    with pytest.raises(PolicyError) as excinfo:
        validate_policy(
            {"mode": MODE_IDENTIFY, "collect_email": True, "domain_security": True,
             "allowed_domains": "northwind.example"}
        )
    assert "domain_security" in excinfo.value.errors


def test_validate_policy_rejects_an_empty_allowlist():
    """An empty allowlist means "refuse everybody", which is never what a
    checkbox means."""
    with pytest.raises(PolicyError) as excinfo:
        validate_policy(
            {"mode": MODE_VERIFY_EMAIL, "collect_email": True, "domain_security": True,
             "allowed_domains": []}
        )
    assert "allowed_domains" in excinfo.value.errors


def test_validate_policy_rejects_verify_email_without_collecting_an_email():
    with pytest.raises(PolicyError) as excinfo:
        validate_policy({"mode": MODE_VERIFY_EMAIL, "collect_email": False})
    assert "collect_email" in excinfo.value.errors


def test_validate_policy_rejects_an_identify_tier_that_collects_nothing():
    with pytest.raises(PolicyError) as excinfo:
        validate_policy({"mode": MODE_IDENTIFY})
    assert "collect_name" in excinfo.value.errors


def test_validate_policy_collects_several_errors_at_once():
    """The UI puts each message next to the input that caused it, so one round
    trip has to be able to report more than one problem."""
    with pytest.raises(PolicyError) as excinfo:
        validate_policy({"mode": "telepathy", "domain_security": True, "allowed_domains": []})
    assert set(excinfo.value.errors) >= {"mode", "domain_security", "allowed_domains"}


def test_validate_policy_normalises_the_allowlist():
    policy = validate_policy(
        {"mode": MODE_VERIFY_EMAIL, "collect_email": True, "domain_security": True,
         "allowed_domains": "Northwind.example, @contoso.example"}
    )
    assert policy["allowed_domains"] == ["northwind.example", "contoso.example"]


def test_validate_policy_preserves_unknown_fields():
    """A team must be able to add its own field to a policy without a migration
    and without this function knowing about it."""
    policy = validate_policy(
        {"mode": MODE_OPEN, "legal_footer": "Confidential", "scoring": {"weight": 0.4}}
    )
    assert policy["legal_footer"] == "Confidential"
    assert policy["scoring"] == {"weight": 0.4}


def test_validate_policy_defaults_inherit_to_false():
    assert validate_policy({"mode": MODE_OPEN})["inherit"] is False
    assert validate_policy({"mode": MODE_OPEN, "inherit": True})["inherit"] is True


def test_required_fields_follows_the_collect_flags():
    assert required_fields(validate_policy({"mode": MODE_OPEN})) == []
    assert required_fields(
        validate_policy({"mode": MODE_IDENTIFY, "collect_name": True})
    ) == ["name"]
    assert required_fields(
        validate_policy(
            {"mode": MODE_VERIFY_EMAIL, "collect_email": True, "collect_name": True}
        )
    ) == ["name", "email"]
