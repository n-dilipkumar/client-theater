"""Tests for the pure custom-domain and share-link rules.

These are the behaviours the WF-017 research actually sourced, plus the design
inferences called out in :mod:`dsr.domains`. They are tested without a database
because none of them touch storage: the point of keeping the rules pure is that
the sourced behaviour can be pinned exhaustively.
"""

from __future__ import annotations

import pytest

from dsr.domains import (
    DomainError,
    StaticResolver,
    _SECRET_ALPHABET,
    build_collaborator_url,
    build_share_url,
    generate_collaborator_token,
    generate_link_secret,
    is_valid_colour,
    is_valid_font_family,
    link_path,
    link_secret_from_path,
    normalise_domain,
    recognised_host,
    room_slug,
    slugify,
)


# --------------------------------------------------------------------------- #
# Domain normalisation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("proposals.acme.com", "proposals.acme.com"),
        ("  Proposals.Acme.Com  ", "proposals.acme.com"),
        ("https://proposals.acme.com", "proposals.acme.com"),
        ("http://proposals.acme.com/", "proposals.acme.com"),
        ("proposals.acme.com/Proposal-Name", "proposals.acme.com"),
        ("proposals.acme.com?utm=email", "proposals.acme.com"),
        ("proposals.acme.com.", "proposals.acme.com"),
        ("PROPOSALS.acme.com#top", "proposals.acme.com"),
    ],
)
def test_normalisation_recovers_from_what_people_paste(raw, expected):
    assert normalise_domain(raw) == expected


def test_subdomain_format_is_required():
    """Sourced: the value "needs to be in a subdomain format"."""
    with pytest.raises(DomainError, match="subdomain format"):
        normalise_domain("acme.com")


def test_deep_subdomains_are_accepted():
    assert normalise_domain("a.b.c.acme.com") == "a.b.c.acme.com"


@pytest.mark.parametrize(
    "raw,message",
    [
        ("", "required"),
        ("   ", "required"),
        (None, "must be a string"),
        (123, "must be a string"),
        ("proposals.acme.com:8443", "must not include a port"),
        ("192.168.1.10", "IP address cannot be used"),
        ("proposals.acme.invalid_tld", "invalid label"),
        ("-bad.acme.com", "invalid label"),
        ("proposals..acme.com", "not a valid domain"),
        ("proposals.acme.c", "top-level domain must be alphabetic"),
        ("proposals.acme.123", "top-level domain must be alphabetic"),
    ],
)
def test_invalid_domains_are_rejected_with_a_reason(raw, message):
    with pytest.raises(DomainError, match=message):
        normalise_domain(raw)


def test_domain_length_is_bounded():
    label = "a" * 63
    too_long = ".".join([label] * 4) + ".com"
    with pytest.raises(DomainError, match="too long"):
        normalise_domain(too_long)


# --------------------------------------------------------------------------- #
# Link secrets
# --------------------------------------------------------------------------- #


def test_link_secret_avoids_ambiguous_glyphs():
    """These get read aloud and retyped, so 0/O/1/l/I are excluded."""
    secrets_minted = [generate_link_secret() for _ in range(200)]
    for secret in secrets_minted:
        assert len(secret) == 10
        assert not set(secret) & set("01OIl")


def test_link_secret_contains_no_hyphen():
    """No hyphen is what makes the path split unambiguous."""
    for secret in [generate_link_secret() for _ in range(200)]:
        assert "-" not in secret


def test_link_secrets_are_unique():
    assert len({generate_link_secret() for _ in range(500)}) == 500


def test_collaborator_token_is_longer_than_the_buyer_secret():
    """Sourced: collaborator URLs are internal-only, so a separate, longer token."""
    assert len(generate_collaborator_token()) > len(generate_link_secret())


def test_short_link_secret_is_refused():
    with pytest.raises(DomainError, match="at least 8"):
        generate_link_secret(4)


# --------------------------------------------------------------------------- #
# Slugs and paths
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Proposal Name", "Proposal-Name"),
        ("Northwind Traders", "Northwind-Traders"),
        ("  Acme  ", "Acme"),
        ("Sales / Pricing v2", "Sales-Pricing-v2"),
        ("NorthwindTraders", "Northwind-Traders"),
        ("Réunion", "Reunion"),
    ],
)
def test_slugify_keeps_words_readable(name, expected):
    assert slugify(name) == expected


def test_slugify_falls_back_when_nothing_survives():
    assert slugify("///") == "room"
    assert slugify(None) == "room"


def test_slug_matches_the_researched_example():
    """Sourced example: proposals.acme.com/Proposal-Name-aB3xY9zK1q."""
    assert room_slug("Proposal Name", "aB3xY9zK1q") == "Proposal-Name-aB3xY9zK1q"


def test_link_path_shape():
    assert link_path("Proposal Name", "aB3xY9zK1q") == "/r/Proposal-Name-aB3xY9zK1q"


def test_secret_is_recovered_from_the_path():
    secret = generate_link_secret()
    assert link_secret_from_path(f"/r/Proposal-Name-{secret}") == secret


def test_secret_recovery_handles_multi_word_slugs():
    """The split is on the *final* hyphen, so multi-word names still resolve."""
    secret = generate_link_secret()
    assert link_secret_from_path(f"/r/Northwind-Traders-Enterprise-{secret}") == secret


def test_our_secret_has_the_shape_of_the_researched_example():
    """The research illustrates the link as `Proposal-Name-aB3xY9zK1q`.

    The *shape* is sourced: a readable slug, a hyphen, then ten mixed-case
    alphanumeric characters. The literal example token is not reproduced,
    because it contains `1`, which this implementation excludes along with the
    other glyphs people confuse when retyping a link from a PDF. What matters is
    that a generated secret is indistinguishable in shape from the documented
    one, and that a slugified name plus a secret reproduces the documented
    path.

    Asserted over many draws rather than one: "mixed case and alphanumeric"
    is a property of the alphabet, not of any individual secret. A single draw
    has roughly a 1-in-6 chance of containing no digit at all, so asserting it
    on one secret would be a coin-flip test.
    """
    draws = [generate_link_secret() for _ in range(200)]

    for secret in draws:
        assert len(secret) == 10
        assert secret.isalnum()
        # Every character comes from the documented alphabet.
        assert set(secret) <= set(_SECRET_ALPHABET)

    # The alphabet itself supplies both cases and digits, which is what makes a
    # generated secret match the shape of the documented example.
    assert any(char.isupper() for char in _SECRET_ALPHABET)
    assert any(char.isdigit() for char in _SECRET_ALPHABET)
    assert any(char.islower() for char in _SECRET_ALPHABET)

    # And across real draws both cases and digits do appear.
    joined = ''.join(draws)
    assert any(char.isupper() for char in joined)
    assert any(char.isdigit() for char in joined)
    assert any(char.islower() for char in joined)

    secret = draws[0]
    assert room_slug("Proposal Name", secret).startswith("Proposal-Name-")
    assert link_secret_from_path(link_path("Proposal Name", secret)) == secret


@pytest.mark.parametrize(
    "path", ["", "/", "/r/", "/r/plain", None, 42, "/r/Proposal-Name-", "/collab/abc"]
)
def test_secret_recovery_returns_none_when_absent(path):
    assert link_secret_from_path(path) is None


def test_secret_recovery_rejects_a_suffix_outside_the_alphabet():
    """`Overview` contains an uppercase O, which the secret alphabet excludes.

    A trailing word that cannot be a secret is not a link, so recovery returns
    None rather than guessing.
    """
    assert link_secret_from_path("/r/Proposal-Overview") is None
    assert link_secret_from_path("/r/Proposal-final") is None


# --------------------------------------------------------------------------- #
# Share URL construction
# --------------------------------------------------------------------------- #


BASE = "http://127.0.0.1:8000"


def _room(**data):
    payload = {"name": "Proposal Name", "link_secret": "aB3xY9zK1q"}
    payload.update(data)
    return {"data": payload}


def test_share_url_uses_the_default_host_before_a_domain_is_configured():
    """Sourced: default links can be shared during propagation and keep working."""
    url = build_share_url(_room(), base_url=BASE)
    assert url == f"{BASE}/r/Proposal-Name-aB3xY9zK1q"


def test_share_url_switches_host_once_the_domain_verifies():
    url = build_share_url(_room(domain="proposals.acme.com", domain_status="verified"), base_url=BASE)
    assert url == "https://proposals.acme.com/r/Proposal-Name-aB3xY9zK1q"


def test_share_url_ignores_an_unverified_domain():
    """A domain that has not passed verification must not appear in a share link."""
    for status in ("unverified", "failed", None):
        url = build_share_url(_room(domain="proposals.acme.com", domain_status=status), base_url=BASE)
        assert url.startswith(BASE)


def test_share_url_keeps_the_slug_and_secret_when_the_host_changes():
    """Sourced: the slug is preserved and the secret is mandatory on every host."""
    default = build_share_url(_room(), base_url=BASE)
    custom = build_share_url(_room(domain="proposals.acme.com", domain_status="verified"), base_url=BASE)
    assert default.split("/", 3)[3:] == custom.split("/", 3)[3:]


def test_share_url_never_omits_the_secret():
    """Sourced: the secret is non-removable and applies even with a custom domain."""
    custom = build_share_url(_room(domain="proposals.acme.com", domain_status="verified"), base_url=BASE)
    assert custom.endswith("aB3xY9zK1q")


def test_share_url_without_a_secret_is_an_error():
    with pytest.raises(DomainError, match="no link secret"):
        build_share_url({"data": {"name": "Proposal Name"}}, base_url=BASE)


def test_collaborator_url_bypasses_the_custom_domain():
    """Sourced: collaborator URLs "won't use the custom domain"."""
    room = _room(domain="proposals.acme.com", domain_status="verified", collaborator_token="tok123")
    url = build_collaborator_url(room, base_url=BASE)
    assert url == f"{BASE}/collab/tok123"
    assert "acme.com" not in url


def test_collaborator_url_needs_a_token():
    with pytest.raises(DomainError, match="no collaborator token"):
        build_collaborator_url(_room(), base_url=BASE)


# --------------------------------------------------------------------------- #
# Host recognition
# --------------------------------------------------------------------------- #


def test_default_host_is_recognised():
    assert recognised_host("127.0.0.1:8000", base_url=BASE)


def test_claimed_domains_are_recognised():
    assert recognised_host("proposals.acme.com", claimed_domains=["proposals.acme.com"], base_url=BASE)


def test_an_unknown_host_is_not_recognised():
    """Routing, not identity: a host we do not serve is not our traffic."""
    assert not recognised_host("evil.example.net", claimed_domains=["proposals.acme.com"], base_url=BASE)


def test_recognition_is_case_and_port_insensitive():
    assert recognised_host("Proposals.Acme.com:443", claimed_domains=["proposals.acme.com"], base_url=BASE)


def test_an_unusable_stored_domain_is_skipped_not_fatal():
    """A stored value that no longer normalises must not break request routing."""
    assert not recognised_host("good.acme.com", claimed_domains=["acme.com"], base_url=BASE)


@pytest.mark.parametrize("host", ["", "   ", None, 42])
def test_junk_hosts_are_not_recognised(host):
    assert not recognised_host(host, base_url=BASE)


# --------------------------------------------------------------------------- #
# Brand token safety
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "value",
    [
        "#fff",
        "#22c55e",
        "#22C55E",
        "#22c55eff",
        "rgb(34, 197, 94)",
        "rgba(34, 197, 94, 0.5)",
        "hsl(142, 71%, 45%)",
        "oklch(0.7 0.15 145)",
        "rebeccapurple",
        "transparent",
    ],
)
def test_valid_colours_are_accepted(value):
    assert is_valid_colour(value)


@pytest.mark.parametrize(
    "value",
    [
        # The dangerous one: a url() beacon smuggled in through a style attribute.
        "url(https://evil.example.net/beacon.png)",
        "red; background-image: url(https://evil.example.net/x)",
        "expression(alert(1))",
        "red)",
        "#gg0000",
        "",
        "   ",
        None,
        42,
        "#" + "f" * 65,
    ],
)
def test_dangerous_colours_are_rejected(value):
    assert not is_valid_colour(value)


@pytest.mark.parametrize(
    "value",
    [
        "Fira Sans",
        "'Fira Sans', sans-serif",
        "Inter, 'Helvetica Neue', Arial, sans-serif",
        "Fira Code, monospace",
    ],
)
def test_valid_font_stacks_are_accepted(value):
    assert is_valid_font_family(value)


@pytest.mark.parametrize(
    "value",
    [
        "@import url(https://evil.example.net/x.css)",
        "@font-face",
        "Fira Sans; background: url(https://evil.example.net/x)",
        "Fira Sans } body { display: none",
        "url(https://evil.example.net/font.woff2)",
        "",
        None,
        42,
    ],
)
def test_font_stacks_that_could_escape_the_property_are_rejected(value):
    assert not is_valid_font_family(value)


# --------------------------------------------------------------------------- #
# Resolvers
# --------------------------------------------------------------------------- #


def test_static_resolver_reports_a_match():
    resolver = StaticResolver({"proposals.acme.com": ["cname.dsr.test"]})
    assert resolver.resolves_to("proposals.acme.com", "cname.dsr.test") == (True, ["cname.dsr.test"])


def test_static_resolver_reports_a_mismatch_with_what_it_saw():
    """A failed check must be diagnosable, not just false."""
    resolver = StaticResolver({"other.acme.com": ["somewhere.else.test"]})
    matched, observed = resolver.resolves_to("other.acme.com", "cname.dsr.test")
    assert matched is False
    assert observed == ["somewhere.else.test"]


def test_static_resolver_treats_an_unpropagated_record_as_unresolved():
    """A host absent from the table is what a CNAME mid-propagation looks like."""
    resolver = StaticResolver({})
    assert resolver.resolves_to("proposals.acme.com", "cname.dsr.test") == (False, [])


def test_static_resolver_matches_an_edge_address_for_a_flattened_cname():
    """Most resolvers flatten a CNAME to an address, so an edge address stands in."""
    resolver = StaticResolver({"proposals.acme.com": ["203.0.113.7"]})
    assert resolver.resolves_to("proposals.acme.com", "203.0.113.7")[0] is True
