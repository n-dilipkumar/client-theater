"""The OAuth2 consent screen and code-to-token exchange of WF-077.

The research lists this among the APIs the workflow hits - ``POST
/public/v1/access-token`` for the code to token exchange, with the two consent
surfaces named as "OAuth 'Authorize Application' consent screen" - and names the
authorization server endpoints it sits between: ``app.pandadoc.com/oauth2/authorize``
and ``api.pandadoc.com/oauth2/access_token``.

Two things about it are **sourced** and one is an **inference**:

Sourced
-------
* The exchange carries the ``read`` / ``write`` scope levels.
* "The token endpoint will reject unknown scopes." So the exchange re-validates
  the consented scope set through :func:`dsr.workspace_roles.scopes
  .normalise_scopes` rather than trusting the consent record, because a consent
  record written by an older or hand-edited row must not be able to mint a token
  holding a scope this build does not know.
* Scopes are chosen a la carte, and the consent screen shows what each unlocks.

Inference
---------
The research says nothing about a code's lifetime, its reuse, its binding to a
client, or what happens on a second exchange. Those four are this build's
choices, listed in :mod:`dsr.workspace_roles.inferences` and enforced here:

* single use, and the spend is recorded in the **same transaction** as the token
  it mints, so a code cannot be exchanged twice even if the process dies between
  the two writes;
* ten minutes, which is the RFC 6749 recommendation the research does not quote;
* bound to the workspace, the client and the exact consented scope set, so a code
  consented for two scopes cannot be exchanged for five;
* a redirect URI is recorded but **not** enforced, because the research describes
  a server-to-server exchange and never mentions a redirect.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from dsr.workspace_roles import scopes as scope_rules, vocabulary as vocab
from dsr.workspace_roles.errors import ConsentError

#: How long an authorization code stays exchangeable. Inference; see the module
#: docstring and ``inferences.py``.
CODE_TTL_SECONDS = 600

#: The two endpoints the research names, published so the consent surface can show
#: where the flow actually goes rather than only that it exists.
AUTHORIZE_ENDPOINT = "app.pandadoc.com/oauth2/authorize"
TOKEN_ENDPOINT = "api.pandadoc.com/oauth2/access_token"

#: The consent screen's title, from the research's own words.
CONSENT_TITLE = "Authorize Application"


@dataclass(frozen=True)
class ConsentRequest:
    """What the consent screen is being asked to approve."""

    workspace_id: str
    client_id: str
    client_name: str
    requested_scopes: tuple[str, ...]
    redirect_uri: str = ""
    subject: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "client_id": self.client_id,
            "client_name": self.client_name,
            "requested_scopes": list(self.requested_scopes),
            "redirect_uri": self.redirect_uri,
            "subject": self.subject,
        }


def new_consent_request(
    *,
    workspace_id: str,
    client_id: str,
    requested_scopes: Sequence[Any],
    client_name: str = "",
    redirect_uri: str = "",
    subject: str = "",
) -> ConsentRequest:
    """Validate a consent request. Refuses a wildcard or unknown scope here.

    At the *consent screen*, not only at the token endpoint. The research puts the
    rejection at the token endpoint, and this does not contradict it - the token
    endpoint still rejects them - it just refuses a nonsense request before a
    human is shown a consent screen for it. Both paths go through
    :func:`~dsr.workspace_roles.scopes.normalise_scope`, so the two cannot
    disagree about what is a valid scope.
    """
    if not str(workspace_id or "").strip():
        raise ConsentError("a workspace is required to authorize an application")
    if not str(client_id or "").strip():
        raise ConsentError("a client_id is required to authorize an application")
    return ConsentRequest(
        workspace_id=str(workspace_id),
        client_id=str(client_id),
        client_name=str(client_name or client_id),
        requested_scopes=scope_rules.normalise_scopes(requested_scopes),
        redirect_uri=str(redirect_uri or ""),
        subject=str(subject or ""),
    )


def consent_preview(
    request: ConsentRequest, endpoints: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """What the consent screen shows, which is the vendor's own promise.

    "The dashboard's token-creation UI shows which endpoints each scope unlocks."
    So the screen is not a list of scope names and an Approve button: it is the
    list of things approving will let the application do, derived from the same
    per-endpoint declarations the request path enforces.
    """
    granted = request.requested_scopes
    return {
        "title": CONSENT_TITLE,
        "client_id": request.client_id,
        "client_name": request.client_name,
        "workspace_id": request.workspace_id,
        "requested_scopes": list(granted),
        "unlocks": scope_rules.unlocked_endpoints(granted, endpoints),
        "surfaces": scope_rules.granted_surfaces(granted),
        "grants_nothing_else": vocab.NO_HIERARCHY_QUOTE,
        "authorize_endpoint": AUTHORIZE_ENDPOINT,
        "token_endpoint": TOKEN_ENDPOINT,
    }


def issue_code(
    request: ConsentRequest, *, now: datetime, code: str | None = None
) -> dict[str, Any]:
    """Mint an authorization code bound to the consented scopes.

    The returned dict is the *payload* of an ``workspace_oauth_code`` record; the
    caller stores it. Minting is separate from storing so the record's identity and
    the code are not the same string.
    """
    issued = _utc(now)
    return {
        "workspace_id": request.workspace_id,
        "client_id": request.client_id,
        "client_name": request.client_name,
        "scopes": list(request.requested_scopes),
        "redirect_uri": request.redirect_uri,
        "subject": request.subject,
        "code": code or f"ac_{secrets.token_urlsafe(24)}",
        "issued_at": issued,
        "expires_at": _utc(now + timedelta(seconds=CODE_TTL_SECONDS)),
        "used_at": None,
        "revoked": False,
    }


def check_code(
    record: Mapping[str, Any],
    *,
    workspace_id: str,
    client_id: str,
    now: datetime,
    scopes: Sequence[Any] | None = None,
) -> tuple[str, ...]:
    """Validate an authorization code and return the scopes it may mint.

    Every condition is a refusal rather than a ``None`` return, because a client
    that presents a bad code has four genuinely different problems and a message
    that says "invalid code" for all of them is the thing this workflow's own
    closed error catalogue exists to avoid.
    """
    if not record or record.get("revoked"):
        raise ConsentError("that authorization code is not valid")
    if str(record.get("workspace_id") or "") != str(workspace_id):
        raise ConsentError("that authorization code belongs to another workspace")
    if str(record.get("client_id") or "") != str(client_id):
        raise ConsentError("that authorization code was issued to another client")
    if record.get("used_at"):
        raise ConsentError(
            "that authorization code has already been exchanged; start the consent flow again"
        )
    expires_at = str(record.get("expires_at") or "")
    if expires_at and now >= _parse(expires_at):
        raise ConsentError("that authorization code has expired; start the consent flow again")

    consented = scope_rules.normalise_scopes(record.get("scopes") or ())
    if scopes is None:
        return consented

    asked = scope_rules.normalise_scopes(scopes)
    widened = [scope for scope in asked if scope not in consented]
    if widened:
        # Narrowing is fine: a client may ask for less than it was consented.
        # Widening is not, and it is the direction that matters.
        raise ConsentError(
            "that authorization code was not consented to these scopes: " + ", ".join(widened),
            consented=list(consented),
            requested=list(asked),
        )
    return asked


def spend_patch(now: datetime) -> dict[str, Any]:
    """The patch that marks a code spent, written in the token's transaction."""
    return {"used_at": _utc(now)}


def _utc(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse(text: str) -> datetime:
    parsed = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def published_oauth() -> dict[str, Any]:
    return {
        "consent_title": CONSENT_TITLE,
        "authorize_endpoint": AUTHORIZE_ENDPOINT,
        "token_endpoint": TOKEN_ENDPOINT,
        "code_ttl_seconds": CODE_TTL_SECONDS,
        "single_use": True,
        "bound_to": ["workspace_id", "client_id", "scopes"],
        "enforced_at_exchange": vocab.WILDCARD_QUOTE,
        "scope_levels": list(vocab.VERBS),
    }
