"""The capture: one anonymous website request, as the tracking script sends it.

The flow starts at step 1. "Install the Albacross tracking snippet on the site and
note the Client ID", and the data flow says the script "reads IP address, country,
network and other public parameters". So a capture here is a small, closed
vocabulary rather than a bag of anything the snippet happens to be configured
with, and the closure is the rule:

* Three public parameters, exactly the three the sentence names:
  :data:`~dsr.visitor_identification.vocabulary.CAPTURE_PARAMETERS`.
* Four payload fields that are not parameters: the path requested, the client id,
  the moment, and the calling system.
* Anything else is refused, and a key whose purpose is to name a person is
  refused by name - "exclusively company-level identification rather than
  tracking individual users, ensuring respect for user privacy".

The last rule is the one worth stating plainly: this module will not accept a
person-level field, so a person-level record cannot be created from a capture by
accident. The test that proves it does not read this docstring; it counts the
records a capture leaves behind.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from dsr.visitor_identification.errors import (
    InvalidCapture,
    PersonalDataRefused,
    UnknownCaptureParameter,
)
from dsr.visitor_identification.paths import normalise_path
from dsr.visitor_identification.vocabulary import (
    ALLOWED_CAPTURE_KEYS,
    PERSONAL_PARAMETER_NAMES,
)

#: An address or a network name is short. The bound is there so a payload cannot
#: put an unbounded string into every company record it touches.
MAX_ADDRESS_LENGTH = 255
MAX_NETWORK_LENGTH = 64
MAX_COUNTRY_LENGTH = 64
MAX_CLIENT_ID_LENGTH = 64

#: What a capture is attributed to when the caller does not name a system.
DEFAULT_ACTOR = "tracking-snippet"


@dataclass(frozen=True)
class Capture:
    """One anonymous request, read and checked, with nothing personal on it."""

    path: str
    client_id: str
    actor: str
    captured_at: str
    ip_address: str
    country: str
    network: str

    @property
    def identifies_a_network(self) -> bool:
        """Is there something here that can stand for a company?

        The research matches a request "against the company database" using
        network-level facts. With neither an address nor a network there is
        nothing to match on, and the one thing that would stand in - a person - is
        the thing this workflow refuses to build.
        """
        return bool(self.network or self.ip_address)

    def to_record(self) -> dict[str, Any]:
        """The page-visit event, as stored in ``records.data``."""
        return {
            "client_id": self.client_id,
            "path": self.path,
            "ip_address": self.ip_address,
            "country": self.country,
            "network": self.network,
            "captured_at": self.captured_at,
            "actor": self.actor,
        }


def _text(value: Any) -> str:
    """Trim a scalar to a string, or refuse it.

    A capture is small and machine-written. A number where a string belongs is a
    caller sending the wrong shape, and coercing it would hide the mistake until
    a company record held the number 24 as its country.
    """
    if value is None:
        return ""
    if isinstance(value, bool) or isinstance(value, (dict, list)):
        raise InvalidCapture(f"expected a text value, got {type(value).__name__}")
    if isinstance(value, (int, float)):
        raise InvalidCapture(f"expected a text value, got the number {value!r}")
    return str(value).strip()


def _checked(value: Any, *, field: str, limit: int) -> str:
    text = _text(value)
    if len(text) > limit:
        raise InvalidCapture(f"{field} is longer than {limit} characters")
    return text


def parse_capture(payload: dict[str, Any], *, now: datetime) -> Capture:
    """Read a capture body into a :class:`Capture`, or refuse it.

    The order of the checks is the order of the rules. Personal keys first, so a
    caller that sends one is told *why* rather than being told the key is unknown.
    Then the closed list. Then the three public parameters. Then the path.
    """
    body = dict(payload or {})

    for key in body:
        lowered = str(key).strip().lower()
        if lowered in PERSONAL_PARAMETER_NAMES:
            raise PersonalDataRefused(
                f"{key!r} identifies an individual. This workflow identifies companies only, so no "
                "person-level parameter is accepted on a capture."
            )
    for key in body:
        if str(key).strip().lower() not in ALLOWED_CAPTURE_KEYS:
            raise UnknownCaptureParameter(
                f"{key!r} is not a parameter this workflow reads. The capture reads the IP "
                "address, the country and the network."
            )

    path = normalise_path(body.get("path"))
    client_id = _checked(body.get("client_id"), field="client_id", limit=MAX_CLIENT_ID_LENGTH)
    if not client_id:
        raise InvalidCapture(
            "client_id is required. Install the tracking snippet, then send the Client ID it "
            "issued."
        )

    ip_address = _checked(body.get("ip_address"), field="ip_address", limit=MAX_ADDRESS_LENGTH)
    country = _checked(body.get("country"), field="country", limit=MAX_COUNTRY_LENGTH)
    network = _checked(body.get("network"), field="network", limit=MAX_NETWORK_LENGTH)
    if not ip_address and not network:
        raise InvalidCapture(
            "a capture needs the IP address or the network. One of them is what identifies the "
            "company the request came from."
        )

    captured_at = _parse_moment(body.get("captured_at"), now)
    actor = _text(body.get("actor")) or DEFAULT_ACTOR

    return Capture(
        path=path,
        client_id=client_id,
        actor=actor,
        captured_at=captured_at,
        ip_address=ip_address,
        country=country,
        network=network,
    )


def _parse_moment(value: Any, now: datetime) -> str:
    """The capture's moment, as an ISO 8601 string with an offset.

    A caller may send its own timestamp, because a tracking snippet batches. A
    timestamp with no offset is refused rather than assumed to be UTC: a capture
    log is ordered by time, and a value whose zone is a guess puts a visit in the
    wrong place in the sequence that decides what "last visit" means.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return now.isoformat()
    text = _text(value)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise InvalidCapture(
            f"captured_at {text!r} is not an ISO 8601 timestamp, for example "
            "2026-10-04T09:15:00+00:00"
        ) from exc
    if parsed.tzinfo is None:
        raise InvalidCapture(
            f"captured_at {text!r} has no timezone offset. A capture log is ordered by time, so a "
            "moment with no offset cannot be placed in it."
        )
    return parsed.isoformat()
