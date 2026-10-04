"""One error hierarchy for the anonymous-visitor identification package.

Every refusal this package makes is the caller's to fix, so the types share a base
and the feature module registers a single handler for it. Anything that is *not*
a :class:`VisitorIdentificationError` is a bug and must propagate.

``code`` and ``status`` ride on the exception rather than being decided inside the
handler, because "the page definition carries a domain" and "that company is not
in the table" are both this package's errors and only one of them conflicts with
state that already exists. A handler that answered 400 for both would be lying
about the second. FastAPI only accepts exception handlers on the app object, so
the feature module exports this mapping as ``EXCEPTION_HANDLERS``; two features
may not map the same type, which is why the whole hierarchy hangs off one base.

Each class names the sentence in
``docs/research/digital-sales-room-workflows/wf/WF-031.md`` it implements, so a
reviewer can check the behaviour against the specification without reading the
handler.
"""

from __future__ import annotations


class VisitorIdentificationError(ValueError):
    """An anonymous-visit identification request cannot be honoured as written.

    A ``ValueError`` because every subclass is caused by something the caller
    sent. Nothing in this package raises for a fault of its own.
    """

    code = "visitor_identification_error"
    status = 400


# --------------------------------------------------------------------------- #
# The capture
# --------------------------------------------------------------------------- #


class InvalidCapture(VisitorIdentificationError):
    """A captured request cannot be read.

    Raised for a missing path, a missing client id, a capture that carries
    neither an IP address nor a network, and a path that is not a URL path. The
    last of those matters most: the research says the tracking script reads
    "the IP address, the country, the network, and other publicly available
    parameters", so everything it sends is about a network, and a value that is
    not a path is not something this workflow can attribute.
    """

    code = "invalid_capture"
    status = 422


class UnknownCaptureParameter(VisitorIdentificationError):
    """The capture carries a parameter the research does not name.

    "We check the IP address, the country, the network, and other publicly
    available parameters to stay GDPR compliant." Three parameters are named and
    a fourth is not enumerated, so the list is closed at the three rather than
    open-ended. An open list is not a more flexible capture, it is a capture
    that quietly accepts and stores whatever a snippet is configured to send.
    """

    code = "unknown_capture_parameter"
    status = 422


class PersonalDataRefused(VisitorIdentificationError):
    """The capture asks for an individual to be identified.

    "Albacross focuses on exclusively company-level identification rather than
    tracking individual users, ensuring respect for user privacy." A parameter
    whose whole purpose is to name a person is refused by name, with its own
    error, so the refusal is legible from the response instead of hiding inside a
    generic "unknown parameter".
    """

    code = "personal_data_refused"
    status = 422


# --------------------------------------------------------------------------- #
# Intent pages
# --------------------------------------------------------------------------- #


class InvalidPageDefinition(VisitorIdentificationError):
    """An intent page is not usable.

    "Give the page a name and input the URL." A page with no name or no path is
    an unfinished one rather than a broad one, so it is refused rather than
    stored: a page that matches every path would put every company in the lead
    list, which is the opposite of what the Pages filter is for.
    """

    code = "invalid_page_definition"
    status = 422


class PathCarriesADomain(VisitorIdentificationError):
    """The page definition carries a domain instead of a path.

    "When you type in the web page URL do not include the domain." A definition
    of ``https://acme.example/pricing`` matches nothing in this workflow, because
    a capture records a path and no domain, so the page would sit in the Pages
    list looking configured and never match a visit. Refused at the door instead.
    """

    code = "path_carries_a_domain"
    status = 422


class UnknownMatchCondition(VisitorIdentificationError):
    """The match condition is not one of the three the research names.

    "you can select which condition should be followed: Exact ... Contains ...
    Starts with". A fourth is not a more flexible grammar, it is a filter nobody
    can predict, and the three are published at ``GET /api/wf-031/vocabulary`` so
    a picker renders from the server rather than from a list compiled into a page.
    """

    code = "unknown_match_condition"
    status = 422


# --------------------------------------------------------------------------- #
# Ideal customer profile
# --------------------------------------------------------------------------- #


class InvalidIcp(VisitorIdentificationError):
    """A saved ideal customer profile names no criterion.

    "combine with Segment filters, tags, and the ICP." The ICP is a filter on the
    lead list, and a filter whose criteria are empty includes every company, which
    is a control that looks like it narrows something and does not.
    """

    code = "invalid_icp"
    status = 422


# --------------------------------------------------------------------------- #
# Company detail
# --------------------------------------------------------------------------- #


class InvalidCompanyDetail(VisitorIdentificationError):
    """A company detail cannot be read.

    "The insights provided include the company's name, website, address, size,
    and a list of employees or contacts associated with the company." A name or a
    size that is present but empty, a website that is not a URL, or a contact that
    is not a list of people is refused, because each of those renders as a blank
    cell in the drill-down the research describes.
    """

    code = "invalid_company_detail"
    status = 422


class UnknownLeadFilter(VisitorIdentificationError):
    """A lead-list filter names something this workflow does not publish.

    The lead list filters on pages, segment, tags, the ICP, country and size, and
    those are the only filters the research names. A seventh would be a control
    whose predicate nobody has agreed.
    """

    code = "unknown_lead_filter"
    status = 422


# --------------------------------------------------------------------------- #
# Records that are not there
# --------------------------------------------------------------------------- #


class UnknownInstallation(VisitorIdentificationError):
    """No tracking snippet is installed under that client id.

    Step 1 of the flow: "Install the Albacross tracking snippet on the site and
    note the Client ID." A capture from a client id nobody registered is a
    snippet that was never installed, or one whose id was rotated, and storing it
    would attribute a company's traffic to a client that does not exist.
    """

    code = "unknown_installation"
    status = 404


class InvalidInstallation(VisitorIdentificationError):
    """An installation cannot be recorded.

    A client id is required, because it is the only thing a capture carries back
    to the Pages list it belongs to. Re-installing the same client id is not one
    of these: it updates the site and answers 200.
    """

    code = "invalid_installation"
    status = 422


class CompanyAlreadyIdentified(VisitorIdentificationError):
    """A company with that key is already in the identified table.

    409, and not a silent update: adding a company by hand is a statement that it
    is in market, and overwriting the record that a capture created would throw
    away the evidence for it.
    """

    code = "company_already_identified"
    status = 409


class UnknownPage(VisitorIdentificationError):
    """No intent page has that id.

    404, and not an empty result: the caller named a specific page, so silently
    returning every company would answer a different question.
    """

    code = "unknown_page"
    status = 404


class UnknownIcp(VisitorIdentificationError):
    """No saved ideal customer profile has that id."""

    code = "unknown_icp"
    status = 404


class UnknownCompany(VisitorIdentificationError):
    """No company has that key in the identified table.

    A company is created by a capture or added by hand, so an absent key is a
    caller asking about a company this portal has never identified.
    """

    code = "unknown_company"
    status = 404


class CompanyKeyRequired(VisitorIdentificationError):
    """A company added by hand carried no key.

    A capture derives its company key from the network. A hand-added company has
    no network behind it, so the seller has to name the key themselves - and a
    company with no key cannot be filtered, ranked or linked to.
    """

    code = "company_key_required"
    status = 422
