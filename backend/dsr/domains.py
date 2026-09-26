"""Custom domains and share-link construction for white-labelled rooms.

Separated from storage on purpose
---------------------------------
Everything in this module is pure: it normalises, validates, and builds
strings, and it never touches SQLite. The storage-facing orchestration lives in
:mod:`dsr.domain_service`. Keeping the two apart means the rules that the
research actually sourced can be tested exhaustively without a database, and it
makes the storage layer the only place that can write.

What the research sourced, and what it did not
----------------------------------------------
Sourced from the Qwilr custom-domain help article and the Liferay room
management docs (``docs/research/digital-sales-room-workflows/wf/WF-017.md``):

* The domain "needs to be in a subdomain format", e.g. ``proposals.acme.com``
  rather than ``acme.com``.
* Customers add a **CNAME** record pointing at one canonical target host, and
  "it may take up to 24 hours" for the redirect to fully work.
* A custom domain changes only the **host** portion of a page link. The slug is
  preserved and a mandatory, non-removable **link secret** is appended, e.g.
  ``proposals.acme.com/Proposal-Name-aB3xY9zK1q``.
* The link secret is "for security purposes" and "applies even after a custom
  domain is configured".
* Default-host links keep working after a custom domain takes effect.
* **Collaborator** page URLs "won't use the custom domain, as they are for
  internal use only".
* Cloudflare users must set the CNAME to **DNS only** (proxy off) "in order to
  prevent the slow loading" of pages.
* Liferay's complementary primitives: a readable **Friendly URL** for the
  address, and a separate **External Reference Code** that is "the key other
  systems use to find this room".

Design inferences (NOT sourced)
-------------------------------
These are recorded here because the workflow marks them as unspecified, and a
reader must be able to tell sourced behaviour from inferred behaviour:

* :data:`DEFAULT_CNAME_TARGET` is a placeholder. The vendor points customers at
  ``custom-domains.qwilr.com``; that is *their* edge and a customer must never
  be sent there. This product needs its own canonical target, so it is read from
  ``DSR_CNAME_TARGET`` and the deployment operator sets it. The default uses the
  RFC 2606 reserved ``.invalid`` TLD so it can never resolve and cannot collide
  with a real registrable domain.
* DNS verification checks that a host resolves to the canonical target. Most
  public resolvers return CNAME-flattened A/AAAA records, so the check accepts
  either the canonical name or an address that the deployment has configured as
  its edge. The vendor documents *that* it verifies availability but never *how*.
* Whether the CNAME resolves to the right place is a strong signal, not proof of
  control of the domain. A production deployment should also require a DNS TXT
  challenge. That is deliberately not implemented here; see the module docstring
  of :mod:`dsr.domain_service`.
* The secret, not the host, is the room's identity on a share link. This is the
  recorded decision ``secret_is_identity`` (Jev, p=0.99) and it is the answer to
  the hazard the research names: "if you wish to change your custom domain again,
  all shared links would need to be reshared otherwise the links will appear
  broken." With the secret as identity, re-pointing a domain rewrites the
  address and breaks nothing.
"""

from __future__ import annotations

import os
import re
import secrets
import socket
import unicodedata
from typing import Iterable, Protocol

__all__ = [
    "CnameResolver",
    "DEFAULT_CNAME_TARGET",
    "DomainError",
    "StaticResolver",
    "SystemResolver",
    "build_collaborator_url",
    "build_share_url",
    "cname_target",
    "default_base_url",
    "generate_link_secret",
    "generate_collaborator_token",
    "is_valid_colour",
    "is_valid_font_family",
    "link_path",
    "link_secret_from_path",
    "normalise_domain",
    "recognised_host",
    "room_slug",
    "slugify",
]


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class DomainError(ValueError):
    """Raised when a value is not a usable custom domain or brand token."""


# --------------------------------------------------------------------------- #
# Deployment configuration
# --------------------------------------------------------------------------- #

# RFC 2606 reserves .invalid precisely so that a placeholder can never resolve
# and can never be registered. Shipping a real hostname here would point real
# customers' DNS at a host we do not control.
_PLACEHOLDER_CNAME_TARGET = "custom-domains.dsr.invalid"

# Consecutive hyphens, underscores, and any label that is not LDH. A hostname
# label is letters, digits, and hyphens; anything else is rejected before it can
# reach a resolver or a URL.
_LABEL_RE = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
_IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def cname_target() -> str:
    """The canonical CNAME target customers point their domain at."""
    return os.environ.get("DSR_CNAME_TARGET", _PLACEHOLDER_CNAME_TARGET).strip().lower().rstrip(".")


def default_base_url() -> str:
    """Absolute base URL used to build shareable links.

    A share link has to be absolute or it is useless, so the deployment has to
    say what its public address is. Defaults to the local address the README
    serves on.
    """
    return os.environ.get("DSR_PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")


# --------------------------------------------------------------------------- #
# Domain normalisation and validation
# --------------------------------------------------------------------------- #


def normalise_domain(raw: object) -> str:
    """Normalise a user-entered domain, or raise :class:`DomainError`.

    People paste whatever is on their clipboard: a full URL, a trailing path, a
    capitalised name, a stray whitespace. All of that is recoverable. What is
    not recoverable is a bare registrable domain, because the research is
    explicit that the value "needs to be in a subdomain format".
    """
    if not isinstance(raw, str):
        raise DomainError("domain must be a string")

    text = raw.strip().lower()
    if not text:
        raise DomainError("domain is required")

    # Strip a scheme if one was pasted in.
    for scheme in ("https://", "http://"):
        if text.startswith(scheme):
            text = text[len(scheme) :]
            break

    # Strip a path, query, fragment, and any credentials.
    text = text.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    text = text.rsplit("@", 1)[-1]

    # A fully-qualified domain may carry a trailing root dot.
    text = text.rstrip(".")
    if not text:
        raise DomainError("domain is required")

    if " " in text or "\t" in text:
        raise DomainError("domain must not contain whitespace")

    if ":" in text:
        raise DomainError("domain must not include a port; use the hostname only")

    if _IPV4_RE.match(text):
        raise DomainError("an IP address cannot be used as a custom domain")

    labels = text.split(".")
    if any(not label for label in labels):
        raise DomainError(f"{text!r} is not a valid domain")

    for label in labels:
        if not _LABEL_RE.match(label):
            raise DomainError(f"{text!r} contains an invalid label {label!r}")

    tld = labels[-1]
    if not tld.isalpha() or len(tld) < 2:
        raise DomainError("the top-level domain must be alphabetic and at least two characters")

    # The researched rule is "it needs to be in a subdomain format", with
    # `proposals.acme.com` given as the valid example and `acme.com` as the
    # invalid one. That is exactly a minimum of three labels. A real public
    # suffix list would be needed to also reject `acme.co.uk`, which this rule
    # accepts; the simpler rule matches what the research actually specifies.
    if len(labels) < 3:
        raise DomainError(
            "the domain must be in subdomain format, for example proposals.acme.com, not acme.com"
        )

    if len(text) > 253:
        raise DomainError("domain is too long")

    return text


def is_subdomain_format(domain: str) -> bool:
    """True when ``domain`` is a normalisable subdomain (three labels or more)."""
    try:
        return bool(normalise_domain(domain))
    except DomainError:
        return False


# --------------------------------------------------------------------------- #
# Link secrets and slugs
# --------------------------------------------------------------------------- #

# Ambiguous glyphs (0/O, 1/l/I) are excluded: these secrets get read aloud on
# sales calls and retyped from a PDF. Hyphen is excluded too, which is what
# makes `link_secret_from_path` able to split on the final hyphen unambiguously.
_SECRET_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_SECRET_LENGTH = 10


def _random_token(alphabet: str, length: int) -> str:
    return "".join(secrets.choice(alphabet) for _ in range(length))


def generate_link_secret(length: int = _SECRET_LENGTH) -> str:
    """Mint a share-link secret.

    Ten characters from a 57-symbol alphabet is roughly 58 bits of entropy, which
    is a capability token: anyone holding it can read the room, so it is not
    guessable at any realistic request rate.
    """
    if length < 8:
        raise DomainError("a link secret must be at least 8 characters")
    return _random_token(_SECRET_ALPHABET, length)


def generate_collaborator_token(length: int = 16) -> str:
    """Mint an internal collaborator token, separate from the buyer link secret.

    The research is explicit that collaborator URLs are internal-only and bypass
    the custom domain, so they get their own, longer token rather than reusing
    the buyer-facing one.
    """
    return _random_token(_SECRET_ALPHABET, max(16, length))


_SLUG_STRIP_RE = re.compile(r"[^A-Za-z0-9]+")


def slugify(name: object) -> str:
    """Turn a room name into a readable URL segment.

    Matches the researched example, where ``Proposal-Name`` is a room name and
    ``aB3xY9zK1q`` is the appended secret. Casing is preserved because the
    researched example is cased, and because a shared link reads better in the
    buyer's language than in lowercase.

    Camel-case runs are split so ``NorthwindTraders`` becomes two words rather
    than one, and accented characters are folded to their ASCII base so
    ``Réunion`` becomes ``Reunion`` instead of losing its first letter.
    """
    if not isinstance(name, str):
        return "room"

    # Fold accents to their base letter before dropping non-ASCII, so "é" goes
    # to "e" rather than to a separator.
    folded = unicodedata.normalize("NFKD", name)
    ascii_only = folded.encode("ascii", "ignore").decode("ascii")

    # Split camelCase/PascalCase runs so they read as separate words.
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", ascii_only.strip())

    slug = _SLUG_STRIP_RE.sub("-", spaced).strip("-")

    # Collapse runs and cap the length; DNS labels are 63 bytes and URLs read
    # badly long before that.
    slug = re.sub(r"-{2,}", "-", slug)[:48].strip("-")
    return slug or "room"


def room_slug(name: object, link_secret: str) -> str:
    """The researched path shape: readable name, then the mandatory secret.

    ``("Proposal Name", "aB3xY9zK1q") -> "Proposal-Name-aB3xY9zK1q"``
    """
    if not link_secret:
        raise DomainError("a link secret is required to build a room slug")
    return f"{slugify(name)}-{link_secret}"


def link_secret_from_path(path: object) -> str | None:
    """Recover the secret from a request path, if the path carries one.

    The secret is whatever follows the final hyphen: the slug alphabet excludes
    hyphens, so the split is unambiguous. This is the read side of the
    ``secret_is_identity`` decision — identity is recovered from the path
    regardless of which verified host the request arrived on.
    """
    if not isinstance(path, str):
        return None

    segment = path.strip().strip("/").split("/")[-1]
    if not segment or "-" not in segment:
        return None

    candidate = segment.rsplit("-", 1)[-1]
    if not candidate or any(char not in _SECRET_ALPHABET for char in candidate):
        return None
    return candidate


def link_path(name: object, link_secret: str) -> str:
    """The path a share link uses, without scheme or host."""
    return f"/r/{room_slug(name, link_secret)}"


# --------------------------------------------------------------------------- #
# URL construction
# --------------------------------------------------------------------------- #


def build_share_url(
    room: dict,
    *,
    base_url: str | None = None,
    use_custom_domain: bool = True,
) -> str:
    """Build the buyer-facing share URL for a room.

    Applies the researched behaviour end to end: a **verified** custom domain
    replaces the host, the room name becomes the readable slug, and the
    non-removable secret is appended. When no domain is verified -- which is the
    state during the researched "up to 24 hours" propagation window, and the
    state forever if the customer never sets one -- the default host is used and
    the link works, because "you can still share the default links during that
    time, and they'll continue to work".
    """
    data = room.get("data") or {}
    secret = data.get("link_secret")
    if not secret:
        raise DomainError("room has no link secret; mint one before sharing it")

    path = link_path(data.get("name"), secret)
    host = default_base_url() if base_url is None else base_url.rstrip("/")

    if use_custom_domain:
        domain = data.get("domain")
        if domain and data.get("domain_status") == "verified":
            host = f"https://{normalise_domain(domain)}"

    return f"{host}{path}"


def build_collaborator_url(room: dict, *, base_url: str | None = None) -> str:
    """Build the internal collaborator URL for a room.

    Sourced rule: "Collaborator page URLs won't use the custom domain, as they
    are for internal use only." So this always uses the deployment's own base URL
    and a separate collaborator token, and it is never presented to a buyer.
    """
    data = room.get("data") or {}
    token = data.get("collaborator_token")
    if not token:
        raise DomainError("room has no collaborator token; mint one first")

    host = default_base_url() if base_url is None else base_url.rstrip("/")
    return f"{host.rstrip('/')}/collab/{token}"


def recognised_host(
    host: object,
    *,
    claimed_domains: Iterable[str] = (),
    base_url: str | None = None,
) -> bool:
    """True when ``host`` is one this deployment serves.

    Routing concern, not identity: a request for a secret that this deployment
    does not serve is not our traffic at all. Every *verified* domain is
    recognised, and so is the default host, so a link shared before the domain
    was configured keeps resolving afterwards.
    """
    if not isinstance(host, str) or not host.strip():
        return False

    candidate = host.strip().lower()
    # Drop a port; the domain claim is about the hostname.
    candidate = candidate.rsplit(":", 1)[0] if candidate.count(":") == 1 else candidate
    candidate = candidate.rstrip(".")

    base = (default_base_url() if base_url is None else base_url).strip().lower()
    base_host = base.split("//", 1)[-1].split("/", 1)[0]
    if base_host.count(":") == 1:
        base_host = base_host.rsplit(":", 1)[0]

    if candidate == base_host:
        return True

    for domain in claimed_domains:
        try:
            if candidate == normalise_domain(domain):
                return True
        except DomainError:
            # A stored value that no longer normalises is simply not a host we
            # serve. It is not an error at request time.
            continue
    return False


# --------------------------------------------------------------------------- #
# Brand token safety
# --------------------------------------------------------------------------- #

# A tenant-supplied colour ends up in a `style` attribute. Accepting arbitrary
# text there would let one customer inject CSS (or a url() beacon) into another
# customer's room, so the accepted grammar is deliberately narrow.
_COLOUR_RE = re.compile(
    r"""^(
        \#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})
        | rgba?\(\s*[\d.]+\s*,\s*[\d.]+\s*,\s*[\d.]+\s*(?:,\s*[\d.]+\s*)?\)
        | hsla?\(\s*[\d.]+(?:deg)?\s*,\s*[\d.]+%\s*,\s*[\d.]+%\s*(?:,\s*[\d.]+\s*)?\)
        | (?:oklch|color)\(\s*[^()]*\)
        | [a-zA-Z]{3,20}
    )$""",
    re.VERBOSE,
)

# A font stack is a comma-separated list of family names, optionally quoted,
# with optional generic fallbacks. Anything with a URL, a semicolon, or a brace
# is rejected.
_FONT_FAMILY_RE = re.compile(r"""^[A-Za-z0-9 '"_-]+(\s*,\s*[A-Za-z0-9 '"_-]+)*$""")
_FONT_FONT_FACE_RE = re.compile(r"^@font-face$|^@import$")

# Named CSS colours are finite; an unknown bare word is far more likely to be a
# mistake or an attempt than a real colour name.
_NAMED_COLOURS = frozenset(
    """aliceblue antiquewhite aqua aquamarine azure beige bisque black blanchedalmond blue blueviolet
    brown burlywood cadetblue chartreuse chocolate coral cornflowerblue cornsilk crimson cyan darkblue
    darkcyan darkgoldenrod darkgray darkgreen darkgrey darkkhaki darkmagenta darkolivegreen darkorange
    darkorchid darkred darksalmon darkseagreen darkslateblue darkslategray darkslategrey darkturquoise
    darkviolet deeppink deepskyblue dimgray dimgrey dodgerblue firebrick floralwhite forestgreen fuchsia
    gainsboro ghostwhite gold goldenrod gray green greenyellow grey honeydew hotpink indianred indigo
    ivory khaki lavender lavenderblush lawngreen lemonchiffon lightblue lightcoral lightcyan
    lightgoldenrodyellow lightgray lightgreen lightgrey lightpink lightsalmon lightseagreen lightskyblue
    lightslategray lightslategrey lightsteelblue lightyellow lime limegreen linen magenta maroon
    mediumaquamarine mediumblue mediumorchid mediumpurple mediumseagreen mediumslateblue
    mediumspringgreen mediumturquoise mediumvioletred midnightblue mintcream mistyrose moccasin
    navajowhite navy oldlace olive olivedrab orange orangered orchid palegoldenrod palegreen
    paleturquoise palevioletred papayawhip peachpuff peru pink plum powderblue purple rebeccapurple red
    rosybrown royalblue saddlebrown salmon sandybrown seagreen seashell sienna silver skyblue slateblue
    slategray slategrey snow springgreen steelblue tan teal thistle tomato turquoise violet wheat white
    whitesmoke yellow yellowgreen transparent currentcolor""".split()
)


def is_valid_colour(value: object) -> bool:
    """True when ``value`` is a colour token safe to place in a style attribute."""
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text or len(text) > 64:
        return False
    if text.lower() in _NAMED_COLOURS:
        return True
    return bool(_COLOUR_RE.match(text))


def is_valid_font_family(value: object) -> bool:
    """True when ``value`` is a CSS font stack that cannot escape its property.

    Rejects ``@font-face`` / ``@import`` and anything with a semicolon, brace,
    parenthesis, or backslash, which are the characters that would let a font
    stack turn into a stylesheet.
    """
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text or len(text) > 200:
        return False
    if _FONT_FONT_FACE_RE.match(text.replace(" ", "").lower()):
        return False
    for forbidden in (";", "{", "}", "(", ")", "\\", "<", ">", "/*"):
        if forbidden in text:
            return False
    return bool(_FONT_FAMILY_RE.match(text))


# --------------------------------------------------------------------------- #
# DNS resolution for verification
# --------------------------------------------------------------------------- #


class CnameResolver(Protocol):
    """What a DNS verification check needs from a resolver.

    Deliberately narrow so verification is testable without network access: the
    service takes one of these, and tests pass a :class:`StaticResolver`.
    """

    def resolves_to(self, host: str, target: str) -> tuple[bool, list[str]]:
        """Return ``(matches, observed)`` for ``host``.

        ``observed`` is the list of names/addresses the host actually resolved
        to, so a failed check can tell the operator what they got instead of
        just that it was wrong.
        """
        ...


class SystemResolver:
    """Resolver backed by the host's configured DNS.

    ``getaddrinfo`` returns whatever the recursive resolver decided to hand
    back. For a CNAME that is usually the flattened address, so the observed
    names are compared against the canonical target *and* the deployment's edge
    addresses (``DSR_EDGE_ADDRESSES``, comma separated) when flattening occurs.
    """

    def __init__(self, edge_addresses: Iterable[str] | None = None) -> None:
        configured = os.environ.get("DSR_EDGE_ADDRESSES", "")
        raw = list(edge_addresses) if edge_addresses is not None else [a.strip() for a in configured.split(",")]
        self.edge_addresses = {a.strip().lower() for a in raw if a and a.strip()}

    def resolves_to(self, host: str, target: str) -> tuple[bool, list[str]]:
        try:
            infos = socket.getaddrinfo(host, None)
        except (socket.gaierror, UnicodeError, OSError):
            return False, []

        observed: list[str] = []
        for info in infos:
            sockaddr = info[4]
            address = str(sockaddr[0]).lower() if sockaddr else ""
            if address and address not in observed:
                observed.append(address)

        expected = target.lower()
        # A canonical name answers directly; a flattened CNAME answers with an
        # address, so the deployment's edge addresses stand in for it.
        matched = expected in {name.lower() for name in observed} or bool(
            self.edge_addresses & set(observed)
        )
        return matched, observed


class StaticResolver:
    """Resolver backed by a fixed map, for tests and offline development.

    Maps a hostname to the list of names/addresses it resolves to. A host absent
    from the map does not resolve, which is what an unpropagated CNAME looks
    like from the verifier's side.
    """

    def __init__(self, table: dict[str, Iterable[str]] | None = None) -> None:
        self.table = {k.lower(): [str(x).lower() for x in v] for k, v in (table or {}).items()}

    def resolves_to(self, host: str, target: str) -> tuple[bool, list[str]]:
        observed = self.table.get(host.lower(), [])
        expected = target.lower()
        return expected in observed, observed


def resolver_from_env() -> CnameResolver:
    """Build the resolver the deployment configured.

    ``DSR_CNAME_FIXTURES`` holds inline JSON mapping a hostname to the names or
    addresses it resolves to, which lets the verification flow be exercised --
    and tested -- with no DNS at all. Without it the system resolver is used.
    """
    import json

    raw = os.environ.get("DSR_CNAME_FIXTURES", "").strip()
    if not raw:
        return SystemResolver()
    try:
        table = json.loads(raw)
    except json.JSONDecodeError:
        return SystemResolver()
    if not isinstance(table, dict):
        return SystemResolver()
    return StaticResolver(table)
