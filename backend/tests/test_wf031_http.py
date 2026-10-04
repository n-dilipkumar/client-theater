"""WF-031: the HTTP surface of identifying anonymous web visitors as companies.

What is different from ``test_wf031.py``
----------------------------------------

The domain tests build the engine and call it directly. This file goes through the
router, so it is where four things are only observable:

* **The error mapping.** One handler is registered for the whole
  ``VisitorIdentificationError`` hierarchy, and it answers with the status and code
  the exception carries. Each researched refusal is answered with the status that
  matches what it is - a page definition carrying a domain is 422, an uninstalled
  client id is 404, a company key already taken is 409.
* **The audit source.** Every write a route makes passes ``router.prefix``, so the
  audit row names the route that served it. Asserted against the routes the host
  actually mounted, which is the check the brief calls for by name.
* **The query-string surface.** A repeated key is a list, and the filters arrive as
  query parameters rather than as a dictionary.
* **The refusals that are correct.** ``verify_all_routes.py`` calls every route with
  no parameters at all, so every write route here is called empty and has to answer
  4xx rather than 5xx.

The privacy stance is asserted here too, once, through HTTP: a capture over the
wire leaves a company record and no person record.
"""

from __future__ import annotations

import dsr.features as host
import pytest
from dsr.features import load_feature

MODULE = "wf031_identify_anonymous_web_visitors_as_com"
FEATURE_ID = "wf-031-identify-anonymous-web-visitors-as-com"
PREFIX = "/api/wf-031"
CLIENT = "cli-acme"
SOURCE = "test"


@pytest.fixture
def wired(client):
    """A client with the snippet installed, so a capture has a Pages list to join."""
    response = client.post(
        f"{PREFIX}/installations", json={"client_id": CLIENT, "site": "https://acme.example"}
    )
    assert response.status_code == 201, response.text
    return client


def capture(client, **overrides):
    body = {
        "client_id": CLIENT,
        "path": "/pricing",
        "network": "203.0.113.0/24",
        "ip_address": "203.0.113.11",
        "country": "GB",
    }
    body.update(overrides)
    return client.post(f"{PREFIX}/captures", json=body)


def page(client, name, path, condition="Exact", **extra):
    response = client.post(
        f"{PREFIX}/pages",
        json={"name": name, "path": path, "condition": condition, **extra},
        params={"client_id": CLIENT},
    )
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #


def test_the_registry_lists_this_feature_with_its_prefix_and_routes(client):
    listing = client.get("/api/features").json()
    record = next(f for f in listing["features"] if f["id"] == FEATURE_ID)
    assert record["ticket"] == "WF-031"
    assert record["prefix"] == PREFIX
    assert len(record["routes"]) == 18
    assert record["exception_handlers"] == ["VisitorIdentificationError"]


def test_no_feature_failed_to_load(client):
    """A feature that raises on import is skipped rather than fatal, which is why
    the failure has to be asserted rather than assumed."""
    listing = client.get("/api/features").json()
    assert listing["failed_count"] == 0, [f["id"] for f in listing["failed"]]


def test_the_feature_lookup_route_answers(client):
    assert client.get(f"/api/features/{FEATURE_ID}").json()["prefix"] == PREFIX


def test_the_core_health_route_still_answers(client):
    assert client.get("/api/health").status_code == 200


# --------------------------------------------------------------------------- #
# Every route answers, and none of them 5xx
# --------------------------------------------------------------------------- #

#: Every route, with the method that answers it. Called with no parameters at all,
#: which is what ``tools/verify_all_routes.py`` does, so a 4xx is the correct
#: answer for the write routes and a 200 for the reads.
ALL_ROUTES: tuple[tuple[str, str], ...] = (
    ("GET", "/vocabulary"),
    ("GET", "/inferences"),
    ("GET", "/installations"),
    ("GET", "/pages"),
    ("GET", "/icps"),
    ("GET", "/companies"),
    ("GET", "/companies/absent"),
    ("GET", "/companies/absent/visits"),
    ("GET", "/companies/absent/pages"),
    ("POST", "/installations"),
    ("POST", "/captures"),
    ("POST", "/pages"),
    ("POST", "/icps"),
    ("POST", "/companies"),
    ("PATCH", "/companies/absent"),
    ("PATCH", "/pages/absent"),
    ("DELETE", "/pages/absent"),
    ("DELETE", "/icps/absent"),
)


@pytest.mark.parametrize("method,path", ALL_ROUTES, ids=[f"{m} {p}" for m, p in ALL_ROUTES])
def test_every_route_answers_with_no_5xx(client, method, path):
    response = client.request(method, f"{PREFIX}{path}", json={})
    assert response.status_code < 500, f"{method} {path} -> {response.status_code} {response.text}"


def test_every_route_this_feature_advertises_is_called_by_this_file():
    """The list above has to keep up with the router, or the 5xx sweep is theatre."""
    advertised = set()
    for route in host.REGISTRY.by_id(FEATURE_ID).routes:
        tail = route["path"][len(PREFIX) :]
        for placeholder in ("{company_key}", "{page_id}", "{icp_id}"):
            tail = tail.replace(placeholder, "absent")
        for method in route["methods"]:
            advertised.add((method, tail))
    assert set(ALL_ROUTES) == advertised


# --------------------------------------------------------------------------- #
# The published vocabulary and the inferences
# --------------------------------------------------------------------------- #


def test_the_vocabulary_route_publishes_the_three_conditions(client):
    body = client.get(f"{PREFIX}/vocabulary").json()
    assert [c["name"] for c in body["match_conditions"]] == ["exact", "contains", "starts_with"]
    assert [c["label"] for c in body["match_conditions"]] == ["Exact", "Contains", "Starts with"]
    assert [p["name"] for p in body["capture_parameters"]] == [
        "ip_address",
        "country",
        "network",
    ]
    assert body["company_fields"] == ["name", "website", "address", "size", "contacts"]
    assert body["company_level_only"] is True


def test_the_vocabulary_route_names_the_two_downstream_surfaces_it_does_not_build(client):
    """ "#17" is a section of the research file, not a ticket reference."""
    body = client.get(f"{PREFIX}/vocabulary").json()
    assert [s["surface"] for s in body["downstream_surfaces"]] == ["Workflows", "Auto-engage"]


def test_the_inferences_route_serves_every_decision_with_a_change(client):
    body = client.get(f"{PREFIX}/inferences").json()
    assert body["count"] == len(body["inferences"])
    assert body["count"] >= 10
    for entry in body["inferences"]:
        assert {"id", "question", "reading", "why", "change", "risk"} <= set(entry), entry


# --------------------------------------------------------------------------- #
# Installations
# --------------------------------------------------------------------------- #


def test_an_installation_is_created_then_read_back(client):
    created = client.post(
        f"{PREFIX}/installations", json={"client_id": "cli-x", "site": "https://x.example"}
    )
    assert created.status_code == 201
    assert created.json()["created"] is True
    listed = client.get(f"{PREFIX}/installations").json()
    assert [i["client_id"] for i in listed["installations"]] == ["cli-x"]
    assert listed["count"] == 1


def test_reinstalling_the_same_client_id_answers_200_and_does_not_duplicate(client):
    client.post(f"{PREFIX}/installations", json={"client_id": "cli-x", "site": "https://a.example"})
    again = client.post(
        f"{PREFIX}/installations", json={"client_id": "cli-x", "site": "https://b.example"}
    )
    assert again.status_code == 201
    assert again.json()["created"] is False
    assert client.get(f"{PREFIX}/installations").json()["count"] == 1


def test_an_installation_without_a_client_id_is_422(client):
    response = client.post(f"{PREFIX}/installations", json={"site": "https://x.example"})
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_installation"


# --------------------------------------------------------------------------- #
# The capture
# --------------------------------------------------------------------------- #


def test_a_capture_over_http_creates_a_company_and_answers_201(wired):
    response = capture(wired)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["company_created"] is True
    assert body["identified"] == "company"
    assert body["company_level_only"] is True
    assert body["company"]["page_views"] == 1


def test_a_capture_leaves_a_company_record_and_no_person_record(wired):
    """The researched stance, proved through the wire rather than through the engine.

    "a **company-level** record is created (never an individual user)". One capture,
    then every collection the run holds is listed. Nothing anywhere holds a second
    row that could be a person.
    """
    capture(wired)
    collections = client_collections(wired)
    assert collections["identified_company"] == 1
    assert collections["company_page_visit"] == 1
    assert set(collections) == {
        "identified_company",
        "company_page_visit",
        "tracking_installation",
    }


def client_collections(client) -> dict[str, int]:
    from dsr.api import app

    with app.state.db._lock:  # noqa: SLF001 - reading the host's own view of the data
        rows = app.state.db._conn.execute(  # noqa: SLF001
            "SELECT collection, COUNT(*) AS live FROM records"
            " WHERE deleted_at IS NULL GROUP BY collection"
        ).fetchall()
    return {row["collection"]: int(row["live"]) for row in rows}


@pytest.mark.parametrize("key", ["visitor_id", "user_id", "email", "cookie", "person_id"])
def test_a_capture_naming_a_person_is_refused_by_name(wired, key):
    response = capture(wired, **{key: "anything"})
    assert response.status_code == 422
    assert response.json()["error"] == "personal_data_refused"


@pytest.mark.parametrize("key", ["user_agent", "referrer", "utm_source", "screen_size"])
def test_a_capture_parameter_the_research_does_not_name_is_refused(wired, key):
    response = capture(wired, **{key: "x"})
    assert response.status_code == 422
    assert response.json()["error"] == "unknown_capture_parameter"


def test_a_capture_from_an_uninstalled_client_is_404(client):
    response = capture(client, client_id="cli-never")
    assert response.status_code == 404
    assert response.json()["error"] == "unknown_installation"


def test_an_empty_capture_is_422_and_not_500(client):
    response = client.post(f"{PREFIX}/captures", json={})
    assert response.status_code == 422
    assert response.json()["status"] == 422


def test_a_capture_without_a_network_or_an_address_is_422(wired):
    response = wired.post(f"{PREFIX}/captures", json={"client_id": CLIENT, "path": "/pricing"})
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_capture"


def test_a_capture_with_a_naive_moment_is_422(wired):
    response = capture(wired, captured_at="2026-09-01T06:30:00")
    assert response.status_code == 422
    assert "no timezone offset" in response.json()["detail"]


def test_the_second_capture_from_one_network_reuses_the_company(wired):
    first = capture(wired).json()
    second = capture(wired, ip_address="203.0.113.12").json()
    assert second["company_created"] is False
    assert second["company_key"] == first["company_key"]
    assert second["company"]["page_views"] == 2


def test_a_capture_response_names_the_pages_the_path_satisfied(wired):
    article = page(wired, "Read the article", "/newsroom/article", "Starts with")
    body = capture(wired, path="/newsroom/article/2017").json()
    assert body["matches"] is True
    assert body["matched_page_ids"] == [article["id"]]
    assert body["matched_pages"][0]["condition_label"] == "Starts with"


def test_a_capture_response_says_no_when_the_path_satisfied_nothing(wired):
    page(wired, "Read the article", "/newsroom/article", "Exact")
    body = capture(wired, path="/about").json()
    assert body["matches"] is False
    assert body["matched_pages"] == []


# --------------------------------------------------------------------------- #
# The Pages list
# --------------------------------------------------------------------------- #


def test_a_page_definition_that_carries_a_domain_is_422(wired):
    """ "When you type in the web page URL do not include the domain" """
    response = wired.post(
        f"{PREFIX}/pages",
        json={"name": "Priced up", "path": "https://acme.example/pricing"},
        params={"client_id": CLIENT},
    )
    assert response.status_code == 422
    assert response.json()["error"] == "path_carries_a_domain"


def test_a_page_definition_with_a_bare_domain_is_422(wired):
    response = wired.post(
        f"{PREFIX}/pages",
        json={"name": "Priced up", "path": "acme.example/pricing"},
        params={"client_id": CLIENT},
    )
    assert response.status_code == 422
    assert response.json()["error"] == "path_carries_a_domain"


def test_a_fourth_match_condition_is_422(wired):
    """ "Exact ... Contains ... Starts with" is the whole enumeration."""
    response = wired.post(
        f"{PREFIX}/pages",
        json={"name": "Priced up", "path": "/pricing", "condition": "Ends with"},
        params={"client_id": CLIENT},
    )
    assert response.status_code == 422
    assert response.json()["error"] == "unknown_match_condition"


def test_a_condition_label_sent_as_words_is_accepted(wired):
    """A picker sends what a human read off a label."""
    response = wired.post(
        f"{PREFIX}/pages",
        json={"name": "Priced up", "path": "/pricing", "condition": "Starts with"},
        params={"client_id": CLIENT},
    )
    assert response.status_code == 201
    assert response.json()["condition"] == "starts_with"
    assert response.json()["condition_label"] == "Starts with"


def test_a_page_definition_without_a_name_is_422(wired):
    response = wired.post(
        f"{PREFIX}/pages", json={"path": "/pricing"}, params={"client_id": CLIENT}
    )
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_page_definition"


def test_the_pages_list_is_scoped_to_its_client(wired):
    page(wired, "Asked for a price", "/pricing", "Exact")
    wired.post(
        f"{PREFIX}/pages",
        json={"name": "Asked for a price", "path": "/pricing", "condition": "Exact"},
        params={"client_id": "cli-other"},
    )
    mine = wired.get(f"{PREFIX}/pages", params={"client_id": CLIENT}).json()
    theirs = wired.get(f"{PREFIX}/pages", params={"client_id": "cli-other"}).json()
    assert mine["count"] == 1
    assert theirs["count"] == 1
    assert mine["pages"][0]["id"] != theirs["pages"][0]["id"]
    assert wired.get(f"{PREFIX}/pages").json()["count"] == 2


def test_a_page_definition_can_be_amended_over_http(wired):
    defined = page(wired, "Priced up", "/pricing", "Exact")
    response = wired.patch(f"{PREFIX}/pages/{defined['id']}", json={"condition": "Contains"})
    assert response.status_code == 200
    assert response.json()["condition"] == "contains"
    assert response.json()["revision"] == defined["revision"] + 1


def test_amending_a_page_to_carry_a_domain_is_422(wired):
    defined = page(wired, "Priced up", "/pricing")
    response = wired.patch(
        f"{PREFIX}/pages/{defined['id']}", json={"path": "https://acme.example/pricing"}
    )
    assert response.status_code == 422
    assert response.json()["error"] == "path_carries_a_domain"


def test_amending_an_absent_page_is_404(wired):
    response = wired.patch(f"{PREFIX}/pages/pg_absent", json={"condition": "Exact"})
    assert response.status_code == 404
    assert response.json()["error"] == "unknown_page"


def test_a_page_can_be_removed_over_http(wired):
    defined = page(wired, "Priced up", "/pricing")
    assert wired.delete(f"{PREFIX}/pages/{defined['id']}").status_code == 200
    assert wired.get(f"{PREFIX}/pages", params={"client_id": CLIENT}).json()["count"] == 0


def test_removing_an_absent_page_is_404(wired):
    response = wired.delete(f"{PREFIX}/pages/pg_absent")
    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# The ICP
# --------------------------------------------------------------------------- #


def test_an_icp_is_saved_read_and_removed_over_http(wired):
    created = wired.post(f"{PREFIX}/icps", json={"name": "Large", "sizes": ["1000+"]})
    assert created.status_code == 201
    profile = created.json()
    assert profile["created"] is True
    assert wired.get(f"{PREFIX}/icps").json()["count"] == 1
    assert wired.delete(f"{PREFIX}/icps/{profile['id']}").status_code == 200
    assert wired.get(f"{PREFIX}/icps").json()["count"] == 0


def test_an_icp_naming_no_criterion_is_422(wired):
    response = wired.post(f"{PREFIX}/icps", json={"name": "Everyone"})
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_icp"


def test_a_lead_list_filter_naming_an_absent_icp_is_404(wired):
    response = wired.get(f"{PREFIX}/companies", params={"icp": "icp_absent"})
    assert response.status_code == 404
    assert response.json()["error"] == "unknown_icp"


def test_removing_an_absent_icp_is_404(wired):
    assert wired.delete(f"{PREFIX}/icps/icp_absent").status_code == 404


# --------------------------------------------------------------------------- #
# The lead list
# --------------------------------------------------------------------------- #


def test_the_lead_list_starts_empty_and_answers_200(wired):
    body = wired.get(f"{PREFIX}/companies").json()
    assert body["companies"] == []
    assert body["summary"]["companies"] == 0


def test_the_pages_filter_narrows_the_lead_list_over_http(wired):
    article = page(wired, "Article", "/newsroom/article", "Exact")
    capture(wired, path="/newsroom/article")
    capture(wired, network="192.0.2.0/24", ip_address="192.0.2.7", path="/about")

    everyone = wired.get(f"{PREFIX}/companies").json()["summary"]["companies"]
    filtered = wired.get(f"{PREFIX}/companies", params={"page": [article["id"]]}).json()
    assert everyone == 2
    assert filtered["summary"]["companies"] == 1
    assert filtered["filters"]["pages"] == [article["id"]]


def test_the_lead_list_carries_a_repeated_page_parameter_as_a_list(wired):
    first = page(wired, "A", "/a", "Exact")
    second = page(wired, "B", "/b", "Exact")
    capture(wired, path="/a")
    capture(wired, network="192.0.2.0/24", ip_address="192.0.2.7", path="/b")
    response = wired.get(
        f"{PREFIX}/companies", params=[("page", first["id"]), ("page", second["id"])]
    )
    assert response.status_code == 200
    assert response.json()["summary"]["companies"] == 2
    assert response.json()["filters"]["pages"] == [first["id"], second["id"]]


def test_the_lead_list_filter_naming_an_absent_page_is_404(wired):
    response = wired.get(f"{PREFIX}/companies", params={"page": "pg_absent"})
    assert response.status_code == 404
    assert response.json()["error"] == "unknown_page"


def test_the_lead_list_is_ranked_and_says_so(wired):
    capture(wired, path="/a")
    capture(wired, path="/b")
    capture(wired, network="192.0.2.0/24", ip_address="192.0.2.7", path="/c")
    body = wired.get(f"{PREFIX}/companies").json()
    assert [row["page_views"] for row in body["companies"]] == [2, 1]
    from dsr.visitor_identification.vocabulary import RANKING

    assert body["filters"]["limit"] == 50
    assert RANKING == ("page_views", "last_visit_at", "company_key")


def test_the_lead_list_limit_is_applied(wired):
    for index in range(4):
        capture(wired, network=f"198.51.100.{index}/32", ip_address=f"198.51.100.{index}")
    assert wired.get(f"{PREFIX}/companies", params={"limit": 2}).json()["summary"]["companies"] == 2


def test_an_out_of_range_limit_is_422(wired):
    assert wired.get(f"{PREFIX}/companies", params={"limit": 9000}).status_code == 422


def test_the_segment_and_tag_filters_work_over_http(wired):
    capture(wired)
    key = capture(wired).json()["company_key"]
    wired.patch(f"{PREFIX}/companies/{key}", json={"segment": "enterprise", "tags": ["a", "b"]})
    assert (
        wired.get(f"{PREFIX}/companies", params={"segment": "enterprise"}).json()["summary"][
            "companies"
        ]
        == 1
    )
    assert (
        wired.get(f"{PREFIX}/companies", params={"tag": ["a", "b"]}).json()["summary"]["companies"]
        == 1
    )
    assert (
        wired.get(f"{PREFIX}/companies", params={"tag": ["a", "c"]}).json()["summary"]["companies"]
        == 0
    )


# --------------------------------------------------------------------------- #
# The company drill-down
# --------------------------------------------------------------------------- #


def test_the_company_drilldown_carries_the_five_researched_fields(wired):
    """ "the company's name, website, address, size, and a list of employees or
    contacts associated with the company" """
    key = capture(wired).json()["company_key"]
    body = wired.get(f"{PREFIX}/companies/{key}").json()
    for field in ("name", "website", "address", "size", "contacts"):
        assert field in body, field
    assert body["contacts"] == []
    assert body["identified_from"] == "capture"
    # Nothing in the drill-down describes a person, which is the whole point of it.
    assert not {
        "visitor",
        "visitor_id",
        "person",
        "person_id",
        "user_id",
        "email",
        "contact_email",
    }.intersection(body)


def test_the_five_fields_can_be_set_over_http(wired):
    key = capture(wired).json()["company_key"]
    response = wired.patch(
        f"{PREFIX}/companies/{key}",
        json={
            "name": "Northwind Traders",
            "website": "https://northwind.example",
            "address": "4 Shipley Lane, Manchester",
            "size": "1000+",
            "contacts": [{"name": "Dana Kelly", "role": "CRO"}],
        },
        params={"actor": "sam"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "Northwind Traders"
    assert body["contacts"] == [{"name": "Dana Kelly", "role": "CRO"}]


def test_a_field_that_is_present_and_blank_is_422_over_http(wired):
    key = capture(wired).json()["company_key"]
    response = wired.patch(f"{PREFIX}/companies/{key}", json={"name": "  "})
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_company_detail"


def test_a_contact_carrying_an_email_address_is_422_over_http(wired):
    key = capture(wired).json()["company_key"]
    response = wired.patch(
        f"{PREFIX}/companies/{key}",
        json={"contacts": [{"name": "Dana", "email": "dana@example"}]},
    )
    assert response.status_code == 422
    assert "email" in response.json()["detail"]


def test_an_absent_company_is_404_on_every_company_route(wired):
    for path in ("", "/visits", "/pages"):
        response = wired.get(f"{PREFIX}/companies/absent{path}")
        assert response.status_code == 404, path
        assert response.json()["error"] == "unknown_company", path
    assert wired.patch(f"{PREFIX}/companies/absent", json={"name": "x"}).status_code == 404


def test_the_visit_drilldown_groups_by_path_over_http(wired):
    key = capture(wired, path="/pricing").json()["company_key"]
    capture(wired, path="/pricing")
    capture(wired, path="/security")
    body = wired.get(f"{PREFIX}/companies/{key}/visits").json()
    assert body["total"] == 3
    # The roll-up is a list of pairs on the wire, because JSON has no tuple.
    assert body["top_paths"][0] == ["/pricing", 2]
    assert dict(tuple(entry) for entry in body["top_paths"])["/security"] == 1
    assert body["visits"][0]["path"] in {"/pricing", "/security"}


def test_the_visit_drilldown_is_bounded_over_http(wired):
    key = capture(wired).json()["company_key"]
    assert wired.get(f"{PREFIX}/companies/{key}/visits", params={"limit": 200}).status_code == 200


def test_the_company_pages_route_names_the_conditions_it_matched(wired):
    article = page(wired, "Article", "/newsroom/article", "Starts with")
    key = capture(wired, path="/newsroom/article").json()["company_key"]
    body = wired.get(f"{PREFIX}/companies/{key}/pages").json()
    assert body["count"] == 1
    assert body["pages"][0]["id"] == article["id"]
    assert body["pages"][0]["condition_label"] == "Starts with"


def test_a_company_can_be_added_by_hand_over_http(wired):
    response = wired.post(
        f"{PREFIX}/companies",
        json={"company_key": "tailwind-and-friends", "name": "Tailwind and Friends"},
    )
    assert response.status_code == 201, response.text
    assert wired.get(f"{PREFIX}/companies/tailwind-and-friends").json()["page_views"] == 0


def test_adding_the_same_company_twice_is_409(wired):
    body = {"company_key": "tailwind", "name": "Tailwind"}
    assert wired.post(f"{PREFIX}/companies", json=body).status_code == 201
    again = wired.post(f"{PREFIX}/companies", json=body)
    assert again.status_code == 409
    assert again.json()["error"] == "company_already_identified"


def test_adding_a_company_without_a_key_is_422(wired):
    response = wired.post(f"{PREFIX}/companies", json={"name": "Nobody"})
    assert response.status_code == 422
    assert response.json()["error"] == "company_key_required"


# --------------------------------------------------------------------------- #
# The audit source: the route that served the write
# --------------------------------------------------------------------------- #


def _mounted() -> set[str]:
    """``METHOD /path`` for every route the host mounted for this feature.

    Read out of the registry rather than out of a list written here, so the check
    is against what the app actually serves. The defect it prevents has shipped in
    this codebase before: a feature whose audit log kept naming a path the app had
    stopped serving.
    """
    record = host.REGISTRY.by_id(FEATURE_ID)
    return {f"{method} {route['path']}" for route in record.routes for method in route["methods"]}


def _audit(client) -> list[dict]:
    from dsr.api import app

    return app.state.db.audit()


def test_every_write_route_leaves_an_audit_row_naming_itself(wired):
    article = page(wired, "Article", "/newsroom/article", "Exact")
    key = capture(wired, path="/newsroom/article").json()["company_key"]
    wired.patch(f"{PREFIX}/companies/{key}", json={"name": "Someone"})
    wired.post(f"{PREFIX}/companies", json={"company_key": "hand-added"})
    wired.patch(f"{PREFIX}/pages/{article['id']}", json={"condition": "Contains"})
    wired.delete(f"{PREFIX}/pages/{article['id']}")
    profile = wired.post(f"{PREFIX}/icps", json={"name": "L", "sizes": ["1-10"]}).json()
    wired.delete(f"{PREFIX}/icps/{profile['id']}")

    mounted = _mounted()
    sources = {entry["source"] for entry in _audit(wired) if entry.get("source")}
    ours = {source for source in sources if source.startswith(("POST", "PATCH", "DELETE"))}
    assert ours, f"no write was audited at all; saw {sorted(sources)}"
    for source in ours:
        assert source in mounted, f"audit row names {source!r}, which is not a mounted route"


def test_the_expected_routes_are_the_ones_that_wrote(wired):
    """The same rule stated as a list, so a route that stopped being served shows
    up as a missing member rather than as an unnoticed extra row."""
    key = capture(wired).json()["company_key"]
    article = page(wired, "Article", "/newsroom/article", "Exact")
    wired.patch(f"{PREFIX}/companies/{key}", json={"name": "Someone"})
    wired.delete(f"{PREFIX}/pages/{article['id']}")

    sources = {entry["source"] for entry in _audit(wired) if entry.get("source")}
    assert sources == {
        f"POST {PREFIX}/installations",
        f"POST {PREFIX}/captures",
        f"POST {PREFIX}/pages",
        f"PATCH {PREFIX}/companies/{{company_key}}",
        f"DELETE {PREFIX}/pages/{{page_id}}",
    }, sorted(sources)


def test_a_capture_is_audited_under_the_tracking_snippet(wired):
    capture(wired)
    actors = {entry["actor"] for entry in _audit(wired) if entry["actor"]}
    assert "tracking-snippet" in actors, sorted(actors)


def test_a_company_amendment_names_the_actor_the_caller_gave(wired):
    key = capture(wired).json()["company_key"]
    wired.patch(f"{PREFIX}/companies/{key}", json={"name": "Someone"}, params={"actor": "sam"})
    rows = [e for e in _audit(wired) if e["actor"] == "sam" and e["action"] == "update"]
    assert rows, [e["actor"] for e in _audit(wired)]


# --------------------------------------------------------------------------- #
# The feature module, read rather than exercised
# --------------------------------------------------------------------------- #


def test_the_feature_module_is_importable_on_its_own():
    """Discovery mounted it; this proves it does not need the app to load."""
    feature = load_feature(MODULE)
    assert feature.FEATURE["id"] == FEATURE_ID
    assert feature.router.prefix == PREFIX
