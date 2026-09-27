"""The one error type this workflow raises.

One type, not a hierarchy, and that is deliberate. Everything the domain refuses
is the caller's to fix, and every one of those refusals answers 422, so a
hierarchy would add names without adding a behaviour - while multiplying the
number of types another feature could collide with when it registers a handler.

The host refuses two features mapping the same exception type, so a domain type
is what makes this mapping safe: a global registration for :class:`TriageError`
cannot intercept an error raised anywhere else in the product.
"""

from __future__ import annotations


class TriageError(ValueError):
    """A view, filter, sort, or section rule this layer will not accept.

    Subclasses :class:`ValueError` so that a caller who catches ``ValueError``
    around a pure-domain call still behaves sensibly, but it is named, and it is
    the only thing the feature maps.
    """
