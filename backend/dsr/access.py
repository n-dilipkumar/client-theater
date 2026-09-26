"""WF-015: buyer identity verification and email-domain restriction.

The researched workflow (Qwilr help articles 151 / 156 / 797 / 175) describes
three assurance tiers in front of a page, reached from Share → Access Settings:

* **identify** — the softer *Identification* options, Name and/or Email, for
  collecting identity "for tracking purposes in analytics, without needing them
  to login to their email to verify";
* **verify_email** — the buyer enters name and email, receives an email with a
  link, and "once they've verified, they'll be able to view the Page";
* **verify_email + Domain Security** — verification "restricted to viewers with
  a certain email domain, or a set of domains… separate them with commas", and
  the operator "doesn't need to type the @ symbol".

A policy can be set on a template so that verification is "automatically
applied to each page your team creates from it", and a buyer with an existing
account can "log in with their existing [account] to populate their name and
email".

Storage
-------
Everything is a `records` row with an open JSON `data` object. There is no table
for a policy, a session, or a domain, because AGENTS.md forbids a typed column
for a team field, and a team must be able to add its own field to a policy
without coordinating with anyone. The only fixed vocabulary is the envelope plus
the handful of keys this workflow reads.

Four collections:

``access_policy``
    One row per subject (``subject_kind`` of ``room`` or ``template``,
    ``subject_id`` naming it). Written through the audited store, so a change to
    who may see a room is as traceable as any other change.

``access_session``
    The gate in front of the page. **The record id is the token** the buyer
    carries, which is what makes a session revocable (soft delete) and
    auditable, and what makes "who has actually seen this room" answerable.

``verification_outbox``
    The delivery seam. The default delivery writes here and the seller-facing
    UI shows the link, so the verification round trip is demonstrable in a local
    install that has no mail server. Swapping in SMTP is an implementation of
    the same call, not a change to this workflow.

``activity``
    The existing analytics collection. Exactly one row is emitted the first time
    a verified identity actually views the room, which is what the research
    means by the identity being "visible within your page analytics".

Honest limits
-------------
The research is explicit that the vendor has "no public REST endpoint for
security settings", so the transport here is this project's own HTTP surface
rather than a port of theirs. It is also explicit that bot and link-scanner
traffic pollutes analytics, and that the vendor has no IP blocklist: :func:`looks_like_bot`
is a heuristic over headers, recorded on the session so a reviewer can see the
reasoning, and there is no IP allowlist here either.
"""

from __future__ import annotations

import os
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from dsr.db.audited import RecordNotFound, new_id, utcnow
from dsr.store import RecordStore

# -- vocabulary -------------------------------------------------------------- #

MODE_OPEN = "open"
MODE_IDENTIFY = "identify"
MODE_VERIFY_EMAIL = "verify_email"
MODES = (MODE_OPEN, MODE_IDENTIFY, MODE_VERIFY_EMAIL)

STATUS_IDENTIFIED = "identified"
STATUS_PENDING = "pending_verification"
STATUS_VERIFIED = "verified"
STATUS_REFUSED = "refused"

METHOD_IDENTIFIED = "identified"
METHOD_EMAIL_LINK = "email_link"
METHOD_ACCOUNT_LOGIN = "account_login"

REFUSAL_DOMAIN = "domain_not_allowed"
REFUSAL_BOT = "likely_bot"
REFUSAL_EXPIRED = "session_expired"

POLICY_COLLECTION = "access_policy"
SESSION_COLLECTION = "access_session"
OUTBOX_COLLECTION = "verification_outbox"

#: What a room with no policy anywhere looks like: open, and asking for nothing.
DEFAULT_POLICY: dict[str, Any] = {
    "mode": MODE_OPEN,
    "collect_name": False,
    "collect_email": False,
    "domain_security": False,
    "allowed_domains": [],
}

#: How many times a verification token may be presented before it is refused.
#: A token is a capability, so presenting it repeatedly is the thing worth
#: counting; the count lives on the session and therefore in the audit log.
MAX_VERIFY_ATTEMPTS = 10

_EMAIL_RE = re.compile(r"^[^@\s]+@([^@\s]+\.[^@\s]+)$")
_DOMAIN_RE = re.compile(r"^(?!-)[a-z0-9-]+(\.[a-z0-9-]+)+$", re.IGNORECASE)

# Substrings that identify link scanners and previewers rather than buyers. The
# research names "preview services used by Microsoft, Outlook, or antivirus
# scanners" as the source of the pollution, so these are drawn from that
# description. A match is a hint, not proof, which is why it is stored on the
# record rather than acted on silently.
_BOT_USER_AGENT_HINTS = (
    "bot",
    "crawler",
    "spider",
    "slurp",
    "preview",
    "scanner",
    "curl/",
    "wget/",
    "python-requests",
    "httpclient",
    "java/",
    "okhttp",
    "headlesschrome",
    "phantomjs",
)
_BOT_PURPOSE_HEADERS = ("sec-purpose", "x-purpose")


class PolicyError(ValueError):
    """A policy payload is not internally consistent.

    Carries per-field messages so the API can hand the seller something they can
    act on rather than a bare "invalid".
    """

    def __init__(self, errors: Mapping[str, str]) -> None:
        self.errors = dict(errors)
        super().__init__("; ".join(f"{field_name}: {message}" for field_name, message in self.errors.items()))


class AccessDenied(PermissionError):
    """The buyer is not allowed through the gate.

    The attempt is always recorded; only the attribution is withheld.
    """

    def __init__(self, reason: str, session_id: str | None = None, message: str = "") -> None:
        self.reason = reason
        self.session_id = session_id
        super().__init__(message or reason)


# -- domain helpers ---------------------------------------------------------- #


def email_domain(email: str | None) -> str | None:
    """The lowercase domain part of an address, or ``None`` if there is not one.

    Nothing downstream of this function should have to re-parse an address, so an
    unparseable value is a first-class result rather than an exception. The local
    part has to be non-empty: ``@northwind.example`` names a domain but not a
    mailbox, and a gate that treated it as a mailbox would be trivially
    circumvented by dropping one character.
    """
    if not isinstance(email, str):
        return None
    candidate = email.strip().lower()
    if candidate.count("@") != 1 or " " in candidate:
        return None
    local, _, domain = candidate.partition("@")
    domain = domain.strip(".")
    if not local.strip() or not domain or not _DOMAIN_RE.match(domain):
        return None
    return domain


def is_valid_email(email: str | None) -> bool:
    return bool(isinstance(email, str) and _EMAIL_RE.match(email.strip()))


def normalize_domains(raw: Any) -> list[str]:
    """Normalise the allowlist into lowercase domains, order preserved.

    Accepts the researched comma-separated string ("enter the approved
    domain(s)… If you want to allow views from more than one domain, separate
    them with commas. You don't need to type the @ symbol"), a list, or anything
    in between. A leading ``@`` is stripped rather than rejected, because the
    workflow's own documentation tells the operator not to type it.
    """
    if raw is None:
        return []
    parts: Iterable[Any]
    if isinstance(raw, str):
        parts = raw.replace("\n", ",").split(",")
    elif isinstance(raw, (list, tuple, set)):
        parts = raw
    else:
        raise PolicyError({"allowed_domains": "must be a comma-separated string or a list of strings"})

    seen: dict[str, None] = {}
    for part in parts:
        if not isinstance(part, str):
            raise PolicyError({"allowed_domains": "each domain must be a string"})
        text = part.strip().lower().lstrip("@").strip().strip(".")
        if not text:
            continue
        if not _DOMAIN_RE.match(text):
            raise PolicyError({"allowed_domains": f"{part.strip()!r} is not a domain (try 'example.com')"})
        seen.setdefault(text, None)
    return list(seen)


def looks_like_bot(headers: Mapping[str, str] | None) -> bool:
    """Heuristic for link scanners and previewers, not a blocklist.

    The research records the pollution ("analytics notifications that appear to
    be from real viewers but are instead triggered by automated systems or bots
    (such as preview services used by Microsoft, Outlook, or antivirus
    scanners)") and separately records that the vendor supports no IP
    blocklisting. So this inspects only what the request itself claims, and the
    caller stores the verdict for a human to review.
    """
    if not headers:
        return False
    lowered = {str(key).lower(): str(value) for key, value in headers.items()}
    purpose = " ".join(lowered.get(name, "") for name in _BOT_PURPOSE_HEADERS).lower()
    if "prefetch" in purpose or "preview" in purpose:
        return True
    agent = lowered.get("user-agent", "").lower()
    return any(hint in agent for hint in _BOT_USER_AGENT_HINTS)


# -- policy validation ------------------------------------------------------- #


def validate_policy(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Check a policy against the researched rules and return the normalised form.

    Unknown keys are deliberately preserved: a team may add its own field to a
    policy (a watermark, a legal footer, a scoring weight) and must not need
    this function to know about it.
    """
    raw = dict(payload or {})
    errors: dict[str, str] = {}

    mode = raw.get("mode", MODE_OPEN)
    if not isinstance(mode, str) or mode not in MODES:
        errors["mode"] = f"must be one of {', '.join(MODES)}"

    collect_name = bool(raw.get("collect_name", False))
    collect_email = bool(raw.get("collect_email", False))
    domain_security = bool(raw.get("domain_security", False))

    try:
        allowed_domains = normalize_domains(raw.get("allowed_domains"))
    except PolicyError as exc:
        allowed_domains = []
        errors.update(exc.errors)

    # Sourced: ticking Email Verification "will also allow Domain Security to be
    # selected". So Domain Security without it is a misconfiguration, not a
    # distinct mode.
    if domain_security and mode != MODE_VERIFY_EMAIL:
        errors["domain_security"] = "requires Email Verification to be enabled"
    if domain_security and not allowed_domains:
        # An empty allowlist means "refuse everybody", which is never what a
        # checkbox means.
        errors["allowed_domains"] = "at least one approved domain is required when Domain Security is on"
    if mode == MODE_VERIFY_EMAIL and not collect_email:
        errors["collect_email"] = "Email Verification needs the buyer's email address"
    if mode == MODE_IDENTIFY and not (collect_name or collect_email):
        errors["collect_name"] = "an identify policy must collect at least a name or an email"

    if errors:
        raise PolicyError(errors)

    policy = dict(raw)
    policy.update(
        mode=mode,
        collect_name=collect_name,
        collect_email=collect_email,
        domain_security=domain_security,
        allowed_domains=allowed_domains,
    )
    policy["inherit"] = bool(raw.get("inherit", False))
    return policy


def required_fields(policy: Mapping[str, Any]) -> list[str]:
    """Which identity fields the buyer form must ask for, in display order."""
    return [name for name in ("name", "email") if policy.get(f"collect_{name}")]


# -- resolution -------------------------------------------------------------- #


@dataclass(frozen=True)
class ResolvedPolicy:
    """A policy plus where it came from.

    ``level`` matters to the seller: a control inherited from a template and
    shown without its origin is indistinguishable from one they set themselves,
    and the researched point is that policy rather than per-page discipline
    should enforce the control.
    """

    policy: dict[str, Any]
    level: str
    subject: str | None = None
    errors: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "policy": self.policy,
            "level": self.level,
            "source": self.subject,
            "fields": required_fields(self.policy),
        }
        if self.errors:
            body["errors"] = self.errors
        return body


def _ttl_hours(name: str, default: int) -> int:
    raw = os.environ.get(name)
    try:
        value = int(raw) if raw else default
    except ValueError:
        value = default
    return max(1, value)


def _iso_in(hours: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat(timespec="milliseconds")


def _expired(stamp: str | None) -> bool:
    if not stamp:
        return False
    try:
        return datetime.fromisoformat(stamp) <= datetime.now(timezone.utc)
    except ValueError:
        return False


# -- the gate ---------------------------------------------------------------- #


class AccessGate:
    """Behaviour for the identity gate, on top of the schema-flexible store.

    Holds a :class:`RecordStore` and nothing else: no connection, no SQL. Every
    write goes through the audited database, so a policy change and a session
    state change are as traceable as a room rename.
    """

    def __init__(self, store: RecordStore) -> None:
        self.store = store

    # -- policy resolution ------------------------------------------------- #

    def _policy_for(self, kind: str, subject_id: str) -> dict[str, Any] | None:
        matches = self.store.find(
            POLICY_COLLECTION, {"subject_kind": kind, "subject_id": subject_id}, limit=1
        )
        return matches[0] if matches else None

    def template_policy(self, template_id: str) -> dict[str, Any] | None:
        """A template's stored default policy, or ``None`` if it has none.

        Deliberately not resolved: a template is a *default*, and resolving it
        against a room is the caller's job, because a room that overrides the
        template must be able to do so without the template knowing.
        """
        record = self._policy_for("template", template_id)
        return dict(record["data"]) if record is not None else None

    def resolve(self, room_id: str) -> ResolvedPolicy:
        """The policy in force for a room, and the level it resolved at."""
        own = self._policy_for("room", room_id)
        if own is not None:
            data = dict(own["data"])
            if not data.pop("inherit", False):
                return self._validated(own, data)
            # An explicit "inherit" on the room's own row defers to the template.
            template_id = data.get("template_id")
            if template_id:
                return self._resolve_template(str(template_id), fallback_subject=f"room:{room_id}")
            return self._validated(own, data)

        room = self.store.get(room_id)
        template_id = (room or {}).get("data", {}).get("template_id")
        if template_id:
            return self._resolve_template(str(template_id), fallback_subject=f"room:{room_id}")

        return ResolvedPolicy(policy=dict(DEFAULT_POLICY), level="default", subject=None)

    def _resolve_template(self, template_id: str, *, fallback_subject: str | None) -> ResolvedPolicy:
        record = self._policy_for("template", template_id)
        if record is None:
            return ResolvedPolicy(
                policy=dict(DEFAULT_POLICY),
                level="default",
                subject=fallback_subject,
                errors={"template_id": f"template {template_id} has no access policy; the room is open"},
            )
        return self._validated(record, dict(record["data"]))

    def _validated(self, record: Mapping[str, Any], data: Mapping[str, Any]) -> ResolvedPolicy:
        """Validate a stored policy, tolerating one that no longer validates.

        A stored policy that fails a current rule must not lock the seller out of
        their own room, so the gate falls open to ``open`` and carries the reason
        in ``errors``. Hiding the control would be worse than showing it broken:
        an inherited or half-configured policy the seller cannot see is the one
        outcome the researched point about policy-enforced access rules is
        supposed to prevent.
        """
        try:
            policy = validate_policy(data)
        except PolicyError as exc:
            return ResolvedPolicy(
                policy=dict(DEFAULT_POLICY),
                level=str(data.get("subject_kind") or "room"),
                subject=record["id"],
                errors=exc.errors,
            )
        return ResolvedPolicy(
            policy=policy, level=str(data.get("subject_kind") or "room"), subject=record["id"]
        )

    # -- policy writes ------------------------------------------------------ #

    def set_policy(self, kind: str, subject_id: str, payload: Mapping[str, Any], *, actor: str | None = None) -> dict[str, Any]:
        """Validate and store a policy, or update the existing one.

        ``inherit`` and ``template_id`` are the two keys that are about the
        relationship rather than the rule, and they are accepted here so the
        whole policy is one payload for the caller to send.
        """
        if kind not in ("room", "template"):
            raise PolicyError({"subject_kind": f"must be 'room' or 'template', got {kind!r}"})
        if kind == "room" and self.store.get(subject_id) is None:
            raise RecordNotFound(subject_id)

        data = validate_policy({**payload, "subject_kind": kind, "subject_id": subject_id})
        if kind == "room" and not data.get("inherit"):
            # A room policy is about the room, not the template that seeded it.
            data.pop("template_id", None)

        existing = self._policy_for(kind, subject_id)
        if existing is not None:
            record = self.store.update(
                existing["id"],
                data,
                actor=actor,
                source=f"PUT /api/{'rooms' if kind == 'room' else 'templates'}/{subject_id}/access",
            )
        else:
            record = self.store.create(
                POLICY_COLLECTION,
                data,
                room_id=subject_id if kind == "room" else None,
                actor=actor,
                source=f"PUT /api/{'rooms' if kind == 'room' else 'templates'}/{subject_id}/access",
            )
        return {"policy": record["data"], "record_id": record["id"], "revision": record["revision"]}

    def clear_policy(self, kind: str, subject_id: str, *, actor: str | None = None) -> dict[str, Any]:
        """Drop a policy so the subject falls back to what it inherits."""
        existing = self._policy_for(kind, subject_id)
        if existing is None:
            return {"cleared": False, "reason": "no policy to clear"}
        self.store.delete(existing["id"], actor=actor, source=f"DELETE /api/{kind}s/{subject_id}/access")
        return {"cleared": True, "record_id": existing["id"]}

    # -- what the buyer is asked for --------------------------------------- #

    def requirements(self, room_id: str) -> dict[str, Any]:
        """What the gate will demand, without exposing the allowlist.

        The buyer form renders from this. The allowed domains are deliberately
        absent: a form that says "only foo.example is accepted" is a free
        allowlist oracle.
        """
        resolved = self.resolve(room_id)
        policy = resolved.policy
        return {
            "room_id": room_id,
            "mode": policy["mode"],
            "fields": required_fields(policy),
            "domain_security": bool(policy["domain_security"]),
            "requires_verification": policy["mode"] == MODE_VERIFY_EMAIL,
            "allows_account_login": policy["mode"] != MODE_OPEN and bool(policy["collect_email"]),
            "level": resolved.level,
        }

    # -- sessions ----------------------------------------------------------- #

    def _store_session(self, data: Mapping[str, Any], *, actor: str | None, source: str) -> dict[str, Any]:
        # The room association goes in the envelope's `room_id` column, never in
        # `data`: room_id is reserved vocabulary that AuditedDatabase strips from
        # the payload, so putting it in the JSON would silently vanish.
        room_id = str(data.get("room_id") or "")
        payload = {key: value for key, value in data.items() if key != "room_id"}
        return self.store.create(
            SESSION_COLLECTION,
            payload,
            room_id=room_id or None,
            actor=actor,
            source=source,
        )

    def _touch(self, record: Mapping[str, Any], patch: Mapping[str, Any], *, actor: str | None, source: str) -> dict[str, Any]:
        return self.store.update(record["id"], patch, actor=actor, source=source)

    def open_session(
        self,
        room_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        headers: Mapping[str, str] | None = None,
        method: str = METHOD_IDENTIFIED,
        base_url: str | None = None,
        deliver: bool = True,
    ) -> dict[str, Any]:
        """Run a buyer's form submission through the gate.

        The tier decides everything downstream:

        * ``open`` grants immediately and records nothing, because nothing was
          asked;
        * ``identify`` records the identity and grants immediately, with no mail;
        * ``verify_email`` records the identity as pending, queues the
          verification message, and leaves the room closed.

        A refused domain is recorded and still refuses. Recording the attempt is
        what lets a seller see that a room was probed; withholding the identity
        is what stops a probe from being attributed to a person.
        """
        body = dict(payload or {})
        resolved = self.resolve(room_id)
        policy = resolved.policy
        mode = policy["mode"]

        if mode == MODE_OPEN:
            return {
                "status": "granted",
                "mode": mode,
                "level": resolved.level,
                "token": None,
                "session_id": None,
                "message": "This room is not restricted.",
            }

        name = self._clean(body.get("name"))
        email = self._clean(body.get("email"))
        if method not in (METHOD_IDENTIFIED, METHOD_ACCOUNT_LOGIN):
            method = METHOD_IDENTIFIED

        # The account-login path is sourced as "log in with their existing
        # account to populate their name and email", so it is an alternative to
        # typing rather than a different privilege: same tier, same rules.
        if method == METHOD_ACCOUNT_LOGIN and not self.requirements(room_id)["allows_account_login"]:
            method = METHOD_IDENTIFIED

        for name_field in required_fields(policy):
            value = name if name_field == "name" else email
            if not value:
                raise PolicyError({name_field: "required"})
        if email and not is_valid_email(email):
            raise PolicyError({"email": "that does not look like an email address"})

        domain = email_domain(email)
        bot = looks_like_bot(headers)
        now = utcnow()

        if policy["domain_security"] and domain not in policy["allowed_domains"]:
            record = self._store_session(
                {
                    "room_id": room_id,
                    "policy_mode": mode,
                    "status": STATUS_REFUSED,
                    "method": method,
                    "name": name,
                    "email": email,
                    "email_domain": domain,
                    "issued_at": now,
                    "refusal_reason": REFUSAL_DOMAIN,
                    "likely_bot": bot,
                    "attempts": 0,
                    "expires_at": _iso_in(_ttl_hours("DSR_ACCESS_SESSION_TTL_HOURS", 24)),
                },
                actor="gate",
                source="POST /access/sessions",
            )
            raise AccessDenied(
                REFUSAL_DOMAIN,
                session_id=record["id"],
                message="This room is restricted to a different email domain.",
            )

        if bot:
            record = self._store_session(
                {
                    "room_id": room_id,
                    "policy_mode": mode,
                    "status": STATUS_REFUSED,
                    "method": method,
                    "name": name,
                    "email": email,
                    "email_domain": domain,
                    "issued_at": now,
                    "refusal_reason": REFUSAL_BOT,
                    "likely_bot": True,
                    "attempts": 0,
                    "expires_at": _iso_in(_ttl_hours("DSR_ACCESS_SESSION_TTL_HOURS", 24)),
                },
                actor="gate",
                source="POST /access/sessions",
            )
            raise AccessDenied(
                REFUSAL_BOT, session_id=record["id"], message="This request does not look like a browser."
            )

        pending = mode == MODE_VERIFY_EMAIL
        status = STATUS_PENDING if pending else STATUS_IDENTIFIED
        record = self._store_session(
            {
                "room_id": room_id,
                "policy_mode": mode,
                "status": status,
                "method": method,
                "name": name,
                "email": email,
                "email_domain": domain,
                "issued_at": now,
                "verified_at": None,
                "viewed_at": None,
                "refusal_reason": None,
                "likely_bot": False,
                "attempts": 0,
                "expires_at": _iso_in(_ttl_hours("DSR_ACCESS_SESSION_TTL_HOURS", 24)),
            },
            actor="gate",
            source="POST /access/sessions",
        )
        # The token is the record id, so the buyer carries the thing the audit
        # log already describes.
        record = self._touch(record, {"token": record["id"]}, actor="gate", source="POST /access/sessions")

        if not pending:
            return {
                "status": "granted",
                "mode": mode,
                "level": resolved.level,
                "token": record["id"],
                "session_id": record["id"],
                "identity": self._identity(record),
                "message": "Identity recorded.",
            }

        delivery = (
            self._queue_verification(record, base_url=base_url)
            if deliver
            else {"delivered_via": "suppressed", "status": "suppressed", "message_id": None, "link": None,
                  "open_link": None}
        )
        return {
            "status": STATUS_PENDING,
            "mode": mode,
            "level": resolved.level,
            "token": record["id"],
            "session_id": record["id"],
            "identity": self._identity(record),
            "delivered_via": delivery.get("delivered_via"),
            "message_id": delivery.get("message_id"),
            "link": delivery.get("link"),
            "open_link": delivery.get("open_link"),
            "expires_at": record["data"].get("expires_at"),
            "message": "Check your email for the verification link.",
        }

    def _identity(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = record["data"]
        return {"name": data.get("name"), "email": data.get("email"), "email_domain": data.get("email_domain")}

    def _queue_verification(self, session: Mapping[str, Any], *, base_url: str | None) -> dict[str, Any]:
        """Write the verification message to the outbox.

        This is the delivery seam. The link is built here and nowhere else, so
        the link a buyer receives and the link a test can follow cannot drift.

        Two links, on purpose:

        ``link``
            The API endpoint. Canonical, and what a test or an integration
            follows, so the flow is verifiable without a browser.
        ``open_link``
            The same endpoint with ``redirect=1``, which verifies and then sends
            the buyer to the room in the app. A human who clicks a link in an
            email and gets a JSON body has been sent the wrong link.
        """
        token = session["id"]
        room_id = session.get("room_id")
        root = (base_url or "").rstrip("/")
        link = f"{root}/api/rooms/{room_id}/access/verify?token={token}"
        open_link = f"{link}&redirect=1"
        message = self.store.create(
            OUTBOX_COLLECTION,
            {
                "session_id": token,
                "to": session["data"].get("email"),
                "subject": "Verify your email to open the room",
                "body": "Confirm this address to view the room.",
                "link": link,
                "open_link": open_link,
                "delivered_via": "outbox",
                "status": "queued",
                "queued_at": utcnow(),
            },
            room_id=room_id,
            actor="gate",
            source="verification delivery",
        )
        return {
            "message_id": message["id"],
            "delivered_via": "outbox",
            "status": "queued",
            "link": link,
            "open_link": open_link,
        }

    # -- verification ------------------------------------------------------- #

    def verify(self, room_id: str, token: str) -> dict[str, Any]:
        """The link from the verification email.

        Idempotent, because a buyer who clicks twice should not be punished, but
        every presentation is counted and audited. Counting happens *before* the
        idempotent return on purpose: someone replaying a verified link is exactly
        the thing worth noticing, and an early return would hide it.
        """
        record = self._require_session(room_id, token)
        data = dict(record["data"])
        status = data.get("status")

        attempts = int(data.get("attempts") or 0) + 1
        if attempts > MAX_VERIFY_ATTEMPTS:
            self._touch(
                record,
                {"status": STATUS_REFUSED, "refusal_reason": "too_many_attempts", "attempts": attempts},
                actor="gate",
                source="GET /access/verify",
            )
            raise AccessDenied("too_many_attempts", session_id=token, message="This verification link was retired.")

        # Recorded on every presentation, verified or not.
        record = self._touch(
            record, {"attempts": attempts}, actor="gate", source="GET /access/verify"
        )
        data = dict(record["data"])

        if status == STATUS_REFUSED:
            raise AccessDenied(
                str(data.get("refusal_reason") or REFUSAL_DOMAIN),
                session_id=token,
                message="This request is not allowed to view the room.",
            )

        if _expired(data.get("expires_at")):
            self._touch(
                record,
                {"status": STATUS_REFUSED, "refusal_reason": REFUSAL_EXPIRED},
                actor="gate",
                source="GET /access/verify",
            )
            raise AccessDenied(REFUSAL_EXPIRED, session_id=token, message="This verification link has expired.")

        if status == STATUS_VERIFIED:
            return {
                "status": STATUS_VERIFIED,
                "token": token,
                "session_id": token,
                "identity": self._identity(record),
                "already_verified": True,
            }

        # Re-check the allowlist at the door, not only at the form: policy can
        # have been tightened while the email was in flight.
        resolved = self.resolve(room_id)
        if resolved.policy.get("domain_security"):
            if data.get("email_domain") not in resolved.policy.get("allowed_domains", []):
                self._touch(
                    record,
                    {"status": STATUS_REFUSED, "refusal_reason": REFUSAL_DOMAIN},
                    actor="gate",
                    source="GET /access/verify",
                )
                raise AccessDenied(
                    REFUSAL_DOMAIN, session_id=token, message="This room is restricted to a different email domain."
                )

        updated = self._touch(
            record,
            {
                "status": STATUS_VERIFIED,
                "verified_at": utcnow(),
                "expires_at": _iso_in(_ttl_hours("DSR_ACCESS_GRANT_TTL_HOURS", 168)),
                "refusal_reason": None,
            },
            actor="gate",
            source="GET /access/verify",
        )
        self._mark_outbox_sent(token, room_id)
        return {
            "status": STATUS_VERIFIED,
            "token": token,
            "session_id": token,
            "identity": self._identity(updated),
            "already_verified": False,
        }

    def _mark_outbox_sent(self, token: str, room_id: str) -> None:
        for message in self.store.find(
            OUTBOX_COLLECTION, {"session_id": token}, limit=10
        ):
            if message["data"].get("status") != "sent":
                self.store.update(
                    message["id"],
                    {"status": "sent", "sent_at": utcnow()},
                    actor="gate",
                    source="GET /access/verify",
                )
            return

    def _require_session(self, room_id: str, token: str) -> dict[str, Any]:
        if not token:
            raise AccessDenied("missing_token", message="A verification token is required.")
        record = self.store.get(token)
        if record is None or record["collection"] != SESSION_COLLECTION:
            raise AccessDenied("unknown_token", message="That verification link is not valid.")
        # The room binding lives in the envelope, not the payload.
        if room_id and str(record.get("room_id")) != str(room_id):
            raise AccessDenied("unknown_token", message="That verification link is not valid.")
        return record

    def check(self, room_id: str, token: str | None) -> dict[str, Any]:
        """What a token is worth right now, and what is still owed.

        A grant is the *identity*, not the URL, so re-opening the room re-checks
        the session rather than trusting anything cached client-side.
        """
        resolved = self.resolve(room_id)
        if resolved.policy["mode"] == MODE_OPEN and not token:
            return {"status": "granted", "mode": MODE_OPEN, "identity": None, "owed": []}
        if not token:
            return {
                "status": "required",
                "mode": resolved.policy["mode"],
                "identity": None,
                "owed": required_fields(resolved.policy),
                "requires_verification": resolved.policy["mode"] == MODE_VERIFY_EMAIL,
            }

        record = self._require_session(room_id, token)
        data = dict(record["data"])
        status = data.get("status")

        if status == STATUS_REFUSED:
            return {
                "status": STATUS_REFUSED,
                "mode": data.get("policy_mode"),
                "identity": None,
                "refusal_reason": data.get("refusal_reason"),
                "owed": [],
            }
        if _expired(data.get("expires_at")):
            return {
                "status": "expired",
                "mode": data.get("policy_mode"),
                "identity": None,
                "refusal_reason": REFUSAL_EXPIRED,
                "owed": required_fields(resolved.policy),
            }
        if status == STATUS_PENDING:
            # The gate answers in its own vocabulary (granted | required |
            # pending | refused | expired); the stored session status is
            # `pending_verification`. `owed` is what the UI branches on.
            return {
                "status": "pending",
                "mode": data.get("policy_mode"),
                "identity": None,
                "owed": ["verification"],
                "expires_at": data.get("expires_at"),
            }

        granted = self._record_view(record, room_id)
        return {
            "status": "granted",
            "mode": data.get("policy_mode"),
            "session_id": record["id"],
            "identity": self._identity(granted),
            "verified": status == STATUS_VERIFIED,
            "viewed_at": granted["data"].get("viewed_at"),
            "owed": [],
        }

    def _record_view(self, session: Mapping[str, Any], room_id: str) -> dict[str, Any]:
        """Stamp the first view and emit exactly one analytics row for it.

        The research ties the identity to the *view*, not to the verification:
        "once someone has verified their identity and viewed your page, you'll be
        able to see this information within your page analytics". So the row is
        written on the first grant after verification and never again, which also
        keeps a refresh from inflating the numbers.
        """
        if session["data"].get("viewed_at"):
            return session
        updated = self._touch(
            session,
            {"viewed_at": utcnow(), "views": int(session["data"].get("views") or 0) + 1},
            actor="gate",
            source="GET /access/session",
        )
        room = self.store.get(room_id) or {}
        self.store.create(
            "activity",
            {
                "person": updated["data"].get("email"),
                "name": updated["data"].get("name"),
                "account": (room.get("data") or {}).get("account"),
                "action": "viewed",
                "target": (room.get("data") or {}).get("name") or room_id,
                "identity_verified": updated["data"].get("status") == STATUS_VERIFIED,
                "session_id": updated["id"],
                "occurred_at": utcnow(),
            },
            room_id=room_id,
            actor="gate",
            source="GET /access/session",
        )
        return updated

    # -- seller views ------------------------------------------------------- #

    def sessions(self, room_id: str, *, include_bots: bool = False, include_refused: bool = True) -> dict[str, Any]:
        """Who has been let in, and who has been turned away.

        Bot-flagged attempts are excluded from the identity list by default: the
        research is explicit that scanners "pollute analytics", and a flag is only
        useful if it actually changes what the seller sees.
        """
        records = self.store.list(SESSION_COLLECTION, room_id=room_id, limit=500)
        included, excluded = [], 0
        for record in records:
            data = record["data"]
            if data.get("likely_bot") and not include_bots:
                excluded += 1
                continue
            if not include_refused and data.get("status") == STATUS_REFUSED:
                continue
            included.append(record)

        counts: dict[str, int] = {}
        for record in included:
            key = str(record["data"].get("status"))
            counts[key] = counts.get(key, 0) + 1

        return {
            "room_id": room_id,
            "count": len(included),
            "excluded_bots": excluded,
            "by_status": counts,
            "verified": sum(1 for r in included if r["data"].get("status") == STATUS_VERIFIED),
            "sessions": included,
        }

    def outbox(self, room_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        return self.store.list(OUTBOX_COLLECTION, room_id=room_id, limit=limit)

    # -- helpers ------------------------------------------------------------ #

    @staticmethod
    def _clean(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


def build_token() -> str:
    """A fresh, unguessable token for callers that need one before a record id.

    Sessions use their record id as the token, so this exists for the rare case
    where a capability must exist before the row does.
    """
    return f"access_session_{secrets.token_hex(24)}"
