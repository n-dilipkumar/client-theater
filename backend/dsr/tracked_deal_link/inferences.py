"""Every decision WF-121 makes that its research does not, and how to change it.

The research for this workflow is ``docs/research/digital-sales-room-workflows/
wf/WF-121.md``, quoted in full on issue #122. It names the behaviour and leaves
five values open. Each one is recorded here rather than buried in a default, and
the register is served at ``GET /api/wf-121/inferences`` so a reviewer can
disagree with a named entry instead of hunting through a diff.

The distinction the register keeps is the one the project contract asks for: the
sourced half is checkable against the research, and this half is a decision with
a stated alternative.
"""

from __future__ import annotations

from typing import Any

from . import vocabulary as vocab

#: The register. Ordered so the served payload reads the way the build was
#: reasoned about.
INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "link_lifetime",
        "decision": f"A tracked link expires {vocab.DEFAULT_LINK_LIFETIME_DAYS} days after it is created.",
        "sourced": False,
        "research_says": (
            "The automations field lists 'Link-expiry', and no rule, duration or "
            "default is given anywhere in the specification."
        ),
        "why": (
            "A link with no expiry would be a link a buyer can open after the deal "
            "closes, which is worse than a link that lapses while the deal is live. "
            "The default is deliberately long enough that a real sales cycle does not "
            "hit it and short enough that a stale link stops working within a quarter."
        ),
        "change_it_by": (
            "Pass lifetime_days on the link, or pass an explicit expires_at. Both are "
            "stored on the link with no migration."
        ),
        "rejected": "No expiry at all, and a 7-day default, which would expire a live deal.",
    },
    {
        "id": "alert_page_threshold",
        "decision": f"A view alerts the rep once a viewer reaches page {vocab.DEFAULT_ALERT_PAGE_THRESHOLD}.",
        "sourced": False,
        "research_says": (
            "The automations field says 'auto-notify rep on N-th page view or on "
            "return visit'. The capability is named. N is not."
        ),
        "why": (
            "The user flow says the rep is alerted on the share, so a first view "
            "always alerts; the threshold is what makes a deeper read a second, "
            "stronger signal rather than another identical notification."
        ),
        "change_it_by": "Pass alert_on_page on the link; it is stored per link.",
        "rejected": "Alerting on every page view, which would bury the signal it exists to carry.",
    },
    {
        "id": "dwell_alert",
        "decision": (
            "A view alerts the rep when the elapsed dwell reaches "
            f"{vocab.DEFAULT_DWELL_ALERT_SECONDS} seconds."
        ),
        "sourced": False,
        "research_says": "No dwell threshold is named. The research names dwell as a metric.",
        "why": (
            "The same sentence that asks for the n-th page view asks to be told when a "
            "buyer is reading seriously. The per-page engine already answers 'how long', "
            "and this turns that answer into a signal a rep can act on."
        ),
        "change_it_by": (
            "The constant is DwellRefused-free and lives in this package; a workspace "
            "override is a settings row, not a migration."
        ),
        "rejected": "Inferring a 'serious read' from a single long page, which is not evidence.",
    },
    {
        "id": "do_not_rebuild_the_gate",
        "decision": "The password and email-verification gate stays WF-069's.",
        "sourced": True,
        "research_says": (
            "The features_tools field lists a 'link-level password and email-verification "
            "gate'. The issue quotes that line and asks the implementer to check the "
            "existing link-gating feature before building a second gate."
        ),
        "why": (
            "dsr.link_gating is a merged workflow whose whole subject is that gate: password "
            "hashing, one-time codes, email verification, the expiry boundary and revocation. "
            "A second gate here would be a second definition of 'verified buyer'."
        ),
        "change_it_by": (
            "This workflow reuses rules.expiry_state and rules.normalize_email, and a link "
            "stores the verified address the same way WF-069 does."
        ),
        "rejected": "A private password and OTP implementation inside this feature.",
    },
    {
        "id": "no_object_storage_and_no_pdf_renderer",
        "decision": (
            "The link distributes a WF-018 library asset. No object store and no PDF "
            "renderer are added."
        ),
        "sourced": False,
        "research_says": (
            "apis_hit names 'Object storage (S3 / R2 / Vercel Blob)' and 'PDF.js for "
            "client-side rendering', and the issue asks the implementer to check whether "
            "this product already has a document byte store."
        ),
        "why": (
            "The product has a library of content assets (WF-018) and a room document model, "
            "but no byte store, and inventing one would be a second source of truth for "
            "documents. The link therefore references an asset id and stores page numbers "
            "and durations. The viewer is out of scope and is not simulated."
        ),
        "change_it_by": (
            "A deployment with object storage points the link's asset_id at its own "
            "document record and emits the same view payload."
        ),
        "rejected": "Storing document bytes in records.data, which the schema-flexible store forbids.",
    },
    {
        "id": "no_email_transport",
        "decision": "An alert is a record. No mail is sent.",
        "sourced": False,
        "research_says": (
            "apis_hit names 'email (Resend)' for the per-viewer alert, and the issue asks "
            "the implementer to check whether the app has a mail sender."
        ),
        "why": (
            "The product has no mail transport. WF-097 and WF-069 both hit the same wall and "
            "both recorded the dispatch instead of claiming a send; this workflow does the "
            "same and names the missing capability in the page."
        ),
        "change_it_by": (
            "An alert already carries the trigger, the viewer and the link, so a deployment "
            "adds one callable over the alert rows."
        ),
        "rejected": "A vendor-specific Resend client with no credential to configure it.",
    },
    {
        "id": "privacy_boundary_is_enforced",
        "decision": "A view payload carrying a document body or a raw form value is refused by name.",
        "sourced": True,
        "research_says": (
            "The extensibility field states that DeckLens 'explicitly does not transmit "
            "document bodies or raw form values', and the issue calls that a stated "
            "requirement rather than an accident."
        ),
        "why": (
            "A promise in a docstring is not a boundary. Refusing the keys by name makes the "
            "ingest unable to accept a body even if a caller sends one."
        ),
        "change_it_by": "REFUSED_PAYLOAD_KEYS, in vocabulary.py. Adding a key there is one line.",
        "rejected": "Storing the body and promising not to read it.",
    },
    {
        "id": "crm_write_back_is_delegated",
        "decision": (
            "The engagement record is handed to WF-037's EngagementSync, carrying WF-042's "
            "'deal' object name and the room's opportunity id."
        ),
        "sourced": True,
        "research_says": (
            "The user flow ends 'the engagement record is written back to the CRM "
            "opportunity', and dependencies name WF-042 as what provisions the room's CRM "
            "deal, account and contact records."
        ),
        "why": (
            "WF-037 already owns the two-stage write (room row first, CRM create enqueued), "
            "the vendor create surfaces and the retry ladder. A second write path would be a "
            "second definition of 'synced'. This workflow supplies the event and the "
            "opportunity; WF-042 supplies the identity that resolves the buyer."
        ),
        "change_it_by": (
            "Register a connector and a field map for the event type "
            f"{vocab.ENGAGEMENT_EVENT_TYPE!r}; the queue then drains to it."
        ),
        "rejected": "A private CRM client with its own retry and status vocabulary.",
    },
    {
        "id": "revisit_rule_is_wf018s",
        "decision": (
            "A revisit is a gap of more than "
            f"{vocab.REVISIT_GAP_SECONDS} seconds in one viewer's page timings."
        ),
        "sourced": True,
        "research_says": (
            "The data flow names 'session' per page event, and the reading of 'which "
            "revisit' is the same question WF-018 answers with its session gap."
        ),
        "why": "One definition of a reading, shared with the workflow that already computes dwell.",
        "change_it_by": "It is WF-018's SESSION_GAP_SECONDS, imported, not copied.",
        "rejected": "A second session-gap constant that could drift from WF-018's.",
    },
    {
        "id": "internal_views_are_recorded_but_do_not_alert",
        "decision": "An internal viewer's view is stored and raises no rep alert.",
        "sourced": True,
        "research_says": (
            "WF-018 states the rule this workflow inherits: engagement analytics count "
            "external users, and a share is the one internal exception."
        ),
        "why": (
            "A rep previewing their own tracked link is not a buyer signal, and an alert "
            "from it would train the rep to ignore alerts."
        ),
        "change_it_by": "The audience travels on every row; a different rule reads the same field.",
        "rejected": "Dropping internal views entirely, which would lose the record.",
    },
)


def describe() -> dict[str, Any]:
    """The register, as the payload ``GET /api/wf-121/inferences`` serves."""
    return {
        "ticket": "WF-121",
        "spec": "docs/research/digital-sales-room-workflows/wf/WF-121.md",
        "inferences": [dict(entry) for entry in INFERENCES],
        "count": len(INFERENCES),
    }


__all__ = ["INFERENCES", "describe"]
