"""The hierarchical root-domain model buyer intent rolls activity up into.

Sourced
-------
"Buyer intent uses a hierarchical root-domain model and truncates 'www' for
display purposes. This means that activity from subdomains is rolled up into the
root domain."

That one sentence is load-bearing for the whole workflow, because a company is
*identified* by its root domain and everything else hangs off it. A visit to
``careers.northwind.com`` and a visit to ``www.northwind.com/pricing`` are one
company; the table has one row for them, the auto-add has one company to add,
and the credit ledger charges 10 credits once rather than twice.

Design inference
----------------
The research says "root domain" without saying how a root is found, and the
answer is a public-suffix problem, so this module carries an explicit list of
the second-level suffixes that make ``co.uk`` a suffix rather than a registrable
domain. Three consequences are inferences and are named as such in
:mod:`dsr.market_intent.inferences`:

* **Which suffixes.** There is no dependency here on a public-suffix list, so
  the list is a small, visible, editable one. A domain the list does not know is
  reduced to its last two labels, which is right for every ordinary gTLD and
  wrong only for an obscure ccTLD - and wrong in the direction of *not* merging
  two companies, which is the safer direction to be wrong in.
* **One level of ``www``.** "Truncates 'www' for display purposes" names the
  prefix, so exactly one is removed. ``www.www.example.com`` is not a real
  host and reducing it twice would be inventing a rule.
* **An address is not a domain.** "Buyer intent connects anonymous web visitors
  to known companies' IP addresses", so a bare IP reaches this module. Left
  alone, a naive last-two-labels reduction turns ``203.0.113.7`` into ``113.7``
  and every visitor behind one IP becomes one company; an IP is therefore
  returned unchanged and flagged.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

#: Second-level labels that form part of a public suffix, so the registrable
#: domain is the last *three* labels rather than the last two. Kept explicit and
#: short rather than pulled from a dependency: the failure mode of a missing
#: entry is that two companies are not merged, which is recoverable, and the
#: failure mode of a wrong entry is that two companies are merged, which is not.
MULTI_LABEL_SUFFIXES: frozenset[str] = frozenset(
    {
        "ac.at",
        "ac.nz",
        "ac.uk",
        "co.at",
        "co.id",
        "co.il",
        "co.in",
        "co.jp",
        "co.kr",
        "co.ma",
        "co.nz",
        "co.th",
        "co.uk",
        "co.za",
        "com.ar",
        "com.au",
        "com.br",
        "com.cn",
        "com.co",
        "com.hk",
        "com.mx",
        "com.my",
        "com.pe",
        "com.ph",
        "com.pk",
        "com.sg",
        "com.tr",
        "com.tw",
        "com.ua",
        "com.uy",
        "com.vn",
        "edu.au",
        "go.jp",
        "gov.au",
        "gov.uk",
        "net.au",
        "net.br",
        "net.cn",
        "ne.jp",
        "or.jp",
        "org.au",
        "org.br",
        "org.cn",
        "org.mx",
        "org.uk",
    }
)

#: "truncates 'www' for display purposes" - exactly this one prefix, once.
TRUNCATED_PREFIX = "www"

_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")


@dataclass(frozen=True)
class DomainInfo:
    """One host, and the root domain its activity rolls up into.

    ``subdomains`` is the hierarchy below the root, outermost first, so
    ``eu.shop.northwind.com`` reports ``("eu", "shop")``. Keeping the levels
    rather than only the root is what makes "hierarchical" real: a filter on a
    subdomain can be answered, and the table can show which hosts were actually
    seen rather than only the domain they were merged into.
    """

    host: str
    root: str
    subdomains: tuple[str, ...]
    is_ip: bool

    @property
    def display(self) -> str:
        """What the table and the card show.

        The root with ``www`` already removed, which is the research's "truncates
        'www' for display purposes". Equal to :attr:`root` by construction; the
        property exists so the display rule has one name and a test.
        """
        return self.root

    def to_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "root": self.root,
            "subdomains": list(self.subdomains),
            "is_ip": self.is_ip,
            "display": self.display,
        }


def parse_host(value: Any) -> str:
    """The host out of a URL or a bare host, lowercased and without a port.

    Accepts both because the tracking code will hand over whatever it has: a
    ``Referer`` header, a full page URL, or the host it resolved. Everything
    that is not the host is discarded here rather than by each caller, so a
    query string cannot leak into a path filter.
    """
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text:
        return ""

    if "//" not in text and ("://" in text or text.startswith("//")):
        text = "//" + text
    split = urlsplit(text)
    host = split.hostname or ""
    if not host:
        # ``urlsplit`` on a bare host puts it all in ``path``.
        host = split.path.split("/", 1)[0]
    host = host.strip().rstrip(".").lower()
    if "@" in host:  # userinfo in a schemeless authority
        host = host.rsplit("@", 1)[1]
    return host


def is_ip_literal(host: str) -> bool:
    """Whether a host is an address rather than a name.

    ``ipaddress`` rather than a regular expression, so ``1.2.3`` - which looks
    like three labels and reduces to ``2.3`` - is not mistaken for one.
    """
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def root_domain(host: str) -> str:
    """The registrable domain, ``www`` removed.

    Two labels are returned as they are, because ``northwind.com`` is already a
    root. Three or more keep the last three when the last two form a known
    second-level suffix, and the last two otherwise.
    """
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    if ".".join(labels[-2:]) in MULTI_LABEL_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def resolve(value: Any) -> DomainInfo:
    """Parse a host or URL and reduce it to the root domain model.

    A value with no usable host resolves to an empty :class:`DomainInfo` rather
    than raising: this is called on every page view, and one malformed referer
    in a batch should not fail the batch. Callers that require a host check
    ``.host``.
    """
    host = parse_host(value)
    if not host:
        return DomainInfo(host="", root="", subdomains=(), is_ip=False)
    if is_ip_literal(host):
        return DomainInfo(host=host, root=host, subdomains=(), is_ip=True)

    labels = host.split(".")
    root = root_domain(host)
    # "truncates 'www' for display purposes" needs no separate rule: reducing
    # ``www.northwind.com`` to its last two labels already drops the ``www``,
    # because ``www`` is a subdomain label and not part of the suffix. One
    # prefix goes with it for free; a second is not removed, and the levels
    # below the root are reported so a reader can see it was there.
    subdomains = tuple(labels[: len(labels) - len(root.split("."))])
    return DomainInfo(host=host, root=root, subdomains=subdomains, is_ip=False)


def same_company(first: Any, second: Any) -> bool:
    """Whether two hosts or URLs belong to one company.

    This is the comparison that implements "activity from subdomains is rolled
    up into the root domain", and it is the one the attribution step uses when
    it matches an anonymous visitor's IP or a known contact's email domain to a
    company it has already seen.
    """
    left = resolve(first)
    right = resolve(second)
    # An address compares exactly, which is the same comparison the domain case
    # makes: :func:`resolve` leaves an address as its own root.
    return bool(left.root) and left.root == right.root


def display_name(value: Any) -> str:
    """A readable company name from a domain, for a row with no name on file.

    A company added by auto-add has no name yet - the tracking code matched an
    address, not a company - and the table would otherwise show a bare domain in
    the column headed "company". The second-level label, title-cased and with
    hyphens turned into spaces, is a placeholder and is labelled as one by the
    page; it is derived for display and is never written to a record.
    """
    info = resolve(value)
    if not info.root:
        return ""
    label = info.root.split(".")[0] if info.root.count(".") >= 1 else info.root
    return label.replace("-", " ").replace("_", " ").title()


def is_valid_domain(value: Any) -> bool:
    """Whether a value is a registrable domain this model can key a company on.

    Used by the exclusions list, where a typo would silently exclude nothing.
    An address is not a valid domain: excluding ``203.0.113.0/24`` is a network
    rule, not a company rule, and pretending otherwise would make the list
    look like it does something it does not.
    """
    info = resolve(value)
    if not info.root or info.is_ip:
        return False
    if "." not in info.root:
        return False
    return all(_LABEL.match(label) for label in info.root.split("."))
