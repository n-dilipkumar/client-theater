"""WF-094 vocabulary: the quote statuses, the ceilings and the locale set.

Every name here is either quoted from the researched specification or is a
decision recorded in ``rules`` beside the rule that uses it. The researched
property names are camelCase (``hs_quote_link``). This codebase is snake_case
throughout, so the mapping is ``hs_quote_link`` -> ``hs_quote_link`` under a
``hs_`` prefix with underscores, and the researched spelling is preserved in the
docstring of each constant so a reviewer can find the sentence it came from.

Source: ``docs/research/digital-sales-room-workflows/wf/WF-094.md``.
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# Quote statuses
# --------------------------------------------------------------------------- #
#
# The research names these as ``hs_status`` values in HubSpot's documented
# vocabulary. The active state carries the same price as the draft state; only
# the overall amount is locked.

DRAFT = "DRAFT"
PENDING_APPROVAL = "PENDING_APPROVAL"
REJECTED = "REJECTED"
PUBLISHED = "PUBLISHED"

#: "When you click Share, the quote moves to a *Shared* status, even if it
#: hasn't been sent to the buyer." Shared is its own status and is deliberately
#: not PUBLISHED: sharing makes the link copyable and the PDF downloadable
#: without recording that anybody was sent anything.
SHARED = "SHARED"

#: The statuses a quote may be published from.
#:
#: **This set is a design decision, not a quoted sentence.** The research says
#: publishing happens "in the quote editor top-right click **Share** (if no
#: approval needed)", so a quote awaiting approval does not publish straight to
#: the buyer, and it never enumerates the full set of publishable sources. The
#: three unlock targets are included because they are by definition the states in
#: which a quote's properties are editable.
#:
#: ``SHARED`` is included because publishing is a second step *after* sharing, not
#: an alternative to it. The research makes the quote publicly viewable when it
#: is shared, and separately freezes the amount when it is published, so a seller
#: may hand a buyer a readable link first and commit the figures afterwards.
#: Without that edge a SHARED quote would be a state the workflow could reach but
#: never leave. Jev chose this over a terminal SHARED and over dropping SHARED
#: altogether (audit ``jev-20261004T215726-29100-46936``, ``pass``, 0.92).
PUBLISHABLE_FROM = (DRAFT, PENDING_APPROVAL, REJECTED, SHARED)

#: "To modify any properties after you've published a quote, you must first
#: update the ``hs_status`` of the quote back to ``DRAFT``,
#: ``PENDING_APPROVAL``, or ``REJECTED``." This set is that list, and nothing
#: else unlocks. There is no fourth name and no "unlock" flag.
UNLOCK_TARGETS = (DRAFT, PENDING_APPROVAL, REJECTED)

#: Every status this workflow recognises. A quote in any other state is one
#: this feature did not write, so publishing it is refused rather than assumed.
KNOWN_STATUSES = (DRAFT, PENDING_APPROVAL, REJECTED, PUBLISHED, SHARED)

#: The statuses in which the quote's totals are frozen. The research freezes the
#: amount at publish and releases it only through an explicit unlock.
LOCKED_STATUSES = (PUBLISHED,)

#: The status a quote moves to when it has been published and then emailed.
#: The research logs "Quote published" and "Quote sent" as two separate
#: activities, so "sent" is recorded as an event and as a flag on the quote
#: rather than as a third locked status.
SENT_FLAG = "sent"


# --------------------------------------------------------------------------- #
# Size ceilings
# --------------------------------------------------------------------------- #

#: "when you share a quote by email, HubSpot doesn't attach the generated quote
#: PDF if it's larger than 20 MB." The email still sends. This is the email
#: attachment cap and nothing else.
EMAIL_ATTACHMENT_CAP_BYTES = 20 * 1024 * 1024

#: "You'll see optimum performance when the file size is less than 2 MB." That is
#: a Dynamics document-generation performance note, not a rule this workflow
#: enforces. It is recorded here so it is never mistaken for a second cap, and
#: no code refuses a PDF because of it.
DYNAMICS_PERFORMANCE_TARGET_BYTES = 2 * 1024 * 1024


# --------------------------------------------------------------------------- #
# Addresses
# --------------------------------------------------------------------------- #

#: The researched user flow states "**Cc** up to nine addresses". The cap is on
#: the Cc list only. ``To`` is a single address and ``From`` is the configured
#: default, neither of which is capped.
CC_LIMIT = 9


# --------------------------------------------------------------------------- #
# Locale
# --------------------------------------------------------------------------- #

#: The research lists ``hs_language``, ``hs_locale`` and ``hs_timezone`` among
#: the computed publish properties, and names locale and language as the control
#: over the public surface. The values below are the ones the page can render, so
#: an unknown value is refused at publish time rather than stored as a link that
#: renders in the wrong language.
LANGUAGES = ("en", "de", "fr", "es", "nl")
LOCALES = ("en-GB", "en-US", "de-DE", "fr-FR", "es-ES", "nl-NL")
TIMEZONES = ("UTC", "Europe/Amsterdam", "Europe/London", "America/New_York")

#: The values used when a setting is missing and the allowed set is itself empty.
#: They are the English entries above, so a build that emptied the lists would
#: still publish a link it can render rather than one with a blank locale.
LANGUAGE_UNSET = "en"
LOCALE_UNSET = "en-GB"
TIMEZONE_UNSET = "UTC"


# --------------------------------------------------------------------------- #
# Domain and slug
# --------------------------------------------------------------------------- #

#: "By default, quotes are hosted on the landing page primary domain connected to
#: your account", and "a separate subdomain (e.g. ``billing.website.com``) can
#: used instead". A quote with no domain configured resolves to this one, and
#: the resolution is recorded on the quote so a later settings change cannot
#: silently repoint a link that was already handed to a buyer.
DEFAULT_QUOTE_DOMAIN = "quotes.website.com"

#: "the generated PDF file is always saved to the default location:
#: ``<record_name>_<record_id>``". This is a naming convention, not a path this
#: workflow resolves, and it is applied to the recorded output location.
PDF_LOCATION_TEMPLATE = "{record_name}_{record_id}"


# --------------------------------------------------------------------------- #
# Activities
# --------------------------------------------------------------------------- #

#: "Quote activity (``Quote published``, ``Quote sent``) is logged
#: automatically." These two are the whole set the research names.
ACTIVITY_PUBLISHED = "Quote published"
ACTIVITY_SENT = "Quote sent"

#: The three labels this feature adds. None of them is in the researched set, so
#: they are recorded here rather than inline, and every one of them describes an
#: action the researched user flow does describe: the **Share** button, and the
#: **Copy link** and **Download PDF** buttons on the copy-link tab.
ACTIVITY_SHARED = "Quote shared"
ACTIVITY_LINK_COPIED = "Quote link copied"
ACTIVITY_PDF_REQUESTED = "Quote PDF requested"


# --------------------------------------------------------------------------- #
# Collections
# --------------------------------------------------------------------------- #
#
# Ordinary strings, not schema. They are conventions this feature uses and the
# generic ``/api/records`` endpoints accept any of them.

QUOTE = "quote"
ACTIVITY = "quote_activity"
