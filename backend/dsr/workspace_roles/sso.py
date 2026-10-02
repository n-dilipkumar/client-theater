"""Directory-driven SSO: staff authenticate through the company IdP, WF-077.

The fourth step of the researched user flow: "Admin wires directory-driven SSO so
staff authenticate through the company IdP rather than local credentials." The
data source the research names is "corporate IdP (SAML/OIDC metadata)" and the
feature is a "Seismic auth-token configuration surface".

This is the part of WF-077 with the least research behind it, and it is worth
being honest about that. The research states the *intent* - authenticate staff
through the IdP rather than local credentials - and names the metadata format. It
states no validation rules, no attribute mapping, no discovery flow, no certificate
handling and no rotation policy. So:

* the record shape is this build's;
* the one rule that is implemented as behaviour rather than storage is the plain
  reading of the sentence: **a member whose directory identity is mandatory cannot
  authenticate with local credentials while SSO is enabled**. That is the entire
  point of the workflow, and a build that stored the connection and changed nothing
  about sign-in would have implemented the storage and missed the workflow;
* everything else - who may wire it, when it is gated - comes from the plan gate,
  which is researched behaviour applied to this feature.

The named judgement calls are in :mod:`dsr.workspace_roles.inferences`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from dsr.workspace_roles import vocabulary as vocab
from dsr.workspace_roles.errors import SsoError

PROTOCOL_SAML = "saml"
PROTOCOL_OIDC = "oidc"
PROTOCOLS: tuple[str, ...] = (PROTOCOL_SAML, PROTOCOL_OIDC)

#: How a member authenticates.
AUTH_LOCAL = "local"
AUTH_SSO = "sso"
AUTH_METHODS: tuple[str, ...] = (AUTH_LOCAL, AUTH_SSO)

#: The gate name used for plan entitlement, matching
#: :data:`vocabulary.GATED_FEATURES`.
GATED_FEATURE = "sso"


def normalise_protocol(raw: Any) -> str:
    text = str(raw or "").strip().lower()
    if text not in PROTOCOLS:
        raise SsoError(f"protocol must be one of {', '.join(PROTOCOLS)}, not {raw!r}")
    return text


def normalise_auth_method(raw: Any) -> str:
    text = str(raw or "").strip().lower()
    if text not in AUTH_METHODS:
        raise SsoError(f"auth must be one of {', '.join(AUTH_METHODS)}, not {raw!r}")
    return text


def normalise_domains(raw: Any) -> tuple[str, ...]:
    """Lower-case, de-duplicated, order-preserving email domains.

    The research does not say how a connection is scoped to staff - by domain, by
    group, by an explicit member list. Domain is the narrowest thing that makes the
    sentence "staff authenticate through the company IdP" mean anything, so it is
    the one implemented, and an empty list means "every member", which is how a
    whole-company rollout is expressed.
    """
    if raw is None:
        return ()
    if isinstance(raw, str):
        candidates = [part for part in raw.replace(";", ",").split(",")]
    else:
        candidates = [str(part) for part in raw]
    seen: dict[str, None] = {}
    for candidate in candidates:
        text = candidate.strip().lower().lstrip("@").strip()
        if text:
            seen.setdefault(text, None)
    return tuple(seen)


def validate_connection(data: Mapping[str, Any]) -> dict[str, Any]:
    """Check and canonicalise a connection payload.

    Only two things are required, because they are the only two a real SAML or OIDC
    connection cannot be established without: the protocol, and the IdP's issuer or
    entity id. Everything else is optional because a team wiring an unusual IdP
    must not need this workflow changed first - which is the schema-flexibility
    rule stated as behaviour.
    """
    protocol = normalise_protocol(data.get("protocol"))
    entity = str(data.get("idp_entity_id") or data.get("issuer") or "").strip()
    if not entity:
        raise SsoError(
            "idp_entity_id (or issuer) is required: without it the IdP cannot be identified"
        )
    payload = dict(data)
    payload["protocol"] = protocol
    payload["idp_entity_id"] = entity
    payload["domains"] = list(normalise_domains(data.get("domains")))
    payload["enabled"] = bool(data.get("enabled", True))
    if protocol == PROTOCOL_SAML and not str(data.get("sso_url") or "").strip():
        raise SsoError("sso_url is required for a SAML connection")
    return payload


@dataclass(frozen=True)
class SsoDecision:
    """Whether a member may sign in with local credentials, and why."""

    allowed: bool
    reason: str
    protocol: str = ""
    idp_entity_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "local_credentials_allowed": self.allowed,
            "reason": self.reason,
            "protocol": self.protocol,
            "idp_entity_id": self.idp_entity_id,
        }


def decide_local_login(
    member: Mapping[str, Any],
    connection: Mapping[str, Any] | None,
) -> SsoDecision:
    """The one behavioural rule of the SSO half.

    "so staff authenticate through the company IdP rather than local credentials."
    Three readings were possible and this is the strict one:

    * A member with ``auth == "sso"`` on a workspace whose connection is **enabled**
      and covers their domain cannot use a local password.
    * The same member on a **disabled** connection can, because there is no
      directory to authenticate through and locking everyone out of the product
      would be worse than the governance gap.
    * A member with ``auth == "local"`` always can. SSO is never forced on a member
      who has not been moved to it - forcing sign-in on somebody is an outage
      waiting for a misconfigured IdP.
    """
    method = normalise_auth_method(member.get("auth") or AUTH_LOCAL)
    if method != AUTH_SSO:
        return SsoDecision(allowed=True, reason="this member authenticates with local credentials")

    if not connection or not connection.get("enabled"):
        return SsoDecision(
            allowed=True,
            reason="directory SSO is not enabled for this workspace, so local credentials stand",
        )

    domains = tuple(normalise_domains(connection.get("domains")))
    email = str(member.get("email") or "")
    domain = email.rpartition("@")[2].lower()
    if domains and domain and domain not in domains:
        return SsoDecision(
            allowed=True,
            reason=f"{domain or 'this member'} is outside the connection's domains, so SSO does not reach them",
            protocol=str(connection.get("protocol") or ""),
            idp_entity_id=str(connection.get("idp_entity_id") or ""),
        )

    return SsoDecision(
        allowed=False,
        reason="this member's directory identity is mandatory; local credentials are refused",
        protocol=str(connection.get("protocol") or ""),
        idp_entity_id=str(connection.get("idp_entity_id") or ""),
    )


def connection_summary(connection: Mapping[str, Any] | None) -> dict[str, Any]:
    """The connection as the page shows it, tolerant of an absent one."""
    if not connection:
        return {
            "configured": False,
            "enabled": False,
            "protocol": "",
            "idp_entity_id": "",
            "domains": [],
            "gated_feature": GATED_FEATURE,
            "requires_plan": vocab.GATED_FEATURES[GATED_FEATURE],
        }
    return {
        "configured": True,
        "enabled": bool(connection.get("enabled", True)),
        "protocol": str(connection.get("protocol") or ""),
        "idp_entity_id": str(connection.get("idp_entity_id") or ""),
        "sso_url": str(connection.get("sso_url") or ""),
        "domains": list(normalise_domains(connection.get("domains"))),
        "gated_feature": GATED_FEATURE,
        "requires_plan": vocab.GATED_FEATURES[GATED_FEATURE],
    }


def published_sso() -> dict[str, Any]:
    return {
        "protocols": list(PROTOCOLS),
        "auth_methods": list(AUTH_METHODS),
        "gated_feature": GATED_FEATURE,
        "requires_plan": vocab.GATED_FEATURES[GATED_FEATURE],
        "metadata": "SAML/OIDC metadata from the corporate IdP",
        "rule": (
            "A member whose directory identity is mandatory cannot authenticate with local "
            "credentials while the connection is enabled and covers their domain."
        ),
    }
