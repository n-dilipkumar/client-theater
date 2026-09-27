"""The automatically generated security token.

Sourced: "As an option for security measures, you have a token to use in your
service or tool to prove that traffic is coming from the Albacross platform. It
is optional to specify this automatically generated token in your system to
verify the Webhook."

Three consequences, and they are the whole of this module.

**Generated, never supplied.** The research says "automatically generated", so a
caller cannot bring its own token. :func:`generate` is the only source, and a
create request carrying a ``token`` is refused rather than ignored - a silently
ignored field is how somebody ends up believing their own token is in force.

**Revealed once at creation, then only on request.** Every other read path
returns a masked hint. :func:`mask` exists so that is a property of the code
rather than a thing every caller has to remember; see
``token-is-never-in-a-list-response`` in :mod:`dsr.intent_stream.inferences`.

**No rotation.** Nothing in this workflow's sources mentions rotating a token,
so nothing here rotates one. If a token leaks, the researched remedy is to make
a new workflow. That is recorded as a deliberate omission rather than quietly
left out, because a reader who has seen webhook features elsewhere will look for
a rotate route and its absence is a decision.
"""

from __future__ import annotations

import hmac
import secrets
from typing import Any

from dsr.intent_stream.vocabulary import TOKEN_BYTES, TOKEN_HEADER, TOKEN_PREFIX

#: How much of a token :func:`mask` shows, and from which end.
#:
#: The prefix is always shown because it is a constant and it is what tells an
#: operator "this is the token, and not some other secret in the row". Four
#: trailing characters are enough to tell two tokens apart in a list without
#: being enough to use one. Nothing in the middle is ever shown.
MASK_PREFIX_CHARS = 10
MASK_SUFFIX_CHARS = 4


def generate() -> str:
    """A fresh token, prefixed so it is recognisable in a log or a config file."""
    return f"{TOKEN_PREFIX}{secrets.token_hex(TOKEN_BYTES)}"


def mask(token: Any) -> str:
    """A display form safe for a list or a detail response.

    A token too short to mask safely is shown as ``'<not shown>'`` rather than
    truncated into something that looks usable.
    """
    text = "" if token is None else str(token)
    if len(text) <= MASK_PREFIX_CHARS + MASK_SUFFIX_CHARS:
        return "<not shown>" if text else ""
    return f"{text[:MASK_PREFIX_CHARS]}{'…'}{text[-MASK_SUFFIX_CHARS:]}"


def matches(expected: Any, presented: Any) -> bool:
    """Constant-time comparison of a presented token against the stored one.

    Not used to authenticate an inbound request - the research puts verification
    on the *destination* side, and this product has no inbound surface. It is here
    so the behaviour the vocabulary's recipe recommends can be stated once, in
    code, and tested, rather than left as a string in documentation that nothing
    checks.
    """
    if expected is None or presented is None:
        return False
    return hmac.compare_digest(str(expected), str(presented))


def headers_for(token: str) -> dict[str, str]:
    """The headers every POST carries.

    ``Content-Type`` and the token header. The user agent names this product so
    a destination operator reading their own access log can tell where a request
    came from without configuring anything.
    """
    return {
        "Content-Type": "application/json",
        TOKEN_HEADER: token,
        "User-Agent": "digital-sales-room/intent-stream",
        "Accept": "application/json",
    }
