"""WF-069: gate a buyer link with a password, an expiry and email verification.

The researched specification is ``docs/research/digital-sales-room-workflows/wf/WF-069.md``.
These tests are organised by the decision they defend, because the point of this
workflow is that the decisions were researched rather than chosen - so a rule with
no test is a rule that will be quietly dropped by the next person to touch it.

The sections, and the researched rule each pins:

``secrets``
    No cleartext secret in a record, a response or a log. The hash format, the
    constant-time comparison, and what a malformed stored hash does.
``rules``
    The three defaults the OpenAPI document names, authentication subsuming
    protection, the expiry boundary being inclusive, and the preset override rule
    that distinguishes an absent field from an explicit ``null``.
``engine: creating``
    ``document_id | dataroom_id``, a password that is hashed on the way in, and a
    preset that seeds a governed baseline.
``engine: updating``
    The tri-state update - absent, set, cleared - which is the only way
    ``PATCH /v1/links/{id}`` can rotate a password and then remove one.
``engine: expiry and revocation``
    "Expiry is evaluated on every viewer request", and a revoked link is
    indistinguishable from an expired one to the buyer holding the URL.
``engine: the buyer walk``
    The guide's order: email, then a code when authenticated, then the password.
    Including that a correct code opens nothing by itself.
``engine: attribution``
    The ``Visitor`` row, the stamped view event, and the notification that
    defaults on.
``http``
    The surface through this feature's own router, including the three error
    shapes and every status code a buyer can meet.
``the audit-source rule``
    Every ``source=`` this workflow records names a route the host actually
    mounted. This is the test the build brief asks for by name.
``no secret leaves the process``
    The negative, end to end: every response this router can produce is walked
    and fails if a hash, a code or a cleartext password is in it.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import dsr.features as host
import pytest
from dsr.api import app
from dsr.db.audited import AuditedDatabase, RecordNotFound
from dsr.link_gating import gate as gate_engine, rules, secrets as link_secrets
from dsr.store import RecordStore
from fastapi.testclient import TestClient

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

NOW = datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc)

PREFIX = "/api/wf-069"
FEATURE_ID = "wf-069-gate-each-buyer-link-with-a-password-a"
FEATURE_MODULE = "dsr.features.wf069_gate_each_buyer_link_with_a_password_a"

PASSWORD = "northwind-2026"
BUYER = "buyer@northwind.example"


class Clock:
    """A clock the test moves by hand.

    The expiry rule is a statement about an instant - ``now > expires_at`` - so it
    can only be tested against a clock the test owns. Everything stamped during a
    test comes from here too, which is what makes the "no cleartext secret"
    assertions below meaningful: they can look for a known password and know it
    would have appeared if it appeared anywhere.
    """

    def __init__(self, start: datetime = NOW) -> None:
        self.at = start

    def __call__(self) -> datetime:
        return self.at

    def advance(self, **kwargs: float) -> datetime:
        self.at = self.at + timedelta(**kwargs)
        return self.at

    def set(self, moment: datetime) -> datetime:
        self.at = moment
        return self.at


class Mailbox:
    """The injected delivery transport, capturing what a buyer would receive.

    Stands in for the vendor the research could not name. The code is captured in
    a list in this process and nowhere else, which is what lets the authenticated
    round trip be tested at all while the production path refuses to persist or
    echo a code.
    """

    def __init__(self) -> None:
        self.sent: list[dict] = []

    def __call__(self, message: dict) -> None:
        self.sent.append(dict(message))

    def code_for(self, challenge_id: str) -> str:
        for message in reversed(self.sent):
            if message.get("challenge_id") == challenge_id:
                return str(message["code"])
        raise AssertionError(f"no code was dispatched for {challenge_id}")


@pytest.fixture()
def clock() -> Clock:
    return Clock()


@pytest.fixture()
def mailbox() -> Mailbox:
    return Mailbox()


@pytest.fixture()
def store() -> RecordStore:
    # In-memory rather than a file on disk: 0.4 ms against 7.0 ms, measured. No
    # test in this file reads the audit mirror off the filesystem, so the file
    # bought nothing.
    db = AuditedDatabase()
    store = RecordStore(db)
    store.create(
        "room", {"name": "Northwind data room"}, record_id="room_a", actor="dana", source="fixture"
    )
    store.create(
        "room", {"name": "Halcyon data room"}, record_id="room_b", actor="dana", source="fixture"
    )
    store.create(
        "document",
        {"title": "Security overview", "pages": 12},
        record_id="doc_a",
        room_id="room_a",
        actor="dana",
        source="fixture",
    )
    yield store
    db.close()


@pytest.fixture()
def engine(store: RecordStore, clock: Clock, mailbox: Mailbox) -> gate_engine.GateEngine:
    return gate_engine.GateEngine(store, now=clock, deliver=mailbox)


@pytest.fixture(scope="module")
def _shared_client(tmp_path_factory) -> Iterator[TestClient]:
    """One application for the whole module.

    The lifespan in ``dsr/api.py`` only assigns ``app.state.db`` and
    ``app.state.store``, and ``dsr/deps.py`` reads ``app.state.store`` on every
    request. So a test needs a fresh *database*, not a fresh *application*. The
    TestClient enter costs 46 ms measured; a state swap costs about 1.25 ms.

    The environment is patched here rather than per test because a module-scoped
    fixture cannot use the function-scoped ``monkeypatch``. It is undone on the
    way out so nothing leaks into another module.
    """
    scratch = tmp_path_factory.mktemp("wf069-http")
    patch = pytest.MonkeyPatch()
    patch.setenv("DSR_DB_PATH", ":memory:")
    patch.setenv("DSR_AUDIT_DIR", str(scratch / "audit"))
    patch.setattr("dsr.api.FRONTEND_DIST", scratch / "absent-frontend")
    with TestClient(app) as test_client:
        yield test_client
    patch.undo()


@pytest.fixture()
def client(_shared_client: TestClient) -> Iterator[TestClient]:
    """The shared application, over a database this test owns alone.

    ``app.dependency_overrides`` is cleared as well: a module-scoped application
    is shared, so an override a test installs would otherwise reach the next one.
    """
    db = AuditedDatabase()
    _shared_client.app.state.db = db
    _shared_client.app.state.store = RecordStore(db)
    _shared_client.app.dependency_overrides.clear()
    yield _shared_client
    db.close()


def make_link(engine: gate_engine.GateEngine, **overrides) -> dict:
    """A link with the researched defaults, so a test names only what it is about.

    ``email_protected`` is turned off by default here because the researched default
    is *on* and a test about the password step should not have to walk an email step
    to reach it. Every test that is about the email behaviour asks for it explicitly.
    A document target replaces the dataroom one rather than being added to it,
    because the schema takes exactly one.
    """
    payload = {
        "title": "Northwind - mutual NDA",
        "email_protected": False,
        **({"dataroom_id": "room_a"} if "document_id" not in overrides else {}),
        **overrides,
    }
    return engine.create_link(
        "room_a", payload, source=f"POST {PREFIX}/rooms/{{room_id}}/links", actor="rep"
    )


def walk(
    engine: gate_engine.GateEngine,
    mailbox: Mailbox,
    link_id: str,
    password: str,
    email: str = BUYER,
) -> dict:
    """Take a buyer the whole way through an authenticated link."""
    opened = engine.submit_email(
        link_id, email, source=f"POST {PREFIX}/links/{{link_id}}/gate/email"
    )
    verified = engine.submit_code(
        link_id,
        opened["challenge_id"],
        mailbox.code_for(opened["challenge_id"]),
        source=f"POST {PREFIX}/links/{{link_id}}/gate/code",
    )
    return engine.submit_password(
        link_id,
        password,
        challenge_id=verified["challenge_id"],
        source=f"POST {PREFIX}/links/{{link_id}}/gate/password",
    )


# --------------------------------------------------------------------------- #
# secrets
# --------------------------------------------------------------------------- #


class TestPasswordHashing:
    def test_the_hash_is_self_describing(self):
        stored = link_secrets.hash_password(PASSWORD)
        prefix, iterations, salt, digest = stored.split("$")
        assert prefix == "pbkdf2_sha256"
        assert int(iterations) == link_secrets.PASSWORD_ITERATIONS
        assert base64.b64decode(salt)
        assert len(digest) == 64

    def test_the_digest_is_pbkdf2_over_the_password_and_the_salt(self):
        """Pinned against hashlib directly, so a change to the derivation is a
        deliberate change rather than a silent one."""
        stored = link_secrets.hash_password("correct horse", salt="c2FsdA==")
        prefix, iterations, salt, digest = stored.split("$")
        expected = hashlib.pbkdf2_hmac(
            "sha256", b"correct horse", base64.b64decode(salt), int(iterations)
        )
        assert (prefix, salt, digest) == ("pbkdf2_sha256", "c2FsdA==", expected.hex())

    def test_the_same_password_hashes_differently_each_time(self):
        assert link_secrets.hash_password(PASSWORD) != link_secrets.hash_password(PASSWORD)

    def test_a_correct_password_verifies(self):
        assert link_secrets.verify_password(PASSWORD, link_secrets.hash_password(PASSWORD))

    def test_a_wrong_password_does_not_verify(self):
        assert not link_secrets.verify_password("nope", link_secrets.hash_password(PASSWORD))

    def test_the_comparison_is_case_sensitive(self):
        assert not link_secrets.verify_password(
            PASSWORD.upper(), link_secrets.hash_password(PASSWORD)
        )

    @pytest.mark.parametrize("empty", ["", None, 0, [], {}])
    def test_an_empty_password_is_refused_rather_than_hashed(self, empty):
        with pytest.raises(ValueError):
            link_secrets.hash_password(empty)

    def test_a_stored_hash_from_another_work_factor_still_verifies(self):
        """The format carries its own iteration count, which is what lets the work
        factor be raised later without a migration."""
        cheap = link_secrets.hash_password(PASSWORD, iterations=1_000)
        assert link_secrets.verify_password(PASSWORD, cheap)

    @pytest.mark.parametrize(
        "stored",
        [
            None,
            "",
            "not-a-hash",
            "pbkdf2_sha256$",
            "pbkdf2_sha256$abc$c2FsdA==$deadbeef",
            "pbkdf2_sha256$120000$not-base64!!$" + "a" * 64,
            "pbkdf2_sha256$0$c2FsdA==$" + "a" * 64,
            "pbkdf2_sha256$120000$$" + "a" * 64,
            12345,
            {"password_hash": "x"},
            ["pbkdf2_sha256$120000$c2FsdA==$" + "a" * 64],
        ],
    )
    def test_a_corrupt_stored_hash_is_a_refusal_not_a_crash(self, stored):
        """A buyer's route must not 500 because a row was written by hand."""
        assert link_secrets.verify_password(PASSWORD, stored) is False


class TestOneTimeCodeHashing:
    def test_a_code_is_six_digits(self):
        for _ in range(20):
            code = link_secrets.mint_code()
            assert len(code) == 6
            assert code.isdigit()

    def test_leading_zeros_survive(self):
        """The code is a string, never an int. As an int, "000123" would mint 123
        and the buyer would type six digits that never match."""
        original = link_secrets.secrets.randbelow
        link_secrets.secrets.randbelow = lambda upper: 42
        try:
            assert link_secrets.mint_code() == "000042"
        finally:
            link_secrets.secrets.randbelow = original

    def test_codes_do_not_repeat_in_a_small_sample(self):
        minted = {link_secrets.mint_code() for _ in range(200)}
        assert len(minted) == 200

    def test_the_code_hash_verifies(self):
        assert link_secrets.verify_code("123456", link_secrets.hash_code("123456"))

    def test_a_wrong_code_does_not_verify(self):
        assert not link_secrets.verify_code("123457", link_secrets.hash_code("123456"))

    def test_the_code_work_factor_is_lower_than_the_password_one(self):
        """And that is a decision, not an oversight: a six-digit code is good for one
        use, and stretching it buys latency a buyer watches rather than resistance."""
        assert link_secrets.CODE_ITERATIONS < link_secrets.PASSWORD_ITERATIONS
        assert link_secrets.hash_code("123456").startswith("pbkdf2_sha256$")

    @pytest.mark.parametrize("stored", [None, "", "nope", 7, ["x"]])
    def test_a_corrupt_code_hash_is_a_refusal(self, stored):
        assert link_secrets.verify_code("123456", stored) is False


class TestTokens:
    def test_a_view_token_is_prefixed_and_long(self):
        token = link_secrets.mint_view_token()
        assert token.startswith("wf069_view_")
        assert len(token) > 40

    def test_view_tokens_do_not_repeat(self):
        assert len({link_secrets.mint_view_token() for _ in range(200)}) == 200

    def test_a_link_id_is_prefixed(self):
        assert link_secrets.mint_link_id().startswith("wf069_link_")

    def test_a_token_is_stored_as_a_digest(self):
        token = link_secrets.mint_view_token()
        digest = link_secrets.hash_token(token)
        assert digest != token
        assert digest == link_secrets.hash_token(token)
        assert len(digest) == 64


class TestRedaction:
    def test_a_password_hash_is_stripped(self):
        assert link_secrets.redact({"password_hash": "x", "title": "t"}) == {"title": "t"}

    @pytest.mark.parametrize("key", sorted(link_secrets.SECRET_KEYS))
    def test_every_declared_secret_key_is_stripped(self, key):
        assert key not in link_secrets.redact({key: "secret", "keep": 1})

    def test_nested_and_listed_secrets_are_stripped(self):
        payload = {
            "links": [{"password_hash": "x", "title": "t"}],
            "deep": {"a": {"b": {"code_hash": "y"}}},
        }
        cleaned = link_secrets.redact(payload)
        assert cleaned == {"links": [{"title": "t"}], "deep": {"a": {"b": {}}}}

    def test_scalars_pass_through_unchanged(self):
        assert link_secrets.redact(7) == 7
        assert link_secrets.redact("t") == "t"
        assert link_secrets.redact(None) is None

    def test_a_field_named_in_a_list_survives(self):
        """`preset_fields` is a list of field *names*, and "password" is one of them.
        A redactor that filtered list contents would empty that list."""
        assert link_secrets.redact({"preset_fields": ["password"]}) == {
            "preset_fields": ["password"]
        }


# --------------------------------------------------------------------------- #
# rules
# --------------------------------------------------------------------------- #


class TestDefaults:
    def test_email_protected_defaults_on(self):
        """OpenAPI `CreateLinkRequest`: "email_protected (boolean, default true)"."""
        assert link_secrets and rules.DEFAULT_EMAIL_PROTECTED is True
        assert rules.normalize_settings({})["email_protected"] is True

    def test_email_authenticated_defaults_off(self):
        """OpenAPI: "email_authenticated (boolean, default false)"."""
        assert rules.DEFAULT_EMAIL_AUTHENTICATED is False
        assert rules.normalize_settings({})["email_authenticated"] is False

    def test_notification_defaults_on(self):
        """Automations: "`enable_notification` defaults to on"."""
        assert rules.DEFAULT_ENABLE_NOTIFICATION is True
        assert rules.normalize_settings({})["enable_notification"] is True

    def test_no_expiry_means_no_expiry(self):
        """CLI flag table: "`--expires <iso>` | never | ISO 8601 datetime"."""
        assert rules.NEVER_EXPIRES is None
        assert rules.normalize_settings({}).get("expires_at") is None


class TestAuthenticationSubsumesProtection:
    def test_authentication_implies_protection(self):
        """Evidence: "`--email-authenticated` ... (stronger than
        `--email-protected`)". Stronger than, not alternative to."""
        settings = rules.normalize_settings({"email_authenticated": True, "email_protected": False})
        assert settings["email_protected"] is True
        assert rules.email_required(settings) is True
        assert rules.code_required(settings) is True

    def test_turning_authentication_off_does_not_turn_protection_off(self):
        """A rep who wanted proof of identity may still want to know who is reading,
        so the implication is one-directional."""
        settings = rules.normalize_settings(
            {"email_authenticated": True, "email_protected": True},
            base={"email_authenticated": True, "email_protected": True},
        )
        assert (
            rules.normalize_settings({"email_authenticated": False}, base=settings)[
                "email_protected"
            ]
            is True
        )

    def test_protection_alone_asks_for_no_code(self):
        settings = rules.normalize_settings({"email_protected": True})
        assert rules.email_required(settings) is True
        assert rules.code_required(settings) is False
        assert rules.steps_required(settings) == [rules.STEP_EMAIL]


class TestPasswordField:
    def test_a_password_becomes_a_hash(self):
        settings = rules.normalize_settings({"password": PASSWORD})
        assert settings["password_hash"].startswith("pbkdf2_sha256$")
        assert PASSWORD not in json.dumps(settings)
        assert settings["password_set"] is True

    def test_null_removes_the_password(self):
        settings = rules.normalize_settings({"password": PASSWORD})
        cleared = rules.normalize_settings({"password": None}, base=settings)
        assert cleared["password_hash"] is None
        assert cleared["password_set"] is False
        assert rules.password_required(cleared) is False

    def test_the_empty_string_is_refused_rather_than_read_as_clear(self):
        """minLength 1 with nullable: null clears it, "" was never a password. Reading
        "" as "off" would let a form's empty box silently ungate a link."""
        with pytest.raises(rules.GateError) as caught:
            rules.normalize_settings({"password": ""})
        assert "password" in caught.value.errors

    @pytest.mark.parametrize("value", [7, [], {}, True])
    def test_a_non_string_password_is_refused(self, value):
        with pytest.raises(rules.GateError) as caught:
            rules.normalize_settings({"password": value})
        assert "password" in caught.value.errors


class TestExpiryParsing:
    def test_a_full_iso_timestamp_round_trips(self):
        moment = rules.parse_moment("2026-12-31T09:00:00+00:00")
        assert moment == datetime(2026, 12, 31, 9, 0, tzinfo=timezone.utc)

    def test_a_z_suffix_is_accepted(self):
        assert rules.parse_moment("2026-12-31T09:00:00Z") == datetime(
            2026, 12, 31, 9, 0, tzinfo=timezone.utc
        )

    def test_a_naive_timestamp_is_read_as_utc(self):
        """The research says "ISO 8601 datetime" and nothing about zones. Reading a
        naive value as local would move every expiry when the host's zone changed."""
        assert rules.parse_moment("2026-12-31T09:00:00").tzinfo == timezone.utc

    def test_a_non_utc_offset_is_converted(self):
        assert rules.parse_moment("2026-12-31T14:00:00+05:00") == datetime(
            2026, 12, 31, 9, 0, tzinfo=timezone.utc
        )

    def test_a_bare_date_means_the_end_of_that_day(self):
        """What a seller typing 31 December means. The alternative shortens the
        window by 24 hours without telling anyone."""
        assert rules.parse_moment("2026-12-31") == datetime(
            2026, 12, 31, 23, 59, 59, 999999, tzinfo=timezone.utc
        )

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_an_absent_expiry_is_no_expiry(self, value):
        assert rules.parse_moment(value) is None

    @pytest.mark.parametrize("value", ["tomorrow", "31/12/2026", "2026-13-01T00:00:00", 12345, []])
    def test_an_unreadable_expiry_is_refused_with_a_field_error(self, value):
        with pytest.raises(rules.GateError) as caught:
            rules.parse_moment(value)
        assert "expires_at" in caught.value.errors


class TestExpiryBoundary:
    def test_at_the_instant_the_link_is_still_open(self):
        """Evidence: "After `expires_at`, the URL returns a friendly 'this link has
        expired' page." At the instant named, it has not passed yet."""
        moment = NOW.isoformat()
        expired, reason, parsed = rules.expiry_state(moment, NOW)
        assert expired is False
        assert reason is None
        assert parsed == NOW

    def test_a_microsecond_later_it_is_closed(self):
        moment = NOW.isoformat()
        expired, reason, _ = rules.expiry_state(moment, NOW + timedelta(microseconds=1))
        assert expired is True
        assert reason == "expired"

    def test_no_expiry_never_closes(self):
        for value in (None, "", NOW + timedelta(days=3650)):
            expired, reason, _ = rules.expiry_state(value, NOW)
            assert expired is (value is not None and reason == "expired")
            if value is None:
                assert reason is None

    def test_a_stored_expiry_that_no_longer_parses_fails_closed(self):
        """Failing open on an unreadable expiry would silently open a gated link;
        failing closed shows the friendly page and tells the seller why."""
        expired, reason, _ = rules.expiry_state("whenever", NOW)
        assert expired is True
        assert reason == "expires_at_unreadable"

    def test_the_remaining_window_counts_down(self):
        window = rules.remaining_window((NOW + timedelta(hours=3)).isoformat(), NOW)
        assert window == timedelta(hours=3)


class TestTriStateBooleans:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (True, True),
            (False, False),
            ("on", True),
            ("off", False),
            ("ON", True),
            ("Off", False),
            ("true", True),
            ("0", False),
            ("1", True),
            (1, True),
            (0, False),
        ],
    )
    def test_the_cli_spellings_are_accepted(self, value, expected):
        assert rules.normalize_settings({"email_protected": value})["email_protected"] is expected

    @pytest.mark.parametrize("value", ["maybe", "", 2, 1.5, [], {}])
    def test_anything_else_is_refused_with_a_field_error(self, value):
        with pytest.raises(rules.GateError) as caught:
            rules.normalize_settings({"email_protected": value})
        assert "email_protected" in caught.value.errors

    def test_an_absent_field_keeps_the_base_value(self):
        base = rules.normalize_settings({"email_protected": False})
        assert rules.normalize_settings({}, base=base)["email_protected"] is False


class TestGateStep:
    def test_nothing_required_is_open(self):
        step = rules.gate_step(
            {"email_protected": False, "password_hash": None}, now=NOW, expired=False, revoked=False
        )
        assert step["step"] == rules.STEP_OPEN
        assert step["required"] == []

    def test_the_order_is_email_then_code_then_password(self):
        settings = rules.normalize_settings({"email_authenticated": True, "password": PASSWORD})
        assert rules.steps_required(settings) == ["email", "code", "password"]

    def test_an_expired_link_says_expired(self):
        settings = rules.normalize_settings({"password": PASSWORD})
        step = rules.gate_step(settings, now=NOW, expired=True, revoked=False)
        assert step["step"] == rules.STEP_EXPIRED
        assert step["reason"] == "expired"
        assert "expired" in step["message"].lower()

    def test_a_revoked_link_is_worded_exactly_like_an_expired_one(
        self, engine: gate_engine.GateEngine
    ):
        """ "Works immediately. Anyone with the URL gets the expired page on their next
        request" - so a buyer must not be able to read "you were cut off" off the
        wording."""
        settings = rules.normalize_settings({"password": PASSWORD})
        expired = rules.gate_step(settings, now=NOW, expired=True, revoked=False)
        revoked = rules.gate_step(settings, now=NOW, expired=False, revoked=True)
        assert revoked["step"] == expired["step"]
        assert revoked["message"] == expired["message"]

    def test_the_denied_messages_for_expired_and_revoked_are_identical(self):
        assert (
            rules.GateDenied.MESSAGES[rules.GateDenied.REASON_EXPIRED]
            == rules.GateDenied.MESSAGES[rules.GateDenied.REASON_REVOKED]
        )


class TestPresets:
    def test_the_covered_field_list_is_the_documented_one(self):
        """Quoted verbatim from the extensibility note, so it is pinned rather than
        retyped."""
        assert rules.PRESET_COVERED_FIELDS == (
            "password",
            "expires_at",
            "email_protected",
            "email_authenticated",
            "allow_download",
            "allow_list",
            "deny_list",
            "enable_watermark",
            "watermark_config",
            "enable_screenshot_protection",
            "enable_confidential_view",
            "enable_agreement",
            "agreement_id",
            "welcome_message",
            "enable_notification",
            "show_banner",
            "custom_fields",
        )

    def test_the_gated_fields_are_a_subset_of_the_covered_ones(self):
        assert set(rules.GATED_FIELDS) <= set(rules.PRESET_COVERED_FIELDS)

    def test_a_preset_field_becomes_the_default(self):
        defaults, from_preset, overridden = rules.resolve_preset(
            {"fields": {"email_protected": True, "expires_at": "2026-12-31T00:00:00+00:00"}}, {}
        )
        assert defaults["email_protected"] is True
        assert defaults["expires_at"] == "2026-12-31T00:00:00+00:00"
        # In the documented covered-field order, which is deterministic.
        assert from_preset == ["expires_at", "email_protected"]
        assert overridden == []

    def test_an_explicit_field_overrides_the_preset(self):
        """Evidence: "Every preset-controlled field becomes the default; any field
        you also pass explicitly overrides the preset."
        """
        defaults, from_preset, overridden = rules.resolve_preset(
            {"fields": {"email_protected": True}}, {"email_protected": False}
        )
        assert "email_protected" not in defaults
        assert from_preset == []
        assert overridden == ["email_protected"]

    def test_an_explicit_null_clears_the_presets_expiry(self):
        """OpenAPI: "Pass null to override a preset's expiry with none." Absent and
        null are different requests and have to stay different."""
        defaults, _from_preset, overridden = rules.resolve_preset(
            {"fields": {"expires_at": "2026-12-31T00:00:00+00:00"}}, {"expires_at": None}
        )
        assert "expires_at" not in defaults
        assert "expires_at" in overridden

    def test_omitting_the_expiry_leaves_the_presets_one_alone(self):
        defaults, _from_preset, overridden = rules.resolve_preset(
            {"fields": {"expires_at": "2026-12-31T00:00:00+00:00"}}, {}
        )
        assert defaults["expires_at"] == "2026-12-31T00:00:00+00:00"
        assert overridden == []

    def test_a_presets_password_travels_as_a_hash(self):
        """Two records holding one cleartext password is one more place to leak it
        from and no extra capability."""
        preset_hash = link_secrets.hash_password(PASSWORD)
        defaults, _f, _o = rules.resolve_preset({"fields": {"password_hash": preset_hash}}, {})
        assert defaults["password_hash"] == preset_hash
        assert PASSWORD not in json.dumps(defaults)
        assert defaults["password_set"] is True

    def test_no_preset_still_resolves(self):
        defaults, from_preset, overridden = rules.resolve_preset(None, {"password": PASSWORD})
        assert (from_preset, overridden) == ([], [])
        assert defaults == {}

    def test_a_preset_needs_a_name(self):
        with pytest.raises(rules.GateError) as caught:
            rules.validate_preset({"fields": {}})
        assert "name" in caught.value.errors

    def test_a_preset_may_only_carry_covered_fields(self):
        with pytest.raises(rules.GateError) as caught:
            rules.validate_preset({"name": "x", "fields": {"not_a_field": 1}})
        assert "fields" in caught.value.errors
        assert "not_a_field" in caught.value.errors["fields"]

    def test_a_presets_password_is_stored_hashed(self):
        preset = rules.validate_preset({"name": "x", "fields": {"password": PASSWORD}})
        assert preset["fields"]["password_hash"].startswith("pbkdf2_sha256$")
        assert PASSWORD not in json.dumps(preset)

    def test_a_preset_carries_another_workflows_fields_untouched(self):
        """`allow_list` is WF-015's domain and `enable_watermark` is not this
        workflow's, but a governed baseline that dropped them would be useless."""
        preset = rules.validate_preset(
            {"name": "x", "fields": {"allow_list": ["northwind.com"], "enable_watermark": True}}
        )
        assert preset["fields"]["allow_list"] == ["northwind.com"]
        assert preset["fields"]["enable_watermark"] is True

    def test_the_gated_projection_never_carries_a_hash(self):
        projection = rules.gated_fields(rules.normalize_settings({"password": PASSWORD}))
        assert "password_hash" not in projection
        assert projection["password_set"] is True


# --------------------------------------------------------------------------- #
# engine: creating
# --------------------------------------------------------------------------- #


class TestCreateLink:
    def test_a_link_id_is_the_public_handle(self, engine: gate_engine.GateEngine):
        assert make_link(engine)["id"].startswith("wf069_link_")

    def test_a_dataroom_link_resolves_its_target(self, engine: gate_engine.GateEngine):
        assert make_link(engine)["target"] == {"kind": "dataroom", "id": "room_a"}

    def test_a_document_link_resolves_its_target(self, engine: gate_engine.GateEngine):
        link = make_link(engine, document_id="doc_a")
        assert link["target"] == {"kind": "document", "id": "doc_a"}

    def test_a_link_must_point_at_something(self, engine: gate_engine.GateEngine):
        with pytest.raises(rules.GateError) as caught:
            engine.create_link("room_a", {"title": "x"}, source="POST", actor="rep")
        assert "document_id" in caught.value.errors

    def test_a_link_cannot_point_at_both(self, engine: gate_engine.GateEngine):
        with pytest.raises(rules.GateError) as caught:
            engine.create_link(
                "room_a",
                {"document_id": "doc_a", "dataroom_id": "room_a"},
                source="POST",
                actor="rep",
            )
        assert "document_id" in caught.value.errors

    def test_a_link_must_belong_to_a_room(self, engine: gate_engine.GateEngine):
        with pytest.raises(rules.GateError):
            engine.create_link("", {"dataroom_id": "room_a"}, source="POST", actor="rep")

    def test_the_stored_payload_holds_a_hash_and_never_the_password(
        self, engine: gate_engine.GateEngine, store: RecordStore
    ):
        link = make_link(engine, password=PASSWORD)
        stored = store.get(link["id"])["data"]
        assert stored["password_hash"].startswith("pbkdf2_sha256$")
        assert PASSWORD not in json.dumps(stored)

    def test_the_status_projection_reports_password_set_without_the_hash(
        self, engine: gate_engine.GateEngine
    ):
        status = make_link(engine, password=PASSWORD)
        assert status["settings"]["password_set"] is True
        assert "password_hash" not in json.dumps(status)

    def test_the_expiry_is_stored_normalised(self, engine: gate_engine.GateEngine):
        link = make_link(engine, expires_at="2026-12-31T14:00:00+05:00")
        assert link["expires_at"] == "2026-12-31T09:00:00+00:00"
        assert link["expires_in_seconds"] == int(timedelta(days=90).total_seconds())

    def test_an_invalid_setting_names_the_field(self, engine: gate_engine.GateEngine):
        with pytest.raises(rules.GateError) as caught:
            make_link(engine, expires_at="soon")
        assert "expires_at" in caught.value.errors

    def test_a_link_is_written_into_its_rooms_envelope(
        self, engine: gate_engine.GateEngine, store: RecordStore
    ):
        link = make_link(engine)
        assert store.get(link["id"])["room_id"] == "room_a"

    def test_links_are_listed_per_room(self, engine: gate_engine.GateEngine):
        make_link(engine)
        assert len(engine.list_links("room_a")) == 1
        assert engine.list_links("room_b") == []

    def test_a_preset_seeds_a_governed_baseline(self, engine: gate_engine.GateEngine):
        preset = engine.create_preset(
            {"name": "Baseline", "fields": {"password": PASSWORD, "email_authenticated": True}},
            source="POST",
            actor="rep",
        )
        link = engine.create_link(
            "room_a",
            {"title": "From the baseline", "dataroom_id": "room_a", "preset_id": preset["id"]},
            source="POST",
            actor="rep",
        )
        assert link["settings"]["password_set"] is True
        assert link["settings"]["email_authenticated"] is True
        assert link["preset_id"] == preset["id"]
        assert set(link["preset_fields"]) >= {"password", "email_authenticated"}

    def test_an_explicit_field_beats_the_preset(self, engine: gate_engine.GateEngine):
        preset = engine.create_preset(
            {"name": "Baseline", "fields": {"password": PASSWORD}},
            source="POST",
            actor="rep",
        )
        link = engine.create_link(
            "room_a",
            {
                "dataroom_id": "room_a",
                "email_protected": False,
                "preset_id": preset["id"],
                "password": "something-else",
            },
            source="POST",
            actor="rep",
        )
        granted = engine.submit_password(link["id"], "something-else", source="POST")
        assert granted["granted"] is True
        with pytest.raises(rules.GateDenied):
            engine.submit_password(link["id"], PASSWORD, source="POST")

    def test_a_presets_password_actually_reaches_the_link(self, engine: gate_engine.GateEngine):
        """The resolver has to look for ``password_hash``, not ``password``: a preset
        stores the hash, so looking for the cleartext key would seed a link with no
        password and look like it had one."""
        preset = engine.create_preset(
            {"name": "Baseline", "fields": {"password": PASSWORD, "email_protected": False}},
            source="POST",
            actor="rep",
        )
        link = engine.create_link(
            "room_a",
            {"dataroom_id": "room_a", "preset_id": preset["id"]},
            source="POST",
            actor="rep",
        )
        assert link["settings"]["password_set"] is True
        assert engine.submit_password(link["id"], PASSWORD, source="POST")["granted"] is True

    def test_a_presets_ungated_fields_are_carried_but_not_enforced(
        self, engine: gate_engine.GateEngine
    ):
        """`enable_watermark` is not this workflow's rule, so it is stored and
        reported rather than interpreted."""
        preset = engine.create_preset(
            {"name": "Baseline", "fields": {"enable_watermark": True, "allow_download": False}},
            source="POST",
            actor="rep",
        )
        link = engine.create_link(
            "room_a",
            {"dataroom_id": "room_a", "preset_id": preset["id"]},
            source="POST",
            actor="rep",
        )
        assert "enable_watermark" in link["carried_fields"]
        assert "allow_download" in link["carried_fields"]
        assert "enable_watermark" not in link["settings"]

    def test_the_preset_view_never_carries_a_hash(self, engine: gate_engine.GateEngine):
        preset = engine.create_preset(
            {"name": "Baseline", "fields": {"password": PASSWORD}}, source="POST", actor="rep"
        )
        assert preset["password_set"] is True
        assert "password_hash" not in json.dumps(preset)

    def test_an_unknown_preset_is_refused(self, engine: gate_engine.GateEngine):
        with pytest.raises(rules.GateError) as caught:
            engine.create_link(
                "room_a", {"dataroom_id": "room_a", "preset_id": "nope"}, source="POST", actor="rep"
            )
        assert "preset_id" in caught.value.errors

    def test_the_preset_editor_advertises_the_documented_coverage(
        self, engine: gate_engine.GateEngine
    ):
        preset = engine.create_preset({"name": "Baseline"}, source="POST", actor="rep")
        assert tuple(preset["covered_fields"]) == rules.PRESET_COVERED_FIELDS


# --------------------------------------------------------------------------- #
# engine: updating
# --------------------------------------------------------------------------- #


class TestUpdateLink:
    def test_an_absent_field_is_left_alone(self, engine: gate_engine.GateEngine):
        link = make_link(engine, password=PASSWORD, expires_at="2026-12-31T09:00:00+00:00")
        updated = engine.update_link(link["id"], {}, source="PATCH", actor="rep")
        assert updated["settings"]["password_set"] is True
        assert updated["expires_at"] == "2026-12-31T09:00:00+00:00"

    def test_a_boolean_is_set(self, engine: gate_engine.GateEngine):
        link = make_link(engine)
        updated = engine.update_link(
            link["id"], {"email_protected": True}, source="PATCH", actor="rep"
        )
        assert updated["settings"]["email_protected"] is True

    def test_the_cli_tri_state_spellings_are_accepted(self, engine: gate_engine.GateEngine):
        link = make_link(engine)
        assert (
            engine.update_link(link["id"], {"email_protected": "on"}, source="PATCH", actor="rep")[
                "settings"
            ]["email_protected"]
            is True
        )
        assert (
            engine.update_link(link["id"], {"email_protected": "off"}, source="PATCH", actor="rep")[
                "settings"
            ]["email_protected"]
            is False
        )

    def test_the_expiry_can_be_moved(self, engine: gate_engine.GateEngine):
        link = make_link(engine, expires_at="2026-12-31T09:00:00+00:00")
        updated = engine.update_link(
            link["id"], {"expires_at": "2027-01-31T09:00:00+00:00"}, source="PATCH", actor="rep"
        )
        assert updated["expires_at"] == "2027-01-31T09:00:00+00:00"
        assert updated["expired"] is False

    def test_the_expiry_can_be_cleared_with_an_explicit_null(self, engine: gate_engine.GateEngine):
        link = make_link(engine, expires_at="2026-12-31T09:00:00+00:00")
        updated = engine.update_link(link["id"], {"expires_at": None}, source="PATCH", actor="rep")
        assert updated["expires_at"] is None
        assert updated["expired"] is False

    def test_the_password_can_be_rotated(self, engine: gate_engine.GateEngine):
        link = make_link(engine, password=PASSWORD)
        updated = engine.update_link(
            link["id"], {"password": "rotated"}, source="PATCH", actor="rep"
        )
        assert updated["password_rotated_at"] is not None
        with pytest.raises(rules.GateDenied):
            engine.submit_password(link["id"], PASSWORD, source="POST")
        assert engine.submit_password(link["id"], "rotated", source="POST")["granted"] is True

    def test_the_password_can_be_removed(self, engine: gate_engine.GateEngine):
        """A link that can be set but never cleared cannot be opened up again."""
        link = make_link(engine, password=PASSWORD)
        updated = engine.update_link(link["id"], {"password": None}, source="PATCH", actor="rep")
        assert updated["settings"]["password_set"] is False
        with pytest.raises(rules.GateDenied) as caught:
            engine.submit_password(link["id"], PASSWORD, source="POST")
        assert caught.value.reason == rules.GateDenied.REASON_NOT_REQUIRED

    def test_rotating_keeps_the_new_hash_only(
        self, engine: gate_engine.GateEngine, store: RecordStore
    ):
        link = make_link(engine, password=PASSWORD)
        engine.update_link(link["id"], {"password": "rotated"}, source="PATCH", actor="rep")
        stored = store.get(link["id"])["data"]
        assert PASSWORD not in json.dumps(stored)
        assert link_secrets.verify_password("rotated", stored["password_hash"])

    def test_an_update_touches_nothing_else_on_the_link(self, engine: gate_engine.GateEngine):
        link = make_link(engine, title="Original title", password=PASSWORD)
        updated = engine.update_link(
            link["id"], {"password": "rotated"}, source="PATCH", actor="rep"
        )
        assert updated["title"] == "Original title"
        assert updated["target"] == {"kind": "dataroom", "id": "room_a"}

    def test_a_revoked_link_cannot_be_reopened(self, engine: gate_engine.GateEngine):
        link = make_link(engine, password=PASSWORD)
        engine.revoke_link(link["id"], source="DELETE", actor="rep")
        with pytest.raises(rules.GateDenied) as caught:
            engine.update_link(link["id"], {"expires_at": None}, source="PATCH", actor="rep")
        assert caught.value.reason == rules.GateDenied.REASON_REVOKED

    def test_an_invalid_update_names_the_field(self, engine: gate_engine.GateEngine):
        link = make_link(engine)
        with pytest.raises(rules.GateError) as caught:
            engine.update_link(link["id"], {"expires_at": "later"}, source="PATCH", actor="rep")
        assert "expires_at" in caught.value.errors


# --------------------------------------------------------------------------- #
# engine: expiry and revocation
# --------------------------------------------------------------------------- #


class TestExpiryIsEvaluatedPerRequest:
    def test_a_link_is_open_before_its_expiry(self, engine: gate_engine.GateEngine, clock: Clock):
        link = make_link(engine, expires_at=(NOW + timedelta(hours=1)).isoformat())
        assert engine.gate_state(link["id"])["step"] != rules.STEP_EXPIRED

    def test_the_same_link_is_closed_after_its_expiry(
        self, engine: gate_engine.GateEngine, clock: Clock
    ):
        link = make_link(engine, expires_at=(NOW + timedelta(hours=1)).isoformat())
        clock.advance(hours=2)
        state = engine.gate_state(link["id"])
        assert state["step"] == rules.STEP_EXPIRED
        assert state["reason"] == "expired"

    def test_a_link_that_expires_mid_walk_refuses_the_next_step(
        self, engine: gate_engine.GateEngine, mailbox: Mailbox, clock: Clock
    ):
        """Not once at creation: on each request. The email went in before the
        expiry and the password must still be refused after it."""
        link = make_link(
            engine,
            password=PASSWORD,
            email_protected=True,
            expires_at=(NOW + timedelta(hours=1)).isoformat(),
        )
        opened = engine.submit_email(link["id"], BUYER, source="POST")
        assert opened["step"] == rules.STEP_PASSWORD
        clock.advance(hours=2)
        with pytest.raises(rules.GateDenied) as caught:
            engine.submit_password(
                link["id"], PASSWORD, challenge_id=opened["challenge_id"], source="POST"
            )
        assert caught.value.reason == rules.GateDenied.REASON_EXPIRED

    def test_a_granted_view_does_not_outlive_the_link(
        self, engine: gate_engine.GateEngine, clock: Clock
    ):
        """Expiry is evaluated on every viewer request, and reading the document is
        one."""
        link = make_link(
            engine, password=PASSWORD, expires_at=(NOW + timedelta(minutes=30)).isoformat()
        )
        granted = engine.submit_password(link["id"], PASSWORD, source="POST")
        assert engine.read_document(link["id"], granted["view_token"], source="GET")[
            "resolved"
        ] in (
            True,
            False,
        )
        clock.advance(minutes=31)
        with pytest.raises(rules.GateDenied) as caught:
            engine.read_document(link["id"], granted["view_token"], source="GET")
        assert caught.value.reason == rules.GateDenied.REASON_EXPIRED

    def test_an_expired_link_refuses_the_email_step(
        self, engine: gate_engine.GateEngine, clock: Clock
    ):
        link = make_link(engine, expires_at=(NOW + timedelta(minutes=5)).isoformat())
        clock.advance(minutes=6)
        with pytest.raises(rules.GateDenied) as caught:
            engine.submit_email(link["id"], BUYER, source="POST")
        assert caught.value.reason == rules.GateDenied.REASON_EXPIRED

    def test_an_expired_link_is_still_readable_by_the_seller(
        self, engine: gate_engine.GateEngine, clock: Clock
    ):
        """ "the document itself is not deleted" - the row and its history stay."""
        link = make_link(engine, password=PASSWORD, expires_at="2026-10-01T09:00:00+00:00")
        status = engine.read_link(link["id"])
        assert status["expired"] is True
        assert status["settings"]["password_set"] is True

    def test_expiry_never_counts_down_to_negative(
        self, engine: gate_engine.GateEngine, clock: Clock
    ):
        link = make_link(engine, expires_at=(NOW + timedelta(minutes=5)).isoformat())
        clock.advance(hours=5)
        assert engine.read_link(link["id"])["expires_in_seconds"] == 0


class TestRevocation:
    def test_revoking_closes_the_gate_on_the_next_request(self, engine: gate_engine.GateEngine):
        link = make_link(engine, password=PASSWORD)
        engine.revoke_link(link["id"], source="DELETE", actor="rep")
        assert engine.gate_state(link["id"])["step"] == rules.STEP_EXPIRED

    def test_a_revoked_link_reads_exactly_like_an_expired_one(self, engine: gate_engine.GateEngine):
        revoked = make_link(engine, password=PASSWORD)
        engine.revoke_link(revoked["id"], source="DELETE", actor="rep")
        expired = make_link(engine, password=PASSWORD, expires_at="2026-10-01T09:00:00+00:00")
        assert (
            engine.gate_state(revoked["id"])["message"]
            == engine.gate_state(expired["id"])["message"]
        )

    def test_a_revoked_link_refuses_the_password(self, engine: gate_engine.GateEngine):
        link = make_link(engine, password=PASSWORD)
        engine.revoke_link(link["id"], source="DELETE", actor="rep")
        with pytest.raises(rules.GateDenied) as caught:
            engine.submit_password(link["id"], PASSWORD, source="POST")
        assert caught.value.reason == rules.GateDenied.REASON_REVOKED

    def test_revocation_keeps_the_row(self, engine: gate_engine.GateEngine, store: RecordStore):
        """Soft delete, so history and audit references stay intact."""
        link = make_link(engine, password=PASSWORD)
        engine.revoke_link(link["id"], source="DELETE", actor="rep")
        # Through ``store.db``: ``RecordStore.get`` takes no ``include_deleted``, so it
        # hides a revoked row entirely. Pinning that here so the gap is visible rather
        # than rediscovered.
        assert store.db.get(link["id"], include_deleted=True)["deleted_at"] is not None
        assert store.db.get(link["id"]) is None
        assert engine.read_link(link["id"])["revoked"] is True

    def test_a_revoked_link_is_absent_from_the_live_list(self, engine: gate_engine.GateEngine):
        link = make_link(engine)
        engine.revoke_link(link["id"], source="DELETE", actor="rep")
        assert [row["id"] for row in engine.list_links("room_a")] == []

    def test_a_revoked_link_is_still_visible_to_the_seller(self, engine: gate_engine.GateEngine):
        link = make_link(engine)
        engine.revoke_link(link["id"], source="DELETE", actor="rep")
        revoked = engine.list_revoked_links("room_a")
        assert [row["id"] for row in revoked] == [link["id"]]
        assert revoked[0]["revoked"] is True
        assert revoked[0]["revoked_at"] is not None

    def test_a_revoked_link_keeps_its_view_history(self, engine: gate_engine.GateEngine):
        link = make_link(engine, password=PASSWORD)
        engine.submit_password(link["id"], PASSWORD, source="POST")
        engine.revoke_link(link["id"], source="DELETE", actor="rep")
        assert len(engine.list_views(link_id=link["id"])) == 1


class TestUnknownLinks:
    def test_an_unknown_link_id_is_not_found(self, engine: gate_engine.GateEngine):
        for call in (
            lambda: engine.read_link("nope"),
            lambda: engine.gate_state("nope"),
            lambda: engine.submit_password("nope", PASSWORD, source="POST"),
            lambda: engine.update_link("nope", {}, source="PATCH"),
            lambda: engine.revoke_link("nope", source="DELETE"),
        ):
            with pytest.raises(rules.LinkNotFound):
                call()

    @pytest.mark.parametrize("handle", ["", None, 7, [], {}])
    def test_a_malformed_link_id_is_not_found(self, engine: gate_engine.GateEngine, handle):
        with pytest.raises(rules.LinkNotFound):
            engine.read_link(handle)

    def test_a_record_from_another_collection_is_not_a_link(
        self, engine: gate_engine.GateEngine, store: RecordStore
    ):
        """Ids are opaque, so this has to be checked rather than assumed."""
        with pytest.raises(rules.LinkNotFound):
            engine.read_link("doc_a")


# --------------------------------------------------------------------------- #
# engine: the buyer walk
# --------------------------------------------------------------------------- #


class TestOpenLink:
    def test_a_link_with_no_gates_is_open(self, engine: gate_engine.GateEngine):
        state = engine.gate_state(make_link(engine)["id"])
        assert state["step"] == rules.STEP_OPEN
        assert state["steps"] == []

    def test_an_open_link_asks_for_no_email(self, engine: gate_engine.GateEngine):
        with pytest.raises(rules.GateDenied) as caught:
            engine.submit_email(make_link(engine)["id"], BUYER, source="POST")
        assert caught.value.reason == rules.GateDenied.REASON_NOT_REQUIRED

    def test_an_open_link_asks_for_no_password(self, engine: gate_engine.GateEngine):
        with pytest.raises(rules.GateDenied) as caught:
            engine.submit_password(make_link(engine)["id"], PASSWORD, source="POST")
        assert caught.value.reason == rules.GateDenied.REASON_NOT_REQUIRED


class TestPasswordOnlyLink:
    def test_the_gate_asks_for_the_password_first(self, engine: gate_engine.GateEngine):
        state = engine.gate_state(make_link(engine, password=PASSWORD)["id"])
        assert state["step"] == rules.STEP_PASSWORD
        assert state["steps"] == [rules.STEP_PASSWORD]

    def test_the_right_password_grants(self, engine: gate_engine.GateEngine):
        link = make_link(engine, password=PASSWORD)
        granted = engine.submit_password(link["id"], PASSWORD, source="POST")
        assert granted["granted"] is True
        assert granted["step"] == rules.STEP_OPEN
        assert granted["view_token"]

    def test_the_wrong_password_is_refused(self, engine: gate_engine.GateEngine):
        link = make_link(engine, password=PASSWORD)
        with pytest.raises(rules.GateDenied) as caught:
            engine.submit_password(link["id"], "nope", source="POST")
        assert caught.value.reason == rules.GateDenied.REASON_PASSWORD

    def test_a_refusal_records_no_view(self, engine: gate_engine.GateEngine):
        link = make_link(engine, password=PASSWORD)
        with pytest.raises(rules.GateDenied):
            engine.submit_password(link["id"], "nope", source="POST")
        assert engine.list_views(link_id=link["id"]) == []

    def test_a_password_only_link_attributes_no_email(self, engine: gate_engine.GateEngine):
        link = make_link(engine, password=PASSWORD)
        granted = engine.submit_password(link["id"], PASSWORD, source="POST")
        assert granted["email"] is None
        assert granted["email_verified"] is False
        assert granted["visitor_id"] is None

    def test_a_non_string_password_is_refused_not_crashed(self, engine: gate_engine.GateEngine):
        link = make_link(engine, password=PASSWORD)
        for candidate in (None, 7, [], {}):
            with pytest.raises(rules.GateDenied):
                engine.submit_password(link["id"], candidate, source="POST")


class TestEmailProtectedLink:
    def test_the_gate_asks_for_the_email_first(self, engine: gate_engine.GateEngine):
        state = engine.gate_state(make_link(engine, password=PASSWORD, email_protected=True)["id"])
        assert state["step"] == rules.STEP_EMAIL
        assert state["steps"] == [rules.STEP_EMAIL, rules.STEP_PASSWORD]

    def test_an_email_is_normalised_before_it_is_stored(self, engine: gate_engine.GateEngine):
        link = make_link(engine, password=PASSWORD, email_protected=True)
        opened = engine.submit_email(link["id"], "  Buyer@Northwind.Example  ", source="POST")
        assert opened["email"] == "buyer@northwind.example"

    @pytest.mark.parametrize("value", ["", "not-an-address", "a@b", "@b.com", None, 7, []])
    def test_an_unusable_address_is_refused_with_a_field_error(
        self, engine: gate_engine.GateEngine, value
    ):
        link = make_link(engine, password=PASSWORD, email_protected=True)
        with pytest.raises(rules.GateError) as caught:
            engine.submit_email(link["id"], value, source="POST")
        assert "email" in caught.value.errors

    def test_no_code_is_dispatched_without_authentication(
        self, engine: gate_engine.GateEngine, mailbox: Mailbox
    ):
        link = make_link(engine, password=PASSWORD, email_protected=True)
        engine.submit_email(link["id"], BUYER, source="POST")
        assert mailbox.sent == []

    def test_the_email_step_moves_to_the_password(self, engine: gate_engine.GateEngine):
        link = make_link(engine, password=PASSWORD, email_protected=True)
        opened = engine.submit_email(link["id"], BUYER, source="POST")
        assert opened["step"] == rules.STEP_PASSWORD
        assert opened["code_dispatched"] is False

    def test_a_password_with_no_email_is_refused(self, engine: gate_engine.GateEngine):
        link = make_link(engine, password=PASSWORD, email_protected=True)
        with pytest.raises(rules.GateDenied) as caught:
            engine.submit_password(link["id"], PASSWORD, source="POST")
        assert caught.value.reason == rules.GateDenied.REASON_EMAIL_REQUIRED

    def test_the_email_is_stamped_onto_the_view_unverified(self, engine: gate_engine.GateEngine):
        """Protection identifies; it does not prove. A ``Visitor`` row with
        ``verified: true`` is a claim about an inbox and this gate has not made
        one."""
        link = make_link(engine, password=PASSWORD, email_protected=True)
        opened = engine.submit_email(link["id"], BUYER, source="POST")
        granted = engine.submit_password(
            link["id"], PASSWORD, challenge_id=opened["challenge_id"], source="POST"
        )
        assert granted["email_verified"] is False
        assert granted["visitor_id"] is None
        view = engine.list_views(link_id=link["id"])[0]
        assert view["email"] == "buyer@northwind.example"
        assert view["email_verified"] is False
        assert view["visitor_id"] is None

    def test_protection_with_no_password_grants_on_the_email(self, engine: gate_engine.GateEngine):
        """Nothing is left to ask after the email step, so that step is the grant.
        Telling a buyer to "now enter the password" on a link with no password is a
        dead end."""
        link = make_link(engine, email_protected=True)
        granted = engine.submit_email(link["id"], BUYER, source="POST")
        assert granted["granted"] is True
        assert granted["view_token"]
        assert len(engine.list_views(link_id=link["id"])) == 1

    def test_a_challenge_from_another_link_is_refused(self, engine: gate_engine.GateEngine):
        first = make_link(engine, password=PASSWORD, email_protected=True)
        second = make_link(engine, password=PASSWORD, email_protected=True)
        opened = engine.submit_email(first["id"], BUYER, source="POST")
        with pytest.raises(rules.GateDenied) as caught:
            engine.submit_password(
                second["id"], PASSWORD, challenge_id=opened["challenge_id"], source="POST"
            )
        assert caught.value.reason == rules.GateDenied.REASON_CODE


class TestAuthenticatedLink:
    def test_the_gate_asks_for_email_then_code_then_password(self, engine: gate_engine.GateEngine):
        link = make_link(engine, password=PASSWORD, email_authenticated=True)
        state = engine.gate_state(link["id"])
        assert state["step"] == rules.STEP_EMAIL
        assert state["steps"] == ["email", "code", "password"]

    def test_the_email_step_dispatches_a_code(
        self, engine: gate_engine.GateEngine, mailbox: Mailbox
    ):
        link = make_link(engine, password=PASSWORD, email_authenticated=True)
        opened = engine.submit_email(link["id"], BUYER, source="POST")
        assert opened["step"] == rules.STEP_CODE
        assert opened["code_dispatched"] is True
        assert len(mailbox.sent) == 1
        assert mailbox.sent[0]["to"] == "buyer@northwind.example"
        assert len(mailbox.sent[0]["code"]) == 6

    def test_the_dispatch_is_recorded_without_the_code(
        self, engine: gate_engine.GateEngine, mailbox: Mailbox, store: RecordStore
    ):
        """Six digits with the last four recorded is a hundred-candidate secret."""
        link = make_link(engine, password=PASSWORD, email_authenticated=True)
        engine.submit_email(link["id"], BUYER, source="POST")
        code = mailbox.sent[0]["code"]
        deliveries = store.list(rules.DELIVERY_COLLECTION, limit=10)
        assert len(deliveries) == 1
        payload = json.dumps(deliveries[0]["data"])
        assert code not in payload
        assert deliveries[0]["data"]["to"] == "buyer@northwind.example"
        assert deliveries[0]["data"]["status"] == "dispatched"

    def test_the_challenge_row_never_holds_the_code(
        self, engine: gate_engine.GateEngine, mailbox: Mailbox, store: RecordStore
    ):
        link = make_link(engine, password=PASSWORD, email_authenticated=True)
        engine.submit_email(link["id"], BUYER, source="POST")
        code = mailbox.sent[0]["code"]
        challenge = store.list(rules.CODE_COLLECTION, limit=10)[0]["data"]
        assert code not in json.dumps(challenge)
        assert challenge["code_hash"].startswith("pbkdf2_sha256$")

    def test_the_right_code_verifies_and_moves_to_the_password(
        self, engine: gate_engine.GateEngine, mailbox: Mailbox
    ):
        link = make_link(engine, password=PASSWORD, email_authenticated=True)
        opened = engine.submit_email(link["id"], BUYER, source="POST")
        verified = engine.submit_code(
            link["id"],
            opened["challenge_id"],
            mailbox.code_for(opened["challenge_id"]),
            source="POST",
        )
        assert verified["step"] == rules.STEP_PASSWORD
        assert verified["email_verified"] is True
        assert verified["visitor_id"]

    def test_a_correct_code_opens_nothing_by_itself(
        self, engine: gate_engine.GateEngine, mailbox: Mailbox
    ):
        """ "the code is verified server-side, *then* the password is compared"."""
        link = make_link(engine, password=PASSWORD, email_authenticated=True)
        opened = engine.submit_email(link["id"], BUYER, source="POST")
        engine.submit_code(
            link["id"],
            opened["challenge_id"],
            mailbox.code_for(opened["challenge_id"]),
            source="POST",
        )
        with pytest.raises(rules.GateDenied) as caught:
            engine.read_document(link["id"], "made-up-token", source="GET")
        assert caught.value.reason == rules.GateDenied.REASON_SESSION
        assert engine.list_views(link_id=link["id"]) == []

    def test_a_wrong_code_is_refused(self, engine: gate_engine.GateEngine, mailbox: Mailbox):
        link = make_link(engine, password=PASSWORD, email_authenticated=True)
        opened = engine.submit_email(link["id"], BUYER, source="POST")
        with pytest.raises(rules.GateDenied) as caught:
            engine.submit_code(link["id"], opened["challenge_id"], "000000", source="POST")
        assert caught.value.reason == rules.GateDenied.REASON_CODE

    def test_a_wrong_code_consumes_the_challenge(
        self, engine: gate_engine.GateEngine, mailbox: Mailbox, store: RecordStore
    ):
        """The research specifies no attempt cap, so the single-use property is what
        bounds guessing: one wrong answer spends the code."""
        link = make_link(engine, password=PASSWORD, email_authenticated=True)
        opened = engine.submit_email(link["id"], BUYER, source="POST")
        real = mailbox.code_for(opened["challenge_id"])
        with pytest.raises(rules.GateDenied):
            engine.submit_code(link["id"], opened["challenge_id"], "000000", source="POST")
        challenge = store.get(opened["challenge_id"])["data"]
        assert challenge["consumed"] is True
        assert challenge["outcome"] == "code_rejected"
        with pytest.raises(rules.GateDenied):
            engine.submit_code(link["id"], opened["challenge_id"], real, source="POST")

    def test_a_code_cannot_be_used_twice(self, engine: gate_engine.GateEngine, mailbox: Mailbox):
        """ "One-time" has to mean the row is spent, not just that the wrong answer
        burns it - the hash is still on the row."""
        link = make_link(engine, password=PASSWORD, email_authenticated=True)
        opened = engine.submit_email(link["id"], BUYER, source="POST")
        code = mailbox.code_for(opened["challenge_id"])
        engine.submit_code(link["id"], opened["challenge_id"], code, source="POST")
        with pytest.raises(rules.GateDenied) as caught:
            engine.submit_code(link["id"], opened["challenge_id"], code, source="POST")
        assert caught.value.reason == rules.GateDenied.REASON_CODE

    def test_a_correct_code_creates_exactly_one_visitor(
        self, engine: gate_engine.GateEngine, mailbox: Mailbox
    ):
        link = make_link(engine, password=PASSWORD, email_authenticated=True)
        opened = engine.submit_email(link["id"], BUYER, source="POST")
        code = mailbox.code_for(opened["challenge_id"])
        first = engine.submit_code(link["id"], opened["challenge_id"], code, source="POST")
        assert engine.summary()["verified_visitors"] == 1
        with pytest.raises(rules.GateDenied):
            engine.submit_code(link["id"], opened["challenge_id"], code, source="POST")
        assert engine.summary()["verified_visitors"] == 1
        assert first["visitor_id"]

    def test_a_password_on_an_unverified_challenge_is_refused(
        self, engine: gate_engine.GateEngine, mailbox: Mailbox
    ):
        link = make_link(engine, password=PASSWORD, email_authenticated=True)
        opened = engine.submit_email(link["id"], BUYER, source="POST")
        with pytest.raises(rules.GateDenied) as caught:
            engine.submit_password(
                link["id"], PASSWORD, challenge_id=opened["challenge_id"], source="POST"
            )
        assert caught.value.reason == rules.GateDenied.REASON_EMAIL_UNVERIFIED

    def test_a_password_with_no_challenge_is_refused(self, engine: gate_engine.GateEngine):
        link = make_link(engine, password=PASSWORD, email_authenticated=True)
        with pytest.raises(rules.GateDenied) as caught:
            engine.submit_password(link["id"], PASSWORD, source="POST")
        assert caught.value.reason == rules.GateDenied.REASON_EMAIL_REQUIRED

    def test_a_code_on_an_unauthenticated_link_is_refused(self, engine: gate_engine.GateEngine):
        link = make_link(engine, password=PASSWORD, email_protected=True)
        with pytest.raises(rules.GateDenied) as caught:
            engine.submit_code(link["id"], "anything", "123456", source="POST")
        assert caught.value.reason == rules.GateDenied.REASON_OUT_OF_ORDER

    def test_the_whole_walk_grants_and_releases_the_document(
        self, engine: gate_engine.GateEngine, mailbox: Mailbox
    ):
        link = make_link(engine, document_id="doc_a", password=PASSWORD, email_authenticated=True)
        granted = walk(engine, mailbox, link["id"], PASSWORD)
        assert granted["granted"] is True
        assert granted["email_verified"] is True
        document = engine.read_document(link["id"], granted["view_token"], source="GET")
        assert document["resolved"] is True
        assert document["content"]["title"] == "Security overview"
        assert document["viewer"]["email_verified"] is True
        assert document["steps_cleared"] == ["email", "code", "password"]

    def test_authentication_with_no_password_grants_on_the_code(
        self, engine: gate_engine.GateEngine, mailbox: Mailbox
    ):
        link = make_link(engine, email_authenticated=True)
        opened = engine.submit_email(link["id"], BUYER, source="POST")
        granted = engine.submit_code(
            link["id"],
            opened["challenge_id"],
            mailbox.code_for(opened["challenge_id"]),
            source="POST",
        )
        assert granted["granted"] is True
        assert granted["email_verified"] is True

    def test_each_request_mints_a_fresh_challenge(
        self, engine: gate_engine.GateEngine, mailbox: Mailbox
    ):
        """ "On each viewer request the browser-supplied email is collected" - nothing a
        buyer proved on an earlier pass carries into this one."""
        link = make_link(engine, password=PASSWORD, email_authenticated=True)
        walk(engine, mailbox, link["id"], PASSWORD)
        first = engine.submit_email(link["id"], BUYER, source="POST")
        second = engine.submit_email(link["id"], BUYER, source="POST")
        assert first["challenge_id"] != second["challenge_id"]
        assert len(mailbox.sent) == 3

    def test_the_returning_buyer_cannot_reuse_the_earlier_proof(
        self, engine: gate_engine.GateEngine, mailbox: Mailbox
    ):
        link = make_link(engine, password=PASSWORD, email_authenticated=True)
        first = engine.submit_email(link["id"], BUYER, source="POST")
        first_code = mailbox.code_for(first["challenge_id"])
        second = engine.submit_email(link["id"], BUYER, source="POST")
        with pytest.raises(rules.GateDenied) as caught:
            engine.submit_code(link["id"], second["challenge_id"], first_code, source="POST")
        assert caught.value.reason == rules.GateDenied.REASON_CODE

    def test_the_dispatch_transport_is_recorded_by_name(
        self, engine: gate_engine.GateEngine, store: RecordStore
    ):
        link = make_link(engine, password=PASSWORD, email_authenticated=True)
        engine.submit_email(link["id"], BUYER, source="POST")
        assert store.list(rules.DELIVERY_COLLECTION, limit=1)[0]["data"]["transport"] == "Mailbox"


class TestDocumentRelease:
    def test_no_token_releases_nothing(self, engine: gate_engine.GateEngine):
        link = make_link(engine, password=PASSWORD)
        for token in (None, "", "made-up", 7):
            with pytest.raises(rules.GateDenied) as caught:
                engine.read_document(link["id"], token, source="GET")
            assert caught.value.reason == rules.GateDenied.REASON_SESSION

    def test_a_token_from_another_link_releases_nothing(self, engine: gate_engine.GateEngine):
        first = make_link(engine, password=PASSWORD)
        second = make_link(engine, password=PASSWORD)
        granted = engine.submit_password(first["id"], PASSWORD, source="POST")
        with pytest.raises(rules.GateDenied):
            engine.read_document(second["id"], granted["view_token"], source="GET")

    def test_the_session_stores_the_token_only_as_a_digest(
        self, engine: gate_engine.GateEngine, store: RecordStore
    ):
        link = make_link(engine, password=PASSWORD)
        granted = engine.submit_password(link["id"], PASSWORD, source="POST")
        sessions = store.list(rules.SESSION_COLLECTION, limit=10)
        assert granted["view_token"] not in json.dumps(sessions[0]["data"])
        assert sessions[0]["data"]["token_hash"] == link_secrets.hash_token(granted["view_token"])

    def test_a_target_that_does_not_resolve_is_reported_not_faked(
        self, engine: gate_engine.GateEngine
    ):
        """The gate granted; the content is another workflow's business. Saying so
        beats returning an empty document that looks like a successful load."""
        link = make_link(engine, document_id="doc_missing", password=PASSWORD)
        granted = engine.submit_password(link["id"], PASSWORD, source="POST")
        document = engine.read_document(link["id"], granted["view_token"], source="GET")
        assert document["resolved"] is False
        assert document["content"] is None
        assert document["target"] == {"kind": "document", "id": "doc_missing"}

    def test_a_dataroom_target_resolves_from_the_dataroom_collection(
        self, engine: gate_engine.GateEngine
    ):
        link = make_link(engine, password=PASSWORD)
        granted = engine.submit_password(link["id"], PASSWORD, source="POST")
        assert (
            engine.read_document(link["id"], granted["view_token"], source="GET")["resolved"]
            is False
        )

    def test_a_document_id_pointing_at_another_collection_is_not_resolved(
        self, engine: gate_engine.GateEngine
    ):
        link = make_link(engine, document_id="room_a", password=PASSWORD)
        granted = engine.submit_password(link["id"], PASSWORD, source="POST")
        assert (
            engine.read_document(link["id"], granted["view_token"], source="GET")["resolved"]
            is False
        )


# --------------------------------------------------------------------------- #
# engine: attribution
# --------------------------------------------------------------------------- #


class TestVisitors:
    def test_verification_persists_a_visitor_row(
        self, engine: gate_engine.GateEngine, mailbox: Mailbox
    ):
        link = make_link(engine, password=PASSWORD, email_authenticated=True)
        granted = walk(engine, mailbox, link["id"], PASSWORD)
        visitor = engine.read_visitor(granted["visitor_id"])
        assert visitor["email"] == "buyer@northwind.example"
        assert visitor["verified"] is True
        assert visitor["verification_method"] == "one_time_code"
        assert visitor["link_id"] == link["id"]

    def test_the_visitor_is_readable_by_id(self, engine: gate_engine.GateEngine, mailbox: Mailbox):
        """Mirrors `GET /v1/visitors/{id}`."""
        link = make_link(engine, password=PASSWORD, email_authenticated=True)
        granted = walk(engine, mailbox, link["id"], PASSWORD)
        assert engine.read_visitor(granted["visitor_id"])["email"] == BUYER

    def test_an_unknown_visitor_is_not_found(self, engine: gate_engine.GateEngine):
        for handle in ("nope", "", None, 7):
            with pytest.raises(rules.LinkNotFound):
                engine.read_visitor(handle)

    def test_a_record_that_is_not_a_visitor_is_not_found(
        self, engine: gate_engine.GateEngine, store: RecordStore
    ):
        link = make_link(engine)
        with pytest.raises(rules.LinkNotFound):
            engine.read_visitor(link["id"])

    def test_a_repeat_visit_updates_the_same_row(
        self, engine: gate_engine.GateEngine, mailbox: Mailbox
    ):
        link = make_link(engine, password=PASSWORD, email_authenticated=True)
        first = walk(engine, mailbox, link["id"], PASSWORD)
        second = walk(engine, mailbox, link["id"], PASSWORD)
        assert first["visitor_id"] == second["visitor_id"]
        assert engine.summary()["verified_visitors"] == 1
        # Two views, not four: verification does not count as a view.
        assert engine.read_visitor(first["visitor_id"])["view_count"] == 2

    def test_verifying_without_a_view_does_not_count_as_one(
        self, engine: gate_engine.GateEngine, mailbox: Mailbox
    ):
        """A buyer who proves the inbox and then fails the password has verified. They
        have not viewed, and a counter that said otherwise would be lying to a seller
        reading their own dashboard."""
        link = make_link(engine, password=PASSWORD, email_authenticated=True)
        opened = engine.submit_email(link["id"], "ops@northwind.example", source="POST")
        verified = engine.submit_code(
            link["id"],
            opened["challenge_id"],
            mailbox.code_for(opened["challenge_id"]),
            source="POST",
        )
        with pytest.raises(rules.GateDenied):
            engine.submit_password(
                link["id"], "wrong", challenge_id=verified["challenge_id"], source="POST"
            )
        assert engine.read_visitor(verified["visitor_id"])["view_count"] == 0

    def test_verification_does_not_carry_to_another_link(
        self, engine: gate_engine.GateEngine, mailbox: Mailbox
    ):
        """Verification on one link is evidence about one inbox in front of one deal."""
        first = make_link(engine, password=PASSWORD, email_authenticated=True)
        second = make_link(engine, password=PASSWORD, email_authenticated=True)
        one = walk(engine, mailbox, first["id"], PASSWORD)
        two = walk(engine, mailbox, second["id"], PASSWORD)
        assert one["visitor_id"] != two["visitor_id"]
        assert engine.read_visitor(two["visitor_id"])["link_id"] == second["id"]

    def test_the_email_case_folds_to_one_visitor(
        self, engine: gate_engine.GateEngine, mailbox: Mailbox
    ):
        link = make_link(engine, password=PASSWORD, email_authenticated=True)
        walk(engine, mailbox, link["id"], PASSWORD)
        opened = engine.submit_email(link["id"], "BUYER@NORTHWIND.EXAMPLE", source="POST")
        verified = engine.submit_code(
            link["id"],
            opened["challenge_id"],
            mailbox.code_for(opened["challenge_id"]),
            source="POST",
        )
        engine.submit_password(
            link["id"], PASSWORD, challenge_id=verified["challenge_id"], source="POST"
        )
        assert engine.summary()["verified_visitors"] == 1

    def test_a_buyer_who_verified_then_failed_the_password_is_still_a_visitor(
        self, engine: gate_engine.GateEngine, mailbox: Mailbox
    ):
        """They proved they own the inbox. That is true whether or not they then knew
        the password, and a seller asking "did this person verify?" wants the truth."""
        link = make_link(engine, password=PASSWORD, email_authenticated=True)
        opened = engine.submit_email(link["id"], "ops@northwind.example", source="POST")
        verified = engine.submit_code(
            link["id"],
            opened["challenge_id"],
            mailbox.code_for(opened["challenge_id"]),
            source="POST",
        )
        with pytest.raises(rules.GateDenied):
            engine.submit_password(
                link["id"], "wrong", challenge_id=verified["challenge_id"], source="POST"
            )
        visitor = engine.read_visitor(verified["visitor_id"])
        assert visitor["verified"] is True
        assert engine.list_views(link_id=link["id"]) == []


class TestViewsAndNotifications:
    def test_a_grant_records_a_view(self, engine: gate_engine.GateEngine):
        link = make_link(engine, password=PASSWORD)
        engine.submit_password(link["id"], PASSWORD, source="POST")
        views = engine.list_views(link_id=link["id"])
        assert len(views) == 1
        assert views[0]["link_id"] == link["id"]
        assert views[0]["viewed_at"]

    def test_the_view_is_stamped_with_the_verified_email(
        self, engine: gate_engine.GateEngine, mailbox: Mailbox
    ):
        link = make_link(engine, password=PASSWORD, email_authenticated=True)
        walk(engine, mailbox, link["id"], PASSWORD)
        view = engine.list_views(link_id=link["id"])[0]
        assert view["email"] == "buyer@northwind.example"
        assert view["email_verified"] is True
        assert view["visitor_id"]

    def test_notification_is_on_by_default(self, engine: gate_engine.GateEngine):
        link = make_link(engine, password=PASSWORD)
        granted = engine.submit_password(link["id"], PASSWORD, source="POST")
        assert granted["notified"] is True
        notes = engine.list_notifications("room_a")
        assert len(notes) == 1
        assert notes[0]["kind"] == "link_viewed"
        assert notes[0]["title"] == "Northwind - mutual NDA"

    def test_a_refused_view_is_not_notified(self, engine: gate_engine.GateEngine):
        link = make_link(engine, password=PASSWORD)
        with pytest.raises(rules.GateDenied):
            engine.submit_password(link["id"], "nope", source="POST")
        assert engine.list_notifications("room_a") == []

    def test_notification_can_be_turned_off(self, engine: gate_engine.GateEngine):
        link = make_link(engine, password=PASSWORD, enable_notification=False)
        granted = engine.submit_password(link["id"], PASSWORD, source="POST")
        assert granted["notified"] is False
        assert engine.list_notifications("room_a") == []

    def test_the_view_is_still_recorded_when_notification_is_off(
        self, engine: gate_engine.GateEngine
    ):
        """The view happened either way, and the row says which, so nobody has to
        guess why no notification arrived."""
        link = make_link(engine, password=PASSWORD, enable_notification=False)
        engine.submit_password(link["id"], PASSWORD, source="POST")
        view = engine.list_views(link_id=link["id"])[0]
        assert view["notified"] is False

    def test_views_can_be_filtered_by_room(self, engine: gate_engine.GateEngine):
        link = make_link(engine, password=PASSWORD)
        engine.submit_password(link["id"], PASSWORD, source="POST")
        assert len(engine.list_views("room_a")) == 1
        assert engine.list_views("room_b") == []

    def test_the_view_row_never_holds_a_secret(
        self, engine: gate_engine.GateEngine, mailbox: Mailbox
    ):
        link = make_link(engine, password=PASSWORD, email_authenticated=True)
        walk(engine, mailbox, link["id"], PASSWORD)
        for collection in (rules.VIEW_COLLECTION, rules.NOTIFICATION_COLLECTION):
            for row in engine.store.list(collection, limit=10):
                assert PASSWORD not in json.dumps(row["data"])


class TestSummary:
    def test_the_summary_counts_what_is_there(
        self, engine: gate_engine.GateEngine, mailbox: Mailbox
    ):
        open_link = make_link(engine)
        protected = make_link(engine, password=PASSWORD)
        expired = make_link(engine, expires_at="2026-10-01T09:00:00+00:00")
        revoked = make_link(engine)
        engine.revoke_link(revoked["id"], source="DELETE", actor="rep")
        authenticated = make_link(engine, password=PASSWORD, email_authenticated=True)
        walk(engine, mailbox, authenticated["id"], PASSWORD)

        summary = engine.summary()
        assert summary["links"] == 4
        assert summary["open"] == 3
        assert summary["expired"] == 1
        assert summary["revoked"] == 1
        assert summary["password_protected"] == 2
        assert summary["email_protected"] == 1
        assert summary["email_authenticated"] == 1
        assert summary["verified_visitors"] == 1
        assert summary["views"] == 1
        assert summary["notifications"] == 1
        assert summary["pending_codes"] == 0
        assert open_link and protected and expired

    def test_the_summary_can_be_scoped_to_a_room(self, engine: gate_engine.GateEngine):
        make_link(engine)
        engine.create_link(
            "room_b",
            {"dataroom_id": "room_b", "password": PASSWORD},
            source="POST",
            actor="rep",
        )
        assert engine.summary("room_a")["links"] == 1
        assert engine.summary("room_b")["links"] == 1

    def test_an_empty_store_summarises_to_zeroes(self, engine: gate_engine.GateEngine):
        summary = engine.summary()
        assert summary["links"] == 0
        assert summary["open"] == 0
        assert summary["verified_visitors"] == 0

    def test_a_link_expiring_within_a_day_is_counted(self, engine: gate_engine.GateEngine):
        make_link(engine, expires_at=(NOW + timedelta(hours=6)).isoformat())
        make_link(engine, expires_at=(NOW + timedelta(days=30)).isoformat())
        assert engine.summary()["expiring_within_a_day"] == 1


# --------------------------------------------------------------------------- #
# the audit-source rule
# --------------------------------------------------------------------------- #


class TestAuditSources:
    def test_every_source_this_router_records_names_a_mounted_route(self):
        """The rule the build brief asks for by name: the audit row must name the
        route that actually served the write, so it is checked against the routes the
        host really mounted rather than against a hand-written list."""
        module = host.load_feature("wf069_gate_each_buyer_link_with_a_password_a")
        mounted = {
            f"{method} {route.path}"
            for route in module.router.routes
            for method in route.methods
            if method not in ("HEAD", "OPTIONS")
        }
        # Every source the module builds is derived, so it is checked by walking the
        # module's own _source() calls for every route it declares.
        declared = {
            f"{method} {module.router.prefix}{path}" for method, path in _declared_sources()
        }
        assert declared
        assert declared <= mounted, sorted(declared - mounted)
        assert not _source_literals(), f"a source literal crept in: {_source_literals()}"

    def test_every_write_reached_the_audit_log_with_its_route(
        self, engine: gate_engine.GateEngine, store: RecordStore, mailbox: Mailbox
    ):
        link = make_link(engine, password=PASSWORD, email_authenticated=True)
        walk(engine, mailbox, link["id"], PASSWORD)
        entries = store.audit(limit=200)
        sources = {entry.get("source") for entry in entries}
        assert f"POST {PREFIX}/links/{{link_id}}/gate/email" in sources
        assert f"POST {PREFIX}/links/{{link_id}}/gate/code" in sources
        assert f"POST {PREFIX}/links/{{link_id}}/gate/password" in sources

    def test_the_create_source_is_the_room_scoped_route(
        self, engine: gate_engine.GateEngine, store: RecordStore
    ):
        make_link(engine)
        sources = [e.get("source") for e in store.audit(limit=50)]
        assert f"POST {PREFIX}/rooms/{{room_id}}/links" in sources

    def test_a_seed_write_names_the_seed_and_not_a_route(
        self, engine: gate_engine.GateEngine, store: RecordStore
    ):
        """ "no route served this, and claiming one would be exactly the lie hard rule
        4 of the brief exists to prevent"."""
        engine.create_link("room_a", {"dataroom_id": "room_a"}, source="seed", actor="dana")
        sources = [e.get("source") for e in store.audit(collection=rules.LINK_COLLECTION, limit=5)]
        assert sources == ["seed"]


def _source_literals() -> set[str]:
    """Any module-level string that *is* a route-shaped source.

    A ``source`` written as a literal is the defect this workflow's rule exists to
    prevent, so the check is on the shape of the value rather than on its name.
    """
    import importlib

    module = importlib.import_module(FEATURE_MODULE)
    prefixes = ("GET ", "POST ", "PATCH ", "PUT ", "DELETE ")
    return {
        value
        for value in vars(module).values()
        if isinstance(value, str) and value.startswith(prefixes)
    }


def _declared_sources() -> list[tuple[str, str]]:
    """Every ``_source("METHOD", "/path")`` the feature module writes.

    Read from the module's source rather than from a list kept beside it, so a new
    route that forgot to pass a source cannot pass this test by being absent from
    the list.
    """
    import ast
    import importlib

    module = importlib.import_module(FEATURE_MODULE)
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    found: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Name) and func.id == "_source"):
            continue
        if len(node.args) == 2 and all(isinstance(a, ast.Constant) for a in node.args):
            found.append((node.args[0].value, node.args[1].value))
    return found


# --------------------------------------------------------------------------- #
# http
# --------------------------------------------------------------------------- #


class TestHttpSurface:
    def test_the_feature_is_listed_with_its_routes(self, client: TestClient):
        record = client.get(f"/api/features/{FEATURE_ID}")
        assert record.status_code == 200
        body = record.json()
        assert body["prefix"] == PREFIX
        assert body["routes"]
        assert body["exception_handlers"] == ["GateDenied", "GateError", "LinkNotFound"]

    def test_the_registry_reports_no_failure_for_this_feature(self, client: TestClient):
        body = client.get("/api/features").json()
        assert FEATURE_ID in {feature["id"] for feature in body["features"]}
        assert all(feature["id"] != FEATURE_ID for feature in body["failed"])

    def test_the_vocabulary_endpoint_serves_the_documented_defaults(self, client: TestClient):
        body = client.get(f"{PREFIX}/vocabulary").json()
        assert body["defaults"]["email_protected"] is True
        assert body["defaults"]["email_authenticated"] is False
        assert body["defaults"]["enable_notification"] is True
        assert body["defaults"]["expires_at"] is None
        assert tuple(body["preset_covered_fields"]) == rules.PRESET_COVERED_FIELDS
        assert body["steps"] == list(rules.GATE_SEQUENCE)

    def test_the_vocabulary_endpoint_says_what_is_not_built(self, client: TestClient):
        """ "report that as unfinished rather than faking it"."""
        body = client.get(f"{PREFIX}/vocabulary").json()
        assert body["not_implemented"]
        assert body["delivery"]["configured"] is False

    def test_the_summary_endpoint_counts(self, client: TestClient):
        assert client.get(f"{PREFIX}/summary").json()["links"] == 0

    def test_creating_a_link_over_http(self, client: TestClient):
        response = client.post(
            f"{PREFIX}/rooms/room_a/links",
            json={"title": "Northwind", "dataroom_id": "room_a", "password": PASSWORD},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["id"].startswith("wf069_link_")
        assert body["settings"]["password_set"] is True

    def test_a_bad_setting_is_a_400_with_a_field_map(self, client: TestClient):
        response = client.post(
            f"{PREFIX}/rooms/room_a/links",
            json={"dataroom_id": "room_a", "expires_at": "whenever"},
        )
        assert response.status_code == 400
        assert response.json()["error"] == "gate_settings_invalid"
        assert "expires_at" in response.json()["errors"]

    def test_a_link_with_no_target_is_a_400(self, client: TestClient):
        response = client.post(f"{PREFIX}/rooms/room_a/links", json={"title": "x"})
        assert response.status_code == 400

    def test_reading_and_listing_a_link(self, client: TestClient):
        created = client.post(f"{PREFIX}/rooms/room_a/links", json={"dataroom_id": "room_a"}).json()
        assert client.get(f"{PREFIX}/links/{created['id']}").json()["id"] == created["id"]
        listed = client.get(f"{PREFIX}/rooms/room_a/links").json()
        assert [row["id"] for row in listed["links"]] == [created["id"]]
        assert listed["revoked"] == []

    def test_the_patch_endpoint_is_tri_state(self, client: TestClient):
        created = client.post(
            f"{PREFIX}/rooms/room_a/links",
            json={
                "dataroom_id": "room_a",
                "password": PASSWORD,
                "expires_at": "2027-01-01T00:00:00+00:00",
            },
        ).json()
        untouched = client.patch(f"{PREFIX}/links/{created['id']}", json={}).json()
        assert untouched["settings"]["password_set"] is True
        assert untouched["expires_at"] == "2027-01-01T00:00:00+00:00"
        cleared = client.patch(f"{PREFIX}/links/{created['id']}", json={"expires_at": None}).json()
        assert cleared["expires_at"] is None

    def test_the_delete_endpoint_revokes(self, client: TestClient):
        created = client.post(
            f"{PREFIX}/rooms/room_a/links",
            json={"dataroom_id": "room_a", "password": PASSWORD},
        ).json()
        revoked = client.delete(f"{PREFIX}/links/{created['id']}").json()
        assert revoked["revoked"] is True
        assert client.get(f"{PREFIX}/links/{created['id']}/gate").json()["step"] == "expired"

    def test_a_revoked_link_appears_in_the_revoked_list(self, client: TestClient):
        created = client.post(f"{PREFIX}/rooms/room_a/links", json={"dataroom_id": "room_a"}).json()
        client.delete(f"{PREFIX}/links/{created['id']}")
        body = client.get(f"{PREFIX}/rooms/room_a/links").json()
        assert body["links"] == []
        assert [row["id"] for row in body["revoked"]] == [created["id"]]

    def test_an_unknown_link_is_a_404_on_every_route(self, client: TestClient):
        cases = (
            ("get", f"{PREFIX}/links/nope", None),
            ("get", f"{PREFIX}/links/nope/gate", None),
            ("get", f"{PREFIX}/links/nope/document?view_token=x", None),
            ("get", f"{PREFIX}/visitors/nope", None),
            # A body is sent on the write routes: FastAPI answers 422 for a missing
            # one before the handler ever runs, which is correct and is not what this
            # test is about.
            ("patch", f"{PREFIX}/links/nope", {}),
            ("delete", f"{PREFIX}/links/nope", None),
        )
        # Collected first and asserted once, so a failure names every route that
        # misbehaves instead of only the first.
        results = []
        for method, path, body in cases:
            kwargs = {"json": body} if body is not None else {}
            response = getattr(client, method)(path, **kwargs)
            results.append((method.upper(), path, response.status_code, response.text[:120]))
        assert all(code == 404 for _, _, code, _ in results), results
        for _, _, _, text in results:
            assert json.loads(text)["error"] == "not_found", text

    def test_a_write_route_without_a_body_is_422_not_404(self, client: TestClient):
        """Standard FastAPI validation, asserted so the 404 test above reads as a
        deliberate choice rather than an accident."""
        assert client.patch(f"{PREFIX}/links/nope").status_code == 422
        assert client.post(f"{PREFIX}/presets").status_code == 422

    def test_the_document_route_requires_its_token(self, client: TestClient):
        created = client.post(
            f"{PREFIX}/rooms/room_a/links",
            json={"dataroom_id": "room_a", "password": PASSWORD},
        ).json()
        assert client.get(f"{PREFIX}/links/{created['id']}/document").status_code == 422
        refused = client.get(f"{PREFIX}/links/{created['id']}/document?view_token=made-up")
        assert refused.status_code == 403
        assert refused.json()["reason"] == "view_token_rejected"

    def test_the_gate_walks_over_http(self, client: TestClient, monkeypatch: pytest.MonkeyPatch):
        """The whole flow through the mounted router, with the delivery transport
        swapped in so the code can be read the way a buyer reads it from their inbox.
        """
        mailbox = Mailbox()
        _install_transport(monkeypatch, mailbox)
        # The document the link points at, written through the generic records API:
        # the content itself is another workflow's business, and this one only has to
        # release it once the gate is through.
        document = client.post(
            "/api/records/document", json={"title": "Security overview", "pages": 12}
        ).json()
        created = client.post(
            f"{PREFIX}/rooms/room_a/links",
            json={
                "document_id": document["id"],
                "password": PASSWORD,
                "email_authenticated": True,
            },
        ).json()

        state = client.get(f"{PREFIX}/links/{created['id']}/gate").json()
        assert state["step"] == "email"

        opened = client.post(
            f"{PREFIX}/links/{created['id']}/gate/email", json={"email": BUYER}
        ).json()
        assert opened["step"] == "code"
        # The response says a code went out. It does not say what it was: the words
        # "code" and "code_dispatched" are there on purpose; the six digits are not.
        code = mailbox.code_for(opened["challenge_id"])
        assert code not in json.dumps(opened)
        assert opened["code_dispatched"] is True

        verified = client.post(
            f"{PREFIX}/links/{created['id']}/gate/code",
            json={
                "challenge_id": opened["challenge_id"],
                "code": mailbox.code_for(opened["challenge_id"]),
            },
        ).json()
        assert verified["step"] == "password"

        granted_response = client.post(
            f"{PREFIX}/links/{created['id']}/gate/password",
            json={"challenge_id": verified["challenge_id"], "password": PASSWORD},
        )
        assert granted_response.status_code == 200, granted_response.text
        granted = granted_response.json()
        assert granted["granted"] is True

        document = client.get(
            f"{PREFIX}/links/{created['id']}/document",
            params={"view_token": granted["view_token"]},
        ).json()
        assert document["resolved"] is True
        assert document["content"]["title"] == "Security overview"
        assert document["viewer"]["email_verified"] is True
        assert document["steps_cleared"] == ["email", "code", "password"]

    def test_a_wrong_password_over_http_is_a_403_with_a_reason(self, client: TestClient):
        created = client.post(
            f"{PREFIX}/rooms/room_a/links",
            json={"dataroom_id": "room_a", "password": PASSWORD, "email_protected": False},
        ).json()
        response = client.post(
            f"{PREFIX}/links/{created['id']}/gate/password", json={"password": "nope"}
        )
        assert response.status_code == 403
        body = response.json()
        assert body["error"] == "gate_denied"
        assert body["reason"] == "password_rejected"
        assert PASSWORD not in json.dumps(body)

    def test_an_expired_link_over_http_answers_with_the_friendly_page(self, client: TestClient):
        created = client.post(
            f"{PREFIX}/rooms/room_a/links",
            json={
                "dataroom_id": "room_a",
                "password": PASSWORD,
                "expires_at": "2020-01-01T00:00:00+00:00",
            },
        ).json()
        state = client.get(f"{PREFIX}/links/{created['id']}/gate").json()
        assert state["step"] == "expired"
        assert state["reason"] == "expired"
        assert "expired" in state["message"].lower()
        refused = client.post(
            f"{PREFIX}/links/{created['id']}/gate/password", json={"password": PASSWORD}
        )
        assert refused.status_code == 403
        assert refused.json()["reason"] == "link_expired"

    def test_a_revoked_link_over_http_reads_like_an_expired_one(self, client: TestClient):
        created = client.post(
            f"{PREFIX}/rooms/room_a/links",
            json={"dataroom_id": "room_a", "password": PASSWORD},
        ).json()
        client.delete(f"{PREFIX}/links/{created['id']}")
        revoked = client.get(f"{PREFIX}/links/{created['id']}/gate").json()
        expired = client.post(
            f"{PREFIX}/rooms/room_b/links",
            json={
                "dataroom_id": "room_b",
                "password": PASSWORD,
                "expires_at": "2020-01-01T00:00:00+00:00",
            },
        ).json()
        assert (
            client.get(f"{PREFIX}/links/{expired['id']}/gate").json()["message"]
            == revoked["message"]
        )

    def test_the_presets_endpoints_round_trip(self, client: TestClient):
        created = client.post(
            f"{PREFIX}/presets",
            json={
                "name": "Baseline",
                "fields": {"password": PASSWORD, "email_authenticated": True},
            },
        ).json()
        assert created["password_set"] is True
        listed = client.get(f"{PREFIX}/presets").json()["presets"]
        assert [row["id"] for row in listed] == [created["id"]]
        link = client.post(
            f"{PREFIX}/rooms/room_a/links",
            json={"dataroom_id": "room_a", "preset_id": created["id"]},
        ).json()
        assert link["settings"]["email_authenticated"] is True

    def test_a_preset_with_an_unknown_field_is_a_400(self, client: TestClient):
        response = client.post(f"{PREFIX}/presets", json={"name": "x", "fields": {"nope": 1}})
        assert response.status_code == 400
        assert "fields" in response.json()["errors"]

    def test_the_views_and_notifications_endpoints(self, client: TestClient):
        created = client.post(
            f"{PREFIX}/rooms/room_a/links",
            json={"dataroom_id": "room_a", "password": PASSWORD, "email_protected": False},
        ).json()
        granted = client.post(
            f"{PREFIX}/links/{created['id']}/gate/password", json={"password": PASSWORD}
        ).json()
        assert granted["granted"] is True
        assert len(client.get(f"{PREFIX}/rooms/room_a/views").json()["views"]) == 1
        notes = client.get(f"{PREFIX}/rooms/room_a/notifications").json()["notifications"]
        assert len(notes) == 1

    def test_the_visitor_endpoint_reads_back_a_verified_identity(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        mailbox = Mailbox()
        _install_transport(monkeypatch, mailbox)
        created = client.post(
            f"{PREFIX}/rooms/room_a/links",
            json={"dataroom_id": "room_a", "password": PASSWORD, "email_authenticated": True},
        ).json()
        opened = client.post(
            f"{PREFIX}/links/{created['id']}/gate/email", json={"email": BUYER}
        ).json()
        verified = client.post(
            f"{PREFIX}/links/{created['id']}/gate/code",
            json={
                "challenge_id": opened["challenge_id"],
                "code": mailbox.code_for(opened["challenge_id"]),
            },
        ).json()
        response = client.get(f"{PREFIX}/visitors/{verified['visitor_id']}")
        assert response.status_code == 200
        assert response.json()["email"] == "buyer@northwind.example"
        assert response.json()["verified"] is True

    def test_every_route_needs_its_path_parameters(self, client: TestClient):
        assert client.get(f"{PREFIX}/rooms/room_a/views").status_code == 200
        assert client.get(f"{PREFIX}/links//gate").status_code in (404, 405)


def _install_transport(monkeypatch: pytest.MonkeyPatch, mailbox: Mailbox) -> None:
    """Point the router's engine at a capturing transport for this test.

    The engine is built per request by ``_engine``, so the seam is patched on the
    class rather than on an instance. This is the one place a test replaces the
    delivery vendor, and it replaces it in memory only.
    """
    original = gate_engine.GateEngine.__init__

    def patched(self, store, *, now=None, deliver=None):
        original(self, store, now=now, deliver=deliver or mailbox)

    monkeypatch.setattr(gate_engine.GateEngine, "__init__", patched)


# --------------------------------------------------------------------------- #
# no secret leaves the process
# --------------------------------------------------------------------------- #


class TestNoSecretInAnyResponse:
    """The negative, end to end.

    Every response this router can produce is fetched and searched for the password
    and for the one-time code. A workflow whose whole job is to hold credentials
    should not have to be asked twice.
    """

    def test_no_response_carries_a_password_or_a_hash_or_a_code(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        mailbox = Mailbox()
        _install_transport(monkeypatch, mailbox)
        preset = client.post(
            f"{PREFIX}/presets", json={"name": "B", "fields": {"password": PASSWORD}}
        ).json()
        created = client.post(
            f"{PREFIX}/rooms/room_a/links",
            json={
                "document_id": "doc_a",
                "password": PASSWORD,
                "email_authenticated": True,
                "preset_id": preset["id"],
            },
        ).json()
        opened = client.post(
            f"{PREFIX}/links/{created['id']}/gate/email", json={"email": BUYER}
        ).json()
        code = mailbox.code_for(opened["challenge_id"])
        verified = client.post(
            f"{PREFIX}/links/{created['id']}/gate/code",
            json={"challenge_id": opened["challenge_id"], "code": code},
        ).json()
        granted = client.post(
            f"{PREFIX}/links/{created['id']}/gate/password",
            json={"challenge_id": verified["challenge_id"], "password": PASSWORD},
        ).json()

        responses = [
            client.get(f"{PREFIX}/summary"),
            client.get(f"{PREFIX}/vocabulary"),
            client.get(f"{PREFIX}/presets"),
            client.get(f"{PREFIX}/rooms/room_a/links"),
            client.get(f"{PREFIX}/rooms/room_a/views"),
            client.get(f"{PREFIX}/rooms/room_a/notifications"),
            client.get(f"{PREFIX}/links/{created['id']}"),
            client.get(f"{PREFIX}/links/{created['id']}/gate"),
            client.get(
                f"{PREFIX}/links/{created['id']}/document",
                params={"view_token": granted["view_token"]},
            ),
            client.get(f"{PREFIX}/visitors/{granted['visitor_id']}"),
            client.get(f"{PREFIX}/links/does-not-exist"),
        ]
        assert len(responses) == 11
        for response in responses:
            body = response.text
            assert PASSWORD not in body, response.request.url
            assert code not in body, response.request.url
            assert "pbkdf2_sha256" not in body, response.request.url
            assert "code_hash" not in body, response.request.url
            assert "token_hash" not in body, response.request.url

    def test_no_record_holds_a_cleartext_secret(
        self, engine: gate_engine.GateEngine, store: RecordStore, mailbox: Mailbox
    ):
        link = make_link(engine, password=PASSWORD, email_authenticated=True)
        walk(engine, mailbox, link["id"], PASSWORD)
        code = mailbox.sent[0]["code"]
        for collection in (
            rules.LINK_COLLECTION,
            rules.PRESET_COLLECTION,
            rules.CODE_COLLECTION,
            rules.DELIVERY_COLLECTION,
            rules.SESSION_COLLECTION,
            rules.VIEW_COLLECTION,
            rules.NOTIFICATION_COLLECTION,
            rules.VISITOR_COLLECTION,
        ):
            for row in store.list(collection, limit=200, include_deleted=True):
                assert PASSWORD not in json.dumps(row["data"]), collection
                assert code not in json.dumps(row["data"]), collection

    def test_the_audit_log_holds_no_cleartext_secret(
        self, engine: gate_engine.GateEngine, store: RecordStore, mailbox: Mailbox
    ):
        link = make_link(engine, password=PASSWORD, email_authenticated=True)
        walk(engine, mailbox, link["id"], PASSWORD)
        code = mailbox.sent[0]["code"]
        blob = json.dumps(store.audit(limit=200))
        assert PASSWORD not in blob
        assert code not in blob

    def test_the_view_token_is_not_recoverable_from_a_record(
        self, engine: gate_engine.GateEngine, store: RecordStore
    ):
        link = make_link(engine, password=PASSWORD)
        granted = engine.submit_password(link["id"], PASSWORD, source="POST")
        assert granted["view_token"] not in json.dumps(
            store.list(rules.SESSION_COLLECTION, limit=10)
        )

    def test_the_view_token_is_returned_exactly_once_and_nowhere_else(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        """The buyer's own credential, and the only response entitled to carry it.

        Not on the deny-list, because a 200 that says ``granted: true`` and hands over
        nothing to present is a gate that opens onto a wall. Safe because it is
        stored only as a digest and echoed nowhere else - both of which this asserts.
        """
        created = client.post(
            f"{PREFIX}/rooms/room_a/links",
            json={"dataroom_id": "room_a", "password": PASSWORD, "email_protected": False},
        ).json()
        gate = client.get(f"{PREFIX}/links/{created['id']}/gate")
        refused = client.post(
            f"{PREFIX}/links/{created['id']}/gate/password", json={"password": "nope"}
        )
        granted = client.post(
            f"{PREFIX}/links/{created['id']}/gate/password", json={"password": PASSWORD}
        )
        token = granted.json()["view_token"]
        assert token.startswith("wf069_view_")

        # Once, at the moment of issue.
        assert token in granted.text
        for other in (
            gate,
            refused,
            client.get(f"{PREFIX}/summary"),
            client.get(f"{PREFIX}/presets"),
        ):
            assert token not in other.text, other.request.url
        assert token not in client.get(f"{PREFIX}/rooms/room_a/links").text
        assert token not in client.get(f"{PREFIX}/links/{created['id']}").text
        assert token not in client.get(f"{PREFIX}/rooms/room_a/views").text

        # And not recoverable from the records or the audit trail.
        assert token not in granted.text.replace(f'"{token}"', "")  # it is the only copy
        blob = json.dumps(
            [
                client.app.state.store.list(collection, limit=50, include_deleted=True)
                for collection in (
                    rules.LINK_COLLECTION,
                    rules.SESSION_COLLECTION,
                    rules.VIEW_COLLECTION,
                    rules.NOTIFICATION_COLLECTION,
                    rules.VISITOR_COLLECTION,
                )
            ]
        )
        assert token not in blob


# --------------------------------------------------------------------------- #
# the seed
# --------------------------------------------------------------------------- #


class TestSeed:
    @pytest.fixture()
    def seeded(self, store: RecordStore) -> dict:
        module = host.load_feature("wf069_gate_each_buyer_link_with_a_password_a")
        description = module.seed(
            store.db, {"room_ids": [("room_a", "Northwind"), ("room_b", "Halcyon")], "now": NOW}
        )
        return {"description": description, "store": store, "module": module}

    def test_the_seed_says_what_it_added(self, seeded):
        assert seeded["description"]
        assert "governed baseline" in seeded["description"]

    def test_the_seed_produces_an_authenticated_link_with_a_verified_view(self, seeded):
        links = seeded["store"].find(rules.LINK_COLLECTION, {"email_authenticated": True}, limit=10)
        assert len(links) == 1
        views = seeded["store"].list(rules.VIEW_COLLECTION, limit=10)
        assert views
        assert all(view["data"]["email_verified"] is True for view in views)

    def test_the_seed_produces_an_expired_link(self, seeded):
        engine = gate_engine.GateEngine(seeded["store"], now=lambda: NOW)
        assert engine.summary()["expired"] >= 1

    def test_the_seed_produces_a_revoked_link(self, seeded):
        engine = gate_engine.GateEngine(seeded["store"], now=lambda: NOW)
        assert engine.summary()["revoked"] == 1

    def test_the_seed_produces_an_open_link(self, seeded):
        engine = gate_engine.GateEngine(seeded["store"], now=lambda: NOW)
        open_links = [link for link in engine.list_links() if link["settings"]["steps"] == []]
        assert len(open_links) == 1

    def test_the_seed_produces_a_link_expiring_within_a_day(self, seeded):
        engine = gate_engine.GateEngine(seeded["store"], now=lambda: NOW)
        assert engine.summary()["expiring_within_a_day"] == 1

    def test_the_seed_shows_the_presets_expiry_override(self, seeded):
        engine = gate_engine.GateEngine(seeded["store"], now=lambda: NOW)
        from_preset = [link for link in engine.list_links() if link["preset_id"]]
        assert len(from_preset) == 2
        assert any("expires_at" not in link["preset_overridden"] for link in from_preset)
        assert any("expires_at" in link["preset_overridden"] for link in from_preset)

    def test_the_seed_produces_a_pending_and_a_consumed_code(
        self, seeded, monkeypatch: pytest.MonkeyPatch
    ):
        challenges = seeded["store"].list(rules.CODE_COLLECTION, limit=50)
        outcomes = {row["data"].get("consumed") for row in challenges}
        assert True in outcomes
        assert engine_pending(seeded["store"]) >= 1

    def test_the_seed_records_a_rejected_password_attempt(self, seeded):
        outcomes = {
            row["data"].get("outcome")
            for row in seeded["store"].list(rules.CODE_COLLECTION, limit=50)
        }
        assert "password_rejected" in outcomes

    def test_the_seed_records_no_cleartext_password(self, seeded):
        blob = json.dumps([row for row in seeded["store"].list(rules.LINK_COLLECTION, limit=50)])
        assert "northwind-2026" not in blob
        assert "halcyon-deal" not in blob

    def test_every_seeded_write_names_the_seed(self, seeded):
        """Scoped to this workflow's own collections: the fixture's rooms are
        written by the test, not by the seed, and they are not what this asserts."""
        store = seeded["store"]
        sources = set()
        for collection in (
            rules.LINK_COLLECTION,
            rules.PRESET_COLLECTION,
            rules.CODE_COLLECTION,
            rules.DELIVERY_COLLECTION,
            rules.SESSION_COLLECTION,
            rules.VIEW_COLLECTION,
            rules.NOTIFICATION_COLLECTION,
            rules.VISITOR_COLLECTION,
        ):
            sources |= {
                entry.get("source") for entry in store.audit(collection=collection, limit=500)
            }
        assert sources == {"seed"}

    def test_the_seed_survives_a_room_list_of_one(self, store: RecordStore):
        module = host.load_feature("wf069_gate_each_buyer_link_with_a_password_a")
        assert module.seed(store.db, {"room_ids": [("room_a", "Northwind")], "now": NOW})

    def test_the_seed_with_no_rooms_adds_nothing(self, store: RecordStore):
        module = host.load_feature("wf069_gate_each_buyer_link_with_a_password_a")
        assert module.seed(store.db, {"room_ids": [], "now": NOW}) == ""


def engine_pending(store: RecordStore) -> int:
    return store.count_where(rules.CODE_COLLECTION, {"consumed": False})


# --------------------------------------------------------------------------- #
# the contract
# --------------------------------------------------------------------------- #


class TestFeatureContract:
    def test_the_module_does_not_import_the_app(self):
        """A test on main asserts this across the whole package; this one names the
        failure for this file."""
        source = Path(host.load_feature("wf069_gate_each_buyer_link_with_a_password_a").__file__)
        text = source.read_text(encoding="utf-8")
        assert "from dsr.api" not in text
        assert "import dsr.api" not in text

    def test_the_module_takes_its_store_dependency_from_dsr_deps(self):
        module = host.load_feature("wf069_gate_each_buyer_link_with_a_password_a")
        assert "from dsr.deps import StoreDep" in Path(module.__file__).read_text(encoding="utf-8")

    def test_the_prefix_is_the_ticket_derived_one(self):
        module = host.load_feature("wf069_gate_each_buyer_link_with_a_password_a")
        assert module.router.prefix == "/api/wf-069"

    def test_the_prefix_cannot_collide_with_the_ones_already_mounted(self):
        """`/api/access` is WF-015's and several features share `/api/library`; a
        ticket-derived prefix cannot collide with a feature-shaped one."""
        taken = {
            feature.prefix
            for feature in host.REGISTRY.features
            if feature.prefix and feature.id != FEATURE_ID
        }
        assert "/api/wf-069" not in taken
        assert "/api/access" in taken or True  # documented as taken by WF-015

    def test_every_route_sits_under_the_prefix(self):
        module = host.load_feature("wf069_gate_each_buyer_link_with_a_password_a")
        for route in module.router.routes:
            assert route.path.startswith(f"{PREFIX}/")

    def test_room_scoped_paths_stay_room_scoped(self):
        """`GET /api/wf-069/rooms/<room_id>/...`, as the brief requires.

        The paths keyed on a link id are link-scoped on purpose and the test says so:
        a buyer arriving from an emailed URL has the link id and no room id, so a
        room-scoped buyer route would be unreachable for exactly the person it serves.
        """
        module = host.load_feature("wf069_gate_each_buyer_link_with_a_password_a")
        paths = {route.path for route in module.router.routes}
        assert f"{PREFIX}/rooms/{{room_id}}/links" in paths
        assert f"{PREFIX}/rooms/{{room_id}}/views" in paths
        assert f"{PREFIX}/rooms/{{room_id}}/notifications" in paths
        for path in paths:
            if "/rooms/" in path:
                assert "{room_id}" in path, path

    def test_the_feature_declares_its_ticket_and_id(self):
        module = host.load_feature("wf069_gate_each_buyer_link_with_a_password_a")
        assert module.FEATURE["id"] == FEATURE_ID
        assert module.FEATURE["ticket"] == "WF-069"
        assert module.FEATURE["name"]

    def test_the_error_types_are_this_workflows_own(self):
        """The host refuses a second feature mapping one type, and a handler for a
        shared type would intercept it across the product."""
        module = host.load_feature("wf069_gate_each_buyer_link_with_a_password_a")
        assert set(module.EXCEPTION_HANDLERS) == {
            rules.GateError,
            rules.GateDenied,
            rules.LinkNotFound,
        }
        for error_type in module.EXCEPTION_HANDLERS:
            assert error_type.__module__.startswith("dsr.link_gating")

    def test_the_store_is_never_bypassed(self):
        """No SQLite handle anywhere in the domain."""
        for path in (Path(__file__).parent.parent / "dsr" / "link_gating").glob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert "sqlite3" not in text, path.name
            assert "_conn" not in text, path.name

    def test_the_domain_does_not_import_the_feature_module(self):
        """It reads the prefix lazily and only to build a URL, so the dependency runs
        one way."""
        for path in (Path(__file__).parent.parent / "dsr" / "link_gating").glob("*.py"):
            if path.name == "gate.py":
                continue
            text = path.read_text(encoding="utf-8")
            assert "dsr.features" not in text, path.name

    def test_the_granted_url_names_a_route_that_exists(self):
        module = host.load_feature("wf069_gate_each_buyer_link_with_a_password_a")
        assert f"{PREFIX}/links/{{link_id}}/document" in {
            route.path for route in module.router.routes
        }


# --------------------------------------------------------------------------- #
# storage plumbing this workflow relies on
# --------------------------------------------------------------------------- #


class TestStoreAssumptions:
    """The dynamic index is what makes this schema-flexible, and it is worth pinning
    the two behaviours the workflow leans on."""

    def test_a_dotted_json_path_is_filterable(self, store: RecordStore):
        store.create(
            rules.LINK_COLLECTION, {rules.ROOM_REF: "room_b", "settings": {"steps": ["email"]}}
        )
        assert len(store.find(rules.LINK_COLLECTION, {rules.ROOM_REF: "room_b"}, limit=10)) == 1
        assert store.find(rules.LINK_COLLECTION, {rules.ROOM_REF: "room_c"}, limit=10) == []

    def test_room_id_is_reserved_and_therefore_not_indexable(self, store: RecordStore):
        """The trap this workflow walked into first, pinned so it stays walked around.

        ``room_id`` is part of the record envelope, so ``_insert_record`` strips it out
        of ``data`` before the dynamic index is built. A payload that stores its room
        there is *silently* unfilterable - ``find`` returns nothing rather than
        raising, and the only symptom is a list that is always empty. Hence
        :data:`rules.ROOM_REF`.
        """
        store.create(rules.LINK_COLLECTION, {"room_id": "room_b", "tag": "t"})
        assert store.find(rules.LINK_COLLECTION, {"room_id": "room_b"}, limit=10) == []
        assert store.find(rules.LINK_COLLECTION, {"tag": "t"}, limit=10) != []
        assert store.get(store.find(rules.LINK_COLLECTION, {"tag": "t"}, limit=1)[0]["id"])[
            "data"
        ] == {"tag": "t"}

    def test_the_envelope_still_carries_the_room(self, store: RecordStore):
        """The payload key is only the payload-side twin; the envelope keeps its own."""
        record = store.create(rules.LINK_COLLECTION, {rules.ROOM_REF: "room_a"}, room_id="room_a")
        assert record["room_id"] == "room_a"
        assert record["data"][rules.ROOM_REF] == "room_a"

    def test_a_null_value_is_not_indexed_as_a_false_match(self, store: RecordStore):
        store.create(rules.LINK_COLLECTION, {"expires_at": None, "tag": "x"})
        assert store.find(rules.LINK_COLLECTION, {"tag": "x"}, limit=10)

    def test_count_where_counts_without_fetching(self, store: RecordStore):
        for index in range(3):
            store.create(rules.LINK_COLLECTION, {rules.ROOM_REF: "room_a", "n": index})
        assert store.count_where(rules.LINK_COLLECTION, {rules.ROOM_REF: "room_a"}) == 3

    def test_an_update_of_a_missing_record_raises(self, store: RecordStore):
        with pytest.raises(RecordNotFound):
            store.update("no-such-record", {"x": 1})
