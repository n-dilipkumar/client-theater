"""Subscriber target URLs, and the presigned file URLs inside a payload.

Target URLs
-----------
Sourced: "Click **Create Webhook**, name it, and enter the HTTPS target URL (Dock
verifies the URL with a POST)."

Two rules come straight out of that sentence, and they are the reason this
module exists:

**HTTPS only.** The research says the field is an HTTPS target URL. So
``http://`` is refused, and a URL that embeds credentials (``https://u:p@host/``)
is refused too - the credentials would go into every delivery's ``Host`` line
and into a subscriber's access log, and the operator has no way to rotate them
there. See ``inferences.py`` entry ``https-only-targets``.

**The URL is verified by a POST before the webhook exists.** A webhook whose
target refuses a POST is not created: nothing is written, so there is no
half-configured webhook in the list that has never worked. See
:func:`verification_failure` for the message that says what came back, because
"Dock verifies the URL with a POST" is a promise the operator will hold this
build to and the endpoint's own answer is the only useful evidence.

Presigned file URLs
-------------------
Sourced, from the researched note on the payload: "Presigned file URLs in
payloads expire ("The URL expires at ``expiresAt`` (one hour)")."

So a presigned URL is *always* stored with its ``expiresAt``, and it is
:data:`PRESIGNED_TTL_SECONDS` from issue. :func:`is_expired` exists because an
expired presigned URL in a stored event is not a bug to be hidden: a subscriber
reading an hour-old ``workspace.form.submitted`` event gets a ``403`` from
object storage and no idea why, and the only honest place to have said so was
the event record at the time it was written.
"""

from __future__ import annotations

import ipaddress
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from urllib.parse import urlsplit

from dsr.event_stream.errors import TargetError

#: "The URL expires at ``expiresAt`` (one hour)." Reproduced as a constant
#: rather than as a number buried in a formatter, because it is the researched
#: value and a reviewer should be able to check it at a glance.
PRESIGNED_TTL_SECONDS = 3600

#: Hostnames a webhook must not be pointed at. Not a sourced list - a deployment
#: reaching its own metadata endpoint through a feature it configured is a real
#: and boring failure - so it is an allowlist-by-default rule with the ability to
#: switch it off, and it is named as an inference.
BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
        "169.254.169.254",  # cloud instance metadata
        "metadata.google.internal",
        "metadata.goog",
    }
)

#: Hostname suffixes blocked for the same reason.
BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".localdomain", ".home.arpa")


def parse_now(value: Any = None) -> datetime:
    """Coerce ``value`` to an aware UTC datetime, defaulting to now.

    The store holds ISO strings, so this is the one place that decides what "now"
    means when a caller passes a stored timestamp back in.
    """
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def iso(moment: datetime) -> str:
    """The wire format for a moment: UTC, millisecond precision, sortable."""
    return moment.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def validate_target_url(url: Any, *, allow_private: bool = False) -> str:
    """Check a subscriber target and return it normalised.

    ``allow_private`` exists because a demo, a staging environment, and a
    subscriber that really is on ``localhost`` all need one, and a rule that
    cannot be relaxed is a rule that gets deleted. It is off by default so the
    default is the safe one.
    """
    if not isinstance(url, str) or not url.strip():
        raise TargetError(
            "targetUrl is required",
            remediation="Send the HTTPS URL that should receive the events, for example https://hooks.example/dsr.",
        )
    candidate = url.strip()
    parts = urlsplit(candidate)

    if parts.scheme != "https":
        raise TargetError(
            f"targetUrl must be https, got {parts.scheme or 'no scheme'!r}",
            remediation=(
                "The target URL field is an HTTPS URL. A webhook carries customer page "
                "metadata in clear text over http, so it is refused."
            ),
        )
    if not parts.netloc:
        raise TargetError(
            "targetUrl must include a host", remediation="Send a full URL including the host."
        )
    # `hostname` is None for a netloc urlsplit cannot read - `https://::1/x` is the
    # example, and it is exactly the shape an unvalidated value takes. Treated as
    # "no host" rather than skipped, because a host we cannot determine is not a
    # host we should be fetching.
    host = (parts.hostname or "").lower()
    if not host:
        raise TargetError(
            "targetUrl has no readable host",
            remediation="Send a full URL including the host, for example https://hooks.example/dsr.",
        )
    if parts.username or parts.password:
        raise TargetError(
            "targetUrl must not embed credentials",
            remediation=(
                "Put the credentials in the target's own query string or header contract. "
                "Credentials in the URL land in every subscriber's access log."
            ),
        )
    if parts.fragment:
        raise TargetError(
            "targetUrl must not carry a fragment",
            remediation="Fragments are not sent to a server, so a target with one can never be verified.",
        )

    if not allow_private:
        blocked = (
            host in BLOCKED_HOSTNAMES
            or host.endswith(BLOCKED_SUFFIXES)
            or _is_internal_address(host)
        )
        if blocked:
            raise TargetError(
                f"targetUrl host {host!r} is not reachable from this deployment",
                remediation=(
                    "Point the webhook at a public HTTPS endpoint. A private address is "
                    "refused so a webhook cannot be pointed at this host's own metadata "
                    "service or an internal network service."
                ),
            )
    return candidate


def _is_internal_address(host: str) -> bool:
    """Whether ``host`` is a loopback, link-local, or private IP literal.

    A name-based check alone would miss ``https://10.0.0.1/hook``, which reaches
    an internal service just as surely as ``https://metadata.google.internal``.
    Not a sourced rule - see the ``https-only-targets`` entry in
    :mod:`dsr.event_stream.inferences` - and switchable with the same flag.
    """
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return (
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_reserved
        or address.is_unspecified
    )


def same_origin(first: str, second: str) -> bool:
    """Whether two URLs share scheme, host, and port.

    Not used on any delivery path - it exists so a test can assert that a demo
    webhook's traffic all went to one place, without comparing full URLs that
    legitimately differ by path.
    """
    a, b = urlsplit(first), urlsplit(second)
    return (a.scheme, a.hostname, a.port) == (b.scheme, b.hostname, b.port)


def verification_failure(
    url: str, status: int | None, error: str | None, body: str = ""
) -> TargetError:
    """The refusal for a target that would not accept the verification POST.

    Carries the endpoint's own answer because "Dock verifies the URL with a
    POST" means the operator is entitled to see what their endpoint said. The
    body is truncated: it is a stranger's response, and an audit mirror should
    not grow without bound from one chatty endpoint.
    """
    detail = f"targetUrl {url} did not accept the verification POST"
    if status is not None:
        detail += f" (HTTP {status})"
    if error:
        detail += f": {error}"
    return TargetError(
        detail,
        code="target_not_verified",
        remediation=(
            "The endpoint answered a POST. Check that it is reachable over HTTPS, that it "
            "accepts POST, and that it returns a 2xx. No webhook was created."
            + (f" It said: {body[:200]}" if body else "")
        ),
    )


def issue_presigned_url(key: str, *, now: Any = None, token: str | None = None) -> dict[str, Any]:
    """A presigned file URL with the researched one-hour expiry.

    The shape is the researched one - a ``url`` and an ``expiresAt`` that is one
    hour out - plus the ``key`` it points at, because a subscriber that stores
    the URL and loses the key cannot ask for the object again.
    """
    issued = parse_now(now)
    expires = issued + timedelta(seconds=PRESIGNED_TTL_SECONDS)
    return {
        "url": f"https://objects.example/signed/{token or key}?expires={int(expires.timestamp())}",
        "key": key,
        "expiresAt": iso(expires),
    }


def is_expired(entry: Mapping[str, Any] | None, *, now: Any = None) -> bool:
    """Whether a presigned URL has expired. ``False`` when it has no expiry.

    A presigned object with no ``expiresAt`` is not something this product
    issued, and treating it as expired would be a claim it cannot support.
    """
    if not entry:
        return False
    expires_at = entry.get("expiresAt") or entry.get("expires_at")
    if not expires_at:
        return False
    try:
        return parse_now(now) >= parse_now(expires_at)
    except (TypeError, ValueError):
        return False


def expiry_note(entry: Mapping[str, Any] | None, *, now: Any = None) -> str:
    """One sentence for the UI, so an expired URL is not a mystery."""
    if not entry or not (entry.get("expiresAt") or entry.get("expires_at")):
        return ""
    when = entry.get("expiresAt") or entry.get("expires_at")
    if is_expired(entry, now=now):
        return f"the presigned URL expired at {when}; the object must be re-signed before it can be fetched"
    return f"the presigned URL expires at {when}"
