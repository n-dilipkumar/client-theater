"""Every judgement call this workflow rests on, named, and served.

The research is a vendor's help article read closely. It is exact about what a
seller sees - the five company fields, the three match conditions, the rule that
a page definition carries a path and not a URL, and the three public parameters
the script reads - and silent about everything underneath: what identifies a
company, how the lead list is ranked, what a country code looks like, and where
the boundary of this workflow ends.

Those edges are where a build goes wrong quietly, so each one is written down
here with the reading taken, the reason, and what would change it. They are served
over HTTP by ``GET /api/wf-031/inferences``, because the point of the endpoint is
to show where the line falls rather than to have a reader reconstruct it from a
diff.
"""

from __future__ import annotations

from typing import Any

#: Each entry: the decision, the reading taken, why, what would reverse it, and
#: the risk of having got it wrong. ``inference: True`` on all of them, which is
#: the point.
INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "room_scope",
        "question": "Is a company, a page definition or an installation tied to a room?",
        "reading": (
            "No. The prefix is /api/wf-031 and nothing under it is room-scoped. A tracking "
            "installation is a property of a website, a page definition is a property of the "
            "account whose Pages list holds it, and an identified company is a property of the "
            "portal."
        ),
        "why": (
            "The research has no room anywhere in it. A website belongs to no room, and scoping "
            "an installation to one would mean the same site was installed twice in two rooms and "
            "that traffic from a buyer's own browser was attributed to whichever room the visitor "
            "happened to be reading."
        ),
        "change": "Nest the routes under /rooms/{room_id} in the feature module.",
        "risk": (
            "A portal that serves several sellers shares one identified-company table. The client "
            "id separates the Pages lists, and company keys are network-derived, so two sellers "
            "behind one network would share a company record."
        ),
    },
    {
        "id": "capture_parameter_list",
        "question": "What are the capture parameters, given 'the IP address, the country, the "
        "network, and other publicly available parameters'?",
        "reading": (
            "Exactly three: the IP address, the country, and the network. The list is closed, and "
            "any other key on a capture is refused."
        ),
        "why": (
            "Three are named and the fourth clause is not enumerated. An open list would be a "
            "capture that accepts and stores whatever a snippet is configured to send, which is "
            "how a person-level field gets into a company-level table one integration at a time. "
            "Closing the list is the only way the privacy stance is a property of the code rather "
            "than of every caller's configuration."
        ),
        "change": "Add the key to ALLOWED_CAPTURE_KEYS in dsr/visitor_identification/vocabulary.py.",
        "risk": (
            "A snippet that sends a User-Agent header will be refused until someone names it. That "
            "is the refusal working, not a gap."
        ),
    },
    {
        "id": "what_identifies_a_company",
        "question": "What turns an anonymous request into a company?",
        "reading": (
            "The network, and an exact address already on file before the network. The company key "
            "is derived from the network, or from the address when the capture carries only one. A "
            "capture that matches neither creates a new company."
        ),
        "why": (
            "The data flow says the script reads 'IP address, country, network and other public "
            "parameters' and that the request is 'matched against the company database'. The "
            "address is the narrowest fact held and the network the coarsest, so an address we "
            "have already attributed is better evidence than a network we have not. Both are "
            "network-level facts: neither resolves to a person, which is the property the whole "
            "workflow rests on."
        ),
        "change": "find_company and derive_company_key in dsr/visitor_identification/company.py.",
        "risk": (
            "Two companies behind one network are one company record. A shared office or a mobile "
            "carrier produces this. The alternative - refusing to identify - loses the vendor's "
            "primary capability, and the record carries every address seen so a seller can see the "
            "mix and split it by hand."
        ),
    },
    {
        "id": "no_merging_on_a_guess",
        "question": "Should a capture from an address inside a known network join the company that "
        "network belongs to?",
        "reading": "No. A network match resolves only on the network. An address that has never been seen is not merged into a network's company.",
        "why": (
            "Merging would make 'companies in market' a guess. Two captures from the same network "
            "and no shared address are two pieces of evidence about a network, not one company, and "
            "the researched privacy stance is about not over-claiming what a network implies."
        ),
        "change": "find_company in dsr/visitor_identification/company.py.",
        "risk": (
            "A large portal's lead table fills with one company per network per client. That is "
            "visible in the table rather than silent, and the page list still finds the right "
            "companies."
        ),
    },
    {
        "id": "company_key_shape",
        "question": "What is a company key, and what goes in a URL with it?",
        "reading": (
            "A lower-cased, URL-safe fold of the network or address, with a short digest of the "
            "raw value appended whenever a character had to be folded. 203.0.113.0/24 becomes "
            "203.0.113.0-24-1f3c9a."
        ),
        "why": (
            "A CIDR block carries a slash, and a key that has to be escaped in every route is a "
            "key that eventually is not escaped in one of them. The digest is there because the "
            "fold is lossy: a/b and a-b both fold to a-b, and a key that named the wrong network "
            "would attribute one company's traffic to another."
        ),
        "change": "slug and keyed in dsr/visitor_identification/company.py.",
        "risk": (
            "Keys are long for a network. They are read in audit rows and URLs, never typed, so "
            "the length costs nothing a seller sees."
        ),
    },
    {
        "id": "company_record_shape",
        "question": "What does an identified company record hold?",
        "reading": (
            "The five researched fields, present and possibly empty: name, website, address, size, "
            "contacts. Alongside them the counters the lead list ranks on (page_views, "
            "first_seen_at, last_visit_at), the two filter attributes the research names (segment, "
            "tags), the public parameters seen on the network (countries, known_ips, "
            "known_networks), and the distinct paths visited."
        ),
        "why": (
            "The sentence is 'The insights provided include the company's name, website, address, "
            "size, and a list of employees or contacts associated with the company.' A record with "
            "those keys absent renders as five indistinguishable blanks, so they are present and "
            "empty instead: an unidentified company is a normal state here, not a broken one. No "
            "migration and no typed column, because all of it is ordinary JSON in records.data."
        ),
        "change": "blank_company and detail_of in dsr/visitor_identification/company.py.",
        "risk": (
            "A team that wants a field of its own adds it to records.data and filters on it. This "
            "workflow does not need to know about it, which is the property the store is built "
            "for."
        ),
    },
    {
        "id": "contacts_carry_no_email",
        "question": "What does one entry in the contact list hold?",
        "reading": "A name, and optionally a role. Nothing else, and in particular no email address.",
        "why": (
            "The research says 'a list of employees or contacts associated with the company' and "
            "names no address for a contact. The role is named once, in the auto-engage target "
            "group: 'Specify your target group by selecting: Role, Industry, Location'. An email "
            "address would be the first person-level field in the package."
        ),
        "change": "CONTACT_FIELDS in dsr/visitor_identification/vocabulary.py.",
        "risk": "A seller who wants to email a contact cannot from here. That is a CRM job.",
    },
    {
        "id": "path_normalisation",
        "question": "How is a page path, and a captured path, normalised before matching?",
        "reading": (
            "A scheme or a protocol-relative prefix is a domain and is refused. A first segment "
            "shaped like a host is a domain and is refused. What is left must start with a slash. "
            "The query string and the fragment are dropped, repeated slashes collapse, and a "
            "trailing slash is dropped. Case is not folded."
        ),
        "why": (
            "Only the first two are sourced: 'When you type in the web page URL do not include the "
            "domain.' The rest are readings. A page definition is the identity of a page, and "
            "neither a query string nor a fragment varies which page was served, so comparing them "
            "would make a page definition that a seller typed correctly fail to match the visits "
            "that page actually received. Case is not folded because a URL path is case-sensitive "
            "and the research never says otherwise."
        ),
        "change": "normalise_path in dsr/visitor_identification/paths.py.",
        "risk": (
            "A site whose first path segment contains a full stop and an alphabetic label - "
            "/v1.beta/pricing - is refused as a domain. The error message names the fix."
        ),
    },
    {
        "id": "matches_are_computed",
        "question": "Is a page match stored on the visit, or computed when the list is read?",
        "reading": (
            "Computed. A company carries the distinct paths it has visited, and the Pages filter "
            "evaluates each page's condition against them at read time."
        ),
        "why": (
            "A page defined today must put a company that visited the path last month into the "
            "filter, and a page removed yesterday must take it out again. A stored answer is wrong "
            "in both cases until somebody runs a sweep."
        ),
        "change": "matched_page_ids in dsr/visitor_identification/leads.py.",
        "risk": (
            "The list costs a page count times a path count per company. At this workflow's scale "
            "that is nothing, and the change that would fix it is a materialised match table."
        ),
    },
    {
        "id": "ranking_keys",
        "question": "What ranks the lead list, given only 'a ranked list of in-market companies'?",
        "reading": (
            "Page views descending, then last visit descending, then company key ascending. The "
            "third term makes the order total."
        ),
        "why": (
            "The research names no sort key. Page views first is the measure of interest the "
            "workflow exists to surface, and the company key last is not decoration: without it two "
            "companies with the same count and the same last visit have no defined order, and a "
            "table that reshuffles between two requests over the same data cannot be read."
        ),
        "change": "The three sort passes in build_rows in dsr/visitor_identification/leads.py.",
        "risk": (
            "A seller who wants recency first gets the opposite order. The lead list serves its "
            "ranking order in every response, so the page can show it rather than guess."
        ),
    },
    {
        "id": "country_is_stored_as_sent",
        "question": "What shape is the country, given the research names only 'the country'?",
        "reading": (
            "Stored trimmed, uppercased when it is two characters, and compared case-insensitively "
            "by the lead-list filter. No code table is enforced."
        ),
        "why": (
            "The research names the parameter and no format. Enforcing ISO 3166 would refuse a "
            "snippet that sends what it has, and the refusal would be about a spelling rather than "
            "about identification."
        ),
        "change": "with_capture in dsr/visitor_identification/company.py.",
        "risk": (
            "A country filter is a text match, so 'UK' and 'GB' are different countries here. The "
            "filter is exact on purpose, so a seller can see what is actually on the record."
        ),
    },
    {
        "id": "timestamp_needs_an_offset",
        "question": "Must a capture carry its own timestamp with a timezone?",
        "reading": (
            "Yes when one is sent. A timestamp with no offset is refused; a capture with none uses "
            "the server's clock."
        ),
        "why": (
            "The lead list ranks on last visit and the country filter reads a country per network, "
            "so both depend on where a capture sits in time. A moment with no zone has no "
            "defensible place in that order, and assuming UTC because it is the server's zone "
            "would move the visit rather than place it."
        ),
        "change": "_parse_moment in dsr/visitor_identification/capture.py.",
        "risk": (
            "A snippet sending local time without an offset is refused. The message says what to "
            "send."
        ),
    },
    {
        "id": "the_page_filter_is_any_of",
        "question": "Does naming two pages in the Pages filter mean both or either?",
        "reading": "Either. A company appears if it satisfied at least one of the named pages.",
        "why": (
            "'apply the Pages filter to isolate companies that visited those pages' - the filter "
            "selects companies that visited those pages, so the selection is over the set the "
            "seller named. Tags are the opposite, and are documented as such on the route: a seller "
            "adding two tags means both."
        ),
        "change": "_passes in dsr/visitor_identification/leads.py.",
        "risk": "A seller expecting both gets a wider list. The response echoes the filter set.",
    },
    {
        "id": "icp_is_size_and_country",
        "question": "What can a saved ideal customer profile be about?",
        "reading": "Company sizes and countries. At least one is required. Within one field the values are alternatives; across fields every field must hold.",
        "why": (
            "The research names the ICP as a lead-list filter and never says what it is a filter "
            "on. Of the company facts this workflow holds, 'size' is one of the five named fields "
            "and 'country' is one of the three named capture parameters. Role and Industry are "
            "named, but in the auto-engage campaign selector, which is a different surface and is "
            "not built here."
        ),
        "change": "ICP_FIELDS and parse_icp in dsr/visitor_identification/leads.py.",
        "risk": (
            "A profile naming sizes and countries narrows rather than widens. A profile naming "
            "three sizes covers a band, which is what naming three sizes reads as."
        ),
    },
    {
        "id": "where_this_workflow_stops",
        "question": "Which researched surfaces are deliberately not built here?",
        "reading": (
            "Two, and both are named in the record at "
            "GET /api/wf-031/vocabulary -> downstream_surfaces. Workflows and Webhooks is section 17 "
            "of the same research file and has its own feature. Auto-engage is a campaign builder "
            "that targets Role, Industry and Location, generates a sequence and sends it."
        ),
        "why": (
            "The user flow has six steps and ends at the company drill-down. The automation note "
            "describes what a downstream tool does with the company list this workflow produces, "
            "not a step in it. '#17' in the research is a section number in the same file, not a "
            "ticket reference."
        ),
        "change": "Build either in its own feature module on its own prefix.",
        "risk": (
            "None to correctness. A reviewer expecting a webhook here is reading the automation "
            "note as a requirement rather than as a pointer."
        ),
    },
    {
        "id": "contacts_are_not_identities",
        "question": "Is the contact list a set of identified people?",
        "reading": (
            "No. A contact is a name and a role attached to a company, and nothing here identifies "
            "the person behind the request. No capture can create one: the capture vocabulary is "
            "closed and every person-level key is refused by name."
        ),
        "why": (
            "'exclusively company-level identification rather than tracking individual users' is a "
            "statement about the whole pipeline, and a contact list is the obvious place for it to "
            "leak. The research asks for the contact list because it is useful, not because the "
            "workflow tracked anyone."
        ),
        "change": "PERSONAL_PARAMETER_NAMES in dsr/visitor_identification/vocabulary.py.",
        "risk": "None. The refusal is the feature.",
    },
)


def describe() -> dict[str, Any]:
    """Every judgement call, served. See ``GET /api/wf-031/inferences``."""
    return {
        "count": len(INFERENCES),
        "inferences": [dict(entry) for entry in INFERENCES],
    }
