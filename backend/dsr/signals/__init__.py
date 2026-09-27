"""WF-027: emit buyer intent signals with indicators, urgency, and attribution.

The buyer-intent domain for the Digital Sales Room, kept in its own package so no
two features claim one path. A signal is "a buyer-focused action or event that
happened in the partner's or customer's platform" and "should have high value and
should drive a seller to act"; this package is the machinery for registering the
type once, emitting against that registration, and publishing the rendered
description to a seller's Live Feed.

The module layout, and why each piece is separate:

``vocabulary``      the values the research fixes by name, served as data
``schema``          the JSON-Schema subset a ``data_shape`` is checked with
``icume``           the ICU Message subset a localized description is rendered with
``indicators``      how specific an indicator's claim is, and whether evidence meets it
``registration``    registering a signal type, and the additive-only amendment rule
``emission``        emitting a signal: shape, indicators, urgency, attribution, receiver
``feed``            the Live Feed: rendered sentences, ordered by urgency
``inferences``      every judgement call, named and served
``engine``          the façade the HTTP layer calls; owns the two collections

:data:`dsr.signals.inferences.SOURCED_QUOTE` is the sentence from the research
that governs most of the field-level behaviour, and
:func:`dsr.signals.inferences.describe` is served at the feature's
``/inferences`` route so a reviewer can see which parts are sourced and which are
this build's judgement without reading the diff.
"""

from __future__ import annotations

from dsr.signals.emission import normalise_emission, resolve_receiver
from dsr.signals.engine import REGISTRATIONS, SIGNALS, SignalEngine
from dsr.signals.errors import (
    DuplicateSignalType,
    EmissionError,
    ImmutableContractError,
    IndicatorNotQualified,
    RegistrationError,
    RenderError,
    SignalError,
    UndeclaredIndicator,
    UnregisteredSignalType,
)
from dsr.signals.registration import amendment_findings, normalise_registration
from dsr.signals.vocabulary import ATTRIBUTION_TYPES, URGENCIES

__all__ = [
    "ATTRIBUTION_TYPES",
    "DuplicateSignalType",
    "EmissionError",
    "REGISTRATIONS",
    "SIGNALS",
    "SignalEngine",
    "SignalError",
    "URGENCIES",
    "UndeclaredIndicator",
    "UnregisteredSignalType",
    "ImmutableContractError",
    "IndicatorNotQualified",
    "RegistrationError",
    "RenderError",
    "amendment_findings",
    "normalise_emission",
    "normalise_registration",
    "resolve_receiver",
]
